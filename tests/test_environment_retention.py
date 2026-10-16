import io
import json
import tarfile

import pytest

from amplifier_web import environment_retention as retention


def backup(entries):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w') as archive:
        for name, kind, value in entries:
            item = tarfile.TarInfo('backup/container/rootfs/' + name)
            item.type = kind
            if kind == tarfile.REGTYPE:
                item.size = len(value)
                archive.addfile(item, io.BytesIO(value))
            else:
                item.linkname = value
                archive.addfile(item)
    result.seek(0)
    return result


ROOTS = [('workspace', tarfile.DIRTYPE, ''), ('artifacts', tarfile.DIRTYPE, '')]


def test_filter_retains_exact_selected_bytes_links_and_receipt(tmp_path):
    source = backup(ROOTS + [('etc/large', tarfile.REGTYPE, b'x' * 1000000),
        ('workspace/exact', tarfile.REGTYPE, b'\x00exact\xff'),
        ('workspace/link', tarfile.SYMTYPE, 'exact'),
        ('workspace/hard', tarfile.LNKTYPE, 'backup/container/rootfs/workspace/exact'),
        ('artifacts/result', tarfile.REGTYPE, b'passed')])
    root = tmp_path / 'retained'; root.mkdir()
    archives = retention.filter_incus_stream(source, root, 'owned')
    (tmp_path / 'source.tar').write_bytes(b'source')
    result = retention.verify_retention(tmp_path, archives)
    assert result['verified'] and result['archiveBytes'] < 30000
    assert result['sourceBytes'] == 6
    assert result['totalBytes'] == result['archiveBytes'] + 6
    assert json.loads((root / 'retention.json').read_text()) == result
    with tarfile.open(root / 'workspace.tar') as archive:
        assert archive.extractfile('workspace/exact').read() == b'\x00exact\xff'
        assert archive.getmember('workspace/link').linkname == 'exact'
        assert archive.getmember('workspace/hard').linkname == 'workspace/exact'


@pytest.mark.parametrize('entry', [
    ('workspace/../escape', tarfile.REGTYPE, b'bad'),
    ('workspace/out', tarfile.SYMTYPE, '/host/private'),
    ('workspace/out', tarfile.SYMTYPE, '../artifacts'),
    ('workspace/out', tarfile.SYMTYPE, 'out'),
    ('workspace/device', tarfile.CHRTYPE, ''),
    ('workspace/pipe', tarfile.FIFOTYPE, ''),
    ('workspace/hard', tarfile.LNKTYPE, 'backup/container/rootfs/etc/private'),
    ('workspace', tarfile.DIRTYPE, ''),
])
def test_unsupported_selection_keeps_partial_without_success_receipt(tmp_path, entry):
    with pytest.raises(ValueError):
        retention.filter_incus_stream(backup(ROOTS + [entry]), tmp_path, 'owned')
    assert not (tmp_path / 'retention.json').exists()


def test_link_chain_escape_and_symlink_parent_are_rejected(tmp_path):
    entries = ROOTS + [('workspace/a', tarfile.SYMTYPE, 'nested/..'),
        ('workspace/nested', tarfile.SYMTYPE, '..')]
    with pytest.raises(ValueError):
        retention.filter_incus_stream(backup(entries), tmp_path, 'owned')


def test_missing_root_and_size_limit_are_not_complete(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        retention.filter_incus_stream(backup(ROOTS[:1]), tmp_path, 'owned')
    second = tmp_path / 'second'; second.mkdir()
    monkeypatch.setattr(retention, 'SELECTED_LIMIT', 5)
    with pytest.raises(ValueError):
        retention.filter_incus_stream(backup(ROOTS + [('workspace/large', tarfile.REGTYPE, b'123456')]), second, 'owned')


def test_verify_catches_receipt_mismatch(tmp_path):
    archives = retention.filter_incus_stream(backup(ROOTS), tmp_path, 'owned')
    archives[0]['bytes'] = 1
    with pytest.raises(ValueError):
        retention.verify_retention(tmp_path, archives)


@pytest.mark.parametrize('change', ['duplicate', 'missing', 'empty', 'truncated', 'trailing'])
def test_unverified_archive_set_is_refused(tmp_path, change):
    archives = retention.filter_incus_stream(backup(ROOTS), tmp_path, 'owned')
    (tmp_path / 'source.tar').write_bytes(b'source')
    if change == 'duplicate':
        archives[1] = dict(archives[0])
    elif change == 'missing':
        archives.pop()
    elif change == 'empty':
        (tmp_path/'workspace.tar').write_bytes(b'')
    elif change == 'truncated':
        (tmp_path/'workspace.tar').write_bytes((tmp_path/'workspace.tar').read_bytes()[:512])
    else:
        with (tmp_path/'workspace.tar').open('ab') as stream:
            stream.write(b'unverified')
    with pytest.raises((ValueError, tarfile.ReadError)):
        retention.verify_retention(tmp_path, archives)
    assert not (tmp_path / 'retention.json').exists()
