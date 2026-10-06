"""Saved peer requests. Durable claims precede effects; uncertain work stays put."""
import hashlib
import json
import uuid
from datetime import UTC, datetime
from .grants import digest


def definitions(schema, string):
    return {
        'coordination.send': {'description': 'Send a saved peer message within a human-approved root scope. Notify leaves a message without starting work; it reaches context at the next natural model request. Queue requests a response and rechecks permission, task and stop state at native admission. Steer binds the current active generation and never starts a later turn. Acceptance is not completion.',
            'schema': schema({'sessionId': string(512), 'recipientSessionId': string(512), 'grantId': string(200), 'text': string(12000), 'mode': {'enum': ['notify', 'queue', 'steer']} })},
        'coordination.result': {'description': 'Inspect this exact peer request and its delivery receipt. A completed turn does not independently qualify a successful result. Never resends work.',
            'schema': schema({'sessionId': string(512), 'requestId': string(200)})},
        'coordination.resume': {'description': 'Human-only release of one held, never-admitted peer request. Rechecks its original permission, task, configuration and stop state; uncertain or already admitted work cannot be resumed.',
            'schema': schema({'sessionId': string(512), 'requestId': string(200)})},
        'coordination.cancel': {'description': 'Human-only cancellation of one queued or held peer request before admission. Does not stop or undo admitted work.',
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

    def context(self, session):
        rows = self.owner.db.execute("SELECT body FROM commands WHERE json_extract(body,'$.operation')='coordination.send' AND (json_extract(body,'$.senderSessionId')=? OR json_extract(body,'$.target.sessionId')=?) ORDER BY rowid DESC LIMIT 33", (session, session)).fetchall()
        # A context read is bounded and never inspects or starts the recipient.
        items = []
        for (body,) in rows[:32]:
            row = json.loads(body)
            items.append({key: row[key] for key in ('commandId', 'inputId', 'senderSessionId', 'target', 'grantId', 'mode', 'status')})
            items[-1].update(text=row['text'][:2048], textTruncated=len(row['text']) > 2048,
                             canResume=row['mode'] == 'queue' and row['status'] == 'held', canCancel=row['mode'] == 'queue' and row['status'] in {'held', 'queued'})
            if row.get('detail'): items[-1]['detail'] = row['detail'][:512]
        notifications = self.owner.db.execute("SELECT body FROM commands WHERE json_extract(body,'$.operation')='coordination.send' AND json_extract(body,'$.mode')='notify' AND json_extract(body,'$.target.sessionId')=? ORDER BY json_extract(body,'$.createdAt') DESC LIMIT 33", (session,)).fetchall()
        return {'requests': items, 'requestsTruncated': len(rows) > 32,
                'notifications': [self.notification(json.loads(body)) for (body,) in reversed(notifications[:32])],
                'notificationsTruncated': len(notifications) > 32}

    @staticmethod
    def notification(row):
        return {'id': row['inputId'], 'text': row['text'], 'createdAt': row['createdAt'],
                'peerEnvelope': row['peerEnvelope'], 'contextDelivered': row.get('contextDelivered', False),
                'contextSuppressed': row.get('contextSuppressed', False)}

    async def notifications(self, params):
        """Only the owning native root may consume its passive inbox. Never starts it."""
        sid, args = params['session'], params['args']
        source = await self.owner.grants.identity(sid, {'origin': 'ui'})
        if set(args) - {'acknowledge'} or not isinstance(args.get('acknowledge', []), list):
            raise ValueError('Exact passive notification acknowledgement required')
        ids = args.get('acknowledge', [])
        if len(ids) > 32 or any(not isinstance(value, str) or not 1 <= len(value) <= 128 for value in ids) or len(set(ids)) != len(ids):
            raise ValueError('Acknowledge at most 32 exact notification identities')
        async with self.owner.lock:
            # Check the complete batch before writing any acknowledgement.
            acknowledged = []
            for identity in ids:
                found = self.owner.db.execute("SELECT body FROM commands WHERE json_extract(body,'$.inputId')=? AND json_extract(body,'$.operation')='coordination.send'", (identity,)).fetchone()
                row = json.loads(found[0]) if found else None
                if not row or row['mode'] != 'notify' or row['target']['sessionId'] != sid or row['recipientNativeId'] != source['nativeSessionId']:
                    raise ValueError('Notification does not belong to this native recipient')
                acknowledged.append(row)
            for row in acknowledged:
                row['contextDelivered'] = True
                self.save(row)
            rows = self.owner.db.execute("SELECT body FROM commands WHERE json_extract(body,'$.operation')='coordination.send' AND json_extract(body,'$.mode')='notify' AND json_extract(body,'$.target.sessionId')=? AND COALESCE(json_extract(body,'$.contextDelivered'),0)=0 AND COALESCE(json_extract(body,'$.contextSuppressed'),0)=0 ORDER BY json_extract(body,'$.createdAt') LIMIT 32", (sid,)).fetchall()
            events = []
            for (body,) in rows:
                row = json.loads(body)
                try:
                    await self.guard_scope(row)
                except ValueError:
                    row['contextSuppressed'] = True
                    self.save(row)
                    continue
                events.append(self.notification(row))
        return {'notifications': events, 'executionStarted': False}

    async def control(self, params, source):
        if params.get('origin') != 'ui' or not params.get('clientId'):
            raise ValueError('Only a human action may release or cancel saved peer work')
        if not params.get('deliveryEnabled'):
            raise ValueError('Guarded peer delivery is unavailable')
        command, args, op = params.get('commandId'), params['args'], params['operation']
        if not isinstance(command, str) or not 1 <= len(command) <= 200:
            raise ValueError('A stable control identity is required')
        signature = digest({key: params.get(key) for key in ('operation', 'args', 'origin', 'clientId', 'actorId')})
        async with self.owner.lock:
            row = self.read(args['requestId'])
            if row['mode'] != 'queue':
                raise ValueError('Only never-admitted queued peer work can be resumed or cancelled')
            if source['sessionId'] not in {row['senderSessionId'], row['target']['sessionId']}:
                raise ValueError('This root is outside the saved peer request')
            previous = self.owner.receipt(command)
            if previous:
                if previous['requestHash'] != signature:
                    raise ValueError('Peer control identity conflicts')
                return {'receipt': previous, 'request': row, 'replayed': False}
            if row['status'] not in ({'held'} if op == 'coordination.resume' else {'held', 'queued'}):
                raise ValueError('Only unadmitted saved requests can be changed; inspect the original outcome')
            if op == 'coordination.resume':
                await self.guard(row)
            control = {'commandId': command, 'operation': op, 'status': 'accepted',
                       'target': row['target'], 'requestId': row['commandId']}
            row.update(status='queued' if op == 'coordination.resume' else 'cancelled',
                       detail='Released by a human action' if op == 'coordination.resume' else 'Cancelled before admission')
            # The human decision and original queue transition commit together.
            for identity, sig, value in [(command, signature, control), (row['commandId'], row['requestHash'], row)]:
                self.owner.db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?)', (identity, sig, json.dumps(value)))
            self.owner.db.commit()
        if op == 'coordination.resume':
            try:
                await self.watch(row['target']['sessionId'])
                await self.drain(row['target']['sessionId'])
            except BaseException:
                current = self.read(row['commandId'])
                if current['status'] == 'queued':
                    current.update(status='held', detail='Delivery watch unavailable; the request has not been admitted')
                    self.save(current)
                raise
        else:
            await self.release_unused_watch(row['target']['sessionId'])
        return {'receipt': control, 'request': self.read(row['commandId']), 'replayed': False}

    async def inspect(self, sid):
        state = await self.owner.host('inspectPeerRecipient', {'session': sid})
        if state.get('available') is not True:
            raise ValueError('Recipient task and admission state are unavailable')
        return state

    async def guard_scope(self, row):
        grant = self.owner.grants.saved(row['grantId'])
        if grant['status'] != 'approved' or grant['result']['revoked'] or grant['result']['revision'] != row['grantRevision']:
            raise ValueError('The human peer scope was revoked or changed')
        scope = grant['result']
        if row['mode'] not in scope['modes'] or row['mode'] == 'queue' and not scope['idleStart']:
            raise ValueError('This scope does not authorize this delivery mode')
        for sid in (row['senderSessionId'], row['target']['sessionId']):
            current = await self.owner.grants.identity(sid, {'origin': 'ui'})
            binding = next((value for value in grant['participantBindings'] if value['sessionId'] == sid), None)
            if not binding or any(current.get(key) != value for key, value in binding.items()):
                raise ValueError('A peer participant moved or changed identity')

    async def guard(self, row):
        await self.guard_scope(row)
        target = await self.inspect(row['target']['sessionId'])
        if target['interruptionRevision'] != row['interruptionRevision'] or target.get('blocked'):
            raise ValueError('The recipient was stopped or became unavailable')
        task = target.get('task') or {}
        if task.get('status') in {'paused', 'blocked', 'completed', 'cancelled', 'stopped'} or (task.get('id'), task.get('revision')) != (row['taskId'], row['taskRevision']):
            raise ValueError('The recipient task changed or is no longer active')
        if target['configurationHash'] != row['configurationHash']:
            raise ValueError('The recipient configuration changed')
        if row['mode'] == 'steer':
            mount = target.get('activeSteering') or {}
            if (target.get('status') != 'working' or mount.get('supported') is not True
                    or not row.get('targetGenerationId') or not row.get('activeTurnId')
                    or mount.get('generationId') != row['targetGenerationId']
                    or mount.get('activeTurnId') != row['activeTurnId']
                    or target.get('activeTurnId') != row['activeTurnId']
                    or target.get('executionRevision') != row['expectedExecutionRevision']):
                raise ValueError('The anchored peer generation is no longer available')
        return target

    async def reconcile_steering(self, row):
        """Only exact durable Host dispositions refine delivery; no retry or turn."""
        if row['mode'] != 'steer' or row['status'] not in {'submitting', 'accepted', 'unknown'}:
            return row
        proof = await self.owner.host('inspectPeerSteering', {'session': row['target']['sessionId'], 'inputId': row['inputId']})
        if (proof.get('inputId') == row['inputId'] and proof.get('grantId') == row['grantId']
                and proof.get('activeTurnId') == row['activeTurnId']
                and proof.get('targetGenerationId') == row['targetGenerationId']
                and proof.get('executionRevision') == row['expectedExecutionRevision']
                and proof.get('disposition') in {'applied', 'held', 'unknown'}):
            row.update(status=proof['disposition'], steering=proof)
            self.save(row)
        return row

    async def submit_steering(self, row):
        try:
            target = await self.guard(row)
        except ValueError as error:
            row.update(status='suppressed', detail=str(error)); self.save(row)
            return
        try:
            result = await self.owner.host('submitPeerSteering', {'session': row['target']['sessionId'], 'input': {
                'commandId': row['inputId'], 'grantId': row['grantId'], 'text': row['text'], 'peerEnvelope': row['peerEnvelope'],
                'taskId': row['taskId'], 'taskRevision': row['taskRevision'],
                'activeTurnId': row['activeTurnId'], 'targetGenerationId': row['targetGenerationId'],
                'expectedExecutionRevision': row['expectedExecutionRevision'], 'expectedInterruptionRevision': row['interruptionRevision'],
                'expectedConfigurationHash': row['configurationHash'], 'expectedNativeSessionId': target['nativeSessionId'],
                'expectedNativeLocationRevision': target['nativeLocationRevision']}})
            row = self.read(row['commandId'])
            if result.get('accepted') is True:
                row.update(status='accepted', admission=result)
            elif result.get('accepted') is False and result.get('executed') is False:
                row.update(status='suppressed', admission=result)
            else:
                row.update(status='unknown', detail='Peer steering disposition is unconfirmed')
            self.save(row)
            await self.reconcile_steering(row)
        except BaseException:
            row = self.read(row['commandId'])
            if row['status'] in {'submitting', 'accepted'}:
                row.update(status='unknown', detail='Steering admission outcome unknown; no replay'); self.save(row)
            raise

    async def action(self, params):
        args = params['args']
        source = await self.owner.grants.identity(args['sessionId'], params)
        if params['operation'] in {'coordination.resume', 'coordination.cancel'}:
            return await self.control(params, source)
        if params['operation'] == 'coordination.result':
            row = self.read(args['requestId'])
            if source['sessionId'] not in {row['senderSessionId'], row['target']['sessionId']}:
                raise ValueError('This root is outside the saved peer request')
            if params.get('steeringEnabled'):
                row = await self.reconcile_steering(row)
            return {'receipt': row, 'qualified': False, 'qualificationSupported': False, 'replayed': False}
        if not params.get('deliveryEnabled'):
            raise ValueError('Guarded peer delivery is unavailable')
        if args.get('mode') == 'steer' and not params.get('steeringEnabled'):
            raise ValueError('Anchored peer steering is not installed')
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
            if args['mode'] == 'notify':
                target = await self.owner.grants.identity(args['recipientSessionId'], {'origin': 'ui'})
                identity = 'peer:' + hashlib.sha256(command.encode()).hexdigest()
                row = {'commandId': command, 'requestHash': signature, 'operation': 'coordination.send', 'inputId': identity,
                       'senderSessionId': source['sessionId'], 'target': {'sessionId': args['recipientSessionId']},
                       'recipientNativeId': target['nativeSessionId'], 'grantId': args['grantId'],
                       'grantRevision': grant['result']['revision'], 'mode': 'notify', 'status': 'notified',
                       'text': args['text'], 'createdAt': datetime.now(UTC).isoformat(),
                       'peerEnvelope': {'version': 1, 'requestId': identity, 'grantId': args['grantId'],
                           'senderSessionId': source['sessionId'], 'recipientSessionId': args['recipientSessionId'], 'mode': 'notify'}}
                await self.guard_scope(row)
                self.save(row)
                await self.owner.notify('owner/changed', {'session': args['recipientSessionId']})
                return {'receipt': row, 'executionStarted': False, 'replayed': False}
            target = await self.inspect(args['recipientSessionId'])
            task = target.get('task') or {}
            identity = 'peer:' + hashlib.sha256(command.encode()).hexdigest()
            row = {'commandId': command, 'requestHash': signature, 'operation': 'coordination.send', 'inputId': identity,
                   'senderSessionId': source['sessionId'], 'target': {'sessionId': args['recipientSessionId']},
                   'grantId': args['grantId'], 'grantRevision': grant['result']['revision'], 'mode': args['mode'],
                   'taskId': task.get('id'), 'taskRevision': task.get('revision'), 'configurationHash': target['configurationHash'],
                   'interruptionRevision': target['interruptionRevision'], 'status': 'queued', 'text': args['text'],
                   'peerEnvelope': {'version': 1, 'requestId': identity, 'grantId': args['grantId'], 'senderSessionId': source['sessionId'], 'recipientSessionId': args['recipientSessionId'], 'mode': args['mode']}}
            if row['mode'] == 'steer':
                mount = target.get('activeSteering') or {}
                row.update(status='submitting', activeTurnId=target.get('activeTurnId'),
                           targetGenerationId=mount.get('generationId'), expectedExecutionRevision=target['executionRevision'])
                await self.guard_scope(row)
            else:
                await self.guard(row)
            self.save(row)
        if row['mode'] == 'steer':
            await self.submit_steering(row)
            return {'receipt': self.read(command), 'replayed': False, 'executionStarted': False}
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
            await self.release_unused_watch(session)

    async def release_unused_watch(self, session):
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
            if row['mode'] == 'steer':
                proof = await self.owner.host('inspectPeerSteering', {'session': sid, 'inputId': row['inputId']})
                if (proof.get('admitted') is not True or proof.get('inputId') != row['inputId']
                        or proof.get('grantId') != row['grantId'] or proof.get('executionRevision') != row['expectedExecutionRevision']
                        or proof.get('activeTurnId') != row['activeTurnId'] or proof.get('targetGenerationId') != row['targetGenerationId']
                        or args.get('activeInputId') != row['activeTurnId'] or args.get('targetGenerationId') != row['targetGenerationId']):
                    raise ValueError('The exact peer steering admission is no longer active')
            else:
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
