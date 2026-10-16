"""Background failures must not conceal independent checks or leak fixtures."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
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
    monkeypatch.setattr(runner, 'retire_group', lambda group, token, preexisting: {'remaining': [123], 'terminated': 1})
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


def test_preexisting_unreadable_process_does_not_block_independent_checks(tmp_path, monkeypatch):
    unrelated = subprocess.Popen([sys.executable, '-c',
        'import ctypes,time;ctypes.CDLL(None).prctl(4,0,0,0,0);print("ready",flush=True);time.sleep(60)'],
        stdout=subprocess.PIPE, start_new_session=True)
    try:
        assert unrelated.stdout.readline() == b'ready\n'
        path = Path('/proc') / str(unrelated.pid) / 'environ'
        original_open = Path.open
        attempted = []
        def protected(self, *args, **kwargs):
            if self == path:
                attempted.append(True)
                raise PermissionError('Unrelated protected process')
            return original_open(self, *args, **kwargs)
        # Kernel restrictions vary with container capabilities. This preserves
        # the real process/start identity while making the denial deterministic.
        monkeypatch.setattr(Path, 'open', protected)
        code, report, _ = execute(tmp_path, [scenario('first', 'pass'), scenario('last', 'pass')])
        assert code == 0 and report['passed'] and report['complete']
        assert not attempted and unrelated.poll() is None
        assert all(row['cleanup'] == {'remaining': [], 'terminated': 0} for row in report['scenarios'])
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=2)


def test_new_unreadable_identity_is_not_exempt_and_stops_coverage(tmp_path, monkeypatch):
    ready = tmp_path / 'owned.pid'
    source = ('import subprocess,sys,json;from pathlib import Path;'
              'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],start_new_session=True);'
              'birth=Path(f"/proc/{p.pid}/stat").read_text().rsplit(")",1)[1].split()[19];'
              f'Path({str(ready)!r}).write_text(json.dumps({{"pid":p.pid,"birth":birth}}))')
    original_open = Path.open
    def protected(self, *args, **kwargs):
        if self.name == 'environ' and ready.exists() and self.parent.name == str(json.loads(ready.read_text())['pid']):
            raise PermissionError('New potentially owned process')
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', protected)
    try:
        code, report, _ = execute(tmp_path, [scenario('new-protected', source), scenario('not-run', 'pass')])
        assert code == 1 and not report['complete']
        assert report['scenarios'][0]['cleanup']['remaining'] == ['unknown']
        assert report['scenarios'][1]['reason'] == 'unsafe_cleanup'
    finally:
        if ready.exists():
            # Test owns this exact child; the runner correctly refused to infer
            # its ownership from an unreadable environment.
            child = json.loads(ready.read_text())
            handle = os.pidfd_open(child['pid'])
            try:
                birth = Path(f'/proc/{child["pid"]}/stat').read_text().rsplit(')', 1)[1].split()[19]
                assert birth == child['birth']
                signal.pidfd_send_signal(handle, signal.SIGKILL)
            finally:
                os.close(handle)


def test_reused_pid_requires_fresh_proof_but_known_owner_precedes_baseline(monkeypatch):
    own = Path('/proc') / str(os.getpid())
    birth = (own / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    original_open = Path.open
    def protected(self, *args, **kwargs):
        if self == own / 'environ':
            raise PermissionError('Reused PID must be inspected')
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', protected)
    baseline = runner.process_identities()
    baseline[os.getpid()] = str(int(birth) - 1)
    with pytest.raises(PermissionError):
        runner.owned_members(-1, 'unused', {}, baseline)
    assert runner.owned_members(-1, 'unused', {os.getpid(): birth}, baseline)[os.getpid()] == birth


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
    additions = {
        'device-pairing': {},
        'standard-ux-keyboard-reflow': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/standard-ux-keyboard-reflow'},
        'smart-tool-updates': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/smart-tool-updates-artifacts'},
        'archive-feedback': {'AMPLIFIER_TEST_OUTPUT': '{root}/output/browser-checks/archive-feedback'},
        'update-restart': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/update-restart-artifacts'},
        'sidebar-reading': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/sidebar-reading-artifacts'},
        'navigation-details-context': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/navigation-details-context-artifacts'},
        'pin-retention': {'AMPLIFIER_TEST_OUTPUT': '{root}/output/browser-checks/pin-retention'},
        'workspace-shortcuts': {'AMPLIFIER_TEST_OUTPUT': '{root}/output/browser-checks/workspace-shortcuts'},
        'mcp-recovery': {'MCP_RECOVERY_SCREENSHOT': '{root}/output/browser-checks/mcp-recovery.png'},
        'inline-media-transition': {'EVIDENCE_DIR': '{root}/output/browser-checks/inline-media-transition'},
    }
    added = [row for row in rows if row['id'] in additions]
    assert len(added) == len(additions)
    for row in added:
        assert row['command'] == ['node', f"tests/{row['id']}-browser.mjs"]
        assert row.get('env', {}) == additions[row['id']]
    original = [row for row in rows if row['id'] not in additions]
    assert len(original) == 65 and len(rows) == 76
    # The same bundle scenario now writes screenshots into its owned evidence.
    bundle = next(row for row in original if row['id'] == 'bundle')
    assert bundle['env'] == {'AMPLIFIER_BUNDLE_SCREENSHOT': '{root}/output/browser-checks/bundle-mobile.png'}
    original = [{key: value for key, value in row.items() if key != 'env'} if row['id'] == 'bundle' else row for row in original]
    assert hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest() == '115d865e2934f442582f89f77bde590ebc6101cb535c841efbba8f3e2511807f'
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


def test_source_browser_duplicates_have_one_owner_and_package_overlaps_remain():
    workflows = ROOT / '.github/workflows'
    readiness = yaml.load((workflows / 'public-readiness.yml').read_text(), Loader=yaml.BaseLoader)
    steps = readiness['jobs']['critical']['steps']
    commands = [(index, line.strip()) for index, step in enumerate(steps)
                for line in step.get('run', '').splitlines()]
    readiness_scripts = {match.group(1) for _, line in commands
                         if (match := re.search(r'node (tests/[^ ]+-browser\.mjs)', line))}
    plan = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())
    extended_scripts = {row['command'][1] for row in plan['scenarios']}
    source_only = {f'tests/{name}-browser.mjs' for name in (
        'interim-replies', 'draft-reload', 'draft-switch', 'activity-feedback',
        'conversation-navigation', 'workspace-shortcuts', 'inline-media-transition')}
    assert source_only <= extended_scripts
    assert not source_only & readiness_scripts
    installed_overlap = {f'tests/{name}-browser.mjs' for name in (
        'feedback', 'message-outbox', 'canonical-work')}
    assert readiness_scripts & extended_scripts == installed_overlap
    install = next(index for index, line in commands if 'uv pip install' in line and '--reinstall .' in line)
    for script in installed_overlap:
        assert any(index > install and f'node {script}' in line for index, line in commands)
    appearance_preview = 'tests/appearance-preview-navigation-browser.mjs'
    assert appearance_preview in readiness_scripts
    assert appearance_preview not in extended_scripts
    assert sum(f'node {appearance_preview}' in line for _, line in commands) == 1
    # All 108 original scripts, three navigation regressions, confirmed restart,
    # Smart Tool recovery, archive feedback and appearance preview navigation
    # and sequential keyboard reflow, saved MCP recovery, device pairing, and media transition
    # each retain an explicit owning lane.
    assert len(readiness_scripts | extended_scripts) == 119
    assert 'tests/mcp-recovery-browser.mjs' in extended_scripts - readiness_scripts
    assert 'tests/device-pairing-browser.mjs' in extended_scripts - readiness_scripts

    # The environment journey belongs to coordination, outside these two lanes.
    environment = 'tests/test-environments-browser.mjs'
    assert environment not in readiness_scripts | extended_scripts
    owners = []
    for path in workflows.glob('*.yml'):
        value = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for job in value.get('jobs', {}).values():
            for step in job.get('steps', []):
                if f'node {environment}' in step.get('run', ''):
                    owners.append((path.name, step))
    assert len(owners) == 1 and owners[0][0] == 'coordination.yml'
    assert owners[0][1]['env']['AMPLIFIER_TEST_OUTPUT_DIR'] == '${{ runner.temp }}/test-environments'
    coordination = yaml.load((workflows / 'coordination.yml').read_text(), Loader=yaml.BaseLoader)
    assert 'frontend/' + environment in coordination['on']['pull_request']['paths']
    assert any(step.get('if') == 'always()' and step.get('uses') == 'actions/upload-artifact@v4'
               and step.get('with', {}).get('path') == '${{ runner.temp }}/test-environments'
               for step in coordination['jobs']['contracts']['steps'])


def test_performance_reports_are_distinct_and_uploaded():
    rows = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())['scenarios']
    reports = {row['id']: row['env']['AMPLIFIER_PERF_EVIDENCE'] for row in rows
               if 'AMPLIFIER_PERF_EVIDENCE' in row.get('env', {})}
    assert len(set(reports.values())) == len(reports), 'A later scenario must not overwrite prior diagnostics'
    assert reports['active-client-performance'] != reports['state-transport']
    # Demand has no performance report writer; it must not borrow another path.
    assert 'demand' not in reports
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    uploads = [step for step in workflow['jobs']['browser']['steps']
               if step.get('uses') == 'actions/upload-artifact@v4']
    directories = [line.strip().rstrip('/') + '/' for step in uploads
                   for line in step['with']['path'].splitlines() if line.strip()]
    assert all(any(path.removeprefix('{root}/').startswith(directory) for directory in directories)
               for path in reports.values())
