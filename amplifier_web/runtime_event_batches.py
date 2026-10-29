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

    async def next(self):
        if self.pending is not None:
            try:
                value = await self.pending
            finally:
                self.pending = None
            # None means the bounded batch filled without reading ahead.
            # Empty bytes still represent the retained EOF barrier.
            if value is not None:
                return value
        return await self.stream.readline()

    async def collect(self, first, event, accept):
        events, size = [event], len(first)
        if size >= MAX_BYTES:
            return events
        collecting = True

        async def ready_lines():
            nonlocal size
            while len(events) < MAX_EVENTS and size < MAX_BYTES:
                line = await self.stream.readline()
                # A genuinely pending read belongs to next(), not to the
                # already-returned prefix. Do not cancel or discard its bytes.
                if not collecting:
                    return line
                candidate = accept(line) if line and size + len(line) <= MAX_BYTES else None
                if candidate is None:
                    return line
                events.append(candidate)
                size += len(line)
            return None

        self.pending = asyncio.create_task(ready_lines())
        try:
            # Buffered readline calls finish synchronously. Let this bounded
            # task drain them in one turn, instead of paying a new background
            # publication/scheduler turn for each already-buffered receipt.
            await asyncio.sleep(0)
        finally:
            collecting = False
        # Read/accept failures stay on the owned task. next() raises them only
        # after this valid prefix has been delivered durably.
        return events

    async def close(self):
        if self.pending is not None:
            self.pending.cancel()
            await asyncio.gather(self.pending, return_exceptions=True)
            self.pending = None
