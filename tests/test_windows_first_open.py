"""A command that the active app version takes over, with real processes.

The launcher that setup installed hands each CLI command over to the active
app generation (application_generations.delegate). The first cases run that
real hand-over, in a real launcher environment with a real active generation:
the caller gets control back when the output is complete, with the real exit
status, and no console window opens. On Windows the launcher waits for the
app for that; elsewhere the hand-over is exec.

The native cases further below run the real `open` command through a
hand-over against this installation's host while that host restarts as
another package version: real private files, the real pinned HTTPS request,
the real grant handler and store. A recorder stands in for the browser (the
BROWSER variable of Python's webbrowser module), so no browser starts.

Three launchers exist on people's PCs, because an installation keeps the
launcher of its setup file:
- This change: the launcher waits for the app and ends with its status.
- From pull request 2169 to this change: exec, with each argument quoted. On
  Windows the caller gets control back at once with exit status 0, so that
  launcher reads the confirmation line of `open`, never the status.
- Before pull request 2169: exec with the plain list, and the exit status as
  the result. In a folder with a space no new version repairs it: the plain
  list splits the folder, and the command never reaches the app (measured
  below for the argument list).
The first-start case runs all three.

Not shown here: Task Scheduler, the update itself, the Windows certificate
question, a real browser, and the launcher program `amplifier-unified.exe`
in front of the launcher's Python. Those are clean-room steps.
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
# The hand-over of application_generations.delegate: the product's own function.
HANDOVER = """import sys
from amplifier_web.application_generations import hand_over
hand_over(sys.executable, sys.argv[1:])
"""
# The hand-over of a launcher from pull request 2169 to this change: exec, and
# on Windows each argument quoted.
EXEC_HANDOVER = """import os, subprocess, sys
values = [sys.executable, *sys.argv[1:]]
os.execv(sys.executable, [subprocess.list2cmdline([value]) for value in values] if os.name == "nt" else values)
"""
# The hand-over of a launcher before pull request 2169: exec with the plain list.
EARLIER_HANDOVER = "import os, sys\nos.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"
REFUSED = ("import sys\n"
           "sys.stderr.write('The owned local host is not reachable or its identity does not match.')\n"
           "raise SystemExit(1)\n")


def script(folder, name, text):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_command_that_exec_hands_over_ends_with_status_0_on_windows(tmp_path):
    """A launcher that setup installed before this change hands over with exec.

    The measured reason why the launcher reads the confirmation line of
    `open`, and not the exit status: an app version can be newer than the
    launcher, and never older.
    """
    handover, failing = script(tmp_path, "handover.py", EXEC_HANDOVER), script(tmp_path, "failing.py", REFUSED)
    done = subprocess.run([sys.executable, str(handover), str(failing), "open"],
                          capture_output=True, text=True, timeout=120, env=ENVIRONMENT)
    # The output of the program that took over still reaches the caller.
    assert "not reachable" in done.stderr and done.stdout == ""
    # Its exit status does not on Windows: the calling process ends at once
    # with 0. Elsewhere exec keeps the process, and the status is the result.
    assert done.returncode == (0 if os.name == "nt" else 1)


# A command of the app, as its caller sees it: output, a pause, more output,
# and an exit status. The failing one ends with a status that is neither 0 nor 1.
FAILING = ("import sys, time\n"
           "print('first line', flush=True)\n"
           "time.sleep(3)\n"
           "print('last line', flush=True)\n"
           "sys.stderr.write('the reason\\n')\n"
           "raise SystemExit(3)\n")
WORKING = FAILING.replace("raise SystemExit(3)", "raise SystemExit(0)")
# What a program has as its console (Windows). It writes that into the file
# that its last argument names: a program without standard handles can say
# nothing else.
CONSOLE = ("import ctypes, json, sys\n"
           "kernel = ctypes.WinDLL('kernel32')\n"
           "kernel.GetConsoleWindow.restype = ctypes.c_void_p\n"
           "with open(sys.argv[-1], 'w', encoding='utf-8') as report:\n"
           "    json.dump({'console': bool(kernel.GetConsoleCP()),\n"
           "               'window': bool(kernel.GetConsoleWindow())}, report)\n"
           "print('reported', flush=True)\n")
# A command that runs until the person ends it with Ctrl+Break (Windows).
UNTIL_BREAK = ("import signal, sys, time\n"
               "def stop(*_):\n"
               "    print('stopped by the person', flush=True)\n"
               "    raise SystemExit(5)\n"
               "signal.signal(signal.SIGBREAK, stop)\n"
               "print('ready', flush=True)\n"
               "for _ in range(600):\n"
               "    time.sleep(0.1)\n"
               "raise SystemExit(9)\n")


@pytest.fixture
def generation_command(tmp_path):
    """A command of a real launcher that a real active app generation takes over.

    Two real private environments, as application_generations has them: the
    launcher that setup installed, and one qualified app generation that is
    active. The launcher runs the product's own `delegate`, isolated with
    `-I`, as an installed launcher does. Give the returned function the
    program of the app generation; it returns the command and its environment.
    With `windowless` the launcher's Python is the one without a console.
    """
    from amplifier_web import application_generations as generations
    from test_application_generations import isolated_launchers
    base = tmp_path
    if os.name == "nt":
        # The app's folder is private on Windows, and the runner's temp folder
        # is not (see `installation` below). A space in the name: every path
        # of the command then has one.
        from amplifier_web.windows_files import address, directory
        base = address(Path.home() / (".unified hand over " + uuid.uuid4().hex))
        for folder in (base, base / "owned-app", base / "owned-app" / "updates"):
            with directory(folder, owned_root=folder if folder == base else base / "owned-app", create=True):
                pass
    try:
        home, bootstrap, candidate, receipt = isolated_launchers(base)
        generations.promote(home, receipt, bootstrap)

        def command(program, *, windowless=False):
            next(candidate.rglob("amplifier_web/__main__.py")).write_text(program, encoding="utf-8")
            python = bootstrap / (("Scripts/pythonw.exe" if windowless else "Scripts/python.exe")
                                  if os.name == "nt" else "bin/python")
            environment = {name: os.environ[name] for name in ("PATH", "SystemRoot") if name in os.environ}
            environment["AMPLIFIER_WEB_HOME"] = str(home)
            return [str(python), "-I", "-m", "amplifier_web", "--data-dir", str(home)], environment
        command.folder, command.app = base, candidate
        yield command
    finally:
        if base != tmp_path:
            # A program that still runs keeps its files for some seconds.
            for attempt in range(40):
                shutil.rmtree(base, ignore_errors=True)
                if not base.exists():
                    break
                time.sleep(0.5)


@pytest.mark.parametrize("program,expected", [(FAILING, 3), (WORKING, 0)], ids=["fails", "works"])
def test_a_command_that_the_active_app_version_takes_over_returns_complete_with_its_exit_status(
        tmp_path, generation_command, program, expected):
    """A caller that waits for the command, as a terminal does, has everything when it returns.

    The output goes to a file, not to a pipe: a caller that reads a pipe to
    its end waits for the program that took over in any case, and would hide
    a command that returns too early.
    """
    command, environment = generation_command(program)
    output = tmp_path / "output.txt"
    with output.open("wb") as stream:
        status = subprocess.run([*command, "service", "start"], stdout=stream, stderr=subprocess.STDOUT,
                                env=environment, timeout=120).returncode
    at_return = output.read_text(encoding="utf-8").splitlines()
    assert at_return == ["first line", "last line", "the reason"]
    assert status == expected


def test_the_launcher_learns_that_a_command_failed_which_the_active_app_version_took_over(generation_command):
    """The launcher's own reader of its commands (`service start`, `config set`) gets the failure."""
    from amplifier_web.update_diagnostics import CommandFailure
    from amplifier_web.updates import process
    command, environment = generation_command(FAILING)
    with pytest.raises(CommandFailure) as failed:
        asyncio.run(process(*command, "service", "start", env=environment, timeout=120))
    assert failed.value.diagnostic_facts["exitCode"] == 3


def _console_report(command, environment, report, **options):
    done = subprocess.run([*command, str(report)], env=environment, timeout=120, **options)
    assert done.returncode == 0, done
    return json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.skipif(os.name != "nt", reason="A Windows console and its window")
def test_native_command_that_is_taken_over_opens_no_console_window(tmp_path, generation_command):
    """The rule "no console window" for a command that the app takes over, measured.

    1. The launcher's own commands: the Start-menu entry's program has no
       console. It starts each command with pipes and with a console that has
       no window (updates.process). The app shares that console.
    2. A launcher process that has no console at all, with and without
       standard handles: the app gets a console that has no window.
    The control shows that this PC can show the difference: a console program
    that a process without a console starts with default flags has a window.
    """
    command, environment = generation_command(CONSOLE)
    shared = _console_report(command, environment, tmp_path / "shared.json", capture_output=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    assert shared == {"console": True, "window": False}
    windowless, _ = generation_command(CONSOLE, windowless=True)
    with (tmp_path / "output.txt").open("wb") as stream:
        with_handles = _console_report(windowless, environment, tmp_path / "with-handles.json",
                                       stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
    assert with_handles == {"console": True, "window": False}
    # The app's own output reached the caller through the launcher's handles.
    assert (tmp_path / "output.txt").read_text(encoding="utf-8").splitlines() == ["reported"]
    # No standard handles, as a program that a shortcut starts.
    without_handles = _console_report(windowless, environment, tmp_path / "without-handles.json",
                                      creationflags=subprocess.DETACHED_PROCESS)
    assert without_handles == {"console": True, "window": False}
    # Control: the same program, started with default flags by a process without a console.
    program = script(tmp_path, "console.py", CONSOLE)
    python = generation_command.app / "Scripts" / "python.exe"
    starter = ("import subprocess, sys\n"
               "raise SystemExit(subprocess.run([sys.argv[1], '-I', sys.argv[2], sys.argv[3]]).returncode)\n")
    control = tmp_path / "control.json"
    done = subprocess.run([windowless[0], "-I", "-c", starter, str(python), str(program), str(control)],
                          env=environment, timeout=120, creationflags=subprocess.DETACHED_PROCESS)
    assert done.returncode == 0, done
    assert json.loads(control.read_text(encoding="utf-8")) == {"console": True, "window": True}


@pytest.mark.skipif(os.name != "nt", reason="Windows console control events")
def test_native_ctrl_break_ends_the_app_and_the_command_returns_its_status(tmp_path, generation_command):
    """Ctrl+Break reaches the launcher and the app. The app decides; the launcher reports its end."""
    import signal
    command, environment = generation_command(UNTIL_BREAK)
    output = tmp_path / "output.txt"
    with output.open("wb") as stream:
        running = subprocess.Popen([*command, "serve"], stdin=subprocess.DEVNULL, stdout=stream,
                                   stderr=subprocess.STDOUT, env=environment,
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        try:
            until = time.monotonic() + 60
            while "ready" not in output.read_text(encoding="utf-8") and time.monotonic() < until:
                assert running.poll() is None, output.read_text(encoding="utf-8")
                time.sleep(0.1)
            running.send_signal(signal.CTRL_BREAK_EVENT)
            status = running.wait(60)
        finally:
            if running.poll() is None:
                running.kill()
    assert output.read_text(encoding="utf-8").splitlines() == ["ready", "stopped by the person"]
    assert status == 5


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
    import venv
    show = script(tmp_path, "show.py", "import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    values = ["C:\\Users\\Jane Doe\\AppData\\Local\\Amplifier\\app", "", 'say "hello"', "open"]
    def received(program, python=sys.executable):
        done = subprocess.run([str(python), str(program), str(show), *values],
                              capture_output=True, text=True, timeout=120, env=ENVIRONMENT)
        return json.loads(done.stdout)
    handover = script(tmp_path, "handover.py", HANDOVER)
    assert received(handover) == values
    # The interpreter of an app generation is itself under that folder: a
    # virtual environment's python, which starts the real one with the same
    # arguments. So the first exec argument has the space too.
    home = tmp_path / "Jane Doe"
    venv.create(home / "generation", with_pip=False)
    python = home / "generation" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert " " in str(python) and received(handover, python) == values
    if os.name == "nt":
        # A launcher before pull request 2169 with such an interpreter: the plain
        # list splits the interpreter's own path, and the command does not
        # arrive at all. No new app version can repair that launcher.
        lost = subprocess.run([str(python), str(script(tmp_path, "earlier.py", EARLIER_HANDOVER)), str(show), "open"],
                              capture_output=True, text=True, timeout=120, env=ENVIRONMENT)
        assert lost.stdout.strip() != json.dumps(["open"])
    # The reason for the product's own list: Windows joins a plain exec list
    # with spaces and quotes nothing. Measured here, not assumed. This is also
    # what a launcher before pull request 2169 still does in a folder with a space.
    plain = script(tmp_path, "plain.py", EARLIER_HANDOVER)
    if os.name == "nt":
        assert received(plain) == ["C:\\Users\\Jane", "Doe\\AppData\\Local\\Amplifier\\app", "say", "hello", "open"]
    else:
        assert received(plain) == values


def test_the_launcher_step_ends_at_its_limit_and_says_not_confirmed(tmp_path):
    """The limit ends the step, and nothing else: the program that took the
    command over had started, and it had not finished when the step returned."""
    from amplifier_web import updates  # Loaded before the time is taken, not inside it.
    assert updates.process
    limit, tolerance = 4, 3  # Seconds. The tolerance is for the start of the process on a busy PC.
    handover = script(tmp_path, "handover.py", HANDOVER)
    started, finished = tmp_path / "started.txt", tmp_path / "finished.txt"
    slow = script(tmp_path, "slow.py", "import os, time\n"
                  f"with open({str(started)!r}, 'w') as record:\n    record.write(str(os.getpid()))\n"
                  "time.sleep(10)\n"
                  f"with open({str(finished)!r}, 'w') as record:\n    record.write('done')\n"
                  f"print({entry.OPENED!r}, flush=True)\n")
    begun = time.monotonic()
    result = asyncio.run(consumer.browser_opened((sys.executable, handover, slow), ENVIRONMENT, limit=limit))
    elapsed = time.monotonic() - begun
    at_return = (started.exists(), finished.exists())
    assert result is False
    # Not a command that failed at once: the program ran. Not a step that
    # waited for the program's end: the program was still in its 10 seconds.
    assert at_return == (True, False)
    # The step ended at its limit: not before it, and not at a longer limit
    # (twice the limit, 8 seconds, is outside the tolerance).
    assert limit - 0.1 <= elapsed < limit + tolerance
    if os.name == "nt":
        # The program that took over is another process there. The launcher
        # cannot end it, so it goes on and reaches its end (in the product:
        # `open` can still open the browser after the launcher's message).
        until_end = time.monotonic() + 60
        while not finished.exists() and time.monotonic() < until_end:
            time.sleep(0.2)
        assert finished.exists()
    else:
        # Elsewhere exec keeps the process, and the limit ended it.
        with pytest.raises(ProcessLookupError):
            os.kill(int(started.read_text()), 0)


@pytest.fixture
def installation(request):
    """A private installation folder with the app's real HTTPS identity and credentials."""
    from amplifier_web import auth, deployment, tls
    from amplifier_web.windows_files import address, directory
    # The runner's temp folder has a broad inherited ACL, which the private
    # boundary refuses. Use a new private child of the profile, as the app does.
    # A space in the name (the default): every path of the command then has
    # one, as for a person whose user name has one.
    name = ".unified first open " if getattr(request, "param", True) else ".unified-first-open-"
    root = address(Path.home() / (name + uuid.uuid4().hex))
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


async def step_before_this_change(command, environment):
    """The launcher's open step as setup files before pull request 2169 have it:
    the command once, 30 seconds, and the exit status as the result."""
    from amplifier_web.updates import process
    await process(*command, "open", env=environment, timeout=30)
    return True  # No error: that launcher shows nothing.


@pytest.mark.skipif(os.name != "nt", reason="Windows private files, the owner token and the hand-over as Windows has it")
@pytest.mark.parametrize("installation,launcher", [(True, "waits"), (True, "exec"), (False, "plain exec")],
                         indirect=["installation"],
                         ids=["launcher of this change, folder with a space",
                              "launcher that hands over with exec, folder with a space",
                              "launcher before pull request 2169, folder without a space"])
def test_native_first_open_waits_for_the_restarting_host_and_opens_the_browser_once(installation, launcher):
    from amplifier_web import auth
    root = installation.root
    earlier_launcher = launcher == "plain exec"
    if earlier_launcher and " " in str(root) + sys.executable:
        pytest.skip("This PC's profile or Python folder has a space: a launcher before pull request 2169 "
                    "cannot work there")
    handover = script(root, "handover.py", {"waits": HANDOVER, "exec": EXEC_HANDOVER,
                                            "plain exec": EARLIER_HANDOVER}[launcher])
    step = step_before_this_change if earlier_launcher else consumer.browser_opened
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
        running = asyncio.create_task(step(command, environment))
        try:
            # The earlier version still serves. `open` finds it, opens nothing
            # and asks again: it waits.
            await until(lambda: earlier.asked >= 2)
            assert not running.done() and not opened.exists()
            await earlier.stop()
            await asyncio.sleep(3)  # No host: the restart for the update.
            assert not running.done() and not opened.exists()
            await current.start()
            return await asyncio.wait_for(running, 120)
        finally:
            running.cancel()
            for host in (earlier, current):
                await host.stop()

    started = time.monotonic()
    assert asyncio.run(first_start()) is True
    print(f"\nfirst open, launcher {launcher}: browser recorded after {time.monotonic() - started:.1f} s; "
          f"health requests: earlier host {earlier.asked}, new host {current.asked}")
    # Compare facts only. A failed comparison prints its values, and the
    # address holds a grant: neither it nor the credential may reach a CI log.
    addresses = opened.read_text(encoding="utf-8").splitlines()
    address = addresses[0] if addresses else ""
    start, _, ticket = address.partition("#ticket=")
    binding = entry.Binding(current.instance, auth.data_identity(installation.home),
                            installation.origin, entry.current_owner_sid())
    store = current.app["browser_entry"]
    facts = {
        "browser starts": len(addresses),
        "login address of this installation": start == installation.origin + "/login",
        "address holds the owner credential": auth.control_token(installation.home) in address,
        # One grant request, and only to the host that the restart brought up.
        "grant requests (earlier host, new host)": (earlier.requested, current.requested),
        "grants held (earlier host, new host)": (len(earlier.app["browser_entry"]._grants), len(store._grants)),
        # The address that the browser got holds that host's grant; it works once.
        "first use of the grant": store.consume(ticket, binding),
        "second use of the grant": store.consume(ticket, binding),
    }
    del addresses, address, ticket
    assert facts == {
        "browser starts": 1,
        "login address of this installation": True,
        "address holds the owner credential": False,
        "grant requests (earlier host, new host)": (0, 1),
        "grants held (earlier host, new host)": (0, 1),
        "first use of the grant": "/",
        "second use of the grant": None,
    }


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
    # Not before the wait. After it: at most one health request that started
    # before its end (its own limit is 5 seconds), and 2 seconds of tolerance.
    latest = 3 + 5 + 2
    assert 3 <= away < latest and 3 <= other < latest
    assert earlier.asked >= 2 and earlier.requested == 0  # Asked again, and never for a grant.
