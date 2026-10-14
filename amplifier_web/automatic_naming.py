"""Idle, bounded naming of discovered chats; independent of read-only previews."""
import asyncio
from collections.abc import MutableMapping
import json
import time

from .naming import directory_for, read
from .naming_backfill import BUSY, NamingBackfill, default_provider


class AttemptReceipts(MutableMapping):
    """Point access to durable attempt receipts, with no startup history cache."""
    def __init__(self, db):
        self.db = db

    def __getitem__(self, identity):
        row = self.db.execute('SELECT source FROM automatic_naming_attempts WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        return row[0]

    def __setitem__(self, identity, source):
        self.db.execute('INSERT INTO automatic_naming_attempts VALUES (?,?) '
            'ON CONFLICT(id) DO UPDATE SET source=excluded.source', (identity, source))

    def __delitem__(self, identity):
        if not self.db.execute('DELETE FROM automatic_naming_attempts WHERE id=?', (identity,)).rowcount:
            raise KeyError(identity)

    def __iter__(self):
        return (row[0] for row in self.db.execute('SELECT id FROM automatic_naming_attempts'))

    def __len__(self):
        return self.db.execute('SELECT COUNT(*) FROM automatic_naming_attempts').fetchone()[0]


def idle(service):
    from .updates import work_paused
    state = service.state
    return (not service.closed and bool(service.queue_clients)
            and not state.get('sharedHistory', {}).get('loading')
            and not work_paused(state)
            and state.get('voice', {}).get('status') not in {'starting', 'connecting', 'connected', 'reconnecting'}
            and not any(row.get('status') in BUSY or row.get('configurationBusy') for row in state['sessions']))


class AutomaticNaming:
    def __init__(self, service, *, provider_ready=None, clock=time.monotonic):
        self.service = service
        self.provider_ready = provider_ready or self._provider_ready
        self.scanning = False
        self.clock = clock
        self.next_scan = 0
        self.cursor = 0
        # An interrupted or failed naming request is never retried at startup.
        # A changed transcript or naming policy makes a new attempt eligible.
        service.db.execute('CREATE TABLE IF NOT EXISTS automatic_naming_attempts (id TEXT PRIMARY KEY, source TEXT NOT NULL)')
        self.attempts = AttemptReceipts(service.db)
        service.db.commit()

    async def _provider_ready(self, session):
        from .setup import SetupManager
        try:
            await asyncio.to_thread(default_provider, SetupManager(self.service.data_dir), session['workspace'])
            return True
        except (OSError, ValueError):
            return False  # Account setup can make this eligible on a later pass.

    async def schedule(self):
        if self.scanning or self.clock() < self.next_scan or not idle(self.service):
            return
        self.scanning = True
        try:
            backfill = getattr(self.service, 'naming_backfill', None)
            if backfill is None:
                backfill = self.service.naming_backfill = NamingBackfill(self.service)
            if backfill.running:
                return
            # The complete directory owns discovery. Decode only this pass's
            # window; unrelated dormant owners never enter the working set.
            directory = self.service.directory
            where = ("history_managed AND native_project IS NOT NULL AND session_kind='root' "
                "AND COALESCE(auto_name,1) AND available "
                "AND COALESCE(name_source,'') NOT IN ('manual','generated') "
                "AND COALESCE(title_source,'') NOT IN ('manual','generated')")
            sql, params = directory.effective()
            total = directory.db.execute(sql + ' SELECT COUNT(*) FROM directory_rows WHERE ' + where, params).fetchone()[0]
            self.next_scan = self.clock() + 15
            chosen = {}
            ready = {}
            # Catalog recovery can tick every 100 ms. Bound both the cadence
            # and metadata reads, rotating through large unnamed histories.
            start = self.cursor % max(1, total)
            size = min(32, total)
            order = 'COALESCE(recent,created,0) DESC,ordinal,id'
            window = directory.select(where, limit=size, offset=start, order=order)
            if len(window) < size:
                window += directory.select(where, limit=size-len(window), order=order)
            for session in window:
                self.cursor += 1
                if session.get('nativeAvailable') is False:
                    continue
                source = json.dumps([session.get('nativeRevision'), read(directory_for(self.service.data_dir, session)).get('name_policy_revision', 0)], sort_keys=True)
                if self.attempts.get(session['id']) == source or backfill.eligibility(session):
                    continue
                workspace = session.get('workspace')
                if workspace not in ready:
                    ready[workspace] = await self.provider_ready(session)
                if not ready[workspace]:
                    continue
                chosen[session['id']] = source
                if len(chosen) >= 2:
                    break
            async with self.service.lock:
                if not chosen or not idle(self.service) or backfill.running:
                    return
                _, identities = backfill.start({'ids': list(chosen), 'limit': 2})
                for identity in identities:
                    self.attempts[identity] = chosen[identity]
                self.service._publish_changes(sessions=set(identities), globals={'namingBackfill'})
                if identities:
                    self.service._task(self._run(backfill, identities, chosen))
        finally:
            self.scanning = False

    async def _run(self, backfill, identities, sources):
        retry = await backfill.run(identities, automatic=True)
        # Paused/busy work never consumed a provider attempt. The returned IDs
        # belong to this run, even if a manual backfill starts meanwhile.
        async with self.service.lock:
            for identity in retry:
                if self.attempts.get(identity) == sources[identity]:
                    self.attempts.pop(identity, None)
            self.service.db.commit()
