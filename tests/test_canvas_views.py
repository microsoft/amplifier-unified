from copy import deepcopy
from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.canvas_views import PROFILE


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    await service.dispatch('session.create', {})
    service.clients.attach('one')
    service.clients.attach('two')
    yield service
    await service.close()


async def command(app, name, args=None, client='one', **kwargs):
    with app.clients.bind(client):
        return await app.dispatch(name, args or {}, **kwargs)


def view(app, identity='primary', client='one'):
    with app.clients.bind(client):
        return deepcopy(app.canvas_views.summary(identity))


def target(value):
    return {key: value[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}


async def show(app, kind='json', content='[{"name":"alpha"},{"name":"beta"}]'):
    await command(app, 'canvas.show', {'kind': kind, 'content': content})
    return view(app)


async def update(app, current, patch, **kwargs):
    return await command(app, 'canvas.views.command', {**target(current), 'action': 'canvas.view', 'args': {'patch': patch}}, **kwargs)


async def renderer(app, **extra):
    manifest = {'id': 'example.reader', 'label': 'Example reader', 'version': '1.0.0', 'apiVersion': '1.0',
                'profile': PROFILE, 'stateSchema': 'canvas-view-v1', 'resourceKinds': ['json', 'markdown'],
                'capabilities': ['canvas.resource.read', 'canvas.view.update']}
    manifest.update(extra)
    staged = await command(app, 'shell.packages.stage', {'manifest': manifest, 'source': 'export default ({React})=>()=>React.createElement("p",null,"Reader")'})
    digest = staged['result']['digest']
    return digest


def validate_fixture(app, digest):
    record = app.shell.get('package', digest)
    record['validation'] = {'status': 'passed', 'hostFingerprint': app.shell.host_fingerprint()}
    app.shell.put('package', digest, record)


async def test_two_views_keep_separate_preferences_without_selecting_another_chat(app):
    first = await show(app)
    selected = app.clients.records['one']['selectedSessionId']
    await command(app, 'canvas.views.open', {'resourceId': first['resourceId'], 'sessionId': selected})
    second = view(app, 'secondary')
    await update(app, first, {'query': 'alpha'})
    await update(app, second, {'query': 'beta'})
    assert view(app)['view']['query'] == 'alpha'
    assert view(app, 'secondary')['view']['query'] == 'beta'
    assert app.clients.records['one']['selectedSessionId'] == selected
    assert app.clients.records['two']['canvas'].get('kind') is None
    replacement = await show(app, 'markdown', '# Another artifact')
    assert replacement['resourceId'] != first['resourceId']
    assert view(app, 'secondary')['resourceId'] == first['resourceId']
    assert view(app, 'secondary')['view']['query'] == 'beta'
    with pytest.raises(AppError, match='changed'):
        await update(app, first, {'query': 'stale'})


async def test_renderer_must_be_validated_compatible_and_in_its_own_slot(app):
    current = await show(app)
    digest = await renderer(app)
    with pytest.raises(AppError, match='validation'):
        await command(app, 'canvas.views.renderer', {**target(current), 'renderer': digest})
    validate_fixture(app, digest)
    await command(app, 'canvas.views.renderer', {**target(current), 'renderer': digest})
    selected = view(app)
    assert selected['renderer'] == digest
    assert selected['resourceRevision'] == current['resourceRevision']
    with pytest.raises(AppError, match='changed'):
        await update(app, current, {'query': 'late old renderer'})
    await update(app, selected, {'customSize': 20})
    assert view(app)['view']['customSize'] == 20
    bad = await renderer(app, id='example.image', resourceKinds=['image'])
    validate_fixture(app, bad)
    with pytest.raises(AppError, match='format'):
        await command(app, 'canvas.views.renderer', {**target(view(app)), 'renderer': bad})
    with pytest.raises(AppError, match='navigation package'):
        await command(app, 'shell.changes.prepare', {'clientId': 'one', 'expectedRevision': 0,
            'composition': {'instances': [{'id': 'wrong-slot', 'package': digest, 'slot': 'navigation'}], 'presentation': {}}})


async def test_dirty_and_incompatible_replacements_defer_and_recovery_retains_artifact(app):
    current = await show(app)
    await command(app, 'canvas.views.dirty', {**target(current), 'dirty': True})
    deferred = await command(app, 'canvas.views.renderer', {**target(current), 'renderer': current['renderer']})
    assert deferred['result']['status'] == 'deferred'
    await command(app, 'canvas.views.dirty', {**target(current), 'dirty': False})
    incompatible = await renderer(app, stateSchema='another-shape')
    validate_fixture(app, incompatible)
    result = await command(app, 'canvas.views.renderer', {**target(current), 'renderer': incompatible})
    assert result['result']['status'] == 'deferred'
    await command(app, 'canvas.views.recover', target(current))
    assert view(app)['resourceId'] == current['resourceId']
    assert view(app)['renderer'] == current['renderer']
    assert app.state['canvasArtifacts'][0]['id'] == current['resourceId']


async def test_missing_package_preserves_choice_resource_and_allows_default_recovery(app):
    current = await show(app)
    digest = await renderer(app)
    validate_fixture(app, digest)
    await command(app, 'canvas.views.renderer', {**target(current), 'renderer': digest})
    (app.shell.directory / (digest + '.mjs')).unlink()
    missing = view(app)
    assert not missing['available'] and missing['renderer'] == digest
    assert missing['resourceId'] == current['resourceId']
    await command(app, 'canvas.views.recover', target(missing))
    assert view(app)['available']


async def test_closed_reopened_view_rejects_old_actions_and_does_not_delete_content(app):
    primary = await show(app)
    sid = primary['resource']['sessionId']
    await command(app, 'canvas.views.open', {'resourceId': primary['resourceId'], 'sessionId': sid})
    old = view(app, 'secondary')
    await update(app, old, {'query': 'saved filter'})
    await command(app, 'canvas.views.close', target(old))
    await command(app, 'canvas.views.open', {'resourceId': primary['resourceId'], 'sessionId': sid})
    assert view(app, 'secondary')['view']['query'] == 'saved filter'
    with pytest.raises(AppError, match='changed'):
        await update(app, old, {'query': 'delayed'})
    assert len(app.state['canvasArtifacts']) == 1


async def test_resource_revision_rejects_stale_actions(app):
    primary = await show(app)
    await command(app, 'canvas.views.open', {'resourceId': primary['resourceId'], 'sessionId': primary['resource']['sessionId']})
    old = view(app, 'secondary')
    from amplifier_web.resource_files import put
    await show(app, 'text', 'A different primary artifact')
    row = app.canvas_views.artifact(primary['resourceId'])
    row['body'] = put(app.db, {'content': '[{"name":"new revision"}]'})
    assert view(app, 'secondary')['resourceRevision'] != old['resourceRevision']
    with pytest.raises(AppError, match='changed'):
        await update(app, old, {'query': 'old content'})


async def test_view_preferences_survive_restart_and_cloned_client_does_not_share_them(app):
    current = await show(app)
    await command(app, 'canvas.views.open', {'resourceId': current['resourceId'], 'sessionId': current['resource']['sessionId']})
    await update(app, view(app, 'secondary'), {'query': 'kept'})
    restored = AppService(app.data_dir, workspace=app.default_workspace)
    try:
        assert view(restored, 'secondary')['view']['query'] == 'kept'
        restored.clients.attach('clone', 'one')
        await update(restored, view(restored, 'secondary', 'clone'), {'query': 'clone only'}, client='clone')
        assert view(restored, 'secondary')['view']['query'] == 'kept'
    finally:
        await restored.close()


async def test_renderer_capabilities_cannot_dispatch_unrelated_operations_or_retarget(app):
    current = await show(app)
    with pytest.raises(AppError, match='capability'):
        await command(app, 'canvas.views.command', {**target(current), 'action': 'session.create', 'args': {}})
    with pytest.raises(AppError, match='retarget'):
        await command(app, 'canvas.views.command', {**target(current), 'action': 'canvas.view', 'args': {'id': 'other', 'patch': {}}})
    digest = await renderer(app, capabilities=['canvas.resource.read'])
    validate_fixture(app, digest)
    await command(app, 'canvas.views.renderer', {**target(current), 'renderer': digest})
    with pytest.raises(AppError, match='capability'):
        await update(app, view(app), {'customSize': 40})


async def test_returning_to_primary_artifact_invalidates_earlier_mount(app):
    old = await show(app)
    await show(app, 'text', 'Other artifact')
    await command(app, 'canvas.select', {'id': old['resourceId']})
    assert view(app)['generation'] > old['generation']
    with pytest.raises(AppError, match='changed'):
        await update(app, old, {'query': 'late from previous mount'})


async def test_failed_renderer_replacement_clears_activation_and_limits_total_state(app):
    current = await show(app)
    await command(app, 'canvas.views.status', {**target(current), 'status': 'error', 'message': 'Old failure'})
    digest = await renderer(app)
    validate_fixture(app, digest)
    await command(app, 'canvas.views.renderer', {**target(current), 'renderer': digest})
    assert view(app)['activation'] is None
    current = view(app)
    await update(app, current, {'first': 'x' * 9000})
    with pytest.raises(AppError, match='Accumulated'):
        await update(app, current, {'second': 'x' * 9000})
    assert 'second' not in view(app)['view']


async def test_missing_secondary_can_close_or_be_replaced_without_losing_other_artifacts(app):
    first = await show(app)
    await command(app, 'canvas.views.open', {'resourceId': first['resourceId'], 'sessionId': first['resource']['sessionId']})
    second = await show(app, 'text', 'Still available')
    app.state['canvasArtifacts'] = [row for row in app.state['canvasArtifacts'] if row['id'] != first['resourceId']]
    with app.clients.bind('one'):
        missing = app.canvas_views.project()['views'][1]
    assert missing['error']
    await command(app, 'canvas.views.close', target(missing))
    await command(app, 'canvas.views.open', {'resourceId': second['resourceId'], 'sessionId': second['resource']['sessionId']})
    assert view(app, 'secondary')['resourceId'] == second['resourceId']


async def test_two_html_views_control_only_the_addressed_document(app):
    first = await show(app, 'html', '<input value="initial">')
    await command(app, 'canvas.views.open', {'resourceId': first['resourceId'], 'sessionId': first['resource']['sessionId']})
    second = view(app, 'secondary')
    document = {'text': '', 'controls': [{'id': 'field', 'tag': 'input', 'type': 'text', 'label': 'Note', 'value': 'initial', 'disabled': False}]}
    for current in (first, second):
        await command(app, 'canvas.views.command', {**target(current), 'action': 'canvas.snapshot', 'args': {'document': document}})
    await command(app, 'canvas.views.command', {**target(second), 'action': 'canvas.interact', 'args': {'controlId': 'field', 'event': 'input', 'value': 'Secondary only'}})
    with app.clients.bind('one'):
        assert app.canvas_views.canvas('secondary')['interaction']['value'] == 'Secondary only'
        assert 'interaction' not in app.canvas_views.canvas('primary')


async def test_large_resource_is_loaded_only_on_explicit_source_read(app):
    path = Path(app.default_workspace) / 'large.html'
    path.write_text('<p>' + 'large ' * 180000 + '</p>')
    await command(app, 'canvas.show', {'kind': 'html', 'path': str(path)})
    current = view(app)
    await command(app, 'canvas.views.open', {'resourceId': current['resourceId'], 'sessionId': current['resource']['sessionId']})
    from amplifier_web.canvas_documents import raw_source
    with app.clients.bind('one'):
        canvas = app.canvas_views.canvas('secondary')
        assert 'content' not in canvas
        assert len(raw_source(canvas, app.db)) > 1_000_000
        result, effects = app.canvas_views.command('canvas.views.command', {**target(view(app, 'secondary')), 'action': 'canvas.copy', 'args': {}}, 'browser')
        assert effects[0]['type'] == 'clipboard.url'


async def test_babylon_export_retains_standalone_document_behavior(app):
    current = await show(app, 'babylon', '<script>const scene = true;</script>')
    response = await command(app, 'canvas.views.command', {**target(current), 'action': 'canvas.download', 'args': {}})
    assert response['effects'][0]['type'] == 'download.url'
    assert response['effects'][0]['filename'] == 'canvas-3d.html'


async def test_view_mutation_retry_does_not_replace_twice(app):
    current = await show(app)
    args = {**target(current), 'renderer': current['renderer']}
    first = await command(app, 'canvas.views.renderer', args, command_id='renderer-retry')
    generation = view(app)['generation']
    retry = await command(app, 'canvas.views.renderer', args, command_id='renderer-retry')
    assert first['result'] == retry['result']
    assert view(app)['generation'] == generation


async def test_reopened_canvas_invalidates_both_previous_mounts(app):
    current = await show(app)
    await command(app, 'canvas.views.open', {'resourceId': current['resourceId'], 'sessionId': current['resource']['sessionId']})
    previous = [view(app, identity) for identity in ('primary', 'secondary')]
    await command(app, 'canvas.close')
    await command(app, 'canvas.reopen')
    for old in previous:
        with pytest.raises(AppError, match='changed'):
            await update(app, old, {'query': 'from before the close'})


async def test_agent_bridge_can_explicitly_address_a_view_without_retargeting_another_client(app):
    current = await show(app)
    sid = current['resource']['sessionId']
    observed = await app.app_bridge('dispatch', {'action': 'canvas.views.inspect', 'args': {'clientId': 'one'}}, sid)
    assert observed['result']['views'][0]['resourceId'] == current['resourceId']
    await app.app_bridge('dispatch', {'action': 'canvas.views.command', 'args': {'clientId': 'one', **target(current), 'action': 'canvas.view', 'args': {'patch': {'query': 'agent choice'}}}}, sid)
    assert view(app)['view']['query'] == 'agent choice'
    assert app.clients.records['two']['canvas'].get('kind') is None
    with pytest.raises(AppError, match='different client'):
        await command(app, 'canvas.views.inspect', {'clientId': 'one'}, client='two')
    with pytest.raises(AppError, match='Attach this client'):
        await app.dispatch('canvas.views.inspect', {'clientId': 'missing'})


async def test_bounded_choice_menu_keeps_an_older_selected_renderer(app):
    current = await show(app)
    selected = await renderer(app)
    validate_fixture(app, selected)
    await command(app, 'canvas.views.renderer', {**target(current), 'renderer': selected})
    for number in range(51):
        digest = await renderer(app, id=f'example.newer-{number}')
        validate_fixture(app, digest)
    current = view(app)
    assert current['available']
    assert current['renderer'] == selected
    assert selected in {choice['id'] for choice in current['choices']}
    assert len(current['choices']) == 51


async def test_agent_download_reaches_only_its_bound_browser_and_diagnostics_follow_artifact(app, monkeypatch):
    current = await show(app, 'text', 'Source content')
    await command(app, 'canvas.views.open', {'resourceId': current['resourceId'], 'sessionId': current['resource']['sessionId']})
    await command(app, 'session.create')
    selected = app.clients.records['one']['selectedSessionId']
    assert selected != current['resource']['sessionId']
    recorded = []
    monkeypatch.setattr(app.diagnostics, 'record', lambda stream, data, **scope: recorded.append((stream, data, scope)))
    await app.app_bridge('dispatch', {'action': 'canvas.views.command', 'args': {'clientId': 'one', **target(view(app, 'secondary')), 'action': 'canvas.download', 'args': {}}}, selected)
    effect = app.clients.records['one']['deviceCommands'][-1]
    assert effect['type'] == 'download' and effect['content'] == 'Source content'
    assert not app.clients.records['two']['deviceCommands']
    assert recorded[-1][1]['data']['sessionId'] == current['resource']['sessionId']
    assert recorded[-1][1]['data']['artifactId'] == current['resourceId']
    assert app.clients.records['one']['selectedSessionId'] == selected
