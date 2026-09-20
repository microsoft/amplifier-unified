"""Client-local renderer choices and two explicitly addressed artifact views.

Artifacts and tool bindings stay in the existing canvas library. The primary
view follows its selection; the secondary view pins one ordinary artifact.
MCP Apps retain their existing single active binding per client.
"""
from __future__ import annotations

import copy
import hashlib
import json

from .shell_modules import IDENTITY, encoded, fail

PROFILE = 'trusted-native-renderer-v1'
KINDS = ['markdown', 'text', 'code', 'json', 'jsonl', 'image', 'html', 'babylon', 'mermaid', 'dot', 'a2ui', 'browser']
CAPABILITIES = ['canvas.resource.read', 'canvas.view.update', 'canvas.view.report']
LABELS = {'markdown': 'Markdown', 'text': 'Plain text', 'code': 'Code', 'json': 'JSON', 'jsonl': 'JSON lines',
          'image': 'Image', 'html': 'HTML', 'babylon': '3D scene', 'mermaid': 'Mermaid', 'dot': 'Graphviz',
          'a2ui': 'Interactive controls', 'browser': 'Website', 'mcp-app': 'Tool app'}
BUILTINS = {'builtin.canvas.' + kind: {'id': 'builtin.canvas.' + kind, 'label': label,
    'version': '1.0.0', 'apiVersion': '1.0', 'profile': PROFILE, 'stateSchema': 'canvas-view-v1',
    'capabilities': CAPABILITIES, 'resourceKinds': [kind]} for kind, label in LABELS.items()}


def definitions(schema, string):
    target = {'viewId': {'enum': ['primary', 'secondary']}, 'resourceId': IDENTITY,
              'resourceRevision': string(64), 'generation': {'type': 'integer', 'minimum': 0}}
    actions = {
        'canvas.views.inspect': ('Inspect both explicitly scoped artifact views and available renderers.', schema()),
        'canvas.views.open': ('Pin an existing ordinary artifact beside the primary view without selecting its chat.', schema({'resourceId': IDENTITY, 'sessionId': {'type': ['string', 'null'], 'maxLength': 200}})),
        'canvas.views.close': ('Close the secondary view; retain its artifact and saved preferences.', schema(target)),
        'canvas.views.renderer': ('Select a validated renderer for this exact artifact/view generation.', schema({**target, 'renderer': string(100)})),
        'canvas.views.recover': ('Restore this view to its built-in renderer; retain the saved artifact.', schema(target)),
        'canvas.views.command': ('Invoke an explicitly targeted renderer control without retargeting the selected conversation.', schema({**target, 'action': string(100), 'args': {'type': 'object'}}, [*target, 'action', 'args'])),
        'canvas.views.dirty': ('Declare unsaved renderer-local edits before a replacement or close.', schema({**target, 'dirty': {'type': 'boolean'}})),
        'canvas.views.status': ('Record browser mount evidence for this renderer; not a package validation receipt.', schema({**target, 'status': {'enum': ['loading', 'ready', 'error']}, 'message': string(2000)})),
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
        return client.setdefault('canvasViews', {'secondary': None, 'preferences': {}})

    def artifact(self, identity):
        row = next((r for r in self.service.state.get('canvasArtifacts', []) if r['id'] == identity), None)
        if row is None:
            fail('The saved artifact is unavailable.', 404)
        return row

    @staticmethod
    def revision(row):
        # View preferences, reports and transient tool context are not content.
        return hashlib.sha256(encoded({key: row.get(key) for key in ('id', 'kind', 'body', 'url')}).encode()).hexdigest()

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
        elif view_id == 'secondary':
            identity = self.record()['secondary']
            if not identity:
                fail('The secondary view is closed.', 404)
        else:
            fail('Unknown canvas view.')
        row = self.artifact(identity)
        preference = self.preference(view_id, row)
        binding = [identity, self.revision(row), bool(current.get('open'))]
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
                'activation': preference.get('activation'),
                'view': copy.deepcopy(self.service.state['canvas'].get('view', {})) if view_id == 'primary' else copy.deepcopy(preference['view']),
                'renderReports': copy.deepcopy(self.service.state['canvas'].get('renderReports', {})) if view_id == 'primary' else copy.deepcopy(preference['renderReports']),
                **{key: copy.deepcopy(preference[key]) for key in ('document', 'interaction', 'events') if key in preference}}

    def project(self):
        from .service import AppError
        result = {'views': []}
        for view_id in ('primary', 'secondary'):
            try:
                result['views'].append(self.summary(view_id))
            except AppError as exc:
                if view_id == 'secondary' and self.record()['secondary']:
                    result['views'].append({'viewId': view_id, 'resourceId': self.record()['secondary'],
                                           'resourceRevision': 'unavailable', 'generation': 0, 'error': str(exc)})
        return result

    def canvas(self, view_id):
        from .state_storage import resource
        row, preference = self.resolve(view_id)
        indirect = row.get('contentResource') and row['kind'] in {'html', 'babylon'}
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
        if action == 'canvas.views.inspect':
            return self.project(), []
        if action == 'canvas.views.open':
            row = self.artifact(args['resourceId'])
            if row.get('sessionId') != args['sessionId']:
                fail('The artifact belongs to a different conversation.', 409)
            if row['kind'] == 'mcp-app':
                fail('Interactive tool apps currently use the primary view. Their work remains active.', 409)
            record = self.record()
            if record['secondary']:
                old = next((r for r in self.service.state.get('canvasArtifacts', []) if r['id'] == record['secondary']), None)
                if old and self.preference('secondary', old)['dirty']:
                    return {'status': 'deferred', 'reason': 'Finish or cancel the secondary view edit first.'}, []
            record['secondary'] = row['id']
            self.preference('secondary', row)['generation'] += 1
            self.service.state['canvas']['open'] = True
            return {'status': 'opened'}, []
        if action == 'canvas.views.close' and args['viewId'] == 'secondary' and self.record()['secondary'] == args['resourceId']:
            if not any(row['id'] == args['resourceId'] for row in self.service.state.get('canvasArtifacts', [])):
                self.record()['secondary'] = None
                return {'status': 'closed'}, []
        row, preference = self.target(args)
        if action in {'canvas.views.renderer', 'canvas.views.close'} and preference['dirty']:
            return {'status': 'deferred', 'reason': 'Finish or cancel this renderer edit first.'}, []
        if action == 'canvas.views.close':
            if args['viewId'] != 'secondary':
                fail('Use the canvas close control for the primary view.')
            self.record()['secondary'] = None
        elif action in {'canvas.views.renderer', 'canvas.views.recover'}:
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
        allowed = {'canvas.view', 'canvas.report', 'canvas.snapshot', 'canvas.interact', 'canvas.event', 'canvas.copy', 'canvas.download', 'canvas.openExternal'}
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
            required = {'canvas.view': 'canvas.view.update', 'canvas.report': 'canvas.view.report'}.get(action)
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
        if action in {'canvas.copy', 'canvas.download'}:
            content = canvas.get('url') if canvas['kind'] == 'browser' else canvas.get('content', json.dumps(canvas.get('surface', {}), indent=2))
            if canvas.get('contentResource') or (action == 'canvas.download' and canvas['kind'] == 'babylon'):
                effects.append({'type': 'clipboard.url' if action == 'canvas.copy' else 'download.url',
                                'url': '/api/canvas/' + row['id'] + ('/source' if action == 'canvas.copy' else '/download'),
                                'canvasId': row['id'], 'filename': 'canvas-3d.html' if canvas['kind'] == 'babylon' else 'canvas.html'})
            else:
                effects.append({'type': 'clipboard.write' if action == 'canvas.copy' else 'download',
                                'content': content, 'filename': 'canvas.' + {'markdown': 'md', 'html': 'html', 'mermaid': 'mmd', 'dot': 'dot', 'json': 'json', 'jsonl': 'jsonl', 'a2ui': 'json'}.get(canvas['kind'], 'txt'),
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
