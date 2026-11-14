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
    from .installation_repair import held
    state = service.state
    return (not service.closed and bool(service.queue_clients)
            and not state.get('sharedHistory', {}).get('loading')
            and not held(service) and not work_paused(state)
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
            self.next_scan = self.clock() + 15
            chosen = {}
            ready = {}
            # A 32-row result is bounded, but counting/sorting the catalog is
            # not. Run that scan in a disposable read-only child, so an idle
            # naming tick cannot stall a new foreground request or stream.
            from .automatic_naming_query import read_candidates
            window = await read_candidates(self.service, self.cursor)
            if not idle(self.service):
                return
            for session in window:
                if not idle(self.service):
                    return
                self.cursor += 1
                if session.get('nativeAvailable') is False:
                    continue
                source = json.dumps([session.get('nativeRevision'), read(directory_for(self.service.data_dir, session)).get('name_policy_revision', 0)], sort_keys=True)
                if self.attempts.get(session['id']) == source or backfill.eligibility(session):
                    continue
                workspace = session.get('workspace')
                if workspace not in ready:
                    # This provider preparation is not a RuntimeManager request.
                    # Register it synchronously before its first await so repair
                    # cannot acquire the gate while the account check is running.
                    self.service._automatic_naming_provider_checks = getattr(
                        self.service, '_automatic_naming_provider_checks', 0) + 1
                    check = asyncio.create_task(self.provider_ready(session))
                    def settled(done):
                        self.service._automatic_naming_provider_checks -= 1
                        if not done.cancelled():
                            done.exception()  # Observe an error if the scheduler was cancelled.
                    check.add_done_callback(settled)
                    # Cancelling a scan cannot cancel a to_thread account check
                    # that is already running, or make that owner disappear.
                    ready[workspace] = await asyncio.shield(check)
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
