"""A checkout move waits for native work to settle; it never cancels a tool."""
import copy
from contextlib import AsyncExitStack


async def inspect(worker, request_id, cancel=False, *, update=False, configuration=False, expected=None, arm_only=False):
    continuing = update or configuration
    # Registration/removal is synchronous on the worker loop. In particular a
    # tool requesting its own move may already own command_lock. Never wait for
    # that tool in order to give it the pending receipt it needs to finish.
    if cancel:
        marker = getattr(worker, '_handoff_boundary', None)
        if marker and marker['id'] == request_id:
            marker['unregister']()
            worker._handoff_boundary = None
        return {'cancelled': True}
    if not arm_only and worker.command_lock.locked():
        # Enrolment acknowledges the original marker, not readiness. Update
        # callers subsequently use observeOnly and must not see an acknowledged
        # intent whose hook was never installed.
        armed = await inspect(worker, request_id, update=update, configuration=configuration,
                              expected=expected, arm_only=True)
        if armed['waitingFor'] != ['generation']:
            return armed
        return {'ready': False, 'waitingFor': ['command']}
    async with AsyncExitStack() as stack:
        if not arm_only:
            await stack.enter_async_context(worker.command_lock)
        marker = getattr(worker, '_handoff_boundary', None)
        if marker and marker['id'] != request_id:
            raise ValueError('Another checkout move owns this runtime boundary.')
        if not worker.session or not worker.runtime:
            return {'ready': False, 'waitingFor': ['runtime']}
        coordinator = worker.session.coordinator
        if expected and expected.get('observeOnly'):
            if not marker:
                raise ValueError('The original update boundary is unavailable; it was not recreated.')
        elif expected and not (configuration and marker):
            task = coordinator.session_state.get('task') or {}
            from .task_binding import matches
            if (not matches(task, expected) or update and task.get('revision') != expected['taskRevision']
                    or task.get('status') != 'active'
                    or (worker.runtime.generation or {}).get('id') not in
                       ({None, expected['generationId']} if configuration else {expected['generationId']})):
                raise ValueError('The requesting task or generation changed before its update boundary.')
        if (continuing and worker.runtime.generation and coordinator.session_state.get('goal')
                and not coordinator.get_capability('live.continuation_guard_supported')):
            return {'ready': False, 'waitingFor': ['unsupported-native-boundary']}
        if not marker:
            from amplifier_core import HookResult
            task = coordinator.session_state.get('task')
            requested_task = ({key: task[key] for key in ('id', 'revision', 'continuationEpoch') if key in task}
                              if continuing and task else None)
            requested_generation = ((worker.runtime.generation or {}).get('id') if continuing else None)
            # Native admission retains immutable Input identities in insertion
            # order before emitting/awaiting steering events. Inspect only new
            # admissions, not history or applied input_ids (which also contains
            # already-admitted child observations).
            accepted = getattr(worker.runtime, 'accepted', {})
            admitted_tail = next(reversed(accepted), None)
            superseded = False

            def current_update():
                nonlocal superseded
                task = coordinator.session_state.get('task') or {}
                generation = (worker.runtime.generation or {}).get('id')
                if not superseded:
                    for identity in reversed(accepted):
                        if identity == admitted_tail:
                            break
                        if accepted[identity].kind in {'user', 'steer'}:
                            superseded = True
                            break
                from .task_binding import matches
                same_task = (matches(task, {'taskId': requested_task['id'],
                    'taskRevision': requested_task['revision'],
                    'taskContinuationEpoch': requested_task.get('continuationEpoch', requested_task['revision'])})
                    if configuration and requested_task else
                    not requested_task or all(task.get(key) == value for key, value in requested_task.items()))
                return (not superseded and (generation is None or generation == requested_generation)
                    and (not requested_task or task.get('status') == 'active' and
                         same_task))

            async def finish_turn(event, data):
                goal = coordinator.session_state.get('goal')
                if continuing and (not current_update()
                        or (worker.runtime.generation or {}).get('id') != requested_generation):
                    return HookResult()
                if (data.get('state') == 'continuing' and goal
                        and data.get('session_id', coordinator.session_id) == coordinator.session_id
                        and data.get('condition') == goal.get('condition')
                        and data.get('turn') == goal.get('turns_used')):
                    # Native goal continuation checks object identity after this
                    # awaited hook. Preserve progress while ending this turn at
                    # the same boundary used by a queued peer outcome.
                    coordinator.session_state['goal'] = copy.deepcopy(goal)
                    try:
                        worker.controls.persist(task_only=True)
                        if continuing:
                            from .task_binding import continuation_epoch
                            worker._handoff_boundary['yieldedGoal'] = copy.deepcopy(goal)
                            task = coordinator.session_state.get('task') or {}
                            worker._handoff_boundary['yieldedTaskBinding'] = {
                                'taskId': task.get('id'), 'taskRevision': task.get('revision'),
                                'taskContinuationEpoch': continuation_epoch(task)}
                    except BaseException:
                        coordinator.session_state['goal'] = goal
                        raise
                return HookResult()

            unregister = coordinator.hooks.register('orchestrator:goal_progress', finish_turn,
                name='unified-checkout-boundary', priority=0)
            worker._handoff_boundary = {'id': request_id, 'unregister': unregister, 'update': continuing,
                'hadGoal': bool(coordinator.session_state.get('goal'))}
            if update and (expected or {}).get('saveAndYield'):
                async def request_yield(event, data):
                    marker = worker._handoff_boundary
                    if (not marker or marker['id'] != request_id or marker.get('yieldRequested')
                            or not current_update() or not worker.runtime.generation
                            or worker.runtime.generation.get('id') != requested_generation):
                        return HookResult()
                    marker['yieldRequested'] = True
                    return HookResult(action='inject_context', ephemeral=True, context_injection_role='system',
                        context_injection='A safe app update is waiting. Finish the current operation, '
                            'save your progress and remaining steps, then end this turn. '
                            'Keep the unfinished task active so it can resume after the update.')
                remove_prompt = coordinator.hooks.register('provider:request', request_yield,
                    name='unified-safe-update-yield', priority=6)
                def remove_boundary_hooks():
                    remove_prompt()
                    unregister()
                worker._handoff_boundary['unregister'] = remove_boundary_hooks
            if configuration:
                worker._handoff_boundary.update(configuration=True, intent=copy.deepcopy(expected))
            if continuing:
                worker._handoff_boundary['updateCurrent'] = current_update
        if continuing:
            from .handoff_drain import _freeze
            _freeze(worker)
        marker = worker._handoff_boundary
        if arm_only:
            return {'ready': False, 'waitingFor': ['generation']}
        waiting = worker.waiting_for()
        # Checkout inspection is only a candidate, not a saved-history proof.
        # Its existing cooperative release saves under the exact writer lease.
        # Settings/updates still need their positive checkpoint and fresh recheck.
        if continuing and not waiting and not await worker.checkpoint_idle():
            waiting.append('checkpoint')
        if worker._handoff_boundary is not marker:
            return {'ready': False, 'waitingFor': ['cancelled'], 'cancelled': True}
        if configuration and not marker['updateCurrent']():
            raise ValueError('New input or changed task intent superseded the settings boundary.')
        # A goal may reach its cap or complete while the calling turn settles.
        # A host observation must not re-arm that exhausted task as fresh input.
        has_task = bool(coordinator.session_state.get('task'))
        continuation_allowed = (not has_task and not marker['hadGoal']) or bool(coordinator.session_state.get('goal'))
        result = {'ready': not waiting, 'waitingFor': waiting, 'continuationAllowed': continuation_allowed}
        if continuing and not waiting and marker['updateCurrent']():
            goal = coordinator.session_state.get('goal')
            yielded = marker.get('yieldedGoal')
            task = coordinator.session_state.get('task')
            from .task_binding import matches
            if (yielded and goal and task and task.get('status') == 'active'
                    and yielded.get('condition') == goal.get('condition') == task.get('objective')
                    and matches(task, marker['yieldedTaskBinding'])):
                result.update(
                    yieldedGoal={key: goal.get(key) for key in ('task_id', 'cap', 'turns_used', 'continuations')},
                    task={key: task.get(key) for key in ('id', 'revision', 'status', 'continuationEpoch')})
        return result


def checkpoint_configuration(worker):
    """Save one recovery proof while the native history writer lease is held.

    Called only after the ordinary settled-worker history checkpoint while
    retaining its lease. A lost host reply can then read the same operation.
    """
    marker = getattr(worker, '_handoff_boundary', None) or {}
    if not marker.get('configuration') or not marker['updateCurrent']():
        return
    state = worker.session.coordinator.session_state
    task, goal = state.get('task'), state.get('goal')
    yielded = marker.get('yieldedGoal')
    from .task_binding import matches
    if not (yielded and task and goal and task.get('status') == 'active'
            and matches(task, marker['yieldedTaskBinding'])
            and yielded.get('condition') == goal.get('condition') == task.get('objective')):
        return
    proof = {'operationId': marker['id'], 'intent': marker['intent'],
        'boundary': {'ready': True, 'waitingFor': [], 'continuationAllowed': True,
            'yieldedGoal': {key: goal.get(key) for key in ('task_id', 'cap', 'turns_used', 'continuations')},
            'task': {key: task.get(key) for key in ('id', 'revision', 'status', 'continuationEpoch')}}}
    previous = state.get('configurationCheckpoint')
    state['configurationCheckpoint'] = proof
    try:
        worker.controls.persist(task_only=True)
    except BaseException:
        state['configurationCheckpoint'] = previous
        raise
