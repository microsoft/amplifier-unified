"""Unavailable historical workspaces do not bypass retained transfer markers."""
import json
from pathlib import Path

import pytest

from amplifier_foundation.session import SharedSessionStore
from amplifier_web.service import AppError
from test_automatic_history import app_factory, files_snapshot, native_rows, native_session, select


@pytest.mark.parametrize('workspace_state', ['missing', 'unresolved', 'legacy-worker'])
async def test_historical_rename_and_read_survive_without_runtime_admission(tmp_path, app_factory, workspace_state):
    workspace = tmp_path / 'saved-project'
    identity = 'root_foundation:worker' if workspace_state == 'legacy-worker' else 'saved-root'
    metadata = {'working_dir': None} if workspace_state == 'unresolved' else {}
    folder = native_session(workspace, identity, metadata=metadata)
    if workspace_state == 'missing':
        workspace.rmdir()
    elif workspace_state == 'unresolved':
        (folder / 'context-intelligence/metadata.json').write_text('{}')
    app = app_factory()
    await app.history.refresh()
    row = native_rows(app)[0]
    loaded = await select(app, row['id'])
    reason = loaded['historyReadOnlyReason']
    assert reason and not app.portability.fenced(row['id'])
    transcript = (folder / 'transcript.jsonl').read_bytes()
    await app.dispatch('session.rename', {'id': row['id'], 'title': 'Retained saved chat'})
    assert json.loads((folder / 'metadata.json').read_text())['name'] == 'Retained saved chat'
    assert (folder / 'transcript.jsonl').read_bytes() == transcript
    assert app._session(row['id'])['historyManaged']
    with pytest.raises(AppError) as rejected:
        await app.dispatch('conversation.send', {'sessionId': row['id'], 'text': 'Must remain unavailable'})
    assert str(rejected.value) == reason
    assert not app.runtime.started and not app.runtime.sent


@pytest.mark.parametrize('workspace_state', ['missing', 'unresolved'])
@pytest.mark.parametrize('fence_source', ['native', 'local'])
async def test_known_transfer_still_blocks_unavailable_history(tmp_path, app_factory, monkeypatch,
                                                              workspace_state, fence_source):
    workspace = tmp_path / 'fenced-project'
    folder = native_session(workspace, 'fenced-root')
    if fence_source == 'native':
        store = SharedSessionStore(workspace, 'fenced-root')
        held = store.acquire(app='test-native-owner')
        try:
            held.fence_transfer('known-transfer', 'other-host')
        finally:
            held.release()
    if workspace_state == 'missing':
        workspace.rmdir()
    else:
        metadata = json.loads((folder / 'metadata.json').read_text())
        metadata['working_dir'] = None
        (folder / 'metadata.json').write_text(json.dumps(metadata))
        (folder / 'context-intelligence/metadata.json').write_text('{}')
    app = app_factory()
    await app.history.refresh()
    row = native_rows(app)[0]
    if fence_source == 'local':
        monkeypatch.setattr(app.portability.node, 'fenced', lambda sid: sid == row['id'])
    assert app.portability.fenced(row['id'])
    before = files_snapshot(folder)
    await select(app, row['id'])
    assert [message['text'] for message in app._session(row['id'])['messages']] == ['Saved CLI question', 'Saved CLI answer']
    for action, args in [('session.rename', {'id': row['id'], 'title': 'Forbidden rename'}),
                         ('conversation.send', {'sessionId': row['id'], 'text': 'Forbidden work'})]:
        with pytest.raises(AppError, match='fenced for transfer') as rejected:
            await app.dispatch(action, args)
        assert rejected.value.status == 409
    assert files_snapshot(folder) == before
    assert not app.runtime.started and not app.runtime.sent


@pytest.mark.parametrize('mismatch', ['runtime-identity', 'native-project'])
async def test_historical_alias_cannot_hide_workspace_native_fence(tmp_path, app_factory, mismatch):
    workspace = tmp_path / 'saved-project'
    native_session(workspace, 'saved-root')
    runtime_identity = 'runtime-alias' if mismatch == 'runtime-identity' else 'saved-root'
    store = SharedSessionStore(workspace, runtime_identity)
    held = store.acquire(app='test-native-owner')
    try:
        held.fence_transfer('known-transfer', 'other-host')
    finally:
        held.release()
    app = app_factory()
    await app.history.refresh()
    row = next(row for row in native_rows(app) if row['nativeIdentity'] == 'saved-root')
    session = app._session(row['id'])
    session['runtimeSessionId'] = runtime_identity
    if mismatch == 'native-project':
        session['nativeProject'] = '-an-older-history-location'
    assert not app.portability.node.records(row['id'])
    with pytest.raises(AppError, match='fenced for transfer'):
        await app.dispatch('session.rename', {'id': row['id'], 'title': 'Forbidden alias rename'})
    assert not app.runtime.started and not app.runtime.sent


@pytest.mark.parametrize('marker_kind', ['malformed', 'dangling-link', 'unreadable'])
async def test_uncertain_historical_marker_never_grants_write_permission(tmp_path, app_factory, monkeypatch, marker_kind):
    folder = native_session(tmp_path / 'unknown', 'root_foundation:worker', metadata={'working_dir': None})
    (folder / 'context-intelligence/metadata.json').write_text('{}')
    marker = folder / 'transfer-fence.json'
    if marker_kind == 'dangling-link':
        marker.symlink_to(folder / 'absent-marker')
    else:
        marker.write_text('incomplete transfer evidence')
    app = app_factory()
    await app.history.refresh()
    row = native_rows(app)[0]
    if marker_kind == 'unreadable':
        real_lstat = Path.lstat
        def denied(path, *args, **kwargs):
            if path == marker: raise PermissionError('Fixture marker cannot be inspected')
            return real_lstat(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'lstat', denied)
    with pytest.raises(AppError, match='transfer fence|fenced for transfer'):
        await app.dispatch('session.rename', {'id': row['id'], 'title': 'Forbidden rename'})
    assert json.loads((folder / 'metadata.json').read_text())['name'] != 'Forbidden rename'
    assert not app.runtime.started and not app.runtime.sent
