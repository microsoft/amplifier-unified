"""Native history discovery never imports or rewrites conversation bodies."""
import json
from pathlib import Path
import uuid

import pytest

from amplifier_web.native_history import NativeHistory
from amplifier_web.session_files import project_slug


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def session(home, workspace, identity, metadata=None, *, transcript=True):
    directory = home / 'projects' / project_slug(workspace) / 'sessions' / identity
    directory.mkdir(parents=True, exist_ok=True)
    if metadata is not None:
        write_json(directory / 'metadata.json', metadata)
    if transcript:
        # Deliberately not JSON: an index must never try to parse a transcript.
        (directory / 'transcript.jsonl').write_text('private conversation body\n')
    return directory


def test_native_projects_are_workspaces_and_children_are_sessions(tmp_path, monkeypatch):
    home = tmp_path / 'amplifier'
    workspace = tmp_path / 'a project-with-hyphens'
    workspace.mkdir()
    root = session(home, workspace, 'root', {
        'working_dir': str(workspace), 'name': 'Saved title', 'description': 'A summary',
        'bundle': 'bundle:anchors', 'created': '2026-01-01T12:00:00Z', 'turn_count': 3,
    })
    child = session(home, workspace, 'root_agent', {'parent_id': 'root', 'agent_name': 'research'})
    before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in home.rglob('*') if path.is_file()}
    original_open = Path.open

    def guard(path, *args, **kwargs):
        assert path.name not in {'transcript.jsonl', 'events.jsonl'}, 'Index read a conversation body'
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guard)
    result = NativeHistory(home).scan()
    assert len(result['workspaces']) == 1
    registration = result['workspaces'][0]
    assert registration['id'] == uuid.uuid5(uuid.NAMESPACE_URL, str(workspace)).hex
    assert registration['path'] == str(workspace)
    assert registration['name'] == workspace.name
    assert registration['available'] is True
    assert registration['sessionCount'] == 1
    assert registration['workerSessionCount'] == 1
    rows = {row['nativeIdentity']: row for row in result['sessions']}
    assert rows['root']['title'] == 'Saved title'
    assert rows['root']['description'] == 'A summary'
    assert rows['root']['bundle'] == 'anchors'
    assert rows['root']['createdAt'] == 1767268800
    assert rows['root']['turnCount'] == 3
    assert rows['root']['canResume'] is True
    assert rows['root']['sessionKind'] == 'root'
    assert rows['root_agent']['parentId'] == 'root'
    assert rows['root_agent']['workspace'] == str(workspace)
    assert rows['root_agent']['title'] == 'research'
    assert rows['root_agent']['canResume'] is False
    assert rows['root_agent']['sessionKind'] == 'worker'
    monkeypatch.setattr(Path, 'open', original_open)
    after = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in home.rglob('*') if path.is_file()}
    assert before == after
    assert root.exists() and child.exists()


def test_same_session_id_in_different_projects_has_distinct_stable_identity(tmp_path):
    home = tmp_path / 'amplifier'
    for folder in ('one', 'two'):
        workspace = tmp_path / folder
        session(home, workspace, 'same-id', {'working_dir': str(workspace)})
    history = NativeHistory(home)
    first = history.scan()
    second = history.scan()
    assert len({row['id'] for row in first['sessions']}) == 2
    assert {row['id'] for row in first['sessions']} == {row['id'] for row in second['sessions']}
    assert all(uuid.UUID(hex=row['id']) for row in first['sessions'])


def test_unchanged_files_are_not_read_and_external_changes_refresh(tmp_path):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'workspace'
    directory = session(home, workspace, 'root', {'working_dir': str(workspace), 'name': 'First'})
    history = NativeHistory(home)
    first = history.scan()
    assert first['metadataReads'] == 1
    first['sessions'][0]['title'] = 'Caller changed its own copy'
    cached = history.scan()
    assert cached['metadataReads'] == 0
    assert cached['sessions'][0]['title'] == 'First'
    write_json(directory / 'metadata.json', {'working_dir': str(workspace), 'name': 'Updated externally'})
    changed = history.scan()
    assert changed['metadataReads'] == 1
    assert changed['sessions'][0]['title'] == 'Updated externally'
    revision = changed['sessions'][0]['transcriptRevision']
    (directory / 'transcript.jsonl').write_text('CLI appended a new conversation turn\n')
    changed_transcript = history.scan()
    assert changed_transcript['metadataReads'] == 0
    assert changed_transcript['sessions'][0]['transcriptRevision'] != revision


def test_partial_or_missing_metadata_keeps_last_good_summary(tmp_path):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'workspace'
    directory = session(home, workspace, 'root', {'working_dir': str(workspace), 'name': 'Keep me'})
    history = NativeHistory(home)
    history.scan()
    (directory / 'metadata.json').write_text('{"name":')
    malformed = history.scan()
    assert malformed['sessions'][0]['title'] == 'Keep me'
    assert malformed['workspaces'][0]['path'] == str(workspace)
    assert any(issue['kind'] == 'unreadable' for issue in malformed['issues'])
    assert history.scan()['metadataReads'] == 0
    (directory / 'metadata.json').unlink()
    assert history.scan()['sessions'][0]['title'] == 'Keep me'
    write_json(directory / 'metadata.json', {'working_dir': str(workspace), 'name': 'Recovered'})
    assert history.scan()['sessions'][0]['title'] == 'Recovered'


def test_ci_fallback_and_separate_naming_metadata(tmp_path):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'workspace'
    directory = session(home, workspace, 'root')
    write_json(directory / 'context-intelligence' / 'metadata.json', {
        'format': 'context-intelligence', 'version': '1.0.0', 'working_dir': str(workspace),
        'parent_id': 'parent', 'started_at': '2026-01-02T00:00:00Z',
    })
    write_json(directory / 'naming.json', {'name': 'Renamed', 'description': 'Summary', 'name_source': 'manual'})
    row = NativeHistory(home).scan()['sessions'][0]
    assert row['workspace'] == str(workspace)
    assert row['parentId'] == 'parent'
    assert row['createdAt'] == 1767312000
    assert row['title'] == 'Renamed'
    assert row['nameSource'] == 'manual'


def test_path_recovery_uses_exact_explicit_metadata_never_slug_reversal(tmp_path):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'missing-folder with spaces'
    session(home, workspace, 'root', {'config': {'working_dir': str(workspace)}})
    other = tmp_path / 'another-workspace'
    session(home, other, 'unknown', {'working_dir': str(tmp_path / 'wrong-folder')})
    result = NativeHistory(home).scan()
    by_project = {row['nativeProject']: row for row in result['workspaces']}
    assert by_project[project_slug(workspace)]['path'] == str(workspace)
    assert by_project[project_slug(workspace)]['available'] is False
    assert by_project[project_slug(other)]['path'] is None
    assert by_project[project_slug(other)]['available'] is False
    known = NativeHistory(home, known_workspaces=[{'path': str(other)}]).scan()
    assert next(row for row in known['workspaces'] if row['nativeProject'] == project_slug(other))['path'] == str(other)


def test_ambiguous_slug_collision_does_not_choose_wrong_workspace(tmp_path):
    home = tmp_path / 'amplifier'
    one, two = tmp_path / 'a-b' / 'c', tmp_path / 'a' / 'b-c'
    assert project_slug(one) == project_slug(two)
    session(home, one, 'one', {'working_dir': str(one)})
    session(home, two, 'two', {'working_dir': str(two)})
    result = NativeHistory(home).scan()
    assert len(result['sessions']) == 2
    assert result['workspaces'][0]['path'] is None


def test_empty_projects_remain_but_empty_diagnostic_captures_are_not_chats(tmp_path):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'workspace'
    directory = session(home, workspace, 'probe', {'working_dir': str(workspace), 'turn_count': 0}, transcript=False)
    write_json(directory / 'context-intelligence' / 'metadata.json', {'working_dir': str(workspace)})
    (directory / 'events.jsonl').write_text('probe diagnostics\n')
    empty = home / 'projects' / '-empty-project'
    empty.mkdir()
    result = NativeHistory(home).scan()
    assert len(result['workspaces']) == 2
    assert result['sessions'] == []


def test_symlink_sessions_and_metadata_are_not_followed(tmp_path):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'workspace'
    directory = session(home, workspace, 'real')
    outside = tmp_path / 'outside'
    outside.mkdir()
    write_json(outside / 'metadata.json', {'name': 'Must not read me'})
    (directory / 'metadata.json').symlink_to(outside / 'metadata.json')
    (directory.parent / 'linked').symlink_to(outside, target_is_directory=True)
    result = NativeHistory(home).scan()
    assert len(result['sessions']) == 1
    assert result['sessions'][0]['title'] == 'Conversation real'
    assert any(issue['kind'] == 'unreadable' for issue in result['issues'])


def test_unreadable_project_retains_previous_valid_rows(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'workspace'
    directory = session(home, workspace, 'root', {'working_dir': str(workspace)})
    history = NativeHistory(home)
    original = history._directories
    first = history.scan()

    def denied(path):
        if path == directory.parent:
            raise PermissionError('Directory currently inaccessible')
        return original(path)

    monkeypatch.setattr(history, '_directories', denied)
    result = history.scan()
    assert result['sessions'] == first['sessions']
    assert any(issue['kind'] == 'unreadable' for issue in result['issues'])


def test_temporarily_missing_transcript_keeps_visible_history_but_disables_resume(tmp_path):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'workspace'
    workspace.mkdir()
    directory = session(home, workspace, 'root', {'working_dir': str(workspace), 'bundle': 'anchors'})
    history = NativeHistory(home)
    first = history.scan()['sessions'][0]
    assert first['canResume'] is True
    (directory / 'transcript.jsonl').unlink()
    pending = history.scan()['sessions'][0]
    assert pending['id'] == first['id']
    assert pending['updatedAt'] == first['updatedAt']
    assert pending['transcriptAvailable'] is False
    assert pending['canResume'] is False
    (directory / 'transcript.jsonl').write_text('A completed native save\n')
    assert history.scan()['sessions'][0]['canResume'] is True


def test_project_metadata_can_locate_an_empty_workspace(tmp_path):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'workspace'
    write_json(home / 'projects' / project_slug(workspace) / 'metadata.json', {'cwd': str(workspace)})
    result = NativeHistory(home).scan()
    assert result['workspaces'][0]['path'] == str(workspace)
    assert result['sessions'] == []


def test_default_home_honors_amplifier_home_without_creating_it(tmp_path, monkeypatch):
    home = tmp_path / 'alternate-amplifier'
    monkeypatch.setenv('AMPLIFIER_HOME', str(home))
    assert NativeHistory().scan()['workspaces'] == []
    assert not home.exists()


def test_legacy_and_worker_ids_are_visible_but_cannot_resume_as_root(tmp_path):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'workspace'
    workspace.mkdir()
    child_id = uuid.uuid4().hex
    session(home, workspace, child_id, {'working_dir': str(workspace), 'bundle': 'anchors', 'parent_session_id': 'root'})
    session(home, workspace, 'root_foundation:explorer', {'working_dir': str(workspace), 'bundle': 'anchors'})
    session(home, workspace, 'old-profile', {'working_dir': str(workspace)})
    rows = {row['nativeIdentity']: row for row in NativeHistory(home).scan()['sessions']}
    assert len(rows) == 3
    assert rows[child_id]['parentId'] == 'root'
    assert rows[child_id]['canResume'] is False
    assert 'Worker' in rows[child_id]['readOnlyReason']
    assert rows['root_foundation:explorer']['canResume'] is False
    assert rows['old-profile']['canResume'] is False
    assert 'bundle' in rows['old-profile']['readOnlyReason']


@pytest.mark.parametrize(('identity', 'metadata', 'capture', 'kind', 'parent'), [
    ('uuid-child', {'parent_id': 'root'}, {}, 'worker', 'root'),
    ('uuid-child', {'parent_session_id': 'root'}, {}, 'worker', 'root'),
    ('uuid-child', {'parent_id': None, 'parent_session_id': 'root'}, {}, 'worker', 'root'),
    ('uuid-root', {'parent_id': None}, {'parent_id': 'stale-child-parent'}, 'root', None),
    ('custom_root_id', {'parent_id': ''}, {}, 'root', None),
    ('uuid-child', {}, {'parent_id': 'root'}, 'worker', 'root'),
    ('older-parent_agent', {}, {}, 'worker', None),
    ('1234567890abcdef-fedcba0987654321_researcher', {}, {}, 'worker', None),
    ('uuid-root', {}, {}, 'root', None),
    ('named-fork', {'parent_id': 'root', 'forked_from_turn': 3, 'forked_at': '2026-01-01T00:00:00Z'}, {}, 'root', 'root'),
    ('fork_with_underscore', {'parent_id': 'root', 'forked_from_turn': 3, 'forked_at': '2026-01-01T00:00:00Z'}, {}, 'root', 'root'),
    ('unified-fork', {'parent_id': None, 'fork': {'source_session_id': 'root', 'through_user_turn': 2}}, {}, 'root', None),
])
def test_worker_classification_respects_metadata_and_independent_fork_roots(tmp_path, identity, metadata, capture, kind, parent):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'workspace'
    workspace.mkdir()
    directory = session(home, workspace, identity, {'working_dir': str(workspace), 'bundle': 'anchors', **metadata})
    if capture:
        write_json(directory / 'context-intelligence' / 'metadata.json', {'working_dir': str(workspace), **capture})
    result = NativeHistory(home).scan()
    row = result['sessions'][0]
    assert row['sessionKind'] == kind
    assert row['parentId'] == parent
    assert result['sessionCount'] == (1 if kind == 'root' else 0)
    assert result['workerSessionCount'] == (1 if kind == 'worker' else 0)
    assert result['workspaces'][0]['sessionCount'] == result['sessionCount']
    assert result['workspaces'][0]['workerSessionCount'] == result['workerSessionCount']
    if kind == 'worker':
        assert row['canResume'] is False
        assert 'Worker sessions' in row['readOnlyReason']
    elif '_' not in identity:
        assert row['canResume'] is True


def test_explicit_root_metadata_refreshes_previous_worker_classification(tmp_path):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'workspace'
    workspace.mkdir()
    directory = session(home, workspace, 'named-root', {'working_dir': str(workspace), 'bundle': 'anchors', 'parent_id': 'old-parent'})
    history = NativeHistory(home)
    assert history.scan()['sessions'][0]['sessionKind'] == 'worker'
    write_json(directory / 'metadata.json', {'working_dir': str(workspace), 'bundle': 'anchors', 'parent_id': None})
    refreshed = history.scan()
    assert refreshed['sessions'][0]['sessionKind'] == 'root'
    assert refreshed['sessions'][0]['parentId'] is None
    assert refreshed['sessions'][0]['canResume'] is True


def test_canonical_metadata_and_missing_transcript_use_read_only_backups(tmp_path):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'workspace'
    workspace.mkdir()
    directory = session(home, workspace, 'root', {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Recovered'})
    (directory / 'metadata.json').rename(directory / 'metadata.json.backup')
    (directory / 'metadata.json').write_text('{partial')
    (directory / 'transcript.jsonl').rename(directory / 'transcript.jsonl.backup')
    before = {path: path.read_bytes() for path in directory.iterdir()}
    result = NativeHistory(home).scan()
    row, = result['sessions']
    assert row['title'] == 'Recovered' and row['canResume'] is True
    assert before == {path: path.read_bytes() for path in directory.iterdir()}
