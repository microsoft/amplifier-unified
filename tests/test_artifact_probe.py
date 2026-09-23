"""The probe group stays owned until helper and inherited pipes are closed."""
import asyncio
import base64
import errno
import json
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import artifact_runtime as inventory


def no_parent_signal(*args):
    raise AssertionError('The parent must not signal a possibly reaped group')


@pytest.mark.parametrize('helper', ['version', 'imports'])
@pytest.mark.parametrize('exit_code', [0, 1])
async def test_completed_helper_never_causes_a_parent_group_signal(monkeypatch, helper, exit_code):
    """Also runs against the old public helpers: their finally killpg fails here."""
    raw = b'v1.2.3\n' if helper == 'version' else b'ARTIFACT_IMPORTS={"good":"passed"}\n'
    class Process:
        pid = 12345
        returncode = None
        reads = 0
        def __init__(self, supervised):
            self.supervised = supervised
            self.stdout = self.stdin = self
        def write(self, data):
            pass
        async def drain(self):
            pass
        def close(self):
            self.returncode = -signal.SIGKILL
        async def readline(self):
            return json.dumps({'status': 'completed', 'returncode': exit_code,
                'output': base64.b64encode(raw).decode()}).encode() + b'\n'
        async def read(self, size):
            self.reads += 1
            if self.supervised:
                return b'{"cleanup":"group"}\n'
            return raw if self.reads == 1 else b''
        async def wait(self):
            if self.returncode is None:
                self.returncode = exit_code
            return self.returncode
    async def create(*args, **kwargs):
        return Process(any(str(arg).endswith('_artifact_probe.py') for arg in args))
    monkeypatch.setattr(inventory.asyncio, 'create_subprocess_exec', create)
    monkeypatch.setattr(inventory, 'os', SimpleNamespace(name='posix', killpg=no_parent_signal))
    monkeypatch.setattr(inventory, 'ARTIFACT_IMPORTS', {'good': 'json'})
    if helper == 'version':
        result = await inventory._version({'status': 'available', 'path': '/fake'},
            {'args': (), 'pattern': r'v(\d+\.\d+\.\d+)'})
        assert result['versionStatus'] == ('verified' if exit_code == 0 else 'unknown')
    else:
        assert await inventory.verify_imports([{'name': 'good'}]) == ('passed' if exit_code == 0 else 'unknown')


@pytest.mark.skipif(os.name != 'posix', reason='Owned process groups require POSIX')
@pytest.mark.parametrize('exit_code', [0, 3])
async def test_real_normal_helper_exit_is_reported_after_group_cleanup(monkeypatch, exit_code):
    monkeypatch.setattr(inventory.os, 'killpg', no_parent_signal)
    result = await inventory._probe([sys.executable, '-I', '-c',
        f'print("version data");raise SystemExit({exit_code})'], limit=2048, timeout=2)
    assert result == {'status': 'completed', 'returncode': exit_code, 'output': b'version data\n'}


def running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Minimal Linux containers may retain an orphan zombie until PID1 reaps it.
    # A zombie has no running code, descriptors or descendants to keep alive.
    stat = Path(f'/proc/{pid}/stat')
    try:
        if stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z':
            return False
    except ProcessLookupError:
        # The process can disappear after kill(pid, 0) succeeds.
        return False
    except FileNotFoundError:
        pass
    return True


@pytest.mark.parametrize(('error', 'expected'), [
    (ProcessLookupError(errno.ESRCH, 'No such process'), False),
    (FileNotFoundError(errno.ENOENT, 'No proc stat file'), True),
    (PermissionError(errno.EACCES, 'Permission denied'), None),
    (OSError(errno.EIO, 'Input/output error'), None),
], ids=['exited-after-kill-check', 'proc-unavailable', 'permission-error', 'io-error'])
def test_running_handles_stat_exit_race_without_hiding_other_errors(monkeypatch, error, expected):
    calls = []
    def kill(pid, sig):
        calls.append(('kill', pid, sig))
    def read_stat(path):
        calls.append(('stat', str(path)))
        raise error
    monkeypatch.setattr(sys.modules[__name__], 'os', SimpleNamespace(kill=kill))
    monkeypatch.setattr(Path, 'read_text', read_stat)
    if expected is None:
        with pytest.raises(type(error)) as caught:
            running(12345)
        assert caught.value is error
    else:
        assert running(12345) is expected
    assert calls == [('kill', 12345, 0), ('stat', '/proc/12345/stat')]


async def stopped(pids):
    async with asyncio.timeout(3):
        while any(running(pid) for pid in pids):
            await asyncio.sleep(.02)


def descendant_command(tmp_path, mode):
    marker = tmp_path / 'pids.json'
    code = f'''import json,os,subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'])
Path({str(marker)!r}).write_text(json.dumps([os.getpid(),child.pid]))
print({'x' * 3000 if mode == 'overflow' else 'v1.2.3'!r},flush=True)
{'raise SystemExit(0)' if mode == 'leader-exits' else 'time.sleep(60)'}
'''
    return marker, [sys.executable, '-I', '-c', code]


@pytest.mark.skipif(os.name != 'posix', reason='Owned process groups require POSIX')
@pytest.mark.parametrize('mode', ['leader-exits', 'timeout', 'overflow'])
async def test_descendant_inheriting_stdout_is_stopped_even_after_helper_exit(monkeypatch, tmp_path, mode):
    marker, command = descendant_command(tmp_path, mode)
    monkeypatch.setattr(inventory.os, 'killpg', no_parent_signal)
    result = await asyncio.wait_for(inventory._probe(command, limit=2048, timeout=1), 4)
    assert result == {'status': 'overflow' if mode == 'overflow' else 'timeout'}
    pids = json.loads(marker.read_text())
    await stopped(pids)


@pytest.mark.skipif(os.name != 'posix', reason='Owned process groups require POSIX')
async def test_cancellation_closes_owned_group_and_propagates(monkeypatch, tmp_path):
    marker, command = descendant_command(tmp_path, 'timeout')
    monkeypatch.setattr(inventory.os, 'killpg', no_parent_signal)
    task = asyncio.create_task(inventory._probe(command, limit=2048, timeout=15))
    async with asyncio.timeout(3):
        while not marker.exists():
            await asyncio.sleep(.01)
    pids = json.loads(marker.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await stopped(pids)


async def test_repeated_cancellation_during_spawn_still_owns_eventual_process(monkeypatch):
    from unittest.mock import AsyncMock
    started = asyncio.Event()
    spawn = asyncio.get_running_loop().create_future()
    async def create(*args, **kwargs):
        started.set()
        return await spawn
    close = AsyncMock(return_value=True)
    monkeypatch.setattr(inventory.asyncio, 'create_subprocess_exec', create)
    monkeypatch.setattr(inventory, '_close_probe', close)
    monkeypatch.setattr(inventory, 'os', SimpleNamespace(name='posix', killpg=no_parent_signal))
    task = asyncio.create_task(inventory._probe(['/fake'], limit=2048, timeout=2))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    process = object()
    spawn.set_result(process)
    with pytest.raises(asyncio.CancelledError):
        await task
    close.assert_awaited_once_with(process)


async def test_repeated_cancellation_during_cleanup_still_waits_and_propagates():
    finished = asyncio.Event()
    cleanup = asyncio.create_task(finished.wait())
    task = asyncio.create_task(inventory._await_cleanup(cleanup))
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finished.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleanup.done() and not cleanup.cancelled()


@pytest.mark.skipif(os.name != 'posix', reason='Owned process groups require POSIX')
async def test_parent_control_eof_stops_helper_without_a_parent_signal(monkeypatch, tmp_path):
    marker, command = descendant_command(tmp_path, 'timeout')
    monkeypatch.setattr(inventory.os, 'killpg', no_parent_signal)
    supervisor = await asyncio.create_subprocess_exec(sys.executable, '-I',
        str(Path(inventory.__file__).with_name('_artifact_probe.py')),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    supervisor.stdin.write(json.dumps({'command': command, 'limit': 2048,
        'timeout': 15, 'stderr': False}).encode() + b'\n')
    await supervisor.stdin.drain()
    async with asyncio.timeout(3):
        while not marker.exists():
            await asyncio.sleep(.01)
        supervisor.stdin.close()
        result = await supervisor.stdout.read()
        await supervisor.wait()
    assert result == b'{"status":"cancelled"}\n{"cleanup":"group"}\n'
    assert supervisor.returncode == -signal.SIGKILL
    await stopped(json.loads(marker.read_text()))


async def test_unsupported_group_cleanup_runs_nothing(monkeypatch):
    monkeypatch.setattr(inventory, 'os', SimpleNamespace(name='nt'))
    result = await inventory._probe(['/not/executed'], limit=2048, timeout=2)
    assert result is None


@pytest.mark.parametrize('failure', ['exit', 'trailer', 'drain', 'close'])
async def test_cleanup_requires_acknowledgement_and_confirmed_exit(failure):
    from unittest.mock import AsyncMock, Mock
    close = Mock(side_effect=OSError('private close detail') if failure == 'close' else None)
    read = AsyncMock(return_value=b'' if failure == 'trailer' else b'{"cleanup":"group"}\n',
        side_effect=TimeoutError() if failure == 'drain' else None)
    process = SimpleNamespace(returncode=0 if failure == 'exit' else -signal.SIGKILL,
        stdin=SimpleNamespace(close=close), stdout=SimpleNamespace(read=read), wait=AsyncMock())
    assert await inventory._close_probe(process) is False


@pytest.mark.parametrize('helper', ['version', 'imports'])
async def test_failed_cleanup_cannot_report_success(monkeypatch, helper):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(inventory, '_close_probe', AsyncMock(return_value=False))
    # This deliberately uses an already-exited supervisor stub. Even a valid
    # result cannot substitute for the owned cleanup acknowledgement.
    raw = b'v1.2.3\n' if helper == 'version' else b'ARTIFACT_IMPORTS={"good":"passed"}\n'
    process = SimpleNamespace(returncode=0, pid=12345,
        stdin=SimpleNamespace(write=lambda _: None, drain=AsyncMock()),
        stdout=SimpleNamespace(readline=AsyncMock(return_value=json.dumps({
            'status': 'completed', 'returncode': 0, 'output': base64.b64encode(raw).decode()}).encode())))
    monkeypatch.setattr(inventory.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    monkeypatch.setattr(inventory, 'os', SimpleNamespace(name='posix', killpg=no_parent_signal))
    monkeypatch.setattr(inventory, 'ARTIFACT_IMPORTS', {'good': 'json'})
    if helper == 'version':
        result = await inventory._version({'status': 'available', 'path': '/fake'},
            {'args': (), 'pattern': r'v(\d+\.\d+\.\d+)'})
        assert result['versionStatus'] == 'unknown'
    else:
        assert await inventory.verify_imports([{'name': 'good'}]) == 'unknown'
