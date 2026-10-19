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
