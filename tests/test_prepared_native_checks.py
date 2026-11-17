"""Cheap controls for the ordinary native suite; no Docker/native dependencies."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def test_prepared_suite_is_one_ordinary_manual_job():
    import yaml
    value = yaml.safe_load((ROOT / '.github/workflows/python-checks.yml').read_text())
    events = value.get('on', value.get(True))
    assert 'prepared-runtime-native' in events['workflow_dispatch']['inputs']['suite']['options']
    assert 'pull_request' not in events
    job = value['jobs']['prepared-runtime-native']
    assert 'workflow_dispatch' in job['if'] and job['permissions'] == {'contents': 'read'}
    assert all('secrets.' not in str(s) for s in job['steps'])
    checkouts = [s for s in job['steps'] if s.get('uses', '').startswith('actions/checkout@')]
    assert all(s['with']['persist-credentials'] is False for s in checkouts)
    assert any(s.get('if') == 'always()' and s.get('uses', '').startswith('actions/upload-artifact@') for s in job['steps'])
    assert "inputs.suite != 'prepared-runtime-native'" in value['jobs']['python']['if']
    assert not (ROOT / '.github/workflows/prepared-runtime-native.yml').exists()
    assert not list((ROOT / 'scripts').glob('candidate_native_*.py'))
    assert 'registered.json' not in (ROOT / 'tests/test_prepared_preset_native.py').read_text()
    driver = (ROOT / 'scripts/fresh_cleanup_native_checks.py').read_text()
    # Selection remains a pre-effect snapshot; the terminal result must not
    # inherit its initial unrun label after the exact case guard succeeds.
    assert "'native': 'unrun'" in driver and "'native': 'passed'" in driver
    assert driver.index('count = verify_cases(') < driver.index("'native': 'passed'")


def test_checkout_gate_uses_actual_bytes_and_distinct_evidence(monkeypatch):
    from native_cleanup_fixture import CheckoutGate
    from amplifier_web import environment_cleanup_execution as execution
    app = object()
    gate = CheckoutGate(app)
    assert gate.bootstrap()['source'] == gate.raw['amplifier_web/environment_cleanup_launcher.py'].hex()
    assert CheckoutGate.receive is execution.Gate.receive
    assert CheckoutGate.validate is execution.Gate.validate
    assert 'serving' not in gate.evidence({'phase': 'loaded'})
    assert gate.evidence({})['kind'] == 'qualification-test-checkout'
    gate.recheck()
    path = next(iter(gate.paths.values()))
    original = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda p: b'changed' if p == path else original(p))
    with pytest.raises(ValueError, match='changed'):
        gate.recheck()


def test_checkout_gate_is_only_pytest_scoped_and_permit_not_replaced(monkeypatch):
    from native_cleanup_fixture import checkout_cleanup
    from amplifier_web import environment_cleanup_execution as execution, task_environments as owner
    original = execution.Gate
    permit, app = object(), SimpleNamespace(data_dir=Path('/unused'))
    seen = {}
    def invoke(*args, **kw):
        seen.update(kw)
        return {'ok': True}
    monkeypatch.setattr(owner, 'invoke', invoke)
    with monkeypatch.context() as scoped:
        checkout_cleanup(scoped)
        assert owner.invoke_cleanup(app, {'backend': 'docker'}, {}, 'remove', {}, permit) == {'ok': True}
    assert execution.Gate is original and seen['_cleanup_permit'] is permit
    assert seen['_cleanup_gate'].evidence({})['kind'] == 'qualification-test-checkout'


@pytest.mark.parametrize('value', [
    {'complete': False, 'candidates': [], 'protectedIds': []},
    {'complete': True, 'candidates': [], 'protectedIds': ['unknown']},
    {'complete': True, 'candidates': [{'id': 'live'}], 'protectedIds': []},
])
def test_unresolved_dependencies_never_allow_next_profile(value):
    from native_cleanup_fixture import require_absent
    with pytest.raises(AssertionError):
        require_absent(value)


def test_absent_dependencies_and_prior_cleanup_latch(tmp_path):
    from native_cleanup_fixture import require_absent, require_prior_cleanup
    require_absent({'complete': True, 'candidates': [], 'protectedIds': []})
    (tmp_path / 'python-project.json').write_text(json.dumps({'cleanupComplete': False}))
    with pytest.raises(AssertionError):
        require_prior_cleanup(tmp_path, 'browser-test')
    (tmp_path / 'python-project.json').write_text(json.dumps({'cleanupComplete': True, 'passed': False}))
    require_prior_cleanup(tmp_path, 'browser-test')  # A known test failure with safe cleanup is not physical custody.


@pytest.mark.parametrize('names', [[], ['python-project'], ['python-project', 'python-project'], ['python-project', 'browser-test', 'extra']])
def test_exact_native_case_accounting(names):
    from fresh_cleanup_native_checks import verify_cases
    suite = ET.Element('testsuites')
    for name in names:
        ET.SubElement(suite, 'testcase', name='test_native_default_prepared_preset_runtime[' + name + ']')
    with pytest.raises(AssertionError):
        verify_cases(0, ET.tostring(suite), 'prepared-runtime-native')


@pytest.mark.parametrize('bad', ['skipped', 'failure', 'error'])
def test_native_skips_and_failures_are_not_qualification(bad):
    from fresh_cleanup_native_checks import verify_cases
    suite = ET.Element('testsuites')
    for profile in ['python-project', 'browser-test']:
        case = ET.SubElement(suite, 'testcase', name='test_native_default_prepared_preset_runtime[' + profile + ']')
    ET.SubElement(case, bad)
    with pytest.raises(AssertionError):
        verify_cases(0, ET.tostring(suite), 'prepared-runtime-native')


def test_success_requires_zero_exit_and_two_named_native_cases():
    from fresh_cleanup_native_checks import verify_cases
    suite = ET.Element('testsuites')
    for profile in ['python-project', 'browser-test']:
        ET.SubElement(suite, 'testcase', name='test_native_default_prepared_preset_runtime[' + profile + ']')
    assert verify_cases(0, ET.tostring(suite), 'prepared-runtime-native') == 2
    with pytest.raises(AssertionError):
        verify_cases(1, ET.tostring(suite), 'prepared-runtime-native')


def test_actual_driver_records_both_sources_before_backend(tmp_path, monkeypatch):
    import fresh_cleanup_native_checks as driver
    root = tmp_path / 'checkout'; root.mkdir()
    product = root / 'amplifier_web'; product.mkdir()
    (product / 'task_environments.py').write_text("DTU_COMMIT='pin'\nAPPROVED_IMAGE='image'\nREPOSITORY='dtu'\n")
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c',
                    'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    expected = driver.git(root, 'rev-parse', 'HEAD')
    monkeypatch.setattr(driver, 'ROOT', root)
    for name, value in {'CHECK_SUITE': 'prepared-runtime-native', 'CLEANUP_NATIVE_ROOT': str(tmp_path / 'work'),
                        'RUNNER_TEMP': str(tmp_path), 'GITHUB_ACTIONS': 'true', 'GITHUB_SHA': expected,
                        'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '2', 'CANDIDATE_REF': expected}.items():
        monkeypatch.setenv(name, value)
    real_git = driver.git
    monkeypatch.setattr(driver, 'git', lambda where, *args: 'pin' if where.name == 'dtu-core' else real_git(where, *args))
    class BackendNotStarted(Exception):
        pass
    def before_backend(argv, **kwargs):
        assert argv == ['docker', 'version']
        record = json.loads((root / 'output/prepared-runtime-native/selection.json').read_text())
        assert record['revision'] == record['candidate'] == expected and record['runAttempt'] == '2'
        assert record['native'] == 'unrun'
        raise BackendNotStarted
    monkeypatch.setattr(driver, 'run', before_backend)
    with pytest.raises(BackendNotStarted):
        driver.main()


async def test_reused_native_app_explicitly_selects_each_profile_workspace(tmp_path):
    from amplifier_web.service import AppService
    from test_prepared_preset_native import NoModelRuntime
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir(); second.mkdir()
    app = AppService(tmp_path / 'app', NoModelRuntime(), workspace=first)
    await app.close()
    app = AppService(tmp_path / 'app', NoModelRuntime(), workspace=second)
    try:
        assert not any(w['path'] == str(second) for w in app.state['workspaces'])
        await app.dispatch('workspace.add', {'path': str(second)})
        selected = next(w for w in app.state['workspaces'] if w['id'] == app.state['selectedWorkspaceId'])
        assert selected['path'] == str(second)
    finally:
        await app.close()
