"""Worker-side schedule admission, under the existing command lock."""
import copy


def finish(controls, event):
    marker = getattr(controls, '_scheduled_monitor', None)
    if not marker: return
    kind = event.get('type')
    if kind in {'input.delivered', 'steering.applied'}:
        identity = event.get('input_id')
        if identity != marker['inputId'] and event.get('source', 'user') == 'user':
            marker['manualInputs'].add(identity)
    if kind in {'generation.started', 'generation.finished', 'generation.failed', 'generation.detached', 'assistant.message'}:
        identities = event.get('input_ids') or [event.get('initial_input_id')]
        event['scheduled_monitor_input_id'] = marker['inputId']
        event['scheduled_monitor_only'] = not any(identity in marker['manualInputs'] for identity in identities)
    # Delegated reports can start follow-up generations after the first reply.
    # Hold the finite guard until the whole loop reaches its idle boundary.
    if kind not in {'session.idle', 'generation.failed', 'generation.detached'}: return
    controls._scheduled_monitor = None
    controls.coordinator.register_capability('live.continuation_guard', marker['guard'])
    task = controls.tasks.record()
    if task and task['status'] == 'active':
        if (task['id'], task['revision']) == (marker['taskId'], marker['taskRevision']):
            controls.coordinator.session_state['goal'] = marker['goal']
        else:
            # A user correction made during the monitor owns the next goal.
            task['appliedRevision'] = None
            controls.tasks.apply(persist=False)
    else:
        controls.coordinator.session_state['goal'] = None
    controls.persist(task_only=True)


async def admit(controls, runtime, args, activation=None):
    task = controls.tasks.record()
    if not task or task['id'] != args.get('taskId') or task['revision'] != args.get('taskRevision') or task['status'] != 'active':
        return {'accepted': False, 'reason': 'The saved task changed or is not active.'}
    if not controls.coordinator.get_capability('live.continuation_guard_supported'):
        return {'accepted': False, 'reason': 'This runtime lacks saved task continuation support.'}
    try:
        controls.require_idle()
    except ValueError as exc:
        return {'accepted': False, 'reason': str(exc)}
    from amplifier_module_loop_live.runtime import Input
    if args.get('kind') == 'monitor':
        goal = copy.deepcopy(controls.coordinator.session_state.get('goal'))
        if goal: task['goalState'] = copy.deepcopy(goal)
        marker = {'inputId': args['inputId'], 'taskId': task['id'], 'taskRevision': task['revision'], 'goal': goal, 'guard': controls.coordinator.get_capability('live.continuation_guard'), 'manualInputs': set()}
        controls._scheduled_monitor = marker
        async def finite_monitor(): return False
        controls.coordinator.register_capability('live.continuation_guard', finite_monitor)
        controls.coordinator.session_state['goal'] = None
        try:
            controls.persist(task_only=True)
        except BaseException:
            controls._scheduled_monitor = None
            controls.coordinator.register_capability('live.continuation_guard', marker['guard'])
            controls.coordinator.session_state['goal'] = goal
            raise
    # Runtime.submit enqueues without yielding, then reports admission. The
    # command lock never waits for model execution or tool permissions.
    try:
        identity = await runtime.submit(Input('user', args['text'], id=args['inputId'], activation=activation))
    except BaseException:
        if args['inputId'] not in runtime.accepted:
            finish(controls, {'type': 'generation.failed', 'input_ids': [args['inputId']]})
        raise
    return {'accepted': True, 'inputId': identity}
