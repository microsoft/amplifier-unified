"""Explicit file navigation from a chat; no message submission or tool replay."""
import copy
import uuid
from .workspace_canvas import canvas_command, ReferencedFileChanged
from .canvas_library import remember
from .peer_scope import participant_matches, valid_binding


def open_file(service, args, origin, *, retained_file=None):
    from .errors import AppError
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
    current_view = service.state['view']
    if args.get('showConversation') and (
            current_view.get('workSurface') != 'workspace'
            or current_view.get('workWorkspaceId') != workspace_id
            or current_view.get('workWorkspaceTab') != 'files'):
        return {'status': 'unavailable', 'message': 'The file browser changed. Open the file from its workspace Files page.'}
    scoped = {**service.state, 'selectedSessionId': sid, 'selectedWorkspaceId': workspace_id}
    file_root = None
    retained = None
    if args.get('referenceRequestId'):
        receipt = service.collaboration.receipt(args['referenceRequestId'])
        if not valid_binding(receipt):
            raise AppError('The exact artifact request location binding is unavailable.', 403)
        # The legacy same-workspace path fallback is not permission to read a
        # moved producer's old directory. Recheck both before any file access.
        service.collaboration.request_target(
            service._session(receipt['senderSessionId'], hydrate=False), receipt, hydrate=False)
        response = receipt.get('response') or {}
        reference = args['path'] + '@sha256:' + args.get('expectedSha256', '')
        if args.get('referenceResponseRevision') is not None:
            from .result_artifacts import resolve
            _, _, response = resolve(service.collaboration, sid, args['referenceRequestId'], reference, args['referenceResponseRevision'])
        if (not participant_matches(receipt, session) or (response.get('status') != 'sealed' and 'artifacts' not in response)
                or reference not in response.get('references', [])):
            raise AppError('Choose an exact artifact reference from this workspace request.', 403)
        if 'artifacts' not in response and receipt['target'].get('workspace', receipt.get('workspace')) != receipt.get('workspace'):
            return {'status': 'unavailable', 'message': 'Cross-workspace references require an explicitly retained file version; no source path was read.'}
        if 'artifacts' in response and retained is None:
            from .result_artifacts import resolve
            item, _, current = resolve(service.collaboration, sid, args['referenceRequestId'], reference, args.get('referenceResponseRevision'))
            if retained_file is None or current != retained_file[2]:
                return {'status': 'unavailable', 'message': 'The result changed while opening it. Inspect and retry.'}
            saved_item, data, _ = retained_file
            if data is None:
                return {'status': saved_item['status'], 'message': saved_item.get('detail', 'The retained version is unavailable.')}
            if item != saved_item:
                return {'status': 'unavailable', 'message': 'The result changed while opening it. Inspect and retry.'}
            retained = (item, data)
        file_root = response.get('executionDirectory')
        if not file_root and retained is None:
            return {'status': 'unavailable', 'message': 'This older result has no recorded execution folder. Its exact source cannot be established.'}
    try:
        if retained is not None:
            from .result_artifacts import preview
            preview(scoped, *retained, origin)
        else:
            canvas_command(scoped, 'canvas.show', {'kind': 'auto', 'path': args['path'],
                'expectedSha256': args.get('expectedSha256')}, origin, file_root=file_root)
    except ReferencedFileChanged as exc:
        return {'status': 'changed', 'message': str(exc)}
    except (AppError, OSError, ValueError):
        return {'status': 'unavailable', 'message': 'This file is unavailable or cannot be previewed in this workspace.'}
    if args.get('expectedSha256'):
        # Retain the checked bytes as a snapshot. A peer artifact preview must
        # not become a file editor pointed at a different task's working copy.
        scoped['canvas']['artifactReference'] = {'sha256': args['expectedSha256'],
            'requestId': args.get('referenceRequestId'), 'responseRevision': args.get('referenceResponseRevision'), 'path': args['path']}
        if retained:
            scoped['canvas']['artifactReference']['outputId'] = retained[0]['outputId']
        scoped['canvas'].pop('path', None)
        scoped['canvas'].pop('workspacePath', None)
        scoped['canvas']['id'] = uuid.uuid4().hex
    from .canvas_versions import assert_clean
    assert_clean(service, scoped['canvas']['id'])
    scoped['canvasArtifacts'] = [copy.deepcopy(row) if row['id'] == scoped['canvas']['id'] else row
                                 for row in scoped.get('canvasArtifacts', [])]
    if 'canvasTabs' in scoped:
        scoped['canvasTabs'] = copy.deepcopy(scoped['canvasTabs'])
    remember(scoped, service.db)
    service.canvas_views.choose(scoped['canvas'], scoped['canvas'].get('revision', 1),
                                'canvas', origin=origin)
    service.state['canvasArtifacts'] = scoped['canvasArtifacts']
    if 'canvasTabs' in scoped:
        service.state['canvasTabs'] = scoped['canvasTabs']
    service.state['canvas'] = scoped['canvas']
    service.state['view'].setdefault('canvasDraft', {}).update(library=False, open=False, browser=False)
    if args.get('showConversation'):
        # One guarded command owns both effects. A delayed receipt must never
        # dispatch another navigation after the user has chosen a newer page.
        current_view['workSurface'] = 'chat'
    return {'status': 'opened', 'id': scoped['canvas']['id']}


def open_attachment(service, args, origin):
    from pathlib import Path
    from .attachments import file_path
    from .workspace_canvas import MAX_TEXT, EXTENSIONS
    from .errors import AppError
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
