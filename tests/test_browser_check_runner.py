"""Background failures must not conceal independent checks or leak fixtures."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import subprocess
import shutil
import pytest

import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('browser_check_runner', ROOT / 'scripts/browser_check_runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def execute(tmp_path, rows, **kwargs):
    (tmp_path / 'frontend').mkdir(exist_ok=True)
    output = tmp_path / 'results'
    code = runner.run_scenarios(tmp_path, {'schemaVersion': 1, 'scenarios': rows}, output,
                                provenance={'revision': 'a' * 40}, **kwargs)
    return code, json.loads((output / 'results.json').read_text()), output


def scenario(name, source):
    return {'id': name, 'command': [sys.executable, '-c', source]}


def test_failed_check_continues_and_keeps_original_failure_and_logs(tmp_path):
    code, report, output = execute(tmp_path, [scenario('fails', 'print("original failure");raise SystemExit(7)'),
                                             scenario('passes', 'print("independent result")')])
    assert code == 1 and report['complete'] and not report['passed']
    assert [row['status'] for row in report['scenarios']] == ['failed', 'passed']
    assert report['scenarios'][0]['exitCode'] == 7
    assert 'original failure' in (output / 'fails.log').read_text()
    assert 'independent result' in (output / 'passes.log').read_text()
    assert report['source']['revision'] == 'a' * 40


@pytest.mark.parametrize('detached', [False, True])
def test_owned_orphan_is_reaped_and_reported_before_next_check(tmp_path, detached):
    pid = tmp_path / 'child.pid'
    source = ('import subprocess,sys;from pathlib import Path;'
              f'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],start_new_session={detached!r});Path({str(pid)!r}).write_text(str(p.pid))')
    code, report, _ = execute(tmp_path, [scenario('leaks', source), scenario('next', 'pass')])
    assert code == 1 and report['scenarios'][0]['cleanup']['terminated'] == 1
    assert report['scenarios'][1]['status'] == 'passed'
    stat = Path('/proc') / pid.read_text() / 'stat'
    assert not stat.exists() or stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z'


def test_uncertain_cleanup_stops_and_records_unrun_without_false_cascade(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'retire_group', lambda group, token: {'remaining': [123], 'terminated': 1})
    code, report, _ = execute(tmp_path, [scenario('first', 'pass'), scenario('must-not-run', 'raise Exception("not reached")')])
    assert code == 1 and not report['complete']
    assert report['scenarios'][1]['status'] == 'unrun'
    assert report['scenarios'][1]['reason'] == 'unsafe_cleanup'
    assert not (tmp_path / 'results/must-not-run.log').exists()


def test_last_successful_exit_with_unreadable_ownership_still_fails(tmp_path, monkeypatch):
    def unavailable(*args):
        raise PermissionError('Cannot prove process ownership')
    monkeypatch.setattr(runner, 'retire_group', unavailable)
    code, report, _ = execute(tmp_path, [scenario('last', 'pass')])
    assert code == 1 and not report['passed']
    assert report['scenarios'][0]['status'] == 'failed'
    assert report['scenarios'][0]['cleanup']['remaining'] == ['unknown']


def test_timeout_kills_only_owned_process_and_next_check_runs(tmp_path):
    code, report, _ = execute(tmp_path, [scenario('timeout', 'import time;time.sleep(60)'),
                                        scenario('after', 'pass')], case_timeout=.15)
    assert code == 1 and report['scenarios'][0]['reason'] == 'timeout'
    assert report['scenarios'][0]['cleanup']['remaining'] == []
    assert report['scenarios'][1]['status'] == 'passed'


def test_cleanup_does_not_signal_an_unrelated_live_process(tmp_path):
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], start_new_session=True)
    try:
        code, report, _ = execute(tmp_path, [scenario('passes', 'pass')])
        assert code == 0 and unrelated.poll() is None
        assert report['scenarios'][0]['cleanup']['terminated'] == 0
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=2)


def test_budget_never_claims_unexecuted_checks_passed(tmp_path):
    code, report, _ = execute(tmp_path, [scenario('not-started', 'pass')], budget=0)
    assert code == 1 and not report['complete']
    assert report['scenarios'][0]['status'] == 'unrun'
    assert report['scenarios'][0]['reason'] == 'budget_exhausted'


def test_running_and_unrun_coverage_is_durable_before_execution(tmp_path):
    proof = tmp_path / 'results/results.json'
    source = (f'import json;from pathlib import Path;r=json.loads(Path({str(proof)!r}).read_text());'
              'assert [x["status"] for x in r["scenarios"]] == ["running","unrun"];assert not r["complete"]')
    code, report, _ = execute(tmp_path, [scenario('reads-proof', source), scenario('last', 'pass')])
    assert code == 0 and report['complete'] and report['passed']


def test_cancellation_reaps_current_and_marks_remainder_unrun(tmp_path):
    source = 'import os,signal,time;os.kill(os.getppid(),signal.SIGTERM);time.sleep(60)'
    code, report, _ = execute(tmp_path, [scenario('cancel', source), scenario('later', 'pass')])
    assert code == 1 and not report['complete']
    assert report['scenarios'][0]['reason'] == 'cancelled'
    assert report['scenarios'][0]['cleanup']['remaining'] == []
    assert report['scenarios'][1]['status'] == 'unrun'


def test_cli_initialization_and_execution_record_exact_source_and_plan(tmp_path):
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'frontend').mkdir()
    shutil.copy(ROOT / 'scripts/browser_check_runner.py', tmp_path / 'scripts/browser_check_runner.py')
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'schemaVersion': 1, 'scenarios': [scenario('control', 'pass')]}))
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path, stderr=subprocess.DEVNULL).decode().strip()
    git('init', '-q')
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Synthetic runner source')
    command = [sys.executable, 'scripts/browser_check_runner.py', '--plan', 'plan.json', '--output', 'evidence']
    subprocess.run([*command, '--initialize'], cwd=tmp_path, check=True)
    initial = json.loads((tmp_path / 'evidence/results.json').read_text())
    assert not initial['passed'] and not initial['complete']
    assert initial['scenarios'][0]['status'] == 'unrun'
    subprocess.run(command, cwd=tmp_path, check=True)
    result = json.loads((tmp_path / 'evidence/results.json').read_text())
    assert result['passed'] and result['source']['revision'] == git('rev-parse', 'HEAD')
    assert result['source']['tree'] == git('rev-parse', 'HEAD^{tree}')
    assert result['source']['planSha256'] == hashlib.sha256(plan.read_bytes()).hexdigest()


def test_existing_scenario_commands_environments_and_distinct_modes_are_preserved():
    plan = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())
    rows = runner.validate_plan(plan)
    # Expanded exact .149 workflow invocations, including compound npm scripts.
    # Deliberate coverage changes must update this migration oracle explicitly.
    assert len(rows) == 65
    assert hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest() == 'ca60c5f5e0a9bf5fd94795d64ea6c8d9c286b2c5f481b7d3c07d5f32fb522173'
    commands = [row['command'] for row in rows]
    assert ['node', 'tests/session-health-browser.mjs', '--context-limit', '--active-worker'] in commands
    assert ['node', 'tests/empty-host-browser.mjs', '--lost-create'] in commands
    assert all((ROOT / 'frontend' / command[1]).is_file() for command in commands)


def test_workflow_keeps_independent_artifacts_and_readiness_unique_python_coverage():
    workflows = ROOT / '.github/workflows'
    browser = yaml.load((workflows / 'browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    steps = browser['jobs']['browser']['steps']
    assert any('browser_check_runner.py' in step.get('run', '') for step in steps)
    uploads = [step for step in steps if step.get('uses') == 'actions/upload-artifact@v4']
    assert len(uploads) == 1 and uploads[0]['if'] == 'always()'
    assert 'output/browser-checks/' in uploads[0]['with']['path']
    readiness = (workflows / 'public-readiness.yml').read_text()
    files = re.findall(r'tests/test_\w+\.py', readiness)
    assert len(files) == len(set(files)) == 98
