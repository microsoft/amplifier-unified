"""Question review shares the exact-version Canvas presentation authority."""
from copy import deepcopy

import pytest

from amplifier_web.service import AppError
from test_canvas_views import app, command, target, view
from test_inline_artifacts import published
from test_questions import create_question


async def reference(app, current):
    with app.clients.bind('one'):
        sid = app.state['selectedSessionId']
        q = await create_question(app, sid, artifacts=[{'id': current['resourceId'], 'version': 1}])
    return {'id': current['resourceId'], 'version': 1, 'questionId': q['id'],
            'questionContentRevision': q['contentRevision'], 'sessionId': sid}


async def inline(app, source, mount, client='one'):
    args = {key: value for key, value in source.items() if key not in {'id', 'sessionId'}}
    return (await command(app, 'canvas.views.inline', {**args, 'resourceId': source['id'],
        'previewId': mount, 'presentationRequest': True}, client=client))['result']


async def test_question_transfer_releases_only_exact_client_version_and_source(app):
    current = await published(app, '# Original', 'markdown')
    question = await reference(app, current)
    publication = {'id': current['resourceId'], 'version': 1, 'messageId': 'inline-message'}
    await command(app, 'canvas.select', {**publication, 'presentation': 'inline'})
    old = await inline(app, publication, 'message')
    await command(app, 'session.select', {'id': question['sessionId']}, client='two')
    await command(app, 'canvas.select', {**publication, 'presentation': 'inline'}, client='two')
    other_client = await inline(app, publication, 'peer', client='two')
    await command(app, 'canvas.versions.revise', {'id': current['resourceId'], 'expectedRevision': 1, 'content': '# Later'})
    await command(app, 'canvas.select', {'id': current['resourceId'], 'version': 2})
    await command(app, 'canvas.select', {**question, 'presentation': 'inline'})
    opened = await inline(app, question, 'question')
    with pytest.raises(AppError, match='Unknown'):
        view(app, old['viewId'])
    with app.clients.bind('one'):
        assert app.canvas_views.canvas(opened['viewId'])['content'] == '# Original'
        assert app.state['canvas']['selectedVersion'] == 2
        assert app.state['canvas']['open']  # Different version remains independent.
    assert view(app, other_client['viewId'], client='two')['resourceId'] == current['resourceId']
    await command(app, 'canvas.select', {**question, 'presentation': 'canvas'})
    with pytest.raises(AppError, match='Unknown'):
        view(app, opened['viewId'])
    assert view(app)['selectedVersion'] == 1
    with pytest.raises(AppError, match='presentation changed'):
        await inline(app, question, 'late-question-mount')
    await command(app, 'canvas.select', {**publication, 'presentation': 'inline'})
    assert (await inline(app, publication, 'returned-message'))['selectedVersion'] == 1


@pytest.mark.parametrize('destination', ['inline', 'canvas', 'saved'])
@pytest.mark.parametrize('patch', [
    {'messageId': 'inline-message'}, {'questionContentRevision': 99},
    {'questionId': 'missing'}, {'version': 2}, {'version': None},
])
async def test_question_choice_rejects_bad_source_before_any_presentation_change(app, destination, patch):
    current = await published(app, '# Original', 'markdown')
    source = await reference(app, current)
    with app.clients.bind('one'):
        before = deepcopy(app.clients.record())
    args = {key: value for key, value in {**source, **patch}.items() if value is not None}
    with pytest.raises(AppError):
        await command(app, 'canvas.select', {**args, 'presentation': destination})
    with app.clients.bind('one'):
        assert app.clients.record()['canvas'] == before['canvas']
        assert app.clients.record().get('canvasViews') == before.get('canvasViews')


async def test_question_choice_and_read_revalidate_immutable_body(app):
    current = await published(app, '# Original', 'markdown')
    source = await reference(app, current)
    await command(app, 'canvas.select', {**source, 'presentation': 'inline'})
    opened = await inline(app, source, 'bound')
    with app.clients.bind('one'):
        q = app.questions.store.get(source['sessionId'], source['questionId'])
        q['artifacts'][0]['bodyDigest'] = '0' * 64
        app.questions.store.put(q)
        with pytest.raises(AppError, match='saved artifact changed'):
            app.canvas_views.canvas(opened['viewId'])
    with pytest.raises(AppError, match='saved artifact changed'):
        await command(app, 'canvas.select', {**source, 'presentation': 'canvas'})


async def test_retained_html_question_transfer_requires_ui_reset_and_never_bypasses_dirty(app):
    current = await published(app, '<input value="local state">', 'html')
    source = await reference(app, current)
    await command(app, 'canvas.visibility', {'open': False, 'sessionId': source['sessionId'], 'canvasId': current['resourceId']})
    with app.clients.bind('one'):
        before = deepcopy(app.clients.record())
    with pytest.raises(AppError) as refused:
        await command(app, 'canvas.select', {**source, 'presentation': 'inline'})
    assert refused.value.code == 'canvas_preview_reset_required'
    with app.clients.bind('one'):
        assert app.clients.record()['canvas'] == before['canvas']
        assert app.clients.record()['canvasViews'] == before['canvasViews']
    with pytest.raises(AppError, match='explicit user'):
        await command(app, 'canvas.select', {**source, 'presentation': 'inline', 'resetPreview': True}, origin='agent')
    await command(app, 'canvas.select', {**source, 'presentation': 'inline', 'resetPreview': True}, origin='ui')
    opened = await inline(app, source, 'html-question')
    await command(app, 'canvas.views.dirty', {**target(opened), 'dirty': True})
    for presentation in ('saved', 'canvas'):
        with pytest.raises(AppError) as refused:
            await command(app, 'canvas.select', {**source, 'presentation': presentation, 'resetPreview': True}, origin='ui')
        assert refused.value.code == 'canvas_view_dirty'
        assert view(app, opened['viewId'])['dirty']
    await command(app, 'canvas.views.dirty', {**target(opened), 'dirty': False})
    with pytest.raises(AppError) as refused:
        await command(app, 'canvas.select', {**source, 'presentation': 'saved'})
    assert refused.value.code == 'canvas_preview_reset_required'
    await command(app, 'canvas.select', {**source, 'presentation': 'saved', 'resetPreview': True}, origin='ui')
    with pytest.raises(AppError, match='Unknown'):
        view(app, opened['viewId'])


async def test_existing_legacy_inline_reader_keeps_exact_latest_publication_only(app):
    current = await published(app, 'Original legacy body', 'text')
    await command(app, 'canvas.versions.revise', {'id': current['resourceId'], 'expectedRevision': 1, 'content': 'Current legacy body'})
    with app.clients.bind('one'):
        row = app.canvas_views.artifact(current['resourceId'])
        row.pop('publications')
        row['messageId'] = 'inline-message'
    args = {'resourceId': current['resourceId'], 'messageId': 'inline-message', 'version': 2, 'previewId': 'legacy'}
    opened = (await command(app, 'canvas.views.inline', args))['result']
    with app.clients.bind('one'):
        assert app.canvas_views.canvas(opened['viewId'])['content'] == 'Current legacy body'
    for patch in ({'version': 1}, {'messageId': 'another-message'}):
        with pytest.raises(AppError, match='published'):
            await command(app, 'canvas.views.inline', {**args, **patch})


async def test_dirty_question_version_does_not_block_distinct_version_or_same_choice(app):
    current = await published(app, '# First', 'markdown')
    source = await reference(app, current)
    await command(app, 'canvas.versions.revise', {'id': current['resourceId'], 'expectedRevision': 1, 'content': '# Second'})
    await command(app, 'canvas.select', {**source, 'presentation': 'inline'})
    opened = await inline(app, source, 'dirty-first')
    await command(app, 'canvas.views.dirty', {**target(opened), 'dirty': True})
    await command(app, 'canvas.select', {**source, 'presentation': 'inline'})
    await command(app, 'canvas.select', {'id': current['resourceId'], 'version': 2})
    assert view(app)['selectedVersion'] == 2
    assert view(app, opened['viewId'])['dirty']
    with pytest.raises(AppError) as refused:
        await command(app, 'canvas.select', {**source, 'presentation': 'canvas'})
    assert refused.value.code == 'canvas_view_dirty'
    assert view(app)['selectedVersion'] == 2


async def test_question_versions_and_ephemeral_message_thumbnail_are_independent(app):
    current = await published(app, 'Version one', 'text')
    first = await reference(app, current)
    await command(app, 'canvas.versions.revise', {'id': current['resourceId'], 'expectedRevision': 1, 'content': 'Version two'})
    with app.clients.bind('one'):
        q = await create_question(app, first['sessionId'], artifacts=[{'id': current['resourceId'], 'version': 2}])
    second = {**first, 'version': 2, 'questionId': q['id']}
    await command(app, 'canvas.select', {**first, 'presentation': 'inline'})
    one = await inline(app, first, 'version-one')
    await command(app, 'canvas.select', {**second, 'presentation': 'inline'})
    two = await inline(app, second, 'version-two')
    with app.clients.bind('one'):
        assert app.canvas_views.canvas(one['viewId'])['content'] == 'Version one'
        assert app.canvas_views.canvas(two['viewId'])['content'] == 'Version two'
    with pytest.raises(AppError, match='existing preview'):
        await inline(app, second, 'duplicate-question-mount')
    # Gallery thumbnail fetches use the same existing short-lived read lease;
    # a late thumbnail between choose and the full question mount is not a viewer.
    image = await published(app, 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=', 'image')
    source = await reference(app, image)
    await command(app, 'canvas.select', {**source, 'presentation': 'inline'})
    thumbnail = (await command(app, 'canvas.views.inline', {'resourceId': image['resourceId'],
        'version': 1, 'messageId': 'inline-message', 'previewId': 'thumbnail'}))['result']
    question = await inline(app, source, 'question-image')
    await command(app, 'canvas.views.release', target(thumbnail))
    assert view(app, question['viewId'])['resourceId'] == image['resourceId']
