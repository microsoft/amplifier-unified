"""Offline exact provider/host/native-loop fitting, never live vendor evidence."""
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
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
DIRECT = 'https://api.anthropic.com'
PORTABLE = 'https://portable.example.invalid'
COMPACT_BETA = 'compact-2026-09-04'
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
        self.signed_blocks = []
        self.native_counts = set()
        self.signed_replay = False
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
            if COMPACT_BETA in request.headers.get('anthropic-beta', '').split(','):
                self.native_counts.add(key)
            return self.http.Response(200, json={'input_tokens': measured})
        assert request.url.path.endswith('/messages') and not body.get('stream')
        assert key in self.counted, 'Provider dispatch bypassed its authoritative count'
        text = json.dumps(body)
        native = body.get('compaction') == {'type': 'summarize'}
        summary = native or 'Next ordered conversation data. Incorporate this evidence' in text
        if native:
            assert request.url.host == 'api.anthropic.com'
            assert COMPACT_BETA in request.headers['anthropic-beta'].split(',')
            assert key in self.native_counts, 'Native preflight must use the beta and identical input'
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
            note = 'ORBIT. Never replay. Completed: ' + ', '.join(facts)
            if native:
                block = {'type': 'compaction', 'signature': f'fixture-{len(self.summaries)}', 'content': note}
                self.signed_blocks.append(block)
                content, stop = [block], 'compaction'
            else:
                assert request.url.host != 'api.anthropic.com', 'Unexpected portable fallback'
                content, stop = [{'type': 'text', 'text': note}], 'end_turn'
        else:
            self.sent.append(count)
            if self.signed_blocks:
                first, *tail = body['messages']
                assert first == {'role': 'assistant', 'content': [self.signed_blocks[-1]]}
                assert COMPACT_BETA in request.headers['anthropic-beta'].split(',')
                assert key in self.native_counts
                if re.search(r'RECEIPT-1\b', self.signed_blocks[-1]['content']):
                    assert not re.search(r'RECEIPT-1\b', json.dumps(tail))
                    self.signed_replay = True
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


@pytest.mark.parametrize('rejections,stop,seed_kind,base_url', [
    (0, False, None, PORTABLE), (1, False, None, PORTABLE), (2, False, None, PORTABLE),
    (1, True, None, PORTABLE), (0, False, 'resume', PORTABLE), (0, False, 'oversized-tail', PORTABLE),
    (0, False, None, DIRECT), (0, False, 'resume', DIRECT)],
    ids=['proactive', 'reactive', 'second-overflow-terminal', 'stop-before-retry',
         'resume-over-1m-signed-history', 'oversized-retained-tail', 'native-signed', 'native-resume-over-1m'])
async def test_cold_nondefault_fable_compacts_inside_one_ordinary_tool_turn(rejections, stop, seed_kind, base_url):
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
    sdk = module.AsyncAnthropic(api_key='synthetic-not-live', max_retries=0, base_url=base_url,
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
        if seed_kind == 'resume' and base_url == DIRECT:
            with pytest.raises(RuntimeError, match='native_input_oversized'):
                await session.execute(prompt)
            assert not wire.sent and not wire.summaries and context.summary is None
            assert [digest(row) for row in (await context.get_messages())[:len(seed)]] == seed_hashes
            assert wire.effects == list(range(1, STEPS + 1))
            return
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
        if base_url == DIRECT:
            assert wire.signed_replay
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
            available = ((bool(self.foreground) or self.control == 'restored')
                         and self.control != 'count-unavailable'
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
            if (len(self.foreground) == 1 and self.control != 'restored') or (
                    self.control == 'second-overflow' and len(self.foreground) == 2):
                self.timeline.append({'kind': 'overflow_response', 'status': 400, **record})
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


def opaque_history_seed(wire):
    seed = [{'role': 'user', 'content': 'Accepted ORBIT task. Preserve the original evidence.'}]
    for index in (1, 2):
        seed.extend([
            {'role': 'assistant', 'content': [
                # Match the saved failure's shape: empty public thinking text
                # with most wire bytes in an opaque encrypted signature.
                {'type': 'thinking', 'thinking': '',
                 'signature': f'{wire.signature_marker}-{index}. ' * 80_000},
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
    return seed


@pytest.mark.parametrize('control', ['recovered', 'count-unavailable', 'second-overflow', 'native-no-count'])
async def test_opaque_history_overflow_requires_one_different_counted_retry(control, monkeypatch):
    module = pytest.importorskip('amplifier_module_provider_anthropic',
        reason='Native qualification installs exact accepted Anthropic source.')
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
        base_url=DIRECT if control == 'native-no-count' else PORTABLE,
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

    async def observe_compaction(event, data):
        wire.timeline.append({'kind': 'hook', 'event': event, 'data': dict(data)})
        return HookResult()

    for name in ('record', 'app_control'):
        await coordinator.mount('tools', NoReplay(name), name=name)
    await context.set_system_prompt_factory(instructions)
    for event in ('context:compaction_started', 'context:compaction_finished'):
        coordinator.hooks.register(event, observe_compaction, name='opaque-compaction-witness')
    seed = opaque_history_seed(wire)
    prompt = 'CURRENT_INPUT_ONLY: finish ORBIT without repeating completed operations.'
    try:
        assert context.config['token_meter'] == 'actual'
        assert context.config['summarize_trigger'] == .7
        measured_view = coordinator.get_capability('context.measured_request_view')
        assert callable(measured_view) and measured_view.__self__ is context
        reason = inspect.signature(measured_view).parameters.get('reason')
        # Check the extension after the behavioral oracle below, so a baseline
        # control discriminates the unchanged retry rather than just its API.
        await context.set_messages(seed)
        wire.seed_hashes = [digest(row) for row in await context.get_messages()]
        tool_hashes = {row['tool_call_id']: hashlib.sha256(row['content'].encode()).hexdigest()
                       for row in seed if row['role'] == 'tool'}
        assert raw._runtime_model_info_cache == {}
        if control == 'native-no-count':
            with pytest.raises(RuntimeError, match='authoritative_measurement_unavailable'):
                await session.execute(prompt)
            assert len(wire.foreground) == 1 and not wire.summaries and not wire.counted
            assert [entry['status'] for entry in wire.timeline if entry['kind'] == 'overflow_response'] == [400]
            assert not effects and context.summary is None
            assert [digest(row) for row in (await context.get_messages())[:len(seed)]] == wire.seed_hashes
            return
        if control == 'recovered':
            result = await session.execute(prompt)
            assert wire.final_marker in result and wire.summary_marker not in result
        elif control == 'second-overflow':
            # The native Core session boundary wraps the typed loop error.
            with pytest.raises(RuntimeError, match='Execution failed: ContextLengthError:.*prompt is too long'):
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
        opaque_tokens = sum(len(block.get('signature', '').encode()) // 4
                            for row in wire.foreground[0]['messages']
                            for block in row['content'] if isinstance(block, dict)
                            and block.get('type') == 'thinking')
        assert opaque_tokens > first['tokens'] * .31
        assert all(block.get('thinking') == ''
                   for row in wire.foreground[0]['messages']
                   for block in row['content'] if isinstance(block, dict)
                   and block.get('type') == 'thinking')
        assert all(f'{wire.signature_marker}-{index}' in json.dumps(wire.foreground[0])
                   for index in (1, 2))
        assert wire.summaries, 'A provider overflow must force the eligible public prefix'
        assert reason is not None and reason.kind == inspect.Parameter.KEYWORD_ONLY
        first_summary = next(index for index, entry in enumerate(wire.timeline)
                             if entry['kind'] == 'send' and entry['summary'])
        rejected = next(index for index, entry in enumerate(wire.timeline)
                        if entry['kind'] == 'overflow_response' and entry['key'] == first['key'])
        started = [index for index, entry in enumerate(wire.timeline)
                   if entry['kind'] == 'hook' and entry['event'] == 'context:compaction_started']
        finished = [index for index, entry in enumerate(wire.timeline)
                    if entry['kind'] == 'hook' and entry['event'] == 'context:compaction_finished']
        assert len(started) == len(finished) == 1
        assert first_send < rejected < started[0] < first_summary < finished[0]
        assert wire.timeline[rejected]['status'] == 400
        assert wire.timeline[finished[0]]['data']['outcome'] == 'completed'
        forced_pressure = wire.timeline[started[0]]['data']
        assert forced_pressure['semantic_measurement_kind'] == 'public_estimate'
        assert forced_pressure['semantic_input_tokens'] < forced_pressure['semantic_trigger']
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
            assert finished[0] < wire.timeline.index(retry)
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


async def _opaque_restart_phase(directory, phase):
    """Fresh interpreter/native session with real host storage and continuity.

    The cold phase prepares and dispatches an already-saved request, not a new
    session.execute(input). This qualifies disk/process checkpoint continuity,
    not Worker startup, live-input admission or automatic failed-turn replay.
    """
    import amplifier_module_provider_anthropic as module
    from anthropic import _base_client
    from amplifier_core import ChatRequest, Message, ToolSpec
    from amplifier_foundation.session.activation import ActivationGate
    from amplifier_web.context_continuity import install as install_continuity
    from amplifier_web.host.session import install_checkpoint_hooks
    from amplifier_web.host.storage import SessionStore

    assert phase in {'failed', 'restored'}
    directory = Path(directory)
    http = getattr(_base_client, 'httpx2', None) or _base_client.httpx
    wire = OpaqueHistoryWire(http, 'count-unavailable' if phase == 'failed' else 'restored')
    plan = {'session': {
        'orchestrator': {'module': 'loop-streaming', 'config': {
            'use_streaming': False, 'min_delay_between_calls_ms': 0,
            'ephemeral_injection_mode': 'tail'}},
        'context': {'module': 'context-managed', 'config': {
            'durable_checkpoints': True, 'max_tokens': LIMIT, 'summarize_trigger': .7,
            'compaction_notice_enabled': False, 'archive_recovery': False}}}}
    paths = {'loop-streaming': Path(streaming.__file__).parent,
             'context-managed': Path(managed.__file__).parent}
    session = await PreparedBundle(plan, BundleModuleResolver(paths),
        Bundle(name='opaque-restart', session=plan['session'])).create_session(
            session_id='opaque-restart')
    coordinator = session.coordinator
    context = coordinator.get('context')
    wire.context = context
    sdk = module.AsyncAnthropic(api_key='synthetic-not-live', max_retries=0, base_url=PORTABLE,
        http_client=http.AsyncClient(transport=http.MockTransport(wire.transport)))
    raw = module.AnthropicProvider(api_key='synthetic-not-live', coordinator=coordinator, config={
        'default_model': 'claude-sonnet-5', 'enable_1m_context': True,
        'use_streaming': False, 'max_retries': 0, 'enable_prompt_caching': False})
    raw._client = sdk
    current = CurrentContext()
    if phase == 'restored':
        current.version = 2
        current.memory = {'items': [{'id': 'preference', 'text': 'MEMORY_TWO'}]}
    surface = SurfaceProvider(raw, current.delivery(), coordinator=coordinator)
    selected = SelectedProvider(raw, {'model': MODEL, 'max_output_tokens': 1000},
                                lambda _: surface)
    await coordinator.mount('providers', selected, name='anthropic')
    coordinator.get('orchestrator').root_provider = selected
    effects = []

    class NoReplay:
        description = 'Synthetic completed operation; never execute again'
        input_schema = {'type': 'object', 'properties': {'index': {'type': 'integer'}}}

        def __init__(self, name):
            self.name = name

        async def execute(self, arguments):
            effects.append(self.name)
            raise AssertionError('Restart repeated a completed tool')

    for name in ('record', 'app_control'):
        await coordinator.mount('tools', NoReplay(name), name=name)

    async def instructions():
        return 'Preserve ORBIT and every completed receipt. Never replay operations.'

    await context.set_system_prompt_factory(instructions)
    gate = ActivationGate()
    gate.activate()
    store = SessionStore(directory / 'sessions', legacy_home=directory / 'unused-legacy')
    runtime = SimpleNamespace(session_id=session.session_id, generation=None)
    continuity = None

    async def checkpoint(status='in_progress', *, timing=None, transcript_required=True):
        gate.check_current()
        if transcript_required:
            store.save(session.session_id, await context.get_messages(), {
                'working_dir': str(directory), 'bundle_name': 'opaque-restart', 'status': status})
            if continuity:
                continuity.save()

    # Same host owner and automatic compaction hook as prepare_manager. Loading
    # is intentionally lazy through preserve(), not a direct export/restore call.
    host_directory = directory / 'host'
    host_directory.mkdir(exist_ok=True)
    continuity = install_continuity(coordinator, session.session_id, host_directory,
        checkpoint, writer_guard=gate.check_current)
    coordinator.register_capability('live.checkpoint', checkpoint)
    install_checkpoint_hooks(coordinator, runtime, checkpoint)
    observed = []

    async def observe(event, data):
        observed.append((event, dict(data)))
        wire.timeline.append({'kind': 'hook', 'event': event, 'data': dict(data)})
        return HookResult()

    for event in ('context:checkpoint_restored', 'context:compaction_started',
                  'context:compaction_finished'):
        coordinator.hooks.register(event, observe, name='opaque-restart-' + event)
    seed = opaque_history_seed(wire)
    prompt = 'CURRENT_INPUT_ONLY: finish ORBIT without repeating completed operations.'
    try:
        if phase == 'failed':
            assert store.load(session.session_id) is None and not continuity.path.exists()
            await context.set_messages(seed)
            wire.seed_hashes = [digest(row) for row in seed]
            with pytest.raises(RuntimeError, match='Provider overflow repair requires a provider count'):
                await session.execute(prompt)
            assert len(wire.foreground) == len(wire.summaries) == 1
            assert not wire.counted  # Counting never recovered in this process.
            assert wire.tokens(wire.foreground[0]) > LIMIT
            assert wire.initial_pressure['semantic_input_tokens'] < wire.initial_pressure['semantic_trigger']
            last_summary = max(index for index, entry in enumerate(wire.timeline)
                               if entry['kind'] == 'send' and entry['summary'])
            assert any(entry['kind'] == 'count' and not entry['available'] and not entry['summary']
                       for entry in wire.timeline[last_summary + 1:])
            assert any(event == 'context:compaction_finished' and data['outcome'] == 'completed'
                       for event, data in observed)
            # No manual save here: prove the real automatic host hook saved the
            # finished semantic checkpoint without an explicit test save.
            saved = json.loads(continuity.path.read_text())
            assert wire.summary_marker in saved['summary']['text']
            assert saved['progress'] is None
            assert context.summary == (saved['summary']['throughMessage'], saved['summary']['text'])
        else:
            saved = json.loads(continuity.path.read_text())
            previous = json.loads((directory / 'failed.json').read_text())
            assert digest(saved) == previous['checkpointHash']
            loaded = store.load(session.session_id)
            assert loaded is not None
            await context.set_messages(loaded[0])
            wire.seed_hashes = [digest(row) for row in loaded[0]]
            assert wire.seed_hashes == previous['canonicalHashes']
            assert context.summary is None and continuity.loaded is False
            assert raw._runtime_model_info_cache == {}
            specs = [ToolSpec(name=name, description=tool.description, parameters=tool.input_schema)
                     for name, tool in coordinator.get('tools').items()]
            counted = []

            async def count_view(view):
                request = ChatRequest(messages=[Message(**row) for row in view],
                                      tools=specs, max_output_tokens=1000)
                decision = await selected.request_budget(request,
                    context_estimate=len(json.dumps(view)) // 4)
                counted.append((request, decision))
                return {'dispatch': request, 'budget_decision': decision}

            measured = coordinator.get_capability('context.measured_request_view')
            result = await measured(provider=selected, retain_contents=[], count_view=count_view)
            assert continuity.loaded and continuity.public()['status'] == 'restored'
            assert any(event == 'context:checkpoint_restored' and data['status'] == 'restored'
                       for event, data in observed)
            assert not any(event.startswith('context:compaction_') for event, _ in observed)
            assert context.summary == (saved['summary']['throughMessage'], saved['summary']['text'])
            attempt = result['final_attempt']
            request, decision = attempt['dispatch'], attempt['budget_decision']
            assert any(candidate is request and budget == decision for candidate, budget in counted)
            assert decision['measurement']['kind'] == 'provider_count'
            assert decision['estimated_input_tokens'] <= decision['input_limit_tokens']
            assert not wire.foreground and not wire.summaries
            reply = await selected.complete(request)
            assert wire.final_marker in str(reply.content)
            assert len(wire.foreground) == 1 and not wire.summaries
            body = wire.foreground[0]
            assert wire.counted[digest(projection(body))] == decision['measurement']['input_tokens']
            assert wire.tokens(body) < LIMIT
            text = json.dumps(body)
            assert text.count(wire.summary_marker) == 1
            for marker in (wire.summary_marker, 'RECEIPT-1', 'RECEIPT-2', 'CURRENT_INPUT_ONLY',
                           'LATEST_RETAINED_CORRECTION', 'CURRENT_REFERENCE_2', 'SURFACE_2', 'MEMORY_TWO'):
                assert marker in text
            for marker in ('CURRENT_REFERENCE_1', 'SURFACE_1', 'MEMORY_ONE', wire.signature_marker):
                assert marker not in text
            # No second ordinary input and no manual restore or canonical edit:
            # cold measured preparation and dispatch leave native history exact.
            assert [digest(row) for row in await context.get_messages()] == previous['canonicalHashes']
            await checkpoint()
            assert digest(json.loads(continuity.path.read_text())) == previous['checkpointHash']
        canonical = (store.load(session.session_id))[0]
        assert [digest(row) for row in canonical[:len(seed)]] == [digest(row) for row in seed]
        assert [row['content'] for row in canonical if row['role'] == 'user'] == (
            [row['content'] for row in seed if row['role'] == 'user'] + [prompt])
        tools = [row for row in canonical if row['role'] == 'tool']
        tool_hashes = {row['tool_call_id']: hashlib.sha256(row['content'].encode()).hexdigest()
                       for row in tools}
        assert [row['tool_call_id'] for row in tools] == ['call-1', 'call-2']
        assert tool_hashes == {row['tool_call_id']: hashlib.sha256(row['content'].encode()).hexdigest()
                               for row in seed if row['role'] == 'tool'}
        for body in wire.summaries:
            assert wire.signature_marker not in json.dumps(body)
        assert effects == [] and wire.violations == []
        receipt = {'pid': os.getpid(), 'phase': phase, 'canonicalHashes': [digest(row) for row in canonical],
                   'checkpointHash': digest(json.loads(continuity.path.read_text())),
                   'toolHashes': tool_hashes, 'summaries': len(wire.summaries),
                   'foreground': len(wire.foreground)}
        (directory / (phase + '.json')).write_text(json.dumps(receipt))
    finally:
        await sdk.close()
        await session.cleanup()


def test_opaque_history_checkpoint_survives_process_restart_after_final_count_failure(tmp_path):
    """Two exited/rebuilt interpreters, not two contexts sharing Python objects."""
    source = Path(__file__).resolve().parents[1]
    code = (
        "import asyncio, sys; from pathlib import Path; "
        "sys.path[:0] = [str(Path(sys.argv[1]) / 'tests'), sys.argv[1]]; "
        "from test_fable_context_loop import _opaque_restart_phase; "
        "asyncio.run(_opaque_restart_phase(sys.argv[2], sys.argv[3]))"
    )
    environment = {**os.environ, 'HOME': str(tmp_path / 'home'), 'TMPDIR': str(tmp_path / 'tmp'),
        'AMPLIFIER_HOME': str(tmp_path / 'amplifier'), 'AMPLIFIER_WEB_HOME': str(tmp_path / 'app'),
        'AMPLIFIER_UNIFIED_IMPORT_HOME': str(tmp_path / 'legacy'),
        'AMPLIFIER_SESSION_STATE_HOME': str(tmp_path / 'ownership')}
    for name in ('home', 'tmp'):
        (tmp_path / name).mkdir()
    receipts = []
    for phase in ('failed', 'restored'):
        # Each call waits for process exit. No module object, response, SDK cache
        # or in-memory checkpoint can cross phases; only the owned disk survives.
        process = subprocess.run([sys.executable, '-c', code, str(source), str(tmp_path), phase],
            cwd=source, env=environment, capture_output=True, text=True, timeout=120)
        assert process.returncode == 0, (phase, process.stdout[-2000:], process.stderr[-4000:])
        receipts.append(json.loads((tmp_path / (phase + '.json')).read_text()))
    failed, restored = receipts
    assert failed['pid'] != restored['pid'] and failed['pid'] != os.getpid() != restored['pid']
    assert failed['canonicalHashes'] == restored['canonicalHashes']
    assert failed['checkpointHash'] == restored['checkpointHash']
    assert failed['toolHashes'] == restored['toolHashes']
    assert failed['summaries'] == 1 and restored['summaries'] == 0
    assert failed['foreground'] == restored['foreground'] == 1


async def test_provider_count_diagnostics_reach_real_context_intelligence_capture(tmp_path, monkeypatch):
    """Real mounts, discovery, Core hooks and CI writer; only HTTP is synthetic.

    Optional imports may skip in the ordinary host suite. The existing native
    qualification guard must require execution with zero skips in its prepared
    candidate environment; this test alone is not live-vendor or fan-out proof.
    """
    module = pytest.importorskip('amplifier_module_provider_anthropic',
        reason='Native qualification installs exact accepted Anthropic source.')
    ci = pytest.importorskip('amplifier_module_hook_context_intelligence',
        reason='Native qualification requires actual CI capture with zero skips.')
    from anthropic import _base_client
    from amplifier_core import ChatRequest, Message, ModuleCoordinator
    from amplifier_module_hook_context_intelligence.handlers.logging_handler import LoggingHandler

    http = getattr(_base_client, 'httpx2', None) or _base_client.httpx
    event = 'provider:token_count_unavailable'
    session_id = 'provider-count-offline'
    project_slug = 'provider-count-diagnostics'
    private = {
        'content': 'synthetic-private-count-request-content',
        'body': 'synthetic-private-count-response-body',
        # The real SDK includes the error-body message in its exception text.
        'exception': 'synthetic-private-count-sdk-exception-text',
        'header': 'synthetic-private-count-unrelated-header',
        'config': 'synthetic-private-count-config-value',
        'api_key': 'synthetic-private-count-api-key-not-live',
    }
    outcomes = [
        (400, 'req_count_400'),
        (500, 'req_count_500_first'),
        (500, 'req_count_500_second'),
        (200, 'req_count_valid'),
    ]
    calls = []
    transport_violations = []

    def transport(request):
        # SDK/provider catches transport assertions as count-unavailable. Keep
        # independent evidence so a fixture error cannot masquerade as an outage.
        calls.append((request.method, request.url.path))
        try:
            assert request.url.host == 'count-fixture.invalid'
            assert calls[-1] == ('POST', '/v1/messages/count_tokens')
            assert request.headers['x-api-key'] == private['api_key']
            body = json.loads(request.content)
            assert body['model'] == MODEL
            assert private['content'] in json.dumps(body)
            status, request_id = outcomes[len(calls) - 1]
        except Exception as error:
            transport_violations.append((type(error).__name__, calls[-1]))
            raise
        if status == 200:
            return http.Response(200, json={'input_tokens': 123})
        return http.Response(status,
            headers={'request-id': request_id, 'x-private-count': private['header']},
            json={'type': 'error', 'error': {
                'type': 'invalid_request_error' if status == 400 else 'api_error',
                'message': private['body'] + ' ' + private['exception']}})

    # Keep both reader and writer resolution inside this test's owned storage.
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(tmp_path))
    coordinator = ModuleCoordinator()
    coordinator.hooks.set_default_fields(session_id=session_id)
    coordinator.register_capability('session.working_dir', str(tmp_path))
    assert await coordinator.collect_contributions('observability.events') == []
    ci_cleanup = await ci.mount(coordinator, {
        'base_path': str(tmp_path), 'project_slug': project_slug,
        'destinations': {}, 'exclude_events': [],
    })
    provider_cleanup = None
    try:
        resolver = coordinator.get_capability('context_intelligence.hook_config_resolver')
        state = coordinator.get_capability('context_intelligence._hook_state')
        handler = state['logging_handler']
        assert isinstance(handler, LoggingHandler)
        assert handler._resolver is resolver
        assert resolver.base_path == tmp_path
        assert resolver.project_slug == project_slug
        assert resolver.working_dir == str(tmp_path)
        assert resolver.destinations == state['destinations'] == {}
        assert resolver.additional_events == frozenset()
        assert resolver.exclude_events == frozenset()
        session_dir = resolver.session_dir(session_id)
        assert session_dir == tmp_path / project_slug / 'sessions' / session_id / 'context-intelligence'
        capture = session_dir / 'events.jsonl'
        assert not capture.exists()
        assert event not in coordinator.hooks.list_handlers()

        # Mount CI first, then the provider. Only the real post-mount discovery
        # may subscribe to this contributed name; no additional_events shortcut.
        provider_cleanup = await module.mount(coordinator, {
            'api_key': private['api_key'], 'default_model': MODEL, 'max_retries': 0,
            'enable_1m_context': True, 'use_streaming': False,
            'enable_prompt_caching': False,
            'extra_request_params': {'metadata': {'user_id': private['config']}},
        })
        assert callable(provider_cleanup)
        assert await coordinator.collect_contributions('observability.events') == [[event]]
        assert coordinator.channels['observability.events'][0]['name'] == 'provider-anthropic'
        await ci.on_session_ready(coordinator)
        assert coordinator.hooks.list_handlers()[event] == ['LoggingHandler']
        assert handler._dispatchers == []
        assert state['destinations'] == {}

        provider = coordinator.get('providers', 'anthropic')
        assert provider._client is None  # No live SDK client was constructed.
        http_client = http.AsyncClient(
            transport=http.MockTransport(transport), trust_env=False)
        provider._client = module.AsyncAnthropic(
            api_key=private['api_key'], base_url='https://count-fixture.invalid/',
            max_retries=0, http_client=http_client)
        request = ChatRequest(
            messages=[Message(role='user', content=private['content'])],
            max_output_tokens=1000)
        before = request.model_dump()
        decisions = [await provider.request_budget(request, context_estimate=200_000)
                     for _ in outcomes]
        assert transport_violations == [], transport_violations
        assert decisions[:3] == [None, None, None]
        assert decisions[3] == {
            'estimated_input_tokens': 123 + 4096,
            'input_limit_tokens': LIMIT,
            'context_token_budget': 200_000,
            'max_output_tokens': 1000,
            'measurement': {
                'kind': 'provider_count',
                'source': 'anthropic.messages.count_tokens',
                'input_tokens': 123,
            },
        }
        assert request.model_dump() == before
        assert calls == [('POST', '/v1/messages/count_tokens')] * 4
        assert sum(path == '/v1/messages' for _, path in calls) == 0
        assert handler._dispatchers == []
        assert handler._seen_sessions == {session_id}
    finally:
        try:
            if provider_cleanup is not None:
                await provider_cleanup()
        finally:
            await ci_cleanup()
    assert http_client.is_closed
    assert provider._client is None
    assert not coordinator.hooks.list_handlers().get(event)
    assert coordinator.get_capability('context_intelligence._hook_state') is None

    # Read only this small synthetic capture, from the resolver-owned path.
    # Nothing in this test writes event records or invokes LoggingHandler itself.
    with capture.open('rb') as stream:
        captured = stream.read(16_385)
    assert len(captured) <= 16_384
    serialized = captured.decode('utf-8')
    assert serialized.endswith('\n')
    records = [json.loads(line) for line in serialized.splitlines()]
    assert len(records) == 2
    expected = [
        (400, 'BadRequestError', 'req_count_400'),
        (500, 'InternalServerError', 'req_count_500_first'),
    ]
    for record, (status, error_type, request_id) in zip(records, expected, strict=True):
        timestamp = record['timestamp']
        assert isinstance(timestamp, str) and timestamp
        # Accepted provider diagnostics classify only allowlisted 400 details.
        # The private fixture message is deliberately unrecognized, not echoed;
        # a 500 retains the existing scalar envelope without validation details.
        detail = ({
            'diagnostic_version': 1,
            'api_error_type': 'invalid_request_error',
            'reason_code': 'unknown',
            'schema_path': 'unknown',
            'detail_status': 'unrecognized',
        } if status == 400 else {})
        assert record == {
            'event': event, 'workspace': project_slug, 'timestamp': timestamp,
            'data': {
                'session_id': session_id, 'timestamp': timestamp,
                'provider': 'anthropic', 'model': MODEL,
                'source': 'anthropic.messages.count_tokens',
                'category': 'request_error', 'error_type': error_type,
                'status_code': status, 'request_id': request_id,
                **detail,
            },
        }
    assert json.loads(json.dumps(records, allow_nan=False)) == records
    assert all(sentinel not in serialized for sentinel in private.values())
    assert 'req_count_500_second' not in serialized
    assert not {'body', 'messages', 'headers', 'config', 'api_key'} & records[0]['data'].keys()