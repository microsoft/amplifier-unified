"""Human decisions for bounded peer scope, stored in the existing command journal.

This owner never reads transcripts or starts a model. Pending approvals are
durable facts; expired runtime futures do not create or replay authority.
"""
import asyncio
import hashlib
import json
import time


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def definitions(schema, string):
    sid = {'sessionId': string(512)}
    return {name: {'description': description, 'schema': shape} for name, (description, shape) in {
        'coordination.grant': ('Propose one exact peer scope from a current delivered human sourceMessageId. Pending is not permission; no work starts. Human UI actions may grant directly.', schema({
            **sid, 'participants': {'type': 'array', 'items': string(512), 'minItems': 1, 'maxItems': 8, 'uniqueItems': True},
            'sourceMessageId': string(512), 'purpose': string(4000),
            'modes': {'type': 'array', 'items': {'enum': ['notify', 'queue', 'steer']}, 'minItems': 1, 'maxItems': 3, 'uniqueItems': True},
            'idleStart': {'type': 'boolean'}, 'allowCreate': {'type': 'boolean'},
        }, ['sessionId', 'participants', 'purpose', 'modes'])),
        'coordination.context': ('Read bounded grants and pending proposals for one root. Reading never starts work or confers authority.', schema(sid)),
        'coordination.decide': ('Human-only decision on an exact saved proposal, including after timeout or restart. Never resumes the expired generation.', schema({**sid, 'proposalId': string(200), 'decision': {'enum': ['allow', 'deny']}})),
        'coordination.revoke': ('Revoke peer authority for this participant. Saved history remains; revocation cannot be undone by repeating a grant command.', schema({**sid, 'grantId': string(200)})),
    }.items()}


class Grants:
    def __init__(self, owner):
        self.owner = owner
        owner.db.execute("CREATE INDEX IF NOT EXISTS coordination_grant_source ON commands(json_extract(body,'$.sourceSessionId'),json_extract(body,'$.result.sourceMessageId'))")

    async def identity(self, session, params):
        value = await self.owner.host('inspectCoordinationIdentity', {'session': session})
        if (value.get('sessionId') != session or value.get('kind') != 'root'
                or not value.get('nativeSessionId') or not value.get('workspace')
                or value.get('blocked') or value.get('parentSessionId')):
            raise ValueError('Choose an available ordinary root conversation')
        if params['origin'] == 'agent' and (session != params.get('callerSession')
                or params.get('actorId') != 'agent:' + value['nativeSessionId']):
            raise ValueError('A child or peer cannot borrow this root authority')
        return value

    async def participants(self, source, ids):
        ids = list(dict.fromkeys([source['sessionId'], *ids]))
        if len(ids) > 8:
            raise ValueError('A grant supports at most eight participant roots')
        values = []
        for sid in ids:
            item = await self.identity(sid, {'origin': 'ui'})
            if item['workspace'] != source['workspace']:
                raise ValueError('All participants must belong to this workspace')
            values.append({key: item[key] for key in ('sessionId', 'nativeSessionId', 'workspace', 'locationRevision')})
        return values

    @staticmethod
    def human(message):
        meta = message.get('_meta') or {}
        details = [message, meta.get('amplifier.dev/input') or {}, meta.get('amplifier.dev/history') or {}]
        if (message.get('role') != 'user' or message.get('inputOrigin') not in {'user', 'ui', 'voice'}
                or (message.get('provenance') or {}).get('source') != 'host-admission'
                or not message.get('sourceDigest') or not isinstance(message.get('text'), str)
                or any(row.get(key) for row in details for key in ('questionId', 'scheduledRunId', 'scheduleId', 'peerEnvelope', 'hostAction'))
                or meta.get('amplifier.dev/scheduled')):
            raise ValueError('Permission requires a retained human input, not peer or generated input')
        return message

    def saved(self, identity):
        value = self.owner.receipt(identity)
        if not value or value.get('operation') != 'coordination.grant':
            raise ValueError('Choose the exact saved peer grant or proposal')
        return value

    def persist(self, value):
        self.owner.save(value['commandId'], value['requestHash'], {k: v for k, v in value.items() if k != 'requestHash'})

    async def revalidate(self, row):
        source = await self.identity(row['sourceSessionId'], {'origin': 'ui'})
        value = row['result']
        if digest(value) != row['scopeDigest'] or source['nativeSessionId'] != row['sourceNativeId'] or source['workspace'] != value['workspace'] or source['interruptionRevision'] != row['interruptionRevision']:
            raise ValueError('Proposal scope, source or interruption changed; no permission granted')
        participants = await self.participants(source, value['participants'])
        if participants != row['participantBindings']:
            raise ValueError('A participant moved or changed identity; no permission granted')
        if value.get('sourceMessageId'):
            message = self.human(await self.owner.host('readCoordinationInput', {'session': source['sessionId'], 'messageId': value['sourceMessageId']}))
            if message['sourceDigest'] != value['sourceDigest']:
                raise ValueError('The original human input changed; no permission granted')

    async def decide(self, identity, source, decision):
        # Caller is either the authenticated UI action or our exact review callback.
        async with self.owner.lock:
            row = self.saved(identity)
            if row['sourceSessionId'] != source:
                raise ValueError('Choose the proposal source conversation')
            if row['status'] != 'pending':
                if row.get('decision', {}).get('value') == decision:
                    return row
                raise ValueError('This proposal already has a decision; it cannot be revived')
            if decision == 'allow':
                await self.revalidate(row)
            row.update(status='approved' if decision == 'allow' else 'denied', accepted=decision == 'allow', decision={'value': decision, 'at': time.time()})
            self.persist(row)
            return row

    async def action(self, params):
        args = params['args']; op = params['operation']; origin = params.get('origin')
        if origin not in {'ui', 'agent'} or origin == 'ui' and not params.get('clientId'):
            raise ValueError('Authenticated peer authority context required')
        source = await self.identity(args['sessionId'], params)
        if op == 'coordination.context':
            rows = self.owner.db.execute("""SELECT body FROM commands WHERE json_extract(body,'$.operation')='coordination.grant'
                AND EXISTS(SELECT 1 FROM json_each(json_extract(body,'$.result.participants')) WHERE value=?)
                ORDER BY rowid DESC LIMIT 33""", (source['sessionId'],)).fetchall()
            items = [json.loads(row[0]) for row in rows[:32]]
            # Bounded original context for a human reviewing a saved proposal.
            # Never substitute agent-written purpose text for the human source.
            for row in items:
                if row['status'] != 'pending' or origin != 'ui':
                    continue
                try:
                    message = self.human(await self.owner.host('readCoordinationInput', {'session': row['sourceSessionId'], 'messageId': row['result']['sourceMessageId']}))
                    if message['sourceDigest'] != row['result']['sourceDigest']:
                        raise ValueError('The original request changed')
                    row['reviewSource'] = {'text': message['text'][:2048], 'truncated': len(message['text']) > 2048}
                except Exception:
                    row['reviewSource'] = {'unavailable': True}
            return {'grants': [row for row in items if row['status'] == 'approved'], 'proposals': [row for row in items if row['status'] != 'approved'], 'truncated': len(rows) > 32, 'executionStarted': False,
                    'delivery': {'supported': False, 'reason': 'Guarded peer delivery is not installed yet'}}
        if op == 'coordination.decide':
            if origin != 'ui':
                raise ValueError('Only a human action may decide a saved proposal')
            return {'receipt': await self.decide(args['proposalId'], source['sessionId'], args['decision']), 'replayed': False}
        if op == 'coordination.revoke':
            async with self.owner.lock:
                row = self.saved(args['grantId'])
                if source['sessionId'] not in row['result']['participants']:
                    raise ValueError('This root is outside the peer grant')
                if row['status'] != 'revoked':
                    row.update(status='revoked', accepted=False, revokedAt=time.time())
                    row['result'].update(revoked=True, revision=row['result']['revision'] + 1)
                    self.persist(row)
                return {'receipt': row, 'replayed': False}
        command = params.get('commandId')
        if not isinstance(command, str) or not 1 <= len(command) <= 200 or not args['purpose'].strip():
            raise ValueError('Stable command identity and a concrete purpose are required')
        signature = digest({key: params.get(key) for key in ('operation', 'args', 'origin', 'callerSession', 'clientId', 'actorId')})
        async with self.owner.lock:
            prior = self.owner.receipt(command)
            if prior:
                if prior['requestHash'] != signature:
                    raise ValueError('Coordination command identity conflicts')
                return {'receipt': prior, 'replayed': False}
            bindings = await self.participants(source, args['participants'])
            value = {'id': command, 'participants': [item['sessionId'] for item in bindings], 'purpose': args['purpose'],
                     'modes': args['modes'], 'idleStart': args.get('idleStart', False), 'allowCreate': args.get('allowCreate', False),
                     'workspace': source['workspace'], 'issuer': 'human', 'mediation': origin, 'revision': 1, 'revoked': False, 'createdAt': time.time()}
            if origin == 'agent':
                if not args.get('sourceMessageId'):
                    raise ValueError('A current delivered sourceMessageId is required')
                message = self.human(await self.owner.host('readCoordinationInput', {'session': source['sessionId'], 'messageId': args['sourceMessageId'], 'actorId': params['actorId'], 'active': True}))
                delivery = message.get('delivery') or {}
                if delivery.get('nativeSessionId') != source['nativeSessionId'] or delivery.get('interruptionRevision') != source['interruptionRevision']:
                    raise ValueError('Current root delivery changed')
                used = self.owner.db.execute("""SELECT 1 FROM commands WHERE json_extract(body,'$.operation')='coordination.grant'
                    AND json_extract(body,'$.sourceSessionId')=? AND json_extract(body,'$.result.sourceMessageId')=? LIMIT 1""", (source['sessionId'], args['sourceMessageId'])).fetchone()
                if used:
                    raise ValueError('This human input already funds a proposal; do not widen it')
                value.update(sourceMessageId=args['sourceMessageId'], sourceDigest=message['sourceDigest'])
            elif args.get('sourceMessageId'):
                raise ValueError('Direct human grants do not borrow a prior source message')
            row = {'commandId': command, 'requestHash': signature, 'operation': op, 'status': 'pending' if origin == 'agent' else 'approved',
                   'target': {'sessionId': source['sessionId']}, 'sourceSessionId': source['sessionId'], 'sourceNativeId': source['nativeSessionId'],
                   'participantBindings': bindings, 'interruptionRevision': source['interruptionRevision'], 'scopeDigest': digest(value),
                   'accepted': origin == 'ui', 'result': value, 'replayed': False, 'executionStarted': False}
            if origin == 'ui':
                row['decision'] = {'value': 'allow', 'at': time.time()}
            self.persist(row)
        if origin == 'agent':
            # No command lock spans a human wait. Timeout leaves the proposal intact.
            try:
                review = await asyncio.wait_for(self.owner.host('reviewCoordinationGrant', {'session': source['sessionId'], 'proposalId': command, 'scope': value, 'sourceText': message['text']}), timeout=80)
            except (Exception, asyncio.CancelledError):
                review = {'pending': True}
            if review.get('decision') in {'allow', 'deny'}:
                row = await self.decide(command, source['sessionId'], review['decision'])
            else:
                row = self.saved(command)
        return {'receipt': row, 'replayed': False}
