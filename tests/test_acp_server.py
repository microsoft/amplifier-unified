"""Actual SDK dispatch and AppService ownership over authenticated WebSockets."""
import asyncio
import json

from aiohttp import WSMsgType, WSServerHandshakeError
import pytest

from amplifier_web.auth import new_session
from amplifier_web.server import create_app
from test_coordination_generation import source_plan, until
from test_service import Runtime as BaseRuntime


class Runtime(BaseRuntime):
    """Controlled native I/O; no model, provider, or external process."""
    def __init__(self):
        super().__init__()
        self.bindings = {}
        self.closed = 0
        self.send_ack = asyncio.Event()
        self.send_ack.set()
        self.cancelled_sends = []

    async def bind_ephemeral_mcp(self, sid, attachment, servers):
        self.bindings[sid] = (attachment, servers)

    def release_ephemeral_mcp(self, sid, attachment):
        if self.bindings.get(sid, (None,))[0] == attachment:
            del self.bindings[sid]

    async def send(self, session, text, input_id, emit):
        self.sent.append((session['id'], text, input_id))
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'working',
                                     'event': 'input.delivered', 'inputId': input_id})
        try:
            await self.send_ack.wait()
        except asyncio.CancelledError:
            self.cancelled_sends.append(input_id)
            raise
        return {'accepted': True}

    async def close(self):
        self.closed += 1


class Peer:
    def __init__(self, socket):
        self.socket = socket
        self.serial = 0
        self.updates = []

    async def call(self, method, **params):
        self.serial += 1
        identity = self.serial
        await self.socket.send_json({'jsonrpc': '2.0', 'id': identity,
                                     'method': method, 'params': params})
        async with asyncio.timeout(5):
            while True:
                row = await self.socket.receive_json()
                if row.get('id') == identity:
                    return row
                self.updates.append(row)

    async def new(self, path, servers=None):
        row = await self.call('session/new', cwd=str(path), mcpServers=servers or [])
        return row['result']['sessionId']

    async def prompt(self, sid, text):
        return await self.call('session/prompt', sessionId=sid,
                               prompt=[{'type': 'text', 'text': text}])

    async def cancel(self, sid):
        await self.socket.send_json({'jsonrpc': '2.0', 'method': 'session/cancel',
                                     'params': {'sessionId': sid}})


@pytest.fixture
async def host(tmp_path, aiohttp_client):
    runtime = Runtime()
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=runtime,
                           preload_providers=False, voice=False, background_updates=False,
                           background_work=False)
    client = await aiohttp_client(app)
    return app, client, runtime, tmp_path


async def connect(host, **query):
    app, client, _, path = host
    socket = await client.ws_connect('/api/acp', params={'workspace': str(path), **query},
        headers={'Authorization': 'Bearer ' + app['control_token']})
    peer = Peer(socket)
    initialized = await peer.call('initialize', protocolVersion=1)
    capabilities = initialized['result']['agentCapabilities']
    assert capabilities['mcpCapabilities'] == {'http': True, 'sse': False}
    assert capabilities['sessionCapabilities']['resume'] == {}
    return peer


async def finish(service, sid, input_id, text='Canonical result'):
    await service.on_runtime_event('runtime.generation', {
        'sessionId': sid, 'rootSessionId': sid, 'event': 'generation.finished',
        'generation_id': 'generation-' + input_id, 'input_ids': [input_id],
        'text': text, 'active_job_ids': [], 'disposition': 'manager_turn_finished',
    })


async def test_control_bearer_required_even_with_valid_browser_cookie(host):
    app, client, runtime, path = host
    for headers, status in [({}, 401), ({'Authorization': 'Bearer wrong'}, 401),
            ({'Cookie': 'amplifier_unified_session=' + new_session(app['session_secret'])}, 403)]:
        with pytest.raises(WSServerHandshakeError) as error:
            await client.ws_connect('/api/acp', params={'workspace': str(path)}, headers=headers)
        assert error.value.status == status
    with pytest.raises(WSServerHandshakeError) as error:
        await client.ws_connect('/api/acp', params={'workspace': 'relative'},
            headers={'Authorization': 'Bearer ' + app['control_token']})
    assert error.value.status == 400
    assert not app['acp_connections'] and not runtime.started
    peer = await connect(host)
    await peer.socket.close()
    await until(lambda: not app['acp_connections'])
    assert runtime.closed == 0


async def test_two_connections_project_independently_and_stop_only_their_turn(host):
    app, _, runtime, path = host
    service = app['service']
    first, second = await connect(host), await connect(host)
    a, b = await first.new(path), await second.new(path)
    for sid in (a, b):
        source_plan(service, service._session(sid))
        assert not service.access_mode.full(service._session(sid))
    one = asyncio.create_task(first.prompt(a, 'First input'))
    two = asyncio.create_task(second.prompt(b, 'Second input'))
    await until(lambda: len(runtime.sent) == 2)
    inputs = {sid: identity for sid, _, identity in runtime.sent}
    await service.on_runtime_event('assistant.delta', {
        'sessionId': a, 'inputId': inputs[a], 'text': 'Only first'})
    for phase, extra in [('running', {'input': {'value': 42}}), ('completed', {'output': {'answer': 42}})]:
        await service.on_runtime_event('execution.event', {'sessionId': a, 'turnId': inputs[a],
            'id': 'tool', 'toolCallId': 'same-id', 'kind': 'tool', 'phase': phase,
            'label': 'Calculator', **extra})
    await second.cancel(a)  # Foreign connection cannot interrupt first's turn.
    await second.cancel(b)
    assert (await two)['result'] == {'stopReason': 'cancelled'}
    assert runtime.stopped == [b]
    assert not one.done()
    await finish(service, a, inputs[a], 'Only first')
    assert (await one)['result'] == {'stopReason': 'end_turn'}
    updates = [row['params'] for row in first.updates]
    assert all(row['sessionId'] == a for row in updates)
    projected = [row['update'] for row in updates]
    assert [row['sessionUpdate'] for row in projected] == [
        'agent_message_chunk', 'tool_call', 'tool_call_update']
    assert projected[1]['rawInput'] == {'value': 42}
    assert projected[2]['rawOutput'] == {'answer': 42}
    assert projected[1]['toolCallId'] == projected[2]['toolCallId']
    assert all(row['params']['sessionId'] == b for row in second.updates)
    await first.socket.close()
    await second.socket.close()
    await until(lambda: not app['acp_connections'])
    assert runtime.closed == 0 and not runtime.bindings


async def test_eof_retains_accepted_input_then_exact_resume_without_replay(host):
    app, _, runtime, path = host
    service = app['service']
    first = await connect(host)
    sid = await first.new(path)
    source_plan(service, service._session(sid))
    runtime.send_ack.clear()
    prompt = asyncio.create_task(first.prompt(sid, 'Accepted once'))
    await until(lambda: len(runtime.sent) == 1)
    input_id = runtime.sent[0][2]
    await first.socket.close()
    await asyncio.gather(prompt, return_exceptions=True)
    await until(lambda: not app['acp_connections'])
    assert not runtime.cancelled_sends and runtime.closed == 0 and not runtime.stopped
    assert service._session(sid)['status'] == 'working'
    runtime.send_ack.set()
    await finish(service, sid, input_id)
    second = await connect(host)
    before = len(service.state['sessions'])
    missing = await second.call('session/resume', sessionId='does-not-exist', cwd=str(path), mcpServers=[])
    assert 'error' in missing and len(service.state['sessions']) == before
    assert (await second.call('session/resume', sessionId=sid, cwd=str(path), mcpServers=[]))['result'] == {}
    assert runtime.sent == [(sid, 'Accepted once', input_id)]
    assert not second.updates  # Resume does not replay prior transcript.
    follow = asyncio.create_task(second.prompt(sid, 'Explicit follow-up'))
    await until(lambda: len(runtime.sent) == 2)
    await finish(service, sid, runtime.sent[-1][2], 'Second result')
    assert (await follow)['result']['stopReason'] == 'end_turn'
    assert len([m for m in service._session(sid)['messages'] if m.get('role') == 'user']) == 2
    await second.socket.close()


async def test_http_mcp_is_transient_and_malformed_content_never_partially_admits(host):
    app, _, runtime, path = host
    peer = await connect(host, bundle='foundation')
    good = {'type': 'http', 'name': 'context', 'url': 'http://127.0.0.1:1234/mcp',
            'headers': [{'name': 'Authorization', 'value': 'Bearer synthetic-private'}]}
    for servers in [[good, {'name': 'bad'}], [good, good],
                    [{'name': 'process', 'command': '/bin/false', 'args': [], 'env': []}],
                    [{**good, 'headers': [{'name': 'bad', 'value': 4}]}]]:
        row = await peer.call('session/new', cwd=str(path), mcpServers=servers)
        assert row['error']['code'] == -32602
        assert not runtime.bindings and not app['service'].state['sessions']
    sid = await peer.new(path, [good])
    assert runtime.bindings[sid][1] == {'context': {'type': 'http', 'url': good['url'],
            'headers': {'Authorization': 'Bearer synthetic-private'}}}
    assert 'synthetic-private' not in json.dumps(app['service'].state)
    assert 'synthetic-private' not in '\n'.join(app['service'].db.iterdump())
    for blocks in [[{'type': 'text', 'text': 'would be truncated'}, {'type': 'image', 'data': 'x', 'mimeType': 'image/png'}],
                   [{'type': 'text', 'text': 42}], []]:
        row = await peer.call('session/prompt', sessionId=sid, prompt=blocks)
        assert row['error']['code'] == -32602
    assert not runtime.sent
    wrong = path / 'other'
    wrong.mkdir()
    assert 'error' in await peer.call('session/new', cwd=str(wrong), mcpServers=[])
    assert 'error' in await peer.call('session/new', cwd=str(path), mcpServers=[], additionalDirectories=[str(wrong)])
    await peer.socket.close()
    await until(lambda: not app['acp_connections'])
    assert not runtime.bindings


@pytest.mark.parametrize('frame', ['binary', 'array', 'oversized'])
async def test_invalid_frames_close_only_the_connection(host, monkeypatch, frame):
    import amplifier_web.acp_server as endpoint
    monkeypatch.setattr(endpoint, 'MAX_MESSAGE_BYTES', 1024)
    app, _, runtime, _ = host
    peer = await connect(host)
    if frame == 'binary':
        await peer.socket.send_bytes(b'{}')
    else:
        await peer.socket.send_str('[]' if frame == 'array' else 'x' * 1025)
    response = await peer.socket.receive(timeout=3)
    assert response.type in {WSMsgType.CLOSE, WSMsgType.CLOSED}
    await until(lambda: not app['acp_connections'])
    assert not runtime.started and runtime.closed == 0


async def test_host_shutdown_drains_connection_observers_before_service_close(host):
    app, _, runtime, _ = host
    peer = await connect(host)
    await asyncio.wait_for(app.shutdown(), 3)
    assert not app['acp_connections']
    assert not app['service']._execution_observers.attachments
    assert runtime.closed == 0  # The normal app cleanup remains the sole owner.
    await peer.socket.close()
