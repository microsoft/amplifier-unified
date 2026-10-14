"""Exercise the actual optional file modules, including cross-process races."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

filesystem = pytest.importorskip('amplifier_module_tool_filesystem')
patch = pytest.importorskip('amplifier_module_tool_apply_patch')

from amplifier_web.file_source_guard import install_file_source_guard, _locked


async def mounted(workspace, locks, engine='native', **config):
    tools, capabilities = {}, {'session.working_dir': str(workspace)}
    async def mount(kind, tool, name):
        tools[name] = tool
    coordinator = SimpleNamespace(mount=mount, get=lambda kind: tools if kind == 'tools' else None,
        get_capability=capabilities.get, register_capability=capabilities.__setitem__,
        hooks=SimpleNamespace(emit=AsyncMock()))
    await filesystem.mount(coordinator, config)
    await patch.mount(coordinator, dict(config, engine=engine))
    await install_file_source_guard(coordinator, locks)
    return coordinator, tools


def change(writer, path='file.txt', old='first', new='second'):
    if writer == 'write':
        return 'write_file', {'file_path': str(path), 'content': new + '\n'}
    if writer == 'edit':
        return 'edit_file', {'file_path': str(path), 'old_string': old, 'new_string': new}
    if writer == 'native':
        return 'apply_patch', {'type': 'update_file', 'path': str(path), 'diff': '@@\n-' + old + '\n+' + new}
    return 'apply_patch', {'patch': f'*** Begin Patch\n*** Update File: {path}\n@@\n-{old}\n+{new}\n*** End Patch'}


async def call(tools, operation):
    name, args = operation
    return await tools[name].execute(args)


@pytest.mark.parametrize('writer', ['write', 'edit', 'native', 'function'])
async def test_read_stale_recovery_and_own_followup(tmp_path, writer):
    path = tmp_path / 'file.txt'
    path.write_bytes(b'first\r\n')
    _, tools = await mounted(tmp_path, tmp_path / 'locks', 'function' if writer == 'function' else 'native')
    result = await call(tools, change(writer))
    assert not result.success and result.error['code'] == 'file_source_unread'
    assert (await tools['read_file'].execute({'file_path': 'file.txt', 'limit': 1})).success
    path.write_bytes(b'first\r\npeer addition\r\n')
    result = await call(tools, change(writer))
    assert not result.success and result.error['code'] == 'file_source_stale'
    assert path.read_bytes() == b'first\r\npeer addition\r\n'
    assert (await tools['read_file'].execute({'file_path': 'file.txt'})).success
    assert (await call(tools, change(writer))).success
    assert (await call(tools, change(writer, old='second', new='third'))).success


@pytest.mark.parametrize('engine', ['native', 'function'])
async def test_delete_requires_current_read_and_create_preserves_existing(tmp_path, engine):
    path = tmp_path / 'file.txt'
    path.write_text('first\n')
    _, tools = await mounted(tmp_path, tmp_path / 'locks', engine)
    delete = ('apply_patch', {'type': 'delete_file', 'path': 'file.txt'} if engine == 'native'
              else {'patch': '*** Begin Patch\n*** Delete File: file.txt\n*** End Patch'})
    assert not (await call(tools, delete)).success
    await tools['read_file'].execute({'file_path': 'file.txt'})
    path.write_text('peer\n')
    assert (await call(tools, delete)).error['code'] == 'file_source_stale'
    await tools['read_file'].execute({'file_path': 'file.txt'})
    assert (await call(tools, delete)).success
    create = ('apply_patch', {'type': 'create_file', 'path': 'file.txt', 'diff': '+new'} if engine == 'native'
              else {'patch': '*** Begin Patch\n*** Add File: file.txt\n+new\n*** End Patch'})
    assert (await call(tools, create)).success
    assert (await call(tools, create)).error['code'] == 'file_source_stale'
    assert path.read_text() == 'new'


async def test_patch_preflights_all_sources_destinations_and_permissions(tmp_path):
    first, second, dest = [tmp_path / name for name in ('first', 'second', 'dest')]
    for path in (first, second, dest):
        path.write_text('first\n')
    _, tools = await mounted(tmp_path, tmp_path / 'locks', 'function', denied_write_paths=[str(dest)])
    for path in (first, second):
        await tools['read_file'].execute({'file_path': str(path)})
    second.write_text('peer\n')
    result = await tools['apply_patch'].execute({'patch':
        '*** Begin Patch\n*** Update File: first\n@@\n-first\n+second\n*** Delete File: second\n*** End Patch'})
    assert result.error['code'] == 'file_source_stale' and result.error['effect'] == 'none'
    assert first.read_text() == 'first\n' and second.read_text() == 'peer\n'
    result = await tools['apply_patch'].execute({'patch':
        '*** Begin Patch\n*** Update File: first\n*** Move to: dest\n@@\n-first\n+second\n*** End Patch'})
    assert not result.success and 'Access denied' in result.error['message']
    assert first.read_text() == dest.read_text() == 'first\n'


async def test_move_destination_no_clobber_and_partial_failure_invalidates(tmp_path):
    for name in ('first', 'second'):
        (tmp_path / name).write_text('first\n')
    _, tools = await mounted(tmp_path, tmp_path / 'locks', 'function')
    for name in ('first', 'second'):
        await tools['read_file'].execute({'file_path': name})
    move = {'patch': '*** Begin Patch\n*** Update File: first\n*** Move to: second\n@@\n-first\n+new\n*** End Patch'}
    assert (await tools['apply_patch'].execute(move)).error['code'] == 'file_source_stale'
    assert (tmp_path / 'first').read_text() == (tmp_path / 'second').read_text() == 'first\n'
    failed = await tools['apply_patch'].execute({'patch':
        '*** Begin Patch\n*** Update File: first\n@@\n-first\n+new\n*** Update File: second\n@@\n-missing\n+new\n*** End Patch'})
    assert not failed.success and failed.error['effect'] == 'unknown'
    assert (tmp_path / 'first').read_text() == 'new\n'
    assert (await call(tools, change('write', 'first'))).error['code'] == 'file_source_unread'
    assert (await call(tools, change('write', 'second'))).error['code'] == 'file_source_unread'
    await tools['read_file'].execute({'file_path': 'first'})
    move['patch'] = move['patch'].replace('Move to: second', 'Move to: new').replace('-first', '-new')
    assert (await tools['apply_patch'].execute(move)).success
    assert not (tmp_path / 'first').exists() and (tmp_path / 'new').read_text() == 'new\n'


async def test_worker_isolation_remount_eviction_and_listing(tmp_path):
    path = tmp_path / 'file.txt'
    path.write_text('first\n')
    root, first = await mounted(tmp_path, tmp_path / 'locks')
    _, child = await mounted(tmp_path, tmp_path / 'locks')
    await first['read_file'].execute({'file_path': 'file.txt'})
    await child['read_file'].execute({'file_path': '.'})
    assert (await call(child, change('write'))).error['code'] == 'file_source_unread'
    guard = root.get_capability('web.file_source_guard')
    for index in range(512):
        guard.remember(tmp_path / str(index), None)
    assert len(guard.observed) == 512
    assert (await call(first, change('write'))).error['code'] == 'file_source_unread'
    old = first['write_file']
    await install_file_source_guard(root, tmp_path / 'locks')
    assert first['write_file'] is old


@pytest.mark.parametrize('writer', ['write', 'edit', 'native', 'function'])
async def test_denials_and_symlink_aliases(tmp_path, writer):
    workspace, outside = tmp_path / 'workspace', tmp_path / 'outside'
    workspace.mkdir(); outside.mkdir()
    (workspace / 'file.txt').write_text('first\n')
    (outside / 'file.txt').write_text('first\n')
    (workspace / 'alias.txt').symlink_to(workspace / 'file.txt')
    (workspace / 'escape').symlink_to(outside, target_is_directory=True)
    _, tools = await mounted(workspace, tmp_path / 'locks', 'function' if writer == 'function' else 'native',
                              allowed_read_paths=[str(workspace)])
    denied = await tools['read_file'].execute({'file_path': 'escape/file.txt'})
    assert not denied.success
    assert not (await call(tools, change(writer, 'escape/file.txt'))).success
    assert (outside / 'file.txt').read_text() == 'first\n'
    await tools['read_file'].execute({'file_path': 'alias.txt'})
    assert (await call(tools, change(writer))).success


async def test_same_worker_pending_writes_do_not_inherit_new_version(tmp_path):
    path = tmp_path / 'file.txt'
    path.write_text('first\n')
    _, tools = await mounted(tmp_path, tmp_path / 'locks')
    await tools['read_file'].execute({'file_path': 'file.txt'})
    async with _locked(tmp_path / 'locks', [path]):
        one = asyncio.create_task(call(tools, change('write', new='one')))
        two = asyncio.create_task(call(tools, change('write', new='two')))
        await asyncio.sleep(.1)
    results = await asyncio.gather(one, two)
    assert sorted(result.success for result in results) == [False, True]
    assert [result.error['code'] for result in results if not result.success] == ['file_source_stale']


async def test_cancelled_mutation_releases_lock_and_invalidates_observation(tmp_path):
    path = tmp_path / 'file.txt'
    path.write_text('first\n')
    root, tools = await mounted(tmp_path, tmp_path / 'locks')
    await tools['read_file'].execute({'file_path': 'file.txt'})
    entered = asyncio.Event()
    original = tools['write_file'].tool.execute
    async def hold(args):
        path.write_text('partial\n')
        entered.set()
        await asyncio.Event().wait()
    tools['write_file'].tool.execute = hold
    task = asyncio.create_task(call(tools, change('write')))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not root.get_capability('web.file_source_guard').observed
    tools['write_file'].tool.execute = original
    assert (await tools['read_file'].execute({'file_path': 'file.txt'})).success
    assert (await call(tools, change('write'))).success


@pytest.mark.parametrize('writers', [('write', 'write'), ('write', 'native'), ('edit', 'function')])
@pytest.mark.parametrize('alias', ['same', 'hardlink'])
def test_separate_process_race(tmp_path, writers, alias):
    path = tmp_path / 'file.txt'
    path.write_text('first\n')
    other = tmp_path / 'other.txt'
    if alias == 'hardlink':
        os.link(path, other)
    else:
        other = path
    script = Path(__file__).parent / 'fixtures/file_source_process_probe.py'
    children = [subprocess.Popen([sys.executable, str(script), str(tmp_path), writer, str(index), str(target)],
                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for index, (writer, target) in enumerate(zip(writers, [path, other]))]
    import time
    try:
        deadline = time.monotonic() + 15
        while not all((tmp_path / f'ready-{i}').exists() for i in range(2)):
            assert time.monotonic() < deadline, 'Workers failed to reach read barrier'
            time.sleep(.02)
        (tmp_path / 'go').touch()
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=15)
            assert child.returncode == 0, stdout + stderr
            results.append(json.loads(stdout))
        assert sorted(result['success'] for result in results) == [False, True], results
        assert [r['error']['code'] for r in results if not r['success']] == ['file_source_stale']
        assert path.read_text() in ('worker-0\n', 'worker-1\n')
    finally:
        for child in children:
            if child.poll() is None:
                child.kill(); child.wait()


@pytest.mark.parametrize('alias', ['same', 'case', 'unicode'])
def test_concurrent_creation_does_not_clobber_aliases(tmp_path, alias):
    names = {'same': ('file.txt', 'file.txt'), 'case': ('File.txt', 'file.txt'),
             'unicode': ('caf\u00e9.txt', 'cafe\u0301.txt')}[alias]
    paths = [tmp_path / name for name in names]
    paths[0].touch()
    aliases_same_file = paths[1].exists()
    paths[0].unlink()
    if not aliases_same_file:
        pytest.skip('This filesystem treats these names as distinct files')
    script = Path(__file__).parent / 'fixtures/file_source_process_probe.py'
    children = [subprocess.Popen([sys.executable, str(script), str(tmp_path), 'write', str(index), str(path), 'create'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for index, path in enumerate(paths)]
    import time
    try:
        deadline = time.monotonic() + 15
        while not all((tmp_path / f'ready-{i}').exists() for i in range(2)):
            assert time.monotonic() < deadline
            time.sleep(.02)
        (tmp_path / 'go').touch()
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=15)
            assert child.returncode == 0, stdout + stderr
            results.append(json.loads(stdout))
        assert sorted(result['success'] for result in results) == [False, True], results
        assert [r['error']['code'] for r in results if not r['success']] == ['file_source_stale']
    finally:
        for child in children:
            if child.poll() is None:
                child.kill(); child.wait()
