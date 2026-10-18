"""Deterministic adapter contracts; no Docker, network, install or desktop calls.

These checks do not replace the manual installed-wheel qualification. They are
safe to run in its owned preparation environment before admitting Docker work.
"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('default_installed_adapter', ROOT / 'scripts/qualify_default_installed.py')
assert SPEC and SPEC.loader
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def fake_wheel(path, name, requirements=(), payload=None):
    with zipfile.ZipFile(path, 'w') as archive:
        info = 'Name: ' + name + '\nVersion: 1.0\n'
        info += ''.join('Requires-Dist: ' + row + '\n' for row in requirements)
        archive.writestr(name + '-1.0.dist-info/METADATA', info)
        for relative, data in (payload or {}).items():
            archive.writestr(relative, data)
    return path


def test_unselected_direct_extras_do_not_require_unrelated_packages(tmp_path):
    wheel = fake_wheel(tmp_path / 'host.whl', 'amplifier-unified', [
        'amplifier-foundation @ https://example.invalid/foundation',
        'amplifier-app-tui @ https://example.invalid/tui ; extra == "tui"'])
    adapter.check_direct_requirements([wheel], ['amplifier-unified', 'amplifier-foundation'], '3.13.16')


def test_selected_ci_client_direct_dependency_must_be_pinned(tmp_path):
    wheel = fake_wheel(tmp_path / 'ci.whl', 'amplifier-bundle-context-intelligence', [
        'missing-library @ https://example.invalid/library ; extra == "client"'])
    with pytest.raises(ValueError, match='Undeclared active'):
        adapter.check_direct_requirements([wheel], ['amplifier-bundle-context-intelligence'], '3.13.16')


def test_source_inventory_checks_links_with_explicit_owned_boundary(tmp_path):
    root = tmp_path / '.venv'
    root.mkdir()
    python = tmp_path / 'python'
    python.write_text('fake interpreter bytes')
    (root / 'python').symlink_to(python)
    with pytest.raises(ValueError, match='symlink escapes'):
        adapter.inventory(root)
    assert adapter.inventory(root, boundary=tmp_path)['python'] == {'link': str(python)}


@pytest.mark.parametrize('drift', ['changed', 'missing', 'extra'])
def test_packaged_payload_must_equal_source_and_resources(tmp_path, drift):
    source = tmp_path / 'candidate'
    source.mkdir()
    (source / 'pkg').mkdir()
    (source / 'pkg/__init__.py').write_text('value=1\n')
    (source / 'resource.txt').write_text('resource\n')
    (source / 'pyproject.toml').write_text('''[tool.hatch.build.targets.wheel]
packages = ["pkg"]
[tool.hatch.build.targets.wheel.force-include]
"resource.txt" = "pkg/resource.txt"
''')
    payload = {'pkg/__init__.py': 'value=1\n', 'pkg/resource.txt': 'resource\n'}
    wheel = fake_wheel(tmp_path / 'good.whl', 'example', payload=payload)
    assert adapter.verify_candidate_wheel(wheel, source)['pkg/resource.txt'] == adapter.digest(source / 'resource.txt')
    if drift == 'changed':
        payload['pkg/resource.txt'] = 'wrong\n'
    elif drift == 'missing':
        payload.pop('pkg/resource.txt')
    else:
        payload['pkg/unexpected.bin'] = 'extra'
    wheel = fake_wheel(tmp_path / 'bad.whl', 'example', payload=payload)
    with pytest.raises(ValueError, match='payload differs'):
        adapter.verify_candidate_wheel(wheel, source)


def test_pins_require_exact_sources(tmp_path):
    data = json.loads((ROOT / 'scripts/default-installed-pins.json').read_text())
    assert adapter.pins(ROOT / 'scripts/default-installed-pins.json')['core']['version'] == '2.0.2'
    data['repositories'][0]['commit'] = 'main'
    path = tmp_path / 'pins.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='Exact repository'):
        adapter.pins(path)


def test_environment_contains_no_caller_credentials(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-must-not-leak')
    monkeypatch.setenv('SSH_AUTH_SOCK', '/synthetic/socket')
    monkeypatch.setenv('DISPLAY', ':7')
    env = adapter.clean_env()
    assert not {'OPENAI_API_KEY', 'SSH_AUTH_SOCK', 'DISPLAY', 'PYTHONPATH'} & env.keys()
    assert env['GIT_CONFIG_GLOBAL'] == '/dev/null'


def test_host_refusal_happens_before_any_execution(monkeypatch, tmp_path):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.setattr(adapter, 'command', lambda *a, **k: pytest.fail('No process may start'))
    with pytest.raises(ValueError, match='GitHub-hosted'):
        adapter.hosted_run(tmp_path / 'work', tmp_path / 'output', tmp_path / 'loop')
    assert list(tmp_path.iterdir()) == []


def test_unexpected_case_count_cannot_publish_success(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    monkeypatch.setattr(adapter, 'in_container', lambda: True)
    monkeypatch.setattr(adapter.os, 'getuid', lambda: 65532)
    (tmp_path / 'prepared-evidence').mkdir()
    (tmp_path / 'evidence').mkdir()
    (tmp_path / 'run').mkdir()
    monkeypatch.setattr(adapter, 'command', lambda *a, **k: None)

    def run(*args, **kwargs):
        (tmp_path / 'evidence/junit.xml').write_text('<testsuites><testsuite><testcase/></testsuite></testsuites>')
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(adapter.subprocess, 'run', run)
    with pytest.raises(ValueError, match='all 12'):
        adapter.guest_test()
    result = json.loads((tmp_path / 'evidence/result.json').read_text())
    assert result['passed'] is False and result['attestation'] is True


def test_workflow_is_manual_and_bridge_does_not_change_normal_matrix():
    # BaseLoader retains YAML's 'on' key rather than interpreting it as Boolean.
    workflow = yaml.load((ROOT / '.github/workflows/default-installed.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch', 'workflow_call'}
    assert workflow['permissions'] == {'contents': 'read'}
    steps = workflow['jobs']['default-installed']['steps']
    private = next(s for s in steps if s.get('with', {}).get('repository') == 'microsoft/amplifier-module-loop-live')
    assert private['with']['persist-credentials'] == 'false'
    assert private['with']['ref'] == '${{ steps.pins.outputs.loop }}'
    bridge = yaml.load((ROOT / '.github/workflows/integration-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert bridge['on']['workflow_dispatch']['inputs']['default_installed']['default'] == 'false'
    assert bridge['jobs']['default-installed']['if'] == "github.event_name == 'workflow_dispatch' && inputs.default_installed"
    assert bridge['jobs']['plan']['if'] == "github.event_name != 'workflow_dispatch' || !inputs.default_installed"
    assert bridge['jobs']['default-installed']['uses'] == './.github/workflows/default-installed.yml'


@pytest.mark.parametrize('name,kind', [('../escape', 'file'), ('link', 'link'), ('/absolute', 'file')])
def test_evidence_extraction_rejects_paths_and_links(tmp_path, name, kind):
    archive = tmp_path / 'evidence.tar'
    with tarfile.open(archive, 'w') as tar:
        member = tarfile.TarInfo(name)
        if kind == 'link':
            member.type = tarfile.SYMTYPE
            member.linkname = '/outside'
            tar.addfile(member)
        else:
            member.size = 1
            tar.addfile(member, io.BytesIO(b'x'))
    with pytest.raises(ValueError, match='Unsafe evidence'):
        adapter.extract_evidence(archive, tmp_path / 'extracted')
    assert not (tmp_path / 'extracted').exists()


def test_evidence_archive_retains_exact_regular_bytes(tmp_path):
    archive = tmp_path / 'evidence.tar'
    with tarfile.open(archive, 'w') as tar:
        member = tarfile.TarInfo('./result.json')
        payload = b'{"passed":false}\n'
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    adapter.extract_evidence(archive, tmp_path / 'extracted')
    assert (tmp_path / 'extracted/result.json').read_bytes() == payload


def test_container_execution_contract_keeps_evidence_until_collection():
    # Structural guard, not Docker/runtime acceptance. Actual tmpfs readback and
    # daemon cleanup still require the manual hosted run.
    source = (ROOT / 'scripts/qualify_default_installed.py').read_text()
    assert "'--network=none'" in source and "'--read-only'" in source
    assert "'--cap-drop=ALL'" in source and "'--user=65532:65532'" in source
    assert 'exit-code.tmp /opt/defaults/evidence/exit-code' in source
    assert "['docker', 'exec', test, 'tar'" in source
    assert "test + ':' + str(GUEST / 'evidence')" not in source
    assert source.index("state['preparationEvidenceRetained'] = True") < source.index("effect(['docker', 'stop'")
    assert "except (subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit, OSError):" in source
