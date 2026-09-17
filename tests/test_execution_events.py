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
    events.hook('root','tool:pre',{'tool_call_id':'call','tool_name':'bash','tool_input':{'command':'secret content','token':'private'}})
    assert 'secret content' not in str(events.nodes) and 'private' not in str(events.nodes)
    events.hook('root','tool:post',{'tool_call_id':'call','tool_result':{'success':False,'error':{'message':'private'}}})
    assert events.nodes['tool:root:call']['phase']=='error'
    events.lifecycle({'type':'child.updated','sessionId':'child','status':'completed','report':'Completed the requested review.'})
    assert events.nodes['worker:child']['summary']=='Completed the requested review.'
