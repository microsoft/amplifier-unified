"""Fresh owner requests to finished members, without reopening old work."""
from .task_binding import matches


def owner_assignment(service, source, target):
    assignment = service.assignments.current(source)
    if (not assignment or assignment['status'] != 'active'
            or assignment['ownerSessionId'] != source['id'] or source['id'] == target['id']
            or assignment['members'].get(target['id'], {}).get('status') != 'bound'):
        return None
    current = service.assignments.current(target)
    return assignment if current and current['id'] == assignment['id'] else None


def prepare(service, source, target, task, mode, args, *, creation=False):
    if (mode not in {'queue', 'auto'} or creation or args.get('resume') or args.get('scheduleRepair')
            or task.get('status') != 'completed' or not task.get('id')
            or not owner_assignment(service, source, target)):
        return None
    return {'version': 1, 'taskId': task['id'], 'taskRevision': task['revision']}


def matches_request(service, receipt, task, *, for_reply=False):
    proof = receipt.get('completedTaskFollowup')
    if (not isinstance(proof, dict) or proof.get('version') != 1
            or receipt.get('commandAction') != 'coordination.send'
            or receipt.get('mode') not in {'queue', 'auto'}
            or any(receipt.get(key) for key in
                   ('dependencyRequestId', 'creationOperationId', 'humanResume', 'scheduleRepair'))
            or 'taskContinuationEpoch' in receipt
            or (proof.get('taskId'), proof.get('taskRevision')) !=
               (receipt.get('taskId'), receipt.get('taskRevision'))):
        return False
    source = service._session(receipt['senderSessionId'])
    target = service._session(receipt['target']['sessionId'])
    assignment = owner_assignment(service, source, target)
    if not assignment or assignment['id'] != receipt.get('assignmentId'):
        return False
    if task.get('status') == 'completed' and matches(task, proof):
        return True
    # Reporting may describe native objectives created within this correction.
    # This never admits another input, and does not search or rewrite history.
    previous = task.get('followupOf') or {}
    return bool(for_reply and task.get('status') in {'active', 'completed'}
                and previous.get('taskId') == proof['taskId']
                and previous.get('revision') == proof['taskRevision']
                and previous.get('inputId') == receipt.get('inputId')
                and previous.get('generationId'))
