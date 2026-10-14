"""Bounded test-only admission spans; never changes lock or save authority."""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
import time
import uuid

from aiohttp import web


class AdmissionDiagnostics:
    def __init__(self, service):
        self.rows = deque(maxlen=32)
        self.current = ContextVar('fixture_admission', default=None)
        acquire, save = service.lock.acquire, service._save_changes

        async def measured_acquire():
            with self.span('lockWait'):
                return await acquire()

        def measured_save(*args, **kwargs):
            with self.span('save', cpu=True):
                return save(*args, **kwargs)

        service.lock.acquire, service._save_changes = measured_acquire, measured_save

    @contextmanager
    def span(self, name, *, cpu=False):
        row = self.current.get()
        if row is None:
            yield
            return
        started = time.perf_counter()
        thread = time.thread_time() if cpu else None
        try:
            yield
        finally:
            row[name + 'Ms'] = row.get(name + 'Ms', 0) + (time.perf_counter() - started) * 1000
            row[name + 'Calls'] = row.get(name + 'Calls', 0) + 1
            if cpu:
                row[name + 'ThreadCpuMs'] = row.get(name + 'ThreadCpuMs', 0) + (time.thread_time() - thread) * 1000

    @web.middleware
    async def middleware(self, request, handler):
        if request.path != '/fixture/admit':
            return await handler(request)
        row = {'requestId': uuid.uuid4().hex, 'startedAtMs': time.time() * 1000}
        token = self.current.set(row)
        started = time.perf_counter()
        try:
            response = await handler(request)
            response.headers['X-Fixture-Request-Id'] = row['requestId']
            response.headers['Server-Timing'] = 'fixture;dur=%.2f' % ((time.perf_counter() - started) * 1000)
            row['status'] = response.status
            return response
        except BaseException as error:
            row['errorType'] = type(error).__name__
            raise
        finally:
            row['elapsedMs'] = (time.perf_counter() - started) * 1000
            row['endedAtMs'] = time.time() * 1000
            self.rows.append(row)
            self.current.reset(token)

    def call(self, identity):
        # Fixture IDs only; request bodies and arbitrary state are not retained.
        row = self.current.get()
        if row is not None:
            row['callId'] = identity[:80] if isinstance(identity, str) else None

    def snapshot(self):
        return [dict(row) for row in self.rows]
