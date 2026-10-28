"""Bounded process-local timings, never conversation content or persisted state.

Operation timings are inclusive and can overlap. Event-loop delay includes host
suspension and CPU scheduling; it is evidence of delay, not a cause attribution.
Stack sampling remains the separate opt-in profiling facility.
"""
import asyncio
from collections import deque
from contextlib import contextmanager, nullcontext
from functools import wraps
import inspect
import threading
import time


class PerformanceDiagnostics:
    def __init__(self):
        self.operations = {}
        self.counters = {}
        self.slow = deque(maxlen=60)
        self.lag = deque(maxlen=60)
        self.resource_rebuilds = deque(maxlen=60)
        self.projection_failures = deque(maxlen=16)
        self.shell_queries = deque(maxlen=60)
        self.storage_collection = None
        self.resource_rebuild_counts = {'total': 0, 'byReason': {}, 'byResource': {}}
        self.lock = threading.Lock()
        self.task = None
        self.started = time.time()

    @contextmanager
    def measure(self, name, *, synchronous=False, session_id=None):
        started, wall = time.monotonic(), time.time()
        cpu = time.thread_time() if synchronous else None
        try:
            yield
        finally:
            elapsed = (time.monotonic() - started) * 1000
            row = {'operation': name, 'at': wall, 'elapsedMs': round(elapsed, 2)}
            if cpu is not None:
                row['threadCpuMs'] = round((time.thread_time() - cpu) * 1000, 2)
            if isinstance(session_id, str):
                row['sessionId'] = session_id[:200]
            with self.lock:
                if name in self.operations or len(self.operations) < 64:
                    summary = self.operations.setdefault(name, {'count': 0, 'totalMs': 0, 'maxMs': 0})
                    summary['count'] += 1
                    summary['totalMs'] += elapsed
                    summary['maxMs'] = max(summary['maxMs'], elapsed)
                if elapsed >= 100:
                    self.slow.append(row)

    def increment(self, name, amount=1):
        with self.lock:
            if name in self.counters or len(self.counters) < 64:
                self.counters[name] = self.counters.get(name, 0) + amount

    def record_lag(self, delay):
        if delay >= 0.1:
            with self.lock:
                self.lag.append({'at': time.time(), 'delayMs': round(delay * 1000, 2)})

    def record_storage_collection(self, *, outcome, reason, elapsed_ms, catchups,
                                  indexedPruned=0, indexedFilesRemoved=0,
                                  indexedBytesRemoved=0, unindexedFilesRemoved=0):
        """Latest bounded collector facts; no artifact identities or paths.

        Byte counts describe successful indexed-file unlinks, not filesystem free
        space. Interrupted/cancelled passes can have partially removed files;
        only completed-pass counts establish measured reclamation here.
        """
        with self.lock:
            self.storage_collection = {'at': time.time(), 'outcome': outcome,
                'reason': reason, 'elapsedMs': round(elapsed_ms, 2), 'rootCatchups': catchups,
                'indexedPruned': indexedPruned, 'indexedFilesRemoved': indexedFilesRemoved,
                'indexedBytesRemoved': indexedBytesRemoved, 'unindexedFilesRemoved': unindexedFilesRemoved,
                'removalCountsComplete': outcome == 'completed'}

    def record_resource_rebuild(self, resource, package, reasons, globals_changed):
        """Call-site enums only; page keys and their values never enter diagnostics."""
        with self.lock:
            self.resource_rebuild_counts['total'] += 1
            for field, names in (('byReason', reasons), ('byResource', (resource,))):
                counts = self.resource_rebuild_counts[field]
                for name in names:
                    if name in counts or len(counts) < 64:
                        counts[name] = counts.get(name, 0) + 1
            self.resource_rebuilds.append({'at': time.time(), 'resource': resource,
                'package': package, 'reasons': tuple(reasons), 'changedGlobals': tuple(globals_changed)})

    def record_projection_failure(self, reason, failure, *, session_id=None):
        """Fixed worker facts and the host's requested chat ID, never sources."""
        from .history_projection_failure import sanitize_failure
        from .history_projection_process import REASONS
        value = sanitize_failure(failure)
        if value is not None and reason in REASONS:
            # Identity comes from the admitting host, not the worker's payload.
            # Multiple queued reads may finish after the viewer switches chats.
            if isinstance(session_id, str):
                value['sessionId'] = session_id[:200]
            with self.lock:
                self.projection_failures.append({'at': time.time(), 'reason': reason, **value})

    def record_shell_query(self, operation, section, queued_at, started_at, result, *, accepted):
        """Bounded scheduling facts; no page keys, query text or source paths."""
        if operation not in {'chats', 'workspace_counts', 'pinned_navigation'}:
            return
        section = section if section in {'recent', 'shortcuts', 'pinned', 'search'} else None
        stats = result.get('stats', {})
        row = {'at': time.time(), 'operation': operation, 'section': section,
               'queueWaitMs': round(max(0, started_at - queued_at) * 1000, 2),
               'readMs': round(max(0, time.monotonic() - started_at) * 1000, 2),
               'accepted': bool(accepted)}
        for key in ('vmSteps', 'decodedRows', 'elapsedMs', 'threadCpuMs'):
            value = stats.get(key)
            if type(value) in (int, float) and 0 <= value < float('inf'):
                row[key] = round(value, 2)
        from .history_projection_failure import sanitize_failure
        failure = sanitize_failure(result.get('failure'))
        if failure is not None:
            row['failure'] = failure
        with self.lock:
            self.shell_queries.append(row)

    async def _heartbeat(self):
        while True:
            started = time.monotonic()
            await asyncio.sleep(0.2)
            self.record_lag(max(0, time.monotonic() - started - 0.2))

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._heartbeat())

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    def snapshot(self):
        with self.lock:
            return {'schemaVersion': 1, 'startedAt': self.started,
                    'counters': dict(self.counters),
                    'operations': {name: {key: round(value, 2) if key != 'count' else value
                                         for key, value in values.items()}
                                   for name, values in self.operations.items()},
                    'slowOperations': [dict(row) for row in self.slow],
                    'eventLoopDelays': [dict(row) for row in self.lag],
                    'historyProjectionFailures': [dict(row) for row in self.projection_failures],
                    'shellQueries': [{**row, **({'failure': dict(row['failure'])}
                                      if 'failure' in row else {})} for row in self.shell_queries],
                    'storageCollection': dict(self.storage_collection) if self.storage_collection is not None else None,
                    'shellResourceRebuilds': {'total': self.resource_rebuild_counts['total'],
                        'byReason': dict(self.resource_rebuild_counts['byReason']),
                        'byResource': dict(self.resource_rebuild_counts['byResource']),
                        'recent': [{**row, 'reasons': list(row['reasons']),
                                    'changedGlobals': list(row['changedGlobals'])}
                                   for row in self.resource_rebuilds]},
                    'limitations': ['Inclusive operations overlap; do not add their durations.',
                                    'Loop delays include host suspension and scheduling; they do not identify the cause.',
                                    'No request bodies, prompts, credentials, paths or stack locals are collected.']}


def measured(name, *, session=False):
    """Labels are call-site constants. Optional identity is an existing chat ID."""
    def decorate(function):
        asynchronous = inspect.iscoroutinefunction(function)
        def measurement(owner, args):
            service = getattr(owner, 'service', owner)
            diagnostics = getattr(service, 'performance_diagnostics', None)
            identity = args[0] if session and args else None
            if isinstance(identity, dict):
                identity = identity.get('id')
            return (diagnostics.measure(name, synchronous=not asynchronous, session_id=identity)
                    if diagnostics is not None else nullcontext())
        if asynchronous:
            @wraps(function)
            async def wrapped(self, *args, **kwargs):
                with measurement(self, args):
                    return await function(self, *args, **kwargs)
        else:
            @wraps(function)
            def wrapped(self, *args, **kwargs):
                with measurement(self, args):
                    return function(self, *args, **kwargs)
        return wrapped
    return decorate


def measure(service, name, **kwargs):
    diagnostics = getattr(service, 'performance_diagnostics', None)
    return diagnostics.measure(name, **kwargs) if diagnostics is not None else nullcontext()


def count(service, name, amount=1):
    diagnostics = getattr(service, 'performance_diagnostics', None)
    if diagnostics is not None:
        diagnostics.increment(name, amount)


class TimedLock(asyncio.Lock):
    """Same cancellation/fairness behavior, with inclusive acquisition latency."""
    def __init__(self, service, name):
        super().__init__()
        self.service, self.name = service, name

    async def acquire(self):
        with measure(self.service, self.name):
            return await super().acquire()
