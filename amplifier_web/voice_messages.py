"""Host-owned voice input identity; never infer ownership from prompt text."""
from __future__ import annotations

import hashlib
import json
import re
import uuid

_PART = re.compile(r'[A-Za-z0-9_-]{1,96}', re.ASCII)
_MARKER = 'unified.voice.v1:'


def voice_input_id(call_id, delegation_id):
    if not all(isinstance(value, str) and _PART.fullmatch(value)
               for value in (call_id, delegation_id)):
        raise ValueError('Voice call and delegation identities must be bounded identifiers.')
    return f'voice:{call_id}:{delegation_id}'


def voice_provenance(input_id):
    if not isinstance(input_id, str):
        return None
    parts = input_id.split(':')
    if len(parts) != 3 or parts[0] != 'voice':
        return None
    try:
        if voice_input_id(parts[1], parts[2]) != input_id:
            return None
    except ValueError:
        return None
    return {'version': 1, 'kind': 'user', 'id': input_id, 'source': 'user',
            'call_id': _MARKER + input_id}


def validated_voice_provenance(value, input_id):
    expected = voice_provenance(input_id)
    if (expected is None or not isinstance(value, dict)
            or type(value.get('version')) is not int or value['version'] != 1
            or any(value.get(key) != item for key, item in expected.items())):
        return None
    return expected


def is_internal_voice_input(row):
    """Only canonical user metadata with the supported exact host marker hides."""
    if not isinstance(row, dict) or row.get('role') != 'user':
        return False
    metadata = row.get('metadata')
    value = metadata.get('amplifier_input') if isinstance(metadata, dict) else None
    return (isinstance(value, dict)
            and validated_voice_provenance(value, value.get('id')) is not None)


def voice_fingerprint(session_id, text):
    return hashlib.sha256(json.dumps(['voice_delegate', session_id, text]).encode()).hexdigest()


def presentation_proof(session_id, text, input_id):
    provenance = voice_provenance(input_id)
    if provenance is None:
        raise ValueError('Invalid voice input identity.')
    return {'version': 1, 'sessionId': session_id, 'inputId': input_id,
            'fingerprint': voice_fingerprint(session_id, text),
            'call_id': provenance['call_id']}


class _VoiceSnapshot(dict):
    """In-process capability: JSON/client snapshot fields cannot impersonate it."""
    def __init__(self, session, proof):
        super().__init__(session)
        self._voice_proof = dict(proof)


def private_voice_snapshot(session, text, input_id, fingerprint, receipt):
    """Bind a private snapshot only to an exact durable host presentation proof."""
    expected = presentation_proof(session['id'], text, input_id) if voice_provenance(input_id) else None
    proof = receipt.get('voicePresentation') if isinstance(receipt, dict) else None
    if (expected is None or not isinstance(proof, dict)
            or type(proof.get('version')) is not int or proof != expected
            or fingerprint != expected['fingerprint']
            or receipt.get('inputId') != input_id or receipt.get('sessionId') != session['id']):
        return dict(session)
    return _VoiceSnapshot(session, proof)


def private_voice_provenance(session, text, input_id):
    if not isinstance(session, _VoiceSnapshot):
        return None
    expected = presentation_proof(session['id'], text, input_id) if voice_provenance(input_id) else None
    if expected is None or session._voice_proof != expected:
        return None
    return voice_provenance(input_id)


RESPONSE_METADATA = 'unified_voice_response'


def response_scope(runtime, private_inputs):
    """Snapshot complete applied membership backed by private IPC acceptance.

    This is provisional append ownership, NOT final generation membership or
    delivery evidence. A later mixed steering input invalidates suppression.
    """
    generation = runtime.generation
    if not isinstance(generation, dict):
        return None
    input_ids = generation.get('input_ids')
    generation_id = generation.get('id')
    if (not isinstance(generation_id, str) or not 0 < len(generation_id) <= 128
            or not isinstance(input_ids, list) or not input_ids
            or not all(isinstance(identity, str) for identity in input_ids)
            or len(input_ids) > 2000 or len(set(input_ids)) != len(input_ids)):
        return None
    bindings = []
    for input_id in input_ids:
        provenance = validated_voice_provenance(private_inputs.get(input_id), input_id)
        command = runtime.accepted.get(input_id)
        if (provenance is None or command is None or command.id != input_id
                or command.kind != 'user' or command.source != 'user'
                or command.call_id != provenance['call_id']):
            return None
        bindings.append({'commandId': input_id, 'acceptedInputId': command.id,
                         'voiceCallId': input_id.split(':')[1]})
    return {'version': 1, 'presentationRole': 'backend-relay',
            'rootSessionId': runtime.session_id, 'generationId': generation_id,
            'inputIds': list(input_ids), 'bindings': bindings}


class VoiceResponseContext:
    """Delegate the configured root context; stamp only actual assistant appends.

    All getters, dynamic prompt factories, measured/retaining request views,
    setters and compaction remain methods of the exact configured instance.
    Cleanup remains the module's original registered cleanup.
    """
    def __init__(self, context, runtime, coordinator):
        self.context, self.runtime, self.coordinator = context, runtime, coordinator

    def __getattr__(self, name):
        return getattr(self.context, name)

    @property
    def max_tokens(self):
        return self.context.max_tokens

    @max_tokens.setter
    def max_tokens(self, value):
        # RuntimeControls budget.set/restore writes this public module field.
        # Request getters stay bound to the configured context, so update it.
        self.context.max_tokens = value

    async def add_message(self, message):
        if message.get('role') == 'assistant':
            from .execution_events import CALL_PURPOSE
            from amplifier_module_loop_live.scope import LIVE_OWNER, JOB_CALL
            metadata = message.get('metadata')
            metadata = dict(metadata) if isinstance(metadata, dict) else {}
            # Provider/model metadata may not confer a host presentation role.
            metadata.pop(RESPONSE_METADATA, None)
            owner = LIVE_OWNER.get()
            compacting = self.coordinator.get_capability('context.compacting')
            if (owner is not None and getattr(owner, 'runtime', None) is self.runtime
                    and not CALL_PURPOSE.get() and not JOB_CALL.get()
                    and not (callable(compacting) and compacting())):
                scope = response_scope(self.runtime, self.coordinator.get_capability('web.voice.inputs') or {})
                if scope is not None:
                    metadata[RESPONSE_METADATA] = {**scope, 'appendId': str(uuid.uuid4())}
            message = {**message, 'metadata': metadata}
        return await self.context.add_message(message)


async def install_response_context(coordinator, runtime):
    """Public post-initialize/pre-execute mount; unknown contexts stay visible."""
    context = coordinator.get('context')
    if isinstance(context, VoiceResponseContext):
        return True
    if (type(context).__module__ != 'amplifier_module_context_simple'
            or type(context).__name__ != 'SimpleContextManager'):
        coordinator.register_capability('web.voice.response_context', {
            'supported': False, 'reason': 'Configured context delegation is not qualified; backend text remains visible.'})
        return False
    await coordinator.mount('context', VoiceResponseContext(context, runtime, coordinator))
    coordinator.register_capability('web.voice.response_context', {'supported': True})
    return True