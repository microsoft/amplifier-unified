"""Real Core/Foundation mounting must not lend a child its root's identity."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('amplifier_module_loop_live')


@pytest.mark.parametrize('mode', ['finite', 'resumed', 'persistent', 'nested'])
async def test_mounted_child_app_access_has_own_principal(tmp_path, monkeypatch, mode):
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.host.children import install_children, _PERSISTENT
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.approvals import Approvals

    spec = importlib.util.spec_from_file_location('authority_child_fixture',
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
    worker = Worker()
    worker.session = parent
    worker.runtime = SimpleNamespace(generation={'id': 'root-generation'})
    worker.context_inputs = ['original-input']
    worker.context_bindings = {'original-input': {'clientId': 'original-client', 'targets': []}}
    worker.bridge = AsyncMock(return_value={'accepted': True})
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))
    registry = await install_children(parent, prepared, Runtime('root'), SessionStore(tmp_path / 'children'), approvals)
    tools_seen = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        control = tools['app_control']
        tools_seen.append(control)
        assert control is not parent.coordinator.get('tools')['app_control']
        result = await control.execute({'operation': 'dispatch', 'args': {
            'action': 'coordination.create', 'id': 'request',
            '_runtimeSessionId': 'root', '_generationId': 'root-generation',
            'args': {'title': 'Peer', 'text': 'Inspect'}}})
        assert result.success, result.error
        _, envelope = worker.bridge.call_args.args
        assert envelope['_runtimeSessionId'] == coordinator.session_id
        assert '_generationId' not in envelope
        assert envelope['_inputBindings'] == [{'inputId': 'original-input', 'clientId': 'original-client'}]
        if mode == 'nested' and coordinator.session_id == 'child':
            worker.context_inputs = ['unrelated-later-input']
            worker.context_bindings = {'unrelated-later-input': {'clientId': 'other-client', 'targets': []}}
            await registry.spawn('worker', 'nested', registry.sessions['child'], sub_session_id='grandchild')
        return 'done'

    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', execute)
    token = _PERSISTENT.set(mode == 'persistent')
    try:
        await registry.spawn('worker', 'inspect', parent, sub_session_id='child')
        if mode == 'resumed':
            await registry.resume('child', 'continue', parent)
        if mode in {'resumed', 'nested'}:
            assert len(tools_seen) == 2 and tools_seen[0] is not tools_seen[1]
        await parent.coordinator.get('tools')['app_control'].execute({'operation': 'list_actions'})
        assert worker.bridge.call_args.args[1]['_runtimeSessionId'] == 'root'
        assert worker.bridge.call_args.args[1]['_generationId'] == 'root-generation'
    finally:
        _PERSISTENT.reset(token)
        await parent.cleanup()
