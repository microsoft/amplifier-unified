"""Branding storage checks need only pytest, PyYAML and Pillow, not Foundation."""
from contextlib import contextmanager
import base64
import io
import json
import os
from pathlib import Path
import random
import shutil
import uuid

from PIL import Image
import pytest

from amplifier_web import instance_branding as branding, windows_files
from amplifier_web.deployment import DEFAULT_SERVER


@pytest.fixture
def tmp_path(tmp_path_factory):
    if os.name != 'nt':
        yield tmp_path_factory.mktemp('branding')
        return
    # Exercise real private ACLs below the profile. Never repair a hosted
    # runner's broadly shared temporary-directory permissions.
    root = windows_files.address(Path.home() / ('.unified-branding-test-' + uuid.uuid4().hex))
    with windows_files.directory(root, owned_root=root, create=True):
        pass
    try:
        yield root
    finally:
        shutil.rmtree(root)


def owner(root):
    return branding.InstanceBranding(root, DEFAULT_SERVER)


def noisy_image():
    pixels = random.Random(37).randbytes(512 * 512 * 4)
    image = Image.frombytes('RGBA', (512, 512), pixels)
    output = io.BytesIO()
    image.save(output, 'PNG')
    return base64.b64encode(output.getvalue()).decode('ascii')


def test_windows_record_lookup_uses_real_supported_read_limit(tmp_path, monkeypatch):
    checked = []
    pinned = windows_files.PinnedDirectory.__new__(windows_files.PinnedDirectory)
    pinned._check = lambda: None

    def missing(name):
        checked.append(name)
        raise FileNotFoundError(name)

    pinned._existing = missing

    @contextmanager
    def directory(path, *, owned_root):
        assert path.parent.parent == owned_root == tmp_path
        yield pinned

    monkeypatch.setattr(branding, '_WINDOWS', True)
    monkeypatch.setattr(branding, '_record_absent', lambda path: False)
    monkeypatch.setattr(windows_files, 'directory', directory)
    # This is the real read_private validator, not a stand-in accepting any
    # size. The old 2 MiB limit raises before reaching the missing-file lookup.
    store = owner(tmp_path)
    assert checked == [store.path.name]
    assert not store.path.exists()
    assert branding._RECORD_LIMIT == windows_files._MAX_CONTENT == 1024 * 1024


def test_windows_absent_record_is_noncreating_and_does_not_require_private_root(tmp_path_factory, monkeypatch):
    ordinary = tmp_path_factory.mktemp('ordinary-root') / 'app'
    ordinary.mkdir()
    monkeypatch.setattr(branding, '_WINDOWS', True)

    def no_private_root_required(*args, **kwargs):
        pytest.fail('An absent optional branding record must not open a private root')

    monkeypatch.setattr(windows_files, 'directory', no_private_root_required)
    initial = owner(ordinary).snapshot()
    assert initial['name'].startswith('Amplifier')
    assert list(ordinary.iterdir()) == []


def test_windows_unsafe_link_component_is_not_treated_as_missing(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import stat
    probe = owner(tmp_path)
    original = Path.lstat

    def reparse(path):
        if path == tmp_path:
            return SimpleNamespace(st_mode=stat.S_IFDIR,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(path)

    def refused(*args, **kwargs):
        raise PermissionError('unsafe link must reach private boundary')

    monkeypatch.setattr(branding, '_WINDOWS', True)
    monkeypatch.setattr(Path, 'lstat', reparse)
    monkeypatch.setattr(windows_files, 'directory', refused)
    with pytest.raises(PermissionError, match='unsafe link'):
        owner(tmp_path)
    assert not probe.path.exists()


def test_noisy_upload_reserves_worst_case_escaped_name_and_reloads_identically(tmp_path):
    encoded = noisy_image()
    assert len(base64.b64decode(encoded)) <= branding.MAX_IMAGE_BYTES
    # This admitted raster would exceed the native record cap if merely
    # re-encoded at 512px. Fitting must happen before prepared upload returns.
    assert len(encoded) > windows_files._MAX_CONTENT
    store = owner(tmp_path)
    prepared = store.prepare({'image': encoded})
    max_name = '\U0001f600' * 120
    record = {'version': 1, 'name': max_name, 'color': '#abcdef', **prepared}
    assert len(branding._record_bytes(record)) <= windows_files._MAX_CONTENT
    with Image.open(io.BytesIO(base64.b64decode(prepared['image']))) as image:
        assert max(image.size) < 512
    saved = store.update({**prepared, 'name': max_name, 'color': '#abcdef'})
    before = store.path.read_bytes()
    assert len(before) <= windows_files._MAX_CONTENT
    assert max_name.encode() not in before and b'\\ud83d\\ude00' in before
    restored = owner(tmp_path)
    assert restored.snapshot() == saved
    assert restored._record == store._record
    assert restored.icon(512) == store.icon(512)
    assert store.path.read_bytes() == before


def test_exact_native_record_cap_counts_complete_escaped_json(tmp_path):
    # Synthetic image text isolates the serialized byte boundary; actual
    # raster admission and decoding are covered separately above.
    record = {'version': 1, 'name': '\U0001f600' * 120, 'color': '#abcdef', 'image': ''}
    overhead = len(json.dumps(record, ensure_ascii=True).encode('ascii'))
    record['image'] = 'A' * (windows_files._MAX_CONTENT - overhead)
    assert len(branding._record_bytes(record)) == windows_files._MAX_CONTENT
    store = owner(tmp_path)
    saved = store.update({key: record[key] for key in ('name', 'color', 'image')})
    before = store.path.read_bytes()
    assert len(before) == windows_files._MAX_CONTENT
    record['image'] += 'A'
    with pytest.raises(ValueError, match='too large'):
        store.update({'image': record['image']})
    assert store.path.read_bytes() == before
    assert store.snapshot() == saved


@pytest.mark.parametrize('character', ['x', '\U0001f600'])
def test_exact_record_threshold_and_overflow_preserve_file_and_snapshot(tmp_path, monkeypatch, character):
    store = owner(tmp_path)
    prepared = store.prepare({'name': character * 119, 'color': '#abcdef'})
    record = {**store._record, **prepared}
    exact_size = len(json.dumps(record, ensure_ascii=True).encode('ascii'))
    # Lower only the owner's cap to make a small valid record hit equality;
    # the native file boundary itself remains real and unchanged.
    with monkeypatch.context() as patch:
        patch.setattr(branding, '_RECORD_LIMIT', exact_size)
        saved = store.update(prepared)
        before = store.path.read_bytes()
        assert len(before) == exact_size
        with pytest.raises(ValueError, match='too large'):
            store.update(store.prepare({'name': character * 120}))
        assert store.path.read_bytes() == before
        assert store.snapshot() == saved
    assert owner(tmp_path).snapshot() == saved


@pytest.mark.skipif(os.name != 'nt', reason='requires real Windows private read/publication and profile ACLs')
def test_native_windows_branding_cold_start_private_write_and_restart(tmp_path):
    assert branding._WINDOWS
    first = owner(tmp_path)  # No record: real native cold read must succeed.
    initial = first.snapshot()
    assert not first.path.exists()
    saved = first.update(first.prepare({
        'image': noisy_image(), 'name': '\U0001f600' * 120, 'color': '#abcdef'}))
    with windows_files.directory(first.path.parent, owned_root=tmp_path) as pinned:
        raw = pinned.read_private(first.path.name, limit=windows_files._MAX_CONTENT)
    assert raw == first.path.read_bytes()
    assert len(raw) <= windows_files._MAX_CONTENT
    restarted = owner(tmp_path)
    assert restarted.snapshot() == saved
    assert restarted.icon(512) == first.icon(512)
    # Also exercise native replacement, not only first publication.
    renamed = restarted.update(restarted.prepare({'name': 'Windows studio'}))
    assert owner(tmp_path).snapshot() == renamed
    assert restarted.update(reset=True) == initial
    assert owner(tmp_path).snapshot() == initial


@pytest.mark.skipif(os.name != 'nt', reason='requires real Windows private ACL rejection')
def test_native_windows_existing_branding_with_unsafe_acl_is_refused(tmp_path):
    import subprocess
    store = owner(tmp_path)
    store.update(store.prepare({'name': 'Private'}))
    path = store.path
    before = path.read_bytes()
    subprocess.run(['icacls', str(path), '/grant', '*S-1-1-0:(R)'],
                   check=True, capture_output=True)
    try:
        with pytest.raises(PermissionError):
            owner(tmp_path)
        assert path.read_bytes() == before
    finally:
        subprocess.run(['icacls', str(path), '/remove:g', '*S-1-1-0'],
                       check=True, capture_output=True)