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
    async def inventory():pytest.fail('The old app must not inspect component declarations')
    monkeypatch.setattr(app_updates,'check',application)
    monkeypatch.setattr(manager,'inventory_sources',inventory)
    await manager.check()
    assert events==['app']
    assert manager.service.state['updates']['sequence']['other']['status']=='waiting'
    assert manager.inventory==[]


@pytest.mark.parametrize('included_changed',[False, True])
async def test_check_skips_current_tiers_but_stops_at_included_updates(manager, monkeypatch, included_changed):
    calls=[]
    async def inventory():return [source('included'),source('other')]
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
    async def inventory():return [source('included'),source('other')]
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
async def test_after_activation_tick_checks_next_tier_without_waiting_for_interval(manager,monkeypatch,install):
    manager.service.state['settings']['updates'].update(autoCheck=False,autoInstall=False)
    manager.service.state['updates'].update(phase='installed',sequence={'stage':'included','nextStage':'other','install':install})
    calls=[]
    async def check(**kwargs):
        calls.append(kwargs)
        manager.service.state['updates'].update(phase='available',available=1)
    async def apply():calls.append('install')
    monkeypatch.setattr(manager,'check',check)
    monkeypatch.setattr(manager,'install',apply)
    await manager.tick()
    assert calls==[{'tier':'other','install':install}]+(['install'] if install else [])


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
    from amplifier_foundation.sources.resolver import SimpleSourceResolver
    destination=tmp_path/'foundation'
    cached=destination/'cache/source';cached.mkdir(parents=True)
    async def resolve(self,uri):
        assert self.cache_dir==destination/'cache'
        return SimpleNamespace(active_path=cached)
    async def process(*args,**kwargs):return 'b'*40
    monkeypatch.setattr(SimpleSourceResolver,'resolve',resolve)
    monkeypatch.setattr(updates,'process',process)
    row={'url':'https://example.invalid/skills','ref':'main','latest':'a'*40}
    with pytest.raises(ValueError,match='moved after checking'):
        await update_sequence.stage_missing(None,destination,row)
