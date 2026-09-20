from amplifier_web.execution import ensure_turn,ingest,finish

def test_nested_usage_rolls_up_once_and_repeated_completion_dedupes():
    session={};ensure_turn(session,'turn','request')
    nodes=[{'id':'tool','kind':'tool'},{'id':'worker','kind':'worker','parentId':'tool'},{'id':'nested-tool','kind':'tool','parentId':'worker'},{'id':'llm','kind':'llm','parentId':'nested-tool','usage':{'inputTokens':100,'outputTokens':20,'totalTokens':120,'costUsd':.003,'costType':'reported'}}]
    for event in nodes:ingest(session,event)
    ingest(session,nodes[-1])
    assert len(session['execution']['nodes'])==4
    for node in session['execution']['nodes']:
        assert node['aggregateUsage']['calls']==1
        assert node['aggregateUsage']['costUsd']==.003
    assert session['execution']['turns'][0]['aggregateUsage']['totalTokens']==120
    finish(session);assert session['execution']['turns'][0]['phase']=='completed'

def test_late_worker_usage_stays_on_original_turn_and_unknown_cost_is_explicit():
    session={};ensure_turn(session,'one');ingest(session,{'id':'w','kind':'worker','turnId':'one'})
    ensure_turn(session,'two');ingest(session,{'id':'call','kind':'llm','parentId':'w','usage':{'inputTokens':3,'outputTokens':2}})
    assert session['execution']['nodes'][-1]['turnId']=='one'
    one,two=session['execution']['turns']
    assert one['aggregateUsage']['calls']==1 and two['aggregateUsage']['calls']==0
    assert one['aggregateUsage']['costType']=='unavailable'
    assert one['aggregateUsage']['unknownCalls']==1


def test_voice_work_positions_are_persisted_and_do_not_follow_later_messages():
    from amplifier_web.execution import anchor_turns
    session={'messages':[{'id':'user','role':'user','createdAt':10},{'id':'ack','role':'assistant','createdAt':11}],
             'execution':{'turns':[{'id':'voice-delegate','startedAt':12},{'id':'voice-second','startedAt':13}], 'nodes':[]}}
    anchor_turns(session)
    assert [t['anchorMessageId'] for t in session['execution']['turns']]==['ack','ack']
    session['messages'].extend([{'id':'later','role':'user','createdAt':20},{'id':'late-transcript','role':'user','createdAt':11.5}])
    anchor_turns(session)
    assert [t['anchorMessageId'] for t in session['execution']['turns']]==['ack','ack']


def test_typed_turns_match_input_ids_and_work_without_messages_stays_at_start():
    session={'messages':[{'id':'u','role':'user','inputId':'typed','createdAt':10},{'id':'other','role':'user','createdAt':15}],
             'execution':{'turns':[{'id':'typed','startedAt':20},{'id':'early','startedAt':1}], 'nodes':[]}}
    from amplifier_web.execution import anchor_turns
    anchor_turns(session)
    assert [t['anchorMessageId'] for t in session['execution']['turns']]==['u',None]
    ensure_turn(session,'new-voice')
    assert session['execution']['turns'][-1]['anchorMessageId']=='other'


def test_pending_usage_settles_without_double_counting_or_inventing_cost():
    session={};ensure_turn(session,'turn')
    call={'id':'call','kind':'llm','phase':'running','sessionId':'root','rootSessionId':'root'}
    ingest(session,call)
    usage=session['execution']['turns'][0]['aggregateUsage']
    assert usage['calls']==usage['tokenPendingCalls']==usage['costPendingCalls']==1
    assert usage['pricedCalls']==0
    ingest(session,{**call,'phase':'completed','endedAt':10,'usage':{'inputTokens':12,'outputTokens':3,'totalTokens':15}})
    usage=session['execution']['turns'][0]['aggregateUsage']
    assert usage['calls']==1 and usage['totalTokens']==15
    assert usage['tokenPendingCalls']==usage['costPendingCalls']==0
    assert usage['unknownCalls']==1 and usage['costType']=='unavailable'
    ingest(session,{**call,'phase':'completed','endedAt':10,'usage':{'totalTokens':15,'costUsd':.003,'costType':'estimated'}})
    assert session['execution']['aggregateUsage']['costType']=='estimated'
    assert session['execution']['aggregateUsage']['calls']==1


def test_root_stop_settles_dangling_calls_but_retains_independent_worker_lifecycles():
    session={};ensure_turn(session,'turn')
    for identity,kind,sid in [('root-call','llm','root'),('worker','worker','child'),('child-call','llm','child')]:
        ingest(session,{'id':identity,'kind':kind,'phase':'running','sessionId':sid,'rootSessionId':'root','startedAt':1})
    finish(session,'stopped')
    root,worker,child=session['execution']['nodes']
    assert root['phase']=='stopped' and root['endedAt']>=1
    assert worker['phase']==child['phase']=='running'
    assert session['execution']['aggregateUsage']['costPendingCalls']==1
    assert session['execution']['turns'][0]['endedAt']>=1


def test_model_payloads_never_enter_public_tree():
    session={};ensure_turn(session,'turn')
    ingest(session,{'id':'model','kind':'llm','phase':'completed','input':'private prompt','output':'private response','error':'private trace'})
    assert all(field not in session['execution']['nodes'][0] for field in ('input','output','error'))


def test_process_exit_cannot_settle_another_process_background_call_or_new_turn():
    from amplifier_web.execution import finish_background
    session={};ensure_turn(session,'old')
    ingest(session,{'id':'old-name','kind':'llm','lifecycle':'background','phase':'running','sessionId':'root','rootSessionId':'root','startedAt':1})
    ensure_turn(session,'new')
    ingest(session,{'id':'new-name','kind':'llm','lifecycle':'background','phase':'running','sessionId':'root','rootSessionId':'root','startedAt':2})
    finish_background(session,['old-name'],'interrupted')
    old,new=session['execution']['nodes']
    assert old['phase']=='interrupted' and old['endedAt']>=1
    assert new['phase']=='running' and not new.get('endedAt')
    assert session['execution']['turns'][1]['phase']=='running'
    assert session['execution']['aggregateUsage']['tokenPendingCalls']==1
