"""Offline exact provider/host/native-loop fitting, never live vendor evidence."""
import hashlib
import json
import re
from pathlib import Path

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
    def __init__(self, http, *, rejections=0):
        self.http = http
        self.counted = {}
        self.counts, self.sent, self.summaries, self.effects, self.events = [], [], [], [], []
        self.metadata_calls = 0
        self.first_count_cold = None
        self.rejections = rejections
        self.refusals = []
        self.on_refusal = None
        self.canonical = None
        self.receipt_snapshots = {}
        self.violations = []
        # Reactive fitting converts a public-context budget from server feedback.
        # Keep that case's physical text/token ratio realistic (about 4 MB total),
        # rather than mixing the proactive case's synthetic 28x token scale.
        self.filler = FILLER * 24 if rejections else FILLER

    def tokens(self, body):
        text = json.dumps(projection(body), sort_keys=True)
        # Synthetic token scale; includes fixed overhead, tools and system.
        scaled = 0 if self.filler != FILLER else text.count(FILLER) * 168_000
        return 40_000 + len(text.encode()) // 4 + scaled

    async def transport(self, request):
        if request.method == 'GET':
            self.metadata_calls += 1
            return self.http.Response(404, json={
                'type': 'error', 'error': {'type': 'not_found_error', 'message': 'No model metadata'}})
        body = json.loads(request.content)
        assert body['model'] == MODEL
        key, count = digest(projection(body)), self.tokens(body)
        if request.url.path.endswith('/count_tokens'):
            # Count is the next request boundary after native tool persistence,
            # before compaction can derive a smaller working view. Preserve the
            # actual saved row/envelope, not an assumed serialization shape.
            if self.canonical:
                for row in await self.canonical():
                    if row['role'] == 'tool':
                        saved = (digest(row), hashlib.sha256(row['content'].encode()).hexdigest())
                        previous = self.receipt_snapshots.setdefault(row['tool_call_id'], saved)
                        # Provider deliberately converts counter exceptions to
                        # unavailable; record outside that catch boundary too.
                        if saved != previous:
                            self.violations.append(row['tool_call_id'])
            if self.first_count_cold is None:
                self.first_count_cold = self.metadata_calls == 0
            # Reactive cases model a stale server count, not a host bypass.
            measured = count // 2 if self.rejections and not self.refusals and len(self.effects) >= 6 else count
            self.counts.append(measured)
            self.counted[key] = measured
            return self.http.Response(200, json={'input_tokens': measured})
        assert request.url.path.endswith('/messages') and not body.get('stream')
        assert key in self.counted, 'Provider dispatch bypassed its authoritative count'
        text = json.dumps(body)
        summary = 'Next ordered conversation data. Incorporate this evidence' in text
        if not summary and self.rejections and (len(self.effects) >= 6):
            self.rejections -= 1
            self.refusals.append((key, len(self.effects)))
            if self.on_refusal:
                self.on_refusal()
            return self.http.Response(400, headers={'request-id': f'overflow-{len(self.refusals)}'},
                json={'type': 'error', 'error': {'type': 'invalid_request_error',
                    'message': f'prompt is too long: {max(count, LIMIT + 5207)} tokens > 1000000 maximum'}})
        assert self.counted[key] == count
        assert count + 4096 <= LIMIT, 'Oversized input reached dispatch'
        if summary:
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


@pytest.mark.parametrize('rejections,stop,seed_kind', [
    (0, False, None), (1, False, None), (2, False, None), (1, True, None),
    (0, False, 'resume'), (0, False, 'oversized-tail')],
    ids=['proactive', 'reactive', 'second-overflow-terminal', 'stop-before-retry',
         'resume-over-1m-signed-history', 'oversized-retained-tail'])
async def test_cold_nondefault_fable_compacts_inside_one_ordinary_tool_turn(rejections, stop, seed_kind):
    module = pytest.importorskip('amplifier_module_provider_anthropic',
        reason='Native qualification installs exact accepted Anthropic source.')
    from anthropic import _base_client
    http = getattr(_base_client, 'httpx2', None) or _base_client.httpx
    wire = Wire(http, rejections=rejections)
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
    wire.canonical = context.get_messages
    raw = module.AnthropicProvider(api_key='synthetic-not-live', coordinator=coordinator, config={
        'default_model': 'claude-sonnet-5', 'enable_1m_context': True,
        'use_streaming': False, 'max_retries': 0, 'enable_prompt_caching': False})
    raw._client = sdk
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
                'receipt': f'RECEIPT-{index}', 'outcome': 'completed', 'filler': wire.filler})

    class AppControl:
        # Host observation delivery is explicitly gated on the mounted app tool.
        name, description = 'app_control', 'Synthetic host observation surface'
        input_schema = {'type': 'object'}

        async def execute(self, arguments):
            raise AssertionError('The fixture must not invoke unrelated host tools')

    async def observe(event, data):
        wire.events.append(dict(data))
        return HookResult()

    async def instructions():
        return 'Preserve ORBIT and every completed outcome. Never replay operations.'

    await coordinator.mount('tools', Record(), name='record')
    await coordinator.mount('tools', AppControl(), name='app_control')
    await context.set_system_prompt_factory(instructions)
    coordinator.hooks.register('context:compaction_finished', observe, name='fable-compaction-witness')
    coordinator.hooks.register('orchestrator:provider_overflow_recovery', observe, name='fable-overflow-witness')
    if stop:
        wire.on_refusal = lambda: coordinator.cancellation.request_cancellation()
    prompt = 'Finish ORBIT. Keep originals and completed receipts; never replay.'
    seed = []
    if seed_kind == 'resume':
        seed = [{'role': 'user', 'content': 'Accepted ORBIT task; preserve every original receipt.'}]
        for index in range(1, STEPS + 1):
            seed.extend([
                {'role': 'assistant', 'content': [
                    {'type': 'thinking', 'thinking': f'Saved reasoning {index}.',
                     'signature': f'synthetic-signed-{index}'},
                    {'type': 'tool_call', 'id': f'call-{index}', 'name': 'record',
                     'input': {'index': index}}]},
                {'role': 'tool', 'name': 'record', 'tool_call_id': f'call-{index}',
                 'content': json.dumps({'receipt': f'RECEIPT-{index}', 'outcome': 'completed',
                                       'filler': FILLER})}])
        # Ordinary saved conversations have more than one accepted input. The
        # existing boundary retains the latest two; make the older tool prefix
        # eligible without changing its retention policy for this test.
        seed.extend([{'role': 'user', 'content': 'Accepted correction: retain ORBIT.'},
                     {'role': 'assistant', 'content': 'Correction acknowledged.'}])
        await context.set_messages(seed)
        wire.effects = list(range(1, STEPS + 1))
    elif seed_kind == 'oversized-tail':
        prompt += FILLER * 8
    seed_hashes = [digest(row) for row in seed]
    try:
        assert raw._runtime_model_info_cache == {}
        if seed_kind == 'oversized-tail':
            with pytest.raises(Exception, match='(?i)(context|budget|fit)'):
                await session.execute(prompt)
            rows = await context.get_messages()
            assert not wire.sent and not wire.summaries and not wire.effects
            assert prompt in str(rows)
            assert context.summary is None
            return
        if rejections > 1 or stop:
            with pytest.raises(Exception):
                await session.execute(prompt)
            assert len(wire.refusals) == (1 if stop else 2)
            assert wire.effects == list(range(1, 7))
            assert len(wire.sent) == 6  # Rejected iterations produce no tool.
            tools = [row for row in await context.get_messages() if row['role'] == 'tool']
            assert [row['tool_call_id'] for row in tools] == [f'call-{i}' for i in range(1, 7)]
            for row in tools:
                assert wire.receipt_snapshots[row['tool_call_id']] == (
                    digest(row), hashlib.sha256(row['content'].encode()).hexdigest())
            return
        result = await session.execute(prompt)
        rows = await context.get_messages()
        assert 'ORBIT complete' in result
        assert wire.first_count_cold is True
        assert not any(raw._runtime_model_info_cache.values())
        assert wire.refusals if rejections else max(wire.counts) >= LIMIT
        assert max(wire.sent) + 4096 <= LIMIT
        assert len(wire.sent) == (1 if seed else STEPS + 1)
        assert wire.summaries and context.summary is not None
        assert any(event.get('outcome') == 'completed' for event in wire.events)
        assert wire.effects == list(range(1, STEPS + 1))
        assert [row['content'] for row in rows if row['role'] == 'user'
                and not (row.get('metadata') or {}).get('ephemeral')] == (
                    [seed[0]['content'], seed[-2]['content'], prompt] if seed else [prompt])
        assert [digest(row) for row in rows[:len(seed)]] == seed_hashes
        tools = [row for row in rows if row['role'] == 'tool']
        assert [row['tool_call_id'] for row in tools] == [f'call-{i}' for i in wire.effects]
        for index, row in enumerate(tools, 1):
            assert wire.receipt_snapshots[row['tool_call_id']] == (
                digest(row), hashlib.sha256(row['content'].encode()).hexdigest())
            receipt = json.loads(row['content'])
            # Core's no-op hook currently returns an equal copy, which the
            # native loop persists as the whole ToolResult envelope. That
            # existing protocol is separate; preserve its full saved bytes
            # above and inspect the output only to verify the receipt facts.
            if 'success' in receipt:
                assert receipt['success'] is True and receipt['error'] is None
                receipt = receipt['output']
            assert receipt == {
                'receipt': f'RECEIPT-{index}', 'outcome': 'completed', 'filler': wire.filler}
    finally:
        await sdk.close()
        await session.cleanup()
        assert wire.violations == [], 'Canonical receipt changed during preparation'


class OpaqueHistoryWire:
    """Unavailable counter, real SDK overflow, then distinguishable summary/retry."""

    private_marker = 'SYNTHETIC_PRIVATE_THINKING'
    signature_marker = 'synthetic-opaque-signature'
    summary_marker = 'OPAQUE_PREFIX_SUMMARY'
    final_marker = 'OPAQUE_RETRY_COMPLETE'

    def __init__(self, http, control):
        self.http, self.control = http, control
        self.timeline, self.foreground, self.summaries = [], [], []
        self.counted = {}
        self.context = None
        self.seed_hashes = []
        self.violations = []
        self.initial_pressure = None

    @staticmethod
    def tokens(body):
        # Physical synthetic wire size, including instructions, tools and opaque
        # thinking. Unlike Wire's proactive scale, no inflated token multiplier.
        return 40_000 + len(json.dumps(projection(body), sort_keys=True).encode()) // 4

    async def transport(self, request):
        if request.method == 'GET':
            return self.http.Response(404, json={
                'type': 'error', 'error': {'type': 'not_found_error', 'message': 'No model metadata'}})
        rows = await self.context.get_messages()
        if [digest(row) for row in rows[:len(self.seed_hashes)]] != self.seed_hashes:
            # Counter exceptions can be converted to None by the real provider.
            # Keep violations outside that catch boundary, as in Wire above.
            self.violations.append('canonical prefix changed')
        body = json.loads(request.content)
        if body.get('model') != MODEL:
            self.violations.append('counter or dispatch lost the selected model')
        text = json.dumps(body)
        summary = 'Next ordered conversation data. Incorporate this evidence' in text
        key, count = digest(projection(body)), self.tokens(body)
        record = {'key': key, 'tokens': count, 'summary': summary}
        if request.url.path.endswith('/count_tokens'):
            # Keep the opaque request uncountable after the rejection too.
            # Otherwise a restored >1M count would trigger ordinary compaction
            # and hide the missing provider_overflow force reason.
            available = (bool(self.foreground) and self.control != 'count-unavailable'
                         and self.private_marker not in text and self.signature_marker not in text)
            self.timeline.append({'kind': 'count', 'available': available, **record})
            if not available:
                # Fail at the SDK's HTTP boundary, not by stubbing request_budget.
                return self.http.Response(503, headers={'request-id': 'synthetic-count-unavailable'},
                    json={'type': 'error', 'error': {'type': 'api_error',
                          'message': 'Synthetic token counter temporarily unavailable'}})
            self.counted[key] = count
            return self.http.Response(200, json={'input_tokens': count})
        assert request.url.path.endswith('/messages') and not body.get('stream')
        self.timeline.append({'kind': 'send', **record})
        if summary:
            self.summaries.append(body)
            facts = sorted(set(re.findall(r'RECEIPT-\d+', text)))
            reply = self.summary_marker + '. ORBIT. Completed: ' + ', '.join(facts)
        else:
            self.foreground.append(body)
            if len(self.foreground) == 1:
                self.initial_pressure = dict(self.context.last_budget)
            if len(self.foreground) == 1 or (
                    self.control == 'second-overflow' and len(self.foreground) == 2):
                return self.http.Response(400,
                    headers={'request-id': f'synthetic-opaque-overflow-{len(self.foreground)}'},
                    json={'type': 'error', 'error': {'type': 'invalid_request_error',
                          'message': f'prompt is too long: {max(count, LIMIT + 5207)} tokens > 1000000 maximum'}})
            reply = self.final_marker + '. ORBIT complete.'
        return self.http.Response(200, json={
            'id': f'synthetic-opaque-{len(self.foreground)}-{len(self.summaries)}',
            'type': 'message', 'role': 'assistant', 'model': MODEL,
            'content': [{'type': 'text', 'text': reply}],
            'stop_reason': 'end_turn', 'stop_sequence': None,
            'usage': {'input_tokens': 100, 'cache_read_input_tokens': count - 100,
                      'output_tokens': 30}})


@pytest.mark.parametrize('control', ['recovered', 'count-unavailable', 'second-overflow'])
async def test_opaque_history_overflow_requires_one_different_counted_retry(control, monkeypatch):
    module = pytest.importorskip('amplifier_module_provider_anthropic',
        reason='Native qualification installs exact accepted Anthropic source.')
    from amplifier_core.llm_errors import ContextLengthError
    from anthropic import _base_client
    http = getattr(_base_client, 'httpx2', None) or _base_client.httpx
    wire = OpaqueHistoryWire(http, control)
    plan = {'session': {
        'orchestrator': {'module': 'loop-streaming', 'config': {
            'use_streaming': False, 'min_delay_between_calls_ms': 0,
            'ephemeral_injection_mode': 'tail'}},
        'context': {'module': 'context-managed', 'config': {
            'max_tokens': LIMIT, 'summarize_trigger': .7,
            'compaction_notice_enabled': False, 'archive_recovery': False}}}}
    paths = {'loop-streaming': Path(streaming.__file__).parent,
             'context-managed': Path(managed.__file__).parent}
    session = await PreparedBundle(plan, BundleModuleResolver(paths),
        Bundle(name='opaque-history-loop', session=plan['session'])).create_session(
            session_id='opaque-history-loop')
    sdk = module.AsyncAnthropic(api_key='synthetic-not-live', max_retries=0,
        http_client=http.AsyncClient(transport=http.MockTransport(wire.transport)))
    coordinator = session.coordinator
    context = coordinator.get('context')
    wire.context = context
    raw = module.AnthropicProvider(api_key='synthetic-not-live', coordinator=coordinator, config={
        'default_model': 'claude-sonnet-5', 'enable_1m_context': True,
        'use_streaming': False, 'max_retries': 0, 'enable_prompt_caching': False})
    raw._client = sdk
    surface = SurfaceProvider(raw, CurrentContext().delivery(), coordinator=coordinator)
    selected = SelectedProvider(raw, {'model': MODEL, 'max_output_tokens': 1000},
                                lambda _: surface)
    real_budget = selected.request_budget

    async def record_budget(request, *, request_options=None, **kwargs):
        # Observe, without changing the wrappers' selected request/error binding.
        decision = await real_budget(request, request_options=request_options, **kwargs)
        wire.timeline.append({'kind': 'budget',
            'summary': (request.metadata or {}).get('purpose') == 'context-compaction',
            'decision': json.loads(json.dumps(decision))})
        return decision

    monkeypatch.setattr(selected, 'request_budget', record_budget)
    await coordinator.mount('providers', selected, name='anthropic')
    coordinator.get('orchestrator').root_provider = selected
    effects = []

    class NoReplay:
        description = 'Synthetic fixture; completed operations must not repeat'
        input_schema = {'type': 'object', 'properties': {'index': {'type': 'integer'}}}

        def __init__(self, name):
            self.name = name

        async def execute(self, arguments):
            effects.append((self.name, arguments))
            raise AssertionError('No fixture tool should execute after its saved receipt')

    async def instructions():
        return 'Preserve ORBIT and every completed receipt. Never replay operations.'

    for name in ('record', 'app_control'):
        await coordinator.mount('tools', NoReplay(name), name=name)
    await context.set_system_prompt_factory(instructions)
    seed = [{'role': 'user', 'content': 'Accepted ORBIT task. Preserve the original evidence.'}]
    for index in (1, 2):
        seed.extend([
            {'role': 'assistant', 'content': [
                {'type': 'thinking', 'thinking': f'{wire.private_marker}-{index}. ' * 70_000,
                 'signature': f'{wire.signature_marker}-{index}'},
                {'type': 'text', 'text': f'Public completed evidence {index}.'},
                {'type': 'tool_call', 'id': f'call-{index}', 'name': 'record',
                 'input': {'index': index}}]},
            {'role': 'tool', 'name': 'record', 'tool_call_id': f'call-{index}',
             'content': json.dumps({'receipt': f'RECEIPT-{index}', 'outcome': 'completed',
                                   'filler': FILLER * 2})}])
    # More than two ordinary human boundaries make the complete old tool prefix
    # eligible, while the latest correction and current input remain in the tail.
    seed.extend([
        {'role': 'user', 'content': 'Accepted earlier correction: keep ORBIT.'},
        {'role': 'assistant', 'content': 'Earlier correction acknowledged.'},
        {'role': 'user', 'content': 'LATEST_RETAINED_CORRECTION: preserve completed work.'},
        {'role': 'assistant', 'content': 'Latest correction acknowledged.'}])
    prompt = 'CURRENT_INPUT_ONLY: finish ORBIT without repeating completed operations.'
    try:
        await context.set_messages(seed)
        wire.seed_hashes = [digest(row) for row in await context.get_messages()]
        tool_hashes = {row['tool_call_id']: hashlib.sha256(row['content'].encode()).hexdigest()
                       for row in seed if row['role'] == 'tool'}
        assert raw._runtime_model_info_cache == {}
        if control == 'recovered':
            result = await session.execute(prompt)
            assert wire.final_marker in result and wire.summary_marker not in result
        elif control == 'second-overflow':
            with pytest.raises(ContextLengthError, match='prompt is too long'):
                await session.execute(prompt)
        else:
            with pytest.raises(Exception, match='(?i)(context|count|measur|budget|fit|prompt is too long)'):
                await session.execute(prompt)
        sends = [entry for entry in wire.timeline if entry['kind'] == 'send']
        first = next(entry for entry in sends if not entry['summary'])
        first_send = wire.timeline.index(first)
        initial_budgets = [entry['decision'] for entry in wire.timeline[:first_send]
                           if entry['kind'] == 'budget']
        assert initial_budgets and all(value is None for value in initial_budgets)
        assert any(entry['kind'] == 'count' and not entry['available']
                   for entry in wire.timeline[:first_send])
        assert not sends[0]['summary'], 'Compacted before provider rejection'
        pressure = wire.initial_pressure
        assert pressure['semantic_measurement_kind'] == 'public_estimate'
        assert pressure['semantic_input_tokens'] < pressure['semantic_trigger']
        assert pressure['semantic_trigger'] == pressure['semantic_policy_budget'] * .7
        assert first['tokens'] > LIMIT
        opaque_tokens = sum(len(block['thinking'].encode()) // 4
                            for row in wire.foreground[0]['messages']
                            for block in row['content'] if isinstance(block, dict)
                            and block.get('type') == 'thinking')
        assert opaque_tokens > first['tokens'] * .31
        assert all(f'{wire.signature_marker}-{index}' in json.dumps(wire.foreground[0])
                   for index in (1, 2))
        assert wire.summaries, 'A provider overflow must force the eligible public prefix'
        first_summary = next(index for index, entry in enumerate(wire.timeline)
                             if entry['kind'] == 'send' and entry['summary'])
        assert not any(entry['kind'] == 'count' and entry['available'] and not entry['summary']
                       for entry in wire.timeline[:first_summary])
        assert context.summary is not None
        for body in wire.summaries:
            text = json.dumps(body)
            assert wire.private_marker not in text and wire.signature_marker not in text
            assert 'LATEST_RETAINED_CORRECTION' not in text and 'CURRENT_INPUT_ONLY' not in text
            assert all(f'RECEIPT-{index}' in text for index in (1, 2))
            quoted = next(block['text'] for row in body['messages']
                          for block in ([{'text': row['content']}] if isinstance(row['content'], str)
                                        else row['content']) if isinstance(block, dict)
                          and 'Next ordered conversation data. Incorporate this evidence' in block.get('text', ''))
            source = quoted.split('Next ordered conversation data. Incorporate this evidence into the continuation note:\n', 1)[1]
            public_rows = [json.loads(line) for line in source.splitlines()]
            for index in (1, 2):
                assert any(row['role'] == 'assistant' and any(
                    block.get('type') == 'tool_call' and block.get('id') == f'call-{index}'
                    for block in row['content']) for row in public_rows if isinstance(row['content'], list))
                assert any(row['role'] == 'tool' and row.get('tool_call_id') == f'call-{index}'
                           for row in public_rows)
        assert all(body['model'] == MODEL for body in wire.foreground + wire.summaries)
        for body in wire.foreground:
            text = json.dumps(body)
            assert body['max_tokens'] == 1000
            for marker in ('ORBIT', 'RECEIPT-1', 'RECEIPT-2',
                           'CURRENT_REFERENCE_1', 'MEMORY_ONE', 'SURFACE_1',
                           'LATEST_RETAINED_CORRECTION', 'CURRENT_INPUT_ONLY'):
                assert marker in text
        if control == 'count-unavailable':
            assert len(wire.foreground) == 1
            assert not wire.counted
            last_summary = max(index for index, entry in enumerate(wire.timeline)
                               if entry['kind'] == 'send' and entry['summary'])
            assert any(entry['kind'] == 'count' and not entry['summary'] and not entry['available']
                       for entry in wire.timeline[last_summary + 1:])
            assert any(entry['kind'] == 'budget' and not entry['summary'] and entry['decision'] is None
                       for entry in wire.timeline[last_summary + 1:])
        else:
            assert len(wire.foreground) == 2  # The refused original plus ONE retry.
            assert len(wire.summaries) == 1
            retry = [entry for entry in sends if not entry['summary']][1]
            assert retry['key'] != first['key']
            assert retry['tokens'] < first['tokens'] and retry['tokens'] + 4096 <= LIMIT
            assert wire.summary_marker in json.dumps(wire.foreground[1])
            # Both the public summary and the complete selected host retry must
            # have identical counted/provider-visible input envelopes before send.
            for entry in sends[1:]:
                position = wire.timeline.index(entry)
                assert any(item['kind'] == 'count' and item['available']
                           and item['key'] == entry['key'] and item['tokens'] == entry['tokens']
                           for item in wire.timeline[first_send + 1:position])
                assert any(item['kind'] == 'budget' and item['summary'] == entry['summary']
                           and item['decision'] is not None
                           and item['decision'].get('measurement', {}).get('kind') == 'provider_count'
                           and item['decision']['measurement']['input_tokens'] == entry['tokens']
                           for item in wire.timeline[first_send + 1:position])
                assert wire.counted[entry['key']] == entry['tokens']
            assert wire.private_marker not in json.dumps(wire.foreground[1])
        rows = await context.get_messages()
        assert [digest(row) for row in rows[:len(seed)]] == wire.seed_hashes
        assert [row['content'] for row in rows if row['role'] == 'user'
                and not (row.get('metadata') or {}).get('ephemeral')] == (
                    [row['content'] for row in seed if row['role'] == 'user'] + [prompt])
        tools = [row for row in rows if row['role'] == 'tool']
        assert [row['tool_call_id'] for row in tools] == ['call-1', 'call-2']
        assert {row['tool_call_id']: hashlib.sha256(row['content'].encode()).hexdigest()
                for row in tools} == tool_hashes
        assert effects == []
        if control == 'recovered':
            assert sum(wire.final_marker in str(row['content']) for row in rows) == 1
        else:
            assert all(wire.final_marker not in str(row['content']) for row in rows)
    finally:
        await sdk.close()
        await session.cleanup()
        assert wire.violations == [], 'Canonical history changed during preparation'