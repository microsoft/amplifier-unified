"""A denied local model call keeps its cause through the actual native loop."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import normalize_event
from amplifier_web.runtime_worker import Worker
from test_assignments import app, limits, start
from test_task_continuity import controls


async def native_attempt(app, source, control, *, identity, tools=(), history=(), final=False,
                         active_goal=False):
    pytest.importorskip('amplifier_module_loop_live')
    from amplifier_core import AmplifierSession
    from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
    from amplifier_module_loop_live.runtime import Input, Runtime

    sid = source['id']
    pending = None
    emitted = []
    telemetry = None
    evaluations = []

    async def project(previous, event):
        if previous:
            await previous
        normalized = normalize_event(event, sid)
        if normalized:
            await app.on_runtime_event(*normalized)

    def observe(event):
        nonlocal pending
        emitted.append(event)
        if telemetry:
            telemetry.lifecycle(event)
        pending = asyncio.create_task(project(pending, event))

    telemetry = ExecutionEvents(sid, observe)
    live = Runtime(session_id=sid, observer=observe)
    native = AmplifierSession({'session': {
        'orchestrator': {'module': 'loop-live', 'config': {
            'configured_bundle': True, 'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []}, session_id=sid)
    await native.initialize()
    native.coordinator.register_capability('live.runtime', live)
    if active_goal:
        native.coordinator.session_state['goal'] = control.coordinator.session_state['goal']
        native.coordinator.register_capability('live.continuation_guard', control.tasks.continuation_allowed)
        async def unexpected_evaluation(*args):
            evaluations.append(True)
            raise AssertionError('A denied model request must not reach automatic goal evaluation')
        native.coordinator.get('orchestrator')._evaluate_goal = unexpected_evaluation
    context = native.coordinator.get('context')
    for message in history:
        await context.add_message(message)

    async def admitted(row):
        if pending:
            await pending
        return await app.app_bridge('capacity.admit', {
            'call': row, '_generationId': live.generation['id'],
            '_runtimeSessionId': sid, 'waitForCapacity': True}, sid)
    control.capacity.admit = admitted
    telemetry.admission_guard = control.capacity.guard

    class Provider:
        name = 'fixture'
        config = {}
        calls = 0
        def get_info(self):
            return SimpleNamespace(id='fixture', default_model='fixture')
        def parse_tool_calls(self, response):
            return response.tool_calls or []
        async def complete(self, request, **kwargs):
            self.calls += 1
            if final:
                return ChatResponse(content=[TextBlock(text='Remaining work completed.')])
            return ChatResponse(content=[], tool_calls=[
                ToolCall(id='once', name='save_once', arguments={})],
                usage={'input_tokens': 4, 'output_tokens': 1, 'total_tokens': 5})

    provider = Provider()
    telemetry.instrument_provider(sid, provider)
    await native.coordinator.mount('providers', provider, name='fixture')
    for tool in tools:
        await native.coordinator.mount('tools', tool, name=tool.name)
    task = asyncio.create_task(native.execute(''))
    try:
        await live.wait_for(lambda event: event['type'] == 'session.ready', 2)
        await live.submit(Input('user', 'Continue only the remaining work.', id=identity))
        if final:
            await live.wait_for(lambda event: event['type'] == 'generation.finished', 3)
            await live.submit(Input('stop', target='cancel'))
        else:
            await live.wait_for(lambda event: event['type'] == 'generation.failed', 3)
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)
        if active_goal:
            assert task.done() and live.closed and live.generation is None
            assert live.queued_inputs == 0 and live.inbox.empty() and not live.pending_steering
            assert evaluations == []  # An orchestrator could swallow a raised evaluation error.
            count = len(emitted)
            await asyncio.sleep(.1)
            assert len(emitted) == count  # No retry/notification loop after the failed attempt.
            assert sum(e['type'] == 'generation.started' for e in emitted) == 1
            assert sum(e['type'] == 'generation.failed' for e in emitted) == 1
        if pending:
            await pending
        if not final:
            # The real worker completion path follows the native terminal/closure.
            import amplifier_web.runtime_worker as worker_module
            original = worker_module.publish
            worker_module.publish = observe
            try:
                worker = Worker()
                worker.executed(task)
            finally:
                worker_module.publish = original
            if pending:
                await pending
        return provider.calls, await context.get_messages(), emitted
    finally:
        if not task.done():
            await live.submit(Input('stop', target='cancel'))
            await asyncio.gather(asyncio.wait_for(task, 2), return_exceptions=True)
        if pending:
            await pending
        await native.cleanup()


async def test_actual_loop_local_limit_retains_actionable_cause_and_fresh_continuation(app, monkeypatch):
    from amplifier_core import ToolResult

    source = app.state['sessions'][0]
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    await start(app)
    control = controls(source['id'])

    class Save:
        name = 'save_once'
        description = 'Save one finished effect before the next model call.'
        input_schema = {'type': 'object', 'properties': {}}
        calls = 0
        async def execute(self, args):
            self.calls += 1
            await limits(app, source, maxTotalTokens=1)
            return ToolResult(success=True, output={'saved': 'retained-effect'})

    tool = Save()
    try:
        calls, history, events = await native_attempt(app, source, control,
            identity='original-input', tools=[tool])
        assert calls == 1 and tool.calls == 1, events
        assert any(m.get('role') == 'tool' and 'retained-effect' in str(m.get('content')) for m in history)
        assert source['failure']['category'] == 'model_capacity'
        assert source['failure']['stage'] == 'model_admission'
        assert 'limit reached' in ' '.join(source['failure']['admission']['reasons']).lower()
        assert source['failure']['admission']['providerRequestSent'] is False
        assert source['failure']['retryable'] is False
        assert source['failure']['replayed'] is False
        terminal = next(e for e in events if e['type'] == 'generation.failed')
        assert source['failure']['generationId'] == terminal['generation_id']
        failure = copy.deepcopy(source['failure'])
        await app.on_runtime_event('runtime.status', {'sessionId': source['id'], 'status': 'ready'})
        assert source['failure'] == failure
        await app.dispatch('assignment.set', {'sessionId': source['id'],
            'expectedRevision': app.assignments.current(source)['revision'],
            'maxTotalTokens': None}, command_id='operator-adjustment')
        before = copy.deepcopy(app.assignments.current(source)['limits'])
        fresh_calls, new_history, fresh_events = await native_attempt(app, source, control,
            identity='fresh-input', history=history, final=True)
        assert fresh_calls == 1 and tool.calls == 1
        assert app.assignments.current(source)['limits'] == before
        assert any(m.get('role') == 'tool' and 'retained-effect' in str(m.get('content')) for m in new_history)
        assert not source.get('failure')
        assert control.capacity.last_denial is None
        assert any(e['type'] == 'generation.finished' for e in fresh_events)
    finally:
        await control.close()


@pytest.mark.parametrize('restart', [False, True])
async def test_current_nonrenewable_denial_ends_native_active_goal_without_spin(app, monkeypatch, restart):
    source = app.state['sessions'][0]
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    await start(app)
    await limits(app, source, maxTotalTokens=1, reserveTokens=2)
    control = controls(source['id'])
    try:
        await control.perform('task.create', {
            'commandId': 'active-denied-goal', 'expectedRevision': 0,
            'objective': 'Keep working until verified', 'maxTurns': 4})
        assert control.capacity.last_denial is None
        if restart:
            control.persist(task_only=True)
            await control.close()
            control = controls(source['id'])
            await control.restore()
        used = control.tasks.record()['goalState']['turns_used']
        for index in range(3):
            # Native user commands also carry host-framed peer inputs; source
            # is always "user" at this adapter (not service observation).
            # Each fresh input is one bounded attempt, not automatic re-entry.
            calls, _, events = await native_attempt(
                app, source, control, identity=f'current-denied-input-{index}',
                active_goal=True)
            assert calls == 0
            assert not any(e['type'] == 'generation.finished' for e in events)
            assert control.tasks.record()['goalState']['turns_used'] == used
        assert control.tasks.record()['status'] == 'active'
        assert source['failure']['category'] == 'model_capacity'
        assert any('insufficient unreserved' in reason
                   for reason in source['failure']['admission']['reasons'])
        assert app.assignments.usage(app.assignments.current(source))['calls'] == 0
        assert not app._model_admission_waiters
    finally:
        await control.close()


from test_model_admission_wait import providers, waiting_pair


async def test_unknown_reservations_remain_held_and_reason_survives_reopen(app, providers):
    from amplifier_web.capacity import restore_observation
    from amplifier_web.model_admission import ModelAdmissionPaused

    source, target, first, second, a, b = await waiting_pair(app, providers)
    restore_observation(source)
    app._publish_changes(sessions={source['id']})
    with pytest.raises(ModelAdmissionPaused):
        await b
    observed = copy.deepcopy(target['modelAdmission'])
    assert 'concurrent model-call limit' in ' '.join(observed['reasons'])
    held = copy.deepcopy(app.assignments.usage(app.assignments.current(source))['receipts'])
    assert held[0]['phase'] == 'outcome_unknown'
    assert not second.called
    await app.on_runtime_event(*normalize_event({
        'type': 'generation.failed', 'generation_id': observed['generationId'],
        'input_ids': observed['inputIds'], 'error_type': 'ValueError',
        'error_category': 'unknown', 'error_stage': 'manager_turn'}, target['id']))
    assert target['failure']['category'] == 'model_capacity'
    assert 'unresolved' in target['failure']['guidance']
    assert target['failure']['terminalFailure']['error_category'] == 'unknown'
    assert app.assignments.usage(app.assignments.current(source))['receipts'] == held
    # Inspect the committed observation without mounting a competing app owner.
    import json
    from amplifier_web.cold_display import load
    encoded = app.db.execute("SELECT value FROM state_records WHERE kind='session' AND id=?",
                             (target['id'],)).fetchone()[0]
    saved = json.loads(encoded)
    if saved.get('$viewPayload'):
        saved.update(load(app.db, saved['$viewPayload']))
    assert saved['failure'] == target['failure']
    assert saved['modelAdmission'] == observed
    assert app.assignments.usage(app.assignments.current(source))['receipts'] == held


async def test_wait_expiry_is_distinct_from_provider_timeout_or_saved_budget_pause(app, providers):
    from amplifier_web.model_admission import ModelAdmissionWaitExpired

    await app.dispatch('modelCapacity.set', {'expectedRevision': 0,
        'waitTimeoutSeconds': 1, 'maxWaitingCalls': 256}, command_id='short-outcome-wait')
    source, target, first, second, a, b = await waiting_pair(app, providers)
    with pytest.raises(ModelAdmissionWaitExpired) as error:
        await b
    assert not second.called and second.control.capacity.last_denial is None
    observed = target['modelAdmission']
    await app.on_runtime_event(*normalize_event({
        'type': 'generation.failed', 'generation_id': observed['generationId'],
        'input_ids': observed['inputIds'],
        **pytest.importorskip('amplifier_module_loop_live.failures').turn_failure(error.value)}, target['id']))
    assert target['failure']['category'] == 'model_capacity_wait_expired'
    assert target['failure']['retryable'] is True
    assert target['failure']['admission']['providerRequestSent'] is False


@pytest.mark.parametrize('change', [
    'generation', 'input', 'active-input', 'stop', 'configuration',
    'different-error', 'safety-error', 'missing-observation'])
def test_denial_never_relabels_an_unrelated_terminal(change):
    from amplifier_web.model_admission import failure

    observed = {'generationId': 'generation', 'inputIds': ['input'],
        'interruptionRevision': 2, 'configurationRevision': 3, 'callId': 'call',
        'state': 'paused', 'budgetRevision': 1, 'reasons': ['A saved limit was reached.'],
        'recordedAt': 123, 'providerRequestSent': False}
    session = {'modelAdmission': observed,
        'collaborationGeneration': {'id': 'generation', 'inputIds': ['input']},
        'interruptionRevision': 2, 'configurationPendingRevision': 3}
    event = {'generation_id': 'generation', 'input_ids': ['input'],
        'error_type': 'ValueError', 'error_category': 'unknown'}
    assert failure(session, event)
    if change == 'generation': event['generation_id'] = 'later'
    elif change == 'input': event['input_ids'] = ['later']
    elif change == 'active-input': session['collaborationGeneration']['inputIds'] = ['later']
    elif change == 'stop': session['interruptionRevision'] += 1
    elif change == 'configuration': session['configurationPendingRevision'] += 1
    elif change == 'different-error': event['error_type'] = 'OSError'
    elif change == 'safety-error': event['error_category'] = 'content_filter'
    else: session.pop('modelAdmission')
    assert failure(session, event) is None


@pytest.mark.parametrize('change', ['stop', 'input', 'generation', 'background', 'child', 'duplicate'])
async def test_late_or_nonroot_admission_cannot_attach_to_root(app, monkeypatch, change):
    from amplifier_web import capacity_wait
    from amplifier_web.collaboration import BINDING
    from amplifier_web.model_admission import admit
    target = app.state['sessions'][0]
    active = target['collaborationGeneration']
    row = {'id': 'unsubmitted', 'sessionId': target['id'], 'rootSessionId': target['id'],
           'turnId': active['inputIds'][0]}
    if change == 'background': row['lifecycle'] = 'background'
    elif change == 'child': row['sessionId'] = 'child'
    elif change == 'duplicate':
        target['execution'] = {'nodes': [{'id': row['id'], 'admittedAt': 123}]}

    async def deny(*args):
        if change == 'stop': target['interruptionRevision'] = target.get('interruptionRevision', 0) + 1
        elif change == 'input': active['inputIds'] = ['later']
        elif change == 'generation': active['id'] = 'later'
        return {'allowed': False, 'state': 'paused', 'reasons': ['limit']}
    monkeypatch.setattr(capacity_wait, 'admit', deny)
    token = BINDING.set({'_generationId': active['id']})
    try:
        assert not (await admit(app, target['id'], {'call': row}))['allowed']
        assert 'modelAdmission' not in target
    finally:
        BINDING.reset(token)


async def test_denial_size_is_bounded_and_later_admission_retires_observation(app, monkeypatch):
    from amplifier_web import capacity_wait
    from amplifier_web.collaboration import BINDING
    from amplifier_web.model_admission import admit
    target = app.state['sessions'][0]
    active = target['collaborationGeneration']
    row = {'id': 'unsubmitted', 'sessionId': target['id'], 'rootSessionId': target['id'],
           'turnId': active['inputIds'][0]}
    result = {'allowed': False, 'state': 'paused', 'reasons': ['x' * 2000] * 30}
    async def reply(*args):
        return result
    monkeypatch.setattr(capacity_wait, 'admit', reply)
    token = BINDING.set({'_generationId': active['id']})
    try:
        await admit(app, target['id'], {'call': row})
        saved = target['modelAdmission']
        assert len(saved['reasons']) == 8
        assert all(len(reason) == 500 for reason in saved['reasons'])
        result = {'allowed': True}
        await admit(app, target['id'], {'call': {**row, 'id': 'later-permitted'}})
        assert 'modelAdmission' not in target
    finally:
        BINDING.reset(token)


@pytest.mark.parametrize('terminal_before_restart', [False, True])
async def test_exact_host_observation_survives_process_restart(app, monkeypatch, terminal_before_restart):
    from amplifier_web import capacity_wait
    from amplifier_web.collaboration import BINDING
    from amplifier_web.model_admission import admit
    from amplifier_web.service import AppService
    target = app.state['sessions'][0]
    active = target['collaborationGeneration']
    row = {'id': 'never-submitted', 'sessionId': target['id'], 'rootSessionId': target['id'],
           'turnId': active['inputIds'][0]}
    async def blocked(*args):
        return {'allowed': False, 'state': 'paused', 'budgetRevision': 2,
                'reasons': ['Unresolved calls still hold their reservations.']}
    monkeypatch.setattr(capacity_wait, 'admit', blocked)
    token = BINDING.set({'_generationId': active['id']})
    try:
        await admit(app, target['id'], {'call': row})
    finally:
        BINDING.reset(token)
    before = copy.deepcopy(target['modelAdmission'])
    terminal = normalize_event({
        'type': 'generation.failed', 'generation_id': before['generationId'],
        'input_ids': before['inputIds'], 'error_type': 'ValueError',
        'error_category': 'unknown', 'error_stage': 'manager_turn'}, target['id'])
    if terminal_before_restart:
        await app.on_runtime_event(*terminal)
        failure_before = copy.deepcopy(target['failure'])
        assert failure_before['category'] == 'model_capacity'
        assert failure_before['admission']['callId'] == row['id']
        assert failure_before['admission']['providerRequestSent'] is False
    await app.close()
    reopened = AppService(app.data_dir, workspace=target['workspace'])
    try:
        restored = reopened._session(target['id'])
        assert restored['modelAdmission'] == before
        assert before['callId'] == row['id'] and before['providerRequestSent'] is False
        assert reopened.runtime is None
        assert restored['collaborationGeneration']['terminal']
        if terminal_before_restart:
            assert restored['failure'] == failure_before
        else:
            assert (restored.get('failure') or {}).get('category') != 'model_capacity'
        prior_failure = copy.deepcopy(restored.get('failure'))
        prior_generation = copy.deepcopy(restored['collaborationGeneration'])
        prior_status = restored['status']
        await reopened.on_runtime_event(*terminal)
        assert restored['modelAdmission'] == before
        assert restored.get('failure') == prior_failure
        assert restored['collaborationGeneration'] == prior_generation
        assert restored['status'] == prior_status
    finally:
        await reopened.close()


async def test_late_success_does_not_clear_a_newer_denial(app, monkeypatch):
    from amplifier_web import capacity_wait
    from amplifier_web.collaboration import BINDING
    from amplifier_web.model_admission import admit
    target = app.state['sessions'][0]
    active = target['collaborationGeneration']
    row = {'id': 'allowed', 'sessionId': target['id'], 'rootSessionId': target['id'],
           'turnId': active['inputIds'][0]}
    target['modelAdmission'] = {'generationId': active['id'], 'callId': 'old-denial'}
    newer = {'generationId': active['id'], 'callId': 'new-denial'}
    async def delayed_success(*args):
        target['modelAdmission'] = newer
        return {'allowed': True}
    monkeypatch.setattr(capacity_wait, 'admit', delayed_success)
    token = BINDING.set({'_generationId': active['id']})
    try:
        assert (await admit(app, target['id'], {'call': row}))['allowed']
        assert target['modelAdmission'] == newer
    finally:
        BINDING.reset(token)
