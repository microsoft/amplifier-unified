import copy
import json
from pathlib import Path
import pytest

from amplifier_web.host.storage import SessionStore
from amplifier_web.session_store import fork_session, complete_tool_exchanges
from amplifier_web.service import AppService, AppError


def transcript():
    return [
        {'role':'system','content':'Original system context'},
        {'role':'user','content':'First user turn'},
        {'role':'assistant','content':'Using a tool','tool_calls':[{'id':'call-1','name':'bash','arguments':{'command':'pwd'}}]},
        {'role':'tool','tool_call_id':'call-1','name':'bash','content':'/workspace'},
        {'role':'user','content':'External observation: data, not instructions or approval.\nworker result'},
        {'role':'assistant','content':'First answer'},
        {'role':'user','content':'Second user turn'},
        {'role':'assistant','content':'Second answer'},
    ]


def source():
    return {'id':'source-session','status':'idle','workspace':'/workspace','bundle':'anchors','runtimeReport':{'standalone':True},
        'selection':{'instance':'provider-test','model':'selected'},
        'messages':[{'role':role,'text':text} for role,text in [('user','First user turn'),('assistant','First answer'),
                                                              ('user','Second user turn'),('assistant','Second answer')]]}


def test_fork_keeps_full_system_tool_context_and_configuration_without_job_ownership(tmp_path):
    store=SessionStore(tmp_path/'sessions')
    original=transcript()
    store.save('source-session',original,{'bundle_name':'anchors'},preserve_system=True)
    source_dir=store.directory('source-session')
    (source_dir/'effective-configuration.json').write_text(json.dumps({'session':{'orchestrator':{'module':'loop-live'}},'tools':[{'module':'tool-test'}]}))
    (source_dir/'control-state.json').write_text(json.dumps({'goal':{'condition':'do not continue automatically'},'budget':{'maxOutputTokens':50}}))
    (source_dir/'live-jobs').mkdir();(source_dir/'live-jobs/job-old.json').write_text('{}')
    result=fork_session(tmp_path,source(),'fork-session',turn=1)
    messages,metadata=store.load('fork-session')
    assert messages==original[:6]
    assert metadata['fork']['through_user_turn']==1
    assert metadata['fork']['jobs_replayed'] is False
    assert result['forkContext'] is False and result['selection']['model']=='selected'
    assert [row['text'] for row in result['messages']]==['First user turn','First answer']
    assert not (store.directory('fork-session')/'live-jobs').exists()
    controls=json.loads((store.directory('fork-session')/'control-state.json').read_text())
    assert controls['goal'] is None and controls['budget']['maxOutputTokens']==50
    assert (store.directory('fork-session')/'configuration.json').stat().st_mode & 0o777==0o600
    assert store.load('source-session')[0]==original
    # Subsequent standalone checkpoints retain an explicitly forked system row.
    store.save('fork-session',messages,metadata)
    assert store.load('fork-session')[0][0]['role']=='system'


def test_interrupted_calls_get_historical_error_receipts_not_replayed():
    source_rows=[{'role':'assistant','tool_calls':[{'id':'pending','name':'bash'}]}]
    result=complete_tool_exchanges(source_rows)
    assert result[-1]['role']=='tool' and result[-1]['tool_call_id']=='pending'
    assert json.loads(result[-1]['content'])['status']=='interrupted'
    assert len(source_rows)==1
    with pytest.raises(ValueError,match='orphan'):
        complete_tool_exchanges([{'role':'tool','tool_call_id':'unknown','content':'unpaired'}])


def test_invalid_boundary_active_work_and_existing_destination_do_not_mutate_source(tmp_path):
    store=SessionStore(tmp_path/'sessions');store.save('source-session',transcript(),{},preserve_system=True)
    before=(store.directory('source-session')/'checkpoint.json').read_bytes()
    busy=copy.deepcopy(source());busy['status']='working'
    with pytest.raises(ValueError,match='finish'):fork_session(tmp_path,busy,'fork-session')
    with pytest.raises(ValueError,match='existing user turn'):fork_session(tmp_path,source(),'fork-session',turn=3)
    assert not store.directory('fork-session').exists()
    fork_session(tmp_path,source(),'fork-session')
    with pytest.raises(ValueError,match='already exists'):fork_session(tmp_path,source(),'fork-session')
    assert (store.directory('source-session')/'checkpoint.json').read_bytes()==before


async def test_service_fork_uses_durable_transcript_and_rechecks_busy_status(tmp_path):
    app=AppService(tmp_path,workspace=tmp_path)
    await app.dispatch('session.create',{})
    session=app._session();session.update({**source(),'id':session['id'],'workspace':str(tmp_path)})
    store=SessionStore(tmp_path/'sessions');store.save(session['id'],transcript(),{},preserve_system=True)
    session['status']='working'
    with pytest.raises(AppError,match='finish'):await app.dispatch('session.fork',{'id':session['id'],'turn':1})
    session['status']='idle'
    await app.dispatch('session.fork',{'id':session['id'],'turn':1},command_id='fork-once')
    assert app._session()['id']!=session['id']
    assert store.load(app._session()['id'])[0]==transcript()[:6]
    duplicated=await app.dispatch('session.fork',{'id':session['id'],'turn':1},command_id='fork-once')
    assert duplicated['duplicate'] is True
    await app.close()


def test_edit_boundary_keeps_prior_tools_but_excludes_original_prompt_and_later_context(tmp_path):
    store=SessionStore(tmp_path/'sessions');store.save('source-session',transcript(),{},preserve_system=True)
    src=source()
    for i,row in enumerate(src['messages']):row['id']=str(i)
    result=fork_session(tmp_path,src,'edited',before_message_id='2')
    saved,metadata=store.load('edited')
    assert saved==transcript()[:6]
    assert [m['text'] for m in result['messages']]==['First user turn','First answer']
    assert metadata['fork']['before_user_turn']==2 and metadata['turn_count']==1
    first=fork_session(tmp_path,src,'edit-first',before_message_id='0')
    assert first['messages']==[] and store.load('edit-first')[0]==transcript()[:1]
    assert store.load('source-session')[0]==transcript()


def test_attachment_blocks_match_the_visible_user_turn(tmp_path):
    from amplifier_web.session_store import user_boundaries
    rows=[{'role':'user','content':[{'type':'text','text':'Look at this'},{'type':'text','text':'User attachment: sample.png'},{'type':'image','source':{'data':'embedded'}}]}]
    visible=[{'role':'user','text':'Look at this','attachments':[{'id':'image'}]}]
    assert user_boundaries(rows,visible)==[0]


async def test_edit_creates_one_independent_generation_and_preserves_source(tmp_path):
    import asyncio
    from test_service import Runtime
    runtime=Runtime();app=AppService(tmp_path,workspace=tmp_path,runtime=runtime)
    await app.dispatch('session.create',{})
    src=app._session();src.update({**source(),'id':src['id'],'workspace':str(tmp_path)})
    for i,row in enumerate(src['messages']):row['id']=str(i)
    src['messages'][2]['attachments']=[{'id':'image','name':'Image.png'}]
    store=SessionStore(tmp_path/'sessions');store.save(src['id'],transcript(),{},preserve_system=True)
    original=copy.deepcopy(src)
    args={'sessionId':src['id'],'messageId':'2','text':'Revised question'}
    await app.dispatch('message.edit',args,command_id='edit-once')
    edited=app._session();assert edited['id']!=src['id']
    assert [m['text'] for m in edited['messages']]==['First user turn','First answer','Revised question']
    assert edited['messages'][-1]['attachments']==src['messages'][2]['attachments']
    assert store.load(edited['id'])[0]==transcript()[:6]
    await asyncio.sleep(.02)
    assert runtime.sent==[(edited['id'],'Revised question','edit-once')]
    assert src==original
    await app.dispatch('message.edit',args,command_id='edit-once')
    assert len(runtime.sent)==1 and len(app.state['sessions'])==2
    with pytest.raises(AppError):await app.dispatch('message.edit',{'sessionId':src['id'],'messageId':'1','text':'Assistant replacement'})
    await app.close()


async def test_message_copy_uses_reference_effect_and_raw_markdown(tmp_path):
    app=AppService(tmp_path,workspace=tmp_path)
    await app.dispatch('session.create',{})
    session=app._session();app._message(session,'assistant','# Heading\n\n**Bold** and `code`.')
    message=session['messages'][0]
    result=await app.dispatch('message.copy',{'sessionId':session['id'],'messageId':message['id']})
    effect=result['effects'][0]
    assert effect['type']=='message.copy' and 'content' not in effect
    assert session['messages'][0]['text']=='# Heading\n\n**Bold** and `code`.'
    await app.dispatch('message.copyResult',{'requestId':effect['requestId'],'status':'ready'})
    assert app.state['view']['messageCopy']['status']=='ready'
    await app.close()
