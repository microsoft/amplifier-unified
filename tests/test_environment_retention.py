import io
import copy
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


@pytest.mark.parametrize('kind', [tarfile.REGTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_file_or_link_cannot_replace_implicit_ancestor(tmp_path, kind):
    value = b'bad' if kind == tarfile.REGTYPE else 'bytes' if kind == tarfile.SYMTYPE else 'backup/container/rootfs/workspace/bytes'
    entries = ROOTS + [('workspace/bytes',tarfile.REGTYPE,b'good'),
        ('workspace/implicit/child',tarfile.REGTYPE,b'child'),('workspace/implicit',kind,value)]
    with pytest.raises(ValueError,match='replaces'):
        retention.filter_incus_stream(backup(entries),tmp_path,'owned')


def test_large_flat_selection_indexes_ancestors_once():
    names,links,ancestors={},{},set()
    for number in range(50000):
        name=f'workspace/file-{number}'
        retention.validate_member(tarfile.TarInfo(name),name,names,links,ancestors)
    assert len(names)==50000 and ancestors=={'workspace'}


def raw_header(name, kind=tarfile.DIRTYPE, size=0):
    item=tarfile.TarInfo(name);item.type=kind;item.size=size
    return item.tobuf()


@pytest.mark.parametrize('corruption', ['middle', 'after-footer', 'missing-footer', 'short-footer', 'truncated-header'])
def test_strict_input_never_turns_corruption_into_successful_retention(tmp_path,corruption):
    roots=raw_header(retention.PREFIX+'workspace')+raw_header(retention.PREFIX+'artifacts')
    lost=raw_header(retention.PREFIX+'workspace/lost.txt',tarfile.REGTYPE,4)+b'lost'.ljust(512,b'\0')
    raw={
        'middle':roots+b'not-a-tar-header'.ljust(512,b'x')+lost+bytes(1024),
        'after-footer':roots+bytes(1024)+lost+bytes(1024),
        'missing-footer':roots+lost,
        'short-footer':roots+lost+bytes(512),
        'truncated-header':roots+b'not-complete',
    }[corruption]
    with pytest.raises((ValueError,tarfile.TarError)):
        retention.filter_incus_stream(io.BytesIO(raw),tmp_path,'owned')
    assert not (tmp_path/'retention.json').exists()


def test_selected_archive_verification_also_rejects_corrupt_later_header(tmp_path):
    path=tmp_path/'workspace.tar'
    path.write_bytes(raw_header('workspace')+b'invalid'.ljust(512,b'x')+bytes(1024))
    with pytest.raises(ValueError,match='header'):
        retention.inspect_archive(path,'workspace')


def test_read_archives_do_not_cache_skipped_metadata(tmp_path,monkeypatch):
    opened=[]
    original=retention.tarfile.open
    def record(*args,**kwargs):
        value=original(*args,**kwargs)
        if kwargs.get('stream'):
            opened.append(value)
        return value
    monkeypatch.setattr(retention.tarfile,'open',record)
    archives=retention.filter_incus_stream(backup(ROOTS+[('etc/skip',tarfile.REGTYPE,b'ignored')]),tmp_path,'owned')
    for row in archives:
        retention.inspect_archive(row['destination'],row['source'].strip('/'))
    assert len(opened)==3 and all(not value.members for value in opened)


def test_selected_writers_do_not_retain_member_metadata(tmp_path,monkeypatch):
    written=[]
    original=retention.tarfile.open
    def record(*args,**kwargs):
        value=original(*args,**kwargs)
        if kwargs.get('mode')=='w' and kwargs.get('fileobj') is not None:
            written.append(value)
        return value
    source=backup(ROOTS+[(f'workspace/file-{i}',tarfile.REGTYPE,b'x') for i in range(1000)])
    monkeypatch.setattr(retention.tarfile,'open',record)
    archives=retention.filter_incus_stream(source,tmp_path,'owned')
    assert len(written)==2 and all(not value.members for value in written)
    assert retention.inspect_archive(archives[0]['destination'],'workspace')==1000


def test_artifacts_only_ignores_oversized_workspace_without_qualifying_removal(tmp_path, monkeypatch):
    monkeypatch.setattr(retention, 'SELECTED_LIMIT', 5)
    source = backup(ROOTS + [('workspace/large', tarfile.REGTYPE, b'123456'),
                            ('artifacts/result', tarfile.REGTYPE, b'pass')])
    archives = retention.filter_incus_stream(source, tmp_path, 'owned', selections=('artifacts',))
    result = retention.verify_artifacts(archives)
    assert result['selection'] == ['/artifacts'] and result['verified']
    assert result['logicalBytes'] == 4 and not (tmp_path / 'workspace.tar').exists()
    assert not (tmp_path / 'retention.json').exists()
    with pytest.raises(ValueError, match='Both distinct'):
        retention.verify_retention(tmp_path, archives)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'wrong-root', 'escape', 'truncated', 'too-large'])
def test_artifact_recovery_requires_complete_safe_bounded_selection(tmp_path, change, monkeypatch):
    entries = [('artifacts', tarfile.DIRTYPE, '')]
    if change == 'escape':
        entries += [('artifacts/out', tarfile.SYMTYPE, '../workspace')]
    else:
        entries += [('artifacts/result', tarfile.REGTYPE, b'evidence')]
    if change == 'missing':
        entries = []
    if change == 'too-large':
        monkeypatch.setattr(retention, 'SELECTED_LIMIT', 2)
    with pytest.raises((ValueError, tarfile.TarError)):
        archives = retention.filter_incus_stream(backup(entries), tmp_path, 'owned', selections=('artifacts',))
        if change == 'duplicate':
            archives.append(dict(archives[0]))
        if change == 'wrong-root':
            archives[0]['source'] = '/workspace'
        if change == 'truncated':
            (tmp_path / 'artifacts.tar').write_bytes((tmp_path / 'artifacts.tar').read_bytes()[:512])
        retention.verify_artifacts(archives)
    assert not (tmp_path / 'artifacts-retention.json').exists()


@pytest.mark.parametrize('existing_receipt', [False, True])
def test_verifying_existing_exports_is_read_only_and_never_republishes(tmp_path, existing_receipt):
    """Proposed publish=False permits inspection, never continuation authority."""
    selected = tmp_path / 'retained'; selected.mkdir()
    archives = retention.filter_incus_stream(backup(ROOTS), selected, 'owned')
    (tmp_path / 'source.tar').write_bytes(b'original source snapshot')
    if existing_receipt:
        retention.verify_retention(tmp_path, archives)
    before = {p.name:(p.read_bytes(), p.stat().st_mtime_ns) for p in selected.iterdir()}
    original = copy.deepcopy(archives)
    inspected = retention.verify_retention(tmp_path, archives, publish=False)
    assert inspected['verified'] and inspected['selection'] == ['/workspace', '/artifacts']
    assert archives == original
    assert before == {p.name:(p.read_bytes(), p.stat().st_mtime_ns) for p in selected.iterdir()}


def test_forged_verified_bit_never_replaces_archive_verification(tmp_path):
    archives = retention.filter_incus_stream(backup(ROOTS), tmp_path, 'owned')
    (tmp_path / 'source.tar').write_bytes(b'source')
    for row in archives:
        row['verified'] = True
    path = tmp_path / 'artifacts.tar'
    path.write_bytes(b'not a tar archive')
    with pytest.raises((ValueError, tarfile.TarError)):
        retention.verify_retention(tmp_path, archives)
    assert path.read_bytes() == b'not a tar archive'
    assert not (tmp_path / 'retention.json').exists()


def test_complete_retention_publication_never_overwrites_prior_receipt(tmp_path):
    archives = retention.filter_incus_stream(backup(ROOTS), tmp_path, 'owned')
    (tmp_path / 'source.tar').write_bytes(b'source')
    first = retention.verify_retention(tmp_path, archives)
    before = (tmp_path / 'retention.json').read_bytes()
    with pytest.raises(FileExistsError):
        retention.verify_retention(tmp_path, archives)
    assert (tmp_path / 'retention.json').read_bytes() == before
    assert retention.verify_retention(tmp_path, archives, publish=False) == first
    assert not list(tmp_path.glob('.retention-*'))


def test_recovery_archives_preserve_pytest_absolute_links_without_following_them(tmp_path):
    entries = ROOTS + [('artifacts/current', tarfile.SYMTYPE, '/artifacts/test-1'),
                       ('artifacts/test-1', tarfile.DIRTYPE, ''),
                       ('workspace/outside', tarfile.SYMTYPE, '/private/not-readable')]
    archives = retention.filter_incus_stream(backup(entries), tmp_path, 'owned', archive_only=True)
    (tmp_path / 'source.tar').write_bytes(b'source')
    result = retention.verify_retention(tmp_path, archives, 'archive-only')
    assert result['verified'] and result['archiveOnly']
    with tarfile.open(tmp_path / 'artifacts.tar') as value:
        assert value.getmember('artifacts/current').linkname == '/artifacts/test-1'
    with tarfile.open(tmp_path / 'workspace.tar') as value:
        assert value.getmember('workspace/outside').linkname == '/private/not-readable'
    assert not (tmp_path / 'test-1').exists()


@pytest.mark.parametrize('entry', [
    ('workspace/../escape', tarfile.REGTYPE, b'bad'),
    ('workspace/socket', tarfile.FIFOTYPE, ''),
    ('workspace/file', tarfile.REGTYPE, b'x' * 21),
])
def test_recovery_archive_keeps_path_type_and_size_bounds(tmp_path, monkeypatch, entry):
    monkeypatch.setattr(retention, 'RECOVERY_LIMIT', 20)
    with pytest.raises(ValueError):
        retention.filter_incus_stream(backup(ROOTS + [entry]), tmp_path, 'owned', archive_only=True)
    assert not (tmp_path / 'retention.json').exists()


@pytest.mark.parametrize('archive_only', [False, True])
@pytest.mark.parametrize('kind,reason,member_type', [
    (tarfile.FIFOTYPE, 'unsupported_type', 'fifo'),
    (tarfile.CHRTYPE, 'unsupported_type', 'character'),
    (tarfile.BLKTYPE, 'unsupported_type', 'block'),
])
def test_validation_diagnostic_preserves_special_entry_rejection(tmp_path, archive_only, kind, reason, member_type):
    # Only archive headers, never a real FIFO/device in the test filesystem.
    path = tmp_path / 'workspace.tar'
    raw = raw_header('workspace') + raw_header('workspace/special', kind) + bytes(1024)
    path.write_bytes(raw)
    with pytest.raises(retention.ValidationFailure) as caught:
        retention.inspect_archive(path, 'workspace', archive_only=archive_only)
    assert caught.value.diagnostic == {'version': 1, 'reasonCode': reason,
        'memberType': member_type, 'relativePath': 'workspace/special'}
    assert path.read_bytes() == raw and not (tmp_path / 'special').exists()


@pytest.mark.parametrize('case,reason', [
    ('duplicate', 'duplicate_entry'), ('traversal', 'unsafe_path'),
    ('header', 'invalid_header'), ('footer', 'incomplete_footer'),
    ('trailing', 'trailing_data'), ('members', 'member_limit'),
    ('size', 'selected_size_limit'), ('root', 'missing_root'),
])
def test_validation_diagnostic_names_exact_bounded_failure(tmp_path, monkeypatch, case, reason):
    root = raw_header('workspace')
    raw = root + bytes(1024)
    if case == 'duplicate':
        raw = root + root + bytes(1024)
    elif case == 'traversal':
        raw = root + raw_header('workspace/../private-secret') + bytes(1024)
    elif case == 'header':
        raw = root + b'invalid'.ljust(512, b'x') + bytes(1024)
    elif case == 'footer':
        raw = root + bytes(512)
    elif case == 'trailing':
        raw = root + bytes(1024) + b'private-secret'.ljust(512, b'x')
    elif case == 'members':
        monkeypatch.setattr(retention, 'MEMBER_LIMIT', 0)
    elif case == 'size':
        monkeypatch.setattr(retention, 'RECOVERY_LIMIT', 0)
        raw = root + raw_header('workspace/large', tarfile.REGTYPE, 1) + b'x'.ljust(512, b'\0') + bytes(1024)
    elif case == 'root':
        raw = raw_header('workspace/nested') + bytes(1024)
    path = tmp_path / 'workspace.tar'
    path.write_bytes(raw)
    with pytest.raises(retention.ValidationFailure) as caught:
        retention.inspect_archive(path, 'workspace', archive_only=True)
    diagnostic = retention.validation_failure_diagnostic(caught.value)
    assert diagnostic['reasonCode'] == reason
    assert len(json.dumps(diagnostic).encode()) < 1024
    assert str(tmp_path) not in json.dumps(diagnostic) and 'private-secret' not in json.dumps(diagnostic)
    assert path.read_bytes() == raw


@pytest.mark.parametrize('change,reason', [('negative', 'invalid_size'), ('sparse', 'sparse_entry')])
def test_validation_diagnostic_does_not_accept_sparse_or_negative_members(change, reason):
    item = tarfile.TarInfo('workspace/member')
    if change == 'negative':
        item.size = -1
    else:
        item.pax_headers['GNU.sparse.map'] = '0,1'
    with pytest.raises(retention.ValidationFailure) as caught:
        retention.validate_member(item, item.name, {}, {}, set(), archive_only=True)
    assert caught.value.diagnostic['reasonCode'] == reason


@pytest.mark.parametrize('path', ['/host/private', 'workspace/../private', 'workspace/a\nsecret',
                                  'workspace/' + 'x' * 300, 'workspace/token=secret', 'elsewhere/file'])
def test_validation_diagnostic_omits_unsafe_names_and_arbitrary_details(path):
    item = tarfile.TarInfo(path)
    item.type, item.linkname = tarfile.FIFOTYPE, '/host/private-target'
    failure = retention.ValidationFailure('unsupported_type', 'private exception text', item=item, name=path)
    assert failure.diagnostic == {'version': 1, 'reasonCode': 'unsupported_type', 'memberType': 'fifo'}
    assert retention.validation_failure_diagnostic(ValueError('private exception')) is None
    assert retention.validation_failure_diagnostic(tarfile.ReadError('private exception')) == {
        'version': 1, 'reasonCode': 'archive_format'}
    assert retention.validation_diagnostic({'version': 1, 'reasonCode': 'private arbitrary'}) is None
    assert retention.validation_diagnostic({**failure.diagnostic, 'linkTarget': item.linkname}) is None


def test_validation_diagnostic_never_includes_symbolic_target():
    item = tarfile.TarInfo('workspace/link')
    item.type, item.linkname = tarfile.SYMTYPE, '/private/token'
    with pytest.raises(retention.ValidationFailure) as caught:
        retention.validate_member(item, item.name, {}, {}, set())
    assert caught.value.diagnostic == {'version': 1, 'reasonCode': 'symbolic_target',
        'memberType': 'symbolic', 'relativePath': 'workspace/link'}
