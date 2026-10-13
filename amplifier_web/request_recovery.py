"""Bounded recovery inside an unreturned model call, never turn/tool replay."""
import asyncio
from contextvars import ContextVar
import copy
import math

_ACTIVE = ContextVar('unified_request_recovery', default=None)
DELAYS = (2, 5)
_UNCOPYABLE = object()


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
        scope = _ACTIVE.get()
        if scope is not None:
            scope['exposed'] = True
        return HookResult()
    # Some complete() implementations emit progressive output through hooks.
    # Once anything escapes, replay needs provider-specific reconciliation.
    for event in ('content_block:start', 'content_block:delta', 'content_block:end',
                  'provider:response', 'llm:response', 'tool:pre'):
        coordinator.hooks.register(event, exposed, name='unified-request-recovery', priority=-200)
    coordinator.register_capability('web.request_recovery', True)


class RecoveringProvider:
    def __init__(self, original, coordinator):
        self.original, self.coordinator = original, coordinator

    def __getattr__(self, name):
        method = getattr(self.original, name)
        if name == 'stream' and callable(method):
            async def stream(request, **kwargs):
                async for event in self._stream(method, request, kwargs):
                    yield event
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
        try:
            await self.coordinator.hooks.emit('provider:retry', {
                'attempt': attempt + 1, 'max_retries': len(DELAYS),
                'delay': delay, 'error_type': type(error).__name__,
                'recovery': 'unreturned_model_request', 'tools_replayed': False})
        except Exception:
            pass  # Diagnostic failure cannot change the request outcome.
        await asyncio.sleep(delay)  # User Stop cancels this same executing task.
        return generation == self._generation() and not scope['exposed'] and request == saved and kwargs == saved_kwargs

    async def complete(self, request, **kwargs):
        saved, saved_kwargs = snapshot(request), snapshot(kwargs)
        generation = self._generation()
        for attempt in range(len(DELAYS) + 1):
            scope = {'exposed': False}
            token = _ACTIVE.set(scope)
            try:
                return await self.original.complete(request, **kwargs)
            except Exception as error:
                if not await self._retry(error, attempt, scope, generation, request, saved, kwargs, saved_kwargs):
                    raise
            finally:
                _ACTIVE.reset(token)

    async def _stream(self, method, request, kwargs):
        saved, saved_kwargs = snapshot(request), snapshot(kwargs)
        generation = self._generation()
        for attempt in range(len(DELAYS) + 1):
            scope = {'exposed': False}
            token = _ACTIVE.set(scope)
            iterator = None
            try:
                try:
                    iterator = method(request, **kwargs)
                    async for event in iterator:
                        scope['exposed'] = True
                        yield event
                finally:
                    if callable(getattr(iterator, 'aclose', None)):
                        await iterator.aclose()
                return
            except Exception as error:
                if not await self._retry(error, attempt, scope, generation, request, saved, kwargs, saved_kwargs):
                    raise
            finally:
                _ACTIVE.reset(token)
