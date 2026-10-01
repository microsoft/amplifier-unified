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
