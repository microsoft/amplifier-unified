"""Mounted sessions may be idle; accepted inputs and controls may not be lost."""
import asyncio
import sys
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_updates
from amplifier_web.management import Management
from amplifier_web.runtime import RuntimeManager
from test_app_updates import prepared_activation


WORKER = r'''
import json, sys
pending = None
def emit(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    data = json.loads(line)
    if data['op'] == 'start':
        emit({'type': 'runtime.ready'})
    elif data['op'] == 'control':
        if data.get('arguments', {}).get('wait'):
            pending = data['id']
        else:
            emit({'op': 'reply', 'id': data['id'], 'result': {'plan': {}}})
    elif data['op'] == 'send':
        emit({'op': 'reply', 'id': data['id'], 'result': {'accepted': True}})
    elif data['op'] == 'release':
        if pending:
            emit({'op': 'reply', 'id': pending, 'result': {}})
            pending = None
        emit({'type': 'session.idle'})
    elif data['op'] == 'stop':
        break
'''


@pytest.fixture
async def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path / 'no-legacy-home'))
    service, manager, _ = await prepared_activation(tmp_path, monkeypatch)
    worker = tmp_path / 'worker.py'
    worker.write_text(WORKER)
    service.runtime = RuntimeManager(command=[sys.executable, str(worker)], startup_timeout=3)
    service.management = Management(service)
    await service.dispatch('session.create', {})
    service.state['updates']['phase'] = 'app-staged'
    yield service, manager
    await service.close()


async def test_finished_configuration_inspection_does_not_block_staged_update(prepared, monkeypatch):
    service, manager = prepared
    await service.management.command('configuration.inspect', {'id': service._session()['id']})
    assert service._session()['status'] == 'ready'
    assert not manager.busy()

    async def process(*args, **kwargs):
        return '99.0.0' if '-c' in args else ''

    restart = AsyncMock()
    monkeypatch.setattr(app_updates, 'process', process)
    monkeypatch.setattr(app_updates, 'request_managed_restart', restart)
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed', lambda home: True)
    await app_updates.activate(manager)
    restart.assert_awaited_once_with(manager)
    assert service.state['updates']['pendingRestart']['version'] == '99.0.0'


async def test_ready_during_cold_start_send_keeps_admitted_turn_protected(prepared, monkeypatch):
    service, manager = prepared
    entered, deliver = asyncio.Event(), asyncio.Event()
    original = service.runtime._request

    async def before_send(*args, **kwargs):
        entered.set()
        await deliver.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(service.runtime, '_request', before_send)
    send = asyncio.create_task(service.dispatch('conversation.send', {'text': 'Accepted work'}))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert service._session()['status'] == 'ready'
        assert manager.busy()
        target = AsyncMock(side_effect=AssertionError('must not replace a busy host'))
        monkeypatch.setattr(app_updates, 'installed_target', target)
        await app_updates.activate(manager)
        target.assert_not_awaited()
        deliver.set()
        await send
        # Receiving an admission receipt alone does not finish the turn.
        assert manager.busy()
        await service.on_runtime_event('runtime.status', {'sessionId': service._session()['id'], 'status': 'idle'})
        assert not manager.busy()
    finally:
        deliver.set()
        await send


@pytest.mark.parametrize('caller', ['waiting', 'cancelled', 'timed-out'])
async def test_runtime_control_stays_busy_until_worker_reply(prepared, monkeypatch, caller):
    service, manager = prepared
    session = service._session()
    await service.runtime.start(session, service.on_runtime_event)
    if caller == 'timed-out':
        original_wait = asyncio.wait_for

        async def short_control_timeout(awaitable, timeout):
            return await original_wait(awaitable, .01 if timeout == 600 else timeout)

        monkeypatch.setattr(asyncio, 'wait_for', short_control_timeout)
    request = asyncio.create_task(service.runtime.control(session['id'], 'tool.invoke', {'wait': True}))
    try:
        async with asyncio.timeout(3):
            while not service.runtime.has_pending_operations():
                await asyncio.sleep(0)
        assert session['status'] == 'ready'
        assert manager.busy()
        if caller == 'cancelled':
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        elif caller == 'timed-out':
            with pytest.raises(RuntimeError, match='may still be running'):
                await request
        # A disconnected/timed-out caller does not stop the worker's tool.
        assert manager.busy()
        await service.runtime._write(service.runtime.workers[session['id']], {'op': 'release'})
        if caller == 'waiting':
            await request
        async with asyncio.timeout(3):
            while service.runtime.has_pending_operations():
                await asyncio.sleep(0)
        assert not manager.busy()
    finally:
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)


async def test_rejected_transport_message_does_not_leave_runtime_busy(prepared, monkeypatch):
    service, manager = prepared
    session = service._session()
    await service.runtime.start(session, service.on_runtime_event)
    monkeypatch.setattr('amplifier_web.runtime_protocol.MAX_MESSAGE_BYTES', 200)
    with pytest.raises(ValueError, match='transport limit'):
        await service.runtime.control(session['id'], 'tool.invoke', {'text': 'x' * 500})
    assert not manager.busy()


async def test_worker_exit_clears_uncertain_cancelled_control(prepared):
    service, manager = prepared
    session = service._session()
    await service.runtime.start(session, service.on_runtime_event)
    request = asyncio.create_task(service.runtime.control(session['id'], 'tool.invoke', {'wait': True}))
    async with asyncio.timeout(3):
        while not service.runtime.has_pending_operations():
            await asyncio.sleep(0)
    request.cancel()
    await asyncio.gather(request, return_exceptions=True)
    assert manager.busy()
    await service.runtime.stop(session['id'])
    assert not manager.busy()


async def test_worker_start_is_busy_even_before_ready_status_is_published(prepared):
    service, manager = prepared
    entered, released = asyncio.Event(), asyncio.Event()

    async def emit(kind, payload):
        if kind == 'runtime.status' and payload['status'] == 'starting':
            entered.set()
            await released.wait()
        await service.on_runtime_event(kind, payload)

    start = asyncio.create_task(service.runtime.start(service._session(), emit))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert service._session()['status'] == 'idle'
        assert manager.busy()
    finally:
        released.set()
        await start
    assert not manager.busy()


@pytest.mark.parametrize('runtime', [None, object()])
async def test_ready_without_runtime_operation_api_is_idle(prepared, runtime):
    service, manager = prepared
    original = service.runtime
    try:
        service.runtime = runtime
        service._session()['status'] = 'ready'
        assert not manager.busy()
        service._session()['execution'] = {'turns': [{'phase': 'completed'}]}
        assert not manager.busy()
        service._session()['execution']['turns'].append({'phase': 'running'})
        assert manager.busy()
    finally:
        service.runtime = original


@pytest.mark.parametrize('guard', ['working', 'starting', 'stopping', 'configuration',
                                  'worker', 'persistent-worker', 'voice', 'feedback', 'smart-tool'])
async def test_existing_work_guards_still_defer_update(prepared, guard):
    service, manager = prepared
    session = service._session()
    session['status'] = 'ready'
    if guard in {'working', 'starting', 'stopping'}:
        session['status'] = guard
    elif guard == 'configuration':
        session['configurationBusy'] = True
    elif guard == 'worker':
        session['workers'] = [{'status': 'running'}]
    elif guard == 'persistent-worker':
        session['workers'] = [{'status': 'idle', 'persistent': True}]
    elif guard == 'voice':
        service.state['voice']['status'] = 'connected'
    elif guard == 'feedback':
        service.state['feedback'] = {'requests': [{'status': 'sending'}]}
    elif guard == 'smart-tool':
        service.state['smartTools'] = {'operations': [{'status': 'running'}]}
    assert manager.busy()
