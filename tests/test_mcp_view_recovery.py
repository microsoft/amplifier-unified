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
