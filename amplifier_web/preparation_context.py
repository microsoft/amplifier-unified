"""One host read per fitted request; no cache across requests or revalidation."""
import asyncio
from contextvars import ContextVar

OPERATIONS = ('context.manifest', 'message.reactions.context', 'coordination.current', 'memory.context')
# Preserve the existing per-section read deadline on the host. Allow one more
# read interval for transport so its response can retain the completed sections.
SECTION_TIMEOUT = 1.5
TRANSPORT_TIMEOUT = SECTION_TIMEOUT * 2


class PreparationContext:
    def __init__(self, bridge):
        self.bridge = bridge
        self.current = ContextVar('unified_preparation_context', default=None)

    async def read(self, operation, args):
        current = self.current.get()
        if current is not None and operation in OPERATIONS and not args:
            item = current.get(operation, {})
            if item.get('available') is not True:
                raise ValueError('Current preparation context is unavailable')
            return item['value']
        return await self.bridge(operation, args)

    def wrap(self, delivery):
        owner = self

        class Delivery:
            def __getattr__(self, name):
                return getattr(delivery, name)

            async def prepare(self, request, provider, *, commit=False):
                from .execution_events import CALL_PURPOSE
                if CALL_PURPOSE.get() or not any(getattr(t, 'name', None) == 'app_control'
                                                for t in request.tools or []):
                    return await delivery.prepare(request, provider, commit=commit)
                try:
                    values = await asyncio.wait_for(owner.bridge('context.prepare', {}), TRANSPORT_TIMEOUT)
                    if not isinstance(values, dict):
                        raise ValueError('Invalid preparation context')
                except Exception:
                    values = {}
                token = owner.current.set(values)
                try:
                    return await delivery.prepare(request, provider, commit=commit)
                finally:
                    owner.current.reset(token)

        return Delivery()
