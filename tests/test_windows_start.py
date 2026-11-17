"""A start that a person can see, and one start at a time (amplifier_web.windows_start).

Portable: what the sign says for each step of a start, and what the lock of a
running start does with each answer of Windows.

Native Windows (a skip fails the lane `windows-local-connection`): the real
sign window on this PC's desktop, the real named locks across two processes,
the launcher's start with both, the installation's lock around setup's
entries, and the start texts over real private files. Stand-ins there: the
installation check, Task Scheduler, the certificate question and the browser.

Not shown by a runner: that a person notices the sign, how it looks, where it
is beside a real taskbar, and a real second click on the Start-menu entry.
Those are clean-room steps.
"""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

from amplifier_web import windows_consumer as consumer, windows_start

REPOSITORY = Path(__file__).resolve().parents[1]
NATIVE = pytest.mark.skipif(os.name != "nt", reason="A real window, real named locks and real private files")


def test_the_sign_says_that_amplifier_is_starting_and_what_the_long_step_does():
    shown = windows_start.Sign()
    assert shown.text == windows_start.STARTING == "Amplifier is starting."
    said = []
    for name in ("address", "tls", "prepare", "address", "task", "ready"):
        shown.step(name)
        said.append(shown.text)
    # Only the step that downloads says more, and only while it runs.
    assert said == [windows_start.STARTING, windows_start.STARTING, windows_start.PREPARING,
                    windows_start.STARTING, windows_start.STARTING, windows_start.STARTING]
    assert windows_start.PREPARING == ("Amplifier is starting.\n\nIt downloads the remaining parts of the app "
                                       "from the internet. This can take a few minutes.")


def test_elsewhere_than_on_windows_there_is_no_sign_and_no_lock(tmp_path, monkeypatch):
    # Elsewhere there is no Start-menu entry. The same code runs there in tests of the start.
    monkeypatch.setattr(windows_start, "os", SimpleNamespace(name="posix"))
    with windows_start.only(tmp_path), windows_start.only(tmp_path):
        with windows_start.sign() as shown:
            shown.step("prepare")
            assert shown.window is None and shown.text == windows_start.PREPARING
    assert not [thread for thread in threading.enumerate() if thread.name == "amplifier-start-sign"]


def test_the_lock_of_a_running_start_for_each_answer_of_windows(tmp_path, monkeypatch):
    from amplifier_web import windows_uninstall
    monkeypatch.setattr(windows_start, "os", SimpleNamespace(name="nt"))
    calls, answer = [], [None]

    @contextmanager
    def named(prefix, root, *, wait):
        calls.append(("take", prefix, root, wait))
        if answer[0] is not None:
            raise answer[0]
        try:
            yield
        finally:
            calls.append(("release",))
    monkeypatch.setattr(windows_uninstall, "named", named)
    # Free: this start holds it for its whole time and never waits for it.
    with windows_start.only(tmp_path):
        assert calls == [("take", "Local\\AmplifierUnified-start-", tmp_path, 0)]
    assert calls[-1] == ("release",)
    # A failure of the start itself releases it, and is not hidden.
    with pytest.raises(ValueError, match="the start failed"):
        with windows_start.only(tmp_path):
            raise ValueError("the start failed")
    assert calls[-1] == ("release",)
    # Taken: another start runs. Nothing is entered.
    answer[0] = windows_uninstall.Busy()
    with pytest.raises(windows_start.Running):
        with windows_start.only(tmp_path):
            pytest.fail("A second start must not go on.")
    # Windows gives no lock (a lock of this name that another account made, or no lock at all):
    # the person still gets Amplifier. The only loss is a second browser tab.
    for refused in (PermissionError(5, "The installation lock belongs to another account."), OSError(8, "No memory")):
        answer[0] = refused
        entered = []
        with windows_start.only(tmp_path):
            entered.append(True)
        assert entered == [True]


def _user():
    import ctypes as C
    from ctypes import wintypes as W
    user = C.WinDLL("user32", use_last_error=True)
    handle = C.c_void_p
    user.IsWindow.argtypes = user.IsWindowVisible.argtypes = user.DestroyWindow.argtypes = [handle]
    user.GetWindowTextW.argtypes = [handle, W.LPWSTR, C.c_int]
    user.GetWindowLongPtrW.restype = C.c_ssize_t
    user.GetWindowLongPtrW.argtypes = [handle, C.c_int]
    user.GetWindowRect.argtypes = [handle, C.POINTER(W.RECT)]
    user.GetForegroundWindow.restype = handle
    user.SystemParametersInfoW.argtypes = [W.UINT, W.UINT, handle, W.UINT]
    user.CreateWindowExW.restype = handle
    user.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int,
                                     handle, handle, handle, handle]

    def text(window):
        buffer = C.create_unicode_buffer(1024)
        user.GetWindowTextW(window, buffer, 1024)
        return buffer.value

    def place(window):
        rect, area = W.RECT(), W.RECT()
        assert user.GetWindowRect(window, C.byref(rect)) and user.SystemParametersInfoW(0x0030, 0, C.byref(area), 0)
        return rect, area
    user.text, user.place = text, place
    return user


def _until(condition, what, limit=10):
    deadline = time.monotonic() + limit
    while not condition():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


@NATIVE
def test_native_sign_is_on_the_screen_for_the_time_of_a_start_takes_no_focus_and_goes_away():
    user = _user()
    with windows_start.sign() as shown:
        window, label = shown.window, shown.label
        assert window and label, "The sign was not made on this desktop."
        assert user.IsWindowVisible(window) and user.text(label) == "Amplifier is starting."
        # In front of other windows, with no taskbar button, and never the active window.
        style = user.GetWindowLongPtrW(window, -20)
        assert style & 0x00000008 and style & 0x00000080 and style & 0x08000000
        assert user.GetForegroundWindow() != window
        # In the corner above the clock, inside the screen without the taskbar.
        rect, area = user.place(window)
        assert area.left <= rect.left < rect.right <= area.right and area.top <= rect.top < rect.bottom <= area.bottom
        assert area.right - rect.right < 64 and area.bottom - rect.bottom < 64
        # The step that downloads: more text, a higher sign, still inside the screen.
        shown.step("prepare")
        _until(lambda: user.text(label) == windows_start.PREPARING, "The sign did not say what the long step does.")
        longer, area = user.place(window)
        assert longer.bottom - longer.top > rect.bottom - rect.top and area.top <= longer.top
        assert longer.bottom == rect.bottom and longer.right == rect.right
        shown.step("task")
        _until(lambda: user.text(label) == windows_start.STARTING, "The sign kept the text of the long step.")
        # A message of this program is on the screen: the sign hides, and comes back after it.
        message = user.CreateWindowExW(0, "#32770", "Amplifier Unified", 0x80000000 | 0x10000000,
                                       0, 0, 240, 120, None, None, None, None)
        assert message
        try:
            _until(lambda: not user.IsWindowVisible(window), "The sign stayed beside a message of its own program.")
        finally:
            user.DestroyWindow(message)
        _until(lambda: user.IsWindowVisible(window), "The sign did not come back after the message.")
        assert user.GetForegroundWindow() != window
    # The start ended: the sign is gone, with no click, and nothing of it runs on.
    assert not user.IsWindow(window)
    assert not [thread for thread in threading.enumerate() if thread.name == "amplifier-start-sign"]


def _try(root, seen, lock=windows_start.only):
    def attempt():
        try:
            with lock(root):
                seen.append("entered")
        except windows_start.Running:
            seen.append("running")
    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join(30)
    assert not thread.is_alive()


@NATIVE
def test_native_second_start_finds_the_first_across_processes_and_takes_a_lock_that_was_left(tmp_path):
    """Two clicks on the Start-menu entry are two programs. The second finds the first one's lock."""
    from amplifier_web import windows_uninstall
    root, other = tmp_path / "installation", tmp_path / "another installation"
    held = tmp_path / "held.txt"
    program = ("import sys, time\n"
               "from pathlib import Path\n"
               "from amplifier_web import windows_start\n"
               "with windows_start.only(Path(sys.argv[1])):\n"
               "    Path(sys.argv[2]).write_text('held')\n"
               "    time.sleep(120)\n")
    first = subprocess.Popen([sys.executable, "-c", program, str(root), str(held)],
                             env={**os.environ, "PYTHONPATH": str(REPOSITORY)})
    seen = []
    try:
        _until(lambda: held.exists() or first.poll() is not None, "The first start did not take its lock.", 60)
        assert first.poll() is None
        _try(root, seen)
        assert seen == ["running"]  # The first start runs: a second one does not go on.
        _try(other, seen)
        assert seen == ["running", "entered"]  # Another installation's start is not held up.
        # A start does not keep an uninstall waiting: the installation's lock is another lock.
        with windows_uninstall.single(root, wait=0.2):
            pass
    finally:
        first.kill()  # The first program ends without releasing its lock.
        first.wait(30)
    del seen[:]
    _try(root, seen)
    assert seen == ["entered"]  # The lock that it left is taken by the next start.
    # In one program: held for the block, free after it.
    with windows_start.only(root):
        _try(root, seen)
    _try(root, seen)
    assert seen == ["entered", "running", "entered"]


def _start_stand_ins(monkeypatch, root, events, opened):
    """The launcher's start with stand-ins for the installation, Task Scheduler, trust and the browser."""
    from amplifier_web import browser_entry, deployment, updates, windows_installation
    from amplifier_web.update_diagnostics import CommandOutput
    monkeypatch.setattr(consumer, "installation", lambda folder: (root, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})

    async def prepared(*args, identity, progress):
        progress("ready")
        events.append("start step")
        return {"background": True, "created": False, "health": {"ok": True}}
    monkeypatch.setattr(windows_installation, "prepare_background", prepared)
    monkeypatch.setattr(deployment, "load_server_config", lambda home: {})
    monkeypatch.setattr(browser_entry, "_owner_probe_targets", lambda *a: (["private-target"], "private-pin"))
    monkeypatch.setattr(windows_installation, "review_browser_trust", lambda *a, fresh: True)

    async def process(*args, env, timeout):
        events.append("open command")
        await asyncio.to_thread(opened.wait, 60)  # The browser command runs until the test lets it end.
        return CommandOutput(browser_entry.OPENED)
    monkeypatch.setattr(updates, "process", process)
    monkeypatch.setattr(windows_installation, "owner_notice",
                        lambda message, **options: events.append("message: " + message) or True)


@NATIVE
def test_native_second_start_during_a_start_does_nothing_and_the_first_shows_its_sign(tmp_path, monkeypatch):
    """The real lock and the real sign in the launcher's start; one browser command for two starts."""
    user = _user()
    root = tmp_path / "installation"
    root.mkdir()
    events, opened, signs, results = [], threading.Event(), [], []
    _start_stand_ins(monkeypatch, root, events, opened)
    real_sign = windows_start.sign

    @contextmanager
    def sign():
        with real_sign() as shown:
            signs.append(shown)
            yield shown
    monkeypatch.setattr(windows_start, "sign", sign)
    first = threading.Thread(target=lambda: results.append(asyncio.run(consumer.launch(root))))
    first.start()
    try:
        _until(lambda: "open command" in events, "The first start did not reach its browser command.", 60)
        # After the certificate step, while the browser command runs: the sign is on the screen.
        (shown,) = signs
        window = shown.window
        assert window and user.IsWindowVisible(window) and user.text(shown.label) == "Amplifier is starting."
        # The second click.
        second = asyncio.run(consumer.launch(root))
        assert second == {"browser": {"opened": False, "reason": "start-in-progress"}}
        assert events == ["start step", "open command"] and len(signs) == 1
        assert user.IsWindowVisible(window)
    finally:
        opened.set()
        first.join(60)
    assert results == [{"background": True, "created": False, "health": {"ok": True}, "browser": {"opened": True}}]
    assert events == ["start step", "open command"]  # One start step and one browser command; no message.
    assert not user.IsWindow(window)
    # The first start has ended: the next click starts Amplifier again.
    again = asyncio.run(consumer.launch(root))
    assert again["browser"] == {"opened": True} and events == ["start step", "open command"] * 2


@pytest.fixture
def installation_lock(monkeypatch):
    """The installation's real lock on Windows; elsewhere a lock with the same meaning."""
    from amplifier_web import windows_uninstall
    if os.name != "nt":
        locks = {}

        @contextmanager
        def single(root, *, wait=10.0):
            lock = locks.setdefault(str(root), threading.Lock())
            if not lock.acquire(timeout=wait):
                raise windows_uninstall.Busy()
            try:
                yield
            finally:
                lock.release()
        monkeypatch.setattr(windows_uninstall, "single", single)
    return windows_uninstall.single


def test_setups_entries_wait_for_an_uninstall_and_are_not_added_for_the_installation_that_it_deleted(
        tmp_path, monkeypatch, installation_lock):
    """A second setup run in the seconds in which an uninstall deletes the folder (follow-up B7).

    The uninstall holds the installation's lock while it removes the entries
    and then deletes the launcher's records. Without the lock around setup's
    step, that step read the records while they were still there and added
    the entries again, for an installation that was deleted a moment later.
    """
    from amplifier_web import windows_consumer_shell
    from amplifier_web.windows_files import address
    root = address(tmp_path / "installation")
    root.mkdir()
    record = root / consumer.RECEIPT
    record.write_text("{}")

    def installation(folder):
        json.loads(record.read_text())  # The launcher's record: gone when the uninstall deleted it.
        return root, {"version": "1.0.0"}
    monkeypatch.setattr(consumer, "installation", installation)
    monkeypatch.setattr(consumer, "delivery_proof", lambda home: {"audited": True})
    added, removing, times = [], threading.Event(), {}
    monkeypatch.setattr(windows_consumer_shell, "install", lambda *args: added.append(args) or {"shortcuts": 4})

    def uninstall():
        with installation_lock(root):  # Taken and released on this one thread, as the uninstall does.
            removing.set()             # Its entries are removed.
            time.sleep(1.5)            # The seconds between.
            record.unlink()            # The launcher's records are deleted.
            times["deleted"] = time.monotonic()
    running = threading.Thread(target=uninstall)
    running.start()
    try:
        assert removing.wait(30)
        with pytest.raises(FileNotFoundError):
            consumer.integrate(root)
        times["answered"] = time.monotonic()
    finally:
        running.join(30)
    assert added == []  # No Start-menu or Apps-list entry for the deleted installation.
    assert times["answered"] >= times["deleted"]  # Setup's step waited for the uninstall.
    # With no uninstall at work, and the records whole, the entries are added.
    record.write_text("{}")
    assert consumer.integrate(root) == {"shortcuts": 4} and added == [(root, "1.0.0")]


@NATIVE
def test_native_start_texts_over_real_private_files_before_and_after_a_removal_began():
    """What the start texts read from the folder, with the real private files and the real journal writer."""
    from amplifier_web import windows_files, windows_uninstall as plan
    from amplifier_web.deployment import write_private
    root = windows_files.address(Path.home() / ("au-start-text-" + uuid.uuid4().hex[:12]))
    with windows_files.directory(root, owned_root=root, create=True):
        pass
    promise = "run the Amplifier setup file again. It keeps your chats and settings"
    try:
        # A folder that setup did not complete: the program does not know what a setup run finds.
        assert consumer._setup_again(root) == "run the Amplifier setup file again"
        write_private(root / plan.COMPLETE, json.dumps({"schema": 1, "version": "0.106.0"}), owned_root=root)
        assert plan.completed(root) and consumer._setup_again(root) == "run the Amplifier setup file again"
        # A completed installation with the app's folder: a second setup run keeps what is there.
        with windows_files.directory(root / "app", owned_root=root, create=True):
            pass
        assert consumer._setup_again(root) == promise
        assert consumer.start_failure(root) == "Amplifier could not start.\n\nRestart Windows. Then " + promise + "."
        # An uninstall that deletes the folder begins: its journal, by its own writer.
        plan.begin(root)
        assert plan.unfinished(root) and consumer._setup_again(root) == "run the Amplifier setup file again"
        text = consumer.start_failure(root)
        assert text == consumer._UNFINISHED["removing"]
        assert "setup file" not in text and "keeps your chats" not in text
    finally:
        shutil.rmtree(root, ignore_errors=True)
