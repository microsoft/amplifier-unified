"""Prospective output expiry preserves operation evidence and retries."""
import json

import pytest

from amplifier_operations import OperationJournal
from operation_observations import event, final, output

DAY = 86400


def observed(journal, identity, *, days=30, state='completed'):
    journal.ingest('s', 'r', event(identity), output_keep_days=days)
    journal.ingest('s', 'r', output(identity))
    journal.ingest('s', 'r', final(identity, state=state, returncode=0 if state == 'completed' else 1))
    return journal.status('s', identity)


@pytest.mark.parametrize('state', ['completed', 'failed', 'cancelled'])
def test_expiry_is_atomic_truthful_and_deduplicates_original_events(tmp_path, monkeypatch, state):
    clock = [1000]
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: clock[0])
    path = tmp_path / 'operations.db'
    journal = OperationJournal(path)
    before = observed(journal, 'p1', state=state)
    assert before['outputSettledAt'] == 1000 and before['endedAt'] == 2
    assert before['outputExpiresAt'] == 1000 + 30 * DAY
    clock[0] += 30 * DAY
    with monkeypatch.context() as patch:
        patch.setattr(journal, '_save', lambda value: (_ for _ in ()).throw(OSError('Fixture disk failure')))
        with pytest.raises(OSError):
            journal.expire_output('s', 'p1', before['revision'], clock[0])
    assert journal.status('s', 'p1') == before
    assert journal.read('s', 'p1')['chunks'][0]['text'] == 'hello\n'
    result = journal.expire_output('s', 'p1', before['revision'], clock[0])
    assert result == {'status': 'expired', 'removedOutputBytes': 6}
    page = journal.read('s', 'p1')
    assert page['state'] == state and page['returncode'] == before['returncode']
    assert page['outputAvailability'] == 'expired' and page['outputExpiredAt'] == clock[0]
    assert page['streamComplete'] and page['captureComplete'] and not page['outputComplete']
    assert page['chunks'] == [] and page['cursorGap'] and not page['hasMore']
    assert page['nextCursor'] == page['earliestCursor'] == page['latestCursor'] == 1
    assert page['retainedOutputBytes'] == page['retainedChunks'] == 0
    assert page['droppedOutputBytes'] == page['totalOutputBytes'] == 6
    assert journal.read('s', 'p1', 1)['outputAvailability'] == 'expired'
    assert not journal.read('s', 'p1', 1)['cursorGap']
    assert journal.ingest('s', 'r', output('p1')) == (journal.status('s', 'p1'), False)
    with pytest.raises(ValueError, match='Conflicting'):
        journal.ingest('s', 'r', output('p1', text='changed'))
    assert journal.expire_output('s', 'p1', before['revision'], clock[0])['status'] == 'already-expired'
    journal.close()
    restored = OperationJournal(path)
    try:
        restored.recover()
        assert restored.read('s', 'p1') == page
        assert restored.output_expiry_candidates(clock[0])['items'] == []
    finally:
        restored.close()


def test_legacy_unknown_interrupted_active_and_empty_output_are_not_due(tmp_path, monkeypatch):
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: 1000)
    journal = OperationJournal(tmp_path / 'operations.db')
    try:
        observed(journal, 'legacy', days=None)
        for state in ('outcome_unknown', 'interrupted'):
            observed(journal, state, state=state)
        journal.ingest('s', 'r', event('active'), output_keep_days=30)
        journal.ingest('s', 'r', output('active'))
        journal.ingest('s', 'r', event('empty'), output_keep_days=30)
        journal.ingest('s', 'r', final('empty', sequence=2, total_output_bytes=0))
        records = [journal.read('s', identity) for identity in ('legacy', 'outcome_unknown', 'interrupted', 'active', 'empty')]
        assert journal.output_expiry_candidates(1000 + 31 * DAY)['items'] == []
        assert records == [journal.read('s', row['id']) for row in records]
        # Enabling later cannot classify an existing operation retroactively.
        journal.ingest('s', 'r', event('started-off'))
        journal.ingest('s', 'r', output('started-off'), output_keep_days=30)
        journal.ingest('s', 'r', final('started-off'), output_keep_days=30)
        assert 'outputExpiresAt' not in journal.status('s', 'started-off')
        # Actual late final evidence can settle an unknown operation, then its
        # captured prospective lifetime starts at host-observed settlement.
        journal.ingest('s', 'r', final('outcome_unknown', sequence=4))
        assert [row['id'] for row in journal.output_expiry_candidates(1000 + 31 * DAY)['items']] == ['outcome_unknown']
    finally:
        journal.close()


def test_current_longer_policy_extends_but_shorter_cannot_reduce_captured_lifetime(tmp_path, monkeypatch):
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: 1000)
    journal = OperationJournal(tmp_path / 'operations.db')
    try:
        row = observed(journal, 'p1')
        assert journal.expire_output('s', 'p1', row['revision'], 1000 + 2 * DAY,
                                     keep_days=1)['status'] == 'protected'
        assert journal.expire_output('s', 'p1', row['revision'], 1000 + 31 * DAY,
                                     keep_days=60)['status'] == 'protected'
        assert journal.status('s', 'p1') == row
        assert journal.expire_output('s', 'p1', row['revision'], 1000 + 61 * DAY,
                                     keep_days=60)['status'] == 'expired'
    finally:
        journal.close()


def test_due_page_is_indexed_and_advances_past_protected_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr('amplifier_operations.journal.time.time', lambda: 1000)
    journal = OperationJournal(tmp_path / 'operations.db')
    try:
        for index in range(18):
            observed(journal, f'due-{index:02}')
        # Unrelated legacy records do not enter the deadline index or page.
        with journal.db:
            journal.db.executemany('INSERT INTO operations VALUES (?,?,?)',
                ((f'old-{i}', 'other', json.dumps({'id': f'old-{i}', 'state': 'completed', 'createdAt': i})) for i in range(2000)))
        statements = []
        journal.db.set_trace_callback(statements.append)
        first = journal.output_expiry_candidates(1000 + 31 * DAY)
        journal.db.set_trace_callback(None)
        query = next(text for text in statements if text.startswith('SELECT id,session_id'))
        plan = journal.db.execute('EXPLAIN QUERY PLAN ' + query).fetchall()
        assert any('operations_output_expiry' in str(row) for row in plan)
        assert not any('TEMP B-TREE' in str(row) or 'SCAN operations' in str(row) for row in plan)
        second = journal.output_expiry_candidates(1000 + 31 * DAY, after=first['nextCursor'])
        last = journal.output_expiry_candidates(1000 + 31 * DAY, after=second['nextCursor'])
        assert [len(page['items']) for page in (first, second, last)] == [8, 8, 2]
        assert last['nextCursor'] is None
        assert len({row['id'] for page in (first, second, last) for row in page['items']}) == 18
    finally:
        journal.close()
