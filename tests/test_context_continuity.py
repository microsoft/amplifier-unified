"""Host adapter against the actual optional context-managed boundary module."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_web.context_continuity import ContextContinuity, digest
from amplifier_web.runtime_controls import RuntimeControls
from test_task_continuity import Coordinator

boundary = pytest.importorskip('amplifier_module_context_managed.boundary')


async def mount(directory, messages):
    coordinator = Coordinator()
    coordinator.hooks = None
    async def mount_context(name, context): coordinator.context = context
    coordinator.mount = mount_context
    coordinator.register_contributor = lambda *args: None
    await boundary.mount_boundary(coordinator, {'durable_checkpoints': True, 'max_tokens': 6000, 'summarize_trigger': .2})
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
        return SimpleNamespace(content=[])


async def native_fixture(directory, original, *, provider=None):
    from amplifier_core import ChatRequest, Message, ToolSpec
    from amplifier_web.host.session import SelectedProvider
    from amplifier_web.surface_delivery import SurfaceProvider
    coordinator, _, adapter, checkpoint, transcript = await mount(directory, original)
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
    async def prepare():
        return await coordinator.context.get_measured_request_view(
            provider=selected, retain_contents=['Accepted reminder: retain result identities.'],
            count_view=count_view)
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


async def test_native_measured_context_reduces_twice_and_restores_without_provider_calls(tmp_path):
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
