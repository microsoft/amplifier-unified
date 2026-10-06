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


def generation_verdict(session, generation_id):
    """One fail-open verdict for independent live and canonical identities.

    Host membership is monotonic. A final receipt cannot undo observed mixed
    input or conflicting final membership. Provisional append stamps never
    authorize concealment by themselves.
    """
    receipt = session.get('voiceResponses', {}).get(generation_id)
    evidence = session.get('voiceMembership', {}).get(generation_id, {})
    if session.get('voiceMembershipIncomplete') or evidence.get('public'):
        return 'public'
    if not isinstance(receipt, dict):
        return 'unknown'
    checked = validated_response_receipt(receipt, session['id'], generation_id, receipt.get('inputIds'))
    if checked is None:
        return 'unknown'
    if checked['ownership'] == 'public-mixed':
        return 'public'
    if checked['ownership'] != 'exclusive-private-voice':
        return 'unknown'
    if evidence:
        inputs = evidence.get('inputIds', [])
        if not set(inputs).issubset(checked['inputIds']):
            return 'public'
        if evidence.get('terminal') and inputs != checked['inputIds']:
            return 'public'
    return 'exclusive'


def retain_membership(session, generation_id, input_ids, bindings, *, terminal=False):
    """Retain body-free trusted applied membership, never pending acceptance."""
    if (not isinstance(generation_id, str) or not 0 < len(generation_id) <= 128
            or not isinstance(input_ids, list) or not 0 < len(input_ids) <= 2000
            or not all(isinstance(i, str) and 0 < len(i) <= 256 for i in input_ids)
            or len(set(input_ids)) != len(input_ids)):
        return
    records = session.setdefault('voiceMembership', {})
    prior = records.get(generation_id, {})
    public = (prior.get('public', False)
              or [b['commandId'] for b in bindings] != input_ids
              or not set(prior.get('inputIds', [])).issubset(input_ids)
              or prior.get('terminal', False) and prior.get('inputIds') != input_ids)
    records[generation_id] = {'inputIds': list(input_ids), 'public': bool(public),
                              'terminal': terminal or prior.get('terminal', False)}
    while len(records) > 200:
        session['voiceMembershipIncomplete'] = True
        del records[next(iter(records))]


def retain_receipt(session, receipt):
    records = session.setdefault('voiceResponses', {})
    previous = records.get(receipt['generationId'])
    if previous and (previous.get('ownership') == 'public-mixed'
                     or previous.get('inputIds') != receipt['inputIds']
                     or previous.get('bindings') != receipt['bindings']
                     or previous.get('appendIds') != receipt['appendIds']):
        # Conflicting receipt order must not turn a public body private again.
        session.setdefault('voiceMembership', {}).setdefault(receipt['generationId'], {})['public'] = True
        return
    records[receipt['generationId']] = receipt
    while len(records) > 200:
        del records[next(iter(records))]


def response_generations(session, row):
    """Return proven origin generations; no text/index/live-to-append join."""
    if row.get('role') != 'assistant' or row.get('voiceId') or row.get('partial'):
        return None
    canonical = row.get('voiceResponseRef')
    if isinstance(canonical, dict):
        generation = canonical.get('generationId')
        inputs = canonical.get('inputIds')
        if (type(canonical.get('version')) is not int or canonical['version'] != 1
                or canonical.get('presentationRole') != 'backend-relay'
                or not isinstance(generation, str)
                or not isinstance(inputs, list) or not inputs or len(inputs) > 2000
                or not all(isinstance(i, str) for i in inputs)):
            return None
        receipt = session.get('voiceResponses', {}).get(generation, {})
        if (canonical.get('rootSessionId') != session['id']
                or canonical.get('appendId') not in receipt.get('appendIds', [])
                or not canonical.get('inputIds')
                or not set(canonical['inputIds']).issubset(receipt.get('inputIds', []))
                or canonical.get('bindings') != [b for b in receipt.get('bindings', [])
                    if b.get('commandId') in canonical['inputIds']]
                or generation_verdict(session, generation) != 'exclusive'):
            return None
        return [generation]
    refs = row.get('rootGenerations')
    if refs is None:
        refs = [row['rootGeneration']] if row.get('rootGeneration') else []
    if not isinstance(refs, list) or not refs or len(refs) > 200:
        return None
    generations = []
    for ref in refs:
        if (not isinstance(ref, dict) or type(ref.get('version')) is not int or ref.get('version') != 1
                or ref.get('rootSessionId') != session['id']
                or not isinstance(ref.get('generationId'), str)
                or not isinstance(ref.get('inputIds'), list) or not ref['inputIds']
                or not all(isinstance(i, str) for i in ref['inputIds'])):
            return None
        generation = ref.get('generationId')
        receipt = session.get('voiceResponses', {}).get(generation, {})
        if (not set(ref['inputIds']).issubset(receipt.get('inputIds', []))
                or generation_verdict(session, generation) != 'exclusive'):
            return None
        if generation not in generations:
            generations.append(generation)
    return generations


def written_fallback(session, generations):
    calls = {binding['voiceCallId'] for generation in generations
             for binding in session['voiceResponses'][generation]['bindings']}
    expand = False
    for call_id in calls:
        call = session.get('voiceCalls', {}).get(call_id, {})
        for generation in generations:
            outcome = call.get('outcomes', {}).get(generation, {})
            inputs = [binding['commandId'] for binding in session['voiceResponses'][generation]['bindings']
                      if binding['voiceCallId'] == call_id]
            # Scope a negative to this original route, not the current global
            # call. Attempted relay is expressly NOT hearing evidence.
            if (inputs and set(inputs).issubset(call.get('inputIds', []))
                    and outcome.get('sessionId') == session['id']
                    and outcome.get('callId') == call_id
                    and outcome.get('generationId') == generation
                    and outcome.get('clientId') is not None
                    and outcome.get('clientId') == call.get('clientId')
                    and outcome.get('state') in {'relay-failed', 'late-no-attempt'}):
                expand = True
    return {'expanded': expand, 'status': 'relay-unavailable' if expand else 'unconfirmed',
            'notice': ('The original call could not receive this relay. Written answer available.'
                       if expand else 'Audio playback is unconfirmed. Reveal the written answer if needed.')}


def project_message(session, row):
    """Pure public presentation: original body stays in its existing store."""
    generations = response_generations(session, row)
    if generations is None:
        return {**row, **({'voiceUncertainty': 'Relay ownership or audio playback is unconfirmed; written text remains visible.'}
                         if row.get('voiceResponseRef') or row.get('rootGeneration') else {})}
    from .browser_detail import digest
    text = row.get('text', '')
    return {**{key: value for key, value in row.items() if key not in {'text', 'textDetail'}},
            'text': '', 'presentation': 'backend-relay',
            'relayChannel': 'saved' if row.get('voiceResponseRef') else 'live',
            'writtenFallback': written_fallback(session, generations),
            'relayTextDetail': {'sessionId': session['id'], 'part': 'messages',
                'id': row['id'], 'field': 'text', 'digest': digest(text), 'length': len(text)}}


def applied_generation(runtime):
    """Read the runtime's complete applied set, never accepted/pending inputs."""
    generation = runtime.generation
    if not isinstance(generation, dict):
        return None
    identities, identity = generation.get('input_ids'), generation.get('id')
    if (not isinstance(identity, str) or not 0 < len(identity) <= 128
            or not isinstance(identities, list) or not 0 < len(identities) <= 2000
            or not all(isinstance(value, str) and 0 < len(value) <= 256 for value in identities)
            or len(set(identities)) != len(identities)):
        return None
    return {'version': 1, 'rootSessionId': runtime.session_id,
            'generationId': identity, 'inputIds': list(identities)}


def response_scope(runtime, private_inputs):
    """Snapshot complete applied membership backed by private IPC acceptance.

    This is provisional append ownership, NOT final generation membership or
    delivery evidence. A later mixed steering input invalidates suppression.
    """
    generation = applied_generation(runtime)
    if generation is None:
        return None
    bindings = []
    for input_id in generation['inputIds']:
        binding = accepted_voice_binding(runtime, private_inputs, input_id)
        if binding is None:
            return None
        bindings.append(binding)
    return {**generation, 'presentationRole': 'backend-relay', 'bindings': bindings}


def accepted_voice_binding(runtime, private_inputs, input_id):
    provenance = validated_voice_provenance(private_inputs.get(input_id), input_id)
    command = runtime.accepted.get(input_id)
    if (provenance is None or command is None or command.id != input_id
            or command.kind != 'user' or command.source != 'user'
            or command.call_id != provenance['call_id']):
        return None
    return {'commandId': input_id, 'acceptedInputId': command.id,
            'voiceCallId': input_id.split(':')[1]}


def validated_response_receipt(value, root_id, generation_id, input_ids):
    """Validate a private worker receipt; this alone never hides any body."""
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value['version'] != 1 or value.get('rootSessionId') != root_id
            or value.get('generationId') != generation_id or value.get('inputIds') != input_ids
            or not isinstance(input_ids, list) or not 0 < len(input_ids) <= 2000
            or not all(isinstance(i, str) and 0 < len(i) <= 256 for i in input_ids)
            or len(set(input_ids)) != len(input_ids)
            or not isinstance(generation_id, str) or not 0 < len(generation_id) <= 128
            or not isinstance(value.get('ownership'), str)
            or value.get('ownership') not in {'exclusive-private-voice', 'public-mixed', 'unconfirmed'}
            or not isinstance(value.get('appendIds'), list) or not 0 < len(value['appendIds']) <= 512
            or not all(isinstance(i, str) for i in value['appendIds'])
            or len(set(value['appendIds'])) != len(value['appendIds'])):
        return None
    try:
        if any(not isinstance(i, str) or str(uuid.UUID(i)) != i for i in value['appendIds']):
            return None
    except (ValueError, AttributeError):
        return None
    bindings = value.get('bindings')
    if not isinstance(bindings, list) or len(bindings) > len(input_ids):
        return None
    expected = []
    for item in bindings:
        if not isinstance(item, dict):
            return None
        identity = item.get('commandId')
        proof = voice_provenance(identity)
        if proof is None or identity not in input_ids:
            return None
        binding = {'commandId': identity, 'acceptedInputId': identity,
                   'voiceCallId': identity.split(':')[1]}
        if item != binding or binding in expected:
            return None
        expected.append(binding)
    if (value['ownership'] == 'exclusive-private-voice'
            and [b['commandId'] for b in expected] != input_ids):
        return None
    return {'version': 1, 'rootSessionId': root_id, 'generationId': generation_id,
            'inputIds': list(input_ids), 'appendIds': list(value['appendIds']),
            'bindings': expected, 'ownership': value['ownership']}


class VoiceResponseContext:
    """Delegate the configured root context; stamp only actual assistant appends.

    All getters, dynamic prompt factories, measured/retaining request views,
    setters and compaction remain methods of the exact configured instance.
    Cleanup remains the module's original registered cleanup.
    """
    def __init__(self, context, runtime, coordinator):
        self.context, self.runtime, self.coordinator = context, runtime, coordinator
        self.response_appends = []
        self.response_overflow = False

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
        scope = None
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
        result = await self.context.add_message(message)
        if scope is not None:
            # Only a successful configured-context append earns a reference.
            # Bounded, body-free, and consumed when the generation terminates.
            if len(self.response_appends) < 512:
                self.response_appends.append(message['metadata'][RESPONSE_METADATA])
            else:
                self.response_overflow = True
        return result

    def observe(self, event):
        """Associate live root events and reconcile appends before runtime clears.

        No live-block/append join is inferred: the loop drops block indices on
        assistant.message and emits those messages before canonical append.
        """
        kind = event.get('type')
        event.pop('rootGeneration', None)
        event.pop('voiceResponse', None)
        if ((event.get('sessionId') or event.get('session_id')) != self.runtime.session_id
                or (event.get('rootSessionId') or event.get('root_session_id')
                    or self.runtime.session_id) != self.runtime.session_id):
            return
        if kind == 'generation.started':
            self.response_appends = []
            self.response_overflow = False
            return
        if kind in {'assistant.message', 'assistant.delta'}:
            from .execution_events import CALL_PURPOSE
            from amplifier_module_loop_live.scope import LIVE_OWNER, JOB_CALL
            owner = LIVE_OWNER.get()
            compacting = self.coordinator.get_capability('context.compacting')
            if (owner is not None and getattr(owner, 'runtime', None) is self.runtime
                    and not CALL_PURPOSE.get() and not JOB_CALL.get()
                    and not (callable(compacting) and compacting())):
                generation = applied_generation(self.runtime)
                if generation is not None:
                    event['rootGeneration'] = generation
            return
        if kind not in {'generation.finished', 'generation.failed', 'generation.detached'}:
            return
        appends, overflow = self.response_appends, self.response_overflow
        self.response_appends, self.response_overflow = [], False
        generation = applied_generation(self.runtime)
        if (generation is None or not appends or overflow
                or event.get('generation_id') != generation['generationId']
                or event.get('input_ids') != generation['inputIds']
                or any(row['generationId'] != generation['generationId']
                       or row['rootSessionId'] != generation['rootSessionId']
                       or not set(row['inputIds']).issubset(generation['inputIds']) for row in appends)):
            return
        private_inputs = self.coordinator.get_capability('web.voice.inputs') or {}
        scope = response_scope(self.runtime, private_inputs)
        bindings = [binding for identity in generation['inputIds']
                    if (binding := accepted_voice_binding(self.runtime, private_inputs, identity)) is not None]
        value = {**generation, 'appendIds': [row['appendId'] for row in appends],
                 'bindings': bindings,
                 'ownership': ('exclusive-private-voice' if scope else 'public-mixed')
                              if kind == 'generation.finished' else 'unconfirmed'}
        receipt = validated_response_receipt(value, self.runtime.session_id,
                                            generation['generationId'], generation['inputIds'])
        if receipt is not None:
            event['voiceResponse'] = receipt


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