"""Cross-package contract, also run explicitly with the owned standalone runtime."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from amplifier_web.runtime import normalize_event
from amplifier_web.runtime_worker import Worker
from amplifier_web.session_health import generation_failure


@pytest.mark.parametrize('code', [
    'native_input_oversized', 'native_checkpoint_invalid', 'native_no_reduction',
    'native_measurement_unavailable', 'native_compaction_failed', 'disabled',
    'request_context_unavailable', 'invalid_native_contract',
    'authoritative_measurement_unavailable', 'summary_output_limit', 'summary_empty',
    'previous_failure', 'summary_failed', 'native_failed',
    'native_recovery_required_context_oversized', 'checkpoint_persist_failed',
    'summary_work_paused', 'summary_progress_incompatible',
])
def test_real_compaction_error_reaches_app_without_private_payload(code):
    errors = pytest.importorskip('amplifier_module_context_managed.errors')
    failures = pytest.importorskip('amplifier_module_loop_live.failures')
    error = errors.CompactionError(code, 'PRIVATE transcript and provider payload')
    event = {'type': 'generation.failed', 'generation_id': 'generation',
             'input_ids': ['input'], **failures.turn_failure(error)}
    _, payload = normalize_event(event, 'chat')
    visible = generation_failure(payload)
    assert visible['code'] == code
    assert visible['category'] == 'context_compaction'
    assert visible['stage'] == 'context_preparation'
    assert visible['replayed'] is False
    if code in {'summary_work_paused', 'summary_progress_incompatible'}:
        assert isinstance(error, RuntimeError)
        assert event['retryable'] is payload['retryable'] is visible['retryable'] is False
    assert 'PRIVATE' not in json.dumps([event, payload, visible])


@pytest.mark.parametrize('code,summary,guidance', [
    ('summary_work_paused',
     'Conversation preparation reached its local call allowance.',
     'Completed summary progress is saved. Further preparation requires separate admission; '
     'it was not automatically retried. Original history and the checkpoint are preserved.'),
    ('summary_progress_incompatible',
     'Saved summary progress cannot be used with the current source or policy.',
     'No replacement summary work was started. Original history and the checkpoint are preserved. '
     'Review the source and policy mismatch before deciding how to proceed.'),
])
@pytest.mark.parametrize('private', ['PRIVATE source, policy and history', {'PRIVATE': ['provider payload']}])
def test_work_pause_failure_projection_preserves_safe_cause_without_retry(code, summary, guidance, private):
    event = {'type': 'generation.failed', 'generation_id': 'generation', 'input_ids': ['input'],
             'error_type': 'CompactionError', 'error_category': 'context_compaction',
             'error_code': code, 'error_stage': 'context_preparation', 'retryable': False,
             'error_message': private, 'source': private, 'policy': private, 'history': private,
             'checkpoint': private, 'effects': 'PRIVATE', 'replayed': True}
    before = deepcopy(event)
    kind, payload = normalize_event(event, 'chat')
    assert event == before
    assert kind == 'runtime.generation' and payload['event'] == 'generation.failed'
    assert payload['generation_id'] == 'generation' and payload['input_ids'] == ['input']
    assert payload['sessionId'] == payload['rootSessionId'] == 'chat'
    expected = {'code': code, 'category': 'context_compaction', 'errorType': 'CompactionError',
                'stage': 'context_preparation', 'summary': summary, 'guidance': guidance,
                'effects': 'not_rolled_back', 'replayed': False, 'retryable': False}
    assert generation_failure(payload) == generation_failure(event) == expected
    assert 'PRIVATE' not in json.dumps([payload, expected])
    assert not any(text in guidance for text in ('Try Continue', 'Retry', 'Increase', 'recovery copy'))


@pytest.fixture(params=['root', 'child'])
def compaction_activity(request, monkeypatch):
    """Exercise the installed hook callback and normal public-event projection."""
    identity = request.param
    worker = Worker()
    worker.runtime = SimpleNamespace(session_id='root')
    worker.telemetry = Mock()
    callbacks, capabilities, observed = {}, {
        'web.worker_run': 'child-run',
        'live.children': SimpleNamespace(rows={
            'child': {'agent': 'fixture:worker', 'callId': 'tool-1'}}),
    }, []
    coordinator = SimpleNamespace(
        session_id=identity, get=lambda _: {}, get_capability=capabilities.get,
        register_capability=capabilities.__setitem__,
        hooks=SimpleNamespace(register=lambda event, fn, **_: callbacks.update({event: fn})))
    worker.install_activity(coordinator)
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', observed.append)

    async def emit(event, data):
        supplied = {'session_id': identity, **data}
        before = deepcopy(supplied)
        observed.clear()
        result = await callbacks[event](event, supplied)
        assert result.action == 'continue'
        assert supplied == before
        if supplied['session_id'] == identity:
            worker.telemetry.hook.assert_called_with(identity, event, supplied)
        if not observed:
            return None
        raw, = observed
        kind, payload = normalize_event(raw, 'root')
        assert payload['activityOnly'] is True
        assert payload['detail'] == raw['detail']
        assert payload['phase'] == raw['phase']
        if identity == 'child':
            assert raw['type'] == 'worker.activity' and raw['workerId'] == 'child'
            assert kind == 'worker.updated' and payload['id'] == 'child'
            assert payload['name'] == 'fixture:worker'
            assert payload['callId'] == 'tool-1' and payload['runId'] == 'child-run'
            assert 'status' not in payload
        else:
            assert raw['type'] == 'runtime.activity' and kind == 'runtime.status'
            assert payload['status'] == 'working'
        assert 'PRIVATE' not in json.dumps([raw, payload])
        assert not {'completed_parts', 'remaining_parts', 'failure', 'outcome'} & raw.keys()
        return payload

    return emit


@pytest.mark.parametrize('recovery', [False, True])
async def test_compaction_started_has_no_duration_or_keep_sending_promise(compaction_activity, recovery):
    result = await compaction_activity('context:compaction_started', {
        'recovery': recovery, 'history': 'PRIVATE history'})
    assert result['phase'] == 'compacting'
    assert result['detail'] == (
        'Preparing recent context from saved history. Older history remains available.'
        if recovery else 'Making room in the conversation.')


async def test_compaction_progress_does_not_invent_a_fixed_total(compaction_activity):
    # The old denominator went 1/4 -> 2/4 -> 3/5 as pending segments split.
    # A high completed count with two unequal segments is not nearly finished.
    for completed, remaining in [(1, 3), (2, 2), (3, 2), (16, 2)]:
        result = await compaction_activity('context:compaction_progress', {
            'completed_parts': completed, 'remaining_parts': remaining,
            'method': 'semantic', 'summary': 'PRIVATE summary'})
        assert result['phase'] == 'compacting'
        assert result['detail'] == (
            f'Recovering oversized history. {completed} '
            f'{"part" if completed == 1 else "parts"} summarized and saved. '
            f'{remaining} segments waiting; these may be split further.')


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'cancelled', 'superseded'])
async def test_zero_pending_stays_preparing_until_finished(compaction_activity, outcome):
    progress = await compaction_activity('context:compaction_progress', {
        'completed_parts': 16, 'remaining_parts': 0})
    assert progress['phase'] == 'compacting'
    assert progress['detail'] == (
        'Recovering oversized history. 16 parts summarized and saved. '
        'Still preparing conversation context.')
    finished = await compaction_activity('context:compaction_finished', {
        'outcome': outcome, 'failure': {'message': 'PRIVATE failure'}})
    assert finished['phase'] == 'model'  # Presentation only; preserve event classification.
    assert finished['detail'] == (
        'Conversation context prepared; continuing work.' if outcome == 'completed'
        else f'Context preparation {outcome}.')


@pytest.mark.parametrize('counts,detail', [
    ({}, 'Recovering oversized history.'),
    ({'completed_parts': 0}, 'Recovering oversized history. 0 parts summarized and saved.'),
    ({'remaining_parts': 1}, 'Recovering oversized history. 1 segment waiting; it may be split further.'),
    ({'remaining_parts': 0}, 'Recovering oversized history. Still preparing conversation context.'),
])
async def test_compaction_old_optional_count_events_remain_valid(compaction_activity, counts, detail):
    result = await compaction_activity('context:compaction_progress', counts)
    assert result['phase'] == 'compacting'
    assert result['detail'] == detail


@pytest.mark.parametrize('invalid', [
    None, True, False, -1, 1.5, float('inf'), 'PRIVATE', {'PRIVATE': 1}, ['PRIVATE'], 10**5000,
], ids=['null', 'true', 'false', 'negative', 'fraction', 'infinity', 'text', 'object', 'list', 'huge'])
async def test_compaction_malformed_counts_are_not_progress_or_payload(compaction_activity, invalid):
    result = await compaction_activity('context:compaction_progress', {
        'completed_parts': invalid, 'remaining_parts': invalid,
        'future_progress': {'text': 'PRIVATE'}})
    assert result['phase'] == 'compacting'
    assert result['detail'] == 'Recovering oversized history.'


@pytest.mark.parametrize('outcome', [None, True, 42, 'PRIVATE', {'PRIVATE': 1}, ['PRIVATE']])
async def test_compaction_unknown_outcome_is_not_success_or_payload(compaction_activity, outcome):
    result = await compaction_activity('context:compaction_finished', {'outcome': outcome})
    assert result['phase'] == 'model'
    assert result['detail'] == 'Context preparation ended.'


@pytest.mark.parametrize('event', [
    'context:compaction_started', 'context:compaction_progress', 'context:compaction_finished',
])
async def test_compaction_activity_preserves_identity_and_auxiliary_scope(compaction_activity, event):
    from amplifier_web.execution_events import CALL_PURPOSE

    assert await compaction_activity(event, {'session_id': 'foreign', 'outcome': 'completed'}) is None
    token = CALL_PURPOSE.set({'label': 'Session naming'})
    try:
        assert await compaction_activity(event, {'outcome': 'completed'}) is None
    finally:
        CALL_PURPOSE.reset(token)


@pytest.mark.parametrize('identity', ['root', 'child'])
@pytest.mark.parametrize('auxiliary', [None, 'context_compaction', 'session_naming', 'prompt_completion'])
async def test_admitted_ordinary_model_supersedes_optional_compaction_failure(
        tmp_path, monkeypatch, identity, auxiliary):
    from unittest.mock import AsyncMock
    from amplifier_web.execution_events import ExecutionEvents, CALL_PURPOSE
    from amplifier_web.service import AppService

    observed, callbacks = [], {}
    scope = {'runId': 'child-run', 'generationId': 'generation'}
    capabilities = {'web.worker_run': 'child-run', 'web.child_execution': lambda: scope}
    coordinator = SimpleNamespace(
        session_id=identity, get=lambda _: {}, get_capability=capabilities.get,
        register_capability=capabilities.__setitem__,
        hooks=SimpleNamespace(register=lambda event, fn, **_: callbacks.update({event: fn})))
    registry = SimpleNamespace(
        rows={'child': {'agent': 'fixture:worker', 'callId': 'tool-1'}},
        sessions={'child': SimpleNamespace(coordinator=coordinator)})
    capabilities['live.children'] = registry
    worker = Worker()
    worker.session = SimpleNamespace(session_id='root', coordinator=coordinator)
    worker.runtime = SimpleNamespace(session_id='root', generation={'id': 'generation'})
    worker.bridge = AsyncMock(return_value={'allowed': True, 'budgetRevision': 1})
    worker.install_activity(coordinator)
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', lambda row: observed.append(deepcopy(row)))
    data = {'session_id': identity, 'outcome': 'failed', 'failure': {'message': 'PRIVATE'}}
    await callbacks['context:compaction_finished']('context:compaction_finished', data)
    failure = deepcopy(observed[-1])
    assert failure['detail'] == 'Context preparation failed.'

    class Provider:
        def get_info(self): return SimpleNamespace(id='fixture', defaults={'model': 'model'})
        async def complete(self, request):
            assert observed[-1]['detail'] == (
                'Context preparation failed.' if auxiliary else 'Thinking...')
            return SimpleNamespace(usage={})

    telemetry = ExecutionEvents('root', lambda row: None)
    async def admit(row):
        result = await worker.admit_model(row)
        assert result['allowed']
    telemetry.admission_guard = admit
    token = CALL_PURPOSE.set({'label': auxiliary, 'purpose': auxiliary} if auxiliary else None)
    try:
        await telemetry.provider_call(identity, Provider(), SimpleNamespace(model=None, metadata={}),
            lambda: Provider().complete(None), run_id='child-run' if identity == 'child' else None)
    finally:
        CALL_PURPOSE.reset(token)
    assert data['outcome'] == 'failed' and data['failure'] == {'message': 'PRIVATE'}
    assert observed[0] == failure  # Historical failure remains diagnostic evidence.
    if auxiliary:
        assert observed == [failure]
        return
    assert observed[-1]['phase'] == 'model'
    if identity == 'child':
        assert observed[-1]['runId'] == 'child-run'
        assert observed[-1]['callId'] == 'tool-1'
        return
    app = AppService(tmp_path, workspace=tmp_path)
    session = app._new_session({'workspace': str(tmp_path)})
    session['id'] = 'root'
    session['status'] = 'working'
    app.state['sessions'].append(session)
    try:
        for row in observed:
            await app.on_runtime_event(*normalize_event(row, 'root'))
        assert session['activity']['label'] == 'Thinking...'
        await app.on_runtime_event(*normalize_event({'type': 'session.idle'}, 'root'))
        assert session['status'] == 'idle' and session['activity']['label'] == 'Ready'
        assert not session.get('failure')
    finally:
        await app.close()


@pytest.mark.parametrize('admission', ['denied', 'failed', 'child-changed', 'background'])
async def test_model_admission_does_not_fabricate_continuation_activity(monkeypatch, admission):
    from unittest.mock import AsyncMock
    worker = Worker()
    scope = {'runId': 'run', 'generationId': 'generation'}
    current = [scope]
    child = SimpleNamespace(coordinator=SimpleNamespace(
        get_capability=lambda _: lambda: current[0]))
    registry = SimpleNamespace(sessions={'child': child}, rows={'child': {}})
    worker.session = SimpleNamespace(session_id='root', coordinator=SimpleNamespace(
        get_capability=lambda _: registry))
    worker.runtime = SimpleNamespace(generation={})
    worker.telemetry = Mock()
    observed = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', observed.append)

    async def bridge(*args):
        if admission == 'failed':
            raise TimeoutError('Unconfirmed bridge; do not replay')
        if admission == 'child-changed':
            current[0] = {**scope, 'runId': 'new-run'}
        return {'allowed': admission != 'denied', 'budgetRevision': 1}
    worker.bridge = bridge
    row = {'sessionId': 'child', 'runId': 'run',
           'lifecycle': 'background' if admission == 'background' else 'turn'}
    if admission == 'failed':
        with pytest.raises(TimeoutError):
            await worker.admit_model(row)
    else:
        result = await worker.admit_model(row)
        assert result['allowed'] is (admission == 'background')
    assert observed == []


async def test_terminal_compaction_failure_survives_idle_and_late_activity(tmp_path):
    from amplifier_web.service import AppService
    app = AppService(tmp_path, workspace=tmp_path)
    session = app._new_session({'workspace': str(tmp_path)})
    session['id'], session['status'] = 'root', 'working'
    app.state['sessions'].append(session)
    try:
        await app.on_runtime_event(*normalize_event({
            'type': 'generation.failed', 'generation_id': 'failed',
            'input_ids': ['input'], 'error_category': 'context_compaction',
            'error_code': 'native_input_oversized', 'error_type': 'CompactionError',
            'error_stage': 'context_preparation', 'retryable': False}, 'root'))
        failure = deepcopy(session['failure'])
        await app.on_runtime_event('runtime.error', {
            'sessionId': 'root', 'error': 'Conversation context preparation failed.'})
        await app.on_runtime_event(*normalize_event({'type': 'session.idle'}, 'root'))
        await app.on_runtime_event(*normalize_event({
            'type': 'runtime.activity', 'phase': 'model', 'detail': 'Thinking...'}, 'root'))
        assert session['status'] == 'error' and session['failure'] == failure
    finally:
        await app.close()
