"""Host adapter against the actual context-managed module and maintained dev dependencies."""
import asyncio
import copy
from contextlib import asynccontextmanager
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
import amplifier_module_context_managed
import amplifier_module_loop_streaming
import amplifier_module_provider_openai as provider_module
from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
from amplifier_module_context_managed import boundary
from amplifier_web.context_continuity import ContextContinuity, digest
from amplifier_web.runtime_controls import RuntimeControls
from test_task_continuity import Coordinator


async def mount(directory, messages, *, context_config=None):
    coordinator = Coordinator()
    coordinator.hooks = None
    async def mount_context(name, context): coordinator.context = context
    coordinator.mount = mount_context
    coordinator.register_contributor = lambda *args: None
    config = {'durable_checkpoints': True, 'max_tokens': 6000, 'summarize_trigger': .2} if context_config is None else context_config
    if context_config is None:
        await boundary.mount_boundary(coordinator, config)
    else:
        from amplifier_module_context_managed import mount as mount_context_module
        await mount_context_module(coordinator, config)
    provider = SimpleNamespace(get_info=lambda: {'id': 'fixture', 'defaults': {'model': 'model-a'}}, complete=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type='text', text='Original task; revised constraints; operation uncertain-op outcome unknown; artifact report.md.')])) )
    coordinator.loop.root_provider = provider
    coordinator.loop._select_provider = lambda providers: provider
    original_get = coordinator.get
    coordinator.get = lambda name: {'fixture': provider} if name == 'providers' else original_get(name)
    await coordinator.context.set_messages(messages)
    transcript = directory / 'transcript.json'
    adapter = None
    async def checkpoint():
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(json.dumps(await coordinator.context.get_messages()))
        if adapter: adapter.save()
    adapter = ContextContinuity(coordinator, 'session', directory, checkpoint)
    return coordinator, provider, adapter, checkpoint, transcript


@pytest.mark.parametrize('concurrent_change', [False, True])
async def test_warm_idle_model_switch_binds_current_identity_before_fitting(
        tmp_path, monkeypatch, concurrent_change):
    from amplifier_core import ProviderInfo
    from amplifier_web.host.session import SelectedProvider
    original = [{'role': 'user', 'content': 'Retain originals and answer.'}]
    coordinator, raw, adapter, _, _ = await mount(tmp_path, original,
        context_config={'durable_checkpoints': True, 'foreground_continuation': True,
                        'max_tokens': 6000, 'summarize_trigger': .9, 'archive_recovery': False})
    raw.name = 'fixture'
    raw.get_info = lambda: ProviderInfo(id='fixture', display_name='Fixture',
                                      defaults={'model': 'model-a'})
    providers = {'connection': raw}
    get = coordinator.get
    coordinator.get = lambda name: providers if name == 'providers' else get(name)
    coordinator.loop.root_provider = SelectedProvider(raw, {'instance': 'connection', 'model': 'model-a'})
    await adapter.preserve(original)  # Warm A is already cached.
    assert adapter._identity == {'provider': 'connection', 'model': 'model-a'}
    coordinator.loop.root_provider = SelectedProvider(raw, {'instance': 'connection', 'model': 'model-b'})
    selected = coordinator.loop.root_provider
    assert selected.default_model == 'model-b'
    counted = []

    async def count(view):
        from amplifier_core.message_models import ChatRequest, Message
        counted.append(copy.deepcopy(view))
        if concurrent_change:
            coordinator.loop.root_provider = SelectedProvider(raw, {'instance': 'connection', 'model': 'model-c'})
        return {'dispatch': ChatRequest(messages=[Message(**row) for row in view]),
                'budget_decision': None}

    if concurrent_change:
        with pytest.raises(RuntimeError, match='Request contract changed'):
            await coordinator.context.get_measured_request_view(
                provider=selected, count_view=count, retain_contents=[])
    else:
        view = await coordinator.context.get_measured_request_view(
            provider=selected, count_view=count, retain_contents=[])
        assert view and counted
        assert adapter._identity == {'provider': 'connection', 'model': 'model-b'}
    assert await coordinator.context.get_messages() == original
    raw.complete.assert_not_awaited()


async def test_actual_context_multiple_compactions_restart_and_saved_task(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    original = [{'role': 'user', 'content': 'Build the report without replacing originals.'},
                {'role': 'assistant', 'content': 'Research ' * 2400},
                {'role': 'tool', 'tool_call_id': 'evidence', 'content': 'FULL-EVIDENCE-' * 9000},
                {'role': 'user', 'content': 'Correction: use revised constraints'},
                {'role': 'assistant', 'content': 'Working'}, {'role': 'user', 'content': 'Continue'}]
    coordinator, provider, adapter, checkpoint, transcript = await mount(tmp_path / 'session', original)
    controls = RuntimeControls(SimpleNamespace(session_id='session', coordinator=coordinator), SimpleNamespace(generation=None, queued_inputs=0))
    await controls.perform('task.create', {'commandId': 'create', 'expectedRevision': 0, 'objective': 'Build the verified report', 'constraints': ['Keep originals'], 'operationIds': ['uncertain-op'], 'artifactRefs': ['report.md']})
    for i in range(3):
        await coordinator.context.add_message({'role': 'assistant', 'content': 'Evidence ' * 2400})
        await coordinator.context.add_message({'role': 'user', 'content': f'Latest correction {i}'})
        await coordinator.context.get_messages_for_request(provider=provider)
        await checkpoint()
    await controls.perform('task.update', {'commandId': 'correct', 'expectedRevision': 1, 'correction': 'Latest correction 2'})
    await controls.perform('task.block', {'commandId': 'blocked', 'expectedRevision': 2, 'reason': 'Unknown operation outcome needs reconciliation'})
    saved = json.loads(adapter.path.read_text())
    assert saved['evidenceRefs'][0]['sha256'] == digest(original[2])
    canonical = json.loads(transcript.read_text())
    assert canonical[:len(original)] == original
    appended = canonical + [{'role': 'user', 'content': 'New correction after checkpoint'}]
    newer, provider2, adapter2, checkpoint2, transcript2 = await mount(tmp_path / 'session', appended)
    controls2 = RuntimeControls(SimpleNamespace(session_id='session', coordinator=newer), SimpleNamespace(generation=None, queued_inputs=0))
    await controls2.restore()
    view = await newer.context.get_messages_for_request()
    assert adapter2.public()['status'] == 'restored'
    assert provider2.complete.await_count == 0
    assert 'New correction after checkpoint' in str(view) and 'Latest correction 2' in str(view)
    assert 'uncertain-op outcome unknown' in str(view)
    assert await newer.context.get_messages() == appended
    assert controls2.tasks.record()['status'] == 'blocked'
    assert controls2.tasks.record()['corrections'][-1]['text'] == 'Latest correction 2'
    assert controls2.tasks.record()['artifactRefs'] == ['report.md']
    assert not await controls2.tasks.continuation_allowed()
    assert newer.session_state['goal'] is None
    await controls.close(); await controls2.close()


async def test_corrupt_host_checkpoint_remains_visibly_rejected(tmp_path):
    coordinator, provider, adapter, _, transcript = await mount(tmp_path, [{'role': 'user', 'content': 'Original'}])
    adapter.path.write_text('{broken')
    await coordinator.context.get_messages_for_request()
    assert adapter.public()['status'] == 'rejected'
    assert adapter.public()['originalsAvailable']
    assert json.loads(transcript.read_text()) == [{'role': 'user', 'content': 'Original'}]


async def test_partial_progress_is_saved_on_actual_completion_before_worker_restart(tmp_path):
    original = [{'role': 'user', 'content': 'Keep all source evidence.'},
        {'role': 'assistant', 'content': 'Evidence ' * 1200},
        {'role': 'user', 'content': 'Correction'}, {'role': 'assistant', 'content': 'Working'},
        {'role': 'user', 'content': 'Continue'}]
    first, provider, adapter, _, _ = await mount(tmp_path, original)
    first.context.config.update(summary_max_source_chars=1200, summary_max_calls=32)
    from amplifier_core.llm_errors import LLMError
    note = SimpleNamespace(content=[SimpleNamespace(type='text', text='Verified partial evidence')])
    provider.complete = AsyncMock(side_effect=[note, LLMError('offline', retryable=True)])
    # Strict preparation preserves completed work but stops the foreground turn
    # when the next summary request fails; it never silently proceeds unfitted.
    from amplifier_module_context_managed.errors import CompactionError
    with pytest.raises(CompactionError, match='summary_failed'):
        await first.context.get_messages_for_request(provider=provider)
    saved = json.loads(adapter.path.read_text())
    assert saved['summary'] is None and saved['progress']['completed'] == 1
    second, newer_provider, newer_adapter, _, _ = await mount(tmp_path, original)
    second.context.config.update(summary_max_source_chars=1200, summary_max_calls=32)
    await second.context.get_messages_for_request(provider=newer_provider)
    assert second.context.summary is not None and second.context._summary_progress is None, (second.context.checkpoint_status, second.context.summary_failure, newer_provider.complete.await_count, newer_adapter.public())
    assert 'Verified partial evidence' in newer_provider.complete.call_args_list[0].args[0].messages[-1].content
    assert await second.context.get_messages() == original


async def test_restore_observer_failure_does_not_reject_a_valid_checkpoint(tmp_path):
    original = [{'role': 'user', 'content': 'Keep originals.'},
        {'role': 'assistant', 'content': 'Evidence ' * 2400},
        {'role': 'user', 'content': 'Correction'}, {'role': 'assistant', 'content': 'Working'},
        {'role': 'user', 'content': 'Continue'}]
    first, provider, adapter, _, _ = await mount(tmp_path, original)
    await first.context.get_messages_for_request(provider=provider)
    adapter.save()
    second, model, newer_adapter, _, _ = await mount(tmp_path, original)
    second.hooks = SimpleNamespace(emit=AsyncMock(side_effect=ValueError('observer offline')))
    await second.context.get_messages_for_request(provider=model)
    assert newer_adapter.public()['status'] == 'restored'
    model.complete.assert_not_awaited()
    assert await second.context.get_messages() == original


class SyntheticNativeProvider:
    """Deterministic transport, not a claim about vendor summary quality.

    Counts the actual assembled request, including the opaque carrier, tools and
    instructions. Compaction keeps short facts and results and replaces only
    synthetic filler. No network, tool execution or model generation occurs.
    """
    name = 'fixture'
    default_model = 'model-a'

    def __init__(self):
        self.compactions = []
        self.counted = []
        self.sent = []
        self.nonreducing = False
        self.input_limit = 20000

    def get_info(self):
        from amplifier_core import ProviderInfo
        return ProviderInfo(id=self.name, display_name='Fixture',
            defaults={'model': self.default_model})

    def supports_native_compaction(self):
        return True

    def validate_compacted_context(self, message, *, model=None):
        return (message.get('metadata', {}).get('fixture-native', {}).get('model')
                == (model or self.default_model))

    @staticmethod
    def expanded(request):
        rows = []
        for message in request.messages:
            state = (message.metadata or {}).get('fixture-native')
            rows.extend(copy.deepcopy(state['rows']) if state else
                        [message.model_dump(exclude_none=True)])
        return rows

    def request_budget(self, request, *, context_estimate=0, **kwargs):
        rows = self.expanded(request)
        count = len(json.dumps({'messages': rows, 'tools': [
            tool.model_dump() for tool in request.tools or []]})) // 4
        self.counted.append((copy.deepcopy(rows), count))
        return {'estimated_input_tokens': count, 'input_limit_tokens': self.input_limit,
            'context_token_budget': context_estimate if count <= self.input_limit else 0,
            'measurement': {'kind': 'provider_count', 'input_tokens': count,
                            'source': 'synthetic-serialized-request'}}

    async def compact_context(self, request):
        rows = self.expanded(request)
        self.compactions.append(copy.deepcopy(rows))
        if not self.nonreducing:
            rows = [{**row, 'content': 'Synthetic research retained by reference.'}
                    if isinstance(row.get('content'), str)
                    and row['content'].startswith('FILLER:') else row for row in rows]
        return {'kind': 'native', 'message': {'role': 'user',
            'content': 'Native reference state; original history remains available.',
            'metadata': {'source': 'context-managed', 'ephemeral': True, 'persisted': True,
                         'fixture-native': {'model': request.model, 'rows': rows}}}}

    async def complete(self, request, **kwargs):
        self.sent.append(request.model_copy(deep=True))
        return SimpleNamespace(content=[SimpleNamespace(type='text', text='Synthetic final answer.')])


async def native_fixture(directory, original, *, provider=None, context_config=None):
    from amplifier_core import ChatRequest, Message, ToolSpec
    from amplifier_web.host.session import SelectedProvider
    from amplifier_web.surface_delivery import SurfaceProvider
    coordinator, _, adapter, checkpoint, transcript = await mount(directory, original, context_config=context_config)
    raw = provider or SyntheticNativeProvider()
    selected = SelectedProvider(raw, {'model': 'selected-model'})
    # Real host wrapper, with no unrelated surface/coordination input.
    selected.execution_adapter = lambda _: wrapped
    delivery = SimpleNamespace(prepare=AsyncMock(side_effect=lambda request, *_: request),
                               commit=lambda request: None)
    wrapped = SurfaceProvider(raw, delivery)
    coordinator.loop.root_provider = selected
    original_get = coordinator.get
    coordinator.get = lambda name: {'fixture': raw} if name == 'providers' else original_get(name)
    coordinator.context.config.update(max_tokens=20000, summarize_trigger=.2, native_min_new_tokens=0)
    coordinator.context.active_operations = lambda: [{'id': 'uncertain-op', 'outcome': 'unknown',
                                                     'instruction': 'Inspect, never replay.'}]
    async def instructions():
        return 'Accepted instruction: keep originals and never replay uncertain effects.'
    await coordinator.context.set_system_prompt_factory(instructions)
    async def count_view(rows):
        request = ChatRequest(messages=[Message(**row) for row in rows],
            tools=[ToolSpec(name='fixture_read', description='Read only', parameters={})])
        return {'dispatch': request, 'budget_decision':
                await selected.request_budget(request, context_estimate=10000)}
    async def prepare(*, finish_turn=False):
        options = dict(provider=selected, retain_contents=['Accepted reminder: retain result identities.'],
                       count_view=count_view)
        before = len(raw.compactions)
        result = await coordinator.context.get_measured_request_view(**options)
        if not finish_turn:
            return result
        after_turn = coordinator.context.requires_measured_foreground
        expected = before if after_turn else before + 1
        assert len(raw.compactions) == expected
        if transaction := result.get('transaction'):
            assert await transaction.commit()
        response = await selected.complete(result['final_attempt']['dispatch'])
        await coordinator.context.add_message({'role': 'assistant', 'content': response.content[0].text})
        await checkpoint()
        schedule = coordinator.context.schedule_settled_compaction
        if not after_turn:
            assert not schedule(**options)
            return result

        # Wait for maintenance to change the durable checkpoint through the host.
        previous = adapter.path.read_bytes() if adapter.path.exists() else None
        saved = asyncio.Event()
        persist = coordinator.get_capability('context.persist_checkpoint')
        def observe_save():
            outcome = persist()
            if adapter.path.is_file() and adapter.path.read_bytes() != previous:
                saved.set()
            return outcome
        coordinator.register_capability('context.persist_checkpoint', observe_save)
        try:
            assert schedule(**options)
            await asyncio.wait_for(saved.wait(), timeout=5)
        finally:
            coordinator.register_capability('context.persist_checkpoint', persist)
        return result
    return coordinator, raw, selected, adapter, checkpoint, transcript, prepare


def native_history():
    return [
        {'role': 'user', 'content': 'Build report; keep originals.'},
        {'role': 'assistant', 'content': 'FILLER:' + 'research ' * 4000},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'done-once', 'name': 'fixture_read', 'arguments': {'path': 'report.txt'}}]},
        {'role': 'tool', 'tool_call_id': 'done-once', 'name': 'fixture_read',
         'content': 'Verified result: report-v1; effect completed once.'},
        {'role': 'user', 'content': 'Correction: use report-v2.'},
        {'role': 'assistant', 'content': 'Acknowledged.'},
        {'role': 'user', 'content': 'Accepted reminder: retain result identities.',
         'metadata': {'ephemeral': True, 'persisted': True, 'reminder_placement': 'tail'}},
        {'role': 'user', 'content': 'Continue, without replaying effects.'},
    ]


async def test_native_measured_context_reduces_twice_and_restores_without_compaction_or_completion(tmp_path):
    original = native_history()
    c, raw, selected, adapter, checkpoint, transcript, prepare = await native_fixture(tmp_path, original)
    ends = []
    for cycle in range(2):
        if cycle:
            await c.context.add_message({'role': 'assistant', 'content': 'FILLER:' + 'new evidence ' * 3000})
            await c.context.add_message({'role': 'user', 'content': 'Correction: keep report-v3 too.'})
            await c.context.add_message({'role': 'assistant', 'content': 'Accepted.'})
            await c.context.add_message({'role': 'user', 'content': 'Continue cycle two.'})
        canonical = await c.context.get_messages()
        before = len(raw.counted)
        result = await prepare()
        dispatch = result['final_attempt']['dispatch']
        decision = result['final_attempt']['budget_decision']
        assert decision['estimated_input_tokens'] < raw.counted[before][1]
        assert decision['estimated_input_tokens'] <= decision['input_limit_tokens']
        print(json.dumps({'cycle': cycle + 1, 'synthetic_input_before': raw.counted[before][1],
                          'synthetic_input_after': decision['estimated_input_tokens'],
                          'input_limit': decision['input_limit_tokens'],
                          'through_message': c.context.summary[0]}))
        expanded = raw.expanded(dispatch)
        rendered = json.dumps(expanded)
        assert 'Accepted instruction: keep originals' in rendered
        assert 'Accepted reminder: retain result identities.' in rendered
        assert 'Verified result: report-v1; effect completed once.' in rendered
        assert 'uncertain-op' in rendered and 'Inspect, never replay.' in rendered
        assert 'Correction: use report-v2.' in rendered
        if cycle:
            assert 'Correction: keep report-v3 too.' in rendered
        assert any(row.get('tool_call_id') == 'done-once' for row in expanded)
        assert dispatch.tools[0].name == 'fixture_read'
        await selected.complete(dispatch)
        assert raw.expanded(raw.sent[-1]) == raw.counted[-1][0] == expanded
        assert raw.sent[-1].tools == dispatch.tools
        assert raw.sent[-1].model == 'selected-model'
        ends.append(c.context.summary[0])
        await checkpoint()
        assert await c.context.get_messages() == canonical
        assert json.loads(transcript.read_text()) == canonical
    assert ends[1] > ends[0] > 0
    assert len(raw.compactions) == len(raw.sent) == 2
    saved = adapter.path.read_bytes()
    newer, next_raw, _, next_adapter, _, _, next_prepare = await native_fixture(tmp_path, canonical)
    restored = await next_prepare()
    restored_rows = next_raw.expanded(restored['final_attempt']['dispatch'])
    assert restored_rows == raw.counted[-1][0]
    assert next_adapter.public()['status'] == 'restored'
    assert next_raw.compactions == next_raw.sent == []
    assert next_adapter.path.read_bytes() == saved
    assert await newer.context.get_messages() == canonical


async def test_native_nonreduction_blocks_dispatch_and_keeps_checkpoint_and_history(tmp_path):
    from amplifier_module_context_managed.errors import CompactionError
    c, raw, selected, adapter, checkpoint, transcript, prepare = await native_fixture(tmp_path, native_history())
    await prepare()
    await checkpoint()
    saved = adapter.path.read_bytes()
    accepted = copy.deepcopy(c.context.summary)
    raw.nonreducing = True
    await c.context.add_message({'role': 'assistant', 'content': 'FILLER:' + 'new evidence ' * 3000})
    await c.context.add_message({'role': 'user', 'content': 'New correction.'})
    await c.context.add_message({'role': 'assistant', 'content': 'Acknowledged.'})
    await c.context.add_message({'role': 'user', 'content': 'Continue.'})
    canonical = await c.context.get_messages()
    with pytest.raises(CompactionError, match='native_no_reduction'):
        await prepare()
    assert c.context.summary == accepted
    assert adapter.path.read_bytes() == saved
    assert json.loads(transcript.read_text()) == canonical
    assert raw.sent == []
    calls = len(raw.compactions)
    with pytest.raises(CompactionError, match='previous_failure'):
        await prepare()
    assert len(raw.compactions) == calls
    assert await c.context.get_messages() == canonical


async def test_native_reduction_does_not_bypass_oversized_required_current_input(tmp_path):
    from amplifier_core.llm_errors import ContextLengthError
    original = native_history()
    original[-1]['content'] = 'Accepted current input: ' + 'retain this exactly ' * 5000
    c, raw, _, _, _, transcript, prepare = await native_fixture(tmp_path, original)
    with pytest.raises(ContextLengthError):
        await prepare()
    assert raw.sent == []
    assert await c.context.get_messages() == original
    assert json.loads(transcript.read_text()) == original


# These are synthetic workload constants, not vendor tokenization or a diagnosis
# of the historical incident. Neither budget nor endpoint behavior changes after
# failure: only an explicitly requested smaller recovery batch can make progress.
RECOVERY_MODEL = 'gpt-6.1-sol'
RECOVERY_BUDGET = 200000
RECOVERY_TRIGGER = .1
RECOVERY_BATCH_BYTES = 110000
RECOVERY_LARGE_BYTES = 160000
RECOVERY_FILLER = 'x' * 100000
RECOVERY_INSTRUCTION = 'Current instruction: preserve ORBIT and original evidence.'
RECOVERY_REMINDER = 'Current reminder: retain result identities; never replay effects.'
RECOVERY_DONE = 'COMPLETED-EFFECT-17'
RECOVERY_UNKNOWN = {'id': 'UNKNOWN-OP-31', 'outcome': 'unknown',
                    'instruction': 'Inspect its receipt; never replay.'}
RECOVERY_PROJECTION = {
    'conversation', 'input', 'instructions', 'model', 'parallel_tool_calls',
    'previous_response_id', 'reasoning', 'text', 'tool_choice', 'tools', 'truncation',
}


class RecoveryWire:
    """Real SDK HTTP boundary; deterministic, content-dependent synthetic service."""

    def __init__(self):
        self.counts, self.compactions, self.completions = [], [], []
        self.events, self.views, self.preparations = [], [], []
        self.effects = self.unknown_invocations = 0
        self.stop_revision = self.epoch = 0
        self.unknown = copy.deepcopy(RECOVERY_UNKNOWN)
        self.reducing_models = set()
        self.instruction, self.reminder = RECOVERY_INSTRUCTION, RECOVERY_REMINDER

    @staticmethod
    def count(body):
        return max(1, len(json.dumps(body, sort_keys=True).encode()) // 4)

    @classmethod
    def reduce_filler(cls, value):
        if isinstance(value, dict):
            return {key: cls.reduce_filler(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls.reduce_filler(item) for item in value]
        if isinstance(value, str):
            return value.replace(RECOVERY_FILLER, '[synthetic filler retained in originals]')
        return value

    async def transport(self, request):
        body = json.loads(request.content)
        if request.url.path.endswith('/input_tokens'):
            count = self.count(body)
            self.counts.append((copy.deepcopy(body), count))
            return httpx.Response(200, json={'input_tokens': count})
        if request.url.path.endswith('/compact'):
            # Large windows retain all bytes and add opaque state (no reduction).
            # Small windows remove ONLY known filler; named facts, prior opaque
            # windows, function calls and actual results remain in order.
            large = (len(json.dumps(body['input']).encode()) > RECOVERY_LARGE_BYTES
                     and body['model'] not in self.reducing_models)
            window = (copy.deepcopy(body['input']) if large
                      else self.reduce_filler(body['input']))
            window.append({'type': 'compaction', 'encrypted_content': 'synthetic-' + digest(body)})
            self.compactions.append((copy.deepcopy(body), copy.deepcopy(window), large))
            return httpx.Response(200, json={
                'id': 'compact-' + digest(body), 'object': 'response.compaction',
                'created_at': 1, 'output': window,
                'usage': {'input_tokens': self.count(body), 'output_tokens': self.count(window),
                          'total_tokens': self.count(body) + self.count(window)},
            })
        assert request.url.path.endswith('/responses'), request.url
        assert not body.get('stream')
        self.completions.append(copy.deepcopy(body))
        fresh = 'CHECK-FRESH-READ-ONLY' in json.dumps(body['input'])
        completed = any(item.get('type') == 'function_call_output'
                        and RECOVERY_DONE in str(item.get('output'))
                        for item in body['input'])
        if not completed and not fresh:
            assert 'SEED-EFFECT' in json.dumps(body['input'])
            output = [{'type': 'function_call', 'id': 'fc-effect-once',
                       'call_id': 'effect-once', 'name': 'save_once',
                       'arguments': '{}', 'status': 'completed'}]
        else:
            text = ('Fresh read-only continuation.' if fresh else
                    'Finished adjacent correction: ship Tuesday; report-v2; UNKNOWN-OP-31 remains unknown.'
                    if 'ship Tuesday' in json.dumps(body['input']) else
                    'Usable continuation: report-v2; COMPLETED-EFFECT-17 retained; UNKNOWN-OP-31 remains unknown.')
            output = [{'type': 'message', 'id': 'msg-' + digest(body),
                       'role': 'assistant', 'status': 'completed',
                       'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}]
        count = self.count({key: value for key, value in body.items() if key in RECOVERY_PROJECTION})
        return httpx.Response(200, json={
            'id': 'response-' + digest(body), 'object': 'response', 'created_at': 1,
            'model': body['model'], 'status': 'completed', 'output': output,
            'usage': {'input_tokens': count, 'output_tokens': 20, 'total_tokens': count + 20},
        })


@asynccontextmanager
async def ordinary_recovery_case(directory, wire, messages=(), *, live=False,
                                 continuation=False, budget=RECOVERY_BUDGET,
                                 context_config=None, selection=None, foundation_store=None,
                                 provider_config=None):
    """Maintained native_case pattern: Core + PreparedBundle + the real loop.

    The fixture supplies host ownership/Stop observations and synthetic HTTP, not
    a substitute orchestrator, request builder, context recovery or provider.
    """
    from amplifier_core import HookResult, ToolResult
    from amplifier_web.host.session import SelectedProvider
    from amplifier_foundation.session.activation import ActivationGate
    plan = {'session': {
        'orchestrator': {'module': 'loop-streaming', 'config': {
            'use_streaming': False, 'extended_thinking': True,
            'min_delay_between_calls_ms': 0, 'ephemeral_injection_mode': 'tail'}},
        'context': {'module': 'context-managed', 'config': {
            'durable_checkpoints': True, 'archive_recovery': False,
            'max_tokens': budget, 'summarize_trigger': RECOVERY_TRIGGER,
            'native_min_new_tokens': 0, 'compaction_notice_enabled': False,
            'foreground_continuation': continuation}}}}
    if context_config is not None:
        plan['session']['context']['config'] = copy.deepcopy(context_config)
    paths = {'loop-streaming': Path(amplifier_module_loop_streaming.__file__).parent,
             'context-managed': Path(amplifier_module_context_managed.__file__).parent}
    if live:
        import amplifier_module_loop_live
        paths['loop-live'] = Path(amplifier_module_loop_live.__file__).parent
        plan['session']['orchestrator']['module'] = 'loop-live'
    session = await PreparedBundle(plan, BundleModuleResolver(paths),
        Bundle(name='ordinary-recovery', session=plan['session'])).create_session(
            session_id='ordinary-recovery')
    coordinator = session.coordinator
    context = coordinator.get('context')
    gate = ActivationGate()
    activation = gate.activate()
    runtime = SimpleNamespace(generation=None, queued_inputs=0, stop_requested=False, closed=False)
    if live:
        from amplifier_module_loop_live.runtime import Runtime
        runtime = Runtime(session.session_id)
    sdk = openai.AsyncOpenAI(api_key='synthetic-not-live', max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(wire.transport)))
    raw = provider_module.OpenAIProvider(client=sdk, config={
        'default_model': RECOVERY_MODEL, 'use_streaming': False, 'max_retries': 0,
        **(provider_config or {})})
    raw.coordinator = coordinator
    selected = SelectedProvider(raw, selection or {
        'instance': 'openai', 'model': RECOVERY_MODEL, 'effort': 'high', 'max_output_tokens': 1000})
    # The base streaming loop selects its mounted mapping; only loop-live has
    # a root_provider override. Mount the actual selected wrapper on this path.
    await coordinator.mount('providers', raw if selection else selected,
                            name=selected.selection['instance'])
    coordinator.get('orchestrator').root_provider = selected
    coordinator.register_capability('live.activation', gate)
    coordinator.register_capability('live.runtime', runtime)
    coordinator.register_capability('web.context_recovery.epoch', lambda: wire.epoch)
    coordinator.register_capability('context.active_operations', lambda: [copy.deepcopy(wire.unknown)])

    async def bridge(operation, args):
        assert operation == 'dispatch'
        assert args == {'action': 'coordination.list', 'args': {
            'sessionId': session.session_id, 'limit': 1, 'includeWorkers': False}}
        return {'result': {'items': [{'target': {'sessionId': session.session_id},
                                     'signal': {'interruptionRevision': wire.stop_revision}}]}}

    coordinator.register_capability('web.app_access.bridge', bridge)

    class SaveOnce:
        name = 'save_once'
        description = 'Save the synthetic report once'
        input_schema = {'type': 'object', 'properties': {}}

        async def execute(self, arguments):
            assert arguments == {}
            wire.effects += 1
            return ToolResult(success=True, output={
                'receipt': RECOVERY_DONE, 'artifact': 'report-v1', 'outcome': 'completed'})

    class UnknownOperation:
        name = 'unknown_operation'
        description = 'Sentinel that must never execute'
        input_schema = {'type': 'object', 'properties': {}}

        async def execute(self, arguments):
            wire.unknown_invocations += 1
            raise AssertionError('Recovery replayed an operation with an unknown outcome')

    await coordinator.mount('tools', SaveOnce(), name='save_once')
    await coordinator.mount('tools', UnknownOperation(), name='unknown_operation')
    await context.set_messages(copy.deepcopy(list(messages)))

    async def instructions():
        return wire.instruction

    async def reminder(event, data):
        return HookResult(action='inject_context', context_injection=wire.reminder,
                          context_injection_role='user', ephemeral=True)

    async def observe(event, data):
        wire.events.append((event, copy.deepcopy(data)))
        return HookResult()

    await context.set_system_prompt_factory(instructions)
    coordinator.hooks.register('prompt:submit', reminder, name='recovery-current-reminder')
    for event in ('provider:request_attribution', 'context:compaction_finished',
                  'orchestrator:provider_budget', 'tool:post'):
        coordinator.hooks.register(event, observe, priority=99, name='recovery-witness-' + event)
    transcript = directory / 'transcript.json'
    adapter = None

    async def checkpoint():
        gate.check_current()
        directory.mkdir(parents=True, exist_ok=True)
        rows = await context.get_messages()
        if foundation_store is not None:
            foundation_store.save(session.session_id, rows, {}, preserve_system=True)
        transcript.write_text(json.dumps(rows))
        if adapter:
            adapter.save()

    # Test composition also runs against the original host, which has no writer
    # seam. That baseline must reach the missing recovery control, not fail setup.
    writer = ({'writer_guard': gate.check_current}
              if 'writer_guard' in inspect.signature(ContextContinuity).parameters else {})
    adapter = ContextContinuity(coordinator, session.session_id, directory, checkpoint, **writer)
    controls = RuntimeControls(session, runtime)
    measured = coordinator.get_capability('context.measured_request_view')

    async def observe_preparation(**kwargs):
        wire.preparations.append(kwargs)
        result = await measured(**kwargs)
        wire.views.append(result)
        return result

    coordinator.register_capability('context.measured_request_view', observe_preparation)
    try:
        yield SimpleNamespace(session=session, context=context, raw=raw, selected=selected,
            adapter=adapter, controls=controls, runtime=runtime, gate=gate,
            activation=activation, checkpoint=checkpoint, transcript=transcript, measured=measured)
    finally:
        await controls.close()
        await sdk.close()
        await session.cleanup()


async def append_recovery_evidence(context, labels):
    for label in labels:
        await context.add_message({'role': 'user', 'content': f'Accepted evidence {label}: preserve ORBIT.'})
        await context.add_message({'role': 'assistant', 'content': f'FILLER-{label}: ' + RECOVERY_FILLER})
    for row in (
        {'role': 'user', 'content': 'Correction: use report-v2; budget 25.'},
        {'role': 'assistant', 'content': 'Verified correction retained.'},
        {'role': 'user', 'content': 'Keep the completed receipt and UNKNOWN-OP-31 outcome unknown.'},
        {'role': 'assistant', 'content': 'No effects were replayed.'},
    ):
        await context.add_message(row)


@pytest.mark.parametrize('cold', [False, True], ids=['warm', 'cold'])
async def test_ordinary_sdk_model_transition_counts_compacts_and_dispatches_selected_target(tmp_path, cold):
    """Actual Core/Streaming/Context/SelectedProvider/SDK pair, synthetic HTTP."""
    target_model = 'gpt-6.1-astra'
    wire, directory = RecoveryWire(), tmp_path / 'session'
    async with ordinary_recovery_case(directory, wire) as first:
        await first.session.execute('SEED-EFFECT: preserve ORBIT and completed receipt.')
        await append_recovery_evidence(first.context, ['A'])
        await first.session.execute('Establish accepted source checkpoint.')
        await first.checkpoint()
        accepted = first.adapter.path.read_bytes()
        await append_recovery_evidence(first.context, [str(n) for n in range(10)])
        canonical = copy.deepcopy(await first.context.get_messages())
        async def target_request(case):
            coordinator = case.session.coordinator
            # Real ordinary Streaming selects its mounted map, not loop-live's
            # root override. Change both through the actual mounted selection.
            await coordinator.mount('providers', case.raw, name='openai')
            await case.controls.perform('provider.select', {'instance': 'openai', 'model': target_model, 'effort': 'high'})
            target = coordinator.get('orchestrator').root_provider
            await coordinator.mount('providers', target, name='openai')
            coordinator.register_capability('web.context_transition.foreground', lambda: {
                'generationId': 'synthetic-ordinary-generation', 'inputId': 'fresh-target-input',
                'selectionRevision': case.controls.selection_revision,
                'selection': case.controls.selection})
            before = (len(wire.counts), len(wire.compactions), len(wire.completions))
            assert case.adapter.path.read_bytes() == accepted
            wire.reducing_models.add(target_model)
            text = await case.session.execute('Fresh target input: use report-v2 and never replay effects.')
            assert 'Usable continuation' in text
            assert case.adapter.recovery_status()['state'] == 'committed'
            assert case.adapter.recovery_status()['outcome'] == 'checkpoint-installed'
            assert all(body['model'] == target_model for body, _ in wire.counts[before[0]:])
            assert all(body['model'] == target_model for body, _, _ in wire.compactions[before[1]:])
            assert all(body['model'] == target_model for body in wire.completions[before[2]:])
            assert len(wire.compactions) > before[1] and len(wire.completions) == before[2] + 1
            body = wire.completions[-1]
            assert wire.counts[-1][0] == {k: v for k, v in body.items() if k in RECOVERY_PROJECTION}
            assert (await case.context.get_messages())[:len(canonical)] == canonical
            assert wire.effects == 1 and wire.unknown_invocations == 0
            assert wire.unknown == RECOVERY_UNKNOWN
        if not cold:
            await target_request(first)
            return
        first.gate.release(first.activation)
    async with ordinary_recovery_case(directory, wire, canonical) as second:
        await target_request(second)


def frozen_foreground_writer():
    """Unmodified Context a446 exports from the real historical module mount.

    The packet includes the old module/boundary hashes, exact effective config
    (including engine=boundary), generator scope and writer commit. Generation
    was mocked; the writer/export was real. Do not rebuild these records with the
    current reader, remove real configuration keys or recompute their self hashes.
    """
    import hashlib
    data = (Path(__file__).parent / 'fixtures/context_foreground_mounted_baseline.json').read_bytes()
    assert hashlib.sha256(data).hexdigest() == (
        '7e47bee54d2e209a5d163e97750a7a8112d91040d4fc7ecc8e7cf47f3e29f506')
    return json.loads(data)


@pytest.mark.parametrize('warm,lost_ack,other_recovery', [
    (False, False, None), (True, False, None), (False, True, None),
    (False, True, 'unknown'), (False, True, 'refused-progress')])
async def test_frozen_writer_composed_transition_keeps_source_proof_and_install_readback(
        tmp_path, monkeypatch, warm, lost_ack, other_recovery):
    from amplifier_core import ChatRequest, Message
    from amplifier_web.host.storage import SessionStore
    packet = frozen_foreground_writer()
    fixture = packet['fixtures']['portable-true']
    record, config = copy.deepcopy(fixture['checkpoint']), copy.deepcopy(fixture['config'])
    source = copy.deepcopy(packet['messages'])
    source.extend([{'role': 'user', 'content': f'Accepted evidence {i}'}
                   if i % 2 == 0 else {'role': 'assistant', 'content': RECOVERY_FILLER}
                   for i in range(8)])
    source.extend([row for i in range(4) for row in (
        {'role': 'user', 'content': f'Latest correction {i}: keep canonical evidence.'},
        {'role': 'assistant', 'content': 'Confirmed; no tools replayed.'})])
    store = SessionStore.for_app(tmp_path / 'home', tmp_path)
    store.save('ordinary-recovery', source, {}, preserve_system=True)
    source = store.load('ordinary-recovery')[0]
    assert source[:len(packet['messages'])] == packet['messages']
    directory = tmp_path / 'session'
    directory.mkdir()
    path = directory / 'context-checkpoint.json'
    path.write_text(json.dumps(record))
    saved = path.read_bytes()
    wire = RecoveryWire()
    source_selection = {'instance': packet['identity']['provider'],
                        'model': packet['identity']['model'], 'effort': 'high'}
    async with ordinary_recovery_case(directory, wire, source, context_config=config,
                                     selection=source_selection, foundation_store=store) as case:
        if warm:
            await case.adapter.preserve(source)
            assert case.adapter.status['status'] == 'restored', case.adapter.status
            assert case.context.export_checkpoint(packet['identity']) == record
        target_model = 'gpt-6.1-astra'
        await case.controls.perform('provider.select', {
            'instance': 'fixture', 'model': target_model, 'effort': 'high'})
        target = case.session.coordinator.get('orchestrator').root_provider
        origin = {'generationId': 'frozen-target', 'inputId': 'target-input',
                  'selectionRevision': case.controls.selection_revision,
                  'selection': case.controls.selection, 'connection': 'fixture-connection'}
        coordinator = case.session.coordinator
        coordinator.register_capability('web.context_transition.foreground', lambda: copy.deepcopy(origin))
        classify = coordinator.get_capability('context.checkpoint.transition')
        classified = []
        def observe_classification(checkpoint, identity):
            result = classify(checkpoint, identity)
            classified.append(copy.deepcopy(result))
            return result
        coordinator.register_capability('context.checkpoint.transition', observe_classification)
        await case.adapter.preserve(source)
        assert classified[-1]['status'] == 'transition_required', (
            classified[-1], case.context.config, fixture['config'])
        assert classified[-1]['configuration'] == record['configuration']
        assert classified[-1]['sha256'] == record['sha256']
        assert path.read_bytes() == saved
        wire.reducing_models.add(target_model)
        async def count_view(rows):
            request = ChatRequest(model=target_model, max_output_tokens=1000,
                                  messages=[Message(**row) for row in rows])
            decision = target.request_budget(request, context_estimate=10000)
            if inspect.isawaitable(decision):
                decision = await decision
            return {'dispatch': request, 'budget_decision': decision}
        async def prepare():
            return await case.measured(provider=target, retain_contents=(), count_view=count_view)
        recover = coordinator.get_capability('context.recovery.recover')
        installs = []
        from amplifier_web import context_continuity
        write_private = context_continuity.write_private
        writes = []
        def observe_write(destination, text):
            if destination == path:
                writes.append(text)
            return write_private(destination, text)
        monkeypatch.setattr(context_continuity, 'write_private', observe_write)
        async def observed_recover(**kwargs):
            install = kwargs['install']
            async def observed_install(candidate, verification):
                result = await install(candidate, verification)
                installs.append(copy.deepcopy(candidate))
                if lost_ack:
                    raise OSError('synthetic lost frozen-source installation acknowledgement')
                return result
            return await recover(**{**kwargs, 'install': observed_install})
        coordinator.register_capability('context.recovery.recover', observed_recover)
        if lost_ack:
            with pytest.raises(OSError, match='lost frozen-source'):
                await prepare()
            pending = case.adapter._recovery_record()
            assert pending['state'] == 'unknown'
            assert pending['sourceCheckpoint'] == record
            assert pending['sourceCheckpointDigest'] == digest(record)
            assert pending['sourceCheckpointSha256'] == record['sha256']
            candidate_bytes = path.read_bytes()
            before = len(wire.compactions)
            if other_recovery:
                # A valid older journal may contain another unresolved owner.
                # Candidate readback must not rebind past it on cold load.
                other = {key: copy.deepcopy(pending[key]) for key in (
                    'interruptionRevision', 'targetBytes', 'contract', 'identity',
                    'sourceRevision', 'completedParts')}
                other.update(commandId='other-generic',
                    state='unknown' if other_recovery == 'unknown' else 'refused',
                    progress={'retained': 'paid-prefix'} if other_recovery == 'refused-progress' else None)
                if other_recovery == 'refused-progress':
                    other['completedParts'] = 1
                case.adapter._journal['attempts']['other-generic'] = other
                case.adapter._write_recovery()
                journal_bytes = case.adapter.recovery_path.read_bytes()
                case.adapter._journal = None  # Actual parsed two-row cold journal.
                origin.update(inputId='new-target-input', generationId='new-frozen-generation')
                coordinator.register_capability('context.recovery.recover', recover)
                with pytest.raises(RuntimeError, match='recovery_unreconciled'):
                    await prepare()
                assert case.adapter.recovery_path.read_bytes() == journal_bytes
                assert path.read_bytes() == candidate_bytes
                assert len(installs) == len(writes) == 1
                assert len(wire.compactions) == before
                return
            with pytest.raises(RuntimeError, match='recovery_unreconciled'):
                await prepare()
            origin.update(inputId='new-target-input', generationId='new-frozen-generation')
            coordinator.register_capability('context.recovery.recover', recover)
            await prepare()
            assert path.read_bytes() == candidate_bytes and len(wire.compactions) == before
        else:
            result = await prepare()
            assert result['outcome'] == 'transitioned'
        assert case.adapter.recovery_status()['state'] == 'committed'
        assert len(installs) == len(writes) == 1 and wire.compactions
        assert store.load('ordinary-recovery')[0] == source
        assert await case.context.get_messages() == source
        assert wire.effects == wire.unknown_invocations == 0


async def test_frozen_paid_partial_explicit_refusal_retains_real_writer_and_ordinary_resume(tmp_path):
    from amplifier_module_context_managed.errors import CompactionError
    from amplifier_web.host.storage import SessionStore
    packet = frozen_foreground_writer()
    fixture = packet['fixtures']['partial-v5-true']
    record = copy.deepcopy(fixture['checkpoint'])
    store = SessionStore.for_app(tmp_path / 'home', tmp_path)
    store.save('ordinary-recovery', packet['messages'], {}, preserve_system=True)
    source = store.load('ordinary-recovery')[0]
    directory = tmp_path / 'session'
    directory.mkdir()
    path = directory / 'context-checkpoint.json'
    path.write_text(json.dumps(record))
    saved = path.read_bytes()
    wire = RecoveryWire()
    selection = {'instance': packet['identity']['provider'],
                 'model': packet['identity']['model'], 'effort': 'high'}
    async with ordinary_recovery_case(directory, wire, source, context_config=fixture['config'],
                                     selection=selection, foundation_store=store) as case:
        await case.adapter.preserve(source)
        assert case.adapter.status['status'] == 'resuming'
        assert case.context.export_checkpoint(packet['identity']) == record
        await case.controls.perform('context.recovery.request',
                                    recovery_command(wire, 'explicit-over-paid-prefix'))
        async def forbidden_count(rows):
            raise AssertionError('Explicit native recovery counted before refusing a paid partial')
        with pytest.raises(CompactionError, match='summary_progress_incompatible'):
            await case.adapter.run_pending(provider=case.selected, count_view=forbidden_count)
        assert case.context.export_checkpoint(packet['identity']) == record
        assert path.read_bytes() == saved  # Refused recovery did not write a candidate.
        await case.checkpoint()
        assert json.loads(path.read_bytes()) == record
        # Ordinary host persistence serializes its own JSON key order. It keeps
        # the entire exact record/self hash, not the imported fixture whitespace.
        saved = path.read_bytes()
        assert wire.counts == wire.compactions == wire.completions == []
        assert store.load('ordinary-recovery')[0] == source
    async with ordinary_recovery_case(directory, wire, source, context_config=fixture['config'],
                                     selection=selection, foundation_store=store) as cold:
        await cold.adapter.preserve(source)
        assert cold.adapter.status['status'] == 'resuming'
        assert cold.context.export_checkpoint(packet['identity']) == record
        # Generic refusal is inert; ordinary preparation still sees its paid
        # prefix, not a discarded baseline or a renewed explicit-recovery call.
        assert await cold.adapter.run_pending(provider=cold.selected, count_view=forbidden_count) is None
        assert cold.context.export_checkpoint(packet['identity'])['progress'] == record['progress']
        assert path.read_bytes() == saved
        assert await cold.context.get_messages() == source
        assert wire.counts == wire.compactions == wire.completions == []


async def test_old_sdk_writer_paid_prefix_can_continue_after_explicit_refusal(tmp_path):
    """Real old writer+SDK packet, current composed SDK reader, offline transport."""
    import hashlib
    from amplifier_web.host.storage import SessionStore
    from amplifier_module_context_managed.errors import CompactionError
    data = (Path(__file__).parent / 'fixtures/context_old_sdk_partial.json').read_bytes()
    assert hashlib.sha256(data).hexdigest() == (
        '74d4121f0d203b090885d96abb6c380d79ac559639aa27aa12cd594e00d4f280')
    packet = json.loads(data)
    assert packet['source_commit'] == 'a4464af2027f4e03fe7fc388d8d8e3e8ff436da2'
    record = packet['checkpoint']
    class PortableWire(RecoveryWire):
        async def transport(self, request):
            body = json.loads(request.content)
            if (request.url.path.endswith('/responses')
                    and 'continuation note' in str(body.get('input'))):
                self.completions.append(copy.deepcopy(body))
                return httpx.Response(200, json={
                    'id': 'resume-summary', 'object': 'response', 'created_at': 1,
                    'model': body['model'], 'status': 'completed', 'output': [{
                        'type': 'message', 'id': 'resumed-note', 'role': 'assistant',
                        'status': 'completed', 'content': [{'type': 'output_text',
                            'text': 'Continued from PAID-SDK-PREFIX without repurchasing it.',
                            'annotations': []}]}],
                    'usage': {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120}})
            return await super().transport(request)
    wire = PortableWire()
    directory = tmp_path / 'session'
    directory.mkdir()
    path = directory / 'context-checkpoint.json'
    path.write_text(json.dumps(record))
    before = path.read_bytes()
    selection = {'instance': 'fixture', 'model': packet['identity']['model'], 'effort': 'high'}
    store = SessionStore.for_app(tmp_path / 'home', tmp_path)
    store.save('ordinary-recovery', packet['messages'], {}, preserve_system=True)
    async with ordinary_recovery_case(directory, wire, packet['messages'],
            context_config=packet['config'], selection=selection, foundation_store=store) as case:
        await case.adapter.preserve(packet['messages'])
        assert case.context.export_checkpoint(packet['identity']) == record
        await case.controls.perform('context.recovery.request',
                                    recovery_command(wire, 'refuse-native-over-old-sdk-paid'))
        async def forbidden_count(rows):
            raise AssertionError('Native recovery counted before refusing old paid prefix')
        with pytest.raises(CompactionError, match='summary_progress_incompatible'):
            await case.adapter.run_pending(provider=case.selected, count_view=forbidden_count)
        assert path.read_bytes() == before
        assert wire.counts == wire.compactions == wire.completions == []
    # The old writer used an explicit OpenAI-compatible endpoint (portable,
    # not native). Reproduce that supported endpoint choice, never change the
    # paid prefix's model, provider identity or summary generation options.
    async with ordinary_recovery_case(directory, wire, packet['messages'],
            context_config=packet['config'], selection=selection, foundation_store=store,
            provider_config={'base_url': 'https://offline.invalid/v1'}) as cold:
        cold.raw.client.base_url = 'https://offline.invalid/v1'
        assert not cold.raw.supports_native_compaction()
        await cold.adapter.preserve(packet['messages'])
        assert cold.context.export_checkpoint(packet['identity']) == record
        assert await cold.adapter.run_pending(provider=cold.selected, count_view=None) is None
        with pytest.raises(CompactionError, match='summary_work_paused'):
            await cold.context._prepare(cold.raw, None, [])
        continued = cold.context.export_checkpoint(packet['identity'])
        assert continued['progress']['completed'] > record['progress']['completed']
        assert continued['progress']['fingerprint'] == record['progress']['fingerprint']
        assert continued['progress']['generation_contract'] == record['progress']['generation_contract']
        assert len(wire.completions) == 1  # Only the next fragment was purchased.
        body = wire.completions[0]
        assert record['progress']['text'] in str(body)
        assert body['model'] == packet['identity']['model']
        assert body['max_output_tokens'] == record['progress']['generation_contract']['max_output_tokens']
        assert wire.compactions == [] and wire.effects == wire.unknown_invocations == 0
        assert await cold.context.get_messages() == packet['messages']


@pytest.mark.parametrize('legacy_fork', [False, True])
async def test_real_worker_controls_live_sdk_model_transition_admission(tmp_path, monkeypatch, legacy_fork):
    """No foreground lambda: Worker admission → native delivery → controls → SDK."""
    import asyncio
    from amplifier_web.runtime_worker import Worker
    from amplifier_module_loop_live.runtime import Input
    wire = RecoveryWire()
    instance = 'sol' if legacy_fork else 'openai'
    source = ({'instance': instance, 'model': 'gpt-6.1-terra', 'effort': 'high',
               'max_output_tokens': 1000} if legacy_fork else None)
    async with ordinary_recovery_case(tmp_path, wire, live=True, selection=source) as case:
        worker = Worker()
        worker.session, worker.runtime, worker.controls = case.session, case.runtime, case.controls
        worker.activation_gate, worker.activation = case.gate, case.activation
        worker.start_config = {'interruptionRevision': 7}
        case.runtime.capture_activation = case.gate.current
        token = case.gate.bind(case.activation)
        try:
            worker.execution = asyncio.create_task(case.session.execute(''))
        finally:
            case.gate.reset(token)
        case.runtime.observer = worker.observe
        monkeypatch.setattr('amplifier_web.runtime_worker.publish', lambda _: None)
        coordinator = case.session.coordinator
        coordinator.register_capability('web.context_transition.input', worker.transition_input)
        async def send(identity, text, **options):
            before = case.runtime.sequence
            await worker._command_serial({'op': 'send', 'id': 'command-' + identity,
                'input_id': identity, 'text': text, 'interruptionRevision': 7, **options})
            await asyncio.wait_for(case.runtime.wait_for(lambda event:
                event['type'] in {'generation.finished', 'generation.failed'}
                and event.get('sequence', 0) > before and identity in event.get('input_ids', []),
                timeout=10), 12)
            terminal = next(event for event in reversed(case.runtime.events)
                            if event['type'] in {'generation.finished', 'generation.failed'})
            assert terminal['type'] == 'generation.finished', terminal
        try:
            await send('seed', 'SEED-EFFECT: preserve ORBIT.')
            await append_recovery_evidence(case.context, ['A'])
            await send('source', 'Establish accepted source context.')
            await case.checkpoint()
            await append_recovery_evidence(case.context, [str(n) for n in range(10)])
            await coordinator.mount('providers', case.raw, name=instance)
            # Same accepted model family but a different exact identity.
            target_model = 'gpt-6.2-astra' if legacy_fork else 'gpt-6.1-astra'
            selection = {'instance': instance, 'model': target_model,
                         'effort': 'xhigh' if legacy_fork else 'high'}
            options = {}
            if legacy_fork:
                monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'fork-home'))
                coordinator.register_capability('web.context_transition.originals', True)
                coordinator.register_capability('web.context_transition.fork_parent', 'original-parent')
                coordinator.register_capability('web.provider_catalog', {'connectionKeys': {instance: 'same-account'}})
                state = case.controls.state_path()
                state.parent.mkdir(parents=True, exist_ok=True)
                state.write_text(json.dumps({'selection': selection, 'selectionRevision': None}))
                await case.controls.restore()
                assert case.controls.selection_revision is None
                options['applied_fork_choice'] = {'sessionId': case.session.session_id, 'parentId': 'original-parent',
                    'selection': selection, 'connection': 'same-account', 'revision': 1}
            else:
                await case.controls.perform('provider.select', selection)
            before = (len(wire.counts), len(wire.compactions), len(wire.completions))
            wire.reducing_models.add(target_model)
            await send('target', 'New authorized target input; never replay effects.', **options)
            assert case.adapter.recovery_status()['state'] == 'committed'
            assert all(body['model'] == target_model for body, _ in wire.counts[before[0]:])
            assert all(body['model'] == target_model for body, _, _ in wire.compactions[before[1]:])
            assert len(wire.compactions) > before[1]
            assert len(wire.completions) == before[2] + 1
            assert wire.completions[-1]['model'] == target_model
            assert wire.completions[-1]['reasoning']['effort'] == selection['effort']
            assert wire.effects == 1 and wire.unknown_invocations == 0
            row = case.adapter._recovery_record()
            assert row['foreground']['inputId'] == 'target'
            assert row['foreground']['selectionRevision'] == case.controls.selection_revision
            print(json.dumps({'composedAdmission': {
                'inputId': row['foreground']['inputId'],
                'durableHostStop': worker.context_bindings['target']['interruptionRevision'],
                'localStop': worker.context_bindings['target']['localStopRevision'],
                'counts': len(wire.counts) - before[0],
                'compactions': len(wire.compactions) - before[1],
                'creates': len(wire.completions) - before[2],
                'target': target_model, 'effects': wire.effects,
                'unknownInvocations': wire.unknown_invocations}}, sort_keys=True))
        finally:
            if not case.runtime.closed:
                await case.runtime.submit(Input('stop', activation=case.activation))
            await asyncio.wait_for(worker.execution, 5)


async def sdk_transition_process_phase(directory, phase):
    """Two exited interpreters use only durable host files and the real SDK seam."""
    import os
    target_model = 'gpt-6.1-astra'
    class PausedWire(RecoveryWire):
        async def transport(self, request):
            body = json.loads(request.content)
            if (phase == 'prepare' and request.url.path.endswith('/input_tokens')
                    and body.get('model') == target_model
                    and any(row[0]['model'] == target_model for row in self.compactions)):
                return httpx.Response(400, json={'error': {'message': 'synthetic known final-count failure',
                    'type': 'invalid_request_error', 'code': 'invalid_request'}})
            return await super().transport(request)
    directory = Path(directory)
    wire = PausedWire()
    canonical = []
    if phase == 'continue':
        canonical = json.loads((directory / 'transcript.json').read_text())
        wire.effects = 1  # Durable fixture effect counter, not another execution.
    async with ordinary_recovery_case(directory, wire, canonical) as case:
        await case.session.coordinator.mount('providers', case.raw, name='openai')
        before_calls = 0
        if phase == 'prepare':
            await case.session.execute('SEED-EFFECT: preserve ORBIT.')
            await append_recovery_evidence(case.context, ['A'])
            await case.session.execute('Establish source checkpoint.')
            await case.checkpoint()
            await append_recovery_evidence(case.context, [str(n) for n in range(10)])
            await case.controls.perform('provider.select', {'instance': 'openai', 'model': target_model, 'effort': 'high'})
        else:
            await case.controls.restore()
            before_calls = case.adapter.recovery_status()['logicalCalls']
        coordinator = case.session.coordinator
        target = coordinator.get('orchestrator').root_provider
        await coordinator.mount('providers', target, name='openai')
        coordinator.register_capability('web.context_transition.foreground', lambda: {
            'generationId': 'generation-' + phase, 'inputId': 'input-' + phase,
            'selectionRevision': case.controls.selection_revision, 'selection': case.controls.selection})
        wire.reducing_models.add(target_model)
        if phase == 'prepare':
            try:
                await case.session.execute('Prepare target context; preserve completed effect.')
            except RuntimeError:
                pass
            else:
                raise AssertionError('Synthetic final-count failure did not occur')
            record = case.adapter._recovery_record()
            assert record['state'] == 'refused' and record['progress']
            assert record['calls'] and all(row['state'] == 'completed' for row in record['calls'])
            assert len(wire.completions) == 3  # Seed tool request/result + source response.
        else:
            assert 'Usable continuation' in await case.session.execute('New authorized input after actual interpreter restart.')
            assert case.adapter.recovery_status()['state'] == 'committed'
            # New input advances eligible coverage. Only new pieces may be
            # bought; the old acknowledged native prefix stays the seed.
            assert case.adapter.recovery_status()['logicalCalls'] == before_calls + len(wire.compactions)
            assert all(any(item.get('type') == 'compaction' for item in body['input'])
                       for body, _, _ in wire.compactions)
            assert len(wire.completions) == 1 and wire.effects == 1 and wire.unknown_invocations == 0
        return {'phase': phase, 'state': case.adapter.recovery_status()['state'],
                'pid': os.getpid(),
                'compactions': len(wire.compactions), 'effects': wire.effects,
                'logicalCalls': case.adapter.recovery_status()['logicalCalls'],
                'requests': [call['report']['requestSha256'] for call in case.adapter._recovery_record()['calls']]}


async def test_sdk_transition_confirmed_progress_survives_two_exited_interpreters(tmp_path):
    import asyncio
    import os
    import sys
    script = Path(__file__).parent / 'fixtures' / 'model_transition_restart.py'
    reports = []
    for phase in ('prepare', 'continue'):
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(script), str(tmp_path / 'durable'), phase,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=dict(os.environ))
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
        assert process.returncode == 0, (stdout.decode(), stderr.decode())
        reports.append(json.loads(stdout.decode().splitlines()[-1]))
    assert [row['state'] for row in reports] == ['refused', 'committed']
    assert reports[0]['compactions'] > 0
    assert reports[1]['logicalCalls'] == reports[0]['logicalCalls'] + reports[1]['compactions']
    assert reports[1]['requests'][:reports[0]['logicalCalls']] == reports[0]['requests']
    assert len(set(reports[1]['requests'])) == len(reports[1]['requests'])
    assert reports[0]['pid'] != reports[1]['pid']
    print(json.dumps({'twoExitedInterpreterEvidence': reports}, sort_keys=True))


def recovery_command(wire, command_id='recover-once'):
    # Represents a trusted command supplied by the host management boundary.
    # Management authorization tests belong to the parent, not this fixture.
    return {'_recoveryCommand': {'id': command_id, 'interruptionRevision': wire.stop_revision},
            'targetBytes': RECOVERY_BATCH_BYTES}


def assert_recovery_dispatch(wire, result, *, adjacent=False):
    final = result['final_attempt']
    decision = final['budget_decision']
    assert decision['estimated_input_tokens'] <= min(
        RECOVERY_BUDGET, decision['input_limit_tokens'])
    body = wire.completions[-1]
    assert wire.counts[-1][0] == {
        key: value for key, value in body.items() if key in RECOVERY_PROJECTION}
    # The maintained provider's public count contract does not include the
    # separately qualified attribution extension. Compare the actual SDK HTTP
    # count/create projections above and the returned authoritative count here.
    assert decision['measurement']['kind'] == 'provider_count'
    assert decision['measurement']['input_tokens'] == wire.counts[-1][1]
    assert wire.counts[-1][1] == wire.count(wire.counts[-1][0])
    assert body['model'] == RECOVERY_MODEL and body['max_output_tokens'] == 1000
    assert body['reasoning']['effort'] == 'high'
    assert {tool['name'] for tool in body['tools']} == {'save_once', 'unknown_operation'}
    rendered = json.dumps(body)
    for fact in (wire.instruction, wire.reminder, 'ORBIT', 'report-v2',
                 'budget 25', RECOVERY_DONE, 'UNKNOWN-OP-31', 'unknown', 'never replay'):
        assert fact in rendered
    assert '"outcome": "unknown"' in str(body)
    assert not any(item.get('type') == 'function_call_output' and item.get('call_id') == 'UNKNOWN-OP-31'
                   for item in body['input'])
    if adjacent:
        assert 'ship Tuesday' in rendered
    assert wire.effects == 1 and wire.unknown_invocations == 0
    assert wire.unknown == RECOVERY_UNKNOWN


@pytest.mark.parametrize('prior_checkpoint', [False, True], ids=['without-prior', 'with-prior'])
async def test_explicit_recovery_in_ordinary_loop_then_adjacent_compaction(
        tmp_path, monkeypatch, prior_checkpoint):
    """#1198/#1207: actual failed preparation -> explicit intent -> normal loop."""
    from amplifier_module_context_managed.errors import CompactionError
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    wire = RecoveryWire()
    async with ordinary_recovery_case(tmp_path / 'session', wire) as case:
        await case.session.execute('SEED-EFFECT: save report-v1 once; preserve ORBIT.')
        assert wire.effects == 1
        assert all(body['max_output_tokens'] == 1000 for body in wire.completions)
        actual_receipt = copy.deepcopy(next(data for event, data in wire.events if event == 'tool:post'))
        if prior_checkpoint:
            await append_recovery_evidence(case.context, ['PRIOR'])
            await case.session.execute('Establish the prior report checkpoint.')
            assert case.context.summary is not None
        await case.checkpoint()
        prior = copy.deepcopy(case.context.summary)
        saved = case.adapter.path.read_bytes() if case.adapter.path.exists() else None
        await append_recovery_evidence(case.context, ['A', 'B', 'C'])
        prefix = copy.deepcopy(await case.context.get_messages())
        before_calls = len(wire.completions)
        with pytest.raises(RuntimeError, match=r'Execution failed: CompactionError:.*native_no_reduction'):
            await case.session.execute('Failing ordinary request: finish from the retained evidence.')
        failed_source = copy.deepcopy(await case.context.get_messages())
        assert failed_source[:len(prefix)] == prefix
        assert case.context.summary == prior
        assert (case.adapter.path.read_bytes() if case.adapter.path.exists() else None) == saved
        assert json.loads(case.transcript.read_text()) == failed_source
        assert len(wire.completions) == before_calls and wire.compactions[-1][2] is True
        failure = copy.deepcopy([data for event, data in wire.events
                                 if event == 'context:compaction_finished'][-1])
        assert failure['failure']['code'] == 'native_no_reduction'
        assert failure['native_input_tokens_after'] >= failure['input_tokens_before']
        calls = len(wire.compactions)
        # Retry the same preparation, NOT a new human input or changed source.
        with pytest.raises(CompactionError, match='previous_failure'):
            await case.measured(**wire.preparations[-1])
        assert len(wire.compactions) == calls and len(wire.completions) == before_calls
        assert await case.context.get_messages() == failed_source

        # A saved count_view from the failed turn cannot satisfy recovery: the
        # next ordinary loop must assemble these current instructions/reminders.
        wire.instruction += ' Current instruction revision: BETA.'
        wire.reminder += ' Current reminder revision: BETA.'
        before_arm = (len(wire.counts), len(wire.compactions), len(wire.completions))
        task_before = copy.deepcopy(case.controls.tasks.record())
        contract = copy.deepcopy({'plan': case.session.coordinator.config,
                                  'context': case.context.config,
                                  'selection': case.controls.selection})
        pending = await case.controls.perform('context.recovery.request', recovery_command(wire))
        assert pending['state'] == 'pending' and pending['attempts'] == 1
        assert await case.controls.perform('context.recovery.request', recovery_command(wire)) == pending
        assert await case.controls.perform('context.recovery.status') == pending
        assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before_arm
        assert await case.context.get_messages() == failed_source
        assert case.controls.tasks.record() == task_before
        assert case.runtime.generation is None and case.runtime.queued_inputs == 0
        result = await case.session.execute('Explicit fresh input: continue using report-v2 without replay.')
        assert 'Usable continuation: report-v2' in result
        recovered = wire.views[-1]
        assert recovered['outcome'] == 'recovered'
        assert_recovery_dispatch(wire, recovered)
        status = await case.controls.perform('context.recovery.status')
        assert status['state'] == 'committed' and status['attempts'] == 1
        assert status['completedParts'] > 1
        assert status['verification']['after'] < status['verification']['before']
        assert status['verification']['after'] <= status['verification']['limit']
        assert status['verification']['limit'] <= RECOVERY_BUDGET
        assert len(wire.completions) == before_calls + 1
        assert all(not row[2] for row in wire.compactions[calls:])
        assert {'plan': case.session.coordinator.config, 'context': case.context.config,
                'selection': case.controls.selection} == contract
        recovered_window = copy.deepcopy(case.context.summary[1]['metadata']['openai:compaction']['output'])
        recovered_end = case.context.summary[0]
        recovered_source = copy.deepcopy(await case.context.get_messages())
        assert recovered_source[:len(failed_source)] == failed_source

        await append_recovery_evidence(case.context, ['ADJACENT'])
        await case.context.add_message({'role': 'user', 'content': 'Latest settled correction: ship Tuesday.'})
        await case.context.add_message({'role': 'assistant', 'content': 'Tuesday accepted; preserve report-v2.'})
        adjacent_prefix = copy.deepcopy(await case.context.get_messages())
        adjacent_calls = len(wire.compactions)
        wire.instruction = RECOVERY_INSTRUCTION + ' Current instruction revision: GAMMA.'
        wire.reminder = RECOVERY_REMINDER + ' Current reminder revision: GAMMA.'
        result = await case.session.execute('Finish the adjacent cycle with the retained report.')
        assert 'Finished adjacent correction: ship Tuesday' in result
        assert len(wire.compactions) == adjacent_calls + 1
        assert wire.compactions[-1][0]['input'][:len(recovered_window)] == recovered_window
        assert case.context.summary[0] > recovered_end
        assert_recovery_dispatch(wire, wire.views[-1], adjacent=True)
        assert {'plan': case.session.coordinator.config, 'context': case.context.config,
                'selection': case.controls.selection} == contract
        assert await case.controls.perform('context.recovery.status') == status
        assert ('context:compaction_finished', failure) in wire.events
        assert ('tool:post', actual_receipt) in wire.events
        canonical = await case.context.get_messages()
        assert canonical[:len(adjacent_prefix)] == adjacent_prefix
        assert canonical[:len(recovered_source)] == recovered_source
        assert sum(row.get('role') == 'tool' and row.get('tool_call_id') == 'effect-once'
                   for row in canonical) == 1
        assert not any(row.get('role') == 'tool' and row.get('tool_call_id') == 'UNKNOWN-OP-31'
                       for row in canonical)
        await case.checkpoint()
        assert json.loads(case.transcript.read_text()) == canonical


@pytest.mark.parametrize('change', [
    'pending', 'stop', 'configuration', 'prefix', 'running', 'installing', 'unknown',
])
async def test_recovery_intent_remount_and_preparation_fences(tmp_path, monkeypatch, change):
    """A pending intent survives remount; uncertain consumed work never retries."""
    from amplifier_module_context_managed.errors import CompactionError
    from amplifier_web.context_continuity import ContextRecoveryError
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    directory, wire = tmp_path / 'session', RecoveryWire()
    async with ordinary_recovery_case(directory, wire) as first:
        await first.session.execute('SEED-EFFECT: save report-v1 once; preserve ORBIT.')
        await append_recovery_evidence(first.context, ['PRIOR'])
        await first.session.execute('Establish a compatible prior checkpoint.')
        await first.checkpoint()
        prior = copy.deepcopy(first.context.summary)
        saved = first.adapter.path.read_bytes()
        await append_recovery_evidence(first.context, ['A', 'B', 'C'])
        with pytest.raises(RuntimeError, match=r'Execution failed: CompactionError:.*native_no_reduction'):
            await first.session.execute('Failing ordinary preparation before remount.')
        source = copy.deepcopy(await first.context.get_messages())
        command = recovery_command(wire)
        pending = await first.controls.perform('context.recovery.request', command)
        assert pending['state'] == 'pending'
        journal_path = first.adapter.recovery_path
        if change in {'running', 'installing', 'unknown'}:
            # Exact durable crash states, not fabricated successful recovery.
            # No candidate is installed and no provider response is invented.
            journal = json.loads(journal_path.read_text())
            journal['attempts'][journal['current']]['state'] = change
            if change == 'installing':
                journal['attempts'][journal['current']]['candidateSha256'] = json.loads(saved)['sha256']
            journal_path.write_text(json.dumps(journal))
        first.gate.release(first.activation)

    retained_journal = journal_path.read_bytes()
    if change == 'stop':
        wire.stop_revision += 1  # Durable host Stop, independent of a new activation.
    if change == 'prefix':
        source[0]['content'] += ' Changed canonical prefix.'
    before = (len(wire.counts), len(wire.compactions), len(wire.completions))
    async with ordinary_recovery_case(directory, wire, source) as second:
        assert second.gate is not first.gate
        assert second.activation is not first.activation
        assert second.gate.current_valid and not first.gate.current_valid
        assert not second.adapter.loaded and second.context.summary is None
        if change == 'configuration':
            second.context.config['summarize_trigger'] = .15
        uncertain = change in {'running', 'installing', 'unknown'}
        status = await second.controls.perform('context.recovery.status')
        assert status['state'] == ('unknown' if uncertain else 'pending')
        assert status['attempts'] == 1
        assert await second.controls.perform('context.recovery.request', command) == status
        assert journal_path.read_bytes() == retained_journal  # Read/duplicate never rearm.
        assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before
        assert await second.context.get_messages() == source
        assert second.adapter.path.read_bytes() == saved
        if change == 'pending':
            assert 'Usable continuation' in await second.session.execute('Fresh continuation after remount.')
            recovered = wire.views[-1]
            assert recovered['outcome'] == 'recovered'
            assert second.adapter.loaded
            assert second.context.summary[0] > prior[0]
            assert_recovery_dispatch(wire, recovered)
            # Startup restore preceded module recovery: it must seed with the
            # accepted checkpoint instead of reprocessing that original prefix.
            prior_window = prior[1]['metadata']['openai:compaction']['output']
            first_batch = wire.compactions[before[1]][0]['input']
            assert first_batch[:len(prior_window)] == prior_window
            assert len(wire.completions) == before[2] + 1
            committed = await second.controls.perform('context.recovery.status')
            assert committed['state'] == 'committed' and committed['attempts'] == 1
            after = (len(wire.counts), len(wire.compactions), len(wire.completions))
            assert await second.controls.perform('context.recovery.request', command) == committed
            assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == after
        else:
            code = {'stop': 'recovery_interrupted', 'configuration': 'recovery_contract_changed',
                    'prefix': 'recovery_source_changed'}.get(change, 'recovery_unreconciled')
            if not uncertain:
                with pytest.raises(RuntimeError, match='Execution failed: ContextRecoveryError:.*' + code):
                    await second.session.execute('Explicit new input must not bypass the recovery fence.')
            status = await second.controls.perform('context.recovery.status')
            assert status['state'] == ('unknown' if uncertain else 'failed')
            assert second.context.summary is None  # Nothing restored/installed before admission.
            assert second.adapter.path.read_bytes() == saved
            assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before
            assert await second.controls.perform('context.recovery.request', command) == status
            if uncertain:
                with pytest.raises(ContextRecoveryError, match='recovery_unreconciled'):
                    await second.controls.perform('context.recovery.request',
                                                  recovery_command(wire, 'do-not-replay'))
            consumed = journal_path.read_bytes()
            assert await second.adapter.run_pending(provider=second.selected, count_view=None) is None
            assert journal_path.read_bytes() == consumed
            assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before
        canonical = await second.context.get_messages()
        assert canonical[:len(source)] == source
        assert wire.effects == 1 and wire.unknown_invocations == 0
        assert wire.unknown == RECOVERY_UNKNOWN
        assert second.context.config['max_tokens'] == RECOVERY_BUDGET


@pytest.mark.parametrize('state', ['refused', 'failed', 'running', 'installing', 'unknown',
                                  'missing-install-digest', 'malformed'])
async def test_fresh_turn_can_dispatch_without_replaying_recovery(tmp_path, state):
    from amplifier_web.context_continuity import ContextRecoveryError
    directory, wire = tmp_path / 'session', RecoveryWire()
    async with ordinary_recovery_case(directory, wire) as first:
        await first.session.execute('SEED-EFFECT: save report-v1 once.')
        await first.controls.perform('context.recovery.request', recovery_command(wire))
        path = first.adapter.recovery_path
        journal = json.loads(path.read_bytes())
        attempt = journal['attempts'][journal['current']]
        attempt['state'] = 'installing' if state == 'missing-install-digest' else state
        if state == 'installing':
            attempt['candidateSha256'] = '0' * 64
        path.write_text('{' if state == 'malformed' else json.dumps(journal))
        source = await first.context.get_messages()
        first.gate.release(first.activation)
    receipt = path.read_bytes()
    before = len(wire.completions)
    async with ordinary_recovery_case(directory, wire, source) as second:
        if state in {'missing-install-digest', 'malformed'}:
            with pytest.raises(ContextRecoveryError, match='recovery_journal_unavailable'):
                second.adapter.recovery_status()
        assert 'Fresh read-only continuation.' in await second.session.execute(
            'CHECK-FRESH-READ-ONLY: What is the current report status?')
        assert (await second.context.get_messages())[:len(source)] == source
    assert len(wire.completions) == before + 1
    assert path.read_bytes() == receipt
    assert not wire.compactions
    assert wire.effects == 1 and wire.unknown_invocations == 0


async def test_degraded_dispatch_from_shared_fitter(tmp_path, monkeypatch):
    from amplifier_web.execution_events import ExecutionEvents, _CONTINUATION_WARNING
    from amplifier_web.runtime import normalize_event

    if not hasattr(boundary.BoundaryContextManager, '_fit_ordinary'):
        pytest.skip('Requires the shared ordinary fitter candidate')
    wire = RecoveryWire()
    async with ordinary_recovery_case(tmp_path / 'session', wire, continuation=True, budget=50000) as case:
        await case.session.execute('SEED-EFFECT: save report-v1 once.')
        await append_recovery_evidence(case.context, ['A', 'B', 'C'])
        # Simulate a tighter target model allowance, not just the Context
        # compaction trigger. Counting and dispatch still use real SDK HTTP.
        monkeypatch.setattr(case.raw, '_budget_input_limit', lambda params: 50000)
        sid = case.session.session_id
        events = []
        telemetry = ExecutionEvents(sid, events.append)

        async def admit(row):
            case.gate.check_current()

        telemetry.admission_guard = admit
        telemetry.lifecycle({'type': 'generation.started', 'generation_id': 'fresh-generation',
                             'initial_input_id': 'fresh-turn'})
        telemetry.instrument_provider(sid, case.selected)
        before = len(wire.completions)
        assert 'Fresh read-only continuation.' in await case.session.execute(
            'CHECK-FRESH-READ-ONLY: What is the current report status?')
        assert len(wire.completions) == before + 1
        assert _CONTINUATION_WARNING in json.dumps(wire.completions[-1]), (
            [size for _, size in wire.counts], len(wire.compactions),
            [view.get('continuation') for view in wire.views],
            RecoveryWire.count(wire.completions[-1]))
        marks = [normalize_event(event, sid, 'fresh-turn')[1] for event in events
                 if event['event'].get('incompleteHistoryDispatched')]
        assert len(marks) == 1
        assert marks[0]['incompleteHistoryDispatched'] is True
        assert marks[0]['admissionInputId'] == 'fresh-turn'
        assert wire.effects == 1 and wire.unknown_invocations == 0


async def test_degraded_stream_marks_only_after_pull_and_current_admission():
    """Preparing/suspending an iterator is not ordinary provider dispatch."""
    import asyncio
    from amplifier_core import ChatRequest, ChatResponse, Message
    from amplifier_web.execution_events import ExecutionEvents, _CONTINUATION_WARNING

    events, sent = [], []
    admitted, release = asyncio.Event(), asyncio.Event()

    class Provider:
        def get_info(self):
            return {'id': 'fixture', 'defaults': {'model': 'offline'}}

        async def complete(self, request):
            raise AssertionError('This case must use the streaming path')

        async def stream(self, request):
            sent.append(request)
            yield ChatResponse(content=[])

    async def guard(row):
        admitted.set()
        await release.wait()

    provider = Provider()
    telemetry = ExecutionEvents('root', events.append)
    telemetry.admission_guard = guard
    telemetry.lifecycle({'type': 'generation.started', 'generation_id': 'generation',
                         'initial_input_id': 'ordinary-input'})
    telemetry.instrument_provider('root', provider)
    request = ChatRequest(messages=[
        Message(role='developer', content=_CONTINUATION_WARNING, metadata={
            'source': 'context-managed-continuation', 'ephemeral': True}),
        Message(role='user', content='Current accepted input, unchanged.'),
    ])
    iterator = provider.stream(request)
    assert events == sent == []  # A prepared iterator alone does no work.
    pulling = asyncio.create_task(anext(iterator))
    try:
        await asyncio.wait_for(admitted.wait(), 5)
        assert events == sent == []  # Even a pull must wait for current admission.
        release.set()
        await asyncio.wait_for(pulling, 5)
        marked = [event['event'] for event in events
                  if event['event'].get('incompleteHistoryDispatched')]
        assert len(marked) == 1 and sent == [request]
        assert marked[0]['admissionInputId'] == 'ordinary-input'
        assert marked[0]['generationId'] == 'generation'
    finally:
        pulling.cancel()
        await asyncio.wait_for(asyncio.gather(pulling, return_exceptions=True), 5)
        await asyncio.wait_for(iterator.aclose(), 5)


async def test_degraded_stream_cancelled_before_admission_has_no_warning_or_transport():
    import asyncio
    from amplifier_core import ChatRequest, Message
    from amplifier_web.execution_events import ExecutionEvents, _CONTINUATION_WARNING

    events, sent = [], []
    waiting = asyncio.Event()

    class Provider:
        def get_info(self):
            return {'id': 'fixture', 'defaults': {'model': 'offline'}}

        async def complete(self, request):
            raise AssertionError('Unexpected completion')

        async def stream(self, request):
            sent.append(request)
            yield None

    async def guard(row):
        waiting.set()
        await asyncio.Event().wait()

    provider = Provider()
    telemetry = ExecutionEvents('root', events.append)
    telemetry.admission_guard = guard
    telemetry.lifecycle({'type': 'generation.started', 'generation_id': 'generation',
                         'initial_input_id': 'ordinary-input'})
    telemetry.instrument_provider('root', provider)
    iterator = provider.stream(ChatRequest(messages=[Message(
        role='developer', content=_CONTINUATION_WARNING, metadata={
            'source': 'context-managed-continuation', 'ephemeral': True})]))
    pulling = asyncio.create_task(anext(iterator))
    try:
        await asyncio.wait_for(waiting.wait(), 5)
        pulling.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pulling, 5)
        assert events == sent == []
    finally:
        pulling.cancel()
        await asyncio.wait_for(asyncio.gather(pulling, return_exceptions=True), 5)
        await asyncio.wait_for(iterator.aclose(), 5)


@pytest.mark.parametrize('explicit', [None, True, False])
def test_live_plan_enables_ordinary_fitting_without_overriding_explicit_choice(explicit):
    from amplifier_web.host.session import live_plan
    config = {} if explicit is None else {'foreground_continuation': explicit}
    source = {'session': {'orchestrator': 'loop-streaming',
                         'context': {'module': 'context-managed', 'config': config}}}
    before = copy.deepcopy(source)
    plan, _ = live_plan(source)
    assert plan['session']['context']['config']['foreground_continuation'] is (
        True if explicit is None else explicit)
    assert source == before


def recovery_decision_accounts():
    return [{'module': 'provider-fixture-decision', 'instance_id': name,
             'source': 'git+https://example.test/decision@exact', 'enabled': enabled,
             'config': {'api_key': secret, 'decisions': {
                 'enabled': True, 'backends': {'fixture': {'model': 'decision-model'}}}}}
            for name, enabled, secret in (
                ('decision-active', True, 'DECISION-ACTIVE-SECRET-72'),
                ('decision-disabled', False, 'DECISION-DISABLED-SECRET-81'))]


@pytest.fixture
def project_recovery_accounts(monkeypatch):
    from amplifier_web import decision_accounts as accounts
    definitions = {'provider-fixture-decision': 'hooks-fixture-decisions'}
    # Replace only package metadata discovery. UUID allocation, projection and
    # logical-plan validation are real; no decision hook or service is invoked.
    monkeypatch.setattr(accounts, 'catalogs', lambda: definitions)

    def project(case, rows):
        logical = accounts.logical_plan(case.session.coordinator.config)
        logical['providers'] = copy.deepcopy(rows)
        logical.setdefault('hooks', [])
        projection = accounts.DecisionAccounts(
            logical, None, lambda module, source: source)
        case.session.coordinator.config.clear()
        case.session.coordinator.config.update(projection.plan)
        return logical, projection

    return project


@pytest.mark.parametrize('change', [
    'unchanged', 'account', 'source', 'credential', 'settings', 'disabled-credential',
])
async def test_decision_account_remount_recovery_uses_exact_logical_choices(
        tmp_path, monkeypatch, project_recovery_accounts, change):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    directory, wire = tmp_path / 'session', RecoveryWire()
    rows = recovery_decision_accounts()
    secrets = [row['config']['api_key'] for row in rows] + ['ROTATED-DECISION-SECRET-96']
    async with ordinary_recovery_case(directory, wire) as first:
        logical, projection = project_recovery_accounts(first, rows)
        first_plan = copy.deepcopy(first.session.coordinator.config)
        # Existing v1 explicit recovery receipts contain this exact projection.
        # A model-transition credential binding must not alter their digest.
        def version1_contract():
            value = first.controls.recovery_contract()
            return {key: value[key] for key in (
                'plan', 'selection', 'maxOutputTokens', 'context', 'loop', 'mode')}

        contract = version1_contract()
        first.session.coordinator.register_capability(
            'web.context_recovery.contract', version1_contract)
        assert contract['plan'] == logical
        assert first.session.coordinator.config == first_plan  # Inspection is not a remount.
        bindings = {row['binding_id'] for row in projection.records}
        assert len(bindings) == 2 and len(projection.expected) == 1
        await first.session.execute('SEED-EFFECT: save report-v1 once; preserve ORBIT.')
        await append_recovery_evidence(first.context, ['A', 'B', 'C'])
        before_failure = len(wire.completions)
        with pytest.raises(RuntimeError, match=r'Execution failed: CompactionError:.*native_no_reduction'):
            await first.session.execute('Failing preparation with decision accounts before remount.')
        assert len(wire.completions) == before_failure
        source = copy.deepcopy(await first.context.get_messages())
        saved = first.adapter.path.read_bytes() if first.adapter.path.exists() else None
        command = recovery_command(wire)
        before_arm = (len(wire.counts), len(wire.compactions), len(wire.completions))
        pending = await first.controls.perform('context.recovery.request', command)
        assert pending['state'] == 'pending' and pending['attempts'] == 1
        assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before_arm
        journal_path = first.adapter.recovery_path
        journal = json.loads(journal_path.read_text())
        assert journal['attempts'][journal['current']]['contract'] == {
            'version': 1, 'sha256': digest(contract)}
        for secret in secrets:
            assert secret not in journal_path.read_text() + json.dumps(pending)
        first.gate.release(first.activation)

    if change == 'account':
        rows[0]['instance_id'] = 'different-account'
    elif change == 'source':
        rows[0]['source'] += '-changed'
    elif change in {'credential', 'disabled-credential'}:
        rows[1 if change == 'disabled-credential' else 0]['config']['api_key'] = secrets[-1]
    elif change == 'settings':
        rows[0]['config']['decisions']['backends']['fixture']['model'] = 'different-decision-model'
    retained_journal = journal_path.read_bytes()
    before = (len(wire.counts), len(wire.compactions), len(wire.completions))
    async with ordinary_recovery_case(directory, wire, source) as second:
        logical, projection = project_recovery_accounts(second, rows)
        assert bindings.isdisjoint(row['binding_id'] for row in projection.records)
        assert len(projection.records) == 2 and len(projection.expected) == 1
        assert second.session.coordinator.config != first_plan
        assert second.gate is not first.gate and not first.gate.current_valid
        next_contract = second.controls.recovery_contract()
        assert next_contract['plan'] == logical
        assert (next_contract == contract) is (change == 'unchanged')
        assert await second.controls.perform('context.recovery.status') == pending
        assert await second.controls.perform('context.recovery.request', command) == pending
        assert journal_path.read_bytes() == retained_journal
        assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before
        if change == 'unchanged':
            assert 'Usable continuation' in await second.session.execute('Fresh input after decision remount.')
            assert wire.views[-1]['outcome'] == 'recovered'
            assert_recovery_dispatch(wire, wire.views[-1])
            status = await second.controls.perform('context.recovery.status')
            assert status['state'] == 'committed' and status['attempts'] == 1
            assert status['completedParts'] > 1
            assert status['verification']['after'] < status['verification']['before']
            assert len(wire.completions) == before[2] + 1
        else:
            with pytest.raises(RuntimeError, match=r'Execution failed: ContextRecoveryError:.*recovery_contract_changed'):
                await second.session.execute('Changed decision choices must refuse before provider work.')
            status = await second.controls.perform('context.recovery.status')
            assert status['state'] == 'failed' and status['code'] == 'recovery_contract_changed'
            assert not second.adapter.loaded and second.context.summary is None
            assert (second.adapter.path.read_bytes() if second.adapter.path.exists() else None) == saved
            assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before
            consumed = journal_path.read_bytes()
            assert await second.adapter.run_pending(provider=second.selected, count_view=None) is None
            assert journal_path.read_bytes() == consumed
            assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == before
        after = (len(wire.counts), len(wire.compactions), len(wire.completions))
        assert await second.controls.perform('context.recovery.request', command) == status
        assert (len(wire.counts), len(wire.compactions), len(wire.completions)) == after
        assert (await second.context.get_messages())[:len(source)] == source
        assert wire.effects == 1 and wire.unknown_invocations == 0
        assert wire.unknown == RECOVERY_UNKNOWN
        assert second.context.config['max_tokens'] == RECOVERY_BUDGET
        for secret in secrets:
            assert secret not in journal_path.read_text() + json.dumps(status)
        assert 'binding_id' not in journal_path.read_text()


@pytest.mark.parametrize('phase', ['arming', 'preparation'])
async def test_decision_account_recovery_guard_detects_change_during_stop_check(
        tmp_path, monkeypatch, project_recovery_accounts, phase):
    from amplifier_web.context_continuity import ContextRecoveryError
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    wire = RecoveryWire()
    source = [{'role': 'user', 'content': 'Keep the original task.'}]
    async with ordinary_recovery_case(tmp_path / 'session', wire, source) as case:
        rows = recovery_decision_accounts()
        project_recovery_accounts(case, rows)
        if phase == 'preparation':
            pending = await case.controls.perform('context.recovery.request', recovery_command(wire))
            assert pending['state'] == 'pending'
        coordinator = case.session.coordinator
        bridge = coordinator.get_capability('web.app_access.bridge')

        async def change_during_stop_check(*args):
            response = await bridge(*args)
            rows[0]['config']['api_key'] = 'ACTIVE-RUN-CHANGED-SECRET'
            project_recovery_accounts(case, rows)
            return response

        coordinator.register_capability('web.app_access.bridge', change_during_stop_check)
        if phase == 'arming':
            with pytest.raises(ContextRecoveryError, match='recovery_contract_changed'):
                await case.controls.perform('context.recovery.request', recovery_command(wire))
            assert not case.adapter.recovery_path.exists()
        else:
            with pytest.raises(RuntimeError, match=r'Execution failed: ContextRecoveryError:.*recovery_contract_changed'):
                await case.session.execute('Fresh input cannot bypass an in-flight configuration change.')
            status = await case.controls.perform('context.recovery.status')
            assert status['state'] == 'failed' and status['code'] == 'recovery_contract_changed'
            assert not case.adapter.loaded and case.context.summary is None
            assert 'ACTIVE-RUN-CHANGED-SECRET' not in case.adapter.recovery_path.read_text() + json.dumps(status)
        assert not case.adapter.path.exists()
        assert wire.counts == wire.compactions == wire.completions == []
        assert wire.effects == wire.unknown_invocations == 0
        assert (await case.context.get_messages())[:len(source)] == source


async def test_decision_account_recovery_rejects_unvalidated_projection(
        tmp_path, monkeypatch, project_recovery_accounts):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    wire = RecoveryWire()
    source = [{'role': 'user', 'content': 'Keep the original task.'}]
    async with ordinary_recovery_case(tmp_path / 'session', wire, source) as case:
        project_recovery_accounts(case, recovery_decision_accounts())
        hook = case.session.coordinator.config['hooks'][0]
        hook['config']['api_key'] = 'FORGED-HOOK-SECRET'
        with pytest.raises(ValueError, match='does not match its logical account/source'):
            await case.controls.perform('context.recovery.request', recovery_command(wire))
        assert not case.adapter.recovery_path.exists()
        assert not case.adapter.path.exists()
        assert wire.counts == wire.compactions == wire.completions == []
        assert wire.effects == wire.unknown_invocations == 0
        assert await case.context.get_messages() == source


def bind_continuity_recovery_guards(coordinator, adapter, contract):
    """Host ownership/contract observations for the existing synthetic fixture."""
    from amplifier_foundation.session.activation import ActivationGate
    gate = ActivationGate()
    gate.activate()
    coordinator.cancellation = SimpleNamespace(is_cancelled=False)
    coordinator.register_capability('live.activation', gate)
    coordinator.register_capability('live.runtime', SimpleNamespace(stop_requested=False, closed=False))
    coordinator.register_capability('web.context_recovery.epoch', lambda: 0)
    coordinator.register_capability('web.context_recovery.contract', lambda: contract)
    coordinator.register_capability('web.context_transition.contract', lambda: contract)
    coordinator.register_capability('web.app_access.bridge', AsyncMock(return_value={
        'result': {'items': [{'target': {'sessionId': 'session'},
                              'signal': {'interruptionRevision': 0}}]}}))
    adapter.writer_guard = gate.check_current
    return gate


async def target_transition_fixture(directory, *, model='target-model', foreground=True, raw=None, research_parts=6):
    """Use actual Context checkpoint/target fitting; only host admission is synthetic."""
    from amplifier_core import ChatRequest, Message
    from amplifier_web.host.session import SelectedProvider
    source = native_history()
    source[1]['content'] = 'FILLER:' + 'research ' * 400
    source[2:2] = [row for n in range(research_parts) for row in (
        {'role': 'user', 'content': f'Accepted research part {n}.'},
        {'role': 'assistant', 'content': 'FILLER:' + 'research ' * 400})]
    first, _, _, prior, checkpoint, _, prepare = await native_fixture(directory, source)
    await prepare()
    await checkpoint()
    accepted = prior.path.read_bytes()
    coordinator, raw, _, adapter, _, _, _ = await native_fixture(directory, source, provider=raw)
    raw.input_limit = 3000
    target = SelectedProvider(raw, {'instance': 'fixture', 'model': model, 'effort': 'low'})
    coordinator.loop.root_provider = target
    contract = {'selection': target.selection, 'selectionRevision': 'target-choice-1'}
    gate = bind_continuity_recovery_guards(coordinator, adapter, contract)
    origin = {'generationId': 'new-generation', 'inputId': 'new-input',
              'selectionRevision': 'target-choice-1', 'selection': target.selection,
              'connection': 'fixture-connection', 'inputIds': ['new-input']}
    coordinator.register_capability('web.context_transition.foreground',
        lambda: copy.deepcopy(origin) if foreground else None)
    coordinator.register_capability('web.context_transition.selection', lambda: {
        key: copy.deepcopy(origin.get(key)) for key in ('selection', 'selectionRevision', 'connection')})
    async def count_view(rows):
        request = ChatRequest(model=model, messages=[Message(**row) for row in rows])
        return {'dispatch': request, 'budget_decision':
                target.request_budget(request, context_estimate=10000)}
    async def prepare_target():
        return await coordinator.context.get_measured_request_view(
            provider=target, retain_contents=(), count_view=count_view)
    return SimpleNamespace(coordinator=coordinator, raw=raw, selected=target, adapter=adapter,
        source=source, accepted=accepted, contract=contract, gate=gate, origin=origin,
        prepare=prepare_target)


@pytest.mark.parametrize('state', ['failed', 'refused', 'running', 'installing', 'unknown'])
async def test_historical_explicit_recovery_cannot_skip_model_transition_guards(tmp_path, state):
    from amplifier_web.context_continuity import ContextRecoveryError
    case = await target_transition_fixture(tmp_path)
    await case.adapter.request_recovery('historical-explicit', 0)
    record = case.adapter._journal['attempts']['historical-explicit']
    record['state'] = state
    if state == 'installing':
        record['candidateSha256'] = 'a' * 64
    case.adapter._write_recovery()
    retained = case.adapter.recovery_path.read_bytes()
    if state in {'failed', 'refused'}:
        result = await case.prepare()
        assert result['outcome'] == 'transitioned'
        assert case.adapter._journal['attempts']['historical-explicit'] == record
    elif state == 'installing':
        # This unusable journal cannot support transition work, but neither
        # model selection nor its failure turns canonical ordinary fitting off.
        assert await case.adapter.run_pending(provider=case.selected, count_view=None) is None
        assert case.adapter.recovery_path.read_bytes() == retained
        assert case.adapter.path.read_bytes() == case.accepted
        assert case.raw.compactions == []
    else:
        with pytest.raises(ContextRecoveryError, match='recovery_unreconciled'):
            await case.prepare()
        assert case.adapter.recovery_path.read_bytes() == retained
        assert case.adapter.path.read_bytes() == case.accepted
        assert case.raw.compactions == []
    assert await case.coordinator.context.get_messages() == case.source


@pytest.mark.parametrize('other_state', ['unknown', 'refused-progress'])
async def test_existing_transition_cannot_rebind_past_another_unsettled_recovery(tmp_path, other_state):
    from amplifier_web.context_continuity import ContextRecoveryError
    case = await target_transition_fixture(tmp_path)
    await case.adapter.preserve(case.source)
    transition = await case.adapter._prepare_transition()
    transition.update(state='refused', code='transition_intent_required')
    case.adapter._write_recovery()
    await case.adapter.request_recovery('later-explicit', 0)
    other = case.adapter._journal['attempts']['later-explicit']
    other['state'] = 'unknown' if other_state == 'unknown' else 'refused'
    if other_state == 'refused-progress':
        other.update(progress={'retained': 'paid-prefix'}, completedParts=1)
    case.adapter._write_recovery()
    retained = case.adapter.recovery_path.read_bytes()
    case.origin.update(inputId='fresh-input', inputIds=['fresh-input'], generationId='fresh-generation')
    with pytest.raises(ContextRecoveryError, match='recovery_unreconciled'):
        await case.prepare()
    assert case.adapter.recovery_path.read_bytes() == retained
    assert case.adapter._journal['current'] == 'later-explicit'
    assert case.adapter.path.read_bytes() == case.accepted
    assert case.raw.compactions == [] and case.raw.sent == []


@pytest.mark.parametrize('different_model', [False, True])
async def test_live_plan_continuation_default_preserves_legacy_checkpoint_identity(tmp_path, different_model):
    from amplifier_web.host.session import live_plan, SelectedProvider
    config = {'durable_checkpoints': True, 'archive_recovery': True,
              'max_tokens': 20000, 'summarize_trigger': .2, 'native_min_new_tokens': 0}
    source = native_history()
    first, _, _, prior, checkpoint, _, prepare = await native_fixture(
        tmp_path, source, context_config=config)
    assert 'foreground_continuation' not in first.context.config
    await prepare()
    await checkpoint()
    saved = prior.path.read_bytes()
    original = copy.deepcopy(first.context.config)
    plan, _ = live_plan({'session': {
        'context': {'module': 'context-managed', 'config': original},
        'orchestrator': {'module': 'loop-streaming'}}})
    next_config = plan['session']['context']['config']
    assert next_config['foreground_continuation'] is True
    second, raw, _, adapter, _, _, _ = await native_fixture(
        tmp_path, source, context_config=next_config)
    if different_model:
        second.loop.root_provider = SelectedProvider(raw, {'model': 'target-model'})
    await adapter.preserve(source)
    if different_model:
        assert adapter.status['status'] == 'transition_required'
        assert adapter._transition and not adapter._foreign_rejection
    else:
        assert adapter.status['status'] == 'restored'
        assert second.context.summary == first.context.summary
    assert adapter.path.read_bytes() == saved
    assert raw.compactions == [] and raw.counted == [] and raw.sent == []
    assert await second.context.get_messages() == source


async def test_unreadable_generic_journal_keeps_foreground_canonical_only(tmp_path):
    case = await target_transition_fixture(tmp_path)
    case.adapter.recovery_path.write_text('{')
    case.adapter._journal = None
    await case.adapter.refresh_identity()
    assert case.adapter._foreground()['inputId'] == 'new-input'
    # Not transition success: the unreadable ledger cannot be replaced or spend.
    assert await case.adapter.run_pending(provider=case.selected, count_view=None) is None
    await case.adapter.preserve(case.source)
    case.adapter.save()
    assert case.adapter.path.read_bytes() == case.accepted
    assert case.adapter.recovery_path.read_text() == '{'
    assert case.coordinator.context.summary is None
    assert case.raw.compactions == [] and case.raw.sent == [] and case.raw.counted == []
    assert await case.coordinator.context.get_messages() == case.source


async def test_same_model_pinned_foreground_keeps_unconfirmed_generic_recovery_inert(tmp_path, monkeypatch):
    from test_provider_selection_lifecycle import mounted_controls, SELECTION
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    case = await target_transition_fixture(tmp_path, model='selected-model')
    await case.adapter.request_recovery('unconfirmed-explicit', 0)
    record = case.adapter._journal['attempts']['unconfirmed-explicit']
    record.update(state='unknown', candidateSha256='a' * 64)
    case.adapter._write_recovery()
    before = case.adapter.recovery_path.read_bytes()
    controls, _, _ = mounted_controls(SELECTION)
    caps = {'web.provider_catalog': {'connectionKeys': {'terra': 'verified'}},
            'web.context_transition.input': lambda: {
                'inputId': 'fresh', 'selectionRevision': controls.selection_revision}}
    controls.coordinator.get_capability = caps.get
    try:
        await controls.perform('provider.select', {**SELECTION, 'expectedConnectionKey': 'verified'})
        controls.runtime.accepted = {'fresh': SimpleNamespace(kind='user')}
        controls.runtime.generation = {
            'id': 'generation', 'initial_input_id': 'fresh', 'input_ids': ['fresh']}
        assert controls.transition_foreground() is not None
        matching = {**controls.transition_foreground(), 'selection': case.selected.selection}
        case.coordinator.register_capability(
            'web.context_transition.foreground', lambda: matching)
        await case.adapter.refresh_identity()
        assert case.adapter._foreground()['inputId'] == 'fresh'
        assert await case.adapter.run_pending(provider=case.selected, count_view=None) is None
        assert case.adapter.recovery_path.read_bytes() == before
        assert case.adapter.path.read_bytes() == case.accepted
        assert case.raw.compactions == case.raw.sent == case.raw.counted == []
    finally:
        await controls.close()


async def test_foreground_target_transition_keeps_originals_tool_pairs_and_single_checkpoint(tmp_path):
    case = await target_transition_fixture(tmp_path)
    result = await case.prepare()
    assert result['outcome'] == 'transitioned'
    status = case.adapter.recovery_status()
    assert status['state'] == 'committed' and status['outcome'] == 'checkpoint-installed'
    assert 0 < status['logicalCalls'] <= status['maxCalls'] == 16
    journal = json.loads(case.adapter.recovery_path.read_text())
    row = journal['attempts'][journal['current']]
    assert row['sourceCheckpointDigest'] == digest(json.loads(case.accepted))
    assert 'sourceCheckpoint' not in row and row['progress'] is None
    assert all(call['state'] == 'completed' for call in row['calls'])
    assert all(call['report']['targetIdentity']['model'] == 'target-model' for call in row['calls'])
    assert json.loads(case.adapter.path.read_text())['identity']['model'] == 'target-model'
    assert await case.coordinator.context.get_messages() == case.source
    assert case.raw.sent == []
    assert not any('fixture-native' in str(rows) for rows in case.raw.compactions[:1])
    before = len(case.raw.compactions)
    await case.prepare()
    assert len(case.raw.compactions) == before


async def test_host_native_to_supported_portable_transition_shares_durable_call_budget(tmp_path):
    class PortableTarget(SyntheticNativeProvider):
        def supports_native_compaction(self):
            return False
        def request_budget(self, request, **kwargs):
            budget = super().request_budget(request, **kwargs)
            # Synthetic summary endpoint has its own admitted input ceiling;
            # the ordinary target request remains constrained to 3000.
            if (request.metadata or {}).get('purpose') == 'context-compaction':
                budget['input_limit_tokens'] = 20000
                budget['context_token_budget'] = 20000
            return budget
        async def complete(self, request, **kwargs):
            self.sent.append(request.model_copy(deep=True))
            return SimpleNamespace(content=[SimpleNamespace(type='text', text=(
                'Build report; keep originals. Verified result report-v1 completed once. '
                'Correction report-v2; uncertain-op unknown, inspect never replay.'))])
    raw = PortableTarget()
    case = await target_transition_fixture(tmp_path, raw=raw)
    result = await case.prepare()
    assert result['outcome'] == 'transitioned'
    status = case.adapter.recovery_status()
    assert status['state'] == 'committed'
    assert 0 < status['logicalCalls'] == len(raw.sent) <= status['maxCalls']
    assert raw.compactions == []
    assert all(call['report']['method'] == 'semantic' for call in case.adapter._recovery_record()['calls'])
    checkpoint = json.loads(case.adapter.path.read_text())
    assert checkpoint['summary']['text'] and checkpoint['identity']['model'] == 'target-model'
    assert await case.coordinator.context.get_messages() == case.source


async def test_concurrent_transition_preparations_cannot_replace_journal_transaction(tmp_path):
    import asyncio
    case = await target_transition_fixture(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = case.adapter._interruption_revision
    async def delayed():
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await original()
    case.adapter._interruption_revision = delayed
    first = asyncio.create_task(case.prepare())
    await asyncio.wait_for(entered.wait(), 3)
    with pytest.raises(RuntimeError, match='recovery_unreconciled'):
        await case.prepare()
    release.set()
    assert (await first)['outcome'] == 'transitioned'
    journal = json.loads(case.adapter.recovery_path.read_text())
    row = journal['attempts'][journal['current']]
    assert row['state'] == 'committed' and len(journal['attempts']) == 1
    assert len(row['calls']) == len(case.raw.compactions) > 0
    assert all(call['state'] == 'completed' for call in row['calls'])
    assert row['installedCandidate'] == json.loads(case.adapter.path.read_text())['sha256']


async def test_prepared_originals_b_a_b_revalidates_active_source_instead_of_stale_marker(tmp_path):
    from amplifier_core import ChatRequest, Message
    from amplifier_web.host.session import SelectedProvider
    case = await target_transition_fixture(tmp_path)
    case.raw.input_limit = 50000
    assert (await case.prepare())['outcome'] == 'transition_fitted'
    source = SelectedProvider(case.raw, {'instance': 'fixture', 'model': 'selected-model'})
    case.coordinator.loop.root_provider = source
    async def count(rows):
        request = ChatRequest(model='selected-model', messages=[Message(**row) for row in rows])
        return {'dispatch': request, 'budget_decision': source.request_budget(request)}
    await case.coordinator.context.get_measured_request_view(
        provider=source, retain_contents=(), count_view=count)
    assert case.coordinator.context.summary_identity['model'] == 'selected-model'
    case.coordinator.loop.root_provider = case.selected
    case.origin.update(selectionRevision='new-B-choice', inputId='new-B-input', generationId='new-B-generation')
    case.contract['selectionRevision'] = 'new-B-choice'
    assert (await case.prepare())['outcome'] == 'transition_fitted'
    assert case.coordinator.context.summary is None
    assert case.adapter.public()['status'] == 'prepared-originals'
    assert case.raw.compactions == []
    # A cold reader of the completed zero-paid receipt uses ordinary fitting;
    # it does not rearm the already settled historical transition.
    case.adapter._prepared_identity = None
    case.adapter.loaded = False
    case.adapter._journal = None
    case.origin.update(inputId='cold-B-input', generationId='cold-B-generation')
    journal = case.adapter.recovery_path.read_bytes()
    assert (await case.prepare())['outcome'] != 'transition_fitted'
    assert case.adapter.recovery_path.read_bytes() == journal


async def test_warm_foreign_checkpoint_without_classifier_is_unsupported_not_overwritten(tmp_path):
    from amplifier_web.context_continuity import ContextRecoveryError
    from amplifier_web.host.session import SelectedProvider
    original = native_history()
    coordinator, raw, _, adapter, checkpoint, _, prepare = await native_fixture(tmp_path, original)
    await prepare()
    await checkpoint()
    saved = adapter.path.read_bytes()
    source_provider = coordinator.loop.root_provider
    coordinator.register_capability('context.checkpoint.transition', None)
    coordinator.loop.root_provider = SelectedProvider(raw, {'instance': 'fixture', 'model': 'other-model'})
    await coordinator.context.add_message({'role': 'user', 'content': 'Fresh target request'})
    await adapter.preserve(await coordinator.context.get_messages())
    assert adapter.public()['status'] == 'rejected'
    assert adapter.public()['code'] == 'transition_unsupported'
    coordinator.register_capability('web.context_transition.foreground', lambda: {})
    before = (len(raw.counted), len(raw.compactions), len(raw.sent))
    for _ in range(2):
        with pytest.raises(ContextRecoveryError) as failure:
            await prepare()
        assert failure.value.code == 'transition_unsupported'
        assert 'previous model and connection' in str(failure.value)
    with pytest.raises(ContextRecoveryError) as recovery:
        await adapter.request_recovery('explicit-recovery', 0)
    assert recovery.value.code == 'transition_unsupported'
    assert not adapter.recovery_path.exists()
    assert (len(raw.counted), len(raw.compactions), len(raw.sent)) == before
    adapter.save()
    assert adapter.path.read_bytes() == saved
    coordinator.loop.root_provider = source_provider
    await adapter.preserve(await coordinator.context.get_messages())
    assert adapter.public().get('code') != 'transition_unsupported'
    assert adapter.path.read_bytes() == saved


@pytest.mark.parametrize('identity', [None, {'provider': 'fixture', 'model': '  '}, 'unknown'])
async def test_legacy_context_requires_identifiable_source_before_suggesting_model_change(tmp_path, identity):
    from amplifier_web.context_continuity import ContextRecoveryError
    coordinator, raw, _, adapter, _, _, prepare = await native_fixture(tmp_path, native_history())
    coordinator.register_capability('web.context_transition.foreground', lambda: {})
    coordinator.register_capability('context.checkpoint.transition', None)
    adapter.path.write_text(json.dumps({'identity': identity}))
    saved = adapter.path.read_bytes()
    with pytest.raises(ContextRecoveryError) as failure:
        await prepare()
    assert failure.value.code == 'recovery_checkpoint_rejected'
    assert adapter.public()['code'] == 'transition_source_invalid'
    assert adapter.path.read_bytes() == saved
    assert raw.counted == raw.sent == raw.compactions == []


@pytest.mark.parametrize('change', ['source', 'config', 'selection', 'mounted-options', 'stop'])
async def test_transition_after_last_fit_guard_keeps_exact_source_checkpoint(tmp_path, change):
    case = await target_transition_fixture(tmp_path)
    recover = case.coordinator.get_capability('context.recovery.recover')
    async def mutate_before_install(**kwargs):
        install = kwargs['install']
        async def changed(candidate, verification):
            if change == 'source':
                case.coordinator.context.messages[0]['content'] += ' mutated'
            elif change == 'config':
                case.contract['changed'] = True
            elif change == 'selection':
                case.origin['selectionRevision'] = 'newer-choice'
            elif change == 'mounted-options':
                case.selected.selection['effort'] = 'high'
            else:
                case.gate.release(case.gate.current())
            return await install(candidate, verification)
        return await recover(**{**kwargs, 'install': changed})
    case.coordinator.register_capability('context.recovery.recover', mutate_before_install)
    with pytest.raises((RuntimeError, ValueError)):
        await case.prepare()
    assert case.adapter.path.read_bytes() == case.accepted
    assert case.adapter.recovery_status()['state'] in {'refused', 'unknown'}


async def test_transition_unknown_paid_reservation_cannot_reset_on_fresh_input_or_remount(tmp_path):
    case = await target_transition_fixture(tmp_path)
    async def lost(request):
        raise RuntimeError('synthetic lost paid reply')
    case.raw.compact_context = lost
    with pytest.raises(RuntimeError, match='lost paid'):
        await case.prepare()
    row = case.adapter._recovery_record()
    assert row['state'] == 'unknown' and row['calls'][-1]['state'] == 'reserved'
    saved = case.adapter.recovery_path.read_bytes()
    case.origin.update(inputId='another-input', generationId='another-generation')
    with pytest.raises(RuntimeError, match='recovery_unreconciled'):
        await case.prepare()
    assert case.adapter.recovery_path.read_bytes() == saved
    case.adapter._journal = None  # Fresh journal reader, not a cleared receipt.
    with pytest.raises(RuntimeError, match='recovery_unreconciled'):
        await case.prepare()
    assert case.adapter.path.read_bytes() == case.accepted
    assert case.adapter.recovery_status()['logicalCalls'] == 1


@pytest.mark.parametrize('appended', [False, True], ids=['same-source', 'new-foreground-and-refs'])
async def test_transition_lost_install_ack_reconciles_exact_digest_without_second_write(tmp_path, monkeypatch, appended):
    from amplifier_web import context_continuity
    case = await target_transition_fixture(tmp_path)
    recover = case.coordinator.get_capability('context.recovery.recover')
    writes = []
    write = context_continuity.write_private
    def observed(path, text):
        if path == case.adapter.path:
            writes.append(text)
        return write(path, text)
    monkeypatch.setattr(context_continuity, 'write_private', observed)
    async def lost_ack(**kwargs):
        install = kwargs['install']
        async def lose(candidate, verification):
            await install(candidate, verification)
            raise RuntimeError('synthetic install ACK lost')
        return await recover(**{**kwargs, 'install': lose})
    case.coordinator.register_capability('context.recovery.recover', lost_ack)
    with pytest.raises(RuntimeError, match='ACK lost'):
        await case.prepare()
    candidate = case.adapter.path.read_bytes()
    assert len(writes) == 1
    assert case.adapter.recovery_status()['state'] == 'unknown'
    calls = len(case.raw.compactions)
    with pytest.raises(RuntimeError, match='recovery_unreconciled'):
        await case.prepare()  # Same input is never another reconciliation intent.
    case.coordinator.register_capability('context.recovery.recover', recover)
    case.origin.update(inputId='fresh-reconciliation', generationId='fresh-generation')
    if appended:
        await case.coordinator.context.add_message({'role': 'assistant', 'content': 'New settled tail.'})
        await case.coordinator.context.add_message({'role': 'user', 'content': 'New authorized foreground input.'})
        await case.coordinator.context.add_message({'role': 'assistant', 'content': 'Tail acknowledged.'})
        preserve = case.coordinator.get_capability('context.preserve_evidence')
        async def fresh_refs(messages):
            return [*(await preserve(messages)), {'kind': 'fixture-fresh-reference', 'revision': 'new'}]
        case.coordinator.register_capability('context.preserve_evidence', fresh_refs)
        # Boundary captured preserve at mount, as the production registration
        # contract does; replace that binding deliberately for this new request.
        case.coordinator.context.preserve_evidence = fresh_refs
    result = await case.prepare()
    assert result['outcome'] == 'transitioned'
    assert case.adapter.recovery_status()['state'] == 'committed'
    assert case.adapter.path.read_bytes() == candidate
    assert len(writes) == 1 and len(case.raw.compactions) == calls


@pytest.mark.parametrize('checkpointless', [False, True], ids=['checkpoint', 'canonical-originals'])
async def test_transition_final_commit_failure_reconciles_already_active_exact_candidate(tmp_path, monkeypatch, checkpointless):
    from amplifier_web import context_continuity
    case = await target_transition_fixture(tmp_path)
    if checkpointless:
        directory = tmp_path / 'checkpointless'
        directory.mkdir()
        case.origin['originals'] = True
        case.coordinator.register_capability('web.context_transition.originals', True)
        case.adapter = context_continuity.ContextContinuity(case.coordinator, 'session', directory,
            AsyncMock(), writer_guard=case.gate.check_current)
    write = context_continuity.write_private
    failed, writes = [], []
    def fail_final(path, text):
        if path == case.adapter.path:
            writes.append(text)
        if path == case.adapter.recovery_path:
            journal = json.loads(text)
            if journal['attempts'][journal['current']]['state'] == 'committed' and not failed:
                failed.append(True)
                raise OSError('synthetic final commit failure')
        return write(path, text)
    monkeypatch.setattr(context_continuity, 'write_private', fail_final)
    with pytest.raises(OSError, match='final commit'):
        await case.prepare()
    assert case.coordinator.context.summary_identity['model'] == 'target-model'
    candidate = case.adapter.path.read_bytes()
    assert case.adapter.recovery_status()['state'] == 'unknown'
    paid = len(case.raw.compactions)
    case.origin.update(inputId='fresh-final-commit-reconciliation', generationId='new-generation')
    assert (await case.prepare())['outcome'] == 'transitioned'
    assert case.adapter.recovery_status()['state'] == 'committed'
    assert case.adapter.path.read_bytes() == candidate
    assert len(writes) == 1 and len(case.raw.compactions) == paid


async def test_transition_completed_progress_does_not_reset_total_call_cap(tmp_path):
    case = await target_transition_fixture(tmp_path)
    case.coordinator.context.config['summary_max_calls'] = 1
    with pytest.raises(RuntimeError, match='summary_work_paused'):
        await case.prepare()
    row = case.adapter._recovery_record()
    assert row['state'] == 'refused' and row['completedParts'] == 1
    assert row['calls'][0]['state'] == 'completed' and row['progress']
    paid = len(case.raw.compactions)
    case.origin.update(inputId='new-resume-input', generationId='new-generation')
    with pytest.raises(RuntimeError, match='summary_work_paused'):
        await case.prepare()
    assert case.adapter.recovery_status()['logicalCalls'] == paid == 1
    assert case.adapter.path.read_bytes() == case.accepted
    case.adapter._journal = None
    case.origin.update(inputId='another-resume-input')
    with pytest.raises(RuntimeError, match='summary_work_paused'):
        await case.prepare()
    assert len(case.raw.compactions) == 1


@pytest.mark.parametrize('change', ['authorized', 'cold', 'changed-stop', 'changed-model', 'changed-account',
                                    'changed-tools', 'reserved', 'source-edited', 'old-context', 'wrong-offer'])
async def test_exhausted_transition_additional_allowance_is_bound_to_one_new_send(tmp_path, change):
    from amplifier_web.context_continuity import ContextRecoveryError
    case = await target_transition_fixture(tmp_path)
    case.coordinator.context.config['summary_max_calls'] = 1
    with pytest.raises(RuntimeError, match='summary_work_paused'):
        await case.prepare()
    row = case.adapter._recovery_record()
    original = copy.deepcopy(row['calls'])
    assert len(original) == row['completedParts'] == 1
    assert 'allowanceOffer' not in await case.adapter.recovery_control_status()
    # This seam implements the opt-in component protocol and calls the actual
    # selected component for its remaining one allowed paid step.
    case.coordinator.register_capability('context.recovery.additional_call_allowance', lambda: 2)
    case.coordinator.register_capability('web.provider_catalog', {'connectionKeys': {'fixture': 'fixture-connection'}})
    recover = case.coordinator.get_capability('context.recovery.recover')
    authorized = []
    async def observe(**kwargs):
        if grant := kwargs.pop('additional_call_allowance', None):
            assert grant == {'version': 1, 'additionalCalls': 1, 'originalMaxCalls': 1,
                             'confirmedCalls': 1, 'authorizationId': 'approved-send'}
            assert kwargs['max_calls'] == 1
            authorized.append(grant)
        return await recover(**kwargs)
    case.coordinator.register_capability('context.recovery.recover', observe)
    if change == 'cold':
        case.adapter._journal = None  # Read the original unextended 0.113.1 journal.
    offer = (await case.adapter.recovery_control_status())['allowanceOffer']
    assert offer['maxAdditionalCalls'] == 32 and offer['expectedInterruptionRevision'] == 0
    claim = {'offerId': offer['offerId'], 'additionalCalls': 1}
    case.origin.update(inputId='approved-send', generationId='new-gen', inputIds=['approved-send'],
                       recoveryAllowance=claim, allowanceStopRevision=0)
    if change == 'changed-stop':
        case.origin['allowanceStopRevision'] = 1
    elif change == 'changed-model':
        case.origin['selection'] = {**case.origin['selection'], 'effort': 'high'}
    elif change == 'changed-account':
        case.coordinator.register_capability('web.provider_catalog', {'connectionKeys': {'fixture': 'different'}})
    elif change == 'changed-tools':
        case.contract['tools'] = ['different']
    elif change == 'reserved':
        row['calls'][0]['state'] = 'reserved'
        status = await case.adapter.recovery_control_status()
        assert status['unconfirmedCalls'] == 1 and 'allowanceOffer' not in status
    elif change == 'old-context':
        case.coordinator.register_capability('context.recovery.additional_call_allowance', lambda: 1)
    elif change == 'wrong-offer':
        claim['offerId'] = '0' * 64
    elif change == 'source-edited':
        case.adapter.path.write_text('{}')
    if change not in {'authorized', 'cold'}:
        with pytest.raises(ContextRecoveryError, match='transition_allowance_changed'):
            await case.prepare()
        assert not authorized and not case.adapter._recovery_record().get('additionalAllowance')
        assert len(case.raw.compactions) == 1
        return
    try:
        await case.prepare()
    except RuntimeError as exc:
        assert 'summary_work_paused' in str(exc), exc
    row = case.adapter._recovery_record()
    assert len(authorized) == 1 and len(case.raw.compactions) == len(row['calls']) == 2
    assert row['calls'][:1] == original and row['maxCalls'] == 1
    assert row['additionalAllowance']['authorizationId'] == 'approved-send'
    assert not case.adapter.accepts_allowance(claim, 0)
    case.adapter._journal = None
    status = await case.adapter.recovery_control_status()
    assert 'allowanceOffer' not in status
    assert status['additionalAllowance'] == {'authorizedCalls': 1, 'usedCalls': 1}
    if row['state'] != 'committed':
        case.origin.pop('recoveryAllowance')
        case.origin.update(inputId='later', inputIds=['later'], generationId='later-gen')
        with pytest.raises(RuntimeError, match='summary_work_paused'):
            await case.prepare()
        assert len(case.raw.compactions) == 2


@pytest.mark.parametrize('additional,input_limit,parts', [(32, 3000, 6), (1, 2700, 4)])
async def test_qualified_component_finishes_one_authorized_partial_transition(tmp_path, additional, input_limit, parts):
    case = await target_transition_fixture(tmp_path, research_parts=parts)
    case.raw.input_limit = input_limit
    version = case.coordinator.get_capability('context.recovery.additional_call_allowance')
    if not callable(version) or version() != 2:
        pytest.skip('Installed Managed Context does not support an additional transition allowance.')
    case.coordinator.context.config['summary_max_calls'] = 1
    case.coordinator.register_capability('web.provider_catalog', {'connectionKeys': {'fixture': 'fixture-connection'}})
    with pytest.raises(RuntimeError, match='summary_work_paused'):
        await case.prepare()
    case.adapter._journal = None
    row = case.adapter._recovery_record()
    original = copy.deepcopy(row['calls'])
    offer = (await case.adapter.recovery_control_status())['allowanceOffer']
    case.origin.update(inputId='ordinary-approved', generationId='approved-generation', inputIds=['ordinary-approved'],
        recoveryAllowance={'offerId': offer['offerId'], 'additionalCalls': additional},
        allowanceStopRevision=offer['expectedInterruptionRevision'])
    if additional == 1:
        await case.coordinator.context.add_message({'role': 'user', 'content': 'Continue the inventory.'})
    prepared = await case.prepare()
    assert prepared['outcome'] == 'transitioned'
    response = await case.selected.complete(prepared['final_attempt']['dispatch'])
    assert response.content[0].text == 'Synthetic final answer.' and case.raw.sent[-1].model == 'target-model'
    final = await case.adapter.recovery_control_status()
    assert final['state'] == 'committed' and final['maxCalls'] == 1
    assert final['additionalAllowance']['authorizedCalls'] == additional
    assert final['additionalAllowance']['usedCalls'] == len(case.raw.compactions) - 1
    assert 0 <= final['additionalAllowance']['usedCalls'] <= additional and row['calls'][:1] == original
    assert case.coordinator.context.config['summary_max_calls'] == 1
    assert not any(call['state'] != 'completed' for call in row['calls'])
    if additional == 1:
        assert final['additionalAllowance']['usedCalls'] == 1
    print('Additional calls used for qualified transition:', final['additionalAllowance']['usedCalls'])


@pytest.mark.parametrize('portable', ['reservation', 'checkpoint'])
async def test_portable_progress_cannot_offer_a_native_extension(tmp_path, portable):
    case = await target_transition_fixture(tmp_path)
    case.coordinator.context.config['summary_max_calls'] = 1
    case.coordinator.register_capability('web.provider_catalog', {'connectionKeys': {'fixture': 'fixture-connection'}})
    case.coordinator.register_capability('context.recovery.additional_call_allowance', lambda: 2)
    with pytest.raises(RuntimeError, match='summary_work_paused'):
        await case.prepare()
    assert 'allowanceOffer' in await case.adapter.recovery_control_status()
    row = case.adapter._recovery_record()
    if portable == 'reservation':
        row['calls'][0]['report']['method'] = 'semantic'
    else:
        progress = row['progress']
        progress['summary'] = {'text': 'Portable summary', 'throughMessage': progress['sourceRevision']['messages']}
        progress['sha256'] = digest({k: v for k, v in progress.items() if k != 'sha256'})
    assert 'allowanceOffer' not in await case.adapter.recovery_control_status()
    assert len(case.raw.compactions) == 1


async def test_transition_cancelled_paid_call_keeps_source_and_unknown_reservation(tmp_path):
    import asyncio
    case = await target_transition_fixture(tmp_path)
    async def cancel(request):
        raise asyncio.CancelledError()
    case.raw.compact_context = cancel
    with pytest.raises(asyncio.CancelledError):
        await case.prepare()
    assert case.adapter.path.read_bytes() == case.accepted
    assert case.adapter.recovery_status()['state'] == 'unknown'
    assert case.adapter._recovery_record()['calls'][-1]['state'] == 'reserved'


async def test_checkpointless_fork_transition_binds_actual_originals_not_fake_checkpoint(tmp_path):
    from amplifier_web.context_continuity import ContextContinuity
    case = await target_transition_fixture(tmp_path / 'source')
    directory = tmp_path / 'checkpointless'
    directory.mkdir()
    coordinator = case.coordinator
    coordinator.register_capability('web.context_transition.originals', True)
    case.origin['originals'] = True
    async def checkpoint():
        return None
    adapter = ContextContinuity(coordinator, 'session', directory, checkpoint,
                                writer_guard=case.gate.check_current)
    result = await case.prepare()
    assert result['outcome'] == 'transitioned'
    assert adapter.recovery_status()['state'] == 'committed'
    row = adapter._recovery_record()
    assert row['sourceCheckpointDigest'] == digest(None)
    assert row['sourceCheckpointSha256'] is None and 'sourceCheckpoint' not in row
    assert row['sourceRevision'] == {'messages': len(case.source), 'sha256': digest(case.source)}
    assert all(call['report']['sourceCheckpointSha256'] is None for call in row['calls'])


@pytest.mark.parametrize('phase', ['reserve', 'progress'])
async def test_transition_unconfirmed_journal_write_never_replays_paid_step(tmp_path, monkeypatch, phase):
    from amplifier_web import context_continuity
    case = await target_transition_fixture(tmp_path)
    write = context_continuity.write_private
    failed = []
    def fail(path, text):
        if path == case.adapter.recovery_path:
            journal = json.loads(text)
            row = journal['attempts'][journal['current']]
            calls = row.get('calls', [])
            if calls and not failed and calls[-1]['state'] == ('reserved' if phase == 'reserve' else 'completed'):
                failed.append(True)
                raise OSError('synthetic persistence lost')
        return write(path, text)
    monkeypatch.setattr(context_continuity, 'write_private', fail)
    with pytest.raises(OSError, match='persistence lost'):
        await case.prepare()
    assert failed and case.adapter.recovery_status()['state'] == 'unknown'
    assert case.adapter._recovery_record()['calls'][-1]['state'] == 'reserved'
    paid = len(case.raw.compactions)
    assert paid == (0 if phase == 'reserve' else 1)
    case.origin.update(inputId='fresh-input-after-uncertain-write')
    with pytest.raises(RuntimeError, match='recovery_unreconciled'):
        await case.prepare()
    assert len(case.raw.compactions) == paid
    assert case.adapter.path.read_bytes() == case.accepted


async def test_transition_foreign_refusal_does_not_brick_fully_valid_source_identity(tmp_path):
    from amplifier_core import ChatRequest, Message
    from amplifier_web.host.session import SelectedProvider
    case = await target_transition_fixture(tmp_path)
    case.raw.nonreducing = True
    with pytest.raises(RuntimeError):
        await case.prepare()
    assert case.adapter.recovery_status()['state'] == 'refused'
    source_provider = SelectedProvider(case.raw, {'instance': 'fixture', 'model': 'selected-model'})
    case.coordinator.loop.root_provider = source_provider
    async def count(rows):
        request = ChatRequest(model='selected-model', messages=[Message(**row) for row in rows])
        return {'dispatch': request, 'budget_decision': source_provider.request_budget(request)}
    paid = len(case.raw.compactions)
    result = await case.coordinator.context.get_measured_request_view(
        provider=source_provider, retain_contents=(), count_view=count)
    assert result['final_attempt']['dispatch'].model == 'selected-model'
    assert len(case.raw.compactions) == paid
    assert case.adapter.path.read_bytes() == case.accepted


@pytest.mark.parametrize('state', ['unknown', 'running'])
async def test_h1_unknown_target_call_does_not_brick_strict_source_continuation(tmp_path, state):
    from amplifier_core import ChatRequest, Message
    from amplifier_web.host.session import SelectedProvider
    case = await target_transition_fixture(tmp_path)
    compact = case.raw.compact_context
    async def lost(request):
        raise RuntimeError('lost target reply')
    case.raw.compact_context = lost
    with pytest.raises(RuntimeError, match='lost target'):
        await case.prepare()
    row = case.adapter._recovery_record()
    row['state'] = state  # A crashed running reservation is equally uncertain.
    case.adapter._write_recovery()
    journal = case.adapter.recovery_path.read_bytes()
    case.raw.compact_context = compact
    source = SelectedProvider(case.raw, {'instance': 'fixture', 'model': 'selected-model'})
    case.coordinator.loop.root_provider = source
    async def count(rows):
        request = ChatRequest(model='selected-model', messages=[Message(**r) for r in rows])
        return {'dispatch': request, 'budget_decision': source.request_budget(request)}
    paid = len(case.raw.compactions)
    result = await case.coordinator.context.get_measured_request_view(
        provider=source, retain_contents=(), count_view=count)
    assert result['final_attempt']['dispatch'].model == 'selected-model'
    assert len(case.raw.compactions) == paid
    assert case.adapter.recovery_path.read_bytes() == journal
    case.coordinator.loop.root_provider = case.selected
    case.origin.update(inputId='new-target-request', generationId='later-generation')
    with pytest.raises(RuntimeError, match='recovery_unreconciled'):
        await case.prepare()
    assert case.adapter.recovery_path.read_bytes() == journal


async def test_h1_unknown_target_preserves_and_resumes_valid_paid_source_partial(tmp_path):
    from amplifier_core.llm_errors import LLMError
    from amplifier_module_context_managed.errors import CompactionError
    from amplifier_web.host.session import SelectedProvider
    original = [{'role': 'user', 'content': 'Preserve all evidence.'},
        {'role': 'assistant', 'content': 'Evidence ' * 1200},
        {'role': 'user', 'content': 'Correction'}, {'role': 'assistant', 'content': 'Working'},
        {'role': 'user', 'content': 'Continue'}]
    coordinator, provider, adapter, _, _ = await mount(tmp_path, original)
    coordinator.context.config.update(summary_max_source_chars=1200, summary_max_calls=32)
    note = SimpleNamespace(content=[SimpleNamespace(type='text', text='Acknowledged paid source part')])
    provider.complete = AsyncMock(side_effect=[note, LLMError('offline', retryable=True)])
    with pytest.raises(CompactionError, match='summary_failed'):
        await coordinator.context.get_messages_for_request(provider=provider)
    accepted = json.loads(adapter.path.read_text())
    assert accepted['progress']['completed'] == 1
    contract = {'selection': 'target'}
    bind_continuity_recovery_guards(coordinator, adapter, contract)
    from amplifier_core import ProviderInfo
    provider.get_info = lambda: ProviderInfo(id='fixture', display_name='Fixture',
                                            defaults={'model': 'model-a'})
    target = SelectedProvider(provider, {'instance': 'fixture', 'model': 'other-model'})
    coordinator.loop.root_provider = target
    origin = {'generationId': 'g', 'inputId': 'target', 'selectionRevision': 'choice',
              'selection': target.selection}
    coordinator.register_capability('web.context_transition.foreground', lambda: origin)
    recover = coordinator.get_capability('context.recovery.recover')
    async def lose(**kwargs):
        kwargs['admit_call']({'method': 'semantic', 'targetIdentity': kwargs['identity'],
            'sourceCheckpointSha256': accepted['sha256'], 'sequence': 1,
            'requestSha256': 'a' * 64, 'maxOutputTokens': 10})
        raise RuntimeError('unknown target inference')
    coordinator.register_capability('context.recovery.recover', lose)
    with pytest.raises(RuntimeError, match='unknown target'):
        await adapter.run_pending(provider=target, retain_contents=(), count_view=AsyncMock())
    journal = adapter.recovery_path.read_bytes()
    coordinator.register_capability('context.recovery.recover', recover)
    coordinator.loop.root_provider = provider
    provider.complete = AsyncMock(return_value=note)
    # The host gate must accept Context's strictly validated 'resuming'
    # disposition; ordinary source generation consumes its paid prefix.
    await adapter.run_pending(provider=provider, retain_contents=(), count_view=AsyncMock())
    await coordinator.context.get_messages_for_request(provider=provider)
    assert coordinator.context.summary is not None
    assert 'Acknowledged paid source part' in str(provider.complete.call_args_list[0])
    assert adapter.recovery_path.read_bytes() == journal


async def test_source_return_repeated_preparation_preserves_paid_failure_cooldown(tmp_path):
    from amplifier_core import ChatRequest, Message
    from amplifier_core.llm_errors import LLMError
    from amplifier_web.host.session import SelectedProvider
    case = await target_transition_fixture(tmp_path)
    async def lost(request):
        raise RuntimeError('unknown target reply')
    case.raw.compact_context = lost
    with pytest.raises(RuntimeError, match='unknown target'):
        await case.prepare()
    journal = case.adapter.recovery_path.read_bytes()
    source = SelectedProvider(case.raw, {'instance': 'fixture', 'model': 'selected-model'})
    case.coordinator.loop.root_provider = source
    case.raw.input_limit = 50000
    for row in (
        {'role': 'assistant', 'content': 'FILLER:' + 'new research ' * 2500},
        {'role': 'user', 'content': 'Correction after source return.'},
        {'role': 'assistant', 'content': 'Acknowledged.'},
        {'role': 'user', 'content': 'Continue source work.'},
    ):
        await case.coordinator.context.add_message(row)
    failed = AsyncMock(side_effect=LLMError('source temporarily offline', retryable=True))
    case.raw.compact_context = failed
    async def count(rows):
        request = ChatRequest(model='selected-model', messages=[Message(**row) for row in rows])
        return {'dispatch': request, 'budget_decision': source.request_budget(request)}
    async def prepare_source():
        return await case.coordinator.context.get_measured_request_view(
            provider=source, retain_contents=(), count_view=count)
    with pytest.raises(RuntimeError):
        await prepare_source()
    assert failed.await_count == 1
    failure = copy.deepcopy(case.coordinator.context.summary_failure)
    partial = copy.deepcopy(case.coordinator.context._summary_progress)
    assert failure and failure['retryable']
    with pytest.raises(RuntimeError, match='previous_failure'):
        await prepare_source()
    assert failed.await_count == 1
    assert case.coordinator.context.summary_failure == failure
    assert case.coordinator.context._summary_progress == partial
    assert case.adapter.recovery_path.read_bytes() == journal


async def test_checkpointless_unknown_target_allows_different_empty_canonical_identity_without_rearm(tmp_path):
    from amplifier_web.context_continuity import ContextContinuity
    from amplifier_web.host.session import SelectedProvider
    from amplifier_core import ChatRequest, Message
    case = await target_transition_fixture(tmp_path)
    directory = tmp_path / 'checkpointless'
    directory.mkdir()
    case.origin['originals'] = True
    case.coordinator.register_capability('web.context_transition.originals', True)
    case.adapter = ContextContinuity(case.coordinator, 'session', directory, AsyncMock(),
                                    writer_guard=case.gate.check_current)
    async def lost(request):
        raise RuntimeError('unknown checkpointless target')
    case.raw.compact_context = lost
    with pytest.raises(RuntimeError, match='unknown checkpointless'):
        await case.prepare()
    journal = case.adapter.recovery_path.read_bytes()
    other = SelectedProvider(case.raw, {'instance': 'fixture', 'model': 'different-canonical-model'})
    case.coordinator.loop.root_provider = other
    case.origin.update(inputId='new-other-input', generationId='other-generation',
        selectionRevision='other-choice', selection=other.selection)
    case.raw.input_limit = 50000
    case.coordinator.context.config['summarize_trigger'] = 1
    async def count(rows):
        request = ChatRequest(model='different-canonical-model', messages=[Message(**row) for row in rows])
        return {'dispatch': request, 'budget_decision': other.request_budget(request)}
    result = await case.coordinator.context.get_measured_request_view(
        provider=other, retain_contents=(), count_view=count)
    assert result['final_attempt']['dispatch'].model == 'different-canonical-model'
    assert case.coordinator.context.summary is None
    assert case.adapter.recovery_path.read_bytes() == journal
    # Returning to the uncertain target is still fenced even on a fresh input.
    case.coordinator.loop.root_provider = case.selected
    case.origin.update(inputId='target-again', generationId='target-again-generation',
        selectionRevision='target-choice-1', selection=case.selected.selection)
    with pytest.raises(RuntimeError, match='recovery_unreconciled'):
        await case.prepare()
    assert case.adapter.recovery_path.read_bytes() == journal


@pytest.mark.parametrize('kind', ['zero-call', 'completed-progress', 'installed-candidate'])
async def test_fresh_authorized_foreground_rebinds_durable_stop_epoch_without_replaying_work(tmp_path, monkeypatch, kind):
    from amplifier_web import context_continuity
    case = await target_transition_fixture(tmp_path)
    epoch = {'value': 7}
    case.adapter._interruption_revision = AsyncMock(side_effect=lambda: epoch['value'])
    case.coordinator.register_capability('web.context_recovery.epoch', lambda: epoch['value'])
    recover = case.coordinator.get_capability('context.recovery.recover')
    async def initial_failure(**kwargs):
        if kind == 'zero-call':
            raise RuntimeError('known refusal with zero effects')
        install = kwargs['install']
        async def fail(candidate, verification):
            if kind == 'installed-candidate':
                await install(candidate, verification)
            raise RuntimeError('lost installation ACK' if kind == 'installed-candidate' else 'known preinstall refusal')
        return await recover(**{**kwargs, 'install': fail})
    case.coordinator.register_capability('context.recovery.recover', initial_failure)
    with pytest.raises(RuntimeError):
        await case.prepare()
    prior = case.adapter._recovery_record()
    assert prior['interruptionRevision'] == 7
    paid = len(case.raw.compactions)
    saved = case.adapter.path.read_bytes()
    writes = []
    write = context_continuity.write_private
    def observe(path, text):
        if path == case.adapter.path:
            writes.append(text)
        return write(path, text)
    monkeypatch.setattr(context_continuity, 'write_private', observe)
    case.coordinator.register_capability('context.recovery.recover', recover)
    epoch['value'] = 8
    # Merely observing a newer Stop is never a fresh authorization.
    with pytest.raises(RuntimeError, match='recovery_unreconciled|recovery_interrupted'):
        await case.prepare()
    assert len(case.raw.compactions) == paid
    case.origin.update(inputId='new-epoch-input', generationId='new-epoch-generation')
    result = await case.prepare()
    assert result['outcome'] == 'transitioned'
    row = case.adapter._recovery_record()
    assert row['state'] == 'committed' and row['interruptionRevision'] == 8
    assert row['maxCalls'] == prior['maxCalls'] == 16
    assert any(attempt['interruptionRevision'] == 7 for attempt in row['preparations'])
    if kind != 'zero-call':
        assert len(case.raw.compactions) == paid
    if kind == 'installed-candidate':
        assert case.adapter.path.read_bytes() == saved
        assert writes == []


@pytest.mark.parametrize('change', ['stop-during-bind', 'source-during-bind', 'paid-contract', 'unknown-call'])
async def test_epoch_rebind_does_not_admit_changed_scope_or_uncertain_paid_effects(tmp_path, change):
    case = await target_transition_fixture(tmp_path)
    epoch = {'value': 7}
    case.adapter._interruption_revision = AsyncMock(side_effect=lambda: epoch['value'])
    case.coordinator.register_capability('web.context_recovery.epoch', lambda: epoch['value'])
    recover = case.coordinator.get_capability('context.recovery.recover')
    async def fail(**kwargs):
        if change in {'stop-during-bind', 'source-during-bind'}:
            raise RuntimeError('known prepay refusal')
        if change == 'unknown-call':
            kwargs['admit_call']({'method': 'native', 'targetIdentity': kwargs['identity'],
                'sourceCheckpointSha256': json.loads(case.accepted)['sha256'],
                'sequence': 1, 'requestSha256': 'b' * 64, 'maxOutputTokens': 100})
            raise RuntimeError('unknown paid reply')
        async def refuse_install(candidate, verification):
            raise RuntimeError('known local final refusal')
        return await recover(**{**kwargs, 'install': refuse_install})
    case.coordinator.register_capability('context.recovery.recover', fail)
    with pytest.raises(RuntimeError):
        await case.prepare()
    journal = case.adapter.recovery_path.read_bytes()
    paid = len(case.raw.compactions)
    case.coordinator.register_capability('context.recovery.recover', recover)
    epoch['value'] = 8
    case.origin.update(inputId='new-admitted-input', generationId='new-admitted-generation')
    if change == 'stop-during-bind':
        async def interrupt():
            value = epoch['value']
            epoch['value'] += 1
            return value
        case.adapter._interruption_revision = interrupt
    elif change == 'source-during-bind':
        async def replace_source():
            case.adapter.path.write_text('{}')
            return epoch['value']
        case.adapter._interruption_revision = replace_source
    elif change == 'paid-contract':
        case.contract['tools'] = 'changed after paid progress'
    with pytest.raises(RuntimeError, match='recovery_interrupted|recovery_unreconciled|recovery_source_changed'):
        await case.prepare()
    assert len(case.raw.compactions) == paid
    assert case.adapter.recovery_path.read_bytes() == journal


async def test_known_zero_effect_refusal_new_foreground_can_use_updated_tool_contract(tmp_path):
    from amplifier_web.context_continuity import ContextRecoveryError

    case = await target_transition_fixture(tmp_path)
    recover = case.coordinator.get_capability('context.recovery.recover')
    async def refuse(**kwargs):
        raise RuntimeError('known initial required-tool refusal')
    case.coordinator.register_capability('context.recovery.recover', refuse)
    with pytest.raises(RuntimeError):
        await case.prepare()
    old = copy.deepcopy(case.adapter._recovery_record())
    assert old['calls'] == [] and old['progress'] is None
    case.contract['tools'] = 'fewer authorized tools'
    case.coordinator.register_capability('context.recovery.recover', recover)
    with pytest.raises(ContextRecoveryError, match='recovery_unreconciled') as held:
        await case.prepare()
    assert held.value.reason == 'input_already_used'
    case.origin.update(inputId='new-input-with-fewer-tools', generationId='new-generation')
    assert (await case.prepare())['outcome'] == 'transitioned'
    row = case.adapter._recovery_record()
    assert row['commandId'] == old['commandId'] and row['maxCalls'] == old['maxCalls']
    assert row['contract'] != old['contract']
    assert any(attempt['contract'] == old['contract'] and attempt['state'] == 'refused'
               for attempt in row['preparations'])


async def test_source_continuation_rejects_replaced_checkpoint_without_clearing_target_fence(tmp_path):
    case = await target_transition_fixture(tmp_path)
    async def lost(request):
        raise RuntimeError('uncertain target')
    case.raw.compact_context = lost
    with pytest.raises(RuntimeError, match='uncertain target'):
        await case.prepare()
    journal = case.adapter.recovery_path.read_bytes()
    from amplifier_web.host.session import SelectedProvider
    case.coordinator.loop.root_provider = SelectedProvider(case.raw, {'model': 'selected-model'})
    await case.adapter.preserve(await case.coordinator.context.get_messages())
    case.adapter.path.write_text('{}')
    with pytest.raises(RuntimeError, match='recovery_unreconciled'):
        await case.prepare()
    assert case.adapter.recovery_path.read_bytes() == journal


def test_transition_errors_give_distinct_actionable_guidance():
    from amplifier_web.context_continuity import ContextRecoveryError
    missing = str(ContextRecoveryError('transition_intent_required'))
    rejected = str(ContextRecoveryError('recovery_checkpoint_rejected'))
    assert 'explicit model connection' in missing and 'new ordinary message' in missing
    assert 'schedules' in missing and 'retries' in missing
    assert 'Preserve the original history' in rejected and 'compatible validated source' in rejected
    assert 'explicit recovery' not in rejected
    assert 'Choosing a model does not repair corruption' in rejected


async def test_h2_zero_reservation_refusal_can_retry_only_new_foreground_same_binding(tmp_path):
    case = await target_transition_fixture(tmp_path)
    recover = case.coordinator.get_capability('context.recovery.recover')
    async def refuse_before_call(**kwargs):
        raise RuntimeError('known preparation refusal before paid admission')
    case.coordinator.register_capability('context.recovery.recover', refuse_before_call)
    with pytest.raises(RuntimeError, match='known preparation'):
        await case.prepare()
    row = case.adapter._recovery_record()
    assert row['state'] == 'refused' and row['calls'] == [] and row['progress'] is None
    case.coordinator.register_capability('context.recovery.recover', recover)
    with pytest.raises(RuntimeError, match='recovery_unreconciled'):
        await case.prepare()
    case.origin.update(inputId='new-authorized-input', generationId='new-generation')
    assert (await case.prepare())['outcome'] == 'transitioned'
    assert case.adapter.recovery_status()['attempts'] == 1
    assert case.adapter.recovery_status()['logicalCalls'] <= row['maxCalls'] == 16


@pytest.mark.parametrize('checkpointless', [False, True], ids=['checkpoint', 'canonical-originals'])
async def test_h2_settled_originals_tool_continuation_uses_ordinary_compaction(tmp_path, checkpointless):
    from amplifier_web.context_continuity import ContextContinuity
    case = await target_transition_fixture(tmp_path)
    case.raw.input_limit = 50000
    if checkpointless:
        directory = tmp_path / 'fork'
        directory.mkdir()
        case.origin['originals'] = True
        case.coordinator.register_capability('web.context_transition.originals', True)
        case.adapter = ContextContinuity(case.coordinator, 'session', directory,
            AsyncMock(), writer_guard=case.gate.check_current)
    assert (await case.prepare())['outcome'] == 'transition_fitted'
    journal = case.adapter.recovery_path.read_bytes()
    recover = AsyncMock(side_effect=AssertionError('settled originals transition rearmed'))
    case.coordinator.register_capability('context.recovery.recover', recover)
    # Same generation/initial input can have ordinary tool continuations. They
    # do not need another Send to reuse an acknowledged originals disposition.
    await case.prepare()
    assert case.raw.compactions, 'ordinary target trigger must remain enabled'
    recover.assert_not_awaited()
    assert case.adapter.recovery_path.read_bytes() == journal


@pytest.mark.parametrize('source', ['checkpoint', 'canonical-originals', 'canonical-unknown'])
async def test_empty_originals_ordinary_failure_keeps_cooldown_without_rearming(tmp_path, source):
    from amplifier_core import ChatRequest, Message
    from amplifier_core.llm_errors import LLMError
    from amplifier_web.context_continuity import ContextContinuity
    from amplifier_web.host.session import SelectedProvider
    case = await target_transition_fixture(tmp_path)
    if source != 'checkpoint':
        directory = tmp_path / 'fork'
        directory.mkdir()
        case.origin['originals'] = True
        case.coordinator.register_capability('web.context_transition.originals', True)
        case.adapter = ContextContinuity(case.coordinator, 'session', directory,
            AsyncMock(), writer_guard=case.gate.check_current)
    selected = case.selected
    prepare = case.prepare
    if source == 'canonical-unknown':
        async def lost(request):
            raise RuntimeError('original target UNKNOWN')
        case.raw.compact_context = lost
        with pytest.raises(RuntimeError, match='original target UNKNOWN'):
            await case.prepare()
        selected = SelectedProvider(case.raw, {'instance': 'fixture', 'model': 'other-canonical-model'})
        case.coordinator.loop.root_provider = selected
        case.origin.update(inputId='different-input', generationId='different-generation',
            selectionRevision='different-choice', selection=selected.selection)
        async def count(rows):
            request = ChatRequest(model='other-canonical-model', messages=[Message(**row) for row in rows])
            return {'dispatch': request, 'budget_decision': selected.request_budget(request)}
        async def prepare():
            return await case.coordinator.context.get_measured_request_view(
                provider=selected, retain_contents=(), count_view=count)
    else:
        case.raw.input_limit = 50000
        assert (await prepare())['outcome'] == 'transition_fitted'
    case.raw.input_limit = 50000
    journal = case.adapter.recovery_path.read_bytes()
    checkpoint = case.adapter.path.read_bytes() if case.adapter.path.exists() else None
    recover = AsyncMock(side_effect=AssertionError('ordinary failure rearmed historical transition'))
    case.coordinator.register_capability('context.recovery.recover', recover)
    failed = AsyncMock(side_effect=LLMError('temporary ordinary target outage', retryable=True))
    case.raw.compact_context = failed
    with pytest.raises(RuntimeError):
        await prepare()
    failure = copy.deepcopy(case.coordinator.context.summary_failure)
    assert failure and failure['retryable'] and failed.await_count == 1
    assert case.coordinator.context.summary is None
    assert case.coordinator.context._summary_progress is None
    assert case.adapter.recovery_path.read_bytes() == journal
    with pytest.raises(RuntimeError, match='previous_failure'):
        await prepare()
    assert case.coordinator.context.summary_failure == failure
    assert failed.await_count == 1
    recover.assert_not_awaited()
    assert case.adapter.recovery_path.read_bytes() == journal
    assert (case.adapter.path.read_bytes() if case.adapter.path.exists() else None) == checkpoint


async def test_h2_cold_settled_originals_needs_no_new_selection_intent_or_old_contract(tmp_path):
    from amplifier_core import ChatRequest, Message
    from amplifier_web.host.session import SelectedProvider
    case = await target_transition_fixture(tmp_path)
    case.raw.input_limit = 50000
    assert (await case.prepare())['outcome'] == 'transition_fitted'
    journal = case.adapter.recovery_path.read_bytes()
    coordinator, raw, _, adapter, _, _, _ = await native_fixture(tmp_path, case.source)
    raw.input_limit = 50000
    target = SelectedProvider(raw, {'instance': 'fixture', 'model': 'target-model', 'effort': 'high'})
    coordinator.loop.root_provider = target
    # No host foreground callback/generation on cold ordinary preparation; a
    # newer harmless options contract must not rearm historical work.
    bind_continuity_recovery_guards(coordinator, adapter, {'new-options': 1})
    coordinator.register_capability('web.context_transition.selection', lambda: {
        key: copy.deepcopy(case.origin.get(key)) for key in ('selection', 'selectionRevision', 'connection')})
    recover = AsyncMock(side_effect=AssertionError('cold settled originals rearmed'))
    coordinator.register_capability('context.recovery.recover', recover)
    async def count(rows):
        request = ChatRequest(model='target-model', messages=[Message(**r) for r in rows])
        return {'dispatch': request, 'budget_decision': target.request_budget(request)}
    result = await coordinator.context.get_measured_request_view(
        provider=target, retain_contents=(), count_view=count)
    assert result['outcome'] != 'transition_fitted'
    recover.assert_not_awaited()
    assert raw.compactions and raw.sent == []
    assert adapter.recovery_path.read_bytes() == journal


@pytest.mark.parametrize('damage', ['source-prefix', 'source-record', 'checkpoint-file', 'choice', 'connection', 'active-derived'])
async def test_h2_settled_originals_cannot_mask_changed_provenance_or_derived_unit(tmp_path, damage):
    case = await target_transition_fixture(tmp_path)
    case.raw.input_limit = 50000
    assert (await case.prepare())['outcome'] == 'transition_fitted'
    if damage == 'source-prefix':
        case.coordinator.context.messages[0]['content'] += ' changed'
    elif damage == 'source-record':
        case.adapter._accepted_record['summary']['message']['content'] += ' corrupt'
    elif damage == 'checkpoint-file':
        case.adapter.path.write_text('{"unexpected": "replacement"}')
    elif damage == 'choice':
        case.origin['selectionRevision'] = 'new-choice'
    elif damage == 'connection':
        case.origin['connection'] = 'different-account'
    else:
        accepted = json.loads(case.accepted)
        restore = case.coordinator.get_capability('context.checkpoint.restore')
        assert restore(accepted, accepted['identity'])['status'] == 'restored'
    messages = await case.coordinator.context.get_messages()
    await case.adapter.preserve(messages)
    assert not case.adapter._settled_originals(messages)
    assert case.adapter._prepared_identity is None


@pytest.mark.parametrize('damage', ['configuration', 'digest', 'unreadable', 'non-object'])
async def test_h3_rejected_same_identity_reconstructs_from_originals_and_saves(tmp_path, damage):
    original = native_history()
    first, _, _, old, checkpoint, _, prepare = await native_fixture(tmp_path, original)
    await prepare()
    await checkpoint()
    if damage == 'digest':
        record = json.loads(old.path.read_text())
        record['summary']['message']['content'] += ' INVALID'
        old.path.write_text(json.dumps(record))
    elif damage == 'unreadable':
        old.path.write_text('{invalid')
    elif damage == 'non-object':
        old.path.write_text('[]')
    newer, raw, _, adapter, _, _, prepare_new = await native_fixture(tmp_path, original)
    if damage == 'configuration':
        newer.context.config['summary_max_output_tokens'] = 999
    await prepare_new()
    adapter.save()
    assert adapter.public()['status'] != 'rejected'
    record = json.loads(adapter.path.read_text())
    assert record['sha256'] == digest({k: v for k, v in record.items() if k != 'sha256'})
    assert record['identity']['model'] == 'selected-model'
    assert raw.compactions and raw.sent == []
    assert not adapter.recovery_path.exists()
    assert await newer.context.get_messages() == original


async def test_transition_fitting_originals_has_no_paid_work_or_fake_install(tmp_path):
    case = await target_transition_fixture(tmp_path)
    case.raw.input_limit = 50000
    result = await case.prepare()
    assert result['outcome'] == 'transition_fitted'
    assert case.adapter.recovery_status()['outcome'] == 'prepared-originals'
    assert case.adapter.path.read_bytes() == case.accepted
    assert case.coordinator.context.summary is None
    assert case.raw.compactions == []
    case.origin.update(inputId='next-input', generationId='next-generation')
    await case.prepare()
    assert case.adapter.recovery_status()['attempts'] == 1
    # Ordinary target trigger policy remains enabled; it may now create its
    # own target summary, but never rearms the historical transition journal.
    assert case.adapter.recovery_status()['logicalCalls'] == 0
    if case.adapter.path.read_bytes() != case.accepted:
        assert json.loads(case.adapter.path.read_text())['identity']['model'] == 'target-model'


@pytest.mark.parametrize('route', ['selected', 'other-selection', 'lookalike'])
async def test_transition_accepts_implicit_model_only_for_selected_refit_route(tmp_path, route):
    from amplifier_core import ChatRequest, Message
    from amplifier_module_context_managed.errors import CompactionError
    from amplifier_module_loop_streaming._request_refit import RefitProvider
    from amplifier_web.host.session import SelectedProvider

    case = await target_transition_fixture(tmp_path)
    case.raw.input_limit = 50000
    owner = (SelectedProvider(case.raw, dict(case.selected.selection))
             if route == 'other-selection' else case.selected)
    wrapper = RefitProvider(owner, AsyncMock(side_effect=AssertionError('unexpected refit')))
    if route == 'lookalike':
        wrapper = SimpleNamespace(provider=owner, default_model=owner.default_model,
                                  request_budget=owner.request_budget)
    counted = []

    async def count(rows):
        request = ChatRequest(messages=[Message(**row) for row in rows])
        counted.append(request)
        return {'dispatch': request, 'budget_decision':
                wrapper.request_budget(request, context_estimate=10000)}

    prepare = case.coordinator.context.get_measured_request_view
    if route == 'selected':
        result = await prepare(provider=wrapper, count_view=count, retain_contents=())
        assert result['outcome'] == 'transition_fitted'
        assert result['final_attempt']['dispatch'] is counted[-1]
        assert counted[-1].model is None
    else:
        with pytest.raises(CompactionError) as error:
            await prepare(provider=wrapper, count_view=count, retain_contents=())
        assert error.value.code == 'recovery_request_changed'
    assert case.adapter.path.read_bytes() == case.accepted
    assert case.adapter.recovery_status()['logicalCalls'] == 0


@pytest.mark.parametrize('damage', ['digest', 'config', 'source'])
async def test_transition_rejects_invalid_source_without_laundering_identity(tmp_path, damage):
    case = await target_transition_fixture(tmp_path)
    if damage == 'digest':
        record = json.loads(case.accepted)
        record['summary']['message']['content'] += ' corrupt'
        case.adapter.path.write_text(json.dumps(record))
    elif damage == 'config':
        case.coordinator.context.config['summary_max_output_tokens'] = 777
    else:
        case.coordinator.context.messages[0]['content'] += ' changed'
    saved = case.adapter.path.read_bytes()
    await case.adapter.preserve(await case.coordinator.context.get_messages())
    assert case.adapter.public()['status'] == 'rejected'
    with pytest.raises(RuntimeError):
        await case.prepare()
    assert case.adapter.path.read_bytes() == saved
    assert case.raw.compactions == [] and not case.adapter.recovery_path.exists()


@pytest.mark.parametrize(('source', 'code'), [
    ('legacy', 'transition_unsupported'),
    ('missing-identity', 'recovery_checkpoint_rejected'),
    ('corrupt-current', 'recovery_checkpoint_rejected'),
    ('unreadable', 'recovery_checkpoint_rejected'),
])
async def test_cold_manual_recovery_checks_source_before_recording_intent(tmp_path, source, code):
    from amplifier_web.context_continuity import ContextRecoveryError
    case = await target_transition_fixture(tmp_path)
    adapter = case.adapter
    assert not adapter.loaded and not adapter.recovery_path.exists()
    record = json.loads(case.accepted)
    if source in ('legacy', 'missing-identity'):
        case.coordinator.register_capability('context.checkpoint.transition', None)
    if source == 'missing-identity':
        record['identity'] = {'provider': 'fixture', 'model': ''}
        adapter.path.write_text(json.dumps(record))
    elif source == 'corrupt-current':
        record['summary']['message']['content'] += ' damaged'
        adapter.path.write_text(json.dumps(record))
    elif source == 'unreadable':
        adapter.path.write_text('{')
    saved = adapter.path.read_bytes()
    with pytest.raises(ContextRecoveryError) as failure:
        await adapter.request_recovery('first-command-on-restart', 0)
    assert failure.value.code == code
    assert adapter.path.read_bytes() == saved
    assert not adapter.loaded and not adapter.recovery_path.exists()
    assert await case.coordinator.context.get_messages() == case.source
    assert not (case.raw.counted or case.raw.compactions or case.raw.sent)


async def test_cold_manual_recovery_keeps_existing_unknown_and_honors_stop(tmp_path):
    from amplifier_web.context_continuity import ContextRecoveryError
    case = await target_transition_fixture(tmp_path)
    adapter = case.adapter
    with pytest.raises(ContextRecoveryError, match='recovery_interrupted'):
        await adapter.request_recovery('old-stop-revision', 1)
    assert not adapter.recovery_path.exists()
    # Current Context can validate a foreign source; manual admission is inert.
    row = await adapter.request_recovery('real-manual', 0)
    assert row['state'] == 'pending' and not adapter.loaded
    adapter._recovery_record()['state'] = 'unknown'
    adapter._write_recovery()
    prior = adapter.recovery_path.read_bytes()
    case.coordinator.register_capability('context.checkpoint.transition', None)
    # An old writer may already have submitted recovery. Never reclassify or
    # erase that receipt, even if the new request would fail source validation.
    assert (await adapter.request_recovery('real-manual', 0))['state'] == 'unknown'
    with pytest.raises(ContextRecoveryError, match='recovery_unreconciled'):
        await adapter.request_recovery('second-command', 0)
    assert prior == adapter.recovery_path.read_bytes()
    assert case.accepted == adapter.path.read_bytes()
    assert not (case.raw.counted or case.raw.compactions or case.raw.sent)


async def test_cold_manual_recovery_with_unknown_target_never_arms(tmp_path, monkeypatch):
    from amplifier_web.context_continuity import ContextRecoveryError
    case = await target_transition_fixture(tmp_path)
    monkeypatch.setattr(case.selected, 'get_info', lambda: {'id': 'fixture', 'defaults': {}})
    with pytest.raises(ContextRecoveryError, match='recovery_identity_unavailable'):
        await case.adapter.request_recovery('unknown-model', 0)
    assert not case.adapter.recovery_path.exists()
    assert case.adapter.path.read_bytes() == case.accepted
    assert not (case.raw.counted or case.raw.compactions or case.raw.sent)


@pytest.mark.parametrize('prior_unknown', [False, True], ids=['no-attempt', 'unknown-target'])
async def test_known_rejected_foreign_checkpoint_cannot_arm_generic_explicit_recovery(tmp_path, prior_unknown):
    case = await target_transition_fixture(tmp_path)
    if prior_unknown:
        async def lost(request):
            raise RuntimeError('unknown existing target')
        case.raw.compact_context = lost
        with pytest.raises(RuntimeError, match='unknown existing target'):
            await case.prepare()
    journal = case.adapter.recovery_path.read_bytes() if prior_unknown else None
    record = json.loads(case.accepted)
    record['summary']['message']['content'] += ' corrupt'
    case.adapter.path.write_text(json.dumps(record))
    if prior_unknown:
        case.adapter._accepted_record = record
    await case.adapter.preserve(await case.coordinator.context.get_messages())
    assert case.adapter._foreign_rejection
    saved = case.adapter.path.read_bytes()
    with pytest.raises(RuntimeError, match='recovery_checkpoint_rejected'):
        await case.adapter.request_recovery('not-a-foreign-salvage-route', 0)
    assert case.adapter.path.read_bytes() == saved
    assert (case.adapter.recovery_path.read_bytes() if prior_unknown else None) == journal
    if prior_unknown:
        row = case.adapter._recovery_record()
        # A repeated exact command remains an idempotent read, not a new attempt.
        await case.adapter.request_recovery(row['commandId'], row['interruptionRevision'], row['targetBytes'])
        assert case.adapter.recovery_path.read_bytes() == journal
    else:
        assert not case.adapter.recovery_path.exists()
    assert case.raw.compactions == [] and case.raw.sent == []


@pytest.mark.parametrize('warm', [False, True], ids=['cold', 'warm'])
async def test_model_transition_preserves_source_checkpoint_without_foreground_intent(tmp_path, warm):
    """An explicit new model is not permission to reuse or replace opaque state."""
    from amplifier_web.context_continuity import ContextRecoveryError
    from amplifier_web.host.session import SelectedProvider
    source = native_history()
    first, raw, _, adapter, checkpoint, _, prepare = await native_fixture(tmp_path, source)
    await prepare()
    await checkpoint()
    saved = adapter.path.read_bytes()
    old_summary = copy.deepcopy(first.context.summary)
    if warm:
        coordinator = first
    else:
        coordinator, raw, _, adapter, checkpoint, _, prepare = await native_fixture(tmp_path, source)
    target = SelectedProvider(raw, {
        'instance': 'fixture', 'model': 'target-model', 'effort': 'low'})
    coordinator.loop.root_provider = target
    coordinator.register_capability('web.context_transition.foreground', lambda: None)
    from amplifier_core import ChatRequest, Message
    async def count_target(rows):
        request = ChatRequest(model='target-model', messages=[Message(**row) for row in rows])
        return {'dispatch': request, 'budget_decision':
                target.request_budget(request, context_estimate=10000)}
    async def prepare_target():
        return await coordinator.context.get_measured_request_view(
            provider=target, retain_contents=(), count_view=count_target)
    before = (len(raw.compactions), len(raw.sent))
    await adapter.preserve(source)  # Passive restore/classification: zero model work.
    assert adapter.public()['status'] == 'transition_required'
    adapter.save()
    assert adapter.path.read_bytes() == saved
    with pytest.raises(ContextRecoveryError, match='transition_intent_required'):
        await prepare_target()
    assert adapter.path.read_bytes() == saved
    assert (len(raw.compactions), len(raw.sent)) == before
    assert await coordinator.context.get_messages() == source
    if warm:
        assert coordinator.context.summary == old_summary


@pytest.mark.parametrize('change', [
    None, 'selection', 'maxOutputTokens', 'context', 'loop', 'plan',
], ids=['unchanged', 'model', 'output', 'context', 'loop', 'credentials'])
async def test_recovery_contract_digest_hides_secrets_and_fences_remount(tmp_path, change):
    from amplifier_web.context_continuity import ContextRecoveryError
    sentinels = ('CREDENTIAL-SENTINEL-72', 'PROMPT-SENTINEL-81', 'TOOL-SCHEMA-SENTINEL-96')
    contract = {
        'selection': {'model': 'selected-model'}, 'maxOutputTokens': 1000,
        'context': {'summarize_trigger': .2}, 'loop': {'use_streaming': False},
        'plan': {'providers': [{'config': {'api_key': sentinels[0]}}],
                 'instructions': sentinels[1], 'tools': [{'parameters': {'description': sentinels[2]}}]},
    }
    source = native_history()
    first, raw, _, adapter, _, _, _ = await native_fixture(tmp_path, source)
    gate = bind_continuity_recovery_guards(first, adapter, contract)
    pending = await adapter.request_recovery('digest-only', 0)
    journal = json.loads(adapter.recovery_path.read_text())
    assert journal['attempts']['digest-only']['contract'] == {'version': 1, 'sha256': digest(contract)}
    assert raw.counted == raw.compactions == raw.sent == []
    assert not adapter.path.exists()  # Arming did not checkpoint or compact.
    for sentinel in sentinels:
        assert sentinel not in adapter.recovery_path.read_text()
        assert sentinel not in json.dumps(pending)
    gate.release(gate.current())

    # A remount gets fresh object identities and a different dictionary order.
    newer_contract = copy.deepcopy(dict(reversed(list(contract.items()))))
    if change == 'maxOutputTokens':
        newer_contract[change] += 1
    elif change == 'selection':
        newer_contract[change]['model'] = 'different-model'
    elif change == 'context':
        newer_contract[change]['summarize_trigger'] = .3
    elif change == 'loop':
        newer_contract[change]['use_streaming'] = True
    elif change == 'plan':
        newer_contract[change]['providers'][0]['config']['api_key'] = 'changed-credential'
    second, newer_raw, _, newer, _, _, prepare = await native_fixture(tmp_path, source)
    bind_continuity_recovery_guards(second, newer, newer_contract)
    if change:
        with pytest.raises(ContextRecoveryError, match='recovery_contract_changed'):
            await prepare()
        assert newer.recovery_status()['state'] == 'failed'
        assert not newer.loaded and second.context.summary is None
        assert not newer.path.exists()
        assert newer_raw.counted == newer_raw.compactions == newer_raw.sent == []
    else:
        assert (await prepare())['outcome'] == 'recovered'
        assert newer.recovery_status()['state'] == 'committed'
    for sentinel in sentinels:
        assert sentinel not in newer.recovery_path.read_text()
        assert sentinel not in json.dumps(newer.recovery_status())


@pytest.mark.parametrize('invalidate', ['before-export', 'during-export'])
async def test_ordinary_checkpoint_save_fences_activation_after_recovery_commit(tmp_path, invalidate):
    source = native_history()
    coordinator, raw, _, adapter, _, _, prepare = await native_fixture(tmp_path, source)
    gate = bind_continuity_recovery_guards(coordinator, adapter, {'fixture': 1})
    await adapter.request_recovery('save-fence', 0)
    assert (await prepare())['outcome'] == 'recovered'
    assert adapter.recovery_status()['state'] == 'committed'
    saved = adapter.path.read_bytes()
    calls = (len(raw.counted), len(raw.compactions), len(raw.sent))
    export = coordinator.get_capability('context.checkpoint.export')
    exports = []
    coordinator.context.evidence_refs.append({'kind': 'fixture', 'revision': 'new-current-evidence'})

    def observed_export(identity):
        exports.append(identity)
        record = export(identity)
        if invalidate == 'during-export' and len(exports) == 1:
            gate.release(gate.current())
        return record

    coordinator.register_capability('context.checkpoint.export', observed_export)
    persist = coordinator.get_capability('context.persist_checkpoint')
    if invalidate == 'before-export':
        gate.release(gate.current())
    with pytest.raises(RuntimeError, match='released or superseded'):
        persist()
    assert len(exports) == (0 if invalidate == 'before-export' else 1)
    assert adapter.path.read_bytes() == saved

    gate.activate()
    persist()  # The current owner can still save ordinary progress after commit.
    record = json.loads(adapter.path.read_text())
    assert record['evidenceRefs'][-1] == {'kind': 'fixture', 'revision': 'new-current-evidence'}
    assert adapter.path.read_bytes() != saved
    assert adapter.recovery_status()['state'] == 'committed'
    assert (len(raw.counted), len(raw.compactions), len(raw.sent)) == calls


@pytest.mark.parametrize('state', ['installing', 'unknown', 'committed'])
@pytest.mark.parametrize('path', ['preserve', 'non-measured'])
async def test_lazy_restore_requires_committed_recovery_candidate(tmp_path, state, path):
    source = native_history()
    first, _, _, adapter, _, _, prepare = await native_fixture(tmp_path, source)
    gate = bind_continuity_recovery_guards(first, adapter, {'fixture': 1})
    await adapter.request_recovery('restore-fence', 0)
    assert (await prepare())['outcome'] == 'recovered'
    summary = copy.deepcopy(first.context.summary)
    candidate = adapter.path.read_bytes()
    journal = json.loads(adapter.recovery_path.read_text())
    attempt = journal['attempts'][journal['current']]
    assert attempt['candidateSha256'] == json.loads(candidate)['sha256']
    # Synthetic crash disposition over an actual verified candidate. The test
    # changes only receipt state; it does not invent provider output or replay it.
    attempt['state'] = state
    adapter.recovery_path.write_text(json.dumps(journal))
    retained = adapter.recovery_path.read_bytes()
    gate.release(gate.current())

    second, raw, selected, newer, _, _, _ = await native_fixture(tmp_path, source)
    bind_continuity_recovery_guards(second, newer, {'fixture': 1})
    assert not newer.loaded and second.context.summary is None
    async def read_view():
        if path == 'preserve':
            return await newer.preserve(source)
        return await second.context.get_messages_for_request_retaining(
            provider=selected, retain_contents=['Accepted reminder: retain result identities.'])
    if state != 'committed':
        await newer.preserve(source)
        assert newer.loaded and second.context.summary is None
        await read_view()
        newer.save()
        await read_view()  # Reading again is not reconciliation.
        assert newer.public()['status'] != 'restored'
    else:
        await read_view()
        assert newer.loaded and second.context.summary == summary
        assert newer.public()['status'] == 'restored'
    assert newer.path.read_bytes() == candidate
    assert newer.recovery_path.read_bytes() == retained
    assert raw.sent == []
    if path == 'preserve' or state == 'committed':
        assert raw.compactions == []
    if path == 'preserve':
        assert raw.counted == []
    else:
        assert raw.counted  # Ordinary fitting is not replay or another recovery.
    assert await second.context.get_messages() == source


@pytest.mark.parametrize('warm', [False, True])
@pytest.mark.parametrize('fault', ['malformed', 'noncurrent-candidate', 'other-candidate'])
async def test_ordinary_journal_fence_skips_transition_derived_state(tmp_path, warm, fault):
    source = native_history()
    coordinator, raw, _, adapter, _, _, _ = await native_fixture(tmp_path, source)
    bind_continuity_recovery_guards(coordinator, adapter, {'fixture': 1})
    if fault == 'malformed':
        adapter.recovery_path.write_text('{')
        adapter._journal = None
    else:
        await adapter.request_recovery('older', 0)
        older = adapter._journal['attempts']['older']
        older.update(state='unknown', candidateSha256='a' * 64)
        current = {**copy.deepcopy(older), 'commandId': 'current', 'state': 'committed'}
        current.pop('candidateSha256')
        adapter._journal['attempts']['current'] = current
        adapter._journal['current'] = 'current'
        if fault == 'other-candidate':
            adapter._reconciling = older
            adapter._journal['attempts']['another'] = {
                **copy.deepcopy(older), 'commandId': 'another'}
        adapter._write_recovery()
    retained = adapter.recovery_path.read_bytes()
    # Exercise the actual transition-helper seam that a plain ordinary fixture
    # lacks: it must never inspect an unreadable/unresolved journal.
    coordinator.register_capability('web.context_transition.selection', lambda: {})
    coordinator.register_capability('context.checkpoint.prepared_originals', lambda identity: True)
    if warm:
        adapter.loaded = True
        adapter._accepted_record = {'identity': {'provider': 'old', 'model': 'old'}}
        def forbidden(*args):
            raise AssertionError('journal fence must precede warm classification')
        coordinator.register_capability('context.checkpoint.transition', forbidden)
    await adapter.preserve(source)
    adapter.save()
    assert adapter.recovery_path.read_bytes() == retained
    assert not adapter.path.exists()
    assert await coordinator.context.get_messages() == source
    assert raw.compactions == [] and raw.counted == [] and raw.sent == []


@pytest.mark.parametrize('explicit_engine', [False, True])
@pytest.mark.parametrize('foreground_opt_out', [False, True], ids=['after_turn', 'foreground'])
async def test_live_plan_default_boundary_retains_native_checkpoint_across_reload(
        tmp_path, explicit_engine, foreground_opt_out):
    from amplifier_web.host.session import live_plan
    from amplifier_core import ChatRequest, Message
    requested = {'module': 'context-managed', 'config': {
        'max_tokens': 20000, 'summarize_trigger': .2,
        # This checks checkpoint continuity, not archive selection/omission.
        'archive_recovery': False,
        **({'compaction_schedule': 'foreground'} if foreground_opt_out else {}),
        **({'engine': 'boundary'} if explicit_engine else {})}}
    plan, _ = live_plan({'session': {'orchestrator': 'loop-live', 'context': requested}})
    config = plan['session']['context']['config']
    original = native_history()
    original[1]['content'] = 'FILLER:' + 'research ' * 12000

    c, raw, _, adapter, _, transcript, prepare = await native_fixture(
        tmp_path, original, context_config=config)
    assert c.context.requires_measured_foreground is (not foreground_opt_out)
    raw_limit = raw.input_limit
    # Canonical history is larger than the continuation budget, not merely
    # above a trigger. The fake native compactor accepts a larger source window.
    base_budget = raw.request_budget(ChatRequest(messages=[Message(**row) for row in original]))
    assert base_budget['estimated_input_tokens'] > raw_limit
    raw.input_limit = 50000
    if not foreground_opt_out:
        foreground = await prepare()
        assert foreground['final_attempt']['budget_decision']['estimated_input_tokens'] < raw_limit
        assert raw.compactions == raw.sent == []
        assert c.context.summary is None and not adapter.path.exists()
    result = await prepare(finish_turn=True)
    settled = await c.context.get_messages()
    assert result['final_attempt']['budget_decision']['estimated_input_tokens'] < raw_limit
    assert adapter.path.is_file(), 'Default Boundary must persist the accepted native checkpoint'
    first = json.loads(adapter.path.read_text())
    saved_bytes = adapter.path.read_bytes()
    assert first['summary']['kind'] == 'native'
    assert settled[:-1] == original
    assert json.loads(transcript.read_text()) == settled
    assert len(raw.sent) == len(raw.compactions) == 1

    # A fresh context/host adapter receives the same originals but cannot
    # compact this prefix again. Only restoring the accepted checkpoint works.
    next_raw = SyntheticNativeProvider()
    next_raw.nonreducing = True
    newer, next_raw, _, restored, _, _, next_prepare = await native_fixture(
        tmp_path, settled, provider=next_raw, context_config=config)
    restored.save()
    assert restored.path.read_bytes() == saved_bytes
    fitted = await next_prepare()
    assert restored.path.read_bytes() == saved_bytes
    assert restored.public()['status'] == 'restored'
    assert next_raw.compactions == next_raw.sent == []
    assert next_raw.counted, 'Restoration still counts; it is not zero provider calls'
    assert fitted['final_attempt']['budget_decision']['estimated_input_tokens'] < raw_limit
    assert await newer.context.get_messages() == settled

    next_raw.nonreducing = False
    for row in [
        {'role': 'assistant', 'content': 'FILLER:' + 'new research ' * 7000},
        {'role': 'user', 'content': 'Correction: keep report-v3 too.'},
        {'role': 'assistant', 'content': 'Acknowledged.'},
        {'role': 'user', 'content': 'Continue without replay.'},
    ]:
        await newer.context.add_message(row)
    next_raw.input_limit = 50000
    if not foreground_opt_out:
        foreground = await next_prepare()
        assert foreground['final_attempt']['budget_decision']['estimated_input_tokens'] < raw_limit
        assert next_raw.compactions == next_raw.sent == []
        assert restored.path.read_bytes() == saved_bytes
    await next_prepare(finish_turn=True)
    second_view = await next_prepare()
    second = json.loads(restored.path.read_text())
    assert second['summary']['throughMessage'] > first['summary']['throughMessage']
    assert second_view['final_attempt']['budget_decision']['estimated_input_tokens'] < raw_limit
    assert len(next_raw.compactions) == len(next_raw.sent) == 1
    text = json.dumps(next_raw.expanded(second_view['final_attempt']['dispatch']))
    for required in ('Build report; keep originals.', 'report-v1; effect completed once.',
                     'Correction: use report-v2.', 'Correction: keep report-v3 too.',
                     'Accepted instruction:', 'Accepted reminder:', 'uncertain-op'):
        assert required in text
    assert (await newer.context.get_messages())[:len(original)] == original
    print(json.dumps({'default_engine': not explicit_engine,
        'synthetic_canonical_tokens': base_budget['estimated_input_tokens'],
        'continuation_limit': raw_limit,
        'first_fitted': result['final_attempt']['budget_decision']['estimated_input_tokens'],
        'restored_fitted': fitted['final_attempt']['budget_decision']['estimated_input_tokens'],
        'second_fitted': second_view['final_attempt']['budget_decision']['estimated_input_tokens'],
        'covered_messages': [first['summary']['throughMessage'], second['summary']['throughMessage']]}))
