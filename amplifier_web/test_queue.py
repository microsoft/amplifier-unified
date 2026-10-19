"""Durable test admission in the host command journal, with exact effect receipts.

The host waits, executes a bounded check and stops its own environment. A model
does not poll peers for a slot. Restart recovery observes admitted operations;
it never replays them. Stopped environments and all evidence remain retained.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
import uuid

from . import task_environments as environments
from . import test_environment_profiles as profiles
from .collaboration import PROTOCOL
from amplifier_operations.coordination import fingerprint

ACTION = 'workspace.tests.submit'
PENDING = {'queued', 'starting', 'checking', 'stopping', 'removing'}
BOUND = ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision')
log = logging.getLogger(__name__)


def normalized_binding(values):
    # Cold metadata spells the same defaults explicitly after a host restart.
    # Normalize old persisted receipts too; real moves and Stop revisions differ.
    workspace, directory, execution, interruption = values
    return [workspace, directory or workspace, 0 if execution is None else execution,
            0 if interruption is None else interruption]


def binding(source):
    return normalized_binding([source.get(key) for key in BOUND])


def definitions(schema, string):
    scope = {'workspaceId': string(100), 'sessionId': string(200)}
    return {
        ACTION: ('Queue one isolated check. The host waits for capacity, snapshots the working folder when admitted, runs the command once, stops its environment, exports source and artifacts and removes the owned environment. Evidence is retained. Continue independent work while waiting. Unknown effects never replay. A guarded completion notification can resume this chat.', schema({
            **scope, 'requestId': {**string(200), 'minLength': 1}, 'profileId': {'enum': [row['id'] for row in profiles.catalog()]},
            'command': {**string(16384), 'minLength': 1},
            'timeoutSeconds': {'type': 'integer', 'minimum': 1, 'maximum': 1800},
            'waitSeconds': {'type': 'integer', 'minimum': 1, 'maximum': 86400},
            'notify': {'type': 'boolean'}}, [*scope, 'requestId', 'profileId', 'command'])),
        'workspace.tests.read': ('Read this chat’s queued checks and retained outcomes. Does not execute or replay a check.', schema({**scope,
            'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}, list(scope))),
        'workspace.tests.cancel': ('Cancel this chat’s waiting check or request prompt termination of its admitted durable guest job. Cancellation is confirmed only after observed termination and owned cleanup. Evidence is retained.', schema({**scope,
            'requestId': string(200)}, [*scope, 'requestId'])),
        'workspace.tests.result': ('Read a bounded page of the exact recorded check output and its retained environment. Never executes or repeats a check.', schema({**scope,
            'requestId': string(200), 'offset': {'type': 'integer', 'minimum': 0}}, [*scope, 'requestId'])),
    }


class TestQueue:
    __test__ = False

    def __init__(self, app):
        self.app = app
        self.tasks = {}
        self.runner = None
        self.closing = False
        self.admission = asyncio.Lock()
        self.backoff = {}
        self.wakeup = asyncio.Event()
        self.notification_cursor = 0
        self.job_wakeups = {}
        self.poll_seconds = 5
        app.db.execute("""CREATE INDEX IF NOT EXISTS workspace_test_queue ON commands(
            json_extract(receipt,'$.job.status'), json_extract(receipt,'$.senderSessionId'))
            WHERE json_extract(receipt,'$.commandAction')='workspace.tests.submit'""")
        app.db.commit()
        app.db.execute("""CREATE INDEX IF NOT EXISTS workspace_test_notifications ON commands(
            json_extract(receipt,'$.subscription.status'))
            WHERE json_extract(receipt,'$.commandAction')='workspace.tests.submit'""")
        app.db.commit()

    def rows(self, *, active=False, sid=None, offset=0, limit=64):
        terms = ["json_extract(receipt,'$.commandAction')=?"]
        params = [ACTION]
        if active:
            terms.append("json_extract(receipt,'$.job.status') IN ('queued','starting','checking','stopping','removing')")
        if sid:
            terms.append("json_extract(receipt,'$.senderSessionId')=?")
            params.append(sid)
        return [json.loads(row[0]) for row in self.app.db.execute(
            'SELECT receipt FROM commands WHERE ' + ' AND '.join(terms) + ' ORDER BY rowid ' + ('ASC' if active else 'DESC') + ' LIMIT ? OFFSET ?',
            (*params, limit, offset))]

    def identity(self, sid, request):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'workspace-test:' + sid + ':' + request))

    def get(self, identity):
        row = self.app.collaboration.receipt(identity)
        if row.get('commandAction') != ACTION:
            self.app.collaboration.error('Queued test not found.', 404)
        return row

    def save(self, receipt):
        receipt['job']['updatedAt'] = time.time()
        self.app.collaboration.save(receipt['requestId'], receipt)
        self.app.db.commit()

    def public(self, receipt):
        job = receipt['job']
        value = {key: copy.deepcopy(job[key]) for key in ('id', 'status', 'createdAt', 'updatedAt', 'expiresAt',
            'resourceId', 'capacity', 'detail', 'cancelRequested', 'source', 'timeoutSeconds', 'profileId', 'guestJob') if key in job}
        # Full output lives in the existing exact operation receipt. Queue lists
        # cannot multiply a 32 KiB command result by the page size.
        for phase in ('launch', 'check', 'cleanup', 'removal'):
            if phase in job:
                result = job[phase]
                value[phase] = {key: result[key] for key in ('requestId', 'state', 'message') if key in result}
        value['notification'] = receipt['subscription']['status']
        return value

    def summary(self, request):
        return {key: request[key] for key in ('requestId', 'state', 'message') if key in request}

    def guard(self, receipt):
        job = receipt['job']
        if job.get('cancelRequested'):
            raise ValueError('The queued check was cancelled; no new check will start.')
        if job['status'] == 'queued' and job['expiresAt'] <= time.time():
            raise ValueError('The saved check expired while waiting; no environment was started.')
        source = self.app._session(receipt['senderSessionId'], hydrate=False)
        if normalized_binding(job['binding']) != binding(source):
            raise ValueError('The conversation stopped or moved; its queued check will not start.')
        if self.app.assignments.blocked(source, receipt.get('assignmentId')):
            raise ValueError('The assignment stopped; its queued check will not start.')
        self.app.configuration_authority.check(job.get('authority'))
        config, _, _ = environments.configuration(self.app)
        if not config or environments.config_key(config) != job['backendKey']:
            raise ValueError('The approved test backend changed; this request will not start on another backend.')
        return config

    def continuation_guard(self, receipt):
        if receipt['job'].get('cancelRequested'):
            return 'The queued check was cancelled; its result remains available.'
        source = self.app._session(receipt['senderSessionId'], hydrate=False)
        if normalized_binding(receipt['job']['binding']) != binding(source):
            return 'The conversation stopped or moved after this check was requested.'
        return None

    def finish(self, receipt, status, detail):
        receipt['job'].update(status=status, detail=detail)
        receipt['terminalOutcome'] = {'status': 'observed', 'kind': 'test', 'outcome': status,
            'qualified': False, 'text': detail + ' Inspect workspace.tests.read and the retained environment result. This is check evidence, not proof of product acceptance.',
            'outcomeRevision': 1}
        self.save(receipt)
        self.backoff.clear()
        self.wakeup.set()
        if receipt['subscription']['status'] == 'waiting':
            try:
                self.app.collaboration.enqueue_continuation(receipt)
            except Exception:
                # Retained results and notifications have separate commit
                # boundaries. tick retries delivery without replaying the test.
                pass

    async def dispatch(self, action, args, origin, caller, authority):
        owner = self.app.collaboration
        source = owner.source(args, origin, caller, hydrate=False)
        if source['id'] != args['sessionId']:
            owner.error('A queued check belongs to its calling root conversation.')
        workspace = next((w for w in self.app.state['workspaces'] if w['id'] == args['workspaceId']), None)
        if not workspace or workspace['path'] != source['workspace']:
            owner.error('Choose this conversation’s current workspace.')
        if action.endswith('.read'):
            size, offset = args.get('limit', 10), args.get('offset', 0)
            rows = self.rows(sid=source['id'], offset=offset, limit=size+1)
            return {'accepted': True, 'result': {'items': [self.public(r) for r in rows[:size]],
                'nextOffset': offset+size if len(rows)>size else None}}
        identity = self.identity(source['id'], args['requestId'])
        if action.endswith('.result'):
            receipt = self.get(identity)
            try:
                request = self.app.operations.requests.read(source['id'], identity+':execute')
            except ValueError:
                request = {'state': 'not-started'}
            output = request.get('result', {}).get('result', {})
            content = output.get('stdout', '') + ('\n'+output['stderr'] if output.get('stderr') else '')
            offset = args.get('offset', 0)
            row = environments.get(self.app.data_dir, source['id'], receipt['job']['resourceId']) if receipt['job'].get('resourceId') else None
            # Budget the encoded text, including JSON escaping and surrogate
            # pairs, leaving room for the operation and app_control envelope.
            page = content[offset:offset+6000]
            low, high = 0, len(page)
            while low < high:
                middle = (low+high+1)//2
                if len(json.dumps(page[:middle]).encode('utf-8')) <= 12000:
                    low = middle
                else:
                    high = middle-1
            page = page[:low]
            return {'accepted': True, 'result': {'check': self.public(receipt), 'operation': self.summary(request),
                'exitCode': output.get('exit_code'), 'text': page, 'offset': offset,
                'nextOffset': offset+len(page) if offset+len(page)<len(content) else None,
                'truncatedAtSource': bool(output.get('truncated') or output.get('stdout_truncated') or output.get('stderr_truncated')),
                'retention': environments.public(self.app.data_dir, row)['retention'] if row else None}}
        async with self.app.lock:
            if action.endswith('.cancel'):
                if origin not in {'ui', 'user'}:
                    owner.active_binding(owner.source(args, origin, caller, hydrate=False))
                receipt = self.get(identity)
                if receipt['job']['status'] not in PENDING:
                    return {'accepted': True, 'result': self.public(receipt)}
                receipt['job']['cancelRequested'] = True
                receipt['subscription'].update(status='suppressed', open=False)
                if receipt['job']['status'] == 'queued':
                    self.finish(receipt, 'cancelled', 'Cancelled before environment admission. No check was started.')
                else:
                    self.save(receipt)
                    if receipt['job']['status'] == 'starting' and receipt['job'].get('resourceId'):
                        row = environments.get(self.app.data_dir, source['id'], receipt['job']['resourceId'])
                        environments.cancel_preparation(self.app, row)
                self.wakeup.set()
                self.wake_job(identity)
                return {'accepted': True, 'result': self.public(receipt)}
            digest = fingerprint([ACTION, args, source['id'], origin])
            previous = self.app.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (identity,)).fetchone()
            if previous:
                if previous[0] != digest:
                    owner.error('This test request ID already has different arguments.', 409)
                return {'accepted': True, 'duplicate': True, 'result': self.public(json.loads(previous[1]))}
            if origin not in {'ui', 'user'}:
                owner.active_binding(source)
            if self.app.assignments.blocked(source):
                owner.error('This assignment is stopped.', 409)
            self.app.configuration_authority.check(authority)
            seconds = profiles.validate_timeout(args['profileId'], args.get('timeoutSeconds'))
            config, _, reason = environments.configuration(self.app)
            if not config:
                owner.error(reason, 409)
            if self.rows(active=True, sid=source['id']):
                owner.error('This chat already has a pending check. Inspect or cancel that request.', 409)
            if len(self.rows(active=True)) >= 64:
                owner.error('The host has 64 pending checks. Inspect or cancel existing requests.', 429)
            notify = args.get('notify', True)
            if notify:
                owner.require_continuation()
                owner.require_inbox_slot(source['id'])
            lineage = owner.lineage(source, origin)
            now = time.time()
            receipt = {'accepted': True, 'commandAction': ACTION, 'requestId': identity,
                'senderSessionId': source['id'], 'target': {'sessionId': source['id']},
                'workspace': source['workspace'], 'protocol': PROTOCOL, 'origin': origin, **lineage,
                'causalDepth': owner.causal_depth(source, lineage, origin),
                'assignmentId': source.get('assignmentId'),
                'subscription': owner.saved_wait(source, identity, status='waiting' if notify else 'disabled'),
                'job': {'id': args['requestId'], 'status': 'queued', 'createdAt': now, 'updatedAt': now,
                    'expiresAt': now + args.get('waitSeconds', 3600),
                    'source': source.get('workingDirectory') or source['workspace'],
                    'binding': binding(source), 'authority': authority,
                    'backendKey': environments.config_key(config), 'command': args['command'],
                    'timeoutSeconds': seconds, 'profileId': args['profileId'], 'durableVersion': 1,
                    'actor': {'sessionId': source['id'],
                        'principal': source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']}}}
            owner.require_causal_depth(receipt['causalDepth'])
            owner.insert(identity, digest, receipt)
            self.app.db.commit()
        self.start()
        return {'accepted': True, 'result': self.public(receipt)}

    def start(self):
        if (self.runner is None or self.runner.done()) and not self.closing:
            self.runner = asyncio.create_task(self.loop())

    async def close(self):
        self.closing = True
        if self.runner:
            self.runner.cancel()
            await asyncio.gather(self.runner, return_exceptions=True)
        # An admitted thread retains its own operation receipt; cancellation of
        # the host coroutine must not invent confirmation or restart the effect.
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def loop(self):
        try:
            while not self.closing:
                try:
                    await self.tick()
                except Exception:
                    # A temporary journal/backend failure cannot silently kill
                    # durable waiting work. Exact operation receipts protect
                    # recovery from repeating any admitted effect.
                    log.exception('Queued test scheduler could not reconcile this pass')
                try:
                    await asyncio.wait_for(self.wakeup.wait(), timeout=5)
                    self.wakeup.clear()
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def tick(self):
        for identity, task in list(self.tasks.items()):
            if task.done():
                # Consume exceptions and release completed job working sets.
                if not task.cancelled():
                    task.exception()
                self.tasks.pop(identity, None)
        notifications = self.app.db.execute("""SELECT rowid,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='workspace.tests.submit'
                AND json_extract(receipt,'$.terminalOutcome.status')='observed'
                AND json_extract(receipt,'$.subscription.status') IN ('waiting','backpressured')
                AND rowid>? ORDER BY rowid LIMIT 64""", (self.notification_cursor,)).fetchall()
        self.notification_cursor = notifications[-1][0] if notifications else 0
        for _, encoded in notifications:
            try:
                self.app.collaboration.enqueue_continuation(json.loads(encoded))
            except Exception:
                # The saved result survives a temporarily unavailable inbox.
                pass
        capacities = {}
        for receipt in self.rows(active=True):
            identity = receipt['requestId']
            task = self.tasks.get(identity)
            if task and not task.done():
                continue
            if task:
                self.tasks.pop(identity)
            if receipt['job']['status'] != 'queued':
                self.tasks[identity] = asyncio.create_task(self.recover(identity))
                continue
            try:
                if receipt['job']['expiresAt'] <= time.time():
                    self.finish(receipt, 'expired', 'The saved check expired while waiting for capacity. No environment was started.')
                    continue
                config = self.guard(receipt)
            except Exception as exc:
                self.finish(receipt, 'cancelled', str(exc))
                continue
            if self.admission.locked():
                continue
            key = receipt['job']['backendKey']
            if key not in capacities:
                retry, delay = self.backoff.get(key, (0, 5))
                if retry > time.time():
                    continue
                capacity = await asyncio.to_thread(environments.capacity, self.app, config)
                limits, counts = capacity.get('limits', {}), capacity.get('counts', {})
                maximum = limits.get('max_running', 1)
                preparing = sum(item['job']['status'] == 'starting' and item['job']['backendKey'] == key
                                for item in self.rows(active=True))
                free = max(0, maximum-counts.get('active', 0)-preparing) if maximum else max(0, 64-preparing)
                capacities[key] = [capacity, free if capacity.get('available') else 0]
                if not capacity.get('available'):
                    self.backoff[key] = (time.time()+delay, min(60, delay*2))
                else:
                    self.backoff.pop(key, None)
            capacity, free = capacities[key]
            receipt = self.get(identity)
            receipt['job']['capacity'] = capacity
            self.save(receipt)
            if free <= 0 or receipt['job']['status'] != 'queued':
                continue
            capacities[key][1] -= 1
            self.tasks[identity] = asyncio.create_task(self.run(identity, config))

    async def effect(self, receipt, config, action, *, cleanup=False, suffix=None, previous_request=None):
        job, app = receipt['job'], self.app
        row = environments.get(app.data_dir, receipt['senderSessionId'], job['resourceId'])
        if cleanup:
            active, _, _ = environments.configuration(app)
            config = environments.resolve_configuration(app, row, active)
        request_id = receipt['requestId'] + ':' + (suffix or action)
        if action == 'start':
            request_id += ':'+str(job.get('attempt', 0))
        try:
            return app.operations.requests.read(receipt['senderSessionId'], request_id)
        except ValueError:
            pass
        args = {'requestId': request_id, 'expectedRevision': row['revision'],
            'command': job['command'], 'timeoutSeconds': job['timeoutSeconds']}
        if previous_request is not None:
            args['previousRequestId'] = previous_request
        if action == 'execute' and job.get('durableVersion') == 1:
            args['durableJob'] = True
        request, fresh = app.operations.requests.begin(receipt['senderSessionId'],
            'workspace.environments.'+action, args, job['actor'])
        if not fresh:
            return request
        loop = asyncio.get_running_loop()
        async def checked():
            async with app.lock:
                # Cleanup is part of the original admitted check, even when its
                # waiting authority is revoked. It only stops that exact owner;
                # it never starts work, changes settings or removes evidence.
                if not cleanup:
                    self.guard(self.get(receipt['requestId']))
                else:
                    active, _, _ = environments.configuration(app)
                    current = environments.get(app.data_dir, receipt['senderSessionId'], row['id'])
                    if environments.resolve_configuration(app, current, active) != config:
                        raise ValueError('The retained cleanup backend changed.')
        def admit():
            asyncio.run_coroutine_threadsafe(checked(), loop).result(timeout=10)
        result = await app.worktrees.finish_thread(environments.effect, app, row, action, args, config, request, admit)
        return result['request']

    async def run(self, identity, config):
        try:
            async with self.admission:
                receipt = self.get(identity)
                config = self.guard(receipt)
                # tick shares one physical observation across this FIFO batch.
                # The backend rechecks atomically immediately before launch.
                job = receipt['job']
                row = await asyncio.to_thread(environments.prepare, self.app.data_dir,
                    receipt['senderSessionId'], next(w['id'] for w in self.app.state['workspaces'] if w['path']==receipt['workspace']),
                    job['actor'], job['source'], job['backendKey'], identity+':start:'+str(job.get('attempt', 0)), config.get('installationId'), config, job.get('profileId', environments.PROFILE))
                # Cancellation may arrive while local allocation is persisted.
                # Never overwrite the newer cancellation/subscription record.
                receipt = self.get(identity)
                job = receipt['job']
                try:
                    self.guard(receipt)
                except Exception:
                    environments.save(self.app.data_dir, row, status='retired', environmentState='not-started')
                    job['resourceId'] = row['id']
                    self.finish(receipt, 'cancelled', 'Cancelled before launch. The unused allocation was retired and local evidence retained.')
                    return
                job.update(resourceId=row['id'], status='starting')
                self.save(receipt)
            launch = await self.effect(receipt, config, 'start')
            receipt = self.get(identity)
            receipt['job']['launch'] = self.summary(launch)
            if launch['state'] != 'completed':
                if (launch.get('result') or {}).get('admissionDenied'):
                    # Proven refusal before launch is retryable. Retire only
                    # the unused logical allocation; its source stays on disk.
                    row = environments.get(self.app.data_dir, receipt['senderSessionId'], job['resourceId'])
                    environments.save(self.app.data_dir, row, status='retired')
                    receipt['job'].pop('resourceId', None)
                    receipt['job']['attempt'] = receipt['job'].get('attempt', 0)+1
                    receipt['job']['status'] = 'queued'
                    self.save(receipt)
                    return
                if launch['state'] == 'failed':
                    row = environments.get(self.app.data_dir, receipt['senderSessionId'], job['resourceId'])
                    if not row.get('backendAdmitted') and not row.get('preparationPending'):
                        environments.save(self.app.data_dir, row, status='retired')
                elif launch['state'] == 'outcome_unknown':
                    await self.recover(identity)
                    return
                self.finish(receipt, 'unknown' if launch['state']=='outcome_unknown' else 'cancelled' if receipt['job'].get('cancelRequested') else 'failed',
                    'Environment admission did not complete. Inspect its exact saved receipt; it was not retried.')
                return
            try:
                self.guard(receipt)
                receipt['job']['status'] = 'checking'
                self.save(receipt)
                check = await self.effect(receipt, config, 'execute')
            except Exception as exc:
                check = {'state': 'cancelled', 'message': str(exc)}
            row = environments.get(self.app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
            if receipt['job'].get('durableVersion') == 1 and row.get('durableJob'):
                return await self.monitor(identity, config, check)
            receipt = self.get(identity)
            receipt['job'].update(check=self.summary(check), status='stopping')
            self.save(receipt)
            await self.cleanup(receipt, config)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception('Queued check lifecycle interrupted; reconciling its recorded effects')
            receipt = self.get(identity)
            if receipt['job'].get('resourceId'):
                # A failure between saving an effect and saving the queue's
                # next phase must still reconcile the owned environment.
                await self.recover(identity)
                return
            status = 'failed' if receipt['job']['status'] == 'queued' else 'unknown'
            self.finish(receipt, status, 'The test lifecycle did not complete: '+str(exc)[:500]+'. No uncertain effect will be replayed.')

    async def cleanup(self, receipt, config, *, recovery=False):
        identity = receipt['requestId']
        row = environments.get(self.app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
        if not row.get('backendAdmitted') or row.get('guestRemoved') or row.get('environmentState') == 'stopped':
            cleanup = {'state': 'completed', 'message': 'Observed no running owned environment.'}
        else:
            cleanup = await self.effect(receipt, config, 'stop', cleanup=True,
                suffix='recovery-stop' if recovery else None)
        receipt = self.get(identity)
        receipt['job']['cleanup'] = self.summary(cleanup)
        if cleanup['state'] != 'completed':
            self.finish(receipt, 'unknown', 'Check finished, but stopping its environment was not confirmed. Evidence and ownership are retained; inspect before further cleanup.')
        else:
            receipt['job']['status'] = 'removing'
            self.save(receipt)
            removal = await self.effect(receipt, config, 'remove', cleanup=True)
            receipt = self.get(identity)
            if (removal['state'] == 'failed' and removal.get('result', {}).get('effectAdmitted') is False
                    and removal['result'].get('phase') in {'preserving', 'verifying'}
                    and removal['result'].get('code') in {'preservation_failed', 'archive_verification_failed'}):
                # A definitive pre-teardown failure permits the separately
                # recorded preservation path, never replay of an unknown effect.
                receipt['job']['previousRemoval'] = self.summary(removal)
                self.save(receipt)
                removal = await self.effect(receipt, config, 'finishCleanup', cleanup=True,
                    previous_request=removal['requestId'])
                receipt = self.get(identity)
            receipt['job']['removal'] = self.summary(removal)
            if removal['state'] != 'completed':
                self.finish(receipt, 'unknown', 'The environment is stopped, but evidence export or removal was not confirmed. Its retained files and exact ownership remain available.')
                return
            check = receipt['job'].get('check', {'state': 'outcome_unknown'})
            status = ('cancelled' if receipt['job'].get('cancelRequested') else
                      {'completed': 'passed', 'failed': 'failed', 'cancelled': 'cancelled'}.get(check['state'], 'unknown'))
            self.finish(receipt, status, 'The check outcome is '+status+'. Source, artifacts and operation receipts were retained before the owned environment was removed.')

    def wake_job(self, identity):
        event = self.job_wakeups.get(identity)
        if event is not None:
            event.set()

    async def monitor(self, identity, config, check=None):
        """Reattach observation after restart; only guest admission runs commands."""
        event = self.job_wakeups[identity] = asyncio.Event()
        try:
            return await self._monitor(identity, config, check, event)
        finally:
            if self.job_wakeups.get(identity) is event:
                self.job_wakeups.pop(identity, None)

    async def _monitor(self, identity, config, check, event):
        errors = 0
        while True:
            receipt = self.get(identity)
            row = environments.get(self.app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
            observed = check is None or receipt['job'].get('cancelRequested')
            if observed:
                try:
                    check = await asyncio.to_thread(environments.observe_job, self.app, row, config,
                        cancel=bool(receipt['job'].get('cancelRequested')))
                    errors = 0
                except Exception as exc:
                    errors += 1
                    if errors >= 3:
                        self.finish(self.get(identity), 'unknown', 'The durable guest could not be observed: '+str(exc)[:300]+'. Its environment and evidence are preserved; no command or cleanup was replayed.')
                        return
                    await asyncio.sleep(1)
                    continue
            guest = check.get('result', {}).get('job', {})
            if observed and not guest:
                self.finish(self.get(identity), 'unknown', 'The backend returned no durable guest receipt. The environment is preserved; no command or cleanup was repeated.')
                return
            receipt = self.get(identity)
            summary = self.summary(check)
            changed = receipt['job'].get('check') != summary
            receipt['job']['check'] = summary
            if guest:
                compact = {key: guest[key] for key in ('id', 'state', 'terminal', 'startedAt', 'finishedAt', 'cancelRequested', 'reason') if key in guest}
                changed |= receipt['job'].get('guestJob') != compact
                receipt['job']['guestJob'] = compact
            if changed:
                self.save(receipt)
            if guest.get('terminal') is True:
                receipt['job']['status'] = 'stopping'
                self.save(receipt)
                await self.cleanup(receipt, config)
                return
            if guest.get('state') == 'unknown':
                self.finish(receipt, 'unknown', guest.get('reason', 'The exact guest job is unverified.')+' Its environment is preserved; the command and cleanup were not repeated.')
                return
            # The start acknowledgement may have been lost after guest commit.
            # Observe the exact reserved identity, never call execute again.
            check = None
            try:
                await asyncio.wait_for(event.wait(), timeout=self.poll_seconds)
                event.clear()
            except TimeoutError:
                pass

    async def recover(self, identity):
        """Reconcile the exact owner and complete cleanup; never rerun the check."""
        receipt = self.get(identity)
        try:
            active, _, _ = environments.configuration(self.app)
            row = environments.get(self.app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
            if row.get('guestRemoved') and row.get('preparationPending'):
                retained = environments.resolve_configuration(self.app, row, active)
                row = await asyncio.to_thread(environments.resume_preparation_removal, self.app, row, retained)
            if row['status'] == 'retired':
                self.restore_operations(receipt)
                check = receipt['job'].get('check', {})
                removal = receipt['job'].get('removal', {})
                status = {'completed': 'passed', 'failed': 'failed'}.get(check.get('state'), 'unknown') if removal.get('state') == 'completed' else 'unknown'
                self.finish(receipt, 'cancelled' if receipt['job'].get('cancelRequested') else status,
                    'Recovered the recorded check and cleanup results. The owned environment is removed; no command was replayed.')
                return
            config = environments.resolve_configuration(self.app, row, active)
            if (receipt['job'].get('durableVersion') == 1 and row.get('durableJob')
                    and receipt['job']['status'] not in {'stopping', 'removing'}):
                await self.monitor(identity, config)
                return
            # read reconciles any prior abandoned pendingRequest and physical
            # state. It never starts an environment or installs source again.
            await asyncio.to_thread(environments.read, self.app, receipt['senderSessionId'], active)
            receipt = self.get(identity)
            self.restore_operations(receipt)
            row = environments.get(self.app.data_dir, receipt['senderSessionId'], row['id'])
            if row.get('environmentState') not in {'running', 'stopped', 'unknown', 'not-started'}:
                raise ValueError('The exact environment has not reached a cleanup boundary.')
            receipt['job'].setdefault('check', {'state': 'outcome_unknown',
                'message': 'The interrupted lifecycle has no recorded check result; no check command was replayed.'})
            self.save(receipt)
            await self.cleanup(receipt, config, recovery=True)
        except Exception as exc:
            self.finish(self.get(identity), 'unknown', 'Recovery could not confirm cleanup: '+str(exc)[:500]+'. All evidence and ownership remain retained; no command was replayed.')

    def restore_operations(self, receipt):
        for phase, operation in (('check', 'execute'), ('cleanup', 'stop'), ('removal', 'remove'), ('removal', 'finishCleanup')):
            try:
                receipt['job'][phase] = self.summary(self.app.operations.requests.read(receipt['senderSessionId'], receipt['requestId']+':'+operation))
            except ValueError:
                pass
