"""Settings receipts are bounded and do not await idle runtime retirement."""
import asyncio
import copy
import json

import pytest

from amplifier_web.management import Management
from amplifier_web.preferences import SettingsStore
from amplifier_web.service import AppService, AppError
from amplifier_web.setup import SetupManager


class Runtime:
    def __init__(self):
        self.workers={}
        self.stops=[]
        self.entered=asyncio.Event()
        self.finish=asyncio.Event()
        self.fail=False
    async def stop(self,identity):
        self.stops.append(identity)
        self.entered.set()
        await self.finish.wait()
        if self.fail:raise ValueError('Private runtime detail')
        self.workers.pop(identity,None)
    async def close(self):self.finish.set()


@pytest.fixture
async def app(tmp_path,monkeypatch):
    service=AppService(tmp_path/'app',Runtime(),workspace=tmp_path)
    service.management=Management(service)
    SettingsStore(service.data_dir).update(tmp_path,'global',lambda settings:settings.update(config={'providers':[
        {'id':'fixture','module':'provider-fixture','config':{'default_model':'first'}}]}))
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
    def publish():
        original();values.append(copy.deepcopy(app.state))
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
    saved=json.loads(app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
    assert saved['managementResults']['save']==final['managementResults']['save']
    await settled(app)


@pytest.mark.parametrize('origin',['ui','agent'])
async def test_receipt_precedes_retirement_and_retries_do_not_repeat_save(app,monkeypatch,origin):
    sid=app.state['selectedSessionId'];app.runtime.workers[sid]=object()
    values=snapshots(app,monkeypatch)
    if origin=='agent':
        dispatch=lambda:app.app_bridge('dispatch',{'action':'providers.save','args':provider_args(),'id':'save'},sid)
    else:
        dispatch=lambda:app.dispatch('providers.save',provider_args(),command_id='save',include_state=False)
    await dispatch()
    await asyncio.wait_for(app.runtime.entered.wait(),1)
    assert app.state['managementResults']['save']['phase']=='ready'
    assert app._session(sid)['configurationBusy'] and app._session(sid)['configurationPending']
    saved=json.loads(app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
    assert saved['managementResults']['save']['phase']=='ready'
    with pytest.raises(AppError,match='Applying conversation settings'):
        await app.dispatch('conversation.send',{'sessionId':sid,'text':'Must not run'})
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
