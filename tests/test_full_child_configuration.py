"""Mounted native children use standing Full through the real configuration owner."""
import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('amplifier_module_loop_live')
pytest.importorskip('amplifier_module_context_simple')

from amplifier_web.preferences import SettingsStore
from amplifier_web.service import AppError
from test_access_mode import save
from test_configuration_authority import app, finished, grant
from test_coordination_generation import until


@pytest.fixture
async def mounted(app, tmp_path, monkeypatch):
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.provider_isolation import ProviderIsolation
    from amplifier_web.runtime_worker import Worker

    spec = importlib.util.spec_from_file_location('full_configuration_child_fixture',
        Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            'agents': {'worker': {'instruction': 'Configure the delegated task'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths),
        Bundle(name='fixture', session=plan['session'], agents=plan['agents']))
    approval_prompt = AsyncMock(return_value='deny')
    approvals = Approvals(None, approval_prompt)
    parent = await prepared.create_session(session_id='native-root', approval_system=approvals)
    parent.coordinator.register_capability('session.working_dir', str(tmp_path))
    parent.coordinator.register_capability('web.provider_isolation', ProviderIsolation())
    source = app.state['sessions'][0]
    source.update(runtimeSessionId='native-root', collaborationGeneration={
        'id': 'generation-1', 'inputIds': ['assigned-input'], 'interruptionRevision': 0, 'terminal': False})
    app._publish_changes(sessions={source['id']})
    worker = Worker()
    worker.session = parent
    worker.runtime = SimpleNamespace(generation={'id': 'generation-1', 'input_ids': ['assigned-input']},
                                     closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.context_inputs = ['assigned-input']
    worker.context_bindings = {'assigned-input': {'clientId': None, 'targets': []}}
    envelopes, authorities = [], []

    async def transport(operation, args):
        envelopes.append(deepcopy(args))
        return await app.app_bridge(operation, args, source['id'])

    command = app.management.command

    async def retain_authority(action, args, command_id=None, authority=None, source=None, **options):
        authorities.append((command_id, deepcopy(authority)))
        return await command(action, args, command_id, authority, source, **options)

    monkeypatch.setattr(app.management, 'command', retain_authority)
    worker.bridge = transport
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))
    registry = await install_children(parent, prepared, Runtime('native-root'), SessionStore(tmp_path / 'children'), approvals)
    failures = []

    def install_execute(execute):
        async def checked(*args, **kwargs):
            try:
                return await execute(*args, **kwargs)
            except Exception as exc:
                # Children retain execution failures in their own outcome.
                # A failed test assertion must also fail the enclosing test.
                failures.append(exc)
                raise
        monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', checked)

    value = SimpleNamespace(app=app, source=source, parent=parent, worker=worker, registry=registry,
        envelopes=envelopes, authorities=authorities, approval_prompt=approval_prompt, install_execute=install_execute)
    try:
        yield value
    finally:
        await parent.cleanup()
        if failures:
            raise failures[0]


async def dispatch(control, action, args, identity):
    # Model-supplied private identities must never override the actual mount.
    return await control.execute({'operation': 'dispatch', 'args': {
        'action': action, 'args': args, 'id': identity,
        '_runtimeSessionId': 'native-root', '_generationId': 'forged-generation'}})


def permissions(source, path='/delegated-output'):
    return {'sessionId': source['id'], 'scope': 'local', 'allowed': [path], 'denied': []}


def saved_permissions(mounted):
    return SettingsStore(mounted.app.data_dir).read(mounted.source['workspace'], 'local')


@pytest.mark.parametrize('mode', ['finite', 'resumed', 'persistent', 'nested', 'nested-reprepare'])
async def test_mounted_full_configuration_and_own_reads_keep_actual_actor(mounted, monkeypatch, mode):
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.host.children import _PERSISTENT

    h = mounted
    await save(h.app)
    visited = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        actor = coordinator.session_id
        visited.append(actor)
        control = tools['app_control']
        for action, field in [('access.get', 'mode'), ('capability.read', 'accessMode')]:
            result = await dispatch(control, action, {'sessionId': h.source['id']}, action + str(len(visited)))
            assert result.success, result.error
            assert result.output['result'][field] == 'full'
        identity = 'child-save-' + str(len(visited))
        args = permissions(h.source, '/delegated-' + str(len(visited)))
        result = await dispatch(control, 'permissions.save', args, identity)
        assert result.success, result.error
        assert (await finished(h.app, identity))['phase'] == 'ready'
        authority = next(value for key, value in h.authorities if key == identity)
        assert authority['runtimeSessionId'] == actor != 'native-root'
        assert authority['sessionId'] == h.source['id'] and authority['origin'] == 'agent'
        assert authority['generationId'] == 'generation-1'
        assert authority['inputIds'] == ['assigned-input'] and authority['interruptionRevision'] == 0
        assert authority['inputCoverage'] == 'complete'
        assert h.envelopes[-1]['_runtimeSessionId'] == actor
        assert saved_permissions(h)['overrides']['tool-filesystem']['config']['allowed_write_paths'] == args['allowed']
        # A lost acknowledgement observes the exact receipt; no second effect.
        before = len(h.authorities)
        retried = await dispatch(control, 'permissions.save', args, identity)
        assert retried.success and retried.output['duplicate']
        assert len(h.authorities) == before
        changed = await dispatch(control, 'permissions.save', {**args, 'allowed': ['/different']}, identity)
        assert not changed.success and 'different' in str(changed.error)
        if mode in {'nested', 'nested-reprepare'} and actor == 'child':
            h.worker.runtime.generation['input_ids'].append('later-input')
            h.source['collaborationGeneration']['inputIds'].append('later-input')
            h.worker.context_inputs = ['later-input']
            h.worker.context_bindings = {'later-input': {'clientId': None, 'targets': []}}
            if mode == 'nested-reprepare':
                await install_app_access(coordinator, h.worker.app_access_bridge(coordinator))
            await h.registry.spawn('worker', 'nested configuration', h.registry.sessions['child'], sub_session_id='grandchild')
        return 'configured'

    h.install_execute(execute)
    token = _PERSISTENT.set(mode == 'persistent')
    try:
        await h.registry.spawn('worker', 'configure', h.parent, sub_session_id='child')
        if mode == 'resumed':
            await h.registry.resume('child', 'continue configuring', h.parent)
        assert visited == (['child', 'grandchild'] if mode.startswith('nested') else
                           ['child', 'child'] if mode == 'resumed' else ['child'])
        assert h.app.configuration_authority.rows(h.source['id']) == []
        assert not h.source['approvals']
        h.approval_prompt.assert_not_awaited()
    finally:
        _PERSISTENT.reset(token)


async def test_mounted_full_child_input_overflow_stays_unavailable_without_blocking_write(mounted):
    h = mounted
    await save(h.app)
    assigned = ['assigned-' + str(i) for i in range(17)]
    h.worker.runtime.generation['input_ids'] = list(assigned)
    h.source['collaborationGeneration']['inputIds'] = list(assigned)
    h.worker.context_inputs = list(assigned)
    h.worker.context_bindings = {identity: {'clientId': None, 'targets': []} for identity in assigned}
    completed = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        control = tools['app_control']
        result = await dispatch(control, 'permissions.save', permissions(h.source), 'overflow-write')
        assert result.success, result.error
        assert (await finished(h.app, 'overflow-write'))['phase'] == 'ready'
        assert saved_permissions(h)['overrides']['tool-filesystem']['config']['allowed_write_paths'] == ['/delegated-output']
        authority = next(value for key, value in h.authorities if key == 'overflow-write')
        assert authority['runtimeSessionId'] == coordinator.session_id == 'child'
        assert authority['sessionId'] == h.source['id'] and authority['origin'] == 'agent'
        assert authority['generationId'] == 'generation-1' and authority['interruptionRevision'] == 0
        assert authority['inputIds'] == [] and authority['inputCoverage'] == 'unavailable'
        assert h.envelopes[-1]['_inputBindings'] == []
        # The same bounded observation must survive in the durable operation,
        # without borrowing the root's complete input list to fill it in.
        h.source['status'] = 'working'
        plan = {'providers': [{'module': 'provider-fixture'}],
                'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}}}
        queued = await dispatch(control, 'configuration.apply', {
            'id': h.source['id'], 'config': plan, 'whenIdle': True}, 'overflow-queued')
        assert queued.success, queued.error
        assert (await finished(h.app, 'overflow-queued'))['phase'] == 'ready'
        record = h.app.management.configuration_record(h.source['id'], h.source['pendingConfiguration']['operationId'])
        retained = record['request']['authority']
        assert retained['runtimeSessionId'] == 'child' and retained['generationId'] == 'generation-1'
        assert retained['inputIds'] == [] and retained['inputCoverage'] == 'unavailable'
        assert not h.source['approvals'] and h.app.configuration_authority.rows(h.source['id']) == []
        h.approval_prompt.assert_not_awaited()
        completed.append(True)
        return 'configured'

    h.install_execute(execute)
    await h.registry.spawn('worker', 'configure with the assigned context', h.parent, sub_session_id='child')
    assert completed == [True]


async def test_mounted_child_inherits_source_full_and_retains_queued_configuration(mounted, monkeypatch):
    from amplifier_web.configuration_authority import fingerprint
    h = mounted
    ancestor = h.app.state['sessions'][1]
    await save(h.app, ancestor)
    h.source['collaboration'] = {'creatorSessionId': ancestor['id'], 'requestId': 'commission'}
    h.app._publish_changes(sessions={h.source['id']})
    assert h.app.access_mode.snapshot(h.source)['source'] == 'inherited'
    h.source['status'] = 'working'
    plan = {'providers': [{'module': 'provider-fixture'}],
            'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}}}

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        result = await dispatch(tools['app_control'], 'configuration.apply', {
            'id': h.source['id'], 'config': plan, 'whenIdle': True}, 'queued-child')
        assert result.success, result.error
        assert (await finished(h.app, 'queued-child'))['phase'] == 'ready'
        record = h.app.management.configuration_record(h.source['id'], h.source['pendingConfiguration']['operationId'])
        assert record['requestHash'] == fingerprint(record['request'])
        assert record['request']['config'] == plan
        assert record['request']['authority']['runtimeSessionId'] == coordinator.session_id
        assert record['request']['authority']['inputIds'] == ['assigned-input']
        before = deepcopy(record)
        await save(h.app, ancestor, mode='ask')
        with pytest.raises(AppError, match='Full access changed'):
            h.app.management.check_configuration_operation(h.source, record)
        assert h.app.management.configuration_record(h.source['id'], record['id']) == before
        return 'queued'

    h.install_execute(execute)
    await h.registry.spawn('worker', 'queue configuration', h.parent, sub_session_id='child')


@pytest.mark.parametrize('advance', ['input', 'generation', 'stop'])
async def test_full_child_first_mount_preserves_assignment_across_provider_await(mounted, monkeypatch, advance):
    h = mounted
    await save(h.app)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_provider_preparation(bundle, prepared, *, isolation):
        entered.set()
        await release.wait()

    monkeypatch.setattr('amplifier_web.provider_environment.materialize_bundle_providers', held_provider_preparation)
    outcomes = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        result = await dispatch(tools['app_control'], 'permissions.save', permissions(h.source), 'late-mount')
        outcomes.append(result.success)
        if result.success:
            assert (await finished(h.app, 'late-mount'))['phase'] == 'ready'
            assert h.authorities[-1][1]['inputIds'] == ['assigned-input']
        else:
            assert not h.authorities and saved_permissions(h) == {}
        return 'checked'

    h.install_execute(execute)
    task = asyncio.create_task(h.registry.spawn('worker', 'configure', h.parent, sub_session_id='child'))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        if advance == 'input':
            h.worker.runtime.generation['input_ids'].append('later-input')
            h.source['collaborationGeneration']['inputIds'].append('later-input')
            h.worker.context_inputs = ['later-input']
            h.worker.context_bindings = {'later-input': {'clientId': None, 'targets': []}}
        elif advance == 'generation':
            h.worker.runtime.generation = {'id': 'later-generation', 'input_ids': ['later-input']}
            h.source['collaborationGeneration'] = {'id': 'later-generation', 'inputIds': ['later-input'],
                                                   'interruptionRevision': 0, 'terminal': False}
        else:
            await h.app.dispatch('conversation.stop', {'sessionId': h.source['id']})
            h.worker.stop_revision += 1
        release.set()
        await asyncio.wait_for(task, 30)
        assert outcomes == [advance == 'input']
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('change', ['revoke', 'off-on', 'stop'])
async def test_full_child_delayed_write_rechecks_retained_authority(mounted, monkeypatch, change):
    h = mounted
    await save(h.app)

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        await h.app.management.lock.acquire()
        try:
            result = await dispatch(tools['app_control'], 'permissions.save', permissions(h.source), 'delayed-child')
            assert result.success, result.error
            await until(lambda: bool(h.authorities))
            if change == 'stop':
                await h.app.dispatch('conversation.stop', {'sessionId': h.source['id']})
            else:
                await save(h.app, mode='ask')
                if change == 'off-on':
                    await save(h.app)
        finally:
            h.app.management.lock.release()
        report = await finished(h.app, 'delayed-child')
        assert report['phase'] == 'error' and 'Full access changed' in report['error']
        assert saved_permissions(h) == {}
        return 'fenced'

    h.install_execute(execute)
    await h.registry.spawn('worker', 'configure', h.parent, sub_session_id='child')


@pytest.mark.parametrize('mode', ['ask', 'full'])
async def test_mounted_child_does_not_borrow_grants_or_root_controls(mounted, monkeypatch, mode):
    h = mounted
    row = await grant(h.app, ['file_access'], ['local'])
    if mode == 'full':
        await save(h.app)

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        control = tools['app_control']
        if mode == 'ask':
            result = await dispatch(control, 'permissions.save', permissions(h.source), 'borrow-grant')
            assert not result.success and 'child cannot borrow' in str(result.error)
        else:
            peer = h.app.state['sessions'][1]
            for action in ('access.get', 'capability.read'):
                result = await dispatch(control, action, {'sessionId': peer['id']}, 'peer-' + action)
                assert not result.success
            result = await dispatch(control, 'permissions.save', {**permissions(h.source), 'sessionId': peer['id']}, 'peer-write')
            assert not result.success
        for action, args in [
            ('access.set', {'sessionId': h.source['id'], 'scope': 'session', 'mode': 'full',
                            'expectedRevision': h.app.access_mode.snapshot(h.source)['revision']}),
            ('capability.grant', {'sessionId': h.source['id'], 'effects': ['file_access'], 'scopes': ['local'], 'reason': 'Self grant'}),
            ('capability.request', {'sessionId': h.source['id'], 'effects': ['file_access'], 'scopes': ['local'], 'reason': 'Borrow root'}),
            ('capability.revoke', {'sessionId': h.source['id'], 'id': row['id'], 'expectedRevision': row['revision']}),
            ('conversation.stop', {'sessionId': h.source['id']})]:
            result = await dispatch(control, action, args, 'root-control-' + action)
            assert not result.success, action
        assert saved_permissions(h) == {}
        assert h.app.configuration_authority.record(row['id'])['status'] == 'active'
        return 'unchanged'

    h.install_execute(execute)
    await h.registry.spawn('worker', 'inspect', h.parent, sub_session_id='child')
