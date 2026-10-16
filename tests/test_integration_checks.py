"""Missing prerequisites must not turn designated integration jobs green."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('integration_checks', ROOT / 'scripts/integration_checks.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


@pytest.mark.parametrize('problem', ['skipped', 'error', 'failure', 'empty', 'missing', 'counter'])
def test_incomplete_or_skipped_execution_cannot_pass(tmp_path, problem):
    case = '<testcase classname="tests.test_example" name="test_required"/>'
    if problem in {'skipped', 'error', 'failure'}:
        case = f'<testcase classname="tests.test_example" name="test_required"><{problem}/></testcase>'
    if problem == 'empty':
        case = ''
    if problem == 'missing':
        case = case.replace('test_required', 'test_unrelated')
    counters = ' skipped="1"' if problem == 'counter' else ''
    path = tmp_path / 'results.xml'
    path.write_text(f'<testsuites><testsuite{counters}>{case}</testsuite></testsuites>')
    with pytest.raises(ValueError):
        checks.verify_junit(path, ['tests/test_example.py::test_required'])


def test_successful_parameterized_execution_and_module_collection(tmp_path):
    path = tmp_path / 'results.xml'
    path.write_text('<testsuites><testsuite><testcase classname="tests.test_example" '
                    'name="test_required[first]"/></testsuite></testsuites>')
    assert checks.verify_junit(path, ['tests/test_example.py::test_required', 'tests/test_example.py']) == 1


def test_changed_feature_selects_its_owner_without_full_matrix():
    selected = checks.select_lanes(['tests/test_ci_live.py'])
    assert selected == [{'lane': 'context-intelligence', 'os': 'ubuntu-latest'}]
    assert checks.select_lanes(['README.md']) == []
    all_jobs = checks.select_lanes()
    assert checks.select_lanes(['scripts/integration_checks.py']) == all_jobs
    assert checks.select_lanes(['scripts/run_owned_integration.py']) == all_jobs
    assert checks.select_lanes(['tests/conftest.py']) == all_jobs
    assert not {'release-runtime', 'browser-installed'} & {job['lane'] for job in all_jobs}


@pytest.mark.parametrize('probe', ['warm_worker_probe.py', 'history_edit_probe.py', 'attachments_runtime_probe.py',
                                  'live_terminal_probe.py', 'standalone_controls_probe.py', 'computation_runtime.py'])
def test_runtime_probe_changes_select_native_owner(probe):
    assert 'native' in {row['lane'] for row in checks.select_lanes([f'tests/fixtures/{probe}'])}


def test_filesystem_process_probe_selects_mac_owner():
    assert {'lane': 'filesystem-aliases', 'os': 'macos-latest'} in checks.select_lanes(
        ['tests/fixtures/file_source_process_probe.py'])


def test_every_audited_skip_has_an_owner_and_its_test_selects_that_owner():
    coverage = checks.manifest()
    records = coverage['baselineRecords']
    assert len(records) == coverage['baseline']['skipRecords'] == 87
    assert len({record['node'] for record in records}) == 87
    assert sum(record['kind'] == 'module_collection' for record in records) == 3
    for record in records:
        lane = coverage['lanes'][record['owner']]
        assert lane['selectors']
        if lane['mode'] == 'automatic':
            selected = checks.select_lanes([record['node'].split('::')[0]])
            assert record['owner'] in {job['lane'] for job in selected}


def test_publication_stays_short_and_background_owns_integration():
    import yaml
    release = yaml.load((ROOT / '.github/workflows/release.yml').read_text(), Loader=yaml.BaseLoader)
    assert 'integration' not in release['jobs']
    post = yaml.load((ROOT / '.github/workflows/post-release.yml').read_text(), Loader=yaml.BaseLoader)
    assert post['jobs']['integrations']['uses'] == './.github/workflows/integration-checks.yml'
    assert 'integrations' in post['jobs']['report']['needs']
    workflow = yaml.load((ROOT / '.github/workflows/integration-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert {'pull_request', 'workflow_call', 'workflow_dispatch'} <= set(workflow['on'])
    assert 'pull_request_target' not in workflow['on']


@pytest.mark.parametrize('changed', ['pyproject.toml', 'uv.lock', '.github/workflows/integration.yml',
    'frontend/src/main.jsx', 'frontend/tests/browser.mjs', 'frontend/package.json',
    'frontend/package-lock.json', 'frontend/vite.config.js', 'frontend/index.html',
    'amplifier_web/static/assets/main.js', 'amplifier_web/static/index.html',
    'amplifier_web/static/assets/main.css', 'amplifier_web/static/assets/viewer.wasm'])
def test_dependency_and_workflow_inputs_change_source_identity(tmp_path, monkeypatch, changed):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="fixture"\n')
    (tmp_path / 'uv.lock').write_text('version=1\n')
    (tmp_path / '.github/workflows').mkdir(parents=True)
    (tmp_path / '.github/workflows/integration.yml').write_text('name: fixture\n')
    path = tmp_path / changed
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('original input\n')
    monkeypatch.setattr(checks, 'ROOT', tmp_path)
    before = checks.source_identity('fixture')
    path.write_text(path.read_text() + '\n# changed\n')
    assert checks.source_identity('fixture')['testedSourceDigest'] != before['testedSourceDigest']


def test_generated_caches_and_output_are_not_source_inputs(tmp_path, monkeypatch):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="fixture"\n')
    (tmp_path / 'uv.lock').write_text('version=1\n')
    monkeypatch.setattr(checks, 'ROOT', tmp_path)
    before = checks.source_identity('fixture')
    for name in ('frontend/tests/output/result.json', 'frontend/src/node_modules/lib.js',
                 'amplifier_web/__pycache__/service.pyc'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('generated\n')
    assert checks.source_identity('fixture') == before


@pytest.mark.parametrize('outcome', ['success', 'source-changed', 'launch-error', 'pytest-failed'])
def test_run_is_source_bound_and_cannot_reuse_stale_success(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(checks, 'manifest', lambda: {'lanes': {'fixture': {
        'selectors': ['tests/test_example.py::test_required']}}})
    identities = iter([{'baseRevision': 'fixture', 'testedSourceDigest': 'before'},
                       {'baseRevision': 'fixture', 'testedSourceDigest': 'after' if outcome == 'source-changed' else 'before'}])
    monkeypatch.setattr(checks, 'source_identity', lambda _: next(identities))
    junit = tmp_path / 'junit.xml'
    receipt = tmp_path / 'result.json'
    receipt.write_text('{"passed": true}')
    junit.write_text('<stale/>')
    def execute(*args, **kwargs):
        assert not junit.exists() and not receipt.exists()
        if outcome == 'launch-error':
            raise FileNotFoundError('interpreter missing')
        junit.write_text('<testsuites><testsuite><testcase classname="tests.test_example" '
                         'name="test_required"/></testsuite></testsuites>')
        return SimpleNamespace(returncode=2 if outcome == 'pytest-failed' else 0)
    monkeypatch.setattr(checks.subprocess, 'run', execute)
    code = checks.run_lane('fixture', 'python', tmp_path, revision='fixture')
    result = json.loads(receipt.read_text())
    assert result['passed'] is (outcome == 'success')
    assert code == (0 if outcome == 'success' else 1)
    if outcome == 'source-changed':
        assert result['sourceAfter']['testedSourceDigest'] == 'after'
        assert 'changed' in result['error']
    if outcome == 'launch-error':
        assert not junit.exists() and result['exitCode'] is None
        assert 'interpreter missing' in result['error']
