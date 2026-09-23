"""Reproduced issue boundaries, with disposable state and no model or GitHub calls."""
import asyncio
import json
import pytest
from amplifier_web.service import AppService
from amplifier_web.runtime import normalize_event
from test_feedback import github, settle, payload

async def test_new_install_follows_device_but_saved_choice_survives(tmp_path):
    app=AppService(tmp_path,workspace=tmp_path)
    assert app.state['view']['scheme']=='system'
    await app.dispatch('view.update',{'patch':{'scheme':'dark'}})
    await app.close()
    restored=AppService(tmp_path,workspace=tmp_path)
    assert restored.state['view']['scheme']=='dark'
    await restored.close()

async def test_delayed_admission_does_not_block_selection_or_clear_other_draft(tmp_path):
    entered,release=asyncio.Event(),asyncio.Event()
    class Runtime:
        async def send(self,*args):entered.set();await release.wait()
        async def close(self):pass
    app=AppService(tmp_path,Runtime(),workspace=tmp_path)
    try:
        await app.dispatch('session.create',{'title':'Other'})
        other=app.state['selectedSessionId']
        await app.dispatch('view.update',{'patch':{'draft':'Other draft'}})
        await app.dispatch('session.create',{'title':'Sending'})
        first=app.state['selectedSessionId']
        await app.dispatch('view.update',{'patch':{'draft':'Submitted'}})
        send=asyncio.create_task(app.dispatch('conversation.send',{'sessionId':first,'text':'Submitted'},command_id='one-send'))
        await asyncio.wait_for(entered.wait(),1)
        await asyncio.wait_for(app.dispatch('session.select',{'id':other}),1)
        assert app.state['view']['draft']=='Other draft'
        # Even a late draft save explicitly belongs to its original conversation.
        await app.dispatch('view.update',{'sessionId':first,'patch':{'draft':'Submitted'}})
        assert app.state['view']['draft']=='Other draft'
        release.set();await send
        assert app.state['selectedSessionId']==other
        assert app.state['view']['draft']=='Other draft'
        assert app._session(first)['draft']==''
        assert len(app._session(first)['messages'])==1
    finally:release.set();await app.close()
    restored=AppService(tmp_path,workspace=tmp_path)
    await restored.dispatch('session.select',{'id':other})
    assert restored.state['view']['draft']=='Other draft'
    await restored.close()

async def test_error_dismissal_keeps_details_and_new_occurrence_is_unread(tmp_path):
    app=AppService(tmp_path,workspace=tmp_path)
    try:
        await app.dispatch('session.create',{})
        event={'sessionId':app.state['selectedSessionId'],'error':'Fixture problem'}
        await app.on_runtime_event('runtime.error',event)
        item=app.state_context()['attention']['items'][0]
        await app.dispatch('attention.read',{'ids':[item['id']],'fingerprints':{item['id']:item['fingerprint']}})
        assert app.state_context()['attention']['items'][0]['read']
        assert app._session()['error']=='Fixture problem'
        await app.on_runtime_event('runtime.error',event)
        latest=app.state_context()['attention']['items'][0]
        assert not latest['read'] and latest['fingerprint']!=item['fingerprint']
        await app.dispatch('attention.read',{'ids':[item['id']],'fingerprints':{item['id']:item['fingerprint']}})
        assert not app.state_context()['attention']['items'][0]['read']
    finally:await app.close()

@pytest.mark.parametrize('event,expected',[('job.returned','completed'),('job.cancelled','cancelled'),('job.failed','error')])
def test_definitive_job_event_has_canonical_status(event,expected):
    assert normalize_event({'type':event,'status':'returned','job_id':'job','call_id':'call'},'parent')[1]['status']==expected

async def test_diagnostics_default_and_acceptance_snapshot_exclude_private_content(tmp_path,github):
    app=AppService(tmp_path,workspace=tmp_path)
    try:
        await app.dispatch('session.create',{'title':'PRIVATE TITLE'})
        app._session()['messages']=[{'id':'m','role':'user','text':'PRIVATE MESSAGE'}]
        app._session()['error']='PRIVATE ERROR'
        app.state['devices']={'client':{'online':True,'url':'PRIVATE URL','controls':[{'value':'PRIVATE INPUT'}]}}
        args=payload(deviceDiagnostics={'frontendVersion':'0.10.7','frontendBuild':'abcdef0123456789','browser':'Safari','browserVersion':'26.0','deviceOS':'macOS','width':1400,'height':900})
        del args['includeDiagnostics']
        app.feedback.accept(args)
        app.state['view']['scheme']='dark';app._session()['messages'].clear()
        await app.feedback.send(args['requestId'])
        body=github.call_args.args[1]
        facts=json.loads(body.split('```json\n')[1].split('\n```')[0])
        assert facts['conversation']['messages']==1
        assert facts['presentation']['appearance']=='system'
        assert facts['device']['frontendVersion']=='0.10.7'
        assert facts['reportedOnlineViews']==1
        assert 'PRIVATE' not in body and str(tmp_path) not in body
        await app.dispatch('feedback.submit',args);await settle(app)
        github.assert_awaited_once()
        with pytest.raises(Exception,match='Additional properties'):
            await app.dispatch('feedback.submit',{**args,'requestId':'bad-device','deviceDiagnostics':{'url':'PRIVATE URL'}})
    finally:await app.close()
