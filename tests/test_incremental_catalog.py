"""Changed native metadata stays scoped through durable publication."""
import json
import sqlite3
import time

import pytest

from amplifier_web.native_history import NativeHistory
from amplifier_web.session_files import project_slug
from test_native_history import session, write_json
from test_automatic_history import app_factory, native_session


def test_persistent_cache_reuses_compact_metadata_without_reading_bodies(tmp_path):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, 'saved-' + str(n),
                     {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Saved ' + str(n)})
             for n in range(80)]
    before = {(path / 'transcript.jsonl'): (path / 'transcript.jsonl').read_bytes() for path in paths}
    cache = tmp_path / 'app' / 'native-catalog.sqlite3'
    first = NativeHistory(home, cache_path=cache)
    token, initial = first.scan_changes()
    assert initial['reset'] and len(initial['sessions']) == 80
    first.close()
    second = NativeHistory(home, cache_path=cache)
    _, restarted = second.scan_changes()
    assert len(restarted['sessions']) == 80 and restarted['metadataReads'] == 0
    assert not restarted['issues']
    # Changed while stopped: only the changed compact source is reparsed.
    write_json(paths[9] / 'metadata.json', {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Changed'})
    third = NativeHistory(home, cache_path=cache)
    _, changed = third.scan_changes()
    assert changed['metadataReads'] == 1
    assert next(row for row in changed['sessions'] if row['nativeIdentity'] == 'saved-9')['title'] == 'Changed'
    assert all(path.read_bytes() == body for path, body in before.items())
    assert cache.stat().st_mode & 0o777 == 0o600


def test_missed_notifications_recover_in_bounded_slices_and_keep_sources(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, f'saved-{n:03d}',
                     {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Original'})
             for n in range(160)]
    index = NativeHistory(home, watch=True, cache_path=tmp_path / 'cache.sqlite3')
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True)
    write_json(paths[-1] / 'metadata.json',
               {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Missed event'})
    before = (paths[-1] / 'transcript.jsonl').read_bytes()
    index._recovery_budget = 12
    index._reconcile_at = 0
    observed = []
    for _ in range(100):
        base, delta = index.scan_changes(since=base)
        assert delta['reconciliation']['steps'] <= 12
        observed.extend(delta['sessions'])
        if delta['reconciliation']['phase'] == 'complete':
            break
    else:
        pytest.fail('Bounded reconciliation did not complete')
    assert any(row['title'] == 'Missed event' for row in observed)
    assert (paths[-1] / 'transcript.jsonl').read_bytes() == before
    assert index._catalog is not None
    index.close()


def test_transient_catalog_save_failure_reconnects_without_restart(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    path = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    index = NativeHistory(home, cache_path=tmp_path / 'cache.sqlite3')
    base, _ = index.scan_changes()
    def locked(*args):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(index._catalog, 'save', locked)
    write_json(path / 'metadata.json', {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'New'})
    base, failed = index.scan_changes(since=base)
    assert index._catalog is None and {'kind': 'unavailable-catalog-cache'} in failed['issues']
    index._cache_retry_at = 0
    _, recovered = index.scan_changes(since=base)
    assert index._catalog is not None and not index._cache_error
    assert index.scan()['sessions'][0]['title'] == 'New'
    assert {'kind': 'unavailable-catalog-cache'} not in recovered['issues']


def test_transient_catalog_retry_deadline_wakes_quiet_watcher(tmp_path, monkeypatch):
    from types import SimpleNamespace
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    index = NativeHistory(home, cache_path=tmp_path / 'cache.sqlite3')
    index.scan_changes()
    info = (home / 'projects').stat()
    index._watch = SimpleNamespace(unchanged=lambda: True, close=lambda: None)
    index._watch_root = info.st_dev, info.st_ino
    index._known_input = frozenset()
    index._reconcile_at = time.monotonic() + 60
    index._catalog = None
    index._cache_error = True
    index._cache_retry_at = time.monotonic() + 30
    assert not index.needs_scan([])
    index._cache_retry_at = 0
    assert index.needs_scan([])
    index.close()


def test_due_recovery_does_not_make_one_dirty_session_probe_whole_project(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, f'saved-{n:03d}',
                     {'working_dir': str(workspace), 'bundle': 'anchors'}) for n in range(160)]
    index = NativeHistory(home, watch=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True)
    index._recovery_budget = 12
    index._reconcile_at = 0
    probes = []
    original = index._native_metadata
    monkeypatch.setattr(index, '_native_metadata',
                        lambda path, *args: (probes.append(path), original(path, *args))[1])
    monkeypatch.setattr(index, '_invalidations',
                        lambda: (True, {(project_slug(workspace), 'saved-007', 'transcript')}))
    with (paths[7] / 'transcript.jsonl').open('a') as source:
        source.write('changed\n')
    _, delta = index.scan_changes(since=base)
    assert len(probes) <= 13
    assert delta['reconciliation']['steps'] <= 12
    assert any(row['nativeIdentity'] == 'saved-007' for row in delta['sessions'])


@pytest.mark.parametrize('ancestor', ['project', 'sessions'])
def test_substituted_ancestors_never_admit_outside_reads(tmp_path, monkeypatch, ancestor):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    saved = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    index = NativeHistory(home, watch=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, initial = index.scan_changes(force=True)
    project = saved.parent.parent
    substituted = project if ancestor == 'project' else saved.parent
    retained = substituted.with_name(substituted.name + '-retained')
    substituted.rename(retained)
    outside = tmp_path / 'outside'
    outside.mkdir()
    external = outside / 'sessions' if ancestor == 'project' else outside
    external.mkdir(exist_ok=True)
    (external / 'saved').mkdir()
    write_json(external / 'saved' / 'metadata.json', {'working_dir': str(workspace), 'name': 'Outside'})
    (external / 'saved' / 'transcript.jsonl').write_text('must not read')
    substituted.symlink_to(outside, target_is_directory=True)
    probes = []
    original = index._native_metadata
    monkeypatch.setattr(index, '_native_metadata',
                        lambda path, *args: (probes.append(path.resolve()), original(path, *args))[1])
    monkeypatch.setattr(index, '_invalidations',
                        lambda: (True, {(project_slug(workspace), 'saved', 'transcript')}))
    _, delta = index.scan_changes(since=base)
    assert all(not path.is_relative_to(outside) for path in probes)
    assert not any(row['title'] == 'Outside' for row in delta['sessions'])
    assert any(issue['kind'] == 'unreadable' for issue in delta['issues'])


def test_recovery_revalidates_leaf_after_suspension(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    saved = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    index = NativeHistory(home, watch=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True)
    index._recovery_budget = 3
    index._reconcile_at = 0
    base, _ = index.scan_changes(since=base)  # Suspended before the session read.
    outside = tmp_path / 'outside'
    outside.mkdir()
    write_json(outside / 'metadata.json', {'working_dir': str(workspace), 'name': 'Outside'})
    (outside / 'transcript.jsonl').write_text('outside')
    saved.rename(saved.with_name('saved-retained'))
    saved.symlink_to(outside, target_is_directory=True)
    probes = []
    original = index._native_metadata
    monkeypatch.setattr(index, '_native_metadata',
                        lambda path, *args: (probes.append(path.resolve()), original(path, *args))[1])
    for _ in range(8):
        base, _ = index.scan_changes(since=base)
    assert all(not path.is_relative_to(outside) for path in probes)
    index.close()


def test_hot_session_does_not_starve_missed_cold_recovery(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, f'saved-{n:03d}',
                     {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Original'})
             for n in range(160)]
    index = NativeHistory(home, watch=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True)
    write_json(paths[-1] / 'metadata.json',
               {'working_dir': str(workspace), 'bundle': 'anchors', 'name': 'Cold recovered'})
    index._recovery_budget = 12
    index._reconcile_at = 0
    monkeypatch.setattr(index, '_invalidations',
                        lambda: (True, {(project_slug(workspace), 'saved-000', 'transcript')}))
    seen = []
    for number in range(48):
        with (paths[0] / 'transcript.jsonl').open('a') as source:
            source.write(f'hot {number}\n')
        base, delta = index.scan_changes(since=base)
        seen.extend(delta['sessions'])
        if any(row['title'] == 'Cold recovered' for row in seen):
            break
    assert any(row['title'] == 'Cold recovered' for row in seen)
    index.close()


def test_suspended_recovery_cannot_restore_confirmed_removed_project(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    saved = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    cache = tmp_path / 'cache.sqlite3'
    index = NativeHistory(home, watch=True, cache_path=cache)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    base, _ = index.scan_changes(force=True)
    index._recovery_budget = 3
    index._reconcile_at = 0
    base, _ = index.scan_changes(since=base)
    project = saved.parent.parent
    project.rename(tmp_path / 'retained-project')
    monkeypatch.setattr(index, '_invalidations',
                        lambda: (True, {(project_slug(workspace), 'saved', 'transcript')}))
    removed = []
    for _ in range(5):
        base, delta = index.scan_changes(since=base)
        removed.extend(delta['removed'])
        assert project.name not in index._projects
        monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    assert (project.name, 'saved') in removed
    with sqlite3.connect(cache) as db:
        assert db.execute('SELECT count(*) FROM native_rows').fetchone()[0] == 0
    index.close()


def test_close_waits_for_inflight_recovery_slice(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    index = NativeHistory(tmp_path / 'native', watch=True)
    entered, release = threading.Event(), threading.Event()
    def blocked_recovery(known):
        entered.set()
        assert release.wait(3)
        yield
    monkeypatch.setattr(index, '_recover_projects', blocked_recovery)
    def scan():
        with index._lock:
            index._recovery_slice({})
    with ThreadPoolExecutor(max_workers=2) as workers:
        active = workers.submit(scan)
        assert entered.wait(3)
        closing = workers.submit(index.close)
        assert not closing.done()
        release.set()
        active.result(timeout=3)
        closing.result(timeout=3)
    assert index._recovery is None


def test_cached_load_can_cooperatively_stop_without_partial_snapshot(tmp_path, monkeypatch):
    from amplifier_web.native_catalog import NativeCatalog
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    for number in range(30):
        session(home, workspace, f'saved-{number}',
                {'working_dir': str(workspace), 'bundle': 'anchors'})
    cache = tmp_path / 'cache.sqlite3'
    first = NativeHistory(home, cache_path=cache)
    first.scan_changes()
    first.close()
    restarted = NativeHistory(home, cache_path=cache)
    original = NativeCatalog._check_running
    probes = []
    def interrupted(catalog):
        probes.append(True)
        if len(probes) == 5:
            restarted._closing.set()
        original(catalog)
    monkeypatch.setattr(NativeCatalog, '_check_running', interrupted)
    with pytest.raises(InterruptedError):
        restarted.scan_changes()
    assert restarted._snapshot_revision is None
    assert restarted._projects == {}
    with sqlite3.connect(cache) as db:
        assert db.execute('SELECT count(*) FROM native_rows').fetchone()[0] == 30
    restarted.close()


async def test_refresh_error_publication_failure_does_not_terminate_discovery(tmp_path, app_factory, monkeypatch):
    workspace = tmp_path / 'cli'
    native_session(workspace, 'saved-root')
    app = app_factory()
    publish = app._publish
    attempts = []
    def refused(**kwargs):
        attempts.append(kwargs)
        raise OSError('Presentation file unavailable')
    monkeypatch.setattr(app, '_publish', refused)
    # Both the ordinary save and the error report encounter the same disk fault.
    await app.history.refresh(force=False)
    assert len(attempts) == 2
    assert app.history._native_revision is None
    assert app.state['sharedHistory']['error']
    monkeypatch.setattr(app, '_publish', publish)
    await app.history.refresh(force=False)
    assert app.history._native_revision is not None
    assert app.state['sharedHistory']['error'] is None
    assert any(row.get('nativeIdentity') == 'saved-root' for row in app.state['sessions'])


async def test_recovery_slice_counters_do_not_publish_unchanged_library(tmp_path, app_factory, monkeypatch):
    workspace = tmp_path / 'cli'
    for number in range(160):
        native_session(workspace, f'saved-{number:03d}')
    app = app_factory()
    await app.history.refresh()
    await app.history.refresh(force=False)
    index = app.history.index
    index._recovery_budget = 12
    index._reconcile_at = 0
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    publishes = []
    original = app._publish
    def observed(**kwargs):
        publishes.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(app, '_publish', observed)
    for _ in range(60):
        await app.history.refresh(force=False)
        if index._recovery_status['phase'] == 'complete':
            break
    assert index._recovery_status['phase'] == 'complete'
    assert len(publishes) <= 2  # Running and completed freshness, not slice counters.


def test_delta_is_detached_retryable_and_unknown_token_resets(tmp_path):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, 'saved-' + str(n), {'working_dir': str(workspace), 'bundle': 'anchors'})
             for n in range(80)]
    index = NativeHistory(home)
    index.scan_changes()  # Learn the workspace before measuring a change.
    base, initial = index.scan_changes()
    with (paths[7] / 'transcript.jsonl').open('a') as file:
        file.write('one external append\n')
    token, delta = index.scan_changes(since=base)
    assert not delta['reset'] and delta['base'] is base
    assert [row['nativeIdentity'] for row in delta['sessions']] == ['saved-7']
    delta['sessions'][0]['transcriptRevision'][0] = -1
    same_token, retry = index.scan_changes(since=base)
    assert same_token is token and len(retry['sessions']) == 1
    assert retry['sessions'][0]['transcriptRevision'][0] != -1
    _, quiet = index.scan_changes(since=token)
    assert quiet['sessions'] == [] and quiet['workspaces'] == []
    _, reset = index.scan_changes(since=object())
    assert reset['reset'] and len(reset['sessions']) == 80
    public = index.scan()
    assert len(public['sessions']) == 80
    public['sessions'][0]['title'] = 'Detached'
    assert index.scan()['sessions'][0]['title'] != 'Detached'


def test_corrupt_cache_is_reported_without_replacing_source_or_cache(tmp_path):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    path = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    cache = tmp_path / 'broken.sqlite3'
    cache.write_bytes(b'not a database')
    index = NativeHistory(home, cache_path=cache)
    _, delta = index.scan_changes()
    assert len(delta['sessions']) == 1
    assert {'kind': 'unavailable-catalog-cache'} in delta['issues']
    assert cache.read_bytes() == b'not a database'
    assert (path / 'transcript.jsonl').read_text() == 'private conversation body\n'

@pytest.mark.parametrize('workspace_value', [
    {'path': 123}, {'path': []}, {'path': 'relative/path'}, {'available': 'yes'},
])
def test_malformed_cached_workspace_recovers_on_repeated_refresh(tmp_path, workspace_value):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    saved = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    canonical = {path: path.read_bytes() for path in saved.iterdir() if path.is_file()}
    cache = tmp_path / 'cache.sqlite3'
    first = NativeHistory(home, cache_path=cache)
    first.scan_changes()
    first.close()
    with sqlite3.connect(cache) as db:
        name, raw = db.execute('SELECT project,value FROM native_projects').fetchone()
        value = json.loads(raw)
        value['workspace'].update(workspace_value)
        db.execute('UPDATE native_projects SET value=? WHERE project=?', (json.dumps(value), name))
    rejected = cache.read_bytes()
    restarted = NativeHistory(home, cache_path=cache)
    try:
        for force in (False, True):
            _, delta = restarted.scan_changes(force=force)
            assert [row['nativeIdentity'] for row in delta['sessions']] == ['saved']
            assert {'kind': 'unavailable-catalog-cache'} in delta['issues']
            assert cache.read_bytes() == rejected
            assert all(path.read_bytes() == data for path, data in canonical.items())
    finally:
        restarted.close()

def test_transcript_notifications_probe_only_affected_session_on_repeated_changes(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    paths = [session(home, workspace, 'saved-' + str(n), {'working_dir': str(workspace), 'bundle': 'anchors'})
             for n in range(80)]
    index = NativeHistory(home)
    index.scan_changes()
    token, _ = index.scan_changes()
    slug = project_slug(workspace)
    index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(index, '_invalidations', lambda: (True, {(slug, 'saved-7', 'transcript')}))
    reads = []
    original = index._native_metadata

    def read(directory, *args):
        reads.append(directory.name)
        return original(directory, *args)

    monkeypatch.setattr(index, '_native_metadata', read)
    for number in range(2):
        with (paths[7] / 'transcript.jsonl').open('a') as file:
            file.write('append ' + str(number) + '\n')
        token, delta = index.scan_changes(since=token)
        assert [row['nativeIdentity'] for row in delta['sessions']] == ['saved-7']
    assert reads == ['saved-7', 'saved-7']


async def test_single_native_change_does_not_serialize_unrelated_app_views(tmp_path, app_factory, monkeypatch):
    workspace = tmp_path / 'cli'
    native_session(workspace, 'saved-root')
    child = native_session(workspace, 'saved-child', metadata={'parent_id': 'saved-root'})
    app = app_factory()
    await app.dispatch('session.create', {})
    await app.history.refresh()
    await app.history.refresh(force=False)
    app.projections.sessions(app.state)
    child_row = next(row for row in app.state['sessions'] if row.get('nativeIdentity') == 'saved-child')
    original_save = app._save
    scopes = []

    def save(*, session_ids=None):
        scopes.append(session_ids if session_ids is not None else app._publish_save_scope)
        return original_save(session_ids=session_ids)

    monkeypatch.setattr(app, '_save', save)
    from amplifier_web.browser_state import SessionIndex
    original_init = SessionIndex.__init__
    builds = []

    def build(self, state):
        builds.append(len(state['sessions']))
        original_init(self, state)

    monkeypatch.setattr(SessionIndex, '__init__', build)
    from amplifier_web.session_projection import view_path
    own = next(row for row in app.state['sessions'] if not row.get('historyManaged'))
    own_view = view_path(app.data_dir, own)
    before = own_view.read_bytes(), own_view.stat().st_mtime_ns
    app.history.index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(app.history.index, '_invalidations', lambda: (True, {project_slug(workspace)}))
    with (child / 'transcript.jsonl').open('a') as file:
        file.write(json.dumps({'role': 'assistant', 'content': 'One new answer'}) + '\n')
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    assert scopes == [{child_row['id']}]
    assert builds == []
    assert (own_view.read_bytes(), own_view.stat().st_mtime_ns) == before
    assert child_row['nativeRevision'][1] == (child / 'transcript.jsonl').stat().st_size
    assert len(app.history._catalog_rows) >= 2


async def test_scoped_history_commit_does_not_drop_pending_runtime_progress(tmp_path, app_factory, monkeypatch):
    from amplifier_web.session_projection import view_path
    workspace = tmp_path / 'cli'
    saved = native_session(workspace, 'saved-root')
    app = app_factory()
    await app.dispatch('session.create', {})
    await app.history.refresh()
    await app.history.refresh(force=False)
    own = next(row for row in app.state['sessions'] if not row.get('historyManaged'))
    own['draft'] = 'Unrelated progress must reach the durable view'
    app._progress_dirty = True
    # A later labelled writer cannot narrow an earlier unknown mutation.
    native_id = next(row['id'] for row in app.state['sessions'] if row.get('historyManaged'))
    await app.on_runtime_event('assistant.delta', {'sessionId': native_id, 'text': 'later delta'})
    app.history.index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(app.history.index, '_invalidations', lambda: (True, {project_slug(workspace)}))
    with (saved / 'transcript.jsonl').open('a') as file:
        file.write(json.dumps({'role': 'assistant', 'content': 'A new answer'}) + '\n')
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    assert json.loads(view_path(app.data_dir, own).read_text())['draft'] == own['draft']
    assert app._progress_dirty is False


@pytest.mark.parametrize('change', ['missing', 'corrupt', 'worker', 'internal'])
def test_cached_root_cannot_authorize_resume_without_fresh_metadata(tmp_path, change):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    metadata = {'working_dir': str(workspace), 'bundle': 'anchors', 'parent_id': None}
    saved = session(home, workspace, 'saved', metadata)
    cache = tmp_path / 'app' / 'cache.sqlite3'
    first = NativeHistory(home, cache_path=cache)
    source = first.scan()['sessions'][0]
    if change == 'missing':
        (saved / 'metadata.json').unlink()
    elif change == 'corrupt':
        (saved / 'metadata.json').write_text('{broken')
    else:
        write_json(saved / 'metadata.json', {**metadata,
                   'parent_id': 'other' if change == 'worker' else None,
                   'session_visibility': 'internal' if change == 'internal' else 'chat'})
    second = NativeHistory(home, cache_path=cache)
    second.scan_changes()
    before = {path: path.read_bytes() for path in saved.iterdir() if path.is_file()}
    with pytest.raises(ValueError):
        second.validate_resume(source)
    assert all(path.read_bytes() == data for path, data in before.items())


def test_transcript_only_notification_does_not_read_symlink_substitution(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'native', tmp_path / 'workspace'
    workspace.mkdir()
    saved = session(home, workspace, 'saved', {'working_dir': str(workspace), 'bundle': 'anchors'})
    index = NativeHistory(home)
    index.scan_changes()
    token, _ = index.scan_changes()
    saved.rename(saved.parent / '.original')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'metadata.json').write_text('{"name":"Outside"}')
    saved.symlink_to(outside, target_is_directory=True)
    index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(index, '_invalidations', lambda: (True, {(project_slug(workspace), 'saved', 'transcript')}))
    opened = []
    original = index._native_metadata
    monkeypatch.setattr(index, '_native_metadata', lambda directory, *args:
                        (opened.append(directory), original(directory, *args))[1])
    _, delta = index.scan_changes(since=token)
    assert saved not in opened
    assert delta['sessions'] == []
    assert delta['removed'] == [(project_slug(workspace), 'saved')]


async def test_local_commit_during_marker_read_rebuilds_aliases(tmp_path, app_factory, monkeypatch):
    import threading
    import asyncio
    import amplifier_web.automatic_history as module
    workspace = tmp_path / 'cli'
    saved = native_session(workspace, 'saved-root')
    app = app_factory()
    await app.history.refresh()
    await app.history.refresh(force=False)
    app.history.index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(app.history.index, '_invalidations', lambda: (True, {project_slug(workspace)}))
    started, release = threading.Event(), threading.Event()
    original = module.catalog_locations
    calls = []

    def paused(snapshot):
        calls.append(len(snapshot['sessions']))
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        return original(snapshot)

    monkeypatch.setattr(module, 'catalog_locations', paused)
    refresh = asyncio.create_task(app.history.refresh(force=False))
    assert await asyncio.to_thread(started.wait, 5)
    async with app.lock:
        own = app._new_session({'workspace': str(workspace)})
        own.update(runtimeSessionId='new-root', nativeIdentity='new-root',
                   nativeProject=project_slug(workspace))
        app.state['sessions'].append(own)
        app._publish()
    native_session(workspace, 'new-root')
    release.set()
    await refresh
    await app.history.refresh(force=False)
    records = [row for row in app.state['sessions'] if row.get('nativeIdentity') == 'new-root']
    assert len(records) == 1 and records[0] is own
    assert app.state['sharedHistory']['error'] is None


async def test_tombstone_totals_are_complete_and_removal_is_published(tmp_path, app_factory, monkeypatch):
    import shutil
    first, second = tmp_path / 'first', tmp_path / 'second'
    saved = native_session(first, 'first-root')
    native_session(second, 'second-root')
    app = app_factory()
    await app.history.refresh()
    await app.history.refresh(force=False)
    monkeypatch.setattr('amplifier_web.managed_deletion.tombstones',
                        lambda db: [{'project': 'already-deleted'}])
    app.history.index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(app.history.index, '_invalidations', lambda: (True, {project_slug(first)}))
    with (saved / 'transcript.jsonl').open('a') as stream:
        stream.write(json.dumps({'role': 'assistant', 'content': 'increment'}) + '\n')
    await app.history.refresh(force=False)
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['sessionCount'] == 2
    revision = app.state['revision']
    shutil.rmtree(saved.parent.parent)
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['sessionCount'] == 1
    assert app.state['revision'] > revision
    durable = json.loads(app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
    assert durable['sharedHistory']['sessionCount'] == 1
