"""Update phases follow the running app's declarations, without touching live work."""
import json
from types import SimpleNamespace
from pathlib import Path

import pytest
from amplifier_web import app_updates, updates, update_sequence
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager


class Runtime:
    async def close(self):pass


@pytest.fixture
async def manager(tmp_path, monkeypatch):
    service = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
    manager = UpdateManager(service)
    service.update_manager = manager
    async def application():return {'id':'application','kind':'app','status':'current'}
    monkeypatch.setattr(app_updates, 'check', application)
    yield manager
    await service.close()


def source(tier, current='a'*40):
    return {'id':tier,'kind':'bundle / module','updateTier':tier,'label':tier,
            'url':'https://example.invalid/'+tier,'ref':'main','current':current,
            'status':'not_checked','eligible':True}


@pytest.mark.parametrize('status', ['update','check_failed'])
async def test_app_is_checked_before_any_component_inventory(manager, monkeypatch, status):
    events=[]
    async def application():events.append('app');return {'id':'application','status':status}
    async def inventory(**kwargs):pytest.fail('The old app must not inspect component declarations')
    monkeypatch.setattr(app_updates,'check',application)
    monkeypatch.setattr(manager,'inventory_sources',inventory)
    await manager.check()
    assert events==['app']
    assert manager.service.state['updates']['sequence']['other']['status']=='waiting'
    assert manager.inventory==[]


@pytest.mark.parametrize('included_changed',[False, True])
async def test_check_skips_current_tiers_but_stops_at_included_updates(manager, monkeypatch, included_changed):
    calls=[]
    async def inventory(**kwargs):return [source('included'),source('other')]
    async def process(*args,**kwargs):
        calls.append(args[2].rsplit('/',1)[-1])
        return ('b' if included_changed or calls[-1]=='other' else 'a')*40+' refs/heads/main'
    monkeypatch.setattr(manager,'inventory_sources',inventory)
    monkeypatch.setattr(updates,'process',process)
    await manager.check()
    assert calls==(['included'] if included_changed else ['included','other'])
    state=manager.service.state['updates']
    assert state['sequence']['stage']==('included' if included_changed else 'other')
    assert state['available']==1
    assert not state['sequence']['install']


async def test_included_check_error_does_not_install_or_check_optional_sources(manager,monkeypatch):
    calls=[]
    async def inventory(**kwargs):return [source('included'),source('other')]
    async def process(*args,**kwargs):calls.append(args[2]);raise RuntimeError('offline')
    monkeypatch.setattr(manager,'inventory_sources',inventory)
    monkeypatch.setattr(updates,'process',process)
    await manager.check()
    await manager.install()
    assert calls==['https://example.invalid/included']
    assert manager.service.state['updates']['phase']=='error'
    assert not manager.service.state['updates']['sequence']['install']


async def test_manual_install_intent_survives_app_staging(manager,monkeypatch):
    manager.service.state['updates'].update(appAvailable=True,sequence={'stage':'application','install':False})
    calls=[]
    async def app():calls.append('app')
    monkeypatch.setattr(manager,'app',app)
    await manager.install()
    assert calls==['app']
    assert manager.service.state['updates']['sequence']['install'] is True
    restored=UpdateManager(manager.service)
    assert restored.service.state['updates']['sequence']['install'] is True


@pytest.mark.parametrize('install',[False,True])
@pytest.mark.parametrize('tier',['included','other'])
async def test_after_activation_tick_checks_next_tier_without_waiting_for_interval(manager,monkeypatch,install,tier):
    manager.service.state['settings']['updates'].update(autoCheck=False,autoInstall=False)
    manager.service.state['updates'].update(phase='installed',sequence={'stage':'application' if tier=='included' else 'included','nextStage':tier,'install':install})
    calls=[]
    async def check(**kwargs):
        calls.append(kwargs)
        manager.service.state['updates'].update(phase='available',available=1)
    async def apply(*, automatic):
        assert automatic
        calls.append('install')
    monkeypatch.setattr(manager,'check',check)
    monkeypatch.setattr(manager,'install',apply)
    await manager.tick()
    assert calls==[{'tier':tier,'install':install,'fresh':True}]+(['install'] if install else [])


async def test_completed_sequence_has_no_repeated_background_check(manager,monkeypatch):
    import time
    manager.service.state['updates'].update(phase='checked',lastCheck=time.time(),sequence={'stage':'complete','install':False})
    async def check(**kwargs):pytest.fail('completed sequence should wait for the configured interval')
    monkeypatch.setattr(manager,'check',check)
    await manager.tick()


def test_missing_included_sources_and_cold_worker_are_installable_without_writes(tmp_path,monkeypatch):
    monkeypatch.setattr(update_sequence,'included_sources',lambda:({'declared-provider'},[{'url':'https://example.invalid/skills','ref':'main'}]))
    rows=update_sequence.classify(tmp_path,[])
    assert {row['kind'] for row in rows}=={'runtime environment','included source'}
    assert all(row['missing'] and row['updateTier']=='included' for row in rows)
    assert not list(tmp_path.iterdir())


def test_included_classification_keeps_explicit_pins_and_local_sources_protected(tmp_path,monkeypatch):
    monkeypatch.setattr(update_sequence,'included_sources',lambda:({'declared-provider'},[]))
    row={'id':'runtime:declared-provider','package':'declared-provider','kind':'runtime dependency','eligible':False,'status':'pinned','ref':'v1.0.0','url':'https://example.invalid/provider'}
    rows=update_sequence.classify(tmp_path,[row])
    assert rows[-1]['updateTier']=='included'
    assert not rows[-1]['eligible'] and rows[-1]['status']=='pinned'


async def test_missing_source_staging_verifies_exact_checked_revision(tmp_path,monkeypatch):
    from amplifier_foundation.sources.git import GitSourceHandler
    destination=tmp_path/'foundation'
    cached=destination/'cache/source';cached.mkdir(parents=True)
    async def resolve(self,parsed,cache_dir):
        assert cache_dir==destination/'cache' and parsed.ref=='main'
        return SimpleNamespace(active_path=cached)
    async def process(*args,**kwargs):return 'b'*40
    monkeypatch.setattr(GitSourceHandler,'_resolve_locked',resolve)
    monkeypatch.setattr(updates,'process',process)
    row={'url':'https://example.invalid/skills','ref':'main','latest':'a'*40}
    with pytest.raises(ValueError,match='moved after checking'):
        await update_sequence.stage_missing(None,destination,row)


@pytest.mark.parametrize('case', ['pin', 'exact', 'transport', 'interrupted'])
async def test_checked_source_failure_retry_disposition_survives_checks_and_restart(manager, monkeypatch, case):
    """Real check/install/tick/receipts; only remote I/O and costly qualification
    are fixtures. The actual source guard, not an injected typed error, rejects.
    This is scheduling proof, not installed-package qualification.
    """
    import asyncio
    import copy
    import time
    from amplifier_web import update_health, update_plan, session_files
    from amplifier_web.runtime_source_alignment import verify_checked_selection
    from amplifier_web.update_diagnostics import UpdateDiagnostics

    latest = ['b' * 40]
    row = {**source('included'), 'kind': 'runtime dependency',
           'package': 'amplifier-foundation'}
    async def inventory(**kw): return [dict(row)]
    async def remote(*args, **kw): return latest[0] + ' refs/heads/main'
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    monkeypatch.setattr(updates, 'process', remote)
    monkeypatch.setattr(updates, 'foundation_home', lambda home: home/'absent')
    monkeypatch.setattr(session_files, 'amplifier_home', lambda: manager.home/'absent-shared')
    async def build(*args):
        return {'mode': 'worker', 'reason': 'fixture', 'activation': 'per-worker'}
    monkeypatch.setattr(update_plan, 'build', build)
    calls = []
    async def preflight():
        if case == 'transport':
            raise TimeoutError('private endpoint must not enter diagnostics')
        if case == 'interrupted':
            raise asyncio.CancelledError()
        selected = {'url': row['url'], 'ref': 'main', 'revision': 'a'*40,
                    'status': 'explicit-pin' if case == 'pin' else 'selected'}
        verify_checked_selection(
            {'amplifier-foundation': {**selected, 'revision': latest[0]}},
            {'packages': {'amplifier-foundation': selected},
             'batchPaths': {'amplifier-foundation': '/synthetic/private/path'}},
            exact=case == 'exact')
    async def validate(stage, revision):
        calls.append(revision)
        await manager.diagnostics.run('ecosystem-source-preflight', preflight)
    monkeypatch.setattr(manager, 'validate', validate)
    # Keep unrelated periodic storage work out of this scheduler fixture.
    manager._storage_last_pass = time.time()
    manager._storage_policy_due = False
    manager._update_health_initial = False
    await manager.check()
    if case == 'interrupted':
        with pytest.raises(asyncio.CancelledError):
            await manager.install(automatic=True)
    else:
        await manager.install(automatic=True)
    assert len(calls) == 1
    assert not updates.active_release(manager.home)
    diagnostic = manager.diagnostics.state
    failure = copy.deepcopy(diagnostic['lastFailure'])
    mode = 'inspect' if case == 'interrupted' else 'backoff' if case == 'transport' else 'changed-inputs'
    assert diagnostic['batch']['retry']['mode'] == mode
    if case in {'pin', 'exact'}:
        assert failure['package'] == 'amplifier-foundation'
        assert failure['reason'] == ('runtime-source-constraint' if case == 'pin' else 'runtime-resolution-mismatch')
        status = update_health.current(manager.service.state['updates'], {})
        assert status['status'] == 'protected'
        assert status['component'] == 'amplifier-foundation'
    assert 'private' not in json.dumps(failure)
    # The persisted diagnostic reconstruction must retain the same disposition.
    await manager.publish()
    manager.diagnostics = UpdateDiagnostics(manager)
    for _ in range(3):
        await manager.check()
        await manager.tick()
    assert len(calls) == 1
    assert manager.diagnostics.state['lastFailure'] == failure
    if case == 'interrupted':
        latest[0] = 'c'*40
        await manager.check()
        await manager.tick()
        assert len(calls) == 1
        with pytest.raises(ValueError, match='not replayed'):
            await manager.install()
        return
    manager.diagnostics.state['batch']['retry']['retryAt'] = 0
    await manager.check()
    await manager.tick()
    assert len(calls) == (2 if case == 'transport' else 1)
    if case == 'transport':
        assert manager.diagnostics.state['batch']['retry']['attempts'] == 2
        return
    # Unrelated publication/credential settings do not change the source graph.
    manager.service.state['configurationRevision'] = manager.service.state.get('configurationRevision', 0) + 1
    await manager.check()
    await manager.tick()
    assert len(calls) == 1
    latest[0] = 'c'*40
    await manager.check()
    await manager.tick()
    assert len(calls) == 2
    await manager.install()  # Deliberate retry is still allowed; pin stays protected.
    assert len(calls) == 3
    assert not updates.active_release(manager.home)


async def test_preparation_witness_tracks_source_policy_not_unrelated_settings(manager, monkeypatch):
    from amplifier_web.update_health import preparation_key
    from amplifier_web.host.config import write_private
    from amplifier_web.host import config as host_config
    shared = manager.home/'fixture-shared'
    shared.mkdir()
    monkeypatch.setattr(host_config, 'amplifier_home', lambda: shared)
    rows = [{**source('included'), 'latest': 'b'*40}]
    config = shared/'settings.yaml'
    write_private(config, 'updates:\n  intervalHours: 4\n')
    original = preparation_key(manager, rows)
    write_private(config, 'updates:\n  intervalHours: 8\n')
    manager.service.state['configurationRevision'] = 5
    assert preparation_key(manager, rows) == original
    write_private(config, 'sources:\n  bundles:\n    routing-matrix: git+https://example.invalid/custom@v1\n')
    assert preparation_key(manager, rows) != original


async def test_deterministic_disposition_survives_service_reload(manager, monkeypatch):
    from amplifier_web.update_health import preparation_key, record_retry
    manager.inventory=[{**source('included'), 'latest':'b'*40, 'status':'update'}]
    (manager.directory/'inventory.json').write_text(json.dumps(manager.inventory))
    manager.diagnostics.begin('ecosystem', 'b'*32, components=['amplifier-foundation'])
    failure=manager.diagnostics.record('ecosystem-source-preflight','failed',
        reason='runtime-source-constraint',package='amplifier-foundation')
    record_retry(manager.diagnostics.state['batch'],preparation_key(manager,manager.inventory),failure)
    await manager.publish(phase='error', error='Source policy conflict')
    home=manager.home
    await manager.service.close()
    service=AppService(home,Runtime(),workspace=home.parent)
    try:
        restored=UpdateManager(service);service.update_manager=restored
        assert restored.diagnostics.state['lastFailure']==failure
        assert restored.diagnostics.state['batch']['retry']['mode']=='changed-inputs'
        async def forbidden(*args,**kwargs):pytest.fail('reloaded conflict must not prepare')
        monkeypatch.setattr(restored,'stage_cache_sources',forbidden)
        before=set((home/'updates/releases').glob('*'))
        await restored.install(automatic=True)
        assert set((home/'updates/releases').glob('*'))==before
        assert not updates.active_release(home)
    finally:
        await service.close()
