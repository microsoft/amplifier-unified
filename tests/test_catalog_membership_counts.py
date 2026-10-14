"""Catalog totals depend on canonical membership, not notification receipts."""
import json
import sqlite3

import pytest

from amplifier_web.history_directory import HistoryDirectory
from amplifier_web.native_catalog import NativeCatalog, CatalogRows, CatalogPatchRows
from test_history_directory_navigation import directory_fixture
from test_native_directory import seed


def catalog_fixture(tmp_path, count=12):
    state, directory = directory_fixture(tmp_path, 12)
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'native')
    project, workspace = seed(catalog, tmp_path / 'workspace', count)
    directory.bind(catalog)
    state['sessions'] = state['sessions'][:6]
    directory.service._state = state
    return state, directory, catalog, project, workspace


def canonical_count(directory, state):
    sql, params = directory.effective(state)
    return directory.db.execute(sql + " SELECT count(*) FROM directory_rows WHERE session_kind='root'", params).fetchone()[0]


def generation(directory):
    return directory.db.execute('SELECT membership_revision FROM history_directory_layout WHERE version=1').fetchone()[0]


def count_queries(directory):
    statements = []
    directory.db.set_trace_callback(lambda sql: statements.append(sql)
        if sql.startswith('WITH ') and 'SELECT count(*) FROM directory_rows' in sql else None)
    return statements


def test_27000_roots_do_not_recount_for_receipts_titles_status_or_admission(tmp_path, monkeypatch):
    state, directory, _, _, _ = catalog_fixture(tmp_path, 27000)
    queries = count_queries(directory)
    initial = directory.root_count(state)
    assert initial == 27011 and len(queries) == 1
    initial_generation = generation(directory)
    saved_receipts = dict(directory.db.execute('SELECT key,fingerprint FROM history_directory_notices'))
    assert saved_receipts
    def forbidden(*args, **kwargs):
        pytest.fail('Unrelated presentation change queried the complete catalog')
    monkeypatch.setattr(directory, 'effective', forbidden)
    monkeypatch.setattr('amplifier_web.history_directory_observations.revision', forbidden)
    monkeypatch.setattr(directory, 'decode', forbidden)
    for number in range(12):
        state['attentionRead'] = {**(saved_receipts if number % 2 else {}), 'unknown': str(number)}
        state['pinnedSessionIds'] = [f's{number}']
        state['sessions'][1].update(title=f'Renamed{number}', status='working' if number % 2 else 'idle',
                                    manualUnread=f'new-notice-{number}')
        assert directory.root_count(state) == initial  # Unsaved in-place values.
        directory.sync(state['sessions'], state)
        directory.db.commit()
        assert generation(directory) == initial_generation
        assert directory.root_count(state) == initial  # Actual durable save.
        # Eviction/readmission changes residency only, including when another
        # saved chat becomes the next member of the small active working set.
        saved = state['sessions'].pop()
        assert directory.root_count(state) == initial
        state['sessions'].append(saved)
        assert directory.root_count(state) == initial
    assert len(queries) == 1 and len(state['sessions']) == 6
    assert len(directory._root_count_memo) == 2


@pytest.mark.parametrize('change', ['role', 'implicit-parent', 'native-pair', 'project',
                                   'workspace-id', 'workspace-path', 'parent', 'canonical-role'])
def test_scalar_membership_changes_revalidate_and_match_canonical_count(tmp_path, change):
    state, directory, catalog, project, _ = catalog_fixture(tmp_path)
    row = state['sessions'][1]
    if change in {'project', 'canonical-role'}:
        row.update(nativeProject=project, nativeIdentity='saved-00000',
                   runtimeSessionId='saved-00000', historyManaged=True)
        if change == 'canonical-role':
            row.update(sessionKind='worker')  # Native root classification wins.
        directory.sync([row], state)
        directory.db.commit()
    if change == 'workspace-id':
        state['hiddenNativeWorkspaces'] = ['b']
    original = directory.root_count(state)
    before = generation(directory)
    if change == 'role': row['sessionKind'] = 'internal'
    elif change == 'implicit-parent':
        row.pop('sessionKind')
        row['nativeParentId'] = 'parent'
    elif change == 'native-pair':
        row.update(nativeProject=project, nativeIdentity='saved-00000', runtimeSessionId='saved-00000')
    elif change == 'project': row['nativeProject'] = 'new-project'
    elif change == 'workspace-id': row['workspaceId'] = 'b'
    elif change == 'workspace-path': row['workspace'] = '/projects/new-workspace'
    elif change == 'parent': row['parentId'] = 'fork-parent'
    elif change == 'canonical-role': row['historyManaged'] = False
    expected = canonical_count(directory, state)
    queries = count_queries(directory)
    assert directory.root_count(state) == expected
    assert len(queries) == 1
    directory.sync([row], state)
    assert generation(directory) == before + 1
    directory.db.commit()
    assert directory.root_count(state) == expected
    if change not in {'workspace-path', 'parent'}:
        assert expected != original


def test_local_add_remove_and_suppression_are_independent_of_residency(tmp_path):
    state, directory, _, project, _ = catalog_fixture(tmp_path)
    initial = directory.root_count(state)
    added = {'id':'new-local', 'sessionKind':'root', 'title':'New'}
    directory.sync([added], state)
    directory.db.commit()
    assert directory.root_count(state) == initial + 1
    directory.sync([], state, deleted={'new-local'})
    directory.db.commit()
    assert directory.root_count(state) == initial
    hidden = directory.db.execute("SELECT hidden_id FROM history_local_rows WHERE id='s1'").fetchone()[0]
    state['hiddenNativeSessions'] = [hidden]
    assert directory.root_count(state) == initial - 1
    state['hiddenNativeSessions'] = []
    assert directory.root_count(state) == initial
    directory.db.execute('INSERT INTO managed_deletions VALUES(?,?,?,?)',
        ('deletion', 'token', 'confirmed', json.dumps({'id':'deletion','project':project,'ids':['s1']})))
    directory.db.commit()
    assert directory.root_count(state) == initial - 13
    directory.db.execute('DELETE FROM managed_deletions WHERE id=?', ('deletion',))
    directory.db.commit()
    assert directory.root_count(state) == initial


def test_membership_generation_and_count_respect_rollback_and_pinned_snapshot(tmp_path):
    state, directory, catalog, project, workspace = catalog_fixture(tmp_path)
    state['sessions'] = []
    original = directory.root_count(state)
    initial_generation = generation(directory)
    reader = directory.read_snapshot(state)
    try:
        assert reader.root_count() == original
        row = directory.resolve('s1')
        row['sessionKind'] = 'worker'
        directory.sync([row], state)
        assert generation(directory) == initial_generation + 1
        assert directory.root_count(state) == original - 1
        directory.db.rollback()
        assert generation(directory) == initial_generation
        assert directory.root_count(state) == original
        row['sessionKind'] = 'root'
        row['title'] = 'Committed instead'
        directory.sync([row], state)
        directory.db.commit()
        assert generation(directory) == initial_generation
        before = {'workspace': workspace, 'sessions': CatalogRows(catalog, project)}
        catalog.save({project: {**before, 'sessions': CatalogPatchRows(before['sessions'], [], {'saved-00001'})}},
                     {project: before}, {}, set())
        assert directory.root_count(state) == original - 1
        assert reader.root_count() == original
    finally:
        reader.close()


def test_external_repair_and_native_rebind_cannot_reuse_stale_total(tmp_path):
    state, directory, _, _, _ = catalog_fixture(tmp_path)
    original = directory.root_count(state)
    with sqlite3.connect(tmp_path / 'app.sqlite3') as repair:
        repair.execute("DELETE FROM history_local_rows WHERE id='s8'")
    assert directory.root_count(state) == original - 1
    replacement = NativeCatalog(tmp_path / 'replacement.sqlite3', tmp_path / 'replacement')
    seed(replacement, tmp_path / 'replacement-workspace', 2)
    directory.bind(replacement)
    assert directory.root_count(state) == original - 11
    other_namespace = NativeCatalog(replacement.path, tmp_path / 'another-native-home')
    seed(other_namespace, tmp_path / 'another-workspace', 1)
    directory.bind(other_namespace)
    assert directory.root_count(state) == original - 12


def test_old_layout_upgrade_adds_only_generation_without_decoding_rows(tmp_path):
    state, directory = directory_fixture(tmp_path, 12)
    state['sessions'] = []
    directory.service._state = state
    directory.db.execute('ALTER TABLE history_directory_layout DROP COLUMN membership_revision')
    directory.db.execute("UPDATE history_local_rows SET summary='intentionally unreadable history'")
    directory.db.commit()
    original = directory.db.execute('SELECT id,summary FROM history_local_rows ORDER BY id').fetchall()
    statements = []
    directory.db.set_trace_callback(statements.append)
    upgraded = HistoryDirectory(directory.service)
    upgraded.db.commit()
    assert generation(upgraded) == 0
    assert upgraded.root_count(state) == 11
    assert directory.db.execute('SELECT id,summary FROM history_local_rows ORDER BY id').fetchall() == original
    assert not any('UPDATE history_local_rows' in sql or 'SELECT summary' in sql for sql in statements)
