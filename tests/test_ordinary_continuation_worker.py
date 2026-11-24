"""Offline ordinary Worker proof, also runnable against installed app artifacts.

The provider is deterministic and local. Neither these cases nor their token
scale establish vendor behavior or recovery of any original conversation.
"""
import asyncio
import copy
from contextlib import asynccontextmanager
from functools import wraps
import hashlib
import importlib
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path

import pytest

from amplifier_web.runtime_worker import Worker


PROVIDER = '''
import asyncio
import json
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
from amplifier_web.execution_events import CALL_PURPOSE

class Provider:
    name = "continuationfixture"
    def __init__(self):
        self.counted, self.sent, self.compactions = [], [], 0
        self.tool_issued = False
        self.calls = []
        self.foreground_sizes = []
        self.timeout_after_tool = False
        self.post_tool_timeout_seen = False
        self.summary_mode = "timeout"
        self.summary_entered = asyncio.Event()
        self.summary_release = asyncio.Event()
        self.summary_release.set()
        self.summary_cancelled = asyncio.Event()
        self.summary_returned = asyncio.Event()
        self.summary_requests = []
        self.maintenance_counts = []
        self.count_unavailable = False
        self.count_failures = 0
    def get_info(self):
        return ProviderInfo(id=self.name, display_name="Offline continuation fixture",
                            capabilities=["request_budget:provider_count"],
                            defaults={"model": "fixture"})
    async def list_models(self): return []
    def parse_tool_calls(self, response): return response.tool_calls or []
    async def request_budget(self, request, *, require_measurement=False, **kwargs):
        self.counted.append(request.model_copy(deep=True))
        count = len(request.model_dump_json().encode()) // 4
        if asyncio.current_task().get_name() == "context-managed-settled-compaction":
            self.maintenance_counts.append(count)
            if self.count_unavailable:
                self.count_failures += 1
                error = TimeoutError("Synthetic maintenance count unavailable")
                error.context_count_unavailable = True
                raise error
        if require_measurement:
            self.foreground_sizes.append(len(request.messages))
            if (self.timeout_after_tool and not self.post_tool_timeout_seen and
                    any(m.role == "tool" and m.tool_call_id == "once" for m in request.messages)):
                self.post_tool_timeout_seen = True
                error = TimeoutError("Synthetic input counter timed out after the tool settled")
                error.context_count_unavailable = True
                raise error
        return {"input_limit_independent_of_output": True, "estimated_input_tokens": count, "input_limit_tokens": 20000,
                "context_token_budget": 20000,
                "measurement": {"kind": "provider_count",
                                "source": "synthetic-serialized-request", "input_tokens": count}}
    async def complete(self, request, **kwargs):
        if (request.metadata or {}).get("purpose") == "context-compaction":
            self.compactions += 1
            self.calls.append("summary")
            self.summary_requests.append(request.model_copy(deep=True))
            self.summary_entered.set()
            if self.summary_mode == "timeout":
                raise TimeoutError("Synthetic unconfirmed compaction: must not retry")
            try:
                await self.summary_release.wait()
            except asyncio.CancelledError:
                self.summary_cancelled.set()
                if self.summary_mode != "late":
                    raise
                await self.summary_release.wait()
            self.summary_returned.set()
            return ChatResponse(content=[TextBlock(text="SOURCE-OBJECTIVE; SOURCE-CORRECTION; "
                "history-tool already completed; preserve canonical originals and never replay.")])
        if CALL_PURPOSE.get():
            return ChatResponse(content=[TextBlock(text="Offline fixture title")])
        assert request == self.counted[-1], "Dispatch changed after counting"
        assert len(request.model_dump_json().encode()) // 4 <= 20000
        self.sent.append(request.model_copy(deep=True))
        self.calls.append("foreground")
        text = request.model_dump_json()
        if "RUN-ONE-SETTLED-TOOL" in text and not any(
                m.role == "tool" and m.tool_call_id == "once" for m in request.messages):
            # The latest result is deliberately larger than the whole allowance.
            # After it settles the same turn must be allowed to shed this group.
            if not self.tool_issued:
                self.tool_issued = True
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


def offline_bundle(root, monkeypatch, shape):
    """The same local worker setup for omission and automatic-maintenance cases."""
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    workspace = root / 'workspace'
    workspace.mkdir()
    modules = {}
    for name, package in [('loop-live', 'amplifier_module_loop_live'),
                          ('context-managed', 'amplifier_module_context_managed'),
                          ('hook-context-intelligence', 'amplifier_module_hook_context_intelligence')]:
        spec = importlib.util.find_spec(package)
        assert spec and spec.origin, f'Missing prepared dependency: {package}'
        modules[name] = str(Path(spec.origin).parent)
    provider_name = 'continuationfixture' + shape.replace('-', '')
    provider = root / 'provider' / ('amplifier_module_provider_' + provider_name)
    provider.mkdir(parents=True)
    (provider / '__init__.py').write_text(PROVIDER.replace('continuationfixture', provider_name))
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
        'providers': [{'module': 'provider-' + provider_name, 'source': str(provider.parent)}],
        'hooks': [{'module': 'hook-context-intelligence',
                   'source': str(Path(modules['hook-context-intelligence']).parent),
                   'config': {'destinations': {}, 'context_intelligence_server_url': '',
                              'context_intelligence_api_key': '', 'base_path': str(root / 'capture')}}],
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
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(root / 'capture'))
    return workspace, bundle


async def close_worker(worker):
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


@pytest.mark.parametrize('shape', ['same-turn', 'recovery-references', 'tool-continuation', 'edited-input'])
async def test_actual_worker_completes_two_ordinary_turns_after_omission(
        tmp_path, monkeypatch, shape):
    from amplifier_core import ToolResult
    from amplifier_web import runtime_worker
    from amplifier_web.execution_events import _CONTINUATION_WARNING
    from amplifier_web.runtime import normalize_event

    workspace, bundle = offline_bundle(tmp_path, monkeypatch, shape)
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
        elif operation == 'work.effect.check':
            assert event['args']['toolName'] == 'large_result'
            result = {'allowed': True, 'effectAuthorized': True}
        else:
            raise AssertionError(f'Unexpected fixture bridge: {operation}')
        worker.bridges[event['id']].set_result(result)

    monkeypatch.setattr(runtime_worker, 'publish', publish)
    effects = []
    tool_case = shape in {'same-turn', 'tool-continuation'}
    result_size = 1700 if shape == 'tool-continuation' else 160000

    class LargeResult:
        name = 'large_result'
        description = 'One completed synthetic result'
        input_schema = {'type': 'object', 'properties': {}}

        async def execute(self, arguments):
            effects.append('once')
            return ToolResult(success=True, output={
                'status': 'completed', 'receipt': 'completed-once', 'filler': 'x' * result_size})

    try:
        await worker.start({'id': 'ordinary-continuation', 'workspace': str(workspace),
                            'bundle': str(bundle)}, raise_errors=True)
        await until(worker.idle)
        coordinator = worker.session.coordinator
        context = coordinator.get('context')
        raw = coordinator.get_capability('fixture.provider')
        raw.timeout_after_tool = shape == 'tool-continuation'
        await coordinator.mount('tools', LargeResult(), name='large_result')
        original = [
            {'role': 'user', 'content': 'Original objective: preserve Stop and originals.'},
            {'role': 'assistant', 'content': 'Settled historical evidence ' + 'h' * 100000},
            {'role': 'user', 'content': 'Settled correction: never replay completed work.'},
            {'role': 'assistant', 'content': 'Accepted.'},
        ]
        if shape in {'tool-continuation', 'edited-input'}:
            # Large settled worker exchanges like the two Spark-1 failures.
            for index in range(260):
                original.extend([
                    {'role': 'user', 'content': f'Readiness report {index}'},
                    {'role': 'assistant', 'content': 'Settled report ' + 'z' * 2400},
                ])
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
            text = ('RUN-ONE-SETTLED-TOOL' if tool_case and number == 1 else
                    f'CURRENT-INPUT-{number}: answer without replay.')
            if shape == 'edited-input' and number == 2:
                from amplifier_web.automatic_history import read_transcript
                from amplifier_web.session_files import project_slug
                edit_source = {'id': worker.session.session_id, 'workspace': str(workspace),
                               'status': 'idle', 'nativeProject': project_slug(workspace),
                               'nativeIdentity': worker.session.session_id, 'messages': []}
                saved = read_transcript(edit_source)
                target = next(row for row in saved['messages'] if row.get('inputId') == 'input-1'
                              or row.get('text') == 'CURRENT-INPUT-1: answer without replay.')
                edit_source.update(messages=saved['messages'], nativeRevision=saved['revision'])
                await worker.command({'op': 'control', 'id': 'edit', 'operation': 'history.edit',
                    'arguments': {'source': edit_source, 'operationId': 'input-2',
                                  'messageId': target['id'], 'text': text, 'attachments': []}})
            else:
                await worker.command({'op': 'send', 'id': f'send-{number}',
                                      'input_id': f'input-{number}', 'text': text})
            await until(lambda: (worker.idle() or worker.execution.done()) and any(
                e.get('type') in {'generation.finished', 'generation.failed'}
                and f'input-{number}' in e.get('input_ids', []) for e in events))
            assert not any(e.get('type') == 'generation.failed' for e in events), [
                e for e in events if e.get('type') == 'generation.failed']
        assert [event['text'] for event in events if event.get('type') == 'assistant.message'] == [
            'Ordinary answer completed.', 'Ordinary answer completed.']
        assert not any(e.get('type') in {'runtime.error', 'generation.failed'} for e in events)
        assert raw.calls[0] == 'foreground'
        assert raw.compactions <= 1  # An unconfirmed maintenance call cannot be rebought.
        if shape in {'tool-continuation', 'edited-input'}:
            assert raw.foreground_sizes  # Count contract survived the worker's provider wrappers.
            assert all(len(request.messages) < 260 for request in raw.sent)
        assert effects == (['once'] if tool_case else []), [
            [(m.role, str(m.content)[:160]) for m in request.messages] for request in raw.sent]
        canonical = await context.get_messages()
        assert canonical[:len(source)] == source
        if tool_case:
            results = [row for row in canonical if row.get('role') == 'tool'
                       and row.get('tool_call_id') == 'once']
            assert len(results) == 1
            assert 'completed-once' in results[0]['content']
            assert 'x' * result_size in results[0]['content']
            assert sum(any(call.get('id') == 'once' for call in row.get('tool_calls', []))
                       for row in canonical) == 1
        if shape == 'tool-continuation':
            assert raw.post_tool_timeout_seen
            assert any(m.role == 'tool' and m.tool_call_id == 'once' and 'completed-once' in str(m.content)
                       for m in raw.sent[1].messages)
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
        artifacts = os.environ.get('AMPLIFIER_TEST_ARTIFACTS')
        if artifacts:
            try:
                Path(artifacts, 'ordinary-worker-' + shape + '.json').write_text(json.dumps({
                    'events': events, 'effects': effects,
                    'executionDone': worker.execution.done() if worker.execution else None,
                    'generation': getattr(worker.runtime, 'generation', None),
                }, default=str))
            except (OSError, TypeError, ValueError):
                # Optional diagnostics must never replace the original failure
                # or prevent the native owner/session cleanup below.
                pass
        await close_worker(worker)


def loaded_modules(worker):
    """Actual imports, not the app lock or the worker's floating declarations."""
    coordinator = worker.session.coordinator
    names = {
        'context': type(coordinator.get('context')).__module__,
        'loop': type(coordinator.get('orchestrator')).__module__,
        'streaming': 'amplifier_module_loop_streaming',
        'core': 'amplifier_core',
        'maintenance': 'amplifier_module_context_managed.settled_maintenance',
    }
    result = {}
    for key, name in names.items():
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        result[key] = {'module': name, 'path': str(path),
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    # The specifically commissioned source graph. A different installed module
    # must be reported, not silently treated as this proof.
    expected = {
        'context': 'f696350b2e1bcf5b767f8ec60efa0ef720aa291dea6bf4f17e03186b0563dd66',  # 22ee
        'maintenance': 'cc5d824c8b4b824793392737cd2f92a19670d6c1b4e6296b74e74e72b88e9ac7',
        'loop': '6cbc05a44484d522055d1eece8d78e8f0192acca4c34a0bb64acf8aa7774ba65',  # ddc
        'streaming': '2fb687ce75ae371aff0aa7c2f8038e2d39448e9f49d2893779913b48cd969d2b',  # 1419
    }
    assert {key: result[key]['sha256'] for key in expected} == expected, result
    for key, distribution in [('context', 'amplifier-module-context-managed'),
                              ('loop', 'amplifier-module-loop-live'),
                              ('streaming', 'amplifier-module-loop-streaming')]:
        result[key]['directUrl'] = json.loads(
            importlib.metadata.distribution(distribution).read_text('direct_url.json') or 'null')
    result['core']['version'] = importlib.metadata.version('amplifier-core')
    assert result['core']['version'] == '2.0.2'
    return result


@asynccontextmanager
async def automatic_worker(app, config, monkeypatch):
    """Ordered in-process pipe adapter; capacity always uses the real host."""
    from amplifier_web import runtime_worker
    from amplifier_web.runtime import normalize_event
    from amplifier_web.preparation_context import OPERATIONS

    worker, queue = Worker(), asyncio.Queue()
    events, admissions, failures, schedules = [], [], [], []
    scheduled, finished, before_admission, release_admission = (
        asyncio.Event() for _ in range(4))
    original_admit = worker.admit_model

    async def admit(row):
        if row.get('purpose') == 'context_compaction':
            before_admission.set()
            await release_admission.wait()
        return await original_admit(row)

    monkeypatch.setattr(worker, 'admit_model', admit)

    def publish(event):
        events.append(copy.deepcopy(event))
        queue.put_nowait(copy.deepcopy(event))

    async def deliver():
        while True:
            event = await queue.get()
            try:
                if event.get('op') == 'bridge':
                    future = worker.bridges[event['id']]
                    values = {
                        'context.manifest': {'surfaces': [], 'inputIds': event['args'].get('_contextInputs', [])},
                        'message.reactions.context': {'items': []},
                        'coordination.current': {'sessionId': config['id'], 'grants': [], 'requests': [], 'bounded': True},
                        'memory.context': {'items': []},
                    }
                    try:
                        if event['operation'] == 'context.prepare':
                            result = {name: {'available': True, 'value': values[name]} for name in OPERATIONS}
                        elif event['operation'] in values:
                            result = values[event['operation']]
                        else:
                            result = await app.app_bridge(event['operation'], event['args'], config['id'])
                        if event['operation'] == 'capacity.admit':
                            admissions.append({'args': event['args'], 'result': copy.deepcopy(result),
                                'generation': copy.deepcopy(app._session(config['id']).get('collaborationGeneration')),
                                'status': app._session(config['id'])['status']})
                    except asyncio.CancelledError as error:
                        if asyncio.current_task().cancelling():
                            raise
                        admissions.append({'args': event['args'], 'error': type(error).__name__,
                                           'reason': str(error)})
                        if not future.done():
                            future.set_exception(error)
                    except Exception as error:
                        failures.append(error)
                        if not future.done():
                            future.set_exception(error)
                    else:
                        if not future.done():
                            future.set_result(result)
                else:
                    normalized = normalize_event(event, config['id'])
                    if normalized:
                        await app.on_runtime_event(*normalized)
                    if event.get('type') == 'generation.finished':
                        finished.set()
            except Exception as error:
                failures.append(error)
                raise
            finally:
                queue.task_done()

    monkeypatch.setattr(runtime_worker, 'publish', publish)
    reader = asyncio.create_task(deliver())
    try:
        await worker.start(config, raise_errors=True)
        await until(worker.idle)
        context = worker.session.coordinator.get('context')
        assert context.config['durable_checkpoints'] is True
        assert context.config['foreground_continuation'] is True
        assert context.config['compaction_schedule'] == 'after_turn'
        schedule = context.schedule_settled_compaction
        assert getattr(schedule, '_unified_settled', False), 'Worker.install_activity must bind it, not the fixture'

        @wraps(schedule)
        def observed_schedule(*args, **kwargs):
            result = schedule(*args, **kwargs)  # Only called by the real loop.
            schedules.append({'result': result, 'generation': worker.telemetry.generation_id,
                              'input': worker.telemetry.admission_input(config['id']),
                              'task': context._maintenance_task})
            scheduled.set()
            return result

        monkeypatch.setattr(context, 'schedule_settled_compaction', observed_schedule)
        raw = worker.session.coordinator.get_capability('fixture.provider')
        raw.summary_mode = 'success'
        yield dict(worker=worker, context=context, raw=raw, events=events, admissions=admissions,
                   schedules=schedules, scheduled=scheduled, finished=finished,
                   before_admission=before_admission, release_admission=release_admission,
                   queue=queue, failures=failures, origins=loaded_modules(worker))
    finally:
        release_admission.set()
        if worker.session:
            raw = worker.session.coordinator.get_capability('fixture.provider')
            if raw:
                raw.summary_release.set()
        try:
            await close_worker(worker)
            if not reader.done():
                await asyncio.wait_for(queue.join(), 10)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        assert not failures, failures


def reducible_history():
    return [
        {'role': 'user', 'content': 'SOURCE-OBJECTIVE: preserve originals and never replay.'},
        {'role': 'assistant', 'content': 'Settled research ' + 'e' * 50000},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'history-tool', 'name': 'already_completed', 'arguments': {}}]},
        {'role': 'tool', 'tool_call_id': 'history-tool', 'content': 'Completed once: source-receipt.'},
        {'role': 'user', 'content': 'SOURCE-CORRECTION: keep the completed receipt.'},
        {'role': 'assistant', 'content': 'Accepted.'},
        {'role': 'user', 'content': 'Continue from the existing evidence.'},
        {'role': 'assistant', 'content': 'Ready.'},
    ]


@pytest.mark.parametrize('outcome', ['success', 'host-stop', 'worker-stop', 'late-stop',
                                    'configuration', 'generation', 'persist-failure', 'count-unavailable'])
async def test_worker_automatically_compacts_only_its_settled_turn(tmp_path, monkeypatch, outcome):
    from amplifier_web.service import AppService
    from amplifier_web import context_continuity

    workspace, bundle = offline_bundle(tmp_path, monkeypatch, 'automatic' + outcome.replace('-', ''))
    app = AppService(tmp_path / 'service', workspace=workspace)
    record = {'outcome': outcome}
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        sid = session['id']
        await app.dispatch('assignment.start', {'sessionId': sid}, origin='ui')
        config = {'id': sid, 'workspace': str(workspace), 'bundle': str(bundle)}
        source = reducible_history()
        async with automatic_worker(app, config, monkeypatch) as case:
            worker, context, raw = case['worker'], case['context'], case['raw']
            record['origins'] = case['origins']
            await context.set_messages(copy.deepcopy(source))
            raw.count_unavailable = outcome == 'count-unavailable'
            if outcome == 'late-stop':
                raw.summary_mode = 'late'
                raw.summary_release.clear()
            case['finished'].clear()
            await worker.command({'op': 'send', 'id': 'ordinary-command',
                                  'input_id': 'ordinary-input', 'text': 'One ordinary answer, no tool replay.'})
            await asyncio.wait_for(case['scheduled'].wait(), 30)
            await asyncio.wait_for(case['finished'].wait(), 30)
            await until(worker.idle)
            await asyncio.wait_for(case['queue'].join(), 10)
            assert [e['text'] for e in case['events'] if e.get('type') == 'assistant.message'] == ['Ordinary answer completed.']
            assert not any(e.get('type') == 'generation.failed' for e in case['events'])
            assert raw.calls == ['foreground'] and len(raw.sent) == 1
            assert len(case['schedules']) == 1 and case['schedules'][0]['result'] is True
            scheduled = case['schedules'][0]
            task = scheduled['task']
            assert isinstance(task, asyncio.Task)
            generation = next(e for e in case['events'] if e.get('type') == 'generation.finished')
            assert scheduled['generation'] == generation['generation_id']
            assert scheduled['input'] == 'ordinary-input' and generation['input_ids'] == ['ordinary-input']
            assert session['collaborationGeneration']['completed'] and session['status'] == 'idle'
            continuity = worker.session.coordinator.get_capability('web.continuity.recovery')
            before = continuity.path.read_bytes() if continuity.path.exists() else None
            assert before is None and context.summary is None
            canonical = copy.deepcopy(await context.get_messages())
            assert canonical[:len(source)] == source

            if outcome != 'count-unavailable':
                await asyncio.wait_for(case['before_admission'].wait(), 10)
            if outcome == 'host-stop':
                session['interruptionRevision'] = session.get('interruptionRevision', 0) + 1
            elif outcome == 'configuration':
                session['configurationPendingRevision'] = session.get('configurationPendingRevision', 0) + 1
            elif outcome == 'generation':
                session['collaborationGeneration']['id'] = 'replacement-generation'
            elif outcome == 'worker-stop':
                await worker.command({'op': 'stop'})
            case['release_admission'].set()
            if outcome == 'late-stop':
                await asyncio.wait_for(raw.summary_entered.wait(), 10)
                await worker.command({'op': 'stop'})
                await asyncio.wait_for(raw.summary_cancelled.wait(), 10)
                raw.summary_release.set()
            settled = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 30)
            await asyncio.wait_for(case['queue'].join(), 10)
            assert raw.maintenance_counts and max(raw.maintenance_counts) > 20000 * .2
            assert await context.get_messages() == canonical
            assert raw.compactions == (1 if outcome in {'success', 'late-stop', 'persist-failure'} else 0)
            assert raw.calls.count('summary') == raw.compactions
            if outcome in {'success', 'persist-failure'}:
                assert settled == [None]
                saved = json.loads(continuity.path.read_text())
                assert saved['summary']['text'] and 'SOURCE-OBJECTIVE' in saved['summary']['text']
                assert saved['identity']['model'] == 'fixture'
                assert saved['sourceRevision']['sha256'] == context_continuity.digest(
                    canonical[:saved['sourceRevision']['messages']])
                maintenance = [row for row in case['admissions'] if row['args']['call'].get('purpose') == 'context_compaction']
                assert len(maintenance) == 1
                admission = maintenance[0]
                assert admission['args']['_settledCompaction'] is True
                assert admission['args']['_generationId'] == generation['generation_id']
                assert admission['generation']['completed'] and admission['status'] == 'idle'
                assert admission['result']['allowed'] is True
                record['checkpoint'] = saved
                record['checkpointSha256'] = hashlib.sha256(continuity.path.read_bytes()).hexdigest()
            else:
                assert context.summary is None and not continuity.path.exists()
                maintenance = [row for row in case['admissions']
                               if row['args'].get('call', {}).get('purpose') == 'context_compaction']
                if outcome in {'host-stop', 'configuration', 'generation'}:
                    assert isinstance(settled[0], asyncio.CancelledError), settled
                    assert len(maintenance) == 1 and maintenance[0]['error'] == 'CancelledError'
                    assert maintenance[0]['reason']
                    assert maintenance[0]['args']['_settledCompaction'] is True
                    assert maintenance[0]['args']['_generationId'] == generation['generation_id']
                elif outcome == 'worker-stop':
                    assert isinstance(settled[0], asyncio.CancelledError) and not maintenance
                elif outcome == 'late-stop':
                    assert isinstance(settled[0], asyncio.CancelledError), settled
                    assert raw.summary_cancelled.is_set() and raw.summary_returned.is_set()
                    assert len(maintenance) == 1 and maintenance[0]['result']['allowed']
                elif outcome == 'count-unavailable':
                    assert isinstance(settled[0], TimeoutError), settled
                    assert raw.count_failures == 1 and not maintenance
                    assert not raw.summary_entered.is_set()
            assert [e['text'] for e in case['events'] if e.get('type') == 'assistant.message'] == ['Ordinary answer completed.']
            assert not any(e.get('type') in {'generation.failed', 'runtime.error'} or
                           e.get('op') == 'reply' and e.get('error') for e in case['events'])
            if outcome not in {'host-stop', 'worker-stop', 'late-stop', 'generation'}:
                assert session['status'] == 'idle' and session['collaborationGeneration']['completed']
                assert (session.get('activity') or {}).get('phase') not in {'capacity', 'compaction'}
            record.update(scheduler=[{k: v for k, v in row.items() if k != 'task'} for row in case['schedules']],
                          admissions=case['admissions'], events=case['events'],
                          maintenanceCounts=raw.maintenance_counts, calls=raw.calls,
                          maintenanceOutcome=type(settled[0]).__name__)
            if outcome == 'persist-failure':
                # Establish a genuine earlier checkpoint above, not a fabricated
                # sentinel. Fail only its later automatic replacement write.
                previous_bytes = continuity.path.read_bytes()
                previous_summary = copy.deepcopy(context.summary)
                newer_history = reducible_history()
                newer_history[2]['tool_calls'][0]['id'] = 'later-history-tool'
                newer_history[3]['tool_call_id'] = 'later-history-tool'
                for row in newer_history:
                    await context.add_message(copy.deepcopy(row))
                case['release_admission'].clear()
                case['before_admission'].clear()
                case['finished'].clear()
                case['scheduled'].clear()
                writes = []
                original_write = context_continuity.write_private
                def fail_candidate(path, text):
                    if Path(path) == continuity.path and json.loads(text).get('summary') != saved['summary']:
                        writes.append(json.loads(text))
                        raise OSError('Synthetic checkpoint storage failure')
                    return original_write(path, text)
                monkeypatch.setattr(context_continuity, 'write_private', fail_candidate)
                await worker.command({'op': 'send', 'id': 'replacement-command',
                    'input_id': 'replacement-input', 'text': 'Answer about the newer evidence.'})
                await asyncio.wait_for(case['scheduled'].wait(), 30)
                await asyncio.wait_for(case['finished'].wait(), 30)
                await asyncio.wait_for(case['before_admission'].wait(), 10)
                await until(worker.idle)
                assert raw.calls == ['foreground', 'summary', 'foreground']
                second_canonical = copy.deepcopy(await context.get_messages())
                assert len(case['schedules']) == 2 and case['schedules'][1]['result'] is True
                second_task = case['schedules'][1]['task']
                case['release_admission'].set()
                failure = await asyncio.wait_for(asyncio.gather(second_task, return_exceptions=True), 30)
                await asyncio.wait_for(case['queue'].join(), 10)
                assert isinstance(failure[0], OSError), failure
                assert writes and raw.compactions == 2
                assert continuity.path.read_bytes() == previous_bytes
                assert context.summary == previous_summary
                assert await context.get_messages() == second_canonical
                assert not any(e.get('type') in {'generation.failed', 'runtime.error'} for e in case['events'])
                record['replacementFailure'] = {
                    'error': type(failure[0]).__name__, 'attemptedWrites': len(writes),
                    'previousCheckpointSha256': hashlib.sha256(previous_bytes).hexdigest(),
                    'currentCheckpointSha256': hashlib.sha256(continuity.path.read_bytes()).hexdigest(),
                    'summaryCalls': raw.compactions,
                }
        if outcome == 'success':
            async with automatic_worker(app, config, monkeypatch) as restored:
                restored['release_admission'].set()
                next_context, next_raw = restored['context'], restored['raw']
                assert (await next_context.get_messages())[:len(canonical)] == canonical
                await restored['worker'].command({'op': 'send', 'id': 'next-command',
                    'input_id': 'next-input', 'text': 'Continue using the saved summary without repeating it.'})
                await asyncio.wait_for(restored['finished'].wait(), 30)
                await until(restored['worker'].idle)
                await asyncio.wait_for(restored['queue'].join(), 10)
                if next_context._maintenance_task:
                    await asyncio.wait_for(asyncio.shield(next_context._maintenance_task), 30)
                adapter = restored['worker'].session.coordinator.get_capability('web.continuity.recovery')
                assert adapter.public()['status'] == 'restored'
                assert next_context.summary == (saved['summary']['throughMessage'], saved['summary']['text'])
                assert next_raw.compactions == 0 and len(next_raw.sent) == 1
                assert any(e.get('type') == 'generation.finished' and e.get('input_ids') == ['next-input']
                           for e in restored['events'])
                assert not any(e.get('type') in {'generation.failed', 'runtime.error'} or
                               e.get('op') == 'reply' and e.get('error') for e in restored['events'])
                assert saved['summary']['text'] in next_raw.sent[0].model_dump_json()
                assert json.loads(adapter.path.read_text())['summary'] == saved['summary']
                assert (await next_context.get_messages())[:len(canonical)] == canonical
                record['restored'] = {'origins': restored['origins'], 'status': adapter.public(),
                                      'calls': next_raw.calls, 'counted': len(next_raw.counted)}
    finally:
        print('AUTOMATIC-WORKER-COMPACTION', json.dumps(record, default=str))
        await app.close()