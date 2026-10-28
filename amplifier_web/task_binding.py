"""Task boundaries for retained observations, separate from exact mutations."""


def continuation_epoch(task):
    # Existing records gain a stable epoch on their next task mutation. Until
    # then their current revision is the conservative migration boundary.
    return (task or {}).get('continuationEpoch', (task or {}).get('revision'))


def matches(task, binding):
    task = task or {}
    if task.get('id') != binding.get('taskId'):
        return False
    if 'taskContinuationEpoch' in binding:
        return continuation_epoch(task) == binding['taskContinuationEpoch']
    return task.get('revision') == binding.get('taskRevision')


def dependency_blocked(task):
    """Only an owner-agent dependency block, never a reason-text inference."""
    if (not isinstance(task, dict) or task.get('status') != 'blocked'
            or type(task.get('revision')) is not int or task['revision'] < 1
            or not isinstance(task.get('id'), str) or not task['id']):
        return False
    proof = task.get('blockProvenance')
    if proof is not None:
        return (isinstance(proof, dict) and type(proof.get('version')) is int
                and proof['version'] == 1 and proof.get('origin') == 'agent'
                and proof.get('dependency') is True
                and type(proof.get('revision')) is int
                and 1 <= proof['revision'] <= task.get('revision', 0))
    # Legacy canonical changes already retain dispatcher origin. Missing or
    # incomplete history is not evidence of a dependency block.
    start = task.get('changesRecordedFromRevision', 1)
    if type(start) is not int or not 1 <= start <= task['revision']:
        return False
    status, allowed, revision = None, False, start - 1
    changes = task.get('changes', [])
    if not isinstance(changes, list):
        return False
    for row in changes:
        if (not isinstance(row, dict) or type(row.get('revision')) is not int
                or row['revision'] != revision + 1 or row['revision'] > task['revision']
                or not isinstance(row.get('fields'), dict)):
            return False
        revision = row['revision']
        fields = row['fields']
        if 'status' in fields:
            next_status = fields['status']
            allowed = (next_status == 'blocked' and row.get('origin') == 'agent'
                       and (status == 'active' or status == 'blocked' and allowed))
            status = next_status
        elif 'blockedReason' in fields and status == 'blocked':
            allowed = allowed and row.get('origin') == 'agent'
    # Old no-op mutations advanced revision without a change row. A same-reason
    # human block can be such a gap: never reconstruct its missing provenance.
    return revision == task['revision'] and status == 'blocked' and allowed
