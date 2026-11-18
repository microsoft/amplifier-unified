"""Remove bytecode that an earlier app version wrote into an object of the source store.

On Windows a helper of 0.106.5 or earlier wrote ``__pycache__\\*.pyc`` into an
object of the installation's shared source store. Foundation refuses such an
object from then on ("Shared source contents changed"): no chat with that
connection starts, a component update fails, and the Updates page keeps a
source that could not be checked. 0.106.6 stops new bytecode
(``source_imports.prepare``). This step makes an object that already has such
files equal to its commit again.

It deletes a file only when all of these hold:

- it is a plain file directly in a folder named ``__pycache__``, and its name
  ends with ``.pyc``;
- it is below an object of this installation's own store;
- Git lists it as untracked in that object;
- the object has no changed tracked file and no other untracked entry. One
  exception, the one that Foundation makes: ``.amplifier_cache_meta.json`` at
  the object's top. Foundation writes it into every object and leaves it out
  of its own check (``sources.shared.clean_checkout``). This step never
  deletes it.

Then it deletes that ``__pycache__`` folder when this run took a file from it
and it is empty. It deletes nothing else, anywhere. Any doubt leaves the
object exactly as it is: a link of any kind that the listing finds in the
object or below its ``.git`` (Git does not run then), a Git error, a name
that Git gives and the listing did not have. A link that appears after the
listing can be read by Git; the deletion still opens each folder of a file's
path and the file as themselves and checks each on its handle, so nothing
behind such a link is deleted.

Every listing and every deletion uses the native steps of
``windows_removal``: a name comes from the listing of an open handle, and an
entry is opened relative to its parent's handle, as itself. No path is
resolved twice, and no link is followed. Git gets a path; the folders of that
path are held open meanwhile, and Git only decides. What is deleted is
checked again on handles.

It runs only on Windows, only in the host that the start-at-sign-in task
started, only on the store of the installation that this host belongs to, and
only while it holds the data folder's host lock exclusively: no other host of
this data folder is inside its serving part, and this host has started no
worker, helper or update stage yet. Three cases are not excluded; each needs a
host that a person started by hand (docs/design/native-windows-installation.md,
"Repair of a source store object that holds bytecode"). A process of an app
version before 0.106.6 in them can write new bytecode; that object is then
refused again until the next app version. Nothing outside the rule above is
deleted in any of them.

The record ``<data>\\updates\\source-store-repair.json`` holds the app version,
whether the pass is complete, a change mark, the tries of each object that was
left, and the counts and milliseconds of the last run (the search's listings;
the time until the run's last write of the record). It holds no path and no
time of day. A run writes the record before it looks at the store; a
record that cannot be written means that the store is not looked at. After a
complete pass a start of the same app version reads the record and does
nothing more. Every run that finds a file to delete writes a new change mark
(32 random hexadecimal digits) before its first deletion; when that write
fails, the run deletes nothing. The mark is an input of the update check's
scan signature (``update_inventory_index``), so the check after a repair asks
Foundation again. A mark is random and not a count: after a record that was
lost or could not be read, a new run cannot write a mark that an earlier
check saw. Such a record itself gives no mark, as before the first repair;
the update check keeps the rows of one signature only, so it scans again
then too. One sequence keeps saved rows: the record is lost, a check saves
rows, a run repairs, and the record is lost again before the next check. The
check's own rule ends that after 4 hours (``RECONCILE_SECONDS``).

No native step of a run begins after the limit of the whole step: every
listing, open, look at a kind and deletion goes through one wrapper
(``_Bounded``) that checks the limit first. The look for Git into each
folder of PATH, each Git command and the write of a change mark have the same
check, and a Git command
is started only when half a second is left. So after an operation that took
the rest of the time, whether it worked or failed, the run only gives its
handles back and writes the record a last time. One operation has no time
limit of its own, and neither has the first check of the folders above the
store. No handle of this step reaches the Git program: each is opened without
inheritance.

``at_host_start`` raises no error into the host's start. A stop of the
process (``KeyboardInterrupt``, ``SystemExit``) passes through it.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time

RECORD = "source-store-repair.json"
CACHE, META, GIT = "__pycache__", ".amplifier_cache_meta.json", ".git"
# The names that Foundation gives a source folder and an object (sources.shared).
SOURCE, COMMIT = re.compile(r"[0-9a-f]{16}|[0-9a-f]{64}"), re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# For one run. A bound reached: the run stops, and a later start goes on.
LISTINGS, ENTRIES, DEPTH = 20_000, 400_000, 100
SEARCH_SECONDS, GIT_SECONDS, TOTAL_SECONDS = 3.0, 5.0, 8.0
GIT_LEAST = 0.5  # A Git command is started only when this much of the whole step is left.
# For one app version: tries for one object, and runs that do not complete.
TRIES, RUNS = 3, 10
_DIRECTORY, _LINK = 0x10, 0x400  # FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT
# Foundation's two commands (sources.shared.clean_checkout), word for word.
STATUS = ("status", "--porcelain", "--untracked-files=no")
OTHERS = ("ls-files", "--others", "-z")


class _Bound(Exception):
    """A bound of the run is reached."""


class _Leave(Exception):
    """The object stays as it is and is not tried again in this app version."""


class _Later(Exception):
    """The object stays as it is for a reason that can pass (a file in use, a Git failure)."""


class _Stop(Exception):
    """The record cannot be written. This run deletes nothing more."""


def _windows():
    return os.name == "nt"


def _steps():
    from .windows_removal import _Native
    return _Native()


@contextmanager
def _held(api, path):
    """The store folder, proven on its handle and held open. Every folder above it is checked first."""
    from .windows_files import address, directory
    path = address(path)
    with directory(path, owned_root=path):
        handle = api.root(path)
    try:
        yield handle
    finally:
        api.close(handle)


@contextmanager
def _entry(api, parent, name, *, delete=False):
    """One folder of an open folder, opened as itself for listing."""
    handle = api.open(parent, name, enter=True, delete=delete)
    try:
        yield handle
    finally:
        api.close(handle)


def _path(data_dir):
    return Path(data_dir) / "updates" / RECORD


COUNTS = ("objects", "with_cache", "repaired", "files", "folders", "left", "later")
MEASURES = (*COUNTS, "listings", "entries", "git", "ms")


def _new():
    return {"schema": 1, "version": None, "complete": False, "change": None, "runs": 0, "tries": {},
            "next": None, "last": None}


def _whole(value):
    return type(value) is int and value >= 0


def read(data_dir):
    """The saved record. An absent record, or one that cannot be read, is an empty record."""
    return _read(_path(data_dir))


def _read(path):
    """Never raises: the update check reads the record too. Anything that is not a whole record is an empty one."""
    try:
        with path.open("rb") as stream:
            value = json.loads(stream.read(1_000_001))
        record = {name: value[name] for name in _new()}  # Each field by its name; a missing one is an error.
        if (record["schema"] != 1
                or not isinstance(record["version"], str) or type(record["complete"]) is not bool
                or not (record["change"] is None
                        or isinstance(record["change"], str) and re.fullmatch(r"[0-9a-f]{32}", record["change"]))
                or not _whole(record["runs"]) or not isinstance(record["tries"], dict)
                or any(not isinstance(key, str) or not _whole(count) for key, count in record["tries"].items())
                or not (record["next"] is None or isinstance(record["next"], str))
                or not (record["last"] is None or isinstance(record["last"], dict)
                        and set(record["last"]) == set(MEASURES) and all(map(_whole, record["last"].values())))):
            return _new()
        return record
    except Exception:
        return _new()


def change(updates):
    """The mark that a run writes before its first deletion. None when no run has deleted anything.

    ``updates`` is the folder ``<data>\\updates``: the record is beside the
    update check's own index file.
    """
    return _read(Path(updates) / RECORD)["change"]


def _save(data_dir, record):
    from .host.config import write_private
    write_private(_path(data_dir), json.dumps(record, sort_keys=True), owned_root=Path(data_dir))


class _Budget:
    """The bounds of one run. Time in Git and in a deletion does not count as search."""

    def __init__(self, clock):
        self.clock, self.start = clock, clock()
        self.listings = self.entries = self.git = 0
        self.apart, self.since = 0.0, None

    def elapsed(self):
        return self.clock() - self.start

    def searched(self):
        now = self.clock()
        return now - self.start - self.apart - (now - self.since if self.since is not None else 0.0)

    def deadline(self):
        """Before an operation begins: the limit of the whole step."""
        if self.elapsed() >= TOTAL_SECONDS:
            raise _Bound()

    def listed(self, entries):
        """After a listing: the bounds of the search."""
        self.listings += 1
        self.entries += entries
        if (self.listings > LISTINGS or self.entries > ENTRIES
                or self.searched() > SEARCH_SECONDS or self.elapsed() > TOTAL_SECONDS):
            raise _Bound()

    @contextmanager
    def aside(self):
        self.since = self.clock()
        try:
            yield
        finally:
            self.apart += self.clock() - self.since
            self.since = None

    def for_git(self):
        left = TOTAL_SECONDS - self.elapsed()
        if left < GIT_LEAST:
            raise _Bound()  # Too little is left for an answer. Git is not started.
        return min(GIT_SECONDS, left)


class _Bounded:
    """The native steps of a run. None begins after the limit of the whole step.

    Every listing, open, look at a kind and deletion of a run goes through
    here, also the open of the store folder. So after an operation that took
    the rest of the time, whether it worked or failed, no other begins.
    ``close`` has no check: a handle is always given back.
    """

    def __init__(self, api, budget):
        self._api, self._budget = api, budget

    def root(self, path):
        self._budget.deadline()
        return self._api.root(path)

    def children(self, handle):
        self._budget.deadline()
        rows = self._api.children(handle)
        self._budget.listed(len(rows))
        return rows

    def open(self, parent, name, *, enter, delete=True):
        self._budget.deadline()
        return self._api.open(parent, name, enter=enter, delete=delete)

    def kind(self, handle):
        self._budget.deadline()
        return self._api.kind(handle)

    def delete(self, handle):
        self._budget.deadline()
        self._api.delete(handle)

    def close(self, handle):
        self._api.close(handle)


def _walk(api, handle, *, top=True, trail=()):
    """Every folder below an open folder, listed from handles; a link is never entered.

    Returns the plain files directly in each folder named ``__pycache__``,
    whether a link of any kind is anywhere, and the names of the real folders
    at the top. ``.git`` at an object's top is not entered here.
    """
    if len(trail) > DEPTH:
        raise _Leave()
    rows = api.children(handle)
    caches, link, folders = {}, False, []
    if trail and trail[-1] == CACHE:
        caches[trail] = {name for name, attributes in rows if not attributes & (_DIRECTORY | _LINK)}
    for name, attributes in rows:
        if attributes & _LINK:
            link = True
            continue
        if not attributes & _DIRECTORY:
            continue
        folders.append(name)
        if top and name == GIT:
            continue
        with _entry(api, handle, name) as child:
            if api.kind(child) != "folder":  # It changed after the listing.
                link = True
                continue
            below, linked, _ = _walk(api, child, top=False, trail=(*trail, name))
        caches.update(below)
        link = link or linked
    return caches, link, folders


def _program(check=lambda: None):
    """Git of the first PATH folder that has it. Never one from the current folder.

    The task's host has the installation's saved tool path as its PATH, and a
    worker inherits it. Foundation's own check starts ``git`` by its name
    there. If the two programs or their configurations differ, this step can
    leave an object that Foundation would accept without the bytecode, or
    remove bytecode from an object that Foundation still refuses. It cannot
    delete anything else.

    ``check`` runs before the look into each folder: a folder of PATH can be
    on a drive that does not answer.
    """
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(folder) / ("git.exe" if os.name == "nt" else "git")
        if not folder or not candidate.is_absolute():
            continue
        check()
        if candidate.is_file():
            return str(candidate)
    raise _Later()


def _git(program, folder, budget, *words):
    """One of Foundation's two commands, with the paths fixed so that Git discovers nothing."""
    environment = {name: value for name, value in os.environ.items() if not name.upper().startswith("GIT_")}
    command = [program, "-c", "core.longpaths=true", "-c", "core.fsmonitor=false", "--no-optional-locks",
               "--git-dir", os.path.join(folder, GIT), "--work-tree", folder, *words]
    with budget.aside():
        limit = budget.for_git()
        budget.git += 1
        try:
            process = subprocess.Popen(command, cwd=folder, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       creationflags=0x08000000 if os.name == "nt" else 0)  # CREATE_NO_WINDOW
        except OSError as error:
            raise _Later() from error
        try:
            output, _ = process.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            # The limit ends the program that this step started, and the step
            # stops waiting. An installation's ``git\cmd\git.exe`` is Git's
            # starter program: the Git program behind it can run on until its
            # read ends. It only reads, and the object is left as it is.
            process.kill()
            for last in (process.communicate, process.wait):
                try:
                    last(timeout=0.5)  # So a Git that passed its limit adds at most one second.
                except (subprocess.TimeoutExpired, OSError, ValueError):
                    pass
            raise _Later() from None
    if process.returncode:
        raise _Later()
    return output


def _decide(others, caches):
    """The files to delete, each as the parts of its name in the object. Any other finding leaves the object."""
    try:
        names = [name.decode("utf-8") for name in others.split(b"\0") if name]
    except UnicodeDecodeError:
        raise _Leave() from None
    files = []
    for name in names:
        if name == META:
            continue  # Foundation's own file. It never enters this list.
        parts = tuple(name.split("/"))
        if (len(parts) < 2 or parts[-2] != CACHE or not parts[-1].endswith(".pyc")
                or parts[-1] not in caches.get(parts[:-1], ())):
            raise _Leave()  # Another untracked entry, or a name that the listing did not have.
        files.append(parts)
    return sorted(files)


def _remove(api, handle, files, count):
    """Delete each file through its handle, then a folder that this run emptied. True when no file stayed.

    Each folder of a file's path and the file itself are opened again, as
    themselves, and checked on their handles. At the limit of the whole step
    the deletion stops where it is (``_Bounded``): what is deleted stays
    deleted, and a later start takes the rest.
    """
    folders, whole = {}, True
    for parts in files:
        folders.setdefault(parts[:-1], []).append(parts[-1])
    for trail, names in sorted(folders.items()):
        opened, deletable = [], True
        try:
            parent = handle
            for position, part in enumerate(trail):
                last = position == len(trail) - 1
                try:
                    child = api.open(parent, part, enter=True, delete=last)
                except OSError:
                    if not last:
                        raise
                    # Another program holds the folder. Its files can still go; the folder stays.
                    child, deletable = api.open(parent, part, enter=True, delete=False), False
                opened.append(child)
                if api.kind(child) != "folder":
                    raise OSError(22, "not a folder")
                parent = child
            removed = 0
            for name in names:
                try:
                    file = api.open(parent, name, enter=False)
                    try:
                        if api.kind(file) != "file":
                            raise OSError(22, "not a plain file")
                        api.delete(file)
                    finally:
                        api.close(file)
                    removed += 1
                    count["files"] += 1
                except OSError:
                    whole = False  # In use, or changed. It stays.
            if removed and deletable and not api.children(parent):
                try:
                    api.delete(parent)
                    count["folders"] += 1
                except OSError:
                    pass
        except OSError:
            whole = False
        finally:
            for child in reversed(opened):
                api.close(child)
    return whole


def _object(api, parent, name, folder, budget, before_delete, count):
    """One object of the store. Returns when it needs nothing more; else raises why it stays."""
    with _entry(api, parent, name) as handle:
        if api.kind(handle) != "folder":
            raise _Leave()
        caches, link, folders = _walk(api, handle)
        if not caches:
            return
        count["with_cache"] += 1
        if link or GIT not in folders:
            raise _Leave()  # The listing found a link. Git does not run.
        with _entry(api, handle, GIT) as git:
            if api.kind(git) != "folder":
                raise _Leave()
            if _walk(api, git, top=False)[1]:
                raise _Leave()  # The listing found a link below .git. Git does not run.
            program = _program(budget.deadline)
            if _git(program, folder, budget, *STATUS).strip():
                raise _Leave()  # A tracked file is changed.
            files = _decide(_git(program, folder, budget, *OTHERS), caches)
            if not files:
                return
            budget.deadline()
            before_delete()
            with budget.aside():
                if not _remove(api, handle, files, count):
                    raise _Later()
    count["repaired"] += 1


def _run(api, store, data_dir, record, clock, log):
    budget = _Budget(clock)
    api = _Bounded(api, budget)  # From here on no native step begins after the limit.
    count = dict.fromkeys(COUNTS, 0)
    tries, announced = record["tries"], []
    if record["runs"] >= RUNS:
        record["complete"] = True  # Also when each of those runs was cut before its end.
    else:
        record["runs"] += 1
    try:
        # First of all: the record of this run. A record that cannot be written
        # means that the store is not looked at.
        _save(data_dir, record)
    except Exception as error:
        log("Source store repair: the record could not be written (" + type(error).__name__
            + "); the store was not looked at.")
        return None
    if record["complete"]:
        log(f"Source store repair: {RUNS} runs for this app version did not complete; it does not look again.")
        return None

    def before_delete():
        if announced:
            return
        earlier, record["change"] = record["change"], secrets.token_hex(16)
        try:
            _save(data_dir, record)
        except Exception as error:
            record["change"] = earlier
            raise _Stop() from error
        announced.append(True)

    reason, refused = "", set()  # The source folders that could not be listed in this run.
    try:
        with ExitStack() as stack:
            top = stack.enter_context(_held(api, store))
            try:
                objects = stack.enter_context(_entry(api, top, "objects"))
            except FileNotFoundError:
                objects = None  # A store with no object yet.
            if objects is not None and api.kind(objects) != "folder":
                raise OSError(20, "objects is not a folder")
            ids = []
            if objects is not None:
                for source, attributes in api.children(objects):
                    if not SOURCE.fullmatch(source) or attributes & _LINK or not attributes & _DIRECTORY:
                        continue
                    try:
                        with _entry(api, objects, source) as folder:
                            if api.kind(folder) != "folder":
                                raise OSError(20, "the source folder changed into a link")
                            ids += [source + "/" + commit for commit, attributes in api.children(folder)
                                    if COMMIT.fullmatch(commit) and attributes & _DIRECTORY
                                    and not attributes & _LINK]
                    except OSError:
                        refused.add(source)  # The other sources are still looked at. The pass stays open.
            ids.sort()
            count["objects"] = len(ids)
            for key in set(tries) - set(ids):
                if key.split("/")[0] not in refused:  # Gone from the store. An object that was not seen keeps its tries.
                    del tries[key]
            first = True
            for position, key in enumerate(ids):
                if (record["next"] is not None and key < record["next"]) or tries.get(key, 0) >= TRIES:
                    continue
                source, commit = key.split("/")
                try:
                    with _entry(api, objects, source) as folder:
                        if api.kind(folder) != "folder":
                            raise _Leave()
                        _object(api, folder, commit, os.path.join(store, "objects", source, commit),
                                budget, before_delete, count)
                    tries.pop(key, None)
                except _Bound:
                    if first:  # The first object of this run: one try, and the next run starts after it.
                        tries[key] = tries.get(key, 0) + 1
                        record["next"] = ids[position + 1] if position + 1 < len(ids) else None
                    else:
                        record["next"] = key
                    raise
                except _Stop:
                    raise
                except _Leave:
                    tries[key] = TRIES
                    count["left"] += 1
                except Exception:
                    # A file in use, a Git failure, or anything that this code did not
                    # expect of one object. The object stays; the others are still looked at.
                    tries[key] = tries.get(key, 0) + 1
                    count["later"] += 1
                first = False
            record["next"] = None
            record["complete"] = not refused and all(value >= TRIES for value in tries.values())
    except _Bound:
        reason = " A bound was reached."
    except _Stop:
        reason = " The record could not be written, so nothing was deleted."
    except Exception as error:
        # A store folder that is refused, or anything else. It still counts as a run.
        reason = " Stopped (" + type(error).__name__ + "); nothing more was changed."
    if refused:
        reason += " A source folder could not be opened."
    if record["runs"] >= RUNS:
        record["complete"] = True  # The last run for this app version.
    record["last"] = {**count, "listings": budget.listings, "entries": budget.entries, "git": budget.git,
                      "ms": int(budget.elapsed() * 1000)}
    try:
        _save(data_dir, record)
    except Exception as error:
        reason += " The record was not saved (" + type(error).__name__ + ")."
    log("Source store repair: complete=" + str(record["complete"]).lower()
        + "".join(f" {name}={record['last'][name]}" for name in MEASURES) + "." + reason)
    return {**record["last"], "complete": record["complete"], "change": record["change"]}


def _at_host_start(data_dir, clock, log):
    store = os.environ.get("AMPLIFIER_SOURCE_STORE")
    if not _windows() or not store:
        return None
    from . import __version__
    record = read(data_dir)
    if record["version"] == __version__ and record["complete"]:
        return None  # Every start after a complete pass: one small file was read.
    from .windows_consumer import EARLIER_SOURCE_STORE, RECEIPT, SOURCE_STORE
    root = data_dir.parent

    def same(path):
        return os.path.normcase(os.path.abspath(path))
    if (not (root / RECEIPT).is_file()
            or same(store) not in {same(root / SOURCE_STORE), same(root / EARLIER_SOURCE_STORE)}):
        return None  # Not an installation from the setup file, or a store elsewhere: never touched.
    if record["version"] != __version__:
        record.update(version=__version__, complete=False, runs=0, tries={}, next=None)  # The mark stays.
    from .reset import repair_lock
    with ExitStack() as stack:
        try:
            # Every host holds this lock shared while it serves. Taken without a wait.
            stack.enter_context(repair_lock(data_dir))
        except (RuntimeError, ValueError, OSError):
            return None  # Another host or a reset uses this data folder.
        return _run(_steps(), os.path.abspath(store), data_dir, record, clock, log)


def at_host_start(data_dir, *, clock=time.monotonic, log=print):
    """For the host that the start-at-sign-in task started, before it takes its own lock.

    It raises no error. The task's host has its output in its private log
    (``windows_service._serve_host``), so the one line of a run goes there.
    """
    try:
        return _at_host_start(Path(data_dir), clock, log)
    except Exception as error:
        try:
            log("Source store repair: stopped (" + type(error).__name__ + "); the host starts as usual.")
        except Exception:
            pass
        return None
