"""The app's real update compatibility probe in a stage folder, on every platform.

``tests/test_windows_long_paths_off.py`` runs the same probe on native Windows
under the longest installation folder path that setup supports.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

import update_stage_probe as probe

@pytest.fixture
def layout(tmp_path):
    folders = {name: tmp_path / name for name in ('app', 'fixtures', 'capture', 'sources', 'state')}
    for name, folder in folders.items():
        if os.name == 'nt' and name == 'app':   # The app home of an installation is a private folder.
            from amplifier_web.windows_files import directory
            with directory(folder, owned_root=folder, create=True):
                pass
        else:
            folder.mkdir()
    return SimpleNamespace(**folders)


def test_the_real_probe_completes_in_a_stage_and_writes_only_there_and_in_the_capture_folder(layout):
    stage = probe.build(layout.app, layout.fixtures)
    result = probe.run(stage, capture_root=layout.capture, source_store=layout.sources, state_home=layout.state)
    probe.passed(result)
    project = probe.expected_project(stage)
    assert [path.name for path in (stage.shared / 'projects').iterdir()] == [project]
    session = probe.session_of(stage, project)
    assert probe.left_in_stage(stage, project, session) == probe.STAGE_AFTER_A_PROBE
    assert probe.names(layout.capture, project, session) == probe.CAPTURE_AFTER_A_PROBE
    # The app home has the stage and nothing else; the other folders of the installation are untouched.
    assert probe.created(layout.app)[:3] == ['updates/', 'updates/releases/', f'updates/releases/{stage.stage.name}/']
    assert all(name.startswith(f'updates/releases/{stage.stage.name}/') for name in probe.created(layout.app)[3:])
    assert probe.created(layout.sources) == [] and probe.created(layout.state) == []


def test_the_probe_of_a_chats_own_setup_runs_with_the_chats_folder_and_writes_nothing_there(layout, tmp_path):
    """``runtime_profiles.ensure`` gives the probe the folder of the chat, not the stage's check folder."""
    chosen = tmp_path / 'a folder that a person chose'
    chosen.mkdir()
    (chosen / 'notes.txt').write_text('mine', encoding='utf-8')
    stage = probe.for_chat(probe.build(layout.app, layout.fixtures), chosen)
    result = probe.run(stage, capture_root=layout.capture, source_store=layout.sources, state_home=layout.state)
    probe.passed(result)
    project = probe.expected_project(stage)
    assert [path.name for path in (stage.shared / 'projects').iterdir()] == [project]
    session = probe.session_of(stage, project)
    assert probe.left_in_stage(stage, project, session) == probe.STAGE_AFTER_A_PROBE
    assert probe.names(layout.capture, project, session) == probe.CAPTURE_AFTER_A_PROBE
    assert probe.created(chosen) == ['notes.txt'] and (chosen / 'notes.txt').read_text(encoding='utf-8') == 'mine'
    # The session of the check names the chat's folder, not the stage's check folder.
    record = json.loads((stage.shared / 'projects' / project / 'sessions' / session / 'metadata.json').read_text())
    assert os.path.normcase(record['working_dir']) == os.path.normcase(str(chosen))
    profiles = (probe.ROOT / 'amplifier_web/runtime_profiles.py').read_text(encoding='utf-8')
    assert 'workspace = config.workspace' in profiles
    assert len(re.findall(r'str\(workspace\),\s+profile,', profiles)) == 2   # both probes get the chat's folder


def test_the_probe_still_refuses_a_stage_whose_provider_does_not_load(layout):
    stage = probe.build(layout.app, layout.fixtures)
    profile = json.loads(stage.profile.read_text(encoding='utf-8'))
    profile['providers'] = [{'module': 'provider-update-fixture-missing', 'source': str(layout.fixtures / 'absent')}]
    stage.profile.write_text(json.dumps(profile), encoding='utf-8')
    result = probe.run(stage, capture_root=layout.capture, source_store=layout.sources, state_home=layout.state)
    assert result.returncode == 1 and result.facts, result.stdout + result.stderr
    assert result.facts['ok'] is False and result.facts['stage'] != 'complete', result.facts


def test_the_fixture_runs_the_command_and_the_stage_settings_of_the_updater():
    """The helper repeats these names; the updater owns them."""
    updates = (probe.ROOT / 'amplifier_web/updates.py').read_text(encoding='utf-8')
    assert "workspace = stage / 'qualification-workspace'" in updates
    assert "'AMPLIFIER_WEB_HOME':str(stage),'AMPLIFIER_HOME':str(stage/'shared-config')" in updates
    assert "str(Path(__file__).with_name('update_probe.py'))" in updates
    assert "flags.append('--global-only')" in updates and "flags.append('--read-only')" in updates
    assert "flags=['--install-overrides',str(overrides)]" in updates
    assert "stage=self.directory/'releases'/release" in updates and "shared_stage=stage/'shared-config'" in updates


def test_the_decision_catalog_helper_is_read_from_the_app_and_started_as_the_app_starts_it(monkeypatch):
    """``tests/test_windows_chat_setup.py`` starts this helper itself in each new worker environment.

    The call is the app's own: its command, its settings and its time limit. Reading it starts nothing and
    leaves the app's saved answer for this process as it was.
    """
    from amplifier_web import decision_accounts
    run, saved = subprocess.run, decision_accounts._catalog_snapshot.cache_info()
    command, settings = probe.catalog_helper_call()
    assert decision_accounts.subprocess is subprocess and subprocess.run is run
    assert decision_accounts._catalog_snapshot.cache_info() == saved
    script = probe.ROOT / 'amplifier_web' / 'decision_account_catalog.py'
    assert command[:2] == [sys.executable, '-I'] and len(command) == 3 and Path(command[2]).samefile(script)
    # The app's limit for one start, and the few settings that the helper gets. No setting stops bytecode.
    assert settings['timeout'] > 0 and settings['check'] is True and settings['stdout'] is subprocess.PIPE
    assert set(settings['env']) <= {'PATH', 'SYSTEMROOT', 'WINDIR', 'LANG', 'LC_ALL', 'TZ', 'TMPDIR', 'TEMP', 'TMP'}
    # The same call with another time limit: each start prints the catalog.
    times = probe.catalog_helper_times((command, settings), sys.executable)
    assert len(times) == 2 and all(0 < seconds < 300 for seconds in times), times
    # The interpreter and the limit are the caller's, the helper's error output is kept, and everything else
    # is the app's call. A start that fails is named as the test's own start, with that output. A recorder
    # shows it: a real start with a very small limit would depend on the speed of this machine.
    started = []

    def reaches_the_limit(arguments, **given):
        started.append((arguments, given))
        raise subprocess.TimeoutExpired(arguments, given['timeout'], stderr=b'the helper wrote this')

    with monkeypatch.context() as patch, pytest.raises(AssertionError) as failure:
        patch.setattr(subprocess, 'run', reaches_the_limit)
        probe.catalog_helper_times((command, settings), 'another-python', starts=2, limit=7)
    assert started == [(['another-python', *command[1:]], {**settings, 'timeout': 7, 'stderr': subprocess.PIPE})]
    assert isinstance(failure.value.__cause__, subprocess.TimeoutExpired)
    assert str(failure.value).startswith("The test's own start of the decision-catalog helper with another-python "
                                         'failed (TimeoutExpired: ')
    assert str(failure.value).endswith('It is not a command of the app. Error output of the helper: '
                                       'the helper wrote this')
    # A start that ends well and prints no catalog is named the same way.
    with monkeypatch.context() as patch, pytest.raises(AssertionError, match="The test's own start .* printed no catalog"):
        patch.setattr(subprocess, 'run', lambda arguments, **given: subprocess.CompletedProcess(
            arguments, 0, stdout=b'not a catalog', stderr=b''))
        probe.catalog_helper_times((command, settings), 'another-python', starts=1)


def test_the_ci_job_finds_the_lines_that_the_chat_setup_case_prints_for_the_helper():
    """The line of a measured start, and the two places of the workflow that read it.

    PowerShell's ``-match`` takes a regular expression and ignores case. The job keeps a line that begins
    with ``HELPER_LINE`` and warns for one that holds ``HELPER_LONGER``.
    """
    inside = probe.catalog_helper_line('prepare-' + 'a' * 32, 3.44, 0.92, 0, 674, 10)
    longer = probe.catalog_helper_line('q-' + 'b' * 32, 10.04, 1.0, 12, 755, 10)
    assert inside == (
        'MEASURED the decision-catalog helper in the new worker environment prepare-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa: '
        "the test's first start took 3.44 s; 674 more bytecode files were there after it (0 before it); "
        "the test's second start took 0.92 s; the app's time limit for one start is 10 s")
    assert longer == (
        'MEASURED the decision-catalog helper in the new worker environment q-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb: '
        "the test's first start took 10.04 s; 743 more bytecode files were there after it (12 before it); "
        "the test's second start took 1.00 s; the app's time limit for one start is 10 s; "
        'LONGER THAN THE TIME LIMIT OF THE APP')   # the printed time shows why
    # A start of exactly the limit is not longer than it.
    assert probe.HELPER_LONGER not in probe.catalog_helper_line('q-' + 'c' * 32, 10, 1.0, 0, 743, 10)
    workflow = (probe.ROOT / '.github' / 'workflows' / 'windows-path-length.yml').read_text(encoding='utf-8')
    patterns = re.findall(r"-match '([^']+)'", workflow)
    keeps, warns = '^' + probe.HELPER_LINE, probe.HELPER_LONGER
    assert patterns.count(keeps) == 1 and patterns.count(warns) == 1, patterns
    # Both texts are plain text for a regular expression: no character of them has a meaning there.
    assert not set(probe.HELPER_LINE + probe.HELPER_LONGER) & set('.^$*+?{}[]()|\\')
    for line, warned in ((inside, False), (longer, True)):
        assert re.search(keeps, line, re.IGNORECASE) and bool(re.search(warns, line, re.IGNORECASE)) is warned
    # pytest's report of a failure shows source lines, indented. The job takes none of them for a measurement.
    assert not re.search(keeps, '        print(update_stage_probe.' + inside, re.IGNORECASE)
    assert not re.search(keeps, 'E   ' + inside, re.IGNORECASE)


@pytest.mark.skipif(os.name == 'nt', reason='On Windows the switch is the subject: tests/test_windows_long_paths_off.py')
def test_the_reproduction_switch_changes_only_the_project_name(layout):
    """Off Windows the name is the CLI name with and without the switch: the same probe passes."""
    stage = probe.build(layout.app, layout.fixtures)
    result = probe.run(stage, capture_root=layout.capture, source_store=layout.sources, state_home=layout.state,
                       before_the_fix=True)
    probe.passed(result)
    assert [path.name for path in (stage.shared / 'projects').iterdir()] == [probe.cli_name(stage.workspace)]
