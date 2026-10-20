"""Passive availability and precise source-policy provenance."""
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import managed_incus_updates as updates
from amplifier_web import incus_engine_probe as probe
from amplifier_web import test_environment_setup as private


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = SimpleNamespace(data_dir=tmp_path, lock=asyncio.Lock())
    monkeypatch.setattr(updates.runners, 'store', lambda app: {'version': 1, 'runners': []})
    return app


def test_no_runner_inventory_does_not_create_state_or_import_native_controller(app, monkeypatch):
    monkeypatch.setattr(updates, 'inspect', lambda *args: pytest.fail('active inspection during inventory'))
    assert updates.update_sources(app) == []
    assert list(app.data_dir.iterdir()) == []


def test_native_windows_import_and_empty_inventory_are_passive(tmp_path):
    script = '''import builtins,os,sys
from pathlib import Path
from types import SimpleNamespace
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name == 'pwd' or name.endswith('managed_incus_target') or name.endswith('test_runner_controller'):
  raise AssertionError('native target imported: '+name)
 return original(name,*args,**kwargs)
builtins.__import__=guarded
for name in ('getuid','O_NOFOLLOW','O_DIRECTORY'):
 if hasattr(os,name): delattr(os,name)
from amplifier_web import managed_incus_updates as updates
assert updates.update_sources(SimpleNamespace(data_dir=Path(sys.argv[1]))) == []
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


async def test_unsupported_platform_discloses_without_reading_or_probing_config(app, monkeypatch):
    (app.data_dir / 'test-environments.json').write_text('not read on unsupported platform')
    monkeypatch.setattr(updates, 'supported_private_files', lambda: False)
    rows = updates.update_sources(app)
    assert len(rows) == 1 and rows[0]['eligible'] is False
    assert 'not supported' in rows[0]['detail']
    await updates.check_source(app, rows[0])
    assert list(app.data_dir.iterdir()) == [app.data_dir / 'test-environments.json']


def test_actual_completed_setup_filename_recovers_policy_but_pin_and_custom_remain_protected(tmp_path, monkeypatch):
    import pwd
    monkeypatch.setattr(pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_dir=str(tmp_path)))
    config = {'executable': '/owned/engine', 'packageSha256': 'a' * 64, 'maxRunning': 4}
    setup = tmp_path / 'test-runner-setup'; setup.mkdir(mode=0o700)
    path = setup / ('a' * 32 + '.json')
    receipt = {'id': 'a' * 32, 'state': 'completed', 'candidateConfiguration': {'incus': config},
               'priorConfiguration': None, 'proof': {'probeRemoved': True}}
    private.write_json(path, receipt)
    settings = tmp_path / 'test-environments.json'
    assert probe.policy(settings, config, probe.LEGACY_COMMIT)['ref'] == 'main'
    changed = {**config, 'maxRunning': 8}
    assert probe.policy(settings, changed, probe.LEGACY_COMMIT)['ref'] == 'main'
    assert probe.policy(settings, {**changed, 'executable': '/other/engine'}, probe.LEGACY_COMMIT) is None
    receipt['engineSourcePolicy'] = {'version': 1, 'repository': probe.REPOSITORY, 'ref': probe.LEGACY_COMMIT}
    private.write_json(path, receipt)
    assert probe.policy(settings, config, probe.LEGACY_COMMIT)['ref'] == probe.LEGACY_COMMIT
    del receipt['priorConfiguration']; private.write_json(path, receipt)
    assert probe.policy(settings, config, probe.LEGACY_COMMIT) is None


def test_carried_engine_policy_survives_only_mutable_capacity_changes(tmp_path, monkeypatch):
    import pwd
    monkeypatch.setattr(pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_dir=str(tmp_path)))
    config = {'executable': '/owned/engine', 'packageSha256': 'a' * 64, 'maxRunning': 4}
    root = tmp_path / '.local/state/amplifier-test-runners/engine-updates'; root.mkdir(parents=True, mode=0o700)
    policy = {'version': 1, 'repository': probe.REPOSITORY, 'ref': 'main'}
    private.write_json(root / ('selection-' + probe.digest(config) + '.json'),
                       {'configuration': config, 'revision': 'b' * 40, 'policy': policy})
    assert probe.policy(tmp_path / 'test-environments.json', {**config, 'minFreeBytes': 123}, 'b' * 40) == policy
    assert probe.policy(tmp_path / 'test-environments.json', {**config, 'image': 'other'}, 'b' * 40) is None


async def test_source_check_is_target_verified_and_explicit_pin_not_offered(app, monkeypatch):
    config = {'enabled': True, 'backend': 'incus', 'incus': {'executable': '/owned/engine'}}
    private.write_json(app.data_dir / 'test-environments.json', config)
    row = updates.update_sources(app)[0]
    monkeypatch.setattr(updates, 'inspect', lambda *args: {'configuration': config['incus'], 'revision': 'b' * 40,
        'policy': {'version': 1, 'repository': probe.REPOSITORY, 'ref': 'b' * 40}})
    await updates.check_source(app, row)
    assert row['status'] == 'pinned' and not row['eligible']
    assert not (app.data_dir / 'managed-incus-updates').exists()


def test_distinct_app_homes_and_target_config_paths_have_distinct_stable_intents(app, tmp_path):
    config = {'enabled': True, 'backend': 'incus', 'incus': {'executable': '/owned/engine'}}
    private.write_json(app.data_dir / 'test-environments.json', config)
    other = SimpleNamespace(data_dir=tmp_path / 'other-app')
    other.data_dir.mkdir()
    private.write_json(other.data_dir / 'test-environments.json', config)
    first = {**updates.update_sources(app)[0], 'latest': 'a' * 40}
    again = {**updates.update_sources(app)[0], 'latest': 'a' * 40}
    second = {**updates.update_sources(other)[0], 'latest': 'a' * 40}
    assert first['runnerSelection'] == second['runnerSelection']
    assert updates.intent_id(first) == updates.intent_id(again)
    assert updates.intent_id(first) != updates.intent_id(second)
    assert updates.intent_id(first) != updates.intent_id({**first, 'runnerConfigPath': '/another/app/config.json'})
    assert not (app.data_dir / 'managed-incus-updates').exists()


def test_snapshot_archive_is_outside_fixed_source_checkout(app, monkeypatch):
    from amplifier_web import task_environments as environments
    monkeypatch.setattr(environments, 'configuration', lambda app: ({'python': sys.executable}, 'ready', None))
    calls = []
    def export(config, destination, source):
        checkout = Path(source)
        assert (checkout / 'input.txt').read_text() == 'runner-input-v1'
        assert (checkout / 'uncommitted.txt').read_text() == 'working-tree-v1'
        archive = destination / 'source.tar'
        assert not archive.is_relative_to(checkout), 'DTU rejects destination inside source'
        archive.write_bytes(b'checked snapshot')
        calls.append((source, str(archive)))
        return {'archive_sha256': 'fixed'}
    monkeypatch.setattr(updates.runners, 'export_source', export)
    path = updates.path_for(app, 'd' * 32)
    snapshot, content = updates.source_snapshot(app, path)
    import base64
    assert snapshot == {'archive_sha256': 'fixed'}
    assert base64.b64decode(content) == b'checked snapshot'
    assert len(calls) == 1
