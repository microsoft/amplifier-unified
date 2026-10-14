"""Retire committed idle owners without changing durable directory membership.

The limit applies to disposable working-set rows. Active owners and borrowed
mutable aliases remain resident; uncertainty never discards an outstanding write.
"""
import sys
from itertools import chain
import time

from .cold_display import ColdRecord
from .performance_diagnostics import count

RECENT = 8
BATCH = 4
PROBES = 16
PASS_SECONDS = .015
GRAPH_NODES = 2048
GRAPH_SECONDS = .005


def exclusive(row):
    """Bounded CPython ownership proof, without hydrating any detached fields."""
    if sys.implementation.name != 'cpython':
        return False
    objects, incoming, pending = {}, {}, [row]
    began = time.monotonic()
    steps = 0
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in objects:
            continue
        if len(objects) >= GRAPH_NODES or time.monotonic() - began >= GRAPH_SECONDS:
            return False
        if type(current) not in (dict, list, tuple, set, frozenset, ColdRecord):
            return False
        objects[identity] = current
        children = chain(dict.keys(current), dict.values(current)) if isinstance(current, dict) else iter(current)
        for child in children:
            steps += 1
            if steps > GRAPH_NODES or steps % 64 == 0 and time.monotonic() - began >= GRAPH_SECONDS:
                return False
            if type(child) in (dict, list, tuple, set, frozenset, ColdRecord):
                incoming[id(child)] = incoming.get(id(child), 0) + 1
                pending.append(child)
            elif type(child) not in (str, int, float, bool, type(None)):
                return False
        del children
        if 'child' in locals():
            del child
    del current
    for identity in objects:
        current = objects[identity]
        # Root: owning state map/list, caller row, this argument, object map,
        # current and getrefcount. Descendants: owned graph edges + map/current.
        allowed = 6 if current is row else incoming.get(identity, 0) + 3
        if sys.getrefcount(current) > allowed:
            return False
    return True


def protected(service):
    from .history_demand import subscribed_sessions
    ids = subscribed_sessions(service)
    ids.add(service._state.get('selectedSessionId'))
    ids.add(service._state.get('voice', {}).get('sessionId'))
    ids.update(list(service.cold_display.recent)[-RECENT:])
    runtime = getattr(service, 'runtime', None)
    ids.update(getattr(runtime, 'workers', {}))
    ids.update(getattr(runtime, '_preparations', {}))
    ids.update(getattr(service.warmup, 'pending', {}))
    ids.update(identity for identity, lock in service.history.loads.items() if lock.locked())
    ids.update(getattr(service.management, 'pending_config_tasks', {}))
    return ids


def retire(service):
    if (getattr(service, '_retiring_history', False) or getattr(service, '_progress_dirty', False)
            or service.cold_display.persisting or len(service._state['sessions']) <= RECENT):
        return set()
    protected_ids = protected(service)
    # SessionIndex is the only projection that borrows mutable owner rows.
    # Drop those aliases for the ownership proof without invalidating detached
    # directory/navigation results when every candidate stays resident.
    service.projections.values.pop(('session-index',), None)
    removed = set()
    rows = service._state['sessions']
    position = getattr(service, '_history_retire_cursor', 0) % len(rows)
    examined = 0
    started = time.monotonic()
    service._retiring_history = True
    try:
        while rows and examined < min(PROBES, len(rows) + len(removed)) and len(removed) < BATCH:
            if time.monotonic() - started >= PASS_SECONDS:
                break
            position %= len(rows)
            examined += 1
            row = rows[position]
            identity = row['id']
            busy = (row.get('status') in {'working','running','starting','stopping','ready','queued','pending'}
                or bool(row.get('collaborationGeneration')) and not row['collaborationGeneration'].get('terminal')
                or row.get('configurationBusy') or row.get('configurationPending') or row.get('historyLoading')
                or row.get('pendingConfiguration', {}).get('phase') in {'queued','applying'}
                or row.get('ownership', {}).get('status') in {'blocked','yielding','yielded','yield-failed','taking-over'}
                or row.get('naming', {}).get('status') == 'working'
                or any(item.get('status', 'pending') == 'pending' for item in row.get('approvals', []) + row.get('questions', []))
                or any(item.get('status') in {'working','running','starting','queued','pending','stopping'}
                       or item.get('persistent') for item in row.get('workers', [])))
            if identity in protected_ids or busy or not exclusive(row):
                position += 1
                continue
            control = service._state.get('runtimeControl', {}).get(identity)
            if control is not None and not exclusive(control):
                position += 1
                continue
            # A keyed commit preserves current mutations and both resource root
            # sets. No query, cache eviction or missing owner implies deletion.
            service._save(session_ids={identity}, record_only=True)
            # Saving can issue new projection aliases; validate the ownership
            # proof again after those internal temporary references are gone.
            service.projections.values.pop(('session-index',), None)
            if not exclusive(row) or control is not None and not exclusive(control):
                position += 1
                continue
            rows.pop(position)
            service._state.get('runtimeControl', {}).pop(identity, None)
            service._session_projection_refs.pop(identity, None)
            service._saved_detail_facts.pop(identity, None)
            service.cold_display.recent.pop(identity, None)
            from .session_projection import view_path
            # Unresolved native workspaces have no app-owned view path. Their
            # durable native reference still survives ordinary retirement.
            if row.get('workspace'):
                service._view_cache.pop(str(view_path(service.data_dir, row)), None)
            event = service.event_log_view
            for name in ('projected','read_paths','read_revisions'):
                getattr(event, name, {}).pop(identity, None)
            service.history.loads.pop(identity, None)
            removed.add(identity)
        if removed:
            service.projections.invalidate(state=service._state)
            service._browser_snapshot = None
            service._client_snapshots.clear()
            count(service, 'directory.owners.retired', len(removed))
    finally:
        service._history_retire_cursor = position
        service._retiring_history = False
    return removed
