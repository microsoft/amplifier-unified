"""Warm canonical reconciliation probes stamps, not dormant metadata payloads."""
import json
import shutil
import uuid

import pytest

from amplifier_web.native_catalog import NativeCatalog, SCALAR_COLUMNS, row_scalars
from amplifier_web.native_history import NativeHistory, _signature, _small_metadata
from amplifier_web.session_files import project_slug
from test_native_history import session, write_json


def seed_canonical(tmp_path, number):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    catalog = NativeCatalog(tmp_path / 'catalog.sqlite3', home)
    project = project_slug(workspace)
    wid = uuid.uuid5(uuid.NAMESPACE_URL, str(workspace)).hex
    summary = {'id': wid, 'nativeProject': project, 'path': str(workspace), 'name': workspace.name,
               'available': True, 'sessionCount': number, 'workerSessionCount': 0, 'internalSessionCount': 0}
    columns = ','.join(('namespace', 'project', 'identity', 'value', *SCALAR_COLUMNS))
    with catalog.connect() as db:
        db.execute('INSERT INTO native_projects VALUES(?,?,?)',
                   (catalog.namespace, project, json.dumps({'workspace': summary})))
        for position in range(number):
            identity = f'saved-{position:05d}'
            metadata = {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Original ' + str(position)}
            path = session(home, workspace, identity, metadata)
            transcript = _signature(path / 'transcript.jsonl')
            row = {'id': uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-session:{project}/{identity}').hex,
                   'nativeProject': project, 'nativeIdentity': identity, 'workspaceId': wid,
                   'workspace': str(workspace), 'name': metadata['name'], 'title': metadata['name'],
                   'description': '', 'bundle': 'work', 'sessionKind': 'root', 'parentId': None,
                   'createdAt': 1, 'updatedAt': transcript[1] / 1e9, 'recentActivityAt': transcript[1] / 1e9,
                   'transcriptRevision': list(transcript[1:]), 'transcriptAvailable': True,
                   'canResume': True, 'readOnlyReason': None}
            values = (catalog.namespace, project, identity, json.dumps(row), *row_scalars(row, summary))
            db.execute('INSERT INTO native_rows (' + columns + ') VALUES(' + ','.join('?' for _ in values) + ')', values)
            db.execute('INSERT INTO native_files VALUES(?,?,?)', (catalog.namespace, str(path / 'metadata.json'),
                json.dumps([[_signature(path / 'metadata.json'), None], _small_metadata(metadata), False])))
            db.execute('INSERT INTO native_file_sources VALUES(?,?,?,?)',
                (catalog.namespace, str(path / 'metadata.json'), json.dumps([_signature(path / 'metadata.json'), None]), 1))
    return home, workspace, catalog, project


def complete(index, *, token=None, maximum=3000):
    for _ in range(maximum):
        token, delta = index.scan_changes(since=token)
        assert delta['reconciliation']['steps'] <= index._recovery_budget
        if delta['reconciliation']['phase'] == 'complete':
            return token, delta
    pytest.fail('cooperative reconciliation did not complete')


def warm(home, catalog, monkeypatch):
    index = NativeHistory(home, watch=True, cache_path=catalog.path, directory=True)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    return index


def suspended_frame(index, name):
    generator = index._recovery
    while generator is not None and hasattr(generator, 'gi_frame'):
        if generator.gi_code.co_name == name:
            return generator.gi_frame.f_locals
        generator = generator.gi_yieldfrom
    return {}


def test_warm_27000_first_and_periodic_recovery_decode_only_changed_owner(tmp_path, monkeypatch):
    home, workspace, catalog, project = seed_canonical(tmp_path, 27000)
    decoded, files, batches = [], [], []
    original_row, original_file, original_facts = NativeCatalog._row, NativeCatalog.file, NativeCatalog.recovery_facts
    monkeypatch.setattr(NativeCatalog, '_row', staticmethod(lambda *args:
        (decoded.append(args[1]), original_row(*args))[1]))
    monkeypatch.setattr(NativeCatalog, 'file', lambda owner, path:
        (files.append(str(path)), original_file(owner, path))[1])
    monkeypatch.setattr(NativeCatalog, 'recovery_facts', lambda owner, project, ids:
        (batches.append(len(ids)), original_facts(owner, project, ids))[1])
    index = warm(home, catalog, monkeypatch)
    try:
        token, result = complete(index)
        assert result['sessionCount'] == 27000 and result['issues'] == []
        assert decoded == [] and max(batches) == 64
        assert not any('/sessions/' in path for path in files)
        assert len(index._files) <= 1 and not index._directory_recovery_changes
        # The first pass after restart and a normal later pass both use the
        # persisted signatures; process-local project stamp warming is irrelevant.
        index._reconcile_at = 0
        token, _ = complete(index, token=token)
        assert decoded == []
        path = home / 'projects' / project / 'sessions' / 'saved-12345'
        write_json(path / 'metadata.json', {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Missed rename'})
        index._reconcile_at = 0
        complete(index, token=token)
        assert set(decoded) == {'saved-12345'} and len(decoded) < 20
        assert index._catalog.get(project, 'saved-12345')['title'] == 'Missed rename'
        assert len(index._files) <= 4 and not index._directory_recovery_changes
        with catalog.connect() as db:
            assert db.execute('SELECT count(*) FROM native_recovery_seen').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM native_recovery_stages').fetchone()[0] == 0
    finally:
        index.close()


def test_partial_cancel_preserves_absent_rows_then_completed_recovery_removes_only_absence(tmp_path, monkeypatch):
    home, _, catalog, project = seed_canonical(tmp_path, 130)
    path = home / 'projects' / project / 'sessions' / 'saved-00129'
    shutil.rmtree(path)
    index = warm(home, catalog, monkeypatch)
    index._recovery_budget = 8
    index.scan_changes()
    assert catalog.count() == 130
    index.close()
    with catalog.connect() as db:
        assert db.execute('SELECT count(*) FROM native_recovery_seen').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM native_recovery_stages').fetchone()[0] == 0
    resumed = warm(home, catalog, monkeypatch)
    try:
        _, result = complete(resumed)
        assert result['sessionCount'] == catalog.count() == 129
        assert catalog.get(project, 'saved-00129') is None
    finally:
        resumed.close()


def test_warm_recovery_keeps_targeted_changes_and_new_members_during_scan(tmp_path, monkeypatch):
    home, workspace, catalog, project = seed_canonical(tmp_path, 130)
    index = warm(home, catalog, monkeypatch)
    index._recovery_budget = 8
    token, _ = index.scan_changes()
    write_json(home / 'projects' / project / 'sessions' / 'saved-00000' / 'metadata.json',
               {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Latest watched name'})
    session(home, workspace, 'new-watched', {'working_dir': str(workspace), 'bundle': 'work', 'name': 'New watched'})
    monkeypatch.setattr(index, '_invalidations', lambda: (True,
        {(project, 'saved-00000', 'metadata'), (project, 'new-watched', 'metadata')}))
    token, _ = index.scan_changes(since=token)
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    try:
        complete(index, token=token)
        assert catalog.get(project, 'saved-00000')['title'] == 'Latest watched name'
        assert catalog.get(project, 'new-watched')['title'] == 'New watched'
        assert catalog.count() == 131
    finally:
        index.close()


def test_suspended_changed_owner_cannot_replace_another_watched_patch(tmp_path, monkeypatch):
    home, workspace, catalog, project = seed_canonical(tmp_path, 3)
    base = home / 'projects' / project / 'sessions'
    write_json(base / 'saved-00000' / 'metadata.json',
               {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Missed A'})
    index = warm(home, catalog, monkeypatch)
    index._recovery_budget = 1
    token = None
    try:
        for _ in range(50):
            token, _ = index.scan_changes(since=token)
            if suspended_frame(index, '_recover_cached_batch').get('identity') == 'saved-00000':
                break
        else:
            pytest.fail('changed-owner read did not reach its controlled suspension')
        write_json(base / 'saved-00001' / 'metadata.json',
                   {'working_dir': str(workspace), 'bundle': 'work', 'name': 'Watched B'})
        monkeypatch.setattr(index, '_invalidations', lambda: (True, {(project, 'saved-00001', 'metadata')}))
        token, _ = index.scan_changes(since=token)
        monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
        complete(index, token=token)
        assert catalog.get(project, 'saved-00000')['title'] == 'Missed A'
        assert catalog.get(project, 'saved-00001')['title'] == 'Watched B'
        assert catalog.count() == 3
    finally:
        index.close()


def test_pending_deletion_is_not_counted_twice_during_missing_page(tmp_path, monkeypatch):
    from amplifier_web.native_catalog import CatalogPatchRows
    home, _, catalog, project = seed_canonical(tmp_path, 3)
    base = home / 'projects' / project / 'sessions'
    shutil.rmtree(base / 'saved-00001')
    shutil.rmtree(base / 'saved-00002')
    index = warm(home, catalog, monkeypatch)
    index._recovery_budget = 1
    token = None
    try:
        for _ in range(50):
            token, _ = index.scan_changes(since=token)
            if 'after' in suspended_frame(index, '_recover_cached_project_steps'):
                break
        else:
            pytest.fail('missing-page lookup did not reach its controlled suspension')
        def pending_deletion():
            current = index._projects[project]
            index._set_recovered_project(project, {'workspace': {**current['workspace'], 'sessionCount': 2},
                'sessions': CatalogPatchRows(current['sessions'], [], ['saved-00001'])})
            monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
            return True, set()
        monkeypatch.setattr(index, '_invalidations', pending_deletion)
        _, result = complete(index, token=token)
        assert result['sessionCount'] == catalog.count() == 1
        assert index._projects[project]['workspace']['sessionCount'] == 1
    finally:
        index.close()


@pytest.mark.parametrize('failure', ['denied', 'substituted'])
def test_absence_lookup_uncertainty_after_suspension_preserves_membership(tmp_path, monkeypatch, failure):
    home, _, catalog, project = seed_canonical(tmp_path, 3)
    base = home / 'projects' / project / 'sessions'
    missing = base / 'saved-00002'
    shutil.rmtree(missing)
    index = warm(home, catalog, monkeypatch)
    index._recovery_budget = 1
    token = None
    try:
        for _ in range(50):
            token, _ = index.scan_changes(since=token)
            if 'after' in suspended_frame(index, '_recover_cached_project_steps'):
                break
        else:
            pytest.fail('missing-page lookup did not reach its controlled suspension')
        if failure == 'denied':
            original = type(missing).stat
            def denied(path, *args, **kwargs):
                if path == missing:
                    raise PermissionError('Denied canonical absence probe')
                return original(path, *args, **kwargs)
            monkeypatch.setattr(type(missing), 'stat', denied)
        else:
            base.rename(base.with_name('prior-sessions'))
            base.mkdir()
        complete(index, token=token)
        assert catalog.count() == 3
        assert index._project_inputs[project][1]
    finally:
        index.close()


def test_unavailable_traversal_never_proves_absence_and_availability_is_point_normalized(tmp_path, monkeypatch):
    home, workspace, catalog, project = seed_canonical(tmp_path, 3)
    index = warm(home, catalog, monkeypatch)
    original = index._recovery_directories
    def broken(path):
        yield next(original(path))
        raise OSError('Interrupted directory traversal')
    monkeypatch.setattr(index, '_recovery_directories', broken)
    try:
        complete(index)
        assert catalog.count() == 3
        assert index._project_inputs[project][1]
        monkeypatch.setattr(index, '_recovery_directories', original)
        workspace.rmdir()
        index._reconcile_at = 0
        complete(index)
        assert not catalog.get(project, 'saved-00000')['canResume']
        workspace.mkdir()
        index._reconcile_at = 0
        complete(index)
        assert catalog.get(project, 'saved-00000')['canResume']
    finally:
        index.close()
