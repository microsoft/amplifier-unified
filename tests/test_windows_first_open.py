"""The launcher's open step with real processes, as far as a test host shows it.

On Windows an app generation takes over a CLI command with os.execv
(application_generations.delegate). The first cases measure what the caller
then learns, with a stand-in for that hand-over and the real launcher step.

The native cases run the real `open` command through such a hand-over against
this installation's host while that host restarts as another package version:
real private files, the real pinned HTTPS request, the real grant handler and
store. A recorder stands in for the browser (the BROWSER variable of Python's
webbrowser module), so no browser starts.

Not shown here: Task Scheduler, the update itself, the Windows certificate
question and a real browser. Those are clean-room steps.
"""
import asyncio
import hmac
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

import pytest

from amplifier_web import __version__, browser_entry as entry, windows_consumer as consumer

REPOSITORY = Path(__file__).resolve().parents[1]
ENVIRONMENT = {**os.environ, "PYTHONPATH": str(REPOSITORY)}
# The hand-over of application_generations.delegate: the same call, with the
# product's own argument list for it.
HANDOVER = """import os, sys
from amplifier_web.application_generations import exec_arguments
os.execv(sys.executable, exec_arguments([sys.executable, *sys.argv[1:]]))
"""
REFUSED = ("import sys\n"
           "sys.stderr.write('The owned local host is not reachable or its identity does not match.')\n"
           "raise SystemExit(1)\n")


def script(folder, name, text):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_command_that_another_program_takes_over_ends_with_status_0_on_windows(tmp_path):
    """The measured reason why the launcher does not read the exit status."""
    handover, failing = script(tmp_path, "handover.py", HANDOVER), script(tmp_path, "failing.py", REFUSED)
    done = subprocess.run([sys.executable, str(handover), str(failing), "open"],
                          capture_output=True, text=True, timeout=120, env=ENVIRONMENT)
    # The output of the program that took over still reaches the caller.
    assert "not reachable" in done.stderr and done.stdout == ""
    # Its exit status does not on Windows: the calling process ends at once
    # with 0. Elsewhere exec keeps the process, and the status is the result.
    assert done.returncode == (0 if os.name == "nt" else 1)


@pytest.mark.parametrize("child,opened", [
    (f"print({entry.OPENED!r}, flush=True)\n", True),
    ("print('Browser opening disabled; no browser grant was issued.', flush=True)\n", False),
    (f"print('Not ' + {entry.OPENED!r}, flush=True)\n", False),  # The whole line, not a part of one.
    ("", False),
    (REFUSED, False),
])
def test_the_launcher_step_reads_the_confirmation_line_not_the_exit_status(tmp_path, child, opened):
    handover, program = script(tmp_path, "handover.py", HANDOVER), script(tmp_path, "open.py", child)
    count = tmp_path / "runs.txt"
    counted = script(tmp_path, "counted.py", "import runpy, sys\n"
                     f"with open({str(count)!r}, 'a') as record:\n    record.write(sys.argv[-1] + '\\n')\n"
                     f"runpy.run_path({str(program)!r}, run_name='__main__')\n")
    result = asyncio.run(consumer.browser_opened((sys.executable, handover, counted), ENVIRONMENT))
    assert result is opened
    assert count.read_text().splitlines() == ["open"]  # The command ran once, as `open`.


def test_an_argument_with_a_space_reaches_the_program_that_takes_over_as_one_argument(tmp_path):
    """A user name such as "Jane Doe" is in the path of every command of the app."""
    show = script(tmp_path, "show.py", "import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    values = ["C:\\Users\\Jane Doe\\AppData\\Local\\Amplifier\\app", "", 'say "hello"', "open"]
    def received(program):
        done = subprocess.run([sys.executable, str(program), str(show), *values],
                              capture_output=True, text=True, timeout=120, env=ENVIRONMENT)
        return json.loads(done.stdout)
    assert received(script(tmp_path, "handover.py", HANDOVER)) == values
    # The reason for the product's own list: Windows joins a plain exec list
    # with spaces and quotes nothing. Measured here, not assumed.
    plain = script(tmp_path, "plain.py", "import os, sys\n"
                   "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n")
    if os.name == "nt":
        assert received(plain) == ["C:\\Users\\Jane", "Doe\\AppData\\Local\\Amplifier\\app", "say", "hello", "open"]
    else:
        assert received(plain) == values


def test_the_launcher_step_ends_at_its_limit_and_says_not_opened(tmp_path):
    slow = script(tmp_path, "slow.py", "import time\ntime.sleep(20)\n")
    started = time.monotonic()
    assert asyncio.run(consumer.browser_opened((sys.executable, slow), dict(os.environ), limit=2)) is False
    assert time.monotonic() - started < 30


@pytest.fixture
def installation():
    """A private installation folder with the app's real HTTPS identity and credentials."""
    from amplifier_web import auth, deployment, tls
    from amplifier_web.windows_files import address, directory
    # The runner's temp folder has a broad inherited ACL, which the private
    # boundary refuses. Use a new private child of the profile, as the app does.
    # A space in the name: every path of the command then has one, as for a
    # person whose user name has one.
    root = address(Path.home() / (".unified first open " + uuid.uuid4().hex))
    with directory(root, owned_root=root, create=True):
        pass
    try:
        for name in ("app", "workspace"):
            with directory(root / name, owned_root=root, create=True):
                pass
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        home = root / "app"
        config = deployment.load_server_config(home, overrides={"port": port, "bind": ["127.0.0.1"]})
        config = tls.setup_local_ca(home, config)
        auth.session_secret(home)
        auth.control_token(home)
        yield SimpleNamespace(root=root, home=home, config=config, origin=f"https://127.0.0.1:{port}")
    finally:
        shutil.rmtree(root)


class Host:
    """This installation's host as one package version.

    The health fields of server.health, the real grant handler and its store,
    on the installation's own HTTPS identity and port.
    """

    def __init__(self, installation, version):
        from aiohttp import web
        from amplifier_web import auth, tls
        self.asked = self.requested = 0
        self.instance = uuid.uuid4().hex
        home = installation.home
        app = self.app = web.Application()
        app["control_token"] = auth.control_token(home)
        app["allowed_origins"] = frozenset({installation.origin})
        app["service"] = SimpleNamespace(data_dir=home, update_manager=SimpleNamespace(
            running_identity={"instanceId": self.instance, "version": version, "revision": None}))
        entry.initialize(app)

        async def health(request):
            body = {"ok": True, "app": "amplifier-unified", "version": version}
            if hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + app["control_token"]):
                self.asked += 1
                body.update(dataIdentity=auth.data_identity(home), revision=None, instanceId=self.instance)
            return web.json_response(body)

        async def issue(request):
            self.requested += 1
            return await entry.issue(request)

        app.router.add_get("/api/health", health)
        app.router.add_post(entry.ISSUE_PATH, issue)
        self.runner = web.AppRunner(app)
        self.site = lambda: web.TCPSite(self.runner, "127.0.0.1", installation.config["port"],
                                        ssl_context=tls.ssl_context(home, installation.config))

    async def start(self):
        await self.runner.setup()
        await self.site().start()

    async def stop(self):
        await self.runner.cleanup()


async def until(condition, limit=60):
    deadline = time.monotonic() + limit
    while not condition():
        assert time.monotonic() < deadline, "The expected state did not arrive."
        await asyncio.sleep(0.1)


@pytest.mark.skipif(os.name != "nt", reason="Windows private files, the owner token and os.execv as Windows has it")
def test_native_first_open_waits_for_the_restarting_host_and_opens_the_browser_once(installation):
    from amplifier_web import auth
    root = installation.root
    handover = script(root, "handover.py", HANDOVER)
    opened = root / "opened.txt"
    recorder = script(root, "browser.py", "import sys\n"
                      "with open(sys.argv[1], 'a', encoding='utf-8') as record:\n"
                      "    record.write(sys.argv[2] + '\\n')\n")
    environment = {**ENVIRONMENT, "BROWSER": " ".join(
        f'"{Path(value).as_posix()}"' for value in (sys.executable, recorder, opened)) + " %s"}
    # The launcher's own arguments, with the hand-over in the place of the
    # installed amplifier-unified.exe and its app generation.
    command = (sys.executable, handover, "-m", "amplifier_web",
               "--data-dir", installation.home, "--workspace", root / "workspace")
    earlier, current = Host(installation, "0.0.1"), Host(installation, __version__)

    async def first_start():
        await earlier.start()
        step = asyncio.create_task(consumer.browser_opened(command, environment))
        try:
            # The earlier version still serves. `open` finds it, opens nothing
            # and asks again: it waits.
            await until(lambda: earlier.asked >= 2)
            assert not step.done() and not opened.exists()
            await earlier.stop()
            await asyncio.sleep(3)  # No host: the restart for the update.
            assert not step.done() and not opened.exists()
            await current.start()
            return await asyncio.wait_for(step, 120)
        finally:
            step.cancel()
            for host in (earlier, current):
                await host.stop()

    started = time.monotonic()
    assert asyncio.run(first_start()) is True
    print(f"\nfirst open: browser recorded after {time.monotonic() - started:.1f} s; "
          f"health requests: earlier host {earlier.asked}, new host {current.asked}")
    addresses = opened.read_text(encoding="utf-8").splitlines()
    assert len(addresses) == 1  # One browser start.
    assert addresses[0].startswith(installation.origin + "/login#ticket=")
    assert auth.control_token(installation.home) not in addresses[0]
    # One grant request, and only to the host that the restart brought up.
    assert (earlier.requested, current.requested) == (0, 1)
    assert not earlier.app["browser_entry"]._grants and len(current.app["browser_entry"]._grants) == 1
    # The address that the browser got holds that host's grant; it works once.
    binding = entry.Binding(current.instance, auth.data_identity(installation.home),
                            installation.origin, entry.current_owner_sid())
    ticket = addresses[0].split("#ticket=")[1]
    assert current.app["browser_entry"].consume(ticket, binding) == "/"
    assert current.app["browser_entry"].consume(ticket, binding) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows private files, the owner token and loopback connection timing")
def test_native_open_ends_at_its_limit_when_no_host_of_this_version_comes(installation):
    earlier = Host(installation, "0.0.1")

    async def waited(limit):
        started = time.monotonic()
        with pytest.raises(entry.BrowserEntryUnavailable, match="not reachable or its identity"):
            await entry.owner_browser_url(installation.home, installation.config, wait=limit)
        return time.monotonic() - started

    async def both():
        away = await waited(3)  # Nothing listens.
        await earlier.start()
        try:
            return away, await waited(3)  # Only the earlier version answers.
        finally:
            await earlier.stop()

    away, other = asyncio.run(both())
    print(f"\nopen limit 3 s: ended after {away:.1f} s with no listener, {other:.1f} s with another version")
    assert 3 <= away < 30 and 3 <= other < 30
    assert earlier.asked >= 2 and earlier.requested == 0  # Asked again, and never for a grant.
