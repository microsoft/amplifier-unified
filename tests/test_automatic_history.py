"""Shared CLI browsing is automatic, lazy, and never executes conversation work."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.session_files import amplifier_home, project_slug


class ObservedRuntime:
    def __init__(self):
        self.started = []
        self.sent = []
        self.stopped = []

    async def start(self, session, emit):
        self.started.append(session['id'])

    async def send(self, session, text, input_id, emit):
        self.sent.append((session['id'], text))

    async def stop(self, identity):
        self.stopped.append(identity)

    async def close(self):
        pass


@pytest.fixture
async def app_factory(tmp_path, monkeypatch):
    apps = []

    def create(*, workspace=None, home=None):
        workspace = workspace or tmp_path / 'web-workspace'
        workspace.mkdir(parents=True, exist_ok=True)
        app = AppService(home or tmp_path / 'web-app', ObservedRuntime(), workspace=workspace)
        app.observed_diagnostics = []

        def record(stream, event, **kwargs):
            app.observed_diagnostics.append((stream, copy.deepcopy(event), kwargs))

        monkeypatch.setattr(app.diagnostics, 'record', record)
        apps.append(app)
        return app

    yield create
    for app in reversed(apps):
        if not app.closed:
            await app.close()


def native_session(workspace, identity, messages=None, *, metadata=None, folder=True):
    if folder:
        workspace.mkdir(parents=True, exist_ok=True)
    directory = amplifier_home() / 'projects' / project_slug(workspace) / 'sessions' / identity
    directory.mkdir(parents=True, exist_ok=True)
    messages = messages if messages is not None else [
        {'role': 'user', 'content': 'Saved CLI question'},
        {'role': 'assistant', 'content': 'Saved CLI answer'},
    ]
    (directory / 'transcript.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in messages))
    value = {'session_id': identity, 'working_dir': str(workspace), 'bundle': 'bundle:anchors',
             'name': 'CLI conversation ' + identity, 'created': '2026-01-01T12:00:00Z',
             'turn_count': sum(row.get('role') == 'user' for row in messages)}
    value.update(metadata or {})
    (directory / 'metadata.json').write_text(json.dumps(value))
    capture = directory / 'context-intelligence'
    capture.mkdir()
    (capture / 'metadata.json').write_text(json.dumps({
        'format': 'context-intelligence', 'version': '1.0.0', 'session_id': identity,
        'workspace': project_slug(workspace), 'working_dir': str(workspace),
        'started_at': '2026-01-01T12:00:00Z', 'status': 'completed',
    }))
    (capture / 'events.jsonl').write_text(json.dumps({'event': 'prompt:complete', 'data': {'response': 'Saved CLI answer'}}) + '\n')
    return directory


def files_snapshot(directory):
    return {str(path.relative_to(directory)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in directory.rglob('*') if path.is_file()}


def native_rows(app):
    return [row for row in app.get_state()['sessions'] if row.get('nativeIdentity')]


async def finish_actions(app):
    while app.tasks:
        await asyncio.wait_for(asyncio.gather(*list(app.tasks)), timeout=5)


async def test_workspace_availability_tracks_real_folders_without_removing_history(tmp_path, app_factory):
    native = tmp_path / 'cli' / 'same-name'
    saved = native_session(native, 'cli-root')
    app = app_factory(workspace=tmp_path / 'web' / 'same-name')
    web = Path(app.state['settings']['workspace'])
    original_files = files_snapshot(saved)
    await app.history.refresh()
    assert all(row['available'] for row in app.state['workspaces'])
    before = [(row['id'], row['path']) for row in app.state['workspaces']]
    native.rmdir()
    web.rmdir()
    web.write_text('A file is not a workspace directory')
    await app.history.refresh()
    assert not any(row['available'] for row in app.state['workspaces'])
    assert [(row['id'], row['path']) for row in app.state['workspaces']] == before
    assert len(native_rows(app)) == 1
    native.mkdir()
    web.unlink()
    web.mkdir()
    await app.history.refresh()
    assert all(row['available'] for row in app.state['workspaces'])
    assert files_snapshot(saved) == original_files


async def select(app, identity):
    await app.dispatch('session.select', {'id': identity})
    await finish_actions(app)
    return next(row for row in app.get_state()['sessions'] if row['id'] == identity)


async def test_projects_and_chats_appear_automatically_without_loading_transcripts(tmp_path, app_factory, monkeypatch):
    workspace = tmp_path / 'cli-project with-hyphens'
    native_session(workspace, 'cli-root')
    child = native_session(workspace, 'cli-root_foundation:explorer', metadata={'parent_id': 'cli-root'})
    app = app_factory()
    original = Path.open

    def no_transcript_reads(path, *args, **kwargs):
        assert path.name != 'transcript.jsonl', 'Discovery eagerly read a transcript'
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', no_transcript_reads)
    await app.history.refresh()
    assert app.state['sharedHistory']['error'] is None
    registrations = [row for row in app.state['workspaces'] if row.get('nativeProject') == project_slug(workspace)]
    assert len(registrations) == 1
    assert registrations[0]['path'] == str(workspace)
    rows = native_rows(app)
    assert {row['nativeIdentity'] for row in rows} == {'cli-root', child.name}
    assert all(row['messages'] == [] and row['historyLoaded'] is False for row in rows)
    assert all(row['runtimeSessionId'] == row['nativeIdentity'] for row in rows)
    assert not app.runtime.started and not app.runtime.sent


async def test_duplicate_native_ids_in_different_workspaces_remain_separate(tmp_path, app_factory):
    workspaces = [tmp_path / 'first-project', tmp_path / 'second-project']
    for number, workspace in enumerate(workspaces):
        native_session(workspace, 'same-native-id', [{'role': 'user', 'content': f'Question in project {number}'}])
    app = app_factory()
    await app.history.refresh()
    rows = native_rows(app)
    assert len(rows) == 2
    assert len({row['id'] for row in rows}) == 2
    assert {row['runtimeSessionId'] for row in rows} == {'same-native-id'}
    for number, workspace in enumerate(workspaces):
        row = next(row for row in rows if row['workspace'] == str(workspace))
        loaded = await select(app, row['id'])
        assert [message['text'] for message in loaded['messages']] == [f'Question in project {number}']
    await app.history.refresh()
    assert {row['id'] for row in native_rows(app)} == {row['id'] for row in rows}


async def test_selected_history_loads_a_page_and_earlier_pages_keep_message_ids(tmp_path, app_factory):
    messages = [{'role': 'system', 'content': 'Internal instructions'}]
    messages.extend({'role': 'user' if number % 2 == 0 else 'assistant',
                     'content': [{'type': 'text', 'text': f'Message {number}'}]}
                    for number in range(205))
    messages.insert(10, {'role': 'tool', 'content': 'Internal tool result'})
    native_session(tmp_path / 'cli', 'long-history', messages)
    native_session(tmp_path / 'cli', 'unopened-history')
    app = app_factory()
    await app.history.refresh()
    selected = next(row for row in native_rows(app) if row['nativeIdentity'] == 'long-history')
    first = await select(app, selected['id'])
    assert first['sharedHistoryTotal'] == 205
    assert first['sharedHistoryOffset'] == 105
    assert [row['text'] for row in first['messages']] == [f'Message {number}' for number in range(105, 205)]
    first_ids = {row['text']: row['id'] for row in first['messages']}
    assert next(row for row in native_rows(app) if row['nativeIdentity'] == 'unopened-history')['messages'] == []

    await app.dispatch('session.history', {'id': selected['id'], 'before': first['sharedHistoryOffset']})
    await finish_actions(app)
    second = app._session(selected['id'])
    assert second['sharedHistoryOffset'] == 5
    assert len(second['messages']) == 200
    assert all(row['id'] == first_ids[row['text']] for row in second['messages'] if row['text'] in first_ids)
    await app.history.load(selected['id'], before=second['sharedHistoryOffset'])
    complete = app._session(selected['id'])
    assert complete['sharedHistoryOffset'] == 0
    assert [row['text'] for row in complete['messages']] == [f'Message {number}' for number in range(205)]
    assert len({row['id'] for row in complete['messages']}) == 205


async def test_external_cli_append_refreshes_the_selected_chat_without_replay(tmp_path, app_factory):
    directory = native_session(tmp_path / 'cli', 'updated-chat')
    app = app_factory()
    await app.history.refresh()
    row = native_rows(app)[0]
    loaded = await select(app, row['id'])
    original_ids = [message['id'] for message in loaded['messages']]
    with (directory / 'transcript.jsonl').open('a') as stream:
        stream.write(json.dumps({'role': 'user', 'content': 'New CLI turn'}) + '\n')
        stream.write(json.dumps({'role': 'assistant', 'content': 'New CLI response'}) + '\n')
    await app.history.refresh()
    refreshed = app._session(row['id'])
    assert [message['text'] for message in refreshed['messages']][-2:] == ['New CLI turn', 'New CLI response']
    assert [message['id'] for message in refreshed['messages']][:2] == original_ids
    assert not app.runtime.started and not app.runtime.sent


async def test_browsing_preserves_native_files_and_emits_no_prompt_events(tmp_path, app_factory):
    directory = native_session(tmp_path / 'cli', 'read-only-browse')
    original = files_snapshot(directory)
    app = app_factory()
    await app.history.refresh()
    row = native_rows(app)[0]
    await select(app, row['id'])
    await app.dispatch('session.history', {'id': row['id']})
    await finish_actions(app)
    await app.history.refresh()
    assert files_snapshot(directory) == original
    assert not app.runtime.started and not app.runtime.sent
    assert not any(event['event'] in {'prompt:submit', 'prompt:complete'}
                   for _, event, _ in app.observed_diagnostics)
    assert all(kwargs.get('session_id') != row['nativeIdentity'] for _, _, kwargs in app.observed_diagnostics)
    assert not (directory / 'unified' / 'view.json').exists()
    assert not list((app.data_dir / 'sessions').glob('*/transcript.jsonl'))


async def test_restart_persists_only_a_lazy_index_not_chat_bodies_or_native_views(tmp_path, app_factory):
    text = 'This unique transcript text must stay out of the app state database.'
    directory = native_session(tmp_path / 'cli', 'restart-chat', [{'role': 'user', 'content': text}])
    original = files_snapshot(directory)
    app = app_factory()
    await app.history.refresh()
    row = native_rows(app)[0]
    loaded = await select(app, row['id'])
    message_id = loaded['messages'][0]['id']
    stored_text = app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0]
    assert text not in stored_text
    assert not (directory / 'unified' / 'view.json').exists()
    await app.close()
    restored = app_factory()
    indexed = native_rows(restored)[0]
    assert indexed['id'] == row['id']
    assert indexed['nativeIdentity'] == 'restart-chat'
    assert indexed['messages'] == []
    assert indexed['historyLoaded'] is False
    await restored.history.refresh()
    assert restored._session(row['id'])['messages'][0]['id'] == message_id
    assert restored._session(row['id'])['messages'][0]['text'] == text
    assert files_snapshot(directory) == original
    assert not restored.runtime.started and not restored.runtime.sent


async def test_hidden_chats_and_workspaces_are_not_resurrected_by_refresh_or_restart(tmp_path, app_factory):
    chat_dir = native_session(tmp_path / 'chat-workspace', 'hide-chat')
    workspace_dir = native_session(tmp_path / 'hidden-workspace', 'workspace-chat')
    original_chat, original_workspace = files_snapshot(chat_dir), files_snapshot(workspace_dir)
    app = app_factory()
    await app.history.refresh()
    hidden_chat = next(row for row in native_rows(app) if row['nativeIdentity'] == 'hide-chat')
    hidden_workspace = next(row for row in app.state['workspaces'] if row.get('path') == str(tmp_path / 'hidden-workspace'))
    await app.dispatch('session.delete', {'id': hidden_chat['id']})
    await app.dispatch('workspace.remove', {'id': hidden_workspace['id']})
    await finish_actions(app)
    await app.history.refresh()
    assert not any(row['id'] == hidden_chat['id'] for row in native_rows(app))
    assert not any(row['id'] == hidden_workspace['id'] for row in app.state['workspaces'])
    await app.close()
    restored = app_factory()
    await restored.history.refresh()
    assert not any(row['id'] == hidden_chat['id'] for row in native_rows(restored))
    assert not any(row['id'] == hidden_workspace['id'] for row in restored.state['workspaces'])
    assert files_snapshot(chat_dir) == original_chat
    assert files_snapshot(workspace_dir) == original_workspace


@pytest.mark.parametrize('kind', ['missing-folder', 'unresolved-folder', 'colon-child'])
async def test_unavailable_and_worker_chats_are_readable_but_cannot_start_work(tmp_path, app_factory, kind):
    workspace = tmp_path / kind
    identity = 'root_foundation:explorer' if kind == 'colon-child' else 'read-only-root'
    metadata = {'parent_id': 'parent-root'} if kind == 'colon-child' else {}
    if kind == 'unresolved-folder':
        metadata['working_dir'] = '/an/unrelated/path'
    directory = native_session(workspace, identity, folder=kind == 'colon-child', metadata=metadata)
    if kind == 'unresolved-folder':
        capture_path = directory / 'context-intelligence' / 'metadata.json'
        capture = json.loads(capture_path.read_text())
        capture['working_dir'] = '/an/unrelated/path'
        capture_path.write_text(json.dumps(capture))
    original = files_snapshot(directory)
    app = app_factory()
    await app.history.refresh()
    row = native_rows(app)[0]
    loaded = await select(app, row['id'])
    assert loaded['historyReadOnlyReason']
    assert [message['text'] for message in loaded['messages']] == ['Saved CLI question', 'Saved CLI answer']
    with pytest.raises(AppError):
        await app.dispatch('conversation.send', {'sessionId': row['id'], 'text': 'Must not execute'})
    assert not app.runtime.started and not app.runtime.sent
    assert files_snapshot(directory) == original


async def test_partial_jsonl_keeps_visible_messages_and_reports_a_retryable_error(tmp_path, app_factory):
    directory = native_session(tmp_path / 'cli', 'partial-save')
    app = app_factory()
    await app.history.refresh()
    row = native_rows(app)[0]
    first = await select(app, row['id'])
    original = copy.deepcopy(first['messages'])
    transcript = directory / 'transcript.jsonl'
    complete_bytes = transcript.read_bytes()
    with transcript.open('a') as stream:
        stream.write('{"role":"user","content":')
    partial_bytes, partial_mtime = transcript.read_bytes(), transcript.stat().st_mtime_ns
    await app.history.refresh()
    failed = app._session(row['id'])
    assert failed['historyError']
    assert failed['historyLoading'] is False
    assert failed['messages'] == original
    assert transcript.read_bytes() == partial_bytes
    assert transcript.stat().st_mtime_ns == partial_mtime
    transcript.write_bytes(complete_bytes + b'{"role":"user","content":"Completed CLI append"}\n')
    await app.history.refresh()
    recovered = app._session(row['id'])
    assert recovered['historyError'] is None
    assert recovered['messages'][-1]['text'] == 'Completed CLI append'
    assert not app.runtime.started and not app.runtime.sent


async def test_resolved_metadata_migrates_workspace_customization_and_canvas_scope(tmp_path, app_factory):
    workspace = tmp_path / 'eventually-known'
    directory = native_session(workspace, 'resolved-chat', metadata={'working_dir': '/wrong/path', 'bundle': 'unknown'})
    capture = directory / 'context-intelligence' / 'metadata.json'
    metadata = json.loads(capture.read_text()); metadata['working_dir'] = '/wrong/path'
    capture.write_text(json.dumps(metadata))
    app = app_factory()
    await app.history.refresh()
    row = native_rows(app)[0]
    original_workspace_id = row['workspaceId']
    assert row['workspace'] is None
    await select(app, row['id'])
    await app.dispatch('workspace.rename', {'id': original_workspace_id, 'name': 'My custom project name'})
    registration = next(item for item in app.state['workspaces'] if item['id'] == original_workspace_id)
    registration['favorite'] = True
    await app.dispatch('session.rename', {'id': row['id'], 'title': 'My manual chat title'})
    await app.dispatch('canvas.show', {'kind': 'markdown', 'title': 'Keep this tab', 'content': '# Canvas', 'sessionId': row['id']})
    artifact_id = app.state['canvas']['id']
    meta_path = directory / 'metadata.json'
    metadata = json.loads(meta_path.read_text())
    metadata.update(working_dir=str(workspace), bundle='bundle:anchors-amp-dev', description='New CLI summary',
                    name='A different CLI name', turn_count=7, created='2026-02-01T12:00:00Z', updated_at=9999999999)
    meta_path.write_text(json.dumps(metadata))
    native_bytes = files_snapshot(directory)
    await app.history.refresh()
    refreshed = app._session(row['id'])
    assert refreshed['workspace'] == str(workspace)
    assert refreshed['bundle'] == 'anchors-amp-dev'
    assert refreshed['description'] == 'New CLI summary'
    assert refreshed['turnCount'] == 7
    assert refreshed['createdAt'] == 1769947200
    assert refreshed['updatedAt'] == 9999999999
    assert refreshed['historyReadOnlyReason'] is None
    assert refreshed['title'] == 'My manual chat title'
    assert refreshed['workspaceId'] != original_workspace_id
    assert not any(item['id'] == original_workspace_id for item in app.state['workspaces'])
    registration = next(item for item in app.state['workspaces'] if item['id'] == refreshed['workspaceId'])
    assert registration['name'] == 'My custom project name'
    assert registration['favorite'] is True
    assert app.state['selectedWorkspaceId'] == refreshed['workspaceId']
    assert app.state['canvas']['id'] == artifact_id
    assert app.state['canvas']['workspaceId'] == refreshed['workspaceId']
    assert next(item for item in app.state['canvasArtifacts'] if item['id'] == artifact_id)['workspaceId'] == refreshed['workspaceId']
    assert files_snapshot(directory) == native_bytes


async def test_parent_links_use_the_matching_projects_app_alias(tmp_path, app_factory):
    for workspace in (tmp_path / 'first', tmp_path / 'second'):
        native_session(workspace, 'same-root')
        native_session(workspace, 'same-child', metadata={'parent_session_id': 'same-root'})
    app = app_factory()
    await app.history.refresh()
    rows = native_rows(app)
    for child in (row for row in rows if row['nativeIdentity'] == 'same-child'):
        parent = next(row for row in rows if row['nativeIdentity'] == 'same-root' and row['nativeProject'] == child['nativeProject'])
        assert child['parentId'] == parent['id']
        assert child['nativeParentId'] == 'same-root'


async def web_owned_chat(app_factory, workspace):
    messages = [{'role': 'user', 'content': 'Repeat this'}, {'role': 'assistant', 'content': 'Original response'}]
    directory = native_session(workspace, 'web-runtime-id', messages)
    app = app_factory(workspace=workspace)
    session = app._new_session({})
    session.update(runtimeSessionId='web-runtime-id', messages=[
        {'id': 'web-user-id', 'role': 'user', 'text': 'Repeat this', 'via': 'chat', 'createdAt': 1,
         'attachments': [{'id': 'original-attachment', 'name': 'preserve.txt'}]},
        {'id': 'web-assistant-id', 'role': 'assistant', 'text': 'Original response', 'via': 'chat', 'createdAt': 2},
        {'id': 'voice-only-id', 'role': 'assistant', 'text': 'A voice-only aside', 'via': 'call', 'createdAt': 3},
    ], execution={'turns': []}, draftAttachments=[{'id': 'keep-draft', 'name': 'draft.png'}])
    app.state['sessions'].append(session)
    app.state['selectedSessionId'] = session['id']
    await app.history.refresh()
    return app, session, directory


async def test_web_history_merge_keeps_repeated_turns_long_cli_continuations_and_ui_ids(tmp_path, app_factory):
    app, session, directory = await web_owned_chat(app_factory, tmp_path / 'web-project')
    # More than one page is appended before the first web/native alignment.
    with (directory / 'transcript.jsonl').open('a') as stream:
        for number in range(150):
            stream.write(json.dumps({'role': 'user' if number % 2 == 0 else 'assistant', 'content': f'CLI message {number}'}) + '\n')
        stream.write(json.dumps({'role': 'user', 'content': 'Repeat this'}) + '\n')
        stream.write(json.dumps({'role': 'assistant', 'content': 'Second response'}) + '\n')
    await app.history.load(session['id'])
    loaded = app._session(session['id'])
    assert len(loaded['messages']) == 155  # 154 native + one voice-only bubble.
    assert [row['id'] for row in loaded['messages'][:3]] == ['web-user-id', 'web-assistant-id', 'voice-only-id']
    assert loaded['messages'][0]['attachments'][0]['id'] == 'original-attachment'
    assert sum(row['text'] == 'Repeat this' for row in loaded['messages']) == 2
    assert [row['text'] for row in loaded['messages'][3:153]] == [f'CLI message {number}' for number in range(150)]
    assert loaded['draftAttachments'][0]['id'] == 'keep-draft'
    initial_ids = [row['id'] for row in loaded['messages']]
    await app.history.load(session['id'])
    assert [row['id'] for row in loaded['messages']] == initial_ids
    with (directory / 'transcript.jsonl').open('a') as stream:
        stream.write(json.dumps({'role': 'user', 'content': 'Repeat this'}) + '\n')
        stream.write(json.dumps({'role': 'assistant', 'content': 'Third response'}) + '\n')
    await app.history.refresh()
    assert sum(row['text'] == 'Repeat this' for row in loaded['messages']) == 3
    assert [row['id'] for row in loaded['messages'][:155]] == initial_ids
    assert loaded['messages'][-1]['text'] == 'Third response'
    assert not app.runtime.started and not app.runtime.sent


async def test_web_history_merge_preserves_missing_native_messages_between_matched_ui_rows(tmp_path, app_factory):
    app, session, directory = await web_owned_chat(app_factory, tmp_path / 'web-project')
    await app.history.load(session['id'])
    session['messages'].append({'id': 'new-web-message', 'role': 'assistant', 'text': 'New web result', 'via': 'chat', 'createdAt': 4})
    with (directory / 'transcript.jsonl').open('a') as stream:
        stream.write(json.dumps({'role': 'user', 'content': 'Unseen CLI request'}) + '\n')
        stream.write(json.dumps({'role': 'assistant', 'content': 'Unseen CLI response'}) + '\n')
        stream.write(json.dumps({'role': 'assistant', 'content': 'New web result'}) + '\n')
    await app.history.load(session['id'])
    assert [row['text'] for row in session['messages']][-3:] == ['Unseen CLI request', 'Unseen CLI response', 'New web result']
    assert session['messages'][-1]['id'] == 'new-web-message'


async def test_web_history_rewrite_reports_error_without_replacing_ui_rows(tmp_path, app_factory):
    app, session, directory = await web_owned_chat(app_factory, tmp_path / 'web-project')
    await app.history.load(session['id'])
    original = copy.deepcopy(session['messages'])
    path = directory / 'transcript.jsonl'
    path.write_text(path.read_text().replace('Original response', 'Rewritten response'))
    await app.history.load(session['id'])
    assert session['historyError']
    assert session['messages'] == original


@pytest.mark.parametrize('trailing_boundary', [False, True])
async def test_restart_removes_verified_cached_reminders_without_changing_context(tmp_path, app_factory, trailing_boundary):
    from amplifier_web.automatic_history import display_message, revision
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_store import fork_session

    text = '<system-reminder source="fixture">Keep this context.</system-reminder>'
    rows = [
        {'role': 'user', 'content': text, 'metadata': {'ephemeral': True, 'persisted': True}},
        # Literal user quotes must survive, even with exactly the same text.
        {'role': 'user', 'content': text},
        {'role': 'assistant', 'content': 'That is an internal reminder wrapper.'},
        {'role': 'user', 'content': [{'type': 'text', 'text': '<system-reminders>Tail context</system-reminders>'}],
         'metadata': {'ephemeral': True, 'persisted': True}},
    ]
    workspace = tmp_path / 'reminder-workspace'
    directory = native_session(workspace, 'cached-reminders', rows)
    canonical = {name: (directory / name).read_bytes() for name in
                 ('transcript.jsonl', 'metadata.json', 'context-intelligence/events.jsonl')}
    app = app_factory(workspace=workspace)
    session = app._new_session({})
    session.update(runtimeSessionId='cached-reminders', nativeIdentity='cached-reminders',
                   nativeProject=project_slug(workspace), historyManaged=False, historyLoaded=True,
                   draftAttachments=[{'id': 'draft', 'name': 'keep.png'}])
    session['messages'] = [display_message(row, index, session, include_internal=True) for index, row in enumerate(rows)]
    session['messages'][1]['attachments'] = [{'id': 'keep-attachment', 'name': 'quote.txt'}]
    retained = copy.deepcopy(session['messages'][1:3])
    boundary = 3 if trailing_boundary else 2
    session.update(nativeRevision=revision(session), nativeBoundary=boundary,
                   nativeBoundaryId=session['messages'][boundary]['id'])
    app.state['sessions'].append(session)
    app.state['selectedSessionId'] = session['id']
    app._publish()
    await app.close()

    resumed = app_factory(workspace=workspace)
    assert resumed._session()['historyLoaded'] is False
    await resumed.history.refresh()
    visible = resumed._session()
    assert visible['historyError'] is None
    assert visible['messages'] == retained
    assert visible['nativeBoundary'] == 2
    assert visible['sharedHistoryTotal'] == 2
    assert visible['draftAttachments'] == [{'id': 'draft', 'name': 'keep.png'}]
    assert not resumed.runtime.started and not resumed.runtime.sent
    await resumed.history.load(visible['id'])
    assert visible['messages'] == retained
    assert all((directory / name).read_bytes() == value for name, value in canonical.items())

    fork = fork_session(resumed.data_dir, visible, 'reminder-fork', turn=1)
    saved = SessionStore.for_app(resumed.data_dir, workspace).load('reminder-fork')[0]
    assert saved == rows  # Internal context still reaches the fork and resume.
    assert fork['messages'] == retained and fork['sharedHistoryTotal'] == 2


def test_cached_reminder_cleanup_requires_exact_native_provenance():
    from amplifier_web.automatic_history import display_identity, remove_internal_copies
    session = {'id': 'root', 'messages': [
        {'id': 'different-text', 'role': 'user', 'nativeIndex': 0, 'text': 'Real user input'},
        {'id': 'different-index', 'role': 'user', 'nativeIndex': 1, 'text': '<system-reminder>Quoted</system-reminder>'},
        {'id': 'unindexed', 'role': 'user', 'text': '<system-reminder>Quoted</system-reminder>'},
    ]}
    before = copy.deepcopy(session['messages'])
    hidden = {0: display_identity(session, 0, 'user', '<system-reminder>Quoted</system-reminder>')}
    remove_internal_copies(session, hidden)
    assert session['messages'] == before


async def test_ensure_loaded_refreshes_already_loaded_chat_before_continuing(tmp_path, app_factory):
    directory = native_session(tmp_path / 'cli', 'ensure-fresh')
    app = app_factory()
    await app.history.refresh()
    row = native_rows(app)[0]
    await select(app, row['id'])
    with (directory / 'transcript.jsonl').open('a') as stream:
        stream.write(json.dumps({'role': 'assistant', 'content': 'A newer CLI response'}) + '\n')
    await app.history.ensure_loaded(row['id'])
    assert app._session(row['id'])['messages'][-1]['text'] == 'A newer CLI response'
    assert not app.runtime.started and not app.runtime.sent


async def test_native_draft_attachments_and_canvas_selection_survive_restart(tmp_path, app_factory):
    native_session(tmp_path / 'cli', 'draft-chat')
    app = app_factory()
    await app.history.refresh()
    row = native_rows(app)[0]
    await select(app, row['id'])
    app._session(row['id'])['draftAttachments'] = [{'id': 'draft-file', 'name': 'keep-me.png', 'size': 19}]
    await app.dispatch('canvas.show', {'kind': 'markdown', 'title': 'A retained tab', 'content': '# Keep me', 'sessionId': row['id']})
    canvas_id = app.state['canvas']['id']
    await app.close()
    restored = app_factory()
    assert restored._session(row['id'])['draftAttachments'] == [{'id': 'draft-file', 'name': 'keep-me.png', 'size': 19}]
    assert restored.state['canvas']['id'] == canvas_id
    assert restored.state['canvas']['title'] == 'A retained tab'


async def test_hidden_unresolved_workspace_stays_hidden_when_its_path_is_recovered(tmp_path, app_factory):
    workspace = tmp_path / 'hidden-unresolved'
    directory = native_session(workspace, 'hidden-chat', metadata={'working_dir': '/wrong/path'})
    capture = directory / 'context-intelligence' / 'metadata.json'
    metadata = json.loads(capture.read_text()); metadata['working_dir'] = '/wrong/path'
    capture.write_text(json.dumps(metadata))
    app = app_factory()
    await app.history.refresh()
    old = next(row for row in app.state['workspaces'] if row.get('nativeProject') == project_slug(workspace))
    assert old['path'] is None
    await app.dispatch('workspace.remove', {'id': old['id']})
    path = directory / 'metadata.json'
    metadata = json.loads(path.read_text()); metadata['working_dir'] = str(workspace)
    path.write_text(json.dumps(metadata))
    await app.history.refresh()
    assert not any(row.get('nativeProject') == project_slug(workspace) for row in app.state['workspaces'])


async def test_promoted_paged_chat_retains_earlier_history_offsets_and_web_message_ids(tmp_path, app_factory):
    messages = [{'role': 'user' if number % 2 == 0 else 'assistant', 'content': f'Message {number}'} for number in range(205)]
    directory = native_session(tmp_path / 'cli', 'promoted-chat', messages)
    app = app_factory()
    await app.history.refresh()
    row = native_rows(app)[0]
    await select(app, row['id'])
    session = app._session(row['id'])
    assert session['sharedHistoryOffset'] == 105
    assert session['sharedHistoryUserTurnOffset'] == 53
    # The service promotes history on explicit execution/configuration changes.
    session['historyManaged'] = False
    session['messages'][0]['id'] = 'preserve-web-message-id'
    with (directory / 'transcript.jsonl').open('a') as stream:
        stream.write(json.dumps({'role': 'user', 'content': 'New turn'}) + '\n')
        stream.write(json.dumps({'role': 'assistant', 'content': 'New response'}) + '\n')
    await app.history.refresh()
    assert session['sharedHistoryOffset'] == 105
    assert session['sharedHistoryUserTurnOffset'] == 53
    assert len(session['messages']) == 102
    await app.history.load(session['id'], before=105)
    assert session['sharedHistoryOffset'] == 5
    assert session['sharedHistoryUserTurnOffset'] == 3
    assert len(session['messages']) == 202
    # An overlapping page deduplicates on the native index even for web IDs.
    await app.history.load(session['id'], before=107)
    assert session['sharedHistoryOffset'] == 5
    assert session['sharedHistoryUserTurnOffset'] == 3
    assert len(session['messages']) == 202
    assert next(message for message in session['messages'] if message['nativeIndex'] == 105)['id'] == 'preserve-web-message-id'
    await app.history.load(session['id'], before=5)
    assert session['sharedHistoryOffset'] == 0
    assert session['sharedHistoryUserTurnOffset'] == 0
    assert [message['nativeIndex'] for message in session['messages']] == list(range(207))


async def test_native_manual_title_source_refreshes_without_overriding_web_rename(tmp_path, app_factory):
    directory = native_session(tmp_path / 'cli', 'named-chat', metadata={'name_source': 'manual', 'name': 'First CLI title'})
    app = app_factory()
    await app.history.refresh()
    row = native_rows(app)[0]
    assert row['nativeNameSource'] == 'manual'
    assert row['titleSource'] == 'native'
    metadata_path = directory / 'metadata.json'
    metadata = json.loads(metadata_path.read_text())
    metadata['name'] = 'Second CLI title'
    metadata_path.write_text(json.dumps(metadata))
    await app.history.refresh()
    assert app._session(row['id'])['title'] == 'Second CLI title'
    await app.dispatch('session.rename', {'id': row['id'], 'title': 'User web title'})
    metadata['name'] = 'Third CLI title'
    metadata_path.write_text(json.dumps(metadata))
    await app.history.refresh()
    assert app._session(row['id'])['title'] == 'User web title'
    await app.close()
    restored = app_factory()
    assert restored._session(row['id'])['nativeNameSource'] == 'manual'


async def test_worker_histories_remain_available_but_counts_only_include_root_chats(tmp_path, app_factory):
    workspace = tmp_path / 'cli'
    native_session(workspace, 'root-chat', metadata={'parent_id': None})
    native_session(workspace, 'uuid-worker', metadata={'parent_session_id': 'root-chat'})
    native_session(workspace, 'cli-fork', metadata={'parent_id': 'root-chat', 'forked_from_turn': 1, 'forked_at': '2026-01-01T00:00:00Z'})
    app = app_factory()
    await app.history.refresh()
    rows = {row['nativeIdentity']: row for row in native_rows(app)}
    assert rows['root-chat']['sessionKind'] == 'root'
    assert rows['cli-fork']['sessionKind'] == 'root'
    assert rows['uuid-worker']['sessionKind'] == 'worker'
    assert rows['uuid-worker']['parentId'] == rows['root-chat']['id']
    assert rows['cli-fork']['parentId'] == rows['root-chat']['id']
    assert rows['cli-fork']['historyReadOnlyReason'] is None
    assert app.state['sharedHistory']['sessionCount'] == 2
    assert app.state['sharedHistory']['workerSessionCount'] == 1
    registration = next(row for row in app.state['workspaces'] if row.get('path') == str(workspace))
    assert registration['sessionCount'] == 2
    assert registration['workerSessionCount'] == 1
    await app.history.load(rows['uuid-worker']['id'])
    assert app._session(rows['uuid-worker']['id'])['messages'][0]['text'] == 'Saved CLI question'
    await app.close()
    restored = app_factory()
    # Uncustomized native catalog summaries are rebuilt rather than saved in
    # app state on every view change. Classification must survive discovery.
    await restored.history.refresh()
    assert restored._session(rows['uuid-worker']['id'])['sessionKind'] == 'worker'
    assert restored._session(rows['cli-fork']['id'])['sessionKind'] == 'root'
    assert restored._session(rows['uuid-worker']['id'])['historyLoaded'] is False


async def test_existing_index_classification_refreshes_without_using_ui_fork_parent(tmp_path, app_factory):
    workspace = tmp_path / 'cli'
    native_session(workspace, 'web-fork', metadata={'parent_id': None})
    worker_directory = native_session(workspace, 'unclassified-worker')
    app = app_factory()
    await app.history.refresh()
    fork_row = next(row for row in native_rows(app) if row['nativeIdentity'] == 'web-fork')
    worker_row = next(row for row in native_rows(app) if row['nativeIdentity'] == 'unclassified-worker')
    fork_state = app._session(fork_row['id'])
    fork_state.update(historyManaged=False, parentId='ui-fork-lineage', forkTranscript={'sourceSessionId': 'old-root'})
    worker_state = app._session(worker_row['id'])
    worker_state.pop('sessionKind')  # An index saved by the previous app version.
    metadata_path = worker_directory / 'metadata.json'
    metadata = json.loads(metadata_path.read_text()); metadata['parent_id'] = 'web-fork'
    metadata_path.write_text(json.dumps(metadata))
    await app.close()
    restored = app_factory()
    await restored.history.refresh()
    assert restored._session(worker_row['id'])['sessionKind'] == 'worker'
    assert restored._session(worker_row['id'])['parentId'] == fork_row['id']
    assert restored._session(fork_row['id'])['sessionKind'] == 'root'
    assert restored._session(fork_row['id'])['parentId'] == 'ui-fork-lineage'
@pytest.mark.asyncio
async def test_agent_history_search_reads_unloaded_native_chat_without_selection(app_factory, tmp_path):
    workspace = tmp_path / "history-search-workspace"
    directory = native_session(workspace, "search-target", [
        {"role": "user", "content": "Historical unique decision"},
        {"role": "assistant", "content": "Saved answer"}])
    app = app_factory(workspace=workspace)
    await app.history.refresh()
    target = next(row for row in app.state["sessions"] if row.get("nativeIdentity") == "search-target")
    before = copy.deepcopy(app.state)
    files = files_snapshot(directory)
    result = await app.app_bridge("history", {"action": "search", "query": "unique decision"}, target["id"])
    assert any(row["id"] == target["id"] for row in result["items"])
    assert app.state == before
    assert files_snapshot(directory) == files
    assert app.runtime.started == [] and app.runtime.sent == []
