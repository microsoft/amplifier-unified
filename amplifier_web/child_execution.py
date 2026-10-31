"""Live native child scope; a parent turn boundary is not child cancellation.

This is private worker provenance, not a model-provided permission or a durable
lease. The worker checks its actual child coordinator/run before sending it; the
host binds it to the still-current process and checks current execution controls.
"""
from .collaboration import BINDING, PRINCIPAL
from .task_binding import dependency_blocked


def current(service, source, *, child_id=None):
    binding = BINDING.get() or {}
    origin = binding.get('_childOrigin')
    if not isinstance(origin, dict):
        return None
    native = source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']
    identity = origin.get('runtimeSessionId')
    if (not identity or identity == native or child_id is not None and identity != child_id
            or origin.get('rootSessionId') != native
            or PRINCIPAL.get() != (native if child_id is not None else identity)
            or not isinstance(origin.get('generationId'), str) or not origin['generationId']
            or not isinstance(origin.get('runId'), str) or not origin['runId']):
        return None
    inputs = origin.get('inputIds')
    if (not isinstance(inputs, list) or not inputs
            or any(not isinstance(value, str) or not 1 <= len(value) <= 200 for value in inputs)
            or len(set(inputs)) != len(inputs)
            or binding.get('_generationId') != origin['generationId']):
        return None
    workers = getattr(service.runtime, 'workers', None)
    owner = workers.get(source['id']) if isinstance(workers, dict) else None
    if (not owner or owner.get('runtimeOwnerId') != binding.get('_runtimeOwnerId')
            or not owner.get('runtimeOwnerId') or owner['process'].returncode is not None
            or any(owner.get(key) for key in ('closing', 'yielded', 'yielding'))):
        return None
    if (source.get('status') in {'stopped', 'stopping', 'interrupted', 'error'}
            or source.get('fileAccessPending')
            or any(origin.get(key) != source.get(key, 0) for key in
                   ('interruptionRevision', 'executionRevision', 'configurationPendingRevision'))):
        return None
    if source.get('configurationBusy'):
        from .handoff_admission import finishing_turn
        call = {'rootSessionId': native, 'sessionId': identity, 'admissionInputId': inputs[0]}
        if not finishing_turn(service, source, call, child=binding.get('_nativeChild')):
            return None
    from .management import Management
    from .updates import work_paused
    if (work_paused(service.state) or Management.unresolved_configuration(source)
            or (source.get('failure') or {}).get('category') in {'content_filter', 'computer_capture_stop'}
            or service.assignments.blocked(source)):
        return None
    task = service.coordination.task(source['id'])
    if task and (task.get('status') in {'paused', 'completed'}
                 or task.get('status') == 'blocked' and not dependency_blocked(task)):
        return None
    # This observation is an additional rejection fence. Its asynchronous
    # publication is not the producer: only the authenticated live worker can
    # attest its exact run before the first lifecycle row reaches the host.
    children = source.get('execution', {}).get('nodes', [])
    child = next((row for row in reversed(children) if row.get('kind') == 'worker'
                  and row.get('sessionId') == identity), None)
    if (child and child.get('runId') == origin['runId']
            and child.get('phase') in {'completed', 'cancelled', 'error', 'interrupted'}):
        return None
    return origin


def generation(service, source):
    origin = current(service, source)
    return ({'id': origin['generationId'], 'inputIds': list(origin['inputIds']),
             'interruptionRevision': origin['interruptionRevision'], 'terminal': False}
            if origin else source.get('collaborationGeneration') or {})
