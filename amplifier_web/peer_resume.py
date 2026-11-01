"""One fresh peer input backed by the commissioning owner's human request."""
import copy

from amplifier_operations.coordination import fingerprint
from .schedule_handoff import latest_human


def latest_root_generation(target):
    return next((event for event in reversed(target.get('generations', []))
                 if event.get('sessionId', target['id']) == target['id']
                 and event.get('rootSessionId', target['id']) == target['id']), None)


def safety_halt(target):
    active_id = (target.get('collaborationGeneration') or {}).get('id')
    # A later detach of the same root does not erase its earlier safety halt.
    # This retained lifecycle list is bounded; do not inspect transcript bodies.
    retained_safety = active_id and any(
        event.get('sessionId', target['id']) == target['id']
        and event.get('rootSessionId', target['id']) == target['id']
        and event.get('generation_id') == active_id
        and event.get('event') in {'generation.failed', 'generation.detached'}
        and event.get('error_category') in {'content_filter', 'computer_capture_stop'}
        for event in target.get('generations', []))
    if retained_safety or (target.get('failure') or {}).get('category') in {'content_filter', 'computer_capture_stop'}:
        return ('A safety-related failure is still saved. Inspect it and resolve the halted operation; '
                'Stop does not clear it. A separate permitted human request can use normal chat input.')


def retained_terminal_evidence(target):
    """Witness the old terminal state, not delivery or success of its inputs.

    A fresh human request supplies authority. Native service observations need
    not have peer receipts; neither missing nor unknown receipts are repaired.
    """
    from .session_health import current_terminal_failure
    failure = target.get('failure') or {}
    active = target.get('collaborationGeneration') or {}
    terminal = latest_root_generation(target) or {}
    if (failure.get('generationId') or failure.get('category') != 'unknown'
            or not active.get('inputIds') or not current_terminal_failure(target)):
        return None
    return {'generation': copy.deepcopy(terminal)}


def prepare(app, source, target, value, lineage, boundary):
    human = latest_human(source)
    if (not human or human['id'] != value['sourceMessageId']
            or human.get('questionId') or human.get('scheduledRunId')
            or human.get('inputId') not in lineage.get('sourceInputIds', [])):
        raise ValueError('Resume requires the latest actual human request in the owner’s current admitted input.')
    assignment = app.assignments.current(source)
    if (not assignment or assignment['status'] != 'active'
            or assignment['ownerSessionId'] != source['id'] or source['id'] == target['id']
            or assignment['members'].get(target['id'], {}).get('status') != 'bound'
            or (app.assignments.current(target) or {}).get('id') != assignment['id']):
        raise ValueError('Resume only a current commissioned member of your active assignment.')
    if (value['expectedInterruptionRevision'] != target.get('interruptionRevision', 0)
            or human.get('createdAt', 0) <= (target.get('lastInterruption') or {}).get('at', 0)):
        raise ValueError('Stop changed or occurred after the human request; use a fresh human resume instruction.')
    if target.get('status') in {'interrupted', 'stopping'}:
        raise ValueError('Wait for the recipient to settle before requesting resume.')
    if reason := safety_halt(target):
        raise ValueError(reason)
    recovery = None
    if target.get('status') == 'error':
        failure = target.get('failure') or {}
        active = target.get('collaborationGeneration') or {}
        evidence = retained_terminal_evidence(target)
        prove = getattr(app.runtime, 'stopped_boundary', None)
        if (not evidence and (not failure.get('generationId') or failure['generationId'] != active.get('id'))
                or not active.get('terminal') or not callable(prove)
                or target.get('configurationBusy')
                or any(w.get('status') in {'queued', 'starting', 'running', 'working', 'stopping'}
                       for w in target.get('workers', []))):
            raise ValueError('The failed recipient has not reached a recoverable stopped boundary.')
        try:
            proof = boundary.enter_context(prove(target))
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(f'Could not confirm that the failed recipient has stopped. {exc}') from None
        recovery = {**proof, 'failure': copy.deepcopy(failure),
                    **({'terminalEvidence': evidence} if evidence else {})}
    key = fingerprint([source['id'], human['id'], target['id'], target.get('interruptionRevision', 0)])
    previous = app.db.execute("""SELECT id FROM commands
        WHERE json_extract(receipt,'$.humanResume.authorizationKey')=?""", (key,)).fetchone()
    if previous:
        raise ValueError(f'This human resume request already has receipt {previous[0]}; inspect it instead of sending again.')
    return {
        'sourceMessageId': human['id'], 'sourceInputId': human['inputId'],
        'textDigest': fingerprint(human.get('text', '')),
        'assignmentId': assignment['id'], 'assignmentRevision': assignment['revision'],
        'targetHumanMessageId': (latest_human(target) or {}).get('id'),
        'authorizationKey': key,
        **({'failedRecovery': recovery} if recovery else {}),
    }


def guard(app, receipt, source, target):
    scope = receipt['humanResume']
    human = latest_human(source)
    recovered = scope.get('failedRecovery')
    if reason := safety_halt(target):
        return reason
    if target.get('status') == 'error' and (not recovered or recovered['failure'] != target.get('failure')):
        return 'The recipient failure changed before recovery.'
    if recovered and recovered.get('terminalEvidence') and (
            recovered['failure'] != target.get('failure')
            or retained_terminal_evidence(target) != {
                'generation': recovered['terminalEvidence'].get('generation')}):
        # Older saved receipts may also retain peer input snapshots. Keep them
        # as history; the terminal witness is what this fresh admission checks.
        return 'The retained terminal generation changed before recovery.'
    assignment = app.assignments.current(source)
    if (not human or human['id'] != scope['sourceMessageId']
            or human.get('inputId') != scope['sourceInputId']
            or human.get('questionId') or human.get('scheduledRunId')
            or fingerprint(human.get('text', '')) != scope['textDigest']
            or human.get('createdAt', 0) <= (target.get('lastInterruption') or {}).get('at', 0)
            or (latest_human(target) or {}).get('id') != scope['targetHumanMessageId']):
        return 'The human resume instruction or recipient input changed.'
    if (not assignment or assignment['id'] != scope['assignmentId']
            or assignment['revision'] != scope['assignmentRevision']
            or assignment['status'] != 'active' or assignment['ownerSessionId'] != source['id']
            or assignment['members'].get(target['id'], {}).get('status') != 'bound'
            or (app.assignments.current(target) or {}).get('id') != assignment['id']):
        return 'The commissioned assignment changed before resume.'


def pending(receipt):
    # An admitted resume is not a reusable exemption for later replies/waits.
    return bool(receipt.get('humanResume') and receipt.get('delivery') in {'queued', 'submitting'})
