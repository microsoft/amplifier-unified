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
