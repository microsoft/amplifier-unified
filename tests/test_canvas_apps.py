from copy import deepcopy

import pytest

from amplifier_web.service import AppError, AppService


MANIFEST = {'version': 1, 'stateSchema': {'type': 'object', 'properties': {
    'selected': {'type': 'string'}, 'note': {'type': 'string'}}, 'required': ['selected', 'note'], 'additionalProperties': False},
    'events': {'choose': {'schema': {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']},
                          'updates': {'selected': 'value'}}},
    'requests': {name: {'action': 'theme.' + name, 'schema': {'type': 'object'}} for name in ['preview', 'apply', 'revert']}}


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'data', workspace=tmp_path)
    await service.dispatch('session.create', {})
    service._message(service._session(), 'user', 'Create a shared surface')
    service.clients.attach('one')
    service.clients.attach('two')
    yield service
    await service.close()


async def dispatch(app, action, args, client='one', **kwargs):
    with app.clients.bind(client):
        return await app.dispatch(action, args, **kwargs)


async def create(app, **extra):
    return (await dispatch(app, 'canvas.apps.create', {'title': 'Session chooser', 'content': '<h1>A chooser</h1>',
        'manifest': MANIFEST, 'initialState': {'selected': 'ocean', 'note': ''}, **extra}))['result']


def cas(row):
    return {'id': row['id'], 'sessionId': row['sessionId'], 'expectedRevision': row['app']['revision'],
            'expectedStateRevision': row['app']['stateRevision']}


async def edit(app, row, action, **args):
    return (await dispatch(app, 'canvas.apps.' + action, {**cas(row), **args}))['result']


async def test_same_tab_revision_history_and_state_survive_restart(app, tmp_path):
    row = await create(app)
    await dispatch(app, 'view.update', {'patch': {'draft': 'Keep my unfinished message'}})
    row = await edit(app, row, 'event', name='choose', payload={'value': 'forest'})
    row = await edit(app, row, 'state', patch={'note': 'Keep this input'})
    row = await edit(app, row, 'revise', content='<h1>Better chooser</h1>')
    assert row['app']['state'] == {'selected': 'forest', 'note': 'Keep this input'}
    assert len(app.state['canvasArtifacts']) == 1
    assert app.clients.records['one']['canvas']['id'] == row['id']
    assert app.clients.records['one']['view']['draft'] == 'Keep my unfinished message'
    assert len(app._session()['messages']) == 1
    row = await edit(app, row, 'restore', version=1)
    assert row['app']['revision'] == 3
    assert row['app']['state']['selected'] == 'forest'
    await app.close()
    restored = AppService(app.data_dir, workspace=tmp_path)
    try:
        inspected = (await dispatch(restored, 'canvas.apps.inspect', {'id': row['id'], 'includeSource': True}))['result']
        assert inspected['content'] == '<h1>A chooser</h1>'
        assert inspected['app'] == row['app']
        assert restored.clients.records['one']['view']['draft'] == 'Keep my unfinished message'
    finally:
        await restored.close()


async def test_agent_user_parity_stale_edits_and_idempotency(app):
    row = await create(app)
    args = {**cas(row), 'name': 'choose', 'payload': {'value': 'forest'}}
    accepted = await app.app_bridge('dispatch', {'action': 'canvas.apps.event', 'args': args, 'id': 'agent-event'}, row['sessionId'])
    assert accepted['result']['app']['state']['selected'] == 'forest'
    duplicate = await app.app_bridge('dispatch', {'action': 'canvas.apps.event', 'args': args, 'id': 'agent-event'}, row['sessionId'])
    assert duplicate['duplicate']
    with pytest.raises(AppError, match='changed'):
        await edit(app, row, 'state', patch={'selected': 'stale'})
    current = accepted['result']
    assert len(current['app']['events']) == 1
    assert current['app']['events'][0]['origin'] == 'agent'
    current = await edit(app, current, 'event', name='choose', payload={'value': 'sunset'})
    assert current['app']['state']['selected'] == 'sunset'


async def test_incompatible_revision_requires_explicit_valid_migration(app):
    row = await create(app)
    changed = deepcopy(MANIFEST)
    changed['stateSchema']['properties']['selected'] = {'type': 'integer'}
    with pytest.raises(AppError, match='schema'):
        await edit(app, row, 'revise', content='new', manifest=changed)
    assert app.state['canvasArtifacts'][0]['app']['revision'] == 1
    row = await edit(app, row, 'revise', content='new', manifest=changed, migratedState={'selected': 3, 'note': 'preserved'})
    assert row['app']['state']['selected'] == 3
    with pytest.raises(AppError, match='schema'):
        await edit(app, row, 'restore', version=1)


async def test_all_views_share_canonical_state_and_dirty_views_block_revision(app):
    row = await create(app)
    await dispatch(app, 'canvas.select', {'id': row['id']}, client='two')
    with app.clients.bind('two'):
        target = {k: app.canvas_views.summary('primary')[k] for k in ['viewId', 'resourceId', 'resourceRevision', 'generation']}
    await dispatch(app, 'canvas.views.dirty', {**target, 'dirty': True}, client='two')
    with pytest.raises(AppError, match='Finish or cancel'):
        await edit(app, row, 'revise', content='updated')
    await dispatch(app, 'canvas.views.dirty', {**target, 'dirty': False}, client='two')
    row = await edit(app, row, 'revise', content='updated')
    row = await edit(app, row, 'state', patch={'note': 'Shared on every view'})
    await dispatch(app, 'view.update', {'patch': {'canvasWidth': 600}}, client='two')
    with app.clients.bind('two'):
        assert app.canvas_views.summary('primary')['app'] == row['app']
        assert app.state['canvas']['app'] == row['app']
    assert app.state['canvasArtifacts'][0]['app'] == row['app']


async def test_conversation_scope_and_background_updates_do_not_retarget(app):
    row = await create(app)
    await dispatch(app, 'session.create', {})
    other = app.clients.records['one']['selectedSessionId']
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch', {'action': 'canvas.apps.state', 'args': {**cas(row), 'patch': {'note': 'wrong'}}}, other)
    with pytest.raises(AppError, match='unavailable'):
        await dispatch(app, 'canvas.apps.inspect', {'id': row['id']})
    created = await app.app_bridge('dispatch', {'action': 'canvas.apps.create', 'args': {
        'title': 'Background surface', 'content': 'hello', 'manifest': MANIFEST, 'initialState': {'note': '', 'selected': 'ocean'}}}, row['sessionId'])
    assert created['result']['sessionId'] == row['sessionId']
    assert app.clients.records['one']['selectedSessionId'] == other
    assert app.clients.records['one']['canvas'].get('placeholder')


async def test_reviewed_theme_actions_preview_apply_revert_and_no_replay(app):
    row = await create(app)
    before = deepcopy(app.state['theme'])
    theme = {'name': 'Forest', 'css': '#amp-one{--a-accent:#287358}'}
    row = await edit(app, row, 'request', name='preview', input=theme)
    pending = row['app']['requests'][-1]
    assert app.state['theme'] == before
    inspected = (await dispatch(app, 'canvas.apps.inspect', {'id': row['id'], 'requestId': pending['id']}))['result']
    assert inspected['requestInput'] == theme
    row = await edit(app, row, 'resolve', requestId=pending['id'], approve=True)
    assert app.clients.records['one']['view']['themePreview']
    assert not app.clients.records['two']['view'].get('themePreview')
    assert app.state['theme'] == before
    row = await edit(app, row, 'request', name='apply', input=theme)
    row = await edit(app, row, 'resolve', requestId=row['app']['requests'][-1]['id'], approve=True)
    assert app.state['theme'] == theme
    assert not app.clients.records['one']['view']['themePreview']
    with pytest.raises(AppError, match='already resolved'):
        await edit(app, row, 'resolve', requestId=pending['id'], approve=True)
    row = await edit(app, row, 'request', name='revert', input={})
    row = await edit(app, row, 'resolve', requestId=row['app']['requests'][-1]['id'], approve=True)
    assert app.state['theme'] == before
    row = await edit(app, row, 'request', name='apply', input=theme)
    row = await edit(app, row, 'revise', content='refined')
    assert row['app']['requests'][-1]['status'] == 'superseded'


async def test_stale_theme_request_and_undo_cannot_overwrite_another_client(app):
    row = await create(app)
    row = await edit(app, row, 'request', name='apply', input={'name': 'Forest', 'css': '#amp-one{color:green}'})
    await dispatch(app, 'theme.apply', {'name': 'Other', 'css': '#amp-one{color:red}'}, client='two')
    with pytest.raises(AppError, match='shell theme changed'):
        await edit(app, row, 'resolve', requestId=row['app']['requests'][-1]['id'], approve=True)
    await dispatch(app, 'theme.apply', {'name': 'Mine', 'css': '#amp-one{color:blue}'})
    await dispatch(app, 'theme.apply', {'name': 'Other again', 'css': '#amp-one{color:red}'}, client='two')
    with pytest.raises(AppError, match='applied theme changed'):
        await dispatch(app, 'theme.revert', {})


async def test_fork_copies_state_but_not_pending_actions(app):
    row = await create(app)
    row = await edit(app, row, 'request', name='apply', input={'name': 'Forest', 'css': '#amp-one{color:green}'})
    await dispatch(app, 'session.fork', {'id': row['sessionId'], 'turn': 1})
    fork = app.clients.records['one']['selectedSessionId']
    copied = next(r for r in app.state['canvasArtifacts'] if r['sessionId'] == fork)
    assert copied['id'] != row['id'] and copied['app']['state'] == row['app']['state']
    assert copied['app']['requests'] == []
    assert row['app']['requests'][0]['status'] == 'pending'


@pytest.mark.parametrize('bad', [
    {'version': 99},
    {**MANIFEST, 'stateSchema': {'type': 'object', '$ref': 'https://example.com/schema'}},
    {**MANIFEST, 'stateSchema': {'type': 'object', 'patternProperties': {'(a+)+': {'type': 'string'}}}},
    {**MANIFEST, 'requests': {'run': {'action': 'conversation.send', 'schema': {'type': 'object'}}}},
])
async def test_invalid_manifests_have_no_side_effects(app, bad):
    with pytest.raises(AppError):
        await create(app, manifest=bad)
    assert not app.state.get('canvasArtifacts')


async def test_invalid_data_and_events_do_not_mutate_surface(app):
    row = await create(app)
    for action, args in [('state', {'patch': {'selected': 23}}), ('state', {'patch': {'__proto__': {}}}),
                         ('event', {'name': 'unknown', 'payload': {}}), ('event', {'name': 'choose', 'payload': {}}),
                         ('request', {'name': 'apply', 'input': {'name': 'Network', 'css': '@import "https://example.com/x.css";'}})]:
        with pytest.raises(AppError):
            await edit(app, row, action, **args)
        assert app.state['canvasArtifacts'][0]['app'] == row['app']


async def test_palette_requests_preserve_the_skin_and_review_exact_css(app):
    row = await create(app)
    before = deepcopy(app.state['theme'])
    row = await edit(app, row, 'request', name='preview', input={'name': 'Forest', 'tokens': {'accent': '#3e7052'}})
    request = row['app']['requests'][-1]
    reviewed = (await dispatch(app, 'canvas.apps.inspect', {'id': row['id'], 'requestId': request['id']}))['result']['requestInput']
    assert reviewed['css'].startswith(before['css'].rstrip())
    assert '#amp-one{--a-accent:#3e7052}' in reviewed['css']
    row = await edit(app, row, 'resolve', requestId=request['id'], approve=True)
    assert app.clients.records['one']['view']['themeDraft'] == reviewed['css']
    assert app.state['theme'] == before
    await dispatch(app, 'theme.apply', {'name': 'Forest', 'tokens': {'accent': '#3e7052'}})
    assert app.state['theme']['css'] == reviewed['css']
    for values in ({'accent': 'red;display:none'}, {'unknown': '#aabbcc'}, {}):
        with pytest.raises(AppError):
            await dispatch(app, 'theme.apply', {'name': 'Invalid', 'tokens': values})
    with pytest.raises(AppError):
        await dispatch(app, 'theme.apply', {'name': 'Ambiguous', 'tokens': {'accent': '#aabbcc'}, 'css': '#amp-one{}'})


def view_target(view):
    return {key: view[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}


async def inline(app, row, preview, *, follow=False, client='one'):
    link = next(item for item in app.state['canvasArtifacts'] if item['id'] == row['id'])['publications'][0]
    return (await dispatch(app, 'canvas.views.inline', {
        'resourceId': row['id'], 'messageId': link['messageId'], 'version': link['version'],
        'previewId': preview, 'followLatest': follow}, client=client))['result']


async def test_exact_and_live_inline_authority_is_independent_of_primary(app):
    row = await create(app)
    row = await edit(app, row, 'revise', content='<h1>Revision two</h1>')
    old = await inline(app, row, 'old')
    for action, extra in (
        ('state', {'patch': {'note': 'not writable'}}),
        ('event', {'name': 'choose', 'payload': {'value': 'not writable'}}),
        ('request', {'name': 'preview', 'input': {'name': 'Forest', 'tokens': {'accent': '#3e7052'}}}),
    ):
        with pytest.raises(AppError, match='read-only'):
            await edit(app, row, action, **view_target(old), **extra)
    live = await inline(app, row, 'live', follow=True)
    await dispatch(app, 'canvas.select', {'id': row['id'], 'version': 1})
    row = await edit(app, row, 'event', **view_target(live), name='choose', payload={'value': 'forest'})
    with app.clients.bind('one'):
        assert app.canvas_views.canvas(old['viewId'])['app']['state']['selected'] == 'ocean'
        assert app.canvas_views.canvas(live['viewId'])['app']['state']['selected'] == 'forest'
        assert app.state['canvas']['app']['state']['selected'] == 'ocean'
    # Legacy browser calls still refuse historical primary edits.
    with pytest.raises(AppError, match='read-only'):
        await edit(app, row, 'state', patch={'note': 'legacy UI'})
    result = await app.app_bridge('dispatch', {'action': 'canvas.apps.state',
        'args': {**cas(row), 'patch': {'note': 'Authenticated agent edit'}}}, row['sessionId'])
    assert result['result']['app']['state']['note'] == 'Authenticated agent edit'


async def test_follow_retains_original_publication_identity_and_dirty_guard(app):
    row = await create(app)
    original = await inline(app, row, 'same-mount')
    saved = deepcopy(app.state['canvasArtifacts'][0])
    live = (await dispatch(app, 'canvas.views.follow', {
        **view_target(original), 'followLatest': True}))['result']
    assert live['viewId'] == original['viewId']
    assert live['publishedVersion'] == 1 and live['selectedVersion'] is None and live['followLatest']
    assert live['generation'] > original['generation']
    await dispatch(app, 'canvas.views.dirty', {**view_target(live), 'dirty': True})
    for action, args in (
        ('canvas.views.follow', {**view_target(live), 'followLatest': False}),
        ('canvas.views.inline', {'resourceId': row['id'], 'messageId': saved['publications'][0]['messageId'],
            'version': 1, 'previewId': 'same-mount'}),
    ):
        with pytest.raises(AppError, match='Finish or cancel'):
            await dispatch(app, action, args)
    assert app.state['canvasArtifacts'][0] == saved
    await dispatch(app, 'canvas.views.dirty', {**view_target(live), 'dirty': False})
    exact = (await dispatch(app, 'canvas.views.follow', {**view_target(live), 'followLatest': False}))['result']
    assert exact['viewId'] == original['viewId'] and exact['selectedVersion'] == exact['publishedVersion'] == 1
    assert not exact['followLatest']
    assert app.state['canvasArtifacts'][0] == saved
    with pytest.raises(AppError, match='changed'):
        await dispatch(app, 'canvas.views.follow', {**view_target(live), 'followLatest': True})


async def test_inline_restore_preserves_independent_numbered_primary(app):
    row = await create(app)
    row = await edit(app, row, 'revise', content='<h1>Revision two</h1>')
    live = await inline(app, row, 'restore-owner', follow=True)
    await dispatch(app, 'canvas.select', {'id': row['id'], 'version': 1})
    with app.clients.bind('one'):
        app.state['canvas']['publishedVersion'] = 1
        before = deepcopy(app.state['canvas'])
    row = await edit(app, row, 'restore', **view_target(live), version=1)
    assert row['app']['revision'] == 3
    with app.clients.bind('one'):
        assert app.state['canvas'] == before
        restored = app.canvas_views.summary(live['viewId'])
        assert restored['followLatest'] and restored['app']['revision'] == 3
        assert restored['publishedVersion'] == 1


async def test_inspecting_bindings_never_reconciles_other_clients(app, monkeypatch):
    row = await create(app)
    other = await inline(app, row, 'other-client', follow=True, client='two')
    record = app.clients.records['two']
    preference = record['canvasViews']['preferences'][other['viewId'] + ':' + row['id']]
    preference.update(dirty=True, activation={'status': 'ready'}, mcp={'context': {'local': 'kept'}})
    # The canonical definition advances before the other client observes it.
    app.state['canvasArtifacts'][0]['app']['revision'] += 1
    before = deepcopy(record)
    def forbidden(*args, **kwargs):
        raise AssertionError('Passive binding inspection reconciled a client')
    monkeypatch.setattr(app.clients, 'bind', forbidden)
    inspected = app.canvas_views.bindings(row['id'])
    observed = next(view for view in inspected if view['viewId'] == other['viewId'])
    assert observed['dirty'] and observed['generation'] == other['generation']
    assert observed['resourceRevision'] == other['resourceRevision']
    assert record == before


async def test_actual_live_bindings_share_state_but_keep_private_dirty_edits(app):
    row = await create(app)
    one = await inline(app, row, 'one-live', follow=True)
    two = await inline(app, row, 'two-live', follow=True, client='two')
    await dispatch(app, 'canvas.views.dirty', {**view_target(two), 'dirty': True, 'editVersion': 3}, client='two')
    row = await edit(app, row, 'state', **view_target(one), patch={'note': 'Shared acknowledged state'})
    inspected = (await dispatch(app, 'canvas.apps.inspect', {'id': row['id']}))['result']
    dirty = next(view for view in inspected['views'] if view['viewId'] == two['viewId'])
    assert dirty['clientId'] == 'two' and dirty['generation'] == two['generation'] and dirty['dirty']
    for client, view in (('one', one), ('two', two)):
        with app.clients.bind(client):
            current = app.canvas_views.summary(view['viewId'])
            assert current['app']['stateRevision'] == row['app']['stateRevision']
            assert current['app']['state']['note'] == 'Shared acknowledged state'
            assert current['dirty'] == (client == 'two')
    for name, args in (('revise', {'content': 'replacement'}), ('restore', {'version': 1})):
        with pytest.raises(AppError, match='Finish or cancel'):
            await edit(app, row, name, **args)
    await dispatch(app, 'canvas.views.dirty', {**view_target(two), 'dirty': False}, client='two')
    await dispatch(app, 'canvas.views.release', view_target(two), client='two')
    # Abandoned preferences are not an actual binding or a perpetual dirty grant.
    app.clients.records['two']['canvasViews']['preferences']['primary:' + row['id']] = {'dirty': True}
    row = await edit(app, row, 'revise', content='new definition')
    with app.clients.bind('one'):
        assert app.canvas_views.summary(one['viewId'])['app']['revision'] == 2
        assert app.canvas_views.summary(one['viewId'])['generation'] > one['generation']
    with pytest.raises(AppError):
        await dispatch(app, 'canvas.apps.state', {**cas(row), **view_target(one), 'patch': {}}, client='two')
    with pytest.raises(AppError):
        await edit(app, row, 'state', viewId=one['viewId'], patch={})
    with pytest.raises(AppError):
        await edit(app, row, 'state', **view_target(one), patch={})


async def test_primary_follow_is_exact_and_does_not_publish(app):
    row = await create(app)
    await dispatch(app, 'canvas.select', {'id': row['id'], 'version': 1})
    with app.clients.bind('one'):
        exact = app.canvas_views.summary('primary')
    saved = deepcopy(app.state['canvasArtifacts'][0])
    live = (await dispatch(app, 'canvas.views.follow', {**view_target(exact), 'followLatest': True}))['result']
    returned = (await dispatch(app, 'canvas.views.follow', {**view_target(live), 'followLatest': False}))['result']
    assert returned['selectedVersion'] == 1 and returned['publishedVersion'] == 1
    assert app.state['canvasArtifacts'][0] == saved


async def test_present_latest_number_is_still_exact_and_readonly(app):
    row = await create(app)
    row = await edit(app, row, 'revise', content='Version two')
    with app.clients.bind('one'):
        queue = app.subscribe()
        try:
            result = await app.dispatch('canvas.present', {'sessionId': row['sessionId'],
                'id': row['id'], 'version': 2,
                'presentation': {'chat': 'compact', 'canvas': 'open'}})
            assert result['result']['canvas']['status'] == 'opened'
            assert app.state['canvas']['selectedVersion'] == 2
            assert app.state['canvas']['readOnlyVersion']
            target = view_target(app.canvas_views.summary('primary'))
            with pytest.raises(AppError, match='read-only'):
                await app.dispatch('canvas.apps.state', {**cas(row), **target, 'patch': {'note': 'readonly'}})
        finally:
            app.unsubscribe(queue)


@pytest.mark.parametrize('replacement', ['version', 'message', 'artifact'])
async def test_repeated_mount_cannot_allocate_around_dirty_publication(app, replacement):
    row = await create(app)
    row = await edit(app, row, 'revise', content='Second published definition')
    publication = next(item for item in app.state['canvasArtifacts'] if item['id'] == row['id'])['publications'][0]
    incoming = {'resourceId': row['id'], 'version': 1, 'messageId': publication['messageId'],
                'previewId': 'fixed-mount'}
    if replacement == 'version':
        incoming['version'] = 2
    elif replacement == 'message':
        message = app._message(app._session(), 'user', 'Another publication anchor')
        await dispatch(app, 'canvas.present', {'sessionId': row['sessionId'], 'id': row['id'],
            'version': 1, 'messageId': message['id'],
            'presentation': {'chat': 'compact', 'canvas': 'unchanged'}})
        incoming['messageId'] = message['id']
    else:
        other = await create(app)
        other_publication = next(item for item in app.state['canvasArtifacts']
            if item['id'] == other['id'])['publications'][0]
        incoming.update(resourceId=other['id'], messageId=other_publication['messageId'])
    original = await inline(app, row, 'fixed-mount', follow=True)
    await dispatch(app, 'canvas.views.dirty', {**view_target(original), 'dirty': True})
    before = deepcopy(app.clients.records['one']['canvasViews'])
    artifacts = deepcopy(app.state['canvasArtifacts'])
    with pytest.raises(AppError, match='Finish or cancel'):
        await dispatch(app, 'canvas.views.inline', incoming)
    assert app.clients.records['one']['canvasViews'] == before
    assert app.state['canvasArtifacts'] == artifacts
    # Even a clean mount must release explicitly, preserving its immutable
    # publishedVersion and the stale-release boundary until that point.
    await dispatch(app, 'canvas.views.dirty', {**view_target(original), 'dirty': False})
    with pytest.raises(AppError, match='Release this preview'):
        await dispatch(app, 'canvas.views.inline', incoming)
    with app.clients.bind('one'):
        retained = app.canvas_views.summary(original['viewId'])
    assert retained['publishedVersion'] == 1 and retained['viewId'] == original['viewId']
    await dispatch(app, 'canvas.views.release', view_target(retained))
    accepted = (await dispatch(app, 'canvas.views.inline', incoming))['result']
    assert accepted['viewId'] != original['viewId']
    with pytest.raises(AppError):
        await dispatch(app, 'canvas.views.release', view_target(original))
    with app.clients.bind('one'):
        assert app.canvas_views.summary(accepted['viewId'])['resourceId'] == incoming['resourceId']
