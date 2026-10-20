"""Registry Core updates retain exact candidate versions and wheel-only policy."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tomllib
from unittest.mock import AsyncMock

import pytest

from amplifier_web import runtime_environment as environments, runtime_registry_updates as registry
from amplifier_web import runtime_qualification as qualification
from amplifier_web.updates import UpdateManager
from amplifier_web.service import AppService
from test_service import Runtime


BASE = b'''[project]
name="amplifier-web-runtime"
version="1"
dependencies=["amplifier-core>=2.0.2"]
[tool.uv]
no-build-package=["amplifier-core"]
[[tool.uv.index]]
url="https://pypi.org/simple"
default=true
'''


def lock(version='2.0.2', source=None):
    return ('version=1\n[[package]]\nname="amplifier-core"\nversion=' + json.dumps(version) +
            '\nsource=' + environments.toml_value(source or {'registry': registry.INDEX}) + '\n')


@pytest.fixture
def worker(tmp_path, monkeypatch):
    manifest = tmp_path / 'manifest.toml'
    manifest.write_bytes(BASE)
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    home = tmp_path / 'app'
    generation = 'a' * 32
    receipt = environments.receipt_directory(home, generation)
    receipt.mkdir(parents=True)
    for name in ('runtime-base.toml', 'runtime.toml'):
        (receipt / name).write_bytes(BASE)
    (receipt / 'runtime.lock').write_text(lock())
    (home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    project = environments.prepare_project(home, generation)
    graph = [{'name': 'amplifier-core', 'version': '2.0.2'}]
    (receipt / 'runtime-installed.json').write_text(json.dumps(graph))
    monkeypatch.setattr(qualification, 'installed_graph', lambda _: copy.deepcopy(graph))
    return SimpleNamespace(home=home, generation=generation, receipt=receipt, project=project,
                           graph=graph, manifest=manifest)


def core(worker):
    return next(row for row in environments.inventory(worker.home) if row.get('package') == registry.CORE)


def test_app_managed_registry_core_is_eligible_in_included_inventory(worker):
    from amplifier_web.update_sequence import classify
    row = core(worker)
    assert row['eligible'] and row['status'] == 'not_checked'
    assert row['registryManaged'] and row['registry'] == registry.INDEX
    assert row['current'] == '2.0.2' and not row.get('override')
    assert classify(worker.home, [row])[0]['updateTier'] == 'included'


@pytest.mark.parametrize('change', ['fixed', 'upper-bound', 'override', 'constraint', 'git', 'local',
    'extra-pin', 'optional-pin', 'custom-index', 'installed-version', 'recorded-version', 'lock-version', 'lock-git', 'project-edit'])
def test_explicit_or_unexplained_core_policy_is_not_reclassified(worker, change):
    if change in {'fixed', 'upper-bound'}:
        replacement = b'amplifier-core==2.0.2' if change == 'fixed' else b'amplifier-core>=2.0.2,<3'
        (worker.receipt / 'runtime-base.toml').write_bytes(BASE.replace(b'amplifier-core>=2.0.2', replacement))
    elif change in {'override', 'constraint'}:
        policy = BASE.replace(b'[tool.uv]\n', ('[tool.uv]\n' + change + '-dependencies=["amplifier-core==2.0.2"]\n').encode())
        (worker.receipt / 'runtime-base.toml').write_bytes(policy)
    elif change in {'extra-pin', 'optional-pin'}:
        table = 'dependency-groups' if change == 'extra-pin' else 'project.optional-dependencies'
        (worker.receipt / 'runtime-base.toml').write_bytes(BASE + ('\n[' + table + ']\ncustom=["amplifier-core==2.0.2"]\n').encode())
    elif change == 'custom-index':
        (worker.project / 'uv.lock').write_text(lock(source={'registry': 'https://custom.invalid/simple'}))
    elif change == 'installed-version':
        worker.graph[0]['version'] = '2.0.3'
    elif change == 'recorded-version':
        (worker.receipt / 'runtime-installed.json').write_text('[{"name":"amplifier-core","version":"1.0.0"}]')
    elif change == 'lock-version':
        (worker.project / 'uv.lock').write_text(lock('2.0.3'))
    elif change == 'lock-git':
        (worker.project / 'uv.lock').write_text(lock(source={'git': 'https://example.invalid/core?rev=main#' + 'b'*40}))
    elif change == 'project-edit':
        (worker.project / 'pyproject.toml').write_bytes(BASE.replace(b'amplifier-core>=2.0.2', b'amplifier-core==2.0.2'))
    elif change == 'git':
        worker.graph[0]['directUrl'] = {'url': 'https://example.invalid/core', 'vcs_info': {'vcs': 'git', 'requested_revision': 'b'*40, 'commit_id': 'b'*40}}
    else:
        worker.graph[0]['directUrl'] = {'url': 'file:///private/core', 'dir_info': {'editable': True}}
    row = core(worker)
    assert not row.get('registryManaged')
    assert not row['eligible']
    if row.get('override'):
        with pytest.raises(environments.ProtectedRuntimeSource):
            environments.augmented_manifest(BASE, [row])


@pytest.mark.parametrize('latest,status', [('2.0.3', 'update'), ('2.0.2', 'current'), ('2.0.1', 'failed'), ('2.1.0rc1', 'failed')])
async def test_registry_check_uses_latest_without_downgrading(worker, monkeypatch, latest, status):
    row = core(worker)
    loader = AsyncMock(return_value=latest)
    monkeypatch.setattr('amplifier_web.app_component_graph.index_latest', loader)
    if status == 'failed':
        with pytest.raises(ValueError):
            await registry.check(row)
    else:
        await registry.check(row)
        assert row['latest'] == latest and row['status'] == status
    loader.assert_awaited_once_with('amplifier-core')


async def test_manager_check_uses_cached_registry_route_and_reports_failure(worker, monkeypatch):
    service = AppService(worker.home, Runtime(), workspace=worker.home.parent)
    manager = UpdateManager(service)
    service.update_manager = manager
    row = core(worker)
    row['updateTier'] = 'included'
    monkeypatch.setattr(manager, 'inventory_sources', AsyncMock(side_effect=lambda **kwargs: [dict(row)]))
    index = AsyncMock(return_value='2.0.3')
    monkeypatch.setattr(manager.check_cache, 'index', index)
    monkeypatch.setattr('amplifier_web.update_checks.git_revision', AsyncMock(side_effect=AssertionError('Core is not a Git lookup')))
    try:
        await manager.check(tier='included')
        checked = next(item for item in manager.inventory if item.get('package') == registry.CORE)
        assert checked['status'] == 'update' and checked['latest'] == '2.0.3'
        index.assert_awaited_once_with(registry.CORE)
        index.side_effect = ValueError('private failure')
        await manager.check(tier='included')
        assert manager.inventory[0]['status'] == 'check_failed'
        assert service.state['updates']['phase'] == 'error'
        assert 'private failure' not in json.dumps(service.state['updates'])
    finally:
        await service.close()


async def stage_fixture(worker, monkeypatch, *, chosen=True, resolved='2.0.3', drift=None):
    row = core(worker)
    selected = {**row, 'latest': '2.0.3', 'status': 'update', 'updateTier': 'included'}
    if drift:
        selected.update(drift)
    commands = []
    async def resolver(*args, **kwargs):
        commands.append(args)
        project = Path(args[args.index('--project') + 1])
        (project / 'uv.lock').write_text(lock(resolved))
        return ''
    monkeypatch.setattr('amplifier_web.updates.process', resolver)
    monkeypatch.setattr(environments.shutil, 'which', lambda _: '/fixture/uv')
    class Diagnostics:
        async def run(self, name, fn, *args, **kwargs):
            return await fn(*args, **kwargs)
        def record(self, *args, **kwargs):
            pass
    manager = SimpleNamespace(home=worker.home, diagnostics=Diagnostics())
    generation = 'b'*32
    receipt = environments.receipt_directory(worker.home, generation)
    receipt.mkdir(parents=True)
    before = {path.name: path.read_bytes() for path in worker.receipt.iterdir()}
    candidates = [selected] if chosen else [{'kind': 'bundle', 'updateTier': 'other'}]
    project = await environments.stage(manager, generation, candidates, finalize=False)
    assert before == {path.name: path.read_bytes() for path in worker.receipt.iterdir()}
    return manager, generation, receipt, project, commands


async def test_stage_pins_exact_checked_core_wheel_and_keeps_no_build(worker, monkeypatch):
    _, _, receipt, project, commands = await stage_fixture(worker, monkeypatch)
    data = tomllib.loads((project / 'pyproject.toml').read_text())
    assert data['tool']['uv']['no-build-package'] == ['amplifier-core']
    assert data['tool']['uv']['override-dependencies'] == ['amplifier-core==2.0.3']
    assert json.loads((receipt / registry.RECEIPT).read_text()) == {registry.CORE: {'version': '2.0.3', 'registry': registry.INDEX}}
    assert commands[0].count('--upgrade-package') == 1
    assert not json.loads((receipt / 'runtime-sources.json').read_text())


async def test_other_tier_retains_existing_registry_version(worker, monkeypatch):
    _, _, receipt, project, commands = await stage_fixture(worker, monkeypatch, chosen=False, resolved='2.0.2')
    assert 'amplifier-core==2.0.2' in (project / 'pyproject.toml').read_text()
    assert '--upgrade-package' not in commands[0]
    assert json.loads((receipt / registry.RECEIPT).read_text())[registry.CORE]['version'] == '2.0.2'


@pytest.mark.parametrize('drift', [{'current': '1.0.0'}, {'registry': 'https://custom.invalid/simple'}, {'minimumVersion': '1.0.0'}])
async def test_staging_rechecks_core_policy_before_resolution(worker, monkeypatch, drift):
    with pytest.raises(ValueError, match='changed since checking'):
        await stage_fixture(worker, monkeypatch, drift=drift)


async def test_resolver_moving_after_check_cannot_qualify(worker, monkeypatch):
    with pytest.raises(ValueError, match='resolved Core wheel'):
        await stage_fixture(worker, monkeypatch, resolved='2.0.4')
    receipt = environments.receipt_directory(worker.home, 'b'*32)
    assert not (receipt / 'runtime.lock').exists()
    assert not (receipt / registry.RECEIPT).exists()


@pytest.mark.parametrize('substitution', ['version', 'local', 'git'])
async def test_dynamic_installer_cannot_replace_checked_core_before_freeze(worker, monkeypatch, substitution):
    manager, generation, receipt, project, _ = await stage_fixture(worker, monkeypatch)
    worker.graph[0]['version'] = '2.0.3'
    if substitution == 'version':
        worker.graph[0]['version'] = '2.0.4'
    elif substitution == 'local':
        worker.graph[0]['directUrl'] = {'url': 'file:///private/core', 'dir_info': {}}
    else:
        worker.graph[0]['directUrl'] = {'url': 'https://example.invalid/core', 'vcs_info': {'vcs': 'git', 'commit_id': 'd'*40}}
    with pytest.raises(ValueError, match='installed Core wheel'):
        await qualification.freeze(manager, generation, project)
    assert not (receipt / 'runtime.lock').exists()
    assert not (receipt / 'runtime-installed.json').exists()


async def test_qualified_registry_generation_retains_future_update_policy_and_exact_revalidation(worker, monkeypatch):
    manager, generation, receipt, project, commands = await stage_fixture(worker, monkeypatch)
    worker.graph[0]['version'] = '2.0.3'
    final = await qualification.freeze(manager, generation, project)
    qualification.verify_recorded(final, receipt)
    (worker.home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    current = core(worker)
    assert current['eligible'] and current['registryManaged'] and current['current'] == '2.0.3'
    assert registry.open_policy((receipt / 'runtime-base.toml').read_bytes()) == '2.0.2'
    before = {path.name: path.read_bytes() for path in receipt.iterdir()}
    commands.clear()
    restored = await environments.stage(manager, generation, [{**current, 'status': 'update', 'latest': '2.0.4'}])
    assert restored == final and '--locked' in commands[0] and '--upgrade-package' not in commands[0]
    assert before == {path.name: path.read_bytes() for path in receipt.iterdir()}


async def test_a_newer_installed_core_is_not_offered_a_downgrade(worker, monkeypatch):
    row = {**core(worker), 'current': '2.1.0'}
    monkeypatch.setattr('amplifier_web.app_component_graph.index_latest', AsyncMock(return_value='2.0.3'))
    await registry.check(row)
    assert row['status'] == 'current'


@pytest.mark.parametrize('setting', [
    'extra-index-url=["https://custom.invalid/simple"]',
    'index-url="https://custom.invalid/simple"',
    'find-links=["https://custom.invalid/wheels"]',
    'no-index=true',
    'index-strategy="unsafe-best-match"',
])
async def test_matching_recorded_alternate_resolver_policy_stays_protected(worker, monkeypatch, setting):
    # Core currently came from PyPI; a configured alternative still owns future resolution.
    policy = BASE.replace(b'[tool.uv]\n', ('[tool.uv]\n' + setting + '\n').encode())
    worker.manifest.write_bytes(policy)
    (worker.project / 'pyproject.toml').write_bytes(policy)
    for name in ('runtime-base.toml', 'runtime.toml'):
        (worker.receipt / name).write_bytes(policy)
    assert registry.open_policy(policy) is None
    row = core(worker)
    assert not row.get('registryManaged') and not row['eligible']
    before = {path.name: path.read_bytes() for path in worker.receipt.iterdir()}
    with pytest.raises(environments.ProtectedRuntimeSource):
        await stage_fixture(worker, monkeypatch)
    assert before == {path.name: path.read_bytes() for path in worker.receipt.iterdir()}
    assert (worker.project / 'pyproject.toml').read_bytes() == policy
