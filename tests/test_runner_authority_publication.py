"""Authority identity publication uses real private files and OS locks."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import threading

import pytest

pytest.importorskip('fcntl', reason='Registered target controller is native Linux only')
from amplifier_web import test_runner_controller as controller


@pytest.mark.parametrize('create', [False, True])
def test_readers_wait_for_immutable_authority_publication(tmp_path, monkeypatch, create):
    linked, release, reading = threading.Event(), threading.Event(), threading.Event()
    real_link = controller.worker.common.os.link
    def held_link(source, destination, *args, **kwargs):
        real_link(source, destination, *args, **kwargs)
        if Path(destination) == tmp_path / 'authority.json':
            linked.set()
            assert release.wait(5), 'publication was not released'
    def reader():
        reading.set()
        return controller.authority_id(tmp_path, create=create)
    monkeypatch.setattr(controller.worker.common.os, 'link', held_link)
    with ThreadPoolExecutor(max_workers=4) as pool:
        writer = pool.submit(controller.authority_id, tmp_path, create=True)
        try:
            assert linked.wait(5)
            assert (tmp_path / 'authority.json').stat().st_nlink == 2
            readers = [pool.submit(reader) for _ in range(7)]
            assert reading.wait(5)
            # The expected wait is a real OS lock, not a retry of an unsafe read.
            with pytest.raises(FutureTimeout):
                readers[0].result(timeout=0.05)
        finally:
            release.set()
        authority = writer.result(timeout=5)
        assert [future.result(timeout=5) for future in readers] == [authority] * 7
    assert controller.authority_id(tmp_path) == authority
    assert (tmp_path / 'authority.json').stat().st_nlink == 1
    assert not list(tmp_path.glob('.backend-proof-*'))
    assert (tmp_path / '.authority.lock').stat().st_nlink == 1


_PROCESS = '''
import json, sys
from pathlib import Path
from amplifier_web import test_runner_controller as controller
root = Path(sys.argv[1])
if sys.argv[2] == 'publish':
    real_link = controller.worker.common.os.link
    def held_link(source, destination, *args, **kwargs):
        real_link(source, destination, *args, **kwargs)
        if Path(destination) == root / 'authority.json':
            print(json.dumps({'stage': 'linked', 'nlink': Path(destination).stat().st_nlink}), flush=True)
            assert sys.stdin.readline() == 'release\\n'
    controller.worker.common.os.link = held_link
    value = controller.authority_id(root, create=True)
else:
    print(json.dumps({'stage': 'reading'}), flush=True)
    value = controller.authority_id(root, create=sys.argv[2] == 'create')
print(json.dumps({'id': value}), flush=True)
'''


def _line(process):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(5), 'controller did not reach the requested boundary'
    value = process.stdout.readline()
    assert value, 'controller exited before the requested boundary'
    return json.loads(value)


def _finish(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)


@pytest.mark.parametrize('mode', ['read', 'create'])
def test_distinct_processes_share_authority_publication_lock(tmp_path, mode):
    env = {**os.environ, 'PYTHONPATH': str(Path(controller.__file__).parent.parent),
           'PYTHONDONTWRITEBYTECODE': '1'}
    def start(operation):
        return subprocess.Popen([sys.executable, '-B', '-c', _PROCESS, str(tmp_path), operation],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, cwd=tmp_path)
    publisher, reader = start('publish'), None
    try:
        assert _line(publisher) == {'stage': 'linked', 'nlink': 2}
        reader = start(mode)
        assert _line(reader) == {'stage': 'reading'}
        with selectors.DefaultSelector() as selector:
            selector.register(reader.stdout, selectors.EVENT_READ)
            assert not selector.select(0.05), 'reader finished inside the two-link interval'
        publisher.stdin.write('release\n'); publisher.stdin.flush()
        first, second = _line(publisher), _line(reader)
        assert first == second == {'id': controller.authority_id(tmp_path)}
        assert publisher.wait(timeout=5) == reader.wait(timeout=5) == 0
        assert not publisher.stderr.read() and not reader.stderr.read()
    finally:
        _finish(publisher)
        if reader is not None:
            _finish(reader)
    assert (tmp_path / 'authority.json').stat().st_nlink == 1
    assert not list(tmp_path.glob('.backend-proof-*'))


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'mode', 'directory', 'fifo', 'owner'])
def test_unsafe_authority_lock_is_refused(tmp_path, monkeypatch, kind):
    lock = tmp_path / '.authority.lock'
    if kind == 'directory':
        lock.mkdir()
    elif kind == 'fifo':
        os.mkfifo(lock, 0o600)
    else:
        target = tmp_path / 'target'
        target.write_text(''); target.chmod(0o600)
        if kind == 'symlink':
            lock.symlink_to(target)
        elif kind == 'hardlink':
            os.link(target, lock)
        else:
            target.rename(lock)
            if kind == 'mode':
                lock.chmod(0o644)
            else:
                real_uid = os.getuid()
                monkeypatch.setattr(controller.os, 'getuid', lambda: real_uid + 1)
    with pytest.raises((ValueError, OSError)):
        controller.authority_id(tmp_path, create=True)
    assert not (tmp_path / 'authority.json').exists()


def test_replaced_lock_after_wait_does_not_admit_an_authority(tmp_path, monkeypatch):
    real_flock = controller.fcntl.flock
    def replace_after_acquisition(fd, operation):
        real_flock(fd, operation)
        lock = tmp_path / '.authority.lock'
        lock.unlink()
        lock.touch(mode=0o600)
    monkeypatch.setattr(controller.fcntl, 'flock', replace_after_acquisition)
    with pytest.raises(ValueError, match='authority lock changed'):
        controller.authority_id(tmp_path, create=True)
    assert not (tmp_path / 'authority.json').exists()


@pytest.mark.parametrize('failure', [OSError, KeyboardInterrupt])
@pytest.mark.parametrize('boundary', ['write', 'linked', 'lock'])
def test_exception_or_cancellation_releases_lock_and_preserves_identity(tmp_path, monkeypatch, failure, boundary):
    expected = None
    with monkeypatch.context() as changes:
        if boundary == 'lock':
            def interrupted_lock(*args):
                raise failure('interrupted lock wait')
            changes.setattr(controller.fcntl, 'flock', interrupted_lock)
        elif boundary == 'write':
            def interrupted_write(value, stream):
                stream.write('{')
                raise failure('interrupted private write')
            changes.setattr(controller.worker.common.json, 'dump', interrupted_write)
        else:
            real_link = controller.worker.common.os.link
            def interrupted_link(source, destination, *args, **kwargs):
                nonlocal expected
                real_link(source, destination, *args, **kwargs)
                expected = json.loads(Path(destination).read_text())['id']
                raise failure('interrupted after publication')
            changes.setattr(controller.worker.common.os, 'link', interrupted_link)
        with pytest.raises(failure):
            controller.authority_id(tmp_path, create=True)
    assert not list(tmp_path.glob('.backend-proof-*'))
    # A separate open proves the descriptor/OS lock was released after BaseException too.
    with ThreadPoolExecutor(max_workers=1) as pool:
        authority = pool.submit(controller.authority_id, tmp_path, create=True).result(timeout=5)
    assert expected is None or authority == expected
    assert controller.authority_id(tmp_path) == authority
    assert (tmp_path / 'authority.json').stat().st_nlink == 1


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'mode', 'directory', 'fifo', 'owner', 'oversize', 'invalid'])
def test_unsafe_authority_record_remains_refused(tmp_path, monkeypatch, kind):
    path = tmp_path / 'authority.json'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'fifo':
        os.mkfifo(path, 0o600)
    else:
        controller.worker.common.write_private_record(path, {'version': 1, 'id': 'a' * 32})
        if kind == 'symlink':
            other = tmp_path / 'other'; path.rename(other); path.symlink_to(other)
        elif kind == 'hardlink':
            os.link(path, tmp_path / '.backend-proof-interrupted')
        elif kind == 'mode':
            path.chmod(0o644)
        elif kind == 'owner':
            real_uid = os.getuid()
            # The lock itself is verified against the real uid; falsify only the record.
            real_fstat = controller.os.fstat
            def wrong_owner(fd):
                info = real_fstat(fd)
                if info.st_ino == path.stat().st_ino:
                    fields = list(info); fields[4] = real_uid + 1
                    return os.stat_result(fields)
                return info
            monkeypatch.setattr(controller.os, 'fstat', wrong_owner)
        elif kind == 'oversize':
            path.write_text('x' * (controller.MAX_CONTROL + 1))
        elif kind == 'invalid':
            path.write_text('{"version":1,"id":"invalid"}')
    before = path.lstat()
    with pytest.raises((ValueError, OSError)):
        controller.authority_id(tmp_path, create=True)
    assert path.lstat().st_ino == before.st_ino
    if kind == 'hardlink':
        # An unexplained historical second link is never deleted or "recovered".
        assert path.stat().st_nlink == 2
        assert (tmp_path / '.backend-proof-interrupted').exists()
