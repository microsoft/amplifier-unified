"""The workflow that requires the provider state-file cases must fail when a case fails or does not run.

A ``run`` step without ``shell: bash`` gets no ``pipefail``. Then ``pytest | tee`` has the exit status of
``tee``, and a step can pass while a case fails (a review finding of this workflow's first form). The cases
here run each step's own script, the way GitHub runs it, with a stand-in that prints a given pytest result:
the Linux step under bash, and the Windows step under PowerShell on native Windows. The CI job there sets
``AMPLIFIER_REQUIRE_PROVIDER_STATE_FILES``: then a missing PowerShell fails; it never skips.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/provider-state-files.yml'
REQUIRE = 'AMPLIFIER_REQUIRE_PROVIDER_STATE_FILES'
# A pytest result: its summary line, pytest's exit status, and whether the step may pass.
RESULTS = [
    ('20 passed in 1.89s', 0, True),
    ('20 passed, 2 warnings in 1.89s', 0, True),
    ('1 failed, 19 passed in 6.04s', 1, False),    # The first native Windows run of this workflow.
    ('20 passed in 1.89s', 1, False),               # The status of pytest alone refuses.
    ('1 failed, 19 passed in 6.04s', 0, False),    # The summary line alone refuses.
    ('19 passed, 1 skipped in 2.00s', 0, False),
    ('19 passed, 1 deselected in 2.00s', 0, False),
    ('19 passed, 1 xfailed in 2.00s', 0, False),
    ('1 error in 0.50s', 2, False),
    ('no tests ran in 0.01s', 5, False),
    ('', 0, False),
]


def document():
    return yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))


def required_step(job):
    """The step of this job that runs the state-file cases."""
    step, = [step for step in document()['jobs'][job]['steps']
             if 'tests/test_provider_state_files.py' in step.get('run', '')]
    assert step['env'][REQUIRE] == '1'
    return step


@pytest.mark.skipif(os.name == 'nt', reason='The Linux step is a bash script')
@pytest.mark.parametrize('summary,status,passes', RESULTS)
def test_the_linux_step_passes_only_when_every_case_passed(tmp_path, summary, status, passes):
    step = required_step('linux')
    tools = tmp_path / 'tools'
    tools.mkdir()
    stand_in = tools / 'uv'
    # What "uv run --no-sync pytest ..." prints and returns.
    stand_in.write_text('#!/bin/sh\nprintf "%s\\n\\n" "PASSED tests/a.py::case" "$SUMMARY"\nexit "$STATUS"\n')
    stand_in.chmod(0o755)
    script = tmp_path / 'step.sh'
    script.write_text(step['run'])
    # GitHub runs "shell: bash" as "bash --noprofile --norc -eo pipefail {0}", and a step without a shell as
    # "bash -e {0}".
    options = '-eo pipefail' if step.get('shell') == 'bash' else '-e'
    done = subprocess.run(['bash', '--noprofile', '--norc', *options.split(), str(script)], cwd=tmp_path,
                          env={'PATH': f'{tools}:/usr/bin:/bin', 'SUMMARY': summary, 'STATUS': str(status)},
                          capture_output=True, text=True, timeout=30)
    assert (done.returncode == 0) is passes, (done.returncode, done.stdout, done.stderr)
    assert 'uv run --no-sync pytest' in step['run'] and 'tests/test_provider_state_files.py' in step['run']


@pytest.mark.parametrize('summary,status,passes', RESULTS)
def test_the_windows_step_checks_the_status_of_pytest_and_then_the_same_summary_rule(summary, status, passes):
    script = required_step('windows')['run']
    lines = [line.strip() for line in script.splitlines() if line.strip()]
    assert 'pytest' in lines[0] and 'tests/test_provider_state_files.py' in lines[0] and '| Tee-Object' in lines[0]
    assert lines[1].startswith('if ($LASTEXITCODE -ne 0) { throw ')   # Directly after pytest, before any other program.
    pattern, = re.findall(r"if \(\$summary -notmatch '([^']+)'\) \{ throw ", script)
    # Exactly the rule of the Linux step. PowerShell and Python read this pattern the same way.
    linux, = re.findall(r"grep -qE '([^']+)'", required_step('linux')['run'])
    assert pattern.replace(r'\d', '[0-9]') == linux
    assert (status == 0 and re.search(pattern, summary) is not None) is passes


def powershell():
    found = shutil.which('pwsh')
    if found is None:
        reason = 'PowerShell 7 (pwsh) is not on this PC'
        if os.environ.get(REQUIRE) == '1':
            pytest.fail(reason)
        pytest.skip(reason)
    return found


@pytest.fixture(scope='module')
def windows_step_folder(tmp_path_factory):
    """What the Windows step starts: ``.venv\\Scripts\\python.exe``, and beside it a stand-in for pytest."""
    import venv
    folder = tmp_path_factory.mktemp('windows-step')
    venv.create(folder / '.venv', with_pip=False)
    # "python -m pytest" finds this file first: the folder is the working folder of the step.
    (folder / 'pytest.py').write_text(
        'import os, sys\nprint("PASSED tests/a.py::case")\nprint(os.environ["SUMMARY"])\nprint()\n'
        'sys.exit(int(os.environ["STATUS"]))\n', encoding='utf-8')
    return folder


@pytest.mark.skipif(os.name != 'nt', reason='The Windows step is a PowerShell script that starts .venv\\Scripts\\python.exe')
@pytest.mark.parametrize('summary,status,passes', RESULTS)
def test_the_windows_step_own_script_passes_only_when_every_case_passed(windows_step_folder, summary, status, passes):
    step = required_step('windows')
    assert step['shell'] == 'pwsh'
    script = windows_step_folder / 'step.ps1'
    # GitHub puts these two lines around the script of a "shell: pwsh" step and runs: pwsh -command ". '{0}'"
    # -NoProfile and -NonInteractive below are this test's own: a profile of this PC must not change the result.
    script.write_text("$ErrorActionPreference = 'stop'\n" + step['run']
                      + "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n",
                      encoding='utf-8')
    done = subprocess.run([powershell(), '-NoProfile', '-NonInteractive', '-Command', f". '{script}'"],
                          cwd=windows_step_folder, env={**os.environ, 'SUMMARY': summary, 'STATUS': str(status)},
                          capture_output=True, text=True, timeout=120)
    assert (done.returncode == 0) is passes, (done.returncode, done.stdout[-600:], done.stderr[-600:])


def test_every_native_step_that_requires_cases_checks_the_status_and_then_one_summary_rule():
    steps = [step for step in document()['jobs']['windows']['steps'] if REQUIRE in (step.get('env') or {})]
    assert len(steps) == 2   # The state-file cases, and the cases that run that step's own script.
    rules = set()
    for step in steps:
        assert step['shell'] == 'pwsh' and step['env'][REQUIRE] == '1'
        lines = [line.strip() for line in step['run'].splitlines() if line.strip()]
        assert ' -m pytest ' in lines[0] and lines[0].endswith('| Tee-Object -Variable report')
        assert lines[1].startswith('if ($LASTEXITCODE -ne 0) { throw ')
        assert lines[2] == '$summary = [string]($report | Where-Object { $_ } | Select-Object -Last 1)'
        rule, = re.findall(r"^if \(\$summary -notmatch '([^']+)'\) \{ throw ", lines[3])
        rules.add(rule)
        assert len(lines) == 4
    assert len(rules) == 1
    # The second step names the cases by their test name, so that none is deselected.
    assert ('"tests/test_provider_state_files_workflow.py::'
            + test_the_windows_step_own_script_passes_only_when_every_case_passed.__name__ + '"') in steps[1]['run']


def test_a_change_to_a_file_that_the_cases_use_starts_the_workflow():
    workflow = document()
    paths = workflow.get('on', workflow.get(True))['pull_request']['paths']   # YAML reads the key "on" as true.
    cases = (ROOT / 'tests/test_provider_state_files.py').read_text(encoding='utf-8')
    used = {f'tests/{name}.py' for name in re.findall(r'^\s*(?:from|import) (test_\w+)', cases, re.M)}
    assert used == {'tests/test_host_session_resume.py', 'tests/test_provider_environment.py',
                    'tests/test_windows_path_budget.py'}
    for path in {*used, 'tests/test_provider_state_files.py', 'tests/test_provider_state_files_workflow.py',
                 '.github/workflows/provider-state-files.yml', 'amplifier_web/provider_state_files.py',
                 'amplifier_web/host/initialization.py', 'amplifier_web/provider_probe.py',
                 'amplifier_web/portability_probe.py', 'uv.lock'}:
        assert path in paths, path
    # The native job installs the locked dependencies without the project, so the cases there cannot import
    # a test file that needs a package of the project's components.
    native = ' '.join(step.get('run', '') for step in workflow['jobs']['windows']['steps'])
    assert '--no-install-project' in native
    for name in used:
        assert 'amplifier_worktrees' not in (ROOT / name).read_text(encoding='utf-8'), name
