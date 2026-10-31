"""One-shot root lifecycle observations retained in the command owner."""
from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid

from amplifier_operations.coordination import fingerprint


def definitions(schema, string):
    owner = {'sessionId': string(200)}
    return {
        'coordination.watch': ('Save one status-change or idle-boundary observation. No target work is started. Expiry notifies the requester through its guarded inbox; a watch never satisfies a dependency.', schema({
            **owner, 'targetSessionId': string(200), 'condition': {'enum': ['status_changed', 'idle']},
            'expiresInSeconds': {'type': 'integer', 'minimum': 1, 'maximum': 86400}},
            ['sessionId', 'targetSessionId', 'condition', 'expiresInSeconds'])),
        'coordination.watch.read': ('Inspect one retained watch without starting or replaying work.', schema({**owner, 'watchId': string(200)}, ['sessionId', 'watchId'])),
        'coordination.watch.cancel': ('Cancel a pending watch or its unsubmitted notification. Already admitted work is not interrupted or undone.', schema({**owner, 'watchId': string(200)}, ['sessionId', 'watchId'])),
    }


class Watches:
    def __init__(self, collaboration):
        self.owner = collaboration
        self.service = collaboration.service
        self.handles = {}
        self.service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_watches ON commands(
            json_extract(receipt,'$.target.sessionId'), json_extract(receipt,'$.watch.status'))
            WHERE json_extract(receipt,'$.commandAction')='coordination.watch'""")

    def close(self):
        for handle in self.handles.values():
            handle.cancel()
        self.handles.clear()

    def schedule(self, receipt):
        identity = receipt['requestId']
        if identity in self.handles or receipt['watch']['status'] != 'waiting':
            return
        def wake():
            self.handles.pop(identity, None)
            if not self.service.closed:
                self.service._task(self.expire(identity))
        self.handles[identity] = asyncio.get_running_loop().call_later(
            max(0, receipt['watch']['expiresAt']-time.time()), wake)

    async def expire(self, identity):
        async with self.service.lock:
            receipt = self.owner.receipt(identity)
            if receipt['watch']['status'] == 'waiting' and receipt['watch']['expiresAt'] <= time.time():
                self.finish(receipt, 'expired', 'The one-shot observation expired without observing its requested boundary.')

    def finish(self, receipt, status, text, *, observed_at=None):
        observed_at = time.time() if observed_at is None else observed_at
        receipt['watch'].update(status=status, observedAt=observed_at)
        receipt['terminalOutcome'] = {'status': 'observed', 'kind': 'observation', 'outcome': status,
            'qualified': False, 'requestId': receipt['requestId'], 'text': text,
            'observedAt': observed_at, 'outcomeRevision': 1}
        receipt['outcomeRevision'] = 1
        self.owner.save(receipt['requestId'], receipt)
        self.owner.enqueue_continuation(receipt)
        self.owner.publish_receipt(receipt)
        handle = self.handles.pop(receipt['requestId'], None)
        if handle:
            handle.cancel()

    def high_water(self):
        return self.service.db.execute('SELECT coalesce(max(rowid), 0) FROM commands').fetchone()[0]

    def rows(self, where, parameters, high_water):
        """Bound payload residency while considering the entire captured set."""
        after = 0
        while True:
            rows = self.service.db.execute(f"""SELECT rowid, receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.watch'
                AND {where} AND rowid>? AND rowid<=? ORDER BY rowid LIMIT 64""",
                (*parameters, after, high_water)).fetchall()
            if not rows:
                return
            after = rows[-1][0]
            for _, encoded in rows:
                yield json.loads(encoded)

    def observe(self, session, kind, payload):
        changed = set()
        if kind != 'runtime.status' or payload.get('activityOnly') or 'status' not in payload:
            return changed
        if payload.get('rootSessionId', session['id']) != session['id']:
            return changed
        status, workspace, observed_at = session.get('status'), session['workspace'], time.time()
        high_water = self.high_water()
        for receipt in self.rows("""json_extract(receipt,'$.target.sessionId')=?
                AND json_extract(receipt,'$.watch.status')='waiting'""", (session['id'],), high_water):
            watch = receipt['watch']
            if watch['expiresAt'] <= observed_at:
                self.finish(receipt, 'expired', 'The observation expired before this lifecycle event.', observed_at=observed_at)
            elif workspace != receipt['workspace']:
                self.finish(receipt, 'cancelled', 'The observed conversation moved outside the saved workspace.', observed_at=observed_at)
            elif ((watch['condition'] == 'status_changed' and status != watch['baselineStatus'])
                  or watch['condition'] == 'idle' and status == 'idle' and watch['baselineStatus'] != 'idle'):
                watch['observedStatus'] = status
                self.finish(receipt, 'changed' if watch['condition'] == 'status_changed' else 'idle',
                    f"The observed conversation reached status {status}. This is lifecycle evidence, not proof of result correctness or completed external effects.",
                    observed_at=observed_at)
            else:
                continue
            changed.add(receipt['senderSessionId'])
        return changed

    def start(self):
        high_water = self.high_water()
        for receipt in self.rows("json_extract(receipt,'$.watch.status')='waiting'", (), high_water):
            self.schedule(receipt)
        for receipt in self.rows("""json_extract(receipt,'$.terminalOutcome.status')='observed'
                AND json_extract(receipt,'$.subscription.status') IN ('waiting','backpressured')""", (), high_water):
            self.owner.enqueue_continuation(receipt)

    async def dispatch(self, action, args, origin, command_id, caller):
        source = self.owner.source(args, origin, caller)
        if args['sessionId'] != source['id']:
            self.owner.error('A watch belongs to its calling conversation.')
        if action == 'coordination.watch.read':
            receipt = self.owner.receipt(args['watchId'])
            self.authorize(receipt, source)
            return {'accepted': True, 'result': receipt}
        identity = command_id or str(uuid.uuid4())
        digest = fingerprint([action, args, origin, source['id']])
        async with self.service.lock:
            duplicate = self.owner.duplicate(identity, digest)
            if duplicate:
                return duplicate
            self.service.db.commit()
            try:
                lineage = self.owner.lineage(source, origin)
                if action == 'coordination.watch':
                    reason = self.service.assignments.blocked(source)
                    if reason:
                        self.owner.error(reason, 409)
                if action == 'coordination.watch.cancel':
                    receipt = self.owner.receipt(args['watchId'])
                    self.authorize(receipt, source)
                    old = receipt['watch']['status']
                    receipt['watch'].update(status='cancelled', cancelledAt=time.time())
                    receipt['subscription'].update(status='suppressed', open=False,
                        detail='The watcher cancelled this observation. Already admitted effects are not undone.')
                    self.owner.save(args['watchId'], receipt)
                    handle = self.handles.pop(args['watchId'], None)
                    if handle:
                        handle.cancel()
                    for (encoded,) in self.service.db.execute("""SELECT receipt FROM commands
                        WHERE json_extract(receipt,'$.dependencyRequestId')=?
                        AND json_extract(receipt,'$.delivery')='queued'""", (args['watchId'],)).fetchall():
                        pending = json.loads(encoded)
                        pending.update(delivery='suppressed', detail='The one-shot watch was cancelled before admission.')
                        self.owner.save(pending['requestId'], pending)
                    result = {'watchId': args['watchId'], 'status': 'cancelled', 'previousStatus': old,
                              'interruptsAdmittedWork': False}
                    answer = {'accepted': True, 'commandAction': action, 'result': result}
                else:
                    self.owner.require_continuation()
                    depth = self.owner.causal_depth(source, lineage, origin)
                    target = self.owner.authorize(source, {'sessionId': args['targetSessionId']}, hydrate=False)
                    if target['id'] == source['id']:
                        self.owner.error('Observe another conversation; self-watches cannot start continuation loops.', 409)
                    self.owner.require_inbox_slot(source['id'])
                    from .test_execution_settings import concurrency
                    limit = concurrency(self.service)['maxPendingWatchesPerTarget']
                    if limit:
                        count = self.service.db.execute("""SELECT count(*) FROM commands
                            WHERE json_extract(receipt,'$.commandAction')='coordination.watch'
                            AND json_extract(receipt,'$.target.sessionId')=?
                            AND json_extract(receipt,'$.watch.status')='waiting'""", (target['id'],)).fetchone()[0]
                        if count >= limit:
                            self.owner.error(f'This target reached its configured limit of {limit} pending observations. Wait or cancel one before adding another.', 429)
                    from .collaboration import PROTOCOL
                    answer = {'accepted': True, 'commandAction': action, 'requestId': identity,
                        'senderSessionId': source['id'], 'target': {'sessionId': target['id']},
                        'workspace': source['workspace'], 'protocol': PROTOCOL, 'causalDepth': depth,
                        'origin': origin, **lineage,
                        'subscription': self.owner.saved_wait(source, identity),
                        'watch': {'status': 'waiting', 'condition': args['condition'],
                                  'baselineStatus': target.get('status'),
                                  'expiresAt': time.time()+args['expiresInSeconds']}}
                    assignment = source.get('assignmentId') or (source.get('collaboration') or {}).get('assignmentId')
                    if assignment:
                        answer['assignmentId'] = assignment
                self.owner.insert(identity, digest, answer)
                self.owner.publish_receipt(answer)
                if action == 'coordination.watch':
                    # An explicitly requested current idle boundary can settle now;
                    # this never starts or loads the observed conversation.
                    if args['condition'] == 'idle' and target.get('status') == 'idle':
                        self.finish(answer, 'idle', 'The observed conversation is idle. This does not prove external effects or all background work have finished.')
                    else:
                        self.schedule(answer)
                return copy.deepcopy(answer)
            except BaseException:
                if not self.owner.committed(identity):
                    self.service.db.rollback()
                    self.owner.refresh_reference({'senderSessionId': source['id']})
                    if action == 'coordination.watch.cancel':
                        self.schedule(self.owner.receipt(args['watchId']))
                elif action == 'coordination.watch':
                    self.schedule(self.owner.receipt(identity))
                raise

    def authorize(self, receipt, source):
        if (receipt.get('commandAction') != 'coordination.watch'
                or receipt.get('senderSessionId') != source['id']
                or receipt.get('workspace') != source['workspace']):
            self.owner.error('This observation belongs to a different caller or workspace.')
