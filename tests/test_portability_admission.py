"""Actions waiting on history must recheck the original task's transfer owner."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.host.storage import SessionStore
from amplifier_web.service import AppError
from test_portability import export, host_env, setup_hosts


@pytest.fixture
async def hosts(tmp_path, monkeypatch):
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    host_env(monkeypatch, source)
    # Even an unfixed admission bug must never start a real runtime/provider.
    send = AsyncMock(return_value=None)
    monkeypatch.setattr(app, '_send', send)
    # Selection preparation is unrelated to dispatch ownership and must not
    # start a provider or race these exact before/after mutation assertions.
    monkeypatch.setattr(app.warmup, 'schedule', AsyncMock(return_value=None))
    control = AsyncMock(side_effect=AssertionError('A delayed action must not reach the runtime'))
    monkeypatch.setattr(app.runtime, 'control', control)
    host = SimpleNamespace(app=app, target=target, source=source, destination=destination,
                           root=root, destrepo=destrepo, sid=sid, send=send, control=control)
    try:
        yield host
    finally:
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()


def delayed_dispatch(host, monkeypatch, action, args, command):
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = host.app.history.ensure_loaded
    task = None

    async def paused_history(sid):
        result = await original(sid)
        if asyncio.current_task() is task:
            assert sid == host.sid
            entered.set()
            await proceed.wait()
        return result

    monkeypatch.setattr(host.app.history, 'ensure_loaded', paused_history)
    task = asyncio.create_task(host.app.dispatch(action, args, command_id=command))
    return task, entered, proceed


async def resolve_transfer(host, monkeypatch, resolution):
    receipt, _ = await export(host.app, host.target, host.sid, host.root)
    if resolution == 'cancel':
        row = (await host.app.dispatch('portability.cancel', {'sessionId': host.sid,
            'id': receipt['id'], 'expectedRevision': receipt['revision'],
            'evidence': 'The destination has not received a source release.'}))['result']
        assert row['phase'] == 'cancelled'
        assert not host.app.portability.fenced(host.sid)
        return
    host_env(monkeypatch, host.destination)
    staged = (await host.target.dispatch('portability.stage',
        {'path': receipt['package'], 'repository': str(host.destrepo)}))['result']
    host_env(monkeypatch, host.source)
    row = (await host.app.dispatch('portability.release', {'sessionId': host.sid,
        'id': receipt['id'], 'expectedRevision': receipt['revision'],
        'path': staged['receiptPath']}))['result']
    assert row['phase'] == 'released'
    assert host.app.portability.fenced(host.sid)


def native_files(host):
    directory = SessionStore.for_app(host.app.data_dir, host.root).directory(host.sid)
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob('*') if path.is_file()}


async def assert_rejected_without_mutation(host, task, proceed, command):
    sessions = copy.deepcopy(host.app.state['sessions'])
    files = native_files(host)
    proceed.set()
    with pytest.raises(AppError) as rejected:
        await asyncio.wait_for(task, 5)
    assert rejected.value.status == 409
    assert host.app.state['sessions'] == sessions
    assert native_files(host) == files
    assert not host.app.db.execute('SELECT 1 FROM commands WHERE id=?', (command,)).fetchone()
    assert not any(event.get('id') == command for event in host.app.state['events'])
    host.send.assert_not_awaited()
    host.control.assert_not_awaited()
    assert not host.app.runtime.workers


@pytest.mark.parametrize('action', ['conversation.send', 'session.naming'])
@pytest.mark.parametrize('resolution', ['cancel', 'release'])
async def test_explicit_action_waiting_on_history_cannot_cross_transfer(hosts, monkeypatch, action, resolution):
    host = hosts
    args = ({'sessionId': host.sid, 'text': 'Submitted before ownership changed'}
            if action == 'conversation.send' else {'id': host.sid, 'automatic': False})
    command = 'delayed-' + action + '-' + resolution
    task, entered, proceed = delayed_dispatch(host, monkeypatch, action, args, command)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await resolve_transfer(host, monkeypatch, resolution)
        await assert_rejected_without_mutation(host, task, proceed, command)
    finally:
        proceed.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('change_selection', [False, True])
async def test_implicit_send_keeps_original_task_ownership_after_await(hosts, monkeypatch, change_selection):
    host = hosts
    other = (await host.app.dispatch('session.create',
        {'workspace': str(host.root), 'select': False}))['sessionId']
    assert host.app.state['selectedSessionId'] == host.sid
    command = 'delayed-implicit-send'
    task, entered, proceed = delayed_dispatch(host, monkeypatch, 'conversation.send',
        {'text': 'This belongs only to the original selection'}, command)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await resolve_transfer(host, monkeypatch, 'cancel')
        if change_selection:
            await host.app.dispatch('session.select', {'id': other})
        await assert_rejected_without_mutation(host, task, proceed, command)
        assert host.app.state['selectedSessionId'] == (other if change_selection else host.sid)
    finally:
        proceed.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_implicit_send_cannot_retarget_while_waiting_for_admission_lock(hosts):
    host = hosts
    other = (await host.app.dispatch('session.create',
        {'workspace': str(host.root), 'select': False}))['sessionId']
    command = 'lock-implicit-send'
    # Another action already holds the lock. The waiting send resolved its
    # original task before acquiring that lock, while selection changes within
    # the lock holder's own atomic operation.
    async with host.app.lock:
        task = asyncio.create_task(host.app.dispatch('conversation.send',
            {'text': 'Only send to the original selected task'}, command_id=command))
        await asyncio.sleep(0)
        assert not task.done()
        host.app.state['selectedSessionId'] = other
    try:
        await assert_rejected_without_mutation(host, task, asyncio.Event(), command)
        assert host.app.state['selectedSessionId'] == other
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
