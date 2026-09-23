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


def test_unchanged_scan_retains_consumer_revision_without_copying_catalog(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    session(home, workspace, 'root', {'working_dir': str(workspace), 'bundle': 'anchors'})
    history = NativeHistory(home)
    history.scan()  # Learn workspace paths included in subsequent file stamps.
    token, snapshot = history.scan_if_changed()
    saved = snapshot['sessions'][0]['transcriptRevision'][:]
    snapshot['sessions'][0]['transcriptRevision'][0] = -1

    def no_copy(*args, **kwargs):
        raise AssertionError('Unchanged catalog must not be copied')

    with monkeypatch.context() as context:
        context.setattr('amplifier_web.native_history.copy.deepcopy', no_copy)
        current, unchanged = history.scan_if_changed(since=token)
        assert current is token and unchanged is None

    # Failed stamps trigger a real project rebuild; value-equivalent metadata
    # should still avoid a full snapshot. A changed row must invalidate it.
    with monkeypatch.context() as context:
        context.setattr(history, '_project_stamp', lambda *args: None)
        assert history.scan_if_changed(since=token)[1] is None

    # Every full/public result remains detached, even after a conditional scan.
    full = history.scan()
    assert full['sessions'][0]['transcriptRevision'] == saved
    full['sessions'][0]['transcriptRevision'][0] = -2
    forced_token, forced = history.scan_if_changed(since=token, force=True)
    assert forced_token is token
    assert forced['sessions'][0]['transcriptRevision'] == saved
    assert history.scan_if_changed(since=object())[1] == forced
    assert NativeHistory(home).scan_if_changed(since=token)[1] is not None


def test_conditional_scan_reports_metadata_availability_and_root_errors(tmp_path):
    home = tmp_path / 'home'
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    root = session(home, workspace, 'root', {'working_dir': str(workspace), 'bundle': 'anchors'})
    history = NativeHistory(home)
    history.scan()
    token, first = history.scan_if_changed()
    write_json(root / 'metadata.json', {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Renamed'})
    token2, renamed = history.scan_if_changed(since=token)
    assert token2 is not token and renamed['sessions'][0]['title'] == 'Renamed'
    assert first['sessions'][0]['title'] != 'Renamed'
    workspace.rmdir()
    token3, unavailable = history.scan_if_changed(since=token2)
    assert unavailable['workspaces'][0]['available'] is False
    workspace.mkdir()
    token4, available = history.scan_if_changed(since=token3)
    assert available['sessions'][0]['canResume'] is True
    (home / 'projects').rename(home / 'temporarily-away')
    token5, missing = history.scan_if_changed(since=token4)
    assert missing['sessions'] == available['sessions']
    assert {'kind': 'unavailable-root'} in missing['issues']
    assert history.scan_if_changed(since=token5)[1] is None
    (home / 'temporarily-away').rename(home / 'projects')
    token6, recovered = history.scan_if_changed(since=token5)
    assert token6 is not token5 and recovered['issues'] == available['issues']
    assert recovered['sessions'] == available['sessions']


def test_conditional_scan_detects_project_recreation_and_retains_order(tmp_path):
    import shutil
    home = tmp_path / 'home'
    first_workspace = tmp_path / 'first'
    second_workspace = tmp_path / 'second'
    first_workspace.mkdir()
    second_workspace.mkdir()
    root = session(home, first_workspace, 'first', {'working_dir': str(first_workspace), 'bundle': 'anchors'})
    session(home, second_workspace, 'second', {'working_dir': str(second_workspace), 'bundle': 'anchors'})
    history = NativeHistory(home)
    history.scan()
    token, before = history.scan_if_changed()
    shutil.rmtree(root.parent.parent)
    token2, removed = history.scan_if_changed(since=token)
    assert [row['nativeIdentity'] for row in removed['sessions']] == ['second']
    session(home, first_workspace, 'new', {'working_dir': str(first_workspace), 'bundle': 'anchors'})
    token3, recreated = history.scan_if_changed(since=token2)
    assert token3 is not token2
    assert {row['nativeIdentity'] for row in recreated['sessions']} == {'new', 'second'}
    assert recreated['sessions'] == sorted(recreated['sessions'], key=lambda row: (row['updatedAt'], row['id']), reverse=True)
    assert {row['nativeIdentity'] for row in before['sessions']} == {'first', 'second'}


def test_conditional_scan_keeps_watch_reconciliation_and_known_path_resolution(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    root = session(home, workspace, 'saved', {'name': 'Original', 'bundle': 'anchors'})
    history = NativeHistory(home)
    monkeypatch.setattr(history, '_invalidations', lambda: (True, set()))
    token, unresolved = history.scan_if_changed()
    assert unresolved['sessions'][0]['workspace'] is None
    token2, resolved = history.scan_if_changed(since=token, known_workspaces=[str(workspace)])
    assert resolved['sessions'][0]['workspace'] == str(workspace)
    write_json(root / 'metadata.json', {'name': 'Missed watch event', 'bundle': 'anchors'})
    # A quiet watcher skips this project's stamps until bounded reconciliation.
    assert history.scan_if_changed(since=token2, known_workspaces=[str(workspace)])[1] is None
    history._reconcile_at = 0
    token3, reconciled = history.scan_if_changed(since=token2, known_workspaces=[str(workspace)])
    assert token3 is not token2
    assert reconciled['sessions'][0]['title'] == 'Missed watch event'


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


def test_unchanged_projects_skip_rebuild_but_discover_workers_and_late_files(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    directory = session(home, workspace, 'root', {'working_dir': str(workspace), 'name': 'Root'})
    history = NativeHistory(home)
    history.scan()
    history.scan()  # Establish the workspace paths learned from native metadata.
    original = history._scan_project
    rebuilt = []
    def scan(*args):
        rebuilt.append(args[0].name)
        return original(*args)
    monkeypatch.setattr(history, '_scan_project', scan)
    for _ in range(5):
        history.scan()
    assert not rebuilt
    session(home, workspace, 'root_child', {'parent_id': 'root'})
    assert history.scan()['workerSessionCount'] == 1
    assert len(rebuilt) == 1
    write_json(directory / 'context-intelligence' / 'metadata.json', {'description': 'New capture metadata'})
    changed = history.scan()
    assert next(row for row in changed['sessions'] if row['nativeIdentity'] == 'root')['description'] == 'New capture metadata'
    assert len(rebuilt) == 2


def test_watch_invalidates_changed_project_and_reconciles_missed_notifications(tmp_path, monkeypatch):
    import time
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    directory = session(home, workspace, 'root', {'working_dir': str(workspace), 'name': 'Root'})
    history = NativeHistory(home, watch=True)
    try:
        history.scan()
        deadline = time.monotonic() + 5
        while not history._watch.ready and time.monotonic() < deadline:
            time.sleep(.02)
        assert history._watch.ready
        history.scan()
        probes = []
        original = history._project_stamp
        def probe(*args):
            probes.append(args[0].name)
            return original(*args)
        monkeypatch.setattr(history, '_project_stamp', probe)
        for _ in range(4):
            history.scan()
        assert not probes
        assert not history.needs_scan([])
        write_json(directory / 'metadata.json', {'working_dir': str(workspace), 'name': 'Changed'})
        deadline = time.monotonic() + 5
        while not history._watch.dirty and time.monotonic() < deadline:
            time.sleep(.02)
        assert history.needs_scan([])
        assert history.scan()['sessions'][0]['title'] == 'Changed'
        assert probes == [project_slug(workspace)]
        # Runtime event traffic must not invalidate the native catalog.
        (directory / 'events.jsonl').write_text('many events')
        time.sleep(.4)
        assert history._watch.take()[1] == set()
        # The timer still detects changes if a platform notification is lost.
        write_json(directory / 'metadata.json', {'working_dir': str(workspace), 'name': 'Reconciled'})
        monkeypatch.setattr(history, '_invalidations', lambda: (True, set()))
        history._reconcile_at = 0
        assert history.needs_scan([])
        assert history.scan()['sessions'][0]['title'] == 'Reconciled'
    finally:
        watcher = history._watch
        history.close()
        assert not watcher.thread.is_alive()


def test_watch_failure_and_new_root_fall_back_to_file_discovery(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    history = NativeHistory(home, watch=True)
    assert history.scan()['sessions'] == []
    directory = session(home, workspace, 'root', {'working_dir': str(workspace), 'name': 'Root'})
    import watchfiles
    def unavailable(*args, **kwargs):
        raise OSError('native watches unavailable')
    monkeypatch.setattr(watchfiles, 'watch', unavailable)
    try:
        assert history.scan()['sessions'][0]['title'] == 'Root'
        write_json(directory / 'metadata.json', {'working_dir': str(workspace), 'name': 'Stat fallback'})
        assert history.scan()['sessions'][0]['title'] == 'Stat fallback'
        session(home, workspace, 'root_child', {'parent_id': 'root'})
        assert history.scan()['workerSessionCount'] == 1
    finally:
        history.close()


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
    ('internal-root', {'parent_id': None, 'session_visibility': 'internal'}, {}, 'internal', None),
    ('internal-child', {'parent_id': 'root', 'session_visibility': 'internal'}, {}, 'internal', 'root'),
    ('declared-internal', {}, {'session_visibility': 'internal'}, 'internal', None),
    ('real-root', {'parent_id': None}, {'session_visibility': 'internal'}, 'root', None),
    ('real-root', {'session_visibility': 'unknown', 'session_purpose': 'memory-suggestion'}, {}, 'root', None),
    ('human-named-root', {'name': 'Conversation abcd', 'title': 'Memory suggestion', 'origin': 'agent'}, {}, 'root', None),
    ('fork-internal', {'session_visibility': 'internal', 'parent_id': 'job', 'forked_from_turn': 1, 'forked_at': '2026-01-01T00:00:00Z'}, {}, 'root', 'job'),
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
    assert result['internalSessionCount'] == (1 if kind == 'internal' else 0)
    assert result['workspaces'][0]['internalSessionCount'] == result['internalSessionCount']
    if kind == 'internal':
        assert row['canResume'] is False
        assert 'Internal job history' in row['readOnlyReason']
    elif kind == 'worker':
        assert row['canResume'] is False
        assert 'Worker sessions' in row['readOnlyReason']
    elif '_' not in identity:
        assert row['canResume'] is True


def test_internal_purpose_is_bounded_metadata_not_prompt_or_credential_text(tmp_path):
    home, workspace = tmp_path / 'amplifier', tmp_path / 'workspace'
    workspace.mkdir()
    for identity, purpose in [('valid', 'memory.suggestion'), ('invalid', 'private free text ' * 200)]:
        session(home, workspace, identity, {'working_dir': str(workspace), 'bundle': 'work',
            'session_visibility': 'internal', 'session_purpose': purpose})
    before = {str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()}
    rows = {row['nativeIdentity']: row for row in NativeHistory(home).scan()['sessions']}
    assert rows['valid']['sessionPurpose'] == 'memory.suggestion'
    assert 'sessionPurpose' not in rows['invalid']
    assert all(row['sessionKind'] == 'internal' for row in rows.values())
    assert before == {str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()}


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



def test_unchanged_catalog_skips_file_owner_sweep_but_removal_and_recreation_refresh(tmp_path, monkeypatch):
    import shutil
    home = tmp_path / 'home'
    first_workspace, second_workspace = tmp_path / 'one', tmp_path / 'two'
    first = session(home, first_workspace, 'root', {'working_dir': str(first_workspace), 'name': 'First'})
    second = session(home, second_workspace, 'root', {'working_dir': str(second_workspace), 'name': 'Second'})
    history = NativeHistory(home)
    history.scan()
    history.scan()  # Include workspace paths learned from metadata.
    original = Path.relative_to
    sweeps = []
    def observed(path, other, *args, **kwargs):
        if other == home / 'projects':
            sweeps.append(path)
        return original(path, other, *args, **kwargs)
    monkeypatch.setattr(Path, 'relative_to', observed)
    history.scan(force=True)
    assert sweeps == []
    assert history._file_projects == {project_slug(first_workspace), project_slug(second_workspace)}
    shutil.rmtree(first.parent.parent)
    remaining = history.scan(force=True)
    assert [row['title'] for row in remaining['sessions']] == ['Second']
    assert sweeps and history._file_projects == {project_slug(second_workspace)}
    assert all(path.is_relative_to(second.parent.parent) for path in history._files)
    session(home, first_workspace, 'root', {'working_dir': str(first_workspace), 'name': 'Recreated'})
    restored = history.scan(force=True)
    assert {row['title'] for row in restored['sessions']} == {'Second', 'Recreated'}
    assert history._file_projects == {project_slug(first_workspace), project_slug(second_workspace)}
    write_json(second / 'metadata.json', {'working_dir': str(second_workspace), 'name': 'Edited externally'})
    assert {row['title'] for row in history.scan(force=True)['sessions']} == {'Edited externally', 'Recreated'}


def test_cached_session_moved_to_another_project_is_rediscovered(tmp_path):
    home = tmp_path / 'amplifier'
    old_workspace, new_workspace = tmp_path / 'old', tmp_path / 'new'
    old_workspace.mkdir()
    new_workspace.mkdir()
    old = session(home, old_workspace, 'root', {'working_dir': str(old_workspace), 'name': 'Before'})
    history = NativeHistory(home)
    history.scan(force=True)
    target_project = home / 'projects' / project_slug(new_workspace)
    old.parent.parent.rename(target_project)
    write_json(target_project / 'sessions' / 'root' / 'metadata.json',
               {'working_dir': str(new_workspace), 'name': 'Moved'})

    moved = history.scan(force=True)
    assert len(moved['sessions']) == 1
    assert moved['sessions'][0]['name'] == 'Moved'
    assert moved['sessions'][0]['workspace'] == str(new_workspace)
    assert history._file_projects == {project_slug(new_workspace)}
    assert all(path.is_relative_to(target_project) for path in history._files)


def test_explicit_chat_creation_beats_stale_internal_capture():
    from amplifier_web.native_history import classify_session
    assert classify_session('root', {'session_visibility': 'chat'},
                            {'session_visibility': 'internal'}) == ('root', None)
    # A child remains a child; a root declaration cannot erase explicit lineage.
    assert classify_session('child', {'session_visibility': 'chat', 'parent_id': 'root'}) == ('worker', 'root')
