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


def starting_observation(data, roots):
    """Recognize root progress only; this never admits a command or lifecycle."""
    if not isinstance(data, dict) or data.get('op'):
        return False
    identities = {'sessionId', 'session_id', 'rootSessionId', 'root_session_id'}
    if any(data.get(key) is not None and data[key] not in roots for key in identities):
        return False
    kind = data.get('type')
    if kind == 'runtime.activity':
        return (set(data) <= identities | {'type', 'phase', 'detail', 'activeWorkers', 'retryAttempt', 'retryMax'}
                and isinstance(data.get('phase'), str) and data['phase'] in ACTIVITY_PHASES
                and isinstance(data.get('detail'), str))
    if kind == 'tool.pre':
        return (set(data) <= identities | {'type', 'tool', 'call_id'}
                and isinstance(data.get('tool'), str) and isinstance(data.get('call_id'), str))
    if kind != 'execution.event' or set(data) != {'type', 'event'}:
        return False
    event = data.get('event')
    fields = {'id', 'parentId', 'turnId', 'admissionInputId', 'sessionId', 'rootSessionId',
              'kind', 'phase', 'toolCallId', 'label', 'tool', 'startedAt', 'endedAt',
              'revision', 'liveObservation', 'toolObservationId', 'inputSummary',
              'inputPreview', 'actionPath', 'outcomeSummary'}
    return (isinstance(event, dict) and set(event) <= fields
            and isinstance(event.get('sessionId'), str) and bool(event['sessionId'])
            and isinstance(event.get('rootSessionId'), str) and bool(event['rootSessionId'])
            and event.get('sessionId') in roots and event.get('rootSessionId') in roots
            and isinstance(event.get('id'), str) and bool(event['id'])
            and event.get('kind') == 'tool' and event.get('phase') == 'running')


class ObservationTurns:
    """Bound fairness turns without reading ahead or changing event reducers."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.count = self.size = 0

    def yield_before(self, size, eligible):
        if not eligible or size > MAX_BYTES:
            self.reset()
            return True
        boundary = not self.count or self.count >= MAX_EVENTS or self.size + size > MAX_BYTES
        if boundary:
            self.reset()
        self.count += 1
        self.size += size
        return boundary


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
