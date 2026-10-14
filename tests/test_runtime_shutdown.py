"""Real process transport under an orderly host boundary, without model calls."""
import asyncio
import sys

import pytest

from amplifier_web.runtime import RuntimeManager, RuntimeHostClosingError
from amplifier_web.server import create_app
from amplifier_web.service import AppService


SCRIPT = r'''
import json, sys
for line in sys.stdin:
    data = json.loads(line)
    if data['op'] == 'start':
        print(json.dumps({'type': 'runtime.ready'}), flush=True)
    elif data['op'] == 'stop':
        print(json.dumps({'type': 'session.closed'}), flush=True)
        break
    elif data['op'] == 'control':
        if data['operation'] == 'hold':
            continue
        if data['operation'] == 'fail':
            print(json.dumps({'type': 'runtime.error', 'error': 'Reported failure'}), flush=True)
        print(json.dumps({'op': 'reply', 'id': data['id'], 'result': {}}), flush=True)
'''


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
            await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'working'})
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


async def test_pending_send_and_controls_keep_unknown_delivery_without_false_error(tmp_path):
    manager = RuntimeManager(command=[sys.executable, '-c', SCRIPT])
    manager.retention.wake = lambda: None
    service = AppService(tmp_path, runtime=manager, workspace=tmp_path)
    await service.dispatch('session.create', {})
    sid = service._session()['id']
    sending = asyncio.create_task(service.dispatch('conversation.send', {
        'sessionId': sid, 'text': 'Keep this pending request'}, command_id='pending-input'))
    async def admitted():
        while not manager.workers.get(sid, {}).get('sendInputs'):
            await asyncio.sleep(.001)
    await asyncio.wait_for(admitted(), 3)
    held = asyncio.create_task(service._guard(manager.control, (sid, 'hold')))
    while len(manager.workers[sid]['inflight']) < 2:
        await asyncio.sleep(.001)
    try:
        service.begin_shutdown()
        # An already scheduled control arriving after the boundary is rejected
        # without emitting a manufactured conversation failure.
        await service._guard(manager.control, (sid, 'must-not-run'))
        manager.workers[sid]['process'].terminate()
        with pytest.raises(RuntimeHostClosingError):
            await asyncio.wait_for(sending, 3)
        await asyncio.wait_for(held, 3)
        session = service._session(sid)
        assert session['status'] == 'interrupted'
        assert session['failure']['category'] == 'execution_interrupted'
        message = next(row for row in session['messages'] if row.get('inputId') == 'pending-input')
        assert message['text'] == 'Keep this pending request'
        assert message['delivery']['status'] == 'unknown'
        assert len(session['messages']) == 1
    finally:
        await service.close()
    restored = AppService(tmp_path, runtime=None, workspace=tmp_path)
    try:
        session = restored._session(sid)
        assert session['status'] == 'interrupted'
        assert session['failure']['category'] == 'execution_interrupted'
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
        assert session['failure']['category'] == 'execution_interrupted'
        if not external_cancel:
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
        assert session['failure']['category'] == 'execution_interrupted'
        if not external_cancel:
            assert session['messages'][0]['delivery']['status'] == 'unknown'
        assert not manager.workers
    finally:
        await service.close()
