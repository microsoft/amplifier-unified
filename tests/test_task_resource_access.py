"""Actual mounted reference validators; no configuration mutation or tool call."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('amplifier_module_tool_filesystem')
pytest.importorskip('amplifier_module_tool_apply_patch')
from test_file_source_guard import mounted
from amplifier_web.resource_access import observe


@pytest.mark.parametrize('engine', ['native', 'function'])
@pytest.mark.parametrize('policy,expected', [({}, 'denied'), ({'allowed_write_paths': ['.']}, 'allowed'), ({'allowed_write_paths': []}, 'denied'),
    ({'allowed_write_paths': ['workspace']}, 'denied'), ({'allowed_write_paths': ['resource']}, 'allowed'),
    ({'denied_write_paths': ['resource']}, 'denied')])
async def test_current_mounted_policy_reports_broad_narrow_and_readonly_without_expansion(tmp_path, engine, policy, expected):
    workspace, resource = tmp_path/'workspace', tmp_path/'resource'
    workspace.mkdir()
    resource.mkdir()
    config = {key:[str(tmp_path/value) for value in values] for key,values in policy.items()}
    coordinator, tools = await mounted(workspace, tmp_path/'locks', engine, **config)
    session = SimpleNamespace(coordinator=coordinator, session_id='actual-root')
    before = dict(tools)
    report = await observe(session, [str(resource)])
    assert report['status'] == 'observed'
    assert report['paths'][str(resource)]['status'] == expected
    assert all(tools[name] is tool for name, tool in before.items())
    assert not list(resource.iterdir())
    # An actual mounted write must agree; inspection itself has not written.
    result = await tools['write_file'].execute({'file_path':str(resource/'probe.txt'), 'content':'probe'})
    assert result.success == (expected == 'allowed')


async def test_worker_policy_observation_does_not_start_parked_execution(tmp_path, monkeypatch):
    from amplifier_web.runtime_worker import Worker
    from amplifier_web import runtime_worker
    coordinator, _ = await mounted(tmp_path, tmp_path/'locks')
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=coordinator, session_id='root')
    worker.execution = None
    worker.acquire_for_mutation = AsyncMock(side_effect=AssertionError('No mount or execution'))
    sent = []
    monkeypatch.setattr(runtime_worker, 'publish', sent.append)
    await worker.command({'op':'dependencies','id':'resource-access','resourceAccess':True,'paths':[str(tmp_path)]})
    assert sent[-1]['result']['paths'][str(tmp_path)]['status'] == 'allowed'
    worker.acquire_for_mutation.assert_not_awaited()


async def test_changed_mount_and_unknown_tools_cannot_claim_access(tmp_path, monkeypatch):
    import amplifier_web.resource_access as access
    coordinator, tools = await mounted(tmp_path, tmp_path/'locks')
    entered, release = asyncio.Event(), asyncio.Event()
    original = asyncio.to_thread
    async def wait(function, *args):
        entered.set()
        await release.wait()
        return await original(function, *args)
    monkeypatch.setattr(access.asyncio, 'to_thread', wait)
    pending = asyncio.create_task(observe(SimpleNamespace(coordinator=coordinator), [str(tmp_path)]))
    await entered.wait()
    tools['write_file'] = object()
    release.set()
    assert (await pending)['status'] == 'unknown'
    monkeypatch.undo()
    custom = {'custom':object()}
    unknown = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name:custom))
    assert (await observe(unknown, [str(tmp_path)]))['paths'][str(tmp_path)]['status'] == 'unknown'


@pytest.mark.parametrize('engine', ['native', 'function'])
async def test_default_workspace_authority_covers_nested_test_files_and_blocks_symlink_escape(tmp_path, engine):
    from amplifier_web.host.session import _apply_host_policy
    workspace, external = tmp_path/'workspace', tmp_path/'external'
    workspace.mkdir(); external.mkdir()
    target = workspace/'.amplifier'/'task-storage'/'tests'
    target.mkdir(parents=True)
    (workspace/'outside').symlink_to(external, target_is_directory=True)
    bundle = SimpleNamespace(tools=[{'module':'tool-filesystem'}], agents={})
    _apply_host_policy(bundle, SimpleNamespace(workspace=workspace, settings={}))
    _, tools = await mounted(workspace, tmp_path/'locks', engine, **bundle.tools[0]['config'])
    assert (await tools['write_file'].execute({'file_path':str(target/'check.txt'), 'content':'first\n'})).success
    from test_file_source_guard import change
    assert (await tools['read_file'].execute({'file_path':str(target/'check.txt')})).success
    name, args = change(engine, str(target/'check.txt'))
    assert (await tools[name].execute(args)).success
    result = await tools['write_file'].execute({'file_path':str(workspace/'outside'/'escaped.txt'), 'content':'blocked'})
    assert not result.success and not (external/'escaped.txt').exists()
