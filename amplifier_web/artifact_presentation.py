"""One host operation over existing immutable output and Canvas body owners.

Publication is durable; opening is a one-time, independently guarded client
selection. Command reconciliation belongs to AppService's existing ledger.
"""
import base64
import copy
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import time
import uuid

INPUT_DETACHED = ContextVar('artifact_presentation_detached_input', default=False)


def presentation_schema(schema):
    return schema({'chat': {'enum': ['none', 'compact', 'inline']},
                   'canvas': {'enum': ['unchanged', 'open']}})


def definitions(schema, string):
    spec = schema({'sessionId': string(200), 'id': string(100),
                   'outputId': string(100), 'version': {'type': 'integer', 'minimum': 1},
                   'messageId': string(200), 'clientId': string(100),
                   'presentation': presentation_schema(schema)},
                  ['sessionId', 'version', 'presentation'])
    spec['oneOf'] = [{'required': ['id'], 'not': {'required': ['outputId']}},
                     {'required': ['outputId'], 'not': {'required': ['id']}}]
    return {'canvas.present': (
        'Publish an exact owned Canvas version or adapt an exact OutputRegistry version. '
        'chat none/compact/inline is independent of one-time canvas unchanged/open. '
        'A new compact/inline publication needs explicit messageId. Opening requires '
        'one connected capable client and may return a partial dirty/no-client result. '
        'Retry an uncertain command with the same ID and identical arguments; opened '
        'means selection accepted, not rendered pixels.', spec)}


def output_binding(record):
    return {'outputId': record['id'], 'outputVersion': record['version'],
            'sessionId': record['sessionId'], 'sha256': record.get('sha256')}


def check_output(binding, record, sid):
    if (not isinstance(binding, dict) or record.get('sessionId') != sid
            or binding != output_binding(record) or type(binding.get('outputVersion')) is not int):
        raise ValueError('The artifact must reference its exact owning output version.')


def validate_definition(entry, row, record, body, source_body=None):
    """Bind format and saved body as well as the output ID/version tuple."""
    kind = entry.get('kind', row.get('kind'))
    if record.get('bodyFormat') == 'canvas-image-v1':
        if kind != 'image' or entry.get('body') != record.get('body'):
            raise ValueError('Generated image definition differs from its original body.')
    elif record.get('kind') == 'media':
        from .output_media import validate_media_reference
        if not isinstance(body, dict) or set(body) != {'mediaReference'}:
            raise ValueError('Retained media definition has no exact media reference.')
        validate_media_reference(body['mediaReference'], record, row['sessionId'], kind=kind)
    elif record.get('kind') == 'canvas':
        if kind != record.get('canvasKind') or body != source_body:
            raise ValueError('Canvas output definition differs from its admitted source.')
    elif (kind not in {'markdown', 'text', 'code', 'json', 'jsonl', 'image', 'pdf', 'output'}
          or body != {'outputReference': output_binding(record)}):
        raise ValueError('Output reference is not an admitted inert native format.')


def output_bytes(db, record):
    """Read existing saved bytes, never a workspace filename or remote URL."""
    from .state_storage import resource
    value = resource(db, record['body']['$resource'])
    if record.get('bodyFormat') == 'canvas-image-v1':
        prefix = 'data:image/png;base64,'
        content = value.get('content')
        if not isinstance(content, str) or not content.startswith(prefix):
            raise ValueError('The saved image body is unavailable.')
        data = base64.b64decode(content[len(prefix):], validate=True)
    else:
        data = (base64.b64decode(value['data'], validate=True)
                if value['encoding'] == 'base64' else value['data'].encode())
    if hashlib.sha256(data).hexdigest() != record.get('sha256') or len(data) != record.get('bytes'):
        raise ValueError('The saved output no longer matches its exact evidence.')
    return data


def resolve_body(db, body, kind, sid):
    """Expand a small typed reference at an explicit body/view read boundary."""
    if 'outputReference' not in body:
        return body
    from amplifier_outputs import OutputRegistry
    binding = body['outputReference']
    if set(body) != {'outputReference'}:
        raise ValueError('Ambiguous saved output reference.')
    record = OutputRegistry(db).read(binding['outputId'])
    check_output(binding, record, sid)
    data = output_bytes(db, record)
    if kind == 'image':
        from .workspace_canvas import _image
        return {'content': _image(data)}
    if kind == 'pdf':
        from .workspace_canvas import _pdf
        return {'content': _pdf(data)}
    if kind in {'markdown', 'text', 'code', 'json', 'jsonl'}:
        return {'content': data.decode('utf-8')}
    # Unsupported attachments remain an inert, downloadable reference.
    return {'outputReference': copy.deepcopy(binding)}


def generation_binding(record, row, version):
    """Only the canonical image-output producer can project a gallery marker."""
    from .canvas_versions import definition
    saved = definition(row, version)
    receipt = record.get('imageGeneration')
    if (record.get('bodyFormat') != 'canvas-image-v1' or not isinstance(receipt, dict)
            or receipt.get('provenance') != 'producer-reported; local artifact bytes verified'
            or not isinstance(receipt.get('receiptSha256'), str)
            or len(receipt['receiptSha256']) != 64
            or receipt.get('operation') not in {'generate', 'edit'}
            or not isinstance(receipt.get('requestId'), str) or not receipt['requestId']
            or row.get('imageRequestId') != receipt['requestId']
            or record.get('canvasId') != row['id']
            or record.get('sessionId') != row.get('sessionId')
            or saved.get('kind') != 'image' or saved.get('body') != record.get('body')
            or saved.get('outputBinding') != output_binding(record)):
        return None
    return {'verified': True, 'requestId': receipt['requestId'],
            'operation': receipt['operation'], 'parentId': record.get('parentId')}


def admission(service, sid, mid):
    """New anchors need an explicit existing host message, never equal text."""
    from .service import AppError
    if not isinstance(mid, str) or not mid:
        raise AppError('Supply an explicit messageId for a new chat publication.', 409)
    session = service._session(sid)
    message = next((m for m in session.get('messages', []) if m.get('id') == mid), None)
    if (not message or message.get('role') not in {'user', 'assistant'}
            or message.get('observation') or message.get('observation_id')):
        raise AppError('Choose an existing original user or assistant message; no turn was inferred.', 409)


def writable(service, sid, origin):
    from .service import AppError
    session = service._session(sid, hydrate=False)
    if (session.get('sessionKind', 'root') != 'root' or session.get('historyReadOnlyReason')
            or session.get('ownership', {}).get('status') in
            {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}):
        raise AppError('Artifact publication requires a writable root conversation.', 409)
    if origin == 'agent':
        service.collaboration.active_binding(session)
    try:
        service.portability.write_context(sid)
    except ValueError as exc:
        raise AppError(str(exc), 409) from None
    return session


def adapter(service, session, record):
    """Map each exact output tuple once, without resaving its content bytes."""
    from .canvas_versions import definition, save
    from .resource_files import put
    from .state_storage import resource
    binding = output_binding(record)
    mapped = record.get('presentationArtifact')
    identity = mapped.get('id') if isinstance(mapped, dict) else record.get('canvasId') if record.get('bodyFormat') == 'canvas-image-v1' else None
    row = next((r for r in service._state.get('canvasArtifacts', [])
                if r['id'] == identity and r.get('sessionId') == session['id']), None) if identity else None
    if row is not None:
        for entry in row.get('versions', []) or [row]:
            if entry.get('outputBinding') == binding:
                version = entry.get('version', row.get('revision', 1))
                return row, version
    if mapped:
        raise ValueError('The exact output adapter mapping is unavailable; it was not recreated.')
    kind, body, url = 'output', None, None
    if record.get('bodyFormat') == 'canvas-image-v1':
        # This producer already shares its image body and Canvas identity.
        raise ValueError('The canonical saved image Canvas relationship is unavailable.')
    if record.get('kind') == 'media':
        from .output_media import retained_facts, validate_media_reference
        retained_facts(record, session['id'])
        kind = 'gif' if record['media']['mimeType'] == 'image/gif' else 'mp4'
        fields = ('id', 'sessionId', 'title', 'filename', 'version', 'sha256', 'bytes', 'media')
        ref = {'item': {k: record[k] for k in fields}, 'messageId': record['origin']['messageId']}
        validate_media_reference(ref, record, session['id'], kind=kind)
        body = put(service.db, {'mediaReference': ref})
    elif record.get('kind') == 'canvas':
        source = record.get('canvasSource')
        if not source:
            raise ValueError('The saved Canvas output has no immutable source.')
        data = output_bytes(service.db, record)
        value = resource(service.db, source['$resource'])
        if json.loads(data) != value:
            raise ValueError('The saved Canvas output differs from its admitted source.')
        kind = record.get('canvasKind', 'output')
        if kind in {'gif', 'mp4'}:
            from .output_media import validate_media_reference
            reference = value.get('mediaReference', {})
            owning = service.outputs.record(session['id'], reference.get('item', {}).get('id'))
            validate_media_reference(reference, owning, session['id'], kind=kind)
        body = copy.deepcopy(source)
    elif record.get('body'):
        data = output_bytes(service.db, record)
        suffix = Path(record.get('filename', '')).suffix.lower()
        if record.get('kind') == 'writing':
            kind = 'markdown'
        elif data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff'):
            kind = 'image'
        elif data.startswith(b'%PDF-'):
            kind = 'pdf'
        else:
            kind = {'.md': 'markdown', '.txt': 'text', '.json': 'json', '.jsonl': 'jsonl',
                    '.csv': 'text', '.py': 'code', '.js': 'code', '.ts': 'code'}.get(suffix, 'output')
        body = put(service.db, {'outputReference': binding})
        resolve_body(service.db, {'outputReference': binding}, kind, session['id'])
    else:
        # External documents are addresses, never executable copied pages.
        url = record.get('url')
        body = put(service.db, {'outputReference': binding})
    from .agent_canvas import scope
    row = {'id': uuid.uuid4().hex, 'sessionId': session['id'],
           'workspaceId': scope(service, session['id'])[1], 'messageId': None,
           'title': record['title'], 'kind': kind, 'body': body,
           'outputBinding': binding, 'createdAt': time.time(), 'tabOpen': False, 'view': {}}
    if url:
        row['url'] = url
    if kind in {'html', 'babylon', 'pdf'}:
        row['contentResource'] = copy.deepcopy(body)
    save(row, None, service._state, message=None)
    service._state.setdefault('canvasArtifacts', []).append(row)
    record['presentationArtifact'] = {'id': row['id'], 'version': 1, 'outputVersion': record['version']}
    service.db.execute('UPDATE output_records SET value=? WHERE id=?',
                       (json.dumps(record), record['id']))
    return row, 1


def publish(service, row, version, args, record=None):
    from .canvas_versions import definition
    from .canvas_views import capabilities
    selected = definition(row, version, service.db)
    mode = args['presentation']['chat']
    if record and not record.get('linked') and mode != 'none':
        from .service import AppError
        raise AppError('Relink this output before creating an active chat publication.', 409)
    if mode == 'inline' and not capabilities(selected['kind'])['inline']:
        from .service import AppError
        raise AppError(capabilities(selected['kind'])['reason'], 409)
    links = row.setdefault('publications', [])
    mid = args.get('messageId')
    retained_none = mode == 'none' and not mid and record is not None
    if retained_none:
        # Suppress the exact legacy InlineMedia relationship even for a
        # library-only adapter. This is its admitted origin, not a new turn.
        mid = record.get('origin', {}).get('messageId')
    same = [link for link in links if link.get('version') == version]
    existing = next((link for link in same if link.get('messageId') == mid), None) if mid else (
        same[0] if len(same) == 1 else None)
    legacy_mode = 'inline' if selected['kind'] in {'image', 'html', 'babylon', 'mermaid', 'dot'} else 'compact'
    if existing is None and mode != 'none':
        admission(service, row['sessionId'], args.get('messageId'))
    elif existing is not None and existing.get('chat', legacy_mode) != mode:
        # Reusing an admitted identity to open is read-like. A changed
        # publication, however, still needs current explicit anchor admission.
        admission(service, row['sessionId'], args.get('messageId'))
    elif existing is None and mid and not retained_none:
        admission(service, row['sessionId'], mid)
    link = {'messageId': mid if existing is None else existing['messageId'],
            'version': version, 'chat': mode}
    if record:
        link['outputId'] = record['id']
        marker = generation_binding(record, row, version) if link['messageId'] == record.get('origin', {}).get('messageId') else None
        if marker:
            link['imageGeneration'] = marker
    previous_link = {**existing, 'chat': existing.get('chat', legacy_mode)} if existing else None
    unchanged = previous_link == link or existing is None and not link['messageId']
    if existing is not None:
        existing.clear()
        existing.update(link)
    elif link['messageId']:
        links.append(link)
    return {'status': 'unchanged' if unchanged else 'published', 'accepted': True, **link}


def open_canvas(service, row, version, args, *, detached_input=False):
    from .service import AppError
    from .agent_canvas import target
    from .canvas_library import load
    from .canvas_versions import definition
    from .canvas_views import capabilities
    if args['presentation']['canvas'] != 'open':
        return {'status': 'not-requested'}
    caps = capabilities(definition(row, version, service.db)['kind'])
    if not caps['canvas']:
        return {'status': 'unavailable', 'reason': caps['reason']}
    if detached_input and not args.get('clientId'):
        return {'status': 'no-client', 'reason': 'The input has no single originating client; supply an eligible clientId.'}
    current = service.clients.current.get()
    if current is not None and args.get('clientId', current) != current:
        return {'status': 'no-client', 'reason': 'The request cannot open another client from this originating client.'}
    try:
        identity, candidates = target(service, row['sessionId'], args.get('clientId'),
                                      required=True, connected_only=True)
    except AppError as exc:
        return {'status': 'no-client', 'reason': str(exc)}
    if identity is None:
        return {'status': 'no-client', 'reason': 'No single eligible connected client displays this conversation.'}
    client = service.clients.records[identity]
    if client.get('kind') != 'web':
        return {'status': 'no-client', 'reason': 'This client has no native Canvas renderer.'}
    with service.clients.bind(identity):
        try:
            service.canvas_views.guard_transition('canvas.select', {'id': row['id'], 'version': version})
            # Settings and contributed component editors have their own dirty
            # declarations; content dirtiness is not the only replacement guard.
            shell = service.shell.get('client', identity, {})
            if ((client.get('view', {}).get('settingsCanvas') or client.get('canvas', {}).get('kind') == 'settings')
                    and any(view.get('dirty') for view in shell.get('views', {}).values())):
                raise AppError('Finish or cancel the Settings edit before replacing Canvas.', 409)
        except AppError as exc:
            return {'status': 'dirty-blocked', 'clientId': identity, 'reason': str(exc)}
        previous = {key: copy.deepcopy(client.get(key, {})) for key in ('canvas', 'canvasTabs', 'view')}
        load(service.state, service.db, row['id'], version=version)
        if client['canvas'].get('renderReports', {}).get('stored-source', {}).get('status') == 'error':
            client.update(previous)
            return {'status': 'unavailable', 'clientId': identity,
                    'reason': 'The exact saved artifact source is unavailable; publication was retained.'}
        client['view']['settingsCanvas'] = False
        client['view']['canvasFocused'] = False
        client['view'].setdefault('canvasDraft', {}).update(library=False, open=False, browser=False, overview=False)
        client.get('canvasViews', {}).pop('retained', None)
    return {'status': 'opened', 'clientId': identity}


async def dispatch(service, action, args, origin, command_id, expected_revision,
                   *, include_state, caller_session_id, detached_input=False):
    from .service import AppError
    from .canvas_versions import definition, reference, latest
    if origin == 'agent' and (not caller_session_id or args.get('sessionId') != caller_session_id):
        raise AppError('Artifact presentation must target the trusted calling conversation.', 403)
    if origin not in {'ui', 'agent'}:
        raise AppError('Artifact presentation requires an authenticated UI or agent caller.', 403)
    if action == 'canvas.present' and type(args['version']) is not int:
        raise AppError('Choose a positive integer exact version.', 400)
    fingerprint = hashlib.sha256(json.dumps(
        [action, args, origin, service.clients.current.get()], sort_keys=True).encode()).hexdigest()
    async with service.lock:
        prior = service.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?',
                                   (command_id,)).fetchone() if command_id else None
        if prior:
            if prior[0] != fingerprint:
                raise AppError('This command ID was already used with different contents.', 409)
            return {**json.loads(prior[1]), 'duplicate': True,
                    **({'state': service.browser_state()} if include_state else {})}
        if expected_revision is not None and expected_revision != service.state['revision']:
            raise AppError('The app changed. Refresh its state and retry.', 409)
        session = writable(service, args['sessionId'], origin)
        # A rejected publication must not leave an unpublished adapter or body
        # version in the live catalog. Existing immutable resources stay owned
        # by the normal transaction/resource retention boundary.
        before = list(service._state.get('canvasArtifacts', []))
        touched = None
        service.db.execute('SAVEPOINT artifact_presentation')

        def restore():
            if touched is not None:
                identity, snapshot = touched
                identity.clear()
                identity.update(snapshot)
            service._state['canvasArtifacts'] = before
            service.db.execute('ROLLBACK TO artifact_presentation')
            service.db.execute('RELEASE artifact_presentation')
        record = None
        try:
            if action == 'canvas.show':
                admission(service, session['id'], args.get('messageId')) if args.get('presentation', {}).get('chat', 'compact') != 'none' else None
                from .workspace_canvas import canvas_command
                from .canvas_library import remember
                from .agent_canvas import scope
                if args['kind'] == 'settings':
                    raise AppError('Use the existing live Settings operation, not a general artifact publication.', 409)
                scoped = {**service.state, 'selectedSessionId': session['id'],
                          'selectedWorkspaceId': scope(service, session['id'])[1], 'canvas': {},
                          'canvasTabs': {}}
                canvas_command(scoped, 'canvas.show', args, origin)
                from .canvas_versions import assert_clean
                assert_clean(service, scoped['canvas']['id'])
                previous = next((r for r in before if r['id'] == scoped['canvas']['id']), None)
                if previous is not None:
                    touched = previous, copy.deepcopy(previous)
                scoped['canvas']['messageId'] = args.get('messageId')
                remember(scoped, service.db, message=None)
                row = next(r for r in service._state['canvasArtifacts'] if r['id'] == scoped['canvas']['id'])
                if previous is None:
                    # Legacy remember opens tabs by default. General placement
                    # is library-first; only open_canvas may open a local tab.
                    row['tabOpen'] = False
                version = latest(row)
                args = {**args, 'presentation': args.get('presentation', {'chat': 'compact', 'canvas': 'unchanged'})}
            elif 'outputId' in args:
                record = service.outputs.record(session['id'], args['outputId'])
                if type(record.get('version')) is not int or record['version'] != args['version']:
                    raise AppError('Choose the exact owned output version, not a Canvas revision.', 409)
                if not record.get('linked') and args['presentation']['chat'] != 'none':
                    raise AppError('Relink this output before creating an active chat publication.', 409)
                if args['presentation']['chat'] != 'none':
                    # Check before adding a new adapter; an existing anchor is
                    # reused below without recurring history qualification.
                    existing = record.get('presentationArtifact') or (
                        record.get('bodyFormat') == 'canvas-image-v1' and record.get('canvasId'))
                    if not existing:
                        admission(service, session['id'], args.get('messageId'))
                row, version = adapter(service, session, record)
                if any(row is saved for saved in before):
                    touched = row, copy.deepcopy(row)
            else:
                row = next((r for r in service._state.get('canvasArtifacts', [])
                            if r['id'] == args['id'] and r.get('sessionId') == session['id']), None)
                if row is None:
                    raise AppError('The artifact is unavailable in this conversation.', 404)
                touched = row, copy.deepcopy(row)
                version = args['version']
                selected = definition(row, version, service.db)
                if selected.get('outputBinding'):
                    binding = selected['outputBinding']
                    record = service.outputs.record(session['id'], binding['outputId'])
                    check_output(binding, record, session['id'])
            publication = publish(service, row, version, args, record)
            canvas = open_canvas(service, row, version, args, detached_input=detached_input)
        except AppError:
            restore()
            raise
        except (ValueError, KeyError, TypeError, OSError) as exc:
            restore()
            raise AppError(str(exc), 409) from None
        service.db.execute('RELEASE artifact_presentation')
        result = {'id': row['id'], 'version': version, 'revision': latest(row),
                  'reference': reference(row, version), 'publication': publication, 'canvas': canvas}
        if record:
            result.update(outputId=record['id'], outputVersion=record['version'])
        receipt = {'accepted': True, 'revision': service._state['revision'] + 1,
                   'effects': [], 'result': result}
        if command_id:
            service.db.execute('INSERT INTO commands VALUES (?,?,?)',
                               (command_id, fingerprint, json.dumps(receipt)))
        service._publish_changes(globals={'canvasArtifacts', 'canvas', 'canvasTabs', 'view'})
        return {**receipt, **({'state': service.browser_state()} if include_state else {})}