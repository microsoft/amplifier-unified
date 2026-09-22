"""Resolve agent Canvas presentation without retargeting another client's chat."""
def scope(service, session_id, *, required=True):
    from .managed_chats import is_managed
    session = service._session(session_id) if required else next(
        (row for row in service._state['sessions'] if row['id'] == session_id), None)
    if session is None:
        return session_id, None
    if is_managed(session):
        return session_id, None
    workspace = next((row for row in service._state['workspaces']
                      if row['id'] == session.get('workspaceId')
                      or row['path'] == session.get('workspace')), None)
    return session_id, workspace['id'] if workspace else None


def target(service, session_id, client_id=None, *, required=False, allow_detached=False):
    from .service import AppError
    sid, workspace_id = scope(service, session_id, required=False)

    def matches(record):
        return (record.get('selectedSessionId') == sid
                and record.get('selectedWorkspaceId') == workspace_id)

    candidates = [identity for identity, record in service.clients.records.items()
                  if matches(record)]
    if client_id is not None:
        service.clients.validate(client_id)
        if client_id not in candidates:
            if allow_detached:
                return None, candidates
            raise AppError('Choose a client displaying the calling conversation.', 409)
        return client_id, candidates
    current = service.clients.current.get()
    if current in candidates:
        return current, candidates
    if len(candidates) == 1:
        return candidates[0], candidates
    legacy = not service.clients.records and matches(service._state)
    if required and not legacy:
        if candidates:
            raise AppError('Multiple clients display this conversation. Supply clientId to select its Canvas.',
                           409, code='canvas_client_required')
        raise AppError('Open the calling conversation in a client, then retry Canvas selection with its clientId.',
                       409, code='canvas_client_required')
    return None, candidates


def selection_target(service, session_id, args):
    from .service import AppError
    sid, workspace_id = scope(service, session_id)
    artifact = next((row for row in service._state.get('canvasArtifacts', [])
                     if row['id'] == args.get('id')), None)
    if not artifact or artifact.get('sessionId') != sid or artifact.get('workspaceId') != workspace_id:
        raise AppError('This artifact belongs to another chat or is no longer available.')
    return target(service, session_id, args.get('clientId'), required=True)[0]


def state(service, session_id, client_id=None, *, allow_detached=False):
    """Project caller Canvas state; absent/ambiguous clients have no active view.

    Keep the full artifact catalog and its JSON Pointer indices intact. This is
    presentation scoping, not a replacement for artifact ownership validation.
    """
    identity, candidates = target(service, session_id, client_id, allow_detached=allow_detached)
    detached = client_id is not None and identity is None
    sid, workspace_id = scope(service, session_id, required=False)
    with service.clients.bind(identity):
        snapshot = service.state_context()
    legacy = (not detached and not service.clients.records
              and snapshot.get('selectedSessionId') == sid
              and snapshot.get('selectedWorkspaceId') == workspace_id)
    if identity is None and not legacy:
        snapshot = {**snapshot, 'selectedSessionId': sid, 'selectedWorkspaceId': workspace_id,
                    'canvas': {'open': False, 'placeholder': True, 'events': [],
                               'sessionId': sid, 'workspaceId': workspace_id},
                    'view': {'draft': '', 'canvasFocused': False}, 'canvasTabs': {}, 'deviceCommands': []}
        snapshot.pop('client', None)
        snapshot.pop('canvasWorkspace', None)
    snapshot['canvasContext'] = {'sessionId': sid, 'clientId': identity, 'clientIds': candidates,
                                 'status': 'detached' if detached else 'client' if identity else 'default' if legacy else
                                 'ambiguous' if candidates else 'unattached'}
    return snapshot
