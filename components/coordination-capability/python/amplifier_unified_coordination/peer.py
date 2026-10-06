"""Saved peer requests. Durable claims precede effects; uncertain work stays put."""
import hashlib
import json
import uuid
from .grants import digest


def definitions(schema, string):
    return {
        'coordination.send': {'description': 'Send a saved peer request within a human-approved root scope. Queue delivery rechecks permission, task and stop state at native admission. Acceptance is not completion.',
            'schema': schema({'sessionId': string(512), 'recipientSessionId': string(512), 'grantId': string(200), 'text': string(12000), 'mode': {'enum': ['queue']} })},
        'coordination.result': {'description': 'Inspect this exact peer request and its delivery receipt. A completed turn does not independently qualify a successful result. Never resends work.',
            'schema': schema({'sessionId': string(512), 'requestId': string(200)})},
    }


class Peer:
    def __init__(self, owner):
        self.owner = owner
        self.draining = set()
        self.watches = {}
        # A new process cannot recover an in-flight effect or the old watch.
        for (body,) in owner.db.execute("SELECT body FROM commands WHERE json_extract(body,'$.operation')='coordination.send' AND json_extract(body,'$.status') IN ('queued','submitting','accepted')").fetchall():
            row = json.loads(body)
            row.update(status='held' if row['status'] == 'queued' else 'unknown', detail='Owner restarted; saved work was not replayed')
            self.save(row)

    def save(self, row):
        self.owner.save(row['commandId'], row['requestHash'], row)

    def read(self, identity):
        row = self.owner.receipt(identity)
        if not row or row.get('operation') != 'coordination.send':
            raise ValueError('Choose the exact saved peer request')
        return row

    async def inspect(self, sid):
        state = await self.owner.host('inspectPeerRecipient', {'session': sid})
        if state.get('available') is not True:
            raise ValueError('Recipient task and admission state are unavailable')
        return state

    async def guard(self, row):
        grant = self.owner.grants.saved(row['grantId'])
        if grant['status'] != 'approved' or grant['result']['revoked'] or grant['result']['revision'] != row['grantRevision']:
            raise ValueError('The human peer scope was revoked or changed')
        scope = grant['result']
        if 'queue' not in scope['modes'] or not scope['idleStart']:
            raise ValueError('This scope does not authorize queued starts')
        for sid in (row['senderSessionId'], row['target']['sessionId']):
            current = await self.owner.grants.identity(sid, {'origin': 'ui'})
            binding = next((value for value in grant['participantBindings'] if value['sessionId'] == sid), None)
            if not binding or any(current.get(key) != value for key, value in binding.items()):
                raise ValueError('A peer participant moved or changed identity')
        target = await self.inspect(row['target']['sessionId'])
        if target['interruptionRevision'] != row['interruptionRevision'] or target.get('blocked'):
            raise ValueError('The recipient was stopped or became unavailable')
        task = target.get('task') or {}
        if task.get('status') in {'paused', 'blocked', 'completed', 'cancelled', 'stopped'} or (task.get('id'), task.get('revision')) != (row['taskId'], row['taskRevision']):
            raise ValueError('The recipient task changed or is no longer active')
        if target['configurationHash'] != row['configurationHash']:
            raise ValueError('The recipient configuration changed')
        return target

    async def action(self, params):
        args = params['args']
        source = await self.owner.grants.identity(args['sessionId'], params)
        if params['operation'] == 'coordination.result':
            row = self.read(args['requestId'])
            if source['sessionId'] not in {row['senderSessionId'], row['target']['sessionId']}:
                raise ValueError('This root is outside the saved peer request')
            return {'receipt': row, 'qualified': False, 'qualificationSupported': False, 'replayed': False}
        if not params.get('deliveryEnabled'):
            raise ValueError('Guarded peer delivery is unavailable')
        if params.get('origin') not in {'ui', 'agent'} or params['origin'] == 'ui' and not params.get('clientId'):
            raise ValueError('Authenticated peer action required')
        command = params.get('commandId')
        if not isinstance(command, str) or not 1 <= len(command) <= 200 or not args['text'].strip() or args['recipientSessionId'] == source['sessionId']:
            raise ValueError('A bounded identity, message and distinct peer are required')
        signature = digest({key: params.get(key) for key in ('operation', 'args', 'origin', 'callerSession', 'actorId', 'clientId')})
        async with self.owner.lock:
            previous = self.owner.receipt(command)
            if previous:
                if previous['requestHash'] != signature:
                    raise ValueError('Peer command identity conflicts')
                return {'receipt': previous, 'replayed': False}
            grant = self.owner.grants.saved(args['grantId'])
            target = await self.inspect(args['recipientSessionId'])
            task = target.get('task') or {}
            identity = 'peer:' + hashlib.sha256(command.encode()).hexdigest()
            row = {'commandId': command, 'requestHash': signature, 'operation': 'coordination.send', 'inputId': identity,
                   'senderSessionId': source['sessionId'], 'target': {'sessionId': args['recipientSessionId']},
                   'grantId': args['grantId'], 'grantRevision': grant['result']['revision'], 'mode': 'queue',
                   'taskId': task.get('id'), 'taskRevision': task.get('revision'), 'configurationHash': target['configurationHash'],
                   'interruptionRevision': target['interruptionRevision'], 'status': 'queued', 'text': args['text'],
                   'peerEnvelope': {'version': 1, 'requestId': identity, 'grantId': args['grantId'], 'senderSessionId': source['sessionId'], 'recipientSessionId': args['recipientSessionId'], 'mode': 'queue'}}
            await self.guard(row)
            self.save(row)
        try:
            await self.watch(args['recipientSessionId'])
            await self.drain(args['recipientSessionId'])
        except BaseException:
            # A saved queue remains held if its watch cannot be established.
            current = self.read(command)
            if current['status'] == 'queued':
                current.update(status='held', detail='Peer delivery watch unavailable; no work replayed')
                self.save(current)
            raise
        return {'receipt': self.read(command), 'replayed': False}

    async def watch(self, session):
        if session in self.watches.values():
            return
        if len(self.watches) >= 24:
            raise ValueError('Peer queue watch capacity reached')
        token = 'peer:' + str(uuid.uuid4())
        self.watches[token] = session
        try:
            await self.owner.host('watch', {'token': token, 'sessions': [session], 'clientId': ''})
        except BaseException:
            self.watches.pop(token, None)
            raise

    async def drain(self, session):
        if session in self.draining or self.owner.intake.fence:
            return
        self.draining.add(session)
        try:
            ids = self.owner.db.execute("SELECT id FROM commands WHERE json_extract(body,'$.operation')='coordination.send' AND json_extract(body,'$.target.sessionId')=? AND json_extract(body,'$.status')='queued' ORDER BY rowid LIMIT 32", (session,)).fetchall()
            for (identity,) in ids:
                async with self.owner.lock:
                    row = self.read(identity)
                    if row['status'] != 'queued':
                        continue
                    try:
                        target = await self.guard(row)
                    except Exception as error:
                        row.update(status='suppressed', detail=str(error)); self.save(row); continue
                    if target.get('status') != 'idle':
                        return
                    row.update(status='submitting', expectedExecutionRevision=target['executionRevision'])
                    self.save(row)
                try:
                    result = await self.owner.host('submitPeerInput', {'session': session, 'input': {
                        'commandId': row['inputId'], 'grantId': row['grantId'], 'text': row['text'], 'peerEnvelope': row['peerEnvelope'],
                        'taskId': row['taskId'], 'taskRevision': row['taskRevision'],
                        'expectedExecutionRevision': target['executionRevision'], 'expectedInterruptionRevision': row['interruptionRevision'],
                        'expectedConfigurationHash': row['configurationHash'], 'expectedNativeSessionId': target['nativeSessionId'],
                        'expectedNativeLocationRevision': target['nativeLocationRevision']}})
                    row = self.read(identity)
                    if row['status'] == 'submitting':
                        row.update(status='accepted' if result.get('accepted') else 'suppressed', admission=result)
                        self.save(row)
                except BaseException:
                    row = self.read(identity)
                    if row['status'] == 'submitting':
                        row.update(status='unknown', detail='Admission outcome unknown; saved work was not replayed'); self.save(row)
                    raise
                return
        finally:
            self.draining.discard(session)
            pending = self.owner.db.execute("SELECT 1 FROM commands WHERE json_extract(body,'$.operation')='coordination.send' AND json_extract(body,'$.target.sessionId')=? AND json_extract(body,'$.status')='queued' LIMIT 1", (session,)).fetchone()
            if not pending:
                for token, target in list(self.watches.items()):
                    if target == session:
                        self.watches.pop(token, None)
                        await self.owner.host('unwatch', {'token': token})

    async def admission(self, params):
        if self.owner.intake.fence:
            return {'admitted': False, 'reason': 'Coordination intake is paused'}
        sid, args = params['session'], params['args']
        found = self.owner.db.execute("SELECT id FROM commands WHERE json_extract(body,'$.operation')='coordination.send' AND json_extract(body,'$.inputId')=?", (args.get('inputId'),)).fetchone()
        if not found:
            return {'admitted': False, 'reason': 'No saved peer request'}
        row = self.read(found[0])
        if row['status'] not in {'submitting', 'accepted'} or row['target']['sessionId'] != sid or any(args.get(key) != row[key] for key in ('grantId', 'taskId', 'taskRevision')):
            return {'admitted': False, 'reason': 'The exact peer request is not being admitted'}
        try:
            target = await self.guard(row)
            proof = target.get('peerAdmission') or {}
            if (proof.get('inputId') != row['inputId'] or proof.get('grantId') != row['grantId']
                    or proof.get('executionRevision') != row['expectedExecutionRevision']
                    or proof.get('admittedExecutionRevision') != target['executionRevision']
                    or target.get('activeTurnId') != row['inputId']):
                raise ValueError('The original host admission is no longer active')
        except Exception as error:
            return {'admitted': False, 'reason': str(error)}
        return {'admitted': True, 'message': {'text': row['text'], 'peerEnvelope': row['peerEnvelope']}}

    async def settled(self, event):
        found = self.owner.db.execute("SELECT id FROM commands WHERE json_extract(body,'$.operation')='coordination.send' AND json_extract(body,'$.inputId')=? AND json_extract(body,'$.target.sessionId')=?", (event['commandId'], event['session'])).fetchone()
        if found:
            async with self.owner.lock:
                row = self.read(found[0])
                if row['status'] in {'submitting', 'accepted', 'unknown'}:
                    row.update(status='completed' if event['status'] == 'completed' else event['status'], terminal={'status': event['status'], 'inputId': event['commandId']})
                    self.save(row)
        await self.drain(event['session'])

    async def close(self):
        for token in list(self.watches):
            await self.owner.host('unwatch', {'token': token})
        self.watches.clear()
