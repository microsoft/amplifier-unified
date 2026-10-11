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
    from .canvas_versions import assert_clean
    assert_clean(service, scoped['canvas']['id'])
    remember(scoped, service.db)
    service.state['canvas'] = scoped['canvas']
    service.state['view'].setdefault('canvasDraft', {}).update(library=False, open=False, browser=False)
    return {'status': 'opened', 'id': scoped['canvas']['id']}


def open_attachment(service, args, origin):
    from pathlib import Path
    from .attachments import file_path
    from .workspace_canvas import MAX_TEXT, EXTENSIONS
    from .service import AppError
    session = next((row for row in service.state['sessions'] if row['id'] == args['sessionId']), None)
    if not session or service.state.get('selectedSessionId') != session['id']:
        raise AppError('The chat changed. Open the attachment from its original chat.')
    if not any(file.get('id') == args['id'] for message in session.get('messages', []) for file in message.get('attachments', [])):
        raise AppError('This attachment is not available in this conversation.')
    path, row = file_path(service.data_dir, args['id'])
    if row['mime'] != 'text/plain' or row['size'] > MAX_TEXT:
        raise AppError('This attachment cannot be previewed. Download it to open it.')
    content = path.read_bytes().decode('utf-8')
    kind = EXTENSIONS.get(Path(row['name']).suffix.lower(), 'text')
    if kind not in {'markdown', 'text', 'code', 'json', 'jsonl'}:
        kind = 'text'
    scoped = {**service.state}
    canvas_command(scoped, 'canvas.show', {'kind': kind, 'content': content, 'title': row['name']}, origin)
    from .canvas_versions import assert_clean
    assert_clean(service, scoped['canvas']['id'])
    remember(scoped, service.db)
    service.state['canvas'] = scoped['canvas']
    service.state['view'].setdefault('canvasDraft', {}).update(library=False, open=False, browser=False)
    return {'status': 'opened', 'id': scoped['canvas']['id']}
