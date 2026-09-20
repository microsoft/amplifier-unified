"""A worker lock must advance through Updates and survive a rollback unchanged."""
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_environment as environments
from amplifier_web.runtime import RuntimeManager
from amplifier_web.updates import active_release


def git(path, *args):
    return subprocess.check_output(['git', *args], cwd=path, text=True, stderr=subprocess.DEVNULL).strip()


class Diagnostics:
    async def run(self, name, function, *args, **kwargs):
        return await function(*args, **kwargs)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    repo = tmp_path / 'remote'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    (repo / 'pyproject.toml').write_text('[project]\nname="fixture-runtime"\nversion="0.1.0"\n')
    (repo / 'marker.txt').write_text('first')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'first')
    old = git(repo, 'rev-parse', 'HEAD')
    manifest = tmp_path / 'pyproject.toml'
    manifest.write_text('[project]\nname="fixture-worker"\nversion="0.1.0"\n'
                        'requires-python=">=3.13"\ndependencies=["fixture-runtime"]\n'
                        '[tool.uv.sources]\nfixture-runtime={git="' + repo.as_uri() + '",branch="main"}\n')
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    home = tmp_path / 'app'
    current = environments.prepare_project(home)
    uv = shutil.which('uv')
    if not uv:
        pytest.skip('uv is required for the local Git lock integration check')
    subprocess.run([uv, 'lock', '--project', str(current), '--python', '3.13'], check=True, capture_output=True)
    (repo / 'marker.txt').write_text('second')
    git(repo, 'commit', '-am', 'second')
    new = git(repo, 'rev-parse', 'HEAD')
    row = {**environments.inventory(home)[0], 'status': 'update', 'latest': new}
    return SimpleNamespace(home=home, diagnostics=Diagnostics()), current, row, old, new, repo


def test_inventory_is_read_only_and_branches_are_not_pins(environment):
    manager, current, row, old, new, repo = environment
    before = (current / 'uv.lock').read_bytes()
    assert row['current'] == old and row['latest'] == new
    assert row['ref'] == 'main' and row['eligible']
    assert environments.inventory(manager.home)[0]['current'] == old
    assert (current / 'uv.lock').read_bytes() == before


async def test_update_refreshes_worker_lock_and_rollback_reuses_old_environment(environment):
    manager, current, row, old, new, repo = environment
    generation = 'a' * 32
    release = manager.home / 'updates/releases' / generation
    release.mkdir(parents=True)
    project = await environments.stage(manager, generation, [row])
    assert environments.locked_sources(project)['fixture-runtime'].fragment == new
    assert environments.locked_sources(current)['fixture-runtime'].fragment == old
    pointer = manager.home / 'updates/active.json'
    pointer.write_text(json.dumps({'current': generation, 'previous': None}))
    command = RuntimeManager()._command(home=manager.home)
    assert Path(command[command.index('--project') + 1]) == project
    pointer.write_text(json.dumps({'current': None, 'previous': generation}))
    command = RuntimeManager()._command(home=manager.home)
    assert Path(command[command.index('--project') + 1]) == current
    assert environments.inventory(manager.home)[0]['current'] == old
    # Revalidation must reuse its recorded lock even after the remote moves.
    (repo / 'marker.txt').write_text('third')
    git(repo, 'commit', '-am', 'third')
    await environments.stage(manager, generation, [{**row, 'latest': git(repo, 'rev-parse', 'HEAD')}])
    assert environments.locked_sources(project)['fixture-runtime'].fragment == new


async def test_app_manifest_change_cannot_rewrite_rollback_receipt(environment):
    manager, current, row, old, new, repo = environment
    generation = 'c' * 32
    release = manager.home / 'updates/releases' / generation
    release.mkdir(parents=True)
    project = await environments.stage(manager, generation, [row])
    recorded = (release / 'runtime.lock').read_bytes()
    manifest = (release / 'runtime.toml').read_bytes()
    # A new application declares a different branch/dependency contract.
    git(repo, 'checkout', '-b', 'next')
    (repo / 'marker.txt').write_text('next branch')
    git(repo, 'commit', '-am', 'next')
    path = environments.manifest_path()
    path.write_text(path.read_text().replace('branch="main"', 'branch="next"'))
    await environments.stage(manager, generation, [])
    assert (release / 'runtime.lock').read_bytes() == recorded
    assert (release / 'runtime.toml').read_bytes() == manifest
    assert environments.project_path(manager.home, generation) == project
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    assert environments.inventory(manager.home)[0]['kind'] == 'runtime environment'
    assert environments.inventory(manager.home)[0]['status'] == 'update'
    # Deleting a disposable worker cache cannot discard its retained version.
    shutil.rmtree(project)
    assert environments.inventory(manager.home)[1]['current'] == new
    command = RuntimeManager()._command(home=manager.home)
    assert Path(command[command.index('--project') + 1]) == project
    assert '--locked' in command
    assert (project / 'uv.lock').read_bytes() == recorded
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': None, 'previous': generation}))
    command = RuntimeManager()._command(home=manager.home)
    assert Path(command[command.index('--project') + 1]) == current
    assert environments.inventory(manager.home)[1]['current'] == old


async def test_branch_movement_after_check_does_not_activate(environment):
    manager, current, row, old, new, repo = environment
    generation = 'b' * 32
    (manager.home / 'updates/releases' / generation).mkdir(parents=True)
    (repo / 'marker.txt').write_text('third')
    git(repo, 'commit', '-am', 'third')
    with pytest.raises(ValueError, match='branch moved'):
        await environments.stage(manager, generation, [row])
    assert not active_release(manager.home)
    assert environments.locked_sources(current)['fixture-runtime'].fragment == old


def test_cold_inventory_does_not_prepare_worker(tmp_path):
    assert environments.inventory(tmp_path) == []
    assert not (tmp_path / 'runtime').exists()


async def test_update_manager_installs_runtime_only_and_manifest_updates(environment, monkeypatch):
    from amplifier_web import app_updates
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    manager, current, row, old, new, repo = environment
    closed = []
    class Runtime:
        async def close(self):
            closed.append(True)
    async def app_check():
        return {'id': 'application', 'status': 'current'}
    monkeypatch.setattr(app_updates, 'check', app_check)
    app = AppService(manager.home, Runtime(), workspace=manager.home.parent)
    updater = UpdateManager(app)
    async def validate(stage, release):
        await environments.stage(updater, release, [r for r in updater.inventory if r.get('status') == 'update'])
    updater.validate = validate
    try:
        await updater.check()
        assert any(r['id'] == 'runtime:fixture-runtime' and r['status'] == 'update' for r in updater.inventory)
        await updater.install()
        assert app.state['updates']['phase'] == 'installed'
        assert environments.inventory(manager.home)[0]['current'] == new
        assert closed
        # A changed application manifest is a local update, not a Git URL.
        path = environments.manifest_path()
        path.write_text(path.read_text().replace('version="0.1.0"', 'version="0.1.1"'))
        await updater.check()
        assert any(r['id'] == 'runtime:environment' and r['status'] == 'update' for r in updater.inventory)
        await updater.install()
        assert app.state['updates']['phase'] == 'installed'
        await updater.rollback()
        assert any(r['kind'] == 'runtime environment' for r in environments.inventory(manager.home))
    finally:
        await app.close()
