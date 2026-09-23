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
    await dispatch(app, 'canvas.views.open', {'resourceId': row['id'], 'sessionId': row['sessionId']}, client='two')
    with app.clients.bind('two'):
        target = {k: app.canvas_views.summary('secondary')[k] for k in ['viewId', 'resourceId', 'resourceRevision', 'generation']}
    await dispatch(app, 'canvas.views.dirty', {**target, 'dirty': True}, client='two')
    with pytest.raises(AppError, match='Finish or cancel'):
        await edit(app, row, 'revise', content='updated')
    await dispatch(app, 'canvas.views.dirty', {**target, 'dirty': False}, client='two')
    row = await edit(app, row, 'revise', content='updated')
    row = await edit(app, row, 'state', patch={'note': 'Shared on every view'})
    await dispatch(app, 'view.update', {'patch': {'canvasWidth': 600}}, client='two')
    with app.clients.bind('two'):
        assert app.canvas_views.summary('secondary')['app'] == row['app']
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
