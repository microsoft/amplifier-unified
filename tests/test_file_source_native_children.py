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

from native_tool_helpers import execute_tool
from test_collaborative_workspaces import app


async def install_host_access(parent, app):
    """Keep the real worker and host Work checks around this file-policy probe."""
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.runtime_worker import Worker
    sid = parent.coordinator.session_id
    generation = app._session(sid)['collaborationGeneration']
    worker = Worker()
    worker.session = parent
    worker.runtime = SimpleNamespace(
        generation={'id': generation['id'], 'input_ids': list(generation['inputIds'])},
        closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    async def bridge(operation, arguments):
        assert operation == 'work.effect.check'
        result = await app.app_bridge(operation, arguments, sid)
        assert result['applicable'] is False and result['allowed'] is True
        assert result['effectAuthorized'] is False  # File policy remains authoritative.
        return result
    worker.bridge = bridge
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))


@pytest.mark.parametrize('mode', ['finite', 'resumed', 'persistent', 'nested'])
async def test_native_child_file_reads_are_not_inherited(tmp_path, monkeypatch, app, mode):
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
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
    parent = await prepared.create_session(session_id=app.state['sessions'][0]['id'], approval_system=approvals)
    parent.coordinator.register_capability('session.working_dir', str(tmp_path))
    await install_host_access(parent, app)
    path = tmp_path / 'file.txt'
    path.write_text('original\n')
    assert (await execute_tool(parent.coordinator, 'read_file', {'file_path': 'file.txt'})).success
    registry = await install_children(parent, prepared, Runtime(parent.coordinator.session_id), SessionStore(tmp_path / 'children'), approvals)
    guards = []
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        guards.append(coordinator.get_capability('web.file_source_guard'))
        assert guards[-1] is not parent.coordinator.get_capability('web.file_source_guard')
        update = {'type': 'update_file', 'path': 'file.txt', 'diff': '@@\n-original\n+child'}
        result = await execute_tool(coordinator, 'apply_patch', update)
        assert not result.success and result.error['code'] == 'file_source_unread'
        assert (await execute_tool(coordinator, 'read_file', {'file_path': 'file.txt'})).success
        assert (await execute_tool(coordinator, 'apply_patch', update)).success
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


async def test_resumed_native_child_retains_saved_write_ceiling_after_parent_grant(tmp_path, monkeypatch, app):
    """A new root grant permits a fresh child, not escalation of a saved child.

    Uses actual Core/Foundation initialization, checkpoint and resume, with a
    deterministic orchestrator invoking the actual filesystem writer. This is
    not the outer RuntimeManager or a Stories import/review/export acceptance.
    """
    import copy
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.approvals import Approvals

    async def deny_network(*args, **kwargs):
        raise AssertionError('Native child policy acceptance must not call a provider')
    monkeypatch.setattr('httpx.AsyncClient.send', deny_network)
    spec = importlib.util.spec_from_file_location('grant_child_fixture',
        Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    workspace = tmp_path / 'workspace'
    approved = tmp_path / 'approved'
    sibling = tmp_path / 'sibling'
    for folder in (workspace, approved, sibling):
        folder.mkdir()
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple', 'tool-filesystem')}
    store = SessionStore(tmp_path / 'saved-children')
    approvals = Approvals(None, AsyncMock(return_value='deny'))
    observations = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        target = approved / (prompt + '.txt')
        result = await execute_tool(coordinator, 'write_file', {'file_path': str(target), 'content': prompt})
        sibling_result = await execute_tool(coordinator, 'write_file', {
            'file_path': str(sibling / (prompt + '.txt')), 'content': 'must not exist'})
        observations.append((prompt, bool(result.success), target.exists(), bool(sibling_result.success)))
        if not result.success:
            assert result.error['code'] == 'file_source_access'
            assert result.error['effect'] == 'none'
            assert ('ask the parent to start a new worker' in result.error['message']) == (prompt == 'resumed-denied')

        assert not sibling_result.success and not (sibling / (prompt + '.txt')).exists()
        assert sibling_result.error['effect'] == 'none'
        assert ('ask the parent to start a new worker' in sibling_result.error['message']) == prompt.startswith('resumed-')
        await context.add_message({'role': 'user', 'content': prompt})
        await context.add_message({'role': 'assistant', 'content': 'allowed' if result.success else 'denied'})
        return 'allowed' if result.success else 'denied'
    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', execute)

    async def root(allowed):
        plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
                'tools': [{'module': 'tool-filesystem', 'config': {
                    'working_dir': str(workspace), 'allowed_write_paths': allowed}}],
                'agents': {'worker': {'instruction': 'Write only within the mounted permission'}}}
        prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths),
            Bundle(name='fixture', session=plan['session'], tools=plan['tools'], agents=plan['agents']))
        parent = await prepared.create_session(session_id=app.state['sessions'][0]['id'], approval_system=approvals)
        parent.coordinator.register_capability('session.working_dir', str(workspace))
        # Production access installation propagates the file guard to both
        # newly created and resumed native child coordinators.
        await install_host_access(parent, app)
        registry = await install_children(parent, prepared, Runtime(parent.coordinator.session_id), store, approvals)
        return parent, registry

    parent, registry = await root([str(workspace)])
    try:
        await registry.spawn('worker', 'initial-denied', parent, sub_session_id='saved-child')
        saved_messages, saved_metadata = copy.deepcopy(store.load('saved-child'))
    finally:
        await parent.cleanup()
    assert observations == [('initial-denied', False, False, False)]

    # Model the supported safe boundary: retire the old parent and initialize a
    # new real native mount with the newly saved exact-directory policy.
    parent, registry = await root([str(workspace), str(approved)])
    try:
        resumed = await registry.resume('saved-child', 'resumed-denied', parent)
        assert resumed['output'] == 'denied'
        messages, metadata = store.load('saved-child')
        assert messages[:len(saved_messages)] == saved_messages
        assert metadata['mount_plan']['tools'] == saved_metadata['mount_plan']['tools']
        fresh = await registry.spawn('worker', 'fresh-allowed', parent, sub_session_id='fresh-child')
        assert fresh['output'] == 'allowed'
        assert (approved / 'fresh-allowed.txt').read_text() == 'fresh-allowed'
        fresh_resumed = await registry.resume('fresh-child', 'resumed-allowed', parent)
        assert fresh_resumed['output'] == 'allowed'
        assert (approved / 'resumed-allowed.txt').read_text() == 'resumed-allowed'
        assert all(row['status'] == 'completed' for row in registry.snapshot())
        assert [row['resumed'] for row in fixture.created if row['id'] == 'saved-child'] == [False, True]
        assert [row['resumed'] for row in fixture.created if row['id'] == 'fresh-child'] == [False, True]
    finally:
        await parent.cleanup()
    assert observations == [
        ('initial-denied', False, False, False),
        ('resumed-denied', False, False, False),
        ('fresh-allowed', True, True, False),
        ('resumed-allowed', True, True, False),
    ]
