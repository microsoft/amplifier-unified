"""Issue 1701: raw OS evidence and the real CLI's startup side-effect boundary."""
import asyncio
import errno
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import subprocess
import sys
import time

from aiohttp import web
import pytest

from amplifier_web import cli, server
from amplifier_web.deployment import load_server_config, save_server_config
from amplifier_web.session_files import capture_sessions_dir
from amplifier_web.tls import setup_local_ca
from test_onboarding_listener_readiness import _minimal_cli
from test_service import Runtime


MISSING = "192.0.2.254"


def _missing_bind(monkeypatch):
    """Make only this numeric bind unavailable, preserving real other sockets."""
    original = socket.socket.bind

    def bind(sock, address):
        if isinstance(address, tuple) and address[0] == MISSING:
            raise OSError(errno.EADDRNOTAVAIL, "synthetic unavailable address")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "bind", bind)


def _configure(tmp_path, binds):
    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    config = save_server_config(tmp_path, {
        **config, "bind": binds, "public_origins": ["https://fixture.invalid:8941"],
    })
    return config


async def test_real_linux_missing_numeric_address_loses_errno():
    # No interface/config mutation. An unexpected nonlocal-bind policy is a gap,
    # not permission to claim this kernel witnessed an unavailable address.
    with socket.socket() as raw:
        try:
            raw.bind((MISSING, 0))
        except OSError as exc:
            assert exc.errno == errno.EADDRNOTAVAIL
        else:
            pytest.skip("TEST-NET address is bindable in this environment")
    loop = asyncio.get_running_loop()
    with pytest.raises(OSError) as failure:
        await loop.create_server(asyncio.Protocol, MISSING, 0)
    assert failure.value.errno is None
    print("real asyncio missing bind:", repr(failure.value))


@pytest.mark.parametrize("binds", [[MISSING, "127.0.0.1"], ["127.0.0.1", MISSING]])
async def test_aiohttp_bind_order_runs_startup_then_cleans_up_all_sites(monkeypatch, binds):
    _missing_bind(monkeypatch)
    events = []
    app = web.Application()

    async def startup(app):
        events.append("startup")

    async def cleanup(app):
        events.append("cleanup")

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        for host in binds:
            await web.TCPSite(runner, host, 0).start()
            events.append("bound:" + host)
        pytest.fail("Unavailable configured address unexpectedly bound")
    except OSError as exc:
        assert exc.errno is None
        assert events[0] == "startup"
    finally:
        await runner.cleanup()
    assert events[-1] == "cleanup"
    assert not runner.sites
    assert events == (["startup", "cleanup"] if binds[0] == MISSING else
                      ["startup", "bound:127.0.0.1", "cleanup"])
    print("aiohttp ordering:", events)


@pytest.mark.parametrize("binds", [[MISSING], [MISSING, "127.0.0.1"], ["127.0.0.1", MISSING]])
def test_cli_reports_unavailable_numeric_bind_without_raw_oserror(tmp_path, monkeypatch, capsys, binds):
    _minimal_cli(monkeypatch, tmp_path)
    _missing_bind(monkeypatch)
    _configure(tmp_path, binds)
    monkeypatch.setattr(cli.threading, "Timer", lambda *a, **kw: pytest.fail("Browser before bind"))
    with pytest.raises(SystemExit, match="not available on this computer"):
        cli._serve_unlocked(cli._parse(), tmp_path)
    assert "listening on" not in capsys.readouterr().out


def test_real_app_cli_failed_bind_creates_no_app_capture(tmp_path, monkeypatch):
    create_app = server.create_app
    _minimal_cli(monkeypatch, tmp_path)
    _missing_bind(monkeypatch)
    _configure(tmp_path, [MISSING])
    created = []

    async def real_app(*args, **kwargs):
        app = await create_app(
            *args, **kwargs, runtime=Runtime(), voice=False,
            preload_providers=False, background_updates=False,
        )
        created.append(app)
        return app

    monkeypatch.setattr(server, "create_app", real_app)
    with pytest.raises((OSError, SystemExit)):
        cli._serve_unlocked(cli._parse(), tmp_path)
    captures = list(capture_sessions_dir(str(tmp_path)).glob("unified-*/context-intelligence/metadata.json"))
    records = [r for app in created for r in app["service"].diagnostics.read()["items"]]
    print("failed real-app CLI bind:", {"captures": len(captures), "events": [r["event"] for r in records]})
    assert not captures
    assert not created


def test_failed_public_cli_has_no_startup_work_or_state_writes_beyond_repair_lock(tmp_path, monkeypatch):
    _minimal_cli(monkeypatch, tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    _missing_bind(monkeypatch)
    _configure(tmp_path, ["127.0.0.1", MISSING])

    def forbidden(*a, **kw):
        pytest.fail("failed bind entered application startup")

    monkeypatch.setattr("amplifier_web.settings_migration.migrate_settings", forbidden)
    monkeypatch.setattr("amplifier_web.host.config.load_config", forbidden)
    monkeypatch.setattr(server, "create_app", forbidden)
    monkeypatch.setattr(cli.threading, "Timer", forbidden)

    def snapshot():
        return {str(path.relative_to(tmp_path)): (
            hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            if path.is_file() else None
            for path in tmp_path.rglob("*") if path.name != ".reset.lock"}

    before = snapshot()
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        with pytest.raises(SystemExit, match="not available on this computer"):
            cli.main()
        timings.append(round((time.perf_counter() - started) * 1000, 3))
    assert snapshot() == before
    assert (tmp_path / ".reset.lock").read_bytes() == b""
    assert not capture_sessions_dir(str(tmp_path)).exists()
    print("three failed public CLI attempts ms (measurement, not native cadence):", timings)


@pytest.mark.skipif(os.name != "posix", reason="Owned subprocess SIGINT fixture is POSIX only")
async def test_recovered_public_cli_queues_tcp_but_only_authenticated_https_proves_ready(tmp_path, monkeypatch):
    from amplifier_web import __version__, installation_health as health, update_readiness
    from amplifier_web.auth import control_token

    # First invocation fails before the app factory; next explicit invocation
    # uses the same isolated state with an available configured bind.
    home, workspace = tmp_path / "data", tmp_path / "workspace"
    workspace.mkdir()
    config = _configure(home, [MISSING])
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "HOME": str(tmp_path / "user-home"),
           "PYTHONPATH": os.pathsep.join((str(root), str(root / "tests")))}
    script = '''
import asyncio, json, sys
from amplifier_web import cli, server
from test_service import Runtime
original = server.create_app
created = []
async def app(*args, **kwargs):
    print("constructing", flush=True)
    await asyncio.to_thread(sys.stdin.readline)
    value = await original(*args, **kwargs, runtime=Runtime(), voice=False,
                           preload_providers=False, background_updates=False)
    created.append(value)
    return value
server.create_app = app
cli.main()
print("closed:" + json.dumps({
    "events": [r["event"] for app in created for r in app["service"].diagnostics.read()["items"]],
    "runtimeStarted": [s for app in created for s in app["service"].runtime.started],
}), flush=True)
'''
    command = [sys.executable, "-u", "-c", script, "--data-dir", str(home),
               "--workspace", str(workspace), "--no-open", "serve"]
    # Use the real numeric Linux failure, not the controlled bind monkeypatch.
    with socket.socket() as raw:
        try:
            raw.bind((MISSING, 0))
        except OSError as exc:
            assert exc.errno == errno.EADDRNOTAVAIL
        else:
            pytest.skip("TEST-NET address is bindable in this environment")
    failed = subprocess.run(command, input="", text=True, capture_output=True, env=env, cwd=root, timeout=20)
    assert failed.returncode != 0
    assert "not available on this computer" in failed.stderr
    assert "Traceback" not in failed.stderr and "constructing" not in failed.stdout
    assert not capture_sessions_dir(str(workspace)).exists()
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    save_server_config(home, {**config, "bind": ["127.0.0.1"], "port": port})
    token = control_token(home)
    monkeypatch.setattr(health, "service_state", lambda home: "running")
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"version": __version__, "revision": None})
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=env, cwd=root)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(20), "child did not reach construction"
            assert child.stdout.readline().strip() == "constructing"
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass  # TCP succeeds before app construction; this is NOT health.
        during = await health.status(home)
        assert not during["ok"] and not during["responding"]
        assert during["listenerChecks"] and not any(row["verified"] for row in during["listenerChecks"])
        child.stdin.write("continue\n")
        child.stdin.flush()
        ready = await health.status(home, wait=15)
        assert ready["ok"] and ready["responding"]
        assert ready["listenerUrls"] == [f"https://127.0.0.1:{port}"]
        assert token not in json.dumps(ready)
        child.send_signal(signal.SIGINT)
        output, errors = child.communicate(timeout=20)
        assert child.returncode == 0, errors
        assert "listening on 127.0.0.1" in output
        summary = json.loads(output.split("closed:", 1)[1])
        assert summary["events"].count("app:started") == 1
        assert summary["runtimeStarted"] == []
        assert len(list(capture_sessions_dir(str(workspace)).glob("unified-*/context-intelligence/metadata.json"))) == 1
        print("real recovered CLI: TCP queued before health; pinned authenticated HTTPS ready; one app start; normal SIGINT cleanup")
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
            try:
                child.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate(timeout=5)