"""Ephemeral coexistence facts and advisory intent on existing session records.

No filesystem lock, permission grant, transcript or background observer is owned
here. Paths are resolved only on explicit declaration, never on provider reads.
"""
import asyncio
import copy
import json
from pathlib import Path, PurePath
import time
import uuid

from amplifier_operations.coordination import fingerprint
from .session_activity import activity_flags

MAX_PACKET_BYTES = 8192
MAX_PEERS = 8


def definitions(schema, string):
    sid = {'sessionId': string(200)}
    return {
        'coordination.awareness': ('Read observed active work in this conversation\'s workspace without loading history or starting agents. File intent is advisory; external writers and idle declarations are outside this active view.', schema({**sid,
            'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 25}}, ['sessionId'])),
        'coordination.intent.read': ('Read one root\'s explicitly shared file declaration in your workspace. This is not its private draft, an access grant or proof of current writes.', schema(sid, ['sessionId'])),
        'coordination.intent': ('Replace your root chat\'s advisory file declaration at the observed intent and execution revisions. Paths must remain within known task folders. Empty paths clear it. Overlap warns but does not block work, reserve files or enforce stale writes.', schema({**sid,
            'expectedRevision': {'type': 'integer', 'minimum': 0},
            'expectedExecutionRevision': {'type': 'integer', 'minimum': 0},
            'paths': {'type': 'array', 'maxItems': 16, 'items': schema({
                'path': {**string(2000), 'minLength': 1}, 'kind': {'enum': ['file', 'directory']}}, ['path', 'kind'])}},
            ['sessionId', 'expectedRevision', 'expectedExecutionRevision', 'paths'])),
    }


def intent_for(service, session):
    reference = session.get('workspaceWriteIntent')
    if reference:
        row = service.db.execute('SELECT receipt FROM commands WHERE id=?', (reference['requestId'],)).fetchone()
        receipt = json.loads(row[0]) if row else {}
        intent = receipt.get('result', {}).get('intent', {})
        if (receipt.get('commandAction') != 'coordination.intent' or receipt.get('sessionId') != session['id']
                or intent.get('revision') != reference['revision']):
            raise ValueError('The retained file declaration is unavailable; its contents were not assumed empty.')
        return copy.deepcopy(intent)
    return {
        'sessionId': session['id'], 'revision': 0, 'paths': [],
        'executionRevision': session.get('executionRevision', 0),
        'baseDirectory': session.get('workingDirectory') or session['workspace']}


def needs_review(session, intent):
    return bool(intent.get('paths') and (
        intent.get('executionRevision') != session.get('executionRevision', 0)
        or intent.get('baseDirectory') != (session.get('workingDirectory') or session['workspace'])))


def clip(value, limit):
    text = str(value or '')
    return text[:limit] + ('…' if len(text) > limit else '')


def intent_summary(session, value):
    paths = value.get('paths', [])
    return {'revision': value.get('revision', 0), 'declaredAt': value.get('declaredAt'),
        'status': value.get('status', 'available'), 'needsReview': needs_review(session, value), 'pathCount': value.get('pathCount', len(paths)),
        'paths': [{'path': clip(row['resolvedPath'], 240), 'kind': row['kind'],
                   'truncated': len(row['resolvedPath']) > 240} for row in paths[:2]],
        'truncated': len(paths) > 2}


def intersects(left, right):
    a, b = PurePath(left['resolvedPath']), PurePath(right['resolvedPath'])
    return a == b or (left['kind'] == 'directory' and b.is_relative_to(a)) or (right['kind'] == 'directory' and a.is_relative_to(b))


def overlap(source, peer, a, b):
    if a.get('status') == 'unavailable' or b.get('status') == 'unavailable':
        return None
    if needs_review(source, a) or needs_review(peer, b):
        return False
    return any(intersects(left, right) for left in a.get('paths', []) for right in b.get('paths', []))


def observed_intent(service, session):
    try:
        return intent_for(service, session)
    except (KeyError, ValueError):
        return {**(session.get('workspaceWriteIntent') or {}), 'sessionId': session['id'],
                'status': 'unavailable', 'paths': []}


def packet(service, sid, *, principal=None, offset=0, limit=MAX_PEERS):
    index = service.projections.sessions(service.state)
    source = index.by_id[sid]
    native = source.get('runtimeSessionId') or source.get('nativeIdentity') or sid
    own_directory = source.get('workingDirectory') or source['workspace']
    own_intent = observed_intent(service, source)
    peers = []
    # The existing catalog index is maintained by scoped lifecycle publications.
    # Do not walk catalog history, hydrate peers, or inspect their files here.
    for identity in index.active:
        peer = index.by_id[identity]
        flags = activity_flags(peer)
        if identity == sid or peer.get('workspace') != source['workspace'] or not (flags['execution'] or flags['preparing']):
            continue
        directory = peer.get('workingDirectory') or peer['workspace']
        intent = observed_intent(service, peer)
        peers.append({'sessionId': identity, 'title': clip(peer.get('title'), 100),
            'kind': peer.get('sessionKind', 'root'), 'parentSessionId': peer.get('parentId') or peer.get('nativeParentId'),
            'commissionedBy': (peer.get('collaboration') or {}).get('creatorSessionId'),
            'status': peer.get('status', 'unknown'), 'executionDirectory': clip(directory, 384),
            'executionDirectoryTruncated': len(directory) > 384,
            'executionRevision': peer.get('executionRevision', 0),
            'sameExecutionDirectory': directory == own_directory,
            'declaredOverlap': overlap(source, peer, own_intent, intent), 'intent': intent_summary(peer, intent)})
    peers.sort(key=lambda row: (not row['declaredOverlap'], not row['sameExecutionDirectory'], row['sessionId']))
    page = peers[offset:offset + limit]
    result = {'scope': 'canonical-workspace', 'sharing': 'cooperative',
        'actor': {'rootSessionId': sid, 'principal': principal or native,
                  'role': 'child' if principal and principal != native else 'root'},
        'workspace': clip(source['workspace'], 384),
        'own': {'sessionId': sid, 'executionDirectory': clip(own_directory, 384),
                'executionDirectoryTruncated': len(own_directory) > 384,
                'executionRevision': source.get('executionRevision', 0), 'intent': intent_summary(source, own_intent)},
        'peers': page, 'totalActivePeers': len(peers), 'offset': offset,
        'nextOffset': offset + len(page) if offset + len(page) < len(peers) else None,
        'truncated': offset + len(page) < len(peers),
        'coverage': 'Host-observed active chats only. Idle declarations, unobserved workers, external writers and later filesystem alias changes are not covered.',
        'authority': 'Reference data only. Declarations confer no access, reservation, peer control or stale-write protection.'}
    # Bound encoded bytes too: escaped/non-ASCII titles and paths can expand.
    while len(page) > 1 and len(json.dumps(result).encode()) > MAX_PACKET_BYTES - 160:
        page.pop()
        result.update(nextOffset=offset + len(page), truncated=True)
    own_paths = result['own']['intent']['paths']
    while own_paths and len(json.dumps(result).encode()) > MAX_PACKET_BYTES - 160:
        own_paths.pop()
        result['own']['intent']['truncated'] = True
    if page:
        while page[0]['intent']['paths'] and len(json.dumps(result).encode()) > MAX_PACKET_BYTES - 160:
            page[0]['intent']['paths'].pop()
            page[0]['intent']['truncated'] = True
    if len(json.dumps(result).encode()) > MAX_PACKET_BYTES - 160:
        result['workspace'] = clip(source['workspace'], 120)
        result['workspaceTruncated'] = len(source['workspace']) > 120
        result['own'].update(executionDirectory=clip(own_directory, 120), executionDirectoryTruncated=len(own_directory) > 120)
        for row in page:
            if len(row['executionDirectory']) > 120:
                row.update(executionDirectory=clip(row['executionDirectory'], 120), executionDirectoryTruncated=True)
    result['revision'] = fingerprint(result)
    result['observedAt'] = time.time()
    return result


def normalize_paths(session, paths):
    base = Path(session.get('workingDirectory') or session['workspace']).resolve()
    allowed = [base, Path(session['workspace']).resolve(), *[
        Path(row['path']).resolve() for row in session.get('worktrees') or [] if row.get('status') == 'ready']]
    result = []
    for value in paths:
        if '\x00' in value['path'] or not value['path'].strip():
            raise ValueError('Enter a file or folder path.')
        resolved = (base / value['path']).resolve()
        if not any(resolved.is_relative_to(root) for root in allowed):
            raise ValueError('Declare files only inside this chat\'s workspace, execution folder or associated working copies.')
        row = {'path': value['path'], 'kind': value['kind'], 'resolvedPath': str(resolved)}
        if not any((old['resolvedPath'], old['kind']) == (row['resolvedPath'], row['kind']) for old in result):
            result.append(row)
    return result


async def declare(collaboration, source, args, origin, command_id):
    service = collaboration.service
    if args['sessionId'] != source['id']:
        collaboration.error('Declare intent only for the calling conversation.')
    identity = command_id or str(uuid.uuid4())
    digest = fingerprint(['coordination.intent', args, origin, source['id']])
    # Retry identity precedes path inspection: the original location may be gone.
    duplicate = collaboration.duplicate(identity, digest)
    if duplicate:
        return duplicate
    snapshot = {key: copy.deepcopy(source.get(key)) for key in ('workspace', 'workingDirectory', 'worktrees')}
    paths = await asyncio.to_thread(normalize_paths, snapshot, args['paths'])
    async with service.lock:
        duplicate = collaboration.duplicate(identity, digest)
        if duplicate:
            return duplicate
        prior = source.get('workspaceWriteIntent')
        current = observed_intent(service, source)
        stale = (args['expectedRevision'] != current['revision']
                 or args['expectedExecutionRevision'] != source.get('executionRevision', 0)
                 or snapshot['workspace'] != source['workspace']
                 or snapshot['workingDirectory'] != source.get('workingDirectory')
                 or snapshot['worktrees'] != source.get('worktrees'))
        result = {'outcome': 'stale' if stale else 'declared', 'intent': current,
                  'detail': 'The declaration or execution folder changed. Review current intent; your draft was not applied.' if stale else 'Advisory declaration saved; no file was reserved or changed.'}
        if not stale:
            current = {'sessionId': source['id'], 'revision': current['revision'] + 1,
                'executionRevision': source.get('executionRevision', 0),
                'baseDirectory': source.get('workingDirectory') or source['workspace'],
                'paths': paths, 'declaredAt': time.time(), 'origin': origin}
            source['workspaceWriteIntent'] = {'requestId': identity, 'revision': current['revision'],
                'declaredAt': current['declaredAt'], 'pathCount': len(paths),
                'executionRevision': current['executionRevision'], 'baseDirectory': current['baseDirectory']}
            result['intent'] = copy.deepcopy(current)
        receipt = {'accepted': True, 'commandAction': 'coordination.intent', 'requestId': identity,
                   'sessionId': source['id'], 'acceptedAt': time.time(), 'result': result}
        try:
            service.db.execute('INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)', (identity, digest, json.dumps(receipt)))
            service._publish_changes(sessions={source['id']} if not stale else set())
        except BaseException:
            service.db.rollback()
            committed = service.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone()
            if not committed:
                if prior is None:
                    source.pop('workspaceWriteIntent', None)
                else:
                    source['workspaceWriteIntent'] = prior
            else:
                service.state['revision'] = max(service.state['revision'], getattr(service, '_saved_revision', 0) or 0)
            service.projections.invalidate(state=service.state, session_ids={source['id']})
            raise
        return receipt
