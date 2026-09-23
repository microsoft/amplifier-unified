"""Voice callbacks and provider admission cannot cross a task transfer fence.

The two hosts use real Git, AppService actions and native writer fences. Voice
creation alone is replaced; no provider, audio device or remote service runs.
"""
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_foundation.session import SharedSessionStore
from amplifier_portability.capsule import read_capsule
from amplifier_web.service import AppError
from amplifier_web.voice import ProviderError, VoiceCall, VoiceError, VoiceService
from test_portability import export, host_env, setup_hosts


@pytest.fixture
async def hosts(tmp_path, monkeypatch):
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    host_env(monkeypatch, source)
    value = SimpleNamespace(app=app, target=target, source=source,
                            destination=destination, root=root, destrepo=destrepo, sid=sid)
    try:
        yield value
    finally:
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()


def voice_manager(app, monkeypatch, identity='voice-before-transfer'):
    manager = VoiceService(app, api_key='synthetic-not-a-credential', http=object())
    app.voice_service = manager

    async def create(sdp, provider):
        manager.call.id = identity
        return {'id': identity, 'sdp': 'synthetic-answer', 'sessionId': manager.call.session_id,
                'provider': provider}

    created = AsyncMock(side_effect=create)
    monkeypatch.setattr(VoiceCall, 'create', created)
    return manager, created


async def export_args(host):
    inspected = (await host.app.dispatch('worktree.inspect', {'sessionId': host.sid}))['result']
    return {'sessionId': host.sid, 'destination': host.target.portability.node.identity['id'],
            'sourceRevision': inspected['repository']['sourceRevision'],
            'expectedExecutionRevision': host.app._session(host.sid).get('executionRevision', 0),
            'mode': 'carry_dirty', 'reviewedContent': True}


async def saved_voice(host, monkeypatch):
    manager, created = voice_manager(host.app, monkeypatch)
    await manager.connect('v=0', 'live', session_id=host.sid)
    call = manager.call
    await host.app.set_voice_status({'status': 'connected'})
    await call.record('user', 'Completed before transfer', 'saved-item')
    await call.handle({'type': 'response.done', 'response': {'id': 'saved-response',
                       'usage': {'input_tokens': 11, 'output_tokens': 7}}})
    await manager.end()
    assert manager.call.closed
    return manager, created, call


def voice_contents(app, sid):
    session = app._session(sid)
    return copy.deepcopy({'messages': session['messages'], 'execution': session.get('execution')})


async def assert_late_voice_ignored(app, sid, call):
    before = voice_contents(app, sid)
    operations = [
        lambda: call.record('user', ' overwritten after handoff', 'saved-item', delta=True),
        lambda: call.record('assistant', 'New delayed speech', 'late-item'),
        lambda: call.handle({'type': 'response.created', 'response': {'id': 'late-response'}}),
        lambda: call.handle({'type': 'response.done', 'response': {'id': 'saved-response',
                            'usage': {'input_tokens': 900, 'output_tokens': 900}}}),
        lambda: call.handle({'type': 'session.closed',
                            'usage': {'input_tokens': 5, 'output_tokens': 6}}),
    ]
    for operation in operations:
        try:
            await operation()
        except (AppError, VoiceError) as error:
            assert error.status == 409
        assert voice_contents(app, sid) == before


async def transfer(host, monkeypatch, *, returning=False):
    left, right = (host.target, host.app) if returning else (host.app, host.target)
    left_home, right_home = (host.destination, host.source) if returning else (host.source, host.destination)
    repository = host.root if returning else host.destrepo
    host_env(monkeypatch, left_home)
    exported, _ = await export(left, right, host.sid, Path(left._session(host.sid)['workspace']))
    host_env(monkeypatch, right_home)
    staged = (await right.dispatch('portability.stage',
        {'path': exported['package'], 'repository': str(repository)}))['result']
    host_env(monkeypatch, left_home)
    released = (await left.dispatch('portability.release', {'sessionId': host.sid,
        'id': exported['id'], 'expectedRevision': exported['revision'],
        'path': staged['receiptPath']}))['result']
    return exported, staged, released


@pytest.mark.parametrize('status,live_call', [
    ('connecting', False), ('connected', False), ('closing', False), ('ending', False),
    ('error', True), ('closing', True), ('disconnected', True),
])
async def test_export_refuses_authoritative_voice_state_before_quiescence(hosts, monkeypatch, status, live_call):
    host = hosts
    quiesce = AsyncMock(side_effect=AssertionError('A live voice call must not reach writer release'))
    monkeypatch.setattr(host.app.runtime, 'quiesce_for_handoff', quiesce)
    await host.app.set_voice_status({'status': status, 'sessionId': host.sid, 'id': 'active-call'})
    if live_call:
        manager, created = voice_manager(host.app, monkeypatch)
        manager.call = VoiceCall(manager, host.sid)
        manager.call.id = 'active-call'
        manager.call.closing = status == 'closing'
    args = await export_args(host)
    with pytest.raises(AppError, match='voice call'):
        await host.app.dispatch('portability.export', args, command_id='refused-voice-export')
    quiesce.assert_not_awaited()
    assert not host.app.portability.node.records(host.sid)
    assert not host.app._session(host.sid).get('configurationBusy')
    assert SharedSessionStore(host.root, host.sid).transfer_fence() is None


async def test_connect_wins_awaited_export_preflight_race(hosts, monkeypatch):
    host = hosts
    args = await export_args(host)
    from amplifier_web import portability
    # Validate the real, unchanged fixture once before starting the controlled
    # admission race. Git subprocess latency is not part of the voice barrier.
    workspace = await asyncio.to_thread(portability.capture_workspace, str(host.root),
                                        args['sourceRevision'], args['mode'])
    monkeypatch.setattr(portability, 'capture_workspace', lambda *_: copy.deepcopy(workspace))
    manager, created = voice_manager(host.app, monkeypatch)
    original = host.app.history.ensure_loaded
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def paused_preflight(sid):
        entered.set()
        await proceed.wait()
        return await original(sid)

    monkeypatch.setattr(host.app.history, 'ensure_loaded', paused_preflight)
    quiesce = AsyncMock(side_effect=AssertionError('Connected voice must win admission'))
    monkeypatch.setattr(host.app.runtime, 'quiesce_for_handoff', quiesce)
    task = asyncio.create_task(host.app.dispatch('portability.export', args, command_id='voice-race'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await manager.connect('v=0', 'live', session_id=host.sid)
        created.assert_awaited_once()
        proceed.set()
        with pytest.raises(AppError, match='voice call'):
            await asyncio.wait_for(task, 5)
        quiesce.assert_not_awaited()
        assert not host.app.portability.node.records(host.sid)
        assert not host.app.portability.fenced(host.sid)
    finally:
        proceed.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_export_admission_wins_connect_race_before_native_quiescence(hosts, monkeypatch):
    host = hosts
    manager, created = voice_manager(host.app, monkeypatch)
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = host.app.runtime.quiesce_for_handoff

    async def paused_quiesce(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(host.app.runtime, 'quiesce_for_handoff', paused_quiesce)
    started = (await host.app.dispatch('portability.export', await export_args(host),
                                     command_id='export-wins'))['result']
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert host.app.portability.node.get(started['id'])['phase'] == 'preparing'
        assert host.app.portability.fenced(host.sid)
        assert SharedSessionStore(host.root, host.sid).transfer_fence() is None
        with pytest.raises(VoiceError) as rejected:
            await manager.connect('v=0', 'live', session_id=host.sid)
        assert rejected.value.status == 409
        created.assert_not_awaited()
        assert manager.call is None
    finally:
        proceed.set()
        await asyncio.gather(*list(host.app.portability.jobs))
    assert host.app.portability.node.get(started['id'])['phase'] == 'prepared'


async def test_inflight_voice_delegate_cannot_resume_after_cancelled_transfer(hosts, monkeypatch):
    host = hosts
    manager, created = voice_manager(host.app, monkeypatch)
    await manager.connect('v=0', 'live', session_id=host.sid)
    call = manager.call
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def paused_checkpoint(sid):
        assert sid == host.sid
        entered.set()
        await proceed.wait()
        return {}

    monkeypatch.setattr(host.app.surface_context, 'checkpoint', paused_checkpoint)
    task = asyncio.create_task(call.execute('A request still waiting for context', 'delayed-delegate'))
    command = 'voice:' + call.id + ':delayed-delegate'
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await manager.end()
        exported, _ = await export(host.app, host.target, host.sid, host.root)
        await host.app.dispatch('portability.cancel', {'sessionId': host.sid, 'id': exported['id'],
            'expectedRevision': exported['revision'], 'evidence': 'No destination received source release.'})
        before = voice_contents(host.app, host.sid)
        proceed.set()
        with pytest.raises(AppError, match='predates') as rejected:
            await asyncio.wait_for(task, 5)
        assert rejected.value.status == 409
        assert voice_contents(host.app, host.sid) == before
        assert not host.app.db.execute('SELECT 1 FROM commands WHERE id=?', (command,)).fetchone()
        assert not host.app.runtime.workers
    finally:
        proceed.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_released_source_blocks_voice_provider_and_delayed_writes(hosts, monkeypatch):
    host = hosts
    manager, created, old_call = await saved_voice(host, monkeypatch)
    exported, staged, released = await transfer(host, monkeypatch)
    assert released['phase'] == 'released'
    host_env(monkeypatch, host.source)
    archive = Path(released['archive']) / 'transcript.jsonl'
    raw = archive.read_bytes()
    created.reset_mock()
    with pytest.raises(VoiceError) as rejected:
        await manager.connect('v=0', 'live', session_id=host.sid)
    assert rejected.value.status == 409
    created.assert_not_awaited()
    await assert_late_voice_ignored(host.app, host.sid, old_call)
    assert archive.read_bytes() == raw
    assert SharedSessionStore(host.root, host.sid).transfer_fence()['phase'] == 'committed'


async def test_native_fence_alone_blocks_voice_provider_and_delayed_writes(hosts, monkeypatch):
    host = hosts
    manager, created, old_call = await saved_voice(host, monkeypatch)
    store = SharedSessionStore(host.root, host.sid)
    held = store.acquire(app='independent-native-exporter')
    try:
        held.fence_transfer('independent-transfer', 'another-host')
    finally:
        held.release()
    assert not host.app.portability.node.records(host.sid)
    created.reset_mock()
    with pytest.raises(VoiceError) as rejected:
        await manager.connect('v=0', 'live', session_id=host.sid)
    assert rejected.value.status == 409
    created.assert_not_awaited()
    await assert_late_voice_ignored(host.app, host.sid, old_call)
    assert store.transfer_fence()['phase'] == 'staged'


async def test_native_fence_during_live_rejection_prevents_realtime_fallback(hosts, monkeypatch):
    host = hosts
    manager = VoiceService(host.app, api_key='synthetic-not-a-credential', http=object())
    host.app.voice_service = manager
    attempts = []

    async def rejected_live(call, sdp, provider):
        attempts.append(provider)
        if provider == 'live':
            held = SharedSessionStore(host.root, host.sid).acquire(app='independent-native-exporter')
            try:
                held.fence_transfer('native-during-provider-rejection', 'another-host')
            finally:
                held.release()
            raise ProviderError(404, 'model_not_found')
        return {'id': 'unexpected-fallback', 'sdp': 'synthetic-answer', 'sessionId': host.sid}

    monkeypatch.setattr(VoiceCall, 'create', rejected_live)
    with pytest.raises(ProviderError) as rejected:
        await manager.connect('v=0', 'auto', session_id=host.sid)
    assert rejected.value.status == 404
    assert rejected.value.code == "model_not_found"
    assert attempts == ['live']
    assert manager.call.closed
    assert SharedSessionStore(host.root, host.sid).transfer_fence()['phase'] == 'staged'
    assert not host.app.portability.node.records(host.sid)


async def test_direct_voice_create_after_native_fence_never_requests_provider(hosts):
    host = hosts
    manager = VoiceService(host.app, api_key='synthetic-not-a-credential', http=object())
    manager.request = AsyncMock(side_effect=AssertionError('Fenced direct create reached the provider'))
    call = VoiceCall(manager, host.sid)
    held = SharedSessionStore(host.root, host.sid).acquire(app='independent-native-exporter')
    try:
        held.fence_transfer('native-before-direct-create', 'another-host')
    finally:
        held.release()
    with pytest.raises(VoiceError) as rejected:
        await call.create('v=0', 'live')
    assert rejected.value.status == 409
    manager.request.assert_not_awaited()
    assert not call.id and call.socket is None and call.pump is None


async def test_direct_voice_create_from_cancelled_transfer_context_never_requests_provider(hosts):
    host = hosts
    manager = VoiceService(host.app, api_key='synthetic-not-a-credential', http=object())
    manager.request = AsyncMock(side_effect=AssertionError('Stale direct create reached the provider'))
    # Allocate a call before transfer without registering or starting a live
    # provider session. Its captured ownership context becomes stale on cancel.
    call = VoiceCall(manager, host.sid)
    receipt, _ = await export(host.app, host.target, host.sid, host.root)
    await host.app.dispatch('portability.cancel', {'sessionId': host.sid, 'id': receipt['id'],
        'expectedRevision': receipt['revision'], 'evidence': 'No destination received source release.'})
    assert not host.app.portability.fenced(host.sid)
    assert not host.app._session(host.sid).get('configurationBusy')
    with pytest.raises(VoiceError) as rejected:
        await call.create('v=0', 'realtime')
    assert rejected.value.status == 409
    manager.request.assert_not_awaited()
    assert not call.id and call.socket is None and call.pump is None


@pytest.mark.parametrize('resolution', ['cancel', 'roundtrip'])
async def test_completed_voice_preserved_but_retired_callbacks_cannot_edit_returned_task(hosts, monkeypatch, resolution):
    host = hosts
    manager, created, old_call = await saved_voice(host, monkeypatch)
    completed = voice_contents(host.app, host.sid)
    if resolution == 'cancel':
        exported, _ = await export(host.app, host.target, host.sid, host.root)
        payload = read_capsule(exported['package'])['body']['payload']
        assert payload['session']['messages'] == completed['messages']
        assert payload['session']['execution'] == completed['execution']
        await assert_late_voice_ignored(host.app, host.sid, old_call)
        await host.app.dispatch('portability.cancel', {'sessionId': host.sid, 'id': exported['id'],
            'expectedRevision': exported['revision'], 'evidence': 'The destination has not received source release.'})
    else:
        exported, staged, released = await transfer(host, monkeypatch)
        payload = read_capsule(exported['package'])['body']['payload']
        assert payload['session']['messages'] == completed['messages']
        assert payload['session']['execution'] == completed['execution']
        host_env(monkeypatch, host.destination)
        await host.target.dispatch('portability.activate', {'sessionId': host.sid, 'id': staged['id'],
            'expectedRevision': staged['revision'], 'path': released['receiptPath']})
        assert voice_contents(host.target, host.sid) == completed
        returning, return_stage, return_release = await transfer(host, monkeypatch, returning=True)
        host_env(monkeypatch, host.source)
        await host.app.dispatch('portability.activate', {'sessionId': host.sid, 'id': return_stage['id'],
            'expectedRevision': return_stage['revision'], 'path': return_release['receiptPath']})
    assert not host.app.portability.fenced(host.sid)
    returned = voice_contents(host.app, host.sid)
    assert [row for row in returned['messages'] if row.get('voiceId') == old_call.id] == [
        row for row in completed['messages'] if row.get('voiceId') == old_call.id]
    assert returned['execution'] == completed['execution']
    await assert_late_voice_ignored(host.app, host.sid, old_call)
    # Restoring source admission must still permit a deliberately new call.
    manager, created = voice_manager(host.app, monkeypatch, identity='deliberately-new-call')
    await manager.connect('v=0', 'live', session_id=host.sid)
    created.assert_awaited_once()
    await manager.call.record('user', 'New deliberate speech', 'new-item')
    assert host.app._session(host.sid)['messages'][-1]['text'] == 'New deliberate speech'
    await manager.call.handle({'type': 'response.done', 'response': {'id': 'new-response',
                               'usage': {'input_tokens': 2, 'output_tokens': 3}}})
    await manager.call.handle({'type': 'session.closed',
                               'usage': {'input_tokens': 2, 'output_tokens': 3}})
    nodes = host.app._session(host.sid)['execution']['nodes']
    assert any(row['id'] == 'voice:deliberately-new-call:session' for row in nodes)
    await assert_late_voice_ignored(host.app, host.sid, old_call)
