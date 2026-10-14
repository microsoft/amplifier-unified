"""Actual Core/Foundation child lifecycle installs isolated source observations."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('amplifier_module_loop_live')
pytest.importorskip('amplifier_module_context_simple')
pytest.importorskip('amplifier_module_tool_filesystem')
pytest.importorskip('amplifier_module_tool_apply_patch')


@pytest.mark.parametrize('mode', ['finite', 'resumed', 'persistent', 'nested'])
async def test_native_child_file_reads_are_not_inherited(tmp_path, monkeypatch, mode):
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.host.children import install_children, _PERSISTENT
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.approvals import Approvals

    spec = importlib.util.spec_from_file_location('file_child_fixture',
        Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
        'tools': [{'module': 'tool-filesystem', 'config': {'working_dir': str(tmp_path)}},
                  {'module': 'tool-apply-patch', 'config': {'working_dir': str(tmp_path), 'engine': 'native'}}],
        'agents': {'worker': {'instruction': 'Inspect files'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple', 'tool-filesystem', 'tool-apply-patch')}
    prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths),
        Bundle(name='fixture', session=plan['session'], tools=plan['tools'], agents=plan['agents']))
    approvals = Approvals(None, AsyncMock(return_value='deny'))
    parent = await prepared.create_session(session_id='root', approval_system=approvals)
    parent.coordinator.register_capability('session.working_dir', str(tmp_path))
    worker = Worker()
    worker.session = parent
    worker.runtime = SimpleNamespace(generation={'id': 'root-generation'})
    worker.bridge = AsyncMock(return_value={})
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))
    path = tmp_path / 'file.txt'
    path.write_text('original\n')
    await parent.coordinator.get('tools')['read_file'].execute({'file_path': 'file.txt'})
    registry = await install_children(parent, prepared, Runtime('root'), SessionStore(tmp_path / 'children'), approvals)
    guards = []
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        guards.append(coordinator.get_capability('web.file_source_guard'))
        assert guards[-1] is not parent.coordinator.get_capability('web.file_source_guard')
        update = {'type': 'update_file', 'path': 'file.txt', 'diff': '@@\n-original\n+child'}
        result = await tools['apply_patch'].execute(update)
        assert not result.success and result.error['code'] == 'file_source_unread'
        assert (await tools['read_file'].execute({'file_path': 'file.txt'})).success
        assert (await tools['apply_patch'].execute(update)).success
        # Restore bytes externally only for the next independently mounted child.
        path.write_text('original\n')
        if mode == 'nested' and coordinator.session_id == 'child':
            await registry.spawn('worker', 'nested', registry.sessions['child'], sub_session_id='grandchild')
        return 'done'
    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', execute)
    token = _PERSISTENT.set(mode == 'persistent')
    try:
        await registry.spawn('worker', 'inspect', parent, sub_session_id='child')
        if mode == 'resumed':
            await registry.resume('child', 'continue', parent)
        assert len({id(guard) for guard in guards}) == (2 if mode in {'resumed', 'nested'} else 1)
    finally:
        _PERSISTENT.reset(token)
        await parent.cleanup()
