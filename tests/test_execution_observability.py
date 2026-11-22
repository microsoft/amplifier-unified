"""Display preferences retain their explicit scope without changing accounting."""
from copy import deepcopy

import pytest
from jsonschema import ValidationError, validate

from amplifier_web.shell_modules import COMPOSITION
from amplifier_web.service import AppService
from amplifier_web.execution import ensure_turn, ingest, finish
from test_appearance_preferences import report, initialize, update, ack


@pytest.mark.parametrize('scope', ['client', 'shared'])
async def test_metric_preferences_survive_restart_with_explicit_scope_without_usage_changes(tmp_path, scope):
    app = AppService(tmp_path/'data', workspace=tmp_path)
    queues = []
    patch = {'executionVisibility': {'step': False, 'turn': True, 'conversation': False},
             'executionMetrics': ['costUsd', 'cacheReadTokens', 'voiceSeconds'], 'conversationStatus': True}

    async def attach(identity):
        app.clients.attach(identity)
        with app.clients.bind(identity):
            queues.append(app.subscribe())
        await report(app, client=identity)

    try:
        await app.dispatch('session.create', {})
        # Synthetic completed LLM records exercise actual canonical accounting,
        # not an unrelated session metadata field or a real provider call.
        sid = app._session()['id']
        session = app._session(sid)
        ensure_turn(session, 'metrics-turn', 'Synthetic metrics input')
        usage = {'inputTokens': 23, 'outputTokens': 7, 'totalTokens': 30,
                 'cacheReadTokens': 3, 'costUsd': 0.002, 'costType': 'reported'}
        ingest(session, {'id': 'metrics-call', 'kind': 'llm', 'turnId': 'metrics-turn',
                         'sessionId': sid, 'phase': 'completed', 'startedAt': 1.0, 'endedAt': 2.0,
                         'usage': usage})
        finish(session)
        accounting = deepcopy(session['execution']['aggregateUsage'])
        assert accounting['calls'] == 1 and accounting['costUsd'] == 0.002
        assert accounting['inputTokens'] == 23 and accounting['outputTokens'] == 7
        app._save()
        await attach('browser-one')
        await attach('independent-local')
        await initialize(app, client='independent-local')
        independent = app.appearance.read('independent-local')['effective']
        shared = deepcopy(app.appearance.shared())
        if scope == 'client':
            await initialize(app, client='browser-one')
        before = deepcopy(app.state['sessions'])
        original = app.appearance.read('browser-one')
        receipt = await update(app, patch, scope=scope, client='browser-one')
        assert app.state['sessions'] == before
        if scope == 'client':
            assert receipt['status'] == 'awaiting-storage'
            assert receipt['stored'] is False and receipt['activated'] is False
            assert app.appearance.read('browser-one')['effective'] == original['effective']
            stored = await ack(app, client='browser-one')
            assert stored['stored'] and stored['activated'] and not stored['rendered']
            assert app.appearance.shared() == shared
        else:
            assert receipt['status'] == 'saved' and receipt['stored'] and not receipt['activated']
            assert app.appearance.read('browser-one').get('command') is None
        assert app.appearance.read('independent-local')['effective'] == independent
        assert app.state['sessions'] == before
        confirmed = app.appearance.read('browser-one')
        for key, value in patch.items():
            assert confirmed['effective'][key] == value
            assert app.shell.inspect('browser-one')['effectiveComposition']['presentation'][key] == value
        for queue in queues:
            app.unsubscribe(queue)
        queues.clear()
        await app.close()

        app = AppService(tmp_path/'data', workspace=tmp_path)
        restored_execution = app._session(sid)['execution']
        assert restored_execution['aggregateUsage'] == accounting
        assert next(node for node in restored_execution['nodes'] if node['id'] == 'metrics-call')['usage'] == usage
        assert next(turn for turn in restored_execution['turns'] if turn['id'] == 'metrics-turn')['aggregateUsage'] == accounting
        restarted = deepcopy(app.state['sessions'])
        await attach('browser-one')
        fresh = app.appearance.read('browser-one')
        assert fresh['namespace'] == confirmed['namespace']
        assert fresh['generation'] != confirmed['generation']
        assert fresh['local']['values'] is None and fresh['activeScope'] == 'shared'
        if scope == 'client':
            assert fresh['effective'] == shared['values'], 'The host cannot restore browser-owned values by itself'
            # Model only the browser's previously confirmed whole storage record.
            await report(app, client='browser-one', activeScope='client',
                         revision=confirmed['local']['revision'], storageVersion=confirmed['local']['storageVersion'],
                         values=deepcopy(confirmed['local']['values']))
        assert app.appearance.read('browser-one')['effective'] == confirmed['effective']
        await attach('new-browser')
        expected = shared['values'] if scope == 'client' else confirmed['effective']
        assert app.appearance.read('new-browser')['effective'] == expected
        assert app.state['sessions'] == restarted
    finally:
        for queue in queues:
            app.unsubscribe(queue)
        await app.close()


def test_all_ten_execution_metrics_can_be_selected():
    metrics = COMPOSITION['properties']['presentation']['properties']['executionMetrics']['items']['enum']
    assert len(metrics) == 10 and 'voiceSeconds' in metrics
    validate({'executionMetrics': metrics}, COMPOSITION['properties']['presentation'])


@pytest.mark.parametrize('patch', [
    {'executionVisibility': {'secret': True}}, {'executionVisibility': {'step': 'yes'}},
    {'executionMetrics': ['reasoningText']}, {'executionMetrics': ['costUsd', 'costUsd']},
    {'conversationStatus': 'yes'},
])
def test_schema_rejects_unknown_metrics_or_non_boolean_visibility(patch):
    with pytest.raises(ValidationError):
        validate(patch, COMPOSITION['properties']['presentation'])
