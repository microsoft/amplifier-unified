"""A live conversation worker and the folders around its installation (native Windows).

While a conversation worker lives, it holds Amplifier Foundation's session
ownership below ``<installation folder>\\ownership``
(``amplifier_web/runtime_worker.py``, phase ``worker.ownership``). For that
time Foundation pins every folder from the drive root down to the session's
own folder.

Up to Foundation ``7526bb55`` no pin shared write access. So no program could
rename, move or replace an entry directly in the installation folder or in a
folder above it (the profile folder, ``AppData``, ``AppData\\Local``): Windows
error 32. From Foundation ``e37a946b`` a pin on a folder above Foundation's
state folder shares read and write. A pin on the state folder or below it
shares read only. No pin shares delete.

This file holds that ownership for real, in the layout that consumer setup
makes, and requires both halves. It needs the locked Foundation, so it runs in
the job that installs the lock (``.github/workflows/windows-path-length.yml``).

Real: Foundation's Windows ownership at the installed revision, this app's
folder mapping (``windows_consumer.environment``), its private writer and NTFS
sharing. Not real: there is no worker process and no app. This process holds
the ownership with the worker's own call, and a second Python process is the
"other program". A worker that started before an update keeps the Foundation
that it started with; this file cannot show that.
"""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

import pytest

# The CI step sets this. A skipped case would hide the answer; there it runs and fails instead.
REQUIRED = os.environ.get("AMPLIFIER_REQUIRE_LIVE_WORKER_FOLDERS") == "1"
pytestmark = pytest.mark.skipif(os.name != "nt" and not REQUIRED,
                                reason="Windows directory sharing needs native Windows")

# The folders that consumer setup creates in a new installation folder
# (scripts/windows_bootstrap_native.cs). The source store's name comes from the app.
SETUP_FOLDERS = ("app", "shared", "ownership", "capture", "import", "workspace", "cache", "python",
                 "python-bin", "tools", "tool-bin", "temp")


def ordinary_changes(folder, outside):
    """What another program does in a folder. Self-contained: a second process runs this source."""
    import os
    from pathlib import Path

    folder, outside = Path(folder), Path(outside)
    note, renamed, fresh = folder / "note.txt", folder / "note-renamed.txt", folder / "note-renamed.txt.tmp"
    note.write_bytes(b"first")
    os.rename(note, renamed)  # Rename a file.
    fresh.write_bytes(b"second")
    os.replace(fresh, renamed)  # Save by "write a temporary file, then replace".
    assert renamed.read_bytes() == b"second" and not fresh.exists() and not note.exists()
    made, made_renamed = folder / "made", folder / "made-renamed"
    arriving, arrived = outside / "arriving", folder / "arrived"
    made.mkdir()
    os.rename(made, made_renamed)  # Rename a folder.
    arriving.mkdir()
    os.rename(arriving, arrived)  # Move a folder in from elsewhere.
    assert made_renamed.is_dir() and arrived.is_dir() and not made.exists() and not arriving.exists()
    arrived.rmdir()  # Leave nothing, so the next run starts clean.
    made_renamed.rmdir()
    renamed.unlink()


def refusal(action):
    """The Windows error code with which ``action`` is refused."""
    with pytest.raises(OSError) as caught:
        action()
    return caught.value.winerror


def test_native_live_session_leaves_the_installation_folder_usable_and_keeps_its_own_folders_closed(monkeypatch):
    from amplifier_web import windows_consumer as consumer, windows_consumer_shell as shell, windows_files
    from amplifier_web.deployment import write_private
    # The worker's own import (amplifier_web/runtime_worker.py).
    from amplifier_foundation.session.shared_state import SharedSessionStore

    # "parent" stands for AppData\Local: the folder that holds the installation folder.
    parent = windows_files.address(
        Path(tempfile.gettempdir()).resolve(strict=True) / ("au-live-worker-" + uuid.uuid4().hex[:12]))
    root, outside = parent / "Amplifier", parent / "elsewhere"
    try:
        with windows_files.directory(parent, owned_root=parent, create=True):
            pass
        for name in ("", consumer.SOURCE_STORE, *SETUP_FOLDERS):
            with windows_files.directory(root / name, owned_root=root, create=True):
                pass
        outside.mkdir()
        # This app's own mapping decides where Foundation keeps its state.
        environment = consumer.environment(root, {"uv": str(root / "uv" / "uv.exe"),
                                                  "git": str(root / "git" / "cmd" / "git.exe")})
        state = Path(environment["AMPLIFIER_SESSION_STATE_HOME"])
        assert state == root / "ownership"
        for name in ("AMPLIFIER_SESSION_STATE_HOME", "AMPLIFIER_HOME"):
            monkeypatch.setenv(name, environment[name])
        session = str(uuid.uuid4())
        # The worker's own call: no explicit state folder, the same diagnostics.
        held = SharedSessionStore(root / "workspace", session).acquire(app="amplifier-unified", pid=os.getpid())
        try:
            # Foundation's own folders: <state>\v1\<workspace key>\<session>.
            [workspace_key] = [entry for entry in (state / "v1").iterdir() if entry.is_dir()]
            [session_folder] = [entry for entry in workspace_key.iterdir() if entry.is_dir()]
            assert session_folder.name == session
            owned = [state, state / "v1", workspace_key, session_folder]

            # (a) The installation folder stays an ordinary folder for every program:
            #     a new private file, a replacement over it, renames and an arrival.
            #     Before Foundation e37a946b each of these was refused with error 32.
            for status in ("installed", "removed"):
                write_private(root / shell.FILE, json.dumps({"status": status}), owned_root=root)
                with windows_files.directory(root, owned_root=root) as pinned:
                    assert json.loads(pinned.read_private(shell.FILE, limit=4096)) == {"status": status}
            for folder in (root, parent):
                ordinary_changes(folder, outside)
                source = "import sys\n" + inspect.getsource(ordinary_changes) + "\nordinary_changes(*sys.argv[1:])\n"
                other = subprocess.run([sys.executable, "-I", "-c", source, str(folder), str(outside)],
                                       capture_output=True, text=True, timeout=60)
                assert other.returncode == 0, other.stderr

            # (b) A pinned folder itself cannot be renamed or moved away.
            for folder in (root, parent, *owned):
                moved = folder.with_name(folder.name + "-moved")
                assert refusal(lambda: os.rename(folder, moved)) == 32, folder
                assert folder.is_dir() and not moved.exists()

            # (c) Foundation's own folders still refuse an arrival, by intent.
            for folder in owned:
                for name, make in (("stray.txt", lambda path: path.write_bytes(b"stray")),
                                   ("stray-folder", lambda path: path.mkdir())):
                    stray = outside / name
                    make(stray)
                    assert refusal(lambda: os.rename(stray, folder / name)) == 32, folder
                    assert stray.exists() and not (folder / name).exists()
                    stray.unlink() if stray.is_file() else stray.rmdir()
            held.check()  # The ownership itself is unharmed.
        finally:
            held.release()
        # Control: the refusals came from the held session. After its release
        # the same changes work.
        (outside / "stray.txt").write_bytes(b"stray")
        os.rename(outside / "stray.txt", state / "stray.txt")
        os.rename(root, root.with_name("Amplifier-moved"))
        assert (root.with_name("Amplifier-moved") / "ownership" / "stray.txt").read_bytes() == b"stray"
        # The folder above the installation folder is released too.
        moved = parent.with_name(parent.name + "-moved")
        os.rename(parent, moved)
        parent = moved
    finally:
        # Only this test's own throwaway folder, by its exact name.
        shutil.rmtree(parent, ignore_errors=True)
