from copy import deepcopy
import json

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.server import create_app
from amplifier_web.resource_files import retained_references
from test_service import Runtime
from test_automatic_history import app_factory, native_session, native_rows, files_snapshot


async def conversation(app, title='Original'):
    await app.dispatch('session.create', {'title': title})
    row = app._session()
    app._message(row, 'user', 'Keep the original request', 'text')
    app._message(row, 'assistant', 'Original response', 'text')
    app._publish()
    return row['id']


async def test_archive_restore_survive_restart_without_selection_draft_or_history_changes(tmp_path):
    home = tmp_path / 'app'
    app = AppService(home, workspace=tmp_path)
    sid = await conversation(app)
    await app.dispatch('view.update', {'patch': {'draft': 'unsent correction'}})
    before = deepcopy(app._session())
    await app.app_bridge('dispatch', {'action': 'session.archive', 'args': {'id': sid}}, sid)
    assert app.state['selectedSessionId'] == sid
    assert app.state['view']['draft'] == 'unsent correction'
    assert app._session() == before
    assert app.browser_state()['chatNavigation']['total'] == 0
    await app.dispatch('view.update', {'patch': {'navArchive': 'archived'}})
    assert app.browser_state()['chatNavigation']['items'][0]['id'] == sid
    await app.close()
    restored = AppService(home, workspace=tmp_path)
    try:
        assert sid in restored.state['conversationOrganization']['archived']
        assert restored._session(sid)['messages'] == before['messages']
        await restored.app_bridge('dispatch', {'action': 'session.restore', 'args': {'id': sid}}, sid)
        assert sid not in restored.state['conversationOrganization']['archived']
        assert restored._session(sid)['id'] == sid
        assert restored.state['view']['draft'] == 'unsent correction'
    finally:
        await restored.close()


async def test_collections_and_ordering_preserve_chats_and_validate_exact_ids(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        first, second = await conversation(app, 'First'), await conversation(app, 'Second')
        group = (await app.dispatch('collection.create', {'name': 'Research'}))['result']['id']
        for sid in (first, second):
            await app.app_bridge('dispatch', {'action': 'collection.assign', 'args': {'sessionId': sid, 'id': group}}, second)
        await app.dispatch('view.update', {'patch': {'navCollection': group, 'draft': 'still here'}})
        assert [row['id'] for row in app.browser_state()['chatNavigation']['items']] == [first, second]
        await app.dispatch('collection.order', {'id': group, 'sessionIds': [second, first]})
        assert [row['id'] for row in app.browser_state()['chatNavigation']['items']] == [second, first]
        with pytest.raises(AppError):
            await app.dispatch('collection.order', {'id': group, 'sessionIds': [first]})
        with pytest.raises(AppError):
            await app.dispatch('collection.assign', {'sessionId': first, 'id': group, 'beforeId': 'missing'})
        assert app.state['selectedSessionId'] == second
        assert app.state['view']['draft'] == 'still here'
        await app.dispatch('collection.remove', {'id': group})
        assert {s['id'] for s in app.state['sessions']} == {first, second}
        assert not app.state['conversationOrganization']['collections']
    finally:
        await app.close()


async def test_explicit_pin_order_and_invalid_archive_targets(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        first, second = await conversation(app, 'First'), await conversation(app, 'Second')
        for sid in (first, second):
            await app.dispatch('session.pin', {'id': sid, 'pinned': True})
        await app.dispatch('session.pinOrder', {'ids': [first, second]})
        assert [row['id'] for row in app.browser_state()['chatNavigation']['items']] == [first, second]
        with pytest.raises(AppError):
            await app.dispatch('session.pinOrder', {'ids': [first]})
        app._session(first)['sessionKind'] = 'worker'
        with pytest.raises(AppError):
            await app.dispatch('session.archive', {'id': first})
    finally:
        await app.close()


async def test_snapshot_is_immutable_paged_idempotent_and_retained(tmp_path):
    home = tmp_path / 'app'
    app = AppService(home, workspace=tmp_path)
    sid = await conversation(app)
    app._message(app._session(), 'user', 'Spoken answer', 'call', voiceId='voice-1')
    preview = (await app.dispatch('session.sharePreview', {'sessionId': sid}, command_id='freeze'))['result']
    assert preview['status'] == 'preview' and 'path' not in preview
    assert 'Spoken answer' in preview['text'] and '(voice)' in preview['text']
    app._message(app._session(), 'assistant', 'Later response', 'text')
    repeated = await app.dispatch('session.sharePreview', {'sessionId': sid}, command_id='freeze')
    assert repeated['duplicate'] and repeated['result'] == preview
    args = {'id': preview['id'], 'contentHash': preview['contentHash'], 'visibility': 'anyone_with_link'}
    with pytest.raises(AppError):
        await app.dispatch('session.shareCreate', {**args, 'contentHash': 'wrong'})
    share = (await app.dispatch('session.shareCreate', args, command_id='publish'))['result']
    assert share['status'] == 'shared'
    assert 'Later response' not in app.conversation_library.public_snapshot(share['path'].split('/')[-1])
    parts, offset = [], 0
    while True:
        page = (await app.dispatch('session.shareRead', {'id': preview['id'], 'offset': offset, 'limit': 20}))['result']
        parts.append(page['text'])
        if page['nextOffset'] is None:
            break
        offset = page['nextOffset']
    assert ''.join(parts) == preview['text']
    _, record = app.conversation_library.snapshot(preview['id'])
    assert record['content']['$resource'] in retained_references(app.db, {})
    await app.close()
    restored = AppService(home, workspace=tmp_path)
    try:
        assert restored.conversation_library.public_snapshot(share['path'].split('/')[-1])
        await restored.dispatch('session.shareRevoke', {'id': share['id']})
        assert restored.conversation_library.public_snapshot(share['path'].split('/')[-1]) is None
        repeat = await restored.dispatch('session.shareCreate', args, command_id='publish')
        assert repeat['duplicate']
        assert restored.conversation_library.public_snapshot(share['path'].split('/')[-1]) is None
        with pytest.raises(AppError):
            await restored.dispatch('session.shareCreate', args)
    finally:
        await restored.close()


async def test_anonymous_share_is_inert_expires_and_grants_no_app_access(aiohttp_client, tmp_path):
    app = await create_app(tmp_path / 'app', preload_providers=False, workspace=tmp_path,
        runtime=Runtime(), voice=False, background_updates=False)
    client = await aiohttp_client(app)
    service = app['service']
    sid = await conversation(service, '<script>alert(1)</script>')
    service._message(service._session(), 'user', '<img src="https://tracker.example/x" onerror="alert(1)">', 'text')
    preview = (await service.dispatch('session.sharePreview', {'sessionId': sid}))['result']
    share = (await service.dispatch('session.shareCreate', {'id': preview['id'],
        'contentHash': preview['contentHash'], 'visibility': 'anyone_with_link'}))['result']
    response = await client.get(share['path'])
    assert response.status == 200
    body = await response.text()
    assert '<script>' not in body and '<img ' not in body
    assert '&lt;script&gt;' in body and '&lt;img ' in body
    assert "default-src 'none'" in response.headers['Content-Security-Policy']
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert (await client.get('/api/state')).status == 401
    assert (await client.get('/share/' + 'x' * 43)).status == 404
    assert (await client.post(share['path'], allow_redirects=False)).status != 200
    token, row = service.conversation_library.snapshot(preview['id'])
    row['expiresAt'] = 1
    service.conversation_library.save(row, token)
    service.db.commit()
    assert (await client.get(share['path'])).status == 404


async def test_large_catalog_projection_keeps_only_visible_membership(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        sid = await conversation(app)
        value = app.state['conversationOrganization']
        value['archived'] = {f'old-{i}': i for i in range(5000)}
        value['collections'] = [{'id': 'group', 'name': 'Large', 'sessionIds': [sid, *value['archived']]}]
        app._publish()
        projected = app.browser_state()['conversationOrganization']
        assert projected['archivedCount'] == 5000
        assert projected['archived'] == {}
        assert projected['collections'][0]['sessionIds'] == [sid]
        assert len(json.dumps(projected)) < 500
    finally:
        await app.close()


async def test_archived_native_chat_survives_catalog_refresh_without_shared_writes(app_factory, tmp_path):
    workspace = tmp_path / 'cli-project'
    native = native_session(workspace, 'cli-root')
    before = files_snapshot(native)
    app = app_factory()
    await app.history.refresh()
    sid = native_rows(app)[0]['id']
    await app.dispatch('session.archive', {'id': sid})
    collection = (await app.dispatch('collection.create', {'name': 'CLI archive'}))['result']['id']
    await app.dispatch('collection.assign', {'sessionId': sid, 'id': collection})
    await app.history.refresh()
    assert sid in app.state['conversationOrganization']['archived']
    assert files_snapshot(native) == before
    assert app.runtime.started == app.runtime.sent == app.runtime.stopped == []
    home = app.data_dir
    await app.close()
    restored = app_factory(home=home)
    assert restored._session(sid)['nativeIdentity'] == 'cli-root'
    await restored.history.refresh()
    await restored.dispatch('session.restore', {'id': sid})
    assert files_snapshot(native) == before
    assert restored.runtime.started == restored.runtime.sent == restored.runtime.stopped == []
