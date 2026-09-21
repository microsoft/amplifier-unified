import copy
import json
import pytest
from amplifier_web.browser_detail import page,project,read_text
from test_automatic_history import app_factory


def heavy(identity='chat'):
    return {'id':identity,'title':identity,'status':'working','sessionKind':'root','messages':[{'id':f'm{i}','role':'user' if i%2==0 else 'assistant','text':f'{i} '+('paragraph '*800)} for i in range(520)],'workers':[],'approvals':[],
      'execution':{'nodes':[{'id':f'n{i}','turnId':'turn','kind':'tool','label':'Tool','summary':f'{i} '+('result '*2000)} for i in range(400)],'turns':[{'id':'turn','aggregateUsage':{'totalTokens':10000,'costUsd':2}}],'aggregateUsage':{'totalTokens':10000,'costUsd':2}}}

async def test_selected_and_active_histories_are_bounded_with_complete_on_demand_access(app_factory):
    app=app_factory();await app.dispatch('session.create',{})
    first=app._session();first.update(heavy(first['id']))
    await app.dispatch('session.create',{});second=app._session();second.update(heavy(second['id']))
    app._publish();original=copy.deepcopy(app.state['sessions'])
    queue=app.subscribe()
    for panel in ('settings','appearance',None):
        response=await app.dispatch('view.update',{'patch':{'panel':panel}})
        assert len(json.dumps(response['state']))<950000
        assert len(json.dumps(queue.get_nowait()))<950000
    for session in app.browser_state()['sessions']:
        assert len(session['messages'])==60 and len(session['execution']['nodes'])==100
        assert session['execution']['aggregateUsage']['costUsd']==2
    for part,amount in [('messages',520),('nodes',400)]:
        read=[];before=None
        while True:
            result=page(first,part,before);read[0:0]=result['items']
            before=result['before']
            if before is None:break
        assert len(read)==amount and len({row['id'] for row in read})==amount
        assert read[0]['id']==('m0' if part=='messages' else 'n0')
    assert [row['messages'] for row in app.state['sessions']]==[row['messages'] for row in original]
    assert len(next(row for row in app.browser_state(session_id=first['id'])['sessions'] if row['id']==first['id'])['messages'])==520
    # Tool-result bursts keep every publication bounded, and urgent approvals
    # remain present in the next frame rather than waiting for a detail fetch.
    for index in range(20):
        await app.on_runtime_event('execution.event',{'sessionId':second['id'],'id':f'burst-{index}','turnId':'turn','kind':'tool','phase':'completed','summary':'burst result '*1000})
        assert len(json.dumps(queue.get_nowait()))<950000
    await app.on_runtime_event('approval.requested',{'sessionId':second['id'],'id':'permission','title':'Fixture approval'})
    assert next(row for row in queue.get_nowait()['sessions'] if row['id']==second['id'])['approvals'][0]['id']=='permission'
    app.unsubscribe(queue)

def test_text_chunks_use_content_identity_and_reject_stale_or_removed_records():
    session=heavy();view=project(session);row=view['messages'][0];ref=row['textDetail']
    text='';offset=0
    while True:
        result=read_text(session,{**ref,'offset':offset});text+=result['value'];offset=result['nextOffset']
        if offset is None:break
    assert text==session['messages'][460]['text']
    session['messages'][460]['text']='Changed'
    with pytest.raises(ValueError,match='changed'):read_text(session,ref)
    with pytest.raises(ValueError,match='history changed'):page(session,'messages','missing')
    assert page(session,'messages')['userOffset']==230

def test_active_native_chat_is_bounded_after_runtime_materializes_history():
    session={**heavy(),'nativeProject':'project','historyManaged':False}
    assert len(project(session)['messages'])==60
    # Passive native browsing keeps its existing page/scroll protocol.
    session.update(historyManaged=True,messages=session['messages'][:100],sharedHistoryOffset=420)
    view=project(session)
    assert len(view['messages'])==100 and 'messageWindow' not in view
    assert view['sharedHistoryOffset']==420


def test_tool_fields_remain_bounded_and_independently_readable():
    session={'id':'chat','messages':[],'execution':{'turns':[{'id':'turn'}],'nodes':[{'id':'tool','kind':'tool','turnId':'turn','input':'i'*18000,'output':'o'*50000,'error':'e'*6000}]}}
    view=project(session);node=view['execution']['nodes'][0]
    assert len(json.dumps(view))<3000
    for field in ('input','output','error'):
        assert len(node[field])==512
        ref=node[field+'Detail'];text='';offset=0
        while True:
            result=read_text(session,{**ref,'offset':offset});text+=result['value'];offset=result['nextOffset']
            if offset is None:break
        assert text==session['execution']['nodes'][0][field]


def test_new_running_turn_is_visible_before_the_first_execution_node():
    session={'id':'chat','messages':[],'execution':{'turns':[{'id':'empty','phase':'running','startedAt':10}],'nodes':[]}}
    view=project(session)
    assert view['execution']['turns'][0]['id']=='empty'
    assert view['execution']['turns'][0]['nodeCounts']=={'tools':0,'workers':0}


def test_segment_totals_cover_all_calls_when_actions_are_paged():
    session={'id':'chat','messages':[{'id':'question','role':'user','createdAt':1},
              {'id':'interim','role':'assistant','createdAt':150}],
             'execution':{'turns':[{'id':'turn','anchorMessageId':'question'}],
              'nodes':[{'id':str(i),'turnId':'turn','kind':'llm','startedAt':i+2,'endedAt':i+3,'phase':'completed',
                        'usage':{'inputTokens':10,'outputTokens':2,'totalTokens':12,'costUsd':.01}} for i in range(200)]}}
    view=project(session);assert len(view['execution']['nodes'])==100
    groups=view['execution']['segments'];assert len(groups)==2
    assert groups[0]['nodeCounts']=={'tools':0,'models':148}
    assert groups[1]['nodeCounts']=={'tools':0,'models':52}
    assert sum(group['aggregateUsage']['totalTokens'] for group in groups)==2400
    assert view['execution']['nodes'][0]['anchorMessageId']=='question'
    assert view['execution']['nodes'][-1]['anchorMessageId']=='interim'
    earlier=page(session,'nodes',view['executionWindow']['before'])
    assert earlier['segments']==groups[:1]


def test_group_summary_keeps_failures_visible_and_unknown_completion_honest():
    session={'id':'chat','messages':[],'execution':{'turns':[{'id':'turn'}],
             'nodes':[{'id':'one','turnId':'turn','kind':'tool','phase':'error','startedAt':1,'endedAt':2}]}}
    assert project(session)['execution']['segments'][0]['phase']=='error'
    node=session['execution']['nodes'][0];node.update(phase='recorded');node.pop('endedAt')
    assert project(session)['execution']['segments'][0]['phase']=='recorded'
