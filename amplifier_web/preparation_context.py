"""One observation per request, including its single pretransport refit."""
import asyncio
from contextvars import ContextVar

OPERATIONS = ('context.manifest', 'message.reactions.context', 'coordination.current', 'memory.context')
# Preserve the existing per-section read deadline on the host. Allow one more
# read interval for transport so its response can retain the completed sections.
SECTION_TIMEOUT = 1.5
TRANSPORT_TIMEOUT = SECTION_TIMEOUT * 2


def _ordinary_input(message):
    metadata = message.metadata or {}
    origin = metadata.get('amplifier_input') or {}
    return (message.role == 'user' and not any(metadata.get(key) for key in
            ('ephemeral', 'context_summary', 'summary', 'generated', 'interrupted', 'interruption',
             'live_recovery_job', 'amplifier_visible_reference', 'amplifier_recovery_reference'))
            and not (isinstance(origin, dict) and origin.get('version') == 1
                     and origin.get('kind') == 'service'))


def _settled_batch_anchors(messages):
    """Map genuine tool result IDs to the end of their complete parallel batch."""
    anchors, seen = {}, set()
    for index, message in enumerate(messages):
        if message.role != 'assistant':
            continue
        calls = {call.get('id') if isinstance(call, dict) else call.id
                 for call in getattr(message, 'tool_calls', None) or []}
        if isinstance(message.content, list):
            calls.update(block.id for block in message.content if block.type == 'tool_call')
        if not calls:
            continue
        if any(not isinstance(identity, str) or not identity for identity in calls):
            continue
        for identity in calls & seen:
            anchors[identity] = None
        end, results = index + 1, []
        while end < len(messages) and messages[end].role == 'tool':
            results.append(messages[end].tool_call_id)
            end += 1
        if len(results) == len(calls) and set(results) == calls:
            for identity in calls:
                anchors[identity] = None if identity in seen else end
        seen.update(calls)
    return anchors


def anchor_observations(original, prepared):
    """Keep fresh host context before this turn's assistant/tool continuation.

    This is a current-only projection, not a saved provider transcript. Unchanged
    observations reproduce their prior prefix within a tool loop. New input,
    changed context, revocation and compaction deliberately replace that prefix;
    providers own invalidation of any reasoning bound to the old one.
    """
    if not hasattr(original, 'messages') or not hasattr(prepared, 'messages'):
        return prepared
    markers = ('surfaceObservation', 'reactionObservation', 'coordinationObservation',
               'workPlaintextObservation', 'voiceVisualObservation', 'outputImageObservation',
               'outputImagePairObservation', 'memoryContext')
    originals = {id(message) for message in original.messages}
    observations, messages = [], []
    for message in prepared.messages:
        metadata = message.metadata or {}
        if (id(message) not in originals and metadata.get('ephemeral')
                and any(metadata.get(key) for key in markers)):
            observations.append(message)
        else:
            messages.append(message)
    anchor = next((index + 1 for index in range(len(messages) - 1, -1, -1)
                   if _ordinary_input(messages[index])), None)
    if not observations:
        return prepared
    batches = (_settled_batch_anchors(messages)
               if any((message.metadata or {}).get('contextAnchorToolCalls') for message in observations)
               else {})
    positions = {}
    for message in observations:
        metadata = message.metadata or {}
        ids = metadata.get('contextAnchorToolCalls')
        position = anchor
        if ids is not None:
            # No backdating across an acquisition, partial batch, missing
            # receipt or current-input boundary. Unknown/focus stays at tail.
            position = (max(batches[identity] for identity in ids)
                        if ids and all(batches.get(identity) is not None for identity in ids) else None)
            if position is not None and anchor is not None:
                position = max(position, anchor)
        positions.setdefault(len(messages) if position is None else position, []).append(message)
    result = []
    for index in range(len(messages) + 1):
        result.extend(positions.get(index, []))
        if index < len(messages):
            result.append(messages[index])
    return prepared.model_copy(update={'messages': result})


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

            async def _with_context(self, request, provider, values, *, commit=False):
                token = owner.current.set(values)
                try:
                    result = await delivery.prepare(request, provider, commit=commit)
                    result = anchor_observations(request, result)
                    work_request = getattr(delivery, 'work_request', None)
                    binding = work_request(result) if callable(work_request) else None
                    if binding is not None:
                        # Private, bounded by the existing request/refusal records.
                        # Never exported as authority or cached across requests.
                        binding['preparationContext'] = values
                    return result
                finally:
                    owner.current.reset(token)

            async def prepare_refit(self, request, provider, *, binding):
                values = binding.get('preparationContext')
                if not isinstance(values, dict) or binding['required']['status'] != 'available':
                    return await self.prepare(request, provider)
                # Finalization applies the same current policy as an ordinary
                # request: strict Work assurance in Ask; standing authority in
                # Full. Both retain native input/generation/Stop checks.
                return await self._with_context(request, provider, values)

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
                return await self._with_context(request, provider, values, commit=commit)

        return Delivery()
