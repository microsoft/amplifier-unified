"""Live display demand, independent of presentation retained for reconnect."""

_RUNNING = {'working', 'running', 'starting', 'stopping'}


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


def event_sessions(service):
    """Read live subscriptions over the catalog index for the saved generation."""
    selected = subscribed_sessions(service)
    projections = getattr(service, 'projections', None)
    if projections is None or getattr(service, '_progress_dirty', False):
        # Coalesced runtime updates can precede the save that invalidates the
        # index. Keep observing those live transitions until they are saved.
        return [row['id'] for row in service.state['sessions']
                if row['id'] in selected or row.get('status') in _RUNNING]
    index = projections.sessions(service.state)
    candidates = (selected | index.active) & index.by_id.keys()
    return [identity for identity in sorted(candidates, key=index.positions.__getitem__)
            if identity in selected or index.by_id[identity].get('status') in _RUNNING]
