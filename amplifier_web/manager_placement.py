"""Commissioning authority for placement, backed by the existing handoff owner."""
from .task_binding import continuation_epoch

READS = {'worktree.inspect', 'worktree.list', 'worktree.status'}
WRITES = {'worktree.create', 'worktree.attach', 'worktree.handoff'}


def task_binding(app, session):
    task = session.get('task') or app.coordination.task(session['id'])
    return {'id': task.get('id'), 'epoch': continuation_epoch(task)}


def member(app, source, target):
    assignment = app.assignments.current(source)
    if (not assignment or assignment['status'] != 'active'
            or assignment['ownerSessionId'] != source['id']
            or assignment['members'].get(target['id'], {}).get('status') != 'bound'
            or (app.assignments.current(target) or {}).get('id') != assignment['id']
            or source['workspace'] != target['workspace']):
        raise ValueError('Worktree actions must target the calling task or a current member of its owned active assignment.')
    return assignment


def authorize(app, action, sid, caller):
    if action not in READS | WRITES:
        raise ValueError('Manager placement does not authorize another chat’s cleanup or uncertain-handoff reconciliation.')
    source = app.collaboration.source({}, 'agent', caller)
    assignment = app.assignments.current(source)
    # Refuse before resolving/hydrating an unrelated conversation.
    if (not assignment or assignment['status'] != 'active'
            or assignment['ownerSessionId'] != source['id']
            or assignment['members'].get(sid, {}).get('status') != 'bound'):
        raise ValueError('Worktree actions must target the calling task or a current member of its owned active assignment.')
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
            or any(target.get(key) for key in ('configurationBusy', 'configurationPending', 'fileAccessPending'))):
        raise ValueError('Place only an idle commissioned member after its current work and settings have settled.')
    return {
        'sourceSessionId': source['id'], 'assignmentId': assignment['id'],
        'assignmentRevision': assignment['revision'],
        'sourceHumanMessageId': app.worktrees.latest_human(source),
        'sourceTask': task_binding(app, source),
        **lineage,
    }


def guard(app, record):
    scope = record.get('managerPlacement')
    if not scope:
        return None
    source = app._session(scope['sourceSessionId'])
    target = app._session(record['sessionId'])
    try:
        assignment = member(app, source, target)
    except ValueError as exc:
        return str(exc)
    if assignment['id'] != scope['assignmentId'] or assignment['revision'] != scope['assignmentRevision']:
        return 'The commissioning assignment changed during placement.'
    if (source.get('interruptionRevision', 0) != scope['sourceInterruptionRevision']
            or app.worktrees.latest_human(source) != scope['sourceHumanMessageId']
            or task_binding(app, source) != scope['sourceTask']
            or source.get('status') in {'stopped', 'stopping', 'error', 'interrupted'}):
        return 'The manager’s instruction changed during placement.'
    # Native handoff deliberately retires the old worker without a user Stop.
    # The handoff owner's captured interruption revision still rejects a real
    # Stop, including one racing after positive writer-release evidence.
    released_stop = target.get('status') == 'stopped' and record.get('release', {}).get('quiesced')
    if target.get('status') in {'stopped', 'stopping', 'error', 'interrupted'} and not released_stop:
        return 'The member stopped or failed during placement.'
    return None
