"""Actual Core/Streaming preparation; synthetic model and retained visual data."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_core import ProviderInfo, ToolResult
from amplifier_core.message_models import ChatResponse, Message, TextBlock, ToolCall, Usage
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver

from amplifier_web.coordination_delivery import CoordinationDelivery
from amplifier_web.surface_delivery import SurfaceProvider
from amplifier_web.voice_visual_delivery import VoiceVisualDelivery
from test_coordination_delivery import Previous, available_requirements, empty_discovery


async def native_case(mode='stale_visual', max_iterations=-1, legacy_budget=False):
    plan = {'session': {'orchestrator': {'module': 'loop-streaming', 'config': {
        'use_streaming': False, 'extended_thinking': True,
        'min_delay_between_calls_ms': 0, 'max_iterations': max_iterations,
        'ephemeral_injection_mode': 'tail'}}, 'context': {'module': 'context-simple'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-streaming', 'context-simple')}
    session = await PreparedBundle(plan, BundleModuleResolver(paths),
        Bundle(name='refit-fixture', session=plan['session'])).create_session(session_id='refit-fixture')
    scope = {'rootSessionId': session.session_id, 'runtimeSessionId': session.session_id,
             'generationId': 'generation-1', 'inputIds': ['original-input'],
             'inputCoverage': 'complete', 'stopRevision': 0}
    required = available_requirements()
    required.update(rootSessionId=session.session_id, generationId=scope['generationId'],
                    inputIds=scope['inputIds'])
    required['coverage']['supportedInputIds'] = scope['inputIds']
    required['requirementsRef']['args']['sessionId'] = session.session_id
    state = SimpleNamespace(stale=False, measurements=[], transports=[], tools=0, preparations=0,
                            budget_options=[], dispatch_options=[])

    class Surfaces(Previous):
        epoch = ['original-input']
        image_capabilities = SimpleNamespace(supports=AsyncMock(return_value=True))

    async def bridge(operation, args):
        if operation == 'coordination.current':
            state.preparations += 1
            if mode == 'scope_during_refit' and state.preparations == 3:
                scope['generationId'] = 'changed-generation'
            return {'work': {'available': True, 'discovery': empty_discovery(), 'required': required}}
        if operation == 'work.invocation.revalidate':
            if mode == 'authority_unavailable' and len(state.measurements) > 1:
                raise TimeoutError('Synthetic unavailable authority')
            if mode == 'currentness_changed' and len(state.measurements) > 1:
                return {'current': False, 'effectAuthorized': False, 'required': required}
            return {'current': True, 'effectAuthorized': False, 'required': required}
        if operation == 'voice.visual.read':
            if state.stale:
                raise ValueError('Synthetic observation revoked')
            return {'_image': 'c3ludGhldGlj', 'source': 'Synthetic capture'}
        raise AssertionError(operation)

    work = CoordinationDelivery(Surfaces(), bridge, origin_scope=lambda: copy.deepcopy(scope))
    visual = VoiceVisualDelivery(work, bridge)

    class Tool:
        name = 'app_control'
        description = 'Read a synthetic observation once'
        input_schema = {'type': 'object', 'properties': {}}

        async def execute(self, arguments):
            state.tools += 1
            return ToolResult(success=True, output=visual.remember({
                'accepted': True, 'result': {'id': 'capture-1', 'callId': 'visual-1'}}))

    class Provider:
        name = 'fixture'

        def get_info(self):
            return ProviderInfo(id=self.name, display_name='Fixture',
                                capabilities=['tools'], defaults={'model': 'fixture'})

        def parse_tool_calls(self, response):
            return response.tool_calls or []

        async def request_budget(self, request, *, context_estimate=0, request_options=None, **kwargs):
            state.measurements.append(request.model_dump_json())
            state.budget_options.append(copy.deepcopy(request_options))
            # Permission changes after the first measurement containing pixels,
            # before the provider's transport entrypoint is reached.
            if any(isinstance(m.content, list) and any(b.type == 'image' for b in m.content)
                   for m in request.messages):
                if mode not in {'authority_unavailable', 'currentness_changed', 'provider_error', 'provider_typed_error', 'binding_missing'}:
                    state.stale = True
                if mode == 'authority_changed':
                    required['version'] = 'changed-authority'
                elif mode == 'stop':
                    scope['stopRevision'] += 1
                elif mode == 'new_generation':
                    scope['generationId'] = 'changed-generation'
                elif mode == 'new_input':
                    scope['inputIds'] = ['original-input', 'later-input']
                elif mode == 'binding_missing' and len(state.measurements) == 2:
                    work.requests.clear()
            if len(state.measurements) == 3 and mode == 'repeated_change':
                request.messages.append(Message(role='user', content='changed after refit'))
            return {'estimated_input_tokens': 10001 if mode == 'oversized_refit' and len(state.measurements) == 3 else 100,
                    'input_limit_tokens': 10000,
                    'context_token_budget': context_estimate,
                    'measurement': {'kind': 'provider_count', 'input_tokens': 100,
                                    'source': 'fixture'}}

        async def complete(self, request, **kwargs):
            state.transports.append(request.model_dump_json())
            state.dispatch_options.append(copy.deepcopy(kwargs))
            if len(state.transports) == 2:
                if mode == 'provider_error':
                    raise ValueError('Work request bytes changed after fitting; refit before transport.')
                if mode == 'provider_typed_error':
                    from amplifier_web.request_refit import RequestFitChanged
                    raise RequestFitChanged('content_changed')
            return ChatResponse(content=[] if len(state.transports) == 1 else [TextBlock(text='Done')],
                tool_calls=[ToolCall(id='read-once', name='app_control', arguments={})]
                    if len(state.transports) == 1 else [],
                usage=Usage(input_tokens=100, output_tokens=1, total_tokens=101))

    if legacy_budget:
        named_budget = Provider.request_budget
        async def legacy(self, request, **kwargs):
            assert 'request_options' not in kwargs
            return await named_budget(self, request, **kwargs)
        Provider.request_budget = legacy
    await session.coordinator.mount('tools', Tool(), name='app_control')
    await session.coordinator.mount('providers', SurfaceProvider(Provider(), visual), name='fixture')
    return session, state


@pytest.mark.parametrize('mode', ['stale_visual', 'binding_missing'])
@pytest.mark.parametrize('max_iterations', [-1, 1])
async def test_actual_loop_refits_without_repeating_completed_tool(mode, max_iterations):
    session, state = await native_case(mode, max_iterations)
    try:
        assert await session.execute('Read once, then finish using the remaining valid evidence.') == 'Done'
        assert state.tools == 1
        assert len(state.measurements) == 3
        assert len(state.transports) == 2
        assert state.budget_options == [{'extended_thinking': True}] * 3
        assert state.dispatch_options == [{'extended_thinking': True}] * 2
        assert state.transports[1] == state.measurements[2]
        assert 'c3ludGhldGlj' in state.measurements[1]
        if mode == 'stale_visual':
            assert 'c3ludGhldGlj' not in state.transports[1]
        history = await session.coordinator.get('context').get_messages()
        assert sum(m.get('role') == 'tool' for m in history) == 1
        assert sum(m.get('role') == 'user' and m.get('content') ==
                   'Read once, then finish using the remaining valid evidence.' for m in history) == 1
        # The capped loop adds its existing finalization instruction once.
        assert sum(m.get('role') == 'user' for m in history) == (2 if max_iterations == 1 else 1)
    finally:
        await session.cleanup()


@pytest.mark.parametrize('mode, measurements, transports, reason', [
    ('authority_changed', 2, 1, 'authority_changed'),
    ('authority_unavailable', 2, 1, 'authority_unavailable'),
    ('currentness_changed', 2, 1, 'authority_changed'),
    ('stop', 2, 1, 'native_scope_changed'),
    ('new_generation', 2, 1, 'native_scope_changed'),
    ('new_input', 2, 1, 'native_scope_changed'),
    ('scope_during_refit', 2, 1, 'native_scope_changed'),
    ('repeated_change', 3, 1, 'content_changed'),
    ('oversized_refit', 3, 1, 'content_changed'),
    ('provider_error', 2, 2, 'bytes changed'),
    ('provider_typed_error', 2, 2, 'content_changed'),
])
async def test_actual_loop_does_not_replay_changed_authority_or_sent_request(mode, measurements, transports, reason):
    session, state = await native_case(mode)
    try:
        with pytest.raises(RuntimeError, match=reason):
            await session.execute('Read once, then finish using the remaining valid evidence.')
        assert state.tools == 1
        assert len(state.measurements) == measurements
        assert len(state.transports) == transports
        history = await session.coordinator.get('context').get_messages()
        assert sum(m.get('role') == 'tool' for m in history) == 1
        assert sum(m.get('role') == 'user' for m in history) == 1
    finally:
        await session.cleanup()


def test_refusal_diagnostic_names_components_without_request_contents():
    from amplifier_web.request_refit import RequestFitChanged, changed_components
    before = json.dumps({'messages': ['private prompt and pixels'], 'tools': ['private schema']})
    after = json.dumps({'messages': ['different private pixels'], 'tools': ['private schema']})
    error = RequestFitChanged('content_changed', components=changed_components(before, after))
    assert error.components == ('messages',)
    assert str(error) == 'Work request refused before transport: content_changed (messages); refit before transport.'


async def test_actual_loop_legacy_budget_keeps_its_kwargs_shape_during_refit():
    session, state = await native_case(legacy_budget=True)
    try:
        assert await session.execute('Read once, then finish.') == 'Done'
        assert state.tools == 1
        assert state.budget_options == [None] * 3
        assert state.dispatch_options == [{'extended_thinking': True}] * 2
        assert state.transports[-1] == state.measurements[-1]
    finally:
        await session.cleanup()
