"""Commissioning authority for placement, backed by the existing handoff owner."""
from .task_binding import continuation_epoch

READS = {'worktree.inspect', 'worktree.list', 'worktree.status'}
WRITES = {'worktree.create', 'worktree.attach', 'worktree.handoff'}


def task_binding(app, session):
    task = session.get('task') or app.coordination.task(session['id'])
    return {'id': task.get('id'), 'epoch': continuation_epoch(task)}


def member(app, source, target):
    from .service import AppError
    assignment = app.assignments.current(source)
    metadata = target.get('collaboration') or {}
    request_id = metadata.get('requestId')
    try:
        request = app.collaboration.receipt(request_id) if request_id else {}
    except AppError as exc:
        raise ValueError('The commissioned creation receipt is unavailable.') from exc
    membership = (assignment or {}).get('members', {}).get(target['id'], {})
    if (not assignment or assignment['status'] != 'active'
            or membership.get('status') != 'bound'
            or membership.get('role') != 'commissioned'
            or membership.get('creatorSessionId') != source['id']
            or membership.get('creationOperationId') != request_id
            or (app.assignments.current(target) or {}).get('id') != assignment['id']
            or source['workspace'] != target['workspace']
            or request.get('commandAction') != 'coordination.create'
            or not request.get('accepted')
            or request.get('creationVersion') != 1 or request.get('protocol') != 2
            or request.get('senderSessionId') != source['id']
            or request.get('sessionId') != target['id']
            or request.get('workspace') != source['workspace']
            or request.get('result') != metadata
            or metadata.get('creatorSessionId') != source['id']):
        raise ValueError('Worktree actions require a directly commissioned member with its matching creation receipt and active assignment.')
    return assignment


def authorize(app, action, sid, caller):
    if action not in READS | WRITES:
        raise ValueError('Manager placement does not authorize another chat’s cleanup or uncertain-handoff reconciliation.')
    source = app.collaboration.source({}, 'agent', caller)
    assignment = app.assignments.current(source)
    # Refuse before resolving/hydrating an unrelated conversation.
    if (not assignment or assignment['status'] != 'active'
            or assignment['members'].get(sid, {}).get('status') != 'bound'
            or assignment['members'].get(sid, {}).get('creatorSessionId') != source['id']):
        raise ValueError('Worktree actions must target the calling task or a directly commissioned member of its active assignment.')
    target = app._session(sid)
    member(app, source, target)
    return source, target


def prepare(app, action, target, source):
    assignment = member(app, source, target)
    if action in READS:
        return None
    lineage = app.collaboration.lineage(source, 'agent')
    active = target.get('collaborationGeneration') or {}
    if (source.get('status') in {'stopped', 'stopping', 'error', 'interrupted'}
            or target.get('status') != 'idle'
            or active.get('id') and not active.get('terminal')
            or target['id'] in getattr(app.runtime, '_preparations', {})
            or any(target.get(key) for key in ('configurationBusy', 'configurationPending', 'fileAccessPending'))):
        raise ValueError('Place only an idle commissioned member after its current work and settings have settled.')
    return {
        'sourceSessionId': source['id'], 'assignmentId': assignment['id'],
        'assignmentRevision': assignment['revision'],
        'sourceHumanMessageId': app.worktrees.latest_human(source),
        'sourceTask': task_binding(app, source),
        'sourceGenerationInputIds': list((source.get('collaborationGeneration') or {}).get('inputIds', [])),
        **lineage,
    }


def guard(app, record, *, continuation=False):
    scope = record.get('managerPlacement')
    if not scope:
        return None
    source = app._session(scope['sourceSessionId'])
    target = app._session(record['sessionId'])
    try:
        assignment = member(app, source, target)
    except ValueError as exc:
        return str(exc)
    if assignment['id'] != scope['assignmentId']:
        return 'The commissioning assignment changed during placement.'
    # The authenticated generation authorizes admission. Other retained results
    # may start later manager turns while this already accepted move settles.
    # Only changed user/task/Stop authority or membership cancels that work.
    if (source.get('interruptionRevision', 0) != scope['sourceInterruptionRevision']
            or app.worktrees.latest_human(source) != scope['sourceHumanMessageId']
            or task_binding(app, source) != scope['sourceTask']
            or source.get('status') in {'stopped', 'stopping', 'error', 'interrupted'}):
        return 'The manager’s instruction changed during placement.'
    # Native handoff deliberately retires the old worker without a user Stop.
    # The handoff owner's captured interruption revision still rejects a real
    # Stop, including one racing after positive writer-release evidence.
    if continuation:
        return None  # A result observation must also report failed/uncertain release.
    released = record.get('release', {}).get('quiesced')
    target_active = target.get('collaborationGeneration') or {}
    if not released and (target.get('status') != 'idle'
            or target_active.get('id') and not target_active.get('terminal')
            or target['id'] in getattr(app.runtime, '_preparations', {})):
        return 'The member started work or preparation during placement.'
    released_stop = target.get('status') == 'stopped' and released
    if target.get('status') in {'stopped', 'stopping', 'error', 'interrupted'} and not released_stop:
        return 'The member stopped or failed during placement.'
    return None
