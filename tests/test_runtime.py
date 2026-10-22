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
            if root=='native-root':
                print(json.dumps({'type':'generation.started','session_id':'child','root_session_id':root}),flush=True)
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


@pytest.mark.parametrize('boundary', ['admission', 'status', 'qualification', 'spawn'])
async def test_restart_authority_is_rechecked_after_startup_waits(tmp_path, monkeypatch, boundary):
    """A queued restart cannot mount after Stop/revocation; reap any spawned child."""
    writes = tmp_path/'controls.jsonl'
    fixture = "import json,sys\nfor line in sys.stdin:\n open(sys.argv[1], 'a').write(line)\n if json.loads(line)['op']=='stop': break\n"
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', fixture, str(writes)],
        startup_timeout=2, retention={'prewarm_on_select': False})
    manager.home = tmp_path/'app'
    session = {'id':'guarded', 'workspace':str(tmp_path), 'bundle':'fixture'}
    entered, release = asyncio.Event(), asyncio.Event()
    revoked, created = False, []
    real_spawn = asyncio.create_subprocess_exec
    def guard():
        if revoked: raise ValueError('Restart no longer authorized')
    async def emit(kind, payload):
        if boundary == 'status' and kind == 'runtime.status' and not entered.is_set():
            entered.set(); await release.wait()
    async def spawn(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        created.append(proc)
        if boundary == 'spawn': entered.set(); await release.wait()
        return proc
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    if boundary == 'qualification':
        command = manager.command
        manager.command = None
        monkeypatch.setattr(manager, '_command', lambda *args, **kwargs: command)
        async def qualify(*args, **kwargs):
            entered.set(); await release.wait()
            return None
        monkeypatch.setattr('amplifier_web.runtime_profiles.ensure', qualify)
    if boundary == 'admission':
        lock = manager._locks.setdefault(session['id'], asyncio.Lock())
        await lock.acquire()
    task = asyncio.create_task(manager.start(session, emit, guard=guard))
    try:
        if boundary == 'admission':
            await asyncio.sleep(0)
            assert not task.done()
        else: await asyncio.wait_for(entered.wait(), 2)
        revoked = True
        if boundary == 'admission': lock.release()
        else: release.set()
        with pytest.raises((ValueError, RuntimeError)):
            await asyncio.wait_for(task, 5)
        commands = [json.loads(line)['op'] for line in writes.read_text().splitlines()] if writes.exists() else []
        assert commands == (['stop'] if boundary == 'spawn' else [])  # No startup/configuration or input admission.
        assert len(created) == (1 if boundary == 'spawn' else 0)
        assert all(proc.returncode is not None for proc in created)
        assert not manager.workers and not manager._preparations
        assert not list((manager.home/'updates/worker-leases').glob('*.json'))
    finally:
        release.set()
        if boundary == 'admission' and lock.locked(): lock.release()
        await asyncio.gather(task, return_exceptions=True)
        await manager.close()


@pytest.mark.parametrize('cancellation', ['none', 'heartbeat', 'heartbeat-and-reap', 'lease-error'])
async def test_startup_heartbeat_cleanup_keeps_process_ownership(tmp_path, monkeypatch, cancellation):
    """A real child must be registered or reaped before startup relinquishes it."""
    writes = tmp_path / 'controls.jsonl'
    fixture = r'''
import json,sys
for line in sys.stdin:
    with open(sys.argv[1], 'a') as output:
        output.write(line)
    command = json.loads(line)
    if command['op'] == 'start':
        print(json.dumps({'type': 'runtime.ready', 'report': {}}), flush=True)
    elif command['op'] == 'stop':
        break
'''
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', fixture, str(writes)],
                             startup_timeout=3, retention={'prewarm_on_select': False})
    manager.home = tmp_path / 'app'
    session = {'id': 'cleanup-owner', 'workspace': str(tmp_path), 'bundle': 'fixture'}
    created, heartbeats, attempted, events = [], [], [], []
    child_lease = None
    lease_removals = []
    lease_error = OSError('fixture lease acquisition failure')
    outer = None
    injected = []
    checking = True
    loop = asyncio.get_running_loop()
    previous_factory = loop.get_task_factory()
    real_spawn = asyncio.create_subprocess_exec
    real_write = manager._write
    from amplifier_web import generation_leases
    real_acquire = generation_leases.acquire
    real_unlink = Path.unlink

    def tracked_acquire(home, generation, pid):
        nonlocal child_lease
        if created and pid == created[0].pid and cancellation == 'lease-error':
            raise lease_error
        result = real_acquire(home, generation, pid)
        if created and pid == created[0].pid:
            child_lease = result
        return result

    def tracked_unlink(path, *args, **kwargs):
        if checking and path == child_lease:
            assert created[0].returncode is not None
            lease_removals.append(path)
        return real_unlink(path, *args, **kwargs)

    async def tracked_write(row, data):
        attempted.append(data['op'])
        return await real_write(row, data)

    class PreparationHeartbeat(asyncio.Task):
        def cancel(self, msg=None):
            if not injected and cancellation.startswith('heartbeat') and checking:
                assert outer is not None and len(created) == 1
                assert created[0].returncode is None
                # Select the cancellation boundary without replacing the
                # heartbeat coroutine, subprocess, or manager's cleanup.
                injected.append('heartbeat')
                loop.call_soon(outer.cancel)
            return super().cancel(msg)

    def task_factory(loop, coro, **kwargs):
        if coro.cr_code is RuntimeManager._preparation_progress.__code__:
            task = PreparationHeartbeat(coro, loop=loop, **kwargs)
            heartbeats.append(task)
            return task
        if previous_factory:
            return previous_factory(loop, coro, **kwargs)
        return asyncio.Task(coro, loop=loop, **kwargs)

    async def tracked_spawn(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        created.append(proc)
        real_wait = proc.wait

        async def tracked_wait():
            if checking and cancellation == 'heartbeat-and-reap' and 'reap' not in injected:
                # The real reaper owns this wait. Cancel its caller again,
                # while retaining the lease until exit has been confirmed.
                assert child_lease is not None and child_lease.is_file()
                injected.append('reap')
                loop.call_soon(outer.cancel)
                await asyncio.sleep(0)
            return await real_wait()

        monkeypatch.setattr(proc, 'wait', tracked_wait)
        return proc

    async def emit(kind, payload):
        events.append((kind, payload))

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', tracked_spawn)
    monkeypatch.setattr(generation_leases, 'acquire', tracked_acquire)
    monkeypatch.setattr(Path, 'unlink', tracked_unlink)
    monkeypatch.setattr(manager, '_write', tracked_write)
    loop.set_task_factory(task_factory)
    try:
        outer = asyncio.create_task(manager.start(session, emit))
        if cancellation == 'none':
            await asyncio.wait_for(asyncio.shield(outer), 5)
            assert manager.workers[session['id']]['process'] is created[0]
            assert manager.workers[session['id']]['generation_lease'] == child_lease
            assert list((manager.home / 'updates/worker-leases').glob('*.json')) == [child_lease]
            assert attempted == ['start']
            assert [json.loads(line)['op'] for line in writes.read_text().splitlines()] == ['start']
            await asyncio.wait_for(manager.stop(session['id']), 5)
        elif cancellation == 'lease-error':
            from amplifier_web.runtime import RuntimeStartupError
            with pytest.raises(RuntimeStartupError) as failure:
                await asyncio.wait_for(asyncio.shield(outer), 5)
            assert failure.value.__cause__ is lease_error
            assert any(kind == 'runtime.error' for kind, _ in events)
            assert not attempted and not writes.exists()
            assert child_lease is None
        else:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(outer), 5)
            assert outer.cancelled()
            assert injected == (['heartbeat', 'reap'] if cancellation == 'heartbeat-and-reap' else ['heartbeat'])
            assert attempted == []  # Not even a startup write was attempted.
            assert not writes.exists()
        assert len(created) == 1 and created[0].returncode is not None
        assert lease_removals == ([] if child_lease is None else [child_lease])
        assert len(heartbeats) == 1 and heartbeats[0].done()
        assert not manager.workers and not manager._preparations
        assert not list((manager.home / 'updates/worker-leases').glob('*.json'))
        assert not manager.has_pending_operations()
        # Repeated stop/close cannot be needed to finish pre-registration cleanup.
        await asyncio.wait_for(manager.stop(session['id']), 5)
        await asyncio.wait_for(manager.close(), 5)
        assert not manager.workers and not manager._preparations
    finally:
        checking = False
        loop.set_task_factory(previous_factory)
        if outer and not outer.done():
            outer.cancel()
            await asyncio.gather(outer, return_exceptions=True)
        for proc in created:
            if proc.returncode is None:
                proc.kill()
            await asyncio.wait_for(proc.wait(), 5)
        await asyncio.wait_for(manager.close(), 5)


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

    def test_child_generation_retains_identity_and_root_provenance(self):
        kind, payload = normalize_event({'type': 'generation.started', 'session_id': 'child',
            'root_session_id': 'root'}, 'root')
        self.assertEqual(kind, 'runtime.generation')
        self.assertEqual((payload['sessionId'], payload['rootSessionId']), ('child', 'root'))

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


@pytest.mark.parametrize(('event', 'status'), [
    ('generation.finished', 'completed'),
    ('generation.failed', 'error'),
    ('generation.detached', 'interrupted'),
])
async def test_worker_parking_checkpoints_observed_root_generation_outcome(tmp_path, event, status):
    shared = pytest.importorskip("amplifier_foundation.session.shared_state")
    worker = Worker()
    worker.workspace, worker.home = tmp_path / "workspace", tmp_path / "home"
    worker.workspace.mkdir()
    worker.home.mkdir()
    worker.runtime = SimpleNamespace(session_id="outcome-session", queued_inputs=0,
                                     inbox=asyncio.Queue(), generation=None)
    worker.shared_store = shared.SharedSessionStore(worker.workspace, "outcome-session", root=tmp_path / "shared")
    worker.shared_store_stamp = shared.file_stamp
    worker.shared_handle = worker.shared_store.acquire(app="amplifier-unified", fixture=True)
    worker.activation_gate = ActivationGate()
    worker.activation = worker.activation_gate.activate()
    worker.config_inputs = ()
    worker.parked_history_stamp = worker.history_stamp()
    worker.parked_config_stamp = None
    checkpoint_calls = []
    async def checkpoint(outcome):
        checkpoint_calls.append(outcome)
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(
        get=lambda name: None,
        get_capability=lambda name: checkpoint if name == "live.checkpoint" else None))
    try:
        with patch("amplifier_web.runtime_worker.publish"):
            worker.observe({'type': event})
            await worker.park(activation=worker.activation)
        assert checkpoint_calls == [status]
    finally:
        if worker.shared_handle:
            worker.shared_handle.release()


def test_child_generation_does_not_change_root_checkpoint_outcome():
    worker = Worker()
    worker.runtime = SimpleNamespace(session_id='root')
    with patch('amplifier_web.runtime_worker.publish'):
        worker.observe({'type': 'generation.finished', 'session_id': 'root'})
        worker.observe({'type': 'generation.failed', 'session_id': 'child', 'root_session_id': 'root'})
    assert worker.root_generation_outcome == 'completed'


@pytest.mark.parametrize(('event', 'expected'), [
    ('generation.failed', 'error'),
    ('generation.detached', 'interrupted'),
])
async def test_terminal_root_execution_checkpoints_observed_outcome_during_worker_cleanup(event, expected):
    worker = Worker()
    worker.runtime = SimpleNamespace(session_id='root')
    worker.workspace = Path.cwd()
    worker.activation_gate = ActivationGate()
    worker.activation = worker.activation_gate.activate()
    worker.shared_handle = SimpleNamespace(release=lambda: None)
    checkpoint_calls = []
    async def checkpoint(status):
        checkpoint_calls.append(status)
    async def cleanup():
        return None
    async def close():
        return None
    async def read():
        await asyncio.Event().wait()
    worker.session = SimpleNamespace(
        coordinator=SimpleNamespace(get=lambda name: None,
            get_capability=lambda name: checkpoint if name == 'live.checkpoint' else None),
        cleanup=cleanup)
    worker.read = read
    with patch('amplifier_web.runtime_worker.publish'):
        worker.observe({'type': event, 'session_id': 'root'})
    worker.controls = SimpleNamespace(close=close)
    async def fail():
        raise RuntimeError('ContextLengthError: terminal root failure')
    worker.execution = asyncio.create_task(fail())
    await asyncio.gather(worker.execution, return_exceptions=True)
    with patch('amplifier_web.runtime_worker.publish'):
        worker.executed(worker.execution)
        await worker.run()
    assert checkpoint_calls == [expected]
    assert worker.shared_handle is None


async def test_terminal_cleanup_does_not_checkpoint_completed_or_yielding_outcomes():
    async def run(outcome, yielding=False):
        worker = Worker()
        worker.runtime = SimpleNamespace(session_id='root')
        worker.workspace = Path.cwd()
        worker.activation_gate = ActivationGate()
        worker.activation = worker.activation_gate.activate()
        worker.shared_handle = SimpleNamespace(release=lambda: None)
        worker.ownership = SimpleNamespace(yielding=yielding, registration=None)
        calls = []
        async def checkpoint(status):
            calls.append(status)
        async def cleanup():
            return None
        async def read():
            await asyncio.Event().wait()
        worker.session = SimpleNamespace(
            coordinator=SimpleNamespace(get_capability=lambda name: checkpoint if name == 'live.checkpoint' else None),
            cleanup=cleanup)
        worker.read = read
        worker.root_generation_outcome = outcome
        worker.shutdown.set()
        await worker.run()
        return calls
    assert await run('completed') == []
    assert await run('interrupted', yielding=True) == []


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
        capabilities={'web.worker_run':'child-run', 'live.children':SimpleNamespace(rows={'child':{'agent':'foundation:explorer','callId':'tool-1','runId':'child-run'}})}
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
            self.assertEqual(payload['runId'],'child-run')
            self.assertTrue(payload['activityOnly'])
            self.assertNotIn('status',payload)
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
        self.generation_received=asyncio.Event()
        async def bridge(op,args,sid):
            self.bridges.append((op,args,sid));return {'revision':7}
        async def emit(kind,payload):
            self.events.append((kind,payload))
            if kind=='assistant.message': self.received.set()
            if kind=='runtime.generation': self.generation_received.set()
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

    async def test_child_generation_keeps_its_identity_while_root_is_mapped_to_app_alias(self):
        self.session['runtimeSessionId'] = 'native-root'
        await self.manager.start(self.session, self.emit)
        await asyncio.wait_for(self.generation_received.wait(), 3)
        generation = next(payload for kind, payload in self.events if kind == 'runtime.generation')
        self.assertEqual((generation['sessionId'], generation['rootSessionId']), ('child', 'fixture-session'))

    async def test_native_identity_alias_is_used_when_runtime_id_is_not_set(self):
        self.session['nativeIdentity'] = 'native-root'
        await self.manager.start(self.session, self.emit)
        ready = next(p for k,p in self.events if k == 'runtime.status' and p['status'] == 'ready')
        self.assertEqual(ready['report']['session_id'], 'native-root')

    async def test_parallel_start_reuses_single_worker(self):
        await asyncio.gather(*(self.manager.start(self.session,self.emit) for _ in range(4)))
        self.assertEqual(len(self.manager.workers),1)
        self.assertEqual(sum(k=='runtime.status' and p['status']=='starting' for k,p in self.events),1)

    async def test_bundle_replacement_is_one_shot_and_cannot_mutate_a_live_worker(self):
        replacement = {'bundle': 'new-root', 'fingerprint': 'reviewed'}
        self.session['bundleReplacement'] = replacement
        await self.manager.start(self.session, self.emit)
        row = self.manager.workers[self.session['id']]
        self.assertNotIn('bundleReplacement', row['start_session'])
        process = row['process']
        with self.assertRaisesRegex(RuntimeError, 'already has a worker'):
            await self.manager.start(self.session, self.emit)
        self.assertIs(row['process'], process)
        self.assertIsNone(process.returncode)

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
        with self.assertRaisesRegex(RuntimeError, 'This attempt did not send your message') as failure:
            await self.manager.send({'id':'slow'}, 'must not be sent', 'pending-input', self.emit)
        self.assertIn('stopped before accepting your message', str(failure.exception.__cause__))
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

    async def test_start_write_disconnect_preserves_worker_exit_and_cleans_up(self):
        for error_type in (BrokenPipeError, ConnectionResetError):
            with self.subTest(error=error_type.__name__):
                events = []
                async def emit(kind, data):
                    events.append((kind, data))
                broken = RuntimeManager(command=[sys.executable, '-c', 'raise SystemExit(3)'], startup_timeout=3)
                async def disconnected_write(row, data):
                    self.assertEqual(data['op'], 'start')
                    await row['process'].wait()
                    raise error_type('Connection lost')
                try:
                    with patch.object(broken, '_write', disconnected_write):
                        with self.assertRaisesRegex(RuntimeError, 'worker exited.*code 3'):
                            await broken.start({'id': 'dead-write'}, emit)
                    self.assertFalse(broken.workers)
                    errors = [data for kind, data in events if kind == 'runtime.error']
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(errors[0]['exitCode'], 3)
                    self.assertFalse(any(kind in ('assistant.message', 'input.delivered') for kind, _ in events))
                finally:
                    await broken.close()

    async def test_start_write_disconnect_cannot_admit_a_ready_worker(self):
        async def disconnected_write(row, data):
            if data['op'] == 'start':
                row['ready'].set_result(None)
            raise ConnectionResetError('Connection lost')
        try:
            with patch.object(self.manager, '_write', disconnected_write):
                with self.assertRaisesRegex(RuntimeError, 'connection closed during startup'):
                    await self.manager.start(self.session, self.emit)
            self.assertFalse(self.manager.workers)
            self.assertFalse(self.bridges)
            self.assertFalse(any(kind == 'assistant.message' for kind, _ in self.events))
        finally:
            await self.manager.close()


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
            from amplifier_web.attachments import MAX_ENCODED_BYTES, MAX_REQUEST_BYTES
            self.assertGreater(MAX_MESSAGE_BYTES, MAX_REQUEST_BYTES)
            payload='x'*MAX_ENCODED_BYTES
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
        # Receive startup before reporting its outcome. Exiting before reading
        # stdin races the unrelated start write instead of exercising the reader.
        fixture="import json,sys;assert json.loads(sys.stdin.readline())['op']=='start';print(json.dumps({'type':'runtime.ready'}),flush=True);print(json.dumps({'type':'runtime.error','error':'Specific protocol failure','errorType':'ProtocolError'}),flush=True)"
        manager=RuntimeManager(command=[sys.executable,'-c',fixture]);events=[]
        async def emit(kind,data):events.append((kind,data))
        try:
            await manager.start({'id':'probe'},emit)
            await asyncio.wait_for(manager.workers['probe']['reader'],3)
            errors=[data['error'] for kind,data in events if kind=='runtime.error']
            self.assertEqual(errors,['Specific protocol failure'])
            self.assertEqual([data['errorType'] for kind,data in events if kind=='runtime.error'],['ProtocolError'])
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


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError("checkpoint disk failure"), asyncio.CancelledError()])
async def test_terminal_checkpoint_failure_still_cleans_up_and_releases_ownership(failure):
    worker = Worker()
    worker.runtime = SimpleNamespace(session_id="root")
    worker.activation_gate = ActivationGate()
    worker.activation = worker.activation_gate.activate()
    steps = []

    async def checkpoint(status):
        steps.append(("checkpoint", status))
        raise failure

    async def registration_close():
        steps.append("registration")

    async def controls_close():
        steps.append("controls")

    async def cleanup():
        steps.append("session")

    async def read():
        await asyncio.Event().wait()

    worker.ownership = SimpleNamespace(yielding=False, registration=SimpleNamespace(close=registration_close))
    worker.controls = SimpleNamespace(close=controls_close)
    worker.session = SimpleNamespace(
        coordinator=SimpleNamespace(get_capability=lambda name: checkpoint if name == "live.checkpoint" else None),
        cleanup=cleanup)
    worker.shared_handle = SimpleNamespace(release=lambda: steps.append("release"))
    worker.root_generation_outcome = "error"
    worker.read = read
    worker.shutdown.set()
    with pytest.raises(type(failure)):
        await worker.run()
    assert steps == [("checkpoint", "error"), "registration", "controls", "session", "release"]
    assert worker.shared_handle is None
    assert not worker.terminal_checkpointed


async def test_naming_does_not_hold_command_lock_or_require_idle(monkeypatch):
    from unittest.mock import AsyncMock
    worker = Worker()
    started, release, sent = asyncio.Event(), asyncio.Event(), asyncio.Event()
    worker.acquire_for_mutation = AsyncMock()
    worker.bind_activation = lambda: None
    worker.activation_gate = SimpleNamespace(reset=lambda token: None)
    worker.park = AsyncMock()
    worker.session = object()
    worker.execution = object()
    worker.controls = SimpleNamespace(require_idle=Mock(side_effect=AssertionError('Naming must run during work')))
    async def suggest():
        started.set()
        await release.wait()
        return {'name': 'Concurrent name'}
    worker.naming = SimpleNamespace(suggest=suggest)
    dispatch = worker._command_serial
    async def serial(data):
        if data['op'] == 'send': sent.set()
        else: await dispatch(data)
    worker._command_serial = serial
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', lambda event: None)
    task = asyncio.create_task(worker.command({'id': 'naming', 'op': 'control', 'operation': 'session.naming'}))
    await asyncio.wait_for(started.wait(), 1)
    assert worker.operation_controls == 1 and not worker.command_lock.locked()
    await asyncio.wait_for(worker.command({'id': 'input', 'op': 'send'}), .5)
    assert sent.is_set() and not task.done()
    release.set()
    await task
    assert worker.operation_controls == 0


async def test_naming_reuses_worker_without_replacing_foreground_emitter(tmp_path):
    runtime = RuntimeManager(tmp_path)
    ready = asyncio.get_running_loop().create_future()
    ready.set_result({})
    original, naming = Mock(), Mock()
    runtime.workers['active'] = {'process': SimpleNamespace(returncode=None), 'ready': ready, 'emit': original}
    await runtime.start({'id': 'active'}, naming, preserve_emit=True)
    assert runtime.workers['active']['emit'] is original
    await runtime.start({'id': 'active'}, naming)
    assert runtime.workers['active']['emit'] is naming
    runtime.workers.clear()
    await runtime.close()


@pytest.mark.parametrize('exit_code', [7, 143])
async def test_unexpected_worker_exit_retains_type_and_exit_code(exit_code):
    manager = RuntimeManager(command=[sys.executable, '-c', f"import json,sys;sys.stdin.readline();print(json.dumps({{'type':'runtime.ready'}}),flush=True);sys.stdin.readline();raise SystemExit({exit_code})"])
    events = []
    async def emit(kind, data): events.append((kind, data))
    try:
        await manager.start({'id': 'exit-probe'}, emit)
        # Trigger the post-startup exit only after the command channel is ready.
        process = manager.workers['exit-probe']['process']
        process.stdin.write(b'exit\n')
        await process.stdin.drain()
        await asyncio.wait_for(manager.workers['exit-probe']['reader'], 3)
        errors = [data for kind, data in events if kind == 'runtime.error']
        assert len(errors) == 1
        assert errors[0]['errorType'] == 'WorkerExitedError'
        assert errors[0]['exitCode'] == exit_code
    finally:
        await manager.close()


async def test_untyped_worker_error_does_not_override_generation_error_type():
    manager = RuntimeManager(command=[sys.executable, '-c', "import json,sys;sys.stdin.readline();print(json.dumps({'type':'runtime.ready'}),flush=True);sys.stdin.readline();print(json.dumps({'type':'runtime.error','error':'Untyped error'}),flush=True)"])
    events = []
    async def emit(kind, data): events.append((kind, data))
    try:
        await manager.start({'id': 'untyped-probe'}, emit)
        process = manager.workers['untyped-probe']['process']
        process.stdin.write(b'error\n')
        await process.stdin.drain()
        await asyncio.wait_for(manager.workers['untyped-probe']['reader'], 3)
        errors = [data for kind, data in events if kind == 'runtime.error']
        assert len(errors) == 1
        assert 'errorType' not in errors[0]
    finally:
        await manager.close()


@pytest.mark.parametrize('reported_session', ['identity-owner', 'different-owner'])
async def test_ready_worker_identity_is_private_and_bound_to_native_session(reported_session):
    script = (
        "import json,os,sys;sys.stdin.readline();"
        "print(json.dumps({'type':'runtime.ready','report':{'public':True},"
        f"'workerIdentity':{{'sessionId':{reported_session!r},'pid':os.getpid()}}}}),flush=True);"
        "sys.stdin.readline()"
    )
    manager = RuntimeManager(command=[sys.executable, '-c', script])
    events = []
    async def emit(kind, data): events.append((kind, data))
    try:
        await manager.start({'id': 'app-owner', 'runtimeSessionId': 'identity-owner'}, emit)
        row = manager.workers['app-owner']
        assert row.get('native_worker_pid') == (
            row['process'].pid if reported_session == 'identity-owner' else None)
        assert all('workerIdentity' not in value and 'native_worker_pid' not in value for _, value in events)
        assert next(value['report'] for kind, value in events if value.get('status') == 'ready') == {'public': True}
    finally:
        await manager.close()
