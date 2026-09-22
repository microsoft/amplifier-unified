import asyncio
import unittest
from types import SimpleNamespace

from amplifier_web.execution_events import ExecutionEvents, public_usage
from amplifier_web.runtime import normalize_event


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_compaction_calls_have_distinct_label_usage_and_no_public_stream(self):
        from amplifier_web.execution_events import CALL_PURPOSE
        events=ExecutionEvents('root',lambda value:None)
        events.turn_id='turn'
        class Provider:
            def get_info(self):return SimpleNamespace(id='openai',defaults={'model':'fixture'})
            async def complete(self,request):
                self.public_stream = not CALL_PURPOSE.get()
                return SimpleNamespace(usage={'input_tokens':20,'output_tokens':3})
            async def compact_context(self,request):
                self.public_stream = not CALL_PURPOSE.get()
                return {'message':{'opaque':'must not appear in diagnostics'},'usage':{'input_tokens':50,'output_tokens':5}}
        provider=events.instrument_provider('root',Provider())
        request=SimpleNamespace(model=None,metadata={'purpose':'context-compaction'})
        await provider.complete(request)
        self.assertFalse(provider.public_stream)
        await provider.compact_context(request)
        self.assertFalse(provider.public_stream)
        self.assertIsNone(CALL_PURPOSE.get())
        rows=list(events.nodes.values())
        self.assertEqual(len(rows),2)
        self.assertTrue(all(row['label']=='Context compaction' and row['turnId']=='turn' for row in rows))
        self.assertEqual(events.usage()['usage']['totalTokens'],78)
        self.assertNotIn('opaque',str(rows))
        await provider.complete(SimpleNamespace(model=None,metadata={}))
        self.assertTrue(provider.public_stream)
        self.assertEqual(list(events.nodes.values())[-1]['label'],'Model call')

    async def test_failed_compaction_restores_purpose_and_records_safe_failure(self):
        from amplifier_web.execution_events import CALL_PURPOSE
        events=ExecutionEvents('root',lambda value:None)
        class ContextLengthError(Exception):pass
        class Provider:
            def get_info(self):return SimpleNamespace(id='openai',defaults={'model':'fixture'})
            async def complete(self,request):raise ContextLengthError('secret raw request')
        provider=events.instrument_provider('root',Provider())
        with self.assertRaises(ContextLengthError):
            await provider.complete(SimpleNamespace(model=None,metadata={'purpose':'context-compaction'}))
        row=next(iter(events.nodes.values()))
        self.assertEqual(row['label'],'Context compaction')
        self.assertEqual(row['failure']['category'],'context_limit')
        self.assertNotIn('secret',str(row))
        self.assertIsNone(CALL_PURPOSE.get())

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


def test_worker_report_and_tool_failure_have_metadata_without_payload_copies():
    events=ExecutionEvents('root',lambda event:None)
    events.hook('root','tool:pre',{'tool_call_id':'call','tool_name':'bash','tool_input':{'command':'ls -la','token':'user data'}})
    events.hook('root','tool:post',{'tool_call_id':'call','tool_result':{'success':False,'error':'failure body'}})
    row=events.nodes['tool:root:call']
    assert row['phase']=='error' and row['liveObservation']
    assert all(key not in row for key in ('input','output','error','purpose'))
    events.lifecycle({'type':'child.updated','sessionId':'child','status':'completed','report':'Completed review.'})
    assert events.nodes['worker:child']['summary']=='Completed review.'


def test_streaming_hook_transports_lifecycle_without_capturing_alternate_content():
    events=ExecutionEvents('root',lambda event:None)
    arguments={'command':'python -m pytest -q','cwd':'/workspace'}
    events.hook('root','tool:pre',{'tool_call_id':'stream','tool_name':'bash','tool_input':arguments})
    events.hook('root','tool:post',{'tool_call_id':'stream','tool_name':'bash','tool_input':arguments,
        'result':{'success':False,'output':{'stdout':'line\n'*6000,'returncode':1}}})
    row=normalize_event({'type':'execution.event','event':events.nodes['tool:root:stream']},'root')[1]
    assert row['phase']=='error' and row['liveObservation']
    assert not any(key in row for key in ('input','output','error'))
    assert 'python -m pytest' not in str(row) and 'line' not in str(row)


def test_post_only_falsey_result_still_has_a_completed_lifecycle():
    events=ExecutionEvents('root',lambda event:None)
    events.hook('root','tool:post',{'tool_call_id':'missed-pre','tool_name':'bash','tool_input':{'command':'true'},'result':''})
    row=events.nodes['tool:root:missed-pre']
    assert row['phase']=='completed' and 'input' not in row and 'output' not in row


def test_usage_inspection_does_not_duplicate_large_tool_content():
    events=ExecutionEvents('root',lambda event:None)
    events.hook('root','tool:pre',{'tool_call_id':'call','tool_name':'fixture','tool_input':{'text':'x'*64000}})
    assert 'input' not in events.nodes['tool:root:call']
    assert 'input' not in events.usage()['trace'][0]


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
    assert session['execution']['nodes'][0].get('error') is None


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


def test_usage_summary_includes_cache_writes_once_without_changing_reported_counts():
    from amplifier_web.execution import rollup
    usage=public_usage({'input_tokens':4,'output_tokens':2,'total_tokens':6,'cache_read_tokens':3,'cache_write_tokens':100})
    assert usage['inputTokens']==4 and usage['totalTokens']==6
    assert usage['grossInputTokens']==104 and usage['grossTotalTokens']==106
    total=rollup([{'usage':usage},{'usage':usage}])
    assert total['totalTokens']==12 and total['grossTotalTokens']==212
