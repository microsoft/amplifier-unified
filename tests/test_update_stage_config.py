"""An update stage holds no copy of the app's own ``config`` and ``routing`` folders.

The app's ``config`` folder holds the sign-in secrets (``auth``), the TLS key
(``tls``) and other private files. Earlier versions copied the whole folder
into each stage that a component update prepared, and nothing read the copy: a
stage reads its settings from ``shared-config``.

Every private file here is made up. No case opens a real credential.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import time

import pytest

import update_stage_probe as probe
from amplifier_web import runtime_environment, runtime_qualification, updates
from amplifier_web.updates import active_release
from test_updates import app, prepare, repo  # noqa: F401 -- existing real manager fixture

MARK = 'made-up-file-of-the-app-home'
# Names that the app writes below its own ``config`` folder, and a routing file
# of an installation from before the shared settings. The contents are made up.
PRIVATE = {
    'config/auth/control-token': MARK + ' control token\n',
    'config/auth/session-secret': MARK + ' session secret\n',
    'config/auth/session-epoch': '1\n',
    'config/auth/terminal-devices.json': json.dumps({'grants': {}, 'devices': {}, 'note': MARK}),
    'config/tls/leaf.key': MARK + ' key\n',
    'config/tls/leaf.crt': MARK + ' certificate\n',
    'config/tls/ca.crt': MARK + ' root\n',
    'config/keys.env': 'FIXTURE_KEY=' + MARK + '\n',
    'config/chatgpt-sign-in/one/login.json': json.dumps({'note': MARK}),
    'config/services/task-runtime.json': json.dumps({'note': MARK}),
    'routing/custom.yaml': 'name: ' + MARK + '\n',
}


def app_files(home):
    """Write the made-up private files into the app home; what the app home holds then."""
    for name, content in PRIVATE.items():
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    return held(home)


def held(root, folders=('config', 'routing')):
    """Every file below these folders of ``root``: its bytes and its identity."""
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_ino)
            for folder in folders for path in sorted((root / folder).rglob('*')) if path.is_file()}


def holds_the_mark(stage):
    """The files of a stage that hold content of a made-up private file."""
    return sorted(path.relative_to(stage).as_posix() for path in stage.rglob('*')
                  if path.is_file() and not path.is_symlink() and MARK.encode() in path.read_bytes())


@pytest.mark.parametrize('outcome', ['settled', 'failed'])
async def test_a_new_stage_holds_no_copy_of_the_apps_config_or_routing(app, repo, outcome):
    """The real preparation of a component update; only its validation is a stand-in."""
    manager, _ = await prepare(app, repo)
    before = app_files(app.data_dir)
    assert {'config/auth/control-token', 'config/tls/leaf.key', 'config/settings.yaml',
            'routing/custom.yaml'} <= set(before)
    at_validation = []

    async def validate(stage, release):
        at_validation.append(sorted(path.name for path in stage.iterdir()))
        if outcome == 'failed':
            raise RuntimeError('incompatible')

    manager.validate = validate
    await manager.install()
    assert app.state['updates']['phase'] == ('installed' if outcome == 'settled' else 'error')
    stage, = (manager.directory / 'releases').iterdir()
    assert (stage / 'validated.json').is_file() == (outcome == 'settled')
    # What the stage's own checks read is there.
    assert (stage / 'foundation').is_dir() and (stage / 'shared-config/settings.yaml').is_file()
    # No entry at any moment, and no content of a private file anywhere in the stage.
    assert len(at_validation) == 1 and not {'config', 'routing'} & set(at_validation[0])
    assert not os.path.lexists(stage / 'config') and not os.path.lexists(stage / 'routing')
    assert holds_the_mark(stage) == []
    # The app's own files are the same files with the same bytes.
    assert held(app.data_dir) == before


def test_the_policy_inputs_of_a_stage_with_no_config_folder(tmp_path):
    """``policy_inputs`` names the stage's ``config`` folder. A stage has none now, and that is no change of an input."""
    from amplifier_web.runtime_policy_reconciliation import policy_inputs
    stage, project = tmp_path / 'stage', tmp_path / 'project'
    (stage / 'shared-config').mkdir(parents=True)
    (stage / 'shared-config/settings.yaml').write_text('a: 1\n')
    (stage / 'runtime-sources.json').write_text('{}')
    project.mkdir()
    (project / 'pyproject.toml').write_text('')
    first = policy_inputs(stage, project)
    assert policy_inputs(stage, project) == first
    listed = [Path(name) for name, _ in first if os.sep in name]
    assert stage / 'shared-config/settings.yaml' in listed
    assert not any(path.is_relative_to(stage / 'config') for path in listed)
    # A change of an input that the stage does hold is still a change.
    (stage / 'shared-config/settings.yaml').write_text('a: 2\n')
    assert policy_inputs(stage, project) != first


@pytest.mark.parametrize('made', ['before the change', 'after the change'])
async def test_a_restore_validates_and_selects_a_stage_made_before_and_after_the_change(
        app, repo, tmp_path, monkeypatch, made):
    """"Restore previous components" to a stage with the copies, and to a stage without them.

    The stage is of another app version, so the restore validates it again
    before it selects it. Real here: the update owner's restore, its second
    validation (``UpdateManager.validate``) and the app's probe
    (``update_probe.py``) as a child process with the stage as its app home.
    Not real: the stage has the updater's layout from the probe fixture, not a
    whole prepared update; the worker environment, its installer policy file
    and the check of its recorded packages are stand-ins, and the probe runs in
    the test environment's Python (``update_stage_probe``).
    """
    manager, _ = await prepare(app, repo)
    before = app_files(app.data_dir)
    fixtures = tmp_path / 'probe-fixtures'
    fixtures.mkdir()
    built = probe.build(app.data_dir, fixtures, stage_id='b' * 32)
    stage = built.stage
    (stage / 'foundation').mkdir()
    if made == 'before the change':
        # What the preparation of an earlier version did: the whole folders, with every private file.
        for name in ('config', 'routing'):
            shutil.copytree(app.data_dir / name, stage / name)
        copies = held(stage)
        assert set(copies) == set(before) and holds_the_mark(stage)
    else:
        assert not os.path.lexists(stage / 'config') and not os.path.lexists(stage / 'routing')
    (stage / 'validated.json').write_text(json.dumps({
        'hostVersion': 'an-earlier-app', 'sources': 1, 'createdAt': time.time(),
        'generationSchema': 1, 'workerProtocol': 1}))
    (stage / 'runtime.lock').write_text('')            # A settled stage: its worker environment is recorded.
    (stage / 'runtime-installed.json').write_text('[]')
    (manager.directory / 'active.json').write_text(json.dumps({'current': None, 'previous': stage.name}))

    project = tmp_path / 'worker-project'
    project.mkdir()

    async def worker_project(owner, generation, rows, *, finalize):
        assert generation == stage.name and finalize
        return project

    async def installer_policy(given, target, **settings):
        assert given == project and target == stage / 'runtime-install-overrides.txt'
        return built.overrides

    monkeypatch.setattr(runtime_environment, 'stage', worker_project)
    monkeypatch.setattr(runtime_qualification, 'prepare_overrides', installer_policy)
    monkeypatch.setattr(runtime_qualification, 'verify_recorded', lambda *given, **settings: None)
    script = str(probe.ROOT / 'amplifier_web' / 'update_probe.py')
    other, probes = updates.process, []

    async def process(*command, **settings):
        if script not in command:
            return await other(*command, **settings)
        # The updater's own arguments and stage settings for the probe; then the real probe with them.
        assert list(command[command.index(script) + 1:]) == [
            str(built.workspace), 'fixture', '--install-overrides', str(built.overrides),
            '--global-only', '--read-only']
        given = settings['env']
        assert given['AMPLIFIER_WEB_HOME'] == str(stage) and given['AMPLIFIER_HOME'] == str(built.shared)
        assert given['AMPLIFIER_UNIFIED_RELEASE'] == ''
        folders = {}
        for name in ('capture', 'sources', 'state'):
            folders[name] = tmp_path / ('probe-' + name)
            folders[name].mkdir()
        result = await asyncio.to_thread(probe.run, built, capture_root=folders['capture'],
                                         source_store=folders['sources'], state_home=folders['state'])
        probe.passed(result)
        probes.append(result.facts['stage'])

    monkeypatch.setattr(updates, 'process', process)
    await manager.rollback()
    state = app.state['updates']
    assert probes == ['complete']
    assert state['phase'] == 'installed' and state['error'] is None and state['release'] == stage.name
    assert active_release(app.data_dir)['current'] == stage.name
    assert json.loads((stage / 'validated.json').read_text())['hostVersion'] == __import__('amplifier_web').__version__
    assert (stage / 'profiles-qualified.json').is_file()
    if made == 'before the change':
        assert held(stage) == copies            # This restore removes nothing and changes no copy.
    else:
        assert not os.path.lexists(stage / 'config') and not os.path.lexists(stage / 'routing')
        assert holds_the_mark(stage) == []
    assert held(app.data_dir) == before
