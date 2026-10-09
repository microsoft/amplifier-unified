"""Client-local renderer choices for the selected artifact.

Saved artifacts remain in the library. Inline previews and the full Canvas
viewer address the same immutable definitions through this owner.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid

from .shell_modules import IDENTITY, encoded, fail

PROFILE = 'trusted-native-renderer-v1'
KINDS = ['markdown', 'text', 'code', 'json', 'jsonl', 'image', 'html', 'babylon', 'mermaid', 'dot', 'a2ui', 'browser']
CAPABILITIES = ['canvas.resource.read', 'canvas.view.update', 'canvas.view.report']
LABELS = {'markdown': 'Markdown', 'text': 'Plain text', 'code': 'Code', 'json': 'JSON', 'jsonl': 'JSON lines',
          'image': 'Image', 'html': 'HTML', 'babylon': '3D scene', 'mermaid': 'Mermaid', 'dot': 'Graphviz',
          'a2ui': 'Interactive controls', 'browser': 'Website', 'mcp-app': 'Tool app', 'canvas-app': 'Interactive surface'}
BUILTINS = {'builtin.canvas.' + kind: {'id': 'builtin.canvas.' + kind, 'label': label,
    'version': '1.0.0', 'apiVersion': '1.0', 'profile': PROFILE, 'stateSchema': 'canvas-view-v1',
    'capabilities': CAPABILITIES, 'resourceKinds': [kind]} for kind, label in LABELS.items()}


def definitions(schema, string):
    target = {'viewId': string(100), 'resourceId': IDENTITY,
              'resourceRevision': string(64), 'generation': {'type': 'integer', 'minimum': 0}}
    actions = {
        'canvas.views.inline': ('Open a bounded, client-local preview of a version published in the selected chat.', schema({'resourceId': IDENTITY, 'version': {'type': 'integer', 'minimum': 1}, 'messageId': IDENTITY, 'previewId': IDENTITY}, ['resourceId', 'version', 'messageId', 'previewId'])),
        'canvas.views.release': ('Close this exact inline preview without changing the saved artifact or Canvas selection.', schema(target, list(target))),
        'canvas.views.imageDraft': ('Attach this exact saved image to the current client draft for a follow-up. Does not send, generate, or overwrite the original.', schema(target, list(target))),
        'canvas.views.inspect': ('Inspect the selected artifact view and available renderers.', schema()),
        'canvas.views.renderer': ('Select a validated renderer for this exact artifact/view generation.', schema({**target, 'renderer': string(100)})),
        'canvas.views.recover': ('Restore this view to its built-in renderer; retain the saved artifact.', schema(target)),
        'canvas.views.command': ('Invoke an explicitly targeted renderer control without retargeting the selected conversation.', schema({**target, 'action': string(100), 'args': {'type': 'object'}}, [*target, 'action', 'args'])),
        'canvas.views.dirty': ('Declare unsaved renderer-local edits before a replacement or close.', schema({**target, 'dirty': {'type': 'boolean'}, 'editVersion': {'type': 'integer', 'minimum': 0}}, [*target, 'dirty'])),
        'canvas.views.status': ('Record browser mount evidence for this renderer; not a package validation receipt.', schema({**target, 'status': {'enum': ['loading', 'ready', 'error']}, 'message': string(2000)})),
        'canvas.views.observe': ('Report bounded, untrusted drawing and visible-control evidence for this exact view. Does not start an agent turn.', schema({**target,
            'revision': string(80), 'editVersion': {'type': 'integer', 'minimum': 0}, 'pending': {'type': 'boolean'},
            'status': {'enum': ['ready', 'pending', 'unavailable']}, 'image': string(470000), 'text': string(1800),
            'controls': {'type': 'array', 'maxItems': 16, 'items': schema({'label': string(100), 'value': string(100)}, ['label', 'value'])},
            'reason': string(200), 'checkpointId': string(100)}, [*target, 'revision', 'editVersion', 'pending', 'status'])),
    }
    for _, spec in actions.values():
        spec['properties']['clientId'] = IDENTITY
    return actions


class CanvasViews:
    def __init__(self, service):
        self.service = service

    def record(self):
        client = self.service.clients.record()
        if client is None:
            fail('Attach a client before addressing canvas views.', 409)
        return client.setdefault('canvasViews', {'preferences': {}})

    def guard_transition(self, action, args):
        """Reject parent actions before they can discard a mounted dirty view.

        This runs under the action lock, before selection, drafts, tabs, or
        external work change. View-specific replacements retain their existing
        deferred receipts; explicit recovery is the deliberate discard route.
        """
        from .service import AppError

        def check(client, view_ids):
            if not client:
                return
            views = client.get('canvasViews', {})
            for view_id in view_ids:
                if view_id == 'primary':
                    if not (client.get('canvas', {}).get('open') or views.get('retained')):
                        continue
                    identity = client.get('canvas', {}).get('id')
                else:
                    identity = views.get('inline', {}).get(view_id, {}).get('resourceId')
                if identity and views.get('preferences', {}).get(view_id + ':' + identity, {}).get('dirty'):
                    label = 'Canvas' if view_id == 'primary' else 'inline preview'
                    raise AppError('Finish or cancel the ' + label + ' edit before leaving it. '
                                   'Use viewer recovery only to discard that edit.', 409, code='canvas_view_dirty')

        # Deleting shared history can unmount another client's whole canvas.
        if action == 'session.delete':
            for client in self.service.clients.records.values():
                if client.get('selectedSessionId') == args['id']:
                    check(client, ['primary', *client.get('canvasViews', {}).get('inline', {})])
            return
        client = self.service.clients.record()
        if client is None:
            return
        if (action in {'session.draft', 'session.create', 'session.fork', 'message.edit',
                       'workspace.select', 'workspace.add', 'workspace.create', 'workspace.remove'}
                or action == 'session.select' and args['id'] != client.get('selectedSessionId')):
            check(client, list(client.get('canvasViews', {}).get('inline', {})))
        if action == 'canvas.close':
            check(client, ('primary',))
        elif (action in {'session.draft', 'session.create', 'session.fork', 'message.edit',
                         'workspace.select', 'workspace.add', 'workspace.create', 'workspace.remove'}
              or action == 'session.select' and args['id'] != client.get('selectedSessionId')
              or action == 'canvas.select' and (args['id'] != client.get('canvas', {}).get('id') or args.get('version') != client.get('canvas', {}).get('selectedVersion'))
              or action == 'canvas.tabClose' and args['id'] == client.get('canvas', {}).get('id')
              or action in {'canvas.show', 'canvas.openFile', 'canvas.apps.create', 'smartTools.open'} and args.get('sessionId', client.get('selectedSessionId')) == client.get('selectedSessionId')):
            check(client, ('primary',))

    def artifact(self, identity):
        row = next((r for r in self.service.state.get('canvasArtifacts', []) if r['id'] == identity), None)
        if row is None:
            fail('The saved artifact is unavailable.', 404)
        return row

    @staticmethod
    def revision(row):
        # View preferences, reports and transient tool context are not content.
        return hashlib.sha256(encoded({**{key: row.get(key) for key in ('id', 'kind', 'body', 'url', 'revision', 'selectedVersion')}, **({'appRevision': row['app']['revision']} if row.get('app') else {})}).encode()).hexdigest()

    def preference(self, view_id, row):
        preferences = self.record()['preferences']
        key = view_id + ':' + row['id']
        if key not in preferences:
            preferences[key] = {'renderer': 'builtin.canvas.' + row['kind'], 'generation': 0,
                                'view': copy.deepcopy(row.get('view', {})), 'dirty': False, 'renderReports': {}}
        return preferences[key]

    def resolve(self, view_id):
        record = self.record()
        current = self.service.state.get('canvas', {})
        if view_id == 'primary':
            if not current.get('kind') or current.get('placeholder'):
                record['primaryBinding'] = None
                fail('The primary view has no selected artifact.', 404)
            identity = current['id']
        else:
            current = record.get('inline', {}).get(view_id)
            if not current:
                fail('Unknown canvas view.', 404)
            identity = current['resourceId']
        row = self.artifact(identity)
        if view_id != 'primary':
            if (row.get('sessionId') != self.service.state.get('selectedSessionId')
                    or row.get('workspaceId') != self.service.state.get('selectedWorkspaceId')):
                fail('This preview belongs to another conversation.', 409)
        from .canvas_versions import definition
        row = definition(row, current.get('selectedVersion'), self.service.db)
        preference = self.preference(view_id, row)
        previous_binding = record.get(view_id + 'Binding')
        visible_binding = (previous_binding[2] if record.get('retained') and previous_binding else bool(current.get('open'))) if view_id == 'primary' else True
        binding = [identity, self.revision(row), visible_binding]
        if record.get(view_id + 'Binding') != binding:
            record[view_id + 'Binding'] = binding
            preference['generation'] += 1
            preference.pop('activation', None)
        return row, preference

    def target(self, args):
        row, preference = self.resolve(args['viewId'])
        if row['id'] != args['resourceId'] or self.revision(row) != args['resourceRevision'] or preference['generation'] != args['generation']:
            fail('This artifact view changed. Read it again before applying the action.', 409)
        return row, preference

    def manifest(self, renderer):
        result = BUILTINS.get(renderer) or self.service.shell.manifest(renderer)
        if result['profile'] != PROFILE:
            fail('Choose a canvas renderer package.')
        return result

    def choices(self, kind):
        result = [{'id': key, 'label': value['label'], 'manifest': value, 'url': None}
                  for key, value in BUILTINS.items() if kind in value['resourceKinds']]
        for digest, raw in self.service.db.execute("SELECT id,value FROM shell_records WHERE kind='package' ORDER BY rowid DESC"):
            package = json.loads(raw)
            manifest = package['manifest']
            if manifest.get('profile') != PROFILE or kind not in manifest.get('resourceKinds', []):
                continue
            try:
                self.manifest(digest)
            except Exception:
                continue
            result.append({'id': digest, 'label': manifest.get('label', manifest['id']), 'manifest': manifest,
                           'url': f'/api/shell/packages/{digest}.mjs'})
            if len(result) >= 50:
                break
        return result

    def summary(self, view_id):
        from .canvas_paths import paths
        row, preference = self.resolve(view_id)
        choices = self.choices(row['kind'])
        renderer = next((r for r in choices if r['id'] == preference['renderer']), None)
        if renderer is None:
            # Newer packages may fill the bounded menu, but must never evict
            # a still-valid saved choice from a mounted view.
            try:
                manifest = self.manifest(preference['renderer'])
                if row['kind'] in manifest['resourceKinds']:
                    renderer = {'id': preference['renderer'], 'label': manifest.get('label', manifest['id']),
                                'manifest': manifest, 'url': f"/api/shell/packages/{preference['renderer']}.mjs"}
                    choices.append(renderer)
            except Exception:
                pass
        return {'viewId': view_id, 'resourceId': row['id'], 'resourceRevision': self.revision(row),
                'generation': preference['generation'], 'renderer': preference['renderer'], 'dirty': preference['dirty'],
                'resource': {k: row[k] for k in ('id', 'title', 'kind', 'sessionId', 'workspaceId', 'path') if k in row},
                'choices': choices, 'available': renderer is not None,
                'filePaths': paths(self.service.state, row),
                'selectedVersion': row.get('selectedVersion'),
                'latestStateRevision': self.artifact(row['id']).get('app', {}).get('stateRevision'),
                'latestVersion': row.get('latestVersion', row.get('app', {}).get('revision', row.get('revision', 1))),
                'versions': [{k: item.get(k) for k in ('version', 'title', 'createdAt')} for item in row.get('app', {}).get('versions', row.get('versions', []))],
                'activation': preference.get('activation'),
                **({'app': copy.deepcopy(row['app'])} if row.get('app') else {}),
                'view': copy.deepcopy(self.service.state['canvas'].get('view', {})) if view_id == 'primary' else copy.deepcopy(preference['view']),
                'renderReports': copy.deepcopy(self.service.state['canvas'].get('renderReports', {})) if view_id == 'primary' else copy.deepcopy(preference['renderReports']),
                **{key: copy.deepcopy(preference[key]) for key in ('document', 'interaction', 'events') if key in preference}}

    def project(self):
        from .service import AppError
        views = []
        try:
            identities = ['primary', *self.record().get('inline', {})]
        except AppError:
            return {'views': []}
        for identity in identities:
            try:
                views.append(self.summary(identity))
            except AppError:
                pass
        return {'views': views}

    def open_inline(self, args):
        from .canvas_versions import definition, latest
        row = self.artifact(args['resourceId'])
        if (row.get('sessionId') != self.service.state.get('selectedSessionId')
                or row.get('workspaceId') != self.service.state.get('selectedWorkspaceId')):
            fail('Open the conversation that contains this artifact first.', 409)
        publications = row.get('publications') or [{'messageId': row.get('messageId'), 'version': latest(row)}]
        if {'messageId': args['messageId'], 'version': args['version']} not in publications:
            fail('This version was not published in that message.', 409)
        saved = definition(row, args['version'], self.service.db)
        if saved['kind'] not in set(KINDS) - {'a2ui', 'browser'}:
            fail('This artifact opens in Canvas.', 409)
        record = self.record()
        inline = record.setdefault('inline', {})
        for identity, binding in list(inline.items()):
            if binding.get('sessionId') != self.service.state.get('selectedSessionId'):
                self.release_inline(identity)
        binding = {'resourceId': row['id'], 'selectedVersion': args['version'],
                   'messageId': args['messageId'], 'sessionId': row['sessionId'], 'previewId': args['previewId']}
        identity = next((key for key, value in inline.items() if value == binding), None)
        if identity is None:
            if len(inline) >= 8:
                fail('Close a preview to open another here, or open this artifact in Canvas.', 409)
            identity = 'inline-' + str(uuid.uuid4())
            inline[identity] = binding
        return self.summary(identity)

    def release_inline(self, identity):
        record = self.record()
        binding = record.get('inline', {}).get(identity)
        if binding is None:
            return
        key = identity + ':' + binding['resourceId']
        if record['preferences'].get(key, {}).get('dirty'):
            fail('Finish or cancel this preview edit before closing it.', 409)
        record['preferences'].pop(key, None)
        record.pop(identity + 'Binding', None)
        del record['inline'][identity]

    def canvas(self, view_id):
        from .state_storage import resource
        row, preference = self.resolve(view_id)
        indirect = row.get('contentResource') and row['kind'] in {'html', 'babylon', 'canvas-app'}
        body = {} if indirect else resource(self.service.db, row['body']['$resource'])
        canvas = {**copy.deepcopy(row), **body, 'open': True, 'viewId': view_id,
                  'resourceRevision': self.revision(row), 'generation': preference['generation']}
        canvas.pop('body', None)
        if view_id == 'primary':
            live = self.service.state['canvas']
            canvas.update({key: copy.deepcopy(live[key]) for key in ('view', 'renderReports', 'document', 'interaction', 'mcp', 'events') if key in live})
        else:
            canvas.update({key: copy.deepcopy(preference[key]) for key in ('view', 'renderReports', 'document', 'interaction', 'events') if key in preference})
        if indirect:
            canvas.pop('content', None)
        else:
            canvas.pop('contentResource', None)
        return canvas

    def command(self, action, args, origin):
        """Called under the normal AppService action lock and receipt handling."""
        if action == 'canvas.views.inline':
            return self.open_inline(args), []
        if action == 'canvas.views.observe':
            return self.service.surface_context.observe(args), []
        if action == 'canvas.views.inspect':
            return self.project(), []
        row, preference = self.target(args)
        if action == 'canvas.views.imageDraft':
            from .attachments import save, MAX_FILES
            from .canvas_downloads import filename
            canvas = self.canvas(args['viewId'])
            if canvas['kind'] != 'image' or row.get('sessionId') != self.service.state.get('selectedSessionId'):
                fail('Choose an image in this conversation first.', 409)
            draft = self.service.clients.attachments(self.service._session(row['sessionId']))
            if len(draft) >= MAX_FILES:
                fail('Attach up to 8 files per message.', 409)
            attachment = save(self.service.data_dir, filename(canvas), canvas['content'].split(';base64,', 1)[1])
            draft.append(attachment)
            return {'status': 'attached', 'attachmentId': attachment['id'], 'sent': False}, []
        if action == 'canvas.views.release':
            if args['viewId'] == 'primary':
                fail('Use the Canvas close control for the primary viewer.')
            self.release_inline(args['viewId'])
            return {'status': 'closed'}, []
        if action == 'canvas.views.renderer' and preference['dirty']:
            return {'status': 'deferred', 'reason': 'Finish or cancel this renderer edit first.'}, []
        if action in {'canvas.views.renderer', 'canvas.views.recover'}:
            renderer = args.get('renderer', 'builtin.canvas.' + row['kind'])
            manifest = self.manifest(renderer)
            if row['kind'] not in manifest['resourceKinds']:
                fail('This renderer does not support the artifact format.')
            if action != 'canvas.views.recover':
                try:
                    old = self.manifest(preference['renderer'])
                except Exception:
                    old = None
                if old and old['stateSchema'] != manifest['stateSchema']:
                    return {'status': 'deferred', 'reason': 'Renderer state migration is not supported.'}, []
            preference.update(renderer=renderer, generation=preference['generation'] + 1, dirty=False, renderReports={})
            preference.pop('activation', None)
        elif action == 'canvas.views.dirty':
            preference['dirty'] = args['dirty']
            if 'editVersion' in args:
                preference['editVersion'] = args['editVersion']
        elif action == 'canvas.views.status':
            preference['activation'] = {'status': args['status'], 'message': args['message']}
        elif action == 'canvas.views.command':
            return self.control(row, preference, args, origin)
        return {'status': 'updated'}, []

    def control(self, row, preference, target, origin):
        from .workspace_canvas import canvas_command
        from .service import ACTION_DEFINITIONS
        from jsonschema import validate, ValidationError
        action, args = target['action'], copy.deepcopy(target['args'])
        allowed = {'canvas.view', 'canvas.report', 'canvas.snapshot', 'canvas.interact', 'canvas.event', 'canvas.copy', 'canvas.copyPath', 'canvas.download', 'canvas.openExternal', 'canvas.reference'}
        if action not in allowed:
            fail('This operation is not a renderer capability.', 403)
        renderer = preference['renderer']
        try:
            manifest = self.manifest(renderer)
        except Exception:
            renderer = 'builtin.canvas.' + row['kind']
            manifest = BUILTINS[renderer]
        if preference.get('activation', {}).get('status') == 'error':
            renderer = 'builtin.canvas.' + row['kind']
            manifest = BUILTINS[renderer]
        if renderer not in BUILTINS:
            required = {'canvas.view': 'canvas.view.update', 'canvas.report': 'canvas.view.report', 'canvas.copyPath': 'canvas.resource.read'}.get(action)
            if not required or required not in manifest['capabilities']:
                fail('The renderer has not declared this capability.', 403)
        if action != 'canvas.event':
            if args.get('id', row['id']) != row['id']:
                fail('A renderer cannot retarget its artifact.')
            args['id'] = row['id']
        canvas = self.canvas(target['viewId'])
        try:
            if action == 'canvas.view' and renderer not in BUILTINS:
                patch = args.get('patch')
                if not isinstance(patch, dict) or len(encoded({**canvas.get('view', {}), **patch}).encode()) > 16000:
                    fail('Accumulated renderer view state must be an object of at most 16 KB.')
            else:
                validate(args, ACTION_DEFINITIONS[action][1])
        except ValidationError as exc:
            fail(exc.message)
        effects = []
        if action == 'canvas.reference':
            from .canvas_reference import command
            version = row.get('selectedVersion') or row.get('revision', 1)
            if args['version'] != version:
                fail('The selected document version changed. Select the text again.', 409)
            return command(self.service, args), []
        if action == 'canvas.copyPath':
            from .canvas_paths import copy_path
            return copy_path({**self.service.state, 'canvas': canvas}, args)
        if action in {'canvas.copy', 'canvas.download'}:
            from .canvas_downloads import filename
            content = canvas.get('url') if canvas['kind'] == 'browser' else canvas.get('content', json.dumps(canvas.get('surface', {}), indent=2))
            if canvas.get('contentResource') or (action == 'canvas.download' and canvas['kind'] in {'babylon', 'image'}):
                effects.append({'type': 'clipboard.url' if action == 'canvas.copy' else 'download.url',
                                'url': '/api/canvas/' + row['id'] + ('/source' if action == 'canvas.copy' else '/download') + '?version=' + str(row.get('selectedVersion') or row.get('app', {}).get('revision', row.get('revision', 1))),
                                'canvasId': row['id'], 'filename': filename(canvas)})
            else:
                effects.append({'type': 'clipboard.write' if action == 'canvas.copy' else 'download',
                                'content': content, 'filename': filename(canvas),
                                'mime': 'text/plain', 'canvasId': row['id']})
        elif action == 'canvas.openExternal':
            if canvas['kind'] != 'browser':
                fail('Select a website artifact first.')
            effects.append({'type': 'browser.open', 'url': canvas['url']})
        else:
            scoped = {**self.service.state, 'canvas': canvas}
            canvas_command(scoped, action, args, origin)
            for key in ('view', 'renderReports', 'document', 'interaction', 'events'):
                if key in canvas:
                    preference[key] = copy.deepcopy(canvas[key])
                    if target['viewId'] == 'primary':
                        self.service.state['canvas'][key] = copy.deepcopy(canvas[key])
        return {'status': 'updated'}, effects
