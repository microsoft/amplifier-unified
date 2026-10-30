"""Let only a move's originating turn reach its existing native boundary."""
from .management import Management
from .model_admission_identity import input_id


def worker_instance(service, sid):
    workers = getattr(service.runtime, 'workers', None)
    worker = workers.get(sid) if isinstance(workers, dict) else None
    process = worker.get('process') if worker else None
    if (not process or process.returncode is not None or not getattr(process, 'pid', None)
            or worker.get('started_at') is None or any(worker.get(key) for key in ('closing', 'yielding', 'yielded'))):
        return None
    return f"{process.pid}:{worker['started_at']}"


def finishing_turn(service, session, call, drain=None, child=None):
    """Read under app.lock; never clear fences, release a worker, or grant input.

    The durable receipt owns the exception. Legacy or uncertain receipts cannot
    borrow a later worker's identity, even when they retain a generation ID.
    All model budgets and the native settlement boundary still apply.
    """
    sid = session['id']
    native = session.get('runtimeSessionId') or sid
    if (call.get('rootSessionId') != native
            or call.get('lifecycle') == 'background'
            or session.get('fileAccessPending') or session.get('configurationPending')
            or session.get('configurationOperationFence')
            or session.get('configurationRefresh', {}).get('phase') in {'pending', 'applying', 'error'}
            or session.get('pendingConfiguration', {}).get('phase') in {'pending', 'queued', 'applying', 'error'}
            or Management.unresolved_configuration(session)):
        return False
    pending = [item for item in session.get('worktreeHandoffs', []) if item.get('phase') in {'pending', 'unknown'}]
    if len(pending) != 1:
        return False
    try:
        owner = service.worktrees
        record = owner.read(sid, pending[0]['id'])
        binding = record.get('finishingTurn') or {}
        active = session.get('collaborationGeneration') or {}
        if (record.get('phase') != 'pending' or record.get('step') not in {'preflight', 'waiting'}
                or record.get('release') or record.get('managerPlacement') or record.get('reconciles')
                or not binding.get('workerInstance')
                or binding['workerInstance'] != worker_instance(service, sid)
                or binding.get('configurationRevision') != session.get('configurationPendingRevision', 0)
                or session.get('executionRevision', 0) != record.get('executionRevision')
                or session.get('workspace') != record.get('historyHome')
                or (session.get('workingDirectory') or session['workspace']) != record.get('source')
                or owner.authority_changed(record)):
            return False
        owner.check_host(record)
        owner.check_binding(record, session)
        if (isinstance(drain, dict) and drain.get('handoffId') == record['id']
                and drain.get('finishingTurn') == binding):
            # The private runtime bridge supplies this from its exact native
            # boundary marker; public tool arguments cannot supply it.
            if drain.get('kind') == 'root':
                return (call.get('sessionId') == native and not active.get('terminal')
                        and drain.get('generationId') == active.get('id')
                        and drain.get('inputIds') == active.get('inputIds')
                        and input_id(call) in active.get('inputIds', []))
        if (isinstance(child, dict) and call.get('sessionId') != native
                and child.get('sessionId') == call.get('sessionId') and child.get('runId')
                and input_id(call) in record.get('sourceInputIds', [])):
            snapshot = binding.get('nativeDrain') or {}
            identity = {key: child.get(key) for key in ('sessionId', 'parentSessionId', 'runId', 'callId')}
            job = child.get('job')
            return (identity in snapshot.get('children', []) or
                    isinstance(job, dict) and job in snapshot.get('jobs', [])
                    and child.get('parentSessionId') == job.get('ownerSessionId')
                    and child.get('callId') == job.get('callId'))
        return (call.get('sessionId') == native and bool(active.get('id')) and not active.get('terminal')
                and binding.get('generationId') == active['id']
                and record.get('sourceGenerationId') == active['id']
                and binding.get('inputIds') == active.get('inputIds')
                and input_id(call) in record.get('sourceInputIds', []))
    except (KeyError, OSError, TypeError, ValueError):
        return False  # Missing/corrupt/changed provenance does not authorize a call.
