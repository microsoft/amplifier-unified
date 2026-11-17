"""Optional Instance-owned approval policy; this module stores no rules.

Reusable scopes describe canonical grants or supported exact native file calls,
never model-authored reasons. Unstructured prompts remain once-only.
"""
import hashlib
import json
from pathlib import Path
import time
import unicodedata
import uuid

from .configuration_authority import EFFECTS
from .preferences import SettingsStore

NAMESPACE = 'com.microsoft.amplifier.approval-policy'
PRODUCER = 'com.microsoft.amplifier.unified.configuration/v1'


def negotiate(profile):
    if not isinstance(profile, dict) or type(profile.get('version')) is not int:
        return None
    if profile['version'] != 1 or profile.get('owner') != 'instance':
        return None
    identity = profile.get('instanceId')
    try:
        if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity:
            return None
    except ValueError:
        return None
    return {'version': 1, 'owner': 'instance', 'producer': PRODUCER, 'reusableRequests': True}


def descriptor(service, session, approval):
    identity = approval.get('id', '')
    if approval.get('status') != 'pending':
        return None
    if not identity.startswith('capability:'):
        return _tool_descriptor(service, session, approval)
    grant = service.configuration_authority.record(identity)
    if (not grant or grant.get('sessionId') != session['id'] or grant.get('status') != 'pending'
            or grant.get('workspace') != session['workspace']
            or grant.get('interruptionRevision') != session.get('interruptionRevision', 0)
            or grant.get('expiresAt') is not None and grant['expiresAt'] <= time.time()):
        return None
    effects, scopes = sorted(grant['effects']), sorted(grant['scopes'])
    if not effects or any(effect not in EFFECTS for effect in effects):
        return None
    if not scopes or any(scope not in {'global', 'project', 'local'} for scope in scopes):
        return None  # No invented stable target for a session-only capability.
    try:
        settings = SettingsStore(service.data_dir)
        targets = {scope: str(settings.path(session['workspace'], scope).resolve()) for scope in scopes}
        owner = str(service.data_dir.resolve())
    except (OSError, ValueError):
        return None
    meaning = {'producer': PRODUCER, 'operation': 'configuration.grant',
               'owner': owner, 'effects': effects, 'scopes': scopes, 'targets': targets}
    key = hashlib.sha256(json.dumps(meaning, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    where = ('shared Amplifier settings ' + targets['global'] + '; app ' + owner
             if scopes == ['global'] else 'workspace ' + str(Path(session['workspace']).resolve()))
    if 'global' in scopes and len(scopes) > 1:
        where += '; shared Amplifier settings ' + targets['global']
    description = ('Configuration changes: ' + ', '.join(effect.replace('_', ' ') for effect in effects)
                   + '; scopes: ' + ', '.join(scopes) + '; ' + where
                   + ". Matching applies across this agent's chats. Grant lasts until Stop, grant revoke or expiry; removing the rule affects future approvals.")
    try:
        size = len(description.encode('utf-8'))
    except UnicodeError:
        return None
    if size > 512 or any(unicodedata.category(char) == 'Cc' for char in description):
        return None  # Never truncate a reusable scope into misleading wording.
    return {'version': 1, 'producer': PRODUCER, 'key': key, 'description': description,
            'allowOptionId': 'allow', 'authorityLifetime': 'conversation'}


def _tool_descriptor(service, session, approval):
    observe = getattr(getattr(service, 'runtime', None), 'approval_scope', None)
    if not callable(observe):
        return None
    try:
        if set(approval.get('options') or ['allow', 'deny']) != {'allow', 'deny'}:
            return None
        fact = observe(session['id'], approval['id'])
        if (not isinstance(fact, dict) or fact.get('owner') != str(service.data_dir.resolve())
                or fact.get('workspace') != str(Path(session['workspace']).resolve())):
            return None
        value = fact['descriptor']
        key, description = value['key'], value['description']
        if (type(value.get('version')) is not int or value['version'] != 1
                or value.get('producer') != PRODUCER or value.get('allowOptionId') != 'allow'
                or value.get('authorityLifetime') != 'request'
                or not isinstance(key, str) or len(key) != 64
                or any(char not in '0123456789abcdef' for char in key)
                or not isinstance(description, str) or not description
                or len(description.encode()) > 512
                or any(unicodedata.category(char) == 'Cc' for char in description)):
            return None
        return {field: value[field] for field in
                ('version', 'producer', 'key', 'description', 'allowOptionId', 'authorityLifetime')}
    except (KeyError, TypeError, ValueError, OSError):
        return None
