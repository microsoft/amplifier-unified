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
