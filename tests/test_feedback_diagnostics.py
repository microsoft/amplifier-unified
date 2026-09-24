"""Synthetic reporting fixtures, not reproductions of any reporter's failure."""
import copy
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web import feedback, feedback_diagnostics as diagnostics
from amplifier_web.service import AppError, AppService


def example():
    return {'revision': 17, 'selectedSessionId': 'PRIVATE SESSION',
            'updates': {'application': {'runningRevision': 'a' * 40}, 'release': 'b' * 32},
            'sessions': [{'id': 'PRIVATE SESSION', 'status': 'error', 'historyLoaded': True,
                'workspaceAvailable': True, 'error': 'PRIVATE raw traceback /workspace',
                'messages': [{'role': 'user', 'inputId': 'PRIVATE LATEST', 'text': 'PRIVATE message',
                              'delivery': {'status': 'failed'}}],
                'activity': {'phase': 'starting', 'label': 'PRIVATE label'},
                'failure': {'category': 'worker_startup', 'errorType': 'RuntimeStartupError',
                            'recordedAt': 90, 'inputId': 'PRIVATE LATEST', 'summary': 'PRIVATE summary'},
                'moduleFailures': [{'module': 'PRIVATE module', 'type': 'provider',
                                    'reason_code': 'missing_source', 'source': 'PRIVATE URL'}]}]}


def test_startup_summary_is_bounded_and_discloses_no_private_identifiers_or_content():
    state = example()
    result = diagnostics.snapshot(state, {'eventStream': 'open'}, now=100)
    summary = result['troubleshooting']
    assert result['schemaVersion'] == 2
    assert summary['runningAppRevision'] == 'a' * 40
    assert summary['hostActiveComponentGeneration'] == 'b' * 32
    assert summary['workerLoadedComponentGeneration'] == 'unverified'
    assert summary['activityPhase'] == 'starting'
    assert summary['latestInputDelivery'] == 'failed'
    assert summary['recordedFailure'] == {'present': True, 'category': 'worker_startup',
        'errorType': 'RuntimeStartupError', 'code': 'unknown', 'ageSeconds': 10, 'inputRelation': 'latest'}
    assert summary['moduleFailures']['byReason'] == {'missing_source': 1}
    assert 'PRIVATE' not in json.dumps(result)
    assert len(json.dumps(summary).encode()) <= 2048
    markdown = diagnostics.markdown(result)
    assert 'Recorded failure: worker_startup' in markdown
    assert '<details>' in markdown and '```json' in markdown


@pytest.mark.parametrize(('input_id', 'relation'), [('PRIVATE EARLIER', 'earlier'), ('PRIVATE UNLOADED', 'unknown'), (None, 'unknown')])
def test_old_or_unassociated_failure_does_not_claim_to_explain_latest_input(input_id, relation):
    state = example()
    session = state['sessions'][0]
    session['messages'].insert(0, {'role': 'user', 'inputId': 'PRIVATE EARLIER', 'text': 'PRIVATE earlier'})
    session['messages'].append({'role': 'assistant', 'text': 'PRIVATE answer'})
    session['failure']['inputId'] = input_id
    session['failure']['recordedAt'] = '1970-01-01T00:01:30Z'
    summary = diagnostics.snapshot(state, now=100)['troubleshooting']
    assert summary['recordedFailure']['inputRelation'] == relation
    assert summary['recordedFailure']['ageSeconds'] == 10


def test_custom_values_remain_unknown_and_malformed_fields_cannot_leak():
    state = example()
    state['updates'] = {'application': {'runningRevision': 'PRIVATE revision'}, 'release': {'PRIVATE': 1}}
    state['revision'] = {'PRIVATE': 1}
    session = state['sessions'][0]
    session['failure'] = {'errorType': 'PrivateCustomerError', 'category': ['PRIVATE'],
        'code': 'PRIVATE arbitrary', 'recordedAt': float('inf'), 'inputId': {'PRIVATE': 1}}
    session['historyLoaded'] = {'PRIVATE': 1}
    session['workspaceAvailable'] = 'PRIVATE'
    session['sharedHistoryTotal'] = 'PRIVATE'
    session['activity'] = {'phase': ['PRIVATE']}
    session['messages'][0]['delivery']['status'] = ['PRIVATE']
    session['moduleFailures'] = [{'type': ['PRIVATE'], 'reason_code': {'PRIVATE': 1}}] * 1000
    result = diagnostics.snapshot(state, now=100)
    summary = result['troubleshooting']
    assert summary['runningAppRevision'] is None and summary['hostActiveComponentGeneration'] is None
    assert summary['recordedFailure']['errorType'] == 'other'
    assert summary['recordedFailure']['category'] == 'unknown'
    assert summary['recordedFailure']['ageSeconds'] is None
    assert summary['recordedFailure']['inputRelation'] == 'unknown'
    assert summary['latestInputDelivery'] == summary['activityPhase'] == 'unknown'
    assert summary['moduleFailures'] == {'total': 1000, 'sampled': 100,
        'byType': {'unknown': 100}, 'byReason': {'unknown': 100}}
    assert 'PRIVATE' not in json.dumps(result) and 'PrivateCustomerError' not in json.dumps(result)
    assert result['conversation']['historyLoaded'] is None
    assert len(json.dumps(summary).encode()) <= 2048


def test_maximum_summary_stays_under_two_kilobytes():
    state = example()
    state['sessions'][0]['moduleFailures'] = [
        {'type': kind, 'reason_code': reason}
        for kind in diagnostics.MODULE_TYPES | {'unknown'}
        for reason in diagnostics.MODULE_REASONS]
    assert len(json.dumps(diagnostics.snapshot(state, now=100)['troubleshooting']).encode()) <= 2048


def test_provider_preflight_summary_counts_reason_without_exporting_account_identity():
    state = example()
    state['sessions'][0]['moduleFailures'] = [{'type': 'provider', 'reason_code': reason,
        'instance_id': 'PRIVATE-account', 'error': 'PRIVATE-secret'} for reason in
        ('provider_schema_failed', 'provider_configuration_failed')]
    summary = diagnostics.snapshot(state, now=100)['troubleshooting']
    assert summary['moduleFailures']['byReason'] == {'provider_schema_failed': 1, 'provider_configuration_failed': 1}
    assert 'PRIVATE' not in json.dumps(summary)


def test_missing_evidence_is_not_success_or_fresh_worker_attestation():
    result = diagnostics.snapshot({}, now=100)
    summary = result['troubleshooting']
    assert not summary['recordedFailure']['present']
    assert summary['latestInputDelivery'] == 'unknown'
    assert summary['runningAppRevision'] is None
    assert summary['hostActiveComponentGeneration'] is None
    assert summary['workerLoadedComponentGeneration'] == 'unverified'
    assert result['conversation']['historyLoaded'] is None


async def test_on_demand_preview_and_saved_receipt_share_action_without_broadcast_or_replay(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    create = AsyncMock(return_value=feedback.ISSUES_URL + '/42')
    monkeypatch.setattr(feedback, 'create_issue', create)
    monkeypatch.setattr(feedback.shutil, 'which', lambda _: '/fixture/gh')
    try:
        await app.dispatch('session.create', {'title': 'PRIVATE title'})
        session = app._session()
        session.update(example()['sessions'][0], id=session['id'])
        app.state['updates'] = example()['updates']
        args = {'deviceDiagnostics': {'eventStream': 'reconnecting', 'online': True}}
        with monkeypatch.context() as context:
            context.setattr(app, '_publish', lambda *a, **kw: pytest.fail('Read must not publish'))
            context.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('No history read')))
            before = app.state['revision']
            preview = await app.dispatch('feedback.diagnostics', args)
            agent = await app.app_bridge('dispatch', {'action': 'feedback.diagnostics', 'args': args}, session['id'])
            assert preview['result']['diagnostics']['troubleshooting'] == agent['result']['diagnostics']['troubleshooting']
            assert app.state['revision'] == before
            assert 'state' not in preview and preview['effects'] == []
            assert not app.state['feedback']['requests']
            create.assert_not_awaited()
        payload = {'requestId': 'saved-fixture', 'title': 'Reviewed title', 'body': 'Reviewed reproduction',
                   'category': 'bug', **args}
        app.feedback.accept(payload)
        saved = await app.dispatch('feedback.diagnostics', {'requestId': 'saved-fixture'})
        frozen = copy.deepcopy(saved['result']['diagnostics'])
        assert frozen['device']['eventStream'] == 'reconnecting'
        assert frozen['troubleshooting']['recordedFailure']['category'] == 'worker_startup'
        session['failure'] = {'category': 'authentication'}
        session['messages'].clear()
        app._save()
        await app.feedback.send('saved-fixture')
        await app.dispatch('feedback.submit', payload)
        if app.tasks:
            import asyncio
            await asyncio.gather(*list(app.tasks))
        create.assert_awaited_once()
        body = create.call_args.args[1]
        assert json.loads(body.split('```json\n')[1].split('\n```')[0]) == frozen
        assert 'PRIVATE' not in body and str(tmp_path) not in body
        reread = await app.dispatch('feedback.diagnostics', {'requestId': 'saved-fixture'})
        assert reread['result']['snapshot'] == 'accepted'
        assert reread['result']['diagnostics'] == frozen
        assert set(app.state['feedback']['diagnostics']) == set(diagnostics.build_facts())
        with pytest.raises(AppError, match='No saved feedback'):
            await app.dispatch('feedback.diagnostics', {'requestId': 'not-present'})
        with pytest.raises(AppError, match='Additional properties'):
            await app.dispatch('feedback.diagnostics', {'deviceDiagnostics': {'url': 'PRIVATE'}})
        app.feedback.accept({**payload, 'requestId': 'opted-out', 'includeDiagnostics': False})
        assert (await app.dispatch('feedback.diagnostics', {'requestId': 'opted-out'}))['result']['diagnostics'] is None
    finally:
        await app.close()
    reopened = AppService(tmp_path, workspace=tmp_path)
    try:
        assert (await reopened.dispatch('feedback.diagnostics', {'requestId': 'saved-fixture'}))['result']['diagnostics'] == frozen
    finally:
        await reopened.close()
