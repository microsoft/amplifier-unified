"""Current-main admission seams; no native manager, provider or installer effects."""
import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from amplifier_web import installation_repair as repair
from amplifier_web.service import AppError
from amplifier_web.runtime import RuntimeManager
from test_installation_repair import host, prepared  # noqa: F401
from test_automatic_history import app_factory  # noqa: F401
from test_shared_conversation import room, idle, work, configure
from test_external_conversation import until


@pytest.mark.parametrize('boundary', ['retirement_task', 'stop_task'])
@pytest.mark.parametrize('action', ['operations.submit', 'operations.write',
                                    'kernels.create', 'kernels.execute', 'kernels.reset'])
async def test_mutation_waiter_rechecks_repair_before_native_transport(host, monkeypatch, boundary, action):
    service = host.service
    await service.dispatch('session.create', {})
    session = service._session()
    manager = RuntimeManager()
    manager.retention.wake = lambda: None
    pending = asyncio.get_running_loop().create_future()
    row = {boundary: pending}
    manager.workers[session['id']] = row
    manager._admit = AsyncMock(side_effect=AssertionError('No native transport allowed'))
    manager._check_preparation_cleanup = lambda sid: None
    service.runtime = manager
    service.management = None
    args = {'sessionId': session['id']}
    if action.startswith('operations.'):
        args.update(requestId='one-start', command='true', id='owned-process')
        monkeypatch.setattr(service.operations, 'status', lambda *args: {
            'source': 'process', 'controlAvailable': True, 'state': 'running',
            'sourceId': 'owned-process', 'runtimeSessionId': session['id'], 'ownerId': 'owner'})
    else:
        args.update(language='python', kernelId='owned-kernel', generation=1)
        monkeypatch.setattr('amplifier_web.computation.saved', lambda *args: {
            'operationState': 'running', 'generation': 1})
    from amplifier_web.computation import dispatch
    task = asyncio.create_task(
        service.operations.mutate(action, args, 'ui') if action.startswith('operations.')
        else dispatch(service, action, args, 'ui'))
    try:
        await until(lambda: manager._locks.get(session['id']) is not None)
        assert not task.done()
        service._installation_repair_gate = 'a' * 32
        # Settle the old writer; the waiting control must see the newer host gate.
        manager.workers.pop(session['id'])
        pending.set_result(True)
        if action.startswith('operations.'):
            result = await asyncio.wait_for(task, 2)
            assert result['state'] == 'rejected'
            assert service.operations.requests.read(session['id'], 'one-start') == result
            assert await service.operations.mutate(action, args, 'ui') == result
        else:
            with pytest.raises(AppError, match='Installation repair owns'):
                await asyncio.wait_for(task, 2)
        manager._admit.assert_not_awaited()
        assert repair.held(service)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        manager.workers.clear()
        await manager.close()


async def test_admitting_process_request_blocks_repair_without_rewriting_receipt(host):
    service = host.service
    await service.dispatch('session.create', {})
    sid = service._session()['id']
    receipt, fresh = service.operations.requests.begin(sid, 'operations.submit',
        {'sessionId': sid, 'requestId': 'pending', 'command': 'true'}, 'ui')
    assert fresh
    with pytest.raises(ValueError, match='owned processes'):
        repair.require_idle(service)
    assert service.operations.requests.read(sid, 'pending') == receipt
    assert not host.launched


async def test_optional_warm_and_memory_refuse_before_task_or_claim(host, monkeypatch):
    service = host.service
    await service.dispatch('session.create', {})
    sid = service._session()['id']
    service._installation_repair_gate = 'a' * 32
    for action, args in [('session.warm', {'id': sid}), ('memory.consolidate', {'sessionId': sid})]:
        with pytest.raises(AppError, match='Installation repair owns'):
            await service.dispatch(action, args)
    service.runtime.prewarm = AsyncMock()
    await service.warmup.run(sid)
    service.runtime.prewarm.assert_not_awaited()
    memory = service.recall.personalization
    memory.data.claim = lambda *args: pytest.fail('No durable memory attempt may be claimed')
    memory.idle(service._session())
    with pytest.raises(repair.RepairHeld):
        memory.start(service._session(), [sid])
    assert not memory.tasks and not service.warmup.pending


async def test_prewarm_wait_rechecks_repair_without_starting_worker(host):
    from types import SimpleNamespace
    service = host.service
    runtime = RuntimeManager()
    runtime.retention = SimpleNamespace(settings={'max_warm_workers': 1, 'idle_timeout_hours': 1},
                                        background=asyncio.Lock())
    runtime.update_input_gate = lambda: repair.guard(service)
    runtime._check_execution = lambda *args: None
    runtime._start_locked = AsyncMock(side_effect=AssertionError('No startup after repair hold'))
    await runtime.retention.background.acquire()
    task = asyncio.create_task(runtime.prewarm({'id': 'waiting-warm'}, AsyncMock()))
    await asyncio.sleep(0)
    service._installation_repair_gate = 'a' * 32
    runtime.retention.background.release()
    with pytest.raises(repair.RepairHeld):
        await task
    runtime._start_locked.assert_not_awaited()


async def test_active_memory_owner_blocks_confirm_until_settled(host):
    memory = host.service.recall.personalization
    pending = asyncio.get_running_loop().create_future()
    memory.tasks['workspace'] = pending
    try:
        with pytest.raises(ValueError, match='memory consolidation'):
            repair.require_idle(host.service)
    finally:
        pending.set_result(None)
        memory.tasks.clear()


async def test_memory_native_waiter_rechecks_host_guard_before_model_transport(host):
    service = host.service
    runtime = RuntimeManager()
    runtime.retention.wake = lambda: None
    pending = asyncio.get_running_loop().create_future()
    runtime.workers['memory-owner'] = {'retirement_task': pending}
    runtime._check_preparation_cleanup = lambda sid: None
    runtime._admit = AsyncMock(side_effect=AssertionError('No model transport may occur'))
    task = asyncio.create_task(runtime.control('memory-owner', 'memory.consolidate', {'prompt': 'local'},
        guard=lambda: repair.guard(service)))
    try:
        await until(lambda: runtime._locks.get('memory-owner') is not None)
        service._installation_repair_gate = 'a' * 32
        runtime.workers.clear()
        pending.set_result(None)
        with pytest.raises(repair.RepairHeld):
            await task
        runtime._admit.assert_not_awaited()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        runtime.workers.clear()
        await runtime.close()


@pytest.mark.parametrize('owner', ['naming', 'history-edit'])
async def test_queued_conversation_owner_blocks_repair_before_startup(host, owner):
    service = host.service
    await service.dispatch('session.create', {})
    session = service._session()
    if owner == 'naming':
        session['naming'] = {'status': 'working'}
    else:
        session.update(configurationBusy=True, historyEdit={'phase': 'working'})
    with pytest.raises(ValueError, match='naming or history editing'):
        repair.require_idle(service)
    assert not host.launched


async def test_queued_naming_after_hold_refuses_runtime_start_and_transport(host):
    service = host.service
    await service.dispatch('session.create', {})
    service._installation_repair_gate = 'a' * 32
    service.runtime.start = AsyncMock()
    service.runtime.control = AsyncMock()
    await service._regenerate_name(copy.deepcopy(service._session()), {})
    service.runtime.start.assert_not_awaited()
    service.runtime.control.assert_not_awaited()
    assert repair.blocks_action('message.edit', {})


async def test_direct_voice_fresh_admission_is_held_but_exact_receipt_remains_readable(host):
    service = host.service
    await service.dispatch('session.create', {})
    sid = service._session()['id']
    service.surface_context.checkpoint = AsyncMock(return_value={})
    service._send = AsyncMock()
    receipt = await service.voice_delegate('original voice', 'voice-one', sid)
    await until(lambda: service._send.await_count == 1)
    service._installation_repair_gate = 'a' * 32
    before = copy.deepcopy(service._session(sid))
    assert await service.voice_delegate('original voice', 'voice-one', sid) == {
        **receipt, 'duplicate': True}
    with pytest.raises(AppError, match='Installation repair owns') as denied:
        await service.voice_delegate('fresh voice', 'voice-two', sid)
    assert denied.value.code == 'installation_repair_held'
    assert service._session(sid) == before
    assert service.db.execute("SELECT 1 FROM commands WHERE id='voice-two'").fetchone() is None
    assert service._send.await_count == 1


@pytest.mark.parametrize('state', ['pending', 'stopped', 'mixed-unknown'])
async def test_repair_held_startup_keeps_pending_sources_and_releases_once(app_factory, tmp_path, state):
    app = app_factory()
    session = await room(app, tmp_path)
    sid = session['id']
    source = app._message(session, 'user', 'already accepted', inputId='saved', inputOrigin='ui')
    app.external_conversations.shared.capture(session, source)
    if state == 'mixed-unknown':
        app.external_conversations.shared._record(session, session['_externalRoom']['deliveries'][0], 'unknown')
    app._publish_changes(sessions={sid})
    await app.close()
    restored = app_factory()
    configure(restored, tmp_path)
    restored._installation_repair_gate = 'a' * 32
    await restored.external_conversations.resume_pending()
    session = restored._session(sid)
    original = copy.deepcopy(session['_externalRoom']['deliveries'])
    assert len(original) == 2
    assert [row['state'] for row in original] == (['unknown', 'pending'] if state == 'mixed-unknown' else ['pending'] * 2)
    assert not restored.external_conversations.shared.turns
    if state == 'stopped':
        await restored.dispatch('conversation.stop', {'sessionId': sid})
    # A positive settled receipt releases only the exact gate through status.
    operation = {'operationId': 'a' * 32, 'previewId': 'b' * 32,
        'hostInstanceId': restored.instance_id, 'status': 'failed', 'stage': 'reconciled-no-effect',
        'reinstalled': False, 'healthVerified': False, 'serviceState': 'running'}
    with repair._admission(restored):
        repair._write(repair._folder(restored, 'a' * 32) / 'operation.json', operation, restored.data_dir)
        repair._write(repair.root(restored) / 'admissions.json',
            {'schema': 1, 'current': 'a' * 32, 'previews': {'b' * 32: 'a' * 32}}, restored.data_dir)
    await repair.status(restored, {})
    await idle(restored, sid)
    await repair.status(restored, {})
    await restored.external_conversations.resume_pending()
    assert not repair.held(restored)
    if state == 'stopped':
        assert all(row['state'] == 'cancelled' for row in session['_externalRoom']['deliveries'])
        assert not (tmp_path / 'alpha/peer').exists()
    elif state == 'mixed-unknown':
        assert session['_externalRoom']['deliveries'][0] == original[0]
        assert not work(tmp_path, 'alpha')
        assert [row['source'] for row in work(tmp_path, 'beta')] == ['already accepted']
    else:
        assert all([row['source'] for row in work(tmp_path, identity)] == ['already accepted']
                   for identity in ('alpha', 'beta'))


async def test_repair_hold_after_acp_connect_keeps_source_known_unsubmitted(app_factory, tmp_path, monkeypatch):
    from amplifier_web.acp_participant import ACPParticipant
    app = app_factory()
    session = await room(app, tmp_path)
    owner = app.external_conversations.shared
    source = app._message(session, 'user', 'pending attention', inputId='saved', inputOrigin='ui')
    owner.capture(session, source)
    connected, release = asyncio.Event(), asyncio.Event()
    original = ACPParticipant.connect
    async def delayed(participant, *args, **kwargs):
        await original(participant, *args, **kwargs)
        connected.set()
        await release.wait()
    monkeypatch.setattr(ACPParticipant, 'connect', delayed)
    await owner.resume_pending()
    try:
        await asyncio.wait_for(connected.wait(), 3)
        app._installation_repair_gate = 'a' * 32
        release.set()
        await until(lambda: not owner.turns)
        assert all(row['state'] == 'pending' for row in session['_externalRoom']['deliveries'])
        assert all(not work(tmp_path, identity) for identity in ('alpha', 'beta'))
        app._installation_repair_gate = None
        await owner.resume_pending()
        await idle(app, session['id'])
        assert all([row['source'] for row in work(tmp_path, identity)] == ['pending attention']
                   for identity in ('alpha', 'beta'))
    finally:
        release.set()