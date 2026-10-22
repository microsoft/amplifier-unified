"""Model readback of private composer state, separate from client persistence.

Project known presentation fields only. Arbitrary saved artifacts, messages and
user JSON may legitimately contain keys named ``draft`` and remain unchanged.
"""


def session(row):
    from .cold_display import detached
    # Keep dormant display fields lazy. Iterating ColdRecord.items() would load
    # every saved body even for an unrelated scalar read such as /revision.
    result = detached(row)
    for key in ('draft', 'draftDocument', 'draftAttachments'):
        result.pop(key, None)
    return result


def project(state, session_id, args):
    context = state.get('canvasContext', {})
    client_id = context.get('clientId')
    # Only these exact pointers opt into draft inspection. Naming a browser on
    # an overview, parent pointer or unrelated action never opts into it.
    path = args.get('path')
    requested = {'/view/draft': 'draft', '/view/messageEdit': 'messageEdit'}.get(path) if isinstance(path, str) else None
    inspect = (requested and isinstance(args.get('clientId'), str)
               and args['clientId'] == client_id
               and context.get('sessionId') == session_id
               and state.get('selectedSessionId') == session_id)
    edit = state.get('view', {}).get('messageEdit')
    if requested == 'messageEdit' and isinstance(edit, dict) and edit.get('sessionId') != session_id:
        inspect = False
    if requested and not inspect:
        raise ValueError('Draft inspection requires an explicit clientId displaying the calling conversation. Use it only for user-requested draft work.')
    result = {key: value for key, value in state.items() if key != 'draftAttachments'}
    result['view'] = {key: value for key, value in state.get('view', {}).items()
                      if key not in ('draft', 'draftDocument', 'newChatText', 'messageEdit') or key == requested and inspect}
    rows = state.get('sessions', [])
    result['sessions'] = ({key: session(row) for key, row in rows.items()} if isinstance(rows, dict)
                          else [session(row) for row in rows])
    # Saved observations belong to the browser AND the conversation it was
    # displaying when it reported. Selection may have changed since then.
    device = state.get('devices', {}).get(client_id)
    result['devices'] = ({client_id: device} if client_id and isinstance(device, dict)
                         and device.get('sessionId') == session_id else {})
    return result
