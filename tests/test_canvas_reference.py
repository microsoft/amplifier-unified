"""Explicit quote insertion shares source, version, client and draft guards."""
from copy import deepcopy

import pytest

from amplifier_web.service import AppError, AppService


@pytest.fixture
async def app(tmp_path):
    app = AppService(tmp_path/'data', workspace=tmp_path)
    await app.dispatch('session.create', {})
    app.clients.attach('one'); app.clients.attach('two')
    yield app
    await app.close()


async def call(app, name, args=None, client='one', **options):
    with app.clients.bind(client):
        return await app.dispatch(name, args or {}, **options)


async def setup(app, source='Repeated passage.\n\nRepeated passage.', kind='markdown'):
    result = (await call(app, 'canvas.show', {'kind': kind, 'title': 'Plan [draft]', 'content': source}))['result']
    await call(app, 'view.update', {'patch': {'draft': 'Unsent text  '}})
    return {'id': result['id'], 'sessionId': app.clients.records['one']['selectedSessionId'], 'version': 1,
            'excerpt': 'Repeated passage.', 'spans': [{'start': 19, 'end': 36}], 'expectedDraft': 'Unsent text  '}


async def test_exact_repeated_passage_and_immutable_version_preserve_history_and_other_draft(app):
    args = await setup(app)
    await call(app, 'view.update', {'patch': {'draft': 'Other browser'}}, client='two')
    await call(app, 'canvas.versions.revise', {'id': args['id'], 'expectedRevision': 1, 'content': 'New latest content'})
    before = deepcopy(app.state['sessions'])
    result = (await call(app, 'canvas.reference', args))['result']
    assert result['spans'] == [{'start': 19, 'end': 36}]
    assert result['version'] == 1 and result['sent'] is False
    assert result['draft'].startswith('Unsent text  \n\nFrom [Plan \\[draft\\] · Version 1]')
    assert '> Repeated passage\\.' in result['draft'] and 'version=1' in result['draft']
    assert app.clients.records['one']['view']['draft'] == result['draft']
    assert app.clients.records['two']['view']['draft'] == 'Other browser'
    assert app.state['sessions'] == before
    assert app.state['canvasArtifacts'][0]['revision'] == 2
    app._save()
    await app.close()
    reopened = AppService(app.data_dir, workspace=app.state['workspaces'][0]['path'])
    try:
        assert reopened.clients.records['one']['view']['draft'] == result['draft']
    finally:
        await reopened.close()


async def test_formatted_unicode_entities_and_literal_code_are_source_backed(app):
    source = '# Plan\n\nA **bold** &amp; 😀 `code`.'
    args = await setup(app, source)
    args.update(excerpt='A bold & 😀 code.', spans=[
        {'start': 8, 'end': 10}, {'start': 12, 'end': 16}, {'start': 18, 'end': 27},
        {'start': 28, 'end': 32, 'literal': True}, {'start': 33, 'end': 34}])
    result = (await call(app, 'canvas.reference', args))['result']
    assert '> A bold \\& 😀 code\\.' in result['draft']


@pytest.mark.parametrize('change', [
    {'excerpt': 'Invented text'}, {'version': 99}, {'spans': [{'start': 0, 'end': 500}]},
    {'spans': [{'start': 19, 'end': 36}, {'start': 0, 'end': 17}]},
    {'spans': [{'start': 0, 'end': 0}]}, {'spans': []}, {'excerpt': ''}, {'excerpt': 'x'*4001},
])
async def test_invalid_selection_keeps_draft_and_history(app, change):
    args = await setup(app)
    before = deepcopy(app.state['sessions'])
    with pytest.raises(AppError):
        await call(app, 'canvas.reference', {**args, **change})
    assert app.clients.records['one']['view']['draft'] == 'Unsent text  '
    assert app.state['sessions'] == before


async def test_newer_draft_and_duplicate_action_id_do_not_replace_or_repeat(app):
    args = await setup(app)
    first = await call(app, 'canvas.reference', args, command_id='quote-once')
    repeated = await call(app, 'canvas.reference', args, command_id='quote-once')
    assert first['result']['draft'] == repeated['result']['draft']
    assert app.clients.records['one']['view']['draft'].count('> Repeated passage\\.') == 1
    await call(app, 'view.update', {'patch': {'draft': 'Newer text'}})
    with pytest.raises(AppError, match='draft changed'):
        await call(app, 'canvas.reference', args)
    assert app.clients.records['one']['view']['draft'] == 'Newer text'


async def test_late_session_switch_foreign_session_and_unattached_client_are_rejected(app):
    args = await setup(app)
    await call(app, 'session.create', {}, client='one')
    with pytest.raises(AppError, match='browser displaying'):
        await call(app, 'canvas.reference', args)
    foreign = {**args, 'sessionId': app.clients.records['one']['selectedSessionId'], 'expectedDraft': ''}
    with pytest.raises(AppError, match='another conversation'):
        await call(app, 'canvas.reference', foreign)
    with pytest.raises(AppError, match='browser displaying'):
        await app.dispatch('canvas.reference', args)
    assert app.clients.records['one']['drafts'][args['sessionId']] == 'Unsent text  '


async def test_agent_uses_same_action_and_state_with_explicit_client(app):
    args = await setup(app)
    with pytest.raises(AppError):
        await app.app_bridge('dispatch', {'action': 'canvas.reference', 'args': args}, args['sessionId'])
    result = await app.app_bridge('dispatch', {'action': 'canvas.reference', 'args': {**args, 'clientId': 'one'}}, args['sessionId'])
    assert result['result']['sent'] is False
    assert result['state']['view']['draft'] == result['result']['draft']
    other = await call(app, 'session.create', {}, client='two')
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch', {'action': 'canvas.reference', 'args': {**args, 'clientId': 'two'}}, args['sessionId'])


async def test_renderer_view_target_rejects_changed_version_and_foreign_agent(app):
    args = await setup(app)
    with app.clients.bind('one'):
        view = app.canvas_views.project()['views'][0]
    target = {k: view[k] for k in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
    await call(app, 'canvas.versions.revise', {'id': args['id'], 'expectedRevision': 1, 'content': 'Changed'})
    with pytest.raises(AppError):
        await call(app, 'canvas.views.command', {**target, 'action': 'canvas.reference', 'args': args})
    await call(app, 'canvas.select', {'id': args['id'], 'version': 1})
    with app.clients.bind('one'):
        view = app.canvas_views.project()['views'][0]
    target = {k: view[k] for k in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
    await call(app, 'session.create', {}, client='two')
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch', {'action': 'canvas.views.command', 'args': {**target, 'clientId': 'one', 'action': 'canvas.reference', 'args': args}}, app.clients.records['two']['selectedSessionId'])
    result = await call(app, 'canvas.views.command', {**target, 'action': 'canvas.reference', 'args': args})
    assert result['result']['version'] == 1


async def test_plain_text_keeps_markup_literal_and_other_kinds_are_rejected(app):
    text = '**Literal &amp;**'
    args = await setup(app, text, 'text')
    args.update(excerpt=text, spans=[{'start': 0, 'end': len(text)}])
    assert '> \\*\\*Literal \\&amp;\\*\\*' in (await call(app, 'canvas.reference', args))['result']['draft']
    args = await setup(app, '<h1>Title</h1>', 'html')
    with pytest.raises(AppError, match='Markdown or plain-text'):
        await call(app, 'canvas.reference', args)
