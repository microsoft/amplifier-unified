from amplifier_web.execution import ensure_turn,ingest,finish

def test_detached_receipts_only_repair_exact_root_inputs_without_replacing_terminal_outcomes():
    import copy
    from amplifier_web.execution import generation_turns
    phases=['completed','error','failed','cancelled','interrupted','stopped','running']
    session={'id':'root','status':'stopped','execution':{'turns':[
        {'id':phase,'inputId':phase,'phase':phase,'endedAt':10} for phase in phases]+[
        {'id':'child-only','phase':'completed'}, {'id':'foreign-root','phase':'completed'},
        {'id':'missing-owner','phase':'completed'}, {'id':'untouched','phase':'completed'}]},
        'generations':[
            {'event':'generation.detached','sessionId':'root','rootSessionId':'root','input_ids':phases,'at':15},
            {'event':'generation.detached','sessionId':'child','rootSessionId':'root','input_ids':['child-only'],'at':15},
            {'event':'generation.detached','sessionId':'root','rootSessionId':'foreign','input_ids':['foreign-root'],'at':15},
            {'event':'generation.detached','input_ids':['missing-owner'],'at':15}]}
    original=copy.deepcopy(session)
    turns=generation_turns(session)
    assert turns[0]['phase']=='interrupted' and turns[0]['endedAt']==15
    assert turns[1:]==session['execution']['turns'][1:]
    assert session==original


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


def test_voice_turn_anchors_to_exact_accepted_user_before_later_acknowledgment():
    from amplifier_web.execution import anchor_turns
    session = {'messages': [
        {'id': 'spoken', 'role': 'user', 'voiceInputId': 'voice:call:request', 'text': 'Read the file.', 'createdAt': 10},
        {'id': 'unrelated', 'role': 'user', 'voiceInputId': 'voice:call:other', 'text': 'Read the file.', 'createdAt': 10.5},
        {'id': 'ack', 'role': 'assistant', 'text': 'On it.', 'createdAt': 11}],
        'execution': {'turns': [{'id': 'voice:call:request', 'inputId': 'voice:call:request', 'startedAt': 12}], 'nodes': []}}
    anchor_turns(session)
    assert session['execution']['turns'][0]['anchorMessageId'] == 'spoken'


def test_unmatched_voice_turn_keeps_saved_fallback_after_late_exact_binding():
    from amplifier_web.execution import anchor_turns
    session = {'messages': [
        {'id': 'spoken', 'role': 'user', 'voiceInputId': 'voice:call:other', 'text': 'Read the file.', 'createdAt': 10},
        {'id': 'ack', 'role': 'assistant', 'createdAt': 11}],
        'execution': {'turns': [{'id': 'voice:call:request', 'label': 'Read the file.', 'startedAt': 12}], 'nodes': []}}
    anchor_turns(session)
    assert session['execution']['turns'][0]['anchorMessageId'] == 'ack'
    session['messages'][0]['voiceInputId'] = 'voice:call:request'
    session['messages'].append({'id': 'later', 'role': 'user', 'createdAt': 20})
    anchor_turns(session)
    assert session['execution']['turns'][0]['anchorMessageId'] == 'ack'


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


def test_usage_breakdown_retains_metric_coverage_and_reasoning_without_double_counting():
    from amplifier_web.execution import rollup
    result = rollup([
        {'usage': {'inputTokens': 95, 'outputTokens': 10, 'cacheReadTokens': 80, 'cacheWriteTokens': 5, 'reasoningTokens': 4}},
        {'usage': {'inputTokens': 30, 'outputTokens': 2}},
    ])
    assert result['grossInputTokens'] == 130
    assert result['grossTotalTokens'] == 142
    assert result['reasoningTokens'] == 4
    assert result['metricKnownCalls']['inputTokens'] == 2
    assert result['metricKnownCalls']['cacheReadTokens'] == 1
    assert result['metricKnownCalls']['reasoningTokens'] == 1


def test_usage_refresh_does_not_multiply_history_reads_by_turn_count():
    from amplifier_web.execution import refresh_usage
    reads = []
    class ObservedCall(dict):
        def get(self, key, default=None):
            if key == 'turnId':
                reads.append(key)
            return super().get(key, default)
    turns = [{'id': str(i)} for i in range(300)]
    calls = [ObservedCall(id='call-' + str(i), kind='llm', turnId=str(i // 2),
                         usage={'inputTokens': 3, 'outputTokens': 2}) for i in range(600)]
    tree = {'turns': turns, 'nodes': calls}
    refresh_usage(tree)
    assert len(reads) <= len(calls) * 3
    assert tree['aggregateUsage']['calls'] == 600
    assert all(turn['aggregateUsage']['calls'] == 2 for turn in turns)
    assert all(turn['aggregateUsage']['grossTotalTokens'] == 10 for turn in turns)


def test_tool_output_updates_do_not_rescan_model_usage_but_reparenting_does():
    from amplifier_web.execution import refresh_usage
    reads = []
    class ObservedCall(dict):
        def get(self, key, default=None):
            if key == 'usage':
                reads.append(True)
            return super().get(key, default)
    session = {'execution': {'currentTurnId': 'turn', 'turns': [{'id': 'turn'}], 'nodes': [
        {'id': 'tool', 'kind': 'tool', 'turnId': 'turn'},
        {'id': 'other', 'kind': 'worker', 'turnId': 'turn'},
        *[ObservedCall(id=f'call-{n}', kind='llm', parentId='tool', turnId='turn',
                       usage={'inputTokens': n, 'outputTokens': 2}) for n in range(200)]]}}
    refresh_usage(session['execution'])
    before = session['execution']['aggregateUsage'].copy()
    reads.clear()
    for number in range(10):
        ingest(session, {'id': 'tool', 'kind': 'tool', 'phase': 'completed', 'output': f'Result {number}'})
    assert reads == []
    assert session['execution']['nodes'][0]['output'] == 'Result 9'
    assert session['execution']['aggregateUsage'] == before
    ingest(session, {'id': 'tool', 'kind': 'tool', 'parentId': 'other'})
    assert reads
    assert session['execution']['nodes'][1]['aggregateUsage'] == before


def test_new_leaf_tools_avoid_usage_rebuild_but_late_parents_reconcile(monkeypatch):
    import copy
    import amplifier_web.execution as execution
    session = {'execution': {'currentTurnId': 'turn', 'turns': [{'id': 'turn'}], 'nodes': [
        {'id': f'call-{n}', 'kind': 'llm', 'turnId': 'turn',
         'usage': {'inputTokens': n, 'outputTokens': 2}} for n in range(1000)]}}
    execution.refresh_usage(session['execution'])
    original = execution.refresh_usage
    rebuilds = []
    def refresh(tree):
        rebuilds.append(True)
        original(tree)
    monkeypatch.setattr(execution, 'refresh_usage', refresh)
    for n in range(20):
        execution.ingest(session, {'id': f'tool-{n}', 'kind': 'tool', 'turnId': 'turn'})
    assert rebuilds == []
    expected = copy.deepcopy(session['execution'])
    original(expected)
    assert session['execution'] == expected
    # A child can arrive before its worker: this must rebuild all ancestry.
    execution.ingest(session, {'id': 'call-0', 'parentId': 'late-worker'})
    rebuilds.clear()
    execution.ingest(session, {'id': 'late-worker', 'kind': 'worker', 'turnId': 'turn'})
    assert rebuilds
    assert session['execution']['nodes'][-1]['aggregateUsage']['calls'] == 1
