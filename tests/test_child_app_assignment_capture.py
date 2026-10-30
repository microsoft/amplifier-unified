"""First child mount cannot capture authority from newer root work."""
import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.collaboration import BINDING, PRINCIPAL
from test_coordination_generation import app


@pytest.mark.parametrize('advance', ['unchanged', 'input', 'generation', 'stop'])
async def test_first_child_mount_retains_invoking_assignment_through_provider_wait(app, tmp_path, monkeypatch, advance):
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.provider_isolation import ProviderIsolation
    from amplifier_web.runtime_worker import Worker

    spec = importlib.util.spec_from_file_location('assignment_child_fixture',
        Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            'agents': {'worker': {'instruction': 'Inspect only'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths),
        Bundle(name='fixture', session=plan['session'], agents=plan['agents']))
    approvals = Approvals(None, AsyncMock(return_value='deny'))
    parent = await prepared.create_session(session_id='root', approval_system=approvals)
    parent.coordinator.register_capability('session.working_dir', str(tmp_path))
    parent.coordinator.register_capability('web.provider_isolation', ProviderIsolation())

    source = app.state['sessions'][0]
    source['runtimeSessionId'] = 'root'
    source['collaborationGeneration'] = {'id': 'original-generation', 'inputIds': ['original-input'],
                                       'interruptionRevision': 0, 'terminal': False}
    worker = Worker()
    worker.session = parent
    worker.runtime = SimpleNamespace(generation={'id': 'original-generation', 'input_ids': ['original-input']},
                                     closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.context_inputs = ['original-input']
    worker.context_bindings = {'original-input': {'clientId': 'original-browser', 'targets': []}}
    envelopes, actors, outcomes = [], [], []

    async def transport(operation, args):
        envelopes.append(deepcopy(args))
        return await app.app_bridge(operation, args, source['id'])

    async def current_execution(operation, args, sid, **options):
        # Exercise the canonical host consumer after the actual service bridge
        # establishes the child's principal and private transport binding.
        assert sid == source['id'] and operation == 'list_actions'
        actors.append((PRINCIPAL.get(), deepcopy(BINDING.get())))
        app.collaboration.active_binding(source, allow_descendant=True)
        return {'actions': []}

    worker.bridge = transport
    monkeypatch.setattr(app, '_app_bridge', current_execution)
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))
    registry = await install_children(parent, prepared, Runtime('root'), SessionStore(tmp_path / 'children'), approvals)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_provider_preparation(bundle, prepared, *, isolation):
        entered.set()
        await release.wait()

    monkeypatch.setattr('amplifier_web.provider_environment.materialize_bundle_providers', held_provider_preparation)

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        result = await tools['app_control'].execute({'operation': 'list_actions', 'args': {
            '_runtimeSessionId': 'root', '_generationId': 'later-generation'}})
        outcomes.append(result.success)
        assert coordinator.session_id == 'child'
        assert envelopes[-1]['_runtimeSessionId'] == 'child'
        assert envelopes[-1]['_inputBindings'] == [{'inputId': 'original-input', 'clientId': 'original-browser'}]
        if advance in {'unchanged', 'input'}:
            assert envelopes[-1]['_generationId'] == 'original-generation'
            assert result.success
        else:
            assert '_generationId' not in envelopes[-1]
            assert not result.success
            assert coordinator.get_capability('web.work_origin_scope')() is None
        return 'done'

    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', execute)
    spawn = asyncio.create_task(registry.spawn('worker', 'inspect', parent, sub_session_id='child'))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert not any(row['id'] == 'child' for row in fixture.created)
        if advance == 'input':
            worker.runtime.generation['input_ids'].append('later-input')
            worker.context_inputs = ['later-input']
            worker.context_bindings = {'later-input': {'clientId': 'later-browser', 'targets': []}}
            source['collaborationGeneration']['inputIds'].append('later-input')
        elif advance == 'generation':
            worker.runtime.generation = {'id': 'later-generation', 'input_ids': ['later-input']}
            worker.context_inputs = ['later-input']
            worker.context_bindings = {'later-input': {'clientId': 'later-browser', 'targets': []}}
            source['collaborationGeneration'] = {'id': 'later-generation', 'inputIds': ['later-input'],
                                               'interruptionRevision': 0, 'terminal': False}
        elif advance == 'stop':
            # Stop's epoch changes before its native terminal event. Keep the
            # generation visible to prove the installer retained the old Stop.
            worker.stop_revision += 1
            source['interruptionRevision'] = 1
        release.set()
        await asyncio.wait_for(spawn, 30)
        assert outcomes == [advance in {'unchanged', 'input'}]
        assert actors[0][0] == actors[0][1]['_runtimeSessionId'] == 'child'
    finally:
        release.set()
        if not spawn.done():
            spawn.cancel()
        await asyncio.gather(spawn, return_exceptions=True)
        await parent.cleanup()
