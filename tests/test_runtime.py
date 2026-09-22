"""Process contract tests use an explicit fixture, never assert live model success."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import pytest

from amplifier_web.runtime import RuntimeManager, normalize_event
from amplifier_web.runtime_worker import Worker
from amplifier_web.shared_state import ActivationGate

FIXTURE = r'''
import json,sys
for line in sys.stdin:
    data=json.loads(line); op=data['op']
    if op=='start':
        root=data['session']['id']
        if data['session']['id']=='slow':
            print(json.dumps({'type':'runtime.progress','phase':'bundle-preparation','detail':'Loading configured modules.'}),flush=True)
        else:
            print(json.dumps({'type':'runtime.ready','report':{'resumed':True,'session_id':root}}),flush=True)
    elif op=='send':
        print(json.dumps({'type':'input.delivered','input_id':data['input_id']}),flush=True)
        for identity in (root,root+'-child'):
            print(json.dumps({'type':'execution.event','event':{'id':identity+'-call','kind':'llm','phase':'completed','sessionId':identity,'rootSessionId':root}}),flush=True)
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

    def test_assistant_keeps_public_message_provenance_and_known_event_time(self):
        _, payload = normalize_event({'type':'assistant.message', 'text':'progress', 'generation_id':'g',
            'message_id':'m', 'event_id':'e', 'sequence':12, 'time':123.5, 'reasoning':'private'}, 'parent')
        self.assertEqual(payload['runtimeMessage'], {'messageId':'m', 'eventId':'e', 'sequence':12})
        self.assertEqual(payload['createdAt'],123.5)
        self.assertTrue(payload['timestampKnown'])
        self.assertNotIn('reasoning',payload)

    def test_assistant_does_not_invent_provenance_or_accept_invalid_times(self):
        for value in (None, 'yesterday', True, float('nan'), float('inf'), -1):
            _, payload = normalize_event({'type':'assistant.message', 'text':'progress', 'sequence':1, 'time':value}, 'parent')
            self.assertNotIn('runtimeMessage',payload)
            self.assertNotIn('createdAt',payload)

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


@pytest.mark.asyncio
async def test_worker_parking_releases_the_real_shared_handle_and_reacquires_unchanged(tmp_path):
    shared = pytest.importorskip("amplifier_foundation.session.shared_state")
    from amplifier_web.shared_state import configuration_stamp

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    worker = Worker()
    worker.workspace = workspace
    worker.home = tmp_path / "home"
    worker.home.mkdir()
    worker.runtime = SimpleNamespace(session_id="warm-session", queued_inputs=0,
                                     inbox=asyncio.Queue(), generation=None)
    worker.shared_store = shared.SharedSessionStore(workspace, "warm-session", root=tmp_path / "shared")
    worker.shared_store_stamp = shared.file_stamp
    worker.shared_handle = worker.shared_store.acquire(app="amplifier-unified", fixture=True)
    worker.activation_gate = ActivationGate()
    first = worker.activation_gate.activate()
    worker.activation = first
    from amplifier_web.session_files import sessions_dir
    native = sessions_dir(workspace) / "warm-session" / "transcript.jsonl"
    native.parent.mkdir(parents=True)
    native.write_text('{"role":"user","content":"before checkpoint"}\n')
    worker.config_inputs = (str(native),)
    worker.parked_history_stamp = worker.history_stamp()
    worker.parked_config_stamp = configuration_stamp(
        workspace, "warm-session", worker.home, shared.file_stamp, extra_paths=worker.config_inputs)
    checkpoint_calls = []
    async def checkpoint(status):
        checkpoint_calls.append(status)
        native.write_text('{"role":"user","content":"own completed checkpoint"}\n')
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(
        get=lambda name: None,
        get_capability=lambda name: (
            checkpoint
            if name == "live.checkpoint" else None)))

    with patch("amplifier_web.runtime_worker.publish"):
        await worker.park(activation=first)
        assert worker.parked
        assert not worker.shared_handle
        await worker.acquire_for_mutation()

    assert checkpoint_calls == ["completed"]
    assert not worker.parked
    assert worker.shared_handle.active
    with pytest.raises(RuntimeError, match="released or superseded"):
        worker.activation_gate.check(first)
    worker.shared_handle.release()


@pytest.mark.asyncio
async def test_parked_worker_rechecks_native_cli_change_before_admitting_input(tmp_path):
    shared = pytest.importorskip("amplifier_foundation.session.shared_state")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    worker = Worker()
    worker.workspace, worker.home = workspace, tmp_path / "home"
    worker.home.mkdir()
    worker.runtime = SimpleNamespace(session_id="warm-session", queued_inputs=0,
                                     inbox=asyncio.Queue(), generation=None)
    worker.shared_store = shared.SharedSessionStore(workspace, "warm-session", root=tmp_path / "shared")
    worker.shared_store_stamp = shared.file_stamp
    worker.shared_handle = worker.shared_store.acquire(app="amplifier-unified", fixture=True)
    worker.activation_gate = ActivationGate()
    worker.activation = worker.activation_gate.activate()
    messages = [{"role": "user", "content": "first"}]
    worker.shared_handle.write(messages, bundle="anchors", metadata={"fixture": True})
    from amplifier_web.session_files import sessions_dir
    native = sessions_dir(workspace) / "warm-session" / "transcript.jsonl"
    native.parent.mkdir(parents=True)
    native.write_text(json.dumps(messages[0]) + "\n")
    worker.config_inputs = (str(native),)
    checkpoint_calls, remount_calls = [], []
    async def checkpoint(status):
        checkpoint_calls.append(status)
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(
        get=lambda name: None,
        get_capability=lambda name: checkpoint if name == "live.checkpoint" else None))
    async def remount():
        from amplifier_foundation.session.history import SessionHistoryStore
        remount_calls.append(SessionHistoryStore(native.parent).load_messages())
    worker.remount = remount
    try:
        with patch("amplifier_web.runtime_worker.publish"):
            await worker.park(activation=worker.activation)
            native.write_text(native.read_text() + '{"role":"assistant","content":"new CLI turn"}\n')
            after_cli = native.read_bytes()
            await worker.acquire_for_mutation()
        assert len(remount_calls) == 1
        assert remount_calls[0] == messages + [{"role": "assistant", "content": "new CLI turn"}]
        assert checkpoint_calls == ["completed"]
        assert worker.runtime.inbox.empty()
        assert native.read_bytes() == after_cli
        assert worker.shared_handle.read()["messages"] == messages
    finally:
        if worker.shared_handle:
            worker.shared_handle.release()


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

    async def test_native_identity_restores_worker_but_ui_bridge_and_root_events_use_app_alias(self):
        self.session.update(runtimeSessionId='native-root', nativeIdentity='older-native-alias')
        await self.manager.send(self.session, 'continue native history', 'input-1', self.emit)
        await asyncio.wait_for(self.received.wait(), 3)
        self.assertEqual(list(self.manager.workers), ['fixture-session'])
        ready = next(p for k,p in self.events if k == 'runtime.status' and p['status'] == 'ready')
        self.assertEqual(ready['report']['session_id'], 'native-root')
        self.assertEqual(ready['sessionId'], 'fixture-session')
        self.assertEqual(self.bridges, [('get_state', {}, 'fixture-session')])
        root, child = [p for k,p in self.events if k == 'execution.event']
        self.assertEqual((root['sessionId'], root['rootSessionId']), ('fixture-session', 'fixture-session'))
        self.assertEqual((child['sessionId'], child['rootSessionId']), ('native-root-child', 'fixture-session'))
        message = next(p for k,p in self.events if k == 'assistant.message')
        self.assertEqual(message['sessionId'], 'fixture-session')

    async def test_native_identity_alias_is_used_when_runtime_id_is_not_set(self):
        self.session['nativeIdentity'] = 'native-root'
        await self.manager.start(self.session, self.emit)
        ready = next(p for k,p in self.events if k == 'runtime.status' and p['status'] == 'ready')
        self.assertEqual(ready['report']['session_id'], 'native-root')

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


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['metadata', 'backup', 'removed', 'unreadable'])
async def test_parked_native_history_invalidation_keeps_authority_and_releases_on_refusal(tmp_path, change):
    from amplifier_foundation.session.shared_state import SharedSessionStore, file_stamp
    from amplifier_web.session_files import sessions_dir
    from amplifier_web.shared_state import configuration_stamp
    worker = Worker()
    worker.workspace = tmp_path
    worker.home = tmp_path / 'home'
    worker.home.mkdir()
    worker.runtime = SimpleNamespace(session_id='fixture')
    worker.shared_store = SharedSessionStore(tmp_path, 'fixture', root=tmp_path / 'locks')
    worker.shared_store_stamp = file_stamp
    worker.activation_gate = ActivationGate()
    native = sessions_dir(tmp_path) / 'fixture'
    native.mkdir(parents=True)
    (native / 'transcript.jsonl').write_text('{"role":"user","content":"fixture"}\n')
    (native / 'metadata.json').write_text('{"bundle":"anchors"}')
    worker.parked_history_stamp = worker.history_stamp()
    worker.parked_config_stamp = configuration_stamp(tmp_path, 'fixture', worker.home, file_stamp)
    worker.parked = True
    remounted = []
    async def remount():
        remounted.append(True)
    worker.remount = remount
    if change == 'metadata':
        (native / 'metadata.json').write_text('{"bundle":"new-native-bundle"}')
    elif change == 'backup':
        (native / 'transcript.jsonl').rename(native / 'transcript.jsonl.backup')
    elif change == 'removed':
        (native / 'transcript.jsonl').unlink()
    else:
        worker.history_stamp = Mock(side_effect=OSError('stat unavailable'))
    try:
        if change in {'removed', 'unreadable'}:
            with pytest.raises((RuntimeError, OSError)):
                await worker.acquire_for_mutation()
            assert not remounted
            other = worker.shared_store.acquire(app='other')
            other.release()
        else:
            await worker.acquire_for_mutation()
            assert remounted == [True] and worker.shared_handle.active
            assert worker.shared_store.read() is None
    finally:
        if worker.shared_handle:
            worker.shared_handle.release()


@pytest.mark.parametrize('termination',['stop','crash'])
async def test_confirmed_process_exit_settles_independent_naming(tmp_path,termination):
    from amplifier_web.service import AppService
    from amplifier_web.execution import ensure_turn
    worker=tmp_path/'background.py'
    worker.write_text('''
import json,os,sys,time
for line in sys.stdin:
    data=json.loads(line)
    if data['op']=='start':
        sid=data['session']['id']
        print(json.dumps({'type':'runtime.ready','report':{}}),flush=True)
        print(json.dumps({'type':'execution.event','event':{'id':'naming','kind':'llm','lifecycle':'background','label':'Renaming fixture','phase':'running','sessionId':sid,'rootSessionId':sid,'turnId':'turn','startedAt':time.time()}}),flush=True)
        print(json.dumps({'type':'session.idle'}),flush=True)
    elif data['op']=='stop':break
    elif data['op']=='crash':os._exit(7)
''')
    app=AppService(tmp_path/'app',workspace=tmp_path)
    manager=RuntimeManager(command=[sys.executable,str(worker)],startup_timeout=3)
    idle=asyncio.Event();ended=asyncio.Event();events=[]
    try:
        await app.dispatch('session.create',{})
        session=app._session();ensure_turn(session,'turn')
        async def emit(kind,payload):
            events.append((kind,payload));await app.on_runtime_event(kind,payload)
            if kind=='runtime.status' and payload.get('status')=='idle':idle.set()
            if kind=='runtime.ended':ended.set()
        await manager.start(session,emit);await asyncio.wait_for(idle.wait(),3)
        row=session['execution']['nodes'][0];process=manager.workers[session['id']]['process']
        assert process.returncode is None and row['phase']=='running' and not row.get('endedAt')
        assert row['aggregateUsage']['costPendingCalls']==1
        if termination=='stop':await manager.stop(session['id'])
        else:await manager._write(manager.workers[session['id']],{'op':'crash'})
        await asyncio.wait_for(ended.wait(),3)
        assert process.returncode is not None
        assert row['phase']==('stopped' if termination=='stop' else 'interrupted') and row['endedAt']>=row['startedAt']
        assert row['aggregateUsage']['costPendingCalls']==row['aggregateUsage']['tokenPendingCalls']==0
        assert len([event for event in events if event[0]=='runtime.ended'])==1
    finally:
        await manager.close();await app.close()


async def test_cancelled_process_exit_delivery_remains_retryable():
    from amplifier_web.execution import ensure_turn,ingest,finish_background
    session={};ensure_turn(session,'turn')
    ingest(session,{'id':'naming','kind':'llm','lifecycle':'background','phase':'running','startedAt':1})
    waiting=asyncio.Event();release=asyncio.Event();attempts=0
    async def emit(kind,payload):
        nonlocal attempts
        attempts+=1;waiting.set();await release.wait()
        finish_background(session,payload['backgroundCallIds'],payload['status'])
    row={'emit':emit,'backgroundCalls':{'naming'}}
    manager=RuntimeManager(command=[sys.executable,'-c','pass'])
    try:
        reader=asyncio.create_task(manager._execution_ended('root',row,'stopped'))
        await waiting.wait()
        # _stop_row cancels and gathers the reader before retrying delivery.
        reader.cancel();await asyncio.gather(reader,return_exceptions=True)
        assert not row.get('executionEnded')
        assert session['execution']['nodes'][0]['phase']=='running'
        release.set();await manager._execution_ended('root',row,'stopped')
        assert row['executionEnded'] is True
        assert session['execution']['nodes'][0]['phase']=='stopped'
        assert session['execution']['aggregateUsage']['costPendingCalls']==0
        await manager._execution_ended('root',row,'stopped')
        assert attempts==2
    finally:await manager.close()
