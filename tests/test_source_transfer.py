"""Supported bounded transport, immutable source identity and no replay."""
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tarfile

import pytest

from amplifier_web import test_source_transfer as transfer
from amplifier_web import test_environment_worker as worker
from test_test_environment_worker import library, payload, Failure


def snapshot(path):
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    return {'archive_sha256': digest, 'archive_bytes': path.stat().st_size}


def archive(tmp_path, name='saved.txt', data=b'exact saved source'):
    path = tmp_path / 'source.tar'
    with tarfile.open(path, 'w') as target:
        item = tarfile.TarInfo(name); item.size = len(data)
        target.addfile(item, io.BytesIO(data))
    return path, snapshot(path)


def guest(tmp_path, upload, value, chunked):
    artifacts, workspace = tmp_path / 'artifacts', tmp_path / 'workspace'
    artifacts.mkdir(exist_ok=True); workspace.mkdir(exist_ok=True)
    code = transfer.GUEST.replace("'/artifacts'", repr(str(artifacts))).replace("'/workspace'", repr(str(workspace)))
    result = subprocess.run([sys.executable, '-I', '-c', code, json.dumps({
        'path': str(upload), 'bytes': value['archive_bytes'], 'sha256': value['archive_sha256'], 'chunked': chunked,
    })], capture_output=True, text=True, timeout=30)
    return result, workspace, artifacts


@pytest.mark.parametrize('size', [128 * 1024**2, 128 * 1024**2 + 1, 512 * 1024**2])
def test_real_transport_boundaries_and_exact_parts(tmp_path, size):
    path = tmp_path / 'source.tar'
    with path.open('wb') as source:
        source.truncate(size)
    value = snapshot(path)
    with transfer.staged(path, value) as (upload, destination, count, chunked):
        assert count == size and chunked == (size > transfer.SINGLE_FILE_LIMIT)
        if chunked:
            chunks = sorted(upload.iterdir())
            assert len(chunks) == (size + transfer.CHUNK_BYTES - 1) // transfer.CHUNK_BYTES
            assert max(item.stat().st_size for item in chunks) <= 64 * 1024**2
            digest = hashlib.sha256()
            for item in chunks:
                with item.open('rb') as part:
                    for block in iter(lambda: part.read(1024**2), b''):
                        digest.update(block)
            assert digest.hexdigest() == value['archive_sha256']
        else:
            assert upload == path and destination == '/artifacts/.source.tar'
    assert path.exists() and not list(tmp_path.glob('.source-transfer-*'))


@pytest.mark.parametrize('change', ['oversize', 'hash', 'size', 'symlink', 'fifo'])
def test_invalid_source_refused_without_staging(tmp_path, change):
    path, value = archive(tmp_path)
    fifo = None
    if change == 'oversize':
        with path.open('wb') as source: source.truncate(transfer.MAX_ARCHIVE + 1)
    elif change == 'hash': value['archive_sha256'] = '0' * 64
    elif change == 'size': value['archive_bytes'] += 1
    elif change == 'symlink':
        saved = path.with_suffix('.saved'); path.rename(saved); path.symlink_to(saved)
    else:
        path.unlink(); os.mkfifo(path)
        fifo = path.lstat()
    try:
        with pytest.raises((OSError, ValueError)):
            with transfer.staged(path, value): pytest.fail('invalid source admitted')
        assert not list(tmp_path.glob('.source-transfer-*'))
    finally:
        # Pytest scratch can live in retained /artifacts. Remove only our exact
        # FIFO, including on assertion failure; keep ordinary failure evidence.
        if fifo is not None:
            try:
                current = path.lstat()
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISFIFO(current.st_mode) and (current.st_dev, current.st_ino) == (fifo.st_dev, fifo.st_ino):
                    path.unlink()


@pytest.mark.parametrize('unexpected_admission', [False, True])
def test_fifo_negative_fixture_leaves_preservable_artifacts(tmp_path, monkeypatch, unexpected_admission):
    from contextlib import contextmanager
    from amplifier_web import environment_retention as retention

    artifacts = tmp_path / 'artifacts'
    scratch = artifacts / 'pytest-tmp'
    scratch.mkdir(parents=True)
    evidence = artifacts / 'result.txt'
    evidence.write_text('retained check evidence')
    if unexpected_admission:
        @contextmanager
        def admitted(*args):
            yield None
        monkeypatch.setattr(transfer, 'staged', admitted)
        with pytest.raises(pytest.fail.Exception, match='invalid source admitted'):
            test_invalid_source_refused_without_staging(scratch, 'fifo')
    else:
        test_invalid_source_refused_without_staging(scratch, 'fifo')
    assert not (scratch / 'source.tar').exists()
    assert evidence.read_text() == 'retained check evidence'
    exported = tmp_path / 'artifacts.tar'
    with tarfile.open(exported, 'w') as output:
        output.add(artifacts, arcname='artifacts')
    assert retention.inspect_archive(exported, 'artifacts', archive_only=True) == evidence.stat().st_size


def test_fifo_negative_fixture_preserves_replacement_evidence(tmp_path, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def replaced(path, value):
        path.unlink()
        path.write_text('replacement diagnostic')
        raise ValueError('source changed')
        yield  # pragma: no cover - context manager rejects before admission.
    monkeypatch.setattr(transfer, 'staged', replaced)
    test_invalid_source_refused_without_staging(tmp_path, 'fifo')
    assert (tmp_path / 'source.tar').read_text() == 'replacement diagnostic'


@pytest.mark.parametrize('chunked', [False, True])
def test_verified_extraction_removes_transport_only(tmp_path, monkeypatch, chunked):
    path, value = archive(tmp_path)
    if chunked: monkeypatch.setattr(transfer, 'SINGLE_FILE_LIMIT', 1)
    with transfer.staged(path, value) as (upload, _, _, is_chunked):
        # The real transport copies; preserve the host immutable original.
        import shutil
        copied = tmp_path / ('copied-parts' if chunked else 'copied.tar')
        (shutil.copytree if chunked else shutil.copyfile)(upload, copied)
        result, workspace, artifacts = guest(tmp_path, copied, value, is_chunked)
        assert result.returncode == 0, result.stderr
        assert (workspace / 'saved.txt').read_bytes() == b'exact saved source'
        assert not copied.exists() and list(artifacts.iterdir()) == []
    assert snapshot(path) == value


@pytest.mark.parametrize('change', ['corrupt', 'missing', 'extra', 'symlink', 'escape'])
def test_failed_chunks_preserve_diagnostics_and_cannot_escape(tmp_path, monkeypatch, change):
    path, value = archive(tmp_path, '../escape.txt' if change == 'escape' else 'saved.txt')
    monkeypatch.setattr(transfer, 'SINGLE_FILE_LIMIT', 1)
    with transfer.staged(path, value) as (upload, _, _, _):
        part = upload / '0000'
        if change == 'corrupt':
            with part.open('r+b') as stream: stream.write(b'corrupt')
        elif change == 'missing': part.unlink()
        elif change == 'extra': (upload / 'unexpected').write_text('extra')
        elif change == 'symlink': part.unlink(); part.symlink_to(path)
        result, workspace, artifacts = guest(tmp_path, upload, value, True)
        assert result.returncode != 0
        assert upload.exists() and not (tmp_path / 'escape.txt').exists()
        assert not list(workspace.iterdir()) and not list(artifacts.iterdir())
    assert snapshot(path) == value and not list(tmp_path.glob('.source-transfer-*'))


def test_prepare_rejects_invalid_export_before_dependency_or_environment(library, tmp_path):
    _, calls = library
    request = payload(tmp_path, 'prepare')
    (tmp_path / 'source.tar').write_bytes(b'changed')
    result = worker.execute_call(request)
    assert result['status'] == 'not-started' and result['effectAdmitted'] is False
    assert result['code'] == 'source-transfer-invalid'
    assert [name for name, _, _ in calls] == ['snapshot']
    assert (tmp_path / 'source.tar').read_bytes() == b'changed'


def test_start_invalid_frozen_source_never_admits(library, tmp_path):
    _, calls = library
    request = payload(tmp_path, 'prepare')
    prepared = worker.execute_call(request)
    assert prepared['ok']
    calls.clear()
    (tmp_path / 'source.tar').write_bytes(b'changed')
    result = worker.execute_call({**request, 'action': 'start', 'snapshot': prepared['snapshot']})
    assert result['status'] == 'not-started' and result['effectAdmitted'] is False
    assert not calls and not (tmp_path / 'source-ready.json').exists()


def test_directory_upload_loss_cleans_host_chunks_without_claiming_no_effect(library, tmp_path, monkeypatch):
    lib, calls = library
    request = payload(tmp_path, 'start')
    monkeypatch.setattr(worker.source_transfer, 'SINGLE_FILE_LIMIT', 1)
    def failed(_, upload, destination, **kwargs):
        assert upload.is_dir() and destination == '/artifacts/.source-parts'
        raise Failure()
    lib.push_files = failed
    result = worker.execute_call(request)
    assert not result['ok'] and result.get('effectAdmitted') is not False
    assert not list(tmp_path.glob('.source-transfer-*'))
    assert (tmp_path / 'source.tar').read_bytes() == b'source'
    assert not (tmp_path / 'source-ready.json').exists()
    assert [name for name, _, _ in calls] == ['launch']


def test_command_preserves_json_and_data_filter():
    args = shlex.split(transfer.command('/artifacts/.source.tar', 10, {'archive_sha256': 'a' * 64}, False))
    assert args[:2] == ['python', '-c'] and args[2] == transfer.GUEST
    assert json.loads(args[3])['bytes'] == 10 and "filter='data'" in args[2]


def test_source_path_replaced_during_scan_is_not_admitted(tmp_path, monkeypatch):
    path, value = archive(tmp_path)
    replacement = tmp_path / 'replacement.tar'
    replacement.write_bytes(path.read_bytes())  # Even equal bytes are another source file.
    original_stat = transfer.os.stat
    reads = 0
    def changed(name, *args, **kwargs):
        nonlocal reads
        if Path(name) == path:
            reads += 1
            if reads == 3:  # staged size, scan opening identity, scan final identity
                replacement.replace(path)
        return original_stat(name, *args, **kwargs)
    monkeypatch.setattr(transfer.os, 'stat', changed)
    with pytest.raises(ValueError, match='changed'):
        with transfer.staged(path, value): pytest.fail('replacement admitted')


def test_portable_preflight_without_optional_posix_open_flags(tmp_path, monkeypatch):
    path, value = archive(tmp_path)
    monkeypatch.delattr(transfer.os, 'O_NOFOLLOW')
    monkeypatch.delattr(transfer.os, 'O_NONBLOCK')
    assert transfer.preflight(path, value) == path.stat().st_size
    saved = path.with_suffix('.saved'); path.rename(saved); path.symlink_to(saved)
    with pytest.raises(ValueError, match='regular'):
        transfer.preflight(path, value)
