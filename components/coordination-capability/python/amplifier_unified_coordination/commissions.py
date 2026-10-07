"""Durable root-task creation through the existing Host configuration-copy port.

Wait for the source to become idle; reserve before crossing the owner boundary.
Unknown creation is never retried. A created chat survives a suppressed brief.
"""
import hashlib
import json
from .grants import digest


def definition(schema, string):
    return {'description': 'Commission a durable root chat under an explicit allowCreate, queue and idle-start grant. Waits for idle and copies the unchanged source settings; preserves creator/request links and an output namespace. Does not select the new chat. Pending creation is not completion; inspect coordination.context or the original command receipt.',
            'schema': schema({'sessionId': string(512), 'grantId': string(200), 'title': string(100),
                              'text': string(12000), 'references': {'type': 'array', 'maxItems': 16, 'items': string(2000)}},
                             ['sessionId', 'grantId', 'title', 'text'])}


class Commissions:
    def __init__(self, owner):
        self.owner = owner
        self.draining = set()
        for (body,) in owner.db.execute("SELECT body FROM commands WHERE json_extract(body,'$.operation')='coordination.create' AND json_extract(body,'$.status') IN ('queued','creating','created_initial_pending')").fetchall():
            row = json.loads(body)
            row.update(status='held' if row['status'] == 'queued' else 'unknown',
                       detail='Owner restarted; creation or initial input was not replayed')
            self.save(row)

    def save(self, row):
        self.owner.save(row['commandId'], row['requestHash'], row)

    def context(self, session):
        rows = self.owner.db.execute("SELECT body FROM commands WHERE json_extract(body,'$.operation')='coordination.create' AND (json_extract(body,'$.senderSessionId')=? OR json_extract(body,'$.createdSessionId')=?) ORDER BY rowid DESC LIMIT 33", (session, session)).fetchall()
        items = []
        for (body,) in rows[:32]:
            row = json.loads(body)
            items.append({key: row[key] for key in ('commandId', 'senderSessionId', 'createdSessionId', 'title', 'status', 'initialRequestId', 'initialDelivery', 'relationship', 'detail') if key in row})
            items[-1].update(canResume=row['status'] == 'held', canCancel=row['status'] in {'held', 'queued'})
        return {'commissions': items, 'commissionsTruncated': len(rows) > 32}

    def relationship(self, session, grant):
        rows = self.owner.db.execute("SELECT body FROM commands WHERE json_extract(body,'$.operation')='coordination.create' AND json_extract(body,'$.createdSessionId')=? LIMIT 2", (session,)).fetchall()
        row = json.loads(rows[0][0]) if len(rows) == 1 else {}
        return row.get('relationship') if row.get('grantId') == grant else None

    async def guard(self, row, *, capacity=True):
        await self.owner.peer.guard_scope({**row, 'target': {'sessionId': row['senderSessionId']}, 'mode': 'queue'})
        grant = self.owner.grants.saved(row['grantId'])
        if not grant['result']['allowCreate'] or capacity and len(grant['result']['participants']) >= 8:
            raise ValueError('This grant does not allow another commissioned chat')
        source = await self.owner.peer.inspect(row['senderSessionId'])
        task = source.get('task') or {}
        if (source.get('blocked') or source['interruptionRevision'] != row['interruptionRevision']
                or source['configurationHash'] != row['configurationHash']
                or (task.get('id'), task.get('revision')) != (row['taskId'], row['taskRevision'])
                or task.get('status') in {'paused', 'blocked', 'completed', 'cancelled', 'stopped'}):
            raise ValueError('Source task, configuration or stop state changed')
        return source, grant

    async def action(self, params):
        if (not params.get('creationEnabled') or not params.get('deliveryEnabled')
                or params.get('origin') not in {'agent', 'ui'}
                or params['origin'] == 'ui' and not params.get('clientId')):
            raise ValueError('Authenticated configuration-copy and guarded delivery ports are required')
        args = params['args']; source = await self.owner.grants.identity(args['sessionId'], params)
        command = params.get('commandId')
        if not isinstance(command, str) or not 1 <= len(command) <= 200 or not args['title'].strip() or not args['text'].strip():
            raise ValueError('A bounded creation identity, title and brief are required')
        if len(json.dumps(args.get('references', []), ensure_ascii=False).encode()) > 4096 or len(args['text'].encode()) > 32000:
            raise ValueError('Commission references or brief exceed the bounded context')
        signature = digest({key: params.get(key) for key in ('operation', 'args', 'origin', 'callerSession', 'actorId', 'clientId')})
        async with self.owner.lock:
            prior = self.owner.receipt(command)
            if prior:
                if prior['requestHash'] != signature: raise ValueError('Commission identity conflicts')
                return {'receipt': prior, 'replayed': False}
            state = await self.owner.peer.inspect(source['sessionId']); task = state.get('task') or {}
            grant = self.owner.grants.saved(args['grantId']); stable = hashlib.sha256(command.encode()).hexdigest()
            row = {'commandId': command, 'requestHash': signature, 'operation': 'coordination.create',
                   'senderSessionId': source['sessionId'], 'target': {'sessionId': source['sessionId']},
                   'grantId': args['grantId'], 'grantRevision': grant['result']['revision'],
                   'configurationHash': state['configurationHash'], 'interruptionRevision': state['interruptionRevision'],
                   'taskId': task.get('id'), 'taskRevision': task.get('revision'), 'title': args['title'],
                   'text': args['text'], 'references': args.get('references', []), 'status': 'queued',
                   'creationCommandId': 'commission:' + stable, 'initialRequestId': 'brief:' + stable,
                   'principal': {key: params.get(key) for key in ('origin', 'callerSession', 'actorId', 'clientId')}}
            await self.guard(row)
            # Reserve capacity across queued/creating commissions, not just saved participants.
            reserved = self.owner.db.execute("SELECT COUNT(*) FROM commands WHERE json_extract(body,'$.operation')='coordination.create' AND json_extract(body,'$.grantId')=? AND json_extract(body,'$.status') IN ('queued','held','creating','unknown') AND json_extract(body,'$.createdSessionId') IS NULL", (row['grantId'],)).fetchone()[0]
            if len(grant['result']['participants']) + reserved >= 8: raise ValueError('Commission participant capacity is reserved')
            self.save(row)
        try:
            await self.owner.peer.watch(source['sessionId'])
            await self.drain(source['sessionId'])
        except BaseException:
            current = self.owner.receipt(command)
            if current['status'] == 'queued': current.update(status='held', detail='Creation watch unavailable'); self.save(current)
            raise
        return {'receipt': self.owner.receipt(command), 'replayed': False}

    async def control(self, params, source, row):
        if params.get('origin') != 'ui' or not params.get('clientId') or source['sessionId'] != row['senderSessionId']:
            raise ValueError('Only a human in the source chat may release or cancel a commission')
        if not params.get('creationEnabled'): raise ValueError('Configuration-copy port is unavailable')
        command = params.get('commandId')
        if not isinstance(command, str) or not 1 <= len(command) <= 200:
            raise ValueError('A stable control identity is required')
        signature = digest({key: params.get(key) for key in ('operation', 'args', 'origin', 'clientId', 'actorId')})
        async with self.owner.lock:
            row = self.owner.receipt(row['commandId'])
            previous = self.owner.receipt(command)
            if previous:
                if previous['requestHash'] != signature: raise ValueError('Commission control identity conflicts')
                return {'receipt': previous, 'request': row, 'replayed': False}
            if params['operation'] == 'coordination.cancel':
                if row['status'] not in {'queued', 'held'}: raise ValueError('Creation is already admitted or uncertain')
                row.update(status='cancelled', detail='Cancelled before creation')
            else:
                if row['status'] != 'held': raise ValueError('Only never-admitted held creation may resume')
                await self.guard(row); row.update(status='queued', detail='Explicit human resume')
            control = {'commandId': command, 'requestHash': signature, 'operation': params['operation'],
                       'status': 'accepted', 'target': row['target'], 'requestId': row['commandId']}
            from .subscriptions import persist
            persist(self.owner.peer, control, row)
        if row['status'] == 'queued':
            try:
                await self.owner.peer.watch(source['sessionId']); await self.drain(source['sessionId'])
            except BaseException:
                current = self.owner.receipt(row['commandId'])
                if current['status'] == 'queued': current.update(status='held', detail='Creation watch unavailable'); self.save(current)
                raise
        else: await self.owner.peer.release_unused_watch(source['sessionId'])
        return {'receipt': control, 'request': self.owner.receipt(row['commandId']), 'replayed': False}

    async def drain(self, session):
        if session in self.draining or self.owner.intake.fence: return
        self.draining.add(session)
        try:
            ids = self.owner.db.execute("SELECT id FROM commands WHERE json_extract(body,'$.operation')='coordination.create' AND json_extract(body,'$.senderSessionId')=? AND json_extract(body,'$.status')='queued' ORDER BY rowid LIMIT 8", (session,)).fetchall()
            for (identity,) in ids:
                async with self.owner.lock:
                    row = self.owner.receipt(identity)
                    if row['status'] != 'queued': continue
                    try: state, _ = await self.guard(row)
                    except Exception as error:
                        row.update(status='suppressed', detail=str(error)); self.save(row); continue
                    if state.get('status') != 'idle': return
                    row.update(status='creating'); self.save(row)
                try:
                    created = await self.owner.host('createPeerSession', {'commandId': row['creationCommandId'],
                        'idempotencyKey': row['creationCommandId'], 'sourceSession': session,
                        'expectedConfigurationHash': row['configurationHash'], 'title': row['title']})
                    if created.get('creationConfirmed') is not True or not isinstance(created.get('uri'), str):
                        raise ValueError('Root creation was not confirmed')
                    target = await self.owner.grants.identity(created['uri'], {'origin': 'ui'})
                    async with self.owner.lock:
                        row = self.owner.receipt(identity)
                        row.update(createdSessionId=created['uri'], creationReceipt=created)
                        row['relationship'] = {'creatorSessionId': session, 'requestId': identity, 'grantId': row['grantId'],
                            'configurationHash': row['configurationHash'], 'outputNamespace': 'working-files/tasks/' + target['nativeSessionId'],
                            'initialRequestId': row['initialRequestId'], 'references': row['references']}
                        try: _, grant = await self.guard(row)
                        except Exception as error:
                            row.update(status='created_brief_suppressed', detail=str(error)); self.save(row); continue
                        if target['sessionId'] in grant['result']['participants']:
                            raise ValueError('Creation returned an existing grant participant')
                        grant['result']['participants'].append(target['sessionId'])
                        grant['participantBindings'].append({key: target[key] for key in ('sessionId', 'nativeSessionId', 'workspace', 'locationRevision')})
                        grant['scopeDigest'] = digest(grant['result'])
                        row.update(status='created_initial_pending')
                        from .subscriptions import persist
                        persist(self.owner.peer, grant, row)
                    initial = await self.owner.peer.action({**row['principal'], 'operation': 'coordination.send',
                        'commandId': row['initialRequestId'], 'deliveryEnabled': True,
                        'args': {'sessionId': session, 'recipientSessionId': created['uri'], 'grantId': row['grantId'],
                                 'mode': 'queue', 'text': row['text'], 'references': row['references']}})
                    row = self.owner.receipt(identity)
                    row.update(status='created', initialDelivery=initial['receipt']['status']); self.save(row)
                    await self.owner.notify('owner/changed', {'session': session})
                except BaseException:
                    row = self.owner.receipt(identity)
                    if row['status'] in {'creating', 'created_initial_pending'}:
                        row.update(status='unknown', detail='Creation or initial delivery outcome requires inspection; no replay'); self.save(row)
                    raise
        finally:
            self.draining.discard(session)
            await self.owner.peer.release_unused_watch(session)
