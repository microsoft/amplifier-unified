import asyncio
import copy

from amplifier_web.service import AppService


async def test_completions_are_durable_scoped_and_fingerprint_acknowledged(tmp_path):
    app=AppService(tmp_path/'data',workspace=tmp_path)
    try:
        await app.dispatch('session.create',{'title':'First'})
        sid=app.state['selectedSessionId']
        workspace=app.state['selectedWorkspaceId']
        await app.dispatch('session.create',{'title':'Second'})
        event={'sessionId':sid,'event':'generation.finished','generation_id':'g1','input_ids':['input1'],'text':'Ready','active_job_ids':[]}
        await app.on_runtime_event('assistant.message',{'sessionId':sid,'text':'A partial response'})
        await app.on_runtime_event('runtime.status',{'sessionId':sid,'status':'idle'})
        assert app.get_state()['attention']['unread']==0
        await app.on_runtime_event('runtime.generation',{**event,'active_job_ids':['worker']})
        assert app.get_state()['attention']['unread']==0
        await app.on_runtime_event('runtime.generation',{**event,'sessionId':'child','rootSessionId':sid})
        assert app.get_state()['attention']['unread']==0
        await app.on_runtime_event('runtime.generation',event)
        attention=app.get_state()['attention'];item=attention['items'][0]
        assert attention['sessions']=={sid:1}
        assert attention['workspaces'][workspace]==attention['sections']['chats']==1
        assert attention['settingsUnread']==0
        # Selection is not a receipt that the user saw the response.
        await app.dispatch('session.select',{'id':sid},origin='agent')
        assert app.get_state()['attention']['unread']==1
        await app.app_bridge('dispatch',{'action':'attention.read','args':{'ids':[item['id']],'fingerprints':{item['id']:item['fingerprint']}}},sid)
        await app.on_runtime_event('runtime.generation',event)  # duplicate event
        await app.dispatch('session.rename',{'id':sid,'title':'Renamed'})
        assert app.get_state()['attention']['unread']==0
        await app.on_runtime_event('runtime.generation',{**event,'generation_id':'g2'})
        await app.dispatch('attention.read',{'ids':[item['id']],'fingerprints':{item['id']:item['fingerprint']}})
        assert app.get_state()['attention']['unread']==1  # stale UI ack cannot clear g2
        state=copy.deepcopy(app.get_state()['attention'])
    finally:await app.close()
    app=AppService(tmp_path/'data',workspace=tmp_path)
    try:
        assert app.get_state()['attention']==state
        items=app.get_state()['attention']['items']
        await app.dispatch('attention.read',{'ids':[i['id'] for i in items],'fingerprints':{i['id']:i['fingerprint'] for i in items}})
        assert app.get_state()['attention']['unread']==0
    finally:await app.close()


async def test_feedback_closes_on_durable_acceptance_and_result_is_visible_elsewhere(tmp_path,monkeypatch):
    from amplifier_web import feedback
    release=asyncio.Event()
    async def create_issue(*args):
        await release.wait()
        return feedback.ISSUES_URL+'/42'
    monkeypatch.setattr(feedback,'create_issue',create_issue)
    monkeypatch.setattr(feedback.shutil,'which',lambda _: '/fixture/gh')
    app=AppService(tmp_path,workspace=tmp_path)
    payload={'requestId':'feedback-test-123','title':'Test','body':'Body','category':'bug'}
    try:
        app.state['view'].update(panel='feedback',feedbackDraft={'pending':payload})
        result=await asyncio.wait_for(app.dispatch('feedback.submit',payload),1)
        assert result['accepted'] and app.state['view']['panel'] is None
        assert app.state['feedback']['requests'][0]['status'] in {'queued','sending'}
        assert app.db.execute('select count(*) from feedback_requests').fetchone()[0]==1
        await app.dispatch('view.update',{'patch':{'panel':'settings'}})
        release.set()
        await asyncio.gather(*list(app.tasks))
        attention=app.get_state()['attention']
        assert attention['sections']['feedback']==1 and attention['settingsUnread']==0
        assert attention['items'][0]['url']==feedback.ISSUES_URL+'/42'
        assert app.state['view']['panel']=='settings'
        await app.dispatch('attention.read',{'ids':[attention['items'][0]['id']]},origin='agent')
        assert app.get_state()['attention']['unread']==0
        await app.feedback.update(payload['requestId'],status='unknown',message='Check delivery')
        assert app.get_state()['attention']['unread']==1
    finally:await app.close()
