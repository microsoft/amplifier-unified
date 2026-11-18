"""Offline ordinary Worker proof, also runnable against installed app artifacts.

The provider is deterministic and local. Neither these cases nor their token
scale establish vendor behavior or recovery of any original conversation.
"""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from amplifier_web.runtime_worker import Worker


PROVIDER = '''
import json
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall

class Provider:
    name = "continuationfixture"
    def __init__(self):
        self.counted, self.sent, self.compactions = [], [], 0
    def get_info(self):
        return ProviderInfo(id=self.name, display_name="Offline continuation fixture",
                            defaults={"model": "fixture"})
    async def list_models(self): return []
    def parse_tool_calls(self, response): return response.tool_calls or []
    async def request_budget(self, request, **kwargs):
        self.counted.append(request.model_copy(deep=True))
        count = len(request.model_dump_json().encode()) // 4
        return {"estimated_input_tokens": count, "input_limit_tokens": 20000,
                "context_token_budget": 20000,
                "measurement": {"kind": "provider_count",
                                "source": "synthetic-serialized-request", "input_tokens": count}}
    async def complete(self, request, **kwargs):
        if (request.metadata or {}).get("purpose") == "context-compaction":
            self.compactions += 1
            raise TimeoutError("Synthetic unconfirmed compaction: must not retry")
        assert request == self.counted[-1], "Dispatch changed after counting"
        assert len(request.model_dump_json().encode()) // 4 <= 20000
        self.sent.append(request.model_copy(deep=True))
        text = request.model_dump_json()
        if "RUN-ONE-SETTLED-TOOL" in text and not any(
                m.role == "tool" and m.tool_call_id == "once" for m in request.messages):
            # The latest result is deliberately larger than the whole allowance.
            # After it settles the same turn must be allowed to shed this group.
            if len(self.sent) == 1:
                return ChatResponse(content=[], tool_calls=[
                    ToolCall(id="once", name="large_result", arguments={})])
        return ChatResponse(content=[TextBlock(text="Ordinary answer completed.")])

async def mount(coordinator, config=None):
    provider = Provider()
    coordinator.register_capability("fixture.provider", provider)
    await coordinator.mount("providers", provider, name=provider.name)
'''


async def until(predicate):
    async with asyncio.timeout(30):
        while not predicate():
            await asyncio.sleep(.01)


@pytest.mark.parametrize('shape', ['same-turn', 'recovery-references'])
async def test_actual_worker_completes_two_ordinary_turns_after_omission(
        tmp_path, monkeypatch, shape):
    from amplifier_core import ToolResult
    from amplifier_web import runtime_worker
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.execution_events import _CONTINUATION_WARNING
    from amplifier_web.runtime import normalize_event

    root = tmp_path
    workspace = root / 'workspace'
    workspace.mkdir()
    modules = {}
    for name, package in [('loop-live', 'amplifier_module_loop_live'),
                          ('context-managed', 'amplifier_module_context_managed')]:
        spec = importlib.util.find_spec(package)
        assert spec and spec.origin, f'Missing prepared dependency: {package}'
        modules[name] = str(Path(spec.origin).parent)
    provider = root / 'provider' / 'amplifier_module_provider_continuationfixture'
    provider.mkdir(parents=True)
    (provider / '__init__.py').write_text(PROVIDER)
    bundle = root / 'fixture.yaml'
    bundle.write_text(json.dumps({
        'bundle': {'name': 'ordinary-continuation-fixture', 'version': '0.0.1'},
        'session': {
            'orchestrator': {'module': 'loop-live', 'config': {
                'min_delay_between_calls_ms': 0, 'use_streaming': False}},
            # foreground_continuation is intentionally absent: test the host
            # default, not an opt-in which hides a missing assembly setting.
            'context': {'module': 'context-managed', 'config': {
                'max_tokens': 20000, 'summarize_trigger': .2,
                'archive_recovery': False, 'compaction_notice_enabled': False}}},
        'providers': [{'module': 'provider-continuationfixture', 'source': str(provider.parent)}],
    }))
    for name, folder in [('AMPLIFIER_HOME', 'amplifier-home'),
                         ('AMPLIFIER_WEB_HOME', 'home'),
                         ('AMPLIFIER_SESSION_STATE_HOME', 'shared'),
                         ('AMPLIFIER_UNIFIED_IMPORT_HOME', 'legacy')]:
        monkeypatch.setenv(name, str(root / folder))
    settings = root / 'amplifier-home' / 'settings.yaml'
    settings.parent.mkdir()
    settings.write_text(json.dumps({
        'bundle': {'active': str(bundle), 'app': []},
        'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)},
        'sources': {'modules': modules},
    }))
    events = []
    worker = Worker()

    def publish(event):
        events.append(copy.deepcopy(event))
        if event.get('op') != 'bridge':
            return
        operation = event['operation']
        from amplifier_web.preparation_context import OPERATIONS
        values = {
            'context.manifest': {'surfaces': [], 'inputIds': event['args'].get('_contextInputs', [])},
            'message.reactions.context': {'items': []},
            'coordination.current': {'sessionId': worker.session.session_id,
                                     'grants': [], 'requests': [], 'bounded': True},
            'memory.context': {'items': []},
        }
        if operation == 'context.prepare':
            result = {name: {'available': True, 'value': values[name]} for name in OPERATIONS}
        elif operation in values:
            result = values[operation]
        elif operation == 'capacity.admit':
            result = {'allowed': True, 'budgetRevision': worker.controls.capacity.policy['revision']}
        else:
            raise AssertionError(f'Unexpected fixture bridge: {operation}')
        worker.bridges[event['id']].set_result(result)

    monkeypatch.setattr(runtime_worker, 'publish', publish)
    effects = []

    class LargeResult:
        name = 'large_result'
        description = 'One completed synthetic result'
        input_schema = {'type': 'object', 'properties': {}}

        async def execute(self, arguments):
            effects.append('once')
            return ToolResult(success=True, output={
                'status': 'completed', 'receipt': 'completed-once', 'filler': 'x' * 160000})

    try:
        await worker.start({'id': 'ordinary-continuation', 'workspace': str(workspace),
                            'bundle': str(bundle)}, raise_errors=True)
        await until(worker.idle)
        coordinator = worker.session.coordinator
        context = coordinator.get('context')
        raw = coordinator.get_capability('fixture.provider')
        await coordinator.mount('tools', LargeResult(), name='large_result')
        original = [{'role': 'user', 'content': 'Original objective: preserve Stop and originals.'}]
        if shape == 'recovery-references':
            for index in range(244):
                original.append({'role': 'user', 'content': json.dumps({
                    'sourceRole': 'tool', 'toolCallId': f'settled-{index}',
                    'output': 'saved tool evidence ' + 'x' * 1600}),
                    'metadata': {'amplifier_recovery_reference': True, 'ephemeral': True}})
            # All historical tool outputs have become reference data: there is
            # deliberately no native tool-result group eligible for compaction.
            assert not any(row['role'] == 'tool' for row in original)
        await context.set_messages(copy.deepcopy(original))
        source = copy.deepcopy(await context.get_messages())
        for number in (1, 2):
            text = ('RUN-ONE-SETTLED-TOOL' if shape == 'same-turn' and number == 1 else
                    f'CURRENT-INPUT-{number}: answer without replay.')
            await worker.command({'op': 'send', 'id': f'send-{number}',
                                  'input_id': f'input-{number}', 'text': text})
            await until(lambda: worker.idle() and any(
                e.get('type') == 'generation.finished' and f'input-{number}' in e.get('input_ids', [])
                for e in events))
        assert [event['text'] for event in events if event.get('type') == 'assistant.message'] == [
            'Ordinary answer completed.', 'Ordinary answer completed.']
        assert not any(e.get('type') in {'runtime.error', 'generation.failed'} for e in events)
        assert raw.compactions <= 1
        assert effects == (['once'] if shape == 'same-turn' else [])
        canonical = await context.get_messages()
        assert canonical[:len(source)] == source
        marks = [normalize_event(event, worker.session.session_id)[1] for event in events
                 if event.get('type') == 'execution.event'
                 and event['event'].get('incompleteHistoryDispatched')]
        assert marks
        assert all(mark['admissionInputId'] in {'input-1', 'input-2'} for mark in marks)
        warned = [request for request in raw.sent if _CONTINUATION_WARNING in request.model_dump_json()]
        assert warned and all(any(m.role == 'developer' and m.content == _CONTINUATION_WARNING
                                  for m in request.messages) for request in warned)
        assert 'CURRENT-INPUT-2' in raw.sent[-1].model_dump_json()
    finally:
        if worker.execution:
            worker.execution.cancel()
            await asyncio.wait_for(asyncio.gather(worker.execution, return_exceptions=True), 10)
        if worker.controls:
            await worker.controls.close()
        if worker.session:
            await worker.session.cleanup()
        if worker.activation_gate and worker.activation:
            worker.activation_gate.release(worker.activation)
        if worker.ownership.registration:
            await worker.ownership.registration.close()
        if worker.shared_handle:
            await asyncio.to_thread(worker.shared_handle.release)