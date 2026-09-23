"""Versioned native UI packages and client-scoped shell compositions.

Native modules are trusted app code, not a security sandbox. Validation is a
host-run compatibility check against the exact content-addressed artifact.
Conversation data and runtime ownership remain with AppService/Foundation.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from jsonschema import validate, ValidationError
from . import shell_components as components

API = '1.0'
PROFILE = 'trusted-native-navigation-v1'
IDENTITY = {'type': 'string', 'pattern': r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$'}
CAPABILITIES = ['navigation.read', 'navigation.select', 'workspaces.manage', 'chats.manage', 'locations.read', 'history.refresh']
MANIFEST = {'type': 'object', 'additionalProperties': False, 'required': ['id', 'version', 'apiVersion', 'profile', 'stateSchema', 'capabilities'], 'properties': {
    'id': IDENTITY, 'version': {'type': 'string', 'pattern': r'^\d+\.\d+\.\d+$'},
    'apiVersion': {'const': API}, 'profile': {'const': PROFILE},
    'stateSchema': IDENTITY, 'capabilities': {'type': 'array', 'uniqueItems': True, 'maxItems': 6, 'contains': {'const': 'navigation.read'}, 'items': {'enum': CAPABILITIES}},
}}
RENDERER_MANIFEST = copy.deepcopy(MANIFEST)
RENDERER_MANIFEST['required'].append('resourceKinds')
RENDERER_MANIFEST['properties'].update({
    'profile': {'const': 'trusted-native-renderer-v1'},
    'label': {'type': 'string', 'minLength': 1, 'maxLength': 100},
    'capabilities': {'type': 'array', 'uniqueItems': True, 'maxItems': 3,
        'contains': {'const': 'canvas.resource.read'},
        'items': {'enum': ['canvas.resource.read', 'canvas.view.update', 'canvas.view.report']}},
    'resourceKinds': {'type': 'array', 'uniqueItems': True, 'minItems': 1, 'maxItems': 12,
        'items': {'enum': ['markdown', 'text', 'code', 'json', 'jsonl', 'image', 'html', 'babylon', 'mermaid', 'dot', 'a2ui', 'browser']}},
})
COMPONENT_MANIFEST = copy.deepcopy(MANIFEST)
COMPONENT_MANIFEST['required'].extend(['label', 'slots'])
COMPONENT_MANIFEST['properties'].update({
    'profile': {'const': components.PROFILE},
    'label': {'type': 'string', 'minLength': 1, 'maxLength': 100},
    'slots': {'type': 'array', 'minItems': 1, 'maxItems': len(components.SLOTS), 'uniqueItems': True,
              'items': {'enum': list(components.SLOTS)}},
    'capabilities': {'type': 'array', 'uniqueItems': True, 'maxItems': len(components.CAPABILITIES),
                     'contains': {'const': 'shell.read'}, 'items': {'enum': components.CAPABILITIES}},
})
MANIFEST = {'allOf': [
    {'type': 'object', 'properties': {'profile': {'enum': [PROFILE, 'trusted-native-renderer-v1', components.PROFILE]}}, 'required': ['profile']},
    {'if': {'properties': {'profile': {'const': PROFILE}}}, 'then': MANIFEST,
     'else': {'if': {'properties': {'profile': {'const': 'trusted-native-renderer-v1'}}}, 'then': RENDERER_MANIFEST, 'else': COMPONENT_MANIFEST}},
]}
INSTANCE = {'type': 'object', 'additionalProperties': False, 'required': ['id', 'package', 'slot'], 'properties': {
    'id': IDENTITY, 'package': {'type': 'string', 'maxLength': 100}, 'slot': {'enum': ['navigation', *components.SLOTS]},
    'scope': {'type': 'object', 'additionalProperties': False, 'properties': {'workspaceId': IDENTITY, 'mode': {'enum': ['follow', 'pinned', 'all']}}, 'required': ['mode']},
    'hideWhen': {'type': 'object', 'additionalProperties': False, 'required': ['instanceId', 'navChatScope'], 'properties': {'instanceId': IDENTITY, 'navChatScope': {'enum': ['all', 'workspace']}}},
}}
COMPOSITION = {'type': 'object', 'additionalProperties': False, 'required': ['instances', 'presentation'], 'properties': {
    'instances': {'type': 'array', 'maxItems': 64, 'items': INSTANCE},
    'disabledSlots': {'type': 'array', 'uniqueItems': True, 'items': {'enum': list(components.SLOTS)}},
    'presentation': {'type': 'object', 'additionalProperties': False, 'properties': {
        'scheme': {'enum': ['light', 'dark', 'system']}, 'layout': {'enum': ['balanced', 'conversation', 'work']},
        'executionDetail': {'enum': ['minimal', 'standard', 'detailed']},
        'decorations': {'type': 'boolean'},
        'density': {'enum': ['comfortable', 'compact']}, 'accent': {'type': 'string', 'pattern': '^#[0-9a-fA-F]{6}$'},
    }},
}}
DEFAULT = {'instances': [
    {'id': 'workspaces', 'package': 'builtin.workspaces', 'slot': 'navigation', 'hideWhen': {'instanceId': 'chats', 'navChatScope': 'all'}},
    {'id': 'chats', 'package': 'builtin.chats', 'slot': 'navigation'},
], 'presentation': {}}
BUILTINS = {name: {'id': name, 'version': '1.0.0', 'apiVersion': API, 'profile': PROFILE, 'stateSchema': 'navigation-v1', 'capabilities': CAPABILITIES}
            for name in ['builtin.workspaces', 'builtin.chats']}
BUILTINS.update(components.BUILTINS)
VIEW_KEYS = {'navSimple', 'navSort', 'navLocationFilter', 'navWorkspaceList', 'navStatusFilter', 'navArchive', 'navCollection', 'navWorkspaceMode', 'navFilter', 'navChatScope', 'navChatPage', 'navWorkspacePath', 'navWorkspaceFilter', 'navWorkspacePage', 'navWorkspaceAncestorsOpen', 'workspaceDraft', 'locationPicker'}
EDIT_STATE = {'type': 'object', 'additionalProperties': False, 'properties': {
    'mode': {'enum': ['add', 'rename', 'remove', 'chat-rename', 'chat-delete']}, 'id': {'type': 'string', 'maxLength': 200},
    'path': {'type': 'string', 'maxLength': 4000}, 'name': {'type': 'string', 'maxLength': 200},
}}
COMMAND_CAPABILITIES = {
    'session.select': 'navigation.select', 'workspace.select': 'navigation.select',
    'workspace.prepare': 'workspaces.manage', 'workspace.add': 'workspaces.manage', 'workspace.list': 'navigation.read',
    'workspace.create': 'workspaces.manage', 'workspace.rename': 'workspaces.manage', 'workspace.remove': 'workspaces.manage',
    'session.draft': 'chats.manage', 'session.create': 'chats.manage', 'session.rename': 'chats.manage', 'session.naming': 'chats.manage', 'session.delete': 'chats.manage', 'session.deletePreview': 'chats.manage', 'session.pin': 'chats.manage',
    'session.archive': 'chats.manage', 'session.restore': 'chats.manage', 'session.pinOrder': 'chats.manage',
    'locations.create': 'workspaces.manage', 'locations.list': 'locations.read', 'history.refresh': 'history.refresh',
}


def definitions(schema, string):
    client = {'clientId': IDENTITY}
    generation = {'generation': {'type': 'integer', 'minimum': 0}}
    change = {**client, 'changeId': IDENTITY, 'expectedRevision': {'type': 'integer', 'minimum': 0}}
    return {
        'shell.inspect': ('Inspect client composition, package manifests, validation and browser activation evidence.', schema(client)),
        'shell.query': ('Read one module instance\'s bounded snapshot, using its declared data capabilities.', schema({**client, 'instanceId': IDENTITY})),
        'shell.packages.stage': ('Stage a trusted native navigation, component or artifact-renderer package; this does not execute or activate it.', schema({'manifest': MANIFEST, 'source': string(250000)})),
        'shell.packages.validate': ('Run host-owned import and browser lifecycle checks of the staged digest. Requires the local validator toolchain.', schema({'digest': string(64)})),
        'shell.changes.prepare': ('Validate a proposed client composition and return a reviewable change; does not activate it.', schema({**client, 'composition': COMPOSITION, 'expectedRevision': {'type': 'integer', 'minimum': 0}})),
        'shell.changes.preview': ('Preview a prepared composition in the target client. Browser activation is separately reported.', schema(change)),
        'shell.changes.apply': ('Apply a prepared composition at its expected revision; preserve compatible instances.', schema(change)),
        'shell.changes.revert': ('Restore the composition saved before this change, checking the current revision.', schema(change)),
        'shell.recover': ('Restore the default or last successfully rendered composition for this client.', schema({**client, 'target': {'enum': ['default', 'lastGood']}, 'expectedRevision': {'type': 'integer', 'minimum': 0}})),
        'shell.view.update': ('Update only this module instance\'s durable UI state, without changing conversation selection.', schema({**client, **generation, 'instanceId': IDENTITY, 'patch': {'type': 'object', 'maxProperties': 12}, 'dirty': {'type': 'boolean'}}, ['clientId', 'instanceId', 'patch'])),
        'shell.command': ('Execute a declared module capability through the normal app command handler.', schema({**client, **generation, 'instanceId': IDENTITY, 'action': string(100), 'args': {'type': 'object'}}, ['clientId', 'instanceId', 'action', 'args'])),
        'shell.report': ('Report rendered module status from a browser; display evidence, never package validation.', schema({**client, 'revision': {'type': 'integer'}, 'previewId': {'type': ['string', 'null'], 'maxLength': 100}, 'instances': {'type': 'object', 'maxProperties': 64, 'additionalProperties': {'enum': ['ready', 'error', 'loading', 'inactive']}}, 'message': string(2000)}, ['clientId', 'revision', 'instances'])),
    }


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def fail(message, status=400):
    from .service import AppError
    raise AppError(message, status)


class ShellModules:
    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.directory = service.data_dir / 'shell-packages'
        self.directory.mkdir(exist_ok=True, mode=0o700)
        self.db.execute('CREATE TABLE IF NOT EXISTS shell_records (kind TEXT, id TEXT, value TEXT NOT NULL, PRIMARY KEY(kind,id))')
        self.db.commit()
        self.validations = {}
        self.change_tokens = {}

    def get(self, kind, identity, default=None):
        row = self.db.execute('SELECT value FROM shell_records WHERE kind=? AND id=?', (kind, identity)).fetchone()
        return json.loads(row[0]) if row else copy.deepcopy(default)

    def put(self, kind, identity, value):
        self.db.execute('INSERT OR REPLACE INTO shell_records VALUES (?,?,?)', (kind, identity, encoded(value)))
        self.db.commit()

    def client(self, identity):
        saved = self.get('client', identity)
        if saved is not None:
            return saved
        # Migrate legacy navigation defaults once. Subsequent commands in
        # another client must not overwrite this client's filters or pages.
        baseline = {key: copy.deepcopy(value) for key, value in self.service.state['view'].items() if key in VIEW_KEYS | {'navWorkspaceBrowseFor'}}
        saved = {'revision': 0, 'composition': copy.deepcopy(DEFAULT), 'baselineView': baseline, 'views': {}, 'preview': None, 'lastGood': copy.deepcopy(DEFAULT), 'reported': None}
        self.put('client', identity, saved)
        return saved

    def notify(self, identity):
        # Ordinary snapshots retain this token if a full queue drops the shell
        # event. A host epoch also repairs reconnects after a restart.
        self.change_tokens[identity] = self.change_tokens.get(identity, 0) + 1
        self.service._client_snapshots.pop(identity, None)
        for queue in self.service.queues:
            if self.service.queue_sessions.get(queue) is not None:
                continue
            if self.service.queue_clients.get(queue) not in {None, identity}:
                continue
            if queue.full():
                queue.get_nowait()
            queue.put_nowait({'shellClientId': identity})

    def change_token(self, identity):
        return f'{self.service.instance_id}:{self.change_tokens.get(identity, 0)}'

    def manifest(self, package, *, validated=True):
        if package in BUILTINS:
            return BUILTINS[package]
        record = self.get('package', package)
        if not record:
            fail('Unknown shell package.', 404)
        self.source(package)
        receipt = record.get('validation') or {}
        if validated and (receipt.get('status') != 'passed' or receipt.get('hostFingerprint') != self.host_fingerprint()):
            fail('Package requires current host validation.', 409)
        return record['manifest']

    def source(self, digest):
        if not re.fullmatch('[0-9a-f]{64}', digest):
            fail('Invalid package digest.')
        record = self.get('package', digest)
        path = self.directory / (digest + '.mjs')
        if not record or not path.is_file():
            fail('Shell package is missing.', 404)
        source = path.read_text()
        actual = hashlib.sha256((encoded(record['manifest']) + '\n' + source).encode()).hexdigest()
        if actual != digest:
            fail('Package contents changed after staging; stage and validate a new artifact.', 409)
        return source

    def host_fingerprint(self):
        # Receipts expire when the actual harness/runtime or validator changes.
        paths = [Path(__file__), Path(components.__file__), Path(__file__).with_name('canvas_views.py'), Path(__file__).with_name('shell_validator.mjs'), *sorted((Path(__file__).parent / 'static').rglob('*.js')), Path(__file__).parent / 'static/shell-validation.html']
        signature = [(str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in paths if path.exists()]
        if getattr(self, '_host_signature', None) != signature:
            digest = hashlib.sha256((API + PROFILE).encode())
            for path in paths:
                if path.exists():
                    digest.update(path.read_bytes())
            self._host_signature, self._host_digest = signature, digest.hexdigest()
        return self._host_digest

    def instance(self, client, identity):
        composition = client['preview']['composition'] if client['preview'] else client['composition']
        instance = next((item for item in components.resolved(composition) if item['id'] == identity), None)
        if instance is None:
            fail('Module instance is not in this composition.', 404)
        return instance

    def check_composition(self, composition):
        instances = components.resolved(composition)
        ids = [item['id'] for item in instances]
        if len(set(ids)) != len(ids):
            fail('Module instance IDs must be unique, including inherited built-ins.')
        slots = {'navigation': {'maximum': 12}, **components.SLOTS}
        for name, slot in slots.items():
            rows = [item for item in instances if item['slot'] == name]
            if len(rows) > slot['maximum']:
                fail('Too many components in ' + name + '.')
            if rows and name in composition.get('disabledSlots', []):
                fail('A disabled slot cannot also contain components.')
        for item in instances:
            manifest = self.manifest(item['package'])
            if item['slot'] == 'navigation':
                if manifest['profile'] != PROFILE:
                    fail('The navigation slot requires a navigation package.')
                scope = item.get('scope', {})
                if scope.get('mode') == 'pinned' and not scope.get('workspaceId'):
                    fail('Pinned modules require a workspaceId.')
            elif manifest['profile'] != components.PROFILE or item['slot'] not in manifest['slots']:
                fail('This package does not support the requested component slot.')
            elif 'scope' in item or 'hideWhen' in item:
                fail('Navigation bindings are only supported in navigation slots.')

    def transition(self, client, composition):
        """Return a deferral, never discard a live form or incompatible state."""
        self.check_composition(composition)
        previous = client['preview']['composition'] if client['preview'] else client['composition']
        by_id = {item['id']: item for item in components.resolved(composition)}
        for new in by_id.values():
            saved = client['views'].get(new['id'], {})
            if saved.get('stateSchema') and saved['stateSchema'] != self.manifest(new['package'])['stateSchema']:
                return {'status': 'deferred', 'instanceId': new['id'], 'reason': 'State schema migration is not supported; use a new instance ID to keep the saved state intact.'}
        for old in components.resolved(previous):
            new = by_id.get(old['id'])
            view = client['views'].get(old['id'], {})
            changed = new != old
            if changed and (view.get('dirty') or view.get('view', {}).get('workspaceDraft', {}).get('mode')):
                return {'status': 'deferred', 'instanceId': old['id'], 'reason': 'Finish or cancel this module\'s edit first.'}
            if new and old['package'] != new['package']:
                before = self.manifest(old['package'], validated=False)
                after = self.manifest(new['package'])
                if before['stateSchema'] != after['stateSchema']:
                    return {'status': 'deferred', 'instanceId': old['id'], 'reason': 'State schema migration is not supported; the current module remains active.'}
        return None

    def inspect(self, identity, *, snapshots=False, recovery=False):
        client = self.client(identity)
        composition = DEFAULT if recovery else client['preview']['composition'] if client['preview'] else client['composition']
        result = {'clientId': identity, 'apiVersion': API, **client, 'effectiveComposition': composition, 'resolvedInstances': components.resolved(composition),
                  'slots': {'navigation': {'label': 'Navigation', 'maximum': 12, 'defaults': ['builtin.workspaces', 'builtin.chats']}, **copy.deepcopy(components.SLOTS)},
                  'componentCommands': components.commands(), 'packages': {}}
        for instance in components.resolved(composition):
            package = instance['package']
            try:
                result['packages'][package] = {'manifest': self.manifest(package), 'url': None if package in BUILTINS else f'/api/shell/packages/{package}.mjs'}
            except Exception as exc:
                result['packages'][package] = {'error': str(exc)}
        if snapshots:
            result['snapshots'] = {}
            for item in components.resolved(composition):
                if not result['packages'][item['package']].get('error'):
                    result['snapshots'][item['id']] = self.snapshot(client, item)
        else:
            result['registry'] = {key: value for key, value in BUILTINS.items()}
            result['staged'] = [json.loads(row[0]) for row in self.db.execute("SELECT value FROM shell_records WHERE kind='package' ORDER BY rowid DESC LIMIT 100")]
        return result

    def snapshot(self, client, instance):
        value = self.navigation(client, instance) if instance['slot'] == 'navigation' else components.snapshot(self, client, instance)
        return {**value, 'generation': client['views'].get(instance['id'], {}).get('generation', 0)}

    def scoped_state(self, client, instance):
        state = self.service.state
        view = dict(client.get('baselineView', {}))
        view.update(client['views'].get(instance['id'], {}).get('view', {}))
        scope = instance.get('scope', {})
        workspace_id = scope.get('workspaceId') if scope.get('mode') == 'pinned' else state.get('selectedWorkspaceId')
        from .managed_chats import is_managed
        selected = next((s for s in state.get('sessions', []) if s['id'] == state.get('selectedSessionId')), {})
        if scope.get('mode') == 'all' or scope.get('mode') != 'pinned' and is_managed(selected) and view.get('navChatScope', 'workspace') == 'workspace' and not view.get('navWorkspaceList'):
            view['navChatScope'] = 'all'
        return {**state, 'selectedWorkspaceId': workspace_id, 'view': view}

    def navigation(self, client, instance):
        from .conversation_library import projection as organization_projection
        state = self.service.state
        projections = self.service.projections
        scoped = {**self.scoped_state(client, instance), 'attention': projections.attention(state)}
        view, workspace_id = scoped['view'], scoped['selectedWorkspaceId']
        chat_page = projections.chats(scoped)
        workspace = next((row for row in state.get('workspaces', []) if row['id'] == workspace_id and row.get('available') is True), None)
        from .workspace_placement import listing
        home_view = {**view, 'navChatScope': 'all', 'navArchive': 'active', 'navCollection': None,
                     'navFilter': '', 'navStatusFilter': 'all', 'navLocationFilter': 'all', 'navSort': 'activity'}
        home_view.pop('navChatPage', None)
        pinned_scope = instance.get('scope', {}).get('mode') == 'pinned'
        home = chat_page if pinned_scope else projections.chats({**scoped, 'view': home_view})
        overview = {'items': [copy.deepcopy(workspace)] if workspace else [], 'nextOffset': None} if pinned_scope else listing(self.service, {'query': '', 'offset': 0})
        # Only summaries and the selected registration leave this query. No
        # transcripts, draft text, credentials, runtime mounts or full catalog.
        return {'view': view, 'selectedWorkspaceId': workspace_id, 'selectedSessionId': state.get('selectedSessionId'),
                'workspaces': [copy.deepcopy(workspace)] if workspace else [],
                'workspaceDefaults': copy.deepcopy(state.get('workspaceDefaults', {})),
                'settings': {'workspaces': copy.deepcopy(state.get('settings', {}).get('workspaces', {}))},
                'pinnedSessionIds': list(state.get('pinnedSessionIds', [])),
                'homeNavigation': home, 'workspaceOverview': overview,
                'chatNavigation': chat_page, 'workspaceExplorer': projections.workspaces(scoped),
                'conversationOrganization': organization_projection(state, {row['id'] for row in chat_page['items']}),
                'library': {'bounded': True, 'workspaceCount': sum(row.get('available') is True for row in state.get('workspaces', []))},
                'sharedHistory': {key: state.get('sharedHistory', {}).get(key) for key in ['loading', 'refreshing', 'error', 'issues', 'issueCount']},
                'attention': {'sessions': {row['id']: scoped['attention'].get('sessions', {}).get(row['id'], 0) for row in chat_page['items']}},
                'locationListing': copy.deepcopy(state.get('locationListing')) if 'locations.read' in self.manifest(instance['package'], validated=False)['capabilities'] else None,
                'actionStatus': {name: state.get('actionStatus', {}).get(name) for name in ('locations.list', 'locations.create')}}

    async def validate_package(self, digest):
        self.source(digest)
        record = self.get('package', digest)
        root = Path(__file__).parent.parent
        toolchain = Path(os.environ.get('AMPLIFIER_SHELL_NODE_MODULES', root / 'frontend/node_modules')).resolve()
        node = shutil.which('node')
        if not node or not (toolchain / 'playwright').is_dir() or not (Path(__file__).parent / 'static/shell-validation.html').exists():
            fail('Host validation requires Node, Playwright with Chromium, and a production frontend build. No module was approved.', 503)
        fingerprint = self.host_fingerprint()
        process = await asyncio.create_subprocess_exec(node, str(Path(__file__).with_name('shell_validator.mjs')), str(toolchain), str(self.directory / (digest + '.mjs')), encoded(record['manifest']), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
            result = json.loads(stdout) if process.returncode == 0 else {'status': 'failed', 'error': stderr.decode()[-2000:] or stdout.decode()[-2000:]}
        except TimeoutError:
            process.kill()
            await process.wait()
            result = {'status': 'failed', 'error': 'Host validation exceeded its 45 second limit.'}
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        except Exception as exc:
            result = {'status': 'failed', 'error': str(exc)}
        self.source(digest)
        if fingerprint != self.host_fingerprint():
            fail('Host changed during validation; retry.', 409)
        receipt = {'id': str(uuid.uuid4()), 'digest': digest, 'hostFingerprint': fingerprint, 'apiVersion': API, 'profile': record['manifest']['profile'], 'validatedAt': time.time(), **result}
        record['validation'] = receipt
        self.put('package', digest, record)
        if record['manifest']['profile'] == 'trusted-native-renderer-v1':
            async with self.service.lock:
                self.service._publish()
        return receipt

    async def dispatch(self, action, args, origin, command_id):
        bound = self.service.clients.current.get()
        identity = args.get("clientId")
        if bound is None and identity in self.service.clients.records:
            with self.service.clients.bind(identity):
                return await self.dispatch(action, args, origin, command_id)
        if bound is not None and args.get("clientId", bound) != bound:
            fail("The shell command targets a different client.", 400)
        # Separate receipts avoid coupling composition CAS to streamed chat
        # revisions. There is no await inside the synchronous commit section.
        fingerprint = hashlib.sha256(encoded([action, args, origin]).encode()).hexdigest()
        cached = self.get('command', command_id) if command_id else None
        if cached:
            if cached['fingerprint'] != fingerprint:
                fail('Command ID was already used with different arguments.', 409)
            return cached['receipt']
        identity = args.get('clientId')
        client = self.client(identity) if identity else None
        changed = False
        if action in {'shell.command', 'shell.view.update'}:
            instance = self.instance(client, args['instanceId'])
            current = client['views'].get(instance['id'], {}).get('generation', 0)
            if (instance['slot'] != 'navigation' or 'generation' in args) and args.get('generation') != current:
                fail('This component was replaced. Read its current snapshot before acting.', 409)
        if action == 'shell.inspect':
            result = self.inspect(identity)
        elif action == 'shell.query':
            result = self.snapshot(client, self.instance(client, args['instanceId']))
        elif action == 'shell.packages.stage':
            if args['manifest']['id'].startswith('builtin.'):
                fail('The builtin namespace is reserved.')
            digest = hashlib.sha256((encoded(args['manifest']) + '\n' + args['source']).encode()).hexdigest()
            path = self.directory / (digest + '.mjs')
            if not self.get('package', digest):
                path.write_text(args['source'])
                path.chmod(0o600)
                self.put('package', digest, {'digest': digest, 'manifest': args['manifest'], 'validation': None})
            self.source(digest)
            result = self.get('package', digest)
        elif action == 'shell.packages.validate':
            digest = args['digest']
            if digest not in self.validations:
                task = asyncio.create_task(self.validate_package(digest))
                self.service.tasks.add(task)
                task.add_done_callback(self.service.tasks.discard)
                self.validations[digest] = task
                task.add_done_callback(lambda done: self.validations.pop(digest, None))
            result = await asyncio.shield(self.validations[digest])
        elif action == 'shell.command':
            instance = self.instance(client, args['instanceId'])
            if instance['slot'] != 'navigation':
                result = await components.command(self, client, instance, args, origin, command_id)
                if command_id:
                    self.put('command', command_id, {'fingerprint': fingerprint, 'receipt': result})
                return result
            capability = COMMAND_CAPABILITIES.get(args['action'])
            if not capability or capability not in self.manifest(instance['package'])['capabilities']:
                fail('Module has not declared this capability.', 403)
            receipt = await self.service.dispatch(args['action'], args['args'], origin=origin, command_id=command_id)
            if args['action'] in {'workspace.select', 'workspace.create', 'workspace.add'} and instance['package'] == 'builtin.workspaces':
                client = self.client(identity)
                composition = client['preview']['composition'] if client.get('preview') else client['composition']
                target = instance.get('hideWhen', {}).get('instanceId')
                if any(row['id'] == target and row['package'] == 'builtin.chats' for row in composition['instances']):
                    item = client['views'].setdefault(target, {'view': {}, 'dirty': False})
                    item['view'].update(navChatScope='workspace', navWorkspaceList=False, navFilter='', navStatusFilter='all')
                    self.put('client', identity, client)
                    self.notify(identity)
            if args['action'] == 'session.pin':
                # Pinning intentionally reveals the pin in this list. Other
                # lists retain their own current page and filter.
                client = self.client(identity)
                item = client['views'].setdefault(args['instanceId'], {'view': {}, 'dirty': False})
                page = self.navigation(client, self.instance(client, args['instanceId']))['chatNavigation']
                item['view']['navChatPage'] = {**page['scope'], 'index': 0}
                self.put('client', identity, client)
                self.notify(identity)
            return receipt
        elif action == 'shell.view.update':
            instance = self.instance(client, args['instanceId'])
            patch = args['patch']
            if instance['slot'] == 'navigation':
                if set(patch) - VIEW_KEYS or len(encoded(patch)) > 16000:
                    fail('Unsupported or oversized module view state.')
                from .chat_navigation import view_patch as chat_patch
                from .workspace_navigation import view_patch as workspace_patch
                try:
                    if 'workspaceDraft' in patch:
                        validate(patch['workspaceDraft'], EDIT_STATE)
                    if 'locationPicker' in patch:
                        validate(patch['locationPicker'], {'type': ['object', 'null'], 'additionalProperties': False, 'properties': {'controlId': {'type': 'string', 'maxLength': 200}, 'path': {'type': 'string', 'maxLength': 4000}}})
                    if 'navWorkspaceAncestorsOpen' in patch:
                        validate(patch['navWorkspaceAncestorsOpen'], {'type': 'boolean'})
                    patch = chat_patch(workspace_patch(self.scoped_state(client, instance), patch))
                except (ValueError, ValidationError) as exc:
                    fail(str(exc))
            item = client['views'].setdefault(args['instanceId'], {'view': {}, 'dirty': False})
            if len(encoded({**item['view'], **patch})) > 16000:
                fail('Module view state exceeds 16 KB.')
            item['view'].update(patch)
            if 'dirty' in args:
                item['dirty'] = args['dirty']
            self.put('client', identity, client)
            result = {'status': 'updated'}
            changed = True
        elif action == 'shell.report':
            preview_id = client['preview']['id'] if client['preview'] else None
            if args['revision'] != client['revision'] or args.get('previewId') != preview_id:
                result = {'status': 'stale'}
            else:
                effective = client['preview']['composition'] if client['preview'] else client['composition']
                ready = (all(args['instances'].get(item['id']) == 'ready' for item in effective['instances'])
                         and not any(value in {'error', 'loading'} for value in args['instances'].values()))
                client['reported'] = {**args, 'at': time.time(), 'status': 'ready' if ready else 'incomplete'}
                if ready and not client['preview']:
                    client['lastGood'] = client['composition']
                self.put('client', identity, client)
                result = client['reported']
        else:
            if args['expectedRevision'] != client['revision']:
                fail('Shell composition changed. Inspect and prepare against its current revision.', 409)
            if action == 'shell.changes.prepare':
                self.check_composition(args['composition'])
                change_id = str(uuid.uuid4())
                result = {'id': change_id, 'clientId': identity, 'baseRevision': client['revision'], 'before': client['composition'], 'composition': args['composition'], 'status': 'prepared'}
                self.put('change', change_id, result)
            else:
                if action == 'shell.recover':
                    composition = DEFAULT if args['target'] == 'default' else client['lastGood']
                else:
                    change = self.get('change', args['changeId'])
                    if not change or change['clientId'] != identity:
                        fail('Prepared change not found for this client.', 404)
                    if action != 'shell.changes.revert' and change['baseRevision'] != client['revision']:
                        fail('Prepared change is stale.', 409)
                    composition = change['before'] if action == 'shell.changes.revert' else change['composition']
                deferred = self.transition(client, composition) if action != 'shell.recover' else None
                if deferred:
                    return {'accepted': False, 'result': deferred, 'error': deferred['reason'], 'status': 409, 'code': 'shell_change_deferred'}
                if action == 'shell.recover':
                    self.check_composition(composition)
                previous = client['preview']['composition'] if client.get('preview') else client['composition']
                before = {item['id']: item for item in components.resolved(previous)}
                after = {item['id']: item for item in components.resolved(composition)}
                for instance_id in before.keys() | after.keys():
                    if before.get(instance_id) != after.get(instance_id) or action == 'shell.recover':
                        view = client['views'].setdefault(instance_id, {'view': {}, 'dirty': False})
                        view['generation'] = view.get('generation', 0) + 1
                        source = after.get(instance_id) or before.get(instance_id)
                        manifest = BUILTINS.get(source['package']) or (self.get('package', source['package']) or {}).get('manifest', {})
                        state_schema = manifest.get('stateSchema')
                        if action == 'shell.recover' and view.get('stateSchema') not in {None, state_schema}:
                            view['view'] = {}
                        if state_schema:
                            view['stateSchema'] = state_schema
                        if action == 'shell.recover':
                            view['dirty'] = False
                            view['view'].pop('workspaceDraft', None)
                if action == 'shell.changes.preview':
                    client['preview'] = {'id': args['changeId'], 'composition': composition}
                else:
                    client['composition'] = composition
                    client['preview'] = None
                    client['revision'] += 1
                client['reported'] = None
                self.put('client', identity, client)
                result = {'status': 'awaiting-browser', 'revision': client['revision'], 'composition': composition}
                changed = True
        receipt = {'accepted': True, 'result': result}
        if command_id:
            self.put('command', command_id, {'fingerprint': fingerprint, 'receipt': receipt})
        if changed:
            self.notify(identity)
        return receipt
