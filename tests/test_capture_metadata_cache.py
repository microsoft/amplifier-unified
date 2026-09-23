import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from amplifier_web import capture_index
from amplifier_web.capture_index import CaptureMetadataCache


@pytest.fixture
def metadata(tmp_path):
    directory = tmp_path / 'capture'
    directory.mkdir()
    path = directory / 'metadata.json'
    path.write_text(json.dumps({'format': 'context-intelligence', 'version': '1.0.0'}))
    return directory, path


def test_success_only_reuse_is_detached_from_other_instances(metadata, monkeypatch):
    directory, path = metadata
    original = capture_index.validate_capture
    calls = []
    def validate(directory):
        calls.append(directory)
        return original(directory)
    monkeypatch.setattr(capture_index, 'validate_capture', validate)
    first, second = CaptureMetadataCache(), CaptureMetadataCache()
    first.validate(directory)
    first.validate(directory)
    assert calls == [directory]
    second.validate(directory)
    assert calls == [directory, directory]
    assert all(isinstance(value, int) for value in first._validated[str(path)])
    path.write_text('{invalid')
    for _ in range(2):
        with pytest.raises(ValueError):
            first.validate(directory)
        assert str(path) not in first._validated
    path.write_text(json.dumps({'format': 'context-intelligence', 'version': '1.0.0'}))
    first.validate(directory)
    first.validate(directory)
    assert len(calls) == 5


def test_same_size_rewrite_restoring_mtime_is_revalidated(metadata):
    directory, path = metadata
    cache = CaptureMetadataCache()
    cache.validate(directory)
    before = path.stat()
    path.write_text(path.read_text().replace('1.0.0', '9.0.0'))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert after.st_size == before.st_size and after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns
    with pytest.raises(ValueError, match='Unsupported Context Intelligence'):
        cache.validate(directory)
    assert not cache._validated


def test_replacement_deletion_recreation_revalidates(metadata):
    directory, path = metadata
    cache = CaptureMetadataCache()
    valid = path.read_text()
    cache.validate(directory)
    replacement = path.with_suffix('.replacement')
    replacement.write_text(valid.replace('1.0.0', '9.0.0'))
    replacement.replace(path)
    with pytest.raises(ValueError):
        cache.validate(directory)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        cache.validate(directory)
    assert not cache._validated
    path.write_text(valid)
    cache.validate(directory)
    assert str(path) in cache._validated


@pytest.mark.parametrize('parent_link', [False, True])
def test_symlink_redirection_revalidates(metadata, tmp_path, parent_link):
    directory, path = metadata
    other = tmp_path / 'other'
    other.mkdir()
    other_path = other / 'metadata.json'
    other_path.write_text(path.read_text().replace('1.0.0', '9.0.0'))
    cache = CaptureMetadataCache()
    if parent_link:
        link = tmp_path / 'link'
        link.symlink_to(directory, target_is_directory=True)
        cache.validate(link)
        link.unlink()
        link.symlink_to(other, target_is_directory=True)
        target = link
    else:
        original = directory / 'original.json'
        path.rename(original)
        path.symlink_to(original)
        cache.validate(directory)
        path.unlink()
        path.symlink_to(other_path)
        target = directory
    with pytest.raises(ValueError):
        cache.validate(target)
    assert not cache._validated


def test_permission_and_stat_failure_clear_success_before_recovery(metadata, monkeypatch):
    directory, path = metadata
    cache = CaptureMetadataCache()
    cache.validate(directory)
    original_open, original_stat = Path.open, Path.stat
    path.chmod(0o400)  # Real mode change; simulated denial works under root CI too.
    def deny_open(self, *args, **kwargs):
        if self == path:
            raise PermissionError('metadata read denied')
        return original_open(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', deny_open)
        for _ in range(2):
            with pytest.raises(PermissionError, match='metadata read denied'):
                cache.validate(directory)
            assert not cache._validated
    cache.validate(directory)
    def deny_stat(self, *args, **kwargs):
        if self == path:
            raise PermissionError('metadata stat denied')
        return original_stat(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'stat', deny_stat)
        with pytest.raises(PermissionError, match='metadata stat denied'):
            cache.validate(directory)
        assert not cache._validated
    cache.validate(directory)
    path.chmod(0o600)


@pytest.mark.parametrize('disappear', [False, True])
def test_validation_race_does_not_cache_or_change_success(metadata, monkeypatch, disappear):
    directory, path = metadata
    cache = CaptureMetadataCache()
    original = capture_index.validate_capture
    def change_after_read(directory):
        original(directory)
        if disappear:
            path.unlink()
        else:
            path.write_text(path.read_text().replace('1.0.0', '9.0.0'))
    with monkeypatch.context() as patch:
        patch.setattr(capture_index, 'validate_capture', change_after_read)
        cache.validate(directory)  # Original read succeeded; only admission fails.
    assert not cache._validated
    with pytest.raises(FileNotFoundError if disappear else ValueError):
        cache.validate(directory)


def test_nonregular_metadata_never_reuses_or_caches_validation(metadata, monkeypatch):
    directory, path = metadata
    cache = CaptureMetadataCache()
    cache.validate(directory)
    original_stat, original_validate = Path.stat, capture_index.validate_capture
    calls = []
    def nonregular_stat(self, *args, **kwargs):
        info = original_stat(self, *args, **kwargs)
        if self != path:
            return info
        names = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        return SimpleNamespace(**{key: getattr(info, key) for key in names}, st_mode=stat.S_IFIFO | 0o600)
    def validate(directory):
        calls.append(directory)
        return original_validate(directory)
    monkeypatch.setattr(Path, 'stat', nonregular_stat)
    monkeypatch.setattr(capture_index, 'validate_capture', validate)
    cache.validate(directory)
    cache.validate(directory)
    assert calls == [directory, directory] and not cache._validated


def test_bounded_eviction_keeps_recent_use_and_revalidates_old_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(CaptureMetadataCache, 'MAX_ENTRIES', 2)
    original = capture_index.validate_capture
    calls = []
    def validate(directory):
        calls.append(directory)
        return original(directory)
    monkeypatch.setattr(capture_index, 'validate_capture', validate)
    directories = [tmp_path / str(number) for number in range(3)]
    for directory in directories:
        directory.mkdir()
        (directory / 'metadata.json').write_text(json.dumps({'format': 'context-intelligence', 'version': '1.0.0'}))
    cache = CaptureMetadataCache()
    for index in (0, 1, 0, 2, 0, 1):
        cache.validate(directories[index])
        assert len(cache._validated) <= 2
    assert calls == [directories[index] for index in (0, 1, 2, 1)]
