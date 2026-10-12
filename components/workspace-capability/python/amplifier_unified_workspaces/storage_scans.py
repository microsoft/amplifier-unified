"""Transient, client-scoped storage observations; never deletion evidence."""
import asyncio
import copy
import threading
import time
import uuid
from dataclasses import dataclass, field


IDLE_SECONDS = 30
EXPIRE_SECONDS = 300


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
    workspace_scope: object = None
    roots: list = field(default_factory=list)
    cache: dict = field(default_factory=dict)
    seen: dict = field(default_factory=dict)
    walks: dict = field(default_factory=dict)
    listings: dict = field(default_factory=dict)
    measurements: dict = field(default_factory=dict)
    omissions: tuple = ()
    paused: bool = False
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

    async def read(self, args, client, roots=None, omissions=(), *, workspace_scope=None):
        from .storage_usage import inspect
        selection = {k: v for k, v in args.items() if k not in CONTROLS}
        identity = args.get('scanId')
        if identity:
            scan = self.entries.get(identity)
            if scan and scan.client == client and scan.workspace_scope != workspace_scope:
                scan.stopped.set()
            if not scan or scan.workspace_scope != workspace_scope or scan.client != client or scan.selection != selection or time.monotonic() - scan.touched > EXPIRE_SECONDS:
                raise ValueError('Storage measurement expired. Refresh storage to measure again.')
            scan.touched = time.monotonic()
            if args.get('cancel'):
                scan.stopped.set()
            elif scan.paused and scan.task.done() and not scan.stopped.is_set():
                if getattr(self.owner.intake, "fence", None):
                    raise ValueError('Storage measurements are paused while maintenance finishes.')
                self.start(scan)
        else:
            if args.get('cancel') or roots is None:
                raise ValueError('Choose an existing storage measurement to cancel.')
            previous = self.entries.get(args.get('fromScanId'))
            if previous is not None and previous.client == client and previous.workspace_scope != workspace_scope:
                previous.stopped.set()
            if previous is not None and (previous.workspace_scope != workspace_scope or previous.client != client or time.monotonic()-previous.touched > EXPIRE_SECONDS or previous.roots != roots):
                previous = None
            for key, old in list(self.entries.items()):
                if time.monotonic() - old.touched > EXPIRE_SECONDS or old.client == client and not old.task.done():
                    old.stopped.set()
                    del self.entries[key]
            if len(self.entries) >= 8:
                key = min(self.entries, key=lambda k: self.entries[k].touched)
                self.entries.pop(key).stopped.set()
            if len(self.tasks) >= 8:
                raise ValueError('Storage measurements are finishing. Refresh storage shortly.')
            identity = str(uuid.uuid4())
            scan = self.entries[identity] = Scan(selection, client, workspace_scope=copy.deepcopy(workspace_scope), roots=copy.deepcopy(roots), omissions=tuple(omissions))
            if previous is not None:
                # A single worker serializes all access to the observation cache.
                scan.cache, scan.seen = previous.cache, previous.seen
                scan.walks, scan.listings, scan.measurements = previous.walks, previous.listings, previous.measurements
                scan.measured_at, scan.created = previous.measured_at, previous.created
            self.start(scan)
        return {**scan.snapshot(), 'scanId': identity,
                'scanning': not scan.task.done() and not scan.stopped.is_set(),
                **({'cancelled': True} if scan.stopped.is_set() else {})}

    def start(self, scan):
        scan.paused = False
        self.owner.intake.background += 1
        async def run():
            try:
                while not scan.stopped.is_set():
                    async with self.worker:
                        if scan.stopped.is_set(): return
                        if time.monotonic() - scan.touched > IDLE_SECONDS:
                            scan.paused = True
                            return
                        result = await asyncio.to_thread(inspect_scan, scan)
                        scan.publish({**result, 'measuredAt': scan.measured_at})
                    if not result.get('measuring'): return
                    await asyncio.sleep(.05)
            except Exception:
                scan.publish({**scan.snapshot(), 'measurementError': 'Storage could not be measured. Refresh to try again.'})
            finally:
                self.owner.intake.background -= 1
                if self.owner.on_idle and not self.owner.closed:
                    try: await self.owner.on_idle()
                    except Exception: pass
        scan.task = asyncio.create_task(run())
        self.tasks.add(scan.task)
        scan.task.add_done_callback(self.tasks.discard)

    async def close(self):
        for scan in self.entries.values():
            scan.stopped.set()
        tasks = list(self.tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for scan in self.entries.values():
            for walk in scan.walks.values(): walk.close()
            for listing in scan.listings.values():
                if listing.get('entries') is not None:
                    listing['entries'].close()
                    listing['entries'] = None
        self.entries.clear()


def inspect_scan(scan):
    from .storage_usage import inspect
    return inspect(scan.roots, scan.selection, omissions=scan.omissions,
                   stopped=scan.stopped, progress=scan.publish, cache=scan.cache,
                   seen=scan.seen, walks=scan.walks, listings=scan.listings,
                   measurements=scan.measurements)
