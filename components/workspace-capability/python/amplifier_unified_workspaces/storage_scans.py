"""Transient, client-scoped storage observations; never deletion evidence."""
import asyncio
import copy
import threading
import time
import uuid
from dataclasses import dataclass, field


CONTROLS = {'progressive', 'scanId', 'fromScanId', 'cancel'}


@dataclass
class Scan:
    selection: dict
    client: str
    result: dict = field(default_factory=lambda: {'version': 1, 'rows': [], 'bytes': None, 'incomplete': True})
    stopped: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    touched: float = field(default_factory=time.monotonic)
    task: object = None
    roots: list = field(default_factory=list)
    cache: dict = field(default_factory=dict)
    seen: dict = field(default_factory=dict)
    measured_at: float = field(default_factory=time.time)
    created: float = field(default_factory=time.monotonic)

    def publish(self, result):
        with self.lock:
            self.result = copy.deepcopy(result)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.result)


class StorageScans:
    def __init__(self, owner):
        self.owner = owner
        self.entries = {}
        self.tasks = set()
        self.worker = asyncio.Semaphore(1)

    async def read(self, args, client, roots=None, omissions=()):
        from .storage_usage import inspect
        selection = {k: v for k, v in args.items() if k not in CONTROLS}
        identity = args.get('scanId')
        if identity:
            scan = self.entries.get(identity)
            if not scan or scan.client != client or scan.selection != selection or time.monotonic() - scan.touched > 30:
                raise ValueError('Storage measurement expired. Refresh storage to measure again.')
            scan.touched = time.monotonic()
            if args.get('cancel'):
                scan.stopped.set()
        else:
            if args.get('cancel') or roots is None:
                raise ValueError('Choose an existing storage measurement to cancel.')
            previous = self.entries.get(args.get('fromScanId'))
            if previous is not None and (previous.client != client or time.monotonic()-previous.created > 30 or previous.roots != roots):
                previous = None
            for key, old in list(self.entries.items()):
                if time.monotonic() - old.touched > 30 or old.client == client and not old.task.done():
                    old.stopped.set()
                    del self.entries[key]
            if len(self.entries) >= 8:
                key = min(self.entries, key=lambda k: self.entries[k].touched)
                self.entries.pop(key).stopped.set()
            if len(self.tasks) >= 8:
                raise ValueError('Storage measurements are finishing. Refresh storage shortly.')
            identity = str(uuid.uuid4())
            scan = self.entries[identity] = Scan(selection, client, roots=copy.deepcopy(roots))
            if previous is not None:
                # A single worker serializes all access to the observation cache.
                scan.cache, scan.seen = previous.cache, previous.seen
                scan.measured_at, scan.created = previous.measured_at, previous.created
            self.owner.intake.background += 1

            async def run():
                try:
                    async with self.worker:
                        if scan.stopped.is_set():
                            return
                        result = await asyncio.to_thread(inspect, roots, selection, omissions=omissions,
                            stopped=scan.stopped, progress=scan.publish, cache=scan.cache, seen=scan.seen)
                        scan.publish({**result, 'measuredAt': scan.measured_at})
                except Exception:
                    scan.publish({**scan.snapshot(), 'measurementError': 'Storage could not be measured. Refresh to try again.'})
                finally:
                    self.owner.intake.background -= 1
                    if self.owner.on_idle and not self.owner.closed:
                        try:
                            await self.owner.on_idle()
                        except Exception:
                            pass
            scan.task = asyncio.create_task(run())
            self.tasks.add(scan.task)
            scan.task.add_done_callback(self.tasks.discard)
        return {**scan.snapshot(), 'scanId': identity,
                'scanning': not scan.task.done() and not scan.stopped.is_set(),
                **({'cancelled': True} if scan.stopped.is_set() else {})}

    async def close(self):
        for scan in self.entries.values():
            scan.stopped.set()
        tasks = list(self.tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.entries.clear()
