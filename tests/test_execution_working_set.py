"""Canonical current writers, borrowed aliases and post-commit ownership."""
import copy
import sqlite3

import pytest

from amplifier_web import execution, execution_records as records
from amplifier_web.cold_display import ColdRecord, MARKER, materialize
from amplifier_web.execution_preparation import ExecutionPreparation
from amplifier_web.execution_working_set import PendingExecution, current, exact, snapshot, working


@pytest.fixture
def saved(tmp_path):
    db = sqlite3.connect(tmp_path / 'app.sqlite3')
    db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
    tree = {'currentTurnId': 'turn', 'aggregateUsage': execution.rollup([]),
            'nodes': [{'id': str(i), 'kind': 'tool', 'phase': 'completed',
                       'turnId': 'old', 'detail': {'values': [i]}} for i in range(400)],
            'turns': [{'id': 'old', 'phase': 'completed', 'anchorMessageId': None},
                      {'id': 'turn', 'phase': 'running', 'anchorMessageId': None}],
            'retiredUsageNodes': []}
    reference = records.import_tree(db, ('chat',), tree)
    db.commit()
    session = ColdRecord({'id': 'chat', 'runtimeSessionId': 'native', 'workspace': '/workspace',
                          'messages': [], MARKER: {'execution': reference}}, db)
    yield db, session, tree
    db.close()


def save(db, session):
    owner = working(session)
    pending = PendingExecution(owner, owner.stage())
    db.commit()
    ExecutionPreparation().committed({session['id']: pending}, {session['id']: session})
    return pending.reference


def test_current_save_and_detached_history_read_do_not_admit_completed_rows(saved):
    db, session, original = saved
    assert len(current(session)['turns']) == 1
    assert current(session)['nodes'] == []
    assert snapshot(session) == original
    assert materialize(session, db)['execution'] == original
    assert not dict.__contains__(session, 'execution')
    current(session)['currentTurnId'] = 'next'
    reference = save(db, session)
    assert records.materialize(db, reference)['currentTurnId'] == 'next'
    assert current(session)['nodes'] == []


def test_metadata_only_detached_owner_keeps_history_cold_and_overlay_frozen(saved, monkeypatch):
    db, session, _ = saved
    from amplifier_web.cold_display import detached
    row = exact(session, 'nodes', {'id': '7'})
    row['detail']['values'].append('at capture')
    original = records.materialize
    def forbidden(*args, **kwargs):
        raise AssertionError('Metadata-only reads must not materialize execution history')
    monkeypatch.setattr(records, 'materialize', forbidden)
    selected = detached(session)
    assert selected['id'] == 'chat'
    assert selected.get('messages') == []
    assert not dict.__contains__(selected, 'execution')
    row['detail']['values'].append('after capture')
    monkeypatch.setattr(records, 'materialize', original)
    assert selected['execution']['nodes'][7]['detail']['values'] == [7, 'at capture']
    assert snapshot(session)['nodes'][7]['detail']['values'] == [7, 'at capture', 'after capture']
    assert not dict.__contains__(session, 'execution')


def test_borrowed_nested_and_cross_record_aliases_remain_live_across_saves(saved):
    db, session, _ = saved
    first = exact(session, 'nodes', {'id': '7'})
    second = exact(session, 'nodes', {'id': '9'})
    second['detail'] = first['detail']
    borrowed = first['detail']['values']
    save(db, session)
    borrowed.append('late correction')
    reference = save(db, session)
    actual = records.materialize(db, reference)
    assert actual['nodes'][7]['detail']['values'] == [7, 'late correction']
    assert actual['nodes'][9]['detail']['values'] == [7, 'late correction']
    assert actual['nodes'][8]['detail']['values'] == [8]


def test_full_mutable_read_transfers_current_aliases_to_legacy_contract(saved):
    db, session, _ = saved
    row = exact(session, 'nodes', {'id': '7'})
    full = session['execution']
    assert full['nodes'][7] is row
    row['detail']['values'].append('still borrowed')
    assert full['nodes'][7]['detail']['values'] == [7, 'still borrowed']
    assert working(session) is None and not hasattr(session, '_working_execution')


def test_failed_save_keeps_original_root_and_current_changes_for_retry(saved):
    db, session, original = saved
    owner = working(session)
    first = copy.deepcopy(owner.reference)
    exact(session, 'nodes', {'id': '7'})['detail']['values'].append('pending')
    pending = owner.stage()
    db.rollback()
    assert dict.get(session, MARKER)['execution'] == first
    assert owner.reference == first
    assert records.materialize(db, first) == original
    with pytest.raises(ValueError, match='unavailable'):
        records.materialize(db, pending)
    assert records.materialize(db, save(db, session))['nodes'][7]['detail']['values'] == [7, 'pending']


def test_explicit_current_deletion_preserves_other_historical_records(saved):
    db, session, original = saved
    row = exact(session, 'nodes', {'id': '7'})
    current(session)['nodes'].remove(row)
    actual = records.materialize(db, save(db, session))
    assert actual['nodes'] == original['nodes'][:7] + original['nodes'][8:]


def test_newer_live_completion_wins_over_promoted_old_accounting_receipt(saved):
    db, session, _ = saved
    owner = working(session)
    old = {'id': 'call', 'sessionId': 'chat', 'rootSessionId': 'chat', 'kind': 'llm',
           'producerId': 'producer', 'phase': 'running', 'revision': 1,
           'assignmentId': 'assignment', 'assignmentRevision': 3, 'usage': {'totalTokens': 2}}
    owner.current['retiredUsageNodes'].append(old)
    owner.current['nodes'].append({**old, 'revision': 2, 'turnId': 'turn', 'liveObservation': True,
                                   'phase': 'completed', 'endedAt': 12, 'usage': {'totalTokens': 9}})
    actual = records.materialize(db, save(db, session))['retiredUsageNodes']
    assert len(actual) == 1
    assert actual[0]['revision'] == 2 and actual[0]['phase'] == 'completed'
    assert actual[0]['usage']['totalTokens'] == 9 and actual[0]['assignmentRevision'] == 3


def test_stop_and_late_tool_correction_keep_completed_history_and_unknown_outcomes(saved):
    db, session, original = saved
    event = {'id': 'active', 'turnId': 'turn', 'sessionId': 'chat', 'rootSessionId': 'chat',
             'kind': 'tool', 'phase': 'running', 'revision': 1, 'output': {'data': 'started'}}
    execution.ingest(session, event)
    execution.finish(session, 'stopped')
    assert exact(session, 'nodes', event)['phase'] == 'stopped'
    assert exact(session, 'turns', {'id': 'turn'})['phase'] == 'stopped'
    save(db, session)
    execution.ingest(session, {**event, 'phase': 'completed', 'revision': 2, 'endedAt': 5,
                               'output': {'data': 'actual result'}})
    actual = records.materialize(db, save(db, session))
    assert actual['nodes'][-1]['output'] == {'data': 'actual result'}
    assert actual['nodes'][0]['detail'] == original['nodes'][0]['detail']
    assert actual['turns'][-1]['phase'] == 'stopped'


def test_runtime_replacement_and_workspace_move_keep_the_same_chat_records(saved):
    db, session, original = saved
    owner = working(session)
    session.update(runtimeSessionId='replacement-native', workspace='/other-workspace')
    assert working(session) is owner
    exact(session, 'nodes', {'id': '7'})['detail']['values'].append('after move')
    reference = save(db, session)
    restored = ColdRecord({'id': 'chat', 'runtimeSessionId': 'replacement-native',
                          'workspace': '/other-workspace', MARKER: {'execution': reference}}, db)
    assert snapshot(restored)['nodes'][7]['detail']['values'] == [7, 'after move']
    assert snapshot(restored)['nodes'][8] == original['nodes'][8]


def test_duplicate_legacy_ids_keep_complete_mutable_save_semantics(saved):
    db, session, original = saved
    from amplifier_web.cold_display import execution_reference
    from amplifier_web.session_projection import stored_execution
    duplicate = copy.deepcopy(original['nodes'][7])
    duplicate['detail'] = {'values': ['second occurrence']}
    original['nodes'].append(duplicate)
    reference = execution_reference(db, original)
    db.commit()
    legacy = ColdRecord({'id': 'chat', MARKER: {'execution': reference}}, db)
    assert working(legacy) is None
    assert legacy['execution']['nodes'] == original['nodes']
    alias = legacy['execution']['nodes'][-1]['detail']['values']
    alias.append('late edit')
    tree, _ = ExecutionPreparation().stored(db, legacy, legacy['execution'], {})
    assert tree == stored_execution(legacy['execution'])
    assert tree['nodes'][-1]['detail']['values'] == ['second occurrence', 'late edit']


def test_model_rollup_does_not_promote_disposable_historical_aggregates(saved):
    db, _, _ = saved
    from amplifier_web.session_projection import stored_execution
    tree = {'currentTurnId': 'new-turn', 'nodes': [], 'retiredUsageNodes': [], 'turns': [
        {'id': 'old-turn', 'phase': 'completed', 'anchorMessageId': None},
        {'id': 'new-turn', 'phase': 'running', 'anchorMessageId': None}]}
    for i in range(500):
        tree['nodes'].append({'id': str(i), 'kind': 'llm', 'liveObservation': True, 'producerId': 'producer',
                              'sessionId': 'chat', 'rootSessionId': 'chat', 'turnId': 'old-turn',
                              'phase': 'completed', 'endedAt': 10, 'revision': 1, 'usage': {'totalTokens': 1}})
    execution.refresh_usage(tree)
    reference = records.import_tree(db, ('chat',), stored_execution(tree))
    db.commit()
    session = ColdRecord({'id': 'chat', 'messages': [], MARKER: {'execution': reference}}, db)
    execution.ingest(session, {'id': 'new-call', 'kind': 'llm', 'turnId': 'new-turn', 'liveObservation': True,
                               'producerId': 'producer', 'sessionId': 'chat', 'rootSessionId': 'chat',
                               'phase': 'completed', 'endedAt': 11, 'usage': {'totalTokens': 3}})
    assert len(current(session)['nodes']) == 1
    assert current(session)['aggregateUsage']['totalTokens'] == 503
    actual = records.materialize(db, save(db, session))
    assert len(actual['nodes']) == 501 and len(actual['retiredUsageNodes']) == 501
    assert actual['aggregateUsage']['totalTokens'] == 503
