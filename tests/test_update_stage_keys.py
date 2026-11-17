"""Credential lifetimes use synthetic files only; no app or model is launched."""
import asyncio
import os
from pathlib import Path
import shutil
import stat
import time
import uuid

import pytest

from amplifier_web import update_storage as storage
from amplifier_web.host.config import amplifier_home
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


@pytest.mark.parametrize('validated', [False, True])
@pytest.mark.parametrize('entry', ['file', 'directory', 'dangling-symlink'])
async def test_scrub_profile_attempt_entry_protects_only_unvalidated_stage(app, validated, entry):
    manager = app.update_manager
    stage = stage_at(manager.home, validated=validated)
    attempt = stage / 'profile-attempt.json'
    if entry == 'file':
        attempt.write_text('{"status": "preparing"}')
    elif entry == 'directory':
        attempt.mkdir()
    else:
        attempt.symlink_to(stage / 'absent-attempt-target')
    # Creating the entry changes stage mtime: age both folders afterwards so
    # this cannot pass merely through the existing recent-stage exclusion.
    old = time.time() - 25 * 3600
    for folder in (stage, stage / 'shared-config'):
        os.utime(folder, (old, old))
    await storage.scrub_stage_keys(manager)
    assert (stage / 'shared-config/keys.env').exists() is (not validated)
    assert_preserved(stage)
    assert attempt.lstat()  # No attempt entry, including a dangling link, was removed.


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


@pytest.fixture
async def native_product_stage_keys(monkeypatch):
    """Use the real setup writer, manager directory call and install snapshot."""
    from types import SimpleNamespace
    from amplifier_web import setup, windows_files as wf
    from amplifier_web.host import config

    # Only the setup writer's environment/cache side effects are isolated.
    # Every directory/ACL/file write, copy, refresh and read is the real product.
    monkeypatch.setattr(setup, 'os', SimpleNamespace(environ={}))
    monkeypatch.setattr(config, '_KEY_FILE_VALUES', {})
    home = wf.address(Path.home() / ('.a9-product-' + uuid.uuid4().hex))
    updates = home / 'updates'
    live = home / 'live-shared'
    stage = updates / 'releases' / ('a' * 32)
    owner = SimpleNamespace(store=SimpleNamespace(shared_home=live))
    try:
        # UpdateManager.__init__ uses this exact private-directory boundary.
        with wf.directory(updates, owned_root=home, create=True):
            pass
        # SetupManager._keys is the provider setup's real shared keys writer
        # (setup.py), including its private directory, lock and publication.
        setup.SetupManager._keys(owner, {'FIXTURE_A9_PRODUCT_KEY': 'synthetic-original'})
        config.write_private(live / 'settings.yaml', '{}\n', owned_root=live)
        # install() deliberately uses ordinary mkdir; do not replace this with
        # a private-directory writer or directly publish the staged key.
        stage.mkdir(parents=True, mode=0o700)
        proof = {'candidate': stage.name, 'activeBefore': None,
                 'directoryIdentity': [stage.stat().st_dev, stage.stat().st_ino],
                 'boundary': 'copy-only', 'copySettled': True}
        await storage.snapshot_shared_config(proof, live, stage / 'shared-config')
        assert proof['copySettled'] is True, 'The real snapshot copy did not settle'
        assert (stage / 'shared-config/keys.env').is_file(), 'The product snapshot did not copy keys'
        yield home, live, stage, owner
    finally:
        if home.exists():
            shutil.rmtree(home)


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows product snapshot ACLs')
async def test_native_windows_stage_keys_product_snapshot_removal(native_product_stage_keys):
    home, live, stage, owner = native_product_stage_keys
    reason = storage.remove_stage_keys(home, stage)
    assert reason is None, f'Product mkdir/copy2 stage key removal returned {reason!r}: {native_refusal(home, stage)}'
    assert not (stage / 'shared-config/keys.env').exists(), 'Product snapshot key was retained'
    assert (stage / 'shared-config/settings.yaml').read_text() == '{}\n'


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows product refresh and reader')
async def test_native_windows_stage_keys_product_refresh_readback(native_product_stage_keys):
    from amplifier_web import setup
    from amplifier_web.host.config import _read_keys

    home, live, stage, owner = native_product_stage_keys
    current = {'FIXTURE_A9_PRODUCT_KEY': 'synthetic-current'}
    setup.SetupManager._keys(owner, current)
    reason = storage.refresh_stage_keys(home, stage, live)
    assert reason is None, f'Product mkdir/copy2 stage key refresh returned {reason!r}: {native_refusal(home, stage)}'
    # This is the private-file/parser reader used by _load_keys/load_config in
    # qualification. It reads only our synthetic file, not process environment.
    assert _read_keys(stage / 'shared-config/keys.env') == current, 'Qualification reader did not see current synthetic keys'
    reason = storage.remove_stage_keys(home, stage)
    assert reason is None, f'Refreshed product stage key removal returned {reason!r}'
    assert not (stage / 'shared-config/keys.env').exists(), 'Refreshed product key was retained'
    assert (stage / 'shared-config/settings.yaml').read_text() == '{}\n'
