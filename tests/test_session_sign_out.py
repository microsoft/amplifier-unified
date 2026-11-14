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
           "service": SimpleNamespace(data_dir=home), "session_epoch": 0, **values}
    return app


def session(issued):
    return auth._signed(SECRET, {"kind": "session", "issued": issued, "nonce": "n"})


def test_session_from_before_the_sign_out_is_refused_and_a_later_one_accepted(monkeypatch):
    now = 1_900_000_000
    monkeypatch.setattr(auth.time, "time", lambda: now)
    app = application(None, session_epoch=now - 10)
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
    app = application(home, browser_entry=store, session_epoch_hint=auth._epoch_hint(home))
    before = session(1_900_000_050)
    assert auth.valid_session(app, before)
    assert auth.refresh_sessions(app, streams) is False and cancelled == []  # Nothing changed yet.
    auth.sign_out_all(home)  # The owner's command, in another process.
    assert auth.refresh_sessions(app, streams) is True
    assert app["session_epoch"] == 1_900_000_100 and cancelled == ["stream"]
    assert not auth.valid_session(app, before)
    # A ticket issued before the sign-out cannot produce a session after it.
    assert store.consume(ticket, browser_entry.Binding("i", "d", "https://127.0.0.1:1", "S-1-5-21-1-2-3-1001")) is None
    # Applied once: no repeated stream cancellation while nothing changes.
    assert auth.refresh_sessions(app, streams) is False and cancelled == ["stream"]
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_200)
    assert auth.valid_session(app, session(1_900_000_150))  # A new sign-in works.


def test_watcher_keeps_running_after_a_fault_and_stops_when_cancelled(home, monkeypatch):
    calls = []
    def refresh(app, streams):
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("one fault")
    monkeypatch.setattr(auth, "refresh_sessions", refresh)
    async def run():
        task = asyncio.create_task(auth.watch_sessions(application(home), set(), interval=0.01))
        while len(calls) < 3:
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return task.cancelled()
    assert asyncio.run(run()) is True and len(calls) >= 3


def test_real_middleware_refuses_a_signed_out_cookie(home, monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1_900_000_100)
    app = web.Application()
    app.update(application(home, session_epoch=1_900_000_050, control_token="c" * 64,
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
