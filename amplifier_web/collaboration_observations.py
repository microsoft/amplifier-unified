"""Bounded presentation projections; receipts and native records stay authoritative."""
import asyncio
import base64
import hashlib
import json
import math
import time
from .peer_scope import participant_matches

PAGE = 8
MAX_ITEMS = 96
STATES = {'idle', 'ready', 'working', 'running', 'starting', 'stopping', 'stopped',
    'interrupted', 'error', 'failed', 'completed', 'cancelled', 'queued', 'accepted',
    'applied', 'submitting', 'notified', 'suppressed', 'expired', 'unknown', 'not_sent',
    'held', 'pending_steer', 'staged', 'sealed', 'rejected', 'observed', 'waiting',
    'reserved', 'claimed', 'backpressured', 'limit_reached', 'active', 'paused',
    'blocked', 'success', 'deferred', 'declined', 'question', 'conflict', 'changed',
    'planned', 'creating_root', 'root_retained', 'input_committed', 'creation_pending',
    'created_initial_pending', 'retained', 'verified', 'incomplete', 'not_requested',
    'released', 'uncertain', 'started', 'timeout', 'prepared', 'allocating', 'retired'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def state(value):
    return value if isinstance(value, str) and value in STATES else 'unknown'


def interval(start, end):
    start, end = number(start), number(end)
    return round((end-start)*1000, 2) if start is not None and end is not None and end >= start else None


def receipts(app, sid):
    """Use owner indexes and cap each direction before merging their recent pages."""
    found, truncated = {}, False
    for action, field, limit in [('coordination.send', 'senderSessionId', 32),
            ('coordination.send', 'target.sessionId', 32),
            ('coordination.watch', 'senderSessionId', 16),
            ('coordination.create', 'senderSessionId', 16)]:
        rows = app.db.execute(f"""SELECT rowid,receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')=?
            AND json_extract(receipt,'$.{field}')=? ORDER BY rowid DESC LIMIT ?""",
            (action, sid, limit+1)).fetchall()
        truncated |= len(rows) > limit
        found.update((key, json.loads(raw)) for key, raw in rows[:limit])
    return [found[key] for key in sorted(found, reverse=True)], truncated


def outcome(receipt):
    return max((row for row in (receipt.get('response'), receipt.get('terminalOutcome')) if row),
               key=lambda row: row.get('outcomeRevision', 0), default={})


def phase(receipt):
    response, wait = outcome(receipt), receipt.get('subscription') or {}
    if response.get('qualified') is True:
        return 'Result available'
    if response.get('outcome') in {'question', 'conflict'}:
        return 'Needs a reply'
    if response.get('outcome') == 'deferred':
        return 'Work deferred'
    if response.get('outcome') == 'success':
        return 'Success reported; completion not yet verified'
    if response:
        return 'Reported ' + state(response.get('outcome'))
    if wait.get('status') == 'backpressured':
        return 'Waiting for inbox space'
    if receipt.get('commandAction') == 'coordination.watch':
        return 'Waiting for an observation' if receipt.get('watch', {}).get('status') == 'waiting' else 'Observation updated'
    delivery = receipt.get('delivery')
    return {'queued': 'Waiting for admission', 'submitting': 'Admission outcome pending',
        'accepted': 'Waiting for reply', 'applied': 'Waiting for reply',
        'notified': 'Message delivered', 'creation_pending': 'Preparing task',
        'created_initial_pending': 'Task prepared; initial input pending'}.get(delivery, 'Delivery ' + state(delivery))


def recap_items(app, source, rows, resources):
    index = app.projections.sessions(app.state).by_id
    items = []
    def add(key, kind, label, status, version):
        items.append({'id': digest(key), 'kind': kind, 'label': str(label or 'Untitled chat')[:120],
                      'status': status, 'version': digest(version)})
    task = dict.get(source, 'task') or {}
    add(['chat', source['id']], 'chat', source.get('title'), 'Chat ' + state(source.get('status')),
        [source.get('status'), task.get('status'), task.get('revision'), source.get('executionRevision'),
         source.get('interruptionRevision'), (source.get('workspaceWriteIntent') or {}).get('revision')])
    for receipt in rows:
        if receipt.get('origin') == 'system':
            continue  # Its original request carries the human-facing outcome.
        peer_id = (receipt.get('target') or {}).get('sessionId') or receipt.get('sessionId')
        if peer_id == source['id']:
            peer_id = receipt.get('senderSessionId')
        peer = index.get(peer_id) or {}
        # Show current metadata only while the exact recorded location matches.
        # Cold peers can use their receipt's historical label, not a catalog scan.
        recorded = receipt.get('sender') if peer_id == receipt.get('senderSessionId') else receipt.get('target')
        label = (peer.get('title') if peer and participant_matches(receipt, peer)
                 else (recorded or {}).get('title') if not peer else None) or 'Related task'
        response, wait = outcome(receipt), receipt.get('subscription') or {}
        add(['request', receipt['requestId']], 'request', label, phase(receipt),
            [receipt.get('delivery'), receipt.get('phase'), receipt.get('responseRevision'),
             receipt.get('outcomeRevision'), response.get('status'), response.get('qualified'),
             response.get('artifactVerification'), wait.get('status'), wait.get('lastClaimedRevision'),
             (receipt.get('watch') or {}).get('status')])
    for row in resources.get('items', [])[:16]:
        add(['resource', row['id']], 'resource', 'Resource reservation',
            'Reservation ' + state(row.get('status')), [row.get('revision'), row.get('status')])
    for row in resources.get('storage', [])[:16]:
        add(['storage', row['id']], 'resource', 'Task storage',
            'Storage ' + state(row.get('status')),
            [row.get('revision'), row.get('status'), row.get('observation')])
    return items


def recap(source, items, token, truncated):
    truncated = truncated or len(items) > MAX_ITEMS
    items = items[:MAX_ITEMS]
    scope = digest([source['id'], source['workspace']])
    prior, status = {}, 'initial'
    if token:
        status = 'reset'
        try:
            value = json.loads(base64.urlsafe_b64decode(token.encode()))
            if (value.get('version') == 1 and value.get('scope') == scope
                    and isinstance(value.get('items'), dict) and len(value['items']) <= MAX_ITEMS
                    and all(isinstance(k, str) and isinstance(v, str) and len(k) == len(v) == 24
                            and all(c in '0123456789abcdef' for c in k+v) for k, v in value['items'].items())):
                prior, status = value['items'], 'continued'
        except (ValueError, TypeError, UnicodeError, AttributeError):
            pass
    changed = [item for item in items if prior.get(item['id']) != item['version']]
    shown = changed[:PAGE]
    current = {item['id']: item['version'] for item in items}
    # Unshown changes keep their previous signatures. Missing historical rows
    # are a page gap, not proof of deletion, completion or a released resource.
    next_items = {key: value for key, value in prior.items() if key in current}
    next_items.update((item['id'], item['version']) for item in shown)
    cursor = base64.urlsafe_b64encode(json.dumps({'version': 1, 'scope': scope,
        'items': next_items}, separators=(',', ':')).encode()).decode()
    return {'cursorStatus': status, 'changes': shown, 'remainingChanges': max(0, len(changed)-PAGE),
        'nextCursor': cursor, 'bounded': True, 'pageGap': bool(set(prior)-set(current)),
        'truncated': truncated, 'capturedAt': time.time(),
        'detail': 'Recent saved updates. Open a related task for its full history and supporting work.'}


def diagnostics(app, source, rows, truncated):
    phases = []
    for receipt in rows:
        response, wait = outcome(receipt), receipt.get('subscription') or {}
        phases.append({'operationId': digest(receipt['requestId']),
            'actorId': digest(receipt.get('senderSessionId')), 'kind': receipt['commandAction'],
            'acceptedAt': number(receipt.get('acceptedAt')), 'delivery': state(receipt.get('delivery')),
            'preparation': state(receipt.get('phase')), 'admittedAt': number(receipt.get('admittedAt')),
            'queueMs': interval(receipt.get('acceptedAt'), receipt.get('admittedAt')),
            'dependencyId': digest(receipt['dependencyRequestId']) if receipt.get('dependencyRequestId') else None,
            'replyId': digest(response['operationId']) if response.get('operationId') else None,
            'replyRevision': number(receipt.get('responseRevision')), 'replyStatus': state(response.get('status')),
            'outcome': state(response.get('outcome')), 'qualified': response.get('qualified') is True,
            'declaredAt': number(response.get('declaredAt')), 'sealedAt': number(response.get('sealedAt')),
            'artifactVerification': state(response.get('artifactVerification')),
            'subscription': state(wait.get('status')), 'claimedRevision': number(wait.get('lastClaimedRevision'))})
    provider = [{'operationId': digest(row.get('id')), 'inputId': digest(row.get('turnId')),
        'phase': state(row.get('phase')), 'durationMs': interval(row.get('startedAt'), row.get('endedAt'))}
        for row in (dict.get(source, 'execution') or {}).get('nodes', [])[-64:]
        if row.get('kind') == 'llm']
    bridge = [{'operationId': digest(row.get('bridgeId')), 'phase': state(row.get('phase')),
        'at': number(row.get('at')), 'elapsedMs': round(row['elapsedSeconds']*1000, 2)
        if number(row.get('elapsedSeconds')) is not None else None}
        for row in app.diagnostics.bridge_events(source)]
    sampled = app.performance_diagnostics.snapshot()
    lag = [{key: number(row.get(key)) for key in ('at', 'delayMs')}
           for row in sampled.get('eventLoopDelays', [])[-30:]]
    def coverage(rows):
        return {'status': 'observed' if rows else 'unavailable', 'samples': rows}
    return {'schemaVersion': 1, 'sessionId': digest(source['id']), 'capturedAt': time.time(),
        'phases': phases, 'truncated': truncated, 'provider': coverage(provider), 'bridge': coverage(bridge),
        'eventLoopDelay': coverage(lag), 'clientRendering': {'status': 'not_collected'},
        'limitations': ['Current bounded receipts and already loaded native metadata only; no history or worker was started.',
            'Queue timing ends at the durable admission claim, not provider execution.',
            'Provider and bridge durations are inclusive observations and may overlap; do not add them.',
            'Loop delay is process-wide and includes suspension or scheduling; it does not identify a cause.',
            'Hashed IDs correlate this report without exporting raw identifiers, content, paths or private drafts.']}


async def read(collaboration, action, args, origin, caller):
    app = collaboration.service
    source = collaboration.source(args, origin, caller, hydrate=False)
    if args['sessionId'] != source['id']:
        collaboration.error('Inspect the calling root conversation.')
    binding = [source['workspace'], source.get('runtimeSessionId'), source.get('nativeIdentity')]
    resources = {}
    if action == 'coordination.recap':
        workspace = next((row for row in app.state['workspaces'] if row['path'] == source['workspace']), None)
        if workspace:
            from .workspace_resources import dispatch_reservation
            reply = await dispatch_reservation(app, 'workspace.resources.reservations',
                {'workspaceId': workspace['id'], 'limit': 16}, origin, None, caller)
            resources = reply['result']
            from .task_resources import read as read_storage
            storage = await asyncio.to_thread(read_storage, app.data_dir, source['id'], {'limit': 16})
            resources.update(storage=storage['items'], storageNextOffset=storage['nextOffset'])
    source = collaboration.source(args, origin, caller, hydrate=False)
    if binding != [source['workspace'], source.get('runtimeSessionId'), source.get('nativeIdentity')]:
        collaboration.error('The conversation moved while reading its observation scope; inspect again.', 409)
    rows, truncated = receipts(app, source['id'])
    rows = [row for row in rows if (participant_matches(row, source)
            if row.get('commandAction') == 'coordination.send' else row.get('workspace') == source['workspace'])]
    if action == 'coordination.diagnostics':
        return diagnostics(app, source, rows, truncated)
    return recap(source, recap_items(app, source, rows, resources), args.get('cursor'),
        truncated or resources.get('nextOffset') is not None or resources.get('storageNextOffset') is not None)
