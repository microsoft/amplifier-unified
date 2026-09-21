import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web.event_log_view import EventIndex, EventLogView, event_path, read_field
from amplifier_web.browser_detail import page, read_text
from amplifier_web.session_projection import persist


def append(path, name, data, at=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        stream.write(json.dumps({'event': name, 'timestamp': at, 'data': {'session_id': 'native', **data}})+'\n')


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'shared'))
    session={'id':'app','runtimeSessionId':'native','workspace':str(tmp_path/'project'),
             'messages':[{'id':'user','role':'user','createdAt':1,'text':'Inspect it'}], 'workers':[]}
    return session, event_path(session,'native')


def test_canonical_body_exact_unredacted_untruncated_and_never_written(source,tmp_path):
    session,path=source
    arguments={'command':'show fixture','token':'user-owned-fixture-token','reasoning':'a literal tool argument'}
    result={'success':True,'output':'full line\n'*20000}
    append(path,'tool:pre',{'tool_call_id':'one','tool_name':'bash','tool_input':arguments})
    append(path,'tool:post',{'tool_call_id':'one','tool_name':'bash','result':result},11)
    before=path.read_bytes();stamp=path.stat().st_mtime_ns
    view=EventLogView(None);session['execution']=view.read(session)
    node=session['execution']['nodes'][0]
    assert 'user-owned-fixture-token' in node['input'] and 'a literal tool argument' in node['input']
    assert len(node['output'])==512 and node['outputDetail']['length']>64000
    reference=page(session,'nodes')['items'][0]['outputDetail'];full='';offset=0
    while offset is not None:
        chunk=read_text(session,{**reference,'offset':offset});full+=chunk['value'];offset=chunk['nextOffset']
    assert json.loads(full)==result
    persist(tmp_path/'app',{'sessions':[session]}, {})
    saved=json.loads((path.parent.parent/'unified/view.json').read_text())
    assert not saved['execution']['nodes']
    assert 'user-owned-fixture-token' not in json.dumps(saved)
    assert path.read_bytes()==before and path.stat().st_mtime_ns==stamp
    assert '_eventFields' not in page(session,'nodes')['items'][0]


def test_partial_append_and_replacement_rebuild_only_index(source):
    session,path=source
    append(path,'tool:pre',{'tool_call_id':'one','tool_input':{'command':'one'}})
    index=EventIndex(path,'native');assert index.refresh();assert len(index.rows())==1
    with path.open('a') as stream:stream.write('{"event":"tool:post",')
    index.refresh();assert index.rows()[0]['phase']=='running'
    with path.open('a') as stream:stream.write('"timestamp":12,"data":{"session_id":"native","tool_call_id":"one","result":"ok"}}\n')
    index.refresh();assert index.rows()[0]['output']=='ok'
    old=index.rows()[0]['_eventFields']['output']
    replacement=path.with_suffix('.new');append(replacement,'tool:post',{'tool_call_id':'two','result':'new'})
    replacement.replace(path);index.refresh();assert [r['toolCallId'] for r in index.rows()]==['two']
    with pytest.raises(ValueError,match='changed'):read_field(old)


def test_model_response_count_and_duplicate_app_evidence(source):
    _,path=source
    append(path,'provider:request',{'kind':'llm','id':'stable-id','sessionId':'native','model':'fixture','startedAt':10,'phase':'running'},10)
    append(path,'llm:request',{'model':'fixture','provider':'test'},10.1)
    append(path,'llm:response',{'model':'fixture','provider':'test','duration_ms':900,'usage':{'input_tokens':4,'output_tokens':2}},11)
    append(path,'llm:response',{'kind':'llm','id':'stable-id','sessionId':'native','model':'fixture','startedAt':10,'endedAt':11.1,'phase':'completed','usage':{'inputTokens':4,'outputTokens':2,'totalTokens':6}},11.1)
    index=EventIndex(path,'native');index.refresh()
    assert [row['id'] for row in index.rows()]==['stable-id']


def test_parallel_model_responses_without_ids_are_not_guessed_or_double_counted(source):
    _,path=source
    for at in (10,10.1):append(path,'llm:request',{'model':'fixture'},at)
    for at in (12,13):append(path,'llm:response',{'model':'fixture','duration_ms':1000,'usage':{'output_tokens':1}},at)
    index=EventIndex(path,'native');index.refresh();rows=index.rows()
    assert len(rows)==2 and all(row['phase']=='completed' for row in rows)
    assert [row['startedAt'] for row in rows]==[11,12]


def test_child_tools_keep_call_identity_and_parent(source):
    session,path=source
    append(path,'tool:pre',{'tool_call_id':'delegate','tool_name':'delegate','tool_input':{'agent':'helper'}})
    append(path,'delegate:agent_spawned',{'tool_call_id':'delegate','sub_session_id':'child','agent':'helper'},11)
    child=event_path(session,'child')
    append(child,'tool:post',{'session_id':'child','tool_call_id':'one','tool_name':'bash','result':'done'},12)
    tree=EventLogView(None).read(session)
    tool=next(row for row in tree['nodes'] if row.get('toolCallId')=='one')
    worker=next(row for row in tree['nodes'] if row['kind']=='worker')
    assert tool['parentId']==worker['id'] and worker['parentId']=='tool:native:delegate'
    assert tool['turnId']==worker['turnId']


def test_log_overrides_legacy_truncated_copy_and_preserves_stable_node_id(source):
    session,path=source
    session['execution']={'nodes':[{'id':'tool:app:one','sessionId':'app','toolCallId':'one','kind':'tool','turnId':'turn','input':'old truncated'}],
                          'turns':[{'id':'turn','anchorMessageId':'user'}]}
    append(path,'tool:post',{'tool_call_id':'one','tool_input':{'command':'current'},'result':False})
    tree=EventLogView(None).read(session);node,=tree['nodes']
    assert node['id']=='tool:app:one' and node['output']=='false' and 'current' in node['input']
    assert node['turnId']=='turn'


def test_app_metadata_receipt_cannot_clear_native_result_and_retry_resets_it(source):
    _,path=source
    append(path,'tool:pre',{'tool_call_id':'one','tool_input':{'command':'first'}},10)
    append(path,'tool:post',{'tool_call_id':'one','result':'done'},11)
    append(path,'tool:pre',{'tool_call_id':'one','kind':'tool','id':'tool:native:one','phase':'running'},12)
    index=EventIndex(path,'native');index.refresh()
    assert index.rows()[0]['output']=='done' and index.rows()[0]['phase']=='completed'
    append(path,'tool:pre',{'tool_call_id':'one','tool_input':{'command':'retry'}},13)
    index.refresh();node,=index.rows()
    assert node['phase']=='running' and node['startedAt']==13
    assert 'output' not in node and 'output' not in node['_eventFields']


def test_undated_native_transcript_uses_exact_call_associations(source):
    session,path=source
    transcript=[{'role':'user','content':'Check this'},
                {'role':'assistant','content':'Checking the first file.'},
                {'role':'assistant','content':[{'type':'tool_use','id':'first','name':'read_file','input':{}}]},
                {'role':'tool','tool_call_id':'first','content':'first result'},
                {'role':'assistant','content':'Now the second file.'},
                {'role':'assistant','content':[{'type':'tool_use','id':'second','name':'read_file','input':{}}]},
                {'role':'tool','tool_call_id':'second','content':'second result'},
                {'role':'user','content':'Check this'}]
    path.parent.parent.mkdir(parents=True)
    (path.parent.parent/'transcript.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in transcript))
    session['messages']=[{'id':str(i),'role':row['role'],'source':'native','nativeIndex':i,'createdAt':0,'timestampKnown':False,'text':row['content']}
                         for i,row in enumerate(transcript) if row['role'] in {'user','assistant'} and isinstance(row['content'],str)]
    for call,at in [('first',10),('second',11)]:
        append(path,'tool:pre',{'tool_call_id':call,'tool_input':{'path':call}},at)
        append(path,'tool:post',{'tool_call_id':call,'result':'done'},at+.5)
    tree=EventLogView(None).read(session);first,second=tree['nodes']
    assert first['turnId']==second['turnId']=='native-turn:0'
    assert first['anchorMessageId']=='1' and second['anchorMessageId']=='4'
    assert page({**session,'execution':tree},'nodes')['items'][1]['anchorMessageId']=='4'


@pytest.mark.parametrize('already_reconciled', [False, True])
@pytest.mark.parametrize('link', ['inputId', 'messageId', 'userMessageId'])
def test_native_association_keeps_live_input_turn_and_one_work_group(source, already_reconciled, link):
    session, path = source
    session.update(status='working', messages=[{'id':'user', 'role':'user', 'text':'Inspect it',
                                               'createdAt':1, 'nativeIndex':0, 'inputId':'input'}])
    transcript = [{'role':'user', 'content':'Inspect it'}]
    path.parent.parent.mkdir(parents=True)
    (path.parent.parent/'transcript.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in transcript))
    append(path, 'prompt:submit', {'prompt':'Inspect it'}, 2)
    call = {'id':'model', 'kind':'llm', 'sessionId':'app', 'rootSessionId':'app',
            'turnId':'input', 'phase':'running', 'startedAt':10, 'liveObservation':True,
            'revision':1, 'producerId':'worker', 'model':'fixture', 'provider':'test'}
    append(path, 'provider:request', call, 10)
    host = {'id':'input', 'inputId':'input', 'anchorMessageId':'user', 'startedAt':2, 'phase':'running'}
    if link != 'inputId':
        session['messages'][0].pop('inputId')
        host[link] = 'user'
    session['execution'] = {'currentTurnId':'input', 'turns':[host], 'nodes':[dict(call)]}
    if already_reconciled:
        session['execution']['nodes'][0]['turnId'] = 'native-turn:user'
        session['execution']['turns'].append({'id':'native-turn:user', 'anchorMessageId':'user',
                                              'canonicalHistory':True, 'phase':'completed'})
    view = EventLogView(None)
    for _ in range(3):
        session['execution'] = view.read(session)
        node, = session['execution']['nodes']
        assert node['turnId'] == 'input'
        projected = page(session, 'nodes')
        assert [turn['id'] for turn in projected['turns']] == ['input']
        assert [segment['id'] for segment in projected['segments']] == ['input@user']
        assert projected['segments'][0]['phase'] == 'running'
    from amplifier_web.execution import ingest
    completed = {**call, 'phase':'completed', 'endedAt':12, 'revision':2,
                 'usage':{'totalTokens':42, 'costUsd':.01, 'costType':'reported'}}
    ingest(session, completed)
    append(path, 'llm:response', completed, 12)
    session['execution'] = view.read(session)
    assert page(session, 'nodes')['segments'][0]['phase'] == 'completed'
    assert page(session, 'nodes')['segments'][0]['aggregateUsage']['totalTokens'] == 42


def test_exact_inputs_keep_repeated_prompts_and_interim_work_separate(source):
    session, path = source
    transcript = [{'role':'user', 'content':'Again'},
                  {'role':'assistant', 'content':'First update'},
                  {'role':'assistant', 'content':[{'type':'tool_use', 'id':'one', 'name':'bash', 'input':{}}]},
                  {'role':'tool', 'tool_call_id':'one', 'content':'done'},
                  {'role':'assistant', 'content':'Second update'},
                  {'role':'assistant', 'content':[{'type':'tool_use', 'id':'two', 'name':'bash', 'input':{}}]},
                  {'role':'tool', 'tool_call_id':'two', 'content':'done'},
                  {'role':'user', 'content':'Again'},
                  {'role':'assistant', 'content':[{'type':'tool_use', 'id':'three', 'name':'bash', 'input':{}}]},
                  {'role':'tool', 'tool_call_id':'three', 'content':'done'}]
    session['messages'] = [{'id':str(i), 'role':row['role'], 'text':row['content'], 'nativeIndex':i,
                            'timestampKnown':False, **({'inputId':f'input-{i}'} if row['role']=='user' else {})}
                           for i, row in enumerate(transcript) if isinstance(row['content'], str) and row['role']!='tool']
    session['execution'] = {'turns':[{'id':f'input-{i}', 'inputId':f'input-{i}', 'anchorMessageId':str(i),
                                      'startedAt':1, 'phase':'running'} for i in (0, 7)], 'nodes':[]}
    path.parent.parent.mkdir(parents=True)
    (path.parent.parent/'transcript.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in transcript))
    for at, call in enumerate(('one', 'two', 'three'), 10):
        append(path, 'tool:pre', {'tool_call_id':call, 'tool_input':{}}, at)
        append(path, 'tool:post', {'tool_call_id':call, 'result':'done'}, at+.5)
    view = EventLogView(None)
    for _ in range(3):
        session['execution'] = view.read(session)
        assert [node['turnId'] for node in session['execution']['nodes']] == ['input-0', 'input-0', 'input-7']
        projected = page(session, 'nodes')
        assert [segment['id'] for segment in projected['segments']] == ['input-0@1', 'input-0@4', 'input-7@7']
        assert len(projected['turns']) == 2


def test_shared_anchor_does_not_infer_an_input_turn(source):
    session, path = source
    session['messages'][0].update(nativeIndex=0)
    session['execution'] = {'turns':[{'id':f'voice:{i}', 'anchorMessageId':'user', 'phase':'completed'}
                                      for i in (1, 2)], 'nodes':[]}
    path.parent.parent.mkdir(parents=True)
    (path.parent.parent/'transcript.jsonl').write_text(json.dumps({'role':'user', 'content':'Inspect it'})+'\n'+
        json.dumps({'role':'assistant', 'content':[{'type':'tool_use', 'id':'one', 'name':'bash', 'input':{}}]})+'\n')
    append(path, 'tool:pre', {'tool_call_id':'one', 'tool_input':{}}, 10)
    append(path, 'tool:post', {'tool_call_id':'one', 'result':'done'}, 11)
    node, = EventLogView(None).read(session)['nodes']
    assert node['turnId'] == 'native-turn:user'


@pytest.mark.asyncio
async def test_background_reader_observes_external_append_without_runtime_capture(source):
    import asyncio
    session,path=source
    state={'sessions':[session],'selectedSessionId':session['id']};publications=[]
    service=SimpleNamespace(state=state,_state=state,clients=SimpleNamespace(records={}),closed=False,
                            queue_clients={'browser':None},queue_sessions={},
                            lock=asyncio.Lock(),_session=lambda identity:session,_publish=lambda:publications.append(1))
    view=EventLogView(service);view.start()
    try:
        append(path,'tool:post',{'tool_call_id':'external','tool_name':'bash','result':'from the CLI'})
        async with asyncio.timeout(3):
            while not session.get('execution',{}).get('nodes'):await asyncio.sleep(.02)
        assert session['execution']['nodes'][0]['output']=='from the CLI'
        assert publications
        assert not list(path.parent.glob('*.index*'))
    finally:
        service.closed=True;await view.close()


def test_duplicate_model_telemetry_matches_once_after_admission_delay(source):
    _,path=source
    for i in range(2):
        append(path,'provider:request',{'kind':'llm','id':f'stable-{i}','sessionId':'native','model':'fixture','startedAt':1,'phase':'running'},1)
        append(path,'llm:request',{'request_id':str(i),'model':'fixture'},10+i/10)
    for i in range(2):
        append(path,'llm:response',{'request_id':str(i),'model':'fixture','usage':{'input_tokens':4,'output_tokens':2}},12+i/10)
        append(path,'llm:response',{'kind':'llm','id':f'stable-{i}','sessionId':'native','model':'fixture','startedAt':1,'endedAt':12.05+i/10,'phase':'completed','usage':{'inputTokens':4,'outputTokens':2}},12.05+i/10)
    index=EventIndex(path,'native');index.refresh()
    assert [row['id'] for row in index.rows()]==['stable-0','stable-1']


def test_unfinished_historical_record_is_not_a_perpetually_running_call(source):
    session,path=source
    append(path,'tool:pre',{'tool_call_id':'one','tool_name':'bash','tool_input':{'command':'old'}})
    view=EventLogView(None);session['execution']=view.read(session)
    assert session['execution']['nodes'][0]['phase']=='recorded'
    session['status']='working'
    assert view.read(session)['nodes'][0]['phase']=='recorded'
    node=session['execution']['nodes'][0]
    node.update(liveObservation=True,phase='running')
    assert view.read(session)['nodes'][0]['phase']=='running'
    node.update(phase='interrupted',endedAt=12)
    assert view.read(session)['nodes'][0]['phase']=='interrupted'


def test_live_completion_usage_survives_delayed_log_flush(source):
    session,path=source
    append(path,'provider:request',{'id':'stable','kind':'llm','sessionId':'native','phase':'running','startedAt':10},10)
    session['execution']={'turns':[{'id':'turn','anchorMessageId':'user'}],
        'nodes':[{'id':'stable','kind':'llm','sessionId':'native','phase':'completed','startedAt':10,'endedAt':12,
                  'turnId':'turn','liveObservation':True,'usage':{'totalTokens':120,'costUsd':.004,'costType':'reported'}}]}
    node,=EventLogView(None).read(session)['nodes']
    assert node['phase']=='completed' and node['endedAt']==12
    assert node['usage']['totalTokens']==120 and node['usage']['costUsd']==.004


def test_native_model_keeps_observed_identity_across_repeated_reads(source):
    session,path=source
    append(path,'llm:request',{'model':'fixture'},10)
    append(path,'llm:response',{'model':'fixture','usage':{'input_tokens':4,'output_tokens':2}},12)
    session['execution']={'turns':[{'id':'turn','anchorMessageId':'user'}],
        'nodes':[{'id':'observed-stable-id','kind':'llm','sessionId':'native','model':'fixture','phase':'completed','startedAt':9.9,'endedAt':12.1,
                  'turnId':'turn','liveObservation':True,'usage':{'inputTokens':4,'outputTokens':2}}]}
    view=EventLogView(None)
    for _ in range(3):
        session['execution']=view.read(session)
        assert [row['id'] for row in session['execution']['nodes']]==['observed-stable-id']


def test_unmatched_legacy_history_is_preserved_when_log_is_incomplete(source,tmp_path):
    session,path=source
    legacy={'id':'legacy','kind':'tool','turnId':'old','input':'original saved input','output':'original saved result','phase':'completed'}
    session['execution']={'nodes':[legacy],'turns':[{'id':'old','anchorMessageId':'user'}]}
    append(path,'tool:post',{'tool_call_id':'new','result':'new log result'})
    session['execution']=EventLogView(None).read(session)
    persist(tmp_path/'app',{'sessions':[session]}, {})
    saved=json.loads((path.parent.parent/'unified/view.json').read_text())
    retained,=saved['execution']['nodes']
    assert retained['input']==legacy['input'] and retained['output']==legacy['output']


def test_model_request_is_lazy_complete_and_kept_with_stable_call(source, tmp_path):
    session, path = source
    raw = {'model':'fixture', 'instructions':'Owner request '*8000,
           'input':[{'role':'user','content':'Inspect the project'}], 'tools':[{'name':'read_file'}],
           'reasoning':{'effort':'high'}, 'max_output_tokens':1000}
    append(path, 'provider:request', {'kind':'llm','id':'stable-request','sessionId':'native',
           'model':'fixture','provider':'test','startedAt':9,'phase':'running'}, 9)
    append(path, 'llm:request', {'model':'fixture','provider':'test','raw':raw,'message_count':1}, 10)
    view = EventLogView(None)
    session['status'] = 'working'
    session['execution'] = view.read(session)
    node, = session['execution']['nodes']
    assert node['id'] == 'stable-request'
    assert 'request' not in node and node['requestInfo']['tool_count'] == 1
    assert node['requestInfo']['reasoning_effort'] == 'high'
    initial = page(session, 'nodes')['items'][0]
    assert len(json.dumps(initial)) < 2000 and 'Owner request' not in json.dumps(initial)
    assert '_eventFields' not in initial
    reference = initial['requestDetail']
    before = path.read_bytes()
    complete = read_text(session, {**reference, 'complete':'true'})
    assert json.loads(complete['value']) == raw and complete['nextOffset'] is None
    append(path, 'llm:response', {'model':'fixture','provider':'test','usage':{'input_tokens':5,'output_tokens':1}}, 12)
    append(path, 'llm:response', {'kind':'llm','id':'stable-request','sessionId':'native',
           'model':'fixture','provider':'test','startedAt':9,'endedAt':12.1,'phase':'completed',
           'usage':{'inputTokens':5,'outputTokens':1}}, 12.1)
    session['execution'] = view.read(session)
    node, = session['execution']['nodes']
    assert node['id'] == 'stable-request' and node['requestDetail']['id'] == node['id']
    assert json.loads(read_text(session,{**node['requestDetail'],'complete':'true'})['value']) == raw
    persist(tmp_path/'app', {'sessions':[session]}, {})
    saved = (path.parent.parent/'unified/view.json').read_text()
    assert 'Owner request' not in saved and 'requestDetail' not in saved
    assert path.read_bytes().startswith(before)


def test_raw_request_missing_is_not_fabricated_and_parallel_requests_not_misassigned(source):
    _, path = source
    append(path, 'llm:request', {'model':'fixture','message_count':7,'thinking_enabled':True}, 10)
    append(path, 'llm:response', {'model':'fixture','duration_ms':1000}, 11)
    index = EventIndex(path, 'native');index.refresh()
    node, = index.rows()
    assert node['requestInfo']['message_count'] == 7 and 'requestDetail' not in node
    for label, at in [('first',12),('second',12.1)]:
        append(path, 'llm:request', {'model':'parallel','raw':{'input':label}}, at)
    for at in (14,15):append(path,'llm:response',{'model':'parallel','duration_ms':1000},at)
    index.refresh()
    assert all('requestDetail' not in row for row in index.rows() if row.get('model') == 'parallel')


def test_raw_request_reference_detects_replaced_log(source):
    session, path = source
    append(path, 'llm:request', {'request_id':'request','model':'fixture','raw':{'input':'original'}}, 10)
    session['execution'] = EventLogView(None).read(session)
    reference = page(session,'nodes')['items'][0]['requestDetail']
    path.write_text(path.read_text().replace('original','replaced'))
    with pytest.raises(ValueError,match='changed'):
        read_text(session,{**reference,'complete':'true'})


def test_matching_model_names_on_different_providers_keep_own_request(source):
    _, path = source
    append(path,'provider:request',{'kind':'llm','id':'app','sessionId':'native','provider':'other','model':'shared','startedAt':10,'endedAt':12,'phase':'completed'},12)
    append(path,'llm:request',{'provider':'test','model':'shared','raw':{'input':'own request'}},10)
    append(path,'llm:response',{'provider':'test','model':'shared'},12)
    index=EventIndex(path,'native');index.refresh();rows=index.rows()
    assert len(rows)==2
    assert 'requestDetail' not in next(row for row in rows if row['id']=='app')
    assert next(row for row in rows if row.get('provider')=='test')['requestDetail']
