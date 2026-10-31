"""Contention uses actual provider instrumentation, not synthetic usage rows."""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import normalize_event
from test_assignments import app, child, limits, until
from test_task_continuity import controls


@pytest.fixture
async def providers(app, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    resources = []
    emissions = set()

    def make(session, *, child_id=None):
        control = controls(session['id'])
        captured = session['collaborationGeneration']['id']
        async def admitted(row):
            return await app.app_bridge('capacity.admit', {
                'call': row, '_generationId': captured, '_runtimeSessionId': session['id'], 'waitForCapacity': True}, session['id'])
        control.capacity.admit = admitted
        def publish(event):
            task = asyncio.create_task(app.on_runtime_event(*normalize_event(event, session['id'])))
            emissions.add(task)
            task.add_done_callback(emissions.discard)
        telemetry = ExecutionEvents(session['id'], publish)
        telemetry.lifecycle({'type': 'input.delivered',
                             'input_id': session['collaborationGeneration']['inputIds'][-1]})
        telemetry.admission_guard = control.capacity.guard
        release = asyncio.Event()
        called = []
        class Provider:
            def get_info(self):
                return SimpleNamespace(id='fixture', defaults={'model': 'fixture'})
            async def complete(self, request):
                called.append(request.model)
                await release.wait()
                return SimpleNamespace(usage={'input_tokens': 8, 'output_tokens': 2})
        provider = telemetry.instrument_provider(child_id or session['id'], Provider())
        running = []
        def start(label='fixture'):
            task = asyncio.create_task(provider.complete(SimpleNamespace(model=label)))
            running.append(task)
            return task
        item = SimpleNamespace(control=control, release=release, called=called, start=start,
                               running=running, telemetry=telemetry)
        resources.append(item)
        return item
    yield make
    for item in resources:
        for task in item.running:
            if not task.done(): task.cancel()
        await asyncio.gather(*item.running, return_exceptions=True)
        await item.control.close()
    if emissions:
        await asyncio.gather(*emissions)


async def test_provider_contention_waits_then_invokes_once_after_release(app, providers):
    source = app.state['sessions'][0]
    target, _ = await child(app, source)
    await limits(app, source, maxConcurrentCalls=1)
    first, second = providers(source), providers(target)
    a = first.start()
    await until(lambda: first.called or a.done())
    if a.done(): await a
    b = second.start()
    await asyncio.sleep(.1)
    assert not b.done(), b.exception() if not b.cancelled() else 'cancelled'
    assert not second.called and second.control.capacity.last_denial is None
    assert target['activity']['phase'] == 'capacity'
    assert app.assignments.usage(app.assignments.current(source))['calls'] == 1
    first.release.set()
    await a
    await until(lambda: second.called)
    second.release.set()
    await b
    assert first.called == ['fixture'] and second.called == ['fixture']
    assert second.control.capacity.last_denial is None
    assert app.assignments.usage(app.assignments.current(source))['calls'] == 2


async def test_uncapped_assignment_runs_more_than_eight_helpers_and_provider_calls(app, providers):
    source = app.state['sessions'][0]
    # The installation's separate per-source creation policy remains authoritative.
    policy = (await app.dispatch('testing.execution.read', {}))['result']
    await app.dispatch('testing.execution.save', {
        'expectedRevision': policy['revision'], 'queue': {}, 'profiles': {},
        'concurrency': {'maxTaskChatsPerSource': 12}}, command_id='fixture-source-cap')
    sessions = [source]
    for index in range(10):
        helper, _ = await child(app, source, f'helper-{index}')
        sessions.append(helper)
    observed = [providers(session) for session in sessions]
    calls = [item.start(f'call-{index}') for index, item in enumerate(observed)]
    await until(lambda: all(item.called for item in observed) or any(task.done() for task in calls))
    assert all(item.called == [f'call-{index}'] for index, item in enumerate(observed))
    assert all(not task.done() for task in calls)  # All eleven overlap at the provider boundary.
    snapshot = app.assignments.snapshot(source)
    assert snapshot['inFlightCalls'] == 11
    assert len(snapshot['assignment']['members']) == 11
    assert not app._model_admission_waiters
    for item in observed:
        item.release.set()
    await asyncio.gather(*calls)
    await until(lambda: app.assignments.usage(app.assignments.current(source))['metrics']['grossTotalTokens']['value'] == 110)
    assert all(item.control.capacity.last_denial is None for item in observed)


async def waiting_pair(app, providers, **policy):
    source = app.state['sessions'][0]
    target, _ = await child(app, source)
    await limits(app, source, maxConcurrentCalls=1, **policy)
    first, second = providers(source), providers(target)
    a = first.start()
    await until(lambda: first.called)
    b = second.start()
    await until(lambda: target.get('activity', {}).get('phase') == 'capacity' or b.done())
    assert not b.done(), b.exception() if not b.cancelled() else 'cancelled'
    return source, target, first, second, a, b


async def test_waiter_fifo_does_not_hold_slots_and_other_assignments_still_run(app, providers):
    source, target, first, second, a, b = await waiting_pair(app, providers)
    third, _ = await child(app, source, 'third')
    last = providers(third)
    c = last.start('third')
    await until(lambda: third.get('activity', {}).get('phase') == 'capacity')
    independent = next(row for row in app.state['sessions'] if row['title'] == 'UI')
    from test_assignments import generation
    await generation(app, independent, 'independent', ['independent-input'])
    other = providers(independent)
    d = other.start('independent')
    await until(lambda: other.called)
    first.release.set()
    await a
    await until(lambda: second.called)
    assert not last.called
    second.release.set()
    await b
    await until(lambda: last.called)
    last.release.set(); other.release.set()
    await asyncio.gather(c, d)
    assert app._model_admission_waiters == []


@pytest.mark.parametrize('change', ['stop', 'generation', 'input', 'configuration', 'disconnect'])
async def test_waiting_call_never_invokes_after_authority_changes(app, providers, change):
    source, target, first, second, a, b = await waiting_pair(app, providers)
    from test_assignments import generation
    if change == 'stop':
        await app.dispatch('conversation.stop', {'sessionId': target['id']}, command_id='new-stop')
    elif change == 'generation':
        await generation(app, target, 'new-generation', ['new-input'])
    elif change == 'input':
        await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'working',
                                                     'event': 'input.delivered', 'inputId': 'new-input'})
    elif change == 'configuration':
        target['configurationPendingRevision'] = target.get('configurationPendingRevision', 0) + 1
        app._publish_changes(sessions={target['id']})
    else:
        b.cancel()  # Native bridge teardown cancels its host waiter.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(b, 2)
    first.release.set()
    await a
    assert not second.called and second.control.capacity.last_denial is None
    assert app._model_admission_waiters == []
    assert app.assignments.usage(app.assignments.current(source))['calls'] == 1


async def test_current_cumulative_policy_still_blocks_when_slot_releases(app, providers):
    source, target, first, second, a, b = await waiting_pair(app, providers)
    await limits(app, source, maxTotalTokens=10)
    first.release.set()
    await a
    with pytest.raises(ValueError, match='limit reached'):
        await b
    assert not second.called
    assert second.control.capacity.last_denial
    assert app.assignments.usage(app.assignments.current(source))['metrics']['grossTotalTokens']['value'] == 10


async def test_held_reservations_wait_but_real_spend_is_not_erased(app, providers):
    source, target, first, second, a, b = await waiting_pair(
        app, providers, maxTotalTokens=100, reserveTokens=60)
    await limits(app, source, maxConcurrentCalls=2)
    await asyncio.sleep(.05)
    assert not b.done() and not second.called
    first.release.set()
    await a
    await until(lambda: second.called)
    second.release.set()
    await b
    await until(lambda: app.assignments.usage(app.assignments.current(source))['metrics']['grossTotalTokens']['value'] == 20)
    assert second.control.capacity.last_denial is None


async def test_wait_expiry_is_not_a_persisted_budget_pause(app, providers, monkeypatch):
    await app.dispatch('modelCapacity.set', {'expectedRevision': 0, 'waitTimeoutSeconds': 1,
                                           'maxWaitingCalls': 256}, command_id='short-wait')
    source, target, first, second, a, b = await waiting_pair(app, providers)
    with pytest.raises(TimeoutError, match='No provider request was sent'):
        await b
    assert not second.called and second.control.capacity.last_denial is None
    assert app._model_admission_waiters == []
    assert await second.control.tasks.continuation_allowed()
    first.release.set()
    await a
    await until(lambda: app.assignments.usage(app.assignments.current(source))['metrics']['grossTotalTokens']['value'] == 10)
    second.release.set()
    await second.start('fresh-call')
    assert second.called == ['fresh-call']


async def test_unknown_inflight_receipt_cannot_be_freed_by_waiting(app, providers):
    source, target, first, second, a, b = await waiting_pair(app, providers)
    from amplifier_web.capacity import restore_observation
    restore_observation(source)  # Production host-restart reconciliation of the actual admitted call.
    app._publish_changes(sessions={source['id']})
    with pytest.raises(ValueError, match='concurrent model-call limit'):
        await b
    assert not second.called
    assert app.assignments.usage(app.assignments.current(source))['receipts'][0]['phase'] == 'outcome_unknown'
    assert app._model_admission_waiters == []


async def mixed_capacity(app, providers):
    """An old unknown call keeps its slot while current calls can settle."""
    source = app.state['sessions'][0]
    live, _ = await child(app, source, 'live')
    waiting, _ = await child(app, source, 'waiting')
    await limits(app, source, maxConcurrentCalls=2)
    unknown_provider, live_provider = providers(source), providers(live)
    unknown_provider.start('old-call')
    await until(lambda: unknown_provider.called)
    from amplifier_web.capacity import restore_observation
    restore_observation(source)
    app._publish_changes(sessions={source['id']})
    assignment = app.assignments.current(source)
    unknown = copy.deepcopy(app.assignments.usage(assignment)['receipts'][0])
    live_task = live_provider.start('live-call')
    await until(lambda: live_provider.called)
    queued = providers(waiting)
    queued_task = queued.start('queued-call')
    await until(lambda: waiting.get('activity', {}).get('phase') == 'capacity' or queued_task.done())
    assert not queued_task.done(), queued_task.exception() if not queued_task.cancelled() else 'cancelled'
    return source, waiting, assignment, unknown, live_provider, live_task, queued, queued_task


async def test_mixed_unknown_and_live_capacity_waits_fifo_without_releasing_unknown(app, providers):
    source, waiting, assignment, unknown, live, live_task, first, first_task = await mixed_capacity(app, providers)
    last_session, _ = await child(app, source, 'last')
    assignment = app.assignments.current(source)
    last = providers(last_session)
    last_task = last.start('last-call')
    await until(lambda: last_session.get('activity', {}).get('phase') == 'capacity' or last_task.done())
    assert not last_task.done()
    assert not first.called and not last.called
    assert first.control.capacity.last_denial is None and last.control.capacity.last_denial is None
    assert app.assignments.usage(assignment)['calls'] == 2

    live.release.set()
    await live_task
    await until(lambda: first.called)
    assert first.called == ['queued-call'] and not last.called
    first.release.set()
    await first_task
    await until(lambda: last.called)
    last.release.set()
    await last_task

    receipts = app.assignments.usage(assignment)['receipts']
    assert next(row for row in receipts if row['id'] == unknown['id']) == unknown
    assert len(receipts) == 4 and last.called == ['last-call']
    assert first.control.capacity.last_denial is None and last.control.capacity.last_denial is None
    assert not app._model_admission_waiters


async def test_removing_count_ceiling_wakes_waiter_without_erasing_unknown_receipt(app, providers):
    source, waiting, assignment, unknown, live, live_task, queued, task = await mixed_capacity(app, providers)
    await limits(app, source, maxOpenTasks=None, maxConcurrentCalls=None)
    await until(lambda: queued.called)
    assert queued.called == ['queued-call'] and not live_task.done()
    assert not queued.control.capacity.last_denial
    receipts = app.assignments.usage(app.assignments.current(source))['receipts']
    assert len(receipts) == 3
    assert next(row for row in receipts if row['id'] == unknown['id']) == unknown
    live.release.set(); queued.release.set()
    await asyncio.gather(live_task, task)
    assert not app._model_admission_waiters


@pytest.mark.parametrize('change', ['stop', 'generation', 'budget'])
async def test_mixed_capacity_wait_rechecks_authority_and_budget(app, providers, change):
    source, waiting, assignment, unknown, live, live_task, queued, task = await mixed_capacity(app, providers)
    if change == 'stop':
        await app.dispatch('conversation.stop', {'sessionId': waiting['id']}, command_id='stop-mixed-wait')
    elif change == 'generation':
        from test_assignments import generation
        await generation(app, waiting, 'replacement-generation', ['replacement-input'])
    else:
        # Unknown token consumption still blocks an enabled cumulative budget.
        await limits(app, source, maxTotalTokens=100)
    live.release.set()
    await live_task
    with pytest.raises(ValueError if change == 'budget' else asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert not queued.called
    assert bool(queued.control.capacity.last_denial) == (change == 'budget')
    receipts = app.assignments.usage(assignment)['receipts']
    assert len(receipts) == 2
    assert next(row for row in receipts if row['id'] == unknown['id']) == unknown
    assert not app._model_admission_waiters


@pytest.mark.parametrize('ceiling', [1, 2])
async def test_unknown_reservations_alone_at_or_above_ceiling_still_pause(app, providers, ceiling):
    source = app.state['sessions'][0]
    other, _ = await child(app, source, 'other')
    waiting, _ = await child(app, source, 'waiting')
    await limits(app, source, maxConcurrentCalls=2)
    from amplifier_web.capacity import restore_observation
    for session in (source, other):
        observed = providers(session)
        observed.start()
        await until(lambda: observed.called)
        restore_observation(session)
        app._publish_changes(sessions={session['id']})
    await limits(app, source, maxConcurrentCalls=ceiling)
    assignment = app.assignments.current(source)
    before = copy.deepcopy(app.assignments.usage(assignment)['receipts'])
    refused = providers(waiting)
    with pytest.raises(ValueError, match='concurrent model-call limit'):
        await refused.start()
    assert not refused.called and refused.control.capacity.last_denial
    assert app.assignments.usage(assignment)['receipts'] == before
    assert not app._model_admission_waiters


async def test_full_wait_queue_does_not_block_free_unrelated_assignment(app, providers):
    from test_assignments import generation, agent_action
    await app.dispatch('modelCapacity.set', {'expectedRevision': 0, 'waitTimeoutSeconds': 0,
                                           'maxWaitingCalls': 1}, command_id='one-waiter')
    source, target, first, second, a, b = await waiting_pair(app, providers)
    independent = next(row for row in app.state['sessions'] if row['title'] == 'UI')
    await generation(app, independent, 'free-generation', ['free-input'])
    await agent_action(app, independent, 'assignment.start', {'sessionId': independent['id']}, 'free-assignment')
    free = providers(independent)
    free.release.set()
    await free.start('free')
    assert free.called == ['free']
    with pytest.raises(TimeoutError, match='already waiting'):
        await second.start('excess')
    assert not second.called and second.control.capacity.last_denial is None
    assert len(app._model_admission_waiters) == 1
    first.release.set(); second.release.set()
    await asyncio.gather(a, b)


async def test_global_policy_is_user_owned_cas_idempotent_and_persistent(app):
    from amplifier_web.service import AppError, AppService
    before = (await app.dispatch('modelCapacity.read', {}))['result']
    assert before == {'revision': 0, 'maxWaitingCalls': 0, 'waitTimeoutSeconds': 0}
    args = {'expectedRevision': 0, 'maxWaitingCalls': 500, 'waitTimeoutSeconds': 900}
    with pytest.raises(AppError, match='Only the user'):
        await app.dispatch('modelCapacity.set', args, origin='agent', caller_session_id=app.state['sessions'][0]['id'])
    result = await app.dispatch('modelCapacity.set', args, command_id='policy-save')
    assert result['result']['revision'] == 1
    assert (await app.dispatch('modelCapacity.set', args, command_id='policy-save'))['duplicate']
    with pytest.raises(AppError, match='settings changed'):
        await app.dispatch('modelCapacity.set', args, command_id='old-policy')
    with pytest.raises(AppError, match='different contents'):
        await app.dispatch('modelCapacity.set', {**args, 'maxWaitingCalls': 10}, command_id='policy-save')
    workspace = app.state['sessions'][0]['workspace']
    await app.close()
    other = AppService(app.data_dir, workspace=workspace)
    try:
        assert (await other.dispatch('modelCapacity.read', {}))['result'] == result['result']
    finally:
        await other.close()


async def test_live_policy_timeout_edit_wakes_an_existing_unlimited_wait(app, providers):
    source, target, first, second, a, b = await waiting_pair(app, providers)
    await app.dispatch('modelCapacity.set', {'expectedRevision': 0, 'maxWaitingCalls': 256,
                                           'waitTimeoutSeconds': 1}, command_id='bounded-now')
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(b, 2)
    assert second.control.capacity.last_denial is None and not second.called


async def test_wait_deadline_is_rechecked_after_policy_wins_contended_admission_lock(app, providers):
    await app.dispatch('modelCapacity.set', {'expectedRevision': 0, 'waitTimeoutSeconds': 1,
                                           'maxWaitingCalls': 256}, command_id='finite-lock-wait')
    source, target, first, second, a, b = await waiting_pair(app, providers)
    async with app.lock:
        changed = asyncio.create_task(limits(app, source, maxConcurrentCalls=2))
        await asyncio.sleep(0)  # Policy update joins the lock queue before the waiter.
        app.coordination.notify()
        await asyncio.sleep(1.1)  # Deadline passes while host admission cannot hold the lock.
    await changed
    try:
        with pytest.raises(TimeoutError, match='No provider request was sent'):
            await asyncio.wait_for(asyncio.shield(b), .5)
        assert not second.called and second.control.capacity.last_denial is None
        assert app._model_admission_waiters == []
        assert app.assignments.usage(app.assignments.current(source))['calls'] == 1
        second.release.set()
        await second.start('new-call-after-expiry')
        assert second.called == ['new-call-after-expiry']
        assert app.assignments.usage(app.assignments.current(source))['calls'] == 2
    finally:
        first.release.set(); second.release.set()
        await asyncio.gather(a, b, return_exceptions=True)


async def test_default_waiting_queue_crosses_old_count_ceiling_and_continues_once(app, providers):
    # Older waiting tickets from other assignments must not reject a new wait.
    retained = [{'sid': f'other-{i}', 'id': f'call-{i}', 'assignment': f'assignment-{i}',
                 'visible': False} for i in range(256)]
    app._model_admission_waiters = list(retained)
    source, target, first, second, a, b = await waiting_pair(app, providers)
    assert len(app._model_admission_waiters) == 257
    assert not second.called and not b.done()
    first.release.set()
    await a
    await until(lambda: second.called)
    second.release.set()
    await b
    assert second.called == ['fixture']
    assert app._model_admission_waiters == retained


async def test_saved_finite_wait_limit_can_be_cleared_globally(app):
    first = await app.dispatch('modelCapacity.set', {
        'expectedRevision': 0, 'maxWaitingCalls': 256, 'waitTimeoutSeconds': 60}, command_id='finite')
    cleared = await app.dispatch('modelCapacity.set', {
        'expectedRevision': first['result']['revision'], 'maxWaitingCalls': 0,
        'waitTimeoutSeconds': 0}, command_id='unlimited')
    assert cleared['result'] == {'revision': 2, 'maxWaitingCalls': 0, 'waitTimeoutSeconds': 0}
    assert (await app.dispatch('modelCapacity.read', {}))['result'] == cleared['result']
