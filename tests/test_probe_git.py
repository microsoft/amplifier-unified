"""Finite POSIX Git custody: real separate-session writer, no provider/network."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import hashlib
import signal
import tempfile
from types import SimpleNamespace

import pytest

from amplifier_web import probe_git

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux process identity observation')


@pytest.fixture
def executable_directory():
    # Managed runner's /tmp is intentionally noexec; executable stand-ins belong
    # in the owned source workspace, never a host mount or changed mount option.
    with tempfile.TemporaryDirectory(prefix='.probe-git-exec-', dir=Path.cwd()) as value:
        yield Path(value)


def identity(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()
        return {'state': stat[0], 'parent': int(stat[1]), 'group': int(stat[2]),
                'session': int(stat[3]), 'start': stat[19]}
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


async def test_isolated_probe_loop_shutdown_settles_nested_git_before_return(tmp_path):
    from amplifier_web.probe_process import isolated
    ready = tmp_path / 'ready'
    code = f'''import os,pathlib,json,time
pathlib.Path({str(ready)!r}).write_text(json.dumps({{'pid':os.getpid(),'guard':os.getppid(),'group':os.getpgrp()}}))
time.sleep(60)
'''
    async def operation():
        owner = await probe_git.GitProbeOwner().start()
        try:
            return await request(owner, [sys.executable, '-c', code])
        finally:
            await owner.close()
    task = asyncio.create_task(isolated(operation))
    try:
        recorded = await marker(ready)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 8)
        assert all(identity(recorded[key]) is None for key in ('pid', 'guard', 'group'))
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


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


async def test_daemonized_helper_never_counts_as_settled_and_preserves_primary(tmp_path):
    owner = await probe_git.GitProbeOwner().start()
    ready = tmp_path / 'escaped'
    code = f'''import subprocess,sys
subprocess.Popen([sys.executable,'-c', "import os,pathlib,time; pathlib.Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(60)"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
sys.exit(17)
'''
    client = asyncio.create_task(request(owner, [sys.executable, '-c', code]))
    escaped = None
    try:
        async with asyncio.timeout(8):
            while not ready.exists() or not owner.failures:
                await asyncio.sleep(.01)
        escaped = os.pidfd_open(int(ready.read_text()))
        assert owner.failures[0]['primary']['code'] == 17
        assert owner.failures[0]['cleanup']['type'] == 'TimeoutError'
        assert not owner.cleanup_complete and not client.done()
        with pytest.raises(probe_git.GitProbeCleanupError):
            await owner.close()
    finally:
        if escaped is not None:
            signal.pidfd_send_signal(escaped, signal.SIGKILL)
            os.close(escaped)
        for process in owner.processes:
            await asyncio.wait_for(process.wait(), 5)
        for writer in owner.retained_connections:
            writer.close()
        await asyncio.gather(client, return_exceptions=True)
        owner.directory.cleanup()
        probe_git._failed_owners.discard(owner)


@pytest.mark.parametrize('mode', ['eof', 'malformed', 'cleanup_error'])
async def test_ambiguous_control_does_not_unwind_actual_shared_stage(tmp_path, monkeypatch, mode):
    """A real Foundation ensure stage must survive ambiguous broker delivery."""
    from amplifier_web.probe_process import spawn, stop
    # The shared-store mechanism executes its actual finally/_remove_owned_stage
    # path; only Git command transport is deliberately broken.
    marker_file = tmp_path / 'stage'
    source = tmp_path / 'sources'
    socket_path = str(tmp_path / 'control')
    async def broken(reader, writer):
        await reader.readline()
        if mode == 'malformed':
            writer.write(b'{"kind":"result","settled":true}\n')
        elif mode == 'cleanup_error':
            writer.write(b'{"kind":"cleanup_error","settled":false}\n')
        await writer.drain()
        writer.close()
    server = await asyncio.start_unix_server(broken, path=socket_path)
    code = f'''import asyncio, pathlib, os
from amplifier_foundation.sources.shared import SharedSourceStore
from amplifier_foundation.sources.git import git_subprocess_runner
from amplifier_web.probe_git import run_git
# Real ensure creates and owns the stage; observe it immediately before the
# transport call, without replacing ensure or its cleanup.
def observed(args, cwd, timeout):
    pathlib.Path({str(marker_file)!r}).write_text(str(cwd))
    return run_git(args,cwd,timeout)
async def main():
    with git_subprocess_runner(observed):
        await SharedSourceStore({str(source)!r}).ensure('file:///synthetic/repo', 'a'*40)
asyncio.run(main())
'''
    process = await spawn(sys.executable, '-c', code,
        env={**os.environ, 'AMPLIFIER_PROBE_GIT_SOCKET': socket_path},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, error = await asyncio.wait_for(process.communicate(), 8)
        assert process.returncode == 74, error.decode()
        stage = Path(marker_file.read_text())
        assert stage.exists(), 'Ambiguous transport unwound Foundation stage deletion'
    finally:
        await stop(process)
        server.close()
        await server.wait_closed()


async def test_output_overflow_settles_guard_and_writer_before_error(tmp_path):
    owner = await probe_git.GitProbeOwner().start()
    try:
        result = await request(owner, [sys.executable, '-c',
            'import sys,time; sys.stdout.write("x"*(5*1024*1024)); sys.stdout.flush(); time.sleep(60)'])
        assert result == {'kind': 'overflow', 'settled': True}
        assert not owner.processes
    finally:
        await owner.close()


async def test_guard_pipe_creation_cancellation_retains_launch_until_adopted(monkeypatch):
    owner = await probe_git.GitProbeOwner().start()
    loop = asyncio.get_running_loop()
    connect = loop.connect_write_pipe
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await connect(*args, **kwargs)
    monkeypatch.setattr(loop, 'connect_write_pipe', delayed)
    client = asyncio.create_task(request(owner, [sys.executable, '-c', 'raise AssertionError("not admitted")']))
    await entered.wait()
    closing = asyncio.create_task(owner.close())
    try:
        await asyncio.sleep(0)
        assert owner.pending_starts
        for _ in range(3):
            closing.cancel()
            await asyncio.sleep(0)
            assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert owner.cleanup_complete and not owner.pending_starts and not owner.processes
    finally:
        release.set()
        await asyncio.gather(client, closing, return_exceptions=True)
        await owner.close()


@pytest.mark.parametrize('lost', ['guard', 'anchor'])
async def test_lost_identity_never_authorizes_numeric_group_rekill(tmp_path, lost):
    owner = await probe_git.GitProbeOwner().start()
    ready = tmp_path / 'ready'
    code = f'''import os,pathlib,time,json
pathlib.Path({str(ready)!r}).write_text(json.dumps({{'pid':os.getpid(),'guard':os.getppid(),'group':os.getpgrp()}}))
time.sleep(60)
'''
    client = asyncio.create_task(request(owner, [sys.executable, '-c', code], seconds=.3))
    descriptors = {}
    recorded = await marker(ready)
    for key in ('pid', 'guard', 'group'):
        descriptors[key] = os.pidfd_open(recorded[key])
    try:
        signal.pidfd_send_signal(descriptors['guard' if lost == 'guard' else 'group'], signal.SIGKILL)
        async with asyncio.timeout(8):
            while not owner.retained_connections:
                await asyncio.sleep(.01)
        assert not client.done() and owner.errors and owner.processes
        with pytest.raises(probe_git.GitProbeCleanupError):
            await owner.close()
    finally:
        # Test-only identity-bound recovery of the intentionally broken owner.
        for descriptor in descriptors.values():
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(descriptor)
        for process in owner.processes:
            await asyncio.wait_for(process.wait(), 5)
        for writer in owner.retained_connections:
            writer.close()
        await asyncio.gather(client, return_exceptions=True)
        owner.directory.cleanup()
        probe_git._failed_owners.discard(owner)


@pytest.mark.parametrize('cancellations', [1, 3])
@pytest.mark.parametrize('owner,end', [
    ('defaults', 'cancel'), ('defaults', 'timeout'), ('defaults', 'normal'), ('defaults', 'startup_error'),
    ('provider', 'cancel'), ('validator', 'cancel')])
@pytest.mark.parametrize('shared', [False, True])
async def test_defaults_acquisition_owns_real_foundation_git_before_home_removal(tmp_path, monkeypatch, cancellations, owner, end, shared, executable_directory):
    """Actual defaults entrypoint -> load_root_bundle -> Foundation Git acquisition."""
    from amplifier_web import draft_defaults
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.host import config
    from amplifier_web.bundles import SNAPSHOT_VERSION
    home = tmp_path / 'app'
    home.mkdir()
    ready, origin, gate, late, diagnostic = (tmp_path / name for name in ('ready', 'origin', 'gate', 'late', 'diagnostic'))
    binary = executable_directory
    git = binary / 'git'
    repository = tmp_path / 'repository'
    repository.mkdir()
    (repository / 'bundle.md').write_text(
        f'---\nbundle:\n  name: synthetic\n  version: {SNAPSHOT_VERSION}\n'
        'session:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n---\n')
    real_git = shutil.which('git')
    assert real_git
    subprocess.run([real_git, 'init', '-q', '--initial-branch=main', str(repository)], check=True)
    subprocess.run([real_git, '-C', str(repository), 'add', 'bundle.md'], check=True)
    subprocess.run([real_git, '-C', str(repository), '-c', 'user.name=Synthetic',
                    '-c', 'user.email=synthetic@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    revision = subprocess.check_output([real_git, '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
    helper = f"""import pathlib,time
while not pathlib.Path({str(gate)!r}).exists(): time.sleep(.01)
pathlib.Path({str(late)!r}).write_text('helper survived cleanup')
"""
    git.write_text(f'''#!{sys.executable}
import json,os,pathlib,subprocess,sys,time
# Git invocation is not mocked: Foundation resolves a real git+file URI and
# launches this executable through its production runner.
if not any(arg in {{'fetch', 'clone'}} for arg in sys.argv[1:]):
    os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])
child = None if {end!r} == 'normal' else subprocess.Popen([sys.executable, '-c', {helper!r}])
pathlib.Path({str(ready)!r}).write_text(json.dumps({{'pid':os.getpid(),'helper':None if child is None else child.pid,'guard':os.getppid(),'group':os.getpgrp(),'session':os.getsid(0),'args':sys.argv[1:]}}))
while not pathlib.Path({str(gate)!r}).exists(): time.sleep(.01)
if {end!r} != 'normal': pathlib.Path({str(late)!r}).write_text('survived cleanup')
os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])
''')
    git.chmod(0o700)
    wrapper = f'''import asyncio,hashlib,json,os,pathlib,sys,traceback
import amplifier_foundation.sources.git as git
pathlib.Path({str(origin)!r}).write_text(json.dumps({{'pid':os.getpid(),'group':os.getpgrp(),'session':os.getsid(0),'foundationGit':git.__file__,'sha256':hashlib.sha256(pathlib.Path(git.__file__).read_bytes()).hexdigest()}}))
sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parent))
from amplifier_web import {dict(defaults='draft_defaults_probe',provider='provider_probe',validator='module_validate')[owner]} as entry
original = entry.{'main' if owner == 'validator' else 'query'}
async def observed(request):
    try: return await original(request)
    except BaseException:
        pathlib.Path({str(diagnostic)!r}).write_text(traceback.format_exc())
        raise
{'asyncio.run(__import__(\"amplifier_web.probe_git\",fromlist=[\"query\"]).query(entry.main()))' if owner == 'validator' else 'entry.query = observed; entry.main()'}
'''
    monkeypatch.setattr(RuntimeManager, '_command', lambda *a, **k: [sys.executable, '-c', wrapper, 'unused'])
    environment = {
        **os.environ, 'PATH': str(binary),
        'AMPLIFIER_HOME': str(tmp_path / 'community'),
        'AMPLIFIER_SOURCE_STORE': str(tmp_path / 'sources') if shared else '',
        'HOME': str(tmp_path), 'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull, 'PYTHONDONTWRITEBYTECODE': '1',
        'AMPLIFIER_GIT_CLONE_TIMEOUT_S': '.5' if end == 'timeout' else '30'}
    monkeypatch.setattr(config, 'worker_environment', lambda: environment)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    if end == 'startup_error':
        # Missing Git executable is exercised after the real entrypoint records
        # its actual Foundation origin; no runtime/provider shim.
        git.unlink()
    uri = 'git+' + repository.as_uri() + ('@' + revision if shared else '')
    if owner == 'defaults':
        operation = draft_defaults.resolve_defaults(home, str(tmp_path), bundle=uri)
    elif owner == 'provider':
        from amplifier_web.setup import SetupManager
        manager = SetupManager(home)
        monkeypatch.setattr(manager, 'config', lambda _: SimpleNamespace(
            configured_connections=[], module_sources={'provider-fixture': uri}))
        operation = manager.probe('providers.schema', {'module': 'provider-fixture'}, str(tmp_path))
    else:
        from amplifier_web.registry import RegistryManager
        operation = RegistryManager(home)._validate_row(
            {'id': 'tool-fixture', 'module': 'tool-fixture', 'section': 'tools', 'source': uri}, {})
    task = asyncio.create_task(operation)
    recorded = None
    try:
        loaded = await marker(origin)
        if end == 'startup_error':
            with pytest.raises(ValueError, match='Draft default resolution failed'):
                await asyncio.wait_for(task, 10)
            return
        async with asyncio.timeout(10):
            while not ready.exists():
                if task.done():
                    pytest.fail(diagnostic.read_text()[-2600:] if diagnostic.exists() else repr(task.exception()))
                await asyncio.sleep(.01)
        recorded = await marker(ready)
        assert any(arg in {'clone', 'fetch'} for arg in recorded['args'])
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
            try:
                result = await asyncio.wait_for(task, 10)
            except ValueError:
                pytest.fail(diagnostic.read_text()[-2600:] if diagnostic.exists() else 'missing diagnostic')
            assert result['providers'] == []
        assert identity(recorded['guard']) is None
        assert identity(recorded['pid']) is None
        assert identity(recorded['group']) is None
        if recorded['helper'] is not None:
            assert identity(recorded['helper']) is None, 'Git helper was not reaped'
        shutil.rmtree(home)  # No sleep/retry/ignore_errors; cleanup must be safe now.
        gate.touch()
        assert not late.exists()
        import amplifier_foundation.sources.git as loaded_git
        assert loaded['sha256'] == hashlib.sha256(Path(loaded_git.__file__).read_bytes()).hexdigest()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # Fixture cleanup is the same owner path, never a guessed/stale PID kill.


def test_subreaper_refusal_precedes_anchor_or_git_creation(monkeypatch):
    """Unavailable Linux adoption must fail closed before admitting a writer."""
    import errno
    import io
    from amplifier_web import probe_git_guard as guard

    monkeypatch.setattr(guard, 'sys', SimpleNamespace(
        platform='linux', argv=['guard'], stdin=SimpleNamespace(
            buffer=io.BytesIO(b'{"args":["git"]}\n'))))
    monkeypatch.setattr(guard.ctypes, 'CDLL',
                        lambda *a, **k: SimpleNamespace(prctl=lambda *a: -1))
    monkeypatch.setattr(guard.ctypes, 'get_errno', lambda: errno.EPERM)
    def forbidden(*args, **kwargs):
        pytest.fail('Failed adoption must not create an anchor or Git writer')
    monkeypatch.setattr(guard.subprocess, 'Popen', forbidden)
    with pytest.raises(OSError) as caught:
        guard.main()
    assert caught.value.errno == errno.EPERM


@pytest.fixture
def fixture_subreaper():
    """Recover the negative control's orphans without changing the test host."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0
    assert libc.prctl(36, 1, 0, 0, 0) == 0
    try:
        yield
    finally:
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0


@pytest.mark.parametrize('mode', ['cancel', 'timeout', 'success', 'default_negative'])
async def test_real_git_pack_transfer_settles_before_probe_cleanup(
        tmp_path, monkeypatch, executable_directory, fixture_subreaper, mode, record_property):
    """Cancel actual Git mid-pack, not an executable gated before exec.

    The fixture SSH transport runs only a fixed local git-upload-pack. It forwards
    real protocol/pack bytes, then stops reading that server's pipe. No socket,
    credential, external remote or artificial Git executable is involved.
    """
    from amplifier_web import draft_defaults
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.host import config
    from amplifier_web.bundles import SNAPSHOT_VERSION
    import amplifier_foundation.sources.git as foundation_git

    real_git = shutil.which('git')
    assert real_git
    repository = tmp_path / 'repository'
    repository.mkdir()
    (repository / 'bundle.md').write_text(
        f'---\nbundle:\n  name: pack-fixture\n  version: {SNAPSHOT_VERSION}\n'
        'session:\n  orchestrator:\n    module: loop-live\n'
        '  context:\n    module: context-simple\n---\n')
    # Incompressible, finite payload exceeds the explicitly reduced server pipe.
    (repository / 'payload').write_bytes(hashlib.shake_256(b'1852-local-pack').digest(1024 * 1024))
    environment = {**os.environ, 'HOME': str(tmp_path), 'GIT_CONFIG_NOSYSTEM': '1',
                   'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_COUNT': '1',
                   'GIT_CONFIG_KEY_0': 'protocol.version', 'GIT_CONFIG_VALUE_0': '0',
                   'GIT_TERMINAL_PROMPT': '0', 'GIT_SSH_VARIANT': 'ssh'}
    def git(*args):
        return subprocess.check_output([real_git, *args], env=environment, text=True)
    pack_objects = Path(git('--exec-path').strip()) / 'git-pack-objects'
    git('init', '-q', '--initial-branch=main', str(repository))
    git('-C', str(repository), 'add', '.')
    git('-C', str(repository), '-c', 'user.name=Synthetic',
        '-c', 'user.email=synthetic@example.invalid', 'commit', '-qm', 'pack fixture')
    revision = git('-C', str(repository), 'rev-parse', 'HEAD').strip()
    started, ready, gate, late, origin, diagnostic = (
        tmp_path / name for name in ('started', 'pack-ready', 'gate', 'late', 'origin', 'diagnostic'))
    transport = executable_directory / 'local-ssh'
    transport.write_text(f'''#!{sys.executable}
import fcntl,json,os,pathlib,selectors,subprocess,sys,time
def publish(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    os.replace(temporary, path)
def observe(pid):
    root = pathlib.Path('/proc') / str(pid)
    fields = (root / 'stat').read_text().split(') ',1)[1].split()
    return {{'pid':pid,'state':fields[0],'parent':int(fields[1]),'group':int(fields[2]),
             'session':int(fields[3]),'start':fields[19],
             'exe':str((root/'exe').resolve()),
             'argv':(root/'cmdline').read_bytes().replace(b'\\0',b' ').decode()}}
assert 'fixture.invalid' in sys.argv[1:]
assert sys.argv[-1].startswith('git-upload-pack ')
server = subprocess.Popen([{real_git!r},'upload-pack',{str(repository)!r}],
                          stdin=subprocess.PIPE,stdout=subprocess.PIPE)
try:
    fcntl.fcntl(server.stdout, fcntl.F_SETPIPE_SZ, 4096)
    client = observe(os.getppid())
    publish(pathlib.Path({str(started)!r}), {{'proxy':observe(os.getpid()),
        'server':observe(server.pid),'client':client}})
except BaseException:
    # No client protocol has been forwarded yet, so no pack helper can exist.
    server.kill()
    server.wait(timeout=5)
    raise
with selectors.DefaultSelector() as selector:
    selector.register(0, selectors.EVENT_READ, 'input')
    selector.register(server.stdout, selectors.EVENT_READ, 'output')
    tail = b''
    forwarded = 0
    pack_start = None
    paused = False
    while selector.get_map():
        for key,_ in selector.select(.05):
            data = os.read(key.fd,4096)
            if not data:
                selector.unregister(key.fileobj)
                if key.data == 'input': server.stdin.close()
                continue
            destination = server.stdin.fileno() if key.data == 'input' else 1
            view = memoryview(data)
            while view:
                view = view[os.write(destination, view):]
            if key.data == 'output':
                if pack_start is None and b'PACK' in tail + data:
                    pack_start = forwarded - len(tail) + (tail + data).index(b'PACK')
                forwarded += len(data)
                tail = data[-3:]
                if pack_start is not None and forwarded >= pack_start + 12 + 64 and not paused:
                    paused = True
                    children = pathlib.Path(f'/proc/{{server.pid}}/task/{{server.pid}}/children')
                    helpers = [observe(int(pid)) for pid in children.read_text().split()]
                    publish(pathlib.Path({str(ready)!r}), {{
                        'proxy':observe(os.getpid()),'server':observe(server.pid),
                        'client':observe(client['pid']),'helpers':helpers,
                        'forwarded':forwarded,'checkpoint':time.monotonic(),
                        'packPayloadBytes':forwarded-pack_start-12,
                        'pipeBytes':fcntl.fcntl(server.stdout,fcntl.F_GETPIPE_SZ)}})
                    while not pathlib.Path({str(gate)!r}).exists(): time.sleep(.01)
                    pathlib.Path({str(late)!r}).write_text('transport resumed')
sys.exit(server.wait(timeout=5))
''')
    transport.chmod(0o700)
    wrapper = f'''import hashlib,json,os,pathlib,traceback
import amplifier_foundation.sources.git as git
from amplifier_web import draft_defaults_probe as entry
from amplifier_foundation.sources.git import git_subprocess_runner
original = entry.query
async def observed(request):
    runner = git._git_subprocess_runner.get()
    pathlib.Path({str(origin)!r}).write_text(json.dumps({{
        'pid':os.getpid(),'session':os.getsid(0),'broker':runner is not None,
        'sha256':hashlib.sha256(pathlib.Path(git.__file__).read_bytes()).hexdigest()}}))
    try:
        if {mode!r} == 'default_negative':
            with git_subprocess_runner(None):
                return await original(request)
        return await original(request)
    except BaseException:
        pathlib.Path({str(diagnostic)!r}).write_text(traceback.format_exc())
        raise
entry.query = observed
entry.main()
'''
    monkeypatch.setattr(RuntimeManager, '_command', lambda *a, **k: [sys.executable, '-c', wrapper, 'unused'])
    environment.update({
        'GIT_SSH': str(transport), 'AMPLIFIER_HOME': str(tmp_path / 'community'),
        'AMPLIFIER_SOURCE_STORE': str(tmp_path / 'sources'), 'PYTHONDONTWRITEBYTECODE': '1',
        'AMPLIFIER_GIT_CLONE_TIMEOUT_S': '5' if mode == 'timeout' else '30'})
    monkeypatch.setattr(config, 'worker_environment', lambda: environment)
    home = tmp_path / 'app'
    home.mkdir()
    uri = f'git+ssh://fixture.invalid{repository}@{revision}'
    task = asyncio.create_task(draft_defaults.resolve_defaults(home, str(tmp_path), bundle=uri))
    descriptors = {}
    def retain(pid, expected=None):
        try:
            descriptor = os.pidfd_open(pid)
        except ProcessLookupError:
            return
        current = identity(pid)
        if not current or expected is not None and current['start'] != expected:
            os.close(descriptor)
            return
        if pid in descriptors:
            os.close(descriptor)
            return
        descriptors[pid] = descriptor
        # Fixture-only observation of our real Git tree, never production kills
        # from a process scan. Child descriptors are identity-bound before use.
        children = Path(f'/proc/{pid}/task/{pid}/children')
        try:
            child_ids = children.read_text().split()
        except FileNotFoundError:
            return
        for child_id in child_ids:
            child = identity(int(child_id))
            parent = identity(pid)
            if (child and child['parent'] == pid and parent and
                    parent['start'] == current['start']):
                retain(int(child_id), child['start'])
    recorded = None
    try:
        async with asyncio.timeout(10):
            while not ready.exists():
                if task.done():
                    pytest.fail(diagnostic.read_text()[-3000:] if diagnostic.exists() else repr(task.exception()))
                await asyncio.sleep(.01)
        recorded, loaded = await marker(ready), await marker(origin)
        record_property('pack_checkpoint', json.dumps(recorded))
        record_property('foundation_git_sha256', loaded['sha256'])
        processes = [recorded[key] for key in ('client', 'proxy', 'server')] + recorded['helpers']
        assert recorded['helpers'], 'PACK checkpoint must include a live real pack-objects helper'
        assert any('pack-objects' in row['argv'] for row in recorded['helpers'])
        assert recorded['packPayloadBytes'] >= 64 and recorded['pipeBytes'] == 4096
        assert loaded['broker'], 'Actual entrypoint must install the broker scope'
        assert loaded['sha256'] == hashlib.sha256(Path(foundation_git.__file__).read_bytes()).hexdigest()
        for row in processes:
            current = identity(row['pid'])
            assert current and current['state'] != 'Z' and current['start'] == row['start']
            assert row['session'] != loaded['session']
            assert row['session'] == recorded['client']['session']
            assert row['group'] == recorded['client']['group']
            retain(row['pid'], row['start'])
        if mode != 'default_negative':
            # Actual anchored guard session, not a cached group to be killed.
            for pid in (recorded['client']['group'], recorded['client']['session']):
                assert identity(pid) is not None
                retain(pid)
        # Actual client binary, not a script proxy named "git".
        assert Path(recorded['client']['exe']).samefile(real_git)
        assert Path(recorded['server']['exe']).samefile(real_git)
        assert all(Path(row['exe']).samefile(pack_objects) for row in recorded['helpers'])
        stage = Path(f"/proc/{recorded['client']['pid']}/cwd").resolve()
        assert '.source-' in str(stage) and stage.is_relative_to(tmp_path / 'sources')
        record_property('shared_stage', str(stage))
        if mode in {'cancel', 'default_negative'}:
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 6)
        elif mode == 'timeout':
            with pytest.raises(ValueError, match='Draft default resolution failed'):
                await asyncio.wait_for(task, 12)
        else:
            gate.touch()
            assert (await asyncio.wait_for(task, 10))['providers'] == []
        if mode == 'default_negative':
            # Controlled former mechanism, not attribution of the historical CI
            # failure: outer probe exits, detached real Git remains a writer.
            assert identity(recorded['client']['pid'])['state'] != 'Z'
            assert identity(recorded['server']['pid'])['state'] != 'Z'
            assert identity(loaded['pid']) is None
        else:
            assert all(identity(row['pid']) is None for row in processes)
            assert all(identity(pid) is None for pid in descriptors)
            shutil.rmtree(home)  # Safe immediately at the returned boundary.
            if mode != 'success':
                gate.touch()
                assert not late.exists()
        if mode in {'cancel', 'default_negative'}:
            assert stage.exists(), 'Abrupt probe death must not unwind shared-stage removal'
    finally:
        try:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # Only fixture-owned identities, never a scan-derived process kill.
            # Early checkpoint failure still has startup identities for recovery.
            for path in (started, ready):
                if path.exists():
                    recovered = json.loads(path.read_text())
                    rows = [recovered[key] for key in ('client', 'proxy', 'server')]
                    rows.extend(recovered.get('helpers', []))
                    for row in rows:
                        retain(row['pid'], row['start'])
            for descriptor in descriptors.values():
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            async with asyncio.timeout(5):
                while any(identity(pid) is not None for pid in descriptors):
                    for pid in descriptors:
                        try:
                            os.waitpid(pid, os.WNOHANG)
                        except ChildProcessError:
                            pass  # Wait for its real parent or adoption first.
                    await asyncio.sleep(.01)
        finally:
            for descriptor in descriptors.values():
                os.close(descriptor)
