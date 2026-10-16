"""A checkout move waits for native work to settle; it never cancels a tool."""
import copy


async def inspect(worker, request_id, cancel=False):
    async with worker.command_lock:
        marker = getattr(worker, '_handoff_boundary', None)
        if cancel:
            if marker and marker['id'] == request_id:
                marker['unregister']()
                worker._handoff_boundary = None
            return {'cancelled': True}
        if marker and marker['id'] != request_id:
            raise ValueError('Another checkout move owns this runtime boundary.')
        if not worker.session or not worker.runtime:
            return {'ready': worker.parked, 'waitingFor': ['runtime']}
        coordinator = worker.session.coordinator
        if not marker:
            from amplifier_core import HookResult

            async def finish_turn(event, data):
                goal = coordinator.session_state.get('goal')
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
                    except BaseException:
                        coordinator.session_state['goal'] = goal
                        raise
                return HookResult()

            unregister = coordinator.hooks.register('orchestrator:goal_progress', finish_turn,
                name='unified-checkout-boundary', priority=0)
            worker._handoff_boundary = {'id': request_id, 'unregister': unregister,
                'hadGoal': bool(coordinator.session_state.get('goal'))}
        loop = coordinator.get('orchestrator')
        waiting = []
        if worker.runtime.generation: waiting.append('generation')
        if worker.bridges: waiting.append('tool-replies')
        if worker.approvals: waiting.append('approval')
        if worker.operation_ids or worker.operation_controls: waiting.append('operations')
        if worker.runtime.queued_inputs or not worker.runtime.inbox.empty(): waiting.append('inputs')
        if loop and (loop.pending or loop._active_jobs()): waiting.append('children')
        if worker.naming and worker.naming.pending and not worker.naming.pending.done(): waiting.append('naming')
        if worker.remounting or worker.ownership.yielding: waiting.append('ownership')
        # Park is the positive checkpoint + release evidence. An idle UI event
        # alone does not establish that children or the calling tool settled.
        if not worker.parked: waiting.append('checkpoint')
        marker = worker._handoff_boundary
        # A goal may reach its cap or complete while the calling turn settles.
        # A host observation must not re-arm that exhausted task as fresh input.
        has_task = bool(coordinator.session_state.get('task'))
        continuation_allowed = (not has_task and not marker['hadGoal']) or bool(coordinator.session_state.get('goal'))
        return {'ready': not waiting, 'waitingFor': waiting, 'continuationAllowed': continuation_allowed}
