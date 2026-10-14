"""Live display demand, independent of presentation retained for reconnect."""

from .execution import LIVE_PHASES
from .performance_diagnostics import count

_RUNNING = {'working', 'running', 'starting', 'stopping'}
_OBSERVED_WORKERS = LIVE_PHASES | _RUNNING


def subscribed_sessions(service):
    sessions = set()
    for queue, client_id in service.queue_clients.items():
        # Terminal/session streams need not change the client's saved selection.
        identity = service.queue_sessions.get(queue)
        if identity is None:
            view = service._state if client_id is None else service.clients.records.get(client_id, {})
            identity = view.get('selectedSessionId')
        if identity is not None:
            sessions.add(identity)
    return sessions


def _event_ready(service, row):
    # An initial transcript read establishes the message/turn inputs used by
    # canonical work projection. Idle polling before it completes builds an
    # immediately stale graph and competes for the same transcript index.
    # Active work and explicit EventLogView.refresh/read remain independent.
    pending = (row.get('historyLoaded') is False and row.get('historyLoading')
               and row.get('status') not in _RUNNING)
    load = getattr(getattr(service, 'history', None), 'loads', {}).get(row['id'])
    # A cancelled read can leave the presentation flag set. Only a current
    # owner of the existing load lock may postpone this passive projection.
    pending = pending and load is not None and load.locked()
    if pending and not any(worker.get('status', worker.get('phase')) in _OBSERVED_WORKERS
                           for worker in row.get('workers', ())):
        count(service, 'events.projection.deferred_history')
        return False
    return True


def event_sessions(service):
    """Read live subscriptions over the catalog index for the saved generation."""
    selected = subscribed_sessions(service)
    projections = getattr(service, 'projections', None)
    if projections is None or getattr(service, '_progress_dirty', False):
        # Coalesced runtime updates can precede the save that invalidates the
        # index. Keep observing those live transitions until they are saved.
        return [row['id'] for row in service.state['sessions']
                if (row['id'] in selected or row.get('status') in _RUNNING)
                and _event_ready(service, row)]
    index = projections.sessions(service.state)
    candidates = (selected | index.active) & index.by_id.keys()
    return [identity for identity in sorted(candidates, key=index.positions.__getitem__)
            if (identity in selected or index.by_id[identity].get('status') in _RUNNING)
            and _event_ready(service, index.by_id[identity])]
