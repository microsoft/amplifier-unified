from copy import deepcopy

import pytest

from amplifier_web.service import AppError
from test_canvas_views import app, command, show, target, view


async def published(app, content='First version', kind='text'):
    with app.clients.bind('one'):
        app._session(app.state['selectedSessionId'])['messages'].append(
            {'id': 'inline-message', 'role': 'user', 'text': 'Create an artifact'})
    return await show(app, kind, content)


async def inline(app, artifact, **extra):
    result = await command(app, 'canvas.views.inline', {
        'resourceId': artifact['resourceId'], 'messageId': 'inline-message', 'version': 1, 'previewId': 'test-preview', **extra})
    return result['result']


async def test_inline_exact_version_shares_identity_without_retargeting_canvas(app):
    first = await published(app)
    opened = await inline(app, first)
    await command(app, 'canvas.versions.revise', {'id': first['resourceId'], 'expectedRevision': 1, 'content': 'New version'})
    second = await show(app, 'text', 'A different selected artifact')
    with app.clients.bind('one'):
        assert app.canvas_views.canvas(opened['viewId'])['content'] == 'First version'
        assert app.canvas_views.canvas(opened['viewId'])['selectedVersion'] == 1
    await command(app, 'canvas.views.command', {**target(opened), 'action': 'canvas.view', 'args': {'patch': {'source': True}}})
    assert view(app)['resourceId'] == second['resourceId']
    assert not view(app)['view'].get('source')
    assert view(app, opened['viewId'])['view']['source']
    await command(app, 'canvas.select', {'id': opened['resourceId'], 'version': 1})
    with app.clients.bind('one'):
        assert app.canvas_views.canvas('primary')['content'] == 'First version'


async def test_inline_scope_publication_and_stale_targets(app):
    first = await published(app)
    with pytest.raises(AppError, match='published'):
        await inline(app, first, messageId='not-this-message')
    opened = await inline(app, first)
    with pytest.raises(AppError, match='Unknown'):
        await command(app, 'canvas.views.command', {**target(opened), 'action': 'canvas.view', 'args': {'patch': {}}}, client='two')
    await command(app, 'canvas.views.release', target(opened))
    with pytest.raises(AppError, match='Unknown'):
        await command(app, 'canvas.views.command', {**target(opened), 'action': 'canvas.view', 'args': {'patch': {}}})
    reopened = await inline(app, first)
    assert reopened['viewId'] != opened['viewId']
    await command(app, 'session.create')
    with pytest.raises(AppError, match='conversation'):
        await inline(app, first)
    assert not any(row['viewId'] == reopened['viewId'] for row in (await command(app, 'canvas.views.inspect'))['result']['views'])


async def test_inline_dirty_guard_and_bounded_release(app):
    first = await published(app)
    original = deepcopy(app.state['canvasArtifacts'])
    opened = await inline(app, first)
    assert (await inline(app, first))['viewId'] == opened['viewId']
    await command(app, 'canvas.views.dirty', {**target(opened), 'dirty': True})
    with pytest.raises(AppError, match='edit'):
        await command(app, 'canvas.views.release', target(opened))
    with pytest.raises(AppError, match='edit'):
        await command(app, 'session.create')
    await command(app, 'canvas.views.dirty', {**target(opened), 'dirty': False})
    assert app.state['canvasArtifacts'] == original
    for index in range(7):
        item = await show(app, 'text', 'Artifact ' + str(index))
        await inline(app, item)
    extra = await show(app, 'text', 'Ninth artifact')
    with pytest.raises(AppError, match='Close a preview'):
        await inline(app, extra)
    await command(app, 'canvas.views.release', target(opened))
    await inline(app, extra)
    with app.clients.bind('one'):
        record = app.canvas_views.record()
        assert len(record['inline']) == 8
        assert not any(key.startswith(opened['viewId']) for key in record['preferences'])


async def test_late_unmount_cannot_close_replacement_preview(app):
    first = await published(app)
    old = await inline(app, first)
    replacement = await inline(app, first, previewId='replacement-mount')
    await command(app, 'canvas.views.release', target(old))
    assert view(app, replacement['viewId'])['resourceId'] == first['resourceId']
    app.clients.attach('reload', resume='one')
    with app.clients.bind('reload'):
        assert not app.canvas_views.record().get('inline')


async def test_followup_attaches_exact_saved_image_without_sending_or_replacing_draft(app):
    import base64
    from test_output_images import png
    image = png(2, 2)
    first = await published(app, 'data:image/png;base64,' + base64.b64encode(image).decode(), 'image')
    opened = await inline(app, first)
    with app.clients.bind('one'):
        app.clients.draft(app.state['selectedSessionId'], 'Keep my unsent instructions')
        original = deepcopy(app._session(app.state['selectedSessionId'])['messages'])
    first_result = await command(app, 'canvas.views.imageDraft', target(opened), command_id='attach-once')
    duplicate = await command(app, 'canvas.views.imageDraft', target(opened), command_id='attach-once')
    assert duplicate['duplicate']
    assert duplicate['result'] == first_result['result']
    with app.clients.bind('one'):
        assert app.state['view']['draft'] == 'Keep my unsent instructions'
        session = app._session(app.state['selectedSessionId'])
        assert session['messages'] == original
        attachments = app.clients.attachments(session)
        assert len(attachments) == 1 and attachments[0]['name'] == 'canvas.png'
        from amplifier_web.attachments import location
        assert (location(app.data_dir, attachments[0]['id']) / 'content').read_bytes() == image
    with app.clients.bind('two'):
        assert not app.clients.attachments(app._session(app.state['selectedSessionId']))
