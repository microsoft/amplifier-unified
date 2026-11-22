"""Recovery follows actual provider, terminal, Stop and membership events."""
import copy
import time
from types import SimpleNamespace

import pytest
from amplifier_foundation.session import SharedSessionStore
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import RuntimeManager, normalize_event
from amplifier_web.service import AppError
from test_assignments import app, call, child, generation, limits, until
from test_peer_resume import paused_member, submit
from test_collaborative_workspaces import settled


async def fresh_generation(app, target, identity, inputs, epoch, *, runtime_owner=None):
    """Emit the epoch captured by this fresh input's admission, not an old worker."""
    if runtime_owner is None:
        await generation(app, target, identity, inputs, interruption_revision=epoch)
    else:
        # This transport fixture has no process pipe. Model its complete native
        # start witness through the real reducers, not by editing execution state.
        observed = time.time()
        await app.on_runtime_event('runtime.generation', {
            'sessionId': target['id'], 'rootSessionId': target['id'],
            'runtimeOwnerId': runtime_owner, 'event': 'generation.started',
            'generation_id': identity, 'initial_input_id': inputs[0],
            'interruptionRevision': epoch, 'observedAt': observed,
        })
        for input_id in inputs:
            await app.on_runtime_event('runtime.status', {
                'sessionId': target['id'], 'status': 'working',
                'event': 'input.delivered', 'inputId': input_id})
        turn, = [row for row in target['execution']['turns'] if row['id'] == inputs[0]]
        assert turn['runtimeOwnerId'] == runtime_owner and turn['generationId'] == identity
        assert turn['startedAt'] == observed
    current = target['collaborationGeneration']
    assert current['id'] == identity and current['inputIds'] == inputs
    assert current['interruptionRevision'] == epoch and not current['terminal']


async def provider_failure(app, target, category, *, terminal_id='failed-generation',
                           terminal_inputs=None, terminal_category='unknown',
                           terminal_type='RuntimeError'):
    """Do not seed the failure identity required by peer recovery."""
    await fresh_generation(app, target, 'failed-generation', ['failed-input'],
                           target.get('interruptionRevision', 0))
    failure = RuntimeError({'authentication': 'Authentication failed',
                            'rate_limit': 'Rate limit reached',
                            'context_limit': 'Maximum context window exceeded',
                            'computer_capture_stop': 'No usable screenshot'}[category])
    if category == 'computer_capture_stop':
        failure.code = 'computer_result_not_image'
        failure.tool_call_id = 'halted-capture'
        failure.result_kind = 'error'

    class Provider:
        def get_info(self):
            return SimpleNamespace(id='fixture', defaults={'model': 'fixture'})

        async def complete(self, request):
            raise failure

    emitted = []
    events = ExecutionEvents(target['id'], emitted.append)
    events.lifecycle({'type': 'generation.started', 'generation_id': 'failed-generation',
                      'initial_input_id': 'failed-input'})
    events.lifecycle({'type': 'input.delivered', 'input_id': 'failed-input'})
    provider = events.instrument_provider(target['id'], Provider())
    with pytest.raises(RuntimeError):
        await provider.complete(SimpleNamespace(model='fixture'))
    for event in emitted:
        await app.on_runtime_event(*normalize_event(event, target['id'], 'failed-input'))
    failed_call, = [event['event'] for event in emitted if event['event']['phase'] == 'error']
    assert failed_call['generationId'] == 'failed-generation'
    assert failed_call['admissionInputId'] == failed_call['turnId'] == 'failed-input'
    # A provider exception is a call observation, not yet a root turn failure.
    # Only the correlated native terminal may promote its classified cause.
    assert not target.get('failure')
    classified = copy.deepcopy(failed_call['failure'])
    assert classified['category'] == category
    assert 'generationId' not in classified
    await app.on_runtime_event(*normalize_event({
        'type': 'generation.failed', 'generation_id': terminal_id,
        'input_ids': ['failed-input'] if terminal_inputs is None else terminal_inputs,
        'error_category': terminal_category, 'error_type': terminal_type,
        'error_stage': 'manager_turn',
    }, target['id']))
    await app.on_runtime_event('runtime.error', {
        'sessionId': target['id'], 'error': 'Manager turn failed; no automatic replay'})
    await app.on_runtime_event('runtime.ended', {'sessionId': target['id']})
    if (terminal_id == 'failed-generation' and terminal_inputs is None
            and terminal_category == 'unknown' and terminal_type == 'RuntimeError'):
        assert target['failure']['inputId'] == failed_call['admissionInputId']
        assert target['failure']['recordedAt'] == failed_call['endedAt']
    return classified


@pytest.mark.parametrize('category', ['authentication', 'rate_limit', 'context_limit'])
async def test_classified_provider_failure_binds_terminal_identity_without_losing_cause(app, category):
    source, target, human = await paused_member(app)
    classified = await provider_failure(app, target, category)
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'accepted')
    recovered = receipt['humanResume']['failedRecovery']['failure']
    assert recovered['generationId'] == 'failed-generation'
    assert recovered['inputIds'] == ['failed-input']
    for key, value in classified.items():
        assert recovered[key] == value
    assert len(app.runtime.inputs) == 1


@pytest.mark.parametrize('terminal_id,terminal_inputs', [
    ('stale-generation', ['failed-input']),
    ('failed-generation', ['unrelated-input']),
    ('failed-generation', []),
])
async def test_classified_failure_never_borrows_unmatched_terminal_identity(app, terminal_id, terminal_inputs):
    source, target, human = await paused_member(app)
    await provider_failure(app, target, 'authentication',
                           terminal_id=terminal_id, terminal_inputs=terminal_inputs)
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    with pytest.raises(AppError):
        await submit(app, source, target, human)
    assert not app.runtime.inputs


@pytest.mark.parametrize('category', ['authentication', 'rate_limit', 'context_limit'])
async def test_provider_cause_does_not_explain_a_different_terminal_error_type(app, category):
    _, target, _ = await paused_member(app)
    classified = await provider_failure(app, target, category, terminal_type='ManagerTurnError')
    assert classified['category'] == category
    assert target['failure']['category'] == 'unknown'
    assert target['failure']['errorType'] == 'ManagerTurnError'
    assert target['failure']['generationId'] == 'failed-generation'
    assert target['failure']['inputIds'] == ['failed-input']
    assert not app.runtime.inputs


async def stop_assignment(app, source, identity, *, exit_owner='matching'):
    owners = {}
    for sid, member in app.assignments.current(source)['members'].items():
        if member['status'] != 'bound':
            continue
        target = app._session(sid)
        owner = owners[sid] = identity + ':owner:' + sid
        await fresh_generation(app, target, identity + ':generation:' + sid,
            [identity + ':input:' + sid], target.get('interruptionRevision', 0),
            runtime_owner=owner)
    result = await app.dispatch('assignment.stop', {
        'sessionId': source['id'],
        'expectedRevision': app.assignments.current(source)['revision'],
    }, command_id=identity)
    await until(lambda: not app.assignments.interrupting)
    for sid in result['interruptions']:
        # Independently modeled process-exit evidence, even when the Stop reply
        # was lost. It closes routing, never rewrites that unknown Stop receipt.
        await app.on_runtime_event('runtime.ended', {
            'sessionId': sid, 'rootSessionId': sid,
            **({'runtimeOwnerId': owners[sid] if exit_owner == 'matching' else exit_owner}
               if exit_owner is not None else {}),
        })
    return app.collaboration.receipt(identity)


async def resume_assignment(app, source, identity):
    return await app.dispatch('assignment.resume', {
        'sessionId': source['id'],
        'expectedRevision': app.assignments.current(source)['revision'],
    }, command_id=identity)


@pytest.mark.parametrize('exit_owner', [None, 'foreign-process'])
async def test_assignment_exit_requires_the_captured_process_owner(app, exit_owner):
    source = app.state['sessions'][0]
    target, _ = await child(app, source)
    inputs = list(app.runtime.inputs)
    receipt = await stop_assignment(app, source, 'unbound-stop', exit_owner=exit_owner)
    before = copy.deepcopy(app.assignments.current(source))
    assert not source['collaborationGeneration']['terminal']
    assert not target['collaborationGeneration']['terminal']
    with pytest.raises(AppError, match='settle'):
        await resume_assignment(app, source, 'unbound-recovery')
    assert app.assignments.current(source) == before
    assert app.collaboration.receipt('unbound-stop') == receipt
    assert app.runtime.inputs == inputs


async def uncertain_child_stop(app):
    source = app.state['sessions'][0]
    target, _ = await child(app, source)
    original = app.runtime.stop

    async def lost_reply(sid, **kwargs):
        await original(sid, **kwargs)
        if sid == target['id']:
            raise RuntimeError('Lost reply after worker stopped')

    app.runtime.stop = lost_reply
    old = await stop_assignment(app, source, 'first-stop')
    assert old['interruptions'][target['id']] == 'unknown'
    app.runtime.stop = original
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    return source, target, old


@pytest.mark.parametrize('recovered', [True, False])
async def test_detached_new_work_only_blocks_unresolved_historical_stop(app, recovered):
    source, target, old = await uncertain_child_stop(app)
    if recovered:
        first = await resume_assignment(app, source, 'first-recovery')
        proof = first['result']['stopRecovery']['members'][0]
        assert proof['sessionId'] == target['id'] and proof['stopRequestIds'] == ['first-stop']
    await app.dispatch('assignment.detach', {
        'sessionId': target['id'],
        'expectedRevision': app.assignments.current(source)['revision'],
    }, command_id='detach-child')
    await fresh_generation(app, target, 'independent-generation', ['independent-input'],
                           target.get('interruptionRevision', 0))
    held = SharedSessionStore(target['workspace'], target['id']).acquire(app='independent-work')
    before = copy.deepcopy(target)
    try:
        second = await stop_assignment(app, source, 'second-stop')
        assert target['id'] not in second['interruptions']
        if recovered:
            result = await resume_assignment(app, source, 'second-recovery')
            assert result['result']['assignment']['status'] == 'active'
            assert not result['result'].get('stopRecovery')
        else:
            with pytest.raises(AppError, match='confirm'):
                await resume_assignment(app, source, 'second-recovery')
        assert app.collaboration.receipt('first-stop') == old
        assert target == before
    finally:
        held.release()


@pytest.mark.parametrize('category', ['content_filter', 'computer_capture_stop'])
async def test_stop_does_not_clear_safety_halt_but_inspection_and_new_human_input_remain_possible(app, category):
    source, target, _ = await paused_member(app)
    if category == 'computer_capture_stop':
        await provider_failure(app, target, category)
    else:
        await fresh_generation(app, target, 'safety-generation', ['halted-input'],
                               target.get('interruptionRevision', 0))
        await app.on_runtime_event(*normalize_event({
            'type': 'generation.failed', 'generation_id': 'safety-generation',
            'input_ids': ['halted-input'], 'error_category': 'content_filter',
            'error_type': 'ContentFilterError',
        }, target['id']))
        await app.on_runtime_event('runtime.error', {'sessionId': target['id'], 'error': 'Manager turn failed'})
    failure = copy.deepcopy(target['failure'])
    await app.dispatch('conversation.stop', {'sessionId': target['id']}, command_id='ordinary-stop')
    await until(lambda: target['status'] == 'stopped')
    human = app._message(source, 'user', 'Reconcile and resume the worker.',
                         inputId='fresh-human', inputOrigin='ui')
    await generation(app, source, 'fresh-manager', ['fresh-human'])
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    with pytest.raises(AppError, match='safety'):
        await submit(app, source, target, human)
    assert target['failure'] == failure
    assert not app.runtime.inputs
    # Inspection is not execution. The human can submit a separate permitted
    # request through normal admission; this is not automatic peer resumption.
    inspected = await app.dispatch('session.inspect', {'id': target['id']})
    assert inspected['accepted']
    await app.dispatch('conversation.send', {
        'sessionId': target['id'], 'text': 'Explain the saved failure without retrying its operation.',
    }, command_id='new-human-request')
    await until(lambda: len(app.runtime.inputs) == 1)
    assert app.runtime.inputs[0][1] == 'new-human-request'
    assert target['failure'] == failure


@pytest.mark.parametrize('local_limit', [20, 100])
async def test_fresh_recovery_rechecks_enabled_local_and_assignment_limits(app, monkeypatch, local_limit):
    from amplifier_web.capacity import admission, saved_policy
    from amplifier_web.execution import ingest
    from test_task_continuity import controls
    source, target, _ = await paused_member(app)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    worker = controls(target['id'])
    worker.capacity.admit = lambda row: admission(app, target['id'], {'call': row})
    try:
        await worker.perform('capacity.set', {
            'commandId': 'local-limit', 'expectedRevision': 0, 'enabled': True, 'maxTotalTokens': local_limit})
        await limits(app, source, maxTotalTokens=20)
        prior = call(target, 'completed-call')
        await worker.capacity.guard(prior)
        ingest(target, {**prior, 'phase': 'completed', 'endedAt': 1,
                        'revision': 2, 'usage': {'totalTokens': 20}})
        with pytest.raises(ValueError, match='limit reached'):
            await worker.capacity.guard(call(target, 'denied-call'))
        before = copy.deepcopy(saved_policy(app, target['id']))
        await limits(app, source, maxTotalTokens=200)
        # Changing the allowance alone does not restart automatic work.
        assert not app.runtime.inputs
        assert app.assignments.usage(app.assignments.current(source))['metrics']['grossTotalTokens']['value'] == 20
        human = app._message(source, 'user', 'Reconcile and resume the worker with its current limits.',
                             inputId='budget-human', inputOrigin='ui')
        await generation(app, source, 'budget-manager', ['budget-human'])
        epoch = target.get('interruptionRevision', 0)
        await submit(app, source, target, human)
        await settled(app, 'resume-member', 'accepted')
        await fresh_generation(app, target, 'recovery-generation', ['resume-member'], epoch)
        assert await worker.tasks.continuation_allowed(resume=True)
        next_call = call(target, 'new-call', turnId='resume-member')
        if local_limit == 20:
            with pytest.raises(ValueError, match='20 of 20'):
                await worker.capacity.guard(next_call)
            assert worker.capacity.last_denial
        else:
            await worker.capacity.guard(next_call)
            assert worker.capacity.last_denial is None
            assert await worker.tasks.continuation_allowed()
            ingest(target, {**next_call, 'phase': 'completed', 'endedAt': 2,
                            'revision': 2, 'usage': {'totalTokens': 80}})
            with pytest.raises(ValueError, match='100 of 100'):
                await worker.capacity.guard(call(target, 'local-exhausted', turnId='resume-member'))
        current = saved_policy(app, target['id'])
        assert current['maxTotalTokens'] == before['maxTotalTokens']
        assert current['revision'] == before['revision']
        assert app.assignments.usage(app.assignments.current(source))['metrics']['grossTotalTokens']['value'] == (20 if local_limit == 20 else 100)
    finally:
        await worker.close()


async def test_prior_recovery_cannot_settle_a_new_unknown_stop(app):
    source, target, _ = await uncertain_child_stop(app)
    await resume_assignment(app, source, 'first-recovery')
    original = app.runtime.stop

    async def lost_reply(sid, **kwargs):
        await original(sid, **kwargs)
        if sid == target['id']:
            raise RuntimeError('A different stop reply was lost')

    app.runtime.stop = lost_reply
    second = await stop_assignment(app, source, 'second-unknown-stop')
    assert second['interruptions'][target['id']] == 'unknown'
    held = SharedSessionStore(target['workspace'], target['id']).acquire(app='new-owner')
    try:
        with pytest.raises(AppError, match='confirm'):
            await resume_assignment(app, source, 'second-recovery')
    finally:
        held.release()
    result = await resume_assignment(app, source, 'second-recovery')
    assert result['result']['stopRecovery']['members'][0]['stopRequestIds'] == ['second-unknown-stop']


async def test_safety_halt_arriving_after_peer_preparation_blocks_admission(app):
    source, target, human = await paused_member(app)
    captured = copy.deepcopy(target['collaborationGeneration'])
    async def delayed(session, args, guard, emit):
        await app.on_runtime_event(*normalize_event({
            'type': 'generation.failed', 'generation_id': captured['id'],
            'input_ids': captured['inputIds'], 'error_category': 'content_filter',
            'error_type': 'ContentFilterError',
        }, target['id']))
        assert target['status'] == 'stopped'
        assert 'safety' in guard()
        assert not app.collaboration.admission(target['id'], args)['admitted']
        return {'accepted': False, 'reason': guard()}
    app.runtime.collaboration_input = delayed
    await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'suppressed')
    assert 'safety' in receipt['admission']['reason']
    assert not app.runtime.inputs


@pytest.mark.parametrize('case', ['human', 'peer'])
async def test_admitted_input_rechecks_current_budget_without_replaying_effects(app, monkeypatch, case):
    from amplifier_web.capacity import admission
    from amplifier_web.execution import ingest
    from test_task_continuity import controls
    source, target, _ = await paused_member(app)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    worker = controls(target['id'])
    worker.capacity.admit = lambda row: admission(app, target['id'], {'call': row})
    try:
        await worker.perform('capacity.set', {
            'commandId': 'local-limit', 'expectedRevision': 0, 'enabled': True, 'maxTotalTokens': 100})
        await limits(app, source, maxTotalTokens=20)
        prior = call(target, 'completed-call')
        await worker.capacity.guard(prior)
        ingest(target, {**prior, 'phase': 'completed', 'endedAt': 1,
                        'revision': 2, 'usage': {'totalTokens': 20}})
        with pytest.raises(ValueError):
            await worker.capacity.guard(call(target, 'denied-call'))
        await limits(app, source, maxTotalTokens=200)
        epoch = target.get('interruptionRevision', 0)
        if case == 'human':
            await app.dispatch('conversation.send', {
                'sessionId': target['id'], 'text': 'Continue the remaining work using current limits.',
            }, command_id='fresh-input')
            await until(lambda: len(app.runtime.inputs) == 1)
        else:
            human = app._message(source, 'user', 'Resume the worker.',
                                 inputId='budget-human', inputOrigin='ui')
            await generation(app, source, 'budget-manager', ['budget-human'])
            await submit(app, source, target, human, identity='fresh-input')
            await settled(app, 'fresh-input', 'accepted')
        await fresh_generation(app, target, 'recovery-generation', ['fresh-input'], epoch)
        # Exercise the production provider wrapper rather than supplying turnId
        # to admission. Native input.delivered is the accounting identity.
        emitted = []
        events = ExecutionEvents(target['id'], emitted.append)
        events.lifecycle({'type': 'input.delivered', 'input_id': 'fresh-input', 'source': 'user'})
        events.admission_guard = worker.capacity.guard

        class Provider:
            def get_info(self):
                return SimpleNamespace(id='fixture', defaults={'model': 'fixture'})

            async def complete(self, request):
                return SimpleNamespace(usage={'total_tokens': 7})

        provider = events.instrument_provider(target['id'], Provider())
        await provider.complete(SimpleNamespace(model='fixture'))
        assert emitted and all(event['event']['turnId'] == 'fresh-input' for event in emitted)
        for event in emitted:
            await app.on_runtime_event(*normalize_event(event, target['id'], 'fresh-input'))
        assert worker.capacity.last_denial is None
        assert app.assignments.usage(app.assignments.current(source))['metrics']['grossTotalTokens']['value'] == 27
    finally:
        await worker.close()


@pytest.mark.parametrize('terminal_category', ['content_filter', 'authentication'])
async def test_explicit_terminal_cause_replaces_earlier_provider_classification(app, terminal_category):
    source, target, human = await paused_member(app)
    await provider_failure(app, target, 'rate_limit', terminal_category=terminal_category)
    assert target['failure']['category'] == terminal_category
    assert target['failure']['generationId'] == 'failed-generation'
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    if terminal_category == 'content_filter':
        with pytest.raises(AppError, match='safety'):
            await submit(app, source, target, human)
        assert not app.runtime.inputs
    else:
        await submit(app, source, target, human)
        receipt = await settled(app, 'resume-member', 'accepted')
        assert receipt['humanResume']['failedRecovery']['failure']['category'] == terminal_category


async def test_explicit_terminal_safety_after_preparation_cannot_borrow_earlier_rate_limit(app):
    source, target, human = await paused_member(app)
    await provider_failure(app, target, 'rate_limit')
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    prior_failure = copy.deepcopy(target['failure'])

    async def delayed(session, args, guard, emit):
        await app.on_runtime_event(*normalize_event({
            'type': 'generation.failed', 'generation_id': 'failed-generation',
            'input_ids': ['failed-input'], 'error_category': 'content_filter',
            'error_type': 'ContentFilterError', 'error_stage': 'provider_request',
        }, target['id']))
        # A later terminal cannot rewrite the first outcome, but its retained
        # same-generation safety evidence still fences pending peer admission.
        assert target['failure'] == prior_failure
        safety = target['generations'][-1]
        assert safety['generation_id'] == prior_failure['generationId']
        assert safety['input_ids'] == prior_failure['inputIds']
        assert safety['error_category'] == 'content_filter'
        assert 'safety' in guard()
        assert not app.collaboration.admission(target['id'], args)['admitted']
        return {'accepted': False, 'reason': guard()}

    app.runtime.collaboration_input = delayed
    await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'suppressed')
    assert 'safety' in receipt['admission']['reason']
    assert not app.runtime.inputs


async def test_explicit_non_safety_terminal_retains_prior_capture_safety_halt(app):
    source, target, human = await paused_member(app)
    await provider_failure(app, target, 'computer_capture_stop', terminal_category='authentication')
    assert target['failure']['category'] == 'computer_capture_stop'
    assert target['failure']['terminalFailure']['category'] == 'authentication'
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    with pytest.raises(AppError, match='safety'):
        await submit(app, source, target, human)
    assert not app.runtime.inputs
