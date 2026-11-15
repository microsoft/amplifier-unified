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
