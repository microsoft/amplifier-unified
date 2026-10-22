"""Mounted Work/file guards remain active during passive tool inspection."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.app_guidance import install_app_access
from amplifier_web.patch_placement import engine_observation
from amplifier_web.resource_access import observe
from test_file_source_guard import change, mounted


async def composed(directory, engine='native'):
    directory.mkdir(parents=True, exist_ok=True)
    coordinator, tools = await mounted(directory, directory/'locks', engine,
        allowed_write_paths=[str(directory)], denied_write_paths=[str(directory/'denied')])
    coordinator.session_id = 'observation-root'
    coordinator.config = {'tools': [{'module': 'tool-apply-patch', 'config': {'engine': engine}}]}
    coordinator.hooks.register = lambda *args, **kwargs: None
    check = AsyncMock(return_value={'allowed': False})
    coordinator.register_capability('web.work_effect_check', check)
    await install_app_access(coordinator, AsyncMock(return_value={}))
    return coordinator, tools, check


@pytest.mark.parametrize('engine', ['native', 'function'])
async def test_inspection_keeps_mounted_identity_and_both_execution_guards(tmp_path, engine):
    coordinator, tools, check = await composed(tmp_path, engine)
    outer = tools['apply_patch']
    before = dict(tools)
    inspection = engine_observation(SimpleNamespace(coordinator=coordinator), 'tool-apply-patch')
    assert inspection['status'] == 'observed' and inspection['guarded']
    assert inspection['workingDirectory'] == str(tmp_path)
    assert inspection['objectId'].split(':')[1] == str(id(outer))
    result = await observe(SimpleNamespace(coordinator=coordinator), [str(tmp_path), str(tmp_path/'denied')])
    assert result['status'] == 'observed'
    assert result['paths'][str(tmp_path)]['status'] == 'allowed'
    assert result['paths'][str(tmp_path/'denied')]['status'] == 'denied'
    assert all(tools[name] is tool for name, tool in before.items())
    check.assert_not_awaited()
    target = tmp_path/'file.txt'
    target.write_text('first\n')
    name, args = change(engine)
    refused = await tools[name].execute(args)
    assert refused.error['code'] == 'work_precondition_unavailable'
    check.return_value = {'allowed': True}
    refused = await tools[name].execute(args)
    assert refused.error['code'] == 'file_source_unread'
    assert target.read_text() == 'first\n'


@pytest.mark.parametrize('fault', ['original', 'delivery', 'checker', 'name', 'lookalike', 'foreign-work', 'foreign-file', 'engine'])
async def test_foreign_or_changed_wrappers_never_certify_mounted_policy(tmp_path, fault):
    coordinator, tools, _ = await composed(tmp_path/'own')
    outer = tools['apply_patch']
    tools.clear()
    tools['apply_patch'] = outer
    if fault == 'original':
        outer.original = object()
    elif fault == 'delivery':
        outer._work_delivery_guard = object()
    elif fault == 'checker':
        coordinator.register_capability('web.work_effect_check', AsyncMock())
    elif fault == 'name':
        outer.mounted_name = 'write_file'
    elif fault == 'lookalike':
        tools['apply_patch'] = SimpleNamespace(original=outer.original, tool=outer.original.tool,
            _work_delivery_guard=outer._work_delivery_guard, _work_check=outer._work_check)
    elif fault in {'foreign-work', 'foreign-file'}:
        _, foreign, _ = await composed(tmp_path/'foreign')
        tools['apply_patch'] = (foreign['apply_patch'] if fault == 'foreign-work'
                                else foreign['apply_patch'].original)
    else:
        outer.original.tool._engine = object()
    with pytest.raises(ValueError, match='cannot be verified|engines differ'):
        engine_observation(SimpleNamespace(coordinator=coordinator), 'tool-apply-patch')
    report = await observe(SimpleNamespace(coordinator=coordinator), [str(tmp_path/'own')])
    assert report['status'] == 'unknown' or report['paths'][str(tmp_path/'own')]['status'] == 'unknown'


@pytest.mark.parametrize('mutation', ['mount', 'original', 'delivery', 'checker', 'name', 'file-guard'])
async def test_stale_mount_and_replacement_during_observation_remain_unknown(tmp_path, monkeypatch, mutation):
    import amplifier_web.resource_access as access
    coordinator, tools, check = await composed(tmp_path)
    outer = tools['apply_patch']
    inspect = coordinator.get_capability('web.work_inspect_tool')
    tools['apply_patch'] = object()
    with pytest.raises(ValueError, match='changed during inspection'):
        inspect('apply_patch', outer)
    tools['apply_patch'] = outer
    entered, release = asyncio.Event(), asyncio.Event()
    original = asyncio.to_thread
    async def wait(function, *args):
        entered.set()
        await release.wait()
        return await original(function, *args)
    monkeypatch.setattr(access.asyncio, 'to_thread', wait)
    task = asyncio.create_task(observe(SimpleNamespace(coordinator=coordinator), [str(tmp_path)]))
    await entered.wait()
    if mutation == 'mount':
        tools['apply_patch'] = object()
    elif mutation == 'original':
        outer.original = object()
    elif mutation == 'delivery':
        outer._work_delivery_guard = object()
    elif mutation == 'checker':
        coordinator.register_capability('web.work_effect_check', AsyncMock())
    elif mutation == 'name':
        outer.mounted_name = 'write_file'
    else:
        outer.original.guard = SimpleNamespace(coordinator=coordinator)
    release.set()
    assert (await task) == {'status': 'unknown', 'paths': {}, 'runtimeSessionId': None}
    check.assert_not_awaited()


async def test_wrapper_moved_to_another_mount_name_is_not_its_issued_binding(tmp_path):
    coordinator, tools, _ = await composed(tmp_path)
    outer = tools['apply_patch']
    tools['different_name'] = outer
    inspect = coordinator.get_capability('web.work_inspect_tool')
    with pytest.raises(ValueError, match='cannot be verified'):
        inspect('different_name', outer)
    assert inspect('apply_patch', outer) is outer.original
