import asyncio
import unittest
from types import SimpleNamespace

from amplifier_web.execution_events import ExecutionEvents, public_usage
from amplifier_web.runtime import normalize_event


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_worker_calls_keep_original_turn_and_parent(self):
        emitted=[]
        events=ExecutionEvents('root',emitted.append)
        events.lifecycle({'type':'input.delivered','input_id':'turn-a'})
        events.hook('root','tool:pre',{'tool_call_id':'delegate-1','tool_name':'delegate','tool_input':{'secret':'private'}})
        events.lifecycle({'type':'child.updated','sessionId':'child','parentSessionId':'root','callId':'delegate-1','agent':'explorer','status':'running'})
        events.lifecycle({'type':'input.delivered','input_id':'turn-b'})
        async def call(sid, count):
            events.hook(sid,'llm:request',{'provider':'test','model':'fixture','raw':'private reasoning'})
            await asyncio.sleep(0)
            events.hook(sid,'llm:response',{'usage':{'input_tokens':count,'output_tokens':2,'cost_usd':'0.01'},'raw':'private response'})
        await asyncio.gather(call('child',3),call('root',7))
        rows=[row['event'] for row in emitted if row['event']['kind']=='llm' and row['event']['phase']=='completed']
        self.assertEqual(len(rows),2)
        child=next(row for row in rows if row['sessionId']=='child')
        self.assertEqual(child['turnId'],'turn-a')
        self.assertEqual(child['parentId'],'worker:child')
        self.assertEqual(events.children['child']['parentId'],'tool:root:delegate-1')
        self.assertEqual(events.usage()['usage']['totalTokens'],14)
        self.assertNotIn('private',str(emitted))
        normalized=normalize_event({'type':'execution.event','event':{**child,'raw':'private'}},'root')
        self.assertEqual(normalized[1]['rootSessionId'],'root')
        self.assertEqual(normalized[1]['sessionId'],'child')
        self.assertNotIn('raw',normalized[1])

    async def test_lifecycle_updates_do_not_double_count_cost(self):
        events=ExecutionEvents('root',lambda value:None)
        events.hook('root','llm:request',{})
        events.hook('root','provider:retry',{'error_message':'credential'})
        events.hook('root','llm:response',{'usage':{'input_tokens':3,'output_tokens':1}})
        self.assertEqual(events.usage()['calls'],1)
        self.assertEqual(events.usage()['usage']['costType'],'unavailable')
        self.assertIsNone(events.usage()['usage']['costUsd'])

    async def test_provider_boundary_pairs_hooks_dispatched_in_separate_tasks(self):
        events=ExecutionEvents('root',lambda value:None)
        events.lifecycle({'type':'input.delivered','input_id':'user-turn','source':'user'})
        events.lifecycle({'type':'input.delivered','input_id':'job-report','source':'live.job'})
        class Provider:
            def get_info(self):
                return SimpleNamespace(id='fixture',defaults={'model':'fixture'})
            async def complete(self,request,**kwargs):
                async def hook(name,data):events.hook('root',name,data)
                await asyncio.create_task(hook('llm:request',{}))
                await asyncio.sleep(0)
                await asyncio.create_task(hook('llm:response',{'usage':{'input_tokens':100,'output_tokens':2}}))
                return SimpleNamespace(usage={'input_tokens':100,'output_tokens':2,'cost_usd':'0.03'})
        provider=Provider()
        events.instrument_provider('root',provider)
        await asyncio.gather(provider.complete(SimpleNamespace(model=None)),provider.complete(SimpleNamespace(model='selected')))
        self.assertEqual(events.usage()['calls'],2)
        self.assertEqual(events.usage()['usage']['totalTokens'],204)
        self.assertTrue(all(row['turnId']=='user-turn' for row in events.nodes.values()))

    def test_invalid_usage_cannot_publish_nan_or_negative_counts(self):
        result=public_usage({'input_tokens':-3,'output_tokens':float('nan'),'cost_usd':'nan','secret':'no'})
        self.assertEqual(result,{'costType':'unavailable'})


def test_worker_report_and_tool_failure_are_visible_without_raw_payloads():
    events=ExecutionEvents('root',lambda event:None)
    events.hook('root','tool:pre',{'tool_call_id':'call','tool_name':'bash','tool_input':{'command':'ls -la','token':'private'}})
    assert 'ls -la' in events.nodes['tool:root:call']['input'] and 'private' not in str(events.nodes)
    events.hook('root','tool:post',{'tool_call_id':'call','tool_result':{'success':False,'error':{'message':'private'}}})
    assert events.nodes['tool:root:call']['phase']=='error'
    events.lifecycle({'type':'child.updated','sessionId':'child','status':'completed','report':'Completed the requested review.'})
    assert events.nodes['worker:child']['summary']=='Completed the requested review.'


def test_streaming_hook_result_survives_transport_and_browser_projection():
    """loop-streaming publishes `result`, unlike app-control's `tool_result`."""
    from amplifier_web.execution import ingest
    from amplifier_web.browser_detail import page, read_text
    events=ExecutionEvents('root',lambda event:None)
    arguments={'command':'python -m pytest -q','cwd':'/workspace'}
    events.hook('root','tool:pre',{'tool_call_id':'stream','tool_name':'bash','tool_input':arguments})
    events.hook('root','tool:post',{'tool_call_id':'stream','tool_name':'bash','tool_input':arguments,
        'result':{'success':False,'output':{'stdout':'line\n'*6000,'stderr':'test failed','returncode':1},'error':{'message':'test failed','authorization':'secret'}}})
    row=events.nodes['tool:root:stream']
    assert row['phase']=='error' and 'test failed' in row['error']
    assert 'python -m pytest' in row['summary']
    assert 'secret' not in row['output']
    session={'id':'root'}
    ingest(session,normalize_event({'type':'execution.event','event':row},'root')[1])
    projected=page(session,'nodes')['items'][0]
    assert 'outputDetail' in projected
    reference=projected['outputDetail'];offset=0;full=''
    while offset is not None:
        result=read_text(session,{**reference,'offset':offset});full+=result['value'];offset=result['nextOffset']
    assert full==row['output'] and 'returncode' in full


def test_post_only_streaming_event_retains_arguments_and_falsey_result():
    events=ExecutionEvents('root',lambda event:None)
    events.hook('root','tool:post',{'tool_call_id':'missed-pre','tool_name':'bash',
        'tool_input':{'command':'true'},'result':''})
    row=events.nodes['tool:root:missed-pre']
    assert 'true' in row['input'] and row['output']=='' and row['phase']=='completed'


def test_public_tool_details_redact_credentials_and_private_blocks(monkeypatch):
    monkeypatch.setenv('FIXTURE_API_KEY','unique-fixture-credential')
    events=ExecutionEvents('root',lambda event:None)
    events.hook('root','tool:pre',{'tool_call_id':'call','tool_name':'app','tool_input':{'action':'feedback.get','authorization':'Bearer hidden','nested':{'access_token':'also-hidden'},'url':'https://user:pass@example.test','text':'unique-fixture-credential'}})
    events.hook('root','tool:post',{'tool_call_id':'call','tool_result':{'success':False,'output':{'url':'https://github.com/example/repo/issues/2','content':[{'type':'text','text':'Public output'},{'type':'thinking','text':'protected thought'}]},'error':{'message':'Missing item','raw':'private dump'}}})
    row=events.nodes['tool:root:call']
    assert row['phase']=='error' and row['summary']=='Failed app · feedback.get'
    assert 'Public output' in row['output'] and 'https://github.com/example/repo/issues/2' in row['output']
    assert 'Missing item' in row['error']
    for forbidden in ('unique-fixture-credential','also-hidden','Bearer hidden','user:pass','protected thought','private dump'):
        assert forbidden not in str(row)
    normalized=normalize_event({'type':'execution.event','event':row},'root')[1]
    assert normalized['input']==row['input'] and normalized['output']==row['output']
    private=normalize_event({'type':'execution.event','event':{**row,'kind':'llm'}},'root')[1]
    assert all(key not in private for key in ('input','output','error'))


def test_public_tool_details_are_bounded_and_never_stringify_objects():
    from amplifier_web.execution_details import tool_detail,DETAIL_LIMIT
    class Private:
        def __str__(self):return 'must not appear'
    assert tool_detail(Private())=='[unsupported detail]'
    text=tool_detail({'output':'x'*200000})
    assert len(text)<DETAIL_LIMIT+100 and 'omitted' in text
    assert 'private content omitted' in tool_detail({'analysis':'private','nested':{'visibility':'hidden','text':'private'}})


def test_private_tool_block_cannot_leak_through_collapsed_purpose():
    events=ExecutionEvents('root',lambda event:None)
    events.hook('root','tool:pre',{'tool_call_id':'private','tool_name':'fixture','tool_input':{'visibility':'private','description':'Do not expose this description'}})
    assert 'Do not expose' not in str(events.nodes)


def test_usage_inspection_does_not_duplicate_large_tool_content():
    events=ExecutionEvents('root',lambda event:None)
    events.hook('root','tool:pre',{'tool_call_id':'call','tool_name':'fixture','tool_input':{'text':'x'*64000}})
    assert 'input' in events.nodes['tool:root:call']
    assert 'input' not in events.usage()['trace'][0]


def test_tool_text_redacts_userinfo_tokens_assignments_and_private_keys():
    from amplifier_web.execution_details import tool_detail
    result=tool_detail('https://a-secret-token@example.test/path TEAM_KEY=some-key token=another-token\n-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----')
    result += tool_detail('{"api_key":"json-string-credential"}')
    for forbidden in ('a-secret-token','some-key','another-token','private-material','json-string-credential'):
        assert forbidden not in result


def test_repeated_tool_completion_preserves_end_time_and_retry_clears_old_result(monkeypatch):
    events=ExecutionEvents('root',lambda event:None)
    monkeypatch.setattr('amplifier_web.execution_events.time.time',lambda:10)
    events.hook('root','tool:pre',{'tool_call_id':'call','tool_name':'fixture','tool_input':{}})
    monkeypatch.setattr('amplifier_web.execution_events.time.time',lambda:12)
    result={'tool_call_id':'call','tool_result':{'success':False,'error':'failed once'}}
    events.hook('root','tool:post',result)
    monkeypatch.setattr('amplifier_web.execution_events.time.time',lambda:20)
    events.hook('root','tool:post',result)
    assert events.nodes['tool:root:call']['endedAt']==12
    events.hook('root','tool:pre',{'tool_call_id':'call','tool_name':'fixture','tool_input':{}})
    row=events.nodes['tool:root:call']
    assert row['phase']=='running' and row['startedAt']==10
    assert all(row.get(key) is None for key in ('endedAt','output','error'))
    from amplifier_web.execution import ingest
    session={}
    ingest(session,{**row,'phase':'error','endedAt':12,'error':'failed once'})
    ingest(session,row)
    assert session['execution']['nodes'][0]['endedAt'] is None
    assert session['execution']['nodes'][0]['error'] is None


async def test_unwrappable_provider_hook_fallback_preserves_background_lifecycle():
    from amplifier_web.execution_events import CALL_PURPOSE
    events=ExecutionEvents('root',lambda event:None)
    events.turn_id='foreground'
    token=CALL_PURPOSE.set({'label':'Auxiliary call','turnId':'earlier','lifecycle':'background'})
    try:
        events.hook('root','llm:request',{'model':'fixture'})
        pending=next(iter(events.nodes.values()))
        assert pending['lifecycle']=='background' and pending['turnId']=='earlier'
        events.hook('root','llm:response',{'usage':{'input_tokens':2,'output_tokens':1}})
        assert events.nodes[pending['id']]['phase']=='completed'
        assert events.nodes[pending['id']]['lifecycle']=='background'
    finally:CALL_PURPOSE.reset(token)
