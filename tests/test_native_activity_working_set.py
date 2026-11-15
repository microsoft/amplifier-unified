"""Canonical native refresh qualifies no-op activity without taking history ownership."""
import copy
import json
import asyncio

import pytest

from amplifier_web import execution, execution_records as records
from amplifier_web.automatic_history import read_transcript, revision
from amplifier_web.cold_display import MARKER, ColdRecord, detached, execution_reference
from amplifier_web.execution_working_set import current, exact, snapshot, working
from amplifier_web.host.storage import SessionStore
from amplifier_web.native_activity import apply_activity
from amplifier_web.service import AppService
from test_execution_working_set import saved, save
from test_native_activity import fixture


@pytest.mark.parametrize('activity_kind', ['empty', 'shadowed', 'unshadowed'])
async def test_changed_native_revision_keeps_only_noop_activity_cold(tmp_path, monkeypatch, activity_kind):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    root, capture, native = fixture(tmp_path, monkeypatch)
    store = SessionStore(root.parent, shared=True)
    prefix, _ = store.load('native-root')
    prefix.append({'role': 'user', 'content': 'new inspection'})
    store.save('native-root', prefix, {'working_dir': native['workspace'], 'bundle': 'anchors'})
    (capture / 'events.jsonl').write_text('')
    initial = read_transcript(native)
    home = tmp_path / 'app'
    seed = AppService(home, workspace=native['workspace'])
    await seed.dispatch('session.create', {'title': 'Changed native revision'})
    session = seed._session()
    sid = session['id']
    session.update({key: value for key, value in native.items() if key != 'id'})
    session.update(runtimeSessionId='native-root', historyManaged=False, historyLoaded=True,
                   nativeRevision=revision(session), status='idle',
                   messages=[{**row, 'id': 'web-' + row['id']} for row in initial['messages']])
    anchor = session['messages'][-1]['id'] if activity_kind != 'unshadowed' else None
    tree = {'nodes': [{'id': f'old-{i}', 'kind': 'tool', 'turnId': 'old',
                       'phase': 'completed', 'detail': {'values': [i]}} for i in range(160)],
            'turns': [{'id': 'old', 'phase': 'completed', 'anchorMessageId': anchor}],
            'retiredUsageNodes': [{'id': f'prior-call-{i}', 'sessionId': 'native-root',
                                  'kind': 'llm', 'phase': 'completed', 'revision': 1,
                                  'usage': {'inputTokens': 1, 'outputTokens': 1}}
                                 for i in range(160)], 'currentTurnId': None}
    execution.refresh_usage(tree)
    session['execution'] = tree
    seed._publish_changes(sessions={sid})
    # Ordinary persistence selects v1 once the retained accounting collection
    # reaches its existing 128-row boundary; tool count alone is not that gate.
    from amplifier_web.state_records import load
    saved_owner = next(row for row in load(seed.db)['sessions'] if row['id'] == sid)
    assert saved_owner[MARKER]['execution']['executionProjection'] == 1
    await seed.close()

    # A real saved transcript and CI event change, larger than the accounting
    # prefix limit. No worker, provider or fabricated history-reader result.
    calls = [f'call-{i}' for i in range(130)]
    rows = [*prefix, {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': call, 'type': 'function', 'function': {'name': 'inspect', 'arguments': '{}'}}
                for call in calls]},
            *[{'role': 'tool', 'tool_call_id': call, 'content': 'synthetic result'} for call in calls],
            {'role': 'assistant', 'content': 'Updated saved answer'}]
    store.save('native-root', rows,
        {'working_dir': native['workspace'], 'bundle': 'anchors'})
    (capture / 'events.jsonl').write_text('' if activity_kind == 'empty' else ''.join(
        json.dumps({'event': 'tool:post', 'session_id': 'native-root',
                    'data': {'tool_call_id': call, 'tool_name': 'inspect'}}) + '\n' for call in calls))

    app = AppService(home, workspace=native['workspace'])
    try:
        session = app._session(sid, hydrate=False)
        assert not dict.__contains__(session, 'execution')
        assert dict.get(session, MARKER)['execution']['executionProjection'] == 1
        assert session['nativeRevision'] != revision(session)
        from amplifier_web.cold_display import load as load_execution
        original = load_execution(app.db, dict.get(session, MARKER)['execution'])
        assert not hasattr(session, '_working_execution')
        from amplifier_web import cold_display
        reads = []
        reference = dict.get(session, MARKER)['execution']['$resource']
        def counted(db, selected):
            if selected['$resource'] == reference:
                reads.append(selected['$resource'])
            return load_execution(db, selected)
        with monkeypatch.context() as observer:
            observer.setattr(cold_display, 'load', counted)
            await app.history.ensure_loaded(sid, hydrate=False)
        assert reads == [reference]
        assert not session.get('historyError')
        assert session['nativeRevision'] == revision(session)
        result = read_transcript(session, limit=None)
        assert len(result['activity']['nodes']) == (0 if activity_kind == 'empty' else 130)
        expected = {'execution': copy.deepcopy(original), 'messages': copy.deepcopy(session['messages'])}
        apply_activity(expected, result['activity'])
        assert snapshot(session) == expected['execution']
        assert session['historyActivity'] == expected['historyActivity']
        fast = activity_kind != 'unshadowed'
        assert dict.__contains__(session, 'execution') is not fast
        if fast:
            owner = session._working_execution
            assert owner._legacy_read is None
            assert not owner.current['nodes']
        else:
            assert not hasattr(session, '_working_execution')
            assert sum(bool(row.get('nativeHistory')) for row in session['execution']['nodes']) == 130

        event = {'id': 'current-call', 'kind': 'llm', 'sessionId': sid, 'rootSessionId': sid,
                 'turnId': 'old', 'phase': 'running', 'revision': 1, 'startedAt': 1,
                 'usage': {'inputTokens': 10, 'outputTokens': 2, 'totalTokens': 12}}
        await app.on_runtime_event('execution.event', event)
        await app._flush_pending_progress()
        if fast:
            assert not dict.__contains__(session, 'execution')
            assert len(owner.current['nodes']) == 1
            assert owner.usage_prefix is not None
        # The next ordinary token update must not repeat the changed-revision
        # comparison or traverse the saved ledger. The fallback stays honest.
        with monkeypatch.context() as guard:
            if fast:
                def no_history(*args, **kwargs):
                    pytest.fail('A later current event reread completed execution history')
                guard.setattr(records, 'materialize', no_history)
                guard.setattr(records, 'read_usage', no_history)
            await app.on_runtime_event('execution.event', {**event, 'revision': 2,
                'usage': {'inputTokens': 20, 'outputTokens': 2, 'totalTokens': 22}})
            await app._flush_pending_progress()
        actual = snapshot(session)
        assert actual['aggregateUsage']['totalTokens'] == 22
        assert [row for row in actual['nodes'] if row['id'].startswith('old-')] == original['nodes']
        borrowed = exact(session, 'nodes', {'id': 'old-0'})['detail']['values']
        app._publish_changes(sessions={sid})
        borrowed.append('late correction')
        app._publish_changes(sessions={sid})
        assert snapshot(session)['nodes'][0]['detail']['values'] == [0, 'late correction']
        # Native display is never a second durable source in either path.
        from amplifier_web.state_records import load
        from amplifier_web.cold_display import materialize
        durable = load(app.db)['sessions']
        retained = next(row for row in durable if row['id'] == sid)
        assert not any(row.get('nativeHistory') for row in materialize(retained, app.db)['execution']['nodes'])
    finally:
        await app.close()


@pytest.fixture
def legacy_saved(saved):
    db, _, tree = saved
    tree = copy.deepcopy(tree)
    tree['nodes'][0]['phase'] = 'running'
    tree['extension'] = {'nested': ['original']}
    reference = execution_reference(db, tree)
    db.commit()
    session = ColdRecord({'id': 'chat', 'messages': [], MARKER: {'execution': reference}}, db)
    return db, session, tree


def test_native_scope_detaches_snapshots_and_preserves_live_aliases(legacy_saved):
    from amplifier_web.execution_working_set import native_refresh
    db, session, original = legacy_saved
    with native_refresh(session):
        owner = working(session)
        selected = snapshot(session)
        selected['nodes'][0]['detail']['values'].append('snapshot only')
        selected['nodes'][1]['detail']['values'].append('snapshot only')
        selected['extension']['nested'].append('snapshot only')
        frozen = detached(session)
        assert frozen._working_execution._legacy_read is None
        borrowed = current(session)['nodes'][0]['detail']['values']
        borrowed.append('live edit')
        current(session)['extension']['nested'].append('live edit')
        historical = exact(session, 'nodes', {'id': '1'})['detail']['values']
        historical.append('historical correction')
        assert owner._legacy_read[1] == original
        reference = save(db, session)
    assert owner._legacy_read is None
    actual = records.materialize(db, reference)
    assert actual['nodes'][0]['detail']['values'] == [0, 'live edit']
    assert actual['nodes'][1]['detail']['values'] == [1, 'historical correction']
    assert actual['extension']['nested'] == ['original', 'live edit']
    assert frozen['execution'] == original
    borrowed.append('after commit')
    historical.append('after commit')
    actual = records.materialize(db, save(db, session))
    assert actual['nodes'][0]['detail']['values'][-1] == 'after commit'
    assert actual['nodes'][1]['detail']['values'][-1] == 'after commit'


def test_native_read_only_selection_keeps_saved_rows_private(legacy_saved, monkeypatch):
    from amplifier_web.execution_working_set import native_refresh
    db, session, original = legacy_saved
    activity = {'nodes': [], 'turns': [], 'diagnostics': ['read-only selection']}
    with native_refresh(session):
        owner = working(session)
        borrowed = current(session)['nodes'][0]['detail']['values']
        borrowed.append('current edit')
        expected = {'execution': snapshot(session), 'messages': []}
        apply_activity(expected, activity)
        # This private read-only caller must not construct a public full copy.
        # The public snapshot contract is still exercised below and separately.
        with monkeypatch.context() as guard:
            def no_full_snapshot():
                pytest.fail('Native selection requested a detached full snapshot')
            guard.setattr(owner, 'snapshot', no_full_snapshot)
            apply_activity(session, activity)
        assert owner._legacy_read[1] == original
        assert snapshot(session) == expected['execution']
        assert session['historyActivity'] == expected['historyActivity']
        selected = snapshot(session)
        selected['nodes'][1]['detail']['values'].append('snapshot only')
        selected['extension']['nested'].append('snapshot only')
        selected['aggregateUsage']['metricKnownCalls']['inputTokens'] = 99
        assert owner._legacy_read[1] == original
        assert snapshot(session) == expected['execution']
        reference = save(db, session)
    assert owner._legacy_read is None
    assert records.materialize(db, reference)['nodes'][1] == original['nodes'][1]
    borrowed.append('late current edit')
    actual = records.materialize(db, save(db, session))
    assert actual['nodes'][0]['detail']['values'] == [0, 'current edit', 'late current edit']
    assert actual['extension'] == original['extension']


@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
def test_native_scope_failure_keeps_selected_root_and_releases_read(legacy_saved, failure):
    from amplifier_web.execution_working_set import native_refresh
    db, session, original = legacy_saved
    reference = copy.deepcopy(dict.get(session, MARKER)['execution'])
    with pytest.raises(failure):
        try:
            with native_refresh(session):
                owner = working(session)
                current(session)['extension']['nested'].append('pending')
                owner.stage()
                raise failure('publication not accepted')
        finally:
            db.rollback()
    assert owner._legacy_read is None
    assert owner.reference == dict.get(session, MARKER)['execution'] == reference
    from amplifier_web.cold_display import load
    assert load(db, reference) == original
    with native_refresh(session):
        accepted = save(db, session)
    assert owner._legacy_read is None
    actual = records.materialize(db, accepted)
    assert actual['extension']['nested'] == ['original', 'pending']


@pytest.mark.parametrize('append', [False, True])
def test_shadowed_merge_matches_plain_result_and_late_aliases(saved, append):
    db, session, _ = saved
    session['messages'] = [{'id': 'web-message', 'nativeIndex': 0, 'role': 'user', 'text': 'fixture'}]
    current(session)['turns'][0]['anchorMessageId'] = 'web-message'
    current(session)['extension'] = {'nested': ['preserve all top-level fields']}
    activity = {'nodes': [{'id': 'native-tool', 'kind': 'tool', 'turnId': 'native-turn', 'phase': 'completed'}],
                'turns': [{'id': 'native-turn', 'nativeHistory': True, 'nativeIndex': 0,
                           'anchorMessageId': 'old-native-id', 'phase': 'completed'}], 'diagnostics': ['fixture']}
    expected = {'execution': snapshot(session), 'messages': copy.deepcopy(session['messages'])}
    apply_activity(expected, activity, append=append)
    owner = working(session)
    apply_activity(session, activity, append=append)
    assert session._working_execution is owner
    assert snapshot(session) == expected['execution']
    assert session['historyActivity'] == expected['historyActivity']
    borrowed = exact(session, 'nodes', {'id': '7'})['detail']['values']
    save(db, session)
    borrowed.append('late')
    save(db, session)
    assert snapshot(session)['nodes'][7]['detail']['values'] == [7, 'late']


def test_native_topology_repair_transfers_original_current_aliases(saved):
    _, session, _ = saved
    current(session)['nodes'].append({'id': 'borrowed', 'kind': 'tool', 'phase': 'running',
                                      'detail': {'values': []}})
    borrowed = current(session)['nodes'][0]['detail']['values']
    activity = {'nodes': [{'id': 'native-tool', 'kind': 'tool', 'turnId': 'native-turn', 'phase': 'completed'}],
                'turns': [{'id': 'native-turn', 'nativeHistory': True, 'anchorMessageId': 'new',
                           'phase': 'completed'}], 'diagnostics': []}
    expected = {'execution': snapshot(session), 'messages': []}
    apply_activity(expected, activity)
    apply_activity(session, activity)
    assert dict.__contains__(session, 'execution')
    assert not hasattr(session, '_working_execution')
    assert session['execution'] == expected['execution']
    borrowed.append('still the same owner')
    assert next(row for row in session['execution']['nodes'] if row['id'] == 'borrowed')['detail']['values'] == ['still the same owner']
