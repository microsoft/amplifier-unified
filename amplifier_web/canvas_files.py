"""Explicit file navigation from a chat; no message submission or tool replay."""
from .workspace_canvas import canvas_command
from .canvas_library import remember


def open_file(service, args, origin):
    from .service import AppError
    sid = args['sessionId']
    session = next((row for row in service.state['sessions'] if row['id'] == sid), None)
    if not session or service.state.get('selectedSessionId') != sid:
        return {'status': 'unavailable', 'message': 'The chat changed. Open the file from its original chat.'}
    if session.get('workspace') != args['workspace']:
        return {'status': 'unavailable', 'message': 'The workspace changed. Open the file from its original workspace.'}
    from .agent_canvas import scope
    workspace_id = scope(service, sid)[1]
    if service.state.get('selectedWorkspaceId') != workspace_id:
        return {'status': 'unavailable', 'message': 'The workspace changed. Try again in the original chat.'}
    scoped = {**service.state, 'selectedSessionId': sid, 'selectedWorkspaceId': workspace_id}
    try:
        canvas_command(scoped, 'canvas.show', {'kind': 'auto', 'path': args['path']}, origin)
    except (AppError, OSError, ValueError):
        return {'status': 'unavailable', 'message': 'This file is unavailable or cannot be previewed in this workspace.'}
    remember(scoped, service.db)
    service.state['canvas'] = scoped['canvas']
    service.state['view'].setdefault('canvasDraft', {}).update(library=False, open=False, browser=False)
    return {'status': 'opened', 'id': scoped['canvas']['id']}
