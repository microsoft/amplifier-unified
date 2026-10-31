"""Target-side policy changes stay separate from registered owner identity."""
import concurrent.futures
import copy
import json
import os
from pathlib import Path

import pytest

from amplifier_web import test_runner_backend_policy as policy


@pytest.fixture
def target(tmp_path):
    root = tmp_path / 'controller'
    root.mkdir(mode=0o700)
    identity = {'backend': 'incus', 'daemon': 'observed-daemon', 'project': 'tests'}
    config = {'project': 'tests', 'maxRunning': 24, 'maxInstances': 32,
              'minFreeBytes': 10 * 1024**3, 'python': '/fixed/qualified/python',
              'retained': {'identity': 'unchanged'}}
    return root, identity, config


def test_defaults_and_policy_survive_new_controller_generation_without_changing_identity(target):
    root, identity, config = target
    original = copy.deepcopy(config)
    first = policy.snapshot(*target)
    assert first['limits'] == {}
    assert first['effective']['maxRunning'] == 24
    saved = policy.save(*target, first['revision'], {'maxRunning': 30, 'minFreeBytes': 0})
    assert policy.snapshot(*target) == saved
    replacement = {**config, 'python': '/new/qualified/python'}
    assert policy.snapshot(root, identity, replacement)['limits'] == saved['limits']
    applied = policy.apply(root, identity, replacement)
    assert applied == {**replacement, 'maxRunning': 30, 'minFreeBytes': 0}
    assert config == original
    assert policy._read(next(root.glob('*.json')), policy._binding(identity))['generation'] == 1
    assert next(root.glob('*.json')).stat().st_mode & 0o777 == 0o600


def test_zero_disables_limits_and_empty_overrides_restore_configured_defaults(target):
    current = policy.snapshot(*target)
    saved = policy.save(*target, current['revision'], dict.fromkeys(policy.DEFAULTS, 0))
    assert saved['effective'] == dict.fromkeys(policy.DEFAULTS, 0)
    reset = policy.save(*target, saved['revision'], {})
    assert reset['effective'] == current['effective']
    assert reset['revision'] != current['revision']
    with pytest.raises(ValueError, match='changed'):
        policy.save(*target, current['revision'], {'maxInstances': 1})


def test_defaults_change_requires_refresh_even_when_policy_unchanged(target):
    root, identity, config = target
    current = policy.snapshot(*target)
    with pytest.raises(ValueError, match='changed'):
        policy.save(root, identity, {**config, 'maxInstances': 40}, current['revision'], {'maxRunning': 5})


def test_daemon_and_project_budgets_are_independent(target):
    root, identity, config = target
    current = policy.snapshot(*target)
    policy.save(*target, current['revision'], {'maxRunning': 1})
    for key in ('daemon', 'project'):
        other = {**identity, key: 'different'}
        assert policy.snapshot(root, other, config)['limits'] == {}


def test_simultaneous_cas_allows_one_writer(target):
    revision = policy.snapshot(*target)['revision']
    def save(limit):
        try:
            return policy.save(*target, revision, {'maxRunning': limit})
        except ValueError:
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, [7, 8]))
    assert sum(result is not None for result in results) == 1
    assert policy.snapshot(*target)['effective']['maxRunning'] in (7, 8)


def test_lost_save_acknowledgement_is_readable_without_replay(target, monkeypatch):
    original = policy._write
    def lost(path, value):
        original(path, value)
        raise OSError('reply lost after publication')
    monkeypatch.setattr(policy, '_write', lost)
    current = policy.snapshot(*target)
    with pytest.raises(OSError, match='reply lost'):
        policy.save(*target, current['revision'], {'maxInstances': 50})
    assert policy.snapshot(*target)['limits'] == {'maxInstances': 50}
    with pytest.raises(ValueError, match='changed'):
        policy.save(*target, current['revision'], {'maxInstances': 50})


@pytest.mark.parametrize('limits', [{'unknown': 1}, {'maxRunning': True}, {'maxInstances': -1},
                                   {'minFreeBytes': 1.5}, {'maxRunning': policy.MAXIMUM + 1}, []])
def test_invalid_values_do_not_publish(target, limits):
    current = policy.snapshot(*target)
    with pytest.raises(ValueError):
        policy.save(*target, current['revision'], limits)
    assert policy.snapshot(*target) == current
    assert not list(target[0].glob('*.json'))


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'public', 'fifo', 'oversize', 'wrong-binding'])
def test_unsafe_or_mismatched_records_fail_closed(target, kind, tmp_path):
    root, identity, _ = target
    current = policy.snapshot(*target)
    policy.save(*target, current['revision'], {'maxRunning': 4})
    path = next(root.glob('*.json'))
    if kind == 'symlink':
        original = path.rename(tmp_path / 'elsewhere')
        path.symlink_to(original)
    elif kind == 'hardlink':
        os.link(path, tmp_path / 'second-name')
    elif kind == 'public':
        path.chmod(0o644)
    elif kind == 'fifo':
        path.unlink()
        os.mkfifo(path, 0o600)
    elif kind == 'oversize':
        path.write_bytes(b' ' * (policy.MAX_RECORD + 1))
    else:
        value = json.loads(path.read_text())
        value['binding']['backend']['project'] = 'not-this-project'
        path.write_text(json.dumps(value))
    with pytest.raises((ValueError, OSError)):
        policy.snapshot(*target)


def test_linked_controller_storage_is_rejected(target, tmp_path):
    root, identity, config = target
    linked = tmp_path / 'alias'
    linked.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='links'):
        policy.snapshot(linked, identity, config)


def test_lowering_limits_changes_only_policy_files(target):
    root, _, _ = target
    owned = root / 'owner-active'
    owned.mkdir(mode=0o700)
    evidence = owned / 'receipt.json'
    evidence.write_text('{"running":true}')
    before = evidence.read_bytes()
    current = policy.snapshot(*target)
    policy.save(*target, current['revision'], {'maxRunning': 1, 'maxInstances': 1})
    assert evidence.read_bytes() == before


def test_unconfigured_target_defaults_to_resource_admission_not_instance_counts(target):
    root, identity, _ = target
    config = {'project': 'tests'}
    default = policy.snapshot(root, identity, config)
    assert default['effective'] == {'maxRunning': 0, 'maxInstances': 0, 'minFreeBytes': 10*1024**3}
    assert policy.apply(root, identity, config)['maxRunning'] == 0
    assert policy.apply(root, identity, config)['maxInstances'] == 0
