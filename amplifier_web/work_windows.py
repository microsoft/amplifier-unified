"""Requested event windows, separate from authoritative execution/accounting.

Only small source identity descriptors cross the process boundary. The host
retains at most eight bounded pages; it never merges these observations into an
execution tree, grants admission from them, or starts a runtime for a read.
"""
import asyncio
from collections import OrderedDict
from copy import deepcopy
import hashlib
import math
import secrets
import sys
import time

IDENTITY_FIELDS = ('id', 'workspace', 'nativeProject', 'nativeIdentity', 'runtimeSessionId')
MAX_PAGES = 8
MAX_JOBS = 4
PROOF_SECONDS = 20


class _SupersededRead(ValueError):
    """A newer publication owns this display; the discarded read did not fail."""


def source_identity(session):
    return {key: session[key] for key in IDENTITY_FIELDS if key in session}


def web_context(service):
    clients = getattr(service, 'clients', None)
    identity = clients.current.get() if clients is not None else None
    return identity is not None and clients.records.get(identity, {}).get('kind') == 'web'


class _ThreadedWindowProjector:
    """Bounded fallback until native process memory policy is qualified."""
    def __init__(self, home):
        from .event_window_worker import WindowModel
        self.model, self.tasks, self.closed = WindowModel(home), set(), False

    async def project(self, inputs, *, key):
        if self.closed or len(self.tasks) >= MAX_JOBS:
            return {'status': 'retry', 'reason': 'closed' if self.closed else 'queue_full'}
        def guard():
            if self.closed:
                raise InterruptedError()
        task = asyncio.create_task(asyncio.to_thread(self.model.project, inputs, check_budget=guard))
        self.tasks.add(task)
        def finished(done):
            self.tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def close(self):
        self.closed = True
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)


class WorkWindows:
    def __init__(self, service):
        self.service = service
        self.key = secrets.token_bytes(32)
        self.pages = OrderedDict()
        self.current = OrderedDict()
        self.pending = {}
        self.status = OrderedDict()
        self.epochs = OrderedDict()
        self.epoch = 0
        self.generation = 0
        self.closed = False
        self.projector = None
        self.retry_at = {}
        # Operation descriptors only. All scalar evidence and resulting private
        # references live in the SAME eight retained event pages, never a cache
        # capable of continuing after its supporting pages have been evicted.
        self._root_proofs = OrderedDict()
        self._proof_jobs = {}
        self._proof_draining = set()
        self._cold_reads = OrderedDict()
        from .resident_work_pages import ResidentWorkPages
        self.live_pages = ResidentWorkPages(self)

    def _worker(self):
        if self.projector is None and not sys.platform.startswith('linux'):
            self.projector = _ThreadedWindowProjector(self.service.data_dir)
        if self.projector is None:
            from .history_projection_process import ProcessProjector
            self.projector = ProcessProjector(self.service.data_dir,
                _command=(sys.executable, '-m', 'amplifier_web.event_window_worker'),
                _input_limit=64 * 1024, _output_limit=4 * 1024 * 1024,
                _spool_limit=20 * 1024 * 1024, _max_jobs=MAX_JOBS, _timeout=20)
        return self.projector

    def _owner(self, identity):
        return self.service._session(identity, hydrate=False)

    def _check_current(self, identity, owner, source, fence):
        if (self.closed or getattr(self.service, 'closed', False)
                or self.epochs.get(identity) != fence):
            raise _SupersededRead('The requested conversation changed. Open it again.')
        from .service import AppError
        try:
            current = self._owner(identity)
        except AppError as error:
            if error.status not in {404, 409}:
                raise
            raise _SupersededRead('The requested conversation changed. Open it again.') from None
        if current is not owner or source_identity(owner) != source:
            raise _SupersededRead('The requested conversation changed. Open it again.')

    @staticmethod
    def _window_key(source):
        # Identity only: source mutation invalidates the signed page cursor,
        # while transcript/window navigation never broadens the event source.
        return hashlib.sha256(repr(tuple(source.get(key) for key in IDENTITY_FIELDS)).encode()).hexdigest()

    def _notify(self, identity):
        projections = getattr(self.service, 'projections', None)
        if projections is not None:
            projections.detail_bodies.pop(identity, None)
        event = getattr(self.service, 'event_log_view', None)
        if event is not None:
            event._notify_work(identity)

    def _state(self, identity, value):
        self.status.pop(identity, None)
        self.status[identity] = value
        while len(self.status) > MAX_PAGES:
            retired, _ = self.status.popitem(last=False)
            self.retry_at.pop(retired, None)
        self._notify(identity)

    def invalidate(self, identities=None):
        for identity, proof in tuple(self._root_proofs.items()):
            if identities is None or identity in identities:
                self._expire_proof(proof, 'epoch')
        for identity in set(self.current) | set(self.pending) | set(self.epochs):
            if identities is None or identity in identities:
                self.epoch += 1
                self.epochs.pop(identity, None)
                self.epochs[identity] = self.epoch
                while len(self.epochs) > 32:
                    self.epochs.popitem(last=False)
                current = self.status.get(identity)
                if current and current.get('current'):
                    self._state(identity, {**current, 'current': False, 'status': 'loading'})

    def request(self, identity):
        if self.closed or getattr(self.service, 'closed', False):
            return None
        if identity in self._root_proofs:
            # A finite proof never becomes a perpetual background rescan.
            return None
        task = self.pending.get(identity)
        if task is not None:
            return task
        if (len(self.pending) + len(self._proof_jobs) + len(self._proof_draining) >= MAX_JOBS
                or self.retry_at.get(identity, 0) > time.monotonic()):
            return None
        async def refresh():
            owner = None
            try:
                owner = self._owner(identity)
                source, fence = source_identity(owner), self._fence(identity)
                return await self.read(identity, _initial=True)
            except asyncio.CancelledError:
                raise
            except _SupersededRead:
                return None  # The existing interest loop retries the current owner next tick.
            except Exception:
                # A failure from an obsolete probe cannot replace newer status.
                if owner is not None:
                    try:
                        self._check_current(identity, owner, source, fence)
                    except _SupersededRead:
                        return None
                if not self.closed and self.status.get(identity, {}).get('status') != 'retrying':
                    self.retry_at[identity] = time.monotonic() + 5
                    self._state(identity, {'status': 'retrying', 'current': False, 'reason': 'worker_failed'})
                return None
        task = asyncio.create_task(refresh())
        self.pending[identity] = task
        def finished(done):
            if self.pending.get(identity) is done:
                self.pending.pop(identity, None)
            if not done.cancelled():
                done.exception()  # Fixed public status records failures; consume task errors.
        task.add_done_callback(finished)
        return task

    def _fence(self, identity):
        value = self.epochs.pop(identity, None)
        if value is None:
            self.epoch += 1
            value = self.epoch
        self.epochs[identity] = value
        while len(self.epochs) > 32:
            self.epochs.popitem(last=False)
        return value

    def retain_interest(self, identities):
        self.live_pages.retain_interest(identities)
        for key in tuple(self._cold_reads):
            if key[1] not in identities:
                self._cold_reads.pop(key, None)
        for identity, proof in tuple(self._root_proofs.items()):
            if identity not in identities:
                self._expire_proof(proof, 'departed')
        for identity, task in tuple(self.pending.items()):
            if identity not in identities:
                task.cancel()
        for mapping in (self.epochs, self.retry_at):
            for identity in tuple(mapping):
                if identity not in identities and identity not in self.current and identity not in self.pending:
                    mapping.pop(identity, None)

    async def read(self, identity, before=None, *, _initial=False, proof=False, reopen_proof=False):
        if proof:
            if before is not None or _initial:
                raise ValueError('Root proof requires an explicit latest Work request.')
            return await self.prove(identity, reopen=reopen_proof)
        if _initial and identity in self._root_proofs:
            record = self._root_proofs[identity]
            self._proof_state(identity)
            page = self.pages.get(record['pages'][0][0]) if record['pages'] else None
            if page is not None:
                return self._groups(page, bindings=False)
            return {'items': [], 'turns': [], 'rootProof': self._proof_public(record)}
        if self.closed or getattr(self.service, 'closed', False):
            raise ValueError('Work reading is closed.')
        owner = self._owner(identity)
        identity, source = owner['id'], source_identity(owner)
        window_key = self._window_key(source)
        fence = self._fence(identity)
        prior = self.current.get(identity)
        # Read only exact source stats before deciding whether this small page
        # needs rereading. No transcript, tree, directory scan or prefix digest.
        if _initial and prior is not None and self.pages.get(prior, {}).get('source') == source:
            cached = self.pages[prior]
            matches = await asyncio.to_thread(self._matches, source, cached.get('revisions'))
            self._check_current(identity, owner, source, fence)
            if self.pages.get(prior) is not cached or self.current.get(identity) != prior:
                raise _SupersededRead('The requested conversation changed. Open it again.')
            if matches and self._binding_current(cached):
                current = self.status.get(identity)
                self.pages[prior]['_bindingFence'] = fence
                self._prepare_bound_groups(self.pages[prior], owner)
                receipt = self.pages[prior]['receipt']
                if not current or not current.get('current') or current.get('receipt') != receipt:
                    self._state(identity, {'status': 'ready', 'current': True, 'receipt': receipt})
                return self._groups(self.pages[prior])
        if _initial:
            self._state(identity, {'status': 'loading', 'current': False})
        result = await self._worker().project({'operation': 'window', 'session': source,
            'windowKey': window_key, 'before': before, 'cursorKey': self.key.hex()},
            key=identity + ':' + str(before or 'latest'))
        self._check_current(identity, owner, source, fence)
        if result.get('status') != 'complete':
            if _initial:
                self.retry_at[identity] = time.monotonic() + 5
                self._state(identity, {'status': 'retrying', 'current': False,
                                      'reason': result.get('reason', 'worker_failed')})
            raise ValueError('Saved work is unavailable. Try again shortly.')
        page = result['page']
        matches = await asyncio.to_thread(self._matches, source, page.get('revisions'))
        self._check_current(identity, owner, source, fence)
        if not matches:
            if _initial:
                self.retry_at[identity] = time.monotonic() + 1
                self._state(identity, {'status': 'retrying', 'current': False, 'reason': 'source_changed'})
            raise ValueError('Saved work changed while it was read. Refresh this window.')
        page = {**page, 'sessionId': identity, 'source': source, '_bindingFence': fence}
        if not self._binding_current(page):
            raise _SupersededRead('Saved work ownership changed while it was read.')
        # Index-only commits can change binding while capture bytes stay equal.
        # Keep the accepted page identity/revision sensitive to that dependency.
        if page.get('_bindingRevision') is not None:
            page['generation'] = hashlib.sha256(
                repr((page['generation'], page['_bindingRevision'])).encode()).hexdigest()
        group = 'event-window:' + page['generation']
        page['group'] = group
        self._prepare_bound_groups(page, owner)
        self.pages.pop(group, None)
        self.pages[group] = page
        while len(self.pages) > MAX_PAGES:
            retired, _ = self.pages.popitem(last=False)
            for sid, value in tuple(self.current.items()):
                if value == retired:
                    self.current.pop(sid, None)
                    self.status.pop(sid, None)
                    self.epochs.pop(sid, None)
                    self._notify(sid)
        if _initial or before is None:
            self.current.pop(identity, None)
            self.current[identity] = group
            self.generation += 1
            coverage = page.get('coverage', {})
            page['receipt'] = {
                'hostInstanceId': getattr(self.service, 'instance_id', None), 'sessionId': identity,
                'generation': self.generation, 'scope': 'recent-events', 'windowKey': window_key,
                'historyComplete': False, 'sourceCount': coverage.get('sourceCount', 0),
                'itemCount': len(page['items']), 'scannedBytes': coverage.get('scannedBytes', 0),
                'scannedEvents': coverage.get('scannedEvents', 0)}
            self._state(identity, {'status': 'ready', 'current': True, 'receipt': page['receipt']})
        return self._groups(page)

    @staticmethod
    def _matches(source, revisions):
        from .event_window import _source
        path, _, _ = _source(source)
        if not isinstance(revisions, list) or len(revisions) != 1 or revisions[0][0] != str(path):
            return False
        try:
            info = path.stat()
            actual = [[info.st_dev, info.st_ino], info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        except FileNotFoundError:
            actual = None
        return revisions[0][1] == actual

    def _binding_current(self, page):
        from .event_window import binding_revision
        # Old/no-source pages have no binding dependency and cannot place work.
        return ('_bindingRevision' not in page or
                page['_bindingRevision'] == binding_revision(self.service.data_dir))

    def _bound_current(self, page):
        proof_id = page.get('_consumerProof') or page.get('_consumerSupport')
        if proof_id:
            state = self._proof_state(page['sessionId'], physical=True)
            if not state or state['id'] != proof_id or state['status'] != 'complete':
                return False
        return (page.get('_bindingRevision') is not None
                and self.epochs.get(page['sessionId']) == page.get('_bindingFence')
                and self._binding_current(page)
                and self._matches(page['source'], page.get('revisions')))

    def _expire_proof(self, proof, reason):
        proof.update(status='stale', reason=reason, boundCount=0)
        for group, _, _ in proof['pages']:
            page = self.pages.get(group)
            if page is not None and page.get('_rootProofId') == proof['id']:
                page.pop('_rootProofBound', None)

    @staticmethod
    def _proof_public(proof):
        return {key: proof[key] for key in ('id', 'status', 'reason', 'steps', 'boundCount', 'generation')
                if key in proof}

    def _proof_state(self, identity, session=None, *, physical=False):
        proof = self._root_proofs.get(identity)
        if proof is None:
            return None
        if proof['status'] not in {'pending', 'complete'}:
            return self._proof_public(proof)
        from .service import AppError
        try:
            owner = session if session is not None else self._owner(identity)
        except AppError as error:
            if error.status not in {404, 409}:
                raise
            self._expire_proof(proof, 'owner-unavailable')
            return self._proof_public(proof)
        if (self.closed or self.service.closed or id(owner) != proof['owner']
                or source_identity(owner) != proof['source']
                or self.epochs.get(identity) != proof['fence']):
            self._expire_proof(proof, 'owner-or-epoch')
        elif any(group not in self.pages or id(self.pages[group]) != object_id
                 or self.pages[group].get('_proof', {}).get('receiptDigest') != digest
                 for group, object_id, digest in proof['pages']):
            self._expire_proof(proof, 'support-expired')
        elif physical and proof['pages']:
            page = self.pages[proof['pages'][0][0]]
            if not self._matches(proof['source'], page.get('revisions')) or not self._binding_current(page):
                self._expire_proof(proof, 'source-or-index')
        return self._proof_public(proof)

    def proof_nodes(self, identity, proof_id):
        """Private prerequisite result, not a public field/group route.

        Follow-on consumers must validate this dependency again after awaits.
        No field is routed from this proof by the current bundled HTTP surface.
        """
        state = self._proof_state(identity, physical=True)
        if state is None or state['id'] != proof_id or state['status'] != 'complete':
            raise ValueError('This root proof expired or is incomplete.')
        proof = self._root_proofs[identity]
        page = self.pages[proof['pages'][0][0]]
        return deepcopy(page.get('_rootProofBound', []))

    def live_binding(self, session, row, proof_id=None):
        """Overlay only a full attested invocation match; never discover history."""
        state = self._proof_state(session['id'], session, physical=True)
        if (state is None or state['status'] != 'complete'
                or proof_id is not None and state['id'] != proof_id):
            return None, None
        proof = self._root_proofs[session['id']]
        pages = [self.pages[group] for group, _, _ in proof['pages']]
        # These are authenticated, bounded scalar facts retained by the proof,
        # not JSONL fields promoted to authority by their spelling.
        terminal = []
        for page in pages:
            attested = set(page['_proof']['attestedIds'])
            terminal.extend(fact for fact in page['_proof']['facts']
                if fact['id'] in attested and fact['data'].get('id') == row.get('id')
                and fact['data'].get('phase') in {'completed', 'error'})
        if len(terminal) != 1:
            return None, None
        fact = terminal[0]
        metadata = fact['data']
        fields = ('id', 'kind', 'sessionId', 'rootSessionId', 'turnId', 'parentId',
                  'revision', 'phase', 'startedAt', 'endedAt')
        if any(key not in row or key not in metadata or type(row[key]) is not type(metadata[key])
               or row[key] != metadata[key] for key in fields):
            return None, None
        if row.get('kind') == 'tool' and (
                row.get('toolCallId') != metadata.get('tool_call_id')
                or row['id'] != f"tool:{row['rootSessionId']}:{row.get('toolCallId')}"):
            return None, None
        candidates = [bound for bound in pages[0].get('_rootProofBound', [])
                      if fact['id'] in bound['sourceOccurrenceIds']]
        if len(candidates) != 1:
            return None, None
        return state['id'], deepcopy(candidates[0])

    async def read_proven_field(self, identity, proof_id, bound_id, args):
        """Read one exact proof-owned field; callers additionally fence their view."""
        owner = self._owner(identity)
        proof = self._root_proofs.get(identity)
        rows = self.proof_nodes(identity, proof_id)
        matches = [row for row in rows if row['id'] == bound_id]
        row = matches[0] if len(matches) == 1 else {}
        field = args.get('field')
        reference = row.get('_eventFields', {}).get(field)
        detail = row.get(str(field) + 'Detail', {})
        if reference is None or args.get('digest') != detail.get('digest'):
            raise ValueError('This proven Work field is unavailable.')
        result = await self._worker().project({
            'operation': 'field', 'session': proof['source'], 'reference': reference,
            'offset': int(args.get('offset', 0)),
            'limit': min(16384, max(1, int(args.get('limit', 16000))))},
            key=identity + ':proof-field:' + proof_id + ':' + bound_id)
        if self._owner(identity) is not owner or self._root_proofs.get(identity) is not proof:
            raise ValueError('This proven Work field expired.')
        current = self.proof_nodes(identity, proof_id)
        if not any(value == row for value in current):
            raise ValueError('This proven Work field changed.')
        if result.get('status') != 'complete':
            raise ValueError('This proven Work field changed.')
        value = result['field']
        return {'value': value['text'], 'nextOffset': value['nextOffset'], 'total': value['total']}

    async def read_selected(self, identity, before=None):
        """Explicit selected Work read; never called by state/interest/field polling."""
        owner = self._owner(identity)
        if before is not None:
            # Proof pages already own this exact authenticated continuation.
            # Reuse them rather than replacing support while paging raw evidence.
            proof = self._root_proofs.get(identity)
            if proof is not None:
                self._proof_state(identity, owner, physical=True)
                for group, object_id, _ in proof['pages']:
                    page = self.pages.get(group)
                    if (page is not None and id(page) == object_id
                            and page['_proof']['requestBefore'] == before):
                        return self._groups(page, bindings=proof['status'] == 'complete')
            return await self.read(identity, before=before)
        prior = self._proof_state(identity, owner, physical=True)
        # A fresh explicit latest request may reopen superseded support. Passive
        # requests and field/sync reads cannot take this path or retry a proof.
        state = await self.prove(identity, reopen=bool(prior and prior['status'] in {'stale', 'incomplete'}))
        proof = self._root_proofs.get(identity)
        if state['status'] == 'stale':
            raise _SupersededRead('The requested conversation changed. Open it again.')
        if self._owner(identity) is not owner or not proof or proof['id'] != state['id']:
            raise _SupersededRead('The requested conversation changed. Open it again.')
        pages = [self.pages[group] for group, _, _ in proof['pages']]
        if not pages:
            return {'items': [], 'turns': [], 'mode': 'cursor', 'before': None,
                    'hasEarlier': False, 'total': None, 'rootProof': state,
                    'coverage': {'historyComplete': False}, 'unavailableRanges': []}
        first = pages[0]
        # Proof-support pages are raw evidence, never a second set of ordinary
        # page-local groups. Only this selected consumer's cumulative placement
        # below can expose ordinary calls.
        if first.get('_consumerProof') != proof['id']:
            for page in pages:
                page['_boundGroups'], page['_boundNodes'], page['_boundTurns'] = {}, {}, {}
                page['_unassociatedIds'] = {row['id'] for row in page['items']}
                page['_consumerSupport'] = proof['id']
        if state['status'] == 'complete':
            if first.get('_consumerProof') != proof['id']:
                first['_consumerProof'] = proof['id']
                first['_consumerRevision'] = proof['id']
                self._prepare_bound_groups(first, owner, bound_work=first.get('_rootProofBound', []))
                for group in first['_boundGroups'].values():
                    group['detailRevision'] = proof['id']
                used = {occurrence for rows in first['_boundNodes'].values()
                        for row in rows for occurrence in row['sourceOccurrenceIds']}
                from .cold_display import raw
                # Exact resident invocation matches suppress duplicate raw work,
                # but raw evidence remains available through observations().
                resident_nodes = raw(owner).get('execution', {}).get('nodes', [])
                for start in range(max(0, len(resident_nodes) - 800), len(resident_nodes), 100):
                    for row in resident_nodes[start:start + 100]:
                        _, bound = self.live_binding(owner, row, proof['id'])
                        if bound is not None:
                            used.update(bound['sourceOccurrenceIds'])
                for page in pages:
                    page['_consumerSupport'] = proof['id']
                    page['_unassociatedIds'] = {row['id'] for row in page['items']} - used
        current = self._proof_state(identity, physical=True)
        if (current is None or current['id'] != state['id'] or current['status'] != state['status']
                or self._root_proofs.get(identity) is not proof
                or self._owner(identity) is not owner):
            raise _SupersededRead('The requested conversation changed. Open it again.')
        state = current
        self.current[identity] = first['group']
        self.generation += 1
        first['receipt'] = {'hostInstanceId': getattr(self.service, 'instance_id', None),
            'sessionId': identity, 'generation': self.generation, 'scope': 'root-proof',
            'windowKey': first['windowKey'], 'historyComplete': False,
            'proofId': proof['id'], 'itemCount': len(first['items'])}
        self._state(identity, {'status': 'ready', 'current': True,
                               'receipt': first['receipt'], 'rootProof': state})
        return {**self._groups(first, bindings=state['status'] == 'complete'), 'rootProof': state}

    async def prove(self, identity, *, reopen=False):
        """One explicit, joinable, <=8-page proof; never entered from polling."""
        if self.closed or self.service.closed:
            raise ValueError('Work reading is closed.')
        owner = self._owner(identity)
        identity = owner['id']
        task = self._proof_jobs.get(identity)
        if task is not None:
            return await self._await_proof(identity, task, self._root_proofs[identity])
        previous = self._root_proofs.get(identity)
        if previous is not None and not reopen:
            return self._proof_state(identity, owner, physical=True)
        if len(self.pending) + len(self._proof_jobs) + len(self._proof_draining) >= MAX_JOBS:
            raise ValueError('The bounded Work reader is busy.')
        if previous is not None:
            self._expire_proof(previous, 'explicit-reopen')
        if previous is None and len(self._root_proofs) >= MAX_PAGES:
            retired = next((key for key in self._root_proofs if key not in self._proof_jobs), None)
            if retired is None:
                raise ValueError('The bounded Work proof registry is busy.')
            self._expire_proof(self._root_proofs.pop(retired), 'operation-expired')
        record = {'id': secrets.token_hex(16), 'owner': id(owner), 'source': source_identity(owner),
                  'fence': self._fence(identity), 'status': 'pending', 'reason': None,
                  'steps': 0, 'boundCount': 0, 'pages': [],
                  'deadline': time.monotonic() + PROOF_SECONDS,
                  'window': self._window_key(source_identity(owner))}
        self._root_proofs.pop(identity, None)
        self._root_proofs[identity] = record
        task = asyncio.create_task(self._prove(identity, owner, record))
        self._proof_jobs[identity] = task
        def finished(done):
            if self._proof_jobs.get(identity) is done:
                self._proof_jobs.pop(identity, None)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        self._notify(identity)
        return await self._await_proof(identity, task, record)

    async def _await_proof(self, identity, task, record):
        result = await asyncio.shield(task)
        if self._root_proofs.get(identity) is not record:
            return {**result, 'status': 'stale', 'reason': 'operation-expired', 'boundCount': 0}
        # Task completion is not publication to this waiter: a callback can
        # supersede the owner or support after the task's last instruction.
        return self._proof_state(identity, physical=True)

    async def _proof_step(self, inputs, key, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        task = asyncio.create_task(self._worker().project(inputs, key=key))
        try:
            return await asyncio.wait_for(asyncio.shield(task), remaining)
        except BaseException:
            # ProcessProjector shields its physical work. Keep that work owned
            # and counted even when this aggregate operation stops accepting it.
            if not task.done():
                self._proof_draining.add(task)
                def settled(done):
                    self._proof_draining.discard(done)
                    if not done.cancelled():
                        done.exception()
                task.add_done_callback(settled)
            raise

    def _proof_guard(self, identity, owner, proof):
        if time.monotonic() >= proof['deadline']:
            raise TimeoutError()
        self._check_current(identity, owner, proof['source'], proof['fence'])
        if self._root_proofs.get(identity) is not proof or proof['status'] == 'stale':
            raise _SupersededRead('The root proof was superseded.')
        if self._proof_state(identity, owner)['status'] == 'stale':
            raise _SupersededRead('The root proof support expired.')

    async def _prove(self, identity, owner, proof):
        from .event_window import validate_root_proof, StaleWindow
        before, stamp, index = None, None, None
        try:
            for step in range(MAX_PAGES):
                self._proof_guard(identity, owner, proof)
                inputs = {'operation': 'proof-step', 'session': proof['source'],
                          'windowKey': proof['window'], 'before': before, 'cursorKey': self.key.hex(),
                          'deadline': proof['deadline']}
                if step:
                    inputs.update(expectedSource=stamp, expectedIndex=index)
                result = await self._proof_step(inputs, identity + ':root-proof:' + proof['id'] + ':' + str(step),
                                                proof['deadline'])
                self._proof_guard(identity, owner, proof)
                if result.get('status') != 'complete':
                    reason = result.get('reason', 'worker-failed')
                    if reason == 'source_changed':
                        self._expire_proof(proof, 'source-or-index')
                    else:
                        proof.update(status='incomplete', reason=reason)
                    return self._proof_public(proof)
                page = result['page']
                manifest = page.get('_proof')
                if not manifest:
                    proof.update(status='incomplete', reason='missing-proof')
                    return self._proof_public(proof)
                if not step:
                    stamp, index = manifest['sourceStamp'], manifest['indexRevision']
                    proof['generation'] = page['generation']
                elif manifest['sourceStamp'] != stamp or manifest['indexRevision'] != index:
                    raise StaleWindow('The root proof source or index changed.')
                if not self._matches(proof['source'], page.get('revisions')) or not self._binding_current(page):
                    raise StaleWindow('The root proof dependency changed.')
                group = 'event-window:' + page['generation']
                admitted = {**page, 'sessionId': identity, 'source': proof['source'],
                            'group': group, '_bindingFence': proof['fence'],
                            '_rootProofId': proof['id']}
                self._prepare_bound_groups(admitted, owner)
                # Never continue by evicting this operation's own evidence.
                if group not in self.pages and len(self.pages) == MAX_PAGES:
                    retired = next(iter(self.pages))
                    if any(retired == key for key, _, _ in proof['pages']):
                        proof.update(status='incomplete', reason='page-limit')
                        return self._proof_public(proof)
                    self.pages.pop(retired)
                    for sid, value in tuple(self.current.items()):
                        if value == retired:
                            self.current.pop(sid, None)
                            self.status.pop(sid, None)
                            self._notify(sid)
                self.pages[group] = admitted
                self.pages.move_to_end(group)
                proof['pages'].append((group, id(admitted), manifest['receiptDigest']))
                proof['steps'] = step + 1
                if index is None or manifest.get('missing'):
                    proof.update(status='incomplete', reason='index-or-source-unavailable')
                    return self._proof_public(proof)
                if (manifest.get('shapeInvalid') or manifest.get('invalid')
                        or manifest.get('partial') or manifest.get('blocked') or manifest.get('gaps')):
                    proof.update(status='incomplete', reason='incomplete-source')
                    return self._proof_public(proof)
                before = page.get('before')
                if before is None:
                    break
            else:
                proof.update(status='incomplete', reason='page-limit')
                return self._proof_public(proof)
            self._proof_guard(identity, owner, proof)
            pages = [self.pages[group] for group, _, _ in proof['pages']]
            validated = validate_root_proof(proof['source'], pages, cursor_key=self.key,
                check_budget=lambda: self._proof_guard(identity, owner, proof))
            self._proof_guard(identity, owner, proof)
            first = pages[0]
            # Final physical validation happens AFTER every step, SQLite close,
            # and pure aggregation, immediately before publication.
            if not self._matches(proof['source'], first.get('revisions')) or not self._binding_current(first):
                raise StaleWindow('The root proof changed before publication.')
            if validated['status'] != 'complete':
                proof.update(status='incomplete', reason=validated.get('reason', 'incomplete'))
            else:
                # Cache references/metadata only inside an existing support page.
                # No payload body or duplicate observation collection is stored.
                references = [
                    {key: value for key, value in row.items()
                     if key not in {'input', 'output', 'error', 'request', 'response'}}
                    for row in validated['bound']]
                self._proof_guard(identity, owner, proof)
                first['_rootProofBound'] = references
                proof.update(status='complete', reason=None, boundCount=len(validated['bound']))
            return self._proof_public(proof)
        except (StaleWindow, _SupersededRead):
            self._expire_proof(proof, 'source-owner-or-fence')
            return self._proof_public(proof)
        except TimeoutError:
            proof.update(status='incomplete', reason='deadline')
            return self._proof_public(proof)
        except asyncio.CancelledError:
            self._expire_proof(proof, 'cancelled')
            raise
        except (OSError, ValueError, KeyError, TypeError):
            proof.update(status='incomplete', reason='proof-failed')
            return self._proof_public(proof)
        finally:
            self._notify(identity)

    @staticmethod
    def _prepare_bound_groups(page, owner, *, bound_work=None):
        """Place proved root calls only against already resident host facts.

        Never hydrate a cold record, infer a turn from time, or duplicate live
        steps. Off-page/ambiguous placement stays in the existing saved window.
        """
        from .cold_display import raw
        resident = raw(owner)
        tree = resident.get('execution', {})
        page['_boundGroups'], page['_boundNodes'], page['_boundTurns'] = {}, {}, {}
        used = set()
        turns = tree.get('turns', [])[-20:]
        messages = resident.get('messages', [])[-100:]
        # This increment is cold-only. Live ordinary refs are a separate promise.
        if any(row.get('liveObservation') for row in tree.get('nodes', [])[-100:]):
            page['_unassociatedIds'] = {row['id'] for row in page['items']}
            return
        for original in page.get('_boundWork', []) if bound_work is None else bound_work:
            candidates = [t for t in turns if t.get('id') == original['turnId']
                          and t.get('phase') == 'completed']
            if len(candidates) != 1:
                continue
            turn = candidates[0]
            scoped = [m for m in messages if m.get('inputId') == turn['id']
                      or turn['id'] in (m.get('response') or {}).get('inputIds', [])]
            users = [m for m in scoped if m.get('role') == 'user'
                     and m.get('id') == turn.get('anchorMessageId')]
            if len(users) != 1 or not scoped:
                continue
            def known(message):
                value = message.get('createdAt')
                return (message.get('timestampKnown') is not False
                        and type(value) in (int, float) and math.isfinite(value))
            if not all(known(m) for m in scoped):
                continue
            times = [m['createdAt'] for m in scoped]
            if len(set(times)) != len(times):
                continue
            preceding = [m for m in scoped if m['createdAt'] <= original['startedAt']]
            if not preceding:
                continue
            anchor = max(preceding, key=lambda m: m['createdAt'])
            if ((anchor.get('response') or {}).get('phase') == 'final'
                    or anchor['createdAt'] < users[0]['createdAt']):
                continue
            logical = turn['id'] + '@' + anchor['id']
            group = page['group'] + ':bound:' + hashlib.sha256(logical.encode()).hexdigest()[:24]
            node = {**original, 'anchorMessageId': anchor['id'],
                    'evidence': WorkWindows._evidence(page)}
            page['_boundNodes'].setdefault(group, []).append(node)
            used.update(node['sourceOccurrenceIds'])
            page['_boundTurns'][turn['id']] = {key: turn[key] for key in
                ('id', 'phase', 'anchorMessageId', 'startedAt', 'endedAt') if key in turn}
            page['_boundGroups'][group] = {'id': group, 'detailGroup': group,
                'turnId': turn['id'], 'anchorMessageId': anchor['id'], 'phase': 'completed',
                'bindingProvenance': 'host-diagnostics-receipt',
                'placementProvenance': 'timestamp-within-proven-turn',
                'partialWindow': True, 'nodeCounts': None, 'aggregateUsage': None,
                'detailRevision': WorkWindows._public_revision(page), 'coverage': page['coverage'],
                'evidence': WorkWindows._evidence(page)}
        for group, rows in page['_boundNodes'].items():
            page['_boundGroups'][group].update(
                startedAt=min(row['startedAt'] for row in rows),
                endedAt=max(row['endedAt'] for row in rows))
        page['_unassociatedIds'] = {row['id'] for row in page['items']} - used

    @staticmethod
    def _public_revision(page):
        # Consumer publication can change without changing the signed page.
        return page.get('_consumerRevision', page['generation'])

    @staticmethod
    def _evidence(page):
        return {'sessionId': page['sessionId'], 'part': 'observations',
                'group': page['group'], 'revision': WorkWindows._public_revision(page)}

    @staticmethod
    def _groups(page, *, bindings=True):
        identity = page['group']
        revision = WorkWindows._public_revision(page)
        coverage = page.get('coverage', {})
        group = {'id': identity, 'turnId': identity, 'anchorMessageId': None,
            'observationWindow': True, 'phase': 'recorded', 'label': 'Recent saved work',
            'nodeCounts': None, 'aggregateUsage': None, 'detailRevision': revision,
            'coverage': coverage, 'bindingCoverage': page.get('bindingCoverage'),
            'evidence': WorkWindows._evidence(page)}
        separate = bool((page.get('_unassociatedIds', page['items']) if bindings else page['items'])
                        or page.get('unavailableRanges'))
        return {'items': [*([group] if separate else []),
                         *(page.get('_boundGroups', {}).values() if bindings else [])],
            'turns': [*([{'id': identity, 'phase': 'recorded', 'observationWindow': True}] if separate else []),
                      *(page.get('_boundTurns', {}).values() if bindings else [])],
            'mode': 'cursor', 'offset': None, 'total': None, 'before': page.get('before'),
            'hasEarlier': page.get('before') is not None, 'revision': revision,
            'windowKey': page['windowKey'], 'coverage': coverage,
            'evidence': WorkWindows._evidence(page),
            'unavailableRanges': page.get('unavailableRanges', [])}

    def live_view(self, session):
        """Bounded current host evidence; never scan historical native nodes."""
        from .cold_display import raw
        resident = raw(session)
        tree = resident.get('execution', {})
        source = {key: value for key, value in resident.items() if not key.startswith('_cold')}
        source.update(messages=resident.get('messages', [])[-100:],
            generations=resident.get('generations', [])[-20:])
        source.update({
            'execution': {**{key: tree[key] for key in ('aggregateUsage', 'usage', 'plan', 'currentTurnId') if key in tree},
                'nodes': [row for row in tree.get('nodes', [])[-100:]
                if row.get('liveObservation') or not row.get('canonicalHistory') and not row.get('nativeHistory')],
                'turns': tree.get('turns', [])[-20:], 'currentTurnId': tree.get('currentTurnId')}})
        return source

    def project(self, session):
        page = self.pages.get(self.current.get(session['id']))
        rebound = page is not None and page['source'] != source_identity(session)
        if rebound:
            page = None
        # Snapshots do no source/index IO. Published invalidation hides binding;
        # explicit node/field reads also recheck physical source/index stamps.
        root_proof = self._proof_state(session['id'], session)
        page_current = bool(page is not None and not self.closed and not self.service.closed
                            and self.epochs.get(session['id']) == page.get('_bindingFence'))
        support_id = (page.get('_consumerProof') or page.get('_consumerSupport')
                      or page.get('_rootProofId')) if page else None
        if support_id:
            proof = self._root_proofs.get(session['id'])
            # Incomplete association can still describe a current bounded raw
            # page. It cannot survive owner/fence changes or loss of support.
            # Check only retained facts, including incomplete proof descriptors
            # which the field-authority path deliberately never accepts.
            support_current = bool(proof and root_proof and root_proof['id'] == support_id
                and root_proof['status'] in {'complete', 'incomplete'}
                and proof['owner'] == id(session) and proof['source'] == source_identity(session)
                and proof['fence'] == self.epochs.get(session['id']) and proof['pages']
                and all(group in self.pages and id(self.pages[group]) == object_id
                        and self.pages[group].get('_proof', {}).get('receiptDigest') == digest
                        for group, object_id, digest in proof['pages']))
            page_current = page_current and support_current
            if root_proof is None:
                root_proof = {'id': support_id, 'status': 'stale',
                              'reason': 'operation-expired', 'boundCount': 0}
        bindings = page_current and (not support_id or (
            page.get('_consumerProof') == support_id and root_proof['status'] == 'complete'))
        groups = self._groups(page, bindings=bindings) if page else {'items': [], 'turns': [], 'mode': 'cursor',
            'offset': None, 'total': None, 'before': None, 'hasEarlier': False,
            'coverage': {'kind': 'event-window', 'historyComplete': False}}
        from .browser_detail import page as detail_page
        live_source = self.live_view(session)
        live = detail_page(live_source, 'groups')
        live['items'] = [{**row, 'detailRevision': self.live_pages.revision_for(
            session, row['id'], row['detailRevision'])} for row in live['items']]
        from .cold_display import raw
        if len(raw(session).get('execution', {}).get('nodes', [])) > 100:
            # The tail is display evidence, not a complete current-turn count.
            live['items'] = [{**row, 'partialWindow': True, 'nodeCounts': None,
                              'aggregateUsage': None} for row in live['items']]
            live['turns'] = [{key: value for key, value in row.items()
                              if key not in {'nodeCounts', 'aggregateUsage'}}
                             for row in live['turns']]
        from .image_generation import project as image_work
        work_status = (self.status.get(session['id'], {'status': 'loading', 'current': False})
                       if not rebound else {'status': 'loading', 'current': False})
        # A retained publication is not current merely because its status says
        # ready. The displayed page and its exact publication must still exist.
        # Retain a noncurrent receipt as observation, never as field authority.
        work_status = {key: value for key, value in work_status.items() if key != 'rootProof'}
        work_status['current'] = bool(work_status.get('current') and page_current
            and work_status.get('status') == 'ready'
            and work_status.get('receipt') == page.get('receipt'))
        if root_proof is not None:
            work_status = {**work_status, 'rootProof': root_proof}
        return {'execution': {**{key: value for key, value in live_source['execution'].items()
                                  if key not in {'nodes', 'turns'}},
                             'imageGeneration': image_work(live_source, live_source['execution']),
                             'nodes': [], 'turns': [*live['turns'], *groups['turns']],
                             'segments': [*live['items'], *groups['items']],
                             'detailsDeferred': True},
                'executionWindow': {'part': 'groups', **{key: value for key, value in groups.items() if key not in {'items', 'turns'}}},
                'workBindingCoverage': ({'status': page.get('bindingCoverage', {}).get('status', 'unverified'),
                    'placedGroups': len(page.get('_boundGroups', {})) if bindings else 0,
                    'unassociated': len(page.get('_unassociatedIds', page['items']) if bindings else page['items'])} if page else None),
                'workProjection': work_status}

    def _page(self, identity, group):
        owner = self._owner(identity)  # Current canonical authority, not cache presence.
        page = self.pages.get(group) or next((value for value in self.pages.values()
                                            if group in value.get('_boundGroups', {})), None)
        if page is None or page['sessionId'] != identity or source_identity(owner) != page['source']:
            raise ValueError('This saved work window expired. Open the recent window again.')
        if group in page.get('_boundGroups', {}) and not self._bound_current(page):
            raise ValueError('This saved work binding expired. Reopen its Work window.')
        self.pages.move_to_end(page['group'])
        return page

    def nodes(self, identity, group, *, revision=None, known=None, before=None, paged=False, preserve=False):
        page = self._page(identity, group)
        if page.get('_consumerProof') and group in page.get('_boundGroups', {}):
            return self._consumer_nodes(identity, group, page, revision=revision,
                known=known, before=before, paged=paged, preserve=preserve)
        public_revision = self._public_revision(page)
        if revision is not None and revision != public_revision:
            raise ValueError('This saved work window changed. Open it again.')
        items = []
        bound = group in page.get('_boundGroups', {})
        current_binding = self._bound_current(page)
        unassociated = page.get('_unassociatedIds', {r['id'] for r in page['items']})
        selected = page['_boundNodes'][group] if bound else [
            row for row in page['items'] if not current_binding or row['id'] in unassociated]
        if paged and known is not None:
            if (not isinstance(known, dict) or len(known) > 100
                    or not all(isinstance(k, str) and isinstance(v, str) for k, v in known.items())
                    or not set(known) <= {row['id'] for row in selected}):
                raise ValueError('This bounded Work window expired.')
            selected = [row for row in selected if row['id'] in known]
        for original in selected:
            row = {key: deepcopy(value) for key, value in original.items() if key != '_eventFields'}
            row.update(turnId=original['turnId'] if bound else group, detailVersion=public_revision,
                       observationWindow=not bound)
            for field in ('input', 'output', 'error', 'request', 'response'):
                if field + 'Detail' in row:
                    row[field + 'Detail'].update(sessionId=identity, windowKey=group)
            if known is None or known.get(row['id']) != row['detailVersion']:
                items.append(row)
        return {'items': items, 'order': [row['id'] for row in selected],
                'offset': 0, 'total': len(selected), 'before': None,
                'group': group, 'revision': public_revision, 'coverage': page['coverage']}

    def _consumer_nodes(self, identity, group, page, *, revision, known, before, paged, preserve):
        """Bounded client-owned node pages over the already accepted root proof."""
        version, proof_id = self._public_revision(page), page['_consumerProof']
        if revision is not None and revision != version:
            raise ValueError('This Work proof changed. Reopen its latest steps.')
        client = self.live_pages._client()
        key = client, identity, group
        entry = self._cold_reads.get(key)
        rows = page['_boundNodes'][group]
        if known is not None:
            if (not isinstance(known, dict) or len(known) > 100
                    or not all(isinstance(k, str) and isinstance(v, str) for k, v in known.items())
                    or entry is None or entry['page'] != id(page)
                    or entry['proof'] != proof_id or not set(known) <= entry['ids']):
                raise ValueError('This bounded Work window expired.')
            selected = [row for row in rows if row['id'] in known]
            cursor = entry.get('before')
        else:
            if before is not None:
                token = self.live_pages._parse(before)
                if (entry is None or entry['page'] != id(page) or entry['proof'] != proof_id
                        or not isinstance(token, dict)
                        or any(token.get(k) != v for k, v in {
                            'kind': 'cold-proof-page', 'client': client, 'session': identity,
                            'group': group, 'revision': version, 'entry': entry['key'],
                            'fence': self.epochs.get(identity)}.items())):
                    raise ValueError('This Work cursor expired.')
                end = token.get('end')
                if type(end) is not int or not 0 < end <= len(rows):
                    raise ValueError('This Work cursor expired.')
            else:
                end = len(rows)
                if not preserve or entry is None or entry['page'] != id(page) or entry['proof'] != proof_id:
                    entry = {'page': id(page), 'proof': proof_id, 'ids': set(),
                        'key': 'proof-work:' + self.live_pages._token(
                            [client, identity, group, version, secrets.token_hex(16)])}
            start = max(0, end - 100)
            selected = rows[start:end]
            entry['ids'].update(row['id'] for row in selected)
            if len(entry['ids']) > 800:
                raise ValueError('This Work window reached its bounded page limit.')
            cursor = self.live_pages._token({'kind': 'cold-proof-page', 'client': client,
                'session': identity, 'group': group, 'revision': version,
                'entry': entry['key'], 'fence': self.epochs.get(identity), 'end': start}) if start else None
            if preserve and before is None and 'before' in entry:
                cursor = entry['before']
            entry['before'] = cursor
            self._cold_reads.pop(key, None)
            self._cold_reads[key] = entry
            while len(self._cold_reads) > MAX_PAGES:
                self._cold_reads.popitem(last=False)
        items = []
        for original in selected:
            if known is not None and known.get(original['id']) == version:
                continue
            row = {k: deepcopy(v) for k, v in original.items() if k != '_eventFields'}
            row.update(detailVersion=version, observationWindow=False)
            for field in ('input', 'output', 'error', 'request', 'response'):
                if field + 'Detail' in row:
                    row[field + 'Detail'].update(sessionId=identity, id=row['id'],
                        windowKey=entry['key'], proofId=proof_id)
            items.append(row)
        return {'items': items, 'order': [row['id'] for row in selected],
                'mode': 'cursor', 'offset': None, 'total': len(rows), 'before': cursor,
                'hasEarlier': cursor is not None, 'group': group, 'revision': version,
                'coverage': page['coverage'], 'rootProof': self._proof_state(identity)}

    def observations(self, identity, group, revision):
        """Read original occurrences, independently of ordinary Work placement."""
        # Only the accepted root page owns raw fields. A bound subgroup is not
        # an alternate spelling, and ownership-index authority is not claimed.
        if group not in self.pages:
            raise ValueError('This saved evidence window expired. Open its Work window again.')
        page = self._page(identity, group)
        public_revision = self._public_revision(page)
        if revision != public_revision:
            raise ValueError('This saved evidence window changed. Open it again.')
        if not self._matches(page['source'], page.get('revisions')):
            raise ValueError('This saved evidence source changed. Open its Work window again.')
        items = []
        for original in page['items']:
            row = {key: deepcopy(value) for key, value in original.items() if key != '_eventFields'}
            row.update(turnId=group, detailVersion=public_revision, observationWindow=True)
            for field in ('input', 'output', 'error', 'request', 'response'):
                if field + 'Detail' in row:
                    row[field + 'Detail'].update(sessionId=identity, windowKey=group)
            items.append(row)
        return {'items': items, 'order': [row['id'] for row in items],
                'sessionId': identity, 'offset': 0, 'total': len(items), 'before': None,
                'group': group, 'revision': public_revision, 'coverage': page['coverage'],
                'evidence': self._evidence(page)}

    async def read_text(self, identity, args):
        if str(args.get('windowKey', '')).startswith('proof-work:'):
            return await self._read_consumer_field(identity, args)
        owner = self._owner(identity)
        page = self._page(owner['id'], args.get('windowKey'))
        group = args.get('windowKey')
        bound = group in page.get('_boundGroups', {})
        if bound and page.get('_consumerProof'):
            raise ValueError('Use this browser’s admitted Work field reference.')
        fence = self.epochs.get(identity)
        mapping = page.get('_boundNodes', {}).get(group)
        selected = page.get('_boundNodes', {}).get(group, page['items'])
        row = next((row for row in selected if row['id'] == args.get('id')), None)
        field = args.get('field')
        reference = (row or {}).get('_eventFields', {}).get(field)
        detail = (row or {}).get(str(field) + 'Detail', {})
        if reference is None or args.get('digest') != detail.get('digest'):
            raise ValueError('This saved field is no longer available.')
        result = await self._worker().project({'operation': 'field', 'session': page['source'],
            'reference': reference, 'offset': int(args.get('offset', 0)),
            'limit': min(16384, max(1, int(args.get('limit', 16000))))}, key=owner['id'] + ':field:' + row['id'])
        if (self.closed or self._owner(owner['id']) is not owner or self.pages.get(page['group']) is not page
                or source_identity(owner) != page['source']
                or self.epochs.get(identity) != fence
                or not self._matches(page['source'], page.get('revisions'))
                or bound and (page.get('_boundNodes', {}).get(group) is not mapping
                    or not self._bound_current(page))):
            raise ValueError('This saved field expired.')
        if result.get('status') != 'complete':
            raise ValueError('This saved field changed. Reopen its Work window.')
        value = result['field']
        return {'value': value['text'], 'nextOffset': value['nextOffset'], 'total': value['total']}

    async def _read_consumer_field(self, identity, args):
        client = self.live_pages._client()
        entries = [(key, entry) for key, entry in self._cold_reads.items()
                   if key[:2] == (client, identity) and entry['key'] == args.get('windowKey')]
        if len(entries) != 1:
            raise ValueError('This Work field window expired.')
        key, entry = entries[0]
        page = self._page(identity, key[2])
        if (id(page) != entry['page'] or args.get('id') not in entry['ids']
                or args.get('proofId') != entry['proof']):
            raise ValueError('This Work field window expired.')
        # The group membership and admitted page are separate from root proof
        # authority. Both must still exist after the canonical read suspends.
        row = next((r for r in page['_boundNodes'][key[2]] if r['id'] == args.get('id')), None)
        if row is None:
            raise ValueError('This Work field membership expired.')
        accepted = deepcopy(row)
        result = await self.read_proven_field(identity, entry['proof'], row['id'], args)
        if (self.live_pages._client() != client or self._cold_reads.get(key) is not entry
                or self._page(identity, key[2]) is not page or args['id'] not in entry['ids']
                or row != accepted or not any(r is row for r in page['_boundNodes'][key[2]])):
            raise ValueError('This Work field window expired.')
        return result

    async def close(self):
        self.closed = True
        tasks = (*tuple(self.pending.values()), *tuple(self._proof_jobs.values()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.projector is not None:
            await self.projector.close()
        if self._proof_draining:
            await asyncio.gather(*tuple(self._proof_draining), return_exceptions=True)
        self.live_pages.groups.clear()
        self.pages.clear(); self.current.clear(); self.status.clear()
        self.epochs.clear(); self.retry_at.clear()
        self._root_proofs.clear(); self._proof_jobs.clear(); self._proof_draining.clear()
        self._cold_reads.clear()
