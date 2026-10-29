"""Bounded, ordered terminal receipts and observations; never executing commands."""
import asyncio

MAX_EVENTS = 16
MAX_BYTES = 64 * 1024
TERMINAL_PHASES = frozenset({'completed', 'error', 'cancelled', 'interrupted', 'stopped'})


def terminal(kind, payload):
    return (kind == 'execution.event' and isinstance(payload, dict)
            and isinstance(payload.get('id'), str) and bool(payload['id'])
            and isinstance(payload.get('kind'), str) and payload['kind'] in {'llm', 'tool', 'worker'}
            and isinstance(payload.get('phase'), str) and payload['phase'] in TERMINAL_PHASES)


ACTIVITY_PHASES = frozenset({'model', 'processing', 'waiting-workers', 'tools', 'retrying', 'compacting'})
ACTIVITY_FIELDS = frozenset({'sessionId', 'status', 'activityOnly', 'phase', 'detail',
                             'activeWorkers', 'retryAttempt', 'retryMax'})
BRIDGE_FIELDS = frozenset({'sessionId', 'type', 'bridgeId', 'operation', 'generation_id',
                           'timeoutSeconds', 'runtimeSessionId', 'action', 'phase',
                           'elapsedSeconds', 'errorType'})


def observation(kind, payload):
    """Only normalized, non-authoritative root observations may join a receipt."""
    if not isinstance(payload, dict) or not isinstance(payload.get('sessionId'), str):
        return False
    if kind == 'runtime.timing':
        return set(payload) == {'sessionId', 'timing'}
    if kind == 'runtime.bridge':
        return set(payload) <= BRIDGE_FIELDS and payload.get('type') == 'runtime.bridge'
    return (kind == 'runtime.status' and set(payload) <= ACTIVITY_FIELDS
            and payload.get('activityOnly') is True and payload.get('status') == 'working'
            and isinstance(payload.get('phase'), str) and payload['phase'] in ACTIVITY_PHASES
            and isinstance(payload.get('detail'), str))


def receipt(kind, payload):
    return terminal(kind, payload) or observation(kind, payload)


class EventSink:
    """Explicit opt-in. Wrappers without this capability keep single delivery."""
    def __init__(self, single, batch):
        self.single = single
        self.batch = batch

    async def __call__(self, kind, payload):
        return await self.single(kind, payload)


async def finish_owned(awaitable):
    """Finish consumed receipts before propagating cancellation; retain ownership."""
    task = asyncio.create_task(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class BufferedLines:
    """One owned read ahead. A partial next line never delays a terminal commit."""
    def __init__(self, stream):
        self.stream = stream
        self.pending = None
        self.saved = None
        self.failure = None

    async def next(self):
        if self.failure is not None:
            error, self.failure = self.failure, None
            raise error
        if self.saved is not None:
            value, self.saved = self.saved, None
            return value
        if self.pending is None:
            return await self.stream.readline()
        pending = self.pending
        value = await pending
        self.pending = None
        return value

    async def collect(self, first, event, accept):
        events, size = [event], len(first)
        while len(events) < MAX_EVENTS and size < MAX_BYTES:
            if self.pending is None:
                self.pending = asyncio.create_task(self.stream.readline())
            # Only consume a read already ready after giving other owners a turn.
            await asyncio.sleep(0)
            if not self.pending.done():
                break
            if self.pending.cancelled() or self.pending.exception() is not None:
                # The read error is the next barrier, after durable delivery of
                # the valid prefix. next() will raise the original exception.
                break
            line = self.pending.result()
            self.pending = None
            try:
                candidate = accept(line) if line and size + len(line) <= MAX_BYTES else None
            except Exception as exc:
                self.failure = exc
                break
            if candidate is None:
                self.saved = line
                break
            events.append(candidate)
            size += len(line)
        return events

    async def close(self):
        if self.pending is not None:
            self.pending.cancel()
            await asyncio.gather(self.pending, return_exceptions=True)
            self.pending = None
