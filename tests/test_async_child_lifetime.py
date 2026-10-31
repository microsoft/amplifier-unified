"""Admitted finite children outlive a normal parent turn, not Stop or release."""
import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_assignments import app, start, limits, until


@pytest.mark.parametrize('operation,change', [(operation, change)
    for operation in ['tool', 'model', 'queued_model']
    for change in ['finished', 'new_parent', 'stop', 'configuration', 'assignment', 'owner', 'release',
                   'dependency_block', 'human_block', 'overflow_inputs', 'old_run', 'replaced_run', 'terminated_run',
                   'replaced_same_generation', 'terminated_same_generation']]
    + [('work', 'finished'), ('work', 'new_parent'), ('model', 'unassigned'), ('model', 'unassigned_configuration')]
    + [('full_work', change) for change in ['finished', 'new_parent', 'stop', 'owner', 'assignment',
                                           'configuration', 'terminated_same_generation', 'replaced_same_generation']])
async def test_native_child_continues_after_parent_finished(app, tmp_path, monkeypatch, operation, change):
    from amplifier_core import ToolResult
    from amplifier_core.message_models import ToolCall
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.orchestrator import BundleLiveOrchestrator
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.execution_events import ExecutionEvents
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.runtime import RuntimeManager, normalize_event
    from amplifier_web.runtime_worker import Worker
    from test_task_continuity import controls

    spec = importlib.util.spec_from_file_location('lifetime_child_fixture',
        Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            'agents': {'worker': {'instruction': 'Finish the admitted work'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths),
        Bundle(name='fixture', session=plan['session'], agents=plan['agents']))
    approvals = Approvals(None, AsyncMock(return_value='deny'))
    source = app.state['sessions'][0]
    if change not in {'unassigned', 'unassigned_configuration'}:
        await start(app)
    required = None
    if operation == 'full_work':
        from test_full_work_authority import commissioned_full
        _, _, _, source = await commissioned_full(app)
    elif operation == 'work':
        from test_work_invocation import prepared_work, assert_available_commission, current_work
        item = await prepared_work(app, source, 'native-child-work')
        _, source = await assert_available_commission(app, source, item, 'native-child-commission')
        required = (await current_work(app, source))['required']
    sid = source['id']
    generation_id = source['collaborationGeneration']['id']
    original_input = source['collaborationGeneration']['inputIds'][0]
    parent = await prepared.create_session(session_id=sid, approval_system=approvals)
    parent.coordinator.register_capability('session.working_dir', str(tmp_path))
    native_events = []
    runtime = Runtime(sid, observer=native_events.append)
    await runtime.emit('generation.started', generation_id=generation_id, initial_input_id=original_input)
    await runtime.emit('input.delivered', input_id=original_input)
    worker = Worker()
    worker.session, worker.runtime = parent, runtime
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.context_inputs = [original_input]
    worker.context_bindings = {}
    if change == 'overflow_inputs':
        for index in range(17):
            await runtime.emit('input.delivered', input_id=f'extra-input-{index}')
        source['collaborationGeneration']['inputIds'] = list(runtime.generation['input_ids'])
    manager = RuntimeManager()
    manager.app_bridge = app.app_bridge
    owner = {'process': SimpleNamespace(returncode=None), 'runtimeOwnerId': 'fixture-owner'}
    manager.workers[sid] = owner
    app.runtime.workers = manager.workers
    full_bindings = []
    active_binding = app.collaboration.active_binding
    def observe_binding(source, **options):
        result = active_binding(source, **options)
        if operation == 'full_work' and options.get('allow_descendant'):
            from amplifier_web.collaboration import PRINCIPAL
            full_bindings.append((PRINCIPAL.get(), deepcopy(result[1])))
        return result
    monkeypatch.setattr(app.collaboration, 'active_binding', observe_binding)
    async def bridge(operation, args):
        replies = []
        async def write(row, reply): replies.append(reply)
        manager._write = write
        await manager._bridge(sid, owner, {'id': 'bridge', 'operation': operation, 'args': args})
        reply = replies[0]
        if reply.get('error'): raise RuntimeError(reply['error'])
        return reply['result']
    worker.bridge = bridge
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))
    registry = await install_children(parent, prepared, runtime, SessionStore(tmp_path/'children'), approvals)
    ready, release = asyncio.Event(), asyncio.Event()
    calls, results = [], []
    control = controls(sid)
    control.capacity.admit = worker.admit_model
    emissions = []
    def publish(event):
        value = normalize_event(event, sid)
        if value: emissions.append(asyncio.create_task(app.on_runtime_event(*value)))
    telemetry = ExecutionEvents(sid, publish)
    worker.telemetry = telemetry
    telemetry.lifecycle({'type': 'input.delivered', 'input_id': original_input})
    telemetry.admission_guard = control.capacity.guard

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        ready.set()
        await release.wait()
        assert operation == 'queued_model' or 'same_generation' in change or (runtime.generation or {}).get('id') != generation_id
        if operation in {'tool', 'work', 'full_work'}:
            class Probe:
                name = 'probe'
                description = 'Read fixture state'
                input_schema = {'type': 'object', 'properties': {}}
                async def execute(self, arguments):
                    calls.append('tool')
                    return ToolResult(success=True, output='fixture')
            await coordinator.mount('tools', Probe(), name='probe')
            if operation == 'work':
                from test_coordination_delivery import InstalledWorkAdapter
                adapter = InstalledWorkAdapter()
                adapter.coordinator, adapter.tools, adapter.hooks = coordinator, tools, hooks
                adapter.messages, adapter.counter = [], 0
                adapter.loop = BundleLiveOrchestrator({})
                adapter.loop._tool_calls_this_turn = 0
                adapter.delivery = coordinator.get_capability('web.surface_delivery')
                coordinator.register_capability('web.provider_observe', lambda provider:
                    telemetry.instrument_provider(coordinator.session_id, provider,
                        run_id=coordinator.get_capability('web.worker_run')))
                await adapter.fill(required)
                class WorkProvider:
                    def get_info(self): return SimpleNamespace(id='fixture', defaults={'model': 'fixture'})
                    async def complete(self, value, **kwargs):
                        return SimpleNamespace(model_dump=lambda: {'content': [{
                            'type': 'tool_call', 'id': 'child-tool', 'name': 'probe', 'input': {}}]})
                provider = coordinator.get_capability('web.provider_transform')(WorkProvider())
                await provider.complete(adapter.request())
            result = await BundleLiveOrchestrator({})._execute_tool_only(
                ToolCall(id='child-tool', name='probe', arguments={}), tools, hooks, None, coordinator)
            results.append(result)
        else:
            class Provider:
                def get_info(self): return SimpleNamespace(id='fixture', defaults={'model': 'fixture'})
                async def complete(self, request):
                    calls.append('model')
                    return SimpleNamespace(usage={'input_tokens': 2, 'output_tokens': 1})
            provider = telemetry.instrument_provider(coordinator.session_id, Provider(),
                run_id=coordinator.get_capability('web.worker_run'))
            results.append(await provider.complete(SimpleNamespace(model='fixture')))
        return 'child complete'
    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', execute)
    slot_release, occupied = asyncio.Event(), asyncio.Event()
    blocking = None
    if operation == 'queued_model':
        await limits(app, source, maxConcurrentCalls=1)
        class OccupyingProvider:
            def get_info(self): return SimpleNamespace(id='fixture', defaults={'model': 'fixture'})
            async def complete(self, request):
                occupied.set()
                await slot_release.wait()
                return SimpleNamespace(usage={'input_tokens': 2, 'output_tokens': 1})
        occupying = telemetry.instrument_provider(sid, OccupyingProvider())
        blocking = asyncio.create_task(occupying.complete(SimpleNamespace(model='fixture')))
        await asyncio.wait_for(occupied.wait(), 5)
    task = asyncio.create_task(registry.spawn('worker', 'finish', parent, sub_session_id='native-child'))
    try:
        await asyncio.wait_for(ready.wait(), 10)
        if operation == 'queued_model':
            release.set()
            await until(lambda: getattr(app, '_model_admission_waiters', []))
            assert not task.done() and not calls
        if 'same_generation' not in change:
            event = await runtime.emit('generation.finished', disposition='manager_turn_finished')
            await app.on_runtime_event(*normalize_event(event, sid))
            assert source['collaborationGeneration']['terminal']
        assert not source.get('configurationBusy')
        if change == 'new_parent':
            event = await runtime.emit('generation.started', generation_id='new-parent', initial_input_id='unrelated')
            await app.on_runtime_event(*normalize_event(event, sid))
            event = await runtime.emit('input.delivered', input_id='unrelated')
            await app.on_runtime_event(*normalize_event(event, sid))
        elif change == 'stop':
            worker.stop_revision += 1
            source['interruptionRevision'] = 1
        elif change in {'configuration', 'unassigned_configuration'}:
            source['configurationPendingRevision'] = 1
        elif change == 'assignment':
            assignment = app.assignments.current(source)
            assignment['status'] = 'stopped'
            app.assignments.save(assignment)
        elif change == 'owner':
            manager.workers[sid] = {**owner, 'runtimeOwnerId': 'replacement'}
        elif change == 'release':
            worker.activation_gate.current_valid = False
        elif change in {'dependency_block', 'human_block'}:
            app.state.setdefault('runtimeControl', {}).setdefault(sid, {})['task.get'] = {'task': {
                'id': 'task', 'revision': 2, 'status': 'blocked',
                'blockProvenance': {'version': 1, 'revision': 2, 'dependency': True,
                                    'origin': 'agent' if change == 'dependency_block' else 'ui'}}}
        elif change in {'replaced_run', 'replaced_same_generation'}:
            registry.rows['native-child']['runId'] = 'replacement-run'
        elif change in {'terminated_run', 'terminated_same_generation'}:
            registry.rows['native-child']['status'] = 'completed'
        elif change == 'old_run':
            source.setdefault('execution', {'nodes': [], 'turns': []}).setdefault('nodes', []).append({'id': 'worker:old', 'kind': 'worker', 'sessionId': 'native-child',
                                             'runId': 'previous-run', 'rootSessionId': sid, 'phase': 'completed'})
        if operation == 'queued_model':
            # A capacity increase is not assignment revocation. The old provider
            # remains in flight while the admitted child gets the new slot.
            assignment = app.assignments.current(source)
            if assignment['status'] == 'active':
                await app.dispatch('assignment.set', {'sessionId': sid,
                    'expectedRevision': assignment['revision'], 'maxConcurrentCalls': 2},
                    command_id='increase-capacity')
            app.coordination.notify()
        release.set()
        allowed = change in {'finished', 'new_parent', 'dependency_block', 'overflow_inputs', 'old_run', 'unassigned'}
        try:
            outcome = await asyncio.wait_for(task, 10)
        except (RuntimeError, asyncio.CancelledError) as exc:
            assert not allowed, repr(exc)
        else:
            if allowed: assert outcome['output'] == 'child complete'
        assert calls == (['tool' if operation in {'tool', 'work', 'full_work'} else 'model'] if allowed else []), results
        if operation == 'full_work' and allowed:
            # The real Worker/RuntimeManager bridge supplied this assignment;
            # no fitted Work proof or new parent input supplies Full authority.
            assert full_bindings
            for actor, active in full_bindings:
                assert actor == 'native-child'
                assert active['id'] == generation_id
                assert active['inputIds'] == [original_input]
                assert active['interruptionRevision'] == 0 and not active['terminal']
        if blocking is not None: assert not blocking.done()
    finally:
        release.set()
        slot_release.set()
        if blocking is not None: await asyncio.gather(blocking, return_exceptions=True)
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if emissions: await asyncio.gather(*emissions)
        if change == 'release' and operation == 'queued_model':
            cancelled = [row for row in app.assignments.usage(app.assignments.current(source))['receipts']
                         if row['sessionId'] == 'native-child']
            assert len(cancelled) == 1 and cancelled[0]['phase'] == 'cancelled'
            assert cancelled[0]['usage']['totalTokens'] == cancelled[0]['usage']['costUsd'] == 0
        await control.close()
        await parent.cleanup()


@pytest.mark.parametrize('fault', ['none', 'stop', 'configuration', 'owner', 'run_complete', 'coverage',
                                   'dependency', 'task_human', 'task_missing', 'task_edit', 'task_objective', 'task_pause', 'new_task'])
async def test_commissioned_work_uses_child_assignment_without_root_impersonation(app, monkeypatch, fault):
    from test_work_invocation import prepared_work, assert_available_commission, current_work, transport
    from test_work_effects import tool_args, trusted_fixture
    from amplifier_web.collaboration import BINDING, PRINCIPAL
    from amplifier_web.service import AppError
    owner = app.state['sessions'][0]
    from amplifier_web.task_continuity import dispatch as task_dispatch
    from test_task_continuity import controls
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    control = controls(owner['id'])
    async def perform(sid, operation, args):
        assert sid == owner['id']
        return await control.perform(operation, args)
    monkeypatch.setattr(app.runtime, 'control', perform, raising=False)
    monkeypatch.setattr(app, 'management', SimpleNamespace(ensure_runtime=AsyncMock(), setup_manager=None, global_setup_manager=None,
        workspace_setup_managers={}, provider_catalog=SimpleNamespace(close=AsyncMock())))
    async def task_change(operation, origin='agent', **args):
        revision = (control.tasks.record() or {}).get('revision', 0)
        return await task_dispatch(app, operation, {'sessionId': owner['id'],
            'expectedRevision': revision, **args}, origin, 'task-' + str(revision), False)
    await task_change('task.create', objective='Complete the original work')
    item = await prepared_work(app, owner, 'child-lifetime-work')
    _, source = await assert_available_commission(app, owner, item, 'child-lifetime-commission')
    required = (await current_work(app, source))['required']
    child_id = 'native-worker'
    captured = deepcopy(source['collaborationGeneration'])
    private = {'rootSessionId': source['id'], 'runtimeSessionId': child_id,
        'generationId': captured['id'], 'inputIds': list(captured['inputIds']), 'runId': 'native-run',
        'interruptionRevision': 0, 'executionRevision': 0, 'configurationPendingRevision': 0}
    app.runtime.workers = {source['id']: {'process': SimpleNamespace(returncode=None),
                                         'runtimeOwnerId': 'native-owner'}}
    binding = {**transport(source, principal=child_id), '_childOrigin': private, '_runtimeOwnerId': 'native-owner'}
    source['collaborationGeneration'] = {'id': 'new-parent', 'inputIds': ['unrelated'], 'terminal': False}
    args = tool_args()
    witness = trusted_fixture(source, required, args)
    witness['origin']['runtimeSessionId'] = child_id
    if fault == 'stop': source['interruptionRevision'] = 1
    elif fault == 'configuration': source['configurationPendingRevision'] = 1
    elif fault == 'owner': app.runtime.workers[source['id']]['runtimeOwnerId'] = 'new-owner'
    elif fault == 'run_complete':
        source['execution'] = {'nodes': [{'kind': 'worker', 'sessionId': child_id,
                                         'runId': 'native-run', 'phase': 'completed'}]}
    elif fault == 'coverage': witness['coveredCount'] = 0
    if fault in {'dependency', 'task_human', 'task_missing', 'task_edit', 'task_objective', 'task_pause', 'new_task'}:
        if fault == 'task_edit': await task_change('task.update', constraints=['Changed instructions'])
        if fault == 'task_objective': await task_change('task.update', objective='Changed objective')
        if fault == 'task_pause':
            await task_change('task.pause')
            await task_change('task.resume')
        if fault == 'new_task':
            await task_change('task.complete', condition='Complete the original work', evidence=['result'])
            await task_change('task.create', objective='New work')
        await task_change('task.block', origin='ui' if fault == 'task_human' else 'agent',
                          reason='Waiting for admitted child')
        task = app.coordination.task(owner['id'])
        assert task['changes'][-1]['fields']['status'] == 'blocked'
        if fault == 'task_missing':
            task['changes'] = []  # Explicit missing-proof rejection, never a positive fixture.
    result = await app.app_bridge('work.effect.check', {**args, **binding, '_workOrigin': witness}, source['id'])
    assert result['allowed'] is (fault in {'none', 'dependency'}), result
    assert result['effectAuthorized'] is False
    if fault in {'none', 'dependency'}:
        token, actor = BINDING.set(binding), PRINCIPAL.set(child_id)
        try:
            header, _ = app.work_invocation.required(source['id'])
            assert header['status'] == 'available', header
            assert header['generationId'] == captured['id'] and header['inputIds'] == captured['inputIds']
            assert header['version'] == required['version']
            with pytest.raises(AppError): app.collaboration.active_binding(source)
            with pytest.raises(AppError): app.workspace_work.actor({'sessionId': source['id']}, 'agent', source['id'], {'path': source['workspace']})
        finally:
            PRINCIPAL.reset(actor); BINDING.reset(token)
    await control.close()
