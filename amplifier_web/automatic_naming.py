"""Idle, bounded naming of discovered chats; independent of read-only previews."""
import asyncio
import json
import time

from .naming import directory_for, read
from .naming_backfill import BUSY, NamingBackfill, default_provider
from .session_navigation import is_top_level


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
        self.attempts = dict(service.db.execute('SELECT id,source FROM automatic_naming_attempts'))
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
            # Hot catalog fields only: this does not hydrate conversation bodies.
            candidates = sorted((row for row in self.service.state['sessions']
                if row.get('historyManaged') and row.get('nativeProject') and is_top_level(row)
                and row.get('autoName') is not False and row.get('nativeAvailable') is not False
                and row.get('workspaceAvailable') is not False
                and row.get('nativeNameSource') not in {'manual', 'generated'}
                and row.get('titleSource') not in {'manual', 'generated'}),
                key=lambda row: row.get('recentActivityAt') or row.get('createdAt') or 0, reverse=True)
            self.next_scan = self.clock() + 15
            chosen = {}
            ready = {}
            # Catalog recovery can tick every 100 ms. Bound both the cadence
            # and metadata reads, rotating through large unnamed histories.
            start = self.cursor % max(1, len(candidates))
            window = (candidates[start:] + candidates[:start])[:32]
            for session in window:
                self.cursor += 1
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
                    self.service.db.execute('INSERT INTO automatic_naming_attempts VALUES (?,?) ON CONFLICT(id) DO UPDATE SET source=excluded.source', (identity, chosen[identity]))
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
                    self.service.db.execute('DELETE FROM automatic_naming_attempts WHERE id=?', (identity,))
            self.service.db.commit()
