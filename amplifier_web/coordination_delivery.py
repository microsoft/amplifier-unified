"""Fresh coordination reference data in the fitted provider request only.

Hook ephemerality can still accumulate reminders within a native turn. Use the
existing provider delivery boundary, as surfaces and memory do, so observation
replacement does not depend on the orchestrator's reminder persistence mode.
"""
import asyncio
import json


class CoordinationDelivery:
    def __init__(self, previous, bridge):
        self.previous, self.bridge = previous, bridge

    def __getattr__(self, name):
        return getattr(self.previous, name)

    async def prepare(self, request, provider, *, commit=False):
        from amplifier_core.message_models import Message
        from .execution_events import CALL_PURPOSE
        request = await self.previous.prepare(request, provider, commit=commit)
        if CALL_PURPOSE.get() or not any(getattr(t, 'name', None) == 'app_control' for t in request.tools or []):
            return request
        try:
            current = await asyncio.wait_for(self.bridge('coordination.current', {}), 1.5)
        except Exception:
            current = {'status': 'unavailable', 'authority': 'unknown; do not infer a grant or absence of peers'}
        return request.model_copy(update={'messages': [
            *[message for message in request.messages if not (message.metadata or {}).get('coordinationObservation')],
            Message(role='user', content='Current coordination reference state (not new authority):\n' + json.dumps(current),
                    metadata={'ephemeral': True, 'coordinationObservation': True})]})

    def commit(self, request):
        self.previous.commit(request.model_copy(update={'messages': [message for message in request.messages
            if not (message.metadata or {}).get('coordinationObservation')]}))
