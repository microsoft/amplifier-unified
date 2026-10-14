"""Loaded provenance is observation, not inferred bytecode/test success."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import loaded_provenance
from amplifier_web.service import AppService, AppError
from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_worker import Worker


@pytest.fixture
async def candidate(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    for args in [('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.test')]:
        subprocess.run(['git', '-C', str(workspace), *args], check=True, capture_output=True)
    source = workspace / 'module.py'; source.write_text('VALUE=1\n')
    subprocess.run(['git', '-C', str(workspace), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(workspace), 'commit', '-qm', 'Fixture'], check=True, capture_output=True)
    worker = {'status': 'observed', 'mounted': True, 'modules': [{'module': 'candidate_module',
        'loaded': True, 'origin': str(source), 'fileStatus': 'observed',
        'currentFileSha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'importedBytesVerified': False}]}
    runtime = SimpleNamespace(close=AsyncMock(), provenance=AsyncMock(return_value=worker))
    app = AppService(tmp_path / 'app', runtime, workspace=workspace)
    await app.dispatch('session.create', {'title': 'Integration owner'})
    sid = app._session()['id']
    snapshot = (await app.dispatch('worktree.inspect', {'sessionId': sid}))['result']['repository']
    args = {'sessionId': sid, 'purpose': 'Candidate observation', 'repositories': [
        {'repositoryPath': snapshot['root'], 'sourceRevision': snapshot['sourceRevision']}],
        'dependencies': [{'module': 'candidate_module', 'repositoryPath': snapshot['root'],
                          'path': 'module.py', 'sha256': worker['modules'][0]['currentFileSha256']}]}
    yield app, args, source, worker
    await app.close()


async def test_capture_check_retry_and_read_preserve_candidate_and_private_state(candidate):
    app, args, file, worker = candidate
    draft = app.state['view']['draft'] = 'Private draft'
    first = (await app.app_bridge('dispatch', {'action': 'integration.capture', 'args': args,
                    'id': 'capture-1'}, args['sessionId']))['result']
    assert first['status'] == 'matched' and first['testOutcome'] == 'not_run'
    assert not first['isolationVerified'] and not first['checks'][1]['importedBytesVerified']
    assert (await app.dispatch('integration.read', {'sessionId': args['sessionId'], 'id': first['id']}))['result'] == first
    before = app.runtime.provenance.await_count
    duplicate = await app.app_bridge('dispatch', {'action': 'integration.capture', 'args': args,
                    'id': 'capture-1'}, args['sessionId'])
    assert duplicate['duplicate'] and app.runtime.provenance.await_count == before
    checked = (await app.dispatch('integration.check', {'sessionId': args['sessionId'], 'id': first['id']}, command_id='check-1'))['result']
    assert checked['status'] == 'matched' and checked['captureId'] == first['id']
    (file.parent / 'other.txt').write_text('Peer changed a dependency candidate')
    drift = (await app.dispatch('integration.check', {'sessionId': args['sessionId'], 'id': first['id']}, command_id='check-2'))['result']
    assert drift['status'] == 'mismatch' and drift['checks'][0]['status'] == 'mismatch'
    assert (await app.dispatch('integration.read', {'sessionId': args['sessionId'], 'id': first['id']}))['result'] == first
    assert app.state['view']['draft'] == draft and app.state['selectedSessionId'] == args['sessionId']


@pytest.mark.parametrize('case', ['sibling', 'unloaded', 'hash', 'no_worker'])
async def test_dependency_origin_drift_and_unknown_are_explicit(candidate, case):
    app, args, source, worker = candidate
    if case == 'sibling': worker['modules'][0]['origin'] = str(source.parent.parent / 'sibling/module.py')
    elif case == 'unloaded': worker['modules'][0]['loaded'] = False
    elif case == 'hash': worker['modules'][0]['currentFileSha256'] = 'f'*64
    else: worker.update(status='unknown', mounted=False)
    value = (await app.dispatch('integration.capture', args, command_id='capture'))['result']
    assert value['status'] == ('mismatch' if case in {'sibling', 'hash'} else 'unknown')
    assert value['testOutcome'] == 'not_run'


async def test_scope_path_escape_and_foreign_agent_are_rejected(candidate):
    app, args, _, _ = candidate
    for path in ('../module.py', '/tmp/module.py', '.git/config'):
        bad = {**args, 'dependencies': [{**args['dependencies'][0], 'path': path}]}
        with pytest.raises(AppError, match='paths must stay'):
            await app.dispatch('integration.capture', bad)
    await app.dispatch('session.create', {'title': 'Unrelated'})
    other = app._session()['id']
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch', {'action': 'integration.capture', 'args': args}, other)


async def test_source_or_execution_changed_during_capture_is_not_certified(candidate, monkeypatch):
    app, args, source, worker = candidate
    async def change(sid, names):
        source.write_text('VALUE=2\n')
        return worker
    app.runtime.provenance.side_effect = change
    value = (await app.dispatch('integration.capture', args))['result']
    assert value['status'] == 'unknown' and 'changed during' in value['checks'][0]['observed']['detail']
    async def move(sid, names):
        app._session(sid)['executionRevision'] = 1
        return worker
    app.runtime.provenance.side_effect = move
    with pytest.raises(AppError, match='binding changed during'):
        await app.dispatch('integration.capture', args, command_id='moving')
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', ('moving',)).fetchone()


async def test_lost_capture_ack_returns_same_evidence_without_recapture(candidate, monkeypatch):
    app, args, _, _ = candidate
    save = app._save_changes
    def lost(*values, **options):
        save(*values, **options)
        raise OSError('lost after commit')
    monkeypatch.setattr(app, '_save_changes', lost)
    with pytest.raises(AppError, match='lost after commit'):
        await app.dispatch('integration.capture', args, command_id='lost')
    count = app.runtime.provenance.await_count
    duplicate = await app.dispatch('integration.capture', args, command_id='lost')
    assert duplicate['duplicate'] and app.runtime.provenance.await_count == count


async def test_precommit_failure_does_not_retain_phantom_candidate(candidate, monkeypatch):
    app, args, _, _ = candidate
    monkeypatch.setattr(app, '_save_changes', lambda **_: (_ for _ in ()).throw(OSError('before commit')))
    with pytest.raises(AppError, match='before commit'):
        await app.dispatch('integration.capture', args, command_id='failed')
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', ('failed',)).fetchone()


async def test_loaded_inspection_never_imports_requested_code_and_separates_mount_bytes(tmp_path, monkeypatch):
    path = tmp_path / 'loaded.py'; path.write_text('VALUE=1\n')
    module = ModuleType('provenance_loaded'); module.__file__ = str(path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    missing = tmp_path / 'should_not_import.py'; missing.write_text('raise AssertionError("must not import")')
    monkeypatch.syspath_prepend(str(tmp_path))
    mounted = await loaded_provenance.observe(None, ['provenance_loaded', 'should_not_import'])
    rows = {row['module']: row for row in mounted['modules']}
    assert rows['provenance_loaded']['loaded'] and rows['provenance_loaded']['fileStatus'] == 'observed'
    assert not rows['should_not_import']['loaded'] and 'should_not_import' not in sys.modules
    path.write_text('VALUE=2\n')
    current = await loaded_provenance.observe(None, ['provenance_loaded'], mounted=mounted)
    row = next(row for row in current['modules'] if row['module'] == 'provenance_loaded')
    assert row['changedSinceMount'] and not row['importedBytesVerified']
    assert row['fileObservedAtMount']['sha256'] != row['currentFileSha256']


async def test_loaded_file_io_is_detached_and_bounded(tmp_path, monkeypatch):
    path = tmp_path / 'oversize.py'; path.write_bytes(b'x'*(loaded_provenance.MAX_FILE+1))
    module = ModuleType('oversize_provenance'); module.__file__ = str(path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    value = await loaded_provenance.observe(None, [module.__name__])
    row = next(row for row in value['modules'] if row['module'] == module.__name__)
    assert row['loaded'] and row['fileStatus'] == 'unknown' and 'currentFileSha256' not in row
    with pytest.raises(ValueError, match='sixteen'):
        await loaded_provenance.observe(None, ['a'] * 17)


async def test_loaded_identity_changed_during_file_read_stays_unknown(tmp_path, monkeypatch):
    path = tmp_path / 'loaded.py'; path.write_text('VALUE=1\n')
    module = ModuleType('changing_provenance'); module.__file__ = str(path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    arrived, release = threading.Event(), threading.Event()
    hashes = loaded_provenance.hashes
    def slow(rows):
        arrived.set()
        assert release.wait(10)
        return hashes(rows)
    monkeypatch.setattr(loaded_provenance, 'hashes', slow)
    task = asyncio.create_task(loaded_provenance.observe(None, [module.__name__]))
    try:
        assert await asyncio.to_thread(arrived.wait, 5)
        module.__file__ = str(tmp_path / 'replacement.py')
        release.set()
        value = await task
        assert value['status'] == 'unknown' and value['identityStable'] is False
    finally:
        release.set()


@pytest.mark.parametrize('replace', ['module', 'mount'])
async def test_same_origin_or_type_replacement_during_detached_read_is_unknown(tmp_path, monkeypatch, replace):
    path = tmp_path / 'same.py'; path.write_text('VALUE=1\n')
    module = ModuleType('same_origin_provenance'); module.__file__ = str(path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    class Mounted:
        def __eq__(self, other): raise AssertionError('Do not invoke object equality')
    mounted = {'tool': Mounted()}
    session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda section: mounted if section == 'tools' else None))
    arrived, release = threading.Event(), threading.Event()
    hashes = loaded_provenance.hashes
    def slow(rows):
        arrived.set()
        assert release.wait(10)
        return hashes(rows)
    monkeypatch.setattr(loaded_provenance, 'hashes', slow)
    task = asyncio.create_task(loaded_provenance.observe(session, [module.__name__]))
    try:
        assert await asyncio.to_thread(arrived.wait, 5)
        if replace == 'module':
            replacement = ModuleType(module.__name__); replacement.__file__ = str(path)
            monkeypatch.setitem(sys.modules, module.__name__, replacement)
        else:
            mounted['tool'] = Mounted()
        release.set()
        value = await task
        assert value['status'] == 'unknown' and value['identityStable'] is False
        assert isinstance(json.dumps(value), str), 'Private object references must not enter public evidence'
    finally:
        release.set()


async def test_dormant_runtime_provenance_does_not_start_or_unpark():
    manager = RuntimeManager()
    manager._start_locked = AsyncMock(side_effect=AssertionError('Must remain dormant'))
    manager._retired['old'] = ({'id': 'old'}, None)
    assert (await manager.provenance('old', ['json']))['status'] == 'unknown'
    assert not manager.workers


async def test_worker_protocol_inspects_actual_loaded_origins_without_execution(monkeypatch):
    from amplifier_web import runtime_worker
    replies = []
    monkeypatch.setattr(runtime_worker, 'publish', replies.append)
    worker = Worker()
    worker.acquire_for_mutation = AsyncMock(side_effect=AssertionError('No writer acquisition'))
    await worker.command({'op': 'dependencies', 'id': 'provenance', 'provenance': True, 'modules': ['json']})
    value = replies[0]['result']
    assert value['status'] == 'observed' and not value['mounted']
    row = next(row for row in value['modules'] if row['module'] == 'json')
    assert row['loaded'] and row['origin'] == str(Path(json.__file__).resolve())


async def test_repository_reads_release_app_lock_and_cancel_without_receipt(candidate, monkeypatch):
    app, args, _, _ = candidate
    arrived, release = threading.Event(), threading.Event()
    inspect = app.worktrees.git.inspect
    def slow(path):
        arrived.set()
        assert release.wait(10)
        return inspect(path)
    monkeypatch.setattr(app.worktrees.git, 'inspect', slow)
    task = asyncio.create_task(app.dispatch('integration.capture', args, command_id='cancelled'))
    try:
        assert await asyncio.to_thread(arrived.wait, 5)
        async with asyncio.timeout(1):
            await app.dispatch('view.update', {'patch': {'draft': 'Still responsive'}})
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', ('cancelled',)).fetchone()
    finally:
        release.set()


async def test_bounded_worker_reports_and_changed_worker_identity(candidate):
    app, args, _, worker = candidate
    worker['workerInstance'] = 'instance-1'
    await app.dispatch('integration.capture', args, command_id='capture')
    worker['workerInstance'] = 'instance-2'
    value = (await app.dispatch('integration.check', {'sessionId': args['sessionId'], 'id': 'capture'}))['result']
    assert value['status'] == 'unknown' and value['checks'][-1]['kind'] == 'worker'
    app.runtime.provenance.return_value = {'modules': ['invalid']}
    value = (await app.dispatch('integration.capture', args))['result']
    assert value['status'] == 'unknown' and 'invalid' in value['worker']['reason']
    app.runtime.provenance.return_value = {'modules': [], 'detail': 'x'*(513*1024)}
    assert (await app.dispatch('runtime.provenance', {'sessionId': args['sessionId']}))['result']['status'] == 'unknown'


async def test_associated_checkout_removal_during_observation_rechecks_authority(candidate, monkeypatch):
    app, args, source, worker = candidate
    external = source.parent.parent / 'associated'
    subprocess.run(['git', 'clone', '-q', str(source.parent), str(external)], check=True, capture_output=True)
    records = [{'sessionId': args['sessionId'], 'path': str(external), 'status': 'ready'}]
    monkeypatch.setattr(app.worktrees.git, 'records', lambda: records)
    args['repositories'][0]['repositoryPath'] = str(external)
    args['dependencies'][0]['repositoryPath'] = str(external)
    async def remove(sid, names):
        records.clear()
        return worker
    app.runtime.provenance.side_effect = remove
    with pytest.raises(AppError, match='associated checkout'):
        await app.dispatch('integration.capture', args, command_id='removed-association')
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', ('removed-association',)).fetchone()


async def test_overall_inspection_deadline_leaves_no_receipt(candidate, monkeypatch):
    from amplifier_web import integration_evidence
    app, args, _, _ = candidate
    monkeypatch.setattr(integration_evidence, 'MAX_INSPECTION_SECONDS', .05)
    async def slow(*_): await asyncio.Event().wait()
    monkeypatch.setattr(integration_evidence, 'repository_snapshot', slow)
    with pytest.raises(AppError, match='time limit'):
        await app.dispatch('integration.capture', args, command_id='deadline')
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', ('deadline',)).fetchone()


async def test_saved_observation_pages_do_not_shift_when_new_receipts_arrive(candidate):
    app, args, _, _ = candidate
    original = await app.dispatch('integration.capture', args, command_id='capture-page')
    def saved(identity):
        value = {**original, 'result': {**original['result'], 'id': identity}}
        app.db.execute('INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)', (identity, identity, json.dumps(value)))
    for index in range(17): saved(f'page-{index}')
    app.db.commit()
    first = (await app.dispatch('integration.list', {'sessionId': args['sessionId']}))['result']
    assert len(first['items']) == 16 and first['nextBefore'] is not None
    assert all('worker' not in row and 'repositories' not in row for row in first['items'])
    saved('newer'); app.db.commit()
    second = (await app.dispatch('integration.list', {'sessionId': args['sessionId'], 'before': first['nextBefore']}))['result']
    assert [row['id'] for row in second['items']] == ['page-0', 'capture-page']
    assert second['nextBefore'] is None


async def test_candidate_http_parity_restart_list_and_no_private_selection(candidate, authenticated_client):
    from amplifier_web.server import create_app
    app, args, source, worker = candidate
    await app.dispatch('session.create', {'title': 'Independent'})
    other = app._session()['id']
    directory = source.parent.parent / 'app'
    await app.close()
    server = await create_app(directory, workspace=source.parent,
        runtime=SimpleNamespace(close=AsyncMock(), provenance=AsyncMock(return_value=worker)),
        voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(server)
    await client.post('/api/clients/attach', json={'clientId': 'evidence-terminal', 'kind': 'tui'})
    headers = {'X-Amplifier-Client': 'evidence-terminal'}
    endpoint = f"/api/sessions/{args['sessionId']}/commands"
    async def command(action, body, identity):
        response = await client.post(endpoint, json={'id': identity, 'action': action, 'args': body}, headers=headers)
        assert response.status == 200, await response.text()
        return await response.json()
    capture = await command('integration.capture', args, 'capture-http')
    assert capture['result']['status'] == 'matched'
    assert (await command('integration.capture', args, 'capture-http'))['duplicate']
    assert (await command('runtime.provenance', {}, 'inspect-http'))['result']['mounted']
    listed = (await command('integration.list', {}, 'list-http'))['result']['items']
    assert [row['id'] for row in listed] == ['capture-http']
    assert server['service'].state['selectedSessionId'] == other
    denied = await client.post(endpoint, json={'id': 'wrong', 'action': 'integration.read',
        'args': {'sessionId': other, 'id': 'capture-http'}}, headers=headers)
    assert denied.status == 400
    await client.close()
    restarted = AppService(directory, workspace=source.parent)
    try:
        source.unlink()
        assert (await restarted.dispatch('integration.read', {'sessionId': args['sessionId'], 'id': 'capture-http'}))['result'] == capture['result']
        with pytest.raises(AppError, match='exact candidate'):
            await restarted.app_bridge('dispatch', {'action': 'integration.read', 'args': {'sessionId': other, 'id': 'capture-http'}}, other)
        assert not (await restarted.dispatch('integration.list', {'sessionId': other}))['result']['items']
        assert (await restarted.dispatch('integration.capture', args, command_id='capture-http'))['duplicate']
    finally:
        await restarted.close()


async def test_real_native_worker_sibling_dependency_conflict_preserves_contributor_sources(tmp_path, monkeypatch):
    pytest.importorskip('amplifier_module_loop_live')
    pytest.importorskip('amplifier_module_context_simple')
    import amplifier_web
    # Supply the exact source/wheel under test to the native subprocess.
    monkeypatch.setenv('PYTHONPATH', str(Path(amplifier_web.__file__).parent.parent))
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    content = 'class ProvenanceTool:\n    name="fixture"\n    description="Passive fixture"\n    input_schema={"type":"object"}\n    async def execute(self, value):\n        raise AssertionError("must not execute")\n'
    for name in ('candidate', 'dependency'):
        root = workspace / name; root.mkdir()
        for args in [('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.test')]:
            subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
        (root / '.gitignore').write_text('__pycache__/\n')
        (root / 'module.py').write_text(content)
        subprocess.run(['git', '-C', str(root), 'add', '.'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(root), 'commit', '-qm', 'Contributor source'], check=True, capture_output=True)
    runtime = RuntimeManager(command=[sys.executable, str(Path(__file__).parent / 'fixtures/provenance_worker.py')], startup_timeout=20)
    app = AppService(tmp_path / 'app', runtime, workspace=workspace)
    try:
        await app.dispatch('session.create', {'title': 'Combined candidate'})
        sid = app._session()['id']
        async def emit(*args): pass
        await runtime.start(dict(app._session()), emit)
        runtime.workers[sid]['parked'] = True
        before = {name: (workspace / name / 'module.py').read_bytes() for name in ('candidate', 'dependency')}
        value = (await app.app_bridge('dispatch', {'action': 'runtime.provenance', 'args': {'sessionId': sid,
            'modules': ['fixture_dependency', 'not_loaded_project_module']}}, sid))['result']
        assert 'modules' in value, value
        modules = {row['module']: row for row in value['modules']}
        assert value['mounted'] and runtime.workers[sid]['parked']
        assert modules['amplifier_core']['loaded'] and modules['amplifier_foundation']['loaded']
        assert not modules['not_loaded_project_module']['loaded']
        assert any(row['section'] == 'context' for row in value['mounts'])
        dependency = modules['fixture_dependency']
        assert dependency['origin'] == str(workspace / 'dependency/module.py')
        assert dependency['fileStatus'] == 'observed' and dependency['currentFileSha256']
        assert 'fileObservedAtMount' not in dependency and 'changedSinceMount' not in dependency
        roots = []
        for name in ('candidate', 'dependency'):
            snapshot = (await app.dispatch('worktree.inspect', {'sessionId': sid, 'repositoryPath': name}))['result']['repository']
            roots.append({'repositoryPath': snapshot['root'], 'sourceRevision': snapshot['sourceRevision']})
        arguments = {'sessionId': sid, 'repositories': roots, 'dependencies': [{'module': 'fixture_dependency',
            'repositoryPath': roots[0]['repositoryPath'], 'path': 'module.py', 'sha256': dependency['currentFileSha256']}]}
        conflict = (await app.dispatch('integration.capture', arguments, command_id='sibling-conflict'))['result']
        assert conflict['status'] == 'mismatch' and conflict['checks'][-1]['status'] == 'mismatch'
        arguments['dependencies'][0]['repositoryPath'] = roots[1]['repositoryPath']
        captured = (await app.dispatch('integration.capture', arguments, command_id='combined'))['result']
        assert captured['status'] == 'matched'
        assert before == {name: (workspace / name / 'module.py').read_bytes() for name in before}
        (workspace / 'dependency/module.py').write_text(content + '# Contributor refinement\n')
        drift = (await app.dispatch('integration.check', {'sessionId': sid, 'id': 'combined'}))['result']
        assert drift['status'] == 'mismatch'
        row = next(row for row in drift['worker']['modules'] if row['module'] == 'fixture_dependency')
        assert row['currentFileSha256'] != dependency['currentFileSha256']
        assert 'fileObservedAtMount' not in row and 'changedSinceMount' not in row
        assert not row['importedBytesVerified']
        assert (workspace / 'candidate/module.py').read_bytes() == before['candidate']
        assert (workspace / 'dependency/module.py').read_text().endswith('# Contributor refinement\n')
        assert (await app.dispatch('integration.read', {'sessionId': sid, 'id': 'combined'}))['result'] == captured
    finally:
        await app.close()


@pytest.mark.parametrize('bridge', [False, True])
@pytest.mark.parametrize('action', ['integration.capture', 'integration.check', 'integration.read', 'integration.list', 'runtime.provenance'])
async def test_integration_observation_uses_metadata_without_hydrating_history(candidate, monkeypatch, action, bridge):
    app, capture_args, _, _ = candidate
    sid = capture_args['sessionId']
    saved = await app.dispatch('integration.capture', capture_args, command_id='existing-candidate')
    arguments = {'sessionId': sid}
    if action == 'integration.capture': arguments = capture_args
    if action in {'integration.check', 'integration.read'}: arguments['id'] = 'existing-candidate'
    original = app._session
    calls = []
    def metadata_only(identity=None, *, hydrate=True):
        assert not hydrate, 'Integration authority must not load cold conversation bodies'
        calls.append(identity)
        return original(identity, hydrate=False)
    with monkeypatch.context() as patch:
        patch.setattr(app, '_session', metadata_only)
        patch.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('History loaded')))
        patch.setattr(app.cold_display, 'hydrate', lambda *_: pytest.fail('Cold display hydrated'))
        before = app.runtime.provenance.await_count
        if bridge:
            value = await app.app_bridge('dispatch', {'action': action, 'args': arguments,
                'id': 'metadata-observation', '_runtimeSessionId': sid}, sid)
        else:
            value = await app.dispatch(action, arguments, command_id='metadata-observation')
        assert value['accepted'] and calls and set(calls) == {sid}
        if action == 'integration.read': assert value['result'] == saved['result']
        if action == 'integration.list': assert value['result']['items'][0]['id'] == 'existing-candidate'
        assert app.runtime.provenance.await_count == before + (action not in {'integration.read', 'integration.list'})


@pytest.mark.parametrize('principal', ['foreign-root', 'child'])
async def test_metadata_only_integration_authority_still_rejects_other_callers(candidate, monkeypatch, principal):
    app, arguments, _, _ = candidate
    sid = arguments['sessionId']
    await app.dispatch('session.create', {'title': 'Other root'})
    other = app._session()['id']
    original = app._session
    def metadata_only(identity=None, *, hydrate=True):
        assert not hydrate
        return original(identity, hydrate=False)
    with monkeypatch.context() as patch:
        patch.setattr(app, '_session', metadata_only)
        caller = other if principal == 'foreign-root' else sid
        actor = other if principal == 'foreign-root' else 'child-native-id'
        with pytest.raises(AppError, match='calling conversation|child cannot borrow'):
            await app.app_bridge('dispatch', {'action': 'integration.capture', 'args': arguments,
                '_runtimeSessionId': actor}, caller)
    app.runtime.provenance.assert_not_awaited()
