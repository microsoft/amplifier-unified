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


def watch(manager):
    """Read the entries of every stage at each publication of the update owner; the list of those readings.

    A reading is (the moment, the stage's entries). The moment is the phase
    that the publication carries ('staging', 'validating', 'staged', 'error',
    'installed'), or 'publication' for one that carries none (a download
    detail, the plan, the publication after the switch). These are the moments
    that a case sees. Between two of them it sees nothing. ``no_copy`` rests on
    the 'validating' reading: it comes after the place of the copies.
    """
    seen, publish = [], manager.publish
    releases = manager.directory / 'releases'

    async def observe(**values):
        moment = values.get('phase') or 'publication'
        for stage in sorted(releases.iterdir()) if releases.is_dir() else ():
            seen.append((moment, sorted(path.name for path in stage.iterdir())))
        return await publish(**values)

    manager.publish = observe
    return seen


def no_copy(seen):
    """No reading shows a copy, and a reading from after the preparation's copies is among them."""
    assert any('shared-config' in entries for moment, entries in seen if moment == 'validating'), seen
    assert not [reading for reading in seen if {'config', 'routing'} & set(reading[1])], seen


@pytest.mark.parametrize('outcome', ['settled', 'failed'])
async def test_a_new_stage_holds_no_copy_of_the_apps_config_or_routing(app, repo, outcome):
    """The real preparation of a component update, with made-up private files in the app home.

    Stand-ins: the validation here, and those of the shared ``app`` fixture
    (``test_updates.py``): no included sources, the check of the app release,
    and the address that the fetch uses. None of them is the step that made
    the copies.
    """
    manager, _ = await prepare(app, repo)
    before = app_files(app.data_dir)
    assert {'config/auth/control-token', 'config/tls/leaf.key', 'config/settings.yaml',
            'routing/custom.yaml'} <= set(before)
    seen = watch(manager)

    async def validate(stage, release):
        seen.append(('validation', sorted(path.name for path in stage.iterdir())))
        if outcome == 'failed':
            raise RuntimeError('incompatible')

    manager.validate = validate
    await manager.install()
    assert app.state['updates']['phase'] == ('installed' if outcome == 'settled' else 'error')
    stage, = (manager.directory / 'releases').iterdir()
    assert (stage / 'validated.json').is_file() == (outcome == 'settled')
    # What the stage's own checks read is there.
    assert (stage / 'foundation').is_dir() and (stage / 'shared-config/settings.yaml').is_file()
    # No entry at each publication of the preparation, at the start of the validation and after the
    # attempt. After the attempt, no file anywhere in the stage holds content of a private file.
    assert [moment for moment, _ in seen].count('validation') == 1
    no_copy(seen)
    assert not os.path.lexists(stage / 'config') and not os.path.lexists(stage / 'routing')
    assert holds_the_mark(stage) == []
    # The app's own files are the same files with the same bytes.
    assert held(app.data_dir) == before


async def test_a_retry_after_a_failed_preparation_makes_a_second_stage_with_no_copy(app, repo):
    """One update owner: a preparation that fails, then the same request again, which settles.

    The same stand-ins as in the case above.
    """
    manager, _ = await prepare(app, repo)
    before = app_files(app.data_dir)
    seen = watch(manager)
    outcomes = ['failed', 'settled']

    async def validate(stage, release):
        seen.append(('validation', sorted(path.name for path in stage.iterdir())))
        if outcomes.pop(0) == 'failed':
            raise RuntimeError('incompatible')

    manager.validate = validate
    releases = manager.directory / 'releases'
    await manager.install()
    assert app.state['updates']['phase'] == 'error' and not active_release(app.data_dir)
    failed, = releases.iterdir()
    await manager.install()            # A person's request, again: it is not held by the failure.
    assert outcomes == [] and app.state['updates']['phase'] == 'installed'
    settled, = set(releases.iterdir()) - {failed}
    assert active_release(app.data_dir)['current'] == settled.name
    assert not (failed / 'validated.json').exists() and (settled / 'validated.json').is_file()
    assert [moment for moment, _ in seen].count('validation') == 2
    no_copy(seen)
    for stage in (failed, settled):
        assert (stage / 'shared-config/settings.yaml').is_file()
        assert not os.path.lexists(stage / 'config') and not os.path.lexists(stage / 'routing')
        assert holds_the_mark(stage) == []
    assert held(app.data_dir) == before


def test_the_policy_inputs_of_a_stage_with_no_config_folder(tmp_path):
    """``policy_inputs`` still names the stage's ``config`` folder, and a stage has none.

    Shown: no error; two readings are equal; no file below ``config`` is
    listed; a change of ``shared-config/settings.yaml`` is still a change.
    Not compared: a stage that has the folder with a stage that has none. The
    updater never compares those two: both of its readings are of one preparation.
    """
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
@pytest.mark.parametrize('asked', ['restore', 'install'])
async def test_a_stage_of_an_earlier_app_version_is_validated_and_selected_with_and_without_the_copies(
        app, repo, tmp_path, monkeypatch, asked, made):
    """"Restore previous components", and Install of a prepared update, to a stage with the copies and to one without.

    The stage is of another app version, so the request validates it again
    before it selects it. Real here: the update owner's request (``rollback``,
    or ``install`` with a prepared update), its second validation
    (``UpdateManager.validate``) and the app's probe (``update_probe.py``) as a
    child process with the stage as its app home.

    Not real: the stage has the updater's layout from the probe fixture, not a
    whole prepared update. For Install the case itself writes the prepared
    state (``staged``, ``pendingRelease``) into the owner's state; no
    preparation published it. The stage's record names no checked selection (the
    settled case above checks the selection of a stage with no copies at its
    activation). The worker environment, its installer policy file and the
    check of its recorded packages are stand-ins. The probe runs in the test
    environment's Python: it is started without ``uv run``, and with the probe
    fixture's own settings for the child, not the updater's whole environment
    (``update_stage_probe.run``). Of the updater's settings the case checks
    three: the stage as app home, its ``shared-config`` as shared home, and the
    empty generation name.
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
    state = app.state['updates']
    if asked == 'restore':
        (manager.directory / 'active.json').write_text(json.dumps({'current': None, 'previous': stage.name}))
    else:
        state.update(phase='staged', pendingRelease=stage.name)

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
    other, probes = updates.process, []

    async def process(*command, **settings):
        script = next((index for index, part in enumerate(command)
                       if Path(str(part)).name == 'update_probe.py'), None)
        if script is None:
            return await other(*command, **settings)
        # The updater's own arguments and three of its stage settings for the probe; then the real probe.
        assert Path(command[script]).samefile(probe.ROOT / 'amplifier_web' / 'update_probe.py')
        assert list(command[script + 1:]) == [
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
    if asked == 'restore':
        await manager.rollback()
    else:
        await manager.install()
    assert probes == ['complete']
    assert state['phase'] == 'installed' and state['error'] is None
    assert state['release'] == stage.name and state['pendingRelease'] is None
    assert active_release(app.data_dir)['current'] == stage.name
    assert json.loads((stage / 'validated.json').read_text())['hostVersion'] == __import__('amplifier_web').__version__
    assert (stage / 'profiles-qualified.json').is_file()
    if made == 'before the change':
        assert held(stage) == copies            # The request removes nothing and changes no copy.
    else:
        assert not os.path.lexists(stage / 'config') and not os.path.lexists(stage / 'routing')
        assert holds_the_mark(stage) == []
    assert held(app.data_dir) == before
