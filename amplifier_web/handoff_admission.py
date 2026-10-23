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


def finishing_turn(service, session, call):
    """Read under app.lock; never clear fences, release a worker, or grant input.

    The durable receipt owns the exception. Legacy or uncertain receipts cannot
    borrow a later worker's identity, even when they retain a generation ID.
    All model budgets and the native settlement boundary still apply.
    """
    sid = session['id']
    native = session.get('runtimeSessionId') or sid
    if (call.get('sessionId') != native or call.get('rootSessionId') != native
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
                or not active.get('id') or active.get('terminal')
                or binding.get('generationId') != active['id']
                or record.get('sourceGenerationId') != active['id']
                or binding.get('inputIds') != active.get('inputIds')
                or input_id(call) not in record.get('sourceInputIds', [])
                or binding.get('configurationRevision') != session.get('configurationPendingRevision', 0)
                or session.get('executionRevision', 0) != record.get('executionRevision')
                or session.get('workspace') != record.get('historyHome')
                or (session.get('workingDirectory') or session['workspace']) != record.get('source')
                or owner.authority_changed(record)):
            return False
        owner.check_host(record)
        owner.check_binding(record, session)
        return True
    except (KeyError, OSError, TypeError, ValueError):
        return False  # Missing/corrupt/changed provenance does not authorize a call.
