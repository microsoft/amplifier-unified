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


@pytest.mark.parametrize('mount_checkpoint', [False, True])
async def test_current_edit_is_one_generation_in_same_chat_and_blocks_competing_input(tmp_path,monkeypatch,mount_checkpoint):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path));entered=asyncio.Event();release=asyncio.Event()
    class Runtime:
        def __init__(self):self.inputs=[]
        async def start(self,session,emit):
            self.emit=emit
            if mount_checkpoint:
                store = SessionStore.for_app(tmp_path, tmp_path)
                rows, metadata = store.load(session['id'])
                for index, row in enumerate(rows):
                    row.setdefault('metadata', {})['_seq'] = index
                store.save(session['id'], rows, metadata, preserve_system=True)
        async def control(self,sid,operation,args):
            assert operation=='history.edit';entered.set();await release.wait()
            rows = SessionStore.for_app(tmp_path, tmp_path).load(sid)[0]
            controls=Controls(tmp_path,args['source'],rows)
            result=await rewind(controls,args)
            await self.emit('history.revised',{'sessionId':sid,**result})
            self.inputs.append((sid,args['text'],args['operationId']))
            return result
        async def close(self):pass
    runtime=Runtime();app=AppService(tmp_path,runtime,workspace=tmp_path)
    await app.dispatch('session.create',{});src=app._session();identity=src['id'];src.update({**source(tmp_path),'id':identity})
    store=SessionStore.for_app(tmp_path,tmp_path);store.save(identity,transcript(),{'preserve_system':True},preserve_system=True)
    if mount_checkpoint:
        from amplifier_web.automatic_history import revision
        from amplifier_web.session_files import project_slug
        src['nativeProject'] = project_slug(tmp_path)
        src['nativeRevision'] = revision(src)
    src.update(failure={'category':'old'},health={'failure':{'category':'old'}})
    args={'sessionId':identity,'messageId':'2','text':'Revised question','mode':'current'}
    task=asyncio.create_task(app.dispatch('message.edit',args,command_id='edit-current'))
    await entered.wait()
    with pytest.raises(AppError,match='settings'):await app.dispatch('conversation.send',{'sessionId':identity,'text':'Competing input'})
    release.set();await task
    assert len(app.state['sessions'])==1 and app._session()['id']==identity
    assert [row['text'] for row in src['messages']]==['First user turn','First answer','Revised question']
    assert 'failure' not in src and 'health' not in src
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


async def test_unconfirmed_tail_does_not_bypass_an_unreliable_compacted_boundary(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path));src=source(tmp_path)
    src['messages']=src['messages'][:2]+[{'id':'missing','role':'user','text':'Unconfirmed','delivery':{'status':'unknown'}}]
    rows=[{'role':'user','content':'Summary of earlier work'}]
    controls=Controls(tmp_path,src,rows);await controls.checkpoint()
    with pytest.raises(ValueError,match='boundary'):
        await rewind(controls,{'source':src,'messageId':'missing','operationId':'compacted-edit'})
    assert controls.rows==rows


def native_edit_source(tmp_path, rows):
    from amplifier_web.automatic_history import display_message, revision
    from amplifier_web.session_files import project_slug
    src = source(tmp_path)
    store = SessionStore.for_app(tmp_path, tmp_path)
    store.save(src['id'], rows, {'preserve_system': True}, preserve_system=True)
    src['nativeProject'] = project_slug(tmp_path)
    src['nativeRevision'] = revision(src)
    src['messages'] = [display_message(row, index, src) for index, row in enumerate(rows)]
    src['messages'] = [row for row in src['messages'] if row is not None]
    return src, store


async def test_recovery_edit_survives_mount_sequence_checkpoint(tmp_path, monkeypatch):
    from amplifier_web.session_store import capture_edit_context
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    rows = [{'role': 'user', 'content': 'Earlier request'},
            {'role': 'assistant', 'content': 'Earlier response'},
            {'role': 'user', 'content': 'Unanswered request'}]
    src, store = native_edit_source(tmp_path, rows)
    src['historyEditContextDigest'] = capture_edit_context(tmp_path, src)
    mounted = copy.deepcopy(rows)
    for index, row in enumerate(mounted):
        row['metadata'] = {'_seq': index}
    controls = Controls(tmp_path, src, mounted)
    await controls.checkpoint()  # Real context mounts assign sequence IDs.
    result = await rewind(controls, {'source': src, 'messageId': src['messages'][-1]['id'],
                                     'operationId': 'recovery-edit'})
    assert controls.rows == mounted[:2]
    assert store.load(src['id'])[0] == mounted[:2]
    assert [row['text'] for row in result['messages']] == ['Earlier request', 'Earlier response']
    receipt = json.loads(receipt_path(tmp_path, src['id'], 'recovery-edit').read_text())
    assert receipt['nativeBefore'] == mounted and receipt['phase'] == 'committed'


@pytest.mark.parametrize('change', ['content', 'input_identity', 'provider_data', 'append', 'reorder', 'missing_proof'])
async def test_recovery_edit_rejects_actual_history_changes(tmp_path, monkeypatch, change):
    from amplifier_web.session_store import capture_edit_context
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    rows = [{'role': 'user', 'content': 'Earlier request'},
            {'role': 'assistant', 'content': 'Earlier response'},
            {'role': 'user', 'content': 'Unanswered request'}]
    src, store = native_edit_source(tmp_path, rows)
    if change != 'missing_proof':
        src['historyEditContextDigest'] = capture_edit_context(tmp_path, src)
    mounted = copy.deepcopy(rows)
    for index, row in enumerate(mounted):
        row['metadata'] = {'_seq': index}
    if change == 'content': mounted[0]['content'] = 'Changed request'
    if change == 'input_identity': mounted[0]['metadata']['amplifier_input'] = {'id': 'different'}
    if change == 'provider_data': mounted[1]['provider_data'] = {'signature': 'different'}
    if change == 'append': mounted.append({'role': 'assistant', 'content': 'New response'})
    if change == 'reorder': mounted.reverse()
    controls = Controls(tmp_path, src, mounted)
    await controls.checkpoint()
    before = (store.directory(src['id']) / 'transcript.jsonl').read_bytes()
    with pytest.raises(ValueError, match='transcript changed'):
        await rewind(controls, {'source': src, 'messageId': src['messages'][-1]['id'],
                               'operationId': 'rejected-edit'})
    assert controls.rows == mounted
    assert (store.directory(src['id']) / 'transcript.jsonl').read_bytes() == before
    assert not receipt_path(tmp_path, src['id'], 'rejected-edit').exists()


def test_edit_snapshot_rejects_a_stale_view_before_worker_mount(tmp_path):
    from amplifier_web.session_store import capture_edit_context
    src, store = native_edit_source(tmp_path, [{'role': 'user', 'content': 'Original'}])
    store.save(src['id'], [{'role': 'user', 'content': 'Changed'}], {})
    with pytest.raises(ValueError, match='transcript changed'):
        capture_edit_context(tmp_path, src)
