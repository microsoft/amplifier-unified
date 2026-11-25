"""Real process transport under an orderly host boundary, without model calls."""
import asyncio
import copy
import json
import sys

import pytest

from amplifier_web.runtime import RuntimeManager, RuntimeHostClosingError
from amplifier_web.server import create_app
from amplifier_web.service import AppService


SCRIPT = r'''
import json, sys, time
for line in sys.stdin:
    data = json.loads(line)
    if data['op'] == 'start':
        print(json.dumps({'type': 'runtime.ready'}), flush=True)
    elif data['op'] == 'stop':
        print(json.dumps({'type': 'session.closed'}), flush=True)
        break
    elif data['op'] == 'control':
        if data['operation'] == 'begin-work':
            values = data['arguments']
            print(json.dumps({'type': 'generation.started',
              'generation_id': values['generationId'], 'initial_input_id': values['inputId'],
              'interruptionRevision': data['interruptionRevision'], 'observedAt': time.time()}), flush=True)
            print(json.dumps({'type': 'input.delivered', 'input_id': values['inputId']}), flush=True)
        if data['operation'] == 'hold':
            continue
        if data['operation'] == 'fail':
            print(json.dumps({'type': 'runtime.error', 'error': 'Reported failure'}), flush=True)
        print(json.dumps({'op': 'reply', 'id': data['id'], 'result': {}}), flush=True)
'''


async def owned_turn(service, manager, sid, input_id):
    """Observe the real reader's native owner binding, never stamp one in tests."""
    async with asyncio.timeout(3):
        while True:
            from amplifier_web.execution_working_set import current
            turn = next((row for row in current(service._session(sid)).get('turns', [])
                         if row.get('inputId') == input_id), None)
            if turn and turn.get('runtimeOwnerId') == manager.workers[sid]['runtimeOwnerId']:
                assert turn['generationId'] == 'generation-' + input_id
                return turn
            await asyncio.sleep(.001)


@pytest.mark.parametrize('shutdown', [False, True])
@pytest.mark.parametrize('phase', ['working', 'starting', 'ready'])
async def test_cancelled_send_reconciles_only_established_shutdown(tmp_path, phase, shutdown):
    from test_service import Runtime
    from amplifier_web.execution_working_set import current

    class Pending(Runtime):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()
            self._closed = False

        def begin_shutdown(self):
            self._closed = True

        async def send(self, session, text, input_id, emit):
            self.sent.append((session['id'], text, input_id))
            await emit('runtime.status', {'sessionId': session['id'], 'status': phase})
            self.entered.set()
            await asyncio.Future()

    runtime = Pending()
    service = AppService(tmp_path, runtime=runtime, workspace=tmp_path)
    sending = None
    try:
        await service.dispatch('session.create', {})
        session = service._session()
        sid = session['id']
        idle = service._new_session({'title': 'Unrelated idle conversation'})
        service.state['sessions'].append(idle)
        idle_before = copy.deepcopy(idle)
        sending = asyncio.create_task(service.dispatch('conversation.send', {
            'sessionId': sid, 'text': 'Saved but not acknowledged'}, command_id='cancelled-input'))
        await asyncio.wait_for(runtime.entered.wait(), 3)
        message = next(row for row in session['messages'] if row.get('inputId') == 'cancelled-input')
        assert message['delivery']['status'] == 'sending'
        assert not current(session).get('turns')
        if shutdown:
            service.begin_shutdown()
        sending.cancel('original caller cancellation')
        with pytest.raises(asyncio.CancelledError) as error:
            await sending
        assert error.value.args == ('original caller cancellation',)
        receipt = json.loads(service.db.execute('SELECT receipt FROM commands WHERE id=?',
                                                ('cancelled-input',)).fetchone()[0])
        print('CANCELLED_SEND_BOUNDARY', json.dumps({
            'shutdown': shutdown, 'phase': phase, 'status': session['status'],
            'delivery': message['delivery']['status'], 'receiptDelivery': receipt.get('delivery'),
            'turns': current(session).get('turns'), 'sent': len(runtime.sent)}))
        assert session['status'] == ('interrupted' if shutdown else phase)
        assert message['delivery']['status'] == ('unknown' if shutdown else 'sending')
        assert receipt.get('delivery') == message['delivery']['status']
        assert 'failure' not in session and not current(session).get('turns')
        assert len(session['messages']) == 1 and message['text'] == 'Saved but not acknowledged'
        assert runtime.sent == [(sid, 'Saved but not acknowledged', 'cancelled-input')]
        assert idle == idle_before
    finally:
        if sending and not sending.done():
            sending.cancel()
        if sending:
            await asyncio.gather(sending, return_exceptions=True)
        await service.close()
    if shutdown:
        restored = AppService(tmp_path, runtime=None, workspace=tmp_path)
        try:
            session = restored._session(sid)
            assert session['status'] == 'interrupted'
            assert session['messages'][0]['delivery']['status'] == 'unknown'
            assert 'failure' not in session and not current(session).get('turns')
        finally:
            await restored.close()


@pytest.mark.parametrize('change', [
    'new-input', 'new-epoch', 'new-runtime', 'new-owner', 'new-preparation',
    'same-input-retry', 'new-generation', 'accepted', 'failed', 'error', 'terminal',
])
async def test_shutdown_cancellation_does_not_reconcile_a_superseding_attempt(tmp_path, change):
    from test_service import Runtime
    from amplifier_web.execution_working_set import current
    from test_collaborative_workspaces import generation

    class Pending(Runtime):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()
            self.workers, self._preparations = {}, {}
        def begin_shutdown(self):
            pass
        async def send(self, session, text, input_id, emit):
            self.sent.append(input_id)
            self.entered.set()
            await asyncio.Future()

    runtime = Pending()
    service = AppService(tmp_path, runtime, workspace=tmp_path)
    sending = None
    try:
        await service.dispatch('session.create', {})
        session = service._session()
        sid = session['id']
        runtime.workers[sid] = {'runtimeOwnerId': 'original-owner'}
        sending = asyncio.create_task(service.dispatch('conversation.send', {
            'sessionId': sid, 'text': 'Original saved input'}, command_id='pending-input'))
        await asyncio.wait_for(runtime.entered.wait(), 3)
        message = session['messages'][-1]
        if change == 'new-input':
            service._message(session, 'user', 'New input', inputId='newer-input',
                             delivery={'status': 'sending'})
        elif change == 'new-epoch':
            session['interruptionRevision'] = 1
            session['status'] = 'stopping'
        elif change == 'new-runtime':
            service.runtime = Pending()
        elif change == 'new-owner':
            runtime.workers[sid] = {'runtimeOwnerId': 'replacement-owner'}
        elif change == 'new-preparation':
            runtime._preparations[sid] = {'runtimeOwnerId': 'new-preparation'}
        elif change == 'same-input-retry':
            old = message['delivery']
            service._delivery(session, 'pending-input', 'sending')
            assert message['delivery'] == old and message['delivery'] is not old
        elif change == 'new-generation':
            await generation(service, session, 'new-generation', ['newer-input'])
        elif change in {'accepted', 'failed'}:
            service._delivery(session, 'pending-input', change)
        elif change == 'error':
            session.update(status='error', error='Existing failure',
                           failure={'category': 'authentication', 'summary': 'Existing failure'})
        else:
            session['status'] = 'idle'
        before = copy.deepcopy({key: session.get(key) for key in
                                ('status', 'error', 'failure', 'messages', 'collaborationGeneration')})
        service.begin_shutdown()
        sending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sending
        after = {key: session.get(key) for key in before}
        if change in {'error', 'terminal'}:
            # Delivery of the same attempt remains unconfirmed, but its newer
            # terminal/error presentation cannot be replaced by cancellation.
            assert message['delivery']['status'] == 'unknown'
            before['messages'][0]['delivery'] = {'status': 'unknown'}
            if 'navigationPost' in before['messages'][0]:
                before['messages'][0]['navigationPost'] = message['navigationPost']
        assert after == before
        assert runtime.sent == ['pending-input']
        if change != 'new-generation':
            assert not current(session).get('turns')
    finally:
        if sending and not sending.done():
            sending.cancel()
        if sending:
            await asyncio.gather(sending, return_exceptions=True)
        service.runtime = runtime
        runtime.workers.clear()
        runtime._preparations.clear()
        await service.close()


@pytest.mark.parametrize('secondary', ['publication', 'second-cancel'])
async def test_shutdown_reconciliation_failure_preserves_original_cancellation(tmp_path, monkeypatch, caplog, secondary):
    from test_service import Runtime

    entered, reconciling = asyncio.Event(), asyncio.Event()
    class Pending(Runtime):
        async def send(self, session, text, input_id, emit):
            entered.set()
            await asyncio.Future()
    service = AppService(tmp_path, Pending(), workspace=tmp_path)
    sending = None
    publish = service._publish_changes
    original_lock = service.lock
    try:
        await service.dispatch('session.create', {})
        sid = service._session()['id']
        sending = asyncio.create_task(service.dispatch('conversation.send', {
            'sessionId': sid, 'text': 'Uncertain input'}, command_id='cancelled-input'))
        await asyncio.wait_for(entered.wait(), 3)
        service.begin_shutdown()
        if secondary == 'publication':
            def broken_publish(*args, **kwargs):
                raise OSError('synthetic shutdown publication failure')
            monkeypatch.setattr(service, '_publish_changes', broken_publish)
            sending.cancel('original cancellation')
        else:
            # _send's lookup follows lock acquisition; use the lock's acquire
            # boundary to observe its second wait without a timing-only sleep.
            class HeldLock:
                async def __aenter__(self):
                    reconciling.set()
                    await asyncio.Future()
                async def __aexit__(self, *args):
                    pass
            monkeypatch.setattr(service, 'lock', HeldLock())
            sending.cancel('original cancellation')
            await asyncio.wait_for(reconciling.wait(), 3)
            sending.cancel('later cancellation')
            monkeypatch.setattr(service, 'lock', original_lock)
        with pytest.raises(asyncio.CancelledError) as result:
            await sending
        assert result.value.args == ('original cancellation',)
        cause = result.value.__cause__
        assert isinstance(cause, OSError if secondary == 'publication' else asyncio.CancelledError)
        assert result.value.__notes__ == [
            'Shutdown input reconciliation was not confirmed; inspect the retained error.']
        assert 'Shutdown input reconciliation failed' in caplog.text
    finally:
        monkeypatch.setattr(service, '_publish_changes', publish)
        monkeypatch.setattr(service, 'lock', original_lock)
        if sending and not sending.done():
            sending.cancel()
        if sending:
            await asyncio.gather(sending, return_exceptions=True)
        await service.close()


async def test_shutdown_cancelled_explicit_retry_settles_only_its_unbound_turn(tmp_path):
    from test_service import Runtime
    from amplifier_web.execution_working_set import current

    class Pending(Runtime):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()
        async def send(self, session, text, input_id, emit):
            self.sent.append(input_id)
            self.entered.set()
            await asyncio.Future()
        async def retry(self, session, text, input_id, emit):
            return await self.send(session, text, input_id, emit)

    runtime = Pending()
    service = AppService(tmp_path, runtime, workspace=tmp_path)
    task = None
    try:
        await service.dispatch('session.create', {})
        sid = service._session()['id']
        task = asyncio.create_task(service.dispatch('conversation.send', {
            'sessionId': sid, 'text': 'Keep the exact saved input'}, command_id='retry-input'))
        await asyncio.wait_for(runtime.entered.wait(), 3)
        service.begin_shutdown()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service._session(sid)['messages'][0]['delivery']['status'] == 'unknown'
    finally:
        if task and not task.done():
            task.cancel()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await service.close()

    runtime = Pending()
    service = AppService(tmp_path, runtime, workspace=tmp_path)
    task = None
    try:
        session = service._session(sid)
        original_message = copy.deepcopy(session['messages'][0])
        assert not current(session).get('turns')
        task = asyncio.create_task(service.dispatch('conversation.retry', {
            'sessionId': sid, 'inputId': 'retry-input', 'confirmUncertain': True},
            command_id='explicit-retry'))
        await asyncio.wait_for(runtime.entered.wait(), 3)
        turn = current(session)['turns'][-1]
        assert turn['phase'] == 'running' and not turn.get('runtimeOwnerId') and not turn.get('generationId')
        service.begin_shutdown()
        task.cancel('retry cancelled by caller')
        with pytest.raises(asyncio.CancelledError, match='retry cancelled by caller'):
            await task
        assert turn['phase'] == 'interrupted' and turn.get('endedAt')
        assert session['status'] == 'interrupted' and 'failure' not in session
        assert session['messages'][0]['id'] == original_message['id']
        assert session['messages'][0]['delivery']['status'] == 'unknown'
        assert runtime.sent == ['retry-input']
    finally:
        if task and not task.done():
            task.cancel()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await service.close()
    restored = AppService(tmp_path, runtime=None, workspace=tmp_path)
    try:
        session = restored._session(sid)
        assert session['status'] == 'interrupted' and 'failure' not in session
        assert current(session)['turns'][-1]['phase'] == 'interrupted'
        assert session['messages'][0]['delivery']['status'] == 'unknown'
    finally:
        await restored.close()


@pytest.mark.parametrize('external_cancel', [False, True])
async def test_shutdown_reconciles_worker_created_by_send_after_confirmed_retirement(tmp_path, monkeypatch, external_cancel):
    script = SCRIPT.replace(
        "if data['operation'] == 'hold':",
        """if data['operation'] == 'fixture.idle':
            print(json.dumps({'type': 'session.idle'}), flush=True)
        if data['operation'] == 'hold':""").replace(
        "elif data['op'] == 'stop':",
        """elif data['op'] == 'retire':
        print(json.dumps({'op': 'reply', 'id': data['id'], 'result': {'retired': True}}), flush=True)
        print(json.dumps({'type': 'session.closed'}), flush=True)
        break
    elif data['op'] == 'stop':""")
    manager = RuntimeManager(command=[sys.executable, '-c', script])
    manager.retention.wake = lambda: None
    service = AppService(tmp_path, manager, workspace=tmp_path)
    sending = None
    writes = []
    write_completed = asyncio.Event()
    original_write = manager._write
    async def record_write(row, payload):
        if payload['op'] == 'send':
            writes.append((row['runtimeOwnerId'], payload['input_id']))
        await original_write(row, payload)
        if payload['op'] == 'send':
            write_completed.set()
    monkeypatch.setattr(manager, '_write', record_write)
    try:
        await service.dispatch('session.create', {})
        session = service._session()
        sid = session['id']
        await manager.start(session, service.on_runtime_event)
        old = manager.workers[sid]
        # Only the original worker is idle. The replacement reports ordinary
        # runtime.ready but deliberately never emits input.delivered or an ACK.
        # Raw runtime.status is not a worker protocol event and is ignored.
        await manager.control(sid, 'fixture.idle')
        async with asyncio.timeout(3):
            while not old.get('idle'):
                await asyncio.sleep(.001)
        assert session['status'] == 'idle' and manager.retention.eligible(old)
        assert not manager.retention.protected(sid)
        old['update_pending'] = True
        sending = asyncio.create_task(service.dispatch('conversation.send', {
            'sessionId': sid, 'text': 'Input belongs to replacement'}, command_id='replacement-input'))
        async with asyncio.timeout(5):
            while (manager.workers.get(sid) is old or not manager.workers.get(sid, {}).get('sendInputs')
                   or session['status'] != 'ready'):
                if sending.done():
                    sending.result()  # Preserve a causal error, not a later timeout.
                await asyncio.sleep(.001)
            await write_completed.wait()
        replacement = manager.workers[sid]
        assert old['process'].returncode == 0 and old['retirement_task'].result() is True
        assert old.get('executionEnded')
        assert replacement['runtimeOwnerId'] != old['runtimeOwnerId']
        assert replacement['inputId'] is None and not sending.done()
        assert session['messages'][0]['delivery']['status'] == 'sending'
        print('RETIRED_SEND_BOUNDARY', json.dumps({
            'oldOwner': old['runtimeOwnerId'], 'oldExit': old['process'].returncode,
            'retired': old['retirement_task'].result(), 'oldExecutionEnded': old['executionEnded'],
            'newOwner': replacement['runtimeOwnerId'], 'status': session['status'],
            'inputId': replacement['inputId'], 'sendInputs': list(replacement['sendInputs'].values()),
            'writes': writes, 'writeCompleted': write_completed.is_set(), 'externalCancel': external_cancel}))
        service.begin_shutdown()
        if external_cancel:
            sending.cancel()
        await manager.close()
        with pytest.raises(asyncio.CancelledError if external_cancel else RuntimeHostClosingError):
            await sending
        assert session['status'] == 'interrupted' and 'failure' not in session
        assert session['messages'][0]['delivery']['status'] == 'unknown'
        assert writes == [(replacement['runtimeOwnerId'], 'replacement-input')]
        assert not manager.workers
    finally:
        if sending and not sending.done():
            sending.cancel()
        await service.close()
        if sending:
            await asyncio.gather(sending, return_exceptions=True)


async def test_unconfirmed_desktop_receipt_survives_owned_process_stop_without_false_release(tmp_path):
    import json
    script = SCRIPT.replace(
        "print(json.dumps({'type': 'runtime.ready'}), flush=True)",
        """print(json.dumps({'type': 'runtime.ready'}), flush=True)
        print(json.dumps({'type': 'computer.settlement', 'state': 'stopping', 'settled': False,
          'episodes': [{'identity': {'owner': 'mount', 'target': 'opaque-target', 'generation': 'episode'},
            'state': 'unknown', 'workers': 1, 'admission_open': False, 'release_confirmed': False}],
          'omittedEpisodes': 0}), flush=True)""")
    manager = RuntimeManager(command=[sys.executable, "-c", script])
    manager.home = tmp_path / "app"
    manager.retention.wake = lambda: None
    events = []
    async def emit(kind, value):
        events.append((kind, value))
    try:
        await manager.start({"id": "custody", "workspace": str(tmp_path)}, emit)
        async with asyncio.timeout(2):
            while "computerSettlement" not in manager.workers["custody"]:
                await asyncio.sleep(.001)
        row = manager.workers["custody"]
        assert row["computerSettlement"]["settled"] is False
        await manager.stop("custody")
        assert row["process"].returncode is not None
        assert "custody" not in manager.workers
        receipt = json.loads((manager.home / "runtime-reports/custody/computer-settlement.json").read_text())
        assert receipt["settled"] is False and receipt["episodes"][0]["workers"] == 1
        assert not receipt["episodes"][0]["release_confirmed"]
        assert any(kind == "runtime.activity" and value.get("phase") == "stopping"
                   for kind, value in events)
    finally:
        await manager.close()


async def test_cleanup_scoped_receipt_replaces_pending_snapshot_without_claiming_whole_computer_release(tmp_path):
    import json
    script = SCRIPT.replace(
        "if data['operation'] == 'hold':",
        """if data['operation'] in {'pending-cleanup', 'settled-cleanup'}:
            settled = data['operation'] == 'settled-cleanup'
            print(json.dumps({'type': 'computer.settlement', 'scope': 'cleanup',
              'state': 'settled' if settled else 'stopping', 'settled': settled,
              'episodes': [] if settled else [{'state': 'unknown', 'reason': 'child_cleanup_unconfirmed',
                'workers': None, 'release_confirmed': False}], 'omittedEpisodes': 0}), flush=True)
        if data['operation'] == 'hold':""")
    manager = RuntimeManager(command=[sys.executable, "-c", script])
    manager.home = tmp_path / "app"
    manager.retention.wake = lambda: None
    events = []
    async def emit(kind, value):
        events.append((kind, value))
    try:
        await manager.start({"id": "cleanup-scope", "workspace": str(tmp_path)}, emit)
        await manager.control("cleanup-scope", "pending-cleanup")
        row = manager.workers["cleanup-scope"]
        assert row["computerSettlement"]["scope"] == "cleanup"
        assert row["computerSettlement"]["settled"] is False
        assert any(kind == "runtime.activity" and "cleanup is unconfirmed" in value.get("detail", "")
                   for kind, value in events)
        await manager.control("cleanup-scope", "settled-cleanup")
        receipt = row["computerSettlement"]
        assert receipt == {"state": "settled", "settled": True, "scope": "cleanup",
                           "episodes": [], "omittedEpisodes": 0}
        assert "parked" not in row  # Cleanup receipt does not release its writer.
        await manager.stop("cleanup-scope")
        saved = json.loads((manager.home / "runtime-reports/cleanup-scope/computer-settlement.json").read_text())
        assert saved == receipt and "release_confirmed" not in saved
    finally:
        await manager.close()


@pytest.mark.parametrize('boundary', ['http', 'service'])
async def test_shutdown_before_async_cleanup_preserves_idle_and_interrupts_active(
        tmp_path, authenticated_client, monkeypatch, boundary):
    manager = RuntimeManager(command=[sys.executable, '-c', SCRIPT])
    manager.retention.wake = lambda: None
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=manager, voice=False, background_updates=False)
    service = app['service']
    client = await authenticated_client(app)
    sessions, before_messages = {}, {}
    for status in ('idle', 'working', 'error'):
        response = await client.post('/api/actions', json={'action': 'session.create', 'args': {'title': status}})
        assert response.status == 200
        sid = (await response.json())['state']['selectedSessionId']
        session = service._session(sid)
        await manager.start(session, service.on_runtime_event)
        await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'idle'})
        if status == 'error':
            await manager.control(sid, 'fail')
        elif status == 'working':
            await manager.control(sid, 'begin-work', {
                'inputId': 'active-input', 'generationId': 'generation-active-input'})
            await owned_turn(service, manager, sid, 'active-input')
        assert session['status'] == status
        sessions[status] = sid
        before_messages[sid] = list(session.get('messages', []))

    entered, release = asyncio.Event(), asyncio.Event()
    original = service.performance_diagnostics.close
    async def delayed_close():
        entered.set()
        await release.wait()
        await original()
    monkeypatch.setattr(service.performance_diagnostics, 'close', delayed_close)
    closing = None
    try:
        if boundary == 'http':
            stream = await client.get('/api/events')
            assert await stream.content.readline() == b'event: state\n'
            await app.shutdown()
            stream.close()
        else:
            closing = asyncio.create_task(service.close())
            await asyncio.wait_for(entered.wait(), 3)
        assert service.closed and manager._closed
        assert all(row['closing'] for row in manager.workers.values())
        with pytest.raises(RuntimeError, match='host is closing'):
            await manager.control(sessions['idle'], 'must-not-run')
        # A child exits while cleanup is still blocked. The lifecycle boundary,
        # not its exit code or a late blanket error reset, supplies provenance.
        for row in list(manager.workers.values()):
            row['process'].terminate()
        await asyncio.wait_for(asyncio.gather(*(row['reader'] for row in manager.workers.values())), 5)
        assert service._session(sessions['idle'])['status'] == 'idle'
        assert service._session(sessions['working'])['status'] == 'interrupted'
        active = service._session(sessions['working'])
        assert active['failure']['category'] == 'execution_interrupted'
        assert active['execution']['turns'][-1]['phase'] == 'interrupted'
        failed = service._session(sessions['error'])
        assert failed['status'] == 'error' and failed['error'] == 'Reported failure'
        assert 'error' not in service._session(sessions['idle'])
    finally:
        release.set()
        if closing:
            await closing
        else:
            await service.close()
    restored = AppService(tmp_path, runtime=None, workspace=tmp_path)
    try:
        for original_status, final_status in [('idle', 'idle'), ('working', 'interrupted'), ('error', 'error')]:
            sid = sessions[original_status]
            assert restored._session(sid)['status'] == final_status
            assert restored._session(sid).get('messages', []) == before_messages[sid]
    finally:
        await restored.close()


async def test_manager_shutdown_fences_before_retention_cleanup(tmp_path, monkeypatch):
    events = []
    async def emit(kind, data): events.append((kind, data))
    manager = RuntimeManager(command=[sys.executable, '-c', SCRIPT])
    manager.retention.wake = lambda: None
    await manager.start({'id': 'owned', 'workspace': str(tmp_path)}, emit)
    entered, release = asyncio.Event(), asyncio.Event()
    original = manager.retention.close
    async def delayed_close():
        entered.set()
        await release.wait()
        await original()
    monkeypatch.setattr(manager.retention, 'close', delayed_close)
    closing = asyncio.create_task(manager.close())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        manager.workers['owned']['process'].terminate()
        await asyncio.wait_for(manager.workers['owned']['reader'], 3)
        assert not any(kind == 'runtime.error' for kind, _ in events)
        ended = [data for kind, data in events if kind == 'runtime.ended']
        assert len(ended) == 1 and ended[0]['reason'] == 'shutdown'
        assert ended[0]['status'] == 'interrupted'
    finally:
        release.set()
        await closing


async def test_shutdown_during_spawn_reaps_worker_without_startup_error(tmp_path, monkeypatch):
    manager = RuntimeManager(command=[sys.executable, '-c', SCRIPT])
    manager.retention.wake = lambda: None
    events, spawned = [], []
    async def emit(kind, data): events.append((kind, data))
    entered, release = asyncio.Event(), asyncio.Event()
    original = asyncio.create_subprocess_exec
    async def delayed_spawn(*args, **kwargs):
        proc = await original(*args, **kwargs)
        spawned.append(proc)
        entered.set()
        await release.wait()
        return proc
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed_spawn)
    starting = asyncio.create_task(manager.start({'id': 'starting', 'workspace': str(tmp_path)}, emit))
    await asyncio.wait_for(entered.wait(), 3)
    manager.begin_shutdown()
    release.set()
    try:
        with pytest.raises(RuntimeHostClosingError):
            await asyncio.wait_for(starting, 3)
        assert spawned[0].returncode is not None and not manager.workers
        assert not any(kind == 'runtime.error' for kind, _ in events)
        assert any(kind == 'runtime.ended' and data.get('reason') == 'shutdown' for kind, data in events)
    finally:
        await manager.close()


@pytest.mark.parametrize('external_cancel', [False, True])
@pytest.mark.parametrize('native_started', [False, True])
async def test_pending_send_and_controls_keep_unknown_delivery_without_false_error(tmp_path, monkeypatch, native_started, external_cancel):
    # Transport submission need not mean native execution has begun. Qualify
    # both that admission gap and an owned turn whose reply/ACK never returns.
    script = SCRIPT if not native_started else SCRIPT.replace(
        "elif data['op'] == 'stop':",
        """elif data['op'] == 'send':
        print(json.dumps({'type': 'generation.started',
          'generation_id': 'generation-' + data['input_id'], 'initial_input_id': data['input_id'],
          'interruptionRevision': data['interruptionRevision'], 'observedAt': time.time()}), flush=True)
        print(json.dumps({'type': 'assistant.delta', 'text': 'Retained incomplete reply',
          'input_ids': [data['input_id']], 'generation_id': 'generation-' + data['input_id']}), flush=True)
    elif data['op'] == 'stop':""")
    manager = RuntimeManager(command=[sys.executable, '-c', script])
    manager.retention.wake = lambda: None
    service = AppService(tmp_path, runtime=manager, workspace=tmp_path)
    sending = held = primary_error = None
    sends = []
    write = manager._write
    async def observe_write(row, payload):
        if payload.get('op') == 'send':
            sends.append(payload['input_id'])
        return await write(row, payload)
    monkeypatch.setattr(manager, '_write', observe_write)
    try:
        await service.dispatch('session.create', {})
        sid = service._session()['id']
        sending = asyncio.create_task(service.dispatch('conversation.send', {
            'sessionId': sid, 'text': 'Keep this pending request'}, command_id='pending-input'))
        async def admitted():
            while not manager.workers.get(sid, {}).get('sendInputs'):
                await asyncio.sleep(.001)
        await asyncio.wait_for(admitted(), 3)
        if native_started:
            await owned_turn(service, manager, sid, 'pending-input')
            async with asyncio.timeout(3):
                while service._session(sid).get('streaming') != 'Retained incomplete reply':
                    await asyncio.sleep(.001)
            assert service._session(sid)['streamingInputId'] == 'pending-input'
        else:
            from amplifier_web.execution_working_set import current
            assert not current(service._session(sid)).get('turns')
        held = asyncio.create_task(service._guard(manager.control, (sid, 'hold')))
        async with asyncio.timeout(3):
            while len(manager.workers[sid]['inflight']) < 2:
                await asyncio.sleep(.001)
        assert manager.workers[sid]['inputId'] is None  # No input.delivered event.
        assert not sending.done()  # No send reply/ACK.
        service.begin_shutdown()
        if external_cancel:
            sending.cancel()
        # An already scheduled control arriving after the boundary is rejected
        # without emitting a manufactured conversation failure.
        await service._guard(manager.control, (sid, 'must-not-run'))
        manager.workers[sid]['process'].terminate()
        with pytest.raises(asyncio.CancelledError if external_cancel else RuntimeHostClosingError):
            await asyncio.wait_for(sending, 3)
        await asyncio.wait_for(held, 3)
        await asyncio.wait_for(manager.workers[sid]['reader'], 3)
        session = service._session(sid)
        assert session['status'] == 'interrupted'
        if native_started:
            assert session['failure']['category'] == 'execution_interrupted'
            partial = next(row for row in session['messages'] if row.get('partial'))
            assert partial['source'] == 'interrupted-stream'
            assert partial['text'] == '[Interrupted response]\n\nRetained incomplete reply'
            assert partial['inputId'] == 'pending-input'
        else:
            assert not current(session).get('turns')
            assert 'failure' not in session  # No native turn was observed.
        message = next(row for row in session['messages'] if row.get('inputId') == 'pending-input')
        assert message['text'] == 'Keep this pending request'
        assert message['delivery']['status'] == 'unknown'
        assert len(session['messages']) == (2 if native_started else 1)
        assert sends == ['pending-input']
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            await service.close()
        except BaseException as exc:
            if primary_error is None:
                raise
            print(f'Shutdown fixture cleanup also failed: {exc!r}', file=sys.stderr)
        finally:
            tasks = [task for task in (sending, held) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    restored = AppService(tmp_path, runtime=None, workspace=tmp_path)
    try:
        session = restored._session(sid)
        assert session['status'] == 'interrupted'
        if native_started:
            assert session['failure']['category'] == 'execution_interrupted'
            assert session['messages'][-1]['partial'] is True
            assert session['messages'][-1]['inputId'] == 'pending-input'
        else:
            assert 'failure' not in session
        assert session['messages'][0]['delivery']['status'] == 'unknown'
    finally:
        await restored.close()


@pytest.mark.parametrize('external_cancel', [False, True])
async def test_registered_unready_shutdown_settles_input_without_startup_error(tmp_path, external_cancel):
    script = SCRIPT.replace("print(json.dumps({'type': 'runtime.ready'}), flush=True)", 'continue')
    manager = RuntimeManager(command=[sys.executable, '-c', script])
    manager.retention.wake = lambda: None
    service = AppService(tmp_path, runtime=manager, workspace=tmp_path)
    await service.dispatch('session.create', {})
    sid = service._session()['id']
    sending = asyncio.create_task(service.dispatch('conversation.send', {
        'sessionId': sid, 'text': 'Preserve preparation'}, command_id='preparing-input'))
    async def registered():
        while sid not in manager.workers:
            await asyncio.sleep(.001)
    await asyncio.wait_for(registered(), 3)
    assert not manager.workers[sid]['ready'].done()
    service.begin_shutdown()
    if external_cancel:
        sending.cancel()
    try:
        await asyncio.wait_for(manager.close(), 3)
        with pytest.raises(asyncio.CancelledError if external_cancel else RuntimeHostClosingError):
            await sending
        session = service._session(sid)
        assert session['status'] == 'interrupted'
        from amplifier_web.execution_working_set import current
        # Preparation never admitted a native turn. Preserve uncertainty without
        # inventing execution or a provider/startup failure to justify it.
        assert not current(session).get('turns') and 'failure' not in session
        assert session['messages'][0]['delivery']['status'] == 'unknown'
    finally:
        await service.close()


@pytest.mark.parametrize('outcome', ['refused', 'failed', 'accepted'])
async def test_retirement_reply_cannot_reopen_shutdown_owner(tmp_path, monkeypatch, outcome):
    events = []
    async def emit(kind, data): events.append((kind, data))
    manager = RuntimeManager(command=[sys.executable, '-c', SCRIPT])
    manager.retention.wake = lambda: None
    await manager.start({'id': 'owned', 'workspace': str(tmp_path)}, emit)
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed_reply(*args, **kwargs):
        entered.set()
        await release.wait()
        if outcome == 'failed':
            raise RuntimeHostClosingError()
        return {'retired': outcome == 'accepted'}
    monkeypatch.setattr(manager, '_request_unlocked', delayed_reply)
    row = manager.workers['owned']
    retiring = manager.retention.schedule('owned', row)
    try:
        await asyncio.wait_for(entered.wait(), 3)
        manager.begin_shutdown()
        release.set()
        await asyncio.wait_for(retiring, 3)
        assert row['closing'] and row['stop_reason'] == 'shutdown'
        row['process'].terminate()
        await asyncio.wait_for(row['reader'], 3)
        assert not any(kind == 'runtime.error' for kind, _ in events)
    finally:
        release.set()
        await manager.close()


@pytest.mark.parametrize('external_cancel', [False, True])
async def test_queued_send_behind_retirement_reports_shutdown_not_task_cancellation(tmp_path, external_cancel):
    manager = RuntimeManager(command=[sys.executable, '-c', SCRIPT])
    manager.retention.wake = lambda: None
    service = AppService(tmp_path, runtime=manager, workspace=tmp_path)
    await service.dispatch('session.create', {})
    session = service._session()
    sid = session['id']
    await manager.start(session, service.on_runtime_event)
    row = manager.workers[sid]
    manager.retention.schedule(sid, row)
    sending = asyncio.create_task(service.dispatch('conversation.send', {
        'sessionId': sid, 'text': 'Preserve queued input'}, command_id='queued-input'))
    async def queued():
        while not session.get('messages') or not row['inflight']:
            await asyncio.sleep(.001)
    await asyncio.wait_for(queued(), 3)
    assert not sending.done() and not row.get('sendInputs')
    service.begin_shutdown()
    if external_cancel:
        sending.cancel()
    try:
        await asyncio.wait_for(manager.close(), 3)
        with pytest.raises(asyncio.CancelledError if external_cancel else RuntimeHostClosingError):
            await sending
        assert session['status'] == 'interrupted'
        from amplifier_web.execution_working_set import current
        assert not current(session).get('turns') and 'failure' not in session
        assert session['messages'][0]['delivery']['status'] == 'unknown'
        assert not manager.workers
    finally:
        await service.close()


async def test_shutdown_settles_dead_worker_after_reader_error_preserving_unknown_input(tmp_path, caplog):
    manager = RuntimeManager(command=[sys.executable, '-c', SCRIPT])
    manager.retention.wake = lambda: None
    service = AppService(tmp_path, runtime=manager, workspace=tmp_path)
    await service.dispatch('session.create', {})
    session = service._session()
    sid = session['id']
    fail_reporting = False
    async def emit(kind, data):
        if fail_reporting and kind == 'runtime.error':
            raise OSError('retained reader publication failure')
        await service.on_runtime_event(kind, data)
    await manager.start(session, emit)
    row = manager.workers[sid]
    sending = asyncio.create_task(service.dispatch('conversation.send', {
        'sessionId': sid, 'text': 'Do not replay this input'}, command_id='unknown-input'))
    async def admitted():
        while not row.get('sendInputs'):
            await asyncio.sleep(.001)
    try:
        await asyncio.wait_for(admitted(), 3)
        row['emit'] = emit
        await service.on_runtime_event('runtime.error', {
            'sessionId': sid, 'error': 'Original saved failure'})
        fail_reporting = True
        row['process'].terminate()
        await asyncio.wait_for(row['process'].wait(), 3)
        with pytest.raises(OSError, match='retained reader publication failure'):
            await row['reader']
        with pytest.raises(RuntimeError):
            await sending
        reader_failure = row['reader'].exception()
        await manager.close()
        assert not manager.workers
        assert row['executionEnded'] and row['reader'].exception() is reader_failure
        assert not row['generation_lease'].exists()
        assert 'retained reader publication failure' in caplog.text
        assert session['status'] == 'error' and session['error'] == 'Original saved failure'
        assert len(session['messages']) == 1
        assert session['messages'][0]['delivery']['status'] == 'unknown'
    finally:
        fail_reporting = False
        await service.close()
        await asyncio.gather(sending, return_exceptions=True)


@pytest.mark.parametrize('failed_event', ['runtime.ended', 'runtime.warmth'])
async def test_failed_stop_retries_only_dead_worker_settlement(tmp_path, failed_event, caplog):
    events = []
    fail_publication = False
    async def emit(kind, data):
        if fail_publication and kind == failed_event:
            raise OSError('retained settlement publication failure')
        events.append((kind, data))
    manager = RuntimeManager(command=[sys.executable, '-c', SCRIPT])
    manager.retention.wake = lambda: None
    await manager.start({'id': 'owned', 'workspace': str(tmp_path)}, emit)
    row = manager.workers['owned']
    lease = row['generation_lease']
    fail_publication = True
    try:
        row['process'].terminate()
        await asyncio.wait_for(row['process'].wait(), 3)
        await asyncio.gather(row['reader'], return_exceptions=True)
        with pytest.raises(OSError, match='retained settlement publication failure'):
            await manager.stop('owned', reason='shutdown')
        first_stop = row['stop_task']
        assert manager.workers['owned'] is row and lease.exists()
        assert row.get('executionEnded', False) is (failed_event == 'runtime.warmth')
        await manager.close()
        assert manager.workers['owned'] is row and lease.exists()
        fail_publication = False
        await manager.close()
        assert not manager.workers and not lease.exists()
        assert isinstance(first_stop.exception(), OSError)
        assert 'retained settlement publication failure' in caplog.text
        ended = [data for kind, data in events if kind == 'runtime.ended']
        assert len(ended) == 1 and ended[0]['runtimeOwnerId'] == row['runtimeOwnerId']
    finally:
        fail_publication = False
        await manager.close()


@pytest.mark.parametrize('cancelled', [False, True])
async def test_failed_stop_does_not_retry_or_clear_unconfirmed_process(tmp_path, monkeypatch, cancelled):
    events = []
    async def emit(kind, data): events.append((kind, data))
    manager = RuntimeManager(command=[sys.executable, '-c', SCRIPT])
    manager.retention.wake = lambda: None
    await manager.start({'id': 'owned', 'workspace': str(tmp_path)}, emit)
    row = manager.workers['owned']
    writes = []
    async def failed_write(*args):
        writes.append(args)
        raise asyncio.CancelledError() if cancelled else OSError('stop transport unavailable')
    monkeypatch.setattr(manager, '_write', failed_write)
    try:
        with pytest.raises(asyncio.CancelledError if cancelled else OSError):
            await manager.stop('owned', reason='shutdown')
        first_stop = row['stop_task']
        await manager.close()
        assert row['process'].returncode is None
        assert manager.workers['owned'] is row and row['stop_task'] is first_stop
        assert len(writes) == 1 and row['generation_lease'].exists()
        assert not row.get('executionEnded')
        row['process'].terminate()
        await asyncio.wait_for(row['process'].wait(), 3)
        await manager.close()
        assert not manager.workers and len(writes) == 1
        assert first_stop.cancelled() if cancelled else isinstance(first_stop.exception(), OSError)
    finally:
        if row['process'].returncode is None:
            row['process'].kill()
            await row['process'].wait()
        await manager.close()


@pytest.mark.parametrize('cancel_target', ['caller', 'stop_task', 'reader'])
async def test_dead_worker_settlement_preserves_caller_cancellation(tmp_path, cancel_target):
    entered, release = asyncio.Event(), asyncio.Event()
    events = []
    async def emit(kind, data):
        if kind == 'runtime.error':
            entered.set()
            await release.wait()
        events.append((kind, data))
    manager = RuntimeManager(command=[sys.executable, '-c', SCRIPT])
    manager.retention.wake = lambda: None
    await manager.start({'id': 'owned', 'workspace': str(tmp_path)}, emit)
    row = manager.workers['owned']
    caller = None
    try:
        row['process'].terminate()
        await asyncio.wait_for(row['process'].wait(), 3)
        await asyncio.wait_for(entered.wait(), 3)
        caller = asyncio.create_task(manager.stop('owned', reason='shutdown'))
        while not row.get('stop_task'):
            await asyncio.sleep(.001)
        if cancel_target == 'reader':
            row['reader'].cancel()
            await asyncio.wait_for(caller, 3)
        else:
            (caller if cancel_target == 'caller' else row['stop_task']).cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert manager.workers['owned'] is row and row['generation_lease'].exists()
            assert not row.get('executionEnded') and not row['reader'].done()
            release.set()
            await asyncio.wait_for(manager.stop('owned', reason='shutdown'), 3)
        assert not manager.workers and not row['generation_lease'].exists()
        assert len([kind for kind, _ in events if kind == 'runtime.ended']) == 1
    finally:
        release.set()
        if caller:
            await asyncio.gather(caller, return_exceptions=True)
        await manager.close()
