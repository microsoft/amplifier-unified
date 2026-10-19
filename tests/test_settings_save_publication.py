"""Settings receipts are bounded and do not await idle runtime retirement."""
from amplifier_web.state_records import load as load_saved_state
import asyncio
import copy
import json

import pytest

from amplifier_web.management import Management
from amplifier_web.preferences import SettingsStore
from amplifier_web.service import AppService, AppError
from amplifier_web.setup import SetupManager
from configuration_admin import authorized_admin


class Runtime:
    def __init__(self):
        self.workers={}
        self.stops=[]
        self.entered=asyncio.Event()
        self.finish=asyncio.Event()
        self.fail=False
    async def stop(self,identity,*,reason='stopped'):
        self.stops.append(identity)
        self.entered.set()
        await self.finish.wait()
        if self.fail:raise ValueError('Private runtime detail')
        self.workers.pop(identity,None)
        if getattr(self, 'emit', None):
            await self.emit('runtime.warmth' if reason == 'configuration' else 'runtime.status',
                            {'sessionId': identity, 'status': 'cold' if reason == 'configuration' else 'stopped'})
    async def close(self):self.finish.set()


@pytest.fixture
async def app(tmp_path,monkeypatch):
    service=AppService(tmp_path/'app',Runtime(),workspace=tmp_path)
    service.management=Management(service)
    SettingsStore(service.data_dir).update(tmp_path,'global',lambda settings:settings.update(config={'providers':[
        {'id':'fixture','module':'provider-fixture','config':{'default_model':'first'}}]}, routing={'matrix':'balanced'}))
    async def probe(self,action,args,workspace):
        return {'models':[{'id':'first'}],'modelsProviderId':args.get('id')}
    monkeypatch.setattr(SetupManager,'probe',probe)
    for i in range(20):
        row=service._new_session({'title':str(i),'workspace':str(tmp_path)})
        service.state['sessions'].append(row)
    service.state['selectedSessionId']=service.state['sessions'][0]['id']
    yield service
    service.runtime.finish.set()
    await service.close()


async def settled(app):
    while tasks:=[task for task in app.tasks if not task.done()]:
        results=await asyncio.gather(*tasks,return_exceptions=True)
        for result in results:
            if isinstance(result,Exception):raise result


def snapshots(app,monkeypatch):
    values=[];original=app._publish
    def publish(**scope):
        original(**scope);values.append(copy.deepcopy(app.state))
    monkeypatch.setattr(app,'_publish',publish)
    return values


def provider_args(model='second'):
    return {'id':'fixture','module':'provider-fixture','config':{'default_model':model},'scope':'global'}


async def test_provider_save_is_bounded_for_twenty_unmounted_chats(app,monkeypatch):
    values=snapshots(app,monkeypatch)
    await app.management.command('providers.save',provider_args(),'save')
    assert len(values)==2
    final=values[-1]
    assert final['managementResults']['save']=={'phase':'ready','error':None}
    assert final['setup']['operations']['providers.save:fixture']['phase']=='ready'
    assert final['setup']['providers'][0]['config']['default_model']=='second'
    assert not any(row.get('configurationPending') or row.get('configurationBusy') for row in final['sessions'])
    assert app.runtime.stops==[]
    saved=load_saved_state(app.db)
    assert saved['managementResults']['save']==final['managementResults']['save']
    await settled(app)


@pytest.mark.parametrize('origin',['ui','agent'])
async def test_receipt_precedes_retirement_and_retries_do_not_repeat_save(app,monkeypatch,origin):
    sid=app.state['selectedSessionId'];app.runtime.workers[sid]=object()
    admin=await authorized_admin(app,sid,effects=['connections'],scopes=['global']) if origin=='agent' else None
    values=snapshots(app,monkeypatch)
    if origin=='agent':
        dispatch=lambda:admin('dispatch',{'action':'providers.save','args':provider_args(),'id':'save'})
    else:
        dispatch=lambda:app.dispatch('providers.save',provider_args(),command_id='save',include_state=False)
    await dispatch()
    await asyncio.wait_for(app.runtime.entered.wait(),1)
    assert app.state['managementResults']['save']['phase']=='ready'
    assert app._session(sid)['configurationBusy'] and app._session(sid)['configurationPending']
    saved=load_saved_state(app.db)
    assert saved['managementResults']['save']['phase']=='ready'
    with pytest.raises(AppError,match=r'\AApplying or reconciling conversation settings; new input must wait\.\Z') as blocked:
        await app.dispatch('conversation.send',{'sessionId':sid,'text':'Must not run'})
    assert blocked.value.status == 409
    assert (await dispatch())['duplicate']
    assert app.runtime.stops==[sid]
    app.runtime.finish.set();await settled(app)
    assert not app._session(sid)['configurationBusy'] and not app._session(sid)['configurationPending']
    assert any(row.get('managementResults',{}).get('save',{}).get('phase')=='ready' and row['sessions'][0]['configurationBusy'] for row in values)


async def test_invalidation_preserves_active_workers_and_blocked_ownership(app):
    rows=app.state['sessions'][:4]
    rows[0]['status']='working'
    rows[1]['workers']=[{'id':'child','persistent':True,'status':'idle'}]
    rows[2]['ownership']={'status':'blocked'}
    rows[3]['historyManaged']=True
    for row in rows:app.runtime.workers[row['id']]=object()
    await app.management.command('providers.save',provider_args(),'save')
    await settled(app)
    assert app.runtime.stops==[]
    assert all(row['configurationPending'] for row in rows[:3])
    assert not rows[3].get('configurationPending')
    assert not any(row.get('configurationBusy') for row in rows)


async def test_idle_runtime_retirement_is_limited_to_three_workers(app):
    for row in app.state['sessions']:app.runtime.workers[row['id']]=object()
    await app.management.command('providers.save',provider_args(),'save')
    await asyncio.wait_for(app.runtime.entered.wait(),1)
    await asyncio.sleep(0)
    assert len(app.runtime.stops)==3
    assert app.state['managementResults']['save']['phase']=='ready'
    app.runtime.finish.set();await settled(app)
    assert len(app.runtime.stops)==20 and app.runtime.workers=={}


async def test_failed_retirement_keeps_pending_and_next_use_can_retry(app):
    sid=app.state['selectedSessionId'];app.runtime.workers[sid]=object()
    app.runtime.fail=True;app.runtime.finish.set()
    await app.management.command('providers.save',provider_args(),'save');await settled(app)
    row=app._session(sid)
    assert row['configurationPending'] and not row['configurationBusy']
    assert row['configurationRefresh']['phase']=='error'
    assert 'Private runtime detail' not in str(app.browser_state())
    app.runtime.fail=False
    await app.refresh_configuration(sid);await settled(app)
    assert not row['configurationPending'] and 'configurationRefresh' not in row


async def test_newer_configuration_is_not_cleared_by_older_retirement(app):
    sid=app.state['selectedSessionId'];app.runtime.workers[sid]=object()
    await app.management.command('providers.save',provider_args(),'first')
    await app.runtime.entered.wait()
    await app.management.command('providers.save',provider_args('third'),'second')
    assert app._session(sid)['configurationPendingRevision']==2
    app.runtime.finish.set();await settled(app)
    assert not app._session(sid)['configurationPending']
    assert app.runtime.stops==[sid]
    assert app.state['setup']['providers'][0]['config']['default_model']=='third'


async def test_unchanged_save_does_not_invalidate_and_key_rotation_does(app):
    await app.management.command('providers.save',provider_args('first'),'noop')
    assert app.state.get('configurationRevision',0)==0
    args={**provider_args('first'),'apiKey':'first-private-key'}
    await app.management.command('providers.save',args,'new-key')
    revision=app.state['configurationRevision']
    await app.management.command('providers.save',{**args,'apiKey':'rotated-private-key'},'rotated')
    assert app.state['configurationRevision']==revision+1
    assert 'private-key' not in str(app.browser_state())
    await settled(app)


def routing_args(model='first', **values):
    return {'name': 'fixture-routing', 'scope': 'global',
            'matrix': {'description': 'A preset', 'roles': {
                role: {'description': role, 'candidates': [
                    {'provider': 'fixture', 'model': model}]} for role in ('general', 'fast')}}, **values}


async def test_inactive_routing_save_does_not_retire_helpers(app):
    sid = app.state['selectedSessionId']
    app.runtime.workers[sid] = object()
    await app.management.command('routing.save', routing_args(), 'inactive')
    await settled(app)
    assert app.state['setup']['configurationChanged'] is False
    assert app.state.get('configurationRevision', 0) == 0
    assert not app.runtime.stops and app._session(sid)['status'] == 'idle'


async def test_global_routing_changes_only_workspaces_using_that_definition(app, tmp_path):
    manager = SetupManager(app.data_dir)
    workspace = str(tmp_path)
    # The editor uses balanced, another workspace uses the global preset, and
    # a third shadows that same name with a local definition.
    await manager.perform('routing.save', {**routing_args(), 'workspace': workspace})
    users = []
    for name in ('uses-global', 'shadows-global'):
        path = tmp_path / name; path.mkdir()
        await manager.perform('routing.use', {'workspace': str(path), 'scope': 'project', 'name': 'fixture-routing'})
        row = app._new_session({'workspace': str(path), 'title': name})
        app.state['sessions'].append(row)
        app.runtime.workers[row['id']] = object()
        users.append(row)
    await manager.perform('routing.save', {**routing_args('local', scope='local'), 'workspace': users[1]['workspace']})
    app.runtime.workers[app.state['selectedSessionId']] = object()
    app.runtime.finish.set()
    await app.management.command('routing.save', routing_args('second'), 'change-active-elsewhere')
    await settled(app)
    assert app.runtime.stops == [users[0]['id']]
    revision = app.state['configurationRevision']
    await app.management.command('routing.save', routing_args('second'), 'unchanged')
    await settled(app)
    assert app.state['configurationRevision'] == revision
    assert app.runtime.stops == [users[0]['id']]


async def test_bundle_selected_matrix_is_refreshed_without_settings_default(app, tmp_path):
    row = app._session()
    manager = SetupManager(app.data_dir)
    manager.store.update(tmp_path, 'global', lambda settings: settings.pop('routing', None))
    await manager.perform('routing.save', {**routing_args(), 'workspace': str(tmp_path)})
    plan = app.data_dir / 'sessions' / row['id'] / 'effective-configuration.json'
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(json.dumps({'hooks': [{'module': 'hooks-routing', 'config': {'default_matrix': 'fixture-routing'}}]}))
    app.runtime.workers[row['id']] = object()
    app.runtime.finish.set()
    await app.management.command('routing.save', routing_args('second'), 'bundle-default')
    await settled(app)
    assert app.runtime.stops == [row['id']]


async def test_saved_runtime_routing_override_is_not_mistaken_for_unused_matrix(app, tmp_path):
    row = app._session()
    plan = app.data_dir / 'sessions' / row['id'] / 'configuration.json'
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(json.dumps({'hooks': [{'module': 'hooks-routing', 'config': {'default_matrix': 'fixture-routing'}}]}))
    app.runtime.workers[row['id']] = object()
    app.runtime.finish.set()
    await app.management.command('routing.save', routing_args(), 'saved-plan-default')
    await settled(app)
    assert app.runtime.stops == [row['id']]


@pytest.mark.parametrize('problem', ['missing-workspace', 'broken-matrix'])
async def test_unrelated_unavailable_routing_owner_does_not_block_save(app, tmp_path, problem):
    other = tmp_path / 'unavailable'; other.mkdir()
    row = app._new_session({'workspace': str(other), 'title': 'Unavailable'})
    app.state['sessions'].append(row)
    if problem == 'missing-workspace':
        other.rmdir()
    else:
        matrix = other / '.amplifier' / 'routing' / 'balanced.yaml'
        matrix.parent.mkdir(parents=True)
        matrix.write_text('roles: [broken')
    app.runtime.workers[row['id']] = object()
    app.runtime.finish.set()
    await app.management.command('routing.save', routing_args(), 'valid-save')
    await settled(app)
    assert app.state['managementResults']['valid-save']['phase'] == 'ready'
    assert SetupManager(app.data_dir).matrix(str(tmp_path), 'fixture-routing')['roles']['general']['candidates'][0]['model'] == 'first'
    assert app.runtime.stops == [row['id']]


@pytest.mark.parametrize('status', ['idle', 'stopped', 'interrupted', 'error'])
async def test_configuration_retirement_preserves_conversation_lifecycle(app, status):
    row = app._session(); row['status'] = status
    app.runtime.workers[row['id']] = object()
    app.runtime.emit = app.on_runtime_event
    app.runtime.finish.set()
    await app.management.command('providers.save', provider_args(), 'save')
    await settled(app)
    assert row['status'] == status
    assert row['preparation']['status'] == 'cold'
    assert not row.get('configurationBusy') and not row.get('configurationPending')


async def test_cancel_during_operation_has_terminal_receipt(app,monkeypatch):
    entered=asyncio.Event()
    async def perform(*args,**kwargs):
        entered.set();await asyncio.Event().wait()
    monkeypatch.setattr(app.management,'perform',perform)
    task=asyncio.create_task(app.management.command('providers.save',provider_args(),'cancel'))
    await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert app.state['managementResults']['cancel']['phase']=='error'
    assert app.state['setup']['operations']['providers.save:fixture']['phase']=='error'
    assert not app.management.lock.locked()


async def test_stale_provider_list_does_not_overwrite_saved_rows(app,monkeypatch):
    original=SetupManager.perform;entered=asyncio.Event();finish=asyncio.Event()
    async def perform(self,action,args):
        result=await original(self,action,args)
        if action=='providers.list':entered.set();await finish.wait()
        return result
    monkeypatch.setattr(SetupManager,'perform',perform)
    old=asyncio.create_task(app.management.command('providers.list',{},'old'))
    await entered.wait()
    await app.management.command('providers.save',provider_args(),'new')
    finish.set();await old;await settled(app)
    assert app.state['setup']['providers'][0]['config']['default_model']=='second'
    assert app.state['managementResults']['old']['phase']=='ready'


async def test_cancel_before_queued_status_still_records_error(app):
    await app.management.lock.acquire();await app.lock.acquire()
    task=asyncio.create_task(app.management.command('providers.save',provider_args(),'queued-cancel'))
    await asyncio.sleep(0);task.cancel();app.lock.release()
    try:
        with pytest.raises(asyncio.CancelledError):await task
        assert app.state['managementResults']['queued-cancel']['phase']=='error'
    finally:app.management.lock.release()


async def test_restart_reconciles_pending_idle_configuration(app):
    row=app._session();row.update(configurationPending=True,configurationPendingRevision=4,
        configurationRefresh={'phase':'pending','revision':4})
    app.management=Management(app)
    await settled(app)
    assert not row['configurationPending'] and not row.get('configurationBusy')
