"""Exact reviewed live authority never implies a new tool grant or replay."""
from copy import deepcopy

import pytest

from amplifier_web.mcp_contract_review import review, apply
from amplifier_web.mcp_view_recovery import inspect, source
from amplifier_web.service import AppError
from amplifier_web.state_storage import resource
from test_mcp_view_recovery import app, HTML, TOOLS


def args(app):
    canvas = app.state['canvas']
    return {'canvasId': canvas['id'], 'expectedBindingRevision': inspect(app, canvas['id'])['bindingRevision']}


def newer(app, *, html='<h1>Updated same board view</h1>', csp=None, permissions=None):
    async def read_app(*unused):
        return {'html': html, 'tools': ['inspect', 'change', 'added'],
                'csp': csp or {}, 'permissions': permissions or {}}
    app.smart_tools.read_app = read_app
    return html


async def test_review_compares_only_bounded_contract_without_calls_or_new_grants(app):
    canvas = app.state['canvas']; identity = canvas['id']
    original = deepcopy(canvas); saved_row = deepcopy(app.state['canvasArtifacts'][0])
    newer(app, csp={'connectDomains': ['https://example.test']}, permissions={'camera': {}})
    tools = app.smart_tools.schemas['fixture']
    tools[1]['inputSchema']['properties']['amount']['type'] = 'string'
    tools[0]['description'] = 'A new description'
    tools.append({'name': 'added', 'inputSchema': {}, 'annotations': {'readOnlyHint': True}})
    before = app.db.execute('select count(*) from smart_tool_operations').fetchone()[0]
    inspected = await review(app, args(app))
    changes = inspected['changes']
    assert changes['previousDefinitionsAvailable']
    assert changes['added'] == ['added'] and changes['newToolsGranted'] == []
    assert {'name': 'change', 'fields': ['inputs']} in changes['changed']
    assert changes['launcherChanged'] == ['description']
    assert changes['requestedCspChanged'] and changes['requestedPermissionsChanged']
    assert changes['permissionsGranted'] is False
    assert app.state['canvas'] == original and app.state['canvasArtifacts'][0] == saved_row
    assert app.db.execute('select count(*) from smart_tool_operations').fetchone()[0] == before
    result = await apply(app, {**args(app), 'reviewedContract': inspected['fingerprint']})
    assert result['status'] == 'ready' and not result['documentUpdated']
    assert canvas['mcp']['allowedTools'] == ['change', 'inspect']
    assert source(app, identity) == HTML
    assert canvas['mcp']['requestedCsp'] == {} and canvas['mcp']['requestedPermissions'] == {}
    assert inspect(app, identity)['status'] == 'ready'
    assert app.db.execute('select count(*) from smart_tool_operations').fetchone()[0] == before


async def test_legacy_review_does_not_invent_old_definitions(app):
    app.state['canvas']['mcp'].pop('contractSnapshot')
    value = await review(app, args(app))
    assert value['changes']['previousDefinitionsAvailable'] is False
    assert value['changes']['changed'] == []
    assert value['changes']['tools']


async def test_new_document_keeps_domain_identity_history_and_other_client_view(app):
    identity = app.state['canvas']['id']
    app.clients.attach('one'); app.clients.attach('two')
    with app.clients.bind('two'):
        app.state['canvas']['view'] = {'kept': 'local presentation'}
        other = app.state['canvas']
        old_source = source(app, identity)
        old_view = app.canvas_views.summary('primary')
    with app.clients.bind('one'):
        original = deepcopy(app.state['canvas']['mcp'])
        original_call = deepcopy(app.smart_tools.operation('original-call'))
        html = newer(app)
        value = await review(app, args(app))
        request = {**args(app), 'reviewedContract': value['fingerprint'], 'replaceDocument': True}
        with pytest.raises(AppError, match='unfinished input'):
            await apply(app, request)
        result = await apply(app, {**request, 'discardLocalInput': True})
        assert result['revision'] == 2 and result['canvasId'] == identity
        assert source(app, identity) == html
        binding = app.state['canvas']['mcp']
        for key in ('savedResult', 'operationId', 'toolArguments', 'resourceUri', 'serverId', 'context'):
            assert binding.get(key) == original.get(key)
        row = app.state['canvasArtifacts'][0]
        assert len(app.state['canvasArtifacts']) == 1 and len(row['versions']) == 2
        assert resource(app.db, row['versions'][0]['body']['$resource'])['content'] == old_source
        assert app.smart_tools.operation('original-call') == original_call
    with app.clients.bind('two'):
        assert app.state['canvas'] is other
        assert other['view'] == {'kept': 'local presentation'}
        assert other['selectedVersion'] == 1 and other['readOnlyVersion'] is True
        assert source(app, identity) == HTML
        pinned_view = app.canvas_views.summary('primary')
        assert pinned_view['renderIdentity'] == old_view['renderIdentity']
        assert pinned_view['resourceRevision'] != old_view['resourceRevision']
        assert pinned_view['generation'] > old_view['generation']
        with pytest.raises(AppError, match='changed'):
            app.canvas_views.target(old_view)
        with pytest.raises(AppError, match='read-only'):
            app.smart_canvas.binding(identity)
        await app.dispatch('canvas.select', {'id': identity})
        assert app.canvas_views.summary('primary')['renderIdentity'] != old_view['renderIdentity']


async def test_removed_or_hidden_grants_are_dropped_and_additions_stay_ungranted(app):
    tools = app.smart_tools.schemas['fixture']
    tools[1]['_meta'] = {'ui': {'visibility': ['model']}}
    tools.append({'name': 'new_write', 'inputSchema': {}})
    value = await review(app, args(app))
    assert value['changes']['removed'] == ['change']
    await apply(app, {**args(app), 'reviewedContract': value['fingerprint']})
    assert app.state['canvas']['mcp']['allowedTools'] == ['inspect']


@pytest.mark.parametrize('drift', ['schema', 'html', 'csp', 'permissions', 'catalog', 'configuration', 'account'])
async def test_review_revalidated_before_any_change(app, drift):
    value = await review(app, args(app)); request = {**args(app), 'reviewedContract': value['fingerprint']}
    before = deepcopy(app.state['canvas']); row = deepcopy(app.state['canvasArtifacts'][0])
    if drift == 'schema': app.smart_tools.schemas['fixture'][1]['description'] = 'changed later'
    elif drift == 'html': newer(app)
    elif drift == 'csp': newer(app, html=HTML, csp={'connectDomains': ['https://example.test']})
    elif drift == 'permissions': newer(app, html=HTML, permissions={'camera': {}})
    elif drift == 'catalog': app.state['smartTools']['servers'][0]['catalogRevision'] = 'later'
    elif drift == 'configuration': app.state['smartTools']['servers'][0]['command'] = 'different'
    else: app.state['smartTools']['servers'][0]['accountBinding'] = {'issuer': 'new', 'subject': 'new'}
    with pytest.raises(AppError): await apply(app, request)
    assert app.state['canvas'] == before and app.state['canvasArtifacts'][0] == row


async def test_historical_and_agent_approval_are_denied(app):
    value = await review(app, args(app)); request = {**args(app), 'reviewedContract': value['fingerprint']}
    with pytest.raises(AppError, match='person'):
        await app.dispatch('smartTools.updateView', request, origin='agent', caller_session_id=app._session()['id'])
    await app.dispatch('canvas.select', {'id': app.state['canvas']['id'], 'version': 1})
    with pytest.raises(AppError, match='read-only'):
        await review(app, args(app))


async def test_navigation_during_resource_read_does_not_apply_to_another_view(app):
    original = deepcopy(app.state['canvasArtifacts'][0])
    async def read_app(*unused):
        await app.dispatch('session.create', {})
        return {'html': '<h1>Wrong destination</h1>'}
    app.smart_tools.read_app = read_app
    with pytest.raises(AppError): await review(app, args(app))
    assert app.state['canvasArtifacts'][0] == original


async def test_catalog_revision_and_ungranted_addition_are_not_contract_changes(app):
    before = inspect(app, app.state['canvas']['id'])
    app.smart_tools.schemas['fixture'].append({'name': 'add_only', 'inputSchema': {}})
    app.state['smartTools']['servers'][0]['catalogRevision'] = 'rediscovered'
    after = inspect(app, app.state['canvas']['id'])
    assert before['status'] == 'ready' and after['status'] == 'reconnect_required'
    assert (await review(app, args(app)))['changes']['changed'] == []


@pytest.mark.parametrize('snapshot', [[], {'version': 1, 'tools': 'invalid'}, {'version': 1, 'tools': [{}], 'catalogNames': []}])
async def test_malformed_legacy_snapshot_has_honest_unknown_comparison(app, snapshot):
    from amplifier_web.resource_files import put
    app.state['canvas']['mcp']['contractSnapshot'] = put(app.db, snapshot)
    assert not (await review(app, args(app)))['changes']['previousDefinitionsAvailable']


@pytest.mark.parametrize('action', ['smartTools.reviewView', 'smartTools.updateView'])
async def test_restricted_device_cannot_review_or_approve_live_contract(aiohttp_client, tmp_path, action):
    from test_companion_authority import fixture, attach
    _, client, headers, _ = await fixture(aiohttp_client, tmp_path)
    assert (await attach(client, headers)).status == 200
    response = await client.post('/api/actions', headers={**headers, 'X-Amplifier-Client': 'native-review'},
        json={'id': 'restricted-contract-review', 'action': action, 'args': {
            'canvasId': 'unavailable', 'expectedBindingRevision': 'synthetic',
            **({'reviewedContract': 'synthetic'} if action.endswith('updateView') else {})}})
    assert response.status == 403


async def test_contract_only_review_preserves_other_client_private_binding(app):
    app.clients.attach('one'); app.clients.attach('two')
    with app.clients.bind('two'):
        other = app.state['canvas']['mcp']
        other['context'] = {'local': 'second-client-only'}
        other['lastOperationId'] = 'private-view-receipt'
    with app.clients.bind('one'):
        app.smart_tools.schemas['fixture'][1]['description'] = 'Updated description'
        value = await review(app, args(app))
        await apply(app, {**args(app), 'reviewedContract': value['fingerprint']})
    assert other['context'] == {'local': 'second-client-only'}
    assert other['lastOperationId'] == 'private-view-receipt'
    assert other['contractFingerprint'] == app.clients.records['one']['canvas']['mcp']['contractFingerprint']


async def test_review_is_bound_to_client_and_selection_generation(app):
    app.clients.attach('one'); app.clients.attach('two')
    with app.clients.bind('one'):
        value = await review(app, args(app))
        request = {**args(app), 'reviewedContract': value['fingerprint']}
        source_session = app.state['selectedSessionId']
    with app.clients.bind('two'):
        with pytest.raises(AppError, match='Review the changes again'):
            await apply(app, request)
    with app.clients.bind('one'):
        await app.dispatch('session.create', {})
        await app.dispatch('session.select', {'id': source_session})
        await app.dispatch('canvas.select', {'id': request['canvasId']})
        with pytest.raises(AppError, match='Review the changes again'):
            await apply(app, request)
