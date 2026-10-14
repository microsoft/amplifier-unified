"""Warm reconciliation retains membership until a bounded proof is complete."""
import json
import sqlite3
from contextlib import contextmanager

import pytest

from amplifier_web.native_catalog import NativeCatalog, CatalogRows, CatalogPatchRows
from test_native_directory import seed


def test_recovery_stage_is_bounded_current_and_preserves_targeted_writes(tmp_path):
    catalog = NativeCatalog(tmp_path / 'catalog.sqlite3', tmp_path / 'home')
    project, workspace = seed(catalog, tmp_path / 'workspace', 8)
    token = catalog.recovery_begin(project)
    catalog.recovery_seen(token, ['saved-00000', 'saved-00001'])
    assert catalog.recovery_missing(token, limit=2) == [('saved-00002', 'root'), ('saved-00003', 'root')]
    assert catalog.count() == 8  # A partial proof itself never removes rows.
    prior = {'workspace': workspace, 'sessions': CatalogRows(catalog, project)}
    changed = {**catalog.get(project, 'saved-00003'), 'title': 'Changed during scan'}
    added = {**changed, 'id': 'app-new', 'nativeIdentity': 'new-during-scan'}
    next_project = {'workspace': workspace, 'sessions': CatalogPatchRows(prior['sessions'], [changed, added])}
    catalog.save({project: next_project}, {project: prior}, {}, set())
    assert catalog.recovery_missing(token) == [(f'saved-{number:05d}', 'root') for number in (2, 4, 5, 6, 7)]
    assert catalog.recovery_missing(token, after='saved-00004') == [(f'saved-{number:05d}', 'root') for number in (5, 6, 7)]
    replacement = catalog.recovery_begin(project)
    with pytest.raises(ValueError, match='no longer current'):
        catalog.recovery_missing(token)
    with pytest.raises(ValueError, match='no longer current'):
        catalog.recovery_seen(token, ['saved-00002'])
    catalog.recovery_abort(token)  # An old cancellation cannot abort its replacement.
    assert len(catalog.recovery_missing(replacement)) == 9
    catalog.recovery_abort(replacement)
    with pytest.raises(ValueError, match='no longer current'):
        catalog.recovery_missing(replacement)
    assert catalog.count() == 9
    with pytest.raises(ValueError, match='at most 64'):
        catalog.recovery_facts(project, (str(i) for i in range(65)))


def test_recovery_facts_extract_only_small_stamps_and_preserve_uncertainty(tmp_path, monkeypatch):
    catalog = NativeCatalog(tmp_path / 'catalog.sqlite3', tmp_path / 'home')
    project, _ = seed(catalog, tmp_path / 'workspace', 5)
    base = catalog.home / 'projects' / project / 'sessions'
    metadata_stamp = ((1, 2, 3), None)
    files = {base / f'saved-{number:05d}' / 'metadata.json': (metadata_stamp, metadata, failed)
             for number, metadata, failed in [(0, {'_nativeFieldsVersion': 2, 'ignored': 'x' * 100000}, False),
                                              (1, {}, False), (2, {'_nativeFieldsVersion': 1}, False),
                                              (3, {'_nativeFieldsVersion': 2}, True)]}
    catalog.save({}, {}, files, set(files))
    with catalog.connect() as db:
        db.execute("UPDATE native_rows SET transcript_mtime=13,transcript_size=29,transcript_available=1 WHERE namespace=?", (catalog.namespace,))
    monkeypatch.setattr(NativeCatalog, '_row', staticmethod(lambda *_: pytest.fail('No dormant summary decode')))
    original = json.loads
    decoded = []
    def small_only(text, *args, **kwargs):
        assert len(text) < 128
        decoded.append(text)
        return original(text, *args, **kwargs)
    monkeypatch.setattr(json, 'loads', small_only)
    facts = catalog.recovery_facts(project, [f'saved-{i:05d}' for i in range(5)] + ['absent'])
    assert set(facts) == {f'saved-{i:05d}' for i in range(5)}
    assert facts['saved-00000']['files']['metadata.json'] == (metadata_stamp, True)
    assert facts['saved-00001']['files']['metadata.json'] == (metadata_stamp, True)
    assert facts['saved-00002']['files']['metadata.json'] == (metadata_stamp, False)
    assert facts['saved-00003']['files']['metadata.json'] == (metadata_stamp, False)
    assert facts['saved-00004']['files']['metadata.json'] == (None, False)
    assert facts['saved-00004']['files']['naming.json'] == (None, True)
    assert facts['saved-00000']['transcriptRevision'] == [13, 29]
    assert facts['saved-00000']['transcriptAvailable'] is True
    assert len(decoded) == 4


def test_old_layout_migrates_source_facts_once_then_warm_queries_cannot_read_bodies(tmp_path, monkeypatch):
    catalog = NativeCatalog(tmp_path / 'catalog.sqlite3', tmp_path / 'home')
    project, _ = seed(catalog, tmp_path / 'workspace', 2)
    path = catalog.home / 'projects' / project / 'sessions' / 'saved-00000' / 'metadata.json'
    stamp = [[1, 2, 3], None]
    with catalog.connect() as db:
        db.execute("UPDATE native_rows SET value=json_set(value,'$.transcriptRevision',json('[19,23]'),'$.transcriptAvailable',json('true'))")
        db.execute('INSERT INTO native_files VALUES(?,?,?)',
                   (catalog.namespace, str(path), json.dumps([stamp, {'_nativeFieldsVersion': 2, 'ignored': 'x' * 100000}, False])))
        for column in ('transcript_mtime', 'transcript_size', 'transcript_available'):
            db.execute('ALTER TABLE native_rows DROP COLUMN ' + column)
        db.execute('DROP TABLE native_file_sources')
        before = db.execute('SELECT identity,value FROM native_rows ORDER BY identity').fetchall()
    monkeypatch.setattr(NativeCatalog, '_row', staticmethod(lambda *_: pytest.fail('Migration must stay in SQLite')))
    import amplifier_web.native_catalog as owner
    monkeypatch.setattr(owner.uuid, 'uuid5', lambda *_: pytest.fail('Additive facts must not rebuild existing identity scalars'))
    original_loads = json.loads
    def small_only(text, *args, **kwargs):
        assert len(text) < 128
        return original_loads(text, *args, **kwargs)
    monkeypatch.setattr(json, 'loads', small_only)
    migrated = NativeCatalog(catalog.path, catalog.home)
    with migrated.connect() as db:
        assert db.execute('SELECT identity,value FROM native_rows ORDER BY identity').fetchall() == before
        assert db.execute('SELECT transcript_mtime,transcript_size,transcript_available FROM native_rows LIMIT 1').fetchone() == (19, 23, 1)
        assert db.execute('SELECT signature,valid FROM native_file_sources WHERE path=?', (str(path),)).fetchone() == ('[[1,2,3],null]', 1)
    connect = NativeCatalog.connect
    statements = []
    @contextmanager
    def guarded(self):
        with connect(self) as db:
            db.set_authorizer(lambda action, table, column, *_:
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table in {'native_rows', 'native_files'} and column == 'value'
                else sqlite3.SQLITE_OK)
            db.set_trace_callback(statements.append)
            yield db
    monkeypatch.setattr(NativeCatalog, 'connect', guarded)
    # Reopening the now-current schema must not repeat the payload migration.
    warm = NativeCatalog(catalog.path, catalog.home)
    for _ in range(2):
        facts = warm.recovery_facts(project, ['saved-00000', 'saved-00001'])
        assert facts['saved-00000']['transcriptRevision'] == [19, 23]
        assert facts['saved-00000']['files']['metadata.json'] == (((1, 2, 3), None), True)
    assert not any('json_extract' in sql.lower() for sql in statements)


def test_project_removal_reclaims_only_its_membership_stage(tmp_path):
    catalog = NativeCatalog(tmp_path / 'catalog.sqlite3', tmp_path / 'home')
    project, workspace = seed(catalog, tmp_path / 'workspace', 2)
    other, other_workspace = seed(catalog, tmp_path / 'other', 2)
    token, other_token = catalog.recovery_begin(project), catalog.recovery_begin(other)
    catalog.recovery_seen(token, ['saved-00000'])
    catalog.recovery_seen(other_token, ['saved-00000'])
    prior = {project: {'workspace': workspace, 'sessions': CatalogRows(catalog, project)},
             other: {'workspace': other_workspace, 'sessions': CatalogRows(catalog, other)}}
    journal = catalog.save({other: prior[other]}, prior, {}, set())
    assert journal['removed'] == [(project, 'saved-00000'), (project, 'saved-00001')]
    with pytest.raises(ValueError, match='no longer current'):
        catalog.recovery_missing(token)
    assert catalog.recovery_missing(other_token) == [('saved-00001', 'root')]
    with catalog.connect() as db:
        assert db.execute('SELECT count(*) FROM native_recovery_seen WHERE token=?', (token,)).fetchone()[0] == 0


@pytest.mark.parametrize('bundle', ['work', None])
def test_scalar_availability_controls_only_matching_root_reason(tmp_path, bundle):
    catalog = NativeCatalog(tmp_path / 'catalog.sqlite3', tmp_path / 'home')
    project, workspace = seed(catalog, tmp_path / 'workspace', 1)
    missing = 'The original workspace folder is no longer available.'
    with catalog.connect() as db:
        raw = db.execute('SELECT value FROM native_rows').fetchone()[0]
        value = {**json.loads(raw), 'bundle': bundle, 'readOnlyReason': missing, 'canResume': False}
        db.execute('UPDATE native_rows SET value=?,available=0', (json.dumps(value),))
    for row in (catalog.get(project, 'saved-00000'), catalog.lookup('saved-00000')[0], catalog.page()[0]):
        assert row['readOnlyReason'] == missing and not row['canResume']
    with catalog.connect() as db:
        db.execute('UPDATE native_rows SET available=1')
    for row in (catalog.get(project, 'saved-00000'), catalog.lookup('saved-00000')[0], catalog.page()[0]):
        assert row['readOnlyReason'] == (None if bundle else 'This session does not record a resumable bundle.')
        assert row['canResume'] == bool(bundle)
    with catalog.connect() as db:
        db.execute('UPDATE native_rows SET value=?,available=0', (json.dumps({**value, 'readOnlyReason': 'The saved transcript is not available yet.'}),))
    assert catalog.get(project, 'saved-00000')['readOnlyReason'] == 'The saved transcript is not available yet.'
