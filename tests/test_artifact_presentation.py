"""Exact placement regressions. Source fixtures are not rendering acceptance."""
import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest

from amplifier_web.service import ACTION_DEFINITIONS, AppError, AppService
from test_generated_images import receipt
from test_output_images import png
from test_output_media import gif, gif_open_args, media_app, media_args, mp4
from test_service import Runtime


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', workspace=tmp_path, runtime=Runtime())
    await service.dispatch('session.create', {'title': 'Synthetic artifact source'})
    session = service._session()
    service._message(session, 'user', 'Original artifact request')
    service._message(session, 'assistant', 'Original artifact answer', 'text')
    session['collaborationGeneration'] = {'id': 'artifact-generation'}
    service.clients.attach('one')
    service.clients.attach('two')
    for identity in ('one', 'two'):
        with service.clients.bind(identity):
            service.subscribe()
    try:
        yield service
    finally:
        for queue in list(service.queues):
            service.unsubscribe(queue)
        await service.close()


def owner(app):
    return app._session(app._state['selectedSessionId'])


def artifact(app, identity):
    return next(row for row in app._state['canvasArtifacts'] if row['id'] == identity)


def assert_client_preserved(before, after):
    """Passive catalog/cache initialization is not a client presentation edit."""
    for key in ('selectedSessionId', 'selectedWorkspaceId', 'canvas', 'view', 'drafts', 'attachments'):
        assert after.get(key) == before.get(key)
    for identity, tab in before.get('canvasTabs', {}).items():
        assert after['canvasTabs'][identity] == tab
    for identity, tab in after.get('canvasTabs', {}).items():
        if identity not in before.get('canvasTabs', {}):
            assert not tab.get('tabOpen'), 'General publication opened a peer tab'
    for key, value in before.get('canvasViews', {}).get('preferences', {}).items():
        assert after['canvasViews']['preferences'][key] == value


async def call(app, action, args, *, actor='ui', client='one', command_id=None, input_clients=None):
    if actor == 'agent':
        session = owner(app)
        envelope = {'action': action, 'args': args, 'id': command_id,
                    '_runtimeSessionId': session.get('runtimeSessionId') or session['id'],
                    '_generationId': 'artifact-generation'}
        if input_clients is not None:
            envelope['_inputClients'] = input_clients
        return await app.app_bridge('dispatch', envelope, session['id'])
    with app.clients.bind(client):
        return await app.dispatch(action, args, command_id=command_id, include_state=False)


async def save(app, *, chat='compact', canvas='unchanged', actor='ui', content='Exact content'):
    session = owner(app)
    args = {'sessionId': session['id'], 'kind': 'markdown', 'title': 'Synthetic saved document',
            'content': content, 'messageId': session['messages'][0]['id'],
            'presentation': {'chat': chat, 'canvas': canvas}}
    return (await call(app, 'canvas.show', args, actor=actor,
                       input_clients=['one'] if actor == 'agent' else None))['result']


@pytest.mark.parametrize('actor', ['ui', 'agent'])
@pytest.mark.parametrize('chat,canvas', [
    ('compact', 'unchanged'), ('inline', 'unchanged'), ('none', 'open'),
    ('compact', 'open'), ('inline', 'open'),
])
async def test_five_modes_share_actual_host_operation_and_leave_peer_unchanged(app, actor, chat, canvas):
    peer = copy.deepcopy(app.clients.records['two'])
    session = owner(app)
    history = copy.deepcopy(session['messages'])
    saved = await save(app, chat=chat, canvas=canvas, actor=actor)
    assert saved['publication'] == {
        'status': 'published', 'accepted': True, 'chat': chat,
        'messageId': history[0]['id'], 'version': 1}
    assert saved['canvas']['status'] == ('opened' if canvas == 'open' else 'not-requested')
    row = artifact(app, saved['id'])
    assert row['revision'] == 1 and len(row['versions']) == 1
    assert row['publications'] == [{'messageId': history[0]['id'], 'version': 1, 'chat': chat}]
    assert saved['reference'].endswith('version=1')
    if canvas == 'open':
        assert saved['canvas']['clientId'] == 'one'
        assert app.clients.records['one']['canvas']['id'] == row['id']
        assert app.clients.records['one']['view']['canvasFocused'] is False
    assert_client_preserved(peer, app.clients.records['two'])
    assert owner(app)['messages'] == history and not app.runtime.sent
    args = {'sessionId': session['id'], 'id': row['id'], 'version': 1,
            'presentation': {'chat': chat, 'canvas': 'unchanged'}}
    repeated = (await call(app, 'canvas.present', args, actor=actor,
                           input_clients=['one'] if actor == 'agent' else None))['result']
    assert repeated['publication']['status'] == 'unchanged'
    assert len(row['publications']) == 1 and len(row['versions']) == 1


async def test_explicit_show_defaults_compact_saves_once_and_legacy_stays_compatible(app, monkeypatch):
    from amplifier_web import resource_files
    session = owner(app)
    body_saves = []
    real = resource_files.put

    def put(db, value):
        if value == {'content': 'One immutable body'}:
            body_saves.append(value)
        return real(db, value)

    monkeypatch.setattr(resource_files, 'put', put)
    result = (await call(app, 'canvas.show', {
        'kind': 'markdown', 'content': 'One immutable body',
        'messageId': session['messages'][0]['id']}))['result']
    assert body_saves == [{'content': 'One immutable body'}]
    assert result['publication']['chat'] == 'compact'
    assert result['canvas']['status'] == 'not-requested'
    legacy = (await call(app, 'canvas.show', {'kind': 'text', 'content': 'Legacy source'}))['result']
    assert 'publication' not in legacy and 'canvas' not in legacy
    assert app.clients.records['one']['canvas']['id'] == legacy['id']
    assert {'messageId': session['messages'][0]['id'], 'version': 1} in artifact(app, legacy['id'])['publications']
    none = (await call(app, 'canvas.show', {'kind': 'text', 'content': 'Library only',
        'presentation': {'chat': 'none', 'canvas': 'unchanged'}}))['result']
    assert none['publication']['messageId'] is None
    assert artifact(app, none['id'])['publications'] == []


@pytest.mark.parametrize('version', [True, False, '1', 1.0, 0, -1, 99])
async def test_present_requires_positive_integer_exact_history_version(app, version):
    saved = await save(app)
    before = copy.deepcopy(app._state['canvasArtifacts'])
    with pytest.raises(AppError):
        await call(app, 'canvas.present', {
            'sessionId': owner(app)['id'], 'id': saved['id'], 'version': version,
            'presentation': {'chat': 'compact', 'canvas': 'open'}})
    assert app._state['canvasArtifacts'] == before


@pytest.mark.parametrize('selector', [{}, {'id': 'one', 'outputId': 'two'}])
async def test_present_schema_requires_exactly_one_owner_selector(app, selector):
    with pytest.raises(AppError):
        await call(app, 'canvas.present', {'sessionId': owner(app)['id'], **selector, 'version': 1,
            'presentation': {'chat': 'none', 'canvas': 'unchanged'}})


@pytest.mark.parametrize('anchor', [None, 'missing', 'forged-observation'])
async def test_new_publication_has_no_newest_turn_or_observation_inference(app, anchor):
    session = owner(app)
    session['messages'].append({'id': 'forged-observation', 'role': 'assistant',
                                'text': session['messages'][1]['text'], 'observation': {'source': 'service'}})
    before = copy.deepcopy(app._state['canvasArtifacts'])
    args = {'kind': 'markdown', 'content': 'Never inferred', 'sessionId': session['id'],
            'presentation': {'chat': 'compact', 'canvas': 'unchanged'}}
    if anchor is not None:
        args['messageId'] = anchor
    with pytest.raises(AppError):
        await call(app, 'canvas.show', args)
    assert app._state['canvasArtifacts'] == before


@pytest.mark.parametrize('fence', ['worker', 'internal', 'read-only', 'yielded', 'transfer'])
async def test_publication_keeps_nonroot_and_writable_conversation_fences(app, monkeypatch, fence):
    session = owner(app)
    saved = await save(app)
    if fence in {'worker', 'internal'}:
        session['sessionKind'] = fence
    elif fence == 'read-only':
        session['historyReadOnlyReason'] = 'Synthetic read-only history'
    elif fence == 'yielded':
        session['ownership'] = {'status': 'yielded'}
    else:
        def reject(_):
            raise ValueError('Synthetic transfer fence')
        monkeypatch.setattr(app.portability, 'write_context', reject)
    before = copy.deepcopy(app._state['canvasArtifacts'])
    with pytest.raises(AppError):
        await call(app, 'canvas.present', {'sessionId': session['id'], 'id': saved['id'], 'version': 1,
            'presentation': {'chat': 'compact', 'canvas': 'open'}})
    assert app._state['canvasArtifacts'] == before


async def test_agent_requires_current_transport_generation_and_caller_not_payload_session(app):
    session = owner(app)
    saved = await save(app)
    args = {'sessionId': session['id'], 'id': saved['id'], 'version': 1,
            'presentation': {'chat': 'compact', 'canvas': 'open'}}
    with pytest.raises(AppError):
        await app.dispatch('canvas.present', args, origin='agent', include_state=False)
    with pytest.raises(AppError):
        await app.app_bridge('dispatch', {'action': 'canvas.present', 'args': args,
            '_runtimeSessionId': session['id'], '_generationId': 'stale'}, session['id'])
    foreign = app._new_session({'title': 'Synthetic foreign conversation'})
    app._state['sessions'].append(foreign)
    with pytest.raises(AppError):
        await call(app, 'canvas.present', {**args, 'sessionId': foreign['id']}, actor='agent')
    with pytest.raises(AppError):
        await call(app, 'canvas.present', {**args, 'sessionId': foreign['id']})


async def test_exact_command_retry_after_close_never_reopens_and_changed_payload_is_refused(app):
    saved = await save(app)
    args = {'sessionId': owner(app)['id'], 'id': saved['id'], 'version': 1,
            'presentation': {'chat': 'compact', 'canvas': 'open'}}
    first = await call(app, 'canvas.present', args, command_id='stable-present')
    await call(app, 'canvas.close', {})
    closed = copy.deepcopy(app.clients.records['one']['canvas'])
    replay = await call(app, 'canvas.present', args, command_id='stable-present')
    assert replay['duplicate'] and replay['result'] == first['result']
    assert app.clients.records['one']['canvas'] == closed and not closed['open']
    with pytest.raises(AppError, match='different contents'):
        await call(app, 'canvas.present', {**args, 'presentation': {'chat': 'inline', 'canvas': 'open'}},
                   command_id='stable-present')
    fresh = await call(app, 'canvas.present', args, command_id='fresh-explicit-open')
    assert fresh['result']['canvas']['status'] == 'opened'
    assert len(artifact(app, saved['id'])['versions']) == 1


@pytest.mark.parametrize('dirty', ['canvas', 'settings'])
async def test_dirty_open_is_partial_publication_with_existing_view_and_peer_preserved(app, dirty):
    initial = await save(app, canvas='open')
    client = app.clients.records['one']
    if dirty == 'canvas':
        with app.clients.bind('one'):
            view = app.canvas_views.summary('primary')
            await app.dispatch('canvas.views.dirty', {key: view[key] for key in
                ('viewId', 'resourceId', 'resourceRevision', 'generation')} | {'dirty': True}, include_state=False)
    else:
        client['view']['settingsCanvas'] = True
        app.shell.put('client', 'one', {'views': {'settings-editor': {'dirty': True}}})
    local, peer = copy.deepcopy(client), copy.deepcopy(app.clients.records['two'])
    result = await save(app, content='Accepted second artifact', canvas='open')
    assert result['publication']['accepted'] and result['publication']['status'] == 'published'
    assert result['canvas']['status'] == 'dirty-blocked'
    assert artifact(app, result['id'])['id'] != initial['id']
    assert client['canvas'] == local['canvas'] and client['view'] == local['view']
    assert_client_preserved(peer, app.clients.records['two'])


async def test_general_file_refinement_preserves_existing_dirty_content_fence(app, tmp_path):
    path = tmp_path / 'original.md'
    path.write_text('Original saved file')
    saved = (await call(app, 'canvas.show', {'kind': 'auto', 'path': str(path)}))['result']
    with app.clients.bind('two'):
        await app.dispatch('canvas.select', {'id': saved['id']}, include_state=False)
        target = app.canvas_views.summary('primary')
        await app.dispatch('canvas.views.dirty', {key: target[key] for key in
            ('viewId', 'resourceId', 'resourceRevision', 'generation')} | {'dirty': True}, include_state=False)
    before = copy.deepcopy(artifact(app, saved['id']))
    path.write_text('New bytes must not replace a peer edit')
    with pytest.raises(AppError, match='edit'):
        await call(app, 'canvas.show', {'kind': 'auto', 'path': str(path),
            'messageId': owner(app)['messages'][0]['id'],
            'presentation': {'chat': 'compact', 'canvas': 'unchanged'}})
    assert artifact(app, saved['id']) == before


@pytest.mark.parametrize('target', ['none', 'ambiguous', 'other-chat', 'other-client', 'incapable'])
async def test_no_client_open_keeps_valid_publication_with_explicit_reason(app, target):
    session = owner(app)
    args = {'sessionId': session['id'], 'kind': 'text', 'content': 'Publication independent of open',
            'messageId': session['messages'][0]['id'],
            'presentation': {'chat': 'compact', 'canvas': 'open'}}
    client = None
    if target == 'none':
        for queue in list(app.queues):
            app.unsubscribe(queue)
    elif target == 'other-chat':
        app.clients.records['one']['selectedSessionId'] = None
        args['clientId'] = 'one'
    elif target == 'other-client':
        client = 'one'
        args['clientId'] = 'two'
    elif target == 'incapable':
        client = 'one'
        app.clients.records['one']['kind'] = 'tui'
    result = (await call(app, 'canvas.show', args, client=client))['result']
    assert result['publication']['status'] == 'published'
    assert result['canvas']['status'] == 'no-client' and result['canvas']['reason']


async def test_agent_input_origin_preferred_and_mixed_origin_never_selects_arbitrary_peer(app):
    saved = await save(app)
    args = {'id': saved['id'], 'version': 1,
            'presentation': {'chat': 'compact', 'canvas': 'open'}}
    chosen = (await call(app, 'canvas.present', args, actor='agent', input_clients=['two']))['result']
    assert chosen['canvas'] == {'status': 'opened', 'clientId': 'two'}
    await call(app, 'canvas.close', {}, client='two')
    peer = copy.deepcopy(app.clients.records['two'])
    ambiguous = (await call(app, 'canvas.present', args, actor='agent', input_clients=['one', 'two']))['result']
    assert ambiguous['publication']['accepted'] and ambiguous['canvas']['status'] == 'no-client'
    assert_client_preserved(peer, app.clients.records['two'])
    app.clients.records['one']['selectedSessionId'] = None
    detached = (await call(app, 'canvas.present', args, actor='agent', input_clients=['one']))['result']
    assert detached['canvas']['status'] == 'no-client'  # No fallback to the other live peer.


@pytest.mark.parametrize('actor', ['ui', 'agent'])
async def test_historical_version_reuses_publication_without_history_or_source_file_reads(app, tmp_path, monkeypatch, actor):
    path = tmp_path / 'retained.md'
    path.write_text('Original exact content')
    first = (await call(app, 'canvas.show', {'kind': 'auto', 'path': str(path)}))['result']
    await call(app, 'canvas.versions.revise', {'id': first['id'], 'expectedRevision': 1,
                                           'content': 'Later content'})
    path.unlink()
    session = owner(app)
    session['messages'] = []
    args = {'sessionId': session['id'], 'id': first['id'], 'version': 1,
            'presentation': {'chat': 'compact', 'canvas': 'open'}}
    envelope = {'action': 'canvas.present', 'args': args, '_runtimeSessionId': session['id'],
                '_generationId': 'artifact-generation', '_inputClients': ['one']}
    real_session = app._session

    def no_body(sid=None, *, hydrate=True):
        assert hydrate is False, 'Retained presentation requested a message/history body'
        return real_session(sid, hydrate=False)

    monkeypatch.setattr(app, '_session', no_body)
    result = ((await app.app_bridge('dispatch', envelope, session['id'])) if actor == 'agent'
              else (await call(app, 'canvas.present', args)))['result']
    assert result['publication']['status'] == 'unchanged'
    assert app.clients.records['one']['canvas']['content'] == 'Original exact content'
    assert result['version'] == 1 and result['revision'] == 2
    with pytest.raises(AssertionError, match='message/history'):
        await call(app, 'canvas.present', {**args, 'messageId': 'new-anchor'})


async def test_output_adapter_has_one_body_mapping_and_separate_output_canvas_version_namespaces(app, monkeypatch):
    from amplifier_web import resource_files
    session = owner(app)
    first = (await call(app, 'outputs.write', {'sessionId': session['id'], 'title': 'First',
        'content': 'First writing bytes', 'variant': 'document'}))['result']
    second = (await call(app, 'outputs.write', {'sessionId': session['id'], 'title': 'Second',
        'content': 'Second writing bytes', 'variant': 'document', 'parentId': first['id'],
        'messageId': session['messages'][0]['id']}))['result']
    before = copy.deepcopy(app.state_resource(second['body']['$resource']))
    saved_bodies = []
    real_put = resource_files.put

    def put(db, value):
        saved_bodies.append(copy.deepcopy(value))
        return real_put(db, value)

    monkeypatch.setattr(resource_files, 'put', put)
    args = {'sessionId': session['id'], 'outputId': second['id'], 'version': 2,
            'messageId': session['messages'][0]['id'],
            'presentation': {'chat': 'compact', 'canvas': 'open'}}
    result = (await call(app, 'canvas.present', args))['result']
    assert result['outputVersion'] == 2 and result['version'] == 1
    row = artifact(app, result['id'])
    assert app.state_resource(row['body']['$resource']) == {'outputReference': row['outputBinding']}
    assert app.outputs.record(session['id'], second['id'])['body'] == second['body']
    assert before not in saved_bodies
    assert app.clients.records['one']['canvas']['content'] == 'Second writing bytes'
    again = (await call(app, 'canvas.present', args))['result']
    assert again['id'] == result['id'] and again['publication']['status'] == 'unchanged'
    assert row['revision'] == 1 and len(row['versions']) == 1
    await call(app, 'canvas.close', {})
    assert app.state_resource(row['body']['$resource']) == {'outputReference': row['outputBinding']}
    assert before not in saved_bodies  # Passive remember did not duplicate materialized bytes.
    with pytest.raises(AppError):
        await call(app, 'canvas.present', {**args, 'version': 1})


@pytest.mark.parametrize('filename,data,kind', [
    ('writing.md', b'# Saved writing', 'markdown'),
    ('dataset.json', b'{"exact":1}', 'json'),
    ('page.png', png(), 'image'), ('document.pdf', b'%PDF-1.7\nsynthetic fixture', 'pdf'),
    ('unsafe.html', b'<script>not admitted for execution</script>', 'output'),
    ('opaque.bin', b'\x00\xff', 'output'),
])
async def test_saved_file_native_admission_is_not_execution_by_filename(app, tmp_path, filename, data, kind):
    session = owner(app)
    path = tmp_path / filename
    path.write_bytes(data)
    output = (await call(app, 'outputs.attach', {'sessionId': session['id'], 'kind': 'file',
        'path': filename, 'title': 'Synthetic attachment', 'messageId': session['messages'][0]['id']}))['result']
    path.unlink()
    result = (await call(app, 'canvas.present', {'sessionId': session['id'], 'outputId': output['id'],
        'version': output['version'], 'messageId': session['messages'][0]['id'],
        'presentation': {'chat': 'compact', 'canvas': 'open'}}))['result']
    assert artifact(app, result['id'])['kind'] == kind
    assert result['canvas']['status'] == ('unavailable' if kind == 'output' else 'opened')
    assert app.outputs.content(app.outputs.record(session['id'], output['id'])) == data


async def test_external_output_is_inert_address_without_body_fetch(app):
    session = owner(app)
    saved = (await call(app, 'outputs.attach', {'sessionId': session['id'], 'kind': 'external_document',
        'title': 'Synthetic remote reference', 'url': 'https://example.invalid/document',
        'messageId': session['messages'][0]['id']}))['result']
    result = (await call(app, 'canvas.present', {'sessionId': session['id'], 'outputId': saved['id'],
        'version': 1, 'messageId': session['messages'][0]['id'],
        'presentation': {'chat': 'compact', 'canvas': 'open'}}))['result']
    row = artifact(app, result['id'])
    assert row['kind'] == 'output' and row['url'] == saved['url']
    assert result['canvas']['status'] == 'unavailable' and 'body' not in saved


async def test_nested_canvas_snapshot_reuses_original_source_body(app):
    session = owner(app)
    writing = (await call(app, 'outputs.write', {'sessionId': session['id'], 'title': 'Saved writing',
        'content': 'Exact original writing', 'variant': 'document',
        'messageId': session['messages'][0]['id']}))['result']
    placed = (await call(app, 'canvas.present', {'sessionId': session['id'], 'outputId': writing['id'],
        'version': 1, 'messageId': session['messages'][0]['id'],
        'presentation': {'chat': 'compact', 'canvas': 'unchanged'}}))['result']
    snapshot = (await call(app, 'outputs.attach', {'sessionId': session['id'], 'kind': 'canvas',
        'title': 'Snapshot of adapter', 'canvasId': placed['id'],
        'messageId': session['messages'][0]['id']}))['result']
    adapted = (await call(app, 'canvas.present', {'sessionId': session['id'], 'outputId': snapshot['id'],
        'version': 1, 'messageId': session['messages'][0]['id'],
        'presentation': {'chat': 'compact', 'canvas': 'open'}}))['result']
    assert artifact(app, adapted['id'])['body'] == snapshot['canvasSource'] == artifact(app, placed['id'])['body']
    assert app.clients.records['one']['canvas']['content'] == 'Exact original writing'


async def test_restore_output_backed_version_preserves_body_owner_and_exact_publication(app):
    session = owner(app)
    writing = (await call(app, 'outputs.write', {'sessionId': session['id'], 'title': 'Restore original',
        'content': 'Exact retained writing', 'variant': 'document',
        'messageId': session['messages'][0]['id']}))['result']
    placed = (await call(app, 'canvas.present', {'sessionId': session['id'], 'outputId': writing['id'],
        'version': 1, 'messageId': session['messages'][0]['id'],
        'presentation': {'chat': 'compact', 'canvas': 'open'}}))['result']
    row = artifact(app, placed['id'])
    body = copy.deepcopy(row['body'])
    await call(app, 'canvas.versions.revise', {'id': row['id'], 'expectedRevision': 1, 'content': 'Ordinary revision'})
    restored = (await call(app, 'canvas.versions.restore', {'id': row['id'], 'expectedRevision': 2,
                                                        'version': 1}))['result']
    assert restored['version'] == 3 and row['body'] == body
    assert row['versions'][0]['body'] == row['versions'][2]['body'] == body
    assert app.clients.records['one']['canvas']['content'] == 'Exact retained writing'
    assert row['versions'][2]['outputBinding'] == row['versions'][0]['outputBinding']
    restored_link = next(link for link in row['publications'] if link['version'] == 3)
    assert restored_link['outputId'] == writing['id']


async def test_nested_output_fork_remaps_both_bindings_and_never_leaks_future_or_source_ids(app):
    session = owner(app)
    mid = session['messages'][0]['id']
    writing = (await call(app, 'outputs.write', {'sessionId': session['id'], 'title': 'Fork original',
        'content': 'Exact original fork writing', 'variant': 'document', 'messageId': mid}))['result']
    placed = (await call(app, 'canvas.present', {'sessionId': session['id'], 'outputId': writing['id'],
        'version': 1, 'messageId': mid,
        'presentation': {'chat': 'compact', 'canvas': 'unchanged'}}))['result']
    snapshot = (await call(app, 'outputs.attach', {'sessionId': session['id'], 'kind': 'canvas',
        'title': 'Fork snapshot', 'canvasId': placed['id'], 'messageId': mid}))['result']
    adapted = (await call(app, 'canvas.present', {'sessionId': session['id'], 'outputId': snapshot['id'],
        'version': 1, 'messageId': mid,
        'presentation': {'chat': 'compact', 'canvas': 'unchanged'}}))['result']
    app._message(session, 'user', 'Future content must stay at source')
    future = (await call(app, 'outputs.write', {'sessionId': session['id'], 'title': 'Future output',
        'content': 'Never carried', 'variant': 'document', 'messageId': session['messages'][-1]['id']}))['result']
    await call(app, 'canvas.present', {'sessionId': session['id'], 'outputId': future['id'], 'version': 1,
        'messageId': session['messages'][-1]['id'],
        'presentation': {'chat': 'compact', 'canvas': 'unchanged'}})
    source_rows = copy.deepcopy([row for row in app._state['canvasArtifacts'] if row['sessionId'] == session['id']])
    await call(app, 'session.fork', {'id': session['id'], 'turn': 1})
    target_id = app.clients.records['one']['selectedSessionId']
    records = app.outputs.store.list(target_id)['items']
    assert {r['forkSource']['outputId'] for r in records} == {writing['id'], snapshot['id']}
    mapped = {r['forkSource']['outputId']: r for r in records}
    clones = [r for r in app._state['canvasArtifacts'] if r['sessionId'] == target_id]
    assert len(clones) == 2
    for original, output in ((placed, writing), (adapted, snapshot)):
        clone = next(row for row in clones if row['outputBinding']['outputId'] == mapped[output['id']]['id'])
        assert mapped[output['id']]['presentationArtifact']['id'] == clone['id']
        assert clone['id'] != original['id']
        assert clone['outputBinding']['sessionId'] == target_id
        assert clone['publications'][0]['outputId'] == mapped[output['id']]['id']
        for definition in [clone, *clone['versions']]:
            reference = app.state_resource(definition['body']['$resource'])['outputReference']
            assert reference['outputId'] == mapped[writing['id']]['id']  # Snapshot's nested owner is not itself.
            assert reference['sessionId'] == target_id
        with app.clients.bind('one'):
            opened = (await app.dispatch('canvas.present', {'sessionId': target_id, 'id': clone['id'], 'version': 1,
                'presentation': {'chat': 'compact', 'canvas': 'open'}}, include_state=False))['result']
            assert opened['canvas']['status'] == 'opened'
            assert app.clients.records['one']['canvas']['content'] == 'Exact original fork writing'
    assert source_rows == [row for row in app._state['canvasArtifacts'] if row['sessionId'] == session['id']]


async def test_unavailable_saved_source_does_not_claim_opened_or_rollback_publication(app, monkeypatch):
    saved = await save(app)
    target = artifact(app, saved['id'])
    current = copy.deepcopy(app.clients.records['one']['canvas'])
    from amplifier_web import canvas_library
    real = canvas_library.resource

    def missing(db, identity):
        if identity == target['body']['$resource']:
            raise ValueError('Synthetic missing saved body')
        return real(db, identity)

    monkeypatch.setattr(canvas_library, 'resource', missing)
    result = (await call(app, 'canvas.present', {'sessionId': owner(app)['id'], 'id': target['id'], 'version': 1,
        'presentation': {'chat': 'compact', 'canvas': 'open'}}))['result']
    assert result['publication']['accepted'] and result['publication']['status'] == 'unchanged'
    assert result['canvas']['status'] == 'unavailable'
    assert app.clients.records['one']['canvas'] == current


async def test_unlink_suppression_is_durable_and_exact_library_access_survives(app, tmp_path):
    session = owner(app)
    output = (await call(app, 'outputs.write', {'sessionId': session['id'], 'title': 'Saved',
        'content': 'Keep original bytes', 'variant': 'document',
        'messageId': session['messages'][0]['id']}))['result']
    saved = (await call(app, 'canvas.present', {'sessionId': session['id'], 'outputId': output['id'],
        'version': 1, 'messageId': session['messages'][0]['id'],
        'presentation': {'chat': 'compact', 'canvas': 'unchanged'}}))['result']
    await call(app, 'outputs.unlink', {'sessionId': session['id'], 'id': output['id'], 'expectedRevision': 1})
    from amplifier_web.state_records import load
    retained = next(row for row in load(app.db)['canvasArtifacts'] if row['id'] == saved['id'])
    assert retained['publications'][0]['chat'] == 'none'
    with pytest.raises(AppError):
        await call(app, 'canvas.present', {'sessionId': session['id'], 'outputId': output['id'], 'version': 1,
            'presentation': {'chat': 'compact', 'canvas': 'open'}})
    opened = (await call(app, 'canvas.present', {'sessionId': session['id'], 'id': saved['id'], 'version': 1,
        'presentation': {'chat': 'none', 'canvas': 'open'}}))['result']
    assert opened['canvas']['status'] == 'opened'
    assert app.clients.records['one']['canvas']['content'] == 'Keep original bytes'


async def test_durable_generation_binding_is_exact_and_not_inherited_by_ordinary_image_version(app, tmp_path):
    session = owner(app)
    mid = session['messages'][0]['id']
    _, data = receipt(tmp_path, 'synthetic-generated')
    generated = (await call(app, 'outputs.attachImage', {'sessionId': session['id'], 'messageId': mid,
        'title': 'Generated', 'receiptPath': 'synthetic-generated.json'}))['result']
    row = artifact(app, generated['canvasId'])
    marker = {'verified': True, 'requestId': 'synthetic-generated', 'operation': 'generate', 'parentId': None}
    assert row['publications'][0] == {'messageId': mid, 'version': 1, 'chat': 'inline',
        'outputId': generated['id'], 'imageGeneration': marker}
    assert row['body'] == generated['body'] and app.outputs.content(generated) == data
    session['execution'] = {'nodes': [], 'turns': []}
    (tmp_path / 'synthetic-generated.png').unlink()
    (tmp_path / 'synthetic-generated.json').unlink()
    await call(app, 'canvas.versions.revise', {'id': row['id'], 'expectedRevision': 1,
        'content': 'data:image/png;base64,' + base64.b64encode(png(4, 2)).decode()})
    result = (await call(app, 'canvas.present', {'sessionId': session['id'], 'id': row['id'], 'version': 2,
        'messageId': mid, 'presentation': {'chat': 'inline', 'canvas': 'unchanged'}}))['result']
    assert 'outputId' not in result['publication'] and 'imageGeneration' not in result['publication']
    assert next(link for link in row['publications'] if link['version'] == 1)['imageGeneration'] == marker
    retained = (await call(app, 'canvas.present', {'sessionId': session['id'], 'outputId': generated['id'], 'version': 1,
        'presentation': {'chat': 'inline', 'canvas': 'open'}}))['result']
    assert retained['id'] == row['id'] and retained['publication']['imageGeneration'] == marker
    assert retained['version'] == 1 and retained['revision'] == 2


async def test_ordinary_image_and_claimed_request_id_never_gain_generation_marker(app, tmp_path):
    from amplifier_web.artifact_presentation import generation_binding
    session = owner(app)
    (tmp_path / 'ordinary.png').write_bytes(png())
    output = (await call(app, 'outputs.attach', {'sessionId': session['id'], 'kind': 'file',
        'path': 'ordinary.png', 'title': 'Ordinary plot',
        'messageId': session['messages'][0]['id']}))['result']
    placed = (await call(app, 'canvas.present', {'sessionId': session['id'], 'outputId': output['id'], 'version': 1,
        'messageId': session['messages'][0]['id'],
        'presentation': {'chat': 'inline', 'canvas': 'open'}}))['result']
    row = artifact(app, placed['id'])
    assert row['kind'] == 'image' and 'imageGeneration' not in placed['publication']
    claimed = {**output, 'canvasId': row['id'], 'imageGeneration': {
        'requestId': 'claimed', 'operation': 'generate', 'receiptSha256': 'a' * 64,
        'provenance': 'producer-reported; local artifact bytes verified'}}
    assert generation_binding(claimed, {**row, 'imageRequestId': 'claimed'}, 1) is None


@pytest.mark.parametrize('format', ['gif', 'mp4'])
async def test_retained_media_adapter_keeps_original_tuple_without_history_source_or_duplicate_bytes(media_app, format, monkeypatch):
    app = media_app
    args = media_args(app)
    if format == 'mp4':
        data = mp4()
        (Path(app._session()['workspace']) / 'clip.mp4').write_bytes(data)
        args.update(path='clip.mp4', expectedSha256=hashlib.sha256(data).hexdigest())
    saved = (await app.dispatch('outputs.attachMedia', args, include_state=False))['result']
    original = app.outputs.record(args['sessionId'], saved['id'])
    app.clients.attach('retained-one')
    with app.clients.bind('retained-one'):
        app.subscribe()
        present_args = {'sessionId': args['sessionId'], 'outputId': saved['id'], 'version': saved['version'],
                        'messageId': args['messageId'], 'presentation': {'chat': 'inline', 'canvas': 'open'}}
        placed = (await app.dispatch('canvas.present', present_args, include_state=False))['result']
        row = artifact(app, placed['id'])
        ref = app.state_resource(row['body']['$resource'])
        assert set(ref) == {'mediaReference'} and len(json.dumps(ref).encode()) < 8000
        item = ref['mediaReference']['item']
        assert item['id'] == saved['id'] and item['version'] == saved['version']
        assert item['sha256'] == saved['sha256'] and row['kind'] == format
        assert placed['publication']['outputId'] == saved['id']
        with pytest.raises(AppError, match='Download original'):
            await app.dispatch('canvas.download', {'id': row['id']}, include_state=False)
        Path(app._session()['workspace'], args['path']).unlink()
        app._session()['messages'] = [{'id': 'replacement', 'role': 'user', 'text': 'Actual revised history'}]
        from test_retained_media import forbid_history
        with monkeypatch.context() as patch:
            forbid_history(patch, app)
            reopened = (await app.dispatch('canvas.present', present_args, include_state=False))['result']
            assert reopened['id'] == placed['id'] and reopened['publication']['status'] == 'unchanged'
            values = gif_open_args(args, saved)
            from amplifier_web import output_media
            def no_body(*_):
                pytest.fail('HEAD opened or decoded a retained media body')
            patch.setattr(output_media, '_saved_bytes', no_body)
            patch.setattr(output_media, 'validate_retained_body', no_body)
            assert await app.outputs.media.head(values) == original['bytes']
        assert app.outputs.record(args['sessionId'], saved['id'])['body'] == original['body']
        assert app.outputs.media.observation(original)['status'] == 'not_observed'
        with pytest.raises(AppError, match='explicit click'):
            await app.dispatch('canvas.openGif', gif_open_args(args, saved), origin='agent', include_state=False)


async def test_media_none_has_exact_publication_output_id_to_suppress_legacy_auto_inline(media_app):
    app = media_app
    args = media_args(app)
    saved = (await app.dispatch('outputs.attachMedia', args, include_state=False))['result']
    result = (await app.dispatch('canvas.present', {'sessionId': args['sessionId'],
        'outputId': saved['id'], 'version': saved['version'],
        'presentation': {'chat': 'none', 'canvas': 'unchanged'}}, include_state=False))['result']
    assert result['publication']['outputId'] == saved['id']
    assert result['publication']['messageId'] == args['messageId'] and result['publication']['chat'] == 'none'
    assert artifact(app, result['id'])['publications'][0]['outputId'] == saved['id']


async def test_media_views_have_independent_reports_stale_generations_and_inline_cap(media_app):
    app = media_app
    args = media_args(app)
    saved = (await app.dispatch('outputs.attachMedia', args, include_state=False))['result']
    app.clients.attach('media-views')
    with app.clients.bind('media-views'):
        app.subscribe()
        placed = (await app.dispatch('canvas.present', {'sessionId': args['sessionId'], 'outputId': saved['id'],
            'version': saved['version'], 'messageId': args['messageId'],
            'presentation': {'chat': 'inline', 'canvas': 'open'}}, include_state=False))['result']
        primary = app.canvas_views.summary('primary')
        inline = (await app.dispatch('canvas.views.inline', {'resourceId': placed['id'], 'version': placed['version'],
            'messageId': args['messageId'], 'previewId': 'independent-media-preview'}, include_state=False))['result']
        for view, frames in ((primary, 3), (inline, 8)):
            target = {key: view[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
            reported = (await app.dispatch('canvas.views.mediaReport', {**target, 'sequence': 1,
                'renderState': 'ready', 'playback': 'paused', 'presentedFrames': frames, 'mediaTime': frames / 10},
                include_state=False))['result']
            assert reported['status'] == 'observed'
            with pytest.raises(AppError):
                await app.dispatch('canvas.views.mediaReport', {**target, 'generation': target['generation'] - 1,
                    'sequence': 2, 'renderState': 'ready', 'playback': 'playing',
                    'presentedFrames': 99, 'mediaTime': 9.9}, include_state=False)
        assert app.canvas_views.summary('primary')['mediaObservation']['presentedFrames'] == 3
        assert app.canvas_views.summary(inline['viewId'])['mediaObservation']['presentedFrames'] == 8
        assert app.outputs.media.observation(app.outputs.record(args['sessionId'], saved['id']))['status'] == 'not_observed'
        await app.dispatch('canvas.views.dirty', {key: inline[key] for key in
            ('viewId', 'resourceId', 'resourceRevision', 'generation')} | {'dirty': True}, include_state=False)
        for number in range(7):
            await app.dispatch('canvas.views.inline', {'resourceId': placed['id'], 'version': placed['version'],
                'messageId': args['messageId'], 'previewId': f'bounded-media-{number}'}, include_state=False)
        original = copy.deepcopy(app.clients.records['media-views']['canvasViews'])
        with pytest.raises(AppError, match='Close a preview'):
            await app.dispatch('canvas.views.inline', {'resourceId': placed['id'], 'version': placed['version'],
                'messageId': args['messageId'], 'previewId': 'over-cap'}, include_state=False)
        assert app.clients.records['media-views']['canvasViews'] == original


async def test_view_capabilities_follow_selected_definition_not_latest_row_kind(app):
    from amplifier_web.canvas_views import capabilities, project_artifacts
    from amplifier_web.canvas_versions import save as version_save
    from amplifier_web.resource_files import put
    result = await save(app)
    row = artifact(app, result['id'])
    previous = copy.deepcopy(row)
    updated = {**copy.deepcopy(row), 'kind': 'browser', 'url': 'https://example.invalid',
               'body': put(app.db, {})}
    version_save(updated, previous, app._state, force=True, message=owner(app)['messages'][0]['id'])
    row.clear()
    row.update(updated)
    projection = project_artifacts({'canvasArtifacts': [row]})['canvasArtifacts'][0]
    assert projection['presentationCapabilities'] == capabilities('browser')
    assert projection['versions'][0]['presentationCapabilities'] == capabilities('markdown')
    with app.clients.bind('one'):
        historical = await app.dispatch('canvas.views.inline', {'resourceId': row['id'], 'version': 1,
            'messageId': owner(app)['messages'][0]['id'], 'previewId': 'historical-reader'}, include_state=False)
        assert historical['result']['resource']['kind'] == 'markdown'
        with pytest.raises(AppError, match='Canvas'):
            await app.dispatch('canvas.views.inline', {'resourceId': row['id'], 'version': 2,
                'messageId': owner(app)['messages'][0]['id'], 'previewId': 'unsupported-browser-inline'}, include_state=False)
    for kind in ('canvas-app', 'mcp-app', 'browser', 'a2ui'):
        assert not capabilities(kind)['inline'] and capabilities(kind)['reason']
    assert 'canvas.present' in ACTION_DEFINITIONS
    assert 'presentation' in ACTION_DEFINITIONS['canvas.show'][1]['properties']