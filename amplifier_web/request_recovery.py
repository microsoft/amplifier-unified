"""Bounded recovery inside an unreturned model call, never turn/tool replay."""
import asyncio
from contextvars import ContextVar
import copy
import math
from time import monotonic

_ACTIVE = ContextVar('unified_request_recovery', default=None)
# Try soon for short interruptions, then at most twice a minute. The elapsed
# budget also counts time spent inside provider/SDK retries, not just our sleeps.
DELAYS = (5, 10, 20) + (30,) * 19
RECOVERY_SECONDS = 600
_ERROR_DIAGNOSTIC_FIELDS = frozenset((
    'status', 'error', 'provider', 'model', 'duration_ms',
    'request_id', 'unified_call_id', 'session_id', 'parent_id', 'timestamp'))
_UNCOPYABLE = object()


class _RecoveryExpired(Exception):
    pass


async def _before_output(awaitable, scope, bounded):
    if not bounded:
        return await awaitable
    timer = asyncio.timeout(max(0, scope['deadline'] - monotonic()))
    try:
        async with timer:
            scope['timer'] = timer
            try:
                return await awaitable
            finally:
                scope.pop('timer', None)
    except TimeoutError:
        if timer.expired():
            raise _RecoveryExpired from None
        raise


def snapshot(value):
    try:
        return copy.deepcopy(value)
    except Exception:
        # Opaque provider options must not prevent the original request.
        return _UNCOPYABLE


def install(coordinator):
    if coordinator.get_capability('web.request_recovery'):
        return
    from amplifier_core import HookResult
    async def exposed(event, data):
        # A terminal failure diagnostic is not a returned model response.
        # Unknown fields (including raw/content/tool payloads) stay conservative.
        if (event == 'llm:response' and data.get('status') == 'error'
                and data.keys() <= _ERROR_DIAGNOSTIC_FIELDS):
            return HookResult()
        scope = _ACTIVE.get()
        if scope is not None:
            scope['exposed'] = True
            timer = scope.get('timer')
            if timer is not None:
                timer.reschedule(None)  # Never cut off an exposed response.
        return HookResult()
    # Some complete() implementations emit progressive output through hooks.
    # Once anything escapes, replay needs provider-specific reconciliation.
    for event in ('content_block:start', 'content_block:delta', 'content_block:end',
                  'llm:stream_block_start', 'llm:stream_block_delta', 'llm:stream_block_end',
                  'provider:response', 'llm:response', 'tool:pre'):
        coordinator.hooks.register(event, exposed, name='unified-request-recovery', priority=-200)
    coordinator.register_capability('web.request_recovery', True)


class RecoveringProvider:
    def __init__(self, original, coordinator):
        self.original, self.coordinator = original, coordinator

    def __getattr__(self, name):
        method = getattr(self.original, name)
        if name == 'stream' and callable(method):
            def stream(request, **kwargs):
                return self._stream(method, request, kwargs)
            return stream
        return method

    def _generation(self):
        runtime = self.coordinator.get_capability('live.runtime')
        generation = getattr(runtime, 'generation', None) or {}
        return (id(runtime), generation.get('id'), getattr(runtime, 'closed', False))

    async def _retry(self, error, attempt, scope, generation, request, saved, kwargs, saved_kwargs):
        from amplifier_core.llm_errors import LLMTimeoutError, ProviderUnavailableError
        if (attempt >= len(DELAYS) or scope['exposed']
                or not isinstance(error, (LLMTimeoutError, ProviderUnavailableError))
                or error.retryable is not True
                or getattr(self.original, 'native_bundle_live', False)
                or generation != self._generation() or generation[2]
                or saved is _UNCOPYABLE or saved_kwargs is _UNCOPYABLE
                or request != saved or kwargs != saved_kwargs):
            return False
        delay = DELAYS[attempt]
        advised = getattr(error, 'retry_after', None)
        if advised is not None:
            # Respect a provider delay, but never introduce an unbounded wait.
            if not isinstance(advised, (int, float)) or not math.isfinite(advised) or advised < 0 or advised > 30:
                return False
            delay = max(delay, advised)
        remaining = scope['deadline'] - monotonic()
        if remaining <= 0:
            return False
        delay = min(delay, remaining)
        try:
            async with asyncio.timeout(remaining):
                try:
                    await self.coordinator.hooks.emit('provider:retry', {
                        'attempt': attempt + 1, 'max_retries': len(DELAYS),
                        'delay': delay, 'error_type': type(error).__name__,
                        'recovery': 'unreturned_model_request', 'tools_replayed': False})
                except Exception:
                    pass  # Diagnostic failure cannot change the request outcome.
                await asyncio.sleep(delay)  # Stop cancels this same executing task.
        except TimeoutError:
            return False
        return (monotonic() < scope['deadline'] and generation == self._generation()
                and not scope['exposed'] and request == saved and kwargs == saved_kwargs)

    async def complete(self, request, **kwargs):
        saved, saved_kwargs = snapshot(request), snapshot(kwargs)
        generation = self._generation()
        deadline, last_error = monotonic() + RECOVERY_SECONDS, None
        for attempt in range(len(DELAYS) + 1):
            scope = {'exposed': False, 'deadline': deadline}
            token = _ACTIVE.set(scope)
            try:
                # Leave the original healthy call's timeout with its provider.
                # Once recovery starts, all subsequent transport time is bounded.
                return await _before_output(self.original.complete(request, **kwargs), scope, attempt > 0)
            except _RecoveryExpired:
                raise last_error from None
            except Exception as error:
                last_error = error
                if not await self._retry(error, attempt, scope, generation, request, saved, kwargs, saved_kwargs):
                    raise
            finally:
                _ACTIVE.reset(token)

    async def _stream(self, method, request, kwargs):
        saved, saved_kwargs = snapshot(request), snapshot(kwargs)
        generation = self._generation()
        deadline, last_error = monotonic() + RECOVERY_SECONDS, None
        for attempt in range(len(DELAYS) + 1):
            scope = {'exposed': False, 'deadline': deadline}
            iterator = None
            try:
                try:
                    token = _ACTIVE.set(scope)
                    try:
                        iterator = aiter(method(request, **kwargs))
                    finally:
                        _ACTIVE.reset(token)
                    while True:
                        token = _ACTIVE.set(scope)
                        try:
                            event = await _before_output(
                                anext(iterator), scope, attempt > 0 and not scope['exposed'])
                        except StopAsyncIteration:
                            return
                        finally:
                            _ACTIVE.reset(token)
                        # Never retain a ContextVar token across a yield: the
                        # next read or close can belong to another consumer task.
                        scope['exposed'] = True
                        yield event
                finally:
                    if callable(getattr(iterator, 'aclose', None)):
                        token = _ACTIVE.set(scope)
                        try:
                            await iterator.aclose()
                        finally:
                            _ACTIVE.reset(token)
            except _RecoveryExpired:
                raise last_error from None
            except Exception as error:
                last_error = error
                token = _ACTIVE.set(scope)
                try:
                    if not await self._retry(error, attempt, scope, generation, request, saved, kwargs, saved_kwargs):
                        raise
                finally:
                    _ACTIVE.reset(token)
