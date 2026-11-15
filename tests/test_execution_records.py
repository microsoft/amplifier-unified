"""Immutable execution page ownership, late edits and current-update work."""
import copy
import sqlite3

import pytest

from amplifier_web import execution_records as records, resource_files


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / 'app.sqlite3')
    connection.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
    yield connection
    connection.close()


def tree(size=400):
    return {'currentTurnId': 'current', 'turns': [{'id': 'current', 'phase': 'running'}],
        'nodes': [{'id': str(i), 'sessionId': 'native', 'kind': 'tool',
                   'phase': 'completed', 'revision': 1, 'detail': {'values': [i]}}
                  for i in range(size)], 'retiredUsageNodes': []}


OWNER = ('chat', 'native', '/workspace')


def test_old_roots_and_late_nested_corrections_remain_exact(db):
    original = tree()
    first = records.import_tree(db, OWNER, original)
    db.commit()
    identity = records.key('nodes', original['nodes'][7])
    changed = records.read_record(db, first, 'nodes', identity, owner=OWNER)
    changed['detail']['values'].append('late correction')
    changed['revision'] += 1
    second = records.replace_records(db, first, {'nodes': {identity: changed}}, owner=OWNER)
    db.commit()
    expected = copy.deepcopy(original)
    expected['nodes'][7] = changed
    assert records.materialize(db, first, owner=OWNER) == original
    assert records.materialize(db, second, owner=OWNER) == expected
    changed['detail']['values'].append('not committed')
    assert records.materialize(db, second)['nodes'][7]['detail']['values'] == [7, 'late correction']


def test_append_delete_and_pending_facts_preserve_record_order(db):
    value = tree(2)
    first = records.import_tree(db, OWNER, value)
    added = {'id': 'new', 'sessionId': 'native', 'kind': 'llm', 'producerId': 'producer', 'phase': 'running'}
    second = records.replace_records(db, first, {'nodes': {
        records.key('nodes', value['nodes'][0]): None, records.key('nodes', added): added}})
    assert records.materialize(db, second)['nodes'] == [value['nodes'][1], added]
    assert second['pendingObservation'] and second['pendingWork']
    added.update(phase='completed', endedAt=1)
    third = records.replace_records(db, second, {'nodes': {records.key('nodes', added): added},
        'turns': {records.key('turns', value['turns'][0]): {'id': 'current', 'phase': 'completed'}}})
    assert not third['pendingObservation'] and not third['pendingWork']


def test_failed_transaction_keeps_committed_root_and_never_adopts_staged_pages(db):
    value = tree()
    first = records.import_tree(db, OWNER, value)
    db.commit()
    changed = {**value['nodes'][0], 'revision': 2}
    second = records.replace_records(db, first, {'nodes': {records.key('nodes', changed): changed}})
    db.rollback()
    assert records.materialize(db, first) == value
    with pytest.raises(ValueError, match='unavailable'):
        records.materialize(db, second)
    retry = records.replace_records(db, first, {'nodes': {records.key('nodes', changed): changed}})
    db.commit()
    assert records.materialize(db, retry)['nodes'][0] == changed


def test_update_reads_bounded_pages_and_existing_gc_can_follow_every_child(db, monkeypatch):
    value = tree(2000)
    first = records.import_tree(db, OWNER, value)
    db.commit()
    counts, original = [], records._read
    def read(db, reference):
        result = original(db, reference)
        counts.append(len(result.get('records', {})))
        return result
    monkeypatch.setattr(records, '_read', read)
    changed = {**value['nodes'][901], 'revision': 2}
    second = records.replace_records(db, first, {'nodes': {records.key('nodes', changed): changed}})
    assert max(counts) <= records.PAGE_ROWS
    assert sum(counts) < len(value['nodes']) // 2
    assert records.materialize(db, first) == value
    found, pending = set(), [second['$resource']]
    while pending:
        identity = pending.pop()
        if identity in found:
            continue
        found.add(identity)
        pending.extend(resource_files.references(original(db, {'$resource': identity})))
    assert first['$resource'] not in found and second['$resource'] in found
    assert len(found) > 3


def test_owner_and_identity_mismatch_leave_original_readable(db):
    value = tree(2)
    first = records.import_tree(db, OWNER, value)
    with pytest.raises(ValueError, match='different owner'):
        records.materialize(db, first, owner=('other', 'native', '/workspace'))
    with pytest.raises(ValueError, match='change its identity'):
        records.replace_records(db, first, {'nodes': {records.key('nodes', value['nodes'][0]): value['nodes'][1]}})
    assert records.materialize(db, first) == value


def test_duplicate_legacy_records_are_not_silently_collapsed(db):
    value = tree(1)
    value['nodes'].append(copy.deepcopy(value['nodes'][0]))
    with pytest.raises(ValueError, match='Duplicate execution'):
        records.import_tree(db, OWNER, value)


def test_ordered_windows_seek_without_scanning_deleted_history(db, monkeypatch):
    value = tree(3000)
    first = records.import_tree(db, OWNER, value)
    # A wide deleted prefix and gaps are not historical rows to walk on reads.
    kept = [3, 1001, 2003, 2999]
    second = records.replace_records(db, first, {'nodes': {
        records.key('nodes', row): None for i, row in enumerate(value['nodes']) if i not in kept}})
    db.commit()
    bodies, read = [], resource_files.read_detached
    def counted(source, **kwargs):
        result = read(source, **kwargs)
        bodies.append(result)
        return result
    monkeypatch.setattr(resource_files, 'read_detached', counted)
    result = records.read_window(db, second, 'nodes', start=1, limit=2, owner=OWNER)
    assert result == {'rows': [{'ordinal': i, 'value': value['nodes'][i]} for i in kept[1:3]], 'total': 4}
    assert len(bodies) < 20
    assert sum(len(body.get('order', [])) for body in bodies) <= 4


def test_lookup_index_stays_exact_on_correction_and_reinsertion_gets_new_ordinal(db):
    value = tree(500)
    first = records.import_tree(db, OWNER, value)
    identity = records.key('nodes', value['nodes'][7])
    changed = {**value['nodes'][7], 'revision': 2}
    corrected = records.replace_records(db, first, {'nodes': {identity: changed}})
    assert records._root(db, first)['collections']['nodes']['root'] == records._root(db, corrected)['collections']['nodes']['root']
    assert records._root(db, first)['collections']['nodes']['order'] != records._root(db, corrected)['collections']['nodes']['order']
    assert records.read_window(db, corrected, 'nodes', start=7, limit=1)['rows'] == [{'ordinal': 7, 'value': changed}]
    deleted = records.replace_records(db, corrected, {'nodes': {identity: None}})
    reinserted = records.replace_records(db, deleted, {'nodes': {identity: changed}})
    assert records.read_entry(db, reinserted, 'nodes', identity)['ordinal'] == 500
    assert records.read_window(db, reinserted, 'nodes', start=499, limit=1)['rows'] == [{'ordinal': 500, 'value': changed}]
    assert records.read_record(db, first, 'nodes', identity) == value['nodes'][7]


def test_ordered_window_has_byte_and_row_bounds(db):
    first = records.import_tree(db, OWNER, tree(10))
    with pytest.raises(ValueError, match='byte budget'):
        records.read_window(db, first, 'nodes', max_bytes=1)
    with pytest.raises(ValueError, match='bounded execution'):
        records.read_window(db, first, 'nodes', limit=records.PAGE_ROWS + 1)


def test_ordered_page_owns_complete_body_for_rebuild_and_bounded_reads(db):
    value = tree(3)
    value['nodes'][0].update(kind='llm', usage={'inputTokens': 3, 'voiceSeconds': 1.25,
                                              'usageFinalized': False}, output='retained detail ' * 1000)
    reference = records.import_tree(db, OWNER, value)
    root = records._root(db, reference)
    leaf = records._read(db, root['collections']['nodes']['order'])
    assert 'details' not in leaf
    for ordinal, entry in leaf['order']:
        assert entry['value'] == value['nodes'][ordinal]
    assert records.read_usage(db, reference, owner=OWNER) == {kind: value[kind] for kind in ('nodes', 'turns')}
    assert records.materialize(db, reference) == value
    assert records.read_window(db, reference, 'nodes')['rows'] == [
        {'ordinal': i, 'value': row} for i, row in enumerate(value['nodes'])]
    changed = copy.deepcopy(value['nodes'][0])
    changed['usage']['inputTokens'] = 4
    successor = records.replace_records(db, reference, {'nodes': {records.key('nodes', changed): changed}})
    changed_leaf = records._read(db, records._root(db, successor)['collections']['nodes']['order'])
    assert 'details' not in changed_leaf
    assert records.read_record(db, successor, 'nodes', records.key('nodes', changed)) == changed
    assert records.materialize(db, reference) == value
    # The full window pays for the body inside its existing request budget.
    with pytest.raises(ValueError, match='byte budget'):
        records.read_window(db, reference, 'nodes', max_bytes=8192)


def test_realistic_window_reads_contiguous_bodies_not_hashed_history(db, monkeypatch):
    value = tree(28000)
    for row in value['nodes']:
        row['detail']['text'] = 'representative retained placement ' * 15
    reference = records.import_tree(db, OWNER, value)
    db.commit()
    body_rows, byte_count, original = [], [], resource_files.read_detached
    def read(source, **kwargs):
        body = original(source, **kwargs)
        signature = resource_files.detached_signature(source)
        byte_count.append(signature[2] if len(signature) == 5 else len(source.indexed.encode()))
        if 'order' in body:
            body_rows.append(len(body['order']))
        return body
    monkeypatch.setattr(resource_files, 'read_detached', read)
    selected = records.read_window(db, reference, 'nodes', start=27900, limit=100, owner=OWNER)
    assert selected['rows'] == [{'ordinal': i, 'value': value['nodes'][i]} for i in range(27900, 28000)]
    assert len(body_rows) <= 2 and sum(body_rows) <= 2 * records.PAGE_ROWS
    assert sum(byte_count) < 1024 * 1024
