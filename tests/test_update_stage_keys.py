"""Credential lifetimes use synthetic files only; no app or model is launched."""
import asyncio
import json
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import stat
import time
import uuid

import pytest

from amplifier_web import update_storage as storage
from amplifier_web.host.config import amplifier_home
from test_runtime_profile_inputs import profile_inputs  # noqa: F401 -- the real preparation of a chat's own setup
from test_updates import app, prepare, repo  # noqa: F401 -- existing real manager fixture


@pytest.fixture
async def staged_manager(app, repo, monkeypatch):
    """Real install owner/snapshots; no source fetch or qualification subprocess."""
    from types import SimpleNamespace
    from amplifier_web import update_partial
    manager, _ = await prepare(app, repo)
    async def recheck():
        pass
    async def stage_sources(*args):
        return SimpleNamespace(recheck=recheck)
    monkeypatch.setattr(update_partial, 'stage_sources', stage_sources)
    return manager


def stage_at(home, name='a' * 32, *, validated=False, age=0):
    stage = home / 'updates/releases' / name
    shared = stage / 'shared-config'
    shared.mkdir(parents=True)
    (shared / 'keys.env').write_bytes(b'FIXTURE_KEY=fake-old\n')
    (shared / 'keys.env').chmod(0o600)
    (shared / 'settings.yaml').write_text('fixture settings')
    (shared / 'routing').mkdir()
    (shared / 'routing/keep.yaml').write_text('fixture routing')
    if validated:
        (stage / 'validated.json').write_text('{}')
    for folder in (shared, stage):
        os.utime(folder, (time.time() - age, time.time() - age))
    return stage


def assert_preserved(stage):
    assert stage.is_dir()
    assert (stage / 'shared-config/settings.yaml').read_text() == 'fixture settings'
    assert (stage / 'shared-config/routing/keep.yaml').read_text() == 'fixture routing'


def test_remove_only_keys_and_absence_is_success(tmp_path):
    stage = stage_at(tmp_path)
    other = stage_at(tmp_path, 'b' * 32)
    assert storage.remove_stage_keys(tmp_path, stage) is None
    assert not (stage / 'shared-config/keys.env').exists()
    assert storage.remove_stage_keys(tmp_path, stage) is None
    assert storage.remove_stage_keys(tmp_path, tmp_path / 'updates/releases' / ('c' * 32)) is None
    assert (other / 'shared-config/keys.env').is_file()
    assert_preserved(stage)


@pytest.mark.parametrize('part', ['home', 'updates', 'releases', 'stage', 'shared-config', 'keys.env'])
def test_remove_refuses_symlink_at_every_level(tmp_path, part):
    home = tmp_path / 'app'
    stage = stage_at(home)
    original = {'home': home, 'updates': home / 'updates', 'releases': home / 'updates/releases',
                'stage': stage, 'shared-config': stage / 'shared-config',
                'keys.env': stage / 'shared-config/keys.env'}[part]
    saved = tmp_path / 'saved'
    original.rename(saved)
    original.symlink_to(saved, target_is_directory=part != 'keys.env')
    assert storage.remove_stage_keys(home, stage)
    assert (stage / 'shared-config/keys.env').is_file()
    assert original.is_symlink()


@pytest.mark.parametrize('kind', ['directory', 'fifo', 'hardlink'])
def test_remove_refuses_nonregular_or_linked_file(tmp_path, kind):
    stage = stage_at(tmp_path)
    key = stage / 'shared-config/keys.env'
    key.unlink()
    if kind == 'directory':
        key.mkdir()
    elif kind == 'fifo':
        os.mkfifo(key)
    else:
        original = tmp_path / 'fake-input'
        original.write_text('synthetic')
        key.hardlink_to(original)
    assert storage.remove_stage_keys(tmp_path, stage)
    assert key.exists()
    assert_preserved(stage)


@pytest.mark.parametrize('name', ['A' * 32, 'a' * 31, 'not-a-release', 'a' * 32 + '/nested'])
def test_remove_refuses_nonstage_path(tmp_path, name):
    stage = stage_at(tmp_path, name)
    assert storage.remove_stage_keys(tmp_path, stage)
    assert (stage / 'shared-config/keys.env').is_file()


@pytest.mark.parametrize('error', [PermissionError('fake private text'), OSError('fake private text')])
def test_remove_is_nonthrowing_and_reason_is_credential_free(tmp_path, monkeypatch, error):
    stage = stage_at(tmp_path)
    def refuse(*args, **kwargs):
        raise error
    monkeypatch.setattr(os, 'unlink', refuse)
    reason = storage.remove_stage_keys(tmp_path, stage)
    assert reason in {'refused', 'io'}
    assert (stage / 'shared-config/keys.env').is_file()


@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancelled'])
async def test_install_key_lifetime_at_publication(app, staged_manager, monkeypatch, outcome):
    manager = staged_manager
    live = amplifier_home() / 'keys.env'
    live.write_text('FIXTURE_KEY=fake-current\n')
    app.state['sessions'][0]['status'] = 'working'  # Defer activation, not qualification.
    observed = []
    qualified = []
    async def validate(stage, release):
        qualified.append(True)
        assert (stage / 'shared-config/keys.env').read_bytes() == live.read_bytes()
        if outcome == 'failure':
            raise RuntimeError('original-qualification-failure')
        if outcome == 'cancelled':
            raise asyncio.CancelledError()
    monkeypatch.setattr(manager, 'validate', validate)
    publish = manager.publish
    async def observe(**values):
        if values.get('pendingRelease'):
            stage = manager.directory / 'releases' / values['pendingRelease']
            observed.append(not (stage / 'shared-config/keys.env').exists())
        await publish(**values)
    monkeypatch.setattr(manager, 'publish', observe)
    if outcome == 'cancelled':
        with pytest.raises(asyncio.CancelledError):
            await manager.install()
    else:
        await manager.install()
    assert qualified, str(manager.diagnostics.state.get('lastFailure'))
    stage, = (manager.directory / 'releases').iterdir()
    assert stage.is_dir()
    assert (stage / 'shared-config/settings.yaml').is_file()
    assert (stage / 'shared-config/keys.env').exists() == (outcome == 'cancelled')
    if outcome == 'success':
        assert observed == [True]
        assert (stage / 'validated.json').is_file()
    else:
        failure = manager.diagnostics.state['lastFailure']
        assert failure['status'] == ('interrupted' if outcome == 'cancelled' else 'failed')
        assert failure['errorType'] == ('CancelledError' if outcome == 'cancelled' else 'RuntimeError')
        assert failure['phase'] == 'ecosystem-validation'


@pytest.mark.parametrize('fail_validation', [False, True])
async def test_removal_refusal_does_not_change_update_result(app, staged_manager, monkeypatch, fail_validation):
    manager = staged_manager
    (amplifier_home() / 'keys.env').write_text('FIXTURE_KEY=fake\n')
    app.state['sessions'][0]['status'] = 'working'
    calls = []
    def refuse(home, stage):
        calls.append(stage)
        return 'refused'
    monkeypatch.setattr(storage, 'remove_stage_keys', refuse)
    async def validate(*args):
        if fail_validation:
            raise ValueError('original-qualification-failure')
    monkeypatch.setattr(manager, 'validate', validate)
    await manager.install()
    assert len(calls) == 1
    assert app.state['updates']['phase'] == ('error' if fail_validation else 'staged')
    if fail_validation:
        assert manager.diagnostics.state['lastFailure']['errorType'] == 'ValueError'
        assert manager.diagnostics.state['lastFailure']['phase'] == 'ecosystem-validation'
    assert (calls[0] / 'shared-config/keys.env').is_file()


async def test_data_reuse_also_removes_keys(app, staged_manager, monkeypatch):
    from amplifier_web import update_plan
    manager = staged_manager
    (amplifier_home() / 'keys.env').write_text('FIXTURE_KEY=fake\n')
    app.state['sessions'][0]['status'] = 'working'
    async def build(*args):
        return {'mode': 'data', 'reason': 'fixture', 'activation': 'per-worker'}
    async def reuse(manager, stage, plan):
        assert (stage / 'shared-config/keys.env').is_file()
    monkeypatch.setattr(update_plan, 'build', build)
    monkeypatch.setattr(update_plan, 'reuse_runtime', reuse)
    await manager.install()
    stage, = (manager.directory / 'releases').iterdir()
    assert app.state['updates']['phase'] == 'staged'
    assert not (stage / 'shared-config/keys.env').exists()


@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancelled'])
@pytest.mark.parametrize('rollback', [False, True])
async def test_activation_revalidation_has_current_keys_only_during_validate(app, repo, monkeypatch, outcome, rollback):
    import json
    manager, _ = await prepare(app, repo)
    stage = stage_at(manager.home)
    marker = stage / 'validated.json'
    marker.write_text(json.dumps({'hostVersion': 'old-app', 'sources': 1}))
    # Begin with a stale legacy copy. Revalidation must not reuse it.
    live = amplifier_home() / 'keys.env'
    live.write_text('FIXTURE_KEY=fake-new-current\n')
    before = (stage / 'shared-config/settings.yaml').read_bytes()
    state = app.state['updates']
    state.update(phase='staged', pendingRelease=stage.name)
    if rollback:
        (manager.directory / 'active.json').write_text(json.dumps({'current': None, 'previous': stage.name}))
        state.pop('pendingRelease')
    app.state['sessions'][0]['status'] = 'working'  # Stop after validation, before replacement.
    seen = []
    async def validate(target, release):
        assert target == stage and release == stage.name
        assert (stage / 'shared-config/keys.env').read_bytes() == live.read_bytes()
        assert stat.S_IMODE((stage / 'shared-config/keys.env').stat().st_mode) == 0o600
        seen.append(True)
        if outcome == 'failure':
            raise LookupError('qualification-failed')
        if outcome == 'cancelled':
            raise asyncio.CancelledError()
    monkeypatch.setattr(manager, 'validate', validate)
    if outcome == 'success':
        await manager.activate(rollback=rollback)
    else:
        with pytest.raises(LookupError if outcome == 'failure' else asyncio.CancelledError):
            await manager.activate(rollback=rollback)
    assert seen == [True]
    assert (stage / 'shared-config/keys.env').exists() == (outcome == 'cancelled')
    assert (stage / 'shared-config/settings.yaml').read_bytes() == before
    assert_preserved(stage)


@pytest.mark.parametrize('reason', ['refused', 'io'])
@pytest.mark.parametrize('rollback', [False, True])
async def test_a_refused_refresh_does_not_fail_the_activation(app, repo, monkeypatch, reason, rollback):
    """Validation then runs without a keys file in the stage: the old copy is removed first."""
    import json
    manager, _ = await prepare(app, repo)
    stage = stage_at(manager.home)
    (stage / 'validated.json').write_text(json.dumps({'hostVersion': 'old-app', 'sources': 1}))
    assert (stage / 'shared-config/keys.env').is_file()
    state = app.state['updates']
    state.update(phase='staged', pendingRelease=stage.name)
    if rollback:
        (manager.directory / 'active.json').write_text(json.dumps({'current': None, 'previous': stage.name}))
        state.pop('pendingRelease')
    app.state['sessions'][0]['status'] = 'working'  # Stop after validation, before replacement.
    asked = []
    def refuse(home, target, source):
        asked.append((home, target))
        return reason
    monkeypatch.setattr(storage, 'refresh_stage_keys', refuse)
    seen = []
    async def validate(target, release):
        seen.append((stage / 'shared-config/keys.env').exists())
    monkeypatch.setattr(manager, 'validate', validate)
    await manager.activate(rollback=rollback)
    # The refresh did not write the copy that the stage held. Validation never saw that copy.
    assert asked == [(manager.home, stage)] and seen == [False]
    assert state.get('error') is None
    # The validation counted: the stage is marked for this app version, and its copy is gone.
    assert json.loads((stage / 'validated.json').read_text())['hostVersion'] == __import__('amplifier_web').__version__
    assert not (stage / 'shared-config/keys.env').exists()
    assert_preserved(stage)


def held_copy(stage, tmp_path):
    """A stage copy that the removal refuses: it has a second name (a real refusal, not a stub)."""
    second = tmp_path / 'second-name-of-the-stage-copy'
    os.link(stage / 'shared-config/keys.env', second)
    return second


def test_refresh_gives_none_only_for_a_current_copy_or_for_no_copy(tmp_path):
    """After any other result the stage may hold a copy that the refresh did not write."""
    stage = stage_at(tmp_path)
    source = tmp_path / 'live'
    source.mkdir()
    copy = stage / 'shared-config/keys.env'
    before = copy.read_bytes()
    second = held_copy(stage, tmp_path)
    # No live keys file, and the old copy cannot be removed: a reason, and the old copy is still there.
    assert storage.refresh_stage_keys(tmp_path, stage, source) == 'refused'
    assert copy.read_bytes() == before
    # A live keys file exists and the old copy cannot be replaced: a reason, and the old copy is still there.
    (source / 'keys.env').write_bytes(b'FIXTURE_KEY=fake-current\n')
    assert storage.refresh_stage_keys(tmp_path, stage, source) == 'refused'
    assert copy.read_bytes() == before
    # The live entry cannot be read as a file: a reason, and the old copy is still there.
    (source / 'keys.env').unlink()
    os.mkfifo(source / 'keys.env')
    second.unlink()
    assert storage.refresh_stage_keys(tmp_path, stage, source) == 'refused'
    assert copy.read_bytes() == before
    # None, case 1: the stage holds a copy of the current keys file.
    (source / 'keys.env').unlink()
    (source / 'keys.env').write_bytes(b'FIXTURE_KEY=fake-current\n')
    assert storage.refresh_stage_keys(tmp_path, stage, source) is None
    assert copy.read_bytes() == b'FIXTURE_KEY=fake-current\n'
    # None, case 2: no live keys file, and the stage holds no copy (removed now, or absent before).
    (source / 'keys.env').unlink()
    assert storage.refresh_stage_keys(tmp_path, stage, source) is None and not copy.exists()
    assert storage.refresh_stage_keys(tmp_path, stage, source) is None and not copy.exists()


@pytest.mark.parametrize('rollback', [False, True])
async def test_an_old_copy_never_stands_in_for_a_keys_file_that_the_person_removed(app, repo, monkeypatch, tmp_path, rollback):
    """Qualification never reads a copy that the refresh did not write, whatever the refresh answered."""
    import json
    from amplifier_web.update_health import STALE_STAGE_KEYS
    manager, _ = await prepare(app, repo)
    manager._storage_last_pass = time.time()  # The owner's pass below starts no storage maintenance.
    live = amplifier_home() / 'keys.env'
    # This case writes a live entry. It must be the test's own home, and empty.
    assert live.is_relative_to(tmp_path.resolve()) and not live.exists()
    stage = stage_at(manager.home)
    receipt = json.dumps({'hostVersion': 'old-app', 'sources': 1})
    (stage / 'validated.json').write_text(receipt)
    legacy = (stage / 'shared-config/keys.env').read_bytes()
    second = held_copy(stage, tmp_path)
    state = app.state['updates']
    state.update(phase='staged', pendingRelease=stage.name)
    if rollback:
        (manager.directory / 'active.json').write_text(json.dumps({'current': None, 'previous': stage.name}))
        state.pop('pendingRelease')
    pointer = (manager.directory / 'active.json').read_bytes() if rollback else None
    app.state['sessions'][0]['status'] = 'working'  # A later pass stops after validation, before replacement.
    seen = []
    async def validate(target, release):
        seen.append((stage / 'shared-config/keys.env').exists())
    monkeypatch.setattr(manager, 'validate', validate)
    failures = json.dumps([manager.diagnostics.state.get(key) for key in ('lastFailure', 'activeFailures')], sort_keys=True)

    def held():
        # No qualification ran, and no receipt for this app version was written.
        assert seen == []
        assert (stage / 'validated.json').read_text() == receipt
        assert (stage / 'shared-config/keys.env').read_bytes() == legacy
        # Only the text changes. The phase stays, so the next request tries again.
        assert (state['phase'], state['error']) == ('staged', STALE_STAGE_KEYS)
        assert state.get('pendingRelease') == (None if rollback else stage.name)
        if rollback:
            assert (manager.directory / 'active.json').read_bytes() == pointer
        # The page says it, for an update and for a rollback, and names no failed attempt.
        status = state['currentStatus']
        assert (status['status'], status['title'], status['detail']) == (
            'action-required', 'Update needs attention', STALE_STAGE_KEYS)
        assert json.dumps([manager.diagnostics.state.get(key) for key in ('lastFailure', 'activeFailures')],
                          sort_keys=True) == failures
        assert_preserved(stage)

    await manager.activate(rollback=rollback)  # It raises nothing.
    held()
    # The same request again changes nothing while the copy still cannot be removed,
    # and it publishes nothing: the owner's pass comes here every minute.
    published = []
    publish = app._publish_changes
    monkeypatch.setattr(app, '_publish_changes', lambda *args, **kwargs: published.append(True) or publish(*args, **kwargs))
    await manager.activate(rollback=rollback)
    held()
    assert published == []
    # Another answer of the refresh does not end the refusal. Now a live entry exists and cannot be
    # read as a file (a real refusal of the read, not a stub); the old copy is still in the stage.
    os.mkfifo(live)
    assert storage.refresh_stage_keys(manager.home, stage, amplifier_home()) == 'refused'
    await manager.activate(rollback=rollback)
    held()
    assert published == []
    # The same for a live keys file that Amplifier can read but cannot copy over the held copy.
    live.unlink()
    live.write_text('FIXTURE_KEY=fake-new-current\n')
    await manager.activate(rollback=rollback)
    held()
    assert published == []
    live.unlink()
    if not rollback:
        # The owner's pass comes to the same place by itself. Its first pass publishes its own facts
        # (the workers, what it waits for). The next pass publishes nothing: the text is said once.
        await manager.tick()
        held()
        published.clear()
        await manager.tick()
        held()
        assert published == []
    monkeypatch.setattr(app, '_publish_changes', publish)
    # The removal works again. The next request removes the old copy first, then qualifies without it.
    second.unlink()
    if rollback:
        await manager.activate(rollback=True)
    else:
        await manager.tick()
    assert seen == [False]
    assert json.loads((stage / 'validated.json').read_text())['hostVersion'] == __import__('amplifier_web').__version__
    assert not (stage / 'shared-config/keys.env').exists()
    # The refusal is over, and its text is taken back, for an update and for a rollback.
    assert state.get('error') is None
    assert state['currentStatus']['detail'] != STALE_STAGE_KEYS
    assert state['currentStatus']['title'] != 'Update needs attention'


@pytest.mark.parametrize('failure', ['command', 'other'])
@pytest.mark.parametrize('path', ['install-request', 'rollback-request', 'owner'])
async def test_a_validation_that_fails_without_a_keys_file_is_recorded_and_shown(app, repo, monkeypatch, tmp_path, path, failure):
    """The refresh is refused, the old copy is removed, and the validation then fails.

    Nothing in the changed block records or shows that failure: it removes the copy and raises
    again. A command of the validation that fails is recorded by the validation (diagnostics.run).
    Any other error is recorded by the caller's handler. That handler also shows the failure:
    `command` for a person's request, `loop` for the owner's pass. This case goes through each.
    """
    import json
    from amplifier_web.update_diagnostics import CommandFailure
    from amplifier_web.update_health import STALE_STAGE_KEYS
    manager, _ = await prepare(app, repo)
    manager._storage_last_pass = time.time()  # The owner's pass starts no storage maintenance.
    live = amplifier_home() / 'keys.env'
    assert live.is_relative_to(tmp_path.resolve()) and not live.exists()
    os.mkfifo(live)  # A live entry that is no file: the refresh's read is refused (a real refusal).
    stage = stage_at(manager.home)
    receipt = json.dumps({'hostVersion': 'old-app', 'sources': 1})
    (stage / 'validated.json').write_text(receipt)
    state = app.state['updates']
    # An earlier request was held, and its text is still there.
    state.update(phase='staged', pendingRelease=stage.name, error=STALE_STAGE_KEYS)
    rollback = path == 'rollback-request'
    if rollback:
        (manager.directory / 'active.json').write_text(json.dumps({'current': None, 'previous': stage.name}))
        state.pop('pendingRelease')
    active = manager.directory / 'active.json'
    pointer = active.read_bytes() if active.exists() else None
    assert 'lastFailure' not in manager.diagnostics.state
    seen = []

    async def probe():
        raise CommandFailure({'exitCode': 1, 'durationMs': 5, 'stdoutBytes': 0, 'stderrBytes': 9})

    async def validate(target, release):
        seen.append((stage / 'shared-config/keys.env').exists())
        if failure == 'command':
            # As the real validation: one of its recorded commands ends with an error.
            await manager.diagnostics.run('ecosystem-compatibility', probe)
        # As the real validation: an error outside a recorded command.
        raise ValueError('No app-level conversation profiles are configured')

    monkeypatch.setattr(manager, 'validate', validate)
    if path == 'owner':
        waits = []

        async def one_pass(delay):
            waits.append(delay)
            if len(waits) > 1:
                manager.closed = True

        monkeypatch.setattr(manager, 'wait_for_work', one_pass)
        await manager.loop()  # It raises nothing.
    else:
        await manager.command('rollback' if rollback else 'install', None, 'c' * 32)  # It raises nothing.
    # The validation ran once, and it saw no keys file in the stage.
    assert seen == [False]
    # Nothing was switched, no receipt for this app version was written, and the old copy is gone.
    assert (active.read_bytes() if active.exists() else None) == pointer
    assert (stage / 'validated.json').read_text() == receipt
    assert not (stage / 'shared-config/keys.env').exists()
    assert_preserved(stage)
    # The failure is recorded: by the validation for its own command, else by the caller's handler.
    step = ('ecosystem-compatibility' if failure == 'command' else
            {'install-request': 'install', 'rollback-request': 'rollback', 'owner': 'background-update'}[path])
    recorded = manager.diagnostics.state['lastFailure']
    assert (recorded['phase'], recorded['status']) == (step, 'failed')
    assert recorded.get('exitCode') == (1 if failure == 'command' else None)
    assert recorded.get('errorType') == ('CommandFailure' if failure == 'command' else 'ValueError')
    if failure == 'command':
        assert [(group['kind'], group['latest']['id'])
                for group in manager.diagnostics.state['activeFailures'].values()] == [('ecosystem', recorded['id'])]
    # The page says that the update failed, and at which step. The earlier refusal text is gone.
    words = step.replace('-', ' ')
    text = (f'Background update failed during {words}. Review its diagnostic receipt; no work was replayed.'
            if path == 'owner' else
            f'Update failed during {words}. Review the diagnostic receipt; no conversation work was replayed.')
    assert (state['phase'], state['error']) == ('error', text)
    status = state['currentStatus']
    assert (status['status'], status['actionRequired'], status['detail'], status['action']) == (
        'action-required', True, text, 'diagnostics')
    assert status['title'] == ('Update could not finish' if rollback else 'Update needs attention')
    # A prepared update stays prepared.
    assert state.get('pendingRelease') == (None if rollback else stage.name)


@pytest.mark.parametrize('phase', ['installed', 'checked', 'error'])
def test_the_refusal_text_shows_without_a_pending_update_and_before_an_earlier_failure(phase):
    """A rollback has no pending release, and an earlier failure record must not speak in its place."""
    from amplifier_web import update_health as health
    failure = {'id': 'f' * 32, 'at': 5, 'attemptId': 'a' * 32, 'kind': 'ecosystem',
               'phase': 'ecosystem-validation', 'status': 'failed'}
    state = {'phase': phase, 'error': health.STALE_STAGE_KEYS,
             'diagnostics': {'events': [], 'lastFailure': failure, 'activeFailures': {}}}
    value = health.current(state)
    assert (value['status'], value['title'], value['detail'], value['action']) == (
        'action-required', 'Update needs attention', health.STALE_STAGE_KEYS, 'diagnostics')
    assert 'selectedFailure' not in value
    # Another text in the same place is not this refusal.
    assert health.current({**state, 'error': 'another text'})['detail'] != health.STALE_STAGE_KEYS
    # While an update runs, that state speaks.
    assert health.current({**state, 'phase': 'activating'})['status'] == 'working'


@pytest.mark.parametrize('part', ['source', 'stage', 'shared-config', 'keys.env'])
def test_refresh_refuses_symlinks(tmp_path, part):
    stage = stage_at(tmp_path)
    source = tmp_path / 'live'
    source.mkdir()
    (source / 'keys.env').write_text('FIXTURE_KEY=fake-current\n')
    path = {'source': source, 'stage': stage, 'shared-config': stage / 'shared-config',
            'keys.env': stage / 'shared-config/keys.env'}[part]
    saved = tmp_path / 'saved'
    path.rename(saved)
    path.symlink_to(saved, target_is_directory=part != 'keys.env')
    assert storage.refresh_stage_keys(tmp_path, stage, source)
    assert path.is_symlink()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_refresh_reads_the_live_file_that_the_products_own_reader_reads(tmp_path, kind):
    """A person's keys file can be a link to a file elsewhere. The stage snapshot copies it too."""
    stage = stage_at(tmp_path)
    source = tmp_path / 'live'
    source.mkdir()
    real = tmp_path / 'elsewhere.env'
    real.write_bytes(b'FIXTURE_KEY=fake-current\n')
    if kind == 'symlink':
        (source / 'keys.env').symlink_to(real)
    else:
        os.link(real, source / 'keys.env')
    assert storage.refresh_stage_keys(tmp_path, stage, source) is None
    target = stage / 'shared-config/keys.env'
    assert target.read_bytes() == b'FIXTURE_KEY=fake-current\n'
    # The stage gets its own private regular file, never the link.
    assert not target.is_symlink() and target.stat().st_nlink == 1
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert real.read_bytes() == b'FIXTURE_KEY=fake-current\n'


@pytest.mark.parametrize('kind', ['directory', 'fifo', 'dangling-symlink'])
def test_refresh_refuses_a_live_entry_that_is_no_file(tmp_path, kind):
    stage = stage_at(tmp_path)
    before = (stage / 'shared-config/keys.env').read_bytes()
    source = tmp_path / 'live'
    source.mkdir()
    if kind == 'directory':
        (source / 'keys.env').mkdir()
    elif kind == 'fifo':
        os.mkfifo(source / 'keys.env')
    else:
        (source / 'keys.env').symlink_to(tmp_path / 'absent')
    result = storage.refresh_stage_keys(tmp_path, stage, source)
    if kind == 'dangling-symlink':
        # Nothing to read is "the person has no keys file": the old copy must not stand in.
        assert result is None and not (stage / 'shared-config/keys.env').exists()
    else:
        assert result and (stage / 'shared-config/keys.env').read_bytes() == before


def test_a_home_below_a_linked_folder_works_as_the_product_resolves_it(tmp_path):
    """The server resolves its data folder at start (auth.validated_data_dir). No link is left in it."""
    from amplifier_web.auth import validated_data_dir
    tmp_path = tmp_path.resolve()
    (tmp_path / 'real').mkdir()
    (tmp_path / 'link').symlink_to(tmp_path / 'real', target_is_directory=True)
    home = validated_data_dir(tmp_path / 'link/app', create=True)
    assert home == tmp_path / 'real/app'
    stage = stage_at(home)
    source = tmp_path / 'real/live'
    source.mkdir()
    (source / 'keys.env').write_bytes(b'FIXTURE_KEY=fake-current\n')
    assert storage.refresh_stage_keys(home, stage, source) is None
    assert (stage / 'shared-config/keys.env').read_bytes() == b'FIXTURE_KEY=fake-current\n'
    assert storage.remove_stage_keys(home, stage) is None
    assert not (stage / 'shared-config/keys.env').exists()
    # The same folder named through the link is still refused: the walk follows no link.
    (stage / 'shared-config/keys.env').write_bytes(b'FIXTURE_KEY=fake-old\n')
    assert storage.remove_stage_keys(tmp_path / 'link/app', tmp_path / 'link/app/updates/releases' / stage.name)
    assert (stage / 'shared-config/keys.env').is_file()


def test_remove_refuses_a_stage_that_is_no_longer_the_one_that_was_checked(tmp_path):
    stage = stage_at(tmp_path)
    shared = stage / 'shared-config'
    real = {stage: (stage.stat().st_dev, stage.stat().st_ino), shared: (shared.stat().st_dev, shared.stat().st_ino)}
    for changed in (stage, shared):
        assert storage.remove_stage_keys(tmp_path, stage, expected={**real, changed: (0, 0)}) == 'refused'
        assert (shared / 'keys.env').is_file()
    assert storage.remove_stage_keys(tmp_path, stage, expected=real) is None
    assert not (shared / 'keys.env').exists()


def test_refresh_absent_live_file_writes_nothing(tmp_path):
    stage = stage_at(tmp_path)
    source = tmp_path / 'live'
    source.mkdir()
    assert storage.refresh_stage_keys(tmp_path, stage, source) is None
    assert not (stage / 'shared-config/keys.env').exists()
    assert_preserved(stage)


def test_refresh_copies_only_current_keys_privately(tmp_path):
    stage = stage_at(tmp_path)
    source = tmp_path / 'live'
    source.mkdir()
    (source / 'keys.env').write_bytes(b'FIXTURE_KEY=fake-new\n')
    (source / 'settings.yaml').write_text('must not replace frozen settings')
    assert storage.refresh_stage_keys(tmp_path, stage, source) is None
    assert (stage / 'shared-config/keys.env').read_bytes() == (source / 'keys.env').read_bytes()
    assert stat.S_IMODE((stage / 'shared-config/keys.env').stat().st_mode) == 0o600
    assert_preserved(stage)


async def test_scrub_age_preparation_bound_and_no_folder_deletion(app):
    manager = app.update_manager
    old = 25 * 3600
    validated = stage_at(manager.home, '1' * 32, validated=True)
    failed = stage_at(manager.home, '2' * 32, age=old)
    recent = stage_at(manager.home, '3' * 32)
    pending = stage_at(manager.home, '4' * 32, age=old)
    touched_shared = stage_at(manager.home, '5' * 32, age=old)
    os.utime(touched_shared / 'shared-config', None)
    touched_stage = stage_at(manager.home, '6' * 32, age=old)
    os.utime(touched_stage, None)
    manager.diagnostics.state['componentPreparation'] = {
        'retry': {'mode': 'inspect'}, 'preparation': {'candidate': pending.name}, 'revision': pending.name}
    await storage.scrub_stage_keys(manager)
    for stage in (validated, failed):
        assert not (stage / 'shared-config/keys.env').exists()
    for stage in (recent, pending, touched_shared, touched_stage):
        assert (stage / 'shared-config/keys.env').is_file()
    for stage in (validated, failed, recent, pending, touched_shared, touched_stage):
        assert_preserved(stage)
    more = [stage_at(manager.home, f'{n:032x}', validated=True) for n in range(100, 111)]
    await storage.scrub_stage_keys(manager)
    assert sum(not (s / 'shared-config/keys.env').exists() for s in more) == 8
    await storage.scrub_stage_keys(manager)
    assert all(not (s / 'shared-config/keys.env').exists() for s in more)
    assert all(s.is_dir() for s in more)


def aged(stage, hours=25):
    """Both folders are older than the 24 hours that a stage without a receipt waits."""
    old = time.time() - hours * 3600
    for folder in (stage, stage / 'shared-config'):
        os.utime(folder, (old, old))


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
async def test_scrub_takes_the_copy_of_a_failed_or_stopped_chat_setup_at_once(app, status):
    """Its record says that the preparation has ended: no wait for 24 hours."""
    manager = app.update_manager
    stage = stage_at(manager.home)
    attempt = stage / 'profile-attempt.json'
    attempt.write_text(json.dumps({'generation': stage.name, 'status': status, 'errorType': 'RuntimeError'}))
    saved = attempt.read_bytes()
    await storage.scrub_stage_keys(manager)
    assert not (stage / 'shared-config/keys.env').exists()
    assert_preserved(stage)
    assert attempt.read_bytes() == saved   # Only the copy of the keys file went.


NO_ANSWER = {
    'preparing': lambda attempt: attempt.write_text('{"status": "preparing"}'),
    'succeeded-without-a-receipt': lambda attempt: attempt.write_text('{"status": "succeeded"}'),
    'other-letters': lambda attempt: attempt.write_text('{"status": "Failed"}'),
    'no-status': lambda attempt: attempt.write_text('{"errorType": "failed"}'),
    'a-list': lambda attempt: attempt.write_text('["failed"]'),
    'no-json': lambda attempt: attempt.write_text('{"status": "failed"'),
    'not-text': lambda attempt: attempt.write_bytes(b'\xff\xfe{"status": "failed"}'),
    'over-the-bound': lambda attempt: attempt.write_text(
        json.dumps({'status': 'failed', 'padding': 'x' * (1024 * 1024)})),
    'directory': lambda attempt: attempt.mkdir(),
    'dangling-symlink': lambda attempt: attempt.symlink_to(attempt.parent / 'absent-attempt-target'),
    'symlink-to-a-failed-record': lambda attempt: (
        (attempt.parent / 'another-record.json').write_text('{"status": "failed"}'),
        attempt.symlink_to(attempt.parent / 'another-record.json')),
    'fifo': lambda attempt: os.mkfifo(attempt),
}


@pytest.mark.parametrize('old', [False, True])
@pytest.mark.parametrize('entry', sorted(NO_ANSWER))
async def test_scrub_waits_for_age_when_the_chat_setup_record_is_no_answer(app, entry, old):
    """Only "failed" and "cancelled" in a regular, readable record say that the preparation ended."""
    manager = app.update_manager
    stage = stage_at(manager.home)
    attempt = stage / 'profile-attempt.json'
    NO_ANSWER[entry](attempt)
    if old:
        aged(stage)   # after the entry: its creation changes the stage's time
    await asyncio.wait_for(storage.scrub_stage_keys(manager), timeout=5)   # a FIFO must not hold the pass
    assert (stage / 'shared-config/keys.env').exists() is (not old)
    assert_preserved(stage)
    assert attempt.lstat()   # No entry, including a dangling link, was removed.


async def test_scrub_takes_a_validated_stage_whatever_its_chat_setup_record_says(app):
    manager = app.update_manager
    stage = stage_at(manager.home, validated=True)
    (stage / 'profile-attempt.json').write_text('{"status": "preparing"}')
    await storage.scrub_stage_keys(manager)
    assert not (stage / 'shared-config/keys.env').exists()
    assert_preserved(stage)


@pytest.mark.parametrize('validated', [False, True])
async def test_scrub_leaves_a_stage_that_a_preparation_of_this_process_owns_now(app, tmp_path, validated):
    """Neither a receipt nor age takes the copy from a preparation that still runs here."""
    from amplifier_web import runtime_profiles

    manager = app.update_manager
    stage = stage_at(manager.home, validated=validated)
    copy = stage / 'shared-config/keys.env'
    assert not runtime_profiles.owned(stage.name)
    async with runtime_profiles._profile_attempt(
            manager.home, 'f' * 32, stage.name, stage, tmp_path / 'absent-index.json'):
        assert runtime_profiles.owned(stage.name)
        aged(stage)
        await storage.scrub_stage_keys(manager)
        assert copy.is_file()
    # The preparation has ended: the same pass takes the copy now.
    assert not runtime_profiles.owned(stage.name)
    aged(stage)
    await storage.scrub_stage_keys(manager)
    assert not copy.exists()
    assert_preserved(stage)


def one_new_stage(h):
    stages = [path for path in (h.home / 'updates' / 'releases').iterdir() if path != h.receipt]
    assert len(stages) == 1
    return stages[0]


@pytest.mark.parametrize('end', ['failed', 'cancelled', 'succeeded'])
async def test_a_chat_setup_that_fails_or_is_stopped_removes_its_copy_of_the_keys_file(profile_inputs, end):
    """The real preparation (runtime_profiles.ensure) with stand-ins for its commands only."""
    from amplifier_web import runtime_profiles

    h = profile_inputs
    live = (h.shared / 'keys.env').read_bytes()
    during, entered = {}, asyncio.Event()

    async def command(*args, **kwargs):
        stage = one_new_stage(h)
        during.update(copy=(stage / 'shared-config' / 'keys.env').read_bytes(),
                      owned=runtime_profiles.owned(stage.name))
        if end == 'failed':
            raise RuntimeError('fixture failure')
        if end == 'cancelled':
            entered.set()
            await asyncio.Event().wait()

    h.process_hook = command
    task = asyncio.create_task(runtime_profiles.ensure(h.home, h.generation, h.child('keys-' + end)))
    if end == 'cancelled':
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
    if end == 'succeeded':
        await task
    else:
        with pytest.raises({'failed': RuntimeError, 'cancelled': asyncio.CancelledError}[end]):
            await task
    # While its command ran, the stage held the copy and the preparation owned the stage.
    assert during == {'copy': live, 'owned': True}
    stage = one_new_stage(h)
    assert json.loads((stage / 'profile-attempt.json').read_text())['status'] == end
    assert not runtime_profiles.owned(stage.name)
    shared = stage / 'shared-config'
    if end == 'succeeded':
        assert (shared / 'keys.env').read_bytes() == live   # idle maintenance takes it (a validated stage)
    else:
        assert not (shared / 'keys.env').exists()
    # Only that one file went: the stage, its settings and its routing stay, and so does the live keys file.
    assert (shared / 'settings.yaml').is_file() and (shared / 'routing' / 'matrix.yaml').is_file()
    assert (stage / 'foundation').is_dir() and (h.shared / 'keys.env').read_bytes() == live


async def test_a_refused_removal_does_not_change_how_a_chat_setup_ends(profile_inputs, tmp_path):
    """A real refusal: the copy has a second name. The copy stays for idle maintenance, which goes by the
    recorded status (test_scrub_takes_the_copy_of_a_failed_or_stopped_chat_setup_at_once)."""
    from amplifier_web import runtime_profiles

    h = profile_inputs

    async def command(*args, **kwargs):
        os.link(one_new_stage(h) / 'shared-config' / 'keys.env', tmp_path / 'second-name')
        raise RuntimeError('fixture failure')

    h.process_hook = command
    with pytest.raises(RuntimeError, match='fixture failure'):
        await runtime_profiles.ensure(h.home, h.generation, h.child('keys-refused'))
    stage = one_new_stage(h)
    copy = stage / 'shared-config' / 'keys.env'
    assert copy.is_file() and storage.remove_stage_keys(h.home, stage) == 'refused'
    assert json.loads((stage / 'profile-attempt.json').read_text())['status'] == 'failed'
    assert not (stage / 'validated.json').exists() and not runtime_profiles.owned(stage.name)
    # The refusal is over: the same removal works now.
    (tmp_path / 'second-name').unlink()
    assert storage.remove_stage_keys(h.home, stage) is None and not copy.exists() and stage.is_dir()


@pytest.mark.parametrize('blocker', ['lock', 'checking', 'staging', 'validating', 'activating',
                                    'pendingRestart', 'pendingReplacement', 'pendingRelease',
                                    'pendingApp', 'pendingRollback', 'check-task', 'closed'])
async def test_scrub_never_runs_during_update(app, blocker):
    manager = app.update_manager
    stage = stage_at(manager.home, validated=True, age=25 * 3600)
    state = app.state['updates']
    if blocker == 'lock':
        await manager.lock.acquire()
    elif blocker == 'closed':
        manager.closed = True
    elif blocker == 'check-task':
        manager.check_task = asyncio.create_task(asyncio.Event().wait())
    elif blocker.startswith('pending'):
        state[blocker] = 'fixture'
    else:
        state['phase'] = blocker
    try:
        await asyncio.wait_for(storage.scrub_stage_keys(manager), timeout=1)
        assert (stage / 'shared-config/keys.env').is_file()
    finally:
        if blocker == 'lock':
            manager.lock.release()


async def test_retire_storage_scrubs_even_when_code_cleanup_disabled(app, monkeypatch):
    from amplifier_web import storage_cleanup
    manager = app.update_manager
    stage = stage_at(manager.home, validated=True)
    checked = []
    async def no_delay(seconds):
        pass
    async def disabled(*args, **kwargs):
        checked.append(not (stage / 'shared-config/keys.env').exists())
        return {'status': 'disabled', 'removedFileBytes': 0}
    monkeypatch.setattr(asyncio, 'sleep', no_delay)
    monkeypatch.setattr(storage_cleanup, 'run', disabled)
    await manager.retire_storage()
    assert checked == [True]
    assert_preserved(stage)


async def test_scrub_makes_progress_when_its_time_is_used_up_at_once(app, monkeypatch):
    """A slow folder: each look at the clock is past the limit. Every stage is still reached."""
    from types import SimpleNamespace
    manager = app.update_manager
    stages = [stage_at(manager.home, name * 32, validated=True) for name in 'abc']
    clock = iter(range(0, 10 ** 6, 2))  # Each call is 2 s later; the limit is 1 s.
    monkeypatch.setattr(storage, 'time', SimpleNamespace(monotonic=lambda: next(clock), time=time.time))
    left = []
    for _ in range(6):
        await storage.scrub_stage_keys(manager)
        left.append(sum((stage / 'shared-config/keys.env').exists() for stage in stages))
    # One stage per pass, never the same place twice in a row, and the scan starts again after the end.
    assert left[:3] == [2, 1, 0], left
    assert all(stage.is_dir() for stage in stages)
    # The scan starts again after the end: copies that appear later are reached too.
    for stage in stages:
        (stage / 'shared-config/keys.env').write_bytes(b'FIXTURE_KEY=fake-later\n')
    for _ in range(4):
        await storage.scrub_stage_keys(manager)
    assert not any((stage / 'shared-config/keys.env').exists() for stage in stages)


async def test_a_failed_key_scrub_does_not_defer_the_other_maintenance(app, monkeypatch):
    from amplifier_web import storage_cleanup
    manager = app.update_manager
    ran = []
    async def no_delay(seconds):
        pass
    async def broken(owner):
        raise RuntimeError('synthetic scrub failure')
    async def cleanup(*args, **kwargs):
        ran.append('storage')
        return {'status': 'disabled', 'removedFileBytes': 0}
    monkeypatch.setattr(asyncio, 'sleep', no_delay)
    monkeypatch.setattr(storage, 'scrub_stage_keys', broken)
    monkeypatch.setattr(storage_cleanup, 'run', cleanup)
    await manager.retire_storage()
    assert ran == ['storage']
    assert app.state['updates']['storage']['status'] == 'disabled'


async def test_scrub_rechecks_activity_after_lifecycle_admission(app, monkeypatch):
    from contextlib import asynccontextmanager
    manager = app.update_manager
    stage = stage_at(manager.home, validated=True)
    @asynccontextmanager
    async def lifecycle():
        app.state['updates']['phase'] = 'validating'
        yield
    monkeypatch.setattr(app, 'runtime_lifecycle', lifecycle)
    await storage.scrub_stage_keys(manager)
    assert (stage / 'shared-config/keys.env').is_file()


@pytest.mark.parametrize('missing', ['lock', 'diagnostics'])
async def test_scrub_requires_its_own_lock_and_preparation_evidence(app, missing):
    from types import SimpleNamespace
    manager = SimpleNamespace(**vars(app.update_manager))
    stage = stage_at(manager.home, validated=True)
    setattr(manager, missing, None)
    await storage.scrub_stage_keys(manager)
    assert (stage / 'shared-config/keys.env').is_file()


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows private files')
@pytest.mark.parametrize('hold_open', [False, True])
def test_native_windows_stage_keys_private_removal(hold_open):
    from amplifier_web import windows_files as wf
    # Use a private child of the profile, not a runner temp with foreign ACLs.
    home = wf.address(Path.home() / ('.a9-' + uuid.uuid4().hex))
    stage = home / 'updates/releases' / ('a' * 32)
    try:
        with wf.directory(stage / 'shared-config', owned_root=home, create=True) as shared:
            shared.publish_private('keys.env', b'FIXTURE_KEY=synthetic-only\n')
        if hold_open:
            # Python's ordinary file handle denies delete sharing on Windows.
            with (stage / 'shared-config/keys.env').open('rb'):
                assert storage.remove_stage_keys(home, stage) == 'refused'
                assert (stage / 'shared-config/keys.env').exists()
        assert storage.remove_stage_keys(home, stage) is None
        assert not (stage / 'shared-config/keys.env').exists()
        assert stage.is_dir()
    finally:
        if home.exists():
            shutil.rmtree(home)


def native_refusal(home, stage):
    """Which step of the private boundary refuses, in the boundary's own words.

    The product returns a reason code only. A native failure must name its cause,
    or the next reader guesses. Synthetic files only; no content is reported.
    """
    from amplifier_web import windows_files as wf
    from amplifier_web.host.config import _read_keys
    shared = stage / 'shared-config'
    steps = {}

    def attempt(name, action):
        try:
            action()
            steps[name] = 'ok'
        except Exception as error:
            steps[name] = f'{type(error).__name__}: {error}'

    def pin(folder, root):
        with wf.directory(folder, owned_root=root, create=False):
            pass

    def read(root):
        with wf.directory(shared, owned_root=root, create=False) as pinned:
            pinned.read_private('keys.env', limit=1024 * 1024)

    for folder in (home, home / 'updates', home / 'updates/releases', stage, shared):
        attempt(f'private folder {folder.relative_to(home.parent)}', lambda folder=folder: pin(folder, folder))
    attempt('read with owned_root=home', lambda: read(home))
    attempt('read with owned_root=shared-config', lambda: read(shared))
    attempt('product reader', lambda: _read_keys(shared / 'keys.env') and None)
    return steps


def token_sids():
    """(TokenUser, TokenOwner) of this process as text. TokenOwner owns each new object."""
    import ctypes as C
    from amplifier_web import windows_files as wf
    api = wf._API()
    token = wf.HANDLE()
    api.ok(api.OpenProcessToken(api.GetCurrentProcess(), 8, C.byref(token)))
    try:
        def sid(kind):
            size = wf.DWORD()
            api.GetTokenInformation(token, kind, None, 0, C.byref(size))
            buffer = C.create_string_buffer(size.value)
            api.ok(api.GetTokenInformation(token, kind, buffer, size, C.byref(size)))
            return api.sid_text(C.cast(buffer, C.POINTER(wf.PVOID))[0])
        return sid(1), sid(4)
    finally:
        api.close(token)


@contextmanager
def ordinary_default_owner():
    """New objects belong to the account, as in a process that is not elevated.

    The hosted runner's process is elevated. Windows then gives each new object
    to BUILTIN\\Administrators (TokenOwner), and the private-file boundary
    refuses that owner: also for the product's own reader of a stage (run
    44111212512). Setup refuses an elevated token, so an installation does not
    run like that. Only the default owner of NEW objects changes here, for this
    process, and it is put back. No privilege, group, product check or product
    code changes: the stage is still made by the product's plain mkdir and copy.
    """
    import ctypes as C
    from ctypes import wintypes as W
    advapi = C.WinDLL('advapi32', use_last_error=True)
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    advapi.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    advapi.GetTokenInformation.argtypes = [W.HANDLE, W.DWORD, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    advapi.SetTokenInformation.argtypes = [W.HANDLE, W.DWORD, C.c_void_p, W.DWORD]
    token = W.HANDLE()
    # TOKEN_QUERY | TOKEN_ADJUST_DEFAULT
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0088, C.byref(token)):
        raise C.WinError(C.get_last_error())

    def information(kind):
        size = W.DWORD()
        advapi.GetTokenInformation(token, kind, None, 0, C.byref(size))
        buffer = C.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, kind, buffer, size, C.byref(size)):
            raise C.WinError(C.get_last_error())
        return buffer

    def set_owner(buffer):
        # TOKEN_USER and TOKEN_OWNER both begin with the SID pointer.
        sid = C.c_void_p.from_buffer(buffer)
        if not advapi.SetTokenInformation(token, 4, C.byref(sid), C.sizeof(sid)):
            raise C.WinError(C.get_last_error())

    try:
        user, owner = information(1), information(4)
        set_owner(user)
        try:
            yield
        finally:
            set_owner(owner)
    finally:
        kernel.CloseHandle(token)


@contextmanager
def no_change():
    yield


@pytest.fixture
async def native_product_stage(monkeypatch):
    """Use the real setup writer, manager directory call and install snapshot."""
    from types import SimpleNamespace
    from amplifier_web import setup, windows_files as wf
    from amplifier_web.host import config

    # Only the setup writer's environment/cache side effects are isolated.
    # Every directory/ACL/file write, copy, refresh and read is the real product.
    monkeypatch.setattr(setup, 'os', SimpleNamespace(environ={}))
    monkeypatch.setattr(config, '_KEY_FILE_VALUES', {})
    homes = []

    async def make(*, ordinary):
        home = wf.address(Path.home() / ('.a9-product-' + uuid.uuid4().hex))
        homes.append(home)
        updates = home / 'updates'
        live = home / 'live-shared'
        stage = updates / 'releases' / ('a' * 32)
        owner = SimpleNamespace(store=SimpleNamespace(shared_home=live))
        # UpdateManager.__init__ uses this exact private-directory boundary.
        with wf.directory(updates, owned_root=home, create=True):
            pass
        # SetupManager._keys is the provider setup's real shared keys writer
        # (setup.py), including its private directory, lock and publication.
        setup.SetupManager._keys(owner, {'FIXTURE_A9_PRODUCT_KEY': 'synthetic-original'})
        config.write_private(live / 'settings.yaml', '{}\n', owned_root=live)
        with (ordinary_default_owner() if ordinary else no_change()):
            # install() deliberately uses ordinary mkdir; do not replace this with
            # a private-directory writer or directly publish the staged key.
            stage.mkdir(parents=True, mode=0o700)
            proof = {'candidate': stage.name, 'activeBefore': None,
                     'directoryIdentity': [stage.stat().st_dev, stage.stat().st_ino],
                     'boundary': 'copy-only', 'copySettled': True}
            await storage.snapshot_shared_config(proof, live, stage / 'shared-config')
        assert proof['copySettled'] is True, 'The real snapshot copy did not settle'
        assert (stage / 'shared-config/keys.env').is_file(), 'The product snapshot did not copy keys'
        return home, live, stage, owner

    try:
        yield make
    finally:
        for home in homes:
            if home.exists():
                shutil.rmtree(home)


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows product snapshot ACLs')
async def test_native_windows_stage_keys_product_snapshot_removal(native_product_stage):
    home, live, stage, owner = await native_product_stage(ordinary=True)
    reason = storage.remove_stage_keys(home, stage)
    assert reason is None, f'Product mkdir/copy2 stage key removal returned {reason!r}: {native_refusal(home, stage)}'
    assert not (stage / 'shared-config/keys.env').exists(), 'Product snapshot key was retained'
    assert (stage / 'shared-config/settings.yaml').read_text() == '{}\n'


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows product refresh and reader')
async def test_native_windows_stage_keys_product_refresh_readback(native_product_stage):
    from amplifier_web import setup
    from amplifier_web.host.config import _read_keys

    home, live, stage, owner = await native_product_stage(ordinary=True)
    # The qualification's own reader accepts the product's copy before any change.
    assert _read_keys(stage / 'shared-config/keys.env') == {'FIXTURE_A9_PRODUCT_KEY': 'synthetic-original'}, \
        'Qualification reader did not see the copied synthetic keys'
    current = {'FIXTURE_A9_PRODUCT_KEY': 'synthetic-current'}
    setup.SetupManager._keys(owner, current)
    reason = storage.refresh_stage_keys(home, stage, live)
    assert reason is None, f'Product mkdir/copy2 stage key refresh returned {reason!r}: {native_refusal(home, stage)}'
    # This is the private-file/parser reader used by _load_keys/load_config in
    # qualification. It reads only our synthetic file, not process environment.
    assert _read_keys(stage / 'shared-config/keys.env') == current, 'Qualification reader did not see current synthetic keys'
    # The person removes the live keys file. Windows must report "no file" as that: the refresh
    # then takes the stage's copy away and answers None (no live file, and no copy).
    (live / 'keys.env').unlink()
    reason = storage.refresh_stage_keys(home, stage, live)
    assert reason is None, f'Refresh with no live keys file returned {reason!r}: {native_refusal(home, stage)}'
    assert not (stage / 'shared-config/keys.env').exists(), 'The old copy stayed after the live keys file was removed'
    reason = storage.remove_stage_keys(home, stage)
    assert reason is None, f'Refreshed product stage key removal returned {reason!r}'
    assert not (stage / 'shared-config/keys.env').exists(), 'Refreshed product key was retained'
    assert (stage / 'shared-config/settings.yaml').read_text() == '{}\n'


@pytest.mark.skipif(os.name != 'nt', reason='requires a native Windows process token')
async def test_native_windows_stage_keys_with_this_process_default_owner(native_product_stage):
    """What this process's own token gives, stated, not assumed.

    With an ordinary token the copy is removed. With an elevated token Windows
    owns the product's stage folders through BUILTIN\\Administrators: the
    boundary refuses them, for the removal and for the product's own reader
    alike. The removal then reports a refusal, deletes nothing and raises
    nothing, so a later pass can try again.
    """
    from amplifier_web.host.config import _read_keys

    user, default_owner = token_sids()
    home, live, stage, owner = await native_product_stage(ordinary=False)
    key = stage / 'shared-config/keys.env'
    reason = storage.remove_stage_keys(home, stage)
    if default_owner == user:
        assert reason is None, f'Removal with an ordinary token returned {reason!r}: {native_refusal(home, stage)}'
        assert not key.exists()
        return
    assert default_owner == 'S-1-5-32-544', 'This token has a default owner that the case does not know'
    assert reason == 'refused', f'Removal with an elevated default owner returned {reason!r}'
    assert key.is_file() and (stage / 'shared-config/settings.yaml').read_text() == '{}\n'
    with pytest.raises(PermissionError, match='trusted ownership'):
        _read_keys(key)
    assert storage.refresh_stage_keys(home, stage, live) == 'refused'
    assert key.is_file()
