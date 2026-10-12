"""Ephemeral, bounded storage measurements. Never used as deletion evidence."""
import asyncio
import copy
from dataclasses import dataclass, field
import threading
import time
import uuid

from .storage_catalog import CATEGORIES, inspect, summary


@dataclass
class Scan:
    args: dict
    result: dict
    observation: dict = field(default_factory=dict)
    stopped: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    touched: float = field(default_factory=time.monotonic)
    task: object = None
    paused: bool = False
    inputs: tuple = ()

    def publish(self, result):
        with self.lock:
            self.result = result

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.result)


class StorageScans:
    """One filesystem walker per host service; no periodic scan or durable cache."""
    def __init__(self):
        self.entries = {}
        self.worker = asyncio.Semaphore(1)

    async def read(self, home, workspaces, args, sessions, installation):
        identity = args.get('scanId')
        selection = {k: v for k, v in args.items() if k not in {'scanId', 'fromScanId', 'progressive', 'cancel'}}
        if identity:
            scan = self.entries.get(identity)
            if not scan or scan.args != selection:
                raise ValueError('Storage measurement expired. Refresh storage to measure again.')
            scan.touched = time.monotonic()
            if args.get('cancel'):
                scan.stopped.set()
                return {'scanId': identity, 'scanning': False, 'cancelled': True}
            if scan.paused and scan.task.done() and not scan.stopped.is_set():
                scan.paused = False
                scan.task = asyncio.create_task(self.run(scan, *scan.inputs))
        else:
            # Only bounded, transient observations survive a visit. An explicit
            # refresh creates a new observation; it never restarts deletion.
            parent = self.entries.get(args.get('fromScanId'))
            for key, old in list(self.entries.items()):
                if time.monotonic() - old.touched > 30 or len(self.entries) >= 8:
                    old.stopped.set()
                    del self.entries[key]
            initial = (summary([], {c[0] for c in CATEGORIES}) if not selection.get('category')
                       else {'rows': [], 'bytes': None, 'incomplete': True})
            scan = Scan(selection, initial, observation=parent.observation if parent else {})
            scan.inputs = (home, workspaces, sessions, installation)
            identity = uuid.uuid4().hex
            self.entries[identity] = scan
            scan.task = asyncio.create_task(self.run(scan, home, workspaces, sessions, installation))
        result = scan.snapshot()
        return {**result, 'scanId': identity, 'scanning': not scan.task.done() and not scan.stopped.is_set()}

    async def run(self, scan, home, workspaces, sessions, installation):
        try:
            while not scan.stopped.is_set():
                async with self.worker:
                    if scan.stopped.is_set():
                        return
                    if time.monotonic() - scan.touched > 30:
                        scan.paused = True
                        return
                    result = await asyncio.to_thread(inspect, home, workspaces, scan.args, sessions, installation,
                                                    stopped=scan.stopped, progress=scan.publish, observation=scan.observation)
                    scan.publish({**result, 'measuredAt': time.time()})
                if not result.get('measuring'):
                    return
                # Yield between slices: other views share one filesystem worker.
                await asyncio.sleep(.05)
        except Exception as error:
            scan.publish({**scan.snapshot(), 'measurementError': str(error)})

    async def close(self):
        for scan in self.entries.values():
            scan.stopped.set()
        # Let the bounded walkers observe cancellation before releasing their
        # slot. Cancelling to_thread alone would leave hidden scans running.
        tasks = [scan.task for scan in self.entries.values() if scan.task]
        if tasks:
            await asyncio.wait(tasks, timeout=3)
        self.entries.clear()
