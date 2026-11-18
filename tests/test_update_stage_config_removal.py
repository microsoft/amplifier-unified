"""Idle maintenance removes a stage's copies of the app's own ``config`` and ``routing`` folders.

An earlier version of the app copied both folders into each stage that a
component update prepared (``tests/test_update_stage_config.py``). ``config``
holds the sign-in secrets and the TLS key, and nothing reads the copies. The
idle pass that removes a stage's keys file removes them too
(``update_storage.remove_stage_config``, ``update_storage.scrub_stage_keys``).

Every private file here is made up. No case opens a real credential.
"""
import asyncio
from contextlib import contextmanager
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import time
import uuid

import pytest

from amplifier_web import update_storage as storage
from test_update_stage_config import (
    app_files, held, holds_the_mark, request_to_a_stage_of_an_earlier_app_version)
from test_update_stage_keys import ordinary_default_owner, stage_at
from test_updates import app, repo  # noqa: F401 -- existing real manager fixture

COPIES = ('config', 'routing')
# A link that needs no privilege, and removal relative to open folders. The native Windows cases are at the end.
posix = pytest.mark.skipif(os.name == 'nt', reason='not Windows: links without a privilege, open-folder removal')


def earlier_stage(home, name, *, validated=True, keys=False, shared=True):
    """A stage as an earlier version left it: whole copies of the app's ``config`` and ``routing``."""
    stage = stage_at(home, name, validated=validated)
    if not keys:
        (stage / 'shared-config/keys.env').unlink()
    if not shared:
        shutil.rmtree(stage / 'shared-config')
    (stage / 'foundation/cache').mkdir(parents=True)
    (stage / 'foundation/cache/module.py').write_text('made-up module')
    for record in ('runtime-sources.json', 'runtime.lock', 'runtime-installed.json'):
        (stage / record).write_text('{}')
    for folder in COPIES:
        shutil.copytree(home / folder, stage / folder)
    return stage


def small_stage(home, name, *, keys=False):
    """A settled stage with a small made-up copy: 5 entries."""
    stage = stage_at(home, name, validated=True)
    if not keys:
        (stage / 'shared-config/keys.env').unlink()
    for file in ('config/auth/control-token', 'routing/custom.yaml'):
        (stage / file).parent.mkdir(parents=True)
        (stage / file).write_text('made-up')
    return stage


def entries(folder):
    """How many entries a folder is: itself and each file, link and folder below it. No link is followed."""
    return 1 + sum(len(folders) + len(files) for _, folders, files in os.walk(folder))


def copied(stage):
    """The entries of the two copies in a stage; 0 when both are gone."""
    return sum(entries(stage / folder) for folder in COPIES if os.path.lexists(stage / folder))


def left(stage):
    """The names of what is left of the two copies, from the stage. A link or a junction is a name only."""
    names = [folder for folder in COPIES if os.path.lexists(stage / folder)]
    for folder in list(names):
        if (stage / folder).is_symlink() or os.path.isjunction(stage / folder):
            continue  # Never the names of what a link points to.
        for parent, folders, files in os.walk(stage / folder):
            names += [(Path(parent) / name).relative_to(stage).as_posix() for name in folders + files]
    return sorted(names)


def gone(stage):
    return not any(os.path.lexists(stage / folder) for folder in COPIES)


def rest(stage):
    """Every entry of a stage outside the two copies: its kind, its identity and its bytes."""
    found = {}
    for parent, folders, files in os.walk(stage):
        if Path(parent) == stage:
            folders[:] = [name for name in folders if name not in COPIES]
            files = [name for name in files if name not in COPIES]
        for name in folders + files:
            path = Path(parent) / name
            info = path.lstat()
            found[path.relative_to(stage).as_posix()] = (
                stat.S_IFMT(info.st_mode), info.st_ino,
                path.read_bytes() if stat.S_ISREG(info.st_mode) else None)
    return found


@contextmanager
def refusing(monkeypatch, name):
    """The removal of each file of this name is refused, as for a file that cannot be removed."""
    unlink = os.unlink

    def refuse(path, *args, **settings):
        if path == name:
            raise PermissionError('made-up private text')
        return unlink(path, *args, **settings)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'unlink', refuse)
        yield


# Case 3: the pass removes the copies from every kind of stage, and nothing else.

async def test_the_idle_pass_removes_the_copies_from_every_stage_that_has_them(app):
    """The pass, as the update owner runs it. No stage needs its copy, so no stage keeps it."""
    manager = app.update_manager
    home = manager.home
    before = app_files(home)
    stages = {
        'settled, with a keys file': earlier_stage(home, '1' * 32, keys=True),
        'settled, no keys file': earlier_stage(home, '2' * 32),
        'failed a moment ago': earlier_stage(home, '3' * 32, validated=False),
        'current': earlier_stage(home, '4' * 32),
        'previous': earlier_stage(home, '5' * 32),
        'no shared-config': earlier_stage(home, '6' * 32, validated=False, shared=False),
        'made a moment ago, with a keys file': earlier_stage(home, '7' * 32, validated=False, keys=True),
    }
    (manager.directory / 'active.json').write_text(json.dumps(
        {'current': stages['current'].name, 'previous': stages['previous'].name}))
    for stage in stages.values():
        assert set(held(stage)) == set(before) and holds_the_mark(stage)
    # A pass handles at most 8 stages and 64 entries of the copies. These copies need more than one pass.
    total = sum(copied(stage) for stage in stages.values())
    passes = -(-total // storage.STAGE_CONFIG_ENTRIES)
    assert len(stages) <= 8 and passes > 1
    kept = {kind: rest(stage) for kind, stage in stages.items()}
    state = copy.deepcopy(app.state['updates'])
    await storage.scrub_stage_keys(manager)
    assert sum(copied(stage) for stage in stages.values()) == total - storage.STAGE_CONFIG_ENTRIES
    # The keys file keeps its own rule in that pass, whether the stage's copy is gone or not.
    assert not (stages['settled, with a keys file'] / 'shared-config/keys.env').exists()
    for _ in range(passes - 1):
        await storage.scrub_stage_keys(manager)
    for kind, stage in stages.items():
        assert gone(stage), kind
        assert holds_the_mark(stage) == [], kind
        # The keys file keeps its own rule: a settled stage loses it, a stage of the last 24 hours keeps it.
        if kind == 'settled, with a keys file':
            assert kept[kind].pop('shared-config/keys.env')
        assert rest(stage) == kept[kind], kind
    assert (stages['made a moment ago, with a keys file'] / 'shared-config/keys.env').is_file()
    # The app's own folders: the same files with the same bytes.
    assert held(home) == before
    # The pass publishes nothing and changes no update state.
    assert app.state['updates'] == state


async def test_storage_maintenance_removes_the_copies_when_code_cleanup_is_off(app, monkeypatch):
    """Through the update owner's own maintenance step (``retire_storage``)."""
    from amplifier_web import storage_cleanup
    manager = app.update_manager
    before = app_files(manager.home)
    stage = earlier_stage(manager.home, 'a' * 32)
    kept, seen = rest(stage), []

    async def no_delay(seconds):
        pass

    async def disabled(*args, **kwargs):
        seen.append(gone(stage))
        return {'status': 'disabled', 'removedFileBytes': 0}

    monkeypatch.setattr(asyncio, 'sleep', no_delay)
    monkeypatch.setattr(storage_cleanup, 'run', disabled)
    await manager.retire_storage()
    assert seen == [True]
    assert rest(stage) == kept and held(manager.home) == before


async def test_the_removal_does_not_start_the_24_hours_of_a_keys_file_again(app, monkeypatch):
    """The keys file of a stage with no receipt waits 24 hours from the time of the stage's folders.

    The removal of the copies writes into the stage folder. It puts the
    folder's times back, so the keys file goes when its own 24 hours are over.
    """
    from types import SimpleNamespace
    manager = app.update_manager
    app_files(manager.home)
    stage = earlier_stage(manager.home, 'a' * 32, validated=False, keys=True)
    now = time.time()
    for folder in (stage, stage / 'shared-config'):
        os.utime(folder, (now - 23 * 3600, now - 23 * 3600))
    written = stage.stat().st_mtime_ns
    await storage.scrub_stage_keys(manager)
    assert gone(stage) and (stage / 'shared-config/keys.env').is_file()
    assert stage.stat().st_mtime_ns == written
    # Two hours later the keys file has waited 25 hours.
    monkeypatch.setattr(storage, 'time', SimpleNamespace(monotonic=time.monotonic, time=lambda: now + 2 * 3600))
    await storage.scrub_stage_keys(manager)
    assert not (stage / 'shared-config/keys.env').exists()


# Case 4: the admission and the bounds of the pass.

@pytest.mark.parametrize('blocker', ['lock', 'checking', 'staging', 'validating', 'activating',
                                    'pendingRestart', 'pendingReplacement', 'pendingRelease',
                                    'pendingApp', 'pendingRollback', 'check-task', 'closed'])
async def test_the_pass_leaves_the_copies_while_an_update_operation_runs_or_waits(app, blocker):
    manager = app.update_manager
    app_files(manager.home)
    stage = earlier_stage(manager.home, 'a' * 32)
    copies, state = held(stage), app.state['updates']
    earlier = copy.deepcopy(state)
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
        assert held(stage) == copies
    finally:
        if blocker == 'lock':
            manager.lock.release()
        elif blocker == 'closed':
            manager.closed = False
        elif blocker == 'check-task':
            manager.check_task.cancel()
            await asyncio.gather(manager.check_task, return_exceptions=True)
            manager.check_task = None
        else:
            state.clear()
            state.update(earlier)
    # The same stage, when the operation is over.
    await storage.scrub_stage_keys(manager)
    assert gone(stage)


async def test_the_pass_looks_again_for_an_update_operation_after_it_has_the_lifecycle(app, monkeypatch):
    from contextlib import asynccontextmanager
    manager = app.update_manager
    app_files(manager.home)
    stage = earlier_stage(manager.home, 'a' * 32)
    copies = held(stage)

    @asynccontextmanager
    async def lifecycle():
        app.state['updates']['phase'] = 'validating'
        yield

    phase = app.state['updates'].get('phase')
    with monkeypatch.context() as patch:
        patch.setattr(app, 'runtime_lifecycle', lifecycle)
        await storage.scrub_stage_keys(manager)
    assert held(stage) == copies
    # The same stage, when no operation starts.
    app.state['updates']['phase'] = phase
    await storage.scrub_stage_keys(manager)
    assert gone(stage)


@pytest.mark.parametrize('missing', ['lock', 'diagnostics'])
async def test_the_pass_needs_the_update_lock_and_the_preparation_record(app, missing):
    from types import SimpleNamespace
    manager = SimpleNamespace(**vars(app.update_manager))
    app_files(manager.home)
    stage = earlier_stage(manager.home, 'a' * 32)
    copies = held(stage)
    setattr(manager, missing, None)
    await storage.scrub_stage_keys(manager)
    assert held(stage) == copies
    # The same stage, with the update owner itself.
    await storage.scrub_stage_keys(app.update_manager)
    assert gone(stage)


async def test_the_pass_leaves_the_stage_of_a_preparation_that_is_not_reconciled(app):
    """A copy may not have settled there. The record names the stage as its revision or as its candidate."""
    manager = app.update_manager
    app_files(manager.home)
    revision, candidate, other = (earlier_stage(manager.home, name * 32, validated=False) for name in '456')
    copies = held(revision), held(candidate)
    manager.diagnostics.state['componentPreparation'] = {
        'retry': {'mode': 'inspect'}, 'preparation': {'candidate': candidate.name}, 'revision': revision.name}
    await storage.scrub_stage_keys(manager)
    assert (held(revision), held(candidate)) == copies
    assert gone(other)
    # When the preparation is reconciled, the next passes handle both.
    del manager.diagnostics.state['componentPreparation']
    for _ in range(2):
        await storage.scrub_stage_keys(manager)
    assert gone(revision) and gone(candidate)


async def test_the_pass_handles_eight_stages_and_the_next_pass_goes_on(app):
    manager = app.update_manager
    stages = [small_stage(manager.home, f'{number:032x}') for number in range(100, 111)]
    await storage.scrub_stage_keys(manager)
    assert sum(gone(stage) for stage in stages) == 8
    await storage.scrub_stage_keys(manager)
    assert all(gone(stage) for stage in stages)
    assert all(stage.is_dir() and (stage / 'validated.json').is_file() for stage in stages)


async def test_the_copies_and_the_keys_files_have_separate_bounds_of_eight_stages(app):
    """Nine settled stages with a copy and a keys file. Each of the two removals handles eight stages in one pass."""
    manager = app.update_manager
    stages = [small_stage(manager.home, f'{number:032x}', keys=True) for number in range(100, 109)]
    await storage.scrub_stage_keys(manager)
    assert sum(gone(stage) for stage in stages) == 8
    assert sum(not (stage / 'shared-config/keys.env').exists() for stage in stages) == 8
    await storage.scrub_stage_keys(manager)
    assert all(gone(stage) and not (stage / 'shared-config/keys.env').exists() for stage in stages)


def large_stage(home, name, files, *, keys=False):
    """A settled stage with a made-up copy of 6 entries and ``files`` more files."""
    stage = small_stage(home, name, keys=keys)
    (stage / 'config/services').mkdir()
    for number in range(files):
        (stage / 'config/services' / f'made-up-{number}.json').write_text('{}')
    return stage


async def test_a_copy_that_is_larger_than_one_pass_does_not_hold_back_a_keys_file(app):
    """A settled stage with a large copy and a keys file, and settled stages with a keys file only.

    The removal of the copies has its own count and its own place in the list
    of stages. Every keys file goes in the first pass, while the large copy
    still has entries: the size of a copy changes no rule of a keys file.
    """
    manager = app.update_manager
    large = large_stage(manager.home, '5' * 32, 294, keys=True)
    others = [stage_at(manager.home, name * 32, validated=True) for name in '19']
    assert copied(large) == 300
    await storage.scrub_stage_keys(manager)
    for stage in (large, *others):
        assert not (stage / 'shared-config/keys.env').exists()
    assert copied(large) == 300 - storage.STAGE_CONFIG_ENTRIES
    for remaining in (172, 108, 44, 0):
        await storage.scrub_stage_keys(manager)
        assert copied(large) == remaining
    assert (large / 'validated.json').is_file() and (large / 'shared-config/settings.yaml').is_file()


async def test_the_pass_stops_at_its_count_of_entries_and_comes_back_to_a_stage_after_the_others(app):
    """Nine copies that together are larger than the count of one pass, then one copy that is larger alone.

    The order of the stages is the folder's. The case holds for every order.
    """
    manager = app.update_manager
    assert storage.STAGE_CONFIG_ENTRIES == 64
    stages = [large_stage(manager.home, f'{number:032x}', 24) for number in range(1, 10)]
    assert all(copied(stage) == 30 for stage in stages)
    # Each pass handles exactly its count, while that many entries are left: two copies and 4 entries of a third.
    for number, remaining in enumerate((206, 142, 78), 1):
        await storage.scrub_stage_keys(manager)
        assert sum(copied(stage) for stage in stages) == remaining
        # A pass starts after the stage that the pass before left. So each of these passes cuts another stage.
        assert sorted(copied(stage) for stage in stages if 0 < copied(stage) < 30) == [26] * number
    # Every stage had its turn. The next passes go on with the copies that were cut.
    for remaining in (14, 0):
        await storage.scrub_stage_keys(manager)
        assert sum(copied(stage) for stage in stages) == remaining
    # One copy that is larger than the count: each pass goes on with it.
    alone = large_stage(manager.home, 'c' * 32, 144)
    assert copied(alone) == 150
    for remaining in (86, 22, 0):
        await storage.scrub_stage_keys(manager)
        assert copied(alone) == remaining
    for stage in (*stages, alone):
        assert (stage / 'validated.json').is_file() and (stage / 'shared-config/settings.yaml').is_file()


async def test_the_pass_gives_the_removal_the_stage_that_it_saw_and_the_entries_that_are_left(app, monkeypatch):
    manager = app.update_manager
    stages = [small_stage(manager.home, name * 32) for name in 'ab']
    identities = {stage: (stage.lstat().st_dev, stage.lstat().st_ino) for stage in stages}
    remove, calls = storage._remove_stage_config, []

    def record(home, stage, expected, work):
        calls.append((home, stage, expected, work.remaining()))
        return remove(home, stage, expected, work)

    monkeypatch.setattr(storage, '_remove_stage_config', record)
    await storage.scrub_stage_keys(manager)
    assert calls == [(manager.home, stage, {stage: identities[stage]}, remaining)
                     for stage, remaining in zip([call[1] for call in calls], (64, 59))]
    assert {call[1] for call in calls} == set(stages) and all(gone(stage) for stage in stages)


async def test_the_pass_keeps_its_one_second_and_still_reaches_every_stage(app, monkeypatch):
    """A slow folder: each look at the clock is past the limit. One stage in each pass, and none twice."""
    from types import SimpleNamespace
    manager = app.update_manager
    stages = [small_stage(manager.home, name * 32) for name in 'abc']
    clock = iter(range(0, 10 ** 6, 2))  # Each call is 2 s later; the limit is 1 s.
    monkeypatch.setattr(storage, 'time', SimpleNamespace(monotonic=lambda: next(clock), time=time.time))
    left_after = []
    for _ in range(3):
        await storage.scrub_stage_keys(manager)
        left_after.append(sum(not gone(stage) for stage in stages))
    assert left_after == [2, 1, 0]


# Case 5: what is refused, and what a link does.

@posix
@pytest.mark.parametrize('entry', ['a link to the app folder', 'a file'])
def test_an_entry_that_is_a_link_or_no_folder_is_refused_and_never_followed(tmp_path, entry):
    home = tmp_path / 'app'
    before = app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    shutil.rmtree(stage / 'config')
    if entry == 'a file':
        (stage / 'config').write_text('made-up file')
    else:
        (stage / 'config').symlink_to(home / 'config', target_is_directory=True)
    kept, routing = rest(stage), entries(stage / 'routing')
    # The other entry is still handled.
    assert storage.remove_stage_config(home, stage) == ('refused', routing)
    assert storage.remove_stage_config(home, stage) == ('refused', 0)
    assert left(stage)[0] == 'config' and not os.path.lexists(stage / 'routing')
    assert (stage / 'config').is_symlink() == (entry != 'a file')
    assert rest(stage) == kept and held(home) == before


@posix
async def test_the_pass_leaves_a_link_named_config_and_the_folder_that_it_points_to(app):
    manager = app.update_manager
    before = app_files(manager.home)
    stage, other = earlier_stage(manager.home, 'a' * 32), earlier_stage(manager.home, 'b' * 32)
    shutil.rmtree(stage / 'config')
    (stage / 'config').symlink_to(manager.home / 'config', target_is_directory=True)
    for _ in range(2):
        await storage.scrub_stage_keys(manager)
        assert (stage / 'config').is_symlink() and not os.path.lexists(stage / 'routing')
        assert gone(other) and held(manager.home) == before


@posix
def test_a_link_inside_the_copy_goes_as_a_link_and_its_target_stays(tmp_path):
    home = tmp_path / 'app'
    before = app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    outside = tmp_path / 'outside'
    (outside / 'folder').mkdir(parents=True)
    (outside / 'folder/file').write_text('made-up file outside the stage')
    (stage / 'config/auth/to-a-file').symlink_to(home / 'config/auth/control-token')
    (stage / 'config/to-a-folder').symlink_to(home / 'config/tls', target_is_directory=True)
    (stage / 'routing/to-a-folder').symlink_to(outside, target_is_directory=True)
    (stage / 'routing/to-nothing').symlink_to(tmp_path / 'absent')
    kept, total = rest(stage), copied(stage)
    assert storage.remove_stage_config(home, stage) == (None, total)
    assert gone(stage) and rest(stage) == kept and held(home) == before
    assert (outside / 'folder/file').read_text() == 'made-up file outside the stage'


@posix
def test_a_copy_that_is_deeper_than_the_bound_is_refused(tmp_path):
    home = tmp_path / 'app'
    before = app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    deep = stage / 'config' / Path(*['d'] * 40)
    deep.mkdir(parents=True)
    (deep / 'file').write_text('made-up')
    reason, count = storage.remove_stage_config(home, stage)
    assert reason == 'refused' and count
    assert (deep / 'file').is_file() and not os.path.lexists(stage / 'routing')
    # Only the deep folders are left: the other entries of the copy are gone.
    assert left(stage) == sorted(['config', *(Path('config', *['d'] * depth).as_posix() for depth in range(1, 41)),
                                  (Path('config', *['d'] * 40) / 'file').as_posix()])
    assert held(home) == before


@posix
def test_an_entry_on_another_device_is_refused(tmp_path):
    """The walk itself, with a device that is not the stage's: no test folder has a second device."""
    home = tmp_path / 'app'
    app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    copies = held(stage)
    folder = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
    try:
        device, work = os.fstat(folder).st_dev, storage._Work(64)
        assert storage._posix_remove_tree(folder, 'config', device + 1, work, lambda: None) == 'refused'
        # The folder stays, and it is one of the entries that the work handled.
        assert (work.removed, work.stayed) == (0, 1) and held(stage) == copies
        assert storage._posix_remove_tree(folder, 'config', device, work, lambda: None) is None
        assert work.removed and not os.path.lexists(stage / 'config')
    finally:
        os.close(folder)


@posix
def test_a_folder_that_is_replaced_between_the_look_and_the_open_is_refused(tmp_path, monkeypatch):
    """Another folder takes the name ``config`` after the removal looked at the entry. Nothing of it goes."""
    home = tmp_path / 'app'
    before = app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    other = tmp_path / 'other'
    shutil.copytree(home / 'config', other)
    opened, real = [], os.open

    def replace_then_open(name, flags, *args, **settings):
        if name == 'config' and settings.get('dir_fd') is not None and not opened:
            opened.append(name)
            (stage / 'config').rename(tmp_path / 'first')
            other.rename(stage / 'config')
        return real(name, flags, *args, **settings)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'open', replace_then_open)
        result = storage.remove_stage_config(home, stage)
    assert opened and result == ('refused', entries(home / 'routing'))
    # The folder that has the name now is complete, and so is the folder that had it.
    assert set(held(stage, ('config',))) == {name for name in before if name.startswith('config/')}
    assert entries(tmp_path / 'first') == entries(home / 'config')
    assert held(home) == before


@posix
def test_a_stage_that_is_replaced_after_it_was_opened_is_refused_before_the_first_removal(tmp_path, monkeypatch):
    """The open folders are checked again directly before the first removal."""
    home = tmp_path / 'app'
    before = app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    copies, real = held(stage), storage._posix_directory

    @contextmanager
    def replaced(path, expected=None):
        with real(path, expected) as pinned:
            stage.rename(tmp_path / 'first')
            shutil.copytree(tmp_path / 'first', stage)
            yield pinned

    monkeypatch.setattr(storage, '_posix_directory', replaced)
    assert storage.remove_stage_config(home, stage) == ('refused', 0)
    # Neither folder lost an entry: the one that was opened, and the one that has the stage's name now.
    assert held(tmp_path / 'first') == copies and set(held(stage)) == set(copies)
    assert held(home) == before


@posix
def test_remove_refuses_a_stage_that_is_no_longer_the_one_that_was_checked(tmp_path):
    home = tmp_path / 'app'
    app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    copies, total = held(stage), copied(stage)
    assert storage.remove_stage_config(home, stage, expected={stage: (0, 0)}) == ('refused', 0)
    assert held(stage) == copies
    real = {stage: (stage.stat().st_dev, stage.stat().st_ino)}
    assert storage.remove_stage_config(home, stage, expected=real) == (None, total)
    assert gone(stage)


@pytest.mark.parametrize('name', ['A' * 32, 'a' * 31, 'not-a-release', 'a' * 32 + '/nested'])
def test_remove_refuses_a_path_that_is_no_stage(tmp_path, name):
    home = tmp_path / 'app'
    app_files(home)
    stage = earlier_stage(home, name)
    copies = held(stage)
    assert storage.remove_stage_config(home, stage) == ('refused', 0)
    assert held(stage) == copies


def test_remove_refuses_a_stage_of_another_home_and_a_path_with_a_step_back(tmp_path):
    home = tmp_path / 'app'
    app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    copies = held(stage)
    assert storage.remove_stage_config(tmp_path / 'other', stage) == ('refused', 0)
    assert storage.remove_stage_config(home, stage.parent / ('b' * 32) / '..' / stage.name) == ('refused', 0)
    assert storage.remove_stage_config(home / 'updates/..', stage) == ('refused', 0)
    assert storage.remove_stage_config(None, None) == ('refused', 0)
    assert held(stage) == copies


@posix
@pytest.mark.parametrize('part', ['home', 'updates', 'releases', 'stage'])
def test_remove_follows_no_link_on_the_way_to_the_stage(tmp_path, part):
    home = tmp_path / 'app'
    app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    original = {'home': home, 'updates': home / 'updates', 'releases': home / 'updates/releases',
                'stage': stage}[part]
    saved = tmp_path / 'saved'
    original.rename(saved)
    original.symlink_to(saved, target_is_directory=True)
    copies = held(stage)
    reason, count = storage.remove_stage_config(home, stage)
    assert reason in ('refused', 'io') and count == 0
    assert held(stage) == copies and original.is_symlink()


@posix
def test_a_stage_with_no_copy_and_an_absent_stage_have_nothing_to_remove(tmp_path):
    home = tmp_path / 'app'
    stage = stage_at(home)
    kept = rest(stage)
    assert storage.remove_stage_config(home, stage) == (None, 0)
    assert storage.remove_stage_config(home, stage, limit=0) == (None, 0)
    assert storage.remove_stage_config(home, stage.parent / ('c' * 32)) == (None, 0)
    assert rest(stage) == kept


# Case 6: a removal that stops part-way.

@posix
@pytest.mark.parametrize('call, after, error, reason', [
    ('unlink', 1, PermissionError('made-up private text'), 'refused'),
    ('unlink', 4, OSError(errno.EIO, 'made-up private text'), 'io'),
    ('rmdir', 1, PermissionError('made-up private text'), 'refused'),
    ('rmdir', 3, OSError(errno.EIO, 'made-up private text'), 'io')])
def test_a_removal_that_stops_part_way_gives_a_reason_and_a_later_call_removes_the_rest(
        tmp_path, monkeypatch, call, after, error, reason):
    """One removal fails after ``after`` removals of its kind worked. The call goes on with the other entries."""
    home = tmp_path / 'app'
    before = app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    kept, total = rest(stage), copied(stage)
    real, calls = getattr(os, call), []

    def fail_once(*args, **settings):
        calls.append(args[0])
        if len(calls) == after + 1:
            raise error
        return real(*args, **settings)

    with monkeypatch.context() as patch:
        patch.setattr(os, call, fail_once)
        result = storage.remove_stage_config(home, stage)   # No exception.
    stays = left(stage)
    # A reason code and a count: no name, no size and no bytes of a file.
    assert type(result) is tuple and type(result[0]) is str and type(result[1]) is int
    assert result == (reason, total - len(stays)) and result[1] >= after and len(calls) > after
    # What stays is the one entry whose removal failed, and the folders above it.
    assert stays and all(Path(below).parent.as_posix() == above for above, below in zip(stays, stays[1:]))
    assert stays[0] in COPIES and Path(stays[-1]).name == calls[after]
    assert rest(stage) == kept and held(home) == before
    # A later call removes the rest.
    assert storage.remove_stage_config(home, stage) == (None, len(stays))
    assert gone(stage) and rest(stage) == kept and held(home) == before


@posix
def test_a_call_removes_at_most_its_limit_and_a_later_call_goes_on(tmp_path):
    home = tmp_path / 'app'
    before = app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    kept, total, counts = rest(stage), copied(stage), []
    assert storage.remove_stage_config(home, stage, limit=0) == ('more', 0) and copied(stage) == total
    while True:
        result = storage.remove_stage_config(home, stage, limit=5)
        # A reason code and a count: no name, no size and no bytes of a file.
        assert type(result) is tuple and type(result[0]) in (str, type(None)) and type(result[1]) is int
        reason, count = result
        counts.append(count)
        assert copied(stage) == total - sum(counts)
        if reason is None:
            break
        assert (reason, count) == ('more', 5)
    assert sum(counts) == total and len(counts) == -(-total // 5) and gone(stage)
    assert rest(stage) == kept and held(home) == before


@posix
async def test_the_pass_leaves_an_entry_that_cannot_be_removed_and_a_later_pass_removes_it(app, monkeypatch):
    manager = app.update_manager
    before = app_files(manager.home)
    stage, other = earlier_stage(manager.home, 'a' * 32), earlier_stage(manager.home, 'b' * 32)
    kept, state = rest(stage), copy.deepcopy(app.state['updates'])
    with refusing(monkeypatch, 'leaf.key'):
        await storage.scrub_stage_keys(manager)   # No exception.
    for folder in (stage, other):
        assert left(folder) == ['config', 'config/tls', 'config/tls/leaf.key']
    assert rest(stage) == kept and held(manager.home) == before and app.state['updates'] == state
    await storage.scrub_stage_keys(manager)
    assert gone(stage) and gone(other)
    assert rest(stage) == kept and held(manager.home) == before and app.state['updates'] == state


# The work of one call has a bound: an entry that stays counts, and so does the listing of a folder.

@posix
def test_an_entry_that_stays_is_one_of_the_entries_that_a_call_handles(tmp_path, monkeypatch):
    """No file of the copy can be removed. A call stops at its limit: it does not try every file of a large copy."""
    home = tmp_path / 'app'
    before = app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    for number in range(200):
        (stage / 'config/services' / f'made-up-{number}.json').write_text('{}')
    copies, tried, unlink = held(stage), [], os.unlink

    def refuse(path, *args, **settings):
        if settings.get('dir_fd') is None:
            return unlink(path, *args, **settings)
        tried.append(path)
        raise PermissionError('made-up private text')

    with monkeypatch.context() as patch:
        patch.setattr(os, 'unlink', refuse)
        results = [storage.remove_stage_config(home, stage, limit=10) for _ in range(3)]
    # No entry is removed, and no call tries more entries than its limit.
    assert results == [('more', 0)] * 3 and 0 < len(tried) <= 30
    assert held(stage) == copies and held(home) == before


@posix
def test_the_listing_of_a_folder_takes_no_more_entries_than_a_call_may_handle(tmp_path, monkeypatch):
    home = tmp_path / 'app'
    before = app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    for number in range(300):
        (stage / 'config/services' / f'made-up-{number}.json').write_text('{}')
    total, listings, scandir = copied(stage), [], os.scandir

    @contextmanager
    def counted(folder):
        taken = []
        listings.append(taken)
        with scandir(folder) as listing:
            def some():
                for entry in listing:
                    taken.append(entry.name)
                    yield entry
            yield some()

    with monkeypatch.context() as patch:
        patch.setattr(os, 'scandir', counted)
        result = storage.remove_stage_config(home, stage, limit=10)
    assert result == ('more', 10) and copied(stage) == total - 10
    # One entry more than the call may still handle tells it that a folder has more.
    assert listings and max(len(taken) for taken in listings) <= 11
    assert held(home) == before


# A folder that another process moves or replaces while the walk has it open.

@posix
@pytest.mark.parametrize('moved, takes_the_name', [
    ('the copy', None), ('the copy', 'an empty folder'), ('the copy', 'a folder with a file'), ('the stage', None)])
def test_a_copy_or_a_stage_that_is_moved_during_the_walk_loses_no_further_entry(
        tmp_path, monkeypatch, moved, takes_the_name):
    """Another process moves a folder away while the walk has it open, after two files of the copy are gone.

    The walk looks at its folders again before each removal. It stops there:
    what was moved keeps its other entries, and a folder that took the name is
    not touched.
    """
    home = tmp_path / 'app'
    before = app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    source = stage if moved == 'the stage' else stage / 'config'
    away, total, unlink, calls = tmp_path / 'away', entries(source), os.unlink, []

    def unlink_then_move(path, *args, **settings):
        result = unlink(path, *args, **settings)
        if settings.get('dir_fd') is not None:
            calls.append(path)
            if len(calls) == 2:
                source.rename(away)
                if takes_the_name:
                    source.mkdir()
                if takes_the_name == 'a folder with a file':
                    (source / 'made-up').write_text('made-up file of another process')
        return result

    with monkeypatch.context() as patch:
        patch.setattr(os, 'unlink', unlink_then_move)
        reason, count = storage.remove_stage_config(home, stage)   # No exception.
    # No removal came after the move: two files, and the folders that were empty before the second one.
    assert reason == 'refused' and len(calls) == 2 and count >= 2
    assert entries(away) == total - count
    if takes_the_name:
        assert sorted(path.name for path in source.iterdir()) == (
            ['made-up'] if takes_the_name == 'a folder with a file' else [])
    assert held(home) == before


@posix
def test_an_empty_folder_that_takes_a_name_before_the_removal_of_a_folder_stays(tmp_path, monkeypatch):
    """The last file of a folder is gone. Another process moves the folder away and puts an empty folder at its name.

    The walk looks at the name again directly before it removes a folder. The other folder stays.
    """
    home = tmp_path / 'app'
    before = app_files(home)
    stage = earlier_stage(home, 'a' * 32)
    tls, away, unlink, files = stage / 'config/tls', tmp_path / 'away', os.unlink, []

    def unlink_then_replace(path, *args, **settings):
        result = unlink(path, *args, **settings)
        if settings.get('dir_fd') is not None and path in ('leaf.key', 'leaf.crt', 'ca.crt'):
            files.append(path)
            if len(files) == 3:
                tls.rename(away)
                tls.mkdir()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(os, 'unlink', unlink_then_replace)
        reason, count = storage.remove_stage_config(home, stage)   # No exception.
    assert reason == 'refused' and len(files) == 3 and count >= 3
    # The folder that the walk emptied is where the other process put it. The folder that took its name stays.
    assert away.is_dir() and list(away.iterdir()) == []
    assert tls.is_dir() and list(tls.iterdir()) == []
    assert held(home) == before


# Case 7 (b): a restore to a stage of an earlier app version, after the pass.

@posix
@pytest.mark.parametrize('copies', ['removed', 'partly removed'])
async def test_a_restore_to_a_stage_of_an_earlier_app_version_after_the_pass_removed_its_copies(
        app, repo, tmp_path, monkeypatch, copies):
    """"Restore previous components" to a stage that had the copies, after the idle pass.

    The request, its second validation and the probe are those of the case in
    ``tests/test_update_stage_config.py``, with the same stand-ins. Here the
    idle pass runs directly before the request, when the stage is settled and
    is the previous stage: it removes the copies, or it removes all of them but
    one file that is refused. The request validates and selects the stage in
    both states, and it changes nothing of what is left. A pass after the
    request removes the rest.
    """
    refused = ['config', 'config/tls', 'config/tls/leaf.key']

    async def idle_pass(manager, stage):
        assert (stage / 'validated.json').is_file() and holds_the_mark(stage)
        if copies == 'removed':
            await storage.scrub_stage_keys(manager)
            assert gone(stage)
        else:
            with refusing(monkeypatch, 'leaf.key'):
                await storage.scrub_stage_keys(manager)
            assert left(stage) == refused

    manager, stage = await request_to_a_stage_of_an_earlier_app_version(
        app, repo, tmp_path, monkeypatch, 'restore', 'before the change', idle_pass)
    if copies == 'partly removed':
        assert left(stage) == refused
        await storage.scrub_stage_keys(manager)
    assert gone(stage) and holds_the_mark(stage) == []
    assert (stage / 'foundation').is_dir() and (stage / 'shared-config/settings.yaml').is_file()


# The walk that Windows uses, with a stand-in for the private-file boundary. Not Windows.

@posix
def test_the_walk_of_the_private_boundary_with_a_stand_in_for_the_boundary(tmp_path, monkeypatch):
    """The steps of ``_windows_stage_config`` on this platform, with a stand-in for ``windows_files.directory``.

    Shown: which entries the walk gives to the boundary and in which order it
    removes folders, its counts, its limit, and what a refusal leaves. The
    stand-in refuses the removal of a folder while a pin of that folder is
    open: the walk must close a folder's pin first. Not shown: anything that
    Windows itself does (the ACL check, the pins, the delete of an open file,
    a junction). The native cases below show that on a hosted runner.
    """
    from amplifier_web import windows_files
    home = tmp_path / 'app'
    before = app_files(home)
    pins, refused, reads, hooks = [], set(), [], {}

    class Pinned:
        def __init__(self, path):
            self.path = path

        def read_private(self, name, *, limit):
            reads.append(name)
            if name in refused:
                raise PermissionError('made-up private text')
            content = (self.path / name).read_bytes()
            if len(content) > limit:
                raise ValueError('made-up private text')
            return content

        def remove_private(self, name, digest):
            assert hashlib.sha256((self.path / name).read_bytes()).hexdigest() == digest
            (self.path / name).unlink()

    @contextmanager
    def directory(path, *, owned_root, create=False):
        assert owned_root == home and create is False
        hooks.pop(('before the pin', path), lambda: None)()
        if not path.is_dir():
            raise FileNotFoundError('made-up private text')
        pins.append(path)
        try:
            yield Pinned(path)
        finally:
            pins.remove(path)
            hooks.pop(('after the pin', path), lambda: None)()

    rmdir = os.rmdir

    def unpinned_rmdir(path, *args, **settings):
        if path in pins:
            raise PermissionError('made-up private text')
        return rmdir(path, *args, **settings)

    monkeypatch.setattr(windows_files, 'directory', directory)
    monkeypatch.setattr(os, 'rmdir', unpinned_rmdir)
    monkeypatch.setattr(storage, '_posix_stage_config', lambda stage, expected, removed:
                        storage._windows_stage_config(home, stage, expected, removed))

    # The whole copies, with the identity that the pass gives.
    stage = earlier_stage(home, 'a' * 32)
    kept, total = rest(stage), copied(stage)
    identity = {stage: (stage.stat().st_dev, stage.stat().st_ino)}
    assert storage.remove_stage_config(home, stage, expected={stage: (0, 0)}) == ('refused', 0)
    assert storage.remove_stage_config(home, stage, limit=4) == ('more', 4)
    assert storage.remove_stage_config(home, stage, expected=identity) == (None, total - 4)
    assert gone(stage) and rest(stage) == kept and not pins
    assert storage.remove_stage_config(home, stage) == (None, 0)

    # A file that the boundary refuses, a file above 1 MiB and a link: each stays with its folders.
    stage = earlier_stage(home, 'b' * 32)
    refused.add('leaf.key')
    (stage / 'config/services/large').write_bytes(b'0' * (1024 * 1024 + 1))
    (stage / 'routing/to-a-folder').symlink_to(home / 'config', target_is_directory=True)
    kept, total, stays = rest(stage), copied(stage), [
        'config', 'config/services', 'config/services/large', 'config/tls', 'config/tls/leaf.key',
        'routing', 'routing/to-a-folder']
    del reads[:]
    assert storage.remove_stage_config(home, stage) == ('refused', total - len(stays))
    assert left(stage) == stays and rest(stage) == kept and not pins
    assert 'to-a-folder' not in reads   # The link was not given to the boundary.
    # When nothing is refused any more, a later call removes the rest.
    refused.clear()
    (stage / 'config/services/large').write_bytes(b'small')
    (stage / 'routing/to-a-folder').unlink()
    assert storage.remove_stage_config(home, stage) == (None, len(stays) - 1)
    assert gone(stage) and rest(stage) == kept

    # An entry ``config`` that is a link: it stays, and the other entry is still handled.
    stage = earlier_stage(home, 'c' * 32)
    shutil.rmtree(stage / 'config')
    (stage / 'config').symlink_to(home / 'config', target_is_directory=True)
    routing = entries(stage / 'routing')
    assert storage.remove_stage_config(home, stage) == ('refused', routing)
    assert (stage / 'config').is_symlink() and not os.path.lexists(stage / 'routing')
    assert held(home) == before

    # Another folder takes the name of a folder between the walk's look and its pin: nothing of it goes.
    stage = earlier_stage(home, 'd' * 32)
    other, first = tmp_path / 'other-tls', tmp_path / 'first-tls'
    shutil.copytree(home / 'config/tls', other)

    def replace():
        (stage / 'config/tls').rename(first)
        other.rename(stage / 'config/tls')

    hooks[('before the pin', stage / 'config/tls')] = replace
    total = copied(stage)
    stays = ['config', 'config/tls', 'config/tls/ca.crt', 'config/tls/leaf.crt', 'config/tls/leaf.key']
    assert storage.remove_stage_config(home, stage) == ('refused', total - len(stays))
    assert left(stage) == stays and entries(first) == 4 and not hooks and not pins

    # An empty folder takes the name after the pin is closed, before the folder is removed: it stays.
    stage = earlier_stage(home, 'e' * 32)
    away = tmp_path / 'away-tls'

    def replace_with_an_empty_folder():
        (stage / 'config/tls').rename(away)
        (stage / 'config/tls').mkdir()

    hooks[('after the pin', stage / 'config/tls')] = replace_with_an_empty_folder
    total = copied(stage)
    assert storage.remove_stage_config(home, stage) == ('refused', total - 2)
    assert left(stage) == ['config', 'config/tls'] and list(away.iterdir()) == [] and not hooks and not pins
    assert held(home) == before


# Case 8: native Windows, on a hosted runner. A skip fails the workflow's count.

def boundary_refusals(home, stage):
    """Which entries of the copies the private boundary refuses, in the boundary's own words.

    The product returns a reason code only. A native failure must name its
    cause, or the next reader guesses. The names and the files are made up.
    """
    from amplifier_web import windows_files as wf
    refusals = {}
    for folder in COPIES:
        for parent, folders, files in os.walk(stage / folder):
            relative = Path(parent).relative_to(stage).as_posix()
            try:
                with wf.directory(Path(parent), owned_root=home, create=False) as pinned:
                    for name in files:
                        try:
                            pinned.read_private(name, limit=1024 * 1024)
                        except Exception as error:
                            refusals[f'{relative}/{name}'] = f'{type(error).__name__}: {error}'
            except Exception as error:
                refusals[relative] = f'{type(error).__name__}: {error}'
    return refusals


@pytest.fixture
def native_stage():
    """A private app home in the profile, and a stage with copies that a real ``shutil.copytree`` made.

    The account owns what it makes, as on a person's PC (``ordinary_default_owner``).
    The stage and its copies come from an ordinary ``mkdir`` and an ordinary
    copy, as the preparation of the earlier version made them.
    """
    from amplifier_web import windows_files as wf
    homes = []

    def make():
        # A private child of the profile, not a runner temp with foreign ACLs.
        home = wf.address(Path.home() / ('.a34-' + uuid.uuid4().hex))
        homes.append(home)
        stage = home / 'updates/releases' / ('a' * 32)
        # UpdateManager.__init__ makes the updates folder through this boundary.
        with wf.directory(home / 'updates', owned_root=home, create=True):
            pass
        with ordinary_default_owner():
            before = app_files(home)
            stage.mkdir(parents=True, mode=0o700)   # As UpdateManager.install makes it.
            (stage / 'foundation').mkdir()
            (stage / 'foundation/module.py').write_text('made-up module')
            (stage / 'shared-config').mkdir()
            (stage / 'shared-config/settings.yaml').write_text('{}\n')
            (stage / 'validated.json').write_text('{}')
            for folder in COPIES:
                shutil.copytree(home / folder, stage / folder)
        assert set(held(stage)) == set(before) and holds_the_mark(stage)
        return home, stage, before

    try:
        yield make
    finally:
        for home in homes:
            if home.exists():
                shutil.rmtree(home)


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows private files')
def test_native_windows_stage_config_copies_go_through_the_private_boundary(native_stage, capsys):
    home, stage, before = native_stage()
    kept, total = rest(stage), copied(stage)
    info = stage.lstat()
    written = info.st_mtime_ns
    assert storage.remove_stage_config(home, stage, expected={stage: (0, 0)}) == ('refused', 0)
    started = time.perf_counter()
    result = storage.remove_stage_config(home, stage, expected={stage: (info.st_dev, info.st_ino)})
    elapsed = time.perf_counter() - started
    with capsys.disabled():
        # The pass runs this on the host's event loop. The job's log keeps the time of this runner.
        print(f'\nnative removal of a stage\'s copies: {result[1]} entries in {elapsed * 1000:.0f} ms')
    assert result == (None, total), (
        f'The removal of copies that copytree made returned {result!r}; left {left(stage)}; '
        f'the boundary refuses {boundary_refusals(home, stage)}')
    assert gone(stage) and holds_the_mark(stage) == []
    # The stage's other entries and the app's own folders keep their files.
    assert rest(stage) == kept and held(home) == before
    assert storage.remove_stage_config(home, stage) == (None, 0)
    # The stage folder has the time that it had: the 24 hours of a keys file do not start again.
    assert stage.lstat().st_mtime_ns == written, 'The removal changed the time of the stage folder'


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows private files')
def test_native_windows_stage_config_file_that_another_handle_holds_open_stays_until_it_is_closed(native_stage):
    home, stage, before = native_stage()
    kept, total = rest(stage), copied(stage)
    stays = ['config', 'config/tls', 'config/tls/leaf.key']
    # Python's ordinary file handle denies delete sharing on Windows: a program that still has the copy open.
    with (stage / 'config/tls/leaf.key').open('rb'):
        result = storage.remove_stage_config(home, stage)
        assert result == ('refused', total - len(stays)), (
            f'The removal with one file held open returned {result!r}; left {left(stage)}; '
            f'the boundary refuses {boundary_refusals(home, stage)}')
        # That file and its folders stay. The other files are gone.
        assert left(stage) == stays
        assert rest(stage) == kept and held(home) == before
    # The handle is closed: a later call removes the rest.
    result = storage.remove_stage_config(home, stage)
    assert result == (None, len(stays)), (
        f'The removal after the handle closed returned {result!r}; left {left(stage)}; '
        f'the boundary refuses {boundary_refusals(home, stage)}')
    assert gone(stage) and rest(stage) == kept and held(home) == before


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows private files')
def test_native_windows_stage_config_junction_is_refused_and_never_followed(native_stage):
    """A junction named ``config`` that points to the app's ``config``, and a junction inside a copy."""
    import _winapi
    home, stage, before = native_stage()
    shutil.rmtree(stage / 'config')
    junctions = [stage / 'config', stage / 'routing/junction']
    _winapi.CreateJunction(str(home / 'config'), str(junctions[0]))
    _winapi.CreateJunction(str(home / 'config/tls'), str(junctions[1]))
    try:
        assert all(os.path.isjunction(junction) for junction in junctions)
        assert (stage / 'config/auth/control-token').is_file()   # Through the junction: the app's own file.
        kept = rest(stage)
        # The file of the routing copy goes. Both junctions stay, and so does the folder of the second one.
        result = storage.remove_stage_config(home, stage)
        assert result == ('refused', 1), (
            f'The removal with two junctions returned {result!r}; left {left(stage)}; '
            f'the boundary refuses {boundary_refusals(home, stage)}')
        assert storage.remove_stage_config(home, stage) == ('refused', 0)
        assert all(os.path.isjunction(junction) for junction in junctions)
        assert not (stage / 'routing/custom.yaml').exists()
        # The app's own folders keep every file.
        assert held(home) == before and rest(stage) == kept
    finally:
        for junction in junctions:
            if os.path.lexists(junction):
                os.rmdir(junction)   # Removes the junction itself, not what it points to.
    # With no junction, the rest goes.
    assert storage.remove_stage_config(home, stage) == (None, 1)
    assert gone(stage) and held(home) == before


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows private files')
def test_native_windows_stage_config_folder_that_is_replaced_before_its_pin_is_refused(native_stage, monkeypatch):
    """Another folder takes the name of a folder of the copy between the walk's look and its pin.

    The boundary accepts the other folder: it is private too, and the last
    step of this case shows that. The walk compares the pinned folder with the
    folder that it looked at, and refuses.
    """
    from amplifier_web import windows_files as wf
    home, stage, before = native_stage()
    tls, first, other = stage / 'config' / 'tls', home / 'first-tls', home / 'other-tls'
    with ordinary_default_owner():
        shutil.copytree(home / 'config' / 'tls', other)
    total, real, replaced = copied(stage), wf.directory, []

    def replace_then_pin(path, **settings):
        if Path(path) == tls and not replaced:
            replaced.append(path)
            tls.rename(first)
            other.rename(tls)
        return real(path, **settings)

    with monkeypatch.context() as patch:
        patch.setattr(wf, 'directory', replace_then_pin)
        result = storage.remove_stage_config(home, stage)
    stays = ['config', 'config/tls', 'config/tls/ca.crt', 'config/tls/leaf.crt', 'config/tls/leaf.key']
    assert replaced and result == ('refused', total - len(stays)), (
        f'The removal with a replaced folder returned {result!r}; left {left(stage)}; '
        f'the boundary refuses {boundary_refusals(home, stage)}')
    # The folder that took the name is complete, and so is the folder that had it.
    assert left(stage) == stays
    assert sorted(path.name for path in first.iterdir()) == ['ca.crt', 'leaf.crt', 'leaf.key']
    assert held(home) == before
    # The folder is the stage's now: a later call removes it through the boundary.
    result = storage.remove_stage_config(home, stage)
    assert result == (None, len(stays)), (
        f'The removal of the folder that took the name returned {result!r}; left {left(stage)}; '
        f'the boundary refuses {boundary_refusals(home, stage)}')
    assert gone(stage) and held(home) == before
