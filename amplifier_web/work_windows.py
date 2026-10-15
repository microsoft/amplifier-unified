"""Requested event windows, separate from authoritative execution/accounting.

Only small source identity descriptors cross the process boundary. The host
retains at most eight bounded pages; it never merges these observations into an
execution tree, grants admission from them, or starts a runtime for a read.
"""
import asyncio
from collections import OrderedDict
from copy import deepcopy
import hashlib
import secrets
import sys
import time

IDENTITY_FIELDS = ('id', 'workspace', 'nativeProject', 'nativeIdentity', 'runtimeSessionId')
MAX_PAGES = 8
MAX_JOBS = 4


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
        for identity in set(self.current) | set(self.pending):
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
        task = self.pending.get(identity)
        if task is not None:
            return task
        if len(self.pending) >= MAX_JOBS or self.retry_at.get(identity, 0) > time.monotonic():
            return None
        async def refresh():
            try:
                return await self.read(identity, _initial=True)
            except asyncio.CancelledError:
                raise
            except Exception:
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
        for identity, task in tuple(self.pending.items()):
            if identity not in identities:
                task.cancel()
        for mapping in (self.epochs, self.retry_at):
            for identity in tuple(mapping):
                if identity not in identities and identity not in self.current and identity not in self.pending:
                    mapping.pop(identity, None)

    async def read(self, identity, before=None, *, _initial=False):
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
            if await asyncio.to_thread(self._matches, source, self.pages.get(prior, {}).get('revisions')):
                if (not self.closed and self._owner(identity) is owner
                        and source_identity(owner) == source and prior in self.pages):
                    if self.epochs.get(identity) != fence:
                        raise ValueError('Work changed while it was read.')
                    current = self.status.get(identity)
                    if current and not current.get('current'):
                        self._state(identity, {**current, 'status': 'ready', 'current': True})
                    return self._groups(self.pages[prior])
                raise ValueError('The requested conversation changed. Open it again.')
        if _initial:
            self._state(identity, {'status': 'loading', 'current': False})
        result = await self._worker().project({'operation': 'window', 'session': source,
            'windowKey': window_key, 'before': before, 'cursorKey': self.key.hex()},
            key=identity + ':' + str(before or 'latest'))
        if (self.closed or getattr(self.service, 'closed', False)
                or self._owner(identity) is not owner or source_identity(owner) != source
                or self.epochs.get(identity) != fence):
            raise ValueError('The requested conversation changed. Open it again.')
        if result.get('status') != 'complete':
            if _initial:
                self.retry_at[identity] = time.monotonic() + 5
                self._state(identity, {'status': 'retrying', 'current': False,
                                      'reason': result.get('reason', 'worker_failed')})
            raise ValueError('Saved work is unavailable. Try again shortly.')
        page = result['page']
        if not await asyncio.to_thread(self._matches, source, page.get('revisions')):
            if _initial:
                self.retry_at[identity] = time.monotonic() + 1
                self._state(identity, {'status': 'retrying', 'current': False, 'reason': 'source_changed'})
            raise ValueError('Saved work changed while it was read. Refresh this window.')
        if (self.closed or getattr(self.service, 'closed', False)
                or self._owner(identity) is not owner or source_identity(owner) != source
                or self.epochs.get(identity) != fence):
            raise ValueError('The requested conversation changed. Open it again.')
        page = {**page, 'sessionId': identity, 'source': source}
        group = 'event-window:' + page['generation']
        page['group'] = group
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
            self._state(identity, {'status': 'ready', 'current': True, 'receipt': {
                'hostInstanceId': getattr(self.service, 'instance_id', None), 'sessionId': identity,
                'generation': self.generation, 'scope': 'recent-events', 'windowKey': window_key,
                'historyComplete': False, 'sourceCount': coverage.get('sourceCount', 0),
                'itemCount': len(page['items']), 'scannedBytes': coverage.get('scannedBytes', 0),
                'scannedEvents': coverage.get('scannedEvents', 0)}})
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

    @staticmethod
    def _groups(page):
        identity = page['group']
        coverage = page.get('coverage', {})
        group = {'id': identity, 'turnId': identity, 'anchorMessageId': None,
            'observationWindow': True, 'phase': 'recorded', 'label': 'Recent saved work',
            'nodeCounts': None, 'aggregateUsage': None, 'detailRevision': page['generation'],
            'coverage': coverage}
        return {'items': [group] if page['items'] or page.get('unavailableRanges') else [],
            'turns': [{'id': identity, 'phase': 'recorded', 'observationWindow': True}],
            'mode': 'cursor', 'offset': None, 'total': None, 'before': page.get('before'),
            'hasEarlier': page.get('before') is not None, 'revision': page['generation'],
            'windowKey': page['windowKey'], 'coverage': coverage,
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
        groups = self._groups(page) if page else {'items': [], 'turns': [], 'mode': 'cursor',
            'offset': None, 'total': None, 'before': None, 'hasEarlier': False,
            'coverage': {'kind': 'event-window', 'historyComplete': False}}
        from .browser_detail import page as detail_page
        live_source = self.live_view(session)
        live = detail_page(live_source, 'groups')
        from .cold_display import raw
        if len(raw(session).get('execution', {}).get('nodes', [])) > 100:
            # The tail is display evidence, not a complete current-turn count.
            live['items'] = [{**row, 'partialWindow': True, 'nodeCounts': None,
                              'aggregateUsage': None} for row in live['items']]
            live['turns'] = [{key: value for key, value in row.items()
                              if key not in {'nodeCounts', 'aggregateUsage'}}
                             for row in live['turns']]
        from .image_generation import project as image_work
        return {'execution': {**{key: value for key, value in live_source['execution'].items()
                                  if key not in {'nodes', 'turns'}},
                             'imageGeneration': image_work(live_source, live_source['execution']),
                             'nodes': [], 'turns': [*live['turns'], *groups['turns']],
                             'segments': [*live['items'], *groups['items']],
                             'detailsDeferred': True},
                'executionWindow': {'part': 'groups', **{key: value for key, value in groups.items() if key not in {'items', 'turns'}}},
                'workProjection': self.status.get(session['id'], {'status': 'loading', 'current': False})
                    if not rebound else {'status': 'loading', 'current': False}}

    def _page(self, identity, group):
        owner = self._owner(identity)  # Current canonical authority, not cache presence.
        page = self.pages.get(group)
        if page is None or page['sessionId'] != identity or source_identity(owner) != page['source']:
            raise ValueError('This saved work window expired. Open the recent window again.')
        self.pages.move_to_end(group)
        return page

    def nodes(self, identity, group, *, revision=None, known=None):
        page = self._page(identity, group)
        if revision is not None and revision != page['generation']:
            raise ValueError('This saved work window changed. Open it again.')
        items = []
        for original in page['items']:
            row = {key: deepcopy(value) for key, value in original.items() if key != '_eventFields'}
            row.update(turnId=group, detailVersion=page['generation'], observationWindow=True)
            for field in ('input', 'output', 'error', 'request', 'response'):
                if field + 'Detail' in row:
                    row[field + 'Detail'].update(sessionId=identity, windowKey=group)
            if known is None or known.get(row['id']) != row['detailVersion']:
                items.append(row)
        return {'items': items, 'order': [row['id'] for row in page['items']],
                'offset': 0, 'total': len(page['items']), 'before': None,
                'group': group, 'revision': page['generation'], 'coverage': page['coverage']}

    async def read_text(self, identity, args):
        owner = self._owner(identity)
        page = self._page(owner['id'], args.get('windowKey'))
        row = next((row for row in page['items'] if row['id'] == args.get('id')), None)
        field = args.get('field')
        reference = (row or {}).get('_eventFields', {}).get(field)
        detail = (row or {}).get(str(field) + 'Detail', {})
        if reference is None or args.get('digest') != detail.get('digest'):
            raise ValueError('This saved field is no longer available.')
        result = await self._worker().project({'operation': 'field', 'session': page['source'],
            'reference': reference, 'offset': int(args.get('offset', 0)),
            'limit': min(16384, max(1, int(args.get('limit', 16000))))}, key=owner['id'] + ':field:' + row['id'])
        if (self.closed or self._owner(owner['id']) is not owner or self.pages.get(page['group']) is not page
                or source_identity(owner) != page['source']):
            raise ValueError('This saved field expired.')
        if result.get('status') != 'complete':
            raise ValueError('This saved field changed. Reopen its Work window.')
        value = result['field']
        return {'value': value['text'], 'nextOffset': value['nextOffset'], 'total': value['total']}

    async def close(self):
        self.closed = True
        tasks = tuple(self.pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.projector is not None:
            await self.projector.close()
        self.pages.clear(); self.current.clear(); self.status.clear()
        self.epochs.clear(); self.retry_at.clear()
