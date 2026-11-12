"""Finite POSIX Git custody: real separate-session writer, no provider/network."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess

import pytest

from amplifier_web import probe_git

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux process identity observation')


def identity(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()
        return {'state': stat[0], 'group': int(stat[2]), 'session': int(stat[3]), 'start': stat[19]}
    except FileNotFoundError:
        return None


async def marker(path):
    async with asyncio.timeout(10):
        while not path.exists():
            await asyncio.sleep(.01)
    return json.loads(path.read_text())


async def request(owner, args, *, seconds=5, env=None):
    reader, writer = await asyncio.open_unix_connection(owner.path, limit=32 * 1024 * 1024 + 4096)
    writer.write(json.dumps({'args': args, 'cwd': None, 'timeout': seconds,
                             'env': dict(os.environ) if env is None else env}).encode() + b'\n')
    await writer.drain()
    try:
        return json.loads(await reader.readline())
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize('code', [0, 17])
async def test_git_result_preserves_code_streams_environment_and_group_anchor(tmp_path, code):
    owner = await probe_git.GitProbeOwner().start()
    try:
        result = await request(owner, [sys.executable, '-c',
            f'import os,sys; print(os.environ["EXACT"]); print("err", file=sys.stderr); sys.exit({code})'],
            env={**os.environ, 'EXACT': '試験'})
        assert result == {'kind': 'result', 'code': code, 'stdout': '試験\n', 'stderr': 'err\n', 'settled': True}
        assert not owner.processes and not owner.errors
    finally:
        await owner.close()
    assert owner.cleanup_complete and not Path(owner.path).exists()


@pytest.mark.parametrize('mode', ['close', 'timeout'])
async def test_git_writer_settled_before_close_returns_without_collateral_kill(tmp_path, mode):
    owner = await probe_git.GitProbeOwner().start()
    ready = tmp_path / 'ready'
    gate = tmp_path / 'gate'
    late = tmp_path / 'late'
    sentinel = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(60)',
                                                   start_new_session=True)
    code = f'''import os,json,pathlib,time
pathlib.Path({str(ready)!r}).write_text(json.dumps({{'pid':os.getpid(),'guard':os.getppid(),'group':os.getpgrp(),'session':os.getsid(0)}}))
while not pathlib.Path({str(gate)!r}).exists(): time.sleep(.01)
pathlib.Path({str(late)!r}).write_text('late writer survived')
'''
    client = asyncio.create_task(request(owner, [sys.executable, '-c', code], seconds=.3 if mode == 'timeout' else 10))
    try:
        recorded = await marker(ready)
        assert recorded['session'] == recorded['guard']
        assert recorded['group'] != recorded['guard']
        assert recorded['session'] != os.getsid(0)
        if mode == 'timeout':
            assert await client == {'kind': 'timeout', 'settled': True}
        await owner.close()
        # No post-cleanup grace sleep: settlement is required at this boundary.
        assert identity(recorded['guard']) is None, 'guard was not reaped'
        assert identity(recorded['pid']) is None, 'Git was not reaped'
        assert identity(recorded['group']) is None, 'group anchor was not reaped'
        assert sentinel.returncode is None
        gate.touch()
        assert not late.exists()
    finally:
        await owner.close()
        await asyncio.gather(client, return_exceptions=True)
        sentinel.kill()
        await sentinel.wait()


async def test_git_startup_failure_keeps_oserror_result(tmp_path):
    owner = await probe_git.GitProbeOwner().start()
    try:
        result = await request(owner, [str(tmp_path / 'absent')])
        assert result['kind'] == 'spawn_error'
        assert not owner.processes and not owner.errors
    finally:
        await owner.close()


async def test_repeated_cancellation_waits_for_git_owner_finalizer(monkeypatch):
    owner = await probe_git.GitProbeOwner().start()
    entered, release = asyncio.Event(), asyncio.Event()
    original = owner._cleanup
    async def gated(process, response):
        entered.set()
        await release.wait()
        return await original(process, response)
    monkeypatch.setattr(owner, '_cleanup', gated)
    client = asyncio.create_task(request(owner, [sys.executable, '-c', 'print("done")']))
    await entered.wait()
    task = asyncio.create_task(owner.close())
    try:
        await asyncio.sleep(0)  # Enter close's shield before injecting cancellation.
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and owner.processes
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert owner.cleanup_complete and not owner.processes
    finally:
        release.set()
        await asyncio.gather(task, client, return_exceptions=True)
        await owner.close()


async def test_git_cleanup_failure_retains_owner_and_original_error(monkeypatch):
    owner = await probe_git.GitProbeOwner().start()
    failure = OSError('synthetic cleanup refusal')
    original = owner._cleanup
    processes = []
    async def fail(process, response):
        processes.append((process, response))
        owner.errors.append(failure)
        raise probe_git.GitProbeCleanupError(owner) from failure
    monkeypatch.setattr(owner, '_cleanup', fail)
    try:
        client = asyncio.create_task(request(owner, [sys.executable, '-c', 'print("ok")']))
        async with asyncio.timeout(3):
            while not owner.retained_connections:
                await asyncio.sleep(.01)
        assert not client.done(), 'Unconfirmed custody must not release stage cleanup'
        with pytest.raises(probe_git.GitProbeCleanupError) as error:
            await asyncio.wait_for(owner.close(), 12)
        assert error.value.owner is owner
        assert failure in owner.errors and not owner.cleanup_complete
        assert Path(owner.directory.name).exists() and owner.processes
    finally:
        monkeypatch.setattr(owner, '_cleanup', original)
        for process, response in processes:
            await asyncio.wait_for(original(process, response), 6)
        for writer in owner.retained_connections:
            writer.close()
        await asyncio.wait_for(asyncio.gather(client, return_exceptions=True), 3)
        owner.directory.cleanup()
        probe_git._failed_owners.discard(owner)


@pytest.mark.parametrize('cancellations', [1, 3])
@pytest.mark.parametrize('end', ['cancel', 'timeout', 'normal', 'startup_error'])
async def test_defaults_acquisition_owns_real_foundation_git_before_home_removal(tmp_path, monkeypatch, cancellations, end):
    """Actual defaults entrypoint -> load_root_bundle -> Foundation Git acquisition."""
    from amplifier_web import draft_defaults
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.host import config
    from amplifier_web.bundles import SNAPSHOT_VERSION
    home = tmp_path / 'app'
    home.mkdir()
    ready, origin, gate, late = (tmp_path / name for name in ('ready', 'origin', 'gate', 'late'))
    binary = tmp_path / 'bin'
    binary.mkdir()
    git = binary / 'git'
    repository = tmp_path / 'repository'
    repository.mkdir()
    (repository / 'bundle.md').write_text(
        f'---\nbundle:\n  name: synthetic\n  version: {SNAPSHOT_VERSION}\n'
        'session:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n---\n')
    real_git = shutil.which('git')
    assert real_git
    subprocess.run([real_git, 'init', '-q', str(repository)], check=True)
    subprocess.run([real_git, '-C', str(repository), 'add', 'bundle.md'], check=True)
    subprocess.run([real_git, '-C', str(repository), '-c', 'user.name=Synthetic',
                    '-c', 'user.email=synthetic@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    helper = f"""import pathlib,time
while not pathlib.Path({str(gate)!r}).exists(): time.sleep(.01)
pathlib.Path({str(late)!r}).write_text('helper survived cleanup')
"""
    git.write_text(f'''#!{sys.executable}
import json,os,pathlib,subprocess,sys,time
# Git invocation is not mocked: Foundation resolves a real git+file URI and
# launches this executable through its production runner.
child = None if {end!r} == 'normal' else subprocess.Popen([sys.executable, '-c', {helper!r}])
pathlib.Path({str(ready)!r}).write_text(json.dumps({{'pid':os.getpid(),'helper':None if child is None else child.pid,'guard':os.getppid(),'group':os.getpgrp(),'session':os.getsid(0),'args':sys.argv[1:]}}))
while not pathlib.Path({str(gate)!r}).exists(): time.sleep(.01)
if {end!r} != 'normal': pathlib.Path({str(late)!r}).write_text('survived cleanup')
os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])
''')
    git.chmod(0o700)
    wrapper = f'''import hashlib,json,os,pathlib,runpy,sys
import amplifier_foundation.sources.git as git
pathlib.Path({str(origin)!r}).write_text(json.dumps({{'pid':os.getpid(),'group':os.getpgrp(),'session':os.getsid(0),'foundationGit':git.__file__,'sha256':hashlib.sha256(pathlib.Path(git.__file__).read_bytes()).hexdigest()}}))
sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parent))
runpy.run_path(sys.argv[1],run_name='__main__')
'''
    monkeypatch.setattr(RuntimeManager, '_command', lambda *a, **k: [sys.executable, '-c', wrapper, 'unused'])
    monkeypatch.setattr(config, 'worker_environment', lambda: {
        **os.environ, 'PATH': str(binary),
        'AMPLIFIER_HOME': str(tmp_path / 'community'), 'AMPLIFIER_SOURCE_STORE': '',
        'HOME': str(tmp_path), 'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull, 'PYTHONDONTWRITEBYTECODE': '1',
        'AMPLIFIER_GIT_CLONE_TIMEOUT_S': '.5' if end == 'timeout' else '30'})
    if end == 'startup_error':
        # Missing Git executable is exercised after the real entrypoint records
        # its actual Foundation origin; no runtime/provider shim.
        git.unlink()
    task = asyncio.create_task(draft_defaults.resolve_defaults(home, str(tmp_path),
                             bundle='git+' + repository.as_uri()))
    recorded = None
    try:
        loaded = await marker(origin)
        if end == 'startup_error':
            with pytest.raises(ValueError, match='Draft default resolution failed'):
                await asyncio.wait_for(task, 10)
            return
        recorded = await marker(ready)
        assert 'clone' in recorded['args']
        assert recorded['session'] != loaded['session']
        assert recorded['session'] == recorded['guard']
        assert recorded['group'] != recorded['guard']
        if end == 'cancel':
            for _ in range(cancellations):
                task.cancel()
                await asyncio.sleep(0)
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 6)
        elif end == 'timeout':
            with pytest.raises(ValueError, match='Draft default resolution failed'):
                await asyncio.wait_for(task, 6)
        else:
            gate.touch()
            result = await asyncio.wait_for(task, 10)
            assert result['providers'] == []
        assert identity(recorded['guard']) is None
        assert identity(recorded['pid']) is None
        assert identity(recorded['group']) is None
        if recorded['helper'] is not None:
            assert identity(recorded['helper']) is None, 'Git helper was not reaped'
        shutil.rmtree(home)  # No sleep/retry/ignore_errors; cleanup must be safe now.
        gate.touch()
        assert not late.exists()
        assert loaded['foundationGit'] and len(loaded['sha256']) == 64
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # Fixture cleanup is the same owner path, never a guessed/stale PID kill.
