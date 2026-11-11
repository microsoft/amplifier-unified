"""Provider-free input-status correlation, durability and uncertainty boundaries."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import input_status
from amplifier_web.execution import ensure_turn, finish, ingest, interrupt_unfinished
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import normalize_event
from amplifier_web.session_projection import accounting_projection, stored_execution
from test_automatic_history import app_factory, native_session


def message(exact='input', **extra):
    return {'id': 'display-' + exact, 'role': 'user', 'inputId': exact, 'text': 'private body', **extra}


def status(session, exact='input'):
    return input_status.annotate(session, message(exact))['inputStatus']


def lifecycle(kind='started', exact='input', gen='g', at=10, **extra):
    return {'event': 'generation.' + kind, 'sessionId': 'root', 'rootSessionId': 'root',
            'generation_id': gen, 'observedAt': at,
            **({'initial_input_id': exact} if kind == 'started' else {'input_ids': [exact]}), **extra}


def call(**extra):
    return {'id': 'call', 'kind': 'llm', 'phase': 'running', 'liveObservation': True,
            'sessionId': 'root', 'rootSessionId': 'root', 'admissionInputId': 'input',
            'generationId': 'g', 'startedAt': 12, **extra}


def test_admission_display_turn_idle_and_tool_failure_do_not_make_done():
    session = {'id': 'root', 'messages': [message()], 'status': 'working'}
    ensure_turn(session, 'input')
    assert status(session) == {'version': 1, 'inputId': 'input', 'phase': 'unknown', 'basis': 'no_evidence'}
    assert 'messageAnnotations' not in session
    ingest(session, call(turnId='input', admissionInputId='another-input'))
    assert status(session)['phase'] == 'unknown'
    assert status(session, 'another-input')['basis'] == 'execution_observed'
    finish(session)  # Old idle-based work display is not per-input completion.
    assert status(session, 'another-input')['phase'] == 'working'
    input_status.generation(session, lifecycle())
    ingest(session, call(kind='tool', phase='error', endedAt=14))
    finish(session)
    assert status(session)['phase'] == 'working'
    assert status(session)['startedAt'] == 10  # Never ensure_turn's optimistic clock.
    input_status.generation(session, lifecycle('finished', at=20))
    assert status(session)['phase'] == 'done'


@pytest.mark.parametrize('change', [
    {'admissionInputId': None, 'turnId': 'input'}, {'sessionId': 'child'},
    {'rootSessionId': 'other'}, {'liveObservation': False}, {'canonicalHistory': True},
    {'nativeHistory': True}, {'lifecycle': 'background'}, {'lifecycle': 'voice'},
    {'purpose': 'context_compaction'}, {'startedAt': float('nan')},
])
def test_only_live_exact_root_execution_correlates(change):
    session = {'id': 'root'}
    ingest(session, call(**change))
    assert status(session)['phase'] == 'unknown'


def test_generation_terminal_precedence_and_explicit_newer_retry():
    session = {'id': 'root'}
    input_status.generation(session, lifecycle())
    input_status.generation(session, lifecycle('finished', at=20))
    ended = deepcopy(status(session))
    ingest(session, call(startedAt=25))
    input_status.generation(session, lifecycle(at=26))  # Duplicate start.
    input_status.generation(session, lifecycle(gen='old', at=5))  # Delayed older start.
    assert status(session) == ended
    input_status.generation(session, lifecycle(gen='retry', at=30))
    assert status(session)['phase'] == 'working'
    assert status(session)['startedAt'] == 30 and 'endedAt' not in status(session)
    input_status.generation(session, lifecycle('failed', at=35))  # Old generation, late arrival.
    ingest(session, call(startedAt=36))
    assert status(session)['generationId'] == 'retry'
    assert status(session)['phase'] == 'working'
    input_status.generation(session, lifecycle('failed', gen='retry', at=40))
    input_status.generation(session, lifecycle('finished', gen='retry', at=45))
    assert status(session)['phase'] == 'failed' and status(session)['endedAt'] == 40
    input_status.generation(session, lifecycle(gen='third', at=50))
    input_status.generation(session, lifecycle('detached', gen='third', at=55))
    input_status.generation(session, lifecycle('finished', gen='third', at=60))
    assert status(session)['phase'] == 'unknown'
    assert status(session)['basis'] == 'generation_detached'
    assert status(session)['endedAt'] == 55


def test_finished_with_jobs_waits_and_unknown_start_time_is_not_fabricated():
    session = {'id': 'root'}
    input_status.generation(session, lifecycle('finished', at=20, active_job_ids=['job']))
    assert status(session) == {'version': 1, 'inputId': 'input', 'generationId': 'g',
        'phase': 'waiting', 'basis': 'waiting_workers', 'updatedAt': 20, 'endedAt': 20}
    ingest(session, call(startedAt=21))
    assert status(session)['phase'] == 'waiting'
    input_status.generation(session, lifecycle('failed', at=25))
    input_status.generation(session, lifecycle('finished', at=30))
    assert status(session)['phase'] == 'failed'


@pytest.mark.parametrize('change', [
    {'sessionId': 'child'}, {'rootSessionId': 'other'}, {'rootSessionId': None},
    {'generation_id': None}, {'observedAt': None}, {'observedAt': True},
])
def test_legacy_missing_and_child_generation_evidence_stays_unknown(change):
    session = {'id': 'root'}
    input_status.generation(session, lifecycle(**change))
    assert status(session)['phase'] == 'unknown'
    assert 'startedAt' not in status(session)


def test_projection_uses_only_exact_user_identity_and_strips_borrowed_claims():
    session = {'id': 'root'}
    input_status.generation(session, lifecycle())
    for row in [message(role='assistant'), message(inputId=None), message(nativeInputId='different'),
                message(inputId=None, nativeInputId='input', nativeInputAmbiguous=True)]:
        assert 'inputStatus' not in input_status.annotate(session, {**row, 'inputStatus': {'phase': 'done'}})
    native = {'id': 'new-native-display-id', 'role': 'user', 'nativeInputId': 'input'}
    assert input_status.annotate(session, native)['inputStatus'] == status(session)
    assert set(status(session)) == {'version', 'inputId', 'phase', 'basis',
                                    'startedAt', 'updatedAt', 'generationId'}
    assert 'private body' not in json.dumps(session)
    assert input_status.native_input_id('user', {'version': 1, 'kind': 'steer', 'id': 'input'}) == 'input'
    for marker in [{'version': True, 'kind': 'user', 'id': 'input'},
                   {'version': 1, 'kind': 'service', 'id': 'input'}, {'id': 'input'}]:
        assert input_status.native_input_id('user', marker) is None


@pytest.mark.parametrize('kind', [[], {}, ['user'], {'kind': 'user'}, None, 1, True])
def test_malformed_native_marker_kind_is_neutral_in_saved_display(kind):
    from amplifier_web.automatic_history import display_message
    marker = {'version': 1, 'kind': kind, 'id': 'input'}
    assert input_status.native_input_id('user', marker) is None
    row = display_message({'role': 'user', 'content': 'Saved input',
        'metadata': {'amplifier_input': marker}}, 0, {'id': 'root', 'createdAt': 1})
    assert 'nativeInputId' not in row and row['text'] == 'Saved input'


@pytest.mark.parametrize('boundary', ['restart', 'stop', 'error', 'process_loss'])
def test_loss_stops_timers_without_reopening_terminal_or_replaying(boundary, monkeypatch):
    session = {'id': 'root', 'status': 'idle'}
    input_status.generation(session, lifecycle())
    input_status.generation(session, lifecycle('finished', exact='done', at=20))
    input_status.generation(session, lifecycle('failed', exact='failed', at=21))
    input_status.generation(session, lifecycle('finished', exact='jobs', at=22, active_job_ids=['job']))
    monkeypatch.setattr(input_status.time, 'time', lambda: 30)
    if boundary in {'restart', 'process_loss'}:
        # Even a cold owner whose execution summary says no pending work needs
        # its previously observed input timer settled at this loss boundary.
        session['_coldFields'] = {'execution': {'pendingWork': False}}
        assert interrupt_unfinished(session)
    else:
        finish(session, 'stopped' if boundary == 'stop' else 'error')
    assert status(session)['phase'] == 'unknown' and status(session)['endedAt'] == 30
    assert status(session, 'jobs')['phase'] == 'unknown'
    input_status.generation(session, lifecycle('finished', exact='jobs', at=22))
    assert status(session, 'jobs')['phase'] == 'unknown'  # Lost waiting isn't erased by a duplicate finish.
    assert status(session, 'done')['phase'] == 'done'
    assert status(session, 'failed')['phase'] == 'failed'
    ingest(session, call(startedAt=31))
    input_status.generation(session, lifecycle(at=32))
    input_status.generation(session, lifecycle(gen='late-before-loss', at=25))
    assert status(session)['phase'] == 'unknown'
    input_status.generation(session, lifecycle(gen='fresh', at=40))
    assert status(session)['phase'] == 'working'
    assert status(session)['startedAt'] == 40


@pytest.mark.parametrize('terminal', ['finished', 'failed', 'detached'])
@pytest.mark.parametrize('observed_at', [20, 35])
def test_loss_fences_all_late_terminals_until_explicit_new_generation(terminal, observed_at, monkeypatch):
    session = {'id': 'root'}
    input_status.generation(session, lifecycle())
    monkeypatch.setattr(input_status.time, 'time', lambda: 30)
    assert input_status.interrupt(session)
    lost = deepcopy(status(session))
    assert lost['basis'] == 'execution_interrupted' and lost['endedAt'] == 30
    # Includes observedAt=20 delivered after interruption at 30, and observations
    # arriving after loss whose timestamp alone cannot prove a new execution.
    input_status.generation(session, lifecycle(terminal, at=observed_at))
    input_status.generation(session, lifecycle(terminal, gen='unseen-retry', at=36))
    assert status(session) == lost
    input_status.generation(session, lifecycle(at=38))  # Duplicate same generation.
    assert status(session) == lost
    input_status.generation(session, lifecycle(gen='retry', at=40))
    assert status(session)['phase'] == 'working' and status(session)['startedAt'] == 40
    input_status.generation(session, lifecycle(terminal, at=45))
    assert status(session)['phase'] == 'working'  # Late old generation after retry.
    input_status.generation(session, lifecycle('finished', gen='retry', at=50))
    assert status(session)['phase'] == 'done' and status(session)['endedAt'] == 50


def test_legacy_unbound_execution_stops_at_idle_but_never_claims_done():
    session = {'id': 'root'}
    ingest(session, call(generationId=None))
    assert status(session)['phase'] == 'working'
    finish(session)
    assert status(session)['phase'] == 'unknown'
    assert 'endedAt' in status(session)


def test_stop_epoch_rejects_late_unseen_start_call_and_steering():
    session = {'id': 'root', 'interruptionRevision': 1,
               'collaborationGeneration': {'id': 'g', 'interruptionRevision': 0}}
    input_status.generation(session, lifecycle(at=40))
    ingest(session, call(startedAt=41))
    input_status.steering(session, message('steer', steering={
        'generationId': 'g', 'disposition': 'applied', 'contextAt': 42}))
    assert status(session)['phase'] == status(session, 'steer')['phase'] == 'unknown'
    assert 'startedAt' not in status(session) and 'startedAt' not in status(session, 'steer')
    input_status.generation(session, lifecycle(gen='fresh', at=50, interruptionRevision=1))
    assert status(session)['phase'] == 'working'
    session['collaborationGeneration'] = {'id': 'fresh', 'interruptionRevision': 1}
    ingest(session, call(id='fresh-call', generationId='fresh', startedAt=51))
    assert status(session)['basis'] == 'execution_observed'


@pytest.mark.parametrize('kind', ['finished', 'failed', 'detached'])
def test_stop_epoch_rejects_terminal_for_input_without_a_prior_observation(kind):
    session = {'id': 'root', 'interruptionRevision': 1,
               'collaborationGeneration': {'id': 'old-g', 'interruptionRevision': 0}}
    input_status.generation(session, lifecycle(kind, exact='unseen', gen='old-g', at=20))
    assert status(session, 'unseen')['phase'] == 'unknown'
    assert 'messageAnnotations' not in session
    # No start observation is required when current exact generation and epoch
    # are otherwise authoritative. A current terminal remains useful evidence.
    session['collaborationGeneration'] = {'id': 'fresh', 'interruptionRevision': 1}
    input_status.generation(session, lifecycle(kind, exact='unseen', gen='fresh', at=40))
    assert status(session, 'unseen')['basis'] == {
        'finished': 'generation_finished', 'failed': 'generation_failed',
        'detached': 'generation_detached'}[kind]


def test_steering_queue_context_and_terminal_are_separate_evidence(monkeypatch):
    session = {'id': 'root'}
    queued = message('steer', steering={'generationId': 'g', 'disposition': 'queued', 'queuedAt': 11})
    input_status.steering(session, queued)
    assert status(session, 'steer') == {'version': 1, 'inputId': 'steer', 'generationId': 'g',
        'phase': 'queued', 'basis': 'steering_queued', 'updatedAt': 11}
    applied = deepcopy(queued)
    applied['steering'].update(disposition='applied', contextAt=15)
    input_status.steering(session, applied)
    assert status(session, 'steer')['basis'] == 'steering_context'
    assert status(session, 'steer')['phase'] == 'included'
    assert 'startedAt' not in status(session, 'steer')
    # Neither a generation nor a call for a different input starts this timer,
    # even when the display turn suggests the steering input.
    input_status.generation(session, lifecycle(exact='ambient', at=16))
    ingest(session, call(admissionInputId='ambient', turnId='steer', startedAt=17))
    assert status(session, 'steer')['phase'] == 'included'
    assert 'startedAt' not in status(session, 'steer')
    input_status.steering(session, queued)
    assert status(session, 'steer')['basis'] == 'steering_context'
    ingest(session, call(id='exact-steer-call', admissionInputId='steer', startedAt=18))
    assert status(session, 'steer')['basis'] == 'execution_observed'
    assert status(session, 'steer')['startedAt'] == 18
    input_status.steering(session, applied)
    assert status(session, 'steer')['phase'] == 'working'  # Late inclusion never replaces execution.
    input_status.generation(session, lifecycle('finished', exact='steer', at=20))
    input_status.steering(session, applied)
    assert status(session, 'steer')['phase'] == 'done'
    monkeypatch.setattr(input_status.time, 'time', lambda: 25)
    for disposition, expected in [('held', 'failed'), ('unknown', 'unknown')]:
        input_status.steering(session, message(disposition,
            steering={'generationId': 'g', 'disposition': disposition}))
        assert status(session, disposition)['phase'] == expected
        assert 'startedAt' not in status(session, disposition)


@pytest.mark.parametrize('disposition,phase', [('queued', 'queued'), ('applied', 'included')])
def test_exact_execution_can_promote_steering_but_insertion_has_no_timer(disposition, phase):
    session = {'id': 'root'}
    input_status.steering(session, message('steer', steering={'generationId': 'g',
        'disposition': disposition, 'queuedAt': 11, 'contextAt': 15}))
    assert status(session, 'steer')['phase'] == phase
    assert 'startedAt' not in status(session, 'steer') and 'endedAt' not in status(session, 'steer')
    ingest(session, call(admissionInputId='steer', startedAt=20))
    assert status(session, 'steer')['phase'] == 'working'
    assert status(session, 'steer')['startedAt'] == 20


@pytest.mark.parametrize('disposition', ['queued', 'applied'])
def test_unexecuted_steering_is_fenced_on_loss_without_fabricated_start(disposition, monkeypatch):
    session = {'id': 'root'}
    input_status.steering(session, message('steer', steering={'generationId': 'g',
        'disposition': disposition, 'queuedAt': 11, 'contextAt': 15}))
    monkeypatch.setattr(input_status.time, 'time', lambda: 30)
    assert input_status.interrupt(session)
    lost = deepcopy(status(session, 'steer'))
    assert lost['phase'] == 'unknown' and lost['basis'] == 'execution_interrupted'
    assert 'startedAt' not in lost
    input_status.generation(session, lifecycle('finished', exact='steer', at=20))
    input_status.steering(session, message('steer', steering={
        'generationId': 'g', 'disposition': 'applied', 'contextAt': 35}))
    ingest(session, call(admissionInputId='steer', startedAt=36))
    assert status(session, 'steer') == lost


def test_human_wait_requires_exact_input_and_generation_not_ambient_pending(monkeypatch):
    session = {'id': 'root', 'approvals': [{'id': 'ambient', 'status': 'pending'}]}
    input_status.generation(session, lifecycle())
    event = {'id': 'question', 'sessionId': 'root', 'rootSessionId': 'root',
             'inputId': 'input', 'generationId': 'g'}
    for change in [{'inputId': None}, {'generationId': None}, {'generationId': 'other'},
                   {'sessionId': 'child'}, {'rootSessionId': None}]:
        input_status.human_wait(session, {**event, **change})
        assert status(session)['phase'] == 'working'
    monkeypatch.setattr(input_status.time, 'time', lambda: 15)
    input_status.human_wait(session, event)
    assert status(session)['phase'] == 'waiting'
    assert status(session)['basis'] == 'human_decision'
    ingest(session, call(id='successor', startedAt=18))
    assert status(session)['phase'] == 'working'


async def test_provider_admission_cause_is_not_fitted_request_membership_or_read(monkeypatch):
    session = {'id': 'root'}
    rows = []
    def emit(event):
        rows.append(event)
        kind, payload = normalize_event(event, 'root')
        assert kind == 'execution.event'
        ingest(session, payload)
    telemetry = ExecutionEvents('root', emit)
    telemetry.lifecycle({'type': 'input.delivered', 'input_id': 'display-turn'})
    telemetry.lifecycle({'type': 'generation.started', 'generation_id': 'g',
                         'initial_input_id': 'input'})
    class Provider:
        def get_info(self):
            return {'id': 'synthetic', 'defaults': {'model': 'fixture'}}
        async def complete(self, request):
            assert request.messages == []  # Cause deliberately absent from request.
            assert status(session)['basis'] == 'execution_observed'
            return SimpleNamespace(usage={})
    monkeypatch.setattr('amplifier_web.execution_events.time.time', lambda: 12)
    provider = telemetry.instrument_provider('root', Provider())
    await provider.complete(SimpleNamespace(model='fixture', metadata={}, messages=[]))
    assert status(session)['phase'] == 'working'  # A completed call is not a completed turn.
    assert all(row['event']['admissionInputId'] == 'input' for row in rows)
    assert all(row['event']['turnId'] == 'display-turn' for row in rows)
    receipt = accounting_projection(session['execution'])[0]
    assert receipt['admissionInputId'] == 'input' and receipt['generationId'] == 'g'
    assert 'messages' not in json.dumps(receipt) and 'read' not in status(session)
    persisted = stored_execution(session['execution'])
    assert persisted['retiredUsageNodes'][0]['admissionInputId'] == 'input'


def test_display_turn_is_never_an_unobserved_admission_fallback():
    telemetry = ExecutionEvents('root', lambda event: None)
    telemetry.turn_id = 'display-only'
    assert telemetry.admission_input('root') is None
    telemetry.lifecycle({'type': 'generation.started', 'sessionId': 'child',
                         'generation_id': 'child-g', 'initial_input_id': 'input'})
    assert telemetry.admission_input('root') is None
    telemetry.lifecycle({'type': 'generation.started', 'sessionId': 'root', 'rootSessionId': 'other',
                         'generation_id': 'foreign-g', 'initial_input_id': 'input'})
    assert telemetry.admission_input('root') is None


def test_source_lifecycle_timestamp_survives_normalization(monkeypatch):
    from amplifier_web.runtime_worker import Worker
    worker = Worker()
    worker.runtime = SimpleNamespace(session_id='root', generation={'id': 'g'})
    emitted = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', emitted.append)
    monkeypatch.setattr('amplifier_web.runtime_worker.time.time', lambda: 10)
    worker.observe({'type': 'generation.started', 'generation_id': 'g', 'initial_input_id': 'input'})
    monkeypatch.setattr('amplifier_web.runtime_worker.time.time', lambda: 50)
    kind, event = normalize_event(emitted[0], 'root')
    assert kind == 'runtime.generation'
    assert event['observedAt'] == 10  # Not the later receipt/normalization time.
    session = {'id': 'root'}
    input_status.generation(session, event)
    assert status(session)['startedAt'] == 10


async def test_existing_steering_reducer_supplies_queue_and_context_annotations(app_factory, monkeypatch):
    from amplifier_web.conversation_steering import record
    app = app_factory()
    current = app._new_session({'title': 'Steering status'})
    app._state['sessions'].append(current)
    current['messages'] = [message('steer', steering={'generationId': 'g', 'disposition': 'sending'})]
    monkeypatch.setattr(input_status.time, 'time', lambda: 11)
    record(app, current, 'steer', 'queued')
    assert current['messages'][0]['delivery']['status'] == 'accepted'
    assert status(current, 'steer')['phase'] == 'queued'
    assert status(current, 'steer')['basis'] == 'steering_queued'
    assert 'startedAt' not in status(current, 'steer')
    monkeypatch.setattr(input_status.time, 'time', lambda: 15)
    record(app, current, 'steer', 'applied')
    assert status(current, 'steer')['basis'] == 'steering_context'
    assert status(current, 'steer')['phase'] == 'included'
    assert 'startedAt' not in status(current, 'steer')
    input_status.generation(current, {**lifecycle('failed', exact='steer', at=20),
        'sessionId': current['id'], 'rootSessionId': current['id']})
    assert status(current, 'steer')['phase'] == 'failed'
    assert current['messages'][0]['delivery']['status'] == 'accepted'


async def test_durable_annotations_survive_reload_bounded_generation_tail_and_overlay(app_factory):
    from amplifier_web.browser_detail import page, project
    app = app_factory()
    current = app._new_session({'title': 'Status fixture'})
    app._state['sessions'].append(current)
    app._state['selectedSessionId'] = current['id']
    current['messages'] = [message(), message('active')]
    async def observe(event):
        await app.on_runtime_event('runtime.generation',
            {**event, 'sessionId': current['id'], 'rootSessionId': current['id']})
    await observe(lifecycle())
    await observe(lifecycle('finished', at=20))
    await observe(lifecycle(exact='active', gen='active-g', at=30))
    current['generations'].extend({'event': 'legacy', 'generation_id': str(i)} for i in range(200))
    await observe({'event': 'legacy', 'generation_id': 'trim'})
    assert len(current['generations']) == 200
    assert not any(event.get('generation_id') == 'g' for event in current['generations'])
    assert page(current, 'messages')['items'][0]['inputStatus']['phase'] == 'done'
    # An older body window must join current status, not retain a stale saved claim.
    overlay = {'messages': [message(inputStatus={'phase': 'working'})]}
    projected = project(current, message_overlay=overlay,
                        work_overlay={'execution': {'nodes': [], 'turns': [], 'segments': []}})
    assert projected['messages'][0]['inputStatus']['phase'] == 'done'
    assert app.session_state(current['id'])['sessions'][0]['messages'][0]['inputStatus']['phase'] == 'done'
    app._publish()
    identity, home, workspace = current['id'], app.data_dir, current['workspace']
    await app.close()
    restored = app_factory(home=home, workspace=Path(workspace))
    saved = restored._session(identity)
    assert status(saved)['phase'] == 'done'
    assert status(saved, 'active')['phase'] == 'unknown'
    assert 'endedAt' in status(saved, 'active')
    assert saved['messages'][0]['text'] == 'private body'
    assert not restored.runtime.started and not restored.runtime.sent


async def test_bounded_native_history_carries_valid_marker_and_joins_without_resident_body(app_factory, tmp_path):
    from amplifier_web.transcript_windows import TranscriptWindows, _native_page
    from amplifier_web.transcript_window import TranscriptWindowReader
    workspace = tmp_path / 'native-workspace'
    native_session(workspace, 'native-status', [
        {'role': 'user', 'content': 'Native input', 'metadata': {
            'amplifier_input': {'version': 1, 'kind': 'user', 'id': 'input'}}},
        {'role': 'assistant', 'content': 'Native reply'}])
    from amplifier_web.session_files import project_slug
    source = {'id': 'root', 'workspace': str(workspace), 'nativeProject': project_slug(workspace),
              'nativeIdentity': 'native-status'}
    page = _native_page(TranscriptWindowReader(), source, None, 100)
    assert page['items'][0]['nativeInputId'] == 'input'
    assert page['globalInputUniqueness'] == 'unknown'
    assert not page['items'][0].get('nativeInputAmbiguous')
    app = app_factory()
    current = app._new_session({'title': 'Bounded status'})
    app._state['sessions'].append(current)
    current['messages'] = []
    input_status.generation(current, {**lifecycle('finished', at=20),
        'sessionId': current['id'], 'rootSessionId': current['id']})
    windows = TranscriptWindows(app)
    wire = windows._wire(page, current['id'])
    assert wire['items'][0]['inputStatus']['phase'] == 'done'
    assert current['messages'] == []  # No backfill or native-execution mount.
    assert not app.runtime.started and not app.runtime.sent
    await windows.close()


@pytest.mark.parametrize('hidden', ['ephemeral', 'reference', 'recovery', 'image', 'empty'])
async def test_bounded_native_status_counts_hidden_raw_input_occurrences(
        app_factory, tmp_path, monkeypatch, hidden):
    from amplifier_web import transcript_window
    from amplifier_web.session_files import project_slug
    from amplifier_web.transcript_windows import TranscriptWindows, _native_page

    visible = {'role': 'user', 'content': 'Visible native input', 'metadata': {
        'amplifier_input': {'version': 1, 'kind': 'user', 'id': 'input'}}}
    duplicate = deepcopy(visible)
    if hidden == 'image':
        duplicate['content'] = [{'type': 'image', 'source': {
            'type': 'base64', 'media_type': 'image/png', 'data': 'AA=='}}]
    elif hidden == 'empty':
        duplicate['content'] = []
    else:
        key = {'ephemeral': 'ephemeral', 'reference': 'amplifier_visible_reference',
               'recovery': 'amplifier_recovery_reference'}[hidden]
        duplicate['metadata'][key] = True
    workspace = tmp_path / 'native-workspace'
    directory = native_session(workspace, 'hidden-status', [duplicate, visible])
    path = directory / 'transcript.jsonl'
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    source = {'id': 'root', 'nativeProject': project_slug(workspace), 'nativeIdentity': 'hidden-status'}
    reader = transcript_window.TranscriptWindowReader()
    reads = []
    original = reader._read
    def read(*args):
        reads.append(args[0])
        return original(*args)
    monkeypatch.setattr(reader, '_read', read)
    page = _native_page(reader, source, None, 100)
    assert len(reads) == 1
    assert page['coverage']['scannedRecords'] == 2
    assert page['coverage']['rangeStart'] == 0 and not page['hasEarlier']
    assert page['unavailableRanges'] == [] and page['source'] == 'primary'
    assert page['globalInputUniqueness'] == 'unknown'
    assert len(page['items']) == 1 and page['items'][0]['nativeInputId'] == 'input'
    assert page['items'][0]['nativeInputAmbiguous'] is True
    app = app_factory()
    current = app._new_session({'title': 'Hidden duplicate status'})
    app._state['sessions'].append(current)
    input_status.generation(current, {**lifecycle('finished', at=20),
        'sessionId': current['id'], 'rootSessionId': current['id']})
    saved = deepcopy(current)
    windows = TranscriptWindows(app)
    try:
        writes = app.db.total_changes
        wire = windows._wire(page, current['id'])
        assert all('inputStatus' not in row for row in wire['items'])
        assert current == saved and current['messages'] == []
        assert app.db.total_changes == writes
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before
        assert not app.runtime.started and not app.runtime.sent
    finally:
        await windows.close()


@pytest.mark.parametrize('boundary', [
    'unique-limit', 'duplicate-limit', 'raw-limit', 'earlier-cursor',
    'restore-cursor', 'gap', 'backup', 'append-pending-backup',
])
async def test_bounded_native_status_requires_complete_initial_primary(
        app_factory, tmp_path, monkeypatch, boundary):
    from amplifier_web import transcript_window
    from amplifier_web.session_files import project_slug
    from amplifier_web.transcript_windows import TranscriptWindows, _native_page

    native = {'role': 'user', 'content': 'Visible native input', 'metadata': {
        'amplifier_input': {'version': 1, 'kind': 'user', 'id': 'input'}}}
    assistant = {'role': 'assistant', 'content': 'Native reply'}
    rows, limit = [assistant, native], 1
    if boundary == 'duplicate-limit':
        rows = [{**native, 'content': 'Older duplicate'}, native]
    elif boundary == 'raw-limit':
        rows, limit = [native, {'role': 'user', 'content': []}, native], 100
        monkeypatch.setattr(transcript_window, 'RAW_LIMIT', 2)
    elif boundary == 'earlier-cursor':
        rows = [native, assistant]
    elif boundary == 'gap':
        rows, limit = [{**assistant, 'content': 'x' * 512}, native], 100
        monkeypatch.setattr(transcript_window, 'RECORD_LIMIT', 256)
    elif boundary in {'restore-cursor', 'backup', 'append-pending-backup'}:
        rows, limit = [native], 100
    workspace = tmp_path / 'native-workspace'
    directory = native_session(workspace, 'partial-status', rows)
    path = directory / 'transcript.jsonl'
    if boundary in {'backup', 'append-pending-backup'}:
        (directory / 'transcript.jsonl.backup').write_bytes(path.read_bytes())
        if boundary == 'backup':
            path.write_text('invalid primary\n')
        else:
            (directory / 'transcript.jsonl.append-pending').write_text('{}')
    # Only synthetic transcript sources are inspected, never capture/event logs.
    paths = list(directory.glob('transcript.jsonl*'))
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    source = {'id': 'root', 'nativeProject': project_slug(workspace), 'nativeIdentity': 'partial-status'}
    reader = transcript_window.TranscriptWindowReader()
    reads = []
    original = reader._read
    def read(*args):
        reads.append(args[0])
        return original(*args)
    monkeypatch.setattr(reader, '_read', read)
    cursor = None
    if boundary in {'earlier-cursor', 'restore-cursor'}:
        initial = _native_page(reader, source, None, limit)
        cursor = initial['before'] if boundary == 'earlier-cursor' else initial['restoreCursor']
        assert cursor is not None
        reads.clear()
    page = _native_page(reader, source, cursor, limit)
    assert len(reads) == (2 if boundary == 'backup' else 1)
    if boundary.endswith('limit'):
        assert page['hasEarlier'] and page['coverage']['rangeStart'] > 0
    elif boundary == 'earlier-cursor':
        # Even reaching byte zero on an earlier page says nothing about the suffix.
        assert not page['hasEarlier'] and page['coverage']['rangeStart'] == 0
    elif boundary == 'gap':
        assert page['unavailableRanges'] and not page['hasEarlier']
    elif boundary in {'backup', 'append-pending-backup'}:
        assert page['source'] == 'backup' and page['recoveredBackup'] is True
    assert page['globalInputUniqueness'] == 'unknown'
    assert len(page['items']) == 1 and page['items'][0]['nativeInputId'] == 'input'
    assert page['items'][0]['nativeInputAmbiguous'] is True
    app = app_factory()
    current = app._new_session({'title': 'Partial native status'})
    app._state['sessions'].append(current)
    input_status.generation(current, {**lifecycle('finished', at=20),
        'sessionId': current['id'], 'rootSessionId': current['id']})
    saved = deepcopy(current)
    windows = TranscriptWindows(app)
    try:
        writes = app.db.total_changes
        wire = windows._wire(page, current['id'])
        assert all('inputStatus' not in row for row in wire['items'])
        assert current == saved and current['messages'] == []
        assert app.db.total_changes == writes
        assert {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before
        assert not app.runtime.started and not app.runtime.sent
    finally:
        await windows.close()


def test_reaction_overlays_lookup_requested_page_ids_without_scanning_input_receipts():
    from amplifier_web.browser_detail import MESSAGE_LIMIT, page, project
    from amplifier_web.peer_attribution import display_annotations
    from amplifier_web.state_projections import StateProjections
    class LookupOnlyAnnotations(dict):
        def items(self):
            raise AssertionError('Display overlays must not scan all per-input annotations')
        def __iter__(self):
            raise AssertionError('Display overlays must look up only requested message IDs')
        def get(self, key, default=None):
            self.lookups.append(key)
            return super().get(key, default)
    annotations = LookupOnlyAnnotations({input_status.PREFIX + str(index): {
        'inputStatus': {'version': 1, 'inputId': str(index), 'phase': 'unknown', 'basis': 'no_evidence'}}
        for index in range(2000)})
    annotations.lookups = []
    messages = [message(str(index)) for index in range(200)]
    oldest, newest = messages[0]['id'], messages[-1]['id']
    annotations[oldest] = {'reactions': ['\u2705']}
    annotations[newest] = {'reactions': ['\u2705'], 'assistantReactions': [],
                           'inputId': 'forged', 'text': 'forged', 'inputStatus': {'phase': 'done'}}
    # Even a reserved entry with reaction-shaped data is never a message overlay.
    reserved = input_status.PREFIX + '199'
    annotations[reserved]['reactions'] = ['\u2705']
    session = {'id': 'root', 'messages': messages, 'messageAnnotations': annotations}
    assert display_annotations(session, [newest, reserved, []]) == {
        newest: {'reactions': ['\u2705'], 'assistantReactions': []}}
    assert annotations.lookups == [newest]
    annotations.lookups.clear()
    requested_page = page(session, 'messages')
    page_ids = {row['id'] for row in requested_page['items']}
    assert len(page_ids) == MESSAGE_LIMIT and oldest not in page_ids
    assert {key for key in annotations.lookups if not key.startswith(input_status.PREFIX)} <= page_ids
    assert requested_page['items'][-1]['text'] == 'private body'
    assert requested_page['items'][-1]['inputStatus']['phase'] == 'unknown'
    expected = {newest: {'reactions': ['\u2705'], 'assistantReactions': []}}
    for projected in (project(session), StateProjections().detail(session)):
        assert projected['messageAnnotations'] == expected
    annotations.lookups.clear()
    projected = project(session, message_overlay={'messages': messages[-2:]},
        work_overlay={'execution': {'nodes': [], 'turns': [], 'segments': []}})
    assert projected['messageAnnotations'] == expected
    visible = {row['id'] for row in projected['messages']}
    assert {key for key in annotations.lookups if not key.startswith(input_status.PREFIX)} <= visible
    assert len(annotations.lookups) <= 2 * len(visible)


def test_input_receipts_do_not_change_reaction_context_or_omitted_message_count():
    from amplifier_web.message_interactions import reaction_context
    class MetadataOnly(dict):
        def get(self, key, *args):
            assert key != 'messages', 'Reaction context must not hydrate the transcript'
            return super().get(key, *args)
    session = MetadataOnly(id='root',
        messageAnnotations={str(index): {'reactions': ['\u2705']} for index in range(40)},
        messageReactionOrder=['39'])
    baseline = reaction_context(session)
    assert len(baseline['items']) == 32 and baseline['omittedMessageCount'] == 8
    for index in range(2000):
        session['messageAnnotations'][input_status.PREFIX + str(index)] = {
            'inputStatus': {'version': 1, 'inputId': str(index), 'phase': 'unknown', 'basis': 'no_evidence'},
            'reactions': ['\u2705']}  # A reserved key must not enter reaction accounting.
    session['messageReactionOrder'].append(input_status.PREFIX + '1999')
    assert reaction_context(session) == baseline
    empty = MetadataOnly(id='empty', messageAnnotations={
        input_status.PREFIX + 'input': session['messageAnnotations'][input_status.PREFIX + '0']})
    context = reaction_context(empty)
    assert context['items'] == [] and context['omittedMessageCount'] == 0