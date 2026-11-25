"""Conditional detach uses native acknowledgement, never prompt completion."""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web
import pytest

from amplifier_web.runtime import RuntimeManager
from test_acp_server import host, connect  # noqa: F401


WORKER = r'''
import json,sys,urllib.request
held=False
for line in sys.stdin:
    row=json.loads(line); op=row['op']; result={}
    if op=='start':
        source=row['session']['ephemeralMcpServers']['source']
        print(json.dumps({'type':'runtime.ready','report':{}}),flush=True)
        continue
    if op=='stop':break
    if op=='retire':result={'retired':not held}
    if op=='control':
        if row['operation']=='background.start':held=True
        if row['operation']=='background.finish':held=False
        if row['operation']=='context.read':
            request=urllib.request.Request(source['url'],headers=source['headers'])
            with urllib.request.urlopen(request) as response:result=json.load(response)
    print(json.dumps({'op':'reply','id':row['id'],'result':result}),flush=True)
'''


@pytest.fixture
async def mounted(tmp_path, aiohttp_server):
    reads = []
    async def read(request):
        assert request.headers['Authorization'] == 'Bearer fixture-lease'
        reads.append('source-owned read')
        return web.json_response({'text': 'Older public context', 'source': 'owned-room'})
    source = web.Application()
    source.router.add_get('/context', read)
    server = await aiohttp_server(source)
    servers = {'source': {'type': 'http', 'url': str(server.make_url('/context')),
                          'headers': {'Authorization': 'Bearer fixture-lease'}}}
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', WORKER], startup_timeout=3)
    manager.home = tmp_path / 'app'
    manager.retention.wake = lambda: None
    manager.retention.settings['max_warm_workers'] = 0  # Exercise safety after policy requests cleanup.
    writes = []
    original = manager._write
    async def write(row, value):
        writes.append(value['op'])
        await original(row, value)
    manager._write = write
    async def emit(*_):
        pass
    session = {'id': 'owned', 'workspace': str(tmp_path), 'bundle': 'fixture'}
    await manager.bind_ephemeral_mcp('owned', 'attachment', servers)
    await manager.start(session, emit)
    row = manager.workers['owned']
    row['idle'] = True  # A candidate hint; the subprocess still checks held work.
    yield SimpleNamespace(manager=manager, row=row, servers=servers, writes=writes,
                          reads=reads, session=session, emit=emit)
    await manager.close()


async def test_background_context_access_survives_refusal_then_exact_owner_retires(mounted):
    f = mounted
    await f.manager.control('owned', 'background.start')
    f.row['idle'] = True
    assert await f.manager.retire_attachment('owned', 'attachment', guard=lambda: False) == 'busy'
    assert f.row['process'].returncode is None and 'stop' not in f.writes
    assert f.manager._ephemeral_mcp['owned'] == ('attachment', f.servers)
    assert await f.manager.control('owned', 'context.read') == {
        'text': 'Older public context', 'source': 'owned-room'}
    assert f.reads == ['source-owned read']
    await f.manager.control('owned', 'background.finish')
    f.row['idle'] = True
    assert await f.manager.retire_attachment('owned', 'attachment', guard=lambda: False) == 'retired'
    assert f.row['process'].returncode == 0
    assert 'owned' not in f.manager.workers and 'owned' not in f.manager._ephemeral_mcp
    assert f.writes.count('retire') == 2 and 'send' not in f.writes


@pytest.mark.parametrize('barrier', ['busy', 'protected', 'foreign', 'guard', 'admitting'])
async def test_retirement_keeps_exact_mount_when_owner_is_unavailable(mounted, barrier):
    f = mounted
    f.row['idle'] = barrier != 'busy'
    f.manager.retention.protected = lambda _: barrier == 'protected'
    lock = f.manager._locks['owned']
    if barrier == 'admitting':
        await lock.acquire()
    try:
        assert await f.manager.retire_attachment('owned', 'other' if barrier == 'foreign' else 'attachment',
            guard=lambda: barrier == 'guard') == 'busy'
    finally:
        if barrier == 'admitting':
            lock.release()
    assert f.manager.workers['owned'] is f.row and f.row['process'].returncode is None
    assert f.manager._ephemeral_mcp['owned'] == ('attachment', f.servers)
    assert 'retire' not in f.writes and 'stop' not in f.writes


@pytest.mark.parametrize('interruption', ['timeout', 'cancel', 'new-binding', 'new-work'])
async def test_unknown_or_superseded_retirement_never_releases_a_binding(mounted, interruption):
    f = mounted
    entered, proceed = asyncio.Event(), asyncio.Event()
    occupied = False
    original = f.manager._write
    async def write(row, value):
        if value['op'] == 'retire':
            entered.set()
            await proceed.wait()
        await original(row, value)
    f.manager._write = write
    if interruption == 'timeout':
        f.manager.retention.reply_timeout = .01
    request = asyncio.create_task(f.manager.retire_attachment(
        'owned', 'attachment', guard=lambda: occupied))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        retirement = f.row['retirement_task']
        assert f.manager._ephemeral_mcp['owned'] == ('attachment', f.servers)
        if interruption == 'timeout':
            assert await request == 'unknown'
        elif interruption == 'cancel':
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        elif interruption == 'new-binding':
            f.manager._ephemeral_mcp['owned'] = ('new-attachment', f.servers)
        else:
            occupied = True
        assert not retirement.done()
        proceed.set()
        await asyncio.wait_for(asyncio.shield(retirement), 3)
        if interruption in {'new-binding', 'new-work'}:
            assert await request == 'busy'
        assert f.manager._ephemeral_mcp['owned'] == (
            'new-attachment' if interruption == 'new-binding' else 'attachment', f.servers)
        assert f.writes.count('retire') == 1 and 'send' not in f.writes
    finally:
        proceed.set()
        await asyncio.gather(request, return_exceptions=True)


async def test_actual_sdk_retirement_is_scoped_and_repeatable_after_lost_result(host):
    app, _, runtime, path = host
    calls = []
    async def retire(identity, attachment, *, guard):
        assert not guard()
        calls.append((identity, attachment))
        runtime.release_ephemeral_mcp(identity, attachment)
        return 'retired'
    runtime.retire_attachment = retire
    peer = await connect(host)
    other = await connect(host)
    sid = await peer.new(path)
    foreign = await other.new(path)
    assert 'error' in await peer.call('_amplifier/retire', sessionId=foreign)
    second = await peer.new(path)
    assert (await peer.call('_amplifier/retire', sessionId=sid))['result']['disposition'] == 'busy'
    assert not calls
    # A fresh one-session attachment owns the operation. Existing multiple
    # sessions stay valid and are never treated as a work-count violation.
    response = await other.call('_amplifier/retire', sessionId=foreign)
    assert response['result'] == {'sessionId': foreign, 'disposition': 'retired'}
    assert (await other.call('_amplifier/retire', sessionId=foreign)) == {
        'jsonrpc': '2.0', 'id': other.serial, 'result': response['result']}
    assert len(calls) == 1
    assert 'error' in await other.prompt(foreign, 'Must not admit after retirement')
    assert not runtime.sent and not runtime.stopped
    assert sid in runtime.bindings and second in runtime.bindings
    await peer.socket.close()
    await other.socket.close()


async def test_retirement_hint_is_advisory_outside_prompt_and_not_an_ack_loop(host):
    app, _, runtime, path = host
    peer = await connect(host)
    sid = await peer.new(path)
    service = app['service']
    await service.on_runtime_event('runtime.status', {
        'sessionId': sid, 'status': 'idle', 'event': 'session.idle'})
    owner = next(iter(service._execution_observers.attachments))
    assert owner._retirement_hint_task is None  # A finished turn is not a retention request.
    await service.on_runtime_event('runtime.retirement_requested', {'sessionId': sid})
    event = await asyncio.wait_for(peer.socket.receive_json(), 3)
    assert event == {'jsonrpc': '2.0', 'method': '_amplifier/retirementRequested',
                     'params': {'sessionId': sid}}
    assert sid in runtime.bindings and not runtime.stopped
    previous = owner._retirement_hint_task
    await owner.observe('runtime.warmth', {'sessionId': sid, 'status': 'cold'})
    assert owner._retirement_hint_task is previous
    await peer.socket.close()


async def test_new_session_in_flight_cannot_be_hidden_by_connection_retirement(host):
    _, _, runtime, path = host
    peer = await connect(host)
    sid = await peer.new(path)
    entered, proceed = asyncio.Event(), asyncio.Event()
    bind = runtime.bind_ephemeral_mcp
    async def delayed(*args):
        entered.set()
        await proceed.wait()
        await bind(*args)
    runtime.bind_ephemeral_mcp = delayed
    retire_calls = []
    async def retire(*args, **kwargs):
        retire_calls.append(args)
        return 'retired'
    runtime.retire_attachment = retire
    creating = asyncio.create_task(peer.new(path))
    try:
        await entered.wait()
        await peer.socket.send_json({'jsonrpc': '2.0', 'id': 500,
            'method': '_amplifier/retire', 'params': {'sessionId': sid}})
        await asyncio.sleep(0)
        assert not retire_calls
        proceed.set()
        second = await creating
        response = next((row for row in peer.updates if row.get('id') == 500), None)
        if response is None:
            response = await asyncio.wait_for(peer.socket.receive_json(), 3)
        assert response['id'] == 500 and response['result'] == {
            'sessionId': sid, 'disposition': 'busy'}
        assert not retire_calls and {sid, second} <= runtime.bindings.keys()
    finally:
        proceed.set()
        await asyncio.gather(creating, return_exceptions=True)
        await peer.socket.close()


@pytest.mark.parametrize('shutdown', [False, True])
async def test_naming_hint_waits_for_actual_persistence_future(shutdown, monkeypatch):
    from amplifier_web.host import naming
    from amplifier_web.runtime_worker import Worker
    from amplifier_foundation.session.activation import ActivationGate

    worker = Worker()
    worker.shared_handle = SimpleNamespace(active=True)
    worker.activation_gate = ActivationGate()
    worker.activation = worker.activation_gate.activate()
    worker.runtime = SimpleNamespace(queued_inputs=0, inbox=asyncio.Queue(), generation=None)
    checkpoint = AsyncMock()
    loop = SimpleNamespace(pending=[], _active_jobs=lambda: [])
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda _: loop,
        get_capability=lambda name: checkpoint if name == 'live.checkpoint' else None))
    namer = naming.LiveSessionNaming.__new__(naming.LiveSessionNaming)
    worker.naming = namer
    namer.closed, namer.pending, namer.last_attempt = False, None, 0
    namer.completed, namer.directory = {'input'}, None
    namer.coordinator = SimpleNamespace(session_id='owned')
    namer.hook = SimpleNamespace(_load_metadata=lambda _: {}, _defer_counts={},
        config=SimpleNamespace(initial_trigger_turn=1, max_retries=1, update_interval_turns=1))
    namer._snapshot_hook = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(naming, 'automatic_metadata', lambda _: True)
    monkeypatch.setattr(naming, 'has_generated_or_manual_name', lambda _: False)
    provider_done, persist = asyncio.Event(), asyncio.Event()
    async def generate(_):
        provider_done.set()
        await persist.wait()  # Actual name persistence still belongs to this future.
    namer._generate = generate
    events = []
    namer.publish = lambda event: events.append((event, namer.pending.done()))
    namer.schedule()
    await provider_done.wait()
    with patch('amplifier_web.runtime_worker.publish') as publish:
        await worker.command({'op': 'retire', 'id': 'before-save'})
        assert publish.call_args.args[0]['result'] == {'retired': False}
        assert not events and not worker.shutdown.is_set()
        if shutdown:
            namer.closed = True
            namer.pending.cancel()
        persist.set()
        await asyncio.gather(namer.pending, return_exceptions=True)
        await asyncio.sleep(0)  # Run the existing future completion callback.
        if shutdown:
            assert events == []
        else:
            assert events == [({'type': 'session.naming.finished'}, True)]
            await worker.command({'op': 'retire', 'id': 'after-save'})
            assert publish.call_args.args[0]['result'] == {'retired': True}
            assert worker.shutdown.is_set()
