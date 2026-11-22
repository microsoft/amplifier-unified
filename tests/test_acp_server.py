"""Actual SDK dispatch and AppService ownership over authenticated WebSockets."""
import asyncio
import json

from aiohttp import WSMsgType, WSServerHandshakeError
from acp import RequestError
import pytest

from amplifier_web.auth import new_session
from amplifier_web.errors import AppError
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
        self.permission = None

    async def call(self, method, **params):
        self.serial += 1
        identity = self.serial
        await self.socket.send_json({'jsonrpc': '2.0', 'id': identity,
                                     'method': method, 'params': params})
        async with asyncio.timeout(5):
            while True:
                row = await self.socket.receive_json()
                if row.get('method') == 'session/request_permission' and self.permission is not None:
                    outcome = await self.permission(row['params'])
                    await self.socket.send_json({'jsonrpc': '2.0', 'id': row['id'], 'result': {'outcome': outcome}})
                    continue
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


@pytest.mark.parametrize('method,owner_method,error_type', [
    ('session/new', 'create', OSError),
    ('session/resume', 'attach', ValueError),
    ('session/prompt', 'prompt', AppError),
    ('_amplifier/retire', 'retire', RuntimeError),
])
async def test_owner_failures_preserve_reason_without_requesting_authentication(
        host, monkeypatch, method, owner_method, error_type):
    app, _, runtime, path = host
    peer = await connect(host)
    owner, = app['service']._execution_observers.attachments
    params = {'cwd': str(path), 'mcpServers': []}
    if method != 'session/new':
        created = await app['service'].dispatch('session.create', {
            'workspace': str(path), 'bundle': 'foundation', 'select': False,
        }, origin='ui', include_state=False)
        sid = created['result']['sessionId']
        params['sessionId'] = sid
        if method != 'session/resume':
            assert (await peer.call('session/resume', **params))['result'] == {}
            params = {'sessionId': sid}
        if method == 'session/prompt':
            params['prompt'] = [{'type': 'text', 'text': 'One explicit input'}]
    calls = []
    reason = 'Owner refused this request; inspect retained work before retrying.'

    async def refuse(*args, **kwargs):
        calls.append((args, kwargs))
        raise error_type(reason)

    monkeypatch.setattr(owner, owner_method, refuse)
    row = await peer.call(method, **params)
    assert row['error']['code'] == -31000
    assert row['error']['message'] == reason
    assert len(calls) == 1
    assert not runtime.started and not runtime.sent and not runtime.stopped
    await peer.socket.close()


async def test_busy_resume_keeps_saved_identity_and_does_not_prompt_or_stop(host, monkeypatch):
    app, _, runtime, path = host
    service = app['service']
    first = await connect(host)
    sid = await first.new(path)
    await first.socket.close()
    await until(lambda: not app['acp_connections'])
    before = json.dumps(service._session(sid), sort_keys=True)
    sessions = {row['id'] for row in service.state['sessions']}
    calls = []
    reason = 'The existing worker still owns another MCP attachment; wait for its retirement before reattaching'

    async def busy(identity, attachment, servers):
        calls.append((identity, attachment, servers))
        raise ValueError(reason)

    monkeypatch.setattr(runtime, 'bind_ephemeral_mcp', busy)
    second = await connect(host)
    row = await second.call('session/resume', sessionId=sid, cwd=str(path), mcpServers=[])
    assert row['error']['code'] == -31000 and row['error']['message'] == reason
    assert len(calls) == 1 and calls[0][0] == sid
    owner, = service._execution_observers.attachments
    assert not owner.attached and not owner.pending
    assert {row['id'] for row in service.state['sessions']} == sessions
    assert json.dumps(service._session(sid), sort_keys=True) == before
    assert not runtime.started and not runtime.sent and not runtime.stopped
    assert not second.updates
    await second.socket.close()


async def test_unresolved_prompt_outcome_is_not_authentication_or_automatic_retry(host, monkeypatch):
    app, _, runtime, path = host
    peer = await connect(host)
    sid = await peer.new(path)
    owner, = app['service']._execution_observers.attachments
    calls = []

    async def unresolved(identity, input_id, text, on_event):
        calls.append((identity, input_id, text))
        return {'outcome': 'unknown'}

    monkeypatch.setattr(owner, 'prompt', unresolved)
    row = await peer.prompt(sid, 'Outcome is not known')
    assert row['error']['code'] == -31000
    assert row['error']['message'] == 'Outcome unresolved; inspect retained owner state before retrying.'
    assert len(calls) == 1 and calls[0][0] == sid
    assert owner.attached == {sid} and not runtime.stopped
    await peer.socket.close()


async def test_explicit_authentication_error_retains_standard_code_and_data(host, monkeypatch):
    app, _, runtime, path = host
    peer = await connect(host)
    owner, = app['service']._execution_observers.attachments
    calls = []

    async def needs_auth(*args, **kwargs):
        calls.append((args, kwargs))
        raise RequestError.auth_required({'methodId': 'fixture-sign-in'})

    monkeypatch.setattr(owner, 'create', needs_auth)
    row = await peer.call('session/new', cwd=str(path), mcpServers=[])
    assert row['error']['code'] == -32000
    assert row['error']['message'] == 'Authentication required'
    assert row['error']['data'] == {'methodId': 'fixture-sign-in'}
    assert len(calls) == 1 and not runtime.started and not runtime.sent
    await peer.socket.close()


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


async def test_first_attachment_observes_callback_captured_by_existing_ui_worker(host):
    app, _, runtime, path = host
    service = app['service']
    created = await service.dispatch('session.create', {'workspace': str(path), 'bundle': 'foundation'},
                                     origin='ui', include_state=False)
    sid = created['result']['sessionId']
    source_plan(service, service._session(sid))
    captured = []
    start = runtime.start
    async def capture_start(session, emit):
        captured.append(emit)
        await start(session, emit)
    runtime.start = capture_start
    # Ordinary UI/native startup happens before any ACP attachment. A live
    # worker may retain this callback when input later takes the steering path.
    await runtime.start(service._session(sid), service.on_runtime_event)
    assert not app['acp_connections']
    peer = await connect(host)
    assert (await peer.call('session/resume', sessionId=sid, cwd=str(path), mcpServers=[]))['result'] == {}
    pending = asyncio.create_task(peer.prompt(sid, 'New explicit input'))
    await until(lambda: len(runtime.sent) == 1)
    input_id = runtime.sent[0][2]
    await captured[0]('assistant.delta', {'sessionId': sid, 'inputId': input_id, 'text': 'Captured callback'})
    await captured[0]('execution.event', {'sessionId': sid, 'turnId': input_id, 'kind': 'tool',
        'phase': 'completed', 'id': 'captured-tool', 'label': 'Existing worker', 'output': {'value': 42}})
    await finish(service, sid, input_id, 'Captured callback')
    assert (await pending)['result']['stopReason'] == 'end_turn'
    projected = [row['params']['update'] for row in peer.updates]
    assert [row['sessionUpdate'] for row in projected] == ['agent_message_chunk', 'tool_call']
    assert projected[0]['content']['text'] == 'Captured callback'
    assert projected[1]['rawOutput'] == {'value': 42}
    assert runtime.stopped == [] and len(runtime.sent) == 1
    await peer.socket.close()


async def test_agent_options_target_exact_attachment_and_use_cached_choices(host, monkeypatch):
    app, _, runtime, path = host
    service = app['service']
    async def cached(_, session, *, include_history):
        assert not include_history
        service.state.setdefault('runtimeControl', {}).setdefault(session['id'], {})['configuration.catalog'] = {
            'phase':'ready', 'configurationRevision':0, 'nextMessageRevision':session.get('nextMessageRevision',0),
            'providers':[{'id':k, 'sharedCatalogKey':k} for k in ('a','b')],
            'effective':{'instance':'a','model':'first'}}
        service.state['modelCatalogs'] = {k:{'models':['first','second'],
            'metadata':{'modelListInfo':{'request_reasoning_efforts':{'second':['low','high']}}}} for k in ('a','b')}
    monkeypatch.setattr('amplifier_web.conversation_models.browse', cached)
    monkeypatch.setattr('amplifier_web.provider_catalog.configured_binding', lambda *a, **kw: 'key')
    peer, other = await connect(host), await connect(host)
    try:
        sid = await peer.new(path)
        foreign = await other.new(path)
        assert 'error' in await peer.call('session/set_config_option', sessionId=foreign, configId='provider', value='b')
        for key, value in [('provider','b'),('model','second'),('effort','high')]:
            result = await peer.call('session/set_config_option', sessionId=sid, configId=key, value=value)
            assert 'result' in result, result
            options = result['result']['configOptions']
            assert next(row for row in options if row['id']==key)['currentValue'] == value
        assert service._session(sid)['pendingModelSelection'] == {'instance':'b','model':'second','effort':'high'}
        assert not service._session(foreign).get('pendingModelSelection')
        assert not runtime.sent
    finally:
        await peer.socket.close()
        await other.socket.close()
