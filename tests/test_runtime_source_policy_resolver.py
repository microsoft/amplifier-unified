"""Real offline Git/uv install, policy freeze and next-inventory boundaries."""
import json
import shutil
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

from amplifier_web import runtime_environment as environments
from amplifier_web import runtime_qualification as qualification
from amplifier_web import updates


BACKEND = '''
from pathlib import Path
import zipfile

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    name = "fixture_component-1.0"
    filename = name + "-py3-none-any.whl"
    with zipfile.ZipFile(Path(wheel_directory) / filename, "w") as wheel:
        wheel.writestr("fixture_component.py", "value = 1\\n")
        wheel.writestr(name + ".dist-info/METADATA", "Metadata-Version: 2.3\\nName: fixture-component\\nVersion: 1.0\\n")
        wheel.writestr(name + ".dist-info/WHEEL", "Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n")
        wheel.writestr(name + ".dist-info/RECORD", "")
    return filename
'''


class Diagnostics:
    async def run(self, phase, fn, *args, **kwargs):
        return await fn(*args, **kwargs)
    def record(self, *args, **kwargs):
        pass


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL, text=True).strip()


@pytest.mark.parametrize('case', ['tag', 'branch-tag-conflict', 'inactive-marker', 'unused-optional',
                                 'ambiguous-rev', 'ambiguous-uri', 'selected-branch', 'selected-managed-group'])
async def test_real_normalized_install_freezes_only_selected_moving_policy(tmp_path, monkeypatch, case):
    uv = shutil.which('uv')
    if not uv:
        pytest.skip('Real resolver regression requires uv')
    repo = tmp_path / 'repo'; repo.mkdir()
    git(repo, 'init', '-b', 'stable')
    git(repo, 'config', 'user.name', 'Fixture'); git(repo, 'config', 'user.email', 'fixture@example.invalid')
    (repo / 'pyproject.toml').write_text('[project]\nname="fixture-component"\nversion="1.0"\n'
        '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
    (repo / 'backend.py').write_text(BACKEND)
    git(repo, 'add', '.'); git(repo, 'commit', '-m', 'fixture')
    revision = git(repo, 'rev-parse', 'HEAD'); git(repo, 'tag', 'stable')
    home, generation = tmp_path / 'app', 'e' * 32
    project = environments.prepare_project(home, generation)
    url = repo.as_uri()
    # Real uv creates both the normalized lock and PEP 610 install receipt.
    # Preserve the authored declaration separately, as qualification does after
    # an installer has resolved its requested ref to an exact commit.
    (project / 'pyproject.toml').write_text('[project]\nname="worker"\nversion="1"\n'
        'dependencies=["fixture-component @ git+' + url + '@' + revision + '"]\n')
    monkeypatch.setenv('UV_NO_INDEX', '1')
    # --refresh-package in production staging is incompatible with UV_OFFLINE;
    # every dependency here is local Git and index/Python downloads are disabled.
    monkeypatch.delenv('UV_OFFLINE', raising=False)
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS', 'never')
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'uv-cache'))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    await updates.process(uv, 'sync', '--project', str(project), '--python', sys.executable, timeout=90)
    installed = qualification.installed_graph(project)
    row = next(row for row in installed if row['name'] == 'fixture-component')
    assert row['directUrl']['vcs_info']['requested_revision'] == revision
    assert parse_qs(environments.locked_sources(project)['fixture-component'].query)['rev'] == [revision]
    requirement = 'fixture-component @ git+' + url + '@stable'
    data = {'project': {'name': 'worker', 'version': '1'}}
    if case in {'tag', 'ambiguous-rev', 'selected-branch'}:
        data['project']['dependencies'] = ['fixture-component']
        key = {'tag': 'tag', 'ambiguous-rev': 'rev', 'selected-branch': 'branch'}[case]
        data['tool'] = {'uv': {'sources': {'fixture-component': {'git': url, key: 'stable'}}}}
    elif case == 'branch-tag-conflict':
        data['project']['dependencies'] = ['fixture-component']
        data['tool'] = {'uv': {'sources': {'fixture-component': [
            {'git': url, 'branch': 'stable', 'marker': "sys_platform == 'linux'"},
            {'git': url, 'tag': 'stable', 'marker': "sys_platform != 'linux'"}]}}}
    elif case == 'ambiguous-uri':
        data['project']['dependencies'] = [requirement]
    elif case == 'inactive-marker':
        data['project']['dependencies'] = [requirement + ' ; sys_platform == "unavailable-platform"']
    elif case == 'unused-optional':
        data['project']['optional-dependencies'] = {'unused': [requirement]}
    else:
        baseline = b'[project]\nname="worker"\nversion="1"\n'
        content = environments.augmented_manifest(baseline, [{
            'kind': 'runtime dependency', 'package': 'fixture-component', 'url': url, 'ref': 'stable'}])
    if case != 'selected-managed-group':
        content = ('\n'.join(json.dumps(key) + ' = ' + environments.toml_value(value)
                             for key, value in data.items()) + '\n').encode()
    manifest = tmp_path / 'authored.toml'; manifest.write_bytes(content)
    (project / 'pyproject.toml').write_bytes(content)
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)

    manager = SimpleNamespace(home=home, diagnostics=Diagnostics())
    if case == 'selected-managed-group':
        # Exercise the real group generator and stage's exact checked-policy
        # receipt, rather than treating an unchecked @name as branch evidence.
        assert git(repo, 'rev-parse', 'refs/heads/stable') == revision
        checked = {'kind': 'runtime dependency', 'package': 'fixture-component',
                   'url': url, 'ref': 'stable', 'current': revision, 'latest': revision, 'eligible': True}
        manifest.write_bytes(baseline)
        async def inventory(_):
            return [checked]
        monkeypatch.setattr(environments, 'update_inventory', inventory)
        project = await environments.stage(manager, generation, [checked], finalize=False)
        await updates.process(uv, 'sync', '--locked', '--project', str(project), '--python', sys.executable, timeout=90)
    final = await qualification.freeze(manager, generation, project)
    receipt = environments.receipt_directory(home, generation)
    qualification.verify_recorded(final, receipt)
    saved = json.loads((receipt / 'runtime-sources.json').read_text())['fixture-component']
    moving = case in {'selected-branch', 'selected-managed-group'}
    assert saved['ref'] == ('stable' if moving else revision)
    assert saved['policyOrigin']['kind'] == ('checked-source' if case == 'selected-managed-group' else
                                            'prepared-manifest' if moving else 'tag-reference' if case == 'tag' else
                                            'unresolved-reference' if case in {'ambiguous-rev', 'ambiguous-uri'} else 'resolution-reference')
    (home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    inventory = next(row for row in environments.inventory(home) if row.get('package') == 'fixture-component')
    assert inventory['eligible'] is moving
    assert inventory['ref'] == saved['ref']


@pytest.mark.parametrize('kind,topology', [
    ('tag', 'tag-only'), ('rev', 'tag-only'), ('uri', 'tag-only'),
    ('rev', 'same-name'), ('uri', 'same-name'), ('branch', 'same-name'),
    ('sha', 'tag-only'), ('rev', 'branch-only'),
    ('marker-pin', 'branch-only'), ('marker-branch', 'branch-only'),
])
async def test_real_named_ref_resolution_preserves_tag_and_ambiguity(tmp_path, monkeypatch, kind, topology):
    """Resolve the actual authored manifest; no normalized-install substitution."""
    uv = shutil.which('uv')
    if not uv:
        pytest.skip('Real resolver regression requires uv')
    repo = tmp_path / 'repo'; repo.mkdir()
    git(repo, 'init', '-b', 'main' if topology == 'tag-only' else 'stable')
    git(repo, 'config', 'user.name', 'Fixture'); git(repo, 'config', 'user.email', 'fixture@example.invalid')
    (repo / 'pyproject.toml').write_text('[project]\nname="fixture-component"\nversion="1.0"\n'
        '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
    (repo / 'backend.py').write_text(BACKEND)
    git(repo, 'add', '.'); git(repo, 'commit', '-m', 'fixture')
    revision = git(repo, 'rev-parse', 'HEAD')
    if topology != 'branch-only':
        git(repo, 'tag', 'stable')
    url = repo.as_uri()
    data = {'project': {'name': 'worker', 'version': '1'}}
    if kind.startswith('marker-'):
        data['project']['dependencies'] = ['fixture-component']
        active = 'sys_platform == ' + repr(sys.platform)
        inactive = 'sys_platform != ' + repr(sys.platform)
        data['tool'] = {'uv': {'sources': {'fixture-component': [
            {'git': url, 'rev': revision, 'marker': active if kind == 'marker-pin' else inactive},
            {'git': url, 'branch': 'stable', 'marker': inactive if kind == 'marker-pin' else active}]}}}
    elif kind == 'uri':
        data['project']['dependencies'] = ['fixture-component @ git+' + url + '@stable']
    else:
        data['project']['dependencies'] = ['fixture-component']
        data['tool'] = {'uv': {'sources': {'fixture-component': {
            'git': url, 'rev' if kind == 'sha' else kind: revision if kind == 'sha' else 'stable'}}}}
    content = ('\n'.join(json.dumps(key) + ' = ' + environments.toml_value(value)
                         for key, value in data.items()) + '\n').encode()
    manifest = tmp_path / 'authored.toml'; manifest.write_bytes(content)
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    monkeypatch.setenv('UV_NO_INDEX', '1')
    monkeypatch.delenv('UV_OFFLINE', raising=False)
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS', 'never')
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'uv-cache'))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    home, generation = tmp_path / 'app', 'f' * 32
    project = environments.prepare_project(home, generation)
    await updates.process(uv, 'sync', '--project', str(project), '--python', sys.executable, timeout=90)
    query = parse_qs(environments.locked_sources(project)['fixture-component'].query)
    if kind.startswith('marker-'):
        # A universal resolver can collapse active SHA + inactive branch when
        # they resolve to the same commit. The lock is not selected intent.
        assert query == {'branch': ['stable']}
    else:
        assert query == {kind if kind in {'branch', 'tag'} else 'rev': [revision if kind == 'sha' else 'stable']}
    final = await qualification.freeze(SimpleNamespace(home=home, diagnostics=Diagnostics()), generation, project)
    receipt = environments.receipt_directory(home, generation)
    qualification.verify_recorded(final, receipt)
    saved = json.loads((receipt / 'runtime-sources.json').read_text())['fixture-component']
    moving = kind == 'branch'
    assert saved['ref'] == ('stable' if moving else revision)
    expected_kind = {'branch': 'prepared-manifest', 'tag': 'tag-reference', 'sha': 'resolution-reference'}.get(kind, 'unresolved-reference')
    assert saved['policyOrigin']['kind'] == expected_kind
    if expected_kind in {'tag-reference', 'unresolved-reference'}:
        assert saved['policyOrigin']['originalRef'] == 'stable'
    (home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    row = next(row for row in environments.inventory(home) if row.get('package') == 'fixture-component')
    assert row['eligible'] is moving
    assert row['policyOrigin'] == saved['policyOrigin']
