"""Policy replacement preserves a whole private file, including failed saves."""
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from amplifier_web import private_policy_file
from amplifier_web import test_capacity_settings, test_execution_settings


@pytest.fixture(params=['capacity', 'execution'])
def target(request, tmp_path):
    if request.param == 'capacity':
        module = test_capacity_settings
        policy = {'version': 1, 'defaults': {'maxRunning': 3}, 'runners': {}}
        updated = {**policy, 'defaults': {'maxRunning': 4}}
    else:
        module = test_execution_settings
        policy = {'version': 1, 'queue': {'maxPending': 3}, 'profiles': {}, 'concurrency': {}}
        updated = {**policy, 'queue': {'maxPending': 4}}
    app = SimpleNamespace(data_dir=tmp_path)
    path = tmp_path / ('test-' + request.param + '.json')
    module.write(app, policy)
    return SimpleNamespace(module=module, app=app, path=path, policy=policy, updated=updated,
        kind=request.param, before=path.read_bytes())


def test_atomic_replacement_is_private_and_synced(target, monkeypatch):
    events = []
    original_sync, original_replace = os.fsync, os.replace
    expected = json.dumps(target.updated, sort_keys=True).encode()

    def sync(descriptor):
        events.append('directory' if stat.S_ISDIR(os.fstat(descriptor).st_mode) else 'file')
        original_sync(descriptor)

    def replace(source, destination):
        assert target.path.read_bytes() == target.before
        assert Path(source).read_bytes() == expected
        if os.name != 'nt':
            assert stat.S_IMODE(Path(source).stat().st_mode) == 0o600
        events.append('replace')
        original_replace(source, destination)

    monkeypatch.setattr(private_policy_file.os, 'fsync', sync)
    monkeypatch.setattr(private_policy_file.os, 'replace', replace)
    target.module.write(target.app, target.updated)
    assert target.path.read_bytes() == expected
    assert target.module.load(target.app) == target.updated
    assert events == ['file', 'replace'] + (['directory'] if hasattr(os, 'O_DIRECTORY') else [])
    assert list(target.path.parent.iterdir()) == [target.path]


@pytest.mark.parametrize('failure', ['file-sync', 'replace', 'directory-sync'])
def test_failed_save_preserves_whole_policy_and_cleans_temporary(target, monkeypatch, failure):
    if failure == 'directory-sync' and not hasattr(os, 'O_DIRECTORY'):
        pytest.skip('Directory fsync is unavailable on this platform')
    original_sync, original_replace = os.fsync, os.replace
    descriptors = []

    def sync(descriptor):
        descriptors.append(descriptor)
        stage = 'directory-sync' if stat.S_ISDIR(os.fstat(descriptor).st_mode) else 'file-sync'
        if failure == stage:
            raise OSError('injected ' + failure)
        original_sync(descriptor)

    def replace(source, destination):
        if failure == 'replace':
            raise OSError('injected replace')
        original_replace(source, destination)

    monkeypatch.setattr(private_policy_file.os, 'fsync', sync)
    monkeypatch.setattr(private_policy_file.os, 'replace', replace)
    with pytest.raises(OSError, match='injected ' + failure):
        target.module.write(target.app, target.updated)
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    # A failed directory sync reports uncertainty after the atomic replacement;
    # it must not pretend to roll back or leave a partial new policy behind.
    expected = json.dumps(target.updated, sort_keys=True).encode() if failure == 'directory-sync' else target.before
    assert target.path.read_bytes() == expected
    assert list(target.path.parent.iterdir()) == [target.path]


def test_oversize_refusal_keeps_prior_policy_and_domain_error(target):
    oversized = {**target.policy, 'padding': 'x' * 65536}
    with pytest.raises(ValueError) as error:
        target.module.write(target.app, oversized)
    assert str(error.value) == 'The saved test ' + target.kind + ' settings exceed their supported bound.'
    assert target.path.read_bytes() == target.before
    assert list(target.path.parent.iterdir()) == [target.path]
