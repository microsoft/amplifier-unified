"""Bounded physical lease observation, independent of opened chats or pages."""
from __future__ import annotations

import asyncio
from contextlib import closing
import json
import sqlite3
import threading
import time
import uuid

from filelock import FileLock, Timeout

from . import task_environments as owner
from . import test_environment_recovery as recovery
from .updates import work_paused

PAGE_SIZE = 32
INTERVAL_SECONDS = 30


class ReservationMaintenance:
    def __init__(self, app):
        self.app = app
        self.cursor = 0
        self.next_due = 0
        self.busy = asyncio.Lock()

    async def tick(self):
        app = self.app
        if (app.closed or app.test_queue.closing or work_paused(app.state)
                or self.busy.locked() or app.runtime_lifecycle_lock.locked()
                or time.monotonic() < self.next_due):
            return
        async with self.busy, app.runtime_lifecycle():
            self.next_due = time.monotonic() + INTERVAL_SECONDS
            cancelled = threading.Event()

            def guard():
                if cancelled.is_set() or app.closed or app.test_queue.closing or work_paused(app.state):
                    raise ValueError('Capacity observation paused while the host is changing.')

            work = asyncio.create_task(asyncio.to_thread(self.observe_one, guard))
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                # Do not release runtime exclusion while an exact native read
                # is still in flight. Cancellation forbids its later settlement.
                cancelled.set()
                while not work.done():
                    try:
                        await asyncio.shield(work)
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        break
                if not work.cancelled():
                    work.exception()
                raise

    def observe_one(self, guard):
        app = self.app
        guard()
        with closing(owner.storage.connect(app.data_dir)) as db:
            # Only the canonical allocation ledger: no session hydration,
            # transcript scan, foreign installation or machine-wide discovery.
            rows = db.execute("SELECT rowid,id,root,status,body FROM allocations "
                              "WHERE status!='retired' AND rowid>? ORDER BY rowid LIMIT ?",
                              (self.cursor, PAGE_SIZE)).fetchall()
        if not rows:
            self.cursor = 0
            return
        for position, identity, sid, status, encoded in rows:
            self.cursor = position
            probed = False
            try:
                guard()
                row = json.loads(encoded)
                if (not isinstance(row, dict) or row.get('id') != identity
                        or row.get('sessionId') != sid or row.get('status') != status
                        or str(uuid.UUID(identity)) != identity
                        or row.get('kind') != owner.KIND or row.get('testMachine')
                        or not row.get('backendAdmitted') or row.get('pendingRequest')
                        or row.get('preparationPending') and not row.get('preparedDependencies')):
                    continue
                with FileLock(str(app.data_dir / 'workspace-resources' /
                                  ('environment-' + identity + '.lock')), timeout=0):
                    if owner.get(app.data_dir, sid, identity) != row or not owner.host_resources.held(row, blocking=False):
                        continue
                    active, _, _ = owner.local_configuration(app)
                    config = owner.resolve_configuration(app, row, active)
                    if (not config or config.get('remoteRunner')
                            or owner.config_key(config) != row['backendKey']
                            or owner.storage.observe(app.data_dir, row)['observation'] != 'present'):
                        continue
                    guard()
                    # No preliminary inventory is necessary. Only this exact
                    # native proof can release capacity; a failed read cannot.
                    probed = True
                    recovery.recover_reservation(app, row, config, {'status': 'unknown'}, guard=guard, blocking=False)
                    return
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, Timeout):
                # A stale configuration, corrupt row or busy owner retains its
                # lease and does not prevent later pages receiving inspection.
                if probed:
                    return
