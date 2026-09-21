"""Live display demand, independent of presentation retained for reconnect."""


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
