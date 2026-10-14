"""The media fixture permits its real display reader, never arbitrary execution."""
import asyncio
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


spec = importlib.util.spec_from_file_location('media_process_guard',
    Path(__file__).parent / 'fixtures' / 'inline_media_process_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def denied(kind):
    raise AssertionError('Unexpected ' + kind)


def command(home):
    spool = home / 'projection-spool-fixture' / ('a' * 32)
    spool.mkdir(parents=True)
    return (sys.executable, '-m', 'amplifier_web.history_projection_worker',
            '--spool', str(spool), '--cache-home', str(home), '--memory-limit', '805306368')


OPTIONS = dict(stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
               stderr=asyncio.subprocess.DEVNULL, limit=4096)


@pytest.mark.parametrize('change', ['module', 'script', 'extra', 'cache', 'spool', 'memory', 'env', 'cwd', 'stdio'])
async def test_rejects_substituted_process_or_boundary(tmp_path, change):
    args, options = list(command(tmp_path)), dict(OPTIONS)
    if change == 'module':
        args[2] = 'amplifier_web.runtime_worker'
    elif change == 'script':
        args[1:3] = ['-c', 'print(1)']
    elif change == 'extra':
        args.append('--arbitrary')
    elif change == 'cache':
        args[6] = str(tmp_path.parent)
    elif change == 'spool':
        args[4] = str(tmp_path)
    elif change == 'memory':
        args[-1] = '9999999999'
    elif change == 'stdio':
        options['stdout'] = None
    else:
        options[change] = {} if change == 'env' else str(tmp_path)
    restore = guard.install(tmp_path, denied, lambda: None)
    try:
        with pytest.raises(AssertionError, match='Unexpected subprocess'):
            await asyncio.create_subprocess_exec(*args, **options)
        with pytest.raises(AssertionError, match='Unexpected subprocess'):
            subprocess.Popen([sys.executable, '-c', 'print(1)'])
        with pytest.raises(AssertionError, match='Unexpected subprocess'):
            await asyncio.create_subprocess_shell('echo forbidden')
    finally:
        restore()


async def test_authorized_spawn_does_not_grant_other_tasks_or_direct_popen(tmp_path, monkeypatch):
    args = command(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def spawn(*actual, **kwargs):
        entered.set()
        await release.wait()
        with pytest.raises(AssertionError, match='Unexpected subprocess'):
            subprocess.Popen([sys.executable, '-c', 'print(1)'])
        subprocess.Popen(actual)
        return 'display-child'

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(subprocess, 'Popen', lambda args: calls.append(args))
    observed = []
    restore = guard.install(tmp_path, denied, lambda: observed.append(True))
    task = asyncio.create_task(asyncio.create_subprocess_exec(*args, **OPTIONS))
    try:
        await entered.wait()
        with pytest.raises(AssertionError, match='Unexpected subprocess'):
            subprocess.Popen(args)
        release.set()
        assert await task == 'display-child'
        assert calls == [args] and observed == [True]
        with pytest.raises(AssertionError, match='Unexpected subprocess'):
            subprocess.Popen(args)
    finally:
        release.set()
        await task
        restore()


@pytest.mark.parametrize('module', ['amplifier_web.history_projection_worker',
                                   'amplifier_web.shell_query_worker'])
async def test_real_owned_projector_starts_and_closes_without_runtime(tmp_path, module):
    from amplifier_web.history_projection_process import ProcessProjector
    observed = []
    restore = guard.install(tmp_path, denied, lambda: observed.append(True))
    owner = ProcessProjector(tmp_path, _command=(sys.executable, '-m', module))
    process = None
    try:
        await owner._home()
        assert await owner._start() is None
        process = owner._process
        assert process.returncode is None and observed == [True]
    finally:
        await owner.close()
        restore()
    assert process.returncode == 0
    assert not owner._root.exists()
