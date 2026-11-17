"""Worker-bound operation evidence must not expose unrelated retained history."""
import copy

import pytest

from amplifier_web import execution, execution_records as records
from amplifier_web.cold_display import ColdRecord, MARKER, materialize
from amplifier_web.execution_working_set import exact, snapshot, working
from amplifier_web.service import AppError, AppService
from amplifier_web.session_projection import stored_execution
from test_operations import event, final, output
from test_service import Runtime


async def test_operation_bridge_keeps_cold_owner_and_persists_late_alias(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    home = tmp_path / 'app'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    runtime = Runtime()
    seed = AppService(home, runtime, workspace=tmp_path)
    try:
        await seed.dispatch('session.create', {})
        session = seed._session()
        sid = session['id']
        history = [{'id': 'retained-message', 'role': 'user', 'text': 'saved history ' * 1500,
                    'inputId': 'uncertain-input', 'delivery': {'status': 'unknown'}}]
        session['messages'] = copy.deepcopy(history)
        tree = {'nodes': [{'id': f'old-{i}', 'kind': 'tool', 'turnId': 'old',
                           'phase': 'completed', 'detail': {'values': [i]}} for i in range(160)],
                'turns': [{'id': 'old', 'phase': 'completed', 'anchorMessageId': None}],
                'retiredUsageNodes': [{'id': f'receipt-{i}', 'sessionId': sid, 'kind': 'llm',
                                      'phase': 'completed', 'revision': 1,
                                      'usage': {'inputTokens': 1, 'outputTokens': 1}}
                                     for i in range(160)], 'currentTurnId': None}
        execution.refresh_usage(tree)
        session['execution'] = tree
        seed._publish_changes(sessions={sid})
    finally:
        await seed.close()

    app = AppService(home, runtime, workspace=tmp_path)
    try:
        # Startup legitimately reads messages for uncertain-delivery recovery.
        # Retire an unviewed idle owner through the normal committed boundary,
        # using the existing cold-display test's recent-owner/time override.
        await app.dispatch('session.create', {'title': 'Other foreground chat'})
        session = app._session(sid, hydrate=False)
        assert isinstance(session, ColdRecord)
        with monkeypatch.context() as retirement:
            retirement.setattr(app.cold_display, 'RECENT_LIMIT', 0)
            assert sid in app.cold_display.retire(force=True)
        assert not any(dict.__contains__(session, key) for key in ('messages', 'execution'))
        references = copy.deepcopy(dict.get(session, MARKER))
        original = snapshot(session)
        owner = working(session)
        borrowed_row = exact(session, 'nodes', {'id': 'old-7'})
        borrowed = borrowed_row['detail']['values']
        native_id = 'fixture-native-owner'

        async def observe(value, identity=sid):
            # This is the host's already-authenticated worker bridge boundary;
            # no worker process, network authentication or tool runs here.
            return await app.app_bridge('operations.observe', {
                '_runtimeSessionId': native_id, 'runtimeSessionId': native_id,
                'event': value}, identity)

        getitem = ColdRecord.__getitem__
        def no_full_history(row, key):
            if row is session and key in {'messages', 'execution'}:
                pytest.fail('Operation owner validation exposed retained history')
            return getitem(row, key)
        with monkeypatch.context() as guard:
            guard.setattr(ColdRecord, '__getitem__', no_full_history)
            first = await observe(event())
            chunk = await observe(output())
            finished = await observe(final())
            assert not any(row['duplicate'] for row in (first, chunk, finished))
            assert len({row['revision'] for row in (first, chunk, finished)}) == 3
            assert await observe(final()) == {**finished, 'duplicate': True}
            with pytest.raises(AppError, match='Select or create'):
                await observe(event('unknown'), identity='missing-owner')
            session['_deleting'] = True
            try:
                with pytest.raises(AppError, match='being deleted'):
                    await observe(event('deleted'))
            finally:
                session.pop('_deleting')
            with pytest.raises(ValueError, match='No host adapter'):
                await observe(event('wrong-producer', source='unregistered'))

        assert not any(dict.__contains__(session, key) for key in ('messages', 'execution'))
        assert dict.get(session, MARKER) == references
        assert session._working_execution is owner
        assert exact(session, 'nodes', {'id': 'old-7'}) is borrowed_row
        assert snapshot(session) == original
        journal = app.operations.journal.read(sid, 'p1')
        assert journal['state'] == 'completed' and journal['runtimeSessionId'] == native_id
        assert [row['text'] for row in journal['chunks']] == ['hello\n']
        assert [row['id'] for row in app.operations.journal.list(sid)] == ['p1']
        assert app.operations.journal.list('missing-owner') == []

        await app.on_runtime_event('execution.event', {
            'sessionId': sid, 'rootSessionId': sid, 'id': 'current-call', 'kind': 'llm',
            'turnId': 'old', 'phase': 'completed', 'revision': 1, 'startedAt': 3, 'endedAt': 4,
            'usage': {'inputTokens': 10, 'outputTokens': 2, 'totalTokens': 12}})
        await app._flush_pending_progress()
        current = snapshot(session)
        assert [row for row in current['nodes'] if row['id'].startswith('old-')] == original['nodes']
        assert [row for row in current['retiredUsageNodes'] if row['id'].startswith('receipt-')] == original['retiredUsageNodes']
        assert any(row['id'] == 'current-call' for row in current['nodes'])
        borrowed.append('late correction')
        assert next(row for row in snapshot(session)['nodes'] if row['id'] == 'old-7')['detail']['values'] == [7, 'late correction']
        expected = stored_execution(snapshot(session))
        app._publish_changes(sessions={sid})
        assert session._working_execution is owner and not dict.__contains__(session, 'execution')
        assert borrowed_row['detail']['values'] is borrowed
        assert records.materialize(app.db, session[MARKER]['execution']) == expected
        assert materialize(session, app.db)['messages'] == history
        assert runtime.started == runtime.sent == runtime.stopped == []
    finally:
        await app.close()

    restored = AppService(home, runtime, workspace=tmp_path)
    try:
        saved = restored._session(sid, hydrate=False)
        assert snapshot(saved) == expected
        assert materialize(saved, restored.db)['messages'] == history
        assert restored.operations.journal.read(sid, 'p1') == journal
    finally:
        await restored.close()
