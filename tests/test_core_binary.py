"""Core uses published native wheels while worker generations remain reversible."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest

from amplifier_web import runtime_environment as environments

ROOT = Path(__file__).parents[1]
UPSTREAM = 'https://github.com/microsoft/amplifier-core'


def test_all_app_dependency_projects_use_released_core_without_a_standing_pin():
    for relative in ['pyproject.toml', 'amplifier_web/runtime_deps/pyproject.toml', 'scripts/work_profile/pyproject.toml']:
        project = tomllib.loads((ROOT / relative).read_text())
        assert 'amplifier-core>=2.0.1' in project['project']['dependencies']
        policy = project['tool']['uv']
        assert 'amplifier-core' not in policy.get('sources', {})
        assert policy['no-build-package'] == ['amplifier-core']
        assert any(index['url'] == 'https://pypi.org/simple' and index.get('default') for index in policy['index'])


@pytest.mark.parametrize('installer', ['pip', 'sync'])
def test_core_source_build_is_rejected_before_executing_its_backend(tmp_path, installer):
    uv = shutil.which('uv')
    if not uv:
        pytest.skip('uv is needed to verify the wheel-only installation policy')
    source = tmp_path / 'core'
    source.mkdir()
    (source / 'pyproject.toml').write_text('[project]\nname="amplifier-core"\nversion="999.0.0"\n'
        '[build-system]\nrequires=[]\nbuild-backend="fixture"\nbackend-path=["."]\n')
    marker = tmp_path / 'build-attempted'
    (source / 'fixture.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n'
                                     'raise RuntimeError("Core source builds must not run")\n')
    if installer == 'pip':
        environment = tmp_path / 'venv'
        subprocess.run([uv, 'venv', str(environment), '--python', sys.executable], check=True, capture_output=True)
        command = [uv, 'pip', 'install', '--python', str(environment / 'bin/python'), '--no-index', str(source)]
    else:
        project = tmp_path / 'worker'
        project.mkdir()
        policy = tomllib.loads((ROOT / 'amplifier_web/runtime_deps/pyproject.toml').read_text())['tool']['uv']['no-build-package']
        (project / 'pyproject.toml').write_text('[project]\nname="worker"\nversion="1"\n'
            'dependencies=["amplifier-core"]\n[tool.uv]\nno-build-package=' + json.dumps(policy) + '\n'
            '[tool.uv.sources]\namplifier-core={path=' + json.dumps(str(source)) + '}\n')
        command = [uv, 'sync', '--project', str(project), '--python', sys.executable, '--no-index']
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    diagnostic = ' '.join(result.stderr.split())  # uv wraps diagnostics to the terminal width.
    assert ('Building source distributions is disabled' in diagnostic
            or 'marked as `--no-build` but has no binary distribution' in diagnostic)
    assert not marker.exists()


@pytest.fixture
def migration(tmp_path, monkeypatch):
    shipped = (ROOT / 'amplifier_web/runtime_deps/pyproject.toml').read_bytes()
    manifest = tmp_path / 'shipped.toml'
    manifest.write_bytes(shipped)
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    home = tmp_path / 'app'
    generation = 'a' * 32
    receipt = environments.receipt_directory(home, generation)
    receipt.mkdir(parents=True)
    previous = (b'[project]\nname="old-runtime"\nversion="0.1.1"\ndependencies=["amplifier-core>=1.6.1"]\n'
                b'[tool.uv.sources]\namplifier-core={git="' + UPSTREAM.encode() + b'",rev="main"}\n')
    for name in ['runtime.toml', 'runtime-base.toml']:
        (receipt / name).write_bytes(previous)
    (receipt / 'runtime.lock').write_text('version=1\n[[package]]\nname="amplifier-core"\nversion="2.0.0"\n'
                                        'source={git="' + UPSTREAM + '?rev=main#' + 'b' * 40 + '"}\n')
    (home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    before = {p.name: p.read_bytes() for p in receipt.iterdir()}
    return home, receipt, before, shipped


def test_previous_app_core_git_default_moves_to_registry_without_touching_receipts(migration):
    home, receipt, before, shipped = migration
    rows = environments.inventory(home)
    assert next(r for r in rows if r['kind'] == 'runtime environment')['status'] == 'update'
    core = next(r for r in rows if r.get('package') == 'amplifier-core')
    assert core['registryMigration'] and not core['eligible']
    assert core['current'] == 'b' * 40 and core['url'] == UPSTREAM
    assert environments.augmented_manifest(shipped, rows) == shipped
    assert {p.name: p.read_bytes() for p in receipt.iterdir()} == before


@pytest.mark.parametrize('change', ['fork', 'fixed-ref', 'subdirectory', 'editable', 'dirty-cache'])
def test_explicit_installed_core_overrides_are_never_migrated(migration, change):
    home, receipt, before, shipped = migration
    source = {'url': UPSTREAM, 'ref': 'main', 'current': 'b' * 40, 'provenance': 'installed Git distribution'}
    if change == 'fork':
        source['url'] = 'https://example.invalid/user/core'
    elif change == 'fixed-ref':
        source['ref'] = 'c' * 40
    elif change == 'subdirectory':
        source['subdirectory'] = 'local-core'
    else:
        source = {'override': True, 'current': '2.0.0', 'provenance': 'installed local or registry override',
                  **({'cacheManaged': True} if change == 'dirty-cache' else {})}
    rows = environments.inventory(home, installed={'amplifier-core': source})
    core = next(r for r in rows if r.get('package') == 'amplifier-core')
    assert 'registryMigration' not in core
    if change in {'editable', 'dirty-cache'}:
        # A local installation cannot conceal the old tracked Git dependency.
        core['trackedSource'] = True
        with pytest.raises(environments.ProtectedRuntimeSource):
            environments.augmented_manifest(shipped, rows)
    else:
        generated = tomllib.loads(environments.augmented_manifest(shipped, rows).decode())
        assert any(source['url'] in dep and source['ref'] in dep
                   for dep in generated['dependency-groups']['unified-managed-runtime'])
    assert {p.name: p.read_bytes() for p in receipt.iterdir()} == before


def test_same_core_source_is_not_migrated_without_previous_app_policy(migration):
    home, receipt, _, _ = migration
    (receipt / 'runtime-base.toml').write_text('[project]\nname="custom"\n')
    core = next(r for r in environments.inventory(home) if r.get('package') == 'amplifier-core')
    assert 'registryMigration' not in core and core['eligible']
