"""Saved documents and reviewed live authority have separate lifetimes."""
import asyncio
from copy import deepcopy

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.smart_canvas import SmartCanvas
from amplifier_web.smart_tools import SmartToolsManager, configuration_key
from amplifier_web.mcp_view_recovery import inspect, source
from amplifier_web.resource_files import root


HTML = '<!doctype html><p>Immutable saved tool document</p>'
TOOLS = [
    {'name': 'inspect', 'inputSchema': {'type': 'object'}, '_meta': {'ui': {'resourceUri': 'ui://fixture'}}},
    {'name': 'change', 'inputSchema': {'type': 'object', 'properties': {'amount': {'type': 'integer'}}}},
]


def transport(app, tools=None):
    app.smart_tools = SmartToolsManager(app)
    app.smart_canvas = SmartCanvas(app)
    manager = app.smart_tools
    definitions = deepcopy(tools or TOOLS)
    async def read_app(identity, uri):
        return {'html': HTML, 'tools': ['inspect', 'change']}
    async def connect(identity, **kwargs):
        server = manager._server(identity)
        assert kwargs['expected_configuration'] == configuration_key(server)
        manager.schemas[identity] = deepcopy(definitions)
        server.update(status='connected', catalogState='current', catalogRevision='after-reconnect', tools=deepcopy(definitions))
        server['account'] = {'status': 'verified' if server.get('accountBinding') else 'unknown'}
        app._publish()
        return server
    manager.read_app = read_app
    manager.connect = connect
    return manager


async def setup(app):
    transport(app)
    await app.dispatch('session.create', {})
    app._message(app._session(), 'user', 'Publish a synthetic tool document')
    server = {'id': 'fixture', 'name': 'Fixture', 'command': 'never-executed', 'args': [], 'env': {},
              'status': 'connected', 'catalogState': 'current', 'catalogRevision': 'initial', 'tools': deepcopy(TOOLS)}
    app.state['smartTools']['servers'].append(server)
    app.smart_tools.schemas['fixture'] = deepcopy(TOOLS)
    old = {'id': 'original-call', 'status': 'completed', 'action': 'smartTools.call',
           'configuration': configuration_key(server), 'target': {'id': 'fixture', 'name': 'inspect', 'sessionId': app._session()['id']},
           'arguments': {'saved': 'launch'}, 'result': {'content': [{'type': 'text', 'text': 'Historical result'}]}}
    app.smart_tools.persist_operation(old)
    await app.smart_canvas.open({'id': 'fixture', 'tool': 'inspect', 'operationId': old['id']})
    return app.state['canvas']['id']


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'data', workspace=tmp_path)
    await setup(service)
    yield service
    await service.close()


async def recover(app, identity, **kwargs):
    status = inspect(app, identity)
    receipt = await app.dispatch('smartTools.reconnectView', {
        'canvasId': identity, 'expectedBindingRevision': status['bindingRevision'], **kwargs})
    while app.tasks:
        await asyncio.gather(*list(app.tasks))
    return app.smart_tools.operation(receipt['operationId'])


async def test_restart_reconnect_preserves_document_history_and_does_not_replay(tmp_path):
    app = AppService(tmp_path / 'data', workspace=tmp_path)
    try:
        identity = await setup(app)
        row = deepcopy(app.state['canvasArtifacts'][0])
        original = deepcopy(app.smart_tools.operation('original-call'))
        binding = deepcopy(app.state['canvas']['mcp'])
        assert binding['contractFingerprint']
        await app.close()
        app = AppService(tmp_path / 'data', workspace=tmp_path)
        transport(app)
        assert inspect(app, identity)['status'] == 'disconnected'
        assert source(app, identity) == HTML
        with pytest.raises(AppError, match='Reconnect'):
            app.smart_canvas.binding(identity)
        operation = await recover(app, identity)
        assert operation['status'] == 'completed' and operation['result']['status'] == 'ready'
        assert len(app.state['canvasArtifacts']) == 1
        assert app.state['canvasArtifacts'][0]['body'] == row['body']
        assert app.state['canvasArtifacts'][0]['messageId'] == row['messageId']
        assert source(app, identity) == HTML
        assert app.smart_tools.operation('original-call') == original
        assert app.state['canvas']['mcp']['toolArguments'] == binding['toolArguments']
        assert app.state['canvas']['mcp']['operationId'] == binding['operationId']
        assert app.state['canvas']['mcp']['allowedTools'] == binding['allowedTools']
        assert app.state['canvas']['mcp']['contractFingerprint'] == binding['contractFingerprint']
        assert app.state['canvas']['mcp']['catalogRevision'] != binding['catalogRevision']
        assert app.db.execute("SELECT count(*) FROM smart_tool_operations WHERE json_extract(value,'$.action')='smartTools.call'").fetchone()[0] == 1
        app.smart_canvas.binding(identity)
    finally:
        await app.close()


@pytest.mark.parametrize('change', ['schema', 'resource', 'grant', 'configuration', 'account'])
async def test_changed_authority_fails_without_changing_saved_source_or_grants(app, change):
    identity = app.state['canvas']['id']
    binding = deepcopy(app.state['canvas']['mcp'])
    server = app.state['smartTools']['servers'][0]
    server['catalogRevision'] = 'different-generation'
    tools = app.smart_tools.schemas['fixture']
    if change == 'schema':
        tools[1]['inputSchema']['properties']['amount']['type'] = 'string'
    elif change == 'resource':
        tools[0]['_meta']['ui']['resourceUri'] = 'ui://other'
    elif change == 'grant':
        tools[1]['_meta'] = {'ui': {'visibility': ['model']}}
    elif change == 'configuration':
        server['command'] = 'another-executable'
    else:
        server['accountBinding'] = {'issuer': 'https://issuer.example', 'subject': 'another-account'}
    status = inspect(app, identity)
    assert not status['canReconnect']
    with pytest.raises(AppError):
        await recover(app, identity)
    assert app.state['canvas']['mcp'] == binding
    assert source(app, identity) == HTML


async def test_new_tools_are_not_granted_and_reconnect_command_is_idempotent(app):
    identity = app.state['canvas']['id']
    app.smart_tools.schemas['fixture'].append({'name': 'new-dangerous-action', 'inputSchema': {}})
    app.state['smartTools']['servers'][0]['catalogRevision'] = 'refreshed'
    status = inspect(app, identity)
    assert status['status'] == 'reconnect_required'
    args = {'canvasId': identity, 'expectedBindingRevision': status['bindingRevision']}
    receipt = await app.dispatch('smartTools.reconnectView', args, command_id='one-reconnect')
    duplicate = await app.dispatch('smartTools.reconnectView', args, command_id='one-reconnect')
    assert duplicate['duplicate'] and duplicate['operationId'] == receipt['operationId']
    while app.tasks:
        await asyncio.gather(*list(app.tasks))
    assert app.smart_tools.operation('one-reconnect')['status'] == 'completed'
    assert app.state['canvas']['mcp']['allowedTools'] == ['inspect', 'change']
    with pytest.raises(AppError, match='changed'):
        await app.dispatch('smartTools.reconnectView', args)


async def test_legacy_review_is_exact_and_keeps_existing_tab(app):
    identity = app.state['canvas']['id']
    app.state['canvas']['mcp'].pop('contractFingerprint')
    app.state['canvas']['mcp'].pop('accountIdentity')
    status = inspect(app, identity)
    assert status['status'] == 'review_required'
    operation = await recover(app, identity)
    assert operation['result']['status'] == 'review_required'
    assert 'contractFingerprint' not in app.state['canvas']['mcp']
    operation = await recover(app, identity, reviewedContract='not-the-reviewed-contract')
    assert operation['result']['status'] == 'review_required'
    operation = await recover(app, identity, reviewedContract=status['review']['fingerprint'])
    assert operation['result']['status'] == 'ready'
    assert len(app.state['canvasArtifacts']) == 1 and source(app, identity) == HTML


async def test_verified_account_can_reconnect_after_restart_but_cannot_be_replaced(app):
    identity = app.state['canvas']['id']
    principal = {'issuer': 'https://issuer.example', 'subject': 'saved-account'}
    app.state['canvas']['mcp']['accountIdentity'] = deepcopy(principal)
    server = app.state['smartTools']['servers'][0]
    server.update(accountBinding=principal, account={'status': 'unconfirmed'}, status='disconnected', catalogState='stale')
    assert inspect(app, identity)['status'] == 'disconnected'
    operation = await recover(app, identity)
    assert operation['result']['status'] == 'ready'
    server['accountBinding'] = {**principal, 'subject': 'different-account'}
    assert inspect(app, identity)['status'] == 'account_review_required'
    with pytest.raises(AppError, match='account changed'):
        app.smart_canvas.binding(identity)


async def test_connection_lock_rechecks_expected_configuration_before_start(app):
    # Exercise the production lifecycle guard, not the synthetic transport.
    manager = SmartToolsManager(app)
    try:
        with pytest.raises(ValueError, match='configuration changed before reconnecting'):
            await manager.connect('fixture', expected_configuration='different')
        assert not manager.connections
    finally:
        await manager.close()


async def test_catalog_refresh_commits_definitions_and_generation_together(app):
    manager = app.smart_tools
    before = deepcopy(manager.schemas['fixture'])
    changed = deepcopy(TOOLS)
    changed[1]['inputSchema']['properties']['amount']['type'] = 'string'
    received = asyncio.Event()
    class Connection:
        catalog_epoch = 1
        async def request(self, method, **kwargs):
            received.set()
            return {'tools': changed}
    connection = Connection()
    manager.connections['fixture'] = connection
    try:
        async with app.lock:
            pending = asyncio.create_task(manager._refresh('fixture', connection))
            await received.wait()
            assert manager.schemas['fixture'] == before
            assert manager._server('fixture')['catalogRevision'] == 'initial'
        await pending
        assert manager.schemas['fixture'] == changed
        assert manager._server('fixture')['catalogRevision'] != 'initial'
        with pytest.raises(AppError, match='schemas changed'):
            app.smart_canvas.binding(app.state['canvas']['id'])
    finally:
        manager.connections.clear()


async def test_reconnect_does_not_retarget_after_client_navigates(app):
    identity = app.state['canvas']['id']
    app.clients.attach('viewer')
    gate, started = asyncio.Event(), asyncio.Event()
    connect = app.smart_tools.connect
    async def delayed(*args, **kwargs):
        started.set()
        await gate.wait()
        return await connect(*args, **kwargs)
    app.smart_tools.connect = delayed
    with app.clients.bind('viewer'):
        before = deepcopy(app.state['canvas']['mcp'])
        receipt = await app.dispatch('smartTools.reconnectView', {'canvasId': identity, 'expectedBindingRevision': inspect(app, identity)['bindingRevision']})
        await started.wait()
        await app.dispatch('session.create', {})
        other = app.state['selectedSessionId']
    gate.set()
    while app.tasks:
        await asyncio.gather(*list(app.tasks))
    assert app.smart_tools.operation(receipt['operationId'])['status'] == 'failed'
    assert app.clients.records['viewer']['selectedSessionId'] == other
    assert app.state['canvas']['mcp'] == before


async def test_agent_and_user_share_scoped_status_and_reconnect(app):
    identity, sid = app.state['canvas']['id'], app._session()['id']
    app.clients.attach('one')
    app.clients.attach('two')
    with pytest.raises(AppError, match='Multiple clients'):
        await app.app_bridge('dispatch', {'action': 'smartTools.viewStatus', 'args': {'canvasId': identity}}, sid)
    result = await app.app_bridge('dispatch', {'action': 'smartTools.viewStatus', 'args': {'canvasId': identity, 'clientId': 'two'}}, sid)
    args = {'canvasId': identity, 'clientId': 'two', 'expectedBindingRevision': result['result']['bindingRevision']}
    receipt = await app.app_bridge('dispatch', {'action': 'smartTools.reconnectView', 'args': args}, sid)
    while app.tasks:
        await asyncio.gather(*list(app.tasks))
    assert app.smart_tools.operation(receipt['operationId'])['result']['status'] == 'ready'
    with app.clients.bind('one'):
        assert inspect(app, identity)['status'] == 'ready'
        await app.dispatch('session.create', {})
        other = app.state['selectedSessionId']
    with pytest.raises(AppError, match='another chat'):
        await app.app_bridge('dispatch', {'action': 'smartTools.reconnectView', 'args': args}, other)


async def test_http_serves_saved_document_without_live_authority_and_preserves_missing_reference(authenticated_client, tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime
    app = await create_app(tmp_path / 'data', workspace=tmp_path, runtime=Runtime(), voice=False,
                           preload_providers=False, background_updates=False)
    client = await authenticated_client(app)
    service = app['service']
    await service.smart_tools.close()
    identity = await setup(service)
    service.state['smartTools']['servers'][0].update(status='disconnected', catalogState='stale')
    status = await (await client.get(f'/api/canvas/{identity}/status')).json()
    assert status['status'] == 'disconnected' and status['source'] == 'available'
    document = await client.get(f'/api/canvas/{identity}/document')
    assert document.status == 200 and await document.text() == HTML
    assert "connect-src 'none'" in document.headers['Content-Security-Policy']
    assert document.headers['Cache-Control'] == 'no-store'
    binding = service.state['canvas'].pop('mcp')
    assert (await (await client.get(f'/api/canvas/{identity}/status')).json())['status'] == 'binding_unavailable'
    assert await (await client.get(f'/api/canvas/{identity}/document')).text() == HTML
    service.state['canvas']['mcp'] = binding
    reference = deepcopy(service.state['canvasArtifacts'][0]['body'])
    # No inline copy remains from which an ordinary save could recover the
    # exact original bytes while this missing-source request is in flight.
    service.state['canvas']['contentResource'] = reference
    service.state['canvas'].pop('content', None)
    service._publish()
    (root(service.db) / (reference['$resource'] + '.json')).unlink()
    assert (await (await client.get(f'/api/canvas/{identity}/status')).json())['status'] == 'source_unavailable'
    response = await client.get(f'/api/canvas/{identity}/document')
    assert response.status == 404
    await service.dispatch('canvas.select', {'id': identity})
    assert service.state['canvasArtifacts'][0]['body'] == reference


def exact_target(view):
    return {key: view[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}


async def mount_view(service, identity, preview, *, follow=True, client='one'):
    row = next(row for row in service.state['canvasArtifacts'] if row['id'] == identity)
    with service.clients.bind(client):
        return (await service.dispatch('canvas.views.inline', {'resourceId': identity, 'version': 1,
            'messageId': row['publications'][0]['messageId'], 'previewId': preview,
            'followLatest': follow}))['result']


class ViewConnection:
    """Synthetic transport only; host schemas, receipts and HTTP are real."""
    failure = None
    catalog_epoch = 1

    def __init__(self):
        from types import SimpleNamespace
        self.task = SimpleNamespace(done=lambda: False)
        self.requests = []
        self.gate = None
        self.started = asyncio.Event()

    async def request(self, method, *args, **kwargs):
        self.requests.append((method, args))
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if method == 'call_tool':
            return {'structuredContent': {'amount': args[1].get('amount')}}
        return {'contents': [{'uri': args[0], 'text': 'Exact view resource'}]}

    async def close(self):
        pass


@pytest.fixture
async def view_http(authenticated_client, tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime
    app = await create_app(tmp_path / 'views', workspace=tmp_path, runtime=Runtime(),
        voice=False, preload_providers=False, background_updates=False)
    client = await authenticated_client(app)
    service = app['service']
    await service.smart_tools.close()
    identity = await setup(service)
    service.clients.attach('one')
    service.clients.attach('two')
    connection = ViewConnection()
    service.smart_tools.connections['fixture'] = connection
    service.smart_tools._server('fixture')['capabilities'] = {'resources': {}}
    one = await mount_view(service, identity, 'one')
    two = await mount_view(service, identity, 'two')
    with service.clients.bind('one'):
        await service.dispatch('canvas.select', {'id': identity, 'version': 1})
    yield client, service, identity, one, two, connection
    connection.gate = None


def query(view, client='one', **extra):
    return {**exact_target(view), 'clientId': client, **extra}


async def test_http_native_mcp_views_have_exact_source_result_status_and_one_explicit_call(view_http):
    client, service, identity, one, two, connection = view_http
    saved = deepcopy(service.state['canvasArtifacts'][0])
    for view in (one, two):
        status = await client.get(f'/api/canvas/{identity}/status', params=query(view))
        assert status.status == 200 and (await status.json())['status'] == 'ready'
        document = await client.get(f'/api/canvas/{identity}/document', params=query(view))
        assert document.status == 200 and await document.text() == HTML
        assert "connect-src 'none'" in document.headers['Content-Security-Policy']
        source_read = await client.get(f"/api/canvas/views/{view['viewId']}/source", params=query(view))
        assert (await source_read.json())['content'] == HTML
        source_text = await client.get(f'/api/canvas/{identity}/source', params=query(view))
        assert source_text.status == 404  # Text-copy route remains HTML/Babylon/CanvasApp only.
        result = await client.get(f'/api/canvas/{identity}/result', params=query(view))
        assert (await result.json())['result']['content'][0]['text'] == 'Historical result'
        tools = await client.get(f'/api/canvas/{identity}/tools', params=query(view))
        assert {row['name'] for row in (await tools.json())['tools']} == {'inspect', 'change'}
    assert connection.requests == []  # No repeated launch/call on any retained read.
    for view, text in ((one, 'A'), (two, 'B')):
        response = await client.post('/api/actions', params={'clientId': 'one'}, json={
            'action': 'smartTools.context', 'args': {**exact_target(view),
                'canvasId': identity, 'context': {'selection': text}}})
        assert response.status == 200
    with service.clients.bind('one'):
        assert service.canvas_views.canvas(one['viewId'])['mcp']['context'] == {'selection': 'A'}
        assert service.canvas_views.canvas(two['viewId'])['mcp']['context'] == {'selection': 'B'}
    assert service.state['canvasArtifacts'][0] == saved
    payload = {'id': 'explicit-view-command', 'name': 'change', 'arguments': {'amount': 4}}
    response = await client.post(f'/api/canvas/{identity}/tools/call', params=query(one), json=payload)
    assert response.status == 200 and (await response.json())['status'] == 'completed'
    duplicate = await client.post(f'/api/canvas/{identity}/tools/call', params=query(one), json=payload)
    assert duplicate.status == 200
    assert connection.requests == [('call_tool', ('change', {'amount': 4}))]
    conflict = await client.post(f'/api/canvas/{identity}/tools/call', params=query(one),
        json={**payload, 'arguments': {'amount': 5}})
    assert conflict.status == 409 and len(connection.requests) == 1
    result = await client.get(f'/api/canvas/{identity}/resources',
        params=query(two, uri='fixture://object'))
    assert (await result.json())['contents'][0]['text'] == 'Exact view resource'
    with service.clients.bind('one'):
        assert service.canvas_views.canvas(one['viewId'])['mcp']['lastOperationId'] == payload['id']
        assert 'lastOperationId' not in service.canvas_views.canvas(two['viewId'])['mcp']


@pytest.mark.parametrize('change', ['schema', 'account', 'grant', 'configuration'])
async def test_http_mcp_exact_view_authority_changes_refuse_without_tool_work(view_http, change):
    client, service, identity, one, _, connection = view_http
    server = service.smart_tools._server('fixture')
    if change == 'schema':
        service.smart_tools.schemas['fixture'][1]['inputSchema']['properties']['amount']['type'] = 'string'
    elif change == 'grant':
        service.smart_tools.schemas['fixture'][1]['_meta'] = {'ui': {'visibility': ['model']}}
    elif change == 'account':
        server['accountBinding'] = {'issuer': 'https://issuer.example', 'subject': 'different'}
    else:
        server['command'] = 'different'
    response = await client.get(f'/api/canvas/{identity}/tools', params=query(one))
    assert response.status == 409
    response = await client.post(f'/api/canvas/{identity}/tools/call', params=query(one),
        json={'id': 'refused-view-command', 'name': 'change', 'arguments': {'amount': 4}})
    assert response.status == 409 and connection.requests == []


async def test_http_mcp_historical_inline_and_foreign_or_partial_targets_fail_closed(view_http):
    client, service, identity, one, _, connection = view_http
    historical = await mount_view(service, identity, 'historical', follow=False)
    with service.clients.bind('one'):
        await service.dispatch('canvas.select', {'id': identity})
    response = await client.get(f'/api/canvas/{identity}/status', params=query(historical))
    assert (await response.json())['status'] == 'saved_version'
    for route in ('tools', 'resources'):
        assert (await client.get(f'/api/canvas/{identity}/{route}', params=query(historical))).status == 409
    assert (await client.post(f'/api/canvas/{identity}/tools/call', params=query(historical),
        json={'id': 'historical-call', 'name': 'change'})).status == 409
    with service.clients.bind('one'):
        status = inspect(service, identity, exact_target(historical))
        with pytest.raises(AppError):
            await service.dispatch('smartTools.reconnectView', {**exact_target(historical),
                'canvasId': identity, 'expectedBindingRevision': status['bindingRevision']})
    for args in (query(one, client='two'), query(one, generation=one['generation'] + 1),
                 {'clientId': 'one', 'resourceId': identity}):
        assert (await client.get(f'/api/canvas/{identity}/status', params=args)).status in {400, 404, 409}
    assert connection.requests == []
    with service.clients.bind('two'):
        await service.dispatch('session.create', {})
    assert (await client.get(f'/api/canvas/{identity}/status', params=query(one, client='two'))).status != 200


@pytest.mark.parametrize('transition', ['release', 'generation'])
async def test_http_mcp_resource_rechecks_exact_view_after_await(view_http, transition):
    client, service, identity, one, two, connection = view_http
    connection.gate = asyncio.Event()
    pending = asyncio.create_task(client.get(f'/api/canvas/{identity}/resources',
        params=query(one, uri='fixture://slow')))
    await asyncio.wait_for(connection.started.wait(), 2)
    with service.clients.bind('one'):
        await service.dispatch('canvas.views.release' if transition == 'release' else 'canvas.views.recover',
            exact_target(one))
    connection.gate.set()
    response = await pending
    assert response.status in {404, 409}
    peer = await client.get(f'/api/canvas/{identity}/status', params=query(two))
    assert peer.status == 200 and len(connection.requests) == 1


async def test_mcp_reconnect_updates_canonical_authority_without_overwriting_peer_context(view_http):
    _, service, identity, one, two, connection = view_http
    with service.clients.bind('one'):
        for view, value in ((one, 'one'), (two, 'two')):
            await service.dispatch('smartTools.context', {**exact_target(view), 'canvasId': identity,
                'context': {'selection': value}})
        before = deepcopy(service.state['canvasArtifacts'][0])
        status = inspect(service, identity, exact_target(one))
        receipt = await service.dispatch('smartTools.reconnectView', {**exact_target(one),
            'canvasId': identity, 'expectedBindingRevision': status['bindingRevision']})
        await service.wait_smart_tool(receipt['operationId'])
        assert service.smart_tools.operation(receipt['operationId'])['status'] == 'completed'
        assert inspect(service, identity, exact_target(two))['status'] == 'ready'
        assert service.canvas_views.canvas(two['viewId'])['mcp']['context'] == {'selection': 'two'}
        assert service.canvas_views.canvas(one['viewId'])['mcp']['context'] == {'selection': 'one'}
        after = service.state['canvasArtifacts'][0]
        assert after['versions'] == before['versions'] and after['publications'] == before['publications']
        assert after['body'] == before['body'] and after['revision'] == before['revision']
    assert connection.requests == []


async def test_http_operation_receipt_retains_host_binding_from_queued_to_mapped_completion(view_http):
    client, service, identity, one, two, connection = view_http
    connection.gate = asyncio.Event()
    payload = {'id': 'bound-operation', 'action': 'smartTools.appCall',
               'args': {**exact_target(one), 'canvasId': identity, 'name': 'change', 'arguments': {'amount': 6}}}
    response = await client.post('/api/actions', params={'clientId': 'one'},
                                headers={'X-Amplifier-State-Transport': 'delta-v1'}, json=payload)
    assert response.status == 200
    receipt = await response.json()
    assert receipt['operationId'] == payload['id']
    await asyncio.wait_for(connection.started.wait(), 2)
    path = '/api/smart-tools/operations/' + payload['id']
    pending = await client.get(path, params=query(one))
    assert pending.status == 200
    operation = await pending.json()
    assert operation['status'] in {'queued', 'running'}
    assert operation['viewBinding']['target'] == exact_target(one)
    assert operation['viewBinding']['clientId'] == 'one'
    assert set(operation['viewBinding']) == {'target', 'clientId', 'sessionId', 'bindingRevision'}
    for params in (query(two), query(one, client='two'), {'clientId': 'one', 'resourceId': identity}):
        assert (await client.get(path, params=params)).status in {400, 404, 409}
    assert (await client.get('/api/smart-tools/operations/unknown', params=query(one))).status == 409
    # A launch receipt for the same artifact is not this view's operation.
    assert (await client.get('/api/smart-tools/operations/original-call', params=query(one))).status == 409
    connection.gate.set()
    await service.wait_smart_tool(payload['id'])
    complete = await client.get(path, params=query(one))
    assert complete.status == 200
    complete = await complete.json()
    assert complete['status'] == 'completed' and complete['action'] == 'smartTools.call'
    assert complete['viewBinding'] == operation['viewBinding']
    assert complete['result']['structuredContent']['amount'] == 6
    duplicate = await client.post('/api/actions', params={'clientId': 'one'}, json=payload)
    assert duplicate.status == 200 and (await duplicate.json())['duplicate']
    assert len(connection.requests) == 1
    # Copying the same target into another host-side fixture client still
    # cannot lend it this client's operation result.
    service.clients.records['two']['canvasViews'] = deepcopy(service.clients.records['one']['canvasViews'])
    assert (await client.get(path, params=query(one, client='two'))).status != 200
    with service.clients.bind('one'):
        await service.dispatch('canvas.views.release', exact_target(one))
    assert (await client.get(path, params=query(one))).status in {404, 409}
    generic = await client.get(path)
    assert generic.status == 200 and (await generic.json())['result'] == complete['result']
    with service.clients.bind('one'):
        assert service.smart_tools.read_operation({'operationId': payload['id']})['status'] == 'completed'
    assert len(connection.requests) == 1


@pytest.mark.parametrize('change', ['account', 'configuration', 'schema', 'grant', 'disconnect'])
async def test_http_operation_results_require_current_authority_even_for_generic_activity_owner(view_http, change):
    client, service, identity, one, _, connection = view_http
    payload = {'id': 'finished-operation', 'name': 'change', 'arguments': {'amount': 3}}
    assert (await client.post(f'/api/canvas/{identity}/tools/call', params=query(one), json=payload)).status == 200
    server = service.smart_tools._server('fixture')
    if change == 'account':
        server['accountBinding'] = {'issuer': 'https://issuer.example', 'subject': 'changed'}
    elif change == 'configuration':
        server['command'] = 'different-executable'
    elif change == 'schema':
        service.smart_tools.schemas['fixture'][1]['inputSchema']['properties']['amount']['type'] = 'string'
    elif change == 'grant':
        service.smart_tools.schemas['fixture'][1]['_meta'] = {'ui': {'visibility': ['model']}}
    else:
        server['status'] = 'disconnected'
    path = '/api/smart-tools/operations/' + payload['id']
    assert (await client.get(path, params=query(one))).status == 409
    generic = await client.get(path)
    assert generic.status == 200 and (await generic.json())['status'] == 'completed'
    assert len(connection.requests) == 1


async def test_http_reconnect_receipt_returns_progress_only_then_rebinds_only_original_mount(view_http):
    client, service, identity, one, two, connection = view_http
    server = service.smart_tools._server('fixture')
    server.update(status='disconnected', catalogState='stale')
    reconnecting, proceed = asyncio.Event(), asyncio.Event()
    original = service.smart_tools.connect
    async def delayed(*args, **kwargs):
        reconnecting.set()
        await proceed.wait()
        return await original(*args, **kwargs)
    service.smart_tools.connect = delayed
    with service.clients.bind('one'):
        status = inspect(service, identity, exact_target(one))
    payload = {'id': 'reconnect-operation', 'action': 'smartTools.reconnectView',
               'args': {**exact_target(one), 'canvasId': identity, 'expectedBindingRevision': status['bindingRevision']}}
    response = await client.post('/api/actions', params={'clientId': 'one'}, json=payload)
    assert response.status == 200
    await asyncio.wait_for(reconnecting.wait(), 2)
    path = '/api/smart-tools/operations/' + payload['id']
    progress = await client.get(path, params=query(one))
    assert progress.status == 200
    progress = await progress.json()
    assert progress['status'] == 'running' and 'result' not in progress
    assert (await client.get(path, params=query(two))).status == 409
    proceed.set()
    await service.wait_smart_tool(payload['id'])
    result = await client.get(path, params=query(one))
    assert result.status == 200 and (await result.json())['result']['status'] == 'ready'
    assert (await client.get(path, params=query(two))).status == 409
    assert connection.requests == []  # No original tool replay or duplicate launch.


async def test_legacy_primary_transient_context_does_not_override_canonical_or_inline_binding(view_http):
    _, service, identity, one, _, connection = view_http
    with service.clients.bind('one'):
        await service.dispatch('canvas.select', {'id': identity})
        await service.dispatch('smartTools.context', {**exact_target(one), 'canvasId': identity, 'context': {'inline': 'private'}})
        await service.dispatch('smartTools.context', {'canvasId': identity, 'context': {'primary': 'legacy'}})
        receipt = await service.dispatch('smartTools.appCall', {'canvasId': identity, 'name': 'change', 'arguments': {'amount': 9}},
                                         command_id='legacy-primary-operation')
        await service.wait_smart_tool(receipt['operationId'])
        primary = service.canvas_views.canvas('primary')
        assert primary['mcp']['context'] == {'primary': 'legacy'}
        assert primary['mcp']['lastOperationId'] == 'legacy-primary-operation'
        assert service.canvas_views.canvas(one['viewId'])['mcp']['context'] == {'inline': 'private'}
        assert 'lastOperationId' not in service.canvas_views.canvas(one['viewId'])['mcp']
        canonical = deepcopy(primary['mcp'])
        service.state['canvas']['mcp']['configuration'] = 'stale-primary-authority'
        assert service.canvas_views.canvas('primary')['mcp']['configuration'] == canonical['configuration']
        service.state['canvas']['mcp']['configuration'] = canonical['configuration']
        await service.dispatch('canvas.select', {'id': identity, 'version': 1})
        historical = service.canvas_views.canvas('primary')
        assert historical['mcp'].get('context', {}) != {'primary': 'legacy'}
        assert 'lastOperationId' not in historical['mcp']
    assert connection.requests == [('call_tool', ('change', {'amount': 9}))]
