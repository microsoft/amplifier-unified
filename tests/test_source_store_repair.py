"""Bytecode that an earlier app version wrote into a source store object is removed, and nothing else.

Seen on a Windows installation (0.106.3): the connection check of an "OpenAI
Compatible API" connection wrote ``__pycache__`` into an object of the shared
source store, and Foundation refused the object from then on. 0.106.6 stops
new bytecode (``tests/test_source_store_bytecode.py``). A PC that already has
such an object stays broken; ``amplifier_web.source_store_repair`` repairs it
at a host start. A deletion cannot be undone, so most cases here show what is
NOT deleted.

Every case uses Foundation's real store, a Git repository on this computer,
real Git and a real import that writes real bytecode. On Windows the cases
run the real native steps of ``windows_removal`` in a private folder of the
account. On another platform they run a stand-in for those steps over real
paths, and the fixture opens the folders that Foundation seals there, as
Windows leaves them. Nothing goes to a network. No case here skips on any
platform.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from amplifier_web import source_store_repair as repair
from test_source_store_bytecode import MODULE, PACKAGE, repository

NATIVE = os.name == 'nt'
OTHER_VERSION = 'another-app-version'
TAG = sys.implementation.cache_tag  # The part of a bytecode file's name that names this Python.


class Cut(BaseException):
    """The process ends in the middle of a run: the PC is switched off, or Windows ends the host.

    Not an Exception, as ``KeyboardInterrupt`` and ``SystemExit`` are not: the step lets it pass. A case that
    fails with it is reported as a failed case; a real ``KeyboardInterrupt`` would end the whole test run.
    """


class Steps:
    """The native steps of the walk over real paths, for a platform that does not have them.

    A handle is a number that stands for one path. A name is joined to the
    path of its parent's handle and never resolved: a symbolic link is a link
    and is never entered. ``held`` are paths that another program has open
    without sharing delete.
    """

    def __init__(self):
        self.handles, self.last, self.held, self.listed = {}, 0, set(), []

    def _new(self, path):
        self.last += 1
        self.handles[self.last] = Path(path)
        return self.last

    def root(self, path):
        if Path(path).is_symlink() or not Path(path).is_dir():
            raise NotADirectoryError(20, 'not a real folder')
        return self._new(path)

    def close(self, handle):
        del self.handles[handle]

    def children(self, handle):
        rows = []
        self.listed.append(self.handles[handle])
        for entry in os.scandir(self.handles[handle]):
            link = entry.is_symlink()
            folder = entry.is_dir() if link else entry.is_dir(follow_symlinks=False)
            rows.append((entry.name, (0x10 if folder else 0x20) | (0x400 if link else 0)))
        return rows

    def open(self, parent, name, *, enter, delete=True):
        if not name or name in {'.', '..'} or any(mark in name for mark in '\\/:'):
            raise OSError(22, 'not a single entry name')
        path = self.handles[parent] / name
        if not os.path.lexists(path):
            raise FileNotFoundError(2, 'gone')
        if delete and path in self.held:
            raise PermissionError(13, 'in use')
        return self._new(path)

    def kind(self, handle):
        path = self.handles[handle]
        return 'link' if path.is_symlink() else 'folder' if path.is_dir() else 'file'

    def delete(self, handle):
        path = self.handles[handle]
        if self.kind(handle) == 'folder':
            os.rmdir(path)  # A folder that holds something refuses.
        else:
            os.unlink(path)


@contextmanager
def _held(api, path):
    handle = api.root(path)
    try:
        yield handle
    finally:
        api.close(handle)


def _writable(path, *args):
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)


def _clear(folder, links):
    """Remove a folder of a case. Foundation sealed the files and folders of an object."""
    for link in links:
        try:
            os.rmdir(link) if NATIVE else os.unlink(link)
        except OSError:
            pass
    if not NATIVE:
        for parent, names, _ in os.walk(folder, followlinks=False):
            for name in [parent, *(os.path.join(parent, name) for name in names)]:
                if not os.path.islink(name):
                    os.chmod(name, 0o755)
    for attempt in range(20):  # A program of a case can still have a folder as its current folder.
        try:
            shutil.rmtree(folder, onexc=lambda function, path, error: (_writable(path), function(path)))
            return
        except OSError:
            time.sleep(0.5)


@pytest.fixture
def installation(tmp_path, monkeypatch):
    """One installation folder from the setup file: the receipt, the data folder ``app``, the store ``s``."""
    links, steps = [], None
    if NATIVE:
        from amplifier_web import windows_files
        root = windows_files.address(Path.home() / ('au-store-repair-' + uuid.uuid4().hex[:10]))
        for folder in (root, root / 'app', root / 's'):
            with windows_files.directory(folder, owned_root=root, create=True):
                pass
        outside = Path.home() / (root.name + '-outside')
        api = repair._steps()  # The real native steps of windows_removal.
    else:
        root, outside = tmp_path / 'installation', tmp_path / 'outside'
        api = steps = Steps()
        (root / 'app').mkdir(parents=True)
        (root / 's').mkdir()
        monkeypatch.setattr(repair, '_windows', lambda: True)
        monkeypatch.setattr(repair, '_held', _held)
    # One object with the steps for the whole case, so that a case can put itself between two steps.
    monkeypatch.setattr(repair, '_steps', lambda: api)
    (root / 'consumer-install.json').write_text('{}', encoding='utf-8')
    # The rest of the person's disk. Nothing in it may change.
    (outside / '__pycache__').mkdir(parents=True)
    (outside / '__pycache__' / 'precious.cpython-313.pyc').write_bytes(b'not Amplifier\'s')
    (outside / 'precious.txt').write_text('keep me', encoding='utf-8')
    # The environment of the host that the task started (windows_service, windows_consumer.environment).
    monkeypatch.setenv('AMPLIFIER_SOURCE_STORE', str(root / 's'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(root / 'app'))
    # The host has no bytecode setting. A job or a shell that has one must not hide a fact.
    monkeypatch.delenv('PYTHONDONTWRITEBYTECODE', raising=False)
    lines = []

    def start():
        """The host that the start-at-sign-in task started calls this before it takes its own lock."""
        return repair.at_host_start(root / 'app', log=lines.append)

    def link(target, path):
        """A link to a folder: a junction on Windows, a symbolic link elsewhere."""
        if NATIVE:
            import _winapi
            _winapi.CreateJunction(str(target), str(path))
        else:
            os.symlink(target, path, target_is_directory=True)
        links.append(path)

    try:
        yield SimpleNamespace(root=root, data=root / 'app', store=root / 's', outside=outside, steps=steps,
                              api=api, start=start, link=link, lines=lines,
                              source=repository(tmp_path / 'repository'))
    finally:
        # Foundation sealed the objects. Remove the folders of this case here, so that pytest need not.
        _clear(root, links)
        _clear(outside, [])


def snapshot(*folders):
    """Every entry below these folders: its kind, and a file's content or a link's target. No link is entered."""
    rows = {}

    def below(folder, prefix):
        with os.scandir(folder) as entries:
            for entry in sorted(entries, key=lambda entry: entry.name):
                key = prefix + '/' + entry.name
                if entry.is_symlink() or entry.is_junction():
                    rows[key] = ('link', os.readlink(entry.path))
                elif entry.is_dir(follow_symlinks=False):
                    rows[key] = ('folder',)
                    below(entry.path, key)
                else:
                    rows[key] = ('file', hashlib.sha256(Path(entry.path).read_bytes()).hexdigest())
    for top in folders:
        below(top, top.name)
    return rows


def bytecode(folder):
    return sorted(path.relative_to(folder).as_posix() for path in folder.rglob('*')
                  if path.name == '__pycache__' or path.suffix == '.pyc')


async def publish(installation, source=None):
    """Foundation publishes the module's source as an object of the store, as a host of the installation does."""
    from amplifier_foundation.sources.shared import SharedSourceStore
    shared = SharedSourceStore(installation.store)
    cache, source = installation.data / 'foundation' / 'cache', source or installation.source
    await shared.resolve(source, cache)
    record, = (value for value in (json.loads(path.read_text(encoding='utf-8'))
                                   for path in (cache / '.source-bindings').glob('*.json'))
               if value['git_url'] in source)
    target = shared.checkout(record['git_url'], record['commit'])
    if not NATIVE:
        # Foundation takes the write permission from an object's folders, and POSIX then refuses a new entry.
        # A read-only attribute on a Windows folder does not. This gives the folders in the object the state
        # of Windows. The object's own folder stays sealed: Foundation reads that as "published" on POSIX.
        for folder in (path for path in target.rglob('*') if path.is_dir() and not path.is_symlink()):
            folder.chmod(0o755)

    def verify():
        try:
            shared.verify(record['git_url'], record['commit'])
            return 'accepted'
        except ValueError as error:
            return f'refused: {error}'
    return SimpleNamespace(folder=target, verify=verify, cache=cache,
                           key=target.parent.name + '/' + target.name)


def an_earlier_helper_imports(folder):
    """What the connection check of 0.106.5 did: a Python process imports the module from the object."""
    environment = {name: value for name, value in os.environ.items() if name != 'PYTHONDONTWRITEBYTECODE'}
    subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, sys.argv[1]); import ' + PACKAGE,
                    str(folder)], check=True, env=environment, capture_output=True)
    return bytecode(folder)


async def broken(installation, source=None):
    target = await publish(installation, source)
    written = an_earlier_helper_imports(target.folder)
    assert written == [PACKAGE + '/__pycache__', *sorted(
        PACKAGE + '/__pycache__/' + path.name for path in (target.folder / PACKAGE / '__pycache__').iterdir())]
    assert len(written) == 3 and target.verify() == 'refused: Shared source contents changed'
    return target


async def prepared_as_a_worker_does(target, source, monkeypatch):
    """The next preparation of the module, with the worker's rule for a provider that fails."""
    from amplifier_foundation import Bundle
    from amplifier_web.provider_isolation import ProviderIsolation
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(sys, 'dont_write_bytecode', True)  # A worker writes no bytecode.
    isolation = ProviderIsolation()
    provider = {'module': MODULE, 'source': source, 'config': {}}
    await Bundle.from_dict({'bundle': {'name': 'worker'}, 'providers': [provider]}).prepare(
        strict=True, install_deps=False, cache_dir=target.cache,
        provider_failure_policy=isolation.preparation_failed)
    failure = isolation.failure(provider)
    return failure and failure.get('reason_code')


def record(installation):
    path = installation.data / 'updates' / repair.RECORD
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def the_updates_folder(installation):
    """The folder of the record, made as the app makes it (UpdateManager.__init__).

    On Windows the app makes it as a private folder of the account. A hosted runner's account is elevated: a
    folder from an ordinary mkdir would belong to the Administrators group, and the app's private writer
    refuses such a folder.
    """
    folder = installation.data / 'updates'
    if NATIVE:
        from amplifier_web import windows_files
        with windows_files.directory(folder, owned_root=installation.data, create=True):
            pass
    else:
        folder.mkdir(exist_ok=True)
    return folder


def mark(installation):
    """The change mark of the record: None, or 32 hexadecimal digits that a run made."""
    value = repair.change(installation.data / 'updates')
    assert value is None or re.fullmatch('[0-9a-f]{32}', value)
    return value


def facts(result, *names):
    """Counts of a run. The change mark is random, so a case reads only whether there is one."""
    return tuple(result[name] is not None if name == 'change' else result[name] for name in names)


async def test_bytecode_from_an_earlier_version_goes_and_foundation_accepts_the_object_again(
        installation, monkeypatch):
    from amplifier_web import __version__
    target = await broken(installation)
    refused = await prepared_as_a_worker_does(target, installation.source, monkeypatch)
    before = snapshot(installation.store, installation.outside)

    result = installation.start()

    after = snapshot(installation.store, installation.outside)
    gone = sorted(set(before) - set(after))
    assert {
        'a worker, before': refused,
        'what is gone': [name.split(target.folder.name + '/', 1)[1] for name in gone],
        'what is new or changed': {name for name in after if before.get(name) != after[name]},
        'Foundation\'s own file is there': (target.folder / repair.META).is_file(),
        'Foundation, asked for the object again': target.verify(),
        'a worker, after': await prepared_as_a_worker_does(target, installation.source, monkeypatch),
        'the record': {**record(installation), 'change': mark(installation) is not None,
                       'last': {**record(installation)['last'], 'ms': 0}},
        'counts': {name: result[name] for name in ('objects', 'with_cache', 'repaired', 'files', 'folders',
                                                    'left', 'later', 'git', 'complete')},
        'the run gives the mark of the record': result['change'] == mark(installation),
    } == {
        'a worker, before': 'provider_preparation_failed',
        'what is gone': sorted([PACKAGE + '/__pycache__', PACKAGE + '/__pycache__/__init__.' + TAG + '.pyc',
                                PACKAGE + '/__pycache__/_listing.' + TAG + '.pyc']),
        'what is new or changed': set(),
        'Foundation\'s own file is there': True,
        'Foundation, asked for the object again': 'accepted',
        'a worker, after': None,
        # No path of a file and no time of day. The counts of the last run are the cost on this PC.
        'the record': {'schema': 1, 'version': __version__, 'complete': True, 'change': True, 'runs': 1,
                       'tries': {}, 'next': None,
                       'last': {'objects': 1, 'with_cache': 1, 'repaired': 1, 'files': 2, 'folders': 1, 'left': 0,
                                'later': 0, 'listings': result['listings'], 'entries': result['entries'],
                                'git': 2, 'ms': 0}},
        'counts': {'objects': 1, 'with_cache': 1, 'repaired': 1, 'files': 2, 'folders': 1, 'left': 0,
                   'later': 0, 'git': 2, 'complete': True},
        'the run gives the mark of the record': True,
    }
    assert installation.lines[0].endswith(f" git=2 ms={record(installation)['last']['ms']}.")
    assert len(installation.lines) == 1 and installation.lines[0].startswith(
        'Source store repair: complete=true objects=1 with_cache=1 repaired=1 files=2 folders=1 left=0 later=0 ')


@contextmanager
def at_the_top(target):
    """A new entry directly in the object. POSIX needs the object's own folder open for that moment."""
    if not NATIVE:
        target.folder.chmod(0o755)
    try:
        yield target.folder
    finally:
        if not NATIVE:
            target.folder.chmod(0o555)


def _another_file_at_the_top(target, installation):
    with at_the_top(target) as top:
        (top / 'notes.txt').write_text('a person\'s file', encoding='utf-8')


def _bytecode_outside_a_cache_folder(target, installation):
    (target.folder / PACKAGE / 'stray.pyc').write_bytes(b'x')


def _another_file_in_the_cache_folder(target, installation):
    (target.folder / PACKAGE / '__pycache__' / 'notes.txt').write_text('x', encoding='utf-8')


def _a_folder_in_the_cache_folder(target, installation):
    (target.folder / PACKAGE / '__pycache__' / 'inner').mkdir()
    (target.folder / PACKAGE / '__pycache__' / 'inner' / 'deep.pyc').write_bytes(b'x')


def _a_changed_tracked_file(target, installation):
    path = target.folder / PACKAGE / '_listing.py'
    _writable(path)
    path.write_text(path.read_text(encoding='utf-8') + '\n# changed\n', encoding='utf-8')


def _a_tracked_file_is_gone(target, installation):
    with at_the_top(target) as top:
        _writable(top / 'setup.cfg')
        (top / 'setup.cfg').unlink()


def _a_git_folder_that_is_a_file(target, installation):
    """A linked worktree: Foundation refuses it too."""
    with at_the_top(target) as top:
        (top / 'aside').mkdir()
        if not NATIVE:
            (top / '.git').chmod(0o755)
        os.rename(top / '.git', top / 'aside' / 'moved')
        (top / '.git').write_text('gitdir: aside/moved\n', encoding='utf-8')


@pytest.mark.parametrize('change, git', [
    (_another_file_at_the_top, 2), (_bytecode_outside_a_cache_folder, 2), (_another_file_in_the_cache_folder, 2),
    (_a_folder_in_the_cache_folder, 2), (_a_changed_tracked_file, 1), (_a_tracked_file_is_gone, 1),
    (_a_git_folder_that_is_a_file, 0)], ids=lambda value: value.__name__.strip('_') if callable(value) else None)
async def test_an_object_with_anything_else_changed_is_left_exactly_as_it_is(installation, change, git):
    from amplifier_foundation.sources.shared import clean_checkout
    target = await broken(installation)
    change(target, installation)
    before = snapshot(installation.store, installation.outside)

    result = installation.start()

    assert {
        'the store and the rest of the disk': snapshot(installation.store, installation.outside) == before,
        'Git commands': result['git'],
        'counts': {name: result[name] for name in ('with_cache', 'repaired', 'files', 'folders', 'left', 'change')},
        'a change mark': mark(installation),
        'tries': record(installation)['tries'],
        'complete': result['complete'],
        'Foundation finds the object clean': (target.folder / '.git').is_dir() and clean_checkout(target.folder),
    } == {
        'the store and the rest of the disk': True,
        'Git commands': git,  # A changed tracked file is found by the first command.
        'counts': {'with_cache': 1, 'repaired': 0, 'files': 0, 'folders': 0, 'left': 1, 'change': None},
        'a change mark': None,
        'tries': {target.key: repair.TRIES},  # Not tried again in this app version.
        'complete': True,
        'Foundation finds the object clean': False,
    }


def _a_link_named_like_a_cache_folder(target, installation):
    with at_the_top(target) as top:
        installation.link(installation.outside / '__pycache__', top / '__pycache__')


def _a_link_in_a_cache_folder(target, installation):
    installation.link(installation.outside, target.folder / PACKAGE / '__pycache__' / 'inner')


def _a_link_on_the_way(target, installation):
    with at_the_top(target) as top:
        installation.link(installation.outside, top / 'aside')


def _a_link_below_the_git_folder(target, installation):
    if not NATIVE:
        (target.folder / '.git').chmod(0o755)
    installation.link(installation.outside, target.folder / '.git' / 'aside')


@pytest.mark.parametrize('change', [
    _a_link_named_like_a_cache_folder, _a_link_in_a_cache_folder, _a_link_on_the_way, _a_link_below_the_git_folder],
    ids=lambda value: value.__name__.strip('_'))
async def test_a_link_anywhere_in_the_object_leaves_it_and_git_does_not_run(installation, change):
    target = await broken(installation)
    change(target, installation)
    before = snapshot(installation.store, installation.outside)

    result = installation.start()

    assert {
        'the store and the rest of the disk': snapshot(installation.store, installation.outside) == before,
        'the folder behind the link was listed': installation.steps and any(
            installation.outside in (path, *path.parents) for path in installation.steps.listed),
        'Git commands': result['git'],
        'counts': {name: result[name] for name in ('with_cache', 'repaired', 'files', 'folders', 'left', 'change')},
        'a change mark': mark(installation),
        'tries': record(installation)['tries'],
    } == {
        'the store and the rest of the disk': True,
        'the folder behind the link was listed': None if NATIVE else False,
        'Git commands': 0,
        'counts': {'with_cache': 1, 'repaired': 0, 'files': 0, 'folders': 0, 'left': 1, 'change': None},
        'a change mark': None,
        'tries': {target.key: repair.TRIES},
    }


async def test_a_cache_folder_with_no_bytecode_costs_two_git_commands_and_loses_nothing(installation):
    """Also an empty one stays: this run took no file from it."""
    target = await publish(installation)
    (target.folder / PACKAGE / '__pycache__').mkdir()
    before = snapshot(installation.store)
    result = installation.start()
    assert (snapshot(installation.store) == before, result['git'], result['with_cache'], result['repaired'],
            result['change'], result['complete'], record(installation)['tries']) == (True, 2, 1, 0, None, True, {})


async def test_a_store_with_no_cache_folder_costs_no_git_and_a_later_start_reads_only_the_record(
        installation, monkeypatch):
    target = await publish(installation)
    before = snapshot(installation.store)
    first = installation.start()
    assert (first['git'], first['with_cache'], first['complete'], first['change']) == (0, 0, True, None)
    # The folders of the store that were listed: objects, the source, the object and its one package.
    # The object's .git is not listed when the object has no cache folder.
    assert first['listings'] == 4
    assert snapshot(installation.store) == before and target.verify() == 'accepted'

    # The same app version again: no lock, no listing, no line in the log.
    monkeypatch.setattr(repair, '_steps', lambda: pytest.fail('the store was looked at again'))
    monkeypatch.setattr('amplifier_web.reset.repair_lock', lambda *a, **k: pytest.fail('the lock was taken'))
    saved = record(installation)
    assert installation.start() is None and record(installation) == saved and len(installation.lines) == 1


async def test_a_new_app_version_looks_again(installation, monkeypatch):
    target = await publish(installation)
    assert installation.start()['complete']
    an_earlier_helper_imports(target.folder)  # For example the app went back to an earlier version for a time.
    assert installation.start() is None and target.verify().startswith('refused')
    monkeypatch.setattr('amplifier_web.__version__', OTHER_VERSION)
    result = installation.start()
    assert (result['repaired'], result['files'], target.verify()) == (1, 2, 'accepted')
    saved = record(installation)
    assert {**saved, 'last': None} == {'schema': 1, 'version': OTHER_VERSION, 'complete': True,
                                       'change': mark(installation), 'runs': 1, 'tries': {}, 'next': None,
                                       'last': None} and mark(installation) is not None


def _not_windows(installation, monkeypatch):
    monkeypatch.setattr(repair, '_windows', lambda: False)


def _no_store_name(installation, monkeypatch):
    monkeypatch.delenv('AMPLIFIER_SOURCE_STORE')


def _a_store_elsewhere(installation, monkeypatch):
    """The host names a store that is not the installation's own."""
    elsewhere = installation.root / 'elsewhere'
    os.rename(installation.store, elsewhere)
    installation.store.mkdir()
    monkeypatch.setenv('AMPLIFIER_SOURCE_STORE', str(elsewhere))


def _no_receipt_of_setup(installation, monkeypatch):
    (installation.root / 'consumer-install.json').unlink()


@pytest.mark.parametrize('state', [_not_windows, _no_store_name, _a_store_elsewhere, _no_receipt_of_setup],
                         ids=lambda value: value.__name__.strip('_'))
async def test_outside_an_installation_from_the_setup_file_nothing_runs_and_nothing_is_written(
        installation, monkeypatch, state):
    target = await broken(installation)
    state(installation, monkeypatch)
    monkeypatch.setattr(repair, '_steps', lambda: pytest.fail('the store was looked at'))
    before = snapshot(installation.root, installation.outside)
    assert installation.start() is None
    assert snapshot(installation.root, installation.outside) == before and not installation.lines
    assert record(installation) is None and len(bytecode(installation.root)) == 3 and target


HOLDER = '''
import sys, time
from pathlib import Path
from amplifier_web.reset import repair_lock
with repair_lock(Path(sys.argv[1]), shared=True):
    print('held', flush=True)
    sys.stdin.read()
'''


async def test_while_another_host_serves_this_data_folder_nothing_runs(installation):
    """Every host holds the data folder's lock shared while it serves. The repair asks for it alone, without a wait."""
    target = await broken(installation)
    host = subprocess.Popen([sys.executable, '-c', HOLDER, str(installation.data)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, cwd=Path(__file__).resolve().parents[1])
    try:
        assert host.stdout.readline().strip() == 'held'
        before = snapshot(installation.store)
        started = time.monotonic()
        assert installation.start() is None
        assert time.monotonic() - started < 5 and snapshot(installation.store) == before
        assert record(installation) is None and not installation.lines
    finally:
        host.stdin.close()
        host.wait(30)
    # The other host is gone: the next start repairs.
    assert installation.start()['repaired'] == 1 and target.verify() == 'accepted'


def test_only_the_host_that_the_task_started_runs_it_and_before_its_own_lock(tmp_path, monkeypatch):
    from amplifier_web import cli
    order = []

    @contextmanager
    def lock(home, *, shared=False):
        order.append('the lock, shared' if shared else 'the lock, alone')
        yield
    monkeypatch.setattr('amplifier_web.reset.repair_lock', lock)
    monkeypatch.setattr(repair, 'at_host_start', lambda data: order.append('the repair'))
    monkeypatch.setattr(cli, '_serve_unlocked', lambda *a, **k: order.append('the host serves'))
    cli._serve(SimpleNamespace(), tmp_path, task_runner=object())
    assert order == ['the repair', 'the lock, shared', 'the host serves']
    del order[:]
    cli._serve(SimpleNamespace(), tmp_path)  # A host that a person started by hand.
    assert order == ['the lock, shared', 'the host serves']


@contextmanager
def in_use(installation, path):
    """Another program has the file open, as Python has a .pyc that it reads."""
    if NATIVE:
        with open(path, 'rb'):
            yield
    else:
        installation.steps.held.add(path)
        try:
            yield
        finally:
            installation.steps.held.discard(path)


async def test_a_file_in_use_stays_and_the_object_gets_three_tries(installation, monkeypatch):
    target = await broken(installation)
    busy = target.folder / PACKAGE / '__pycache__' / ('_listing.' + TAG + '.pyc')
    with in_use(installation, busy):
        seen, marks = [], [None]
        for _ in range(repair.TRIES):
            result = installation.start()
            seen.append((result['files'], result['folders'], result['later'], result['complete'],
                         record(installation)['tries'], bytecode(target.folder)))
            marks.append(mark(installation))
        # The other file went in the first run. The file in use and its folder stay; nothing raises.
        left = [PACKAGE + '/__pycache__', PACKAGE + '/__pycache__/' + busy.name]
        assert seen == [(1, 0, 1, False, {target.key: 1}, left), (0, 0, 1, False, {target.key: 2}, left),
                        (0, 0, 1, True, {target.key: 3}, left)]
        # After three tries the object counts as left, and a start reads only the record. Each run that
        # found a file to delete wrote a new mark before it tried.
        assert installation.start() is None and mark(installation) == marks[-1]
    monkeypatch.setattr('amplifier_web.__version__', OTHER_VERSION)
    result = installation.start()
    assert (result['files'], result['folders'], bytecode(target.folder), target.verify()) == (1, 1, [], 'accepted')
    marks.append(mark(installation))
    assert len(set(marks)) == repair.TRIES + 2 and marks[0] is None  # Five values, no two equal.


async def test_a_git_that_cannot_run_leaves_the_object_for_a_later_start(installation, monkeypatch):
    target = await broken(installation)
    before = snapshot(installation.store)
    with monkeypatch.context() as patch:
        patch.setenv('PATH', str(installation.outside))  # No Git there.
        for count in range(1, repair.TRIES + 1):
            result = installation.start()
            assert (result['git'], result['later'], result['complete'], record(installation)['tries']) == (
                0, 1, count == repair.TRIES, {target.key: count})
        assert snapshot(installation.store) == before
    assert installation.start() is None  # Three tries are over for this app version.


async def test_a_git_that_ends_with_an_error_is_not_an_empty_answer(installation):
    target = await broken(installation)
    index = target.folder / '.git' / 'index'
    if not NATIVE:
        index.parent.chmod(0o755)
    _writable(index)
    index.write_bytes(b'not an index')
    before = snapshot(installation.store)
    result = installation.start()
    assert (result['git'], result['later'], result['repaired'], result['change'], result['complete']) == (
        1, 1, 0, None, False)
    assert snapshot(installation.store) == before and record(installation)['tries'] == {target.key: 1}


async def test_an_object_that_was_left_for_good_is_not_looked_at_again_while_the_pass_is_open(
        installation, tmp_path):
    """Two objects: one has a person's file, one has a file in use. The second start costs only the second."""
    left = await broken(installation)
    later = await broken(installation, repository(tmp_path / 'another-repository'))
    assert left.key != later.key
    _another_file_at_the_top(left, installation)
    with in_use(installation, later.folder / PACKAGE / '__pycache__' / ('_listing.' + TAG + '.pyc')):
        first = installation.start()
        second = installation.start()
    assert {name: first[name] for name in ('objects', 'with_cache', 'left', 'later', 'git', 'complete')} == {
        'objects': 2, 'with_cache': 2, 'left': 1, 'later': 1, 'git': 4, 'complete': False}
    assert {name: second[name] for name in ('objects', 'with_cache', 'left', 'later', 'git', 'complete')} == {
        'objects': 2, 'with_cache': 1, 'left': 0, 'later': 1, 'git': 2, 'complete': False}
    assert record(installation)['tries'] == {left.key: repair.TRIES, later.key: 2}
    assert len(bytecode(left.folder)) == 3


async def test_one_object_that_this_code_cannot_handle_does_not_stop_the_others(installation, monkeypatch, tmp_path):
    one = await broken(installation)
    two = await broken(installation, repository(tmp_path / 'another-repository'))
    odd, good = sorted((one, two), key=lambda target: target.key)
    real = repair._object

    def object_(api, parent, name, folder, *rest):
        if Path(folder).parent.name == odd.folder.parent.name:  # The two commits can have one name.
            raise RuntimeError('something that this code did not expect')
        return real(api, parent, name, folder, *rest)
    monkeypatch.setattr(repair, '_object', object_)
    result = installation.start()
    assert (result['repaired'], result['later'], record(installation)['tries']) == (1, 1, {odd.key: 1})
    assert good.verify() == 'accepted' and len(bytecode(odd.folder)) == 3


def test_the_listing_notes_only_the_plain_files_of_a_cache_folder_and_never_enters_a_link(installation):
    tree = installation.store / 'tree'
    (tree / 'pkg' / '__pycache__' / 'folder.pyc').mkdir(parents=True)
    (tree / 'pkg' / '__pycache__' / 'a.pyc').write_bytes(b'x')
    (tree / 'pkg' / '__pycache__' / 'notes.txt').write_bytes(b'x')
    (tree / 'pkg' / 'plain').mkdir()
    (tree / '.git' / '__pycache__').mkdir(parents=True)
    (tree / '__pycache__').mkdir()
    api = repair._steps()

    def walk():
        with repair._held(api, str(installation.store)) as top:
            with repair._entry(api, top, 'tree') as handle:
                return repair._walk(api, handle, repair._Budget(time.monotonic))
    # The folder with the name of a bytecode file is not a file. The cache folder below .git is not listed.
    assert walk() == ({('pkg', '__pycache__'): {'a.pyc', 'notes.txt'}, ('__pycache__',): set()},
                      False, sorted_as_listed(tree))
    installation.link(installation.outside, tree / 'pkg' / '__pycache__' / 'link.pyc')
    caches, link, _ = walk()
    assert link is True and caches[('pkg', '__pycache__')] == {'a.pyc', 'notes.txt'}
    if installation.steps:
        assert not any(installation.outside in (path, *path.parents) for path in installation.steps.listed)


def sorted_as_listed(tree):
    """The real folders at the top of a tree, in the order of the listing. .git is one of them."""
    api = repair._steps()
    with repair._held(api, str(tree.parent)) as top:
        with repair._entry(api, top, tree.name) as handle:
            return [name for name, attributes in api.children(handle) if attributes & 0x10 and not attributes & 0x400]


SLOW = 6  # Seconds that the program which stands for Git keeps running.


def a_program_that_does_not_end(folder):
    """It starts another program that holds its output open, as Git's own program on Windows can."""
    if NATIVE:
        path = folder / 'slow.cmd'
        path.write_text(f'@ping -n {SLOW + 1} 127.0.0.1\r\n', encoding='ascii')
    else:
        path = folder / 'slow'
        path.write_text(f'#!/bin/sh\nsleep {SLOW}\n', encoding='ascii')
        path.chmod(0o755)
    return str(path)


async def test_a_git_that_does_not_end_is_ended_at_the_limit_and_the_object_stays(installation, monkeypatch, tmp_path):
    target = await broken(installation)
    before = snapshot(installation.store)
    program = a_program_that_does_not_end(tmp_path)
    monkeypatch.setattr(repair, '_program', lambda: program)
    monkeypatch.setattr(repair, 'GIT_SECONDS', 0.5)
    started, real = [], subprocess.Popen

    def popen(*arguments, **options):
        started.append(real(*arguments, **options))
        return started[-1]
    monkeypatch.setattr(repair.subprocess, 'Popen', popen)
    begin = time.monotonic()
    try:
        result = installation.start()
        seconds = time.monotonic() - begin
        # The program that the step started has ended when the step returns: the step ended it.
        assert len(started) == 1 and started[0].poll() is not None, 'the program still runs'
        assert seconds < SLOW - 1, f'the step took {seconds:.1f} s'
        assert (result['git'], result['later'], result['complete'], result['change']) == (1, 1, False, None)
        assert snapshot(installation.store) == before and record(installation)['tries'] == {target.key: 1}
    finally:
        time.sleep(max(0, begin + SLOW + 2 - time.monotonic()))  # Its child has the object as its folder.


def test_the_bounds_of_listings_and_of_entries_stop_a_run(monkeypatch):
    rows = [('name', 0x20)] * 3
    api = SimpleNamespace(children=lambda handle: rows)
    monkeypatch.setattr(repair, 'LISTINGS', 2)
    budget = repair._Budget(lambda: 0.0)
    assert budget.children(api, 1) == rows and budget.children(api, 1) == rows
    with pytest.raises(repair._Bound):
        budget.children(api, 1)
    monkeypatch.setattr(repair, 'ENTRIES', 5)
    budget = repair._Budget(lambda: 0.0)
    assert budget.children(api, 1) == rows
    with pytest.raises(repair._Bound):
        budget.children(api, 1)  # Six entries.


def test_the_limit_of_the_whole_step_is_also_the_limit_of_git():
    now = [100.0]
    budget = repair._Budget(lambda: now[0])
    assert budget.for_git() == repair.GIT_SECONDS
    now[0] += repair.TOTAL_SECONDS - 2
    assert budget.for_git() == pytest.approx(2)
    now[0] += 2
    with pytest.raises(repair._Bound):
        budget.for_git()
    with pytest.raises(repair._Bound):
        budget.children(SimpleNamespace(children=lambda handle: pytest.fail('listed after the limit')), 1)


def test_time_in_git_does_not_count_as_search():
    now = [0.0]
    budget = repair._Budget(lambda: now[0])
    with budget.aside():
        now[0] += repair.SEARCH_SECONDS + 1
    api = SimpleNamespace(children=lambda handle: [('one', 0x20)])
    assert budget.children(api, 1) == [('one', 0x20)]
    now[0] += repair.SEARCH_SECONDS + 0.5
    with pytest.raises(repair._Bound):
        budget.children(api, 1)


def plain_objects(installation, count, folders, source='0123456789abcdef'):
    """Objects with no cache folder. Only their names and folders matter to the search."""
    keys = []
    for number in range(count):
        commit = f'{number:040x}'
        for folder in range(folders):
            (installation.store / 'objects' / source / commit / f'folder-{folder:03d}').mkdir(parents=True)
        keys.append(source + '/' + commit)
    return keys


def test_a_bound_stops_the_run_and_the_next_start_goes_on_at_the_named_object(installation, monkeypatch):
    keys = plain_objects(installation, 6, 3)  # One object costs 4 listings; the two folders above cost 2.
    monkeypatch.setattr(repair, 'LISTINGS', 10)
    seen = []
    for _ in range(3):
        result = installation.start()
        seen.append((result['complete'], record(installation)['next'], record(installation)['tries']))
    assert seen == [(False, keys[2], {}), (False, keys[4], {}), (True, None, {})]
    assert installation.lines[0].endswith(' A bound was reached.') and installation.start() is None


def test_an_object_that_alone_passes_a_bound_gets_one_try_and_the_next_start_goes_on_after_it(
        installation, monkeypatch):
    large, small = plain_objects(installation, 2, 3)
    for folder in range(20):
        (installation.store / 'objects' / large / f'more-{folder:02d}').mkdir()
    monkeypatch.setattr(repair, 'LISTINGS', 10)
    seen = []
    for _ in range(2 * repair.TRIES):
        result = installation.start()
        seen.append((result['complete'], record(installation)['next'], record(installation)['tries'].get(large)))
    assert seen == [(False, small, 1), (False, None, 1), (False, small, 2), (False, None, 2),
                    (False, small, 3), (True, None, 3)]
    assert installation.start() is None  # One large object cannot hold the pass at itself.


async def test_a_store_folder_that_is_refused_ends_after_ten_starts(installation, monkeypatch):
    target = await broken(installation)
    before = snapshot(installation.store)

    @contextmanager
    def refused(api, path):
        raise PermissionError(13, 'another account has access')
        yield
    monkeypatch.setattr(repair, '_held', refused)
    for count in range(1, repair.RUNS + 1):
        assert installation.start() == {
            'objects': 0, 'with_cache': 0, 'repaired': 0, 'files': 0, 'folders': 0, 'left': 0, 'later': 0,
            'listings': 0, 'entries': 0, 'git': 0, 'ms': record(installation)['last']['ms'],
            'complete': count == repair.RUNS, 'change': None}
    assert ' Stopped (PermissionError); nothing more was changed.' in installation.lines[0]
    assert installation.start() is None and snapshot(installation.store) == before and target


async def test_a_record_that_cannot_be_written_means_that_the_store_is_not_looked_at(installation, monkeypatch):
    """The record is written first in a run. Without it there is no count of runs and no change mark."""
    target = await broken(installation)
    (installation.data / 'updates').write_text('a file where the folder of the record must be', encoding='utf-8')
    monkeypatch.setattr(repair, '_held', lambda *a: pytest.fail('the store was looked at'))
    before = snapshot(installation.store)
    for _ in range(3):
        assert installation.start() is None
    assert snapshot(installation.store) == before and len(bytecode(target.folder)) == 3
    assert len(installation.lines) == 3 and installation.lines[0].startswith(
        'Source store repair: the record could not be written (') and installation.lines[0].endswith(
        '); the store was not looked at.')


async def test_a_record_that_cannot_be_written_before_a_deletion_means_no_deletion(installation, monkeypatch):
    """The first write of the run worked; the write of the change mark, before the first deletion, does not."""
    target = await broken(installation)
    real, writes = repair._save, []

    def save(data_dir, value):
        writes.append(value['change'])
        if value['change'] is not None:
            raise PermissionError(13, 'the record is refused')
        real(data_dir, value)
    monkeypatch.setattr(repair, '_save', save)
    before = snapshot(installation.store)
    result = installation.start()
    assert snapshot(installation.store) == before and len(bytecode(target.folder)) == 3
    assert writes[0] is None and writes[1] is not None and writes[2] is None  # The mark was taken back.
    assert facts(result, 'files', 'folders', 'repaired', 'change', 'complete') == (0, 0, 0, False, False)
    assert ' The record could not be written, so nothing was deleted.' in installation.lines[0]
    assert mark(installation) is None and target.verify().startswith('refused')


async def test_runs_that_are_cut_before_their_end_also_end_after_ten(installation, monkeypatch):
    """For example the PC is switched off during each run. The count of runs is written first."""
    target = await broken(installation)

    def cut(*arguments):
        raise Cut
    monkeypatch.setattr(repair, '_object', cut)
    for count in range(1, repair.RUNS + 1):
        with pytest.raises(Cut):
            installation.start()
        assert (record(installation)['runs'], record(installation)['complete']) == (count, False)
    assert installation.start() is None and record(installation)['complete'] is True
    assert installation.lines[-1] == (
        f'Source store repair: {repair.RUNS} runs for this app version did not complete; it does not look again.')
    assert installation.start() is None and len(bytecode(target.folder)) == 3


@pytest.mark.parametrize('slow', [1, 2])
async def test_the_limit_of_the_whole_step_also_stops_a_deletion(installation, slow):
    """The deletion checks the limit before each file and before it lists the folder a last time.

    What is deleted stays deleted. A later start takes the rest.
    """
    target = await broken(installation)
    now, real, deleted = [0.0], installation.api.delete, []

    def delete(handle):
        real(handle)
        deleted.append(1)
        if len(deleted) == slow:
            now[0] += repair.TOTAL_SECONDS  # This deletion takes as long as the whole step may.
    installation.api.delete = delete
    result = repair.at_host_start(installation.data, clock=lambda: now[0], log=installation.lines.append)
    # After the slow deletion nothing more is deleted: not the second file, and not the emptied folder.
    assert len(deleted) == slow and len(bytecode(target.folder)) == 3 - slow
    assert facts(result, 'files', 'folders', 'repaired', 'complete', 'change') == (slow, 0, 0, False, True)
    assert installation.lines[0].endswith(' A bound was reached.') and record(installation)['tries'] == {target.key: 1}
    installation.api.delete = real
    result = installation.start()
    # A cache folder that an earlier run emptied stays: this run took no file from it. Git lists no empty folder.
    assert facts(result, 'files', 'folders', 'repaired', 'complete') == ((1, 1, 1, True), (0, 0, 0, True))[slow - 1]
    assert target.verify() == 'accepted'


@pytest.mark.parametrize('slow, tried', [
    # The folder above the first cache folder: its open fails, and the next cache folder is not begun.
    ('a-first', [('a-first', False)]),
    # The first cache folder itself: it is not tried again without the right to delete.
    ('__pycache__', [('a-first', False), ('__pycache__', True)])])
async def test_a_folder_open_that_fails_slowly_in_a_deletion_is_not_followed_by_more_work(
        installation, monkeypatch, slow, tried):
    """Two cache folders in one object. One open for the first fails and takes as long as the whole step may."""
    target = await broken(installation)
    with at_the_top(target) as top:  # An untracked folder with a cache folder. Its name sorts before the package.
        (top / 'a-first' / '__pycache__').mkdir(parents=True)
        (top / 'a-first' / '__pycache__' / ('first.' + TAG + '.pyc')).write_bytes(b'x')
    now, real, opened = [0.0], installation.api.open, []

    def open_(parent, name, *, enter, delete=True):
        opened.append((name, delete))
        if name == slow:
            now[0] += repair.TOTAL_SECONDS
            raise PermissionError(13, 'another program holds this folder')
        return real(parent, name, enter=enter, delete=delete)
    real_remove = repair._remove

    def from_the_deletion_on():
        installation.api.open = open_
    between_git_and_the_removal(monkeypatch, from_the_deletion_on)
    try:
        result = repair.at_host_start(installation.data, clock=lambda: now[0], log=installation.lines.append)
    finally:
        installation.api.open = real
        monkeypatch.setattr(repair, '_remove', real_remove)
    assert opened == tried  # Nothing after the slow open: not the package's folder either.
    assert facts(result, 'files', 'folders', 'repaired', 'complete') == (0, 0, 0, False)
    assert installation.lines[0].endswith(' A bound was reached.') and len(bytecode(target.folder)) == 5
    assert facts(installation.start(), 'files', 'folders', 'repaired', 'complete') == (3, 2, 1, True)
    assert target.verify() == 'accepted'


@contextmanager
def a_program_has_the_folder_as_its_current_folder(installation, folder):
    """Then Windows lets no program delete or rename the folder. Its files can still be deleted."""
    if not NATIVE:
        installation.steps.held.add(folder)
        try:
            yield
        finally:
            installation.steps.held.discard(folder)
        return
    program = subprocess.Popen([sys.executable, '-c', 'import sys; print("there", flush=True); sys.stdin.read()'],
                               cwd=folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert program.stdout.readline().strip() == 'there'
        yield
    finally:
        program.stdin.close()
        program.wait(30)


async def test_a_cache_folder_that_another_program_holds_stays_and_its_bytecode_goes(installation):
    target = await broken(installation)
    cache = target.folder / PACKAGE / '__pycache__'
    with a_program_has_the_folder_as_its_current_folder(installation, cache):
        result = installation.start()
        assert cache.is_dir() and not list(cache.iterdir())
    assert facts(result, 'files', 'folders', 'repaired', 'later', 'complete') == (2, 0, 1, 0, True)
    assert target.verify() == 'accepted'  # Git lists no empty folder.


async def test_a_folder_on_the_way_that_became_a_link_is_not_entered_and_nothing_behind_it_is_deleted(
        installation, monkeypatch):
    """The package's folder, not the cache folder itself: each folder of a file's path is checked on its handle."""
    target = await broken(installation)
    package = target.folder / PACKAGE
    names = sorted(path.name for path in (package / '__pycache__').iterdir())
    (installation.outside / 'package' / '__pycache__').mkdir(parents=True)
    for name in names:  # The same names behind the link, in the rest of the person's disk.
        (installation.outside / 'package' / '__pycache__' / name).write_bytes(b'not Amplifier\'s')
    behind = snapshot(installation.outside)

    def swap():
        with at_the_top(target) as top:
            os.rename(package, top / 'moved')
            installation.link(installation.outside / 'package', package)
    between_git_and_the_removal(monkeypatch, swap)
    result = installation.start()
    assert snapshot(installation.outside) == behind
    assert sorted(path.name for path in (target.folder / 'moved' / '__pycache__').iterdir()) == names
    assert facts(result, 'files', 'folders', 'repaired', 'later') == (0, 0, 0, 1)


async def test_an_object_that_became_a_link_after_the_listing_of_its_source_is_left(installation, monkeypatch):
    target = await broken(installation)
    an_object_with_bytecode(installation.outside / 'object')
    behind = snapshot(installation.outside)
    real = repair._object

    def object_(*arguments):
        if not NATIVE:
            target.folder.parent.chmod(0o755)
        os.rename(target.folder, target.folder.with_name('moved'))
        installation.link(installation.outside / 'object', target.folder)
        return real(*arguments)
    monkeypatch.setattr(repair, '_object', object_)
    result = installation.start()
    assert snapshot(installation.outside) == behind and len(bytecode(target.folder.with_name('moved'))) == 3
    assert facts(result, 'with_cache', 'git', 'files', 'left', 'change') == (0, 0, 0, 1, False)
    if installation.steps:
        assert not any(installation.outside in (path, *path.parents) for path in installation.steps.listed)


def test_a_store_with_no_object_yet_is_a_complete_pass(installation):
    result = installation.start()
    assert facts(result, 'objects', 'listings', 'git', 'complete', 'change') == (0, 0, 0, True, False)
    assert installation.start() is None


async def test_a_source_folder_that_cannot_be_opened_does_not_stop_the_others(installation, tmp_path):
    one = await broken(installation)
    two = await broken(installation, repository(tmp_path / 'another-repository'))
    refused, good = sorted((one, two), key=lambda target: target.key)
    real = installation.api.open

    def open_(parent, name, *, enter, delete=True):
        if name == refused.folder.parent.name:
            raise PermissionError(13, 'this source folder is refused')
        return real(parent, name, enter=enter, delete=delete)
    installation.api.open = open_
    result = installation.start()
    assert facts(result, 'objects', 'repaired', 'complete') == (1, 1, False)  # The pass stays open.
    assert installation.lines[0].endswith(' A source folder could not be opened.')
    assert good.verify() == 'accepted' and len(bytecode(refused.folder)) == 3
    installation.api.open = real
    assert facts(installation.start(), 'objects', 'repaired', 'complete') == (2, 1, True)
    assert refused.verify() == 'accepted'


async def test_an_object_whose_source_could_not_be_opened_in_one_run_keeps_its_tries(installation):
    """An object that is not seen in a run is not an object that is gone."""
    left = await broken(installation)
    _another_file_at_the_top(left, installation)
    first = installation.start()
    assert facts(first, 'left', 'git', 'complete') == (1, 2, True) and record(installation)['tries'] == {
        left.key: repair.TRIES}
    (installation.data / 'updates' / repair.RECORD).write_text(json.dumps(
        {**record(installation), 'complete': False}), encoding='utf-8')  # The pass is open again.
    real = installation.api.open

    def open_(parent, name, *, enter, delete=True):
        if name == left.folder.parent.name:
            raise PermissionError(13, 'this source folder is refused')
        return real(parent, name, enter=enter, delete=delete)
    installation.api.open = open_
    assert facts(installation.start(), 'objects', 'git', 'complete') == (0, 0, False)
    assert record(installation)['tries'] == {left.key: repair.TRIES}
    installation.api.open = real
    # The source opens again. The object was left for good in this app version: it costs no Git command.
    assert facts(installation.start(), 'objects', 'with_cache', 'git', 'left', 'complete') == (1, 0, 0, 0, True)


def an_object_with_bytecode(folder):
    """A Git checkout with untracked bytecode in it, as an object has, at any place."""
    (folder / 'pkg' / '__pycache__').mkdir(parents=True)
    (folder / 'pkg' / '__init__.py').write_text('X = 1\n', encoding='utf-8')
    subprocess.run(['git', 'init', '--quiet', str(folder)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(folder), '-c', 'user.name=case', '-c', 'user.email=case@example.invalid',
                    '-c', 'commit.gpgsign=false', 'add', '--all'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(folder), '-c', 'user.name=case', '-c', 'user.email=case@example.invalid',
                    '-c', 'commit.gpgsign=false', 'commit', '--quiet', '--message', 'x'],
                   check=True, capture_output=True)
    (folder / 'pkg' / '__pycache__' / ('__init__.' + TAG + '.pyc')).write_bytes(b'x')


@pytest.mark.parametrize('place', [
    'objects/not-a-source-name/' + 'a' * 40, 'objects/' + '0' * 16 + '/not-a-commit-name',
    'objects/' + '0' * 15 + '/' + 'a' * 40, 'objects/' + '0' * 16 + '/' + 'A' * 40, 'builds/' + '0' * 16,
    'objects/' + '0' * 16, 'elsewhere/' + '0' * 16 + '/' + 'a' * 40])
def test_only_a_folder_with_the_names_of_an_object_below_objects_is_looked_at(installation, place):
    """A Git checkout with untracked bytecode at another place of the store is not an object."""
    (installation.store / 'objects').mkdir()
    an_object_with_bytecode(installation.store / place)
    before = snapshot(installation.store)
    result = installation.start()
    assert facts(result, 'objects', 'with_cache', 'git', 'files', 'change') == (0, 0, 0, 0, False)
    assert snapshot(installation.store) == before


@pytest.mark.parametrize('level', ['the folder objects', 'a source folder', 'an object'])
def test_a_link_in_place_of_a_folder_of_the_store_is_not_entered(installation, level):
    """The checkout with bytecode is in the rest of the person's disk. A link in the store leads to it."""
    real = installation.outside / 'store-like' / 'objects' / ('0' * 16) / ('a' * 40)
    an_object_with_bytecode(real)
    link = {'the folder objects': installation.store / 'objects',
            'a source folder': installation.store / 'objects' / ('0' * 16),
            'an object': installation.store / 'objects' / ('0' * 16) / ('a' * 40)}[level]
    target = {'the folder objects': real.parents[1], 'a source folder': real.parents[0], 'an object': real}[level]
    link.parent.mkdir(parents=True, exist_ok=True)
    installation.link(target, link)
    behind = snapshot(installation.outside)
    result = installation.start()
    assert snapshot(installation.outside) == behind
    assert facts(result, 'objects', 'with_cache', 'git', 'files', 'change') == (0, 0, 0, 0, False)
    if installation.steps:
        assert not any(installation.outside in (path, *path.parents) for path in installation.steps.listed)


async def test_a_file_that_appears_after_the_listing_leaves_the_object(installation, monkeypatch):
    """For example a process of an earlier app version that a person started by hand writes bytecode now."""
    target = await broken(installation)
    program, late = repair._program(), target.folder / PACKAGE / '__pycache__' / 'late.cpython-399.pyc'

    def after_the_listing():
        late.write_bytes(b'new')
        return program
    monkeypatch.setattr(repair, '_program', after_the_listing)
    result = installation.start()
    assert (result['git'], result['left'], result['files'], result['change']) == (2, 1, 0, None)
    assert late.read_bytes() == b'new' and len(bytecode(target.folder)) == 4


def between_git_and_the_removal(monkeypatch, change):
    """Another program changes the object after Git listed it and before the first deletion."""
    real = repair._remove

    def then_remove(api, handle, files, count, budget):
        change()
        return real(api, handle, files, count, budget)
    monkeypatch.setattr(repair, '_remove', then_remove)


async def test_a_cache_folder_that_became_a_link_is_not_entered_and_nothing_behind_it_is_deleted(
        installation, monkeypatch):
    """Nothing is deleted through a link, at any time. Here the cache folder itself became a link."""
    target = await broken(installation)
    cache = target.folder / PACKAGE / '__pycache__'
    names = sorted(path.name for path in cache.iterdir())
    for name in names:  # The same names behind the link, in the rest of the person's disk.
        (installation.outside / '__pycache__' / name).write_bytes(b'not Amplifier\'s')
    behind = snapshot(installation.outside)

    def swap():
        os.rename(cache, cache.with_name('moved'))
        installation.link(installation.outside / '__pycache__', cache)
    between_git_and_the_removal(monkeypatch, swap)
    result = installation.start()
    assert snapshot(installation.outside) == behind
    assert sorted(path.name for path in cache.with_name('moved').iterdir()) == names
    assert (result['files'], result['folders'], result['repaired'], result['later']) == (0, 0, 0, 1)
    assert os.path.islink(cache) or os.path.isjunction(cache)


async def test_a_file_that_became_a_link_is_not_deleted(installation, monkeypatch):
    target = await broken(installation)
    cache = target.folder / PACKAGE / '__pycache__'
    first, second = sorted(cache.iterdir())

    def swap():
        first.unlink()
        installation.link(installation.outside, first)
    between_git_and_the_removal(monkeypatch, swap)
    behind = snapshot(installation.outside)
    result = installation.start()
    assert snapshot(installation.outside) == behind and (os.path.islink(first) or os.path.isjunction(first))
    assert not second.exists() and cache.is_dir()  # The other file went; the folder still holds the link.
    assert (result['files'], result['folders'], result['repaired'], result['later']) == (1, 0, 0, 1)


async def test_a_cache_folder_that_another_program_emptied_is_not_deleted(installation, monkeypatch):
    """The folder goes only when this run took a file from it."""
    target = await broken(installation)
    cache = target.folder / PACKAGE / '__pycache__'
    between_git_and_the_removal(monkeypatch, lambda: [path.unlink() for path in cache.iterdir()])
    result = installation.start()
    assert cache.is_dir() and not list(cache.iterdir())
    assert (result['files'], result['folders'], result['later']) == (0, 0, 1)


async def test_an_object_that_is_gone_from_the_store_does_not_hold_the_pass_open(installation, monkeypatch):
    target = await broken(installation)
    with monkeypatch.context() as patch:
        patch.setenv('PATH', str(installation.outside))  # No Git there: one try, and the pass stays open.
        assert installation.start()['complete'] is False and record(installation)['tries'] == {target.key: 1}
    _clear(target.folder.parent, [])  # For example storage cleanup removed the source.
    result = installation.start()
    assert (result['objects'], result['complete'], record(installation)['tries']) == (0, True, {})


async def test_git_names_in_the_environment_do_not_reach_the_decision(installation, monkeypatch, tmp_path):
    """With another index Git would list every file as changed, and the object would be left."""
    target = await broken(installation)
    monkeypatch.setenv('GIT_INDEX_FILE', str(tmp_path / 'another-index'))
    monkeypatch.setenv('GIT_DIR', str(tmp_path / 'repository' / '.git'))
    monkeypatch.setenv('GIT_WORK_TREE', str(tmp_path / 'repository'))
    result = installation.start()
    assert (result['repaired'], result['files'], result['left'], result['later']) == (1, 2, 0, 0)
    monkeypatch.delenv('GIT_INDEX_FILE')
    monkeypatch.delenv('GIT_DIR')
    monkeypatch.delenv('GIT_WORK_TREE')
    assert target.verify() == 'accepted'


async def test_a_run_that_ends_after_a_deletion_has_already_written_a_new_mark(installation, monkeypatch):
    """The record is written before the first deletion, so the next update check asks Foundation again."""
    target = await broken(installation)
    assert mark(installation) is None
    real = repair._remove

    def the_process_ends(api, handle, files, count, budget):
        real(api, handle, files[:1], count, budget)
        raise Cut  # The PC is switched off after the first file.
    monkeypatch.setattr(repair, '_remove', the_process_ends)
    with pytest.raises(Cut):
        installation.start()
    first = mark(installation)
    assert len(bytecode(target.folder)) == 2 and first is not None and record(installation)['complete'] is False
    monkeypatch.setattr(repair, '_remove', real)
    result = installation.start()  # The next start takes the rest and writes another mark.
    assert (result['files'], result['folders'], target.verify()) == (1, 1, 'accepted')
    assert mark(installation) not in (None, first)


async def test_a_record_that_was_lost_cannot_give_a_mark_that_an_earlier_check_saw(installation, monkeypatch):
    """A count would start again at the same value. A random mark does not repeat."""
    target = await broken(installation)
    busy = target.folder / PACKAGE / '__pycache__' / ('_listing.' + TAG + '.pyc')
    with in_use(installation, busy):
        assert installation.start()['files'] == 1
    first = mark(installation)
    (installation.data / 'updates' / repair.RECORD).write_text('not a record', encoding='utf-8')
    assert mark(installation) is None
    result = installation.start()
    assert (result['files'], target.verify()) == (1, 'accepted') and mark(installation) not in (None, first)


async def test_the_update_check_scans_again_after_a_repair_and_not_after_a_run_that_deleted_nothing(
        installation, monkeypatch):
    from amplifier_web.update_inventory_index import inventory
    target = await broken(installation)
    # The update manager's own folder is <data>/updates (UpdateManager.__init__, the case below). The check
    # reads the record from that folder, beside its own index file.
    manager = SimpleNamespace(home=installation.data, directory=installation.data / 'updates',
                              service=SimpleNamespace(state={'settings': {'bundle': 'work'}}))
    scan = AsyncMock(side_effect=lambda: [{'id': 'binding', 'status': target.verify()}])
    assert (await inventory(manager, scan))[0]['status'].startswith('refused') and scan.await_count == 1
    assert (await inventory(manager, scan))[0]['status'].startswith('refused') and scan.await_count == 1

    assert installation.start()['repaired'] == 1
    # The saved rows said "refused". The check after the repair does not take them.
    assert await inventory(manager, scan) == [{'id': 'binding', 'status': 'accepted'}] and scan.await_count == 2

    monkeypatch.setattr('amplifier_web.__version__', OTHER_VERSION)
    assert await inventory(manager, scan) and scan.await_count == 3  # A new app version scans by itself.
    before = mark(installation)
    result = installation.start()
    assert facts(result, 'repaired', 'complete') == (0, True) and result['change'] == before
    assert await inventory(manager, scan) and scan.await_count == 3  # That run deleted nothing.


def test_the_update_manager_keeps_its_files_where_the_record_is(tmp_path):
    """A reminder by a text scan: the check reads the record from the manager's folder, <data>/updates."""
    import inspect
    from amplifier_web.updates import UpdateManager
    source = inspect.getsource(UpdateManager.__init__)
    assert "self.home = service.data_dir" in source and "self.directory = self.home / 'updates'" in source
    assert repair._path(tmp_path) == tmp_path / 'updates' / repair.RECORD


def test_the_two_git_commands_and_the_exception_are_foundations_own():
    """A reminder, not a proof: it reads the text of Foundation's check.

    The repair decides with the commands of ``clean_checkout``. When
    Foundation changes them, this case fails and the repair must follow. The
    case of the whole way shows the agreement itself: Foundation accepts the
    repaired object.
    """
    import inspect
    from amplifier_foundation.sources import git, shared
    source = ' '.join(inspect.getsource(shared.clean_checkout).split())
    assert '"git", "--no-optional-locks", "status", "--porcelain", "--untracked-files=no"' in source
    assert '["git", "ls-files", "--others", "-z"]' in source
    assert 'name != b".amplifier_cache_meta.json"' in source and 'return not dirty and not extras' in source
    assert git._with_longpaths(['git', 'status']) == ['git', '-c', 'core.longpaths=true', 'status']
    assert repair.STATUS == ('status', '--porcelain', '--untracked-files=no')
    assert repair.OTHERS == ('ls-files', '--others', '-z') and repair.META == '.amplifier_cache_meta.json'


@pytest.mark.parametrize('others', [b'pkg/__pycache__/\xff.pyc\0', b'pkg/__pycache__/a.pyc\0../b/__pycache__/c.pyc\0',
                                    b'__pycache__\0', b'a.pyc\0', b'pkg/__pycache__/a.pyo\0', b'nested/\0',
                                    b'pkg/other/b.pyc\0', b'pkg/__pycache__/unlisted.pyc\0',
                                    b'pkg/.amplifier_cache_meta.json\0'])
def test_a_name_from_git_that_is_not_listed_bytecode_leaves_the_object(others):
    # Also when a listing had the name: it must be directly in a folder with the name __pycache__.
    caches = {('pkg', '__pycache__'): {'a.pyc', 'a.pyo'}, ('pkg', 'other'): {'b.pyc'}, (): {'a.pyc', '__pycache__'}}
    with pytest.raises(repair._Leave):
        repair._decide(others, caches)


def test_what_git_lists_decides_and_foundations_file_never_enters_the_list():
    caches = {('pkg', '__pycache__'): {'a.pyc', 'b.pyc', 'tracked.pyc'}, ('__pycache__',): {'top.pyc'}}
    others = b'.amplifier_cache_meta.json\0pkg/__pycache__/b.pyc\0__pycache__/top.pyc\0pkg/__pycache__/a.pyc\0'
    assert repair._decide(others, caches) == [
        ('__pycache__', 'top.pyc'), ('pkg', '__pycache__', 'a.pyc'), ('pkg', '__pycache__', 'b.pyc')]
    assert repair._decide(b'.amplifier_cache_meta.json\0', caches) == []


async def test_a_tree_deeper_than_the_limit_leaves_the_object(installation, monkeypatch):
    target = await broken(installation)
    monkeypatch.setattr(repair, 'DEPTH', 0)
    before = snapshot(installation.store)
    result = installation.start()
    assert (result['left'], result['git'], result['change']) == (1, 0, None) and snapshot(installation.store) == before


async def test_it_never_raises_into_the_host_start(installation, monkeypatch):
    target = await broken(installation)

    def broken_steps():
        raise RuntimeError('the native steps could not be loaded')
    monkeypatch.setattr(repair, '_steps', broken_steps)
    assert installation.start() is None and len(bytecode(target.folder)) == 3
    assert installation.lines == ['Source store repair: stopped (RuntimeError); the host starts as usual.']
    monkeypatch.setattr(repair, '_at_host_start', lambda *a: (_ for _ in ()).throw(OSError('disk')))
    assert repair.at_host_start(installation.data, log=lambda line: 1 / 0) is None  # Also a log that fails.


def test_a_record_that_cannot_be_read_is_an_empty_record(installation):
    path = the_updates_folder(installation) / repair.RECORD
    empty = {'schema': 1, 'version': None, 'complete': False, 'change': None, 'runs': 0, 'tries': {}, 'next': None,
             'last': None}
    last = dict.fromkeys(repair.MEASURES, 0)
    for text in ('', 'not json', '[]', '{"schema": 2}', json.dumps({**empty, 'version': 'x', 'change': 1}),
                 json.dumps({**empty, 'version': 'x', 'change': 'A' * 32}),
                 json.dumps({**empty, 'version': 'x', 'complete': 'yes'}),
                 json.dumps({**empty, 'version': 'x', 'runs': -1}),
                 json.dumps({**empty, 'version': 'x', 'tries': {'a': 'b'}}),
                 json.dumps({**empty, 'version': 'x', 'last': {**last, 'path': 1}}),
                 json.dumps({**empty, 'version': 'x', 'last': {**last, 'ms': 'long'}})):
        path.write_text(text, encoding='utf-8')
        assert repair.read(installation.data) == empty and mark(installation) is None
    good = {**empty, 'version': 'x', 'change': 'ab' * 16, 'last': last}
    path.write_text(json.dumps({**good, 'more': 1}), encoding='utf-8')
    assert repair.read(installation.data) == good and mark(installation) == 'ab' * 16
    # A record that lacks a field is not a whole record, also a field whose value may be null.
    for name in good:
        path.write_text(json.dumps({key: value for key, value in good.items() if key != name}), encoding='utf-8')
        assert repair.read(installation.data) == empty and mark(installation) is None, name
    path.unlink()
    path.mkdir()  # A folder where the record must be.
    assert repair.read(installation.data) == empty and mark(installation) is None


@pytest.mark.parametrize('text', ['{"schema": 1, "version": "x", "complete": true, "runs": 1, "tries": {}}',
                                  'not json', '{"schema": 1}', '[1, 2]', '"text"', 'null'])
async def test_a_record_that_is_not_whole_does_not_stop_the_update_check_or_the_repair(installation, text):
    """The update check reads the record for its signature. A reader that raises would end the check."""
    from amplifier_web.update_inventory_index import inventory, signature
    target = await broken(installation)
    path = the_updates_folder(installation) / repair.RECORD
    path.write_text(text, encoding='utf-8')
    manager = SimpleNamespace(home=installation.data, directory=installation.data / 'updates',
                              service=SimpleNamespace(state={'settings': {'bundle': 'work'}}))
    before = signature(manager)
    scan = AsyncMock(return_value=[{'id': 'binding'}])
    assert await inventory(manager, scan) == [{'id': 'binding'}] and scan.await_count == 1
    result = installation.start()  # It reads the same record as an empty one, and repairs.
    assert facts(result, 'repaired', 'files', 'complete', 'change') == (1, 2, True, True)
    assert signature(manager) != before and target.verify() == 'accepted'


def test_a_store_of_thirty_objects_with_a_hundred_folders_each_costs_no_git(installation, capsys):
    """The cost of the first start of an app version on a store with no changed object. The number is printed."""
    plain_objects(installation, 30, 100)
    result = installation.start()
    line = installation.lines[0]
    with capsys.disabled():
        print('\nCOST ' + ('native Windows' if NATIVE else 'stand-in, ' + sys.platform) + ': ' + line)
    assert (result['objects'], result['git'], result['complete'], result['listings']) == (30, 0, True, 2 + 30 * 101)
    assert int(line.split(' ms=')[1].split('.')[0]) < repair.SEARCH_SECONDS * 1000
    assert installation.start() is None
