"""Fresh dependency preparation must be isolated from frozen generation replay."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_environment, runtime_qualification, updates
from amplifier_web.updates import UpdateManager


async def test_fresh_probe_freezes_then_uses_an_ordinary_resolver(tmp_path, monkeypatch):
    calls = []
    release = '2' * 32
    receipt = runtime_environment.receipt_directory(tmp_path, release)
    receipt.mkdir(parents=True)
    first, frozen = tmp_path / 'first', tmp_path / 'frozen'
    async def stage(manager, generation, candidates, *, finalize):
        assert not finalize
        calls.append('stage')
        return first
    async def freeze(manager, generation, project):
        assert project == first
        calls.append('freeze')
        (receipt / 'runtime-installed.json').write_text('[]')
        return frozen
    def overrides(project, target):
        calls.append(('overrides', project))
        return target
    def verify(project, target):
        assert project == frozen and target == receipt
        calls.append('verify')
    class Diagnostics:
        async def run(self, phase, function, *command, **kwargs):
            assert command[command.index('--project') + 1] in {str(first), str(frozen)}
            calls.append(('probe', '--refresh-dependencies' in command))
    monkeypatch.setattr(runtime_environment, 'stage', stage)
    monkeypatch.setattr(runtime_qualification, 'freeze', freeze)
    monkeypatch.setattr(runtime_qualification, 'lock_overrides', overrides)
    monkeypatch.setattr(runtime_qualification, 'verify_recorded', verify)
    manager = SimpleNamespace(home=tmp_path, inventory=[], diagnostics=Diagnostics(),
        service=SimpleNamespace(get_state=lambda: {'sessions': [], 'settings': {'workspace': str(tmp_path), 'bundle': 'work'}}))
    await UpdateManager.validate(manager, receipt, release)
    assert calls == ['stage', ('overrides', first), ('probe', True), 'freeze', ('overrides', frozen), ('probe', False), 'verify']


async def test_historical_receipt_never_gets_refresh_or_new_foundation_arguments(tmp_path, monkeypatch):
    release = '3' * 32
    receipt = runtime_environment.receipt_directory(tmp_path, release)
    receipt.mkdir(parents=True)
    (receipt / 'runtime.lock').write_bytes(b'recorded')
    async def stage(manager, generation, candidates, *, finalize):
        assert finalize
        return tmp_path / 'historical'
    class Diagnostics:
        async def run(self, phase, function, *command, **kwargs):
            assert '--refresh-dependencies' not in command
            assert '--install-overrides' not in command
    async def unexpected(*args):
        raise AssertionError('A historical generation was refreshed')
    monkeypatch.setattr(runtime_environment, 'stage', stage)
    monkeypatch.setattr(runtime_qualification, 'freeze', unexpected)
    manager = SimpleNamespace(home=tmp_path, inventory=[], diagnostics=Diagnostics(),
        service=SimpleNamespace(get_state=lambda: {'sessions': [], 'settings': {'workspace': str(tmp_path), 'bundle': 'work'}}))
    await UpdateManager.validate(manager, receipt, release)
    assert (receipt / 'runtime.lock').read_bytes() == b'recorded'


async def test_resume_cannot_enable_refresh(tmp_path):
    from amplifier_web.host.session import prepare_manager
    with pytest.raises(ValueError, match='new isolated qualification'):
        await prepare_manager(tmp_path, resume=True, refresh_dependencies=True)


def test_recorded_graph_rejects_later_distribution_changes(tmp_path, monkeypatch):
    row = {'name': 'amplifier-fixture', 'version': '1.0', 'directUrl': {'url': 'https://example.invalid/fixture',
        'vcs_info': {'vcs': 'git', 'requested_revision': 'main', 'commit_id': 'a' * 40}}}
    (tmp_path / 'runtime-installed.json').write_text(json.dumps([row]))
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda project: [row])
    runtime_qualification.verify_recorded(tmp_path / 'project', tmp_path)
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda project: [{**row, 'version': '2.0'}])
    with pytest.raises(ValueError, match='changed after qualification'):
        runtime_qualification.verify_recorded(tmp_path / 'project', tmp_path)


from test_host_session_resume import mounted_host


async def test_host_forwards_fresh_policy_but_ordinary_preparation_keeps_defaults(mounted_host, tmp_path):
    from amplifier_web.host.session import prepare_manager
    h = mounted_host
    overrides = tmp_path / 'qualified-overrides.txt'
    overrides.write_text('amplifier-core==1.6.1\n')
    await prepare_manager(h.config.workspace, runtime=h.runtime, resume=False,
                          refresh_dependencies=True, install_overrides=overrides)
    policy = h.loaded.prepare.call_args.kwargs
    assert policy['refresh_dependencies'] is True and policy['install_overrides'] == overrides
    h.session.execute.assert_not_called()
    h.runtime.session_id = 'ordinary-new-session'
    await prepare_manager(h.config.workspace, runtime=h.runtime, resume=False)
    policy = h.loaded.prepare.call_args.kwargs
    assert 'refresh_dependencies' not in policy and 'install_overrides' not in policy
    h.session.execute.assert_not_called()


def test_active_generation_uses_qualified_policy_without_overriding_user_choice(tmp_path, monkeypatch):
    release = '6' * 32
    receipt = runtime_environment.receipt_directory(tmp_path, release)
    receipt.mkdir(parents=True)
    (tmp_path / 'updates/active.json').write_text(json.dumps({'current': release}))
    (receipt / 'runtime-installed.json').write_text('[]')
    target = receipt / 'runtime-install-overrides.txt'
    target.write_text('amplifier-core==1.6.1\n')
    (receipt / 'runtime.lock').write_text('version=1\n[[package]]\nname="amplifier-core"\nversion="1.6.1"\nsource={registry="https://pypi.org/simple"}\n')
    runtime_environment.prepare_project(tmp_path, release)
    calls = []
    monkeypatch.setattr(runtime_qualification, 'verify_recorded', lambda project, directory, **policy: calls.append((project, directory)))
    assert runtime_qualification.active_install_overrides(tmp_path) == target
    assert runtime_qualification.active_install_overrides(tmp_path, str(target)) == target
    assert len(calls) == 2
    assert runtime_qualification.active_install_overrides(tmp_path, '/user/explicit-overrides.txt') is None
    assert len(calls) == 2
    target.write_text('amplifier-core==0.0.1\n')
    with pytest.raises(ValueError, match='policy changed'):
        runtime_qualification.active_install_overrides(tmp_path)
    target.unlink()
    with pytest.raises(ValueError, match='policy is missing'):
        runtime_qualification.active_install_overrides(tmp_path)


def test_legacy_worker_generation_has_no_new_install_policy(tmp_path):
    assert runtime_qualification.active_install_overrides(tmp_path) is None
    assert not (tmp_path / 'updates').exists()


def test_active_additions_cannot_replace_any_qualified_dependency(tmp_path, monkeypatch):
    original = {'name': 'amplifier-original', 'version': '1.0'}
    added = {'name': 'amplifier-lazy', 'version': '2.0'}
    (tmp_path / 'runtime-installed.json').write_text(json.dumps([original]))
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda project: [original, added])
    runtime_qualification.verify_recorded(tmp_path, tmp_path, allow_additions=True)
    with pytest.raises(ValueError, match='changed after qualification'):
        runtime_qualification.verify_recorded(tmp_path, tmp_path)
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda project: [{**original, 'version': '1.1'}, added])
    with pytest.raises(ValueError, match='changed after qualification'):
        runtime_qualification.verify_recorded(tmp_path, tmp_path, allow_additions=True)


def test_installer_overrides_preserve_editable_and_local_sources(tmp_path):
    (tmp_path / 'uv.lock').write_text('''version=1
[[package]]
name="amplifier-editable"
version="1.0"
source={editable="../cached-module"}
[[package]]
name="amplifier-local"
version="1.0"
source={directory="../local-module"}
''')
    output = runtime_qualification.lock_overrides(tmp_path, tmp_path / 'overrides.txt').read_text()
    assert '-e ' + (tmp_path.parent / 'cached-module').as_uri() in output
    assert 'amplifier-local @ ' + (tmp_path.parent / 'local-module').as_uri() in output
