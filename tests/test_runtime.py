"""Process contract tests use an explicit fixture, never assert live model success."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from amplifier_web.runtime import RuntimeManager, normalize_event
from amplifier_web.runtime_worker import Worker
from amplifier_web.shared_state import ActivationGate

FIXTURE = r'''
import json,sys
for line in sys.stdin:
    data=json.loads(line); op=data['op']
    if op=='start':
        if data['session']['id']=='slow':
            print(json.dumps({'type':'runtime.progress','phase':'bundle-preparation','detail':'Loading configured modules.'}),flush=True)
        else:
            print(json.dumps({'type':'runtime.ready','report':{'resumed':True}}),flush=True)
    elif op=='send':
        print(json.dumps({'type':'input.delivered','input_id':data['input_id']}),flush=True)
        print(json.dumps({'op':'bridge','id':'bridge1','operation':'get_state','args':{}}),flush=True)
        print(json.dumps({'op':'reply','id':data['id'],'result':{'accepted':True}}),flush=True)
    elif op=='bridge.result':
        print(json.dumps({'type':'assistant.message','text':str(data.get('result',{}).get('revision'))}),flush=True)
    elif op=='stop':
        break
    else:
        print(json.dumps({'op':'reply','id':data['id'],'error':'Not available'}),flush=True)
'''


class NormalizationTests(unittest.TestCase):
    def test_child_identity_remains_distinct_from_parent(self):
        kind,payload=normalize_event({'type':'child.updated','sessionId':'child','status':'idle','agent':'foundation:explorer'},'parent')
        self.assertEqual(kind,'worker.updated')
        self.assertEqual(payload['sessionId'],'parent')
        self.assertEqual(payload['id'],'child')
        self.assertEqual(payload['name'],'foundation:explorer')

    def test_private_provider_events_are_not_published(self):
        self.assertIsNone(normalize_event({'type':'provider.request','secret':'sensitive'},'parent'))
        self.assertIsNone(normalize_event({'type':'analysis.delta','text':'private'},'parent'))

    def test_assistant_retains_input_identity(self):
        self.assertEqual(normalize_event({'type':'assistant.message','text':'hello'},'p','turn')[1]['inputId'],'turn')

    def test_background_activity_reports_waiting_without_exposing_extra_fields(self):
        kind, payload = normalize_event({'type':'runtime.activity','phase':'waiting-workers',
            'detail':'Waiting for 5 delegated tasks to report back.', 'activeWorkers':5,
            'reasoning':'private', 'request':{'secret':'private'}}, 'parent')
        self.assertEqual(kind, 'runtime.status')
        self.assertEqual(payload['status'], 'working')
        self.assertTrue(payload['activityOnly'])
        self.assertEqual(payload['phase'], 'waiting-workers')
        self.assertEqual(payload['activeWorkers'], 5)
        self.assertNotIn('reasoning', payload)
        self.assertNotIn('request', payload)

    def test_queued_worker_is_not_claimed_to_have_started(self):
        payload=normalize_event({'type':'job.queued','job_id':'j','agent':'foundation:explorer'},'p')[1]
        self.assertEqual(payload['status'],'queued')
        self.assertEqual(payload['name'],'foundation:explorer')

    def test_worker_cancellation_is_not_reported_as_completion(self):
        self.assertEqual(normalize_event({'type':'job.cancel_requested','job_id':'j'},'p')[1]['status'],'stopping')
        self.assertEqual(normalize_event({'type':'job.cancelled','job_id':'j'},'p')[1]['status'],'cancelled')


class WorkerActivityTests(unittest.TestCase):
    def test_activation_gate_rejects_a_callback_from_before_park(self):
        gate = ActivationGate()
        first = gate.activate()
        stale_context = gate.bind(first)
        gate.reset(stale_context)
        gate.release(first)
        second = gate.activate()
        current_context = gate.bind(second)
        try:
            gate.check_current()
            with self.assertRaisesRegex(RuntimeError, "released or superseded"):
                gate.check(first)
        finally:
            gate.reset(current_context)

    def test_waiting_counts_actual_pending_jobs_and_preserves_agent_identity(self):
        worker=Worker()
        loop=SimpleNamespace(jobs={
            'a':{'task':Mock(done=lambda:False),'tool_call':{'arguments':{'agent':'foundation:explorer','instruction':'private'}}},
            'b':{'task':Mock(done=lambda:True)},
        })
        worker.session=SimpleNamespace(coordinator=SimpleNamespace(get=lambda name:loop))
        with patch('amplifier_web.runtime_worker.publish') as publish:
            worker.observe({'type':'job.queued','job_id':'a'})
            event=publish.call_args.args[0]
            self.assertEqual(event['agent'],'foundation:explorer')
            self.assertNotIn('instruction',event)
            worker.observe({'type':'generation.finished'})
            activity=publish.call_args.args[0]
            self.assertEqual(activity['phase'],'waiting-workers')
            self.assertEqual(activity['activeWorkers'],1)


class PublicActivityHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_child_retries_show_public_identity_and_count_without_error_body(self):
        worker=Worker();worker.runtime=SimpleNamespace(session_id='parent')
        callbacks={}
        capabilities={'live.children':SimpleNamespace(rows={'child':{'agent':'foundation:explorer','callId':'tool-1'}})}
        coordinator=SimpleNamespace(session_id='child',
            get_capability=capabilities.get,
            register_capability=lambda key,value:capabilities.update({key:value}),
            hooks=SimpleNamespace(register=lambda event,fn,**kw:callbacks.update({event:fn})))
        with patch.dict(sys.modules, {'amplifier_core':SimpleNamespace(HookResult=lambda:None)}):
            worker.install_activity(coordinator)
        with patch('amplifier_web.runtime_worker.publish') as publish:
            await callbacks['provider:retry']('provider:retry',{'attempt':2,'max_retries':5,'error_message':'private endpoint credential detail'})
            event=publish.call_args.args[0]
            self.assertEqual(event['workerId'],'child')
            self.assertEqual(event['phase'],'retrying')
            self.assertEqual(event['retryAttempt'],2)
            self.assertNotIn('error_message',event)
            kind,payload=normalize_event(event,'parent')
            self.assertEqual(kind,'worker.updated')
            self.assertEqual(payload['callId'],'tool-1')
            self.assertEqual(payload['retryMax'],5)
            self.assertNotIn('private',json.dumps(payload))

    async def test_naming_provider_hooks_keep_telemetry_without_busy_notices(self):
        from amplifier_web.execution_events import CALL_PURPOSE
        worker=Worker();worker.runtime=SimpleNamespace(session_id='parent');worker.telemetry=Mock()
        callbacks={};capabilities={}
        coordinator=SimpleNamespace(session_id='parent',get_capability=capabilities.get,
            get=lambda _: {},register_capability=lambda key,value:capabilities.update({key:value}),
            hooks=SimpleNamespace(register=lambda event,fn,**kw:callbacks.update({event:fn})))
        with patch.dict(sys.modules, {'amplifier_core':SimpleNamespace(HookResult=lambda:None)}):
            worker.install_activity(coordinator)
        token=CALL_PURPOSE.set({'label':'Session naming','turnId':'finished'})
        try:
            with patch('amplifier_web.runtime_worker.publish') as publish:
                await callbacks['provider:retry']('provider:retry',{'attempt':2,'max_retries':3})
                publish.assert_not_called()
                worker.telemetry.hook.assert_called_once()
        finally:CALL_PURPOSE.reset(token)


class ProcessContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        worker=Path(self.temp.name)/'fixture.py';worker.write_text(FIXTURE)
        self.events=[]
        self.bridges=[]
        self.received=asyncio.Event()
        async def bridge(op,args,sid):
            self.bridges.append((op,args,sid));return {'revision':7}
        async def emit(kind,payload):
            self.events.append((kind,payload))
            if kind=='assistant.message': self.received.set()
        self.emit=emit
        self.manager=RuntimeManager(bridge,command=[sys.executable,str(worker)],startup_timeout=3)
        self.session={'id':'fixture-session','workspace':self.temp.name}

    async def asyncTearDown(self):
        await self.manager.close()
        self.temp.cleanup()

    async def test_send_and_app_bridge_do_not_deadlock_reader(self):
        result=await self.manager.send(self.session,'hello','input-1',self.emit)
        self.assertTrue(result['accepted'])
        await asyncio.wait_for(self.received.wait(),3)
        self.assertEqual(self.bridges,[('get_state',{},'fixture-session')])
        message=next(p for k,p in self.events if k=='assistant.message')
        self.assertEqual(message['text'],'7')
        self.assertEqual(message['inputId'],'input-1')

    async def test_parallel_start_reuses_single_worker(self):
        await asyncio.gather(*(self.manager.start(self.session,self.emit) for _ in range(4)))
        self.assertEqual(len(self.manager.workers),1)
        self.assertEqual(sum(k=='runtime.status' and p['status']=='starting' for k,p in self.events),1)

    async def test_second_send_keeps_the_same_worker_process(self):
        await self.manager.send(self.session, 'first', 'input-1', self.emit)
        process = self.manager.workers[self.session['id']]['process']
        await self.manager.send(self.session, 'second', 'input-2', self.emit)
        self.assertIs(self.manager.workers[self.session['id']]['process'], process)
        self.assertIsNone(process.returncode)
        self.assertEqual(sum(k == 'runtime.status' and p['status'] == 'starting'
                             for k, p in self.events), 1)

    async def test_stop_terminates_process_and_clears_owner(self):
        await self.manager.start(self.session,self.emit)
        process=self.manager.workers[self.session['id']]['process']
        await asyncio.gather(*(self.manager.stop(self.session['id']) for _ in range(3)))
        self.assertIsNotNone(process.returncode)
        self.assertFalse(self.manager.workers)
        self.assertEqual(self.events[-1][1]['status'],'stopped')

    async def test_runtime_operation_failure_is_returned(self):
        await self.manager.start(self.session,self.emit)
        with self.assertRaisesRegex(RuntimeError,'Not available'):
            await self.manager.approval(self.session['id'],'missing','allow')

    async def test_cold_start_reports_progress_and_bounded_timeout_without_send(self):
        self.manager.startup_timeout = 0.12
        self.manager.progress_interval = 0.02
        with self.assertRaisesRegex(RuntimeError, 'stopped before accepting your message'):
            await self.manager.send({'id':'slow'}, 'must not be sent', 'pending-input', self.emit)
        progress = [p for k,p in self.events if k=='runtime.status' and p.get('phase')=='bundle-preparation']
        self.assertGreaterEqual(len(progress), 2)
        self.assertTrue(all(p['status']=='starting' and 'elapsedSeconds' in p for p in progress))
        self.assertFalse(self.bridges)
        self.assertFalse(self.manager.workers)
        self.assertFalse(any(k=='assistant.message' for k,_ in self.events))

    async def test_dead_worker_start_fails_without_fabricated_response(self):
        broken=RuntimeManager(command=[sys.executable,'-c','raise SystemExit(3)'],startup_timeout=3)
        try:
            with self.assertRaisesRegex(RuntimeError,'exited'):
                await broken.start({'id':'bad'},self.emit)
            self.assertFalse(any(k=='assistant.message' for k,_ in self.events))
        finally:
            await broken.close()


if __name__=='__main__': unittest.main()


class LargeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_worker_reader_roundtrips_large_bridge_sized_message(self):
        from amplifier_web.runtime_protocol import MAX_MESSAGE_BYTES
        script='''
import asyncio
from amplifier_web.runtime_worker import Worker,configure_protocol,publish
class Probe(Worker):
 async def command(self,data):
  if data.get('op')=='echo':publish({'op':'reply','payload':data['payload']})
  else:await super().command(data)
configure_protocol()
asyncio.run(Probe().run())
'''
        proc=await asyncio.create_subprocess_exec(sys.executable,'-c',script,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,limit=MAX_MESSAGE_BYTES)
        try:
            payload='x'*7_000_000
            async def write():
                proc.stdin.write((json.dumps({'op':'echo','payload':payload})+'\n').encode());await proc.stdin.drain()
            send=asyncio.create_task(write())
            response=json.loads(await asyncio.wait_for(proc.stdout.readline(),10))
            await send
            self.assertEqual(response['payload'],payload)
            self.assertIsNone(proc.returncode)
            proc.stdin.write(b'{"op":"stop"}\n');await proc.stdin.drain()
            self.assertEqual(await asyncio.wait_for(proc.wait(),3),0)
        finally:
            if proc.returncode is None:proc.kill();await proc.wait()

    async def test_reported_worker_failure_is_not_overwritten_by_exit_code(self):
        fixture="import json;print(json.dumps({'type':'runtime.ready'}),flush=True);print(json.dumps({'type':'runtime.error','error':'Specific protocol failure'}),flush=True)"
        manager=RuntimeManager(command=[sys.executable,'-c',fixture]);events=[]
        async def emit(kind,data):events.append((kind,data))
        try:
            await manager.start({'id':'probe'},emit)
            await asyncio.wait_for(manager.workers['probe']['reader'],3)
            errors=[data['error'] for kind,data in events if kind=='runtime.error']
            self.assertEqual(errors,['Specific protocol failure'])
        finally:await manager.close()
