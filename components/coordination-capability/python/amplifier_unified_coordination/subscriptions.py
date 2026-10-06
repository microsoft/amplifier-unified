"""One explicit saved wait produces at most one guarded peer continuation."""
import hashlib
import json
from .grants import digest


def definition(schema, string):
    return {'description': 'Subscribe this sender to one exact peer request under its current human-approved queue and idle-start grant. A saved, qualified success may resume this sender once. Stops, changed task/configuration, revocation and uncertain restart hold or suppress continuation. Reading results never subscribes.',
            'schema': schema({'sessionId': string(512), 'requestId': string(200), 'grantId': string(200)})}


def persist(peer, *rows):
    with peer.owner.db:
        for row in rows:
            peer.owner.db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?)',
                (row['commandId'], row['requestHash'], json.dumps(row)))


async def subscribe(peer, params, source):
    owner, args = peer.owner, params['args']
    if (not params.get('resultsEnabled') or not params.get('deliveryEnabled')
            or params.get('origin') not in {'agent', 'ui'}
            or params['origin'] == 'ui' and not params.get('clientId')):
        raise ValueError('Authenticated guarded peer results are required for a saved wait')
    command = params.get('commandId')
    if not isinstance(command, str) or not 1 <= len(command) <= 200:
        raise ValueError('A stable subscription identity is required')
    signature = digest({key: params.get(key) for key in ('operation', 'args', 'origin', 'callerSession', 'actorId', 'clientId')})
    target = None
    async with owner.lock:
        row = peer.read(args['requestId'])
        if row['senderSessionId'] != source['sessionId'] or row['grantId'] != args['grantId'] or row['mode'] not in {'queue', 'steer'}:
            raise ValueError('Subscribe only to this sender\'s exact response request')
        previous = owner.receipt(command)
        if previous:
            if previous['requestHash'] != signature:
                raise ValueError('Subscription command identity conflicts')
            return {'receipt': previous, 'subscription': row.get('subscription'), 'replayed': False}
        if row.get('subscription'):
            raise ValueError('This request already has a saved subscription; inspect its original receipt')
        if row['status'] in {'held', 'unknown', 'suppressed', 'cancelled', 'failed'}:
            raise ValueError('This request is not eligible for an automatic continuation')
        await peer.guard_scope(row)
        state = await peer.inspect(source['sessionId'])
        task = state.get('task') or {}
        identity = 'continuation:' + hashlib.sha256(row['commandId'].encode()).hexdigest()
        pending = {'commandId': identity, 'requestHash': digest(['peer-continuation', row['commandId'], command]),
            'operation': 'coordination.send', 'inputId': 'peer:' + hashlib.sha256(identity.encode()).hexdigest(),
            'senderSessionId': row['target']['sessionId'], 'target': {'sessionId': source['sessionId']},
            'grantId': row['grantId'], 'grantRevision': row['grantRevision'], 'mode': 'queue',
            'taskId': task.get('id'), 'taskRevision': task.get('revision'),
            'configurationHash': state['configurationHash'], 'interruptionRevision': state['interruptionRevision'],
            'status': 'queued', 'dependencyRequestId': row['commandId']}
        # Validate return-direction queue authority now and again on admission.
        await peer.guard(pending, dependency=False)
        row['subscription'] = {'commandId': command, 'requestId': row['commandId'], 'status': 'waiting',
                               'continuationId': identity, 'pending': pending}
        receipt = {'commandId': command, 'requestHash': signature, 'operation': 'coordination.subscribe',
                   'target': {'sessionId': source['sessionId']}, 'requestId': row['commandId'], 'status': 'saved'}
        persist(peer, row, receipt)
        target = await claim(peer, row)
    if target:
        await deliver(peer, target)
    return {'receipt': receipt, 'subscription': peer.read(row['commandId'])['subscription'], 'replayed': False}


async def claim(peer, row):
    """Called under the owner lock. Save seal and the stable queue claim together."""
    wait = row.get('subscription')
    if not wait or wait['status'] != 'waiting':
        return None
    response = row.get('response') or {}
    if response.get('status') != 'sealed' or response.get('qualified') is not True:
        if row['status'] in {'completed', 'cancelled', 'failed', 'unknown'}:
            wait.update(status='needs_attention', detail='No qualified saved result; no continuation started')
            persist(peer, row)
        return None
    pending = wait['pending']
    if peer.owner.receipt(pending['commandId']):
        wait.update(status='claimed'); persist(peer, row)
        return None
    try:
        await peer.guard_scope(row)
        await peer.guard(pending, dependency=False)
    except ValueError as error:
        wait.update(status='suppressed', detail=str(error)); persist(peer, row)
        return None
    refs, size = [], 0
    for ref in [response['terminal']['messageId'], *response.get('references', [])]:
        if len(refs) >= 16 or size + len(ref.encode()) > 4096:
            break
        refs.append(ref); size += len(ref.encode())
    pending['peerEnvelope'] = {'version': 1, 'requestId': pending['inputId'], 'grantId': row['grantId'],
        'senderSessionId': pending['senderSessionId'], 'recipientSessionId': pending['target']['sessionId'],
        'mode': 'queue', 'replyToRequestId': row['inputId'], 'references': refs,
        'referencesTruncated': len(refs) < 1 + len(response.get('references', []))}
    pending['text'] = ('A recipient declared a successful result and its exact saved answer and completed generation were confirmed. '
        'Independently check referenced artifacts before relying on them.\n' + response['text'][:6000]
        + ('\n[Result summary truncated; read the exact saved result.]' if len(response['text']) > 6000 else ''))
    if peer.owner.intake.fence:
        pending.update(status='held', detail='Intake paused at result settlement; no automatic continuation')
    wait.update(status='claimed')
    persist(peer, row, pending)
    return pending['target']['sessionId'] if pending['status'] == 'queued' else None


async def deliver(peer, target):
    try:
        await peer.watch(target)
        await peer.drain(target)
    except Exception:
        # Existing intake journals preserve submitting/unknown outcomes. A failed
        # watch leaves only never-admitted claims held for explicit user resume.
        rows = peer.owner.db.execute("SELECT body FROM commands WHERE json_extract(body,'$.operation')='coordination.send' AND json_extract(body,'$.target.sessionId')=? AND json_extract(body,'$.dependencyRequestId') IS NOT NULL AND json_extract(body,'$.status')='queued'", (target,)).fetchall()
        for (body,) in rows:
            pending = json.loads(body)
            pending.update(status='held', detail='Continuation watch unavailable; no work replayed')
            peer.save(pending)
        raise
