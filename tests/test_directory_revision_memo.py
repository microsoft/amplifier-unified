"""Repeated active-chat reads reuse facts without freezing durable authority."""
import json

from amplifier_web.history_directory_observations import revision
from amplifier_web.native_catalog import NativeCatalog, CatalogRows, CatalogPatchRows
from test_history_directory_navigation import directory_fixture
from test_native_directory import seed


def native_fixture(tmp_path, count=12):
    state, directory = directory_fixture(tmp_path, 12)
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'native')
    project, workspace = seed(catalog, tmp_path / 'workspace', count)
    directory.bind(catalog)
    state['sessions'] = state['sessions'][:6]
    directory.service._state = state
    state['pinnedSessionIds'] = ['saved-00000', 's3']
    # Exercise both expensive revision lookups, including a valid read receipt.
    notice = directory.db.execute('SELECT key,fingerprint FROM history_directory_notices LIMIT 1').fetchone()
    state['attentionRead'] = {notice[0]: notice[1]}
    return state, directory, catalog, project, workspace


def scanned_queries(directory):
    seen = []
    def trace(sql):
        if sql.startswith('WITH ') and (' FROM directory_rows' in sql or ' FROM directory_notices' in sql):
            seen.append(1)
    directory.db.set_trace_callback(trace)
    return seen


def test_27000_directory_revisions_and_counts_do_not_rescan_for_six_chat_switches(tmp_path):
    state, directory, _, _, _ = native_fixture(tmp_path, 27000)
    scans = scanned_queries(directory)
    baseline = revision(directory, state)
    count = directory.root_count(state)
    assert count == 27011 and scans
    scans.clear()
    for index in range(24):
        # Browser-local selection/revision writes are not history mutations.
        state.update(selectedSessionId=state['sessions'][index % 6]['id'], revision=index)
        state['view']['draft'] = f'Private draft {index}'
        state['sessions'][1]['streaming'] = f'Unsettled delta {index}'
        directory.db.execute('CREATE TABLE IF NOT EXISTS client_fixture(value INTEGER)')
        directory.db.execute('INSERT INTO client_fixture VALUES(?)', (index,))
        directory.db.commit()
        assert revision(directory, state) == baseline
        assert directory.root_count(state) == count
    assert scans == []
    assert len(state['sessions']) == 6
    # There is one memo per reader, not one per selection/revision.
    assert len(directory._observation_revision_memo) == len(directory._root_count_memo) == 2
    returned = revision(directory, state)
    returned.clear()
    assert revision(directory, state) == baseline


def test_memo_observes_in_place_overlay_receipt_pin_and_suppression_changes(tmp_path):
    state, directory, _, _, _ = native_fixture(tmp_path)
    initial = revision(directory, state)
    scans = scanned_queries(directory)
    state['sessions'][1]['title'] = 'Changed without publication'
    changed = revision(directory, state)
    assert changed['sessions'] != initial['sessions'] and scans
    state['attentionRead'].clear()
    unread = revision(directory, state)
    assert unread['attention'] != changed['attention']
    state['pinnedSessionIds'].append('saved-00001')
    pinned = revision(directory, state)
    assert pinned['sessions'] != unread['sessions']
    original_count = directory.root_count(state)
    state.setdefault('hiddenNativeWorkspaces', []).append('a')
    assert directory.root_count(state) < original_count
    state['hiddenNativeWorkspaces'].clear()
    assert directory.root_count(state) == original_count
    # Tombstones can change without a state revision or resident mutation.
    directory.db.execute('INSERT INTO managed_deletions VALUES(?,?,?,?)',
        ('delete-one', 'token-one', 'confirmed', json.dumps({'id':'delete-one', 'project':'unrelated-project', 'ids':['saved-00001']})))
    directory.db.commit()
    assert directory.root_count(state) == original_count - 1
    directory.db.execute('DELETE FROM managed_deletions WHERE id=?', ('delete-one',))
    directory.db.commit()
    assert directory.root_count(state) == original_count


def test_native_commit_is_seen_without_app_publish_and_snapshot_stays_pinned(tmp_path):
    state, directory, catalog, project, workspace = native_fixture(tmp_path)
    first = revision(directory, state)
    count = directory.root_count(state)
    reader = directory.read_snapshot(state)
    try:
        frozen = revision(reader, reader._frozen_state)
        frozen_count = reader.root_count()
        before = {'workspace':workspace, 'sessions':CatalogRows(catalog, project)}
        renamed = {**catalog.get(project, 'saved-00001'), 'title':'Changed externally'}
        catalog.save({project:{**before, 'sessions':CatalogPatchRows(before['sessions'], [renamed], {'saved-00002'})}},
                     {project:before}, {}, set())
        assert revision(directory, state)['sessions'] != first['sessions']
        assert directory.root_count(state) == count - 1
        assert revision(reader, reader._frozen_state) == frozen
        assert reader.root_count() == frozen_count
    finally:
        reader.close()


def test_uncommitted_generation_is_never_reused_after_rollback(tmp_path):
    state, directory = directory_fixture(tmp_path, 12)
    state['sessions'] = []
    directory.service._state = state
    before = revision(directory, state)
    count = directory.root_count(state)
    changed = directory.resolve('s1')
    changed.update(sessionKind='worker', parentId='s2', title='Rolled back')
    directory.sync([changed], state)
    assert directory.db.in_transaction
    assert directory.root_count(state) == count - 1
    assert revision(directory, state) != before
    directory.db.rollback()
    assert revision(directory, state) == before
    assert directory.root_count(state) == count
    # A new transaction can reach the same generation with different facts.
    changed = directory.resolve('s1')
    changed['title'] = 'Committed instead'
    directory.sync([changed], state)
    directory.db.commit()
    assert revision(directory, state) != before
    assert directory.root_count(state) == count


def test_other_connection_local_write_and_native_rebinding_invalidate_memo(tmp_path):
    state, directory, catalog, project, _ = native_fixture(tmp_path)
    before = revision(directory, state)
    count = directory.root_count(state)
    import sqlite3
    other = sqlite3.connect(tmp_path / 'app.sqlite3')
    try:
        other.execute('DELETE FROM history_local_rows WHERE id=?', ('s8',))
        other.commit()
    finally:
        other.close()
    # External low-level writes cannot evade the root-count memo even when no
    # normal owning generation was advanced by this synthetic repair.
    assert directory.root_count(state) == count - 1
    replacement = NativeCatalog(tmp_path / 'replacement.sqlite3', tmp_path / 'native')
    seed(replacement, tmp_path / 'replacement-workspace', 2)
    scans = scanned_queries(directory)
    directory.bind(replacement)
    revision(directory, state)
    assert scans  # Equal generation numbers on a different backing are new facts.
    assert directory.root_count(state) == count - 11
