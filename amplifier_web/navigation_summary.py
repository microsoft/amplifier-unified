"""Small navigation facts; never load transcripts or acknowledge attention."""
from collections import Counter
from functools import lru_cache
import re


def task_blocked(state, sid):
    task = state.get('runtimeControl', {}).get(sid, {}).get('task.get', {}).get('task') or {}
    return task.get('status') == 'blocked'


def activity(session, unread=False, *, error_reviewed=False, blocked=False):
    if any(row.get('status') in {None, 'pending'} for row in session.get('approvals', [])):
        return {'kind': 'attention', 'label': 'Approval requested'}
    if any(row.get('status') == 'pending' and row.get('required')
           for row in session.get('questions', [])):
        return {'kind': 'attention', 'label': 'Answer requested'}
    if any((row.get('delivery') or {}).get('status') in {'unknown', 'rejected'}
           for row in session.get('questions', [])):
        return {'kind': 'attention', 'label': 'Check answer delivery'}
    if blocked or session.get('status') == 'blocked':
        return {'kind': 'attention', 'label': 'Work blocked'}
    status = session.get('status', 'idle')
    if status in {'starting', 'working', 'running', 'stopping'}:
        return {'kind': 'working', 'label': {'starting': 'Starting', 'stopping': 'Stopping'}.get(status, 'Working')}
    if (session.get('error') or status in {'error', 'failed'}) and not error_reviewed:
        return {'kind': 'attention', 'label': 'Needs attention'}
    if unread:
        return {'kind': 'unread', 'label': 'New response'}
    return {'kind': 'idle', 'label': 'Idle'}


def path_labels(paths):
    """Shortest unique suffixes, computed against the full available registry."""
    # Registry paths, unlike activity, rarely change between stream updates.
    # Return a copy so callers cannot mutate the shared cached labels.
    return dict(_path_labels(frozenset(paths)))


@lru_cache(maxsize=4)
def _path_labels(paths):
    parts = {path: tuple(part for part in re.split(r'[\\/]+', path) if part) for path in set(paths)}
    counts = Counter(suffix for chunks in parts.values() for size in range(1, len(chunks) + 1)
                     for suffix in [chunks[-size:]])
    labels = {}
    for path, chunks in parts.items():
        for size in range(1, len(chunks) + 1):
            if counts[chunks[-size:]] == 1:
                labels[path] = '/'.join(chunks[-size:])
                break
        else:
            labels[path] = path
    # A root workspace can itself be a suffix of another registration. Keep
    # the absolute label in that case, and never claim two paths are identical.
    duplicates = Counter(labels.values())
    return {path: path if duplicates[label] > 1 else label for path, label in labels.items()}
