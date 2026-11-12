"""Production complete profiles; browser storage owns retained local values.

Older renderer records are inert, not migration inputs. Missing Global values
are virtual defaults; only a separate durable identity is created on first use.
"""
from __future__ import annotations

import copy
import json
import re
import uuid

from jsonschema import validate, ValidationError

from .errors import AppError
from . import theme_library, themes

PREFERENCES_KEY = 'shared-reference-v2'
MISSING = object()
DEFAULTS = {
    'appearanceId': 'builtin:default', 'scheme': 'system', 'layout': 'balanced',
    'executionDetail': 'standard', 'interfaceDetail': 'standard',
    'executionVisibility': {}, 'executionMetrics': ['elapsedSeconds', 'grossInputTokens',
        'outputTokens', 'grossTotalTokens', 'cacheReadTokens', 'cacheWriteTokens',
        'reasoningTokens', 'voiceSeconds', 'costUsd', 'calls'],
    'conversationStatus': False, 'workStartView': 'appearance',
    'collapseInterimReplies': False, 'planPlacement': 'composer',
    'decorations': True, 'density': 'comfortable',
}


def compose_values(*layers):
    """Merge patches within one complete profile, never across sources."""
    values = copy.deepcopy(DEFAULTS)
    for layer in layers:
        visibility = {**values['executionVisibility'], **layer.get('executionVisibility', {})}
        values.update(copy.deepcopy(layer))
        values['executionVisibility'] = visibility
    return values


def patch_values(values, patch):
    """Apply an admitted edit, not hydration, source selection or Undo."""
    changed_look = patch.get('appearanceId', values['appearanceId']) != values['appearanceId']
    updated = compose_values(values, patch)
    if patch.get('accent', ...) is None or changed_look and 'accent' not in patch:
        updated.pop('accent', None)
    return updated


def fields(*, patch=False):
    from .shell_modules import COMPOSITION
    result = {**copy.deepcopy(COMPOSITION['properties']['presentation']['properties']),
              'appearanceId': {'type': 'string', 'minLength': 1, 'maxLength': 240}}
    if patch:
        result['accent'] = {'anyOf': [result['accent'], {'type': 'null'}]}
    return result


def definitions(schema, string):
    target = {'clientId': string(100)}
    revision = {'type': 'integer', 'minimum': 0}
    patch = schema(fields(patch=True), [])
    profile = schema(fields(), list(DEFAULTS))
    local_cas = {**target, 'generation': string(100), 'expectedRevision': revision,
                 'expectedStorageVersion': string(100)}
    update = schema({**local_cas, 'scope': {'enum': ['shared', 'client']},
                     'patch': patch}, ['scope', 'expectedRevision', 'patch'])
    update['if'] = {'properties': {'scope': {'const': 'client'}}}
    update['then'] = {'required': ['generation', 'expectedStorageVersion']}
    return {
        'appearance.preferences.read': ('Read the active complete profile and bounded browser mirror; never editor drafts.', schema(target, [])),
        'appearance.preferences.select': ('Select a whole profile after storage ACK; first browser selection copies the current Global profile.',
            schema({**local_cas, 'activeScope': {'enum': ['shared', 'client']},
                    'expectedSharedRevision': revision},
                   ['generation', 'activeScope', 'expectedRevision', 'expectedStorageVersion'])),
        'appearance.preferences.update': ('Patch the captured profile by CAS. Changing appearance drops its old accent override unless a new color is explicit; accent:null removes only the override. Browser changes commit only after storage ACK.', update),
        'appearance.preferences.undo': ('Conditionally reverse a preference change; never cancels a preview.', schema({**target, 'undoId': string(100)}, ['undoId'])),
        'appearance.client.report': ('Browser-only generation-bound complete storage/render acknowledgement; no shared fallback on failure.',
            schema({**target, 'generation': string(100), 'revision': revision,
                    'activeScope': {'enum': ['shared', 'client']},
                    'values': {'anyOf': [profile, {'type': 'null'}]},
                    'storageVersion': string(100),
                    'status': {'enum': ['ready', 'stored', 'failed']}, 'commandId': string(100),
                    'error': string(240), 'renderedCompositionId': string(64)},
                   ['generation', 'revision', 'storageVersion', 'activeScope', 'values', 'status'])),
        'appearance.preview': ('Validate a named Reference delta for this document only; awaiting-browser is not rendered.',
            schema({**target, 'name': string(100), 'css': string(theme_library.LIMIT)}, ['name', 'css'])),
        'appearance.preview.cancel': ('Cancel only this document preview, preserving preferences and editor work.', schema(target, [])),
    }


class AppearancePreferences:
    def __init__(self, service):
        self.service = service
        self.shell = service.shell
        self.mirrors = {}
        self.namespace = None
        self.owner = None
        self.error = None
        try:
            # Authenticated owner's private app DB scopes this identity; never
            # clientId, hostname, PID or per-start hostInstanceId. No old keys
            # are consulted. INSERT OR IGNORE preserves concurrent cold starts.
            shared = self.saved_record('preferences', PREFERENCES_KEY)
            if shared is not MISSING:
                self.validate_shared(shared)
            identity = self.saved_record('appearance-identity', 'v2')
            if identity is MISSING:
                owner = shared['owner'] if shared is not MISSING else uuid.uuid4().hex
                self.shell.db.execute('INSERT OR IGNORE INTO shell_records VALUES (?,?,?)',
                                      ('appearance-identity', 'v2', json.dumps({'owner': owner})))
                self.shell.db.commit()
                identity = self.saved_record('appearance-identity', 'v2')
            if (not isinstance(identity, dict) or set(identity) != {'owner'}
                    or not isinstance(identity['owner'], str)
                    or not re.fullmatch('[a-f0-9]{32}', identity['owner'])):
                raise ValueError('Invalid appearance owner identity.')
            self.owner = identity['owner']
            if shared is not MISSING and shared['owner'] != self.owner:
                raise ValueError('Appearance owner identity does not match the saved profile.')
            self.namespace = 'amplifier.appearance.v2:' + self.owner
        except (ValueError, TypeError, KeyError, AppError):
            self.error = 'Saved appearance preferences are invalid. Preserve the records and restore a valid appearance backup before changing preferences.'

    def require_valid(self):
        if self.error:
            raise AppError(self.error, 409, code='appearance_preferences_invalid')

    def saved_record(self, kind, identity):
        row = self.shell.db.execute('SELECT value FROM shell_records WHERE kind=? AND id=?',
                                    (kind, identity)).fetchone()
        return json.loads(row[0]) if row else MISSING

    def validate_shared(self, shared):
        if (not isinstance(shared, dict) or set(shared) != {'owner', 'revision', 'values'}
                or not isinstance(shared['owner'], str) or not re.fullmatch('[a-f0-9]{32}', shared['owner'])
                or type(shared['revision']) is not int or shared['revision'] < 0):
            raise ValueError('Invalid saved appearance profile.')
        self.validate_record('shared', shared['values'])
        if shared['values'] is None:
            raise ValueError('Saved Global profile must be complete.')
        return shared

    def shared(self):
        self.require_valid()
        try:
            identity = self.saved_record('appearance-identity', 'v2')
            if not isinstance(identity, dict) or identity != {'owner': self.owner}:
                raise ValueError('Saved appearance owner changed.')
            shared = self.saved_record('preferences', PREFERENCES_KEY)
            if shared is MISSING:
                return {'owner': self.owner, 'revision': 0, 'values': copy.deepcopy(DEFAULTS)}
            self.validate_shared(shared)
            if shared['owner'] != self.owner:
                raise ValueError('Appearance owner changed.')
            return shared
        except (ValueError, TypeError, KeyError, AppError):
            self.error = 'Saved appearance preferences are invalid. Preserve the records and restore a valid appearance backup before changing preferences.'
            self.require_valid()

    def values(self, identity=None):
        """Presentation consumers do not resolve or hash package bodies."""
        self.require_valid()
        mirror = self.mirrors.get(identity)
        if mirror and mirror['activeScope'] == 'client':
            return copy.deepcopy(mirror['values'])
        return self.shared()['values']

    def revision_fence(self, identity):
        mirror = self.mirror(identity)
        fence = {'generation': mirror['generation'], 'activeScope': mirror['activeScope'],
                 'localRevision': mirror['revision'], 'storageVersion': mirror['storageVersion']}
        if mirror['activeScope'] == 'shared' or mirror['values'] is None:
            fence['sharedRevision'] = self.shared()['revision']
        return fence

    def check_revision_fence(self, identity, expected):
        if expected != self.revision_fence(identity):
            raise AppError('Appearance preferences changed since this shell change was prepared. Inspect and prepare again.', 409)

    def mirror(self, identity):
        if identity not in self.mirrors:
            self.mirrors[identity] = {'generation': uuid.uuid4().hex, 'revision': 0,
                                      'activeScope': 'shared', 'values': None,
                                      'reported': False, 'preview': None,
                                      'observedCompositionId': None, 'storageVersion': 'missing',
                                      'seenStorageVersions': set()}
        return self.mirrors[identity]

    def attached(self, identity):
        self.require_valid()
        record = self.service.clients.records.get(identity)
        if not record or record.get('kind') != 'web':
            raise AppError('Attach a web client for browser-local appearance changes.', 409)
        return self.mirror(identity)

    def validate_patch(self, patch, *, selection=True, reset=True):
        try:
            validate(patch, {'type': 'object', 'additionalProperties': False, 'properties': fields(patch=reset)})
        except ValidationError as exc:
            raise AppError('Invalid appearance preferences: ' + exc.message) from None
        if selection and 'appearanceId' in patch:
            self.selected(patch['appearanceId'], required=True)
        return copy.deepcopy(patch)

    def validate_record(self, active_scope, values):
        if active_scope not in {'shared', 'client'} or active_scope == 'client' and values is None:
            raise AppError('This browser requires an initialized complete profile.')
        if values is not None:
            self.validate_patch(values, selection=False, reset=False)
            if set(DEFAULTS) - set(values):
                raise AppError('Report a complete browser profile, not partial preferences.')
        return {'activeScope': active_scope, 'values': copy.deepcopy(values)}

    @staticmethod
    def record(mirror):
        return {key: copy.deepcopy(mirror[key]) for key in ('activeScope', 'values')}

    def check_local_cas(self, mirror, generation, revision, storage_version):
        if generation != mirror['generation']:
            raise AppError('This browser generation was replaced. Read its current appearance state.', 409)
        if revision != mirror['revision'] or storage_version != mirror['storageVersion']:
            raise AppError('Browser preferences changed. Read them again before editing.', 409)

    def selected(self, identity, *, required=False):
        if identity == themes.REFERENCE_ID:
            return {'id': themes.REFERENCE_ID, 'name': 'Reference', 'base': themes.BASE,
                    'css': '', 'fingerprint': theme_library.fingerprint(''), 'source': 'foundation', 'available': True}
        try:
            return {**theme_library.resolve(self.service, identity), 'available': True}
        except (AppError, OSError) as exc:
            if required:
                raise AppError('Select an available library appearance, or reference for recovery.', 409) from exc
            return {'id': identity, 'available': False, 'recoveryId': themes.REFERENCE_ID,
                    'name': 'Unavailable appearance', 'base': themes.BASE, 'css': '',
                    'fingerprint': theme_library.fingerprint('')}

    def read(self, identity=None):
        try:
            shared = self.shared()
        except AppError as exc:
            if exc.code != 'appearance_preferences_invalid':
                raise
            return {'availability': 'failed', 'error': str(exc), 'code': exc.code}
        mirror = self.mirror(identity) if identity in self.service.clients.records else None
        active_scope = mirror['activeScope'] if mirror else 'shared'
        effective = copy.deepcopy(mirror['values'] if active_scope == 'client' else shared['values'])
        source = {'scope': active_scope, 'revision': (
            mirror['revision'] if active_scope == 'client' else shared['revision'])}
        if active_scope == 'client':
            source['storageVersion'] = mirror['storageVersion']
        theme = self.selected(effective['appearanceId'])
        preview = mirror.get('preview') if mirror else None
        rendered = preview['theme'] if preview else theme
        shell_client = self.shell.get('client', identity) if identity else None
        shell_preview = shell_client.get('preview') if shell_client else None
        rendered_preferences = compose_values(effective,
            shell_preview['composition']['presentation'] if shell_preview else {})
        preview_ids = [item for item in (preview['id'] if preview else None,
                                       shell_preview['id'] if shell_preview else None) if item]
        composition = themes.composition(self.service, rendered, source,
            rendered_preferences, ':'.join(preview_ids) or None)
        result = {'namespace': self.namespace,
                  'generation': mirror['generation'] if mirror else None,
                  'shared': {'revision': shared['revision'], 'values': copy.deepcopy(shared['values'])},
                  'activeScope': active_scope,
                  'local': {'revision': mirror['revision'] if mirror else None,
                            'values': copy.deepcopy(mirror['values']) if mirror else None,
                            'storageVersion': mirror['storageVersion'] if mirror else None,
                            'reported': bool(mirror and mirror['reported']),
                            'error': mirror.get('storageError') if mirror else None},
                  'effective': effective, 'renderedPreferences': rendered_preferences,
                  'source': source, 'writeScope': active_scope,
                  'theme': theme, 'foundation': themes.foundation(self.service), 'composition': composition,
                  'preview': copy.deepcopy(preview), 'availability': 'not-attached'}
        if identity:
            client = self.service.clients.records.get(identity)
            connected = identity in self.service.queue_clients.values()
            result.update(clientId=identity, generation=mirror['generation'] if mirror else None,
                          availability='connected' if connected else 'disconnected',
                          observedCompositionId=mirror['observedCompositionId'] if mirror else None)
            if not client or client.get('kind') != 'web':
                result['availability'] = 'unsupported'
            pending = (shell_client or {}).get('preferenceCommand')
            if pending and mirror and pending['generation'] == mirror['generation']:
                result['command'] = {key: copy.deepcopy(pending[key]) for key in (
                    'id', 'generation', 'namespace', 'expectedRevision', 'expectedStorageVersion', 'after', 'status')}
                if pending.get('error'):
                    result['command']['error'] = pending['error']
                if pending.get('undoId'):
                    result['command']['undoId'] = pending['undoId']
        return result

    def queue(self, identity, after, expected, generation, storage_version, *, shell_change=None):
        mirror = self.attached(identity)
        if identity not in self.service.queue_clients.values():
            raise AppError('The target browser is disconnected; no local storage command was issued.', 409)
        if not mirror['reported']:
            raise AppError('The browser must report its current local preferences before editing them.', 409)
        self.check_local_cas(mirror, generation, expected, storage_version)
        after = self.validate_record(after['activeScope'], after['values'])
        client = self.shell.client(identity)
        pending = client.get('preferenceCommand')
        if pending and pending['status'] == 'pending' and pending['generation'] == mirror['generation']:
            raise AppError('A browser preference command is still awaiting storage acknowledgement.', 409)
        command = {'id': uuid.uuid4().hex, 'generation': mirror['generation'], 'namespace': self.namespace,
                   'expectedRevision': expected, 'status': 'pending',
                   'expectedStorageVersion': mirror['storageVersion'],
                   'before': self.record(mirror), 'after': after, 'shellChange': shell_change}
        client['preferenceCommand'] = command
        self.shell.put('client', identity, client)
        return {'status': 'awaiting-storage', 'commandId': command['id'], 'generation': mirror['generation'],
                'stored': False, 'activated': False}

    def select(self, args, identity):
        mirror = self.attached(identity)
        self.check_local_cas(mirror, args['generation'], args['expectedRevision'], args['expectedStorageVersion'])
        after = self.record(mirror)
        after['activeScope'] = args['activeScope']
        if after['activeScope'] == 'client' and after['values'] is None:
            shared = self.shared()
            if args.get('expectedSharedRevision') != shared['revision']:
                raise AppError('Shared preferences changed. First browser selection requires the current shared revision.', 409)
            after['values'] = copy.deepcopy(shared['values'])
        return self.queue(identity, after, args['expectedRevision'], args['generation'], args['expectedStorageVersion'])

    def client_edit(self, identity, patch, *, shell_change=None):
        mirror = self.attached(identity)
        values = mirror['values'] if mirror['values'] is not None else self.shared()['values']
        after = {'activeScope': 'client', 'values': patch_values(values, self.validate_patch(patch))}
        return self.queue(identity, after, mirror['revision'], mirror['generation'],
                          mirror['storageVersion'], shell_change=shell_change)

    def update(self, args, identity):
        patch = self.validate_patch(args['patch'])
        if not patch:
            raise AppError('Name at least one preference to change.')
        if args['scope'] == 'client':
            mirror = self.attached(identity)
            self.check_local_cas(mirror, args['generation'], args['expectedRevision'], args['expectedStorageVersion'])
            if mirror['values'] is None:
                raise AppError('Select This browser to initialize its complete profile before editing.', 409)
            return self.queue(identity, {'activeScope': mirror['activeScope'],
                'values': patch_values(mirror['values'], patch)}, args['expectedRevision'],
                args['generation'], args['expectedStorageVersion'])
        if 'generation' in args:
            if args['generation'] != self.attached(identity)['generation']:
                raise AppError('This browser generation was replaced. Read its current appearance state.', 409)
        shared = self.shared()
        if args['expectedRevision'] != shared['revision']:
            raise AppError('Shared preferences changed. Read the current shared revision before editing.', 409)
        before = copy.deepcopy(shared['values'])
        shared['values'] = patch_values(shared['values'], patch)
        shared['revision'] += 1
        undo_id = uuid.uuid4().hex
        with self.shell.transaction():
            self.shell.put('preferences', PREFERENCES_KEY, shared)
            self.shell.put('change', undo_id, {'scope': 'shared', 'revision': shared['revision'],
                'before': before, 'owner': self.namespace})
        return {'status': 'saved', 'scope': 'shared', 'revision': shared['revision'],
                'undoId': undo_id, 'stored': True, 'activated': False}

    def report(self, args, identity, origin):
        if origin != 'ui' or identity != self.service.clients.current.get():
            raise AppError('Only the admitted browser can acknowledge its local storage.', 403)
        mirror = self.attached(identity)
        if args['generation'] != mirror['generation']:
            raise AppError('This browser generation was replaced. Read its current appearance state.', 409)
        after = self.validate_record(args['activeScope'], args['values'])
        client = self.shell.client(identity)
        command = client.get('preferenceCommand')
        if args.get('commandId'):
            if (not command or command['id'] != args['commandId'] or command['generation'] != mirror['generation']):
                raise AppError('No admitted storage command matches this acknowledgement.', 409)
            if command['status'] != 'pending':
                if command['status'] == 'stored' and (
                        args['status'] != 'stored' or after != command['after']
                        or args['revision'] != command['expectedRevision'] + 1
                        or args['storageVersion'] != command['storageVersion']):
                    raise AppError('Storage acknowledgement does not match the admitted preference change.', 409)
                return {'status': command['status'], 'commandId': command['id'], 'undoId': command.get('undoId'),
                        'stored': command['status'] == 'stored', 'activated': command.get('activated', False)}
            if args['status'] == 'failed':
                command.update(status='failed', error=args.get('error') or 'Browser storage failed; shared preferences were not changed.')
                mirror['storageError'] = command['error']
                client['preferenceCommand'] = command
                self.shell.put('client', identity, client)
                return {'status': 'failed', 'stored': False, 'activated': False, 'error': command['error']}
            if (args['status'] != 'stored' or args['revision'] != command['expectedRevision'] + 1
                    or mirror['revision'] != command['expectedRevision'] or after != command['after']
                    or mirror['storageVersion'] != command['expectedStorageVersion']
                    or args['storageVersion'] in {'missing', command['expectedStorageVersion']}
                    or args['storageVersion'] in mirror['seenStorageVersions']):
                raise AppError('Storage acknowledgement does not match the admitted preference change.', 409)
            change = command.get('shellChange')
            if change:
                self.shell.check_preference_activation(identity, change)
            undo_id = uuid.uuid4().hex
            activated = bool(change) or (
                after != command['before'] and (
                    after['activeScope'] == 'client' or after['activeScope'] != command['before']['activeScope']))
            command.update(status='stored', undoId=undo_id, activated=activated,
                           storageVersion=args['storageVersion'])
            client['preferenceCommand'] = command
            with self.shell.transaction():
                self.shell.put('change', undo_id, {'scope': 'client', 'clientId': identity,
                    'generation': mirror['generation'], 'revision': args['revision'],
                    'storageVersion': args['storageVersion'],
                    'before': command['before'], 'owner': self.namespace})
                self.shell.put('client', identity, client)
                if change:
                    prepared = self.shell.get('change', change['changeId'])
                    if change['action'] != 'shell.changes.revert':
                        prepared['preferenceUndoId'] = undo_id
                        self.shell.put('change', change['changeId'], prepared)
                    self.shell.activate_preference_change(identity, change)
                mirror.update(revision=args['revision'], **after, reported=True,
                              storageVersion=args['storageVersion'], observedCompositionId=None)
                mirror['seenStorageVersions'].add(args['storageVersion'])
                mirror.pop('storageError', None)
            result = {'status': 'stored', 'commandId': command['id'], 'undoId': undo_id, 'stored': True,
                      'activated': activated, 'rendered': False}
        else:
            if args['status'] == 'failed':
                mirror['storageError'] = args.get('error') or 'Browser preference storage is unavailable.'
                return {'status': 'failed', 'stored': False, 'activated': False, 'error': mirror['storageError']}
            if args['storageVersion'] == 'missing' and after != {'activeScope': 'shared', 'values': None}:
                raise AppError('Missing browser storage must select Global with no local profile.')
            if args['status'] != 'ready' or args['revision'] < mirror['revision']:
                raise AppError('Report current browser storage, not an older mirror or an unsolicited command result.', 409)
            if (args['revision'] == mirror['revision'] and mirror['reported']
                    and (after != self.record(mirror) or args['storageVersion'] != mirror['storageVersion'])):
                raise AppError('Changed browser preferences require a newer storage revision.', 409)
            if (mirror['reported'] and after != self.record(mirror)
                    and args['storageVersion'] == mirror['storageVersion']):
                raise AppError('Changed browser preferences require a fresh storage version.', 409)
            if (args['storageVersion'] != 'missing' and args['storageVersion'] != mirror['storageVersion']
                    and args['storageVersion'] in mirror['seenStorageVersions']):
                raise AppError('A previously observed storage version cannot be reused.', 409)
            if command and command['status'] == 'pending' and args['revision'] > command['expectedRevision']:
                command.update(status='superseded', error='Another browser storage edit superseded this command.')
                client['preferenceCommand'] = command
                self.shell.put('client', identity, client)
            changed = after != self.record(mirror) or args['storageVersion'] != mirror['storageVersion']
            mirror.update(revision=args['revision'], **after, reported=True,
                          storageVersion=args['storageVersion'])
            if changed:
                mirror['observedCompositionId'] = None
            if args['storageVersion'] != 'missing':
                mirror['seenStorageVersions'].add(args['storageVersion'])
            mirror.pop('storageError', None)
            result = {'status': 'reported', 'stored': False, 'activated': False}
        if args.get('renderedCompositionId'):
            current = self.read(identity)['composition']['id']
            if args['renderedCompositionId'] == current:
                mirror['observedCompositionId'] = current
                result['rendered'] = True
        return result

    def undo(self, args, identity, *, shell_change=None):
        undo = self.shell.get('change', args['undoId'])
        if not undo or undo.get('owner') != self.namespace:
            raise AppError('Preference undo not found.', 404)
        if undo['scope'] == 'shared':
            shared = self.shared()
            if shared['revision'] != undo['revision']:
                raise AppError('Newer shared preferences prevent this undo.', 409, code='appearance_undo_stale')
            shared['values'] = copy.deepcopy(undo['before'])
            shared['revision'] += 1
            self.shell.put('preferences', PREFERENCES_KEY, shared)
            return {'status': 'saved', 'scope': 'shared', 'revision': shared['revision'], 'stored': True, 'activated': False}
        mirror = self.attached(identity)
        if (undo['clientId'] != identity or undo['generation'] != mirror['generation']
                or undo['revision'] != mirror['revision'] or undo['storageVersion'] != mirror['storageVersion']):
            raise AppError('Newer browser preferences or a new document prevent this undo.', 409, code='appearance_undo_stale')
        return self.queue(identity, undo['before'], undo['revision'], mirror['generation'],
                          undo['storageVersion'], shell_change=shell_change)

    def perform(self, action, args, identity, origin):
        self.shared()  # Validate persisted authority before ANY write or ACK.
        if action == 'appearance.preferences.update':
            return self.update(args, identity)
        if action == 'appearance.preferences.select':
            return self.select(args, identity)
        if action == 'appearance.preferences.undo':
            return self.undo(args, identity)
        if action == 'appearance.client.report':
            return self.report(args, identity, origin)
        mirror = self.attached(identity)
        if action == 'appearance.preview.cancel':
            mirror['preview'] = None
            return {'status': 'awaiting-browser', 'activated': False}
        if action == 'appearance.preview':
            package = theme_library.packages.from_css(args['css'], args['name'])
            preview_id = uuid.uuid4().hex
            mirror['preview'] = {'id': preview_id, 'theme': {'id': 'preview:' + preview_id,
                'name': args['name'], 'css': package.css, 'base': themes.BASE,
                'fingerprint': theme_library.fingerprint(package.css)}}
            return {'status': 'awaiting-browser', 'previewId': preview_id, 'activated': False}
        raise AppError('Unsupported appearance action.')

    async def dispatch(self, action, args, origin, command_id):
        bound = self.service.clients.current.get()
        identity = args.get('clientId', bound)
        if bound is not None and identity != bound:
            raise AppError('The appearance command targets a different client.', 403)
        if action == 'appearance.client.report' and (not bound or origin != 'ui'):
            raise AppError('Storage reports require the originating browser connection.', 403)
        args = {**args, **({'clientId': identity} if identity is not None else {})}
        async with self.service.lock:
            if action != 'appearance.preferences.read':
                self.shared()
            cached = self.shell.cached_command(action, args, origin, command_id)
            if cached is not None:
                return cached
            if action == 'appearance.preferences.read':
                return {'accepted': True, 'effects': [], 'result': self.read(identity)}
            previous = copy.deepcopy(self.mirrors.get(identity))
            try:
                with self.shell.transaction():
                    result = self.perform(action, args, identity, origin)
                    receipt = {'accepted': True, 'effects': [], 'result': result}
                    self.shell.remember_command(action, args, origin, command_id, receipt)
                    targets = list(self.service.clients.records) if result.get('scope') == 'shared' else [identity]
                    for target in targets:
                        if target:
                            self.shell.notify(target)
            except BaseException:
                if previous is None:
                    self.mirrors.pop(identity, None)
                else:
                    self.mirrors[identity] = previous
                raise
            return receipt