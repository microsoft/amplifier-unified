"""The app's real update compatibility probe in a stage folder, on every platform.

``tests/test_windows_long_paths_off.py`` runs the same probe on native Windows
under the longest installation folder path that setup supports.
"""
from __future__ import annotations

import json
import os
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


def test_the_decision_catalog_helper_is_read_from_the_app_and_started_as_the_app_starts_it():
    """``tests/test_windows_chat_setup.py`` starts this helper itself in each new worker environment.

    The call is the app's own: its command, its settings and its time limit. Reading it starts nothing and
    leaves the app's saved answer for this process as it was.
    """
    from amplifier_web import decision_accounts
    run, saved = subprocess.run, decision_accounts._catalog_snapshot.cache_info()
    command, settings = probe.catalog_helper_call()
    assert subprocess.run is run and decision_accounts._catalog_snapshot.cache_info() == saved
    script = probe.ROOT / 'amplifier_web' / 'decision_account_catalog.py'
    assert command == [sys.executable, '-I', str(script)] and script.is_file()
    # The app's limit for one start, and the few settings that the helper gets. No setting stops bytecode.
    assert settings['timeout'] > 0 and settings['check'] is True
    assert set(settings['env']) <= {'PATH', 'SYSTEMROOT', 'WINDIR', 'LANG', 'LC_ALL', 'TZ', 'TMPDIR', 'TEMP', 'TMP'}
    # The same call with another time limit: each start prints the catalog.
    times = probe.catalog_helper_times((command, settings), sys.executable)
    assert len(times) == 2 and all(0 < seconds < 300 for seconds in times), times
    with pytest.raises(subprocess.TimeoutExpired):   # the limit of a start is the caller's
        probe.catalog_helper_times((command, settings), sys.executable, starts=1, limit=0.001)


@pytest.mark.skipif(os.name == 'nt', reason='On Windows the switch is the subject: tests/test_windows_long_paths_off.py')
def test_the_reproduction_switch_changes_only_the_project_name(layout):
    """Off Windows the name is the CLI name with and without the switch: the same probe passes."""
    stage = probe.build(layout.app, layout.fixtures)
    result = probe.run(stage, capture_root=layout.capture, source_store=layout.sources, state_home=layout.state,
                       before_the_fix=True)
    probe.passed(result)
    assert [path.name for path in (stage.shared / 'projects').iterdir()] == [probe.cli_name(stage.workspace)]
