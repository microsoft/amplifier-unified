"""Shared Full capture through real domain actions and a mounted native child."""
import copy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.collaboration import BINDING, PRINCIPAL
from amplifier_web.service import AppError
from test_feedback_attachments import submission
from test_full_access_observations import full_fixture, mode
from test_full_feedback import github, saved, settle


async def child(app, owner, tmp_path, monkeypatch, exercise):
    """Mount a real finite child against the observation fixture's owner."""
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.runtime_worker import Worker
    from test_full_child_configuration import dispatch

    spec = importlib.util.spec_from_file_location('full_capture_child_fixture',
        Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    config = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
              'agents': {'worker': {'instruction': 'Observe the delegated task'}}}
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
    worker = Worker()
    worker.session = parent
    worker.runtime = SimpleNamespace(generation={'id': active['id'], 'input_ids': list(active['inputIds'])},
                                     closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.context_inputs = list(active['inputIds'])
    worker.context_bindings = {identity: {'clientId': None, 'targets': []} for identity in active['inputIds']}
    manager = RuntimeManager()
    manager.app_bridge = app.app_bridge
    runtime_owner = {'process': SimpleNamespace(returncode=None), 'runtimeOwnerId': 'fixture-owner'}
    manager.workers[owner['id']] = runtime_owner
    monkeypatch.setattr(app.runtime, 'workers', manager.workers)
    replies, bridge_id = {}, 0

    async def write(row, reply):
        assert row is runtime_owner
        replies[reply['id']] = reply

    manager._write = write

    async def transport(method, arguments):
        nonlocal bridge_id
        bridge_id += 1
        identity = 'bridge-' + str(bridge_id)
        await manager._bridge(owner['id'], runtime_owner, {
            'id': identity, 'operation': method, 'args': arguments})
        reply = replies.pop(identity)
        if reply.get('error'):
            raise RuntimeError(reply['error'])
        return reply['result']

    worker.bridge = transport
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))
    registry = await install_children(parent, prepared, Runtime(native), SessionStore(tmp_path / 'children'), approvals)
    failures, completed = [], []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        async def invoke(action, arguments, identity):
            result = await dispatch(tools['app_control'], action, arguments, identity)
            assert result.success, result.error
            return result.output
        try:
            await exercise(invoke, coordinator.session_id)
            completed.append(True)
        except Exception as exc:
            failures.append(exc)
            raise
        return 'observing'

    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', execute)
    try:
        await registry.spawn('worker', 'observe', parent, sub_session_id='native-observer-child')
        if failures:
            raise failures[0]
        assert completed == [True]
        ask.assert_not_awaited()
    finally:
        await parent.cleanup()


@pytest.mark.parametrize('input_count', [1, 17])
async def test_native_child_observation_keeps_actual_bounded_assignment(tmp_path, monkeypatch, input_count):
    app, runtime, _, sid, config, _, _, descriptor = await full_fixture(tmp_path, monkeypatch)
    try:
        source = app._session(sid)
        assigned = ['assigned-' + str(i) for i in range(input_count)]
        source['collaborationGeneration']['inputIds'] = list(assigned)
        captured = []
        async def exercise(invoke, principal):
            # The child's installer already captured its inputs. A later input
            # in this same root generation must not be borrowed for attribution.
            source['collaborationGeneration']['inputIds'].append('later-root-input')
            qualified = (await invoke('observation.qualify', {
                'sessionId': sid, 'descriptor': descriptor}, 'child-qualify'))['result']['observer']
            preview = (await invoke('observation.preview', config, 'child-preview'))['result']
            watch = (await invoke('observation.create', {**config,
                'previewHash': preview['previewHash'], 'requestId': 'child-watch'}, 'child-create'))['result']['watch']
            authority = watch['authorization']
            assert authority == qualified['authorization']
            assert authority['origin'] == 'agent' and authority['sessionId'] == sid
            assert authority['runtimeSessionId'] == principal == 'native-observer-child'
            assert authority['generationId'] == 'system-work'
            assert authority['inputIds'] == (assigned if input_count == 1 else [])
            assert authority['inputCoverage'] == ('complete' if input_count == 1 else 'unavailable')
            assert 'inputCount' not in authority and 'messageId' not in authority
            captured.append(copy.deepcopy(watch))
        await child(app, source, tmp_path, monkeypatch, exercise)
        source['collaborationGeneration']['terminal'] = True
        # Accepted background observation outlives the finite child. The actual
        # MCP fixture read is pending and does not start a provider/handoff.
        await app.observations.tick()
        retained = app.observations.store.get('watch', captured[0]['id'])
        assert retained['status'] == 'active'
        assert retained['authorization'] == captured[0]['authorization']
        assert len(app.observations.store.rows('run')) == 1
        runtime.observation_input.assert_not_awaited()
        assert not app.clients.records and not source['approvals']
        assert not any(row.get('role') == 'user' for row in source['messages'])
    finally:
        await app.close()


@pytest.mark.parametrize('include_empty', [False, True])
async def test_missing_observation_is_unavailable_and_retained_reads_need_no_new_capture(tmp_path, monkeypatch, include_empty):
    app, runtime, _, sid, config, *_ = await full_fixture(tmp_path, monkeypatch)
    try:
        source = app._session(sid)
        transport = {'_runtimeSessionId': sid, '_generationId': source['collaborationGeneration']['id'],
                     '_inputClients': [], '_responseMode': 'receipt'}
        if include_empty:
            transport['_inputBindings'] = []
        async def invoke(action, args):
            return await app.app_bridge('dispatch', {'action': action, 'args': args, **transport}, sid)
        preview = (await invoke('observation.preview', config))['result']
        payload = {**config, 'previewHash': preview['previewHash'], 'requestId': 'unavailable-inputs'}
        watch = (await invoke('observation.create', payload))['result']['watch']
        assert watch['authorization']['inputIds'] == []
        assert watch['authorization']['inputCoverage'] == 'unavailable'
        await app.dispatch('conversation.stop', {'sessionId': sid})
        await mode(app, sid, 'ask')
        before = copy.deepcopy(app.observations.store.get('watch', watch['id']))
        changes = app.db.total_changes
        monkeypatch.setattr(app.access_mode, 'capture_full', lambda *a: pytest.fail('receipt read captured new authority'))
        monkeypatch.setattr(app.history, 'ensure_loaded', lambda *a: pytest.fail('receipt read loaded history'))
        duplicate = (await invoke('observation.create', payload))['result']
        read = (await invoke('observation.read', {'sessionId': sid, 'id': watch['id']}))['result']
        assert duplicate['duplicate'] and duplicate['watch'] == read['watch'] == before
        assert app.observations.store.get('watch', watch['id']) == before
        assert app.db.total_changes == changes
        assert not app.observations.store.rows('outbox')
        runtime.observation_input.assert_not_awaited()
    finally:
        await app.close()


@pytest.mark.parametrize('domain', ['observation', 'feedback'])
@pytest.mark.parametrize('envelope', ['fresh-system', 'current', 'stale', 'stopped', 'foreign-input'])
async def test_system_full_retains_actual_native_envelope_when_present(tmp_path, monkeypatch, request, domain, envelope):
    app, _, _, sid, _, _, _, descriptor = await full_fixture(tmp_path, monkeypatch)
    try:
        github = request.getfixturevalue('github')
        source = app._session(sid)
        binding = {'_runtimeSessionId': sid, '_generationId': source['collaborationGeneration']['id'],
                   '_inputBindings': [{'inputId': 'trusted-system-input'}]}
        if envelope == 'stale':
            binding['_generationId'] = 'old-generation'
        elif envelope == 'stopped':
            await app.dispatch('conversation.stop', {'sessionId': sid})
        elif envelope == 'foreign-input':
            binding['_inputBindings'] = [{'inputId': 'not-delivered'}]
        native = envelope != 'fresh-system'
        principal_token = PRINCIPAL.set(sid if native else None)
        binding_token = BINDING.set(binding if native else None)
        try:
            action = 'observation.qualify' if domain == 'observation' else 'feedback.submit'
            args = {'sessionId': sid, 'descriptor': descriptor} if domain == 'observation' else submission(includeDiagnostics=False)
            async def invoke():
                return await app.dispatch(action, args, origin='system', caller_session_id=sid, include_state=False)
            if envelope in {'stale', 'stopped', 'foreign-input'}:
                original = copy.deepcopy(app.observations.store.rows('observer'))
                with pytest.raises(AppError, match='current.*generation|Stop|current admitted input'):
                    await invoke()
                assert not app.db.execute('SELECT 1 FROM feedback_requests').fetchone()
                assert app.observations.store.rows('observer') == original
                assert not github[0]
                return
            result = await invoke()
        finally:
            PRINCIPAL.reset(principal_token)
            BINDING.reset(binding_token)
        if domain == 'observation':
            authority = result['result']['observer']['authorization']
        else:
            await settle(app)
            _, payload, receipt = saved(app)
            assert receipt['status'] == 'submitted'
            authority = receipt['authority']
            assert payload['_authority'] == authority
        assert authority['origin'] == 'system' and authority['sessionId'] == sid
        if native:
            assert authority['runtimeSessionId'] == sid
            assert authority['generationId'] == 'system-work'
            assert authority['inputIds'] == ['trusted-system-input']
            assert authority['inputCoverage'] == 'complete'
        else:
            assert not {'runtimeSessionId', 'generationId', 'inputIds'} & authority.keys()
        assert not app.clients.records and not source['approvals']
    finally:
        await app.close()
