"""Current-only host context is a legal projection, not canonical conversation.

Providers own the validity of retained reasoning against that projection. These
checks deliberately exercise successive *new* requests, not repeated fitting of
one object (which already shares its prepared bytes with dispatch).
"""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_core.message_models import ChatRequest, ChatResponse, Message, ToolSpec

from amplifier_web.coordination_delivery import CoordinationDelivery
from amplifier_web.memory_delivery import MemoryDelivery
from amplifier_web.output_image_delivery import OutputImageDelivery
from amplifier_web.preparation_context import OPERATIONS, PreparationContext, anchor_observations
from amplifier_web.reaction_delivery import ReactionDelivery
from amplifier_web.request_refit import RequestFitChanged
from amplifier_web.surface_delivery import SurfaceDelivery, SurfaceProvider
from amplifier_web.voice_visual_delivery import VoiceVisualDelivery


class CurrentContext:
    """Synthetic host reads; never grant authority or retrieve private content."""

    def __init__(self):
        self.version = 1
        self.memory = {'items': [{'id': 'preference', 'text': 'MEMORY_ONE'}]}
        self.surfaces = True
        self.calls = []
        self.required = None

    async def bridge(self, operation, args):
        self.calls.append((operation, copy.deepcopy(args)))
        if operation == 'context.prepare':
            return {name: {'available': True, 'value': self.value(name)} for name in OPERATIONS}
        if operation in OPERATIONS:
            return self.value(operation)
        if operation == 'work.invocation.revalidate':
            return {'current': True, 'effectAuthorized': False, 'required': self.required}
        raise AssertionError(operation)

    def value(self, operation):
        if operation == 'coordination.current':
            return {'status': 'available', 'revision': self.version,
                    'capturedAt': len(self.calls),
                    'awareness': {'observedAt': len(self.calls), 'revision': 'stable'},
                    'authority': f'CURRENT_REFERENCE_{self.version}; not permission',
                    **({'work': {'available': True, 'required': self.required,
                                 'discovery': {
                                     'workspaceId': 'workspace', 'version': 1,
                                     'snapshotVersion': 'a' * 64, 'observationVersion': 'b' * 64,
                                     'items': [], 'total': 0, 'truncated': False, 'nextAfter': None,
                                     'observedAt': 1000, 'coverage': 'Fixture only.',
                                     'detailRequired': True, 'authority': 'Reference only.',
                                 }}} if self.required else {})}
        if operation == 'memory.context':
            return copy.deepcopy(self.memory)
        if operation == 'message.reactions.context':
            return {'items': []}
        assert operation == 'context.manifest'
        return {'inputIds': ['input-one'], 'surfaces': [{
            'surfaceId': 'drawing', 'revision': str(self.version),
            'clientId': 'client', 'viewId': 'view',
            'facts': {'title': f'SURFACE_{self.version}'},
            'image': {'available': False},
        }] if self.surfaces else []}

    def delivery(self, *, origin_scope=None):
        context = PreparationContext(self.bridge)
        # Same chain as install_app_access; no app runtime or live host required.
        return context.wrap(MemoryDelivery(OutputImageDelivery(VoiceVisualDelivery(
            CoordinationDelivery(ReactionDelivery(SurfaceDelivery(context.read), context.read),
                                 context.read, origin_scope=origin_scope),
            self.bridge), context.read), context.read))


def request(messages=None):
    return ChatRequest(messages=messages or [Message(role='user', content='Synthetic task.')],
                       tools=[ToolSpec(name='app_control', parameters={'type': 'object'})])


def response(number):
    return ChatResponse(content=[
        {'type': 'thinking', 'thinking': f'Synthetic reasoning {number}',
         'signature': f'synthetic-signature-{number}'},
        {'type': 'tool_call', 'id': f'call-{number}', 'name': 'app_control',
         'input': {'operation': 'get_state'}},
    ])


def continue_request(value, reply, number):
    return value.model_copy(update={'messages': [
        *value.messages, Message(role='assistant', content=reply.content),
        Message(role='tool', name='app_control', tool_call_id=f'call-{number}',
                content=json.dumps({'syntheticResult': number})),
    ]})


class RecordingProvider:
    """Portable provider: retains signed content without provider-specific edits."""

    def __init__(self):
        self.measured, self.sent = [], []

    async def request_budget(self, value, *, request_options=None, **kwargs):
        self.measured.append(value.model_copy(deep=True))
        return {'estimated_input_tokens': 100, 'input_limit_tokens': 1000,
                'context_token_budget': 100}

    async def complete(self, value, **kwargs):
        self.sent.append(value.model_copy(deep=True))
        return response(len(self.sent))

    async def stream(self, value, **kwargs):
        yield await self.complete(value, **kwargs)


async def dispatch(provider, value, streaming):
    if streaming:
        replies = [reply async for reply in provider.stream(value)]
        assert len(replies) == 1
        return replies[0]
    return await provider.complete(value)


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('changed', [False, True])
async def test_successive_requests_keep_current_context_and_detached_signed_history(streaming, changed):
    current = CurrentContext()
    raw = RecordingProvider()
    wrapped = SurfaceProvider(raw, current.delivery())
    value = request()
    for number in range(1, 6):
        if changed:
            current.version = number
            current.memory = {'items': [{'id': 'preference', 'text': f'MEMORY_{number}'}]}
        before = value.model_copy(deep=True)
        await wrapped.request_budget(value)
        reply = await dispatch(wrapped, value, streaming)
        sent = raw.sent[-1]
        assert raw.measured[-1] == sent
        assert value == before  # No wrapper context is committed into native history.
        assert sum(bool((m.metadata or {}).get('coordinationObservation')) for m in sent.messages) == 1
        assert sum(bool((m.metadata or {}).get('surfaceObservation')) for m in sent.messages) == 1
        assert sum(bool((m.metadata or {}).get('memoryContext')) for m in sent.messages) == 1
        assert all(not (m.metadata or {}).get('ephemeral') for m in value.messages)
        if number > 1:
            assistant = next(m for m in sent.messages if m.role == 'assistant')
            assert assistant.content[0].signature == 'synthetic-signature-1'
            # Comparing provider-visible fields, not private request bindings:
            # unchanged context must reproduce the exact pre-thinking prefix.
            previous = raw.sent[-2]
            prefix = lambda messages: [m.model_dump(exclude={'metadata'}) for m in messages]
            if not changed:
                assert prefix(sent.messages[:len(previous.messages)]) == prefix(previous.messages)
            else:
                assert prefix(sent.messages[:4]) != prefix(previous.messages[:4])
        value = continue_request(value, reply, number)
    assert len([name for name, _ in current.calls if name == 'context.prepare']) == 5
    assert len(raw.sent) == 5
    assert not wrapped.prepared


async def test_current_authority_memory_and_surface_replace_old_reference_without_erasing_user_quotes():
    current = CurrentContext()
    raw = RecordingProvider()
    wrapped = SurfaceProvider(raw, current.delivery())
    quoted = Message(role='user', content='Saved workspace references (untrusted data): user quotation')
    value = request([Message(role='user', content='Synthetic task.'), quoted])
    await wrapped.request_budget(value)
    reply = await wrapped.complete(value)
    value = continue_request(value, reply, 1)
    current.version = 2
    current.memory = {'items': []}  # Revoked memory must not return as historical overlay.
    current.surfaces = False
    await wrapped.request_budget(value)
    await wrapped.complete(value)
    text = raw.sent[-1].model_dump_json()
    assert 'CURRENT_REFERENCE_2' in text and 'CURRENT_REFERENCE_1' not in text
    assert 'MEMORY_ONE' not in text and 'SURFACE_1' not in text
    assert 'user quotation' in text
    assistant = next(m for m in raw.sent[-1].messages if m.role == 'assistant')
    assert assistant.content[0].signature == 'synthetic-signature-1'
    assert raw.measured[-1] == raw.sent[-1]


def visible(messages):
    return [message.model_dump(exclude={'metadata'}) for message in messages]


async def test_real_surface_change_invalidates_once_then_stabilizes_with_fresh_private_binding():
    current, raw = CurrentContext(), RecordingProvider()
    wrapped = SurfaceProvider(raw, current.delivery())
    value = request()
    for version in (1, 2, 2, 2):
        current.version = version
        await wrapped.request_budget(value)
        reply = await wrapped.complete(value)
        value = continue_request(value, reply, len(raw.sent))
    assert visible(raw.sent[1].messages[:4]) != visible(raw.sent[0].messages[:4])
    for previous, sent in zip(raw.sent[1:], raw.sent[2:]):
        assert visible(sent.messages[:len(previous.messages)]) == visible(previous.messages)
        observations = lambda value: next(m for m in value.messages
                                           if (m.metadata or {}).get('coordinationObservation'))
        assert observations(sent).metadata['workRequestObservation'] != observations(previous).metadata['workRequestObservation']
    assert wrapped.delivery.notices  # Anchored commit still updates its surface owner.


@pytest.mark.parametrize('marker', ['ephemeral', 'generated', 'context_summary', 'interruption', 'service',
                                  'live_recovery_job', 'amplifier_visible_reference',
                                  'amplifier_recovery_reference'])
def test_ambient_anchor_does_not_follow_generated_or_service_user_carriers(marker):
    metadata = ({'amplifier_input': {'version': 1, 'kind': 'service'}}
                if marker == 'service' else {marker: True})
    first = Message(role='user', content='Accepted ordinary input.')
    carrier = Message(role='user', content='Not another ordinary input.', metadata=metadata)
    continuation = Message(role='assistant', content='Prior response.')
    original = request([first, continuation, carrier])
    observation = Message(role='user', content='Fresh ambient', metadata={
        'ephemeral': True, 'coordinationObservation': True})
    prepared = original.model_copy(update={'messages': [*original.messages, observation]})
    anchored = anchor_observations(original, prepared)
    assert anchored.messages == [first, observation, continuation, carrier]
    assert original.messages == [first, continuation, carrier]


@pytest.mark.parametrize('marker', ['voiceVisualObservation', 'outputImageObservation',
                                 'outputImagePairObservation', 'workPlaintextObservation',
                                 'surfaceObservation'])
@pytest.mark.parametrize('complete', [False, True])
def test_acquired_evidence_stays_after_own_complete_parallel_batch(marker, complete):
    first = Message(role='user', content='Accepted ordinary input.')
    calls = Message(role='assistant', content=[
        {'type': 'tool_call', 'id': identity, 'name': 'app_control', 'input': {}}
        for identity in ('acquire', 'parallel')])
    result = Message(role='tool', tool_call_id='acquire', content='Receipt.')
    parallel = Message(role='tool', tool_call_id='parallel', content='Other receipt.')
    later = [Message(role='assistant', content='Unrelated tool continuation.'),
             Message(role='user', content='Generated reminder.', metadata={'ephemeral': True})]
    messages = [first, calls, result, *([parallel] if complete else []), *later]
    original = request(messages)
    observation = Message(role='user', content='Explicit evidence', metadata={
        'ephemeral': True, marker: True, 'contextAnchorToolCalls': ['acquire']})
    prepared = original.model_copy(update={'messages': [*messages, observation]})
    anchored = anchor_observations(original, prepared)
    assert anchored.messages == ([first, calls, result, parallel, observation, *later]
                                 if complete else [*messages, observation])
    # A later unrelated batch cannot move acquired evidence to the new tail.
    if complete:
        extended = original.model_copy(update={'messages': [*messages,
            Message(role='assistant', content='Another continuation.')]})
        again = anchor_observations(extended, extended.model_copy(
            update={'messages': [*extended.messages, observation]}))
        assert again.messages.index(observation) == 4


def test_focus_without_receipt_stays_request_time_and_user_quotation_never_moves():
    first = Message(role='user', content='Accepted ordinary input.')
    quoted = Message(role='user', content='Live surface observations (untrusted data): quoted')
    assistant = Message(role='assistant', content='Prior reasoning.')
    original = request([first, quoted, assistant])
    focus = Message(role='user', content='Focused pixels', metadata={
        'ephemeral': True, 'surfaceObservation': True, 'contextAnchorToolCalls': []})
    prepared = original.model_copy(update={'messages': [*original.messages, focus]})
    assert anchor_observations(original, prepared).messages == prepared.messages


@pytest.mark.parametrize('history_size', [4, 1000])
def test_ambient_placement_borrows_history_without_visiting_tool_batches(monkeypatch, history_size):
    import amplifier_web.preparation_context as preparation
    monkeypatch.setattr(preparation, '_settled_batch_anchors',
                        lambda *_: pytest.fail('Ambient context must not inspect tool batches.'))
    messages = [Message(role='user', content='Accepted ordinary input.'),
                *[Message(role='assistant', content='large historical body' * 100)
                  for _ in range(history_size)]]
    original = request(messages)
    observation = Message(role='user', content='Fresh ambient', metadata={
        'ephemeral': True, 'coordinationObservation': True})
    prepared = original.model_copy(update={'messages': [*messages, observation]})
    anchored = anchor_observations(original, prepared)
    assert anchored.messages[1] is observation
    assert all(left is right for left, right in zip(anchored.messages[2:], messages[1:]))
    assert len(anchored.messages) == len(messages) + 1


async def test_revocation_after_fit_refuses_without_transport_and_refits_only_reduced_context():
    current = CurrentContext()
    current.required = {
        'status': 'available', 'reason': None, 'version': 'c' * 64,
        'rootSessionId': 'owner', 'generationId': 'generation', 'inputIds': ['input'],
        'associationRequestIds': ['request'], 'workCount': 0, 'requirementCount': 0,
        'requirementsRef': {'action': 'workspace.work.invocation',
                            'args': {'sessionId': 'owner', 'expectedVersion': 'c' * 64}},
        'effectAuthorized': False,
        'coverage': {'complete': True, 'supportedInputIds': ['input'], 'unsupportedInputs': []},
    }
    raw = RecordingProvider()
    delivery = current.delivery(origin_scope=lambda: {
        'rootSessionId': 'owner', 'runtimeSessionId': 'owner', 'generationId': 'generation',
        'inputIds': ['input'], 'inputCoverage': 'complete', 'stopRevision': 0,
    })
    wrapped = SurfaceProvider(raw, delivery)
    value = request()
    await wrapped.request_budget(value)
    current.memory = {'items': []}
    with pytest.raises(RequestFitChanged, match='content_changed') as refused:
        await wrapped.complete(value)
    assert not raw.sent
    assert await wrapped.refit_request(value, error=refused.value) is not None
    await wrapped.complete(value)
    assert len(raw.measured) == 2 and len(raw.sent) == 1
    assert 'MEMORY_ONE' not in raw.sent[0].model_dump_json()
    assert raw.measured[-1] == raw.sent[-1]
    assert len([name for name, _ in current.calls if name == 'context.prepare']) == 1


@pytest.mark.parametrize('kind', ['surface', 'focus', 'voice', 'output', 'pair'])
async def test_real_visual_producers_anchor_after_acquiring_batch_and_keep_current_prefix(kind):
    image_row = {'id': 'image-one', 'sha256': 'a' * 64}
    surface_row = {
        'surfaceId': 'drawing', 'revision': '1', 'clientId': 'client', 'viewId': 'view',
        'generation': 1, 'editVersion': 1, 'image': {'available': True, 'digest': 'digest'},
    }
    manifest = {'inputIds': ['input-one'],
                'surfaces': [surface_row] if kind in {'surface', 'focus'} else []}
    image = {**{key: value for key, value in surface_row.items() if key != 'image'},
             'representation': 'image', 'digest': 'digest', '_image': 'cGl4ZWxz'}

    async def bridge(operation, args):
        if operation == 'context.prepare':
            return {'context.manifest': {'available': True, 'value': copy.deepcopy(manifest)}}
        if operation == 'context.manifest':
            return copy.deepcopy(manifest)
        if operation == 'context.read':
            return copy.deepcopy(image)
        if operation == 'voice.visual.read':
            return {'id': 'capture', 'capturedAt': 1000, '_image': 'cGl4ZWxz'}
        if operation == 'outputs.image.read':
            return {**image_row, '_image': 'cGl4ZWxz'}
        if operation == 'outputs.images.read':
            return {'images': [{**row, '_image': 'cGl4ZWxz'} for row in args['images']]}
        raise AssertionError(operation)

    context = PreparationContext(bridge)
    surfaces = SurfaceDelivery(context.read)
    surfaces.epoch, surfaces.epoch_available = ['input-one'], True
    surfaces.image_capabilities.supports = AsyncMock(return_value=True)
    tool_input = {'operation': 'context.read', 'args': {'surfaceId': 'drawing', 'representation': 'image'}}
    if kind == 'surface':
        receipt = await surfaces.read(tool_input['args'])
        delivery = surfaces
    elif kind == 'focus':
        await surfaces.interest({'surfaceId': 'drawing', 'requests': 3})
        receipt, delivery = {'focus': True}, surfaces
    elif kind == 'voice':
        delivery = VoiceVisualDelivery(surfaces, bridge)
        receipt = delivery.remember({'accepted': True, 'result': {'id': 'capture', 'callId': 'voice-call'}})
    else:
        delivery = OutputImageDelivery(surfaces, bridge)
        if kind == 'output':
            receipt = delivery.remember_output({'accepted': True, 'result': image_row})
        else:
            selection = [image_row, {'id': 'image-two', 'sha256': 'b' * 64}]
            tool_input = {'operation': 'dispatch', 'args': {
                'action': 'outputs.images', 'args': {'images': selection}}}
            receipt = delivery.remember_pair({'accepted': True, 'result': {'images': selection}}, tool_input)
            delivery.bind_pair({'tool_name': 'app_control', 'tool_call_id': 'acquire',
                               'tool_input': tool_input, 'result': receipt})
    calls = Message(role='assistant', content=[
        {'type': 'tool_call', 'id': 'acquire', 'name': 'app_control', 'input': tool_input},
        {'type': 'tool_call', 'id': 'parallel', 'name': 'app_control', 'input': {}},
    ])
    value = request([Message(role='user', content='Inspect synthetic evidence.'), calls,
        Message(role='tool', name='app_control', tool_call_id='acquire', content=json.dumps(receipt)),
        Message(role='tool', name='app_control', tool_call_id='parallel', content='Other settled result.'),
        Message(role='assistant', content='An unrelated continuation.')])
    raw = RecordingProvider()
    wrapped = SurfaceProvider(raw, context.wrap(delivery))
    for number in (1, 2):
        await wrapped.request_budget(value)
        reply = await wrapped.complete(value)
        sent = raw.sent[-1]
        evidence = next(message for message in sent.messages if isinstance(message.content, list)
                        and any(block.type == 'image' for block in message.content))
        assert evidence.metadata['contextAnchorToolCalls'] == ([] if kind == 'focus' else ['acquire'])
        if kind == 'focus':
            assert sent.messages[-1] is not sent.messages[4]
            assert sent.messages[-1] == evidence  # No invented acquisition chronology.
        else:
            assert sent.messages[4] == evidence
            if number == 2:
                assert visible(sent.messages[:len(raw.sent[0].messages)]) == visible(raw.sent[0].messages)
        assert raw.measured[-1] == sent
        value = continue_request(value, reply, number)


@pytest.mark.parametrize('reset', ['compaction', 'resume', 'provider_switch'])
async def test_compacted_resumed_and_other_provider_requests_do_not_inherit_old_observations(reset):
    current = CurrentContext()
    first, second = RecordingProvider(), RecordingProvider()
    wrapped = SurfaceProvider(first, current.delivery())
    value = request()
    await wrapped.request_budget(value)
    reply = await wrapped.complete(value)
    value = continue_request(value, reply, 1)
    current.version = 2
    current.memory = {'items': []}
    current.surfaces = False
    if reset == 'compaction':
        value = request([Message(role='user', content='Synthetic compacted task summary.')])
    # Resume constructs a fresh delivery ledger from canonical history. A provider
    # switch shares the live current-context owner, not the old wire request.
    delivery = current.delivery() if reset == 'resume' else wrapped.delivery
    wrapped = SurfaceProvider(second, delivery)
    before = value.model_copy(deep=True)
    await wrapped.request_budget(value)
    await wrapped.complete(value)
    text = second.sent[0].model_dump_json()
    assert 'CURRENT_REFERENCE_2' in text and 'CURRENT_REFERENCE_1' not in text
    assert 'MEMORY_ONE' not in text and 'SURFACE_1' not in text
    assert value == before
    assert second.measured == second.sent
    if reset != 'compaction':
        assistant = next(m for m in second.sent[0].messages if m.role == 'assistant')
        assert assistant.content[0].signature == 'synthetic-signature-1'


def anthropic_fixture(monkeypatch, *, streaming):
    # The ordinary host lock need not install every user's provider. The
    # combined native qualification explicitly installs its exact candidate.
    module = pytest.importorskip('amplifier_module_provider_anthropic',
                                 reason='Native provider integration requires selected Anthropic source.')
    from anthropic.types import Message as NativeMessage
    provider = module.AnthropicProvider(api_key='synthetic-not-a-key', config={
        'default_model': 'claude-fable-5-2', 'use_streaming': streaming,
        'max_retries': 0, 'enable_prompt_caching': False,
    })
    # Same cached native model lookup consumed by production budgeting for
    # models whose published window is newer than the provider's static table.
    provider._runtime_model_info_cache['claude-fable-5-2'] = module._RuntimeModelInfo(
        max_input_tokens=1_000_000, max_tokens=128_000)
    measured, sent = [], []

    def native_response(number):
        return NativeMessage(id=f'response-{number}', model='claude-fable-5-2',
            role='assistant', type='message', stop_reason='tool_use',
            usage={'input_tokens': 100, 'output_tokens': 10},
            content=[
                {'type': 'thinking', 'thinking': f'Synthetic reasoning {number}',
                 'signature': f'synthetic-signature-{number}'},
                {'type': 'tool_use', 'id': f'call-{number}', 'name': 'app_control',
                 'input': {'operation': 'get_state'}},
            ])

    async def count_tokens(**params):
        measured.append(copy.deepcopy(params))
        return SimpleNamespace(input_tokens=100)

    async def create(**params):
        sent.append(copy.deepcopy(params))
        return SimpleNamespace(parse=AsyncMock(return_value=native_response(len(sent))), headers={})

    class Stream:
        def __init__(self, params):
            sent.append(copy.deepcopy(params))
            self.response = None
            self.message = native_response(len(sent))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def get_final_message(self):
            return self.message

    provider._client = SimpleNamespace(base_url='https://api.anthropic.com', default_headers={},
        messages=SimpleNamespace(count_tokens=count_tokens,
            with_raw_response=SimpleNamespace(create=create),
            stream=lambda **params: Stream(params)))
    monkeypatch.setattr(provider, '_get_request_capabilities', AsyncMock(
        return_value=provider._get_capabilities('claude-fable-5-2')))
    return provider, measured, sent


@pytest.mark.parametrize('streaming', [False, True])
async def test_native_anthropic_selective_binding_survives_actual_host_wrapper_requests(monkeypatch, streaming):
    provider, measured, sent = anthropic_fixture(monkeypatch, streaming=streaming)
    current = CurrentContext()
    wrapped = SurfaceProvider(provider, current.delivery())
    value = request()
    versions = (1, 1, 1, 2, 2, 2)
    for number, version in enumerate(versions, 1):
        current.version = version
        if version == 2:
            current.memory, current.surfaces = {'items': []}, False
        before = value.model_copy(deep=True)
        decision = await wrapped.request_budget(value, context_estimate=100)
        assert decision is not None
        reply = await wrapped.complete(value)
        assert value == before
        for key in ('model', 'system', 'messages', 'tools', 'thinking', 'extra_body', 'extra_headers'):
            assert measured[-1].get(key) == sent[-1].get(key), key
        # Native server-selective control, NOT host blanket removal of reasoning.
        assert sent[-1]['thinking']['block_binding'] == {'prefix_mismatch_behavior': 'drop_block'}
        assert 'thinking-binding-controls-2026-08-01' in sent[-1]['extra_headers']['anthropic-beta']
        if number > 1:
            previous = sent[-2]['messages']
            if version == versions[number - 2]:
                assert sent[-1]['messages'][:len(previous)] == previous
            else:
                assert sent[-1]['messages'][:len(previous)] != previous
        value = continue_request(value, reply, number)
    assert len(sent) == len(measured) == len(versions)
    last = json.dumps(sent[-1]['messages'])
    assert 'CURRENT_REFERENCE_2' in last and 'CURRENT_REFERENCE_1' not in last
    assert 'MEMORY_ONE' not in last and 'SURFACE_' not in last
    assert 'synthetic-signature-1' in last and 'synthetic-signature-2' in last
    assert '"tool_use_id": "call-1"' in last and '"tool_use_id": "call-2"' in last