"""A client can configure a new chat without creating history or starting work."""
import base64
import copy
import asyncio
from pathlib import Path
import threading

import pytest

from amplifier_web.service import AppError, AppService
from test_service import Runtime
from test_live_clients import command, snapshot


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    for client in ('web', 'agent'):
        service.clients.attach(client, kind='web' if client == 'web' else 'api')
    yield service
    if not getattr(service, "test_closed", False):
        await service.close()


async def test_setup_is_private_durable_and_does_not_create_a_session(app, tmp_path):
    setup = {'workspace': str(tmp_path / 'not-chosen-yet'), 'title': 'Planned work',
             'bundle': 'custom', 'selection': {'instance': 'provider-a', 'model': 'model-b'}}
    await command(app, 'web', 'session.draft')
    await command(app, 'web', 'view.update', {'patch': {'newSessionDraft': setup, 'draft': 'For later'}})
    await command(app, 'web', 'attachment.add', {'sessionId': None, 'name': 'notes.txt', 'base64': base64.b64encode(b'Keep these notes').decode()})
    for _ in range(3):
        await command(app, 'web', 'session.draft')
    state = snapshot(app, 'web')
    assert state['sessions'] == [] and state['selectedSessionId'] is None
    assert state['view']['newSessionDraft'] == setup
    assert state['view']['draft'] == 'For later'
    assert state['draftAttachments'][0]['name'] == 'notes.txt'
    other = snapshot(app, 'agent')
    assert not other['view'].get('newSessionDraft') and other['draftAttachments'] == []
    assert not app.runtime.started and not app.runtime.sent
    assert not (tmp_path / 'not-chosen-yet').exists()
    assert not list((tmp_path / 'amplifier-home').rglob('transcript.jsonl'))
    app.clients.attach('reload', resume='web')
    assert snapshot(app, 'reload')['view']['newSessionDraft'] == setup
    await app.close()
    app.test_closed = True
    restored = AppService(app.data_dir, Runtime(), workspace=tmp_path)
    try:
        state = snapshot(restored, 'web')
        assert state['view']['newSessionDraft'] == setup and state['view']['draft'] == 'For later'
        assert state['draftAttachments'][0]['name'] == 'notes.txt'
        assert not state['sessions']
    finally:
        await restored.close()


async def test_first_submission_uses_chosen_setup_and_transfers_draft_once(app, tmp_path):
    project = tmp_path / 'project'
    setup = {'workspace': str(project), 'title': 'My chosen name', 'bundle': 'chosen-bundle',
             'selection': {'instance': 'provider-a', 'model': 'chosen-model', 'effort': 'high'}}
    await command(app, 'web', 'session.draft')
    await command(app, 'web', 'view.update', {'patch': {'newSessionDraft': setup, 'draft': 'Next thought'}})
    added = await command(app, 'web', 'attachment.add', {'sessionId': None, 'name': 'notes.txt', 'base64': 'bm90ZXM='})
    attachment = added['state']['draftAttachments'][0]
    created = await command(app, 'web', 'session.create', {**setup, 'fromDraft': True}, command_id='first-creation')
    sid = created['sessionId']
    session = next(row for row in created['state']['sessions'] if row['id'] == sid)
    assert project.is_dir()
    assert session['selection'] == setup['selection'] and session['bundle'] == setup['bundle']
    assert session['workspace'] == str(project) and session['title'] == setup['title']
    assert session['draft'] == 'Next thought' and session['draftAttachments'] == [attachment]
    assert created['state']['draftAttachments'] == []
    assert 'newSessionDraft' not in created['state']['view']
    await command(app, 'web', 'session.draft')
    app.clients.attach('reloaded-sender', resume='web')
    duplicate = await command(app, 'reloaded-sender', 'session.create', {**setup, 'fromDraft': True}, command_id='first-creation')
    assert duplicate['duplicate'] and duplicate['sessionId'] == sid
    assert len(app.state['sessions']) == 1
    # A replayed creation receipt identifies its original chat even after navigation.
    assert duplicate['state']['selectedSessionId'] is None
    await command(app, 'web', 'conversation.send', {'sessionId': sid, 'text': 'Start here', 'attachmentIds': [attachment['id']], 'preserveDraft': True}, command_id='first-message')
    await command(app, 'web', 'conversation.send', {'sessionId': sid, 'text': 'Start here', 'attachmentIds': [attachment['id']], 'preserveDraft': True}, command_id='first-message')
    assert len(app.runtime.sent) == 1 and app.runtime.sent[0][0] == sid
    assert app._session(sid)['title'] == 'My chosen name'
    assert snapshot(app, 'web')['selectedSessionId'] is None


async def test_delayed_upload_stays_with_unsent_chat_after_navigation(app, tmp_path):
    existing = await command(app, 'web', 'session.create', {'title': 'Existing'})
    sid = existing['sessionId']
    await command(app, 'web', 'view.update', {'patch': {'draft': 'Existing unsent text'}})
    await command(app, 'web', 'session.draft')
    await command(app, 'web', 'view.update', {'patch': {'draft': 'Future chat'}})
    await command(app, 'web', 'session.select', {'id': sid})
    added = await command(app, 'web', 'attachment.add', {'sessionId': None, 'name': 'future.txt', 'base64': 'ZnV0dXJl'})
    assert added['state']['sessions'][0]['draftAttachments'] == []
    assert added['state']['view']['draft'] == 'Existing unsent text'
    aid = added['state']['draftAttachments'][0]['id']
    await command(app, 'web', 'attachment.remove', {'sessionId': None, 'id': aid})
    assert snapshot(app, 'web')['draftAttachments'] == []
    await command(app, 'web', 'session.draft')
    assert snapshot(app, 'web')['view']['draft'] == 'Future chat'


@pytest.mark.parametrize('setup', [{'workspace': ''}, {'selection': {'instance': 'missing-model'}}, {'selection': {'instance': '', 'model': ''}}])
async def test_invalid_first_creation_preserves_config_and_text(app, setup):
    setup = {'workspace': str(app.data_dir.parent), **setup}
    await command(app, 'web', 'session.draft')
    await command(app, 'web', 'view.update', {'patch': {'newSessionDraft': setup, 'draft': 'Keep me'}})
    with pytest.raises(AppError):
        await command(app, 'web', 'session.create', {**setup, 'fromDraft': True})
    state = snapshot(app, 'web')
    assert not state['sessions'] and state['view']['newSessionDraft'] == setup
    assert state['view']['draft'] == 'Keep me'


async def test_legacy_launcher_and_agent_share_draft_and_dirty_canvas_guard(app):
    created = await command(app, 'agent', 'session.create', {})
    sid = created['sessionId']
    await command(app, 'agent', 'view.update', {'patch': {'panel': 'new-session'}})
    assert snapshot(app, 'agent')['selectedSessionId'] is None
    assert snapshot(app, 'agent')['view']['panel'] is None
    assert len(app.state['sessions']) == 1
    await command(app, 'agent', 'session.select', {'id': sid})
    shown = await command(app, 'agent', 'canvas.show', {'kind': 'markdown', 'content': 'Keep editor draft'})
    aid = shown['state']['canvas']['id']
    record = app.clients.records['agent']
    record['canvasViews'] = {'preferences': {'primary:' + aid: {'dirty': True}}}
    before = copy.deepcopy(record)
    with pytest.raises(AppError, match='viewer edit'):
        await command(app, 'agent', 'session.draft')
    assert record['selectedSessionId'] == sid and record['canvas'] == before['canvas']


@pytest.mark.parametrize('client,select', [('web', True), ('agent', False)])
async def test_explicit_workspace_creation_is_shared_and_preserves_existing_contents(app, tmp_path, client, select):
    folder = tmp_path / 'new-parent' / 'project'
    args = {'workspace': str(folder), 'select': select, 'title': 'A fresh chat'}
    created = await command(app, client, 'session.create', args, command_id='make-folder')
    assert folder.is_dir()
    assert app._session(created['sessionId'])['workspace'] == str(folder)
    assert created['state']['selectedSessionId'] == (created['sessionId'] if select else None)
    assert not app.runtime.started and not app.runtime.sent
    (folder / 'keep.txt').write_text('Existing work')
    second = await command(app, client, 'session.create', args, command_id='another-chat')
    assert second['sessionId'] != created['sessionId']
    assert (folder / 'keep.txt').read_text() == 'Existing work'


async def test_implicit_missing_workspace_is_not_recreated(app, tmp_path, monkeypatch):
    from amplifier_web import workspace_canvas
    folder = tmp_path / 'removed-workspace'
    folder.mkdir()
    await command(app, 'web', 'workspace.add', {'path': str(folder)})
    folder.rmdir()
    def unexpected(path):
        raise AssertionError('Only an explicit workspace authorizes folder creation')
    monkeypatch.setattr(workspace_canvas, '_create_workspace_folder', unexpected)
    with pytest.raises(AppError, match='folder'):
        await command(app, 'web', 'session.create', {})
    assert not folder.exists() and not app.state['sessions']


@pytest.mark.parametrize('invalid', ['selection', 'identity', 'existing-identity', 'inheritance', 'stale', 'pending-progress', 'dirty-canvas'])
async def test_rejected_create_does_not_attempt_filesystem_creation(app, tmp_path, monkeypatch, invalid):
    from amplifier_web import workspace_canvas
    first = await command(app, 'web', 'session.create', {'title': 'Existing'})
    sid = first['sessionId']
    args = {'workspace': str(tmp_path / 'must-not-be-created'), 'fromDraft': True}
    kwargs = {}
    if invalid == 'selection': args['selection'] = {'instance': 'missing-model'}
    elif invalid == 'identity': args['id'] = 'not-a-uuid'
    elif invalid == 'existing-identity': args['id'] = sid
    elif invalid == 'inheritance': args['inheritConfiguration'] = {'sessionId': sid, 'configurationHash': 'not-reviewed'}
    elif invalid == 'stale': kwargs['expected_revision'] = app.state['revision'] - 1
    elif invalid == 'pending-progress':
        kwargs['expected_revision'] = app.state['revision']
        await app.on_runtime_event('assistant.delta', {'sessionId': sid, 'text': 'Deferred progress'})
        assert app._progress_dirty
    else:
        shown = await command(app, 'web', 'canvas.show', {'kind': 'markdown', 'content': 'Unsaved editor'})
        aid = shown['state']['canvas']['id']
        app.clients.records['web']['canvasViews'] = {'preferences': {'primary:' + aid: {'dirty': True}}}
    before = copy.deepcopy(app.clients.records['web'])
    def unexpected(path):
        raise AssertionError('An invalid request must not attempt mkdir')
    monkeypatch.setattr(workspace_canvas, '_create_workspace_folder', unexpected)
    with pytest.raises(AppError):
        await command(app, 'web', 'session.create', args, command_id='rejected-create', **kwargs)
    assert not Path(args['workspace']).exists()
    assert len(app.state['sessions']) == 1
    assert app.clients.records['web']['selectedSessionId'] == before['selectedSessionId']
    assert app.clients.records['web']['drafts'] == before['drafts']
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', ('rejected-create',)).fetchone()


async def test_duplicate_receipt_and_reused_command_do_not_touch_workspace(app, tmp_path, monkeypatch):
    from amplifier_web import workspace_canvas
    folder = tmp_path / 'created'
    args = {'workspace': str(folder), 'fromDraft': True}
    first = await command(app, 'web', 'session.create', args, command_id='same-create')
    folder.rmdir()
    def unexpected(path):
        raise AssertionError('A recorded command must not recreate its workspace')
    monkeypatch.setattr(workspace_canvas, '_create_workspace_folder', unexpected)
    duplicate = await command(app, 'web', 'session.create', args, command_id='same-create', expected_revision=-1)
    assert duplicate['duplicate'] and duplicate['sessionId'] == first['sessionId']
    with pytest.raises(AppError, match='different contents'):
        await command(app, 'web', 'session.create', {'workspace': str(tmp_path / 'other')}, command_id='same-create')
    assert not folder.exists() and not (tmp_path / 'other').exists()
    assert len(app.state['sessions']) == 1


@pytest.mark.parametrize('invalid', ['file', 'parent-file', 'permission'])
async def test_workspace_creation_failure_keeps_unsent_chat(app, tmp_path, monkeypatch, invalid):
    folder = tmp_path / 'new-project'
    if invalid == 'file': folder.write_text('Keep existing file')
    elif invalid == 'parent-file':
        folder.write_text('Keep existing file')
        folder /= 'child'
    else:
        mkdir = Path.mkdir
        def deny(path, *args, **kwargs):
            if path == folder: raise PermissionError('Fixture denied')
            return mkdir(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'mkdir', deny)
    setup = {'workspace': str(folder), 'title': 'Unsent chat'}
    await command(app, 'web', 'session.draft')
    await command(app, 'web', 'view.update', {'patch': {'newSessionDraft': setup, 'draft': 'Keep my input'}})
    with pytest.raises(AppError):
        await command(app, 'web', 'session.create', {**setup, 'fromDraft': True}, command_id='folder-failure')
    state = snapshot(app, 'web')
    assert not state['sessions']
    assert state['view']['newSessionDraft'] == setup and state['view']['draft'] == 'Keep my input'
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', ('folder-failure',)).fetchone()


@pytest.mark.parametrize('conflicting', [False, True])
async def test_concurrent_create_retries_share_receipt_without_blocking_host(app, tmp_path, monkeypatch, conflicting):
    from amplifier_web import workspace_canvas
    create = workspace_canvas._create_workspace_folder
    loop, started, release = asyncio.get_running_loop(), asyncio.Event(), threading.Event()
    calls = []
    def slow_create(path):
        assert threading.current_thread() is not threading.main_thread()
        assert not app.lock.locked()
        calls.append(path)
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return create(path)
    monkeypatch.setattr(workspace_canvas, '_create_workspace_folder', slow_create)
    folder = tmp_path / 'async-create'
    args = {'workspace': str(folder), 'fromDraft': True}
    first = asyncio.create_task(command(app, 'web', 'session.create', args, command_id='racing-create'))
    retry = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        retry_args = {**args, 'workspace': str(tmp_path / 'different')} if conflicting else args
        retry = asyncio.create_task(command(app, 'web', 'session.create', retry_args, command_id='racing-create'))
        # A slow filesystem must not hold the service lock or block other actions.
        await asyncio.wait_for(command(app, 'agent', 'view.update', {'patch': {'scheme': 'dark'}}), 2)
        assert not folder.exists() and calls == [str(folder)]
    finally:
        release.set()
        results = await asyncio.gather(first, *([retry] if retry else []), return_exceptions=True)
    assert not isinstance(results[0], BaseException), results[0]
    if conflicting:
        assert isinstance(results[1], AppError) and 'different contents' in str(results[1])
        assert not (tmp_path / 'different').exists()
    else:
        assert results[1]['duplicate'] and results[1]['sessionId'] == results[0]['sessionId']
    assert calls == [str(folder)] and len(app.state['sessions']) == 1


async def test_revision_change_during_mkdir_rejects_chat_without_removing_created_folder(app, tmp_path, monkeypatch):
    from amplifier_web import workspace_canvas
    create = workspace_canvas._create_workspace_folder
    loop, started, release = asyncio.get_running_loop(), asyncio.Event(), threading.Event()
    folder = tmp_path / 'created-before-recheck'
    def slow_create(path):
        result = create(path)
        (result / 'other-process.txt').write_text('Keep concurrent work')
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return result
    monkeypatch.setattr(workspace_canvas, '_create_workspace_folder', slow_create)
    await command(app, 'web', 'session.draft')
    await command(app, 'web', 'view.update', {'patch': {'draft': 'Still unsent'}})
    pending = asyncio.create_task(command(app, 'web', 'session.create', {'workspace': str(folder), 'fromDraft': True}, command_id='changed-during-create', expected_revision=app.state['revision']))
    try:
        await asyncio.wait_for(started.wait(), 2)
        await command(app, 'agent', 'view.update', {'patch': {'scheme': 'dark'}})
    finally:
        release.set()
        result = (await asyncio.gather(pending, return_exceptions=True))[0]
    assert isinstance(result, AppError) and 'app changed' in str(result)
    assert (folder / 'other-process.txt').read_text() == 'Keep concurrent work'
    assert not app.state['sessions'] and snapshot(app, 'web')['view']['draft'] == 'Still unsent'
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', ('changed-during-create',)).fetchone()
