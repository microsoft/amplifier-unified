"""Sign out all browsers: one saved time that every session check honors.

Runs on Linux and, in the focused Windows job, on the real private store.
Synchronous on purpose: that job installs no async pytest plugin.
"""
import asyncio
import os
from pathlib import Path
import re
import shutil
import ssl
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import aiohttp
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import pytest

from amplifier_web import auth, browser_entry, cli

ROOT = Path(__file__).resolve().parents[1]
SECRET = "s" * 64


@pytest.fixture
def home(tmp_path_factory):
    # Native Windows refuses a runner temp folder with a broad inherited ACL;
    # use a private profile child there instead of repairing or skipping.
    if os.name != "nt":
        yield tmp_path_factory.mktemp("home")
        return
    from amplifier_web.windows_files import address, directory
    root = address(Path.home() / (".unified-signout-test-" + uuid.uuid4().hex))
    with directory(root, owned_root=root, create=True):
        pass
    try:
        yield root
    finally:
        shutil.rmtree(root)


def application(home, **values):
    app = {"session_secret": SECRET, "server_config": {"session_ttl_seconds": 600},
           "service": SimpleNamespace(data_dir=home), "sessions": auth.Sessions(), **values}
    return app


def session(issued):
    return auth._signed(SECRET, {"kind": "session", "issued": issued, "nonce": "n"})


def changed_look(monkeypatch, home, result):
    """Replace what a look at the saved record finds; return a function that ends it.

    `result` gets the real file information. It returns what the look finds,
    or it raises. Only the look is changed: saves and reads stay real.
    """
    record, real = auth._epoch_path(home), os.stat

    def stat(path, *args, **kwargs):
        if isinstance(path, (str, os.PathLike)) and Path(path) == record:
            return result(lambda: real(path, *args, **kwargs))
        return real(path, *args, **kwargs)
    monkeypatch.setattr(auth.os, "stat", stat)
    return lambda: monkeypatch.setattr(auth.os, "stat", real)


def look_fails(monkeypatch, home):
    """The look fails, for example because another program holds the file. Not: the record is missing."""
    def fail(info):
        raise PermissionError("in use")
    return changed_look(monkeypatch, home, fail)


def test_session_from_before_the_sign_out_is_refused_and_a_later_one_accepted(monkeypatch):
    now = 1_900_000_000
    monkeypatch.setattr(auth.time, "time", lambda: now)
    app = application(None, sessions=auth.Sessions(now - 10))
    assert not auth.valid_session(app, session(now - 11))
    assert not auth.valid_session(app, session(now - 10))  # The same second is refused too.
    assert auth.valid_session(app, session(now - 9))
    # Lifetime, signature and kind still apply.
    assert not auth.valid_session(app, session(now - 601))
    assert not auth.valid_session(app, session(now - 9)[:-1] + "0")
    assert not auth.valid_session(app, auth._signed(SECRET, {"kind": "csrf", "issued": now, "nonce": "n"}))
    assert not auth.valid_session(app, None)
    # No sign-out yet: every valid session is accepted, as before this change.
    assert auth.valid_session(application(None), session(now - 500))


def test_every_session_check_in_the_package_uses_the_one_shared_check():
    # A separate cookie check would silently ignore "sign out all browsers".
    offenders = []
    for path in (ROOT / "amplifier_web").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name != "auth.py" and re.search(r"_(verified|issued)\([^)]*['\"]session['\"]", text):
            offenders.append(path.name)
    assert offenders == []
    for name in ("device_pairing.py", "device_connections.py"):
        assert "valid_session(" in (ROOT / "amplifier_web" / name).read_text(encoding="utf-8")
    body = (ROOT / "amplifier_web/auth.py").read_text(encoding="utf-8")
    assert body.count('"session")') == 1  # Only inside valid_session itself.


def test_sign_out_saves_a_time_that_never_moves_backward(home, monkeypatch):
    assert auth.session_epoch(home) == 0  # No sign-out yet.
    clock = [1_900_000_000]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    assert auth.sign_out_all(home) == 1_900_000_000
    assert auth.session_epoch(home) == 1_900_000_000
    clock[0] = 1_900_000_050
    assert auth.sign_out_all(home) == 1_900_000_050
    clock[0] = 1_800_000_000  # The PC clock was set back.
    assert auth.sign_out_all(home) == 1_900_000_050
    assert auth.session_epoch(home) == 1_900_000_050


def test_saved_time_is_in_the_owner_private_store(home):
    auth.sign_out_all(home)
    path = auth._epoch_path(home)
    assert path.name == "session-epoch" and path.parent.name == "auth"
    if os.name == "nt":
        from amplifier_web.windows_files import directory
        with directory(path.parent, owned_root=home) as pinned:
            assert re.fullmatch(rb"[0-9]+\n", pinned.read_private("session-epoch", limit=32))
    else:
        assert path.stat().st_mode & 0o777 == 0o600


def test_malformed_record_signs_everyone_out_once_and_is_repaired(home, monkeypatch):
    auth.sign_out_all(home)
    monkeypatch.setattr(auth.time, "time", lambda: 1_950_000_000)
    auth._write_epoch(home, 5)
    path = auth._epoch_path(home)
    if os.name == "nt":
        from amplifier_web.windows_files import directory
        with directory(path.parent, owned_root=home) as pinned:
            pinned.publish_private("session-epoch", b"not a time\n", replace=True)
    else:
        path.write_bytes(b"not a time\n")
    # Never revives a session: the present moment replaces the record.
    assert auth.session_epoch(home) == 1_950_000_000
    assert auth._read_epoch(home) == 1_950_000_000
    # Repaired once: a later read does not move the time again.
    monkeypatch.setattr(auth.time, "time", lambda: 1_950_000_500)
    assert auth.session_epoch(home) == 1_950_000_000


def test_failed_save_is_an_error_never_a_silent_success(home, monkeypatch):
    monkeypatch.setattr(auth, "_write_epoch", lambda *a: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(OSError):
        auth.sign_out_all(home)
    monkeypatch.undo()
    monkeypatch.setattr(auth, "_write_epoch", lambda *a: None)  # Reports success but saves nothing.
    with pytest.raises(OSError, match="not saved"):
        auth.sign_out_all(home)


def test_running_host_applies_the_sign_out_and_ends_open_streams(home, monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    store = browser_entry.GrantStore()
    ticket = store.issue(browser_entry.Binding("i", "d", "https://127.0.0.1:1", "S-1-5-21-1-2-3-1001"))
    cancelled = []
    streams = [SimpleNamespace(cancel=lambda: cancelled.append("stream"))]
    app = application(home, browser_entry=store, sessions=auth.Sessions.load(home))
    before = session(1_900_000_050)
    assert auth.valid_session(app, before)
    assert auth.refresh_sessions(app, streams) is False and cancelled == []  # Nothing changed yet.
    auth.sign_out_all(home)  # The owner's command, in another process.
    assert auth.refresh_sessions(app, streams) is True
    assert app["sessions"].epoch == 1_900_000_100 and cancelled == ["stream"]
    assert not auth.valid_session(app, before)
    # A ticket issued before the sign-out cannot produce a session after it.
    assert store.consume(ticket, browser_entry.Binding("i", "d", "https://127.0.0.1:1", "S-1-5-21-1-2-3-1001")) is None
    # Applied once: no repeated stream cancellation while nothing changes.
    assert auth.refresh_sessions(app, streams) is False and cancelled == ["stream"]
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_200)
    assert auth.valid_session(app, session(1_900_000_150))  # A new sign-in works.


def test_ticket_or_code_opens_no_session_until_the_host_has_applied_a_saved_sign_out(home, monkeypatch):
    clock = [1_900_000_100]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    app = application(home, sessions=auth.Sessions.load(home))
    assert auth.valid_session(app, auth.sign_in(app)) and auth.valid_session(app, auth.sign_in(app, remote=True))
    auth.sign_out_all(home)                                 # The owner's command, in another process.
    clock[0] += 1                                           # A second later. The host has not looked yet.
    assert auth.sign_in(app) is None and auth.sign_in(app, remote=True) is None
    assert app["sessions"].epoch == 0                       # Only looked at: the watcher applies it.
    assert auth.refresh_sessions(app) is True
    assert auth.valid_session(app, auth.sign_in(app))       # A sign-in after the sign-out works.


def test_sign_in_reads_the_session_second_before_it_looks_at_the_saved_record(home, monkeypatch):
    # The other order leaves a gap: a sign-out that is saved between the look and
    # the clock read would be missed, and the session could carry a later second.
    events = []
    look = auth._epoch_hint
    monkeypatch.setattr(auth.time, "time", lambda: events.append("second") or 1_900_000_100)
    monkeypatch.setattr(auth, "_epoch_hint", lambda data_dir: events.append("record") or look(data_dir))
    assert auth.sign_in(application(home)) is not None and events == ["second", "record"]


def test_session_made_while_a_sign_out_is_being_saved_does_not_outlast_it(home, monkeypatch):
    """The sign-out starts in one second and its record is there in the next one."""
    clock = [1_900_000_100]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    app = application(home, sessions=auth.Sessions.load(home))
    write, made = auth._write_epoch, []

    def write_across_a_second(data_dir, value):
        if not made:
            clock[0] += 1                                   # The second changes before the record is there.
            made.append(auth.sign_in(app))                  # The host signs a code in: no record yet.
        write(data_dir, value)
    monkeypatch.setattr(auth, "_write_epoch", write_across_a_second)
    hints, save = [], auth._write_epoch

    def recorded(data_dir, value):
        save(data_dir, value)
        hints.append(auth._epoch_hint(data_dir))
    monkeypatch.setattr(auth, "_write_epoch", recorded)
    pause = auth.time.sleep
    monkeypatch.setattr(auth.time, "sleep", lambda seconds: hints.append("pause") or pause(seconds))
    assert auth.sign_out_all(home) == 1_900_000_101         # Saved again, with the later second.
    assert made[0] is not None and auth.valid_session(app, made[0])
    # The host tells the two saves apart: a pause separates their file times.
    assert len(hints) == 3 and hints[1] == "pause" and hints[0] != hints[2]
    assert auth.refresh_sessions(app) is True
    assert not auth.valid_session(app, made[0])


def test_record_that_the_host_could_not_read_keeps_sign_in_closed_and_is_read_again(home, monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    app = application(home, sessions=auth.Sessions.load(home))
    auth.sign_out_all(home)
    read = auth.session_epoch
    monkeypatch.setattr(auth, "session_epoch", lambda data_dir: (_ for _ in ()).throw(RuntimeError("a fault")))
    with pytest.raises(RuntimeError):
        auth.refresh_sessions(app)
    assert app["sessions"].epoch == 0 and auth.sign_in(app) is None     # Not applied, so nothing opens.
    monkeypatch.setattr(auth, "session_epoch", read)
    assert auth.refresh_sessions(app) is True and app["sessions"].epoch == 1_900_000_100
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_101)
    assert auth.valid_session(app, auth.sign_in(app))


def test_sign_out_that_is_never_saved_within_one_second_is_reported_as_uncertain(home, monkeypatch, capsys):
    clock = [1_900_000_100]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    write = auth._write_epoch

    def slow_write(data_dir, value):
        write(data_dir, value)
        clock[0] += 1
    monkeypatch.setattr(auth, "_write_epoch", slow_write)
    with pytest.raises(auth.SignOutUncertain):
        auth.sign_out_all(home)
    assert auth._read_epoch(home) == 1_900_000_102          # Three saves; each ended one second late.
    with pytest.raises(SystemExit) as stopped:
        run_command(home, monkeypatch)
    assert "Run the command again" in str(stopped.value) and "Nothing was changed" not in str(stopped.value)
    assert "could not confirm" in str(stopped.value) and capsys.readouterr().out == ""


def test_save_that_fails_after_an_earlier_save_of_the_same_run_is_not_reported_as_no_change(home, monkeypatch, capsys):
    clock = [1_900_000_100]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    write, saves = auth._write_epoch, []

    def slow_then_denied(data_dir, value):
        if saves:
            raise PermissionError("denied")
        write(data_dir, value)
        saves.append(value)
        clock[0] += 1                                       # The first save ends one second late.
    monkeypatch.setattr(auth, "_write_epoch", slow_then_denied)
    with pytest.raises(auth.SignOutUncertain):
        auth.sign_out_all(home)
    assert auth._read_epoch(home) == 1_900_000_100          # The first save is on disk.
    saves.clear()
    with pytest.raises(SystemExit) as stopped:
        run_command(home, monkeypatch)
    assert "could not confirm" in str(stopped.value) and "Run the command again" in str(stopped.value)
    assert "Nothing was changed" not in str(stopped.value) and capsys.readouterr().out == ""


def test_first_save_that_is_on_disk_but_not_confirmed_is_not_reported_as_no_change(home, monkeypatch, capsys):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    read, reads = auth._read_epoch, []

    def read_fails_after_the_save(data_dir):
        reads.append(1)
        if len(reads) % 2 == 0:                             # The read-back, after the save.
            raise PermissionError("in use")
        return read(data_dir)
    monkeypatch.setattr(auth, "_read_epoch", read_fails_after_the_save)
    with pytest.raises(auth.SignOutUncertain):
        auth.sign_out_all(home)
    assert read(home) == 1_900_000_100                      # The record did change.
    with pytest.raises(SystemExit) as stopped:
        run_command(home, monkeypatch)
    assert "could not confirm" in str(stopped.value) and "Nothing was changed" not in str(stopped.value)
    assert capsys.readouterr().out == ""


def test_second_sign_out_in_the_same_second_still_drops_a_ticket_and_a_code_made_in_between(home, monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    tickets, codes = browser_entry.GrantStore(), browser_entry.CodeStore()
    cancelled = []
    streams = [SimpleNamespace(cancel=lambda: cancelled.append("stream"))]
    app = application(home, browser_entry=tickets, browser_codes=codes, sessions=auth.Sessions.load(home))
    auth.sign_out_all(home)
    assert auth.refresh_sessions(app, streams) is True and cancelled == ["stream"]
    binding = browser_entry.Binding("i", "d", "https://pc.example:1", "S-1-5-21-1-2-3-1001")
    ticket, code = tickets.issue(binding), codes.issue(binding)    # Made after the first sign-out.
    assert auth.sign_out_all(home) == 1_900_000_100         # The owner signs out again in the same second.
    assert auth.sign_in(app) is None                        # The host has not looked yet.
    assert auth.refresh_sessions(app, streams) is False     # No newer time ...
    assert not tickets._grants and not codes._codes         # ... and both are dropped all the same.
    assert tickets.consume(ticket, binding) is None and codes.consume(code, binding) is False
    assert app["sessions"].epoch == 1_900_000_100 and cancelled == ["stream"]
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_102)
    assert auth.valid_session(app, auth.sign_in(app))       # A sign-in after both works.


def test_host_tells_two_saves_apart_by_the_file_itself_when_time_and_size_are_the_same(home, monkeypatch):
    # The file time never changes here. The size and the identity are the real ones.
    changed_look(monkeypatch, home, lambda info: SimpleNamespace(
        st_mtime_ns=5, st_size=info().st_size, st_ino=info().st_ino))
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    codes = browser_entry.CodeStore()
    app = application(home, browser_codes=codes, sessions=auth.Sessions.load(home))
    auth.sign_out_all(home)
    first = auth._epoch_hint(home)
    assert auth.refresh_sessions(app) is True
    codes.issue(browser_entry.Binding("i", "d", "https://pc.example:1", "S-1-5-21-1-2-3-1001"))
    assert auth.sign_out_all(home) == 1_900_000_100         # Again, in the same second.
    second = auth._epoch_hint(home)
    assert first[:2] == second[:2] and first[2] and second[2] and first != second  # Only the file itself differs.
    assert auth.sign_in(app) is None                        # The host has not looked yet.
    assert auth.refresh_sessions(app) is False and not codes._codes


def test_failed_save_with_a_record_that_cannot_be_looked_at_is_not_reported_as_no_change(home, monkeypatch, capsys):
    auth.sign_out_all(home)
    look_fails(monkeypatch, home)                           # Before the save and after it.
    monkeypatch.setattr(auth, "_write_epoch", lambda *a: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(SystemExit) as stopped:
        run_command(home, monkeypatch)
    assert "could not confirm" in str(stopped.value) and "Nothing was changed" not in str(stopped.value)
    assert capsys.readouterr().out == ""


def test_look_at_the_record_that_fails_is_never_no_record_and_never_unchanged(home, monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    tickets, codes = browser_entry.GrantStore(), browser_entry.CodeStore()
    app = application(home, browser_entry=tickets, browser_codes=codes, sessions=auth.Sessions.load(home))
    assert auth._epoch_hint(home) is None and app["sessions"].hint is None      # No record: no sign-out yet.
    binding = browser_entry.Binding("i", "d", "https://pc.example:1", "S-1-5-21-1-2-3-1001")
    tickets.issue(binding)
    codes.issue(binding)
    auth.sign_out_all(home)                                 # The first sign-out of this installation.
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_101)
    again = look_fails(monkeypatch, home)
    assert auth._epoch_hint(home) is auth._UNSEEN
    assert auth.sign_in(app) is None and auth.sign_in(app, remote=True) is None
    # The watcher applies nothing and keeps nothing from a look that failed, and it says so.
    with pytest.raises(OSError, match="could not be looked at"):
        auth.refresh_sessions(app)
    assert app["sessions"].hint is None and app["sessions"].epoch == 0 and tickets._grants and codes._codes
    again()
    assert auth.refresh_sessions(app) is True and app["sessions"].epoch == 1_900_000_100
    assert not tickets._grants and not codes._codes
    assert auth.valid_session(app, auth.sign_in(app))


def test_host_that_could_not_look_at_the_record_at_its_start_opens_nothing_until_it_can(home, monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    auth.sign_out_all(home)
    again = look_fails(monkeypatch, home)
    app = application(home, sessions=auth.Sessions.load(home))
    assert app["sessions"].hint is auth._UNSEEN and app["sessions"].epoch == 1_900_000_100
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_105)
    assert auth.sign_in(app) is None                        # Two looks that failed are not "the same record".
    again()
    assert auth.sign_in(app) is None                        # Not yet: the host itself has not looked with success.
    assert auth.refresh_sessions(app) is False and app["sessions"].hint == auth._epoch_hint(home)
    assert auth.valid_session(app, auth.sign_in(app))


def test_first_save_whose_read_back_and_look_both_fail_is_not_reported_as_no_change(home, monkeypatch, capsys):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    read, reads = auth._read_epoch, []

    def read_back_fails(data_dir):
        reads.append(1)
        if len(reads) == 2:                                 # The read-back, after the save.
            look_fails(monkeypatch, home)                   # The look after it fails as well.
            raise PermissionError("in use")
        return read(data_dir)
    monkeypatch.setattr(auth, "_read_epoch", read_back_fails)
    with pytest.raises(SystemExit) as stopped:              # There was no record before the command.
        run_command(home, monkeypatch)
    assert "could not confirm" in str(stopped.value) and "Nothing was changed" not in str(stopped.value)
    assert read(home) == 1_900_000_100 and capsys.readouterr().out == ""    # The record was saved.


def test_save_that_the_command_cannot_look_at_afterwards_is_not_reported_as_done(home, monkeypatch, capsys):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    monkeypatch.setattr(auth.time, "sleep", lambda seconds: None)
    auth.sign_out_all(home)                                 # A record is there.
    write = auth._write_epoch

    def look_fails_after_the_save(data_dir, value):
        write(data_dir, value)
        look_fails(monkeypatch, home)
    monkeypatch.setattr(auth, "_write_epoch", look_fails_after_the_save)
    # The save and the read-back work. The command cannot see what the host will see.
    with pytest.raises(auth.SignOutUncertain):
        auth.sign_out_all(home)
    with pytest.raises(SystemExit) as stopped:
        run_command(home, monkeypatch)
    assert "could not confirm" in str(stopped.value) and capsys.readouterr().out == ""


def test_save_that_the_host_could_not_tell_from_the_record_before_it_is_not_a_finished_sign_out(home, monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    monkeypatch.setattr(auth.time, "sleep", lambda seconds: None)
    look, looks = auth._epoch_hint, []
    # The first save looks like the record before it; the second one does not.
    monkeypatch.setattr(auth, "_epoch_hint", lambda data_dir: (
        looks.append(1) or ("the same" if len(looks) <= 2 else look(data_dir))))
    assert auth.sign_out_all(home) == 1_900_000_100 and len(looks) == 4     # Saved twice.
    monkeypatch.setattr(auth, "_epoch_hint", lambda data_dir: "the same")
    with pytest.raises(auth.SignOutUncertain):
        auth.sign_out_all(home)


def test_record_that_cannot_be_read_signs_everyone_out_once_and_drops_tickets_and_codes(home, monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    tickets, codes = browser_entry.GrantStore(), browser_entry.CodeStore()
    app = application(home, browser_entry=tickets, browser_codes=codes, sessions=auth.Sessions.load(home))
    binding = browser_entry.Binding("i", "d", "https://pc.example:1", "S-1-5-21-1-2-3-1001")
    tickets.issue(binding)
    codes.issue(binding)
    auth.sign_out_all(home)
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_500)
    monkeypatch.setattr(auth, "_read_epoch", lambda data_dir: (_ for _ in ()).throw(PermissionError("in use")))
    monkeypatch.setattr(auth, "_write_epoch", lambda *a: (_ for _ in ()).throw(PermissionError("in use")))
    # Not readable and not repairable: the present moment is the sign-out time.
    assert auth.refresh_sessions(app) is True and app["sessions"].epoch == 1_900_000_500
    assert not tickets._grants and not codes._codes and not auth.valid_session(app, session(1_900_000_400))
    assert auth.refresh_sessions(app) is False              # Once: the record did not change again.
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_501)
    assert auth.valid_session(app, auth.sign_in(app))       # Sign-in is not closed for good.


def test_watcher_keeps_running_after_a_fault_and_stops_when_cancelled(home, monkeypatch, capsys):
    calls = []
    def refresh(app, streams):
        calls.append(len(calls))
        if len(calls) <= 2:
            raise RuntimeError("a fault")
    monkeypatch.setattr(auth, "refresh_sessions", refresh)
    async def run():
        task = asyncio.create_task(auth.watch_sessions(application(home), set(), interval=0.01))
        while len(calls) < 3:
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return task.cancelled()
    assert asyncio.run(run()) is True and len(calls) >= 3
    # Said once in the host log, not once per attempt and never in silence.
    assert capsys.readouterr().out == (
        "Sign-out of all browsers could not be checked: RuntimeError. "
        "Until it can be checked, no browser can sign in with a ticket, a code, a key or a password.\n")


def test_real_middleware_refuses_a_signed_out_cookie(home, monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    app = web.Application()
    app.update(application(home, sessions=auth.Sessions(1_900_000_050), control_token="c" * 64,
                           allowed_origins=frozenset({"https://127.0.0.1:8941"})))
    async def handler(request):
        return web.json_response({"ok": True})
    def call(cookie):
        async def run():
            protocol = Mock(_reading_paused=False)
            payload = aiohttp.StreamReader(protocol, 8192)
            payload.feed_eof()
            request = make_mocked_request(
                "GET", "/api/protected", app=app, payload=payload,
                sslcontext=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER),
                headers={"Host": "127.0.0.1:8941", "Cookie": f"{auth.SESSION_COOKIE}={cookie}"})
            return await auth.auth_required(request, handler)
        return asyncio.run(run())
    assert call(session(1_900_000_040)).status == 401   # From before the sign-out.
    assert call(session(1_900_000_060)).status == 200   # Signed in again afterwards.


def run_command(home, monkeypatch):
    from amplifier_web import application_generations, host_dependencies
    monkeypatch.setattr(host_dependencies, "prepare_environment", lambda: None)
    monkeypatch.setattr(application_generations, "delegate", lambda: None)
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--data-dir", str(home), "browsers", "sign-out"])
    return cli.main()


def test_real_command_signs_out_without_a_running_host_or_a_credential(home, monkeypatch, capsys):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_300)
    assert auth.session_epoch(home) == 0
    run_command(home, monkeypatch)
    assert auth.session_epoch(home) == 1_900_000_300
    output = capsys.readouterr().out
    assert "All browsers on all devices are signed out" in output and "within a few seconds" in output
    assert "open it from this computer" in output


def test_real_command_reports_a_failed_save_plainly(home, monkeypatch, capsys):
    monkeypatch.setattr(auth, "_write_epoch", lambda *a: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(SystemExit) as stopped:
        run_command(home, monkeypatch)
    assert "NOT signed out" in str(stopped.value) and "Nothing was changed" in str(stopped.value)
    assert capsys.readouterr().out == ""


def test_command_is_explicit_and_names_its_one_operation(home, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--data-dir", str(home), "browsers", "sign-out"])
    args = cli._parse()
    assert (args.command, args.browsers_command) == ("browsers", "sign-out")
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "browsers"])
    with pytest.raises(SystemExit):
        cli._parse()
