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


def test_usage_refresh_matches_complete_reducer_without_reading_retired_receipts(saved, monkeypatch):
    db, session, _ = saved
    from amplifier_web.execution_working_set import refresh_usage
    known = exact(session, 'nodes', {'id': '7'})
    known.update(kind='llm', usage={'inputTokens': 3, 'outputTokens': 2, 'costUsd': .1,
                                  'voiceSeconds': 1.25, 'usageFinalized': False})
    pending = exact(session, 'nodes', {'id': '9'})
    pending.update(kind='llm', parentId='7', turnId=None, phase='running', usage={})
    current(session)['retiredUsageNodes'].append({'id': 'receipt-only', 'sessionId': 'chat',
        'kind': 'llm', 'phase': 'completed', 'usage': {'inputTokens': 999}})
    expected = snapshot(session)
    execution.refresh_usage(expected)
    collections = records._root(db, working(session).reference)['collections']
    read = records._collection
    families = []
    def selected(db, collection, **kwargs):
        kind = next(kind for kind, value in collections.items() if value == collection)
        assert kind != 'retiredUsageNodes', 'Usage does not consume the admission receipt ledger'
        families.append(kind)
        return read(db, collection, **kwargs)
    with monkeypatch.context() as guard:
        guard.setattr(records, '_collection', selected)
        guard.setattr(records, 'materialize', lambda *args, **kwargs: pytest.fail('Unused full history read'))
        refresh_usage(session)
    assert families == ['nodes', 'turns']
    actual = snapshot(session)
    assert actual == expected
    assert actual['aggregateUsage']['calls'] == 2
    assert actual['aggregateUsage']['tokenPendingCalls'] == 1
    assert actual['aggregateUsage']['voiceIncompleteCalls'] == 1
    assert pending['turnId'] == 'old'


def test_usage_refresh_preserves_shared_current_aliases_and_late_edits(saved):
    db, session, _ = saved
    from amplifier_web.execution_working_set import refresh_usage
    first = exact(session, 'nodes', {'id': '7'})
    second = exact(session, 'nodes', {'id': '9'})
    shared = {'inputTokens': 3, 'costUnavailableReason': 'Synthetic unpriced call'}
    first.update(kind='llm', usage=shared)
    second.update(kind='llm', usage=shared)
    receipt = {'id': 'separate-receipt', 'sessionId': 'chat', 'kind': 'llm',
               'phase': 'completed', 'usage': shared}
    current(session)['retiredUsageNodes'].append(receipt)
    save(db, session)
    refresh_usage(session)
    assert first['usage'] is second['usage'] is receipt['usage'] is shared
    shared['inputTokens'] = 9
    expected = snapshot(session)
    execution.refresh_usage(expected)
    refresh_usage(session)
    assert snapshot(session) == expected
    assert current(session)['aggregateUsage']['inputTokens'] == 18
    assert current(session)['aggregateUsage']['costUnavailableReasons'] == ['Synthetic unpriced call']
    assert first['usage'] is second['usage'] is receipt['usage'] is shared


def test_changed_historical_parent_retains_its_complete_body(saved):
    db, session, original = saved
    from amplifier_web.execution_working_set import refresh_usage
    parent = original['nodes'][7]
    parent.update(output={'text': 'retained tool output'}, source={'identity': 'exact source'},
                  arbitrary={'nested': ['keep this field']})
    original['nodes'][9].update(kind='llm', parentId='7', phase=None, status='running',
                               usage={'inputTokens': 2, 'costUsd': .1})
    execution.refresh_usage(original)
    reference = records.import_tree(db, ('chat',), original)
    db.commit()
    session[MARKER]['execution'] = reference
    changed = exact(session, 'nodes', {'id': '9'})
    changed['usage']['inputTokens'] = 8
    expected = snapshot(session)
    execution.refresh_usage(expected)
    refresh_usage(session)
    admitted_parent = exact(session, 'nodes', {'id': '7'})
    assert admitted_parent['aggregateUsage']['inputTokens'] == 8
    for field in ('detail', 'output', 'source', 'arbitrary'):
        assert admitted_parent[field] == parent[field]
    assert snapshot(session) == expected
    assert records.materialize(db, save(db, session)) == expected


@pytest.mark.parametrize('source_flag', ['liveObservation', 'canonicalHistory', 'nativeHistory'])
def test_historical_turn_repair_carries_fresh_aggregate_with_source_flags(saved, source_flag):
    db, session, original = saved
    from amplifier_web.execution_working_set import refresh_usage
    for row in [*original['nodes'], *original['turns']]:
        row['aggregateUsage'] = execution.rollup([])
    original['nodes'][7].update(kind='worker', turnId=None, **{source_flag: True})
    original['nodes'][9].update(kind='llm', parentId='7', turnId=None, usage={'inputTokens': 2})
    reference = records.import_tree(db, ('chat',), original)
    db.commit()
    session[MARKER]['execution'] = reference
    expected = snapshot(session)
    execution.refresh_usage(expected)
    refresh_usage(session)
    parent = exact(session, 'nodes', {'id': '7'})
    assert parent['turnId'] == 'turn'
    assert parent['aggregateUsage']['inputTokens'] == 2
    assert parent['detail'] == original['nodes'][7]['detail']
    # A stored placement may later omit display aggregates. The complete
    # mutable view must still match the reducer before that projection.
    assert snapshot(session) == expected


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


def test_restart_without_pending_observation_does_not_load_execution(saved, monkeypatch):
    _, session, _ = saved
    from amplifier_web import cold_display
    from amplifier_web.capacity import restore_observation
    assert dict.get(session, MARKER)['execution']['pendingObservation'] is False
    monkeypatch.setattr(cold_display, 'load', lambda *args: pytest.fail('Unexpected full execution read'))
    monkeypatch.setattr(records, 'materialize', lambda *args, **kwargs: pytest.fail('Unexpected full execution read'))
    restore_observation(session)
    assert not hasattr(session, '_working_execution')
    assert not dict.__contains__(session, 'execution')


def test_configuration_pending_fact_includes_uncommitted_current_turn(saved):
    db, session, original = saved
    from types import SimpleNamespace
    from amplifier_web.next_message_configuration import pending_input
    for turn in original['turns']:
        turn['phase'] = 'completed'
    reference = records.import_tree(db, ('chat',), original)
    db.commit()
    session[MARKER] = {'execution': reference}
    assert reference['pendingWork'] is False
    current(session)['turns'].append({'id': 'new input', 'phase': 'running'})
    assert pending_input(SimpleNamespace(db=db), session)
    assert dict.get(session, MARKER)['execution']['pendingWork'] is False


def test_terminal_input_updates_every_saved_display_alias_without_reading_nodes(saved, monkeypatch):
    db, session, original = saved
    original['turns'] = [{'id': 'display-one', 'inputId': 'input', 'phase': 'completed'},
                         {'id': 'display-two', 'inputId': 'input', 'phase': 'completed'},
                         {'id': 'other', 'inputId': 'other-input', 'phase': 'completed'}]
    session[MARKER] = {'execution': records.import_tree(db, ('chat',), original)}
    db.commit()
    monkeypatch.setattr(records, 'materialize', lambda *args, **kwargs: pytest.fail('Unrelated history read'))
    execution.fail_generation(session, ['input'])
    actual = {row['id']: row for row in current(session)['turns']}
    assert set(actual) == {'display-one', 'display-two'}
    assert all(row['phase'] == 'error' for row in actual.values())
    assert current(session)['nodes'] == []
    save(db, session)
    assert records.read_record(db, dict.get(session, MARKER)['execution'], 'turns', records.key('turns', {'id': 'other'}))['phase'] == 'completed'


@pytest.mark.parametrize('request_shape', [None, {'tools': 1}])
def test_new_turn_duration_lookup_never_reads_completed_node_history(saved, monkeypatch, request_shape):
    _, session, _ = saved
    from amplifier_web import duration_hints
    monkeypatch.setattr(records, 'materialize', lambda *args, **kwargs: pytest.fail('Unrelated node history read'))
    monkeypatch.setattr(duration_hints, 'shape', lambda *args: request_shape)
    seen = []
    def estimate(turns, shape):
        seen.extend(turns)
        assert shape is request_shape and len(turns) <= 80
        return None
    monkeypatch.setattr(duration_hints, 'estimate', estimate)
    execution.ensure_turn(session, 'new input')
    assert bool(seen) == (request_shape is not None)
    assert current(session)['nodes'] == []


async def test_ordinary_model_admission_preserves_indexed_history_budget_and_replay_refusal(tmp_path, monkeypatch):
    from test_capacity import setup, call
    from amplifier_web.capacity import admission, usage_snapshot
    from amplifier_web.session_projection import stored_execution
    app, worker, sid = await setup(tmp_path, monkeypatch)
    try:
        session = app._session(sid)
        tree = {'currentTurnId': 'turn', 'turns': [{'id': 'turn', 'phase': 'running', 'anchorMessageId': None}],
                'nodes': [call(str(i), sid, rootSessionId=sid, turnId='turn', liveObservation=True,
                               producerId='producer') for i in range(500)], 'retiredUsageNodes': []}
        execution.refresh_usage(tree)
        reference = records.import_tree(app.db, (sid,), stored_execution(tree))
        app.db.commit()
        dict.pop(session, 'execution', None)
        dict.setdefault(session, MARKER, {})['execution'] = reference
        session['capacityProducerId'] = 'producer'
        assert usage_snapshot(session)['calls'] == 500
        assert not dict.__contains__(session, 'execution')
        admitted = call('new', sid, rootSessionId=sid, turnId='turn', liveObservation=True,
                        producerId='producer', phase='running', endedAt=None, usage={})
        assert (await admission(app, sid, {'call': admitted}))['allowed']
        assert not dict.__contains__(session, 'execution')
        assert dict.get(session, MARKER)['execution']['executionProjection'] == 2
        assert not (await admission(app, sid, {'call': admitted}))['allowed']
        result = usage_snapshot(session)
        assert result['calls'] == 501
        assert result['metrics']['totalTokens']['value'] == 5000
        assert result['metrics']['totalTokens']['pendingCalls'] == 1
        assert result['metrics']['totalTokens']['unknownCalls'] == 0
    finally:
        await worker.close()
        await app.close()


async def test_ordinary_web_send_claims_legacy_cold_execution_without_full_hydration(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web.session_projection import stored_execution
    from test_capacity import call
    from test_service import Runtime
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    home = tmp_path / 'app'
    seed = AppService(home, workspace=tmp_path)
    await seed.dispatch('session.create', {'title': 'Legacy retained execution'})
    session = seed._session()
    sid = session['id']
    tree = {'nodes': [call(str(i), sid, rootSessionId=sid, turnId='old', liveObservation=True,
                           producerId='synthetic-producer')
                      for i in range(160)],
            'turns': [{'id': 'old', 'phase': 'completed', 'anchorMessageId': None}],
            'retiredUsageNodes': [], 'currentTurnId': None}
    execution.refresh_usage(tree)
    expected = stored_execution(tree)
    assert len(expected['nodes']) == 160, 'The fixture needs valid owned placements, not unbound live rows'
    session['execution'] = tree
    seed._publish_changes(sessions={sid})
    await seed.close()

    runtime = Runtime()
    app = AppService(home, runtime, workspace=tmp_path)
    try:
        session = app._session(sid, hydrate=False)
        assert not dict.__contains__(session, 'execution')
        assert dict.get(session, MARKER)['execution']['executionProjection'] == 1
        assert not hasattr(session, '_working_execution')
        original = ColdRecord.__getitem__
        def no_full_owner_read(row, key):
            if row is session and key == 'execution':
                pytest.fail('Ordinary send promoted the complete execution owner')
            return original(row, key)
        app.clients.attach('execution-web', kind='web')
        with monkeypatch.context() as guard:
            guard.setattr(ColdRecord, '__getitem__', no_full_owner_read)
            with app.clients.bind('execution-web'):
                await app.dispatch('session.select', {'id': sid})
                result = await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'Continue'},
                                            command_id='ordinary-input')
                assert result['accepted']
        assert len(runtime.sent) == 1
        sent_session, sent_text, input_id = runtime.sent[0]
        assert (sent_session, sent_text) == (sid, 'Continue')
        assert any(message.get('inputId') == input_id and message.get('role') == 'user'
                   and message.get('text') == 'Continue' for message in session['messages'])
        assert any(message.get('inputId') == input_id and message.get('role') == 'assistant'
                   and message.get('text') == 'Test transport result' for message in session['messages'])
        assert not dict.__contains__(session, 'execution')
        assert getattr(session, '_working_execution', None) is not None
        reference = dict.get(session, MARKER)['execution']
        assert reference['executionProjection'] == 2
        saved = records.materialize(app.db, reference)
        assert saved['nodes'] == expected['nodes']
        assert saved['retiredUsageNodes'] == expected['retiredUsageNodes']
        # This existing transport fixture emits a reply and terminal receipt,
        # but no generation.started/input.delivered execution boundary.
        assert saved['turns'] == expected['turns']
    finally:
        await app.close()


async def test_empty_collaboration_drain_keeps_current_execution_owner(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title': 'No queued collaboration'})
        session = app._session(hydrate=False)
        sid = session['id']
        tree = {'nodes': [{'id': 'old', 'kind': 'tool', 'phase': 'completed'}],
                'turns': [], 'retiredUsageNodes': []}
        reference = records.import_tree(app.db, (sid,), tree)
        app.db.commit()
        dict.pop(session, 'execution', None)
        dict.setdefault(session, MARKER, {})['execution'] = reference
        owner = working(session)
        assert owner is not None and owner.current['nodes'] == []
        assert app.collaboration.fair_rows(sid, active=False) == []
        original = ColdRecord.__getitem__
        def no_full_owner_read(row, key):
            if row is session and key == 'execution':
                pytest.fail('An empty collaboration drain promoted execution history')
            return original(row, key)
        with monkeypatch.context() as guard:
            guard.setattr(ColdRecord, '__getitem__', no_full_owner_read)
            await app.collaboration.drain(sid)
        assert getattr(session, '_working_execution', None) is owner
        assert not dict.__contains__(session, 'execution')
        assert dict.get(session, MARKER)['execution'] == reference
        assert records.materialize(app.db, reference) == tree
    finally:
        await app.close()


@pytest.mark.parametrize('restriction', ['none', 'foreign-host', 'pending', 'unknown',
                                        'moved', 'stopped', 'stopped-before-write'])
async def test_real_runtime_send_placement_guard_keeps_execution_cold(tmp_path, monkeypatch, restriction):
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.service import AppService
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    runtime = RuntimeManager()
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    started, sent = [], []
    try:
        await app.dispatch('session.create', {}, include_state=False)
        session = app._session(hydrate=False)
        sid = session['id']
        tree = {'currentTurnId': 'old-turn', 'turns': [
            {'id': 'old-turn', 'phase': 'completed', 'anchorMessageId': None}],
            'nodes': [{'id': str(i), 'kind': 'tool', 'turnId': 'old-turn',
                       'phase': 'completed', 'detail': {'values': [i]}} for i in range(160)],
            'retiredUsageNodes': [{'id': 'receipt-' + str(i), 'sessionId': sid,
                                  'kind': 'llm', 'phase': 'completed', 'usage': {'inputTokens': i}}
                                 for i in range(80)]}
        reference = records.import_tree(app.db, (sid,), tree)
        app.db.commit()
        dict.pop(session, 'execution', None)
        dict.setdefault(session, MARKER, {})['execution'] = reference
        owner = working(session)
        owner.current['nodes'].append({'id': 'current', 'kind': 'tool', 'phase': 'running',
                                       'turnId': 'old-turn', 'detail': {'values': ['before']}})
        alias = owner.current['nodes'][0]['detail']['values']
        # Native startup gets its complete detached request; reading it must
        # not transfer historical mutation authority from the host owner.
        request = copy.deepcopy(session)
        assert len(request['execution']['nodes']) == 161
        assert len(request['execution']['retiredUsageNodes']) == 80
        assert runtime._execution_state.__self__ is app.worktrees
        if restriction == 'foreign-host':
            session['executionHost'] = {'scope': 'local', 'id': 'different-fixture-host'}
            reason = 'execution host differs'
        elif restriction in {'pending', 'unknown'}:
            session['worktreeHandoffs'] = [{'phase': restriction}]
            reason = 'handoff is pending or unconfirmed'
        elif restriction == 'moved':
            session['executionRevision'] = request.get('executionRevision', 0) + 1
            reason = 'execution folder changed'
        elif restriction in {'stopped', 'stopped-before-write'}:
            reason = 'input was stopped'
            if restriction == 'stopped':
                session['interruptionRevision'] = request.get('interruptionRevision', 0) + 1

        async def prepare(selected, emit, *, admission_check):
            admission_check()
            started.append(selected['id'])
            if restriction == 'stopped-before-write':
                session['interruptionRevision'] = request.get('interruptionRevision', 0) + 1
            return None

        async def write(identity, operation, *, guard, **arguments):
            guard()
            sent.append((identity, operation, arguments['input_id']))
            return {'accepted': True}

        async def emit(*args):
            pytest.fail('The admission-only fixture must not start a native worker')

        # Only the final process preparation/IPC endpoints are replaced. The
        # production Send, placement binding, fences and Stop guards all run.
        monkeypatch.setattr(runtime, '_start_for_input', prepare)
        monkeypatch.setattr(runtime, '_request', write)
        if restriction == 'none':
            assert await runtime.send(request, 'synthetic input', 'input', emit) == {'accepted': True}
            assert started == [sid] and sent == [(sid, 'send', 'input')]
        else:
            with pytest.raises((ValueError, RuntimeError), match=reason):
                await runtime.send(request, 'synthetic input', 'input', emit)
            assert sent == []
            assert started == ([sid] if restriction == 'stopped-before-write' else [])
        assert not dict.__contains__(session, 'execution')
        assert getattr(session, '_working_execution', None) is owner
        assert len(owner.current['nodes']) == 1 and owner.current['retiredUsageNodes'] == []
        alias.append('late edit')
        assert snapshot(session)['nodes'][-1]['detail']['values'] == ['before', 'late edit']
        assert request['execution']['nodes'][-1]['detail']['values'] == ['before']
        assert records.materialize(app.db, reference) == tree
    finally:
        await app.close()
