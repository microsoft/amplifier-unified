"""Full continuation uses actual mounted actors and existing native effect owners."""
import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.collaboration import BINDING, PRINCIPAL
from amplifier_web.preferences import SettingsStore
from amplifier_web.runtime_controls import override_path
from amplifier_web.service import AppError
from test_access_mode import save
from test_configuration_authority import app, finished
from test_full_child_configuration import dispatch
from test_full_access_configuration_recovery import refused_operation
from test_patch_placement_recovery import placement, bind_args, MODULE, READ, BIND
from test_queued_configuration_boundary import process_boundary, process_apply, operation, plan


async def child(app, owner, tmp_path, monkeypatch, exercise):
    """Real Core/Foundation child and Worker bridge into the real AppService."""
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.runtime_worker import Worker

    if owner['id'] not in app.runtime.workers:
        await app.runtime.start(owner, app.on_runtime_event)
    host_owner = app.runtime.workers[owner['id']]
    assert host_owner['runtimeOwnerId'] and host_owner['process'].returncode is None
    spec = importlib.util.spec_from_file_location('configuration_continuation_child_fixture',
        Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    config = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
              'agents': {'worker': {'instruction': 'Continue the configuration task'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    prepared = fixture.ProbePrepared(config, BundleModuleResolver(paths),
        Bundle(name='fixture', session=config['session'], agents=config['agents']))
    ask = AsyncMock(return_value='deny')
    approvals = Approvals(None, ask)
    native = owner.get('runtimeSessionId') or owner['id']
    parent = await prepared.create_session(session_id=native, approval_system=approvals)
    parent.coordinator.register_capability('session.working_dir', owner['workspace'])
    active = owner['collaborationGeneration']
    assert not active.get('terminal')
    worker = Worker()
    worker.session = parent
    worker.start_config = {key: owner.get(key, 0) for key in
        ('interruptionRevision', 'executionRevision', 'configurationPendingRevision')}
    worker.stop_revision = owner.get('interruptionRevision', 0)
    worker.runtime = SimpleNamespace(generation={'id': active['id'], 'input_ids': list(active['inputIds'])},
                                     closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.context_inputs = list(active['inputIds'])
    worker.context_bindings = {identity: {'clientId': None, 'targets': []} for identity in active['inputIds']}
    # Keep the real subprocess writer intact. This adapter sends the in-process
    # native child's envelope through the same host owner authentication.
    bridge_host = RuntimeManager()
    bridge_host.workers = app.runtime.workers
    bridge_host.app_bridge = app.app_bridge
    async def transport(method, arguments):
        replies = []
        async def write(row, reply):
            assert row is host_owner
            replies.append(reply)
        bridge_host._write = write
        await bridge_host._bridge(owner['id'], host_owner,
            {'id': 'continuation-child-bridge', 'operation': method, 'args': arguments})
        assert len(replies) == 1
        reply = replies[0]
        if 'error' in reply:
            raise RuntimeError(reply['error'])
        return reply['result']
    worker.bridge = transport
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))
    registry = await install_children(parent, prepared, Runtime(native), SessionStore(tmp_path / 'continuation-children'), approvals)
    failures, completed = [], []
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        async def invoke(action, arguments, identity):
            result = await dispatch(tools['app_control'], action, arguments, identity)
            if not result.success:
                raise AssertionError(result.error)
            return result.output
        try:
            await exercise(invoke, coordinator.session_id)
            completed.append(True)
        except Exception as exc:
            failures.append(exc)
            raise
        return 'continued'
    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', execute)
    try:
        await registry.spawn('worker', 'continue', parent, sub_session_id='native-continuation-child')
        if failures:
            raise failures[0]
        assert completed == [True]
        ask.assert_not_awaited()
    finally:
        await parent.cleanup()


async def system(app, owner, action, args, identity):
    return await app.dispatch(action, args, origin='system', caller_session_id=owner['id'],
                              command_id=identity, include_state=False)


@pytest.mark.parametrize('actor', ['child', 'system'])
async def test_full_patch_placement_needs_exact_choice_without_human_message(placement, tmp_path, monkeypatch, actor):
    b = placement
    await save(b.app, b.owner)
    b.owner['messages'] = []
    b.owner['status'] = 'working'
    cold = (await system(b.app, b.owner, READ,
        {'sessionId': b.owner['id'], 'moduleId': MODULE}, 'cold-placement-read'))['result']
    assert cold['mounted']['status'] == 'unobserved' and not b.runtime.workers
    retained, submitted = [], []
    async def exercise(invoke, principal):
        snapshot = (await invoke(READ, {'sessionId': b.owner['id'], 'moduleId': MODULE}, 'full-placement-read'))['result']
        if actor == 'child':
            assert snapshot['mounted'] == {'status': 'observed', 'workingDirectory': str(b.home)}
            assert b.runtime.workers[b.owner['id']]['process'].returncode is None
        else:
            assert snapshot['mounted']['status'] == 'unobserved' and not b.runtime.workers
        args = bind_args(b, snapshot)
        args.pop('sourceMessageId')
        args['whenIdle'] = True
        result = await invoke(BIND, args, 'full-placement-bind')
        saved = b.app.management.configuration_record(b.owner['id'], result['result']['operationId'])
        authority = saved['request']['authority']
        assert authority['sessionId'] == b.owner['id']
        assert authority['runtimeSessionId'] == principal
        assert authority['origin'] == ('agent' if actor == 'child' else 'system')
        assert saved['request']['source']['sourceMessageId'] is None
        assert saved['request']['source']['choice'] == 'bind-to-execution'
        if actor == 'system':
            assert authority['generationId'] is None and authority['inputCoverage'] == 'unavailable'
        else:
            assert authority['generationId'] == b.owner['collaborationGeneration']['id']
            assert authority['inputIds'] == b.owner['collaborationGeneration']['inputIds']
        assert (await invoke(BIND, args, 'full-placement-bind'))['duplicate']
        retained.append(deepcopy(saved['request']))
        submitted.append(deepcopy(args))
    if actor == 'child':
        await child(b.app, b.owner, tmp_path, monkeypatch, exercise)
    else:
        await exercise(lambda action, args, identity: system(b.app, b.owner, action, args, identity), None)
    b.owner['status'] = 'idle'
    report = await finished(b.app, 'full-placement-bind')
    assert report['phase'] == 'ready', report
    record = b.app.management.configuration_record(b.owner['id'], b.owner['pendingConfiguration']['operationId'])
    assert record['request'] == retained[0] and record['state']['effect'] == 'mounted'
    expected = deepcopy(b.initial)
    expected['tools'][1]['config']['working_dir'] = str(b.checkout)
    assert json.loads(b.path.read_text()) == expected
    assert b.controls.read_bytes() == b.original_controls and b.canonical.read_bytes() == b.original_history
    assert not b.owner['approvals'] and b.app.configuration_authority.rows(b.owner['id']) == []
    if actor == 'system':
        await save(b.app, b.owner, mode='ask')
        duplicate = await system(b.app, b.owner, BIND, submitted[0], 'full-placement-bind')
        assert duplicate['duplicate']
        assert b.app.management.configuration_record(b.owner['id'], record['id']) == record


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('actor', ['child', 'system'])
@pytest.mark.parametrize('outcome', ['none', 'unknown'])
async def test_full_actor_recovers_only_original_proven_no_effect(process_boundary, tmp_path, monkeypatch, actor, outcome):
    b = process_boundary
    if outcome == 'none':
        saved = await refused_operation(b)
    else:
        await b.runtime._request(b.owner['id'], 'fixture.partial')
        assert (await process_apply(b))['phase'] == 'error'
        saved = operation(b)
    actors = []
    original_binding = b.app.collaboration.active_binding
    def observed_binding(source, **options):
        actors.append(PRINCIPAL.get())
        if (BINDING.get() or {}).get('_childOrigin') and source.get('pendingConfiguration', {}).get('effect') == 'unknown':
            from amplifier_web.child_execution import current
            assert current(b.app, source) is None  # Recovery does not reopen ordinary execution.
        return original_binding(source, **options)
    monkeypatch.setattr(b.app.collaboration, 'active_binding', observed_binding)
    async def exercise(invoke, principal):
        args = {'sessionId': b.owner['id'], 'operationId': saved['id']}
        view = (await invoke('configuration.operation.read', {**args, 'includeRecovery': True}, 'full-recovery-view'))['result']
        assert view['recovery']['canReconcile'] and not view['recovery']['canResume']
        reconciled = (await invoke('configuration.operation.reconcile', {
            **args, 'expectedRevision': saved['revision']}, 'full-recovery-reconcile'))['result']
        view = (await invoke('configuration.operation.read', {**args, 'includeRecovery': True}, 'full-recovery-current'))['result']
        assert view['recovery']['canResume'] == (outcome == 'none')
        assert operation(b)['request'] == saved['request'] and operation(b)['requestHash'] == saved['requestHash']
        expected_source = {'action': 'configuration.operation.reconcile',
            'origin': 'agent' if actor == 'child' else 'system', 'sessionId': b.owner['id'],
            'runtimeSessionId': principal,
            'generationId': b.owner['collaborationGeneration']['id'] if actor == 'child' else None,
            'interruptionRevision': b.owner.get('interruptionRevision', 0),
            'inputIds': b.owner['collaborationGeneration']['inputIds'] if actor == 'child' else [],
            'inputCoverage': 'complete' if actor == 'child' else 'unavailable'}
        assert operation(b)['history'][-1]['recoverySource'] == expected_source
        assert 'recoverySource' not in operation(b)['state']
        assert all('recoverySource' not in entry for entry in view['history'])
        if outcome == 'none':
            await b.runtime._request(b.owner['id'], 'fixture.busy', count=0)
            b.owner['status'] = 'working'
            result = await invoke('configuration.operation.resume', {
                **args, 'expectedRevision': reconciled['revision']}, 'full-recovery-resume')
            assert result['result']['state']['phase'] == 'queued'
            assert operation(b)['history'][-1]['recoverySource'] == {
                **expected_source, 'action': 'configuration.operation.resume'}
        else:
            # The original partial-write bytes stay unknown and cannot replay.
            before = override_path(b.owner['runtimeSessionId']).read_bytes()
            with pytest.raises((AssertionError, AppError), match='no-effect'):
                await invoke('configuration.operation.resume', {
                    **args, 'expectedRevision': reconciled['revision']}, 'full-recovery-refused')
            assert override_path(b.owner['runtimeSessionId']).read_bytes() == before
        if actor == 'child':
            assert principal == 'native-continuation-child' and principal in actors
    if actor == 'child':
        await child(b.app, b.owner, tmp_path, monkeypatch, exercise)
    else:
        await exercise(lambda action, args, identity: system(b.app, b.owner, action, args, identity), None)
    if outcome == 'none':
        b.owner['status'] = 'idle'
        await asyncio.wait_for(b.app.management.pending_config_tasks[b.owner['id']], 12)
        final = operation(b)
        assert final['state']['effect'] == 'mounted'
        assert final['request'] == saved['request'] and final['requestHash'] == saved['requestHash']
        assert final['history'][:len(saved['history'])] == saved['history']
        assert final['attempts'][0] == saved['attempts'][0] and len(final['attempts']) == 2
        assert json.loads(override_path(b.owner['runtimeSessionId']).read_text()) == plan(2)
    assert not b.owner['approvals'] and b.app.configuration_authority.rows(b.owner['id']) == []


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('blocked', [
    'ordinary-effect', 'other-operation', 'other-owner', 'foreign-fence', 'unsettled-writer',
    'revoke', 'off-on', 'stop', 'execution', 'configuration', 'replaced-process', 'terminal-run',
])
async def test_full_child_recovery_route_retains_execution_boundaries(process_boundary, tmp_path, monkeypatch, blocked):
    b = process_boundary
    saved = await refused_operation(b)
    observed = []
    original_binding = b.app.collaboration.active_binding
    def observe_binding(source, **options):
        result = original_binding(source, **options)
        observed.append(deepcopy((BINDING.get() or {})['_childOrigin']))
        return result
    monkeypatch.setattr(b.app.collaboration, 'active_binding', observe_binding)
    async def exercise(invoke, principal):
        args = {'sessionId': b.owner['id'], 'operationId': saved['id']}
        initial = (await invoke('configuration.operation.read', {**args, 'includeRecovery': True}, 'recover-before-change'))['result']
        assert initial['recovery']['canReconcile'] and observed
        action = 'configuration.operation.reconcile'
        args['expectedRevision'] = saved['revision']
        process = b.runtime.workers[b.owner['id']]
        if blocked == 'ordinary-effect':
            action = 'permissions.save'
            args = {'sessionId': b.owner['id'], 'scope': 'local', 'allowed': ['/not-admitted'], 'denied': []}
        elif blocked == 'other-operation':
            args['operationId'] = '00000000-0000-0000-0000-000000000000'
        elif blocked == 'other-owner':
            peer = b.app._new_session({'title': 'Other recovery owner'})
            b.app.state['sessions'].append(peer)
            args['sessionId'] = peer['id']
        elif blocked == 'foreign-fence':
            b.owner['configurationOperationFence'] = 'another-operation'
        elif blocked == 'unsettled-writer':
            await b.app.management.lock.acquire()
        elif blocked in {'revoke', 'off-on'}:
            await save(b.app, b.owner, mode='ask')
            if blocked == 'off-on':
                await save(b.app, b.owner)
        elif blocked in {'stop', 'execution', 'configuration'}:
            key = {'stop': 'interruptionRevision', 'execution': 'executionRevision',
                   'configuration': 'configurationPendingRevision'}[blocked]
            b.owner[key] = b.owner.get(key, 0) + 1
        elif blocked == 'replaced-process':
            b.runtime.workers[b.owner['id']] = {**process, 'runtimeOwnerId': 'replacement-owner'}
        elif blocked == 'terminal-run':
            b.owner.setdefault('execution', {}).setdefault('nodes', []).append({
                'kind': 'worker', 'sessionId': principal, 'runId': observed[-1]['runId'], 'phase': 'completed'})
        try:
            with pytest.raises((AssertionError, AppError, RuntimeError)):
                await invoke(action, args, 'blocked-child-recovery')
        finally:
            if blocked == 'unsettled-writer':
                b.app.management.lock.release()
            b.runtime.workers[b.owner['id']] = process
        assert operation(b) == saved
        assert not override_path(b.owner['runtimeSessionId']).exists()
        assert SettingsStore(b.app.data_dir).read(b.owner['workspace'], 'local') == {}
    await child(b.app, b.owner, tmp_path, monkeypatch, exercise)


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('change', ['stop', 'configuration', 'revoke'])
async def test_full_child_recovery_rechecks_after_dispatch_await(process_boundary, tmp_path, monkeypatch, change):
    from amplifier_web import workspace_work_effects
    b = process_boundary
    saved = await refused_operation(b)
    entered, release = asyncio.Event(), asyncio.Event()
    checks = []
    original_check = workspace_work_effects.check
    def observed_check(invocation, sid, args):
        result = original_check(invocation, sid, args)
        if args['toolInput'].get('args', {}).get('action') == 'configuration.operation.reconcile':
            checks.append(result)
        return result
    monkeypatch.setattr(workspace_work_effects, 'check', observed_check)
    original_route = b.app.collaboration.route
    async def held_route(action, args, origin, identity, caller):
        result = await original_route(action, args, origin, identity, caller)
        if action == 'configuration.operation.reconcile':
            # AppControl uses service-owned Work guards, not the separate
            # work.effect.check bridge used by other mounted tools. Hold its
            # real routing await after the first guard and before final admission.
            assert len(checks) == 1 and checks[0]['allowed'] and not checks[0]['effectAuthorized']
            assert result is None and PRINCIPAL.get() == 'native-continuation-child'
            entered.set()
            await release.wait()
        return result
    monkeypatch.setattr(b.app.collaboration, 'route', held_route)
    async def exercise(invoke, principal):
        with pytest.raises((AssertionError, AppError, RuntimeError)):
            await invoke('configuration.operation.reconcile', {'sessionId': b.owner['id'],
                'operationId': saved['id'], 'expectedRevision': saved['revision']}, 'changed-after-check')
    task = asyncio.create_task(child(b.app, b.owner, tmp_path, monkeypatch, exercise))
    try:
        await asyncio.wait_for(entered.wait(), 12)
        if change == 'revoke':
            await save(b.app, b.owner, mode='ask')
        else:
            key = 'interruptionRevision' if change == 'stop' else 'configurationPendingRevision'
            b.owner[key] = b.owner.get(key, 0) + 1
    finally:
        release.set()
        await asyncio.wait_for(task, 12)
    assert len(checks) == 2 and not checks[-1]['allowed']
    assert operation(b) == saved
    assert not override_path(b.owner['runtimeSessionId']).exists()


@pytest.mark.parametrize('change', ['revoke', 'off-on', 'stop'])
async def test_system_full_configuration_rechecks_authority_after_wait(app, change):
    owner = app.state['sessions'][0]
    await save(app, owner)
    await app.management.lock.acquire()
    try:
        result = await system(app, owner, 'permissions.save', {'sessionId': owner['id'],
            'scope': 'local', 'allowed': ['/must-not-save'], 'denied': []}, 'system-delayed')
        assert result['accepted']
        if change == 'stop':
            await app.dispatch('conversation.stop', {'sessionId': owner['id']})
        else:
            await save(app, owner, mode='ask')
            if change == 'off-on':
                await save(app, owner)
    finally:
        app.management.lock.release()
    result = await finished(app, 'system-delayed')
    assert result['phase'] == 'error' and 'Full access changed' in result['error']
    assert SettingsStore(app.data_dir).read(owner['workspace'], 'local') == {}


@pytest.mark.parametrize('invalid', ['ask', 'missing-owner', 'other-owner', 'stale-native', 'foreign-native'])
async def test_system_origin_does_not_replace_native_binding_or_explicit_owner(app, invalid):
    owner, peer = app.state['sessions']
    if invalid != 'ask':
        await save(app, owner)
    args = {'sessionId': peer['id'] if invalid == 'other-owner' else owner['id'],
            'scope': 'local', 'allowed': [], 'denied': []}
    actor = PRINCIPAL.set('foreign-native' if invalid == 'foreign-native' else
                          owner['id'] if invalid == 'stale-native' else None)
    binding = BINDING.set({'_runtimeSessionId': PRINCIPAL.get(), '_generationId': 'old'}
                         if invalid in {'stale-native', 'foreign-native'} else None)
    try:
        with pytest.raises(AppError):
            await app.dispatch('permissions.save', args, origin='system',
                caller_session_id=None if invalid == 'missing-owner' else owner['id'], command_id='invalid-system')
    finally:
        BINDING.reset(binding)
        PRINCIPAL.reset(actor)
    assert SettingsStore(app.data_dir).read(owner['workspace'], 'local') == {}


@pytest.mark.parametrize('invalid', ['choice', 'revision', 'foreign-owner'])
async def test_system_full_patch_retains_exact_target_and_choice(placement, invalid):
    b = placement
    await save(b.app, b.owner)
    snapshot = (await system(b.app, b.owner, READ,
        {'sessionId': b.owner['id'], 'moduleId': MODULE}, 'exact-read'))['result']
    args = bind_args(b, snapshot)
    args.pop('sourceMessageId')
    if invalid == 'choice':
        args.pop('choice')
    elif invalid == 'revision':
        args['expectedRevision'] = 'stale'
    else:
        args['sessionId'] = b.peer['id']
    before = b.path.read_bytes()
    with pytest.raises(AppError):
        await system(b.app, b.owner, BIND, args, 'invalid-binding')
    assert b.path.read_bytes() == before and not b.owner.get('pendingConfiguration')
