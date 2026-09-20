import asyncio
import copy
import json
from types import SimpleNamespace
import pytest
from amplifier_web.history_revision import rewind, receipt_path, recover_pending
from amplifier_web.host.storage import SessionStore
from amplifier_web.host.config import write_private
from amplifier_web.service import AppService, AppError
from test_session_fork import transcript


class Controls:
    def __init__(self, home, source, rows):
        self.home, self.source, self.rows = home, source, copy.deepcopy(rows)
        self.session = SimpleNamespace(session_id=source['id'])
        self.coordinator = SimpleNamespace(session_state={'goal': {'condition':'old goal'}},get=lambda name:self)
        self.store = SessionStore.for_app(home, source['workspace'])
        self.fail = False
    async def get_messages(self): return copy.deepcopy(self.rows)
    async def set_messages(self, rows): self.rows = copy.deepcopy(rows)
    def require_idle(self): pass
    def state_path(self): return self.home/'sessions'/self.source['id']/'control-state.json'
    async def checkpoint(self):
        if self.fail and self.rows != transcript():
            self.fail = False
            raise OSError('Fixture checkpoint failed')
        write_private(self.state_path(), json.dumps({'goal':self.coordinator.session_state['goal']}))
        self.store.save(self.source['id'], self.rows, {'preserve_system':True},preserve_system=True)


def source(tmp_path):
    return {'id':'edit-session','status':'idle','workspace':str(tmp_path),'bundle':'anchors','messages':[
        {'id':str(index),'role':role,'text':text} for index,(role,text) in enumerate([
            ('user','First user turn'),('assistant','First answer'),('user','Second user turn'),('assistant','Second answer')])]}


async def test_rewind_retains_tool_context_and_all_events_under_same_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    src=source(tmp_path);controls=Controls(tmp_path,src,transcript());await controls.checkpoint()
    events=controls.store.directory(src['id'])/'events.jsonl';events.write_text('original events\n')
    result=await rewind(controls,{'source':src,'messageId':'2','operationId':'edit-1'})
    assert controls.rows==transcript()[:6]
    assert [row['text'] for row in result['messages']]==['First user turn','First answer']
    assert controls.coordinator.session_state['goal'] is None
    assert controls.store.load(src['id'])[0]==transcript()[:6]
    assert events.read_text()=='original events\n'
    path=receipt_path(tmp_path,src['id'],'edit-1');saved=json.loads(path.read_text())
    assert saved['contextBefore']==transcript() and saved['phase']=='committed'
    assert path.stat().st_mode&0o777==0o600
    with pytest.raises(ValueError,match='already attempted'):
        await rewind(controls,{'source':src,'messageId':'2','operationId':'edit-1'})


async def test_failed_rewind_restores_context_and_goal(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path));src=source(tmp_path)
    controls=Controls(tmp_path,src,transcript());await controls.checkpoint();controls.fail=True
    with pytest.raises(OSError):await rewind(controls,{'source':src,'messageId':'2','operationId':'failed-edit'})
    assert controls.rows==transcript() and controls.coordinator.session_state['goal']=={'condition':'old goal'}
    assert controls.store.load(src['id'])[0]==transcript()
    assert json.loads(receipt_path(tmp_path,src['id'],'failed-edit').read_text())['phase']=='aborted'


@pytest.mark.parametrize('current,phase',[(transcript(),'aborted'),(transcript()[:6],'committed'),([{'role':'user','content':'New CLI work'}],'superseded')])
def test_crash_classification_does_not_overwrite_newer_owner_work(tmp_path,current,phase):
    src=source(tmp_path);store=SessionStore.for_app(tmp_path,tmp_path)
    store.save(src['id'],current,{'preserve_system':True},preserve_system=True)
    path=receipt_path(tmp_path,src['id'],'interrupted')
    write_private(path,json.dumps({'phase':'prepared','nativeBefore':transcript(),'nativeAfter':transcript()[:6]}))
    write_private(path.parent.parent/'pending-history-edit.json',json.dumps({'operationId':'interrupted'}))
    recover_pending(tmp_path,tmp_path,src['id'])
    assert store.load(src['id'])[0]==current
    assert json.loads(path.read_text())['phase']==phase
    assert not (path.parent.parent/'pending-history-edit.json').exists()


async def test_current_edit_is_one_generation_in_same_chat_and_blocks_competing_input(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path));entered=asyncio.Event();release=asyncio.Event()
    class Runtime:
        def __init__(self):self.inputs=[]
        async def start(self,session,emit):self.emit=emit
        async def control(self,sid,operation,args):
            assert operation=='history.edit';entered.set();await release.wait()
            controls=Controls(tmp_path,args['source'],transcript())
            result=await rewind(controls,args)
            await self.emit('history.revised',{'sessionId':sid,**result})
            self.inputs.append((sid,args['text'],args['operationId']))
            return result
        async def close(self):pass
    runtime=Runtime();app=AppService(tmp_path,runtime,workspace=tmp_path)
    await app.dispatch('session.create',{});src=app._session();identity=src['id'];src.update({**source(tmp_path),'id':identity})
    store=SessionStore.for_app(tmp_path,tmp_path);store.save(identity,transcript(),{'preserve_system':True},preserve_system=True)
    args={'sessionId':identity,'messageId':'2','text':'Revised question','mode':'current'}
    task=asyncio.create_task(app.dispatch('message.edit',args,command_id='edit-current'))
    await entered.wait()
    with pytest.raises(AppError,match='settings'):await app.dispatch('conversation.send',{'sessionId':identity,'text':'Competing input'})
    release.set();await task
    assert len(app.state['sessions'])==1 and app._session()['id']==identity
    assert [row['text'] for row in src['messages']]==['First user turn','First answer','Revised question']
    assert runtime.inputs==[(identity,'Revised question','edit-current')]
    await app.dispatch('message.edit',args,command_id='edit-current')
    assert len(runtime.inputs)==1
    await app.close()


async def test_revision_preserves_earlier_execution_and_reconciles_after_restart(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path))
    app=AppService(tmp_path,workspace=tmp_path)
    await app.dispatch('session.create',{})
    src=app._session();identity=src['id'];src.update({**source(tmp_path),'id':identity})
    src['messages'][0]['inputId']='first';src['messages'][2]['inputId']='second'
    src['execution']={'currentTurnId':'second','turns':[{'id':name,'inputId':name,'anchorMessageId':str(i),'phase':'completed'} for name,i in [('first',0),('second',2)]],
        'nodes':[{'id':name+'-tool','turnId':name,'kind':'tool'} for name in ['first','second']]}
    src['historyEdit']={'phase':'working','operationId':'restart-edit','messageId':'2','text':'Changed second','via':'chat','attachments':[]}
    controls=Controls(tmp_path,src,transcript());await controls.checkpoint()
    result=await rewind(controls,{'source':src,'messageId':'2','operationId':'restart-edit'})
    app._publish();await app.close()
    app=AppService(tmp_path,workspace=tmp_path)
    restored=app._session(identity)
    assert [row['text'] for row in restored['messages']]==['First user turn','First answer','Changed second']
    assert restored['messages'][-1]['delivery']['status']=='unknown'
    assert [row['id'] for row in restored['execution']['nodes']]==['first-tool']
    assert restored['sharedHistoryTotal']==3 and not restored['configurationBusy']
    assert 'restarted during this edit' in restored['error']
    assert controls.store.load(identity)[0]==transcript()[:6]  # Restart did not submit edited input.
    await app.close()


async def test_last_unconfirmed_message_can_be_edited_after_owned_idle_check(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path));src=source(tmp_path)
    src['messages']=src['messages'][:2]+[{'id':'missing','role':'user','text':'Never reached runtime','delivery':{'status':'unknown'}}]
    controls=Controls(tmp_path,src,transcript()[:6]);await controls.checkpoint()
    result=await rewind(controls,{'source':src,'messageId':'missing','operationId':'unconfirmed-edit'})
    assert [row['text'] for row in result['messages']]==['First user turn','First answer']
    assert controls.rows==transcript()[:6]
