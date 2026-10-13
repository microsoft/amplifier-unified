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
KINDS = ['markdown', 'text', 'code', 'json', 'jsonl', 'image', 'gif', 'mp4', 'pdf', 'html', 'babylon', 'mermaid', 'dot', 'a2ui', 'browser', 'canvas-app', 'mcp-app']
LIVE_KINDS = {'canvas-app', 'mcp-app'}
CAPABILITIES = ['canvas.resource.read', 'canvas.view.update', 'canvas.view.report']
LABELS = {'markdown': 'Markdown', 'text': 'Plain text', 'code': 'Code', 'json': 'JSON', 'jsonl': 'JSON lines',
          'image': 'Image', 'gif': 'Retained GIF', 'mp4': 'Retained video', 'pdf': 'PDF', 'html': 'HTML', 'babylon': '3D scene', 'mermaid': 'Mermaid', 'dot': 'Graphviz',
          'a2ui': 'Interactive controls', 'browser': 'Website', 'mcp-app': 'Tool app', 'canvas-app': 'Interactive surface', 'settings': 'Settings'}
BUILTINS = {'builtin.canvas.' + kind: {'id': 'builtin.canvas.' + kind, 'label': label,
    'version': '1.0.0', 'apiVersion': '1.0', 'profile': PROFILE, 'stateSchema': 'canvas-view-v1',
    'capabilities': CAPABILITIES, 'resourceKinds': [kind]} for kind, label in LABELS.items()}


def capabilities(kind):
    """Use the same native admission for projection and mount enforcement."""
    canvas = kind in LABELS
    inline = kind in KINDS
    result = {'compact': True, 'inline': inline, 'canvas': canvas}
    if not inline:
        result['reason'] = ('Inline mounting is not qualified for this renderer.'
                            if canvas else 'No native renderer; download or hand off the saved output.')
    return result


def project_artifacts(state):
    """Metadata only; exact-version consumers also see per-definition support."""
    state['canvasArtifacts'] = [
        {**row, 'presentationCapabilities': capabilities(row.get('kind')),
         'versions': [{**entry, 'presentationCapabilities': capabilities(entry.get('kind', row.get('kind')))}
                      for entry in row.get('versions', [])]}
        for row in state.get('canvasArtifacts', [])]
    return state


def target_fields(string):
    return {'viewId': string(100), 'resourceId': IDENTITY,
            'resourceRevision': string(64), 'generation': {'type': 'integer', 'minimum': 0}}


def view_schema(spec, string):
    """Optional target is all-or-none, never a partially qualified grant."""
    fields = target_fields(string)
    spec['properties'].update(fields)
    spec['dependentRequired'] = {key: [other for other in fields if other != key] for key in fields}
    return spec


def definitions(schema, string):
    target = target_fields(string)
    actions = {
        'canvas.views.inline': ('Open a bounded, client-local preview of a version published in the selected chat. followLatest explicitly opts an interactive owner into live revisions.', schema({'resourceId': IDENTITY, 'version': {'type': 'integer', 'minimum': 1}, 'messageId': IDENTITY, 'previewId': IDENTITY, 'followLatest': {'type': 'boolean'}}, ['resourceId', 'version', 'messageId', 'previewId'])),
        'canvas.views.follow': ('Switch this same interactive view between Latest and its original published number. Refuses dirty edits; never publishes or launches work.', schema({**target, 'followLatest': {'type': 'boolean'}}, [*target, 'followLatest'])),
        'canvas.views.release': ('Close this exact inline preview without changing the saved artifact or Canvas selection.', schema(target, list(target))),
        'canvas.views.imageDraft': ('Attach this exact saved image to the current client draft for a follow-up. Does not send, generate, or overwrite the original.', schema(target, list(target))),
        'canvas.views.reveal': ('Reveal the exact artifact file in the file manager on its named owning app host. It does not open the browser device file manager. Bind hostId and instanceId from fileReveal.', schema({**target,'hostId':string(200),'instanceId':string(100)}, [*target,'hostId','instanceId'])),
        'canvas.views.inspect': ('Inspect the selected artifact view and available renderers.', schema()),
        'canvas.views.renderer': ('Select a validated renderer for this exact artifact/view generation.', schema({**target, 'renderer': string(100)})),
        'canvas.views.recover': ('Restore this view to its built-in renderer; retain the saved artifact.', schema(target)),
        'canvas.views.command': ('Invoke an explicitly targeted renderer control without retargeting the selected conversation.', schema({**target, 'action': string(100), 'args': {'type': 'object'}}, [*target, 'action', 'args'])),
        'canvas.views.dirty': ('Declare unsaved renderer-local edits before a replacement or close.', schema({**target, 'dirty': {'type': 'boolean'}, 'editVersion': {'type': 'integer', 'minimum': 0}}, [*target, 'dirty'])),
        'canvas.views.status': ('Record browser mount evidence for this renderer; not a package validation receipt.', schema({**target, 'status': {'enum': ['loading', 'ready', 'error']}, 'message': string(2000)})),
        'canvas.views.mediaReport': ('Record untrusted playback evidence for this exact Canvas or inline media view, never a global latest observation.', schema({**target,
            'sequence': {'type': 'integer', 'minimum': 1}, 'renderState': {'enum': ['loading', 'ready', 'error']},
            'playback': {'enum': ['paused', 'playing', 'ended']}, 'presentedFrames': {'type': 'integer', 'minimum': 0},
            'mediaTime': {'type': 'number', 'minimum': 0}, 'detail': string(500)},
            [*target, 'sequence', 'renderState', 'playback', 'presentedFrames', 'mediaTime'])),
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
                    label = 'primary viewer' if view_id == 'primary' else 'inline preview'
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
              or action == 'view.update' and args.get('patch', {}).get('settingsCanvas') is True
              or action in {'canvas.show', 'canvas.openFile', 'canvas.openAttachment', 'canvas.openGif', 'canvas.apps.create', 'smartTools.open'} and args.get('sessionId', client.get('selectedSessionId')) == client.get('selectedSessionId')):
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
            preference.pop('mediaObservation', None)
            preference.pop('mcp', None)
        return row, preference

    def target(self, args):
        if args.get('clientId', self.service.clients.current.get()) != self.service.clients.current.get():
            fail('The artifact view targets another client.', 409)
        row, preference = self.resolve(args['viewId'])
        if args['viewId'] == 'primary' and not (
                self.service.state.get('canvas', {}).get('open') or self.record().get('retained')):
            fail('This artifact view is no longer active.', 409)
        if row['id'] != args['resourceId'] or self.revision(row) != args['resourceRevision'] or preference['generation'] != args['generation']:
            fail('This artifact view changed. Read it again before applying the action.', 409)
        return row, preference

    def bindings(self, identity):
        """Only actual mounted bindings count; saved preferences are not leases."""
        from .service import AppError
        result = []
        for client_id, client in self.service.clients.records.items():
            views = client.get('canvasViews', {})
            ids = [view_id for view_id, binding in views.get('inline', {}).items()
                   if binding.get('resourceId') == identity]
            if client.get('canvas', {}).get('id') == identity and (
                    client.get('canvas', {}).get('open') or views.get('retained')):
                ids.insert(0, 'primary')
            if not ids:
                continue
            with self.service.clients.bind(client_id):
                for view_id in ids:
                    try:
                        summary = self.summary(view_id)
                    except AppError:
                        continue
                    if summary['resourceId'] == identity:
                        result.append({**{key: summary[key] for key in
                            ('viewId', 'resourceId', 'resourceRevision', 'generation', 'selectedVersion', 'dirty')},
                            'clientId': client_id, 'renderStatus': summary.get('activation')})
        return result

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
        from .canvas_reveal import status as reveal_status
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
                'fileReveal': reveal_status(self.service.instance_id) if row.get('path') else None,
                'selectedVersion': row.get('selectedVersion'),
                'publishedVersion': (self.service.state['canvas'].get('publishedVersion', row.get('selectedVersion'))
                    if view_id == 'primary' else self.record()['inline'][view_id].get('publishedVersion',
                        self.record()['inline'][view_id].get('selectedVersion'))),
                'followLatest': row['kind'] in LIVE_KINDS and row.get('selectedVersion') is None,
                'latestRevision': self.artifact(row['id']).get('app', {}).get('revision', self.artifact(row['id']).get('revision', 1)),
                'latestStateRevision': self.artifact(row['id']).get('app', {}).get('stateRevision'),
                'latestVersion': row.get('latestVersion', row.get('app', {}).get('revision', row.get('revision', 1))),
                'versions': [{k: item.get(k) for k in ('version', 'title', 'createdAt')} for item in row.get('app', {}).get('versions', row.get('versions', []))],
                'activation': preference.get('activation'),
                **({'mcpContext': copy.deepcopy(preference.get('mcp', {}))} if row['kind'] == 'mcp-app' else {}),
                **({'app': copy.deepcopy(row['app'])} if row.get('app') else {}),
                'view': copy.deepcopy(self.service.state['canvas'].get('view', {})) if view_id == 'primary' else copy.deepcopy(preference['view']),
                'renderReports': copy.deepcopy(self.service.state['canvas'].get('renderReports', {})) if view_id == 'primary' else copy.deepcopy(preference['renderReports']),
                **{key: copy.deepcopy(preference[key]) for key in ('document', 'interaction', 'events', 'mediaObservation') if key in preference}}

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
        if type(args['version']) is not int or args['version'] < 1:
            fail('Choose a positive integer exact published version.')
        row = self.artifact(args['resourceId'])
        if (row.get('sessionId') != self.service.state.get('selectedSessionId')
                or row.get('workspaceId') != self.service.state.get('selectedWorkspaceId')):
            fail('Open the conversation that contains this artifact first.', 409)
        publications = row.get('publications') or [{'messageId': row.get('messageId'), 'version': latest(row)}]
        if not any(link.get('messageId') == args['messageId'] and link.get('version') == args['version']
                   for link in publications):
            fail('This version was not published in that message.', 409)
        saved = definition(row, args['version'], self.service.db)
        follow = args.get('followLatest', False)
        if follow and saved['kind'] not in LIVE_KINDS:
            fail('This renderer has no qualified live owner.', 409)
        if not capabilities(saved['kind'])['inline']:
            fail('This artifact opens in Canvas.', 409)
        record = self.record()
        inline = record.setdefault('inline', {})
        for identity, binding in list(inline.items()):
            if binding.get('sessionId') != self.service.state.get('selectedSessionId'):
                self.release_inline(identity)
        binding = {'resourceId': row['id'], 'publishedVersion': args['version'],
                   'selectedVersion': None if follow else args['version'], 'followLatest': follow,
                   'messageId': args['messageId'], 'sessionId': row['sessionId'], 'previewId': args['previewId']}
        identity = next((key for key, value in inline.items()
            if all(value.get(field) == binding[field] for field in ('resourceId', 'messageId', 'sessionId', 'previewId'))
            and value.get('publishedVersion', value.get('selectedVersion')) == args['version']), None)
        if identity is None:
            if len(inline) >= 8:
                fail('Close a preview to open another here, or open this artifact in Canvas.', 409)
            identity = 'inline-' + str(uuid.uuid4())
            inline[identity] = binding
        elif inline[identity].get('selectedVersion') != binding['selectedVersion']:
            _, preference = self.resolve(identity)
            if preference['dirty']:
                fail('Finish or cancel this preview edit before changing its version.', 409)
            inline[identity].update(binding)
        return self.summary(identity)

    def follow(self, args, row, preference):
        from .canvas_versions import definition
        if row['kind'] not in LIVE_KINDS:
            fail('This renderer has no qualified live owner.', 409)
        view_id = args['viewId']
        binding = (self.service.state['canvas'] if view_id == 'primary'
                   else self.record()['inline'][view_id])
        published = binding.get('publishedVersion', binding.get('selectedVersion'))
        if published is None:
            fail('This view has no original published version to return to.', 409)
        definition(self.artifact(row['id']), published, self.service.db)
        selected = None if args['followLatest'] else published
        if selected != binding.get('selectedVersion'):
            if preference['dirty']:
                fail('Finish or cancel this view edit before changing its version.', 409)
            if view_id == 'primary':
                from .canvas_library import load
                load(self.service.state, self.service.db, row['id'],
                     version=selected, open_panel=binding.get('open', False))
                binding = self.service.state['canvas']
            else:
                binding['selectedVersion'] = selected
        binding.update(publishedVersion=published, followLatest=args['followLatest'])
        return self.summary(view_id)

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
        indirect = row.get('contentResource') and row['kind'] in {'html', 'babylon', 'canvas-app', 'pdf'}
        body = {} if indirect else resource(self.service.db, row['body']['$resource'])
        if indirect and row.get('outputBinding'):
            body = resource(self.service.db, row['body']['$resource'])
            indirect = False
        from .artifact_presentation import resolve_body
        body = resolve_body(self.service.db, body, row['kind'], row['sessionId'])
        canvas = {**copy.deepcopy(row), **body, 'open': True, 'viewId': view_id,
                  'resourceRevision': self.revision(row), 'generation': preference['generation']}
        binding = self.service.state['canvas'] if view_id == 'primary' else self.record()['inline'][view_id]
        canvas.update(publishedVersion=binding.get('publishedVersion', binding.get('selectedVersion')),
                      followLatest=row['kind'] in LIVE_KINDS and row.get('selectedVersion') is None,
                      latestRevision=self.artifact(row['id']).get('app', {}).get('revision',
                          self.artifact(row['id']).get('revision', 1)))
        canvas.pop('body', None)
        if view_id == 'primary':
            live = self.service.state['canvas']
            canvas.update({key: copy.deepcopy(live[key]) for key in ('view', 'renderReports', 'document', 'interaction', 'mcp', 'events') if key in live})
        else:
            canvas.update({key: copy.deepcopy(preference[key]) for key in ('view', 'renderReports', 'document', 'interaction', 'events') if key in preference})
        if row.get('mcpState'):
            try:
                canvas['mcp'] = resource(self.service.db, row['mcpState']['$resource'])
            except (KeyError, ValueError, OSError, TypeError):
                canvas.pop('mcp', None)
                canvas.setdefault('renderReports', {})['stored-tool-state'] = {
                    'status': 'error', 'message': 'The saved tool state is unavailable. No tool work was replayed.'}
            if row.get('selectedVersion') is None:
                if canvas.get('mcp'):
                    canvas['mcp'].update(copy.deepcopy(preference.get('mcp', {})))
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
        if action == 'canvas.views.follow':
            return self.follow(args, row, preference), []
        if action == 'canvas.views.mediaReport':
            if origin != 'ui':
                fail('Media observations must come from the authenticated viewing client.', 403)
            if row['kind'] not in {'gif', 'mp4'}:
                fail('Choose an exact retained media view.', 409)
            from .output_media import validate_media_reference
            canvas = self.canvas(args['viewId'])
            reference = canvas.get('mediaReference', {})
            output = self.service.outputs.record(row['sessionId'], reference.get('item', {}).get('id'))
            validate_media_reference(reference, output, row['sessionId'], kind=row['kind'])
            previous = preference.get('mediaObservation', {})
            if (previous.get('generation') == args['generation']
                    and args['sequence'] <= previous.get('sequence', 0)):
                return {'status': 'unchanged'}, []
            preference['mediaObservation'] = {key: copy.deepcopy(args[key]) for key in
                ('viewId', 'resourceId', 'resourceRevision', 'generation', 'sequence',
                 'renderState', 'playback', 'presentedFrames', 'mediaTime', 'detail') if key in args}
            return {'status': 'observed', 'evidence': 'untrusted-client-playback'}, []
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
            if canvas['kind'] in {'gif', 'mp4'}:
                fail('Use Download original on the retained GIF.' if canvas['kind'] == 'gif'
                     else 'Use Download original on the retained video.', 409)
            from .canvas_downloads import filename
            if action == 'canvas.copy' and canvas['kind'] == 'pdf':
                fail('Select text in the PDF to copy it, or download the original file.')
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
