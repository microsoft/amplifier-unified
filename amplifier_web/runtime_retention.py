"""Host policy for retaining settled workers; never an execution concurrency cap."""
from __future__ import annotations

import asyncio
import math
import time

DEFAULT_RETENTION = {
    "max_warm_workers": 32,
    "idle_timeout_hours": 12,
    "max_background_starts": 2,
}


def validate_retention(value, *, stored=False):
    # Older server.yaml files and initialization callers may contain this retired
    # preference. It no longer authorizes runtime work when viewing a chat.
    if stored and isinstance(value, dict) and "prewarm_on_select" in value:
        if type(value["prewarm_on_select"]) is not bool:
            raise ValueError("runtime.prewarm_on_select must be true or false.")
        value = {key: item for key, item in value.items() if key != "prewarm_on_select"}
    if not isinstance(value, dict) or set(value) - set(DEFAULT_RETENTION):
        raise ValueError("runtime must contain only the documented worker retention settings.")
    result = {**DEFAULT_RETENTION, **value}
    for key, minimum in (("max_warm_workers", 0), ("max_background_starts", 1)):
        if type(result[key]) is not int or result[key] < minimum:
            raise ValueError(f"runtime.{key} must be an integer at least {minimum}.")
    hours = result["idle_timeout_hours"]
    if type(hours) not in (int, float) or not math.isfinite(hours) or hours < 0:
        raise ValueError("runtime.idle_timeout_hours must be a finite nonnegative number.")
    return result


class WorkerRetention:
    def __init__(self, manager, settings=None, *, clock=time.monotonic):
        self.manager = manager
        self.settings = validate_retention(settings or {}, stored=True)
        self.clock = clock
        self.changed = asyncio.Event()
        self.task = None
        self.background = asyncio.Semaphore(self.settings["max_background_starts"])
        self.sweep_lock = asyncio.Lock()
        self.reply_timeout = 30
        self.retirements = set()
        self.protected = lambda sid: False

    def wake(self):
        if self.manager._closed:
            return
        self.changed.set()
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    @staticmethod
    def eligible(row):
        return (row.get("parked") and row["process"].returncode is None
                and not row.get("closing") and not row.get("yielding")
                and not row.get("inflight") and not row.get("bridge_tasks"))

    async def sweep(self):
        async with self.sweep_lock:
            candidates = sorted(((sid, row) for sid, row in self.manager.workers.items()
                                 if self.eligible(row)), key=lambda pair: pair[1]["parked_at"])
            excess = max(0, len(candidates) - self.settings["max_warm_workers"])
            ttl = self.settings["idle_timeout_hours"] * 3600
            now = self.clock()
            for index, (sid, row) in enumerate(candidates):
                if self.protected(sid):
                    continue
                if not row.get("update_pending") and index >= excess and now - row["parked_at"] < ttl:
                    continue
                # Serialize the final idle check with command admission/start.
                async with self.manager._locks.setdefault(sid, asyncio.Lock()):
                    if self.manager.workers.get(sid) is not row or not self.eligible(row) or self.protected(sid):
                        continue
                    task = self.schedule(sid, row)
                try:
                    await asyncio.wait_for(asyncio.shield(task), self.reply_timeout)
                except TimeoutError:
                    # Keep the intent and reply reader alive. Admission waits
                    # for reconciliation; a timeout is not a refusal to retire.
                    continue

    def schedule(self, sid, row):
        # Caller holds the per-session admission lock.
        row['closing'] = True
        # Classify the boundary before sending: the worker can publish its
        # close event immediately after the retirement acknowledgement.
        row.setdefault('stop_reason', 'retention')
        task = asyncio.create_task(self.retire(sid, row))
        row['retirement_task'] = task
        self.retirements.add(task)
        task.add_done_callback(self.retirements.discard)
        return task

    async def retire(self, sid, row):
        try:
            # Retirement has no protocol-reply deadline. The sweep and command
            # callers have bounded waits without cancelling this reconciliation.
            result = await self.manager._request_unlocked(sid, 'retire')
        except Exception:
            async with self.manager._locks.setdefault(sid, asyncio.Lock()):
                if not self.manager._closed and self.manager.workers.get(sid) is row and not row.get('stop_task'):
                    row['closing'] = False
                    row['parked'] = False
                    if row.get('stop_reason') == 'retention':
                        row.pop('stop_reason')
                    if row['process'].returncode is not None:
                        await row['emit']('runtime.error', {'sessionId': sid,
                            'error': 'The worker disconnected before idle retirement was confirmed. Work was not replayed.'})
            return
        async with self.manager._locks.setdefault(sid, asyncio.Lock()):
            if self.manager._closed or self.manager.workers.get(sid) is not row or row.get('stop_task'):
                return
            if not result.get('retired'):
                row['closing'] = False
                row['parked'] = False
                if row.get('stop_reason') == 'retention':
                    row.pop('stop_reason')
                return
            session, emit = row['start_session'], row['emit']
            await self.manager.stop(sid, reason='retention')
            # Commands held behind even a late acknowledgement can restart.
            self.manager._retired[sid] = (session, emit)

    async def run(self):
        try:
            while not self.manager._closed:
                self.changed.clear()
                await self.sweep()
                try:
                    await asyncio.wait_for(self.changed.wait(), 60)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        tasks = list(self.retirements)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
