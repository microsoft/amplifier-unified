"""Exercise the bundled writers, including the native patch path used on Spark."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.host.session import _apply_host_policy


@pytest.mark.parametrize('writer', ['filesystem', 'native', 'function'])
@pytest.mark.parametrize('child', [False, True])
async def test_project_writes_survive_global_extras_with_denials_enforced(tmp_path, monkeypatch, writer, child):
    filesystem = pytest.importorskip('amplifier_module_tool_filesystem')
    patch = pytest.importorskip('amplifier_module_tool_apply_patch')
    workspace = tmp_path / 'project'
    extra = tmp_path / 'notes'
    outside = tmp_path / 'outside'
    for path in (workspace, extra, outside):
        path.mkdir()
    # A web service's process cwd is unrelated to the selected conversation.
    monkeypatch.chdir(outside)
    (workspace / 'escape').symlink_to(outside, target_is_directory=True)
    declaration = {'module': 'tool-filesystem' if writer == 'filesystem' else 'tool-apply-patch',
                   'config': {'denied_write_paths': ['private'],
                              **({} if writer == 'filesystem' else {'engine': writer})}}
    bundle = SimpleNamespace(tools=[] if child else [declaration],
        agents={'worker': {'tools': [declaration]}} if child else {})
    settings = {'modules': {'tools': [{'module': 'tool-filesystem', 'config': {
        'allowed_write_paths': [str(extra)], 'denied_write_paths': ['shared-private']}}]}}
    _apply_host_policy(bundle, SimpleNamespace(workspace=workspace, settings=settings))
    tools, capabilities = {}, {'session.working_dir': str(workspace)}

    async def mount(kind, tool, name):
        tools[name] = tool

    coordinator = SimpleNamespace(mount=mount, get_capability=capabilities.get,
        register_capability=capabilities.__setitem__, hooks=SimpleNamespace(emit=AsyncMock()))
    await (filesystem.mount if writer == 'filesystem' else patch.mount)(coordinator, declaration['config'])

    async def write(path):
        if writer == 'filesystem':
            return await tools['write_file'].execute({'file_path': str(path), 'content': 'fixture\n'})
        if writer == 'native':
            return await tools['apply_patch'].execute({'type': 'create_file', 'path': str(path), 'diff': '+fixture'})
        return await tools['apply_patch'].execute({'patch': f'*** Begin Patch\n*** Add File: {path}\n+fixture\n*** End Patch'})

    for target in ('checkout/frontend/example.txt', extra / 'example.txt'):
        result = await write(target)
        assert result.success, result.error
    assert (workspace / 'checkout/frontend/example.txt').read_text().rstrip('\n') == 'fixture'
    for target in ('private/denied.txt', 'shared-private/denied.txt', 'escape/denied.txt', outside / 'denied.txt'):
        result = await write(target)
        assert not result.success
        assert 'Access denied' in str(result.error)
    assert not (workspace / 'private/denied.txt').exists()
    assert not (workspace / 'shared-private/denied.txt').exists()
    assert not (outside / 'denied.txt').exists()
