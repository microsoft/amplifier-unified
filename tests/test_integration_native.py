"""The integration environment inherits, rather than replaces, runtime policy."""
from pathlib import Path
import shutil
import subprocess
import tomllib

import pytest

from scripts.integration_native import project

ROOT = Path(__file__).resolve().parents[1]


def test_integration_project_preserves_declared_runtime_and_host_test_inputs():
    runtime = tomllib.loads((ROOT / "amplifier_web/runtime_deps/pyproject.toml").read_text())
    host = tomllib.loads((ROOT / "pyproject.toml").read_text())
    prepared = tomllib.loads(project(ROOT))
    assert set(runtime["project"]["dependencies"]) <= set(prepared["project"]["dependencies"])
    assert set(host["dependency-groups"]["dev"]) <= set(prepared["project"]["dependencies"])
    for name, source in runtime["tool"]["uv"]["sources"].items():
        assert prepared["tool"]["uv"]["sources"][name] == source
    assert prepared["tool"]["uv"]["no-build-package"] == runtime["tool"]["uv"]["no-build-package"]
    assert prepared["tool"]["uv"]["override-dependencies"] == runtime["tool"]["uv"]["override-dependencies"]
    assert prepared["tool"]["uv"]["sources"]["amplifier-unified"] == {"path": str(ROOT), "editable": True}
    assert not any("amplifier-app-cli" in item for item in prepared["project"]["dependencies"])


def test_profiling_requires_explicit_source_instead_of_unrelated_same_named_package():
    base = tomllib.loads(project(ROOT))["project"]["dependencies"]
    assert not any("profiling" in item for item in base)
    url = "git+https://example.invalid/canonical-profiling@deadbeef"
    configured = tomllib.loads(project(ROOT, url))["project"]["dependencies"]
    assert configured.count("amplifier-profiling @ " + url) == 1
    assert [item for item in configured if item != "amplifier-profiling @ " + url] == base


def test_core_source_build_policy_cannot_silently_disappear(tmp_path):
    (tmp_path / "amplifier_web/runtime_deps").mkdir(parents=True)
    shutil.copy2(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    source = (ROOT / "amplifier_web/runtime_deps/pyproject.toml").read_text()
    (tmp_path / "amplifier_web/runtime_deps/pyproject.toml").write_text(
        source.replace('no-build-package = ["amplifier-core"]', 'no-build-package = []'))
    with pytest.raises(ValueError, match="prohibit Core source builds"):
        project(tmp_path)


def test_authenticated_loop_checkout_must_be_clean_canonical_and_named(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()
    git("init", "-q")
    git("remote", "add", "origin", "https://github.com/microsoft/amplifier-module-loop-live")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="amplifier-module-loop-live"\nversion="0.0.0"\n')
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "Fixture")
    prepared = tomllib.loads(project(ROOT, loop_source=tmp_path))
    assert prepared["tool"]["uv"]["sources"]["amplifier-module-loop-live"] == {"path": str(tmp_path)}
    (tmp_path / "untracked").write_text("not frozen")
    with pytest.raises(ValueError, match="clean"):
        project(ROOT, loop_source=tmp_path)
    (tmp_path / "untracked").unlink()
    git("remote", "set-url", "origin", "https://example.invalid/other-owner")
    with pytest.raises(ValueError, match="canonical"):
        project(ROOT, loop_source=tmp_path)


def test_native_search_dependencies_are_present_before_pytest_can_skip():
    from scripts.integration_native import REQUIRED_MODULES
    dependencies = tomllib.loads(project(ROOT))['project']['dependencies']
    for package in ('filesystem', 'search'):
        assert any(item.startswith(f'amplifier-module-tool-{package} @ git+https://github.com/microsoft/')
                   for item in dependencies)
    assert {'amplifier_module_tool_filesystem', 'amplifier_module_tool_search.grep',
            'amplifier_module_tool_search.glob'} <= set(REQUIRED_MODULES)


def test_missing_actual_ripgrep_fails_preflight_without_installing(monkeypatch):
    from scripts import integration_native
    monkeypatch.setattr(integration_native.shutil, 'which', lambda _: None)
    monkeypatch.setattr(integration_native.subprocess, 'check_output',
                        lambda *a, **k: pytest.fail('must reject before launching a process'))
    with pytest.raises(ValueError, match='requires ripgrep'):
        integration_native.ripgrep_identity()


def test_native_ripgrep_receipt_uses_the_resolved_executable(monkeypatch):
    from scripts import integration_native
    monkeypatch.setattr(integration_native.shutil, 'which', lambda _: '/owned/bin/rg')
    def version(command, **kwargs):
        assert command == ['/owned/bin/rg', '--version']
        return 'ripgrep 14.1.1\n'
    monkeypatch.setattr(integration_native.subprocess, 'check_output', version)
    assert integration_native.ripgrep_identity() == {'path': '/owned/bin/rg', 'version': 'ripgrep 14.1.1'}
