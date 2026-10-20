"""Immutable host-qualified cleanup configuration, independent of live selection."""
import json
import os
from types import SimpleNamespace

import pytest

from amplifier_web import test_environment_configs as configs
from amplifier_web import task_environments as owner


def config():
    return {'python': '/managed/python', 'dockerHost': 'unix:///owned/docker.sock',
            'image': owner.APPROVED_IMAGE, 'enabled': True, 'retentionMode': 'files'}


def test_private_content_addressed_snapshot_is_stable_and_durable(tmp_path):
    value = config()
    identity = configs.retain(tmp_path, value)
    target = tmp_path / 'workspace-resources' / 'backend-configs' / (identity + '.json')
    assert target.stat().st_mode & 0o777 == 0o400
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert configs.retain(tmp_path, value) == identity
    assert json.loads(target.read_text()) == {'version': 1, 'configuration': value}
    assert configs.key({k: v for k, v in value.items() if k != 'retentionMode'}) != identity


@pytest.mark.parametrize('change', ['tamper', 'symlink', 'hardlink', 'public-file', 'public-directory'])
def test_replaced_or_unprotected_snapshot_never_supplies_an_executable(tmp_path, change):
    value = config()
    identity = configs.retain(tmp_path, value)
    target = tmp_path / 'workspace-resources' / 'backend-configs' / (identity + '.json')
    if change == 'tamper':
        target.chmod(0o600)
        target.write_text(json.dumps({'version': 1, 'configuration': {**value, 'python': '/other/python'}}))
    elif change == 'symlink':
        target.unlink()
        destination = tmp_path / 'foreign'
        destination.write_text('{}')
        target.symlink_to(destination)
    elif change == 'hardlink':
        os.link(target, tmp_path / 'alias')
    elif change == 'public-file':
        target.chmod(0o644)
    else:
        target.parent.chmod(0o755)
    with pytest.raises((OSError, ValueError)):
        configs.resolve(SimpleNamespace(data_dir=tmp_path), {'backendKey': identity, 'backendConfigRetained': True}, value)


def test_untrusted_fields_and_partial_publication_fail_closed(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='qualified'):
        configs.retain(tmp_path, {**config(), 'apiKey': 'do-not-store'})
    def interrupted(*args, **kwargs):
        raise OSError('interrupted before immutable publication')
    monkeypatch.setattr(os, 'link', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        configs.retain(tmp_path, config())
    assert not list((tmp_path / 'workspace-resources' / 'backend-configs').iterdir())


def test_changed_active_configuration_requalifies_exact_retained_installation(tmp_path, monkeypatch):
    value = config()
    identity = configs.retain(tmp_path, value)
    row = {'backendKey': identity, 'backendConfigRetained': True}
    monkeypatch.setattr(owner, 'SUPPORTED_HOST', True)
    seen = []
    def qualify(app, candidate):
        seen.append(candidate)
        return value, 'ready', 'qualified'
    monkeypatch.setattr(owner, 'validate_configuration_value', qualify)
    assert configs.resolve(SimpleNamespace(data_dir=tmp_path), row, {**value, 'python': '/new/python'}) == value
    assert seen == [{k: v for k, v in value.items() if k != 'python'}]
    monkeypatch.setattr(owner, 'validate_configuration_value', lambda *a: ({**value, 'python': '/replacement/python'}, 'ready', 'changed'))
    with pytest.raises(ValueError, match='changed'):
        configs.resolve(SimpleNamespace(data_dir=tmp_path), row, None)


def test_legacy_row_requires_exact_current_configuration_without_guessing(tmp_path):
    value = config()
    row = {'backendKey': configs.key(value)}
    app = SimpleNamespace(data_dir=tmp_path)
    assert configs.resolve(app, row, value) == value
    with pytest.raises(ValueError, match='older environment'):
        configs.resolve(app, row, {**value, 'python': '/new/python'})
    assert not (tmp_path / 'workspace-resources').exists()


def test_new_incus_generation_does_not_rebind_existing_owner(tmp_path, monkeypatch):
    from amplifier_web.incus_environment_worker import QUALIFIED_PACKAGES
    old = {'enabled': True, 'backend': 'incus', 'python': '/snapshot/python',
           'incus': {'executable': '/owned/old/adapter', 'packageSha256': QUALIFIED_PACKAGES['0.4.1']}}
    current = {**old, 'incus': {'executable': '/owned/new/adapter', 'packageSha256': QUALIFIED_PACKAGES['0.4.2']}}
    identity = configs.retain(tmp_path, old)
    seen = []
    def qualify(app, candidate):
        seen.append(candidate)
        return old, 'ready', 'qualified'
    monkeypatch.setattr(owner, 'validate_configuration_value', qualify)
    row = {'backendKey': identity, 'backendConfigRetained': True}
    assert configs.resolve(SimpleNamespace(data_dir=tmp_path), row, current) == old
    assert seen[0]['incus'] == old['incus']
    assert configs.retain(tmp_path, current) != identity


def test_snapshot_refresh_retains_exact_qualified_owner_and_rejects_rebinding(tmp_path):
    old_commit = '8f644b3d8d8b89f0bb3ae9853bb531017dc98a04'
    assert owner.DTU_COMMIT != old_commit
    app = SimpleNamespace(data_dir=tmp_path, smart_tools=SimpleNamespace(state={'installations': []}))

    def installed(identity, commit):
        python = tmp_path / 'smart-tools' / 'installs' / identity / 'venv' / 'bin' / 'python'
        python.parent.mkdir(parents=True)
        python.write_text('managed interpreter fixture')
        app.smart_tools.state['installations'].append({
            'id': identity, 'commit': commit, 'repository': owner.REPOSITORY,
            'status': 'installed', 'extras': [], 'python': str(python),
        })
        request = {'enabled': True, 'installationId': identity, 'commit': commit,
                   'dockerHost': 'unix:///owned/docker.sock'}
        value, status, _ = owner.validate_configuration_value(app, request)
        assert status == 'ready'
        return value

    old = installed('a' * 24, old_commit)
    current = installed('b' * 24, owner.DTU_COMMIT)
    identity = configs.retain(tmp_path, old)
    row = {'backendKey': identity, 'backendConfigRetained': True}
    assert configs.resolve(app, row, current) == old
    assert configs.resolve(app, row, None) == old  # Cold reopen, no selected host.
    assert configs.retain(tmp_path, current) != identity

    def request(value):
        return {key: value[key] for key in ('enabled', 'installationId', 'commit', 'dockerHost')}

    # Qualification is a pair, not interchangeable approved commit names.
    with pytest.raises(ValueError, match='qualified DTU core'):
        owner.validate_configuration_value(app, {**request(old), 'commit': owner.DTU_COMMIT})
    with pytest.raises(ValueError, match='qualified revision'):
        owner.validate_configuration_value(app, {**request(old), 'commit': 'c' * 40})
    app.smart_tools.state['installations'][0]['commit'] = owner.DTU_COMMIT
    with pytest.raises(ValueError, match='qualified DTU core'):
        configs.resolve(app, row, current)
