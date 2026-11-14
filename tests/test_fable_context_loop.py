"""Offline exact provider/host/native-loop fitting, never live vendor evidence."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import amplifier_module_context_managed as managed
import amplifier_module_loop_streaming as streaming
from amplifier_core import HookResult, ToolResult
from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle

from amplifier_web.context_continuity import digest
from amplifier_web.host.session import SelectedProvider
from amplifier_web.surface_delivery import SurfaceProvider
from test_provider_context_continuation import CurrentContext


MODEL = 'claude-fable-5-2'
LIMIT = 1_000_000
FILLER = 'synthetic-sized-evidence-' * 1000
STEPS = 12
INPUT_FIELDS = {'model', 'messages', 'system', 'tools', 'tool_choice', 'thinking'}


def projection(body):
    return {key: value for key, value in body.items() if key in INPUT_FIELDS}


class Wire:
    """Small records calibrated to cross 1M, counting the complete input wire."""
    def __init__(self, http):
        self.http = http
        self.counted = {}
        self.counts, self.sent, self.summaries, self.effects, self.events = [], [], [], [], []
        self.metadata_calls = 0
        self.first_count_cold = None

    @staticmethod
    def tokens(body):
        text = json.dumps(projection(body), sort_keys=True)
        # Synthetic token scale; includes fixed overhead, tools and system.
        return 40_000 + len(text.encode()) // 4 + text.count(FILLER) * 168_000

    async def transport(self, request):
        if request.method == 'GET':
            self.metadata_calls += 1
            return self.http.Response(404, json={
                'type': 'error', 'error': {'type': 'not_found_error', 'message': 'No model metadata'}})
        body = json.loads(request.content)
        assert body['model'] == MODEL
        key, count = digest(projection(body)), self.tokens(body)
        if request.url.path.endswith('/count_tokens'):
            if self.first_count_cold is None:
                self.first_count_cold = self.metadata_calls == 0
            self.counts.append(count)
            self.counted[key] = count
            return self.http.Response(200, json={'input_tokens': count})
        assert request.url.path.endswith('/messages') and not body.get('stream')
        assert key in self.counted, 'Provider dispatch bypassed its authoritative count'
        assert self.counted[key] == count
        assert count + 4096 <= LIMIT, 'Oversized input reached dispatch'
        text = json.dumps(body)
        if 'Next ordered conversation data. Incorporate this evidence' in text:
            self.summaries.append(count)
            facts = sorted(set(re.findall(r'RECEIPT-\d+', text)))
            content = [{'type': 'text', 'text': 'ORBIT. Never replay. Completed: ' + ', '.join(facts)}]
            stop = 'end_turn'
        else:
            self.sent.append(count)
            for marker in ('ORBIT', 'CURRENT_REFERENCE_1', 'MEMORY_ONE', 'SURFACE_1'):
                assert marker in text
            for index in self.effects:
                assert f'RECEIPT-{index}' in text
            assert body['max_tokens'] == 1000
            if len(self.effects) < STEPS:
                index = len(self.effects) + 1
                content = [{'type': 'tool_use', 'id': f'call-{index}', 'name': 'record',
                            'input': {'index': index}}]
                stop = 'tool_use'
            else:
                content, stop = [{'type': 'text', 'text': 'ORBIT complete.'}], 'end_turn'
        return self.http.Response(200, json={
            'id': f'message-{len(self.sent)}-{len(self.summaries)}',
            'type': 'message', 'role': 'assistant', 'model': MODEL, 'content': content,
            'stop_reason': stop, 'stop_sequence': None,
            # Include cache-read input separately, as real Anthropic usage does.
            'usage': {'input_tokens': 100, 'cache_read_input_tokens': max(0, count - 100),
                      'output_tokens': 30}})


async def test_cold_nondefault_fable_compacts_inside_one_ordinary_tool_turn():
    module = pytest.importorskip('amplifier_module_provider_anthropic',
        reason='Native qualification installs exact accepted Anthropic source.')
    from anthropic import _base_client
    http = getattr(_base_client, 'httpx2', None) or _base_client.httpx
    wire = Wire(http)
    plan = {'session': {
        'orchestrator': {'module': 'loop-streaming', 'config': {
            'use_streaming': False, 'min_delay_between_calls_ms': 0,
            'ephemeral_injection_mode': 'tail'}},
        'context': {'module': 'context-managed', 'config': {
            'max_tokens': LIMIT, 'summarize_trigger': .99,
            'compaction_notice_enabled': False, 'archive_recovery': False}}}}
    paths = {'loop-streaming': Path(streaming.__file__).parent,
             'context-managed': Path(managed.__file__).parent}
    session = await PreparedBundle(plan, BundleModuleResolver(paths),
        Bundle(name='fable-context-loop', session=plan['session'])).create_session(
            session_id='fable-context-loop')
    sdk = module.AsyncAnthropic(api_key='synthetic-not-live', max_retries=0,
        http_client=http.AsyncClient(transport=http.MockTransport(wire.transport)))
    coordinator = session.coordinator
    context = coordinator.get('context')
    raw = module.AnthropicProvider(client=sdk, coordinator=coordinator, config={
        'default_model': 'claude-sonnet-5', 'enable_1m_context': True,
        'use_streaming': False, 'max_retries': 0, 'enable_prompt_caching': False})
    surface = SurfaceProvider(raw, CurrentContext().delivery(), coordinator=coordinator)
    selected = SelectedProvider(raw, {'model': MODEL, 'max_output_tokens': 1000},
                                lambda _: surface)
    await coordinator.mount('providers', selected, name='anthropic')
    coordinator.get('orchestrator').root_provider = selected

    class Record:
        name, description = 'record', 'Record one completed synthetic outcome'
        input_schema = {'type': 'object', 'properties': {'index': {'type': 'integer'}},
                        'required': ['index']}

        async def execute(self, arguments):
            index = arguments['index']
            assert index == len(wire.effects) + 1, 'Repeated or reordered operation'
            wire.effects.append(index)
            return ToolResult(success=True, output={
                'receipt': f'RECEIPT-{index}', 'outcome': 'completed', 'filler': FILLER})

    async def observe(event, data):
        wire.events.append(dict(data))
        return HookResult()

    async def instructions():
        return 'Preserve ORBIT and every completed outcome. Never replay operations.'

    await coordinator.mount('tools', Record(), name='record')
    await context.set_system_prompt_factory(instructions)
    coordinator.hooks.register('context:compaction_finished', observe, name='fable-compaction-witness')
    prompt = 'Finish ORBIT. Keep originals and completed receipts; never replay.'
    try:
        assert raw._runtime_model_info_cache == {}
        result = await session.execute(prompt)
        rows = await context.get_messages()
        assert 'ORBIT complete' in result
        assert wire.first_count_cold is True
        assert not any(raw._runtime_model_info_cache.values())
        assert max(wire.counts) >= LIMIT
        assert max(wire.sent) + 4096 <= LIMIT
        assert len(wire.sent) == STEPS + 1
        assert wire.summaries and context.summary is not None
        assert any(event.get('outcome') == 'completed' for event in wire.events)
        assert wire.effects == list(range(1, STEPS + 1))
        assert [row['content'] for row in rows if row['role'] == 'user'
                and not (row.get('metadata') or {}).get('ephemeral')] == [prompt]
        tools = [row for row in rows if row['role'] == 'tool']
        assert [row['tool_call_id'] for row in tools] == [f'call-{i}' for i in wire.effects]
        for index, row in enumerate(tools, 1):
            assert json.loads(row['content']) == {
                'receipt': f'RECEIPT-{index}', 'outcome': 'completed', 'filler': FILLER}
    finally:
        await sdk.close()
        await session.cleanup()