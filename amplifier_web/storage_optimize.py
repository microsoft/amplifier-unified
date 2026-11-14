"""Explicit, resumable saved-storage maintenance; format owners perform effects.

This is not an automatic retention policy. The operation freezes scope and UTC
cutoffs, pages its file ledger on disk and never replays work after a restart.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import uuid

from .errors import AppError

PREFIX = 'maintenance.optimize.'
DEFAULT_POLICY = {'revision': 0, 'requestKeepHours': None, 'responseKeepHours': None}
POLICY_KEY = 'saved-storage-optimize-policy'
ACTIVE = {'previewing', 'applying', 'cancelling'}


def definitions():
    from .action_schema import schema, string
    revision = {'type': 'integer', 'minimum': 0}
    hours = {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 2**53 - 1}
    operation = {'operationId': string(200), 'expectedRevision': revision}
    return {
        PREFIX + 'policy.read': ('Read manual saved-storage choices without scanning or changing automatic schedules.', schema({}, [])),
        PREFIX + 'policy.save': ('Save explicit manual Optimize expiry choices, not automatic cleanup. Null keeps that diagnostic direction.', schema({
            'expectedRevision': revision, 'requestKeepHours': hours, 'responseKeepHours': hours},
            ['expectedRevision', 'requestKeepHours', 'responseKeepHours'])),
        PREFIX + 'preview': ('Inventory saved session storage using format owners. Freeze scope and UTC expiry cutoffs; do not convert or delete.', schema({
            'scope': {'enum': ['current', 'workspace', 'all']}, 'sessionId': string(200),
            'extractImages': {'type': 'boolean'}, 'policyRevision': revision},
            ['scope', 'extractImages', 'policyRevision'])),
        PREFIX + 'read': ('Read the latest or exact operation and one file page, or reconcile an exact commandId. Unknown never means no effect.', schema({
            'operationId': string(200), 'commandId': string(200),
            'after': {'type': 'integer', 'minimum': 0},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, [])),
        PREFIX + 'apply': ('Apply the exact reviewed preview under current authority. Explicit confirmExpiry is required for selected raw diagnostic expiry.', schema({
            **operation, 'confirmExpiry': {'type': 'boolean'}}, list(operation) + ['confirmExpiry'])),
        PREFIX + 'cancel': ('Request a stop at the next safe file checkpoint. Completed changes and recovery copies remain.', schema(operation, list(operation))),
        PREFIX + 'resume': ('Reconcile an interrupted operation through the original format owners, then continue unfinished files. Never automatically replays unknown replacements.', schema(operation, list(operation))),
    }


def cutoff(now, hours):
    if hours is None:
        return None
    if type(hours) is not int or not 1 <= hours <= 2**53 - 1:
        raise AppError('Choose a positive whole number of hours, or keep this detail.', 400)
    # A large valid age means no representable historical timestamp is older.
    try:
        value = now - timedelta(hours=hours)
    except OverflowError:
        value = datetime.min.replace(tzinfo=timezone.utc)
    return value.isoformat().replace('+00:00', 'Z')


class SavedStorageOptimize:
    def __init__(self, app, *, adapter=None):
        self.app = app
        self.journal = app.operations.journal
        self.requests = app.operations.requests
        self.tasks = {}
        self.adapter = adapter
        with self.journal.lock, self.journal.db:
            self.journal.db.executescript("""
                CREATE TABLE IF NOT EXISTS storage_optimize_operations(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, value TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS storage_optimize_owner
                    ON storage_optimize_operations(owner);
                CREATE TABLE IF NOT EXISTS storage_optimize_files(
                    operation_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    value TEXT NOT NULL, PRIMARY KEY(operation_id,ordinal));
            """)
            # Metadata only. Restart observes pending effects; never runs them.
            for identity, raw in self.journal.db.execute(
                    "SELECT id,value FROM storage_optimize_operations WHERE "
                    "json_extract(value,'$.status') IN ('previewing','applying','cancelling')"):
                value = json.loads(raw)
                value.update(status='interrupted', revision=value['revision'] + 1)
                self._save_operation(value)

    def _save_operation(self, value):
        self.journal.db.execute('INSERT OR REPLACE INTO storage_optimize_operations VALUES(?,?,?)',
                               (value['operationId'], value['_owner'], json.dumps(value)))

    def _get(self, identity, owner):
        row = self.journal.db.execute(
            'SELECT value FROM storage_optimize_operations WHERE id=? AND owner=?',
            (identity, owner)).fetchone()
        if not row:
            raise AppError('This saved-storage operation is unavailable to this caller.', 404)
        return json.loads(row[0])

    def policy(self):
        from .storage_cleanup import record
        return {**DEFAULT_POLICY, **(record(self.app, POLICY_KEY, {}) or {})}

    def _authority(self, origin, caller):
        from .storage_cleanup import capture
        if origin in {'ui', 'user'}:
            return 'user', None
        if origin not in {'agent', 'system'} or not caller:
            raise AppError('Saved-storage maintenance needs user control or Full access.', 403)
        source = self.app._session(caller, hydrate=False)
        return caller, capture(self.app, source, origin)

    def _check(self, value):
        from .storage_cleanup import check
        check(self.app, value.get('_authority'))

    def read(self, owner, identity=None, *, after=0, limit=50):
        with self.journal.lock:
            if identity is None:
                row = self.journal.db.execute(
                    'SELECT id FROM storage_optimize_operations WHERE owner=? ORDER BY rowid DESC LIMIT 1',
                    (owner,)).fetchone()
                if not row:
                    return {'operation': None}
                identity = row[0]
            value = self._get(identity, owner)
            page = self.journal.db.execute(
                'SELECT ordinal,value FROM storage_optimize_files WHERE operation_id=? AND ordinal>? '
                'ORDER BY ordinal LIMIT ?', (identity, after, limit + 1)).fetchall()
        rows = [{key: item for key, item in json.loads(raw).items() if not key.startswith('_')}
                for _, raw in page[:limit]]
        allowed = True
        try:
            self._check(value)
        except (ValueError, AppError):
            allowed = False
        return {**{key: item for key, item in value.items() if not key.startswith('_')},
                'rows': rows, 'nextAfter': page[limit - 1][0] if len(page) > limit else None,
                'canApply': allowed and value['status'] == 'previewed' and value['summary']['coverage'].get('eligible', 0) > 0,
                'canResume': allowed and value.get('_phase') != 'preview'
                    and value['status'] in {'interrupted', 'cancelled', 'partial'}
                    and any(value['summary']['coverage'].get(key, 0) for key in ('eligible', 'attempting')),
                'canCancel': allowed and value['status'] in ACTIVE}

    def _update(self, identity, owner, **changes):
        with self.journal.lock, self.journal.db:
            value = self._get(identity, owner)
            value.update(changes, revision=value['revision'] + 1)
            self._save_operation(value)
        return value

    def _file(self, identity, ordinal, value):
        with self.journal.lock, self.journal.db:
            operation = self.journal.db.execute(
                'SELECT value FROM storage_optimize_operations WHERE id=?', (identity,)).fetchone()
            operation = json.loads(operation[0])
            if 'operationId' in value and value['operationId'] != identity:
                raise ValueError('File outcome belongs to another saved-storage operation')
            value = {**value, 'operationId': identity}
            summary = operation['summary']
            accounting = operation.setdefault('_accounting', {})
            old = self.journal.db.execute(
                'SELECT value FROM storage_optimize_files WHERE operation_id=? AND ordinal=?',
                (identity, ordinal)).fetchone()
            def account(row, sign):
                coverage = summary['coverage']
                coverage[row['status']] = coverage.get(row['status'], 0) + sign
                key = {'root': 'roots', 'worker': 'workers'}.get(row.get('kind'), 'unknown')
                coverage[key] = coverage.get(key, 0) + sign
                if row.get('bytes') is not None:
                    size = summary['bytes']
                    source = accounting.setdefault('source', {'known': 0, 'unknown': 0, 'sum': 0})
                    source['known'] += sign
                    source['sum'] += sign * row['bytes']
                else:
                    source = accounting.setdefault('source', {'known': 0, 'unknown': 0, 'sum': 0})
                    source['unknown'] += sign
                summary['bytes']['source'] = source['sum'] if source['known'] and not source['unknown'] else None
                # A measured zero is distinct from unknown. Replacing a file
                # outcome subtracts its old contribution before adding the new
                # one; resume/read never double-counts logical or actual bytes.
                facts = row.get('_accounting', {})
                for name in ('inlineRemoved', 'assetsAdded', 'refsAndJournalAdded',
                             'recoveryRetained', 'unlinked', 'filesystemDelta'):
                    count = accounting.setdefault(name, {'known': 0, 'unknown': 0, 'sum': 0})
                    amount = facts.get(name)
                    if type(amount) is int:
                        count['known'] += sign
                        count['sum'] += sign * amount
                    else:
                        count['unknown'] += sign
                    summary['bytes'][name] = count['sum'] if count['known'] and not count['unknown'] else None
            if old:
                account(json.loads(old[0]), -1)
            account(value, 1)
            self.journal.db.execute('INSERT OR REPLACE INTO storage_optimize_files VALUES(?,?,?)',
                                   (identity, ordinal, json.dumps(value)))
            operation['revision'] += 1
            self._save_operation(operation)

    def _summary(self, identity, owner, *, complete):
        # Updated with each file in the same transaction: progress is O(files),
        # not a repeated GROUP BY over all previous files after every checkpoint.
        summary = self._get(identity, owner)['summary']
        summary['coverage']['complete'] = complete
        return summary

    def _launch(self, value, phase):
        identity = value['operationId']
        task = asyncio.create_task(self._work(identity, value['_owner'], phase))
        self.tasks[identity] = task
        def settled(done):
            self.tasks.pop(identity, None)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(settled)

    def _owner_adapter(self):
        if self.adapter is None:
            from .storage_optimize_owners import OwnerAdapters
            self.adapter = OwnerAdapters(self.app)
        return self.adapter

    async def _work(self, identity, owner, phase):
        try:
            value = self._get(identity, owner)
            adapter = self._owner_adapter()
            if phase == 'preview':
                self._update(identity, owner, _phase='preview')
                ordinal = 0
                async for row in adapter.discover(value):
                    value = self._get(identity, owner)
                    self._check(value)
                    if value['status'] == 'cancelling':
                        self._update(identity, owner, status='cancelled')
                        return
                    ordinal += 1
                    try:
                        row = await adapter.preview(row, value)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        row = {**row, 'status': 'failed',
                               'reason': 'This file could not be qualified; original content was kept'}
                    self._file(identity, ordinal, row)
                    self._update(identity, owner, summary=self._summary(identity, owner, complete=False))
                value = self._get(identity, owner)
                self._check(value)
                self._update(identity, owner, status='cancelled' if value['status'] == 'cancelling' else 'previewed',
                             _phase='preview',
                             summary=self._summary(identity, owner, complete=True))
                return
            after = 0
            while True:
                # Includes skipped/completed rows: explicit Stop and cancellation
                # cannot starve behind a long completed ledger on resume.
                await asyncio.sleep(0)
                value = self._get(identity, owner)
                self._check(value)
                if value['status'] == 'cancelling':
                    self._update(identity, owner, status='cancelled')
                    return
                with self.journal.lock:
                    record = self.journal.db.execute(
                        'SELECT ordinal,value FROM storage_optimize_files WHERE operation_id=? AND ordinal>? '
                        'ORDER BY ordinal LIMIT 1', (identity, after)).fetchone()
                if record is None:
                    summary = self._summary(identity, owner, complete=True)
                    issues = sum(summary['coverage'].get(key, 0) for key in ('held', 'unsupported', 'malformed', 'drift', 'failed'))
                    self._update(identity, owner, status='partial' if issues else 'completed', summary=summary)
                    return
                after, raw = record
                row = json.loads(raw)
                if row['status'] not in {'eligible', 'attempting'}:
                    continue
                # Intent is durable before calling an owner that may replace a
                # file. An interrupted attempt is reconciled only by that owner.
                prior = row['status']
                self._file(identity, after, {**row, 'status': 'attempting'})
                try:
                    result = await adapter.apply(row, value, resume=phase == 'resume' or prior == 'attempting')
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # An admitted owner may have reached an effect. Leave the
                    # attempting row and interrupt; a later resume reconciles
                    # that exact owner journal, never skips/replays it.
                    self._update(identity, owner, status='interrupted',
                                 error='A file outcome is unconfirmed. Explicit resume reconciles its saved owner receipt.')
                    return
                self._file(identity, after, result)
                self._update(identity, owner, summary=self._summary(identity, owner, complete=False))
        except asyncio.CancelledError:
            self._update(identity, owner, status='interrupted')
            raise
        except Exception:
            # Do not publish paths, prompt text or arbitrary exception strings.
            self._update(identity, owner, status='interrupted',
                         error='The operation stopped before confirming its next checkpoint. Inspect and explicitly resume.')

    async def dispatch(self, action, args, origin, command_id, caller=None):
        # Receipt inspection remains available after Full is revoked; it cannot
        # mutate anything. Mutations still capture and recheck current authority.
        passive = action in {PREFIX + 'policy.read', PREFIX + 'read'}
        if passive and origin in {'agent', 'system'} and caller:
            self.app._session(caller, hydrate=False)
            owner, authority = caller, None
        else:
            owner, authority = self._authority(origin, caller)
        if action == PREFIX + 'policy.read':
            return {'accepted': True, 'result': self.policy()}
        if action == PREFIX + 'read':
            if args.get('commandId') and args.get('operationId'):
                raise AppError('Read either an operation or a command outcome.', 400)
            if args.get('commandId'):
                try:
                    receipt = self.requests.read(owner, args['commandId'])
                except ValueError:
                    receipt = None
                if receipt and not receipt['action'].startswith(PREFIX):
                    receipt = None
                return {'accepted': True, 'result': {'commandId': args['commandId'],
                    'status': 'recorded' if receipt and receipt['state'] == 'completed' else 'unknown',
                    'operationId': receipt.get('resourceId') if receipt else None,
                    'result': receipt.get('result') if receipt else None}}
            return {'accepted': True, 'result': self.read(owner, args.get('operationId'),
                after=args.get('after', 0), limit=args.get('limit', 50))}
        if not command_id:
            raise AppError('Keep a stable command ID for saved-storage actions.', 400)
        request_args = {**args, 'requestId': command_id}
        async with self.app.lock:
            previous = self.requests.existing(owner, action, request_args, origin)
            if previous:
                return {'accepted': previous['state'] == 'completed', 'result': previous.get('result'),
                        'commandId': command_id, 'status': previous['state'], 'duplicate': True}
            from .storage_cleanup import check
            check(self.app, authority)
            if action == PREFIX + 'policy.save':
                current = self.policy()
                if current['revision'] != args['expectedRevision']:
                    raise AppError('Optimize choices changed. Reload before saving.', 409)
                for key in ('requestKeepHours', 'responseKeepHours'):
                    cutoff(datetime.now(timezone.utc), args[key])
                result = {key: args[key] for key in ('requestKeepHours', 'responseKeepHours')}
                result['revision'] = current['revision'] + 1
            elif action == PREFIX + 'preview':
                current = self.policy()
                if args['policyRevision'] != current['revision']:
                    raise AppError('Optimize choices changed. Preview the current choices.', 409)
                if args['scope'] != 'all' and not args.get('sessionId'):
                    raise AppError('Choose a conversation for this scope.', 400)
                selected = self.app._session(args['sessionId'], hydrate=False) if args.get('sessionId') else None
                now = datetime.now(timezone.utc)
                identity = str(uuid.uuid4())
                result = {'operationId': identity, 'commandId': command_id, 'revision': 0,
                    'status': 'previewing', 'scope': args['scope'], 'sessionId': args.get('sessionId'),
                    'extractImages': args['extractImages'], 'policy': current,
                    'cutoffs': {'requestBefore': cutoff(now, current['requestKeepHours']),
                                'responseBefore': cutoff(now, current['responseKeepHours'])},
                    'summary': {'coverage': {'complete': False},
                        'bytes': {key: None for key in ('source', 'inlineRemoved', 'assetsAdded',
                            'refsAndJournalAdded', 'recoveryRetained', 'unlinked', 'filesystemDelta')}},
                    '_owner': owner, '_authority': authority, '_phase': 'preview',
                    '_workspace': selected['workspace'] if selected else None,
                    '_nativeId': (selected.get('runtimeSessionId') or selected.get('nativeIdentity') or selected['id']) if selected else None}
            else:
                result = self._get(args['operationId'], owner)
                self._check(result)
                if action != PREFIX + 'cancel' and result['revision'] != args['expectedRevision']:
                    raise AppError('This operation changed. Read its current revision.', 409)
                phase = action.removeprefix(PREFIX)
                allowed = {'apply': {'previewed'}, 'resume': {'interrupted', 'cancelled', 'partial'},
                           'cancel': ACTIVE}
                if phase not in allowed or result['status'] not in allowed[phase]:
                    raise AppError('This operation is not ready for that action.', 409)
                if phase in {'apply', 'resume'} and result['operationId'] in self.tasks:
                    raise AppError('The previous worker has not settled yet.', 409)
                if phase == 'resume' and result.get('_phase') == 'preview':
                    raise AppError('Preview was interrupted. Start a new preview; no conversion was admitted.', 409)
                if phase == 'apply' and any(result['cutoffs'].values()) and not args['confirmExpiry']:
                    raise AppError('Confirm the reviewed diagnostic expiry before applying.', 409)
                result.update(status='cancelling' if phase == 'cancel' else 'applying',
                              revision=result['revision'] + 1, _authority=authority)
                if phase in {'apply', 'resume'}:
                    result['_phase'] = 'apply'
            request, _ = self.requests.begin(owner, action, request_args, origin,
                                             resource_id=result.get('operationId'))
            if action == PREFIX + 'policy.save':
                from .storage_cleanup import store
                store(self.app, POLICY_KEY, result)
                public = result
            else:
                with self.journal.lock, self.journal.db:
                    self._save_operation(result)
                public = self.read(owner, result['operationId'])
            self.requests.finish(request, 'completed', result=public)
            if action == PREFIX + 'preview':
                self._launch(result, 'preview')
            elif action in {PREFIX + 'apply', PREFIX + 'resume'}:
                self._launch(result, action.removeprefix(PREFIX))
        return {'accepted': True, 'result': public}

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def owner(app):
    if not hasattr(app, '_saved_storage_optimize'):
        app._saved_storage_optimize = SavedStorageOptimize(app)
    return app._saved_storage_optimize