"""Run the fresh-install lane only in its two managed Compose guests."""
import asyncio
import hashlib
import grp
import ipaddress
import json
import os
from pathlib import Path
import pwd
import runpy
import shutil
import signal
import socket
import sys
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parents[2]
ROLES = ("source", "destination")
START = Path("/run/fresh-control/start.json")
COORDINATOR_START = Path("/run/link-control/start.json")
CLIENT_REVISION = "1176e683897e1f4fb472d68c2d722b613faa0eec"
CLIENT_INPUTS = "c8576e108d0a0fd0b306c522826ec3135eab040dbdc596511babf974fe590f10"
HOST_REVISION = "de5c5bf93f88a64c8cd9297f3cb8cd31dc3c22e5"
STATIC_COMPONENT = "78b52a9cbfc9d69ff631542eb80be08202f6af9c1af04928be2aa6109185b438"
MAIN_PORT = 8941
PEER_NET = ipaddress.IPv4Network("172.29.242.0/28")


def directory(role):
    return Path(f"/run/link-{role}")


def publish(path, result):
    temporary = path.with_suffix(".next")
    temporary.write_text(json.dumps(result, sort_keys=True) + "\n")
    temporary.chmod(0o644)
    temporary.replace(path)


def read(path):
    return json.loads(path.read_text())


async def wait_for(path, seconds):
    async with asyncio.timeout(seconds):
        while not path.is_file():
            await asyncio.sleep(.1)
    return read(path)


async def start_guest_discovery(role, home, processes):
    """Start native discovery only after proving the guest has no other link."""
    assert {name for _, name in socket.if_nameindex()} == {"lo", "eth0"}
    async with asyncio.timeout(5):
        probe = await asyncio.create_subprocess_exec(
            "ip", "-j", "route", "show", "default", stdout=asyncio.subprocess.PIPE,
        )
        output, _ = await probe.communicate()
    assert probe.returncode == 0 and json.loads(output) == []
    runtime = Path("/run/avahi-daemon").stat()
    assert runtime.st_uid == pwd.getpwnam("avahi").pw_uid == os.getuid()
    assert runtime.st_gid == grp.getgrnam("avahi").gr_gid == os.getgid()
    bus_socket = home / "fresh-system-bus"
    assert not bus_socket.exists()
    address = f"unix:path={bus_socket}"
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = address
    processes.append(await asyncio.create_subprocess_exec(
        "dbus-daemon", "--session", "--nofork", "--nopidfile", "--address=" + address,
        stdout=asyncio.subprocess.DEVNULL,
    ))
    async with asyncio.timeout(5):
        while not bus_socket.is_socket():
            assert processes[0].returncode is None
            await asyncio.sleep(.1)
    config = home / "fresh-avahi.conf"
    with config.open("x") as file:
        file.write(f"""[server]
host-name=fresh-{role}
allow-interfaces=eth0
use-ipv4=yes
use-ipv6=no
enable-dbus=yes
[wide-area]
enable-wide-area=no
[publish]
publish-addresses=no
publish-hinfo=no
publish-workstation=no
publish-domain=no
[reflector]
enable-reflector=no
""")
    processes.append(await asyncio.create_subprocess_exec(
        "avahi-daemon", "--no-drop-root", "--no-chroot", "--no-rlimits", "--file=" + str(config),
    ))
    async with asyncio.timeout(15):
        while True:
            assert all(process.returncode is None for process in processes)
            probe = await asyncio.create_subprocess_exec(
                "avahi-browse", "--terminate", "--parsable", "--no-db-lookup",
                "--domain=local", "_amplifier-link._tcp",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                async with asyncio.timeout(4):
                    output, _ = await probe.communicate()
            finally:
                if probe.returncode is None:
                    probe.kill()
                    await probe.wait()
            if probe.returncode == 0:
                assert not output.strip()
                return
            await asyncio.sleep(.2)


async def app(role, home, restarted):
    sys.path[:0] = [str(ROOT), str(ROOT / "tests/fixtures")]
    from aiohttp import web
    from amplifier_web import auth, linked_instances, linked_peer_listener
    from amplifier_web.deployment import load_server_config
    from amplifier_web.server import create_app
    from empty_host_ui_server import Runtime

    state, workspace = home / "fresh-app", home / "fresh-workspace"
    workspace.mkdir(mode=0o700, exist_ok=True)
    own = directory(role)
    local = socket.gethostbyname(f"fresh-{role}-peer")
    assert ipaddress.IPv4Address(local) in PEER_NET
    assert {name for _, name in socket.if_nameindex()} == {"lo", "eth0"}
    expected = linked_peer_listener.Binding("eth0", ipaddress.IPv4Interface(f"{local}/{PEER_NET.prefixlen}"), "lan")
    assert await linked_peer_listener.select_binding(tailscale=False) == ({expected}, set())
    # Use the public in-guest sign-in form; only the guest PAM decision is fake.
    auth.authenticate_pam = lambda name, password: (name, password) == (
        f"fixture-{role}", f"synthetic-only-{role}",
    )
    if role == "source" and not restarted:
        ledger = linked_instances.LinkStore(state).path
        marker = own / "fresh-post-persist.json"
        assert not ledger.exists() and not marker.exists()
        helper = runpy.run_path(str(ROOT / "tests/web/linked-instances-fixture.py"))
        linked_instances.write_private = helper["fail_after_first_enrollment"](
            linked_instances.write_private, ledger, marker,
        )

    receipt_path = own / "fresh-ready.json"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", MAIN_PORT))
        if not restarted:
            assert not state.exists()
            load_server_config(state)  # persist only the product's fresh defaults
        config = load_server_config(state, create=False)
        assert config["bind"] == ["127.0.0.1"] and config["port"] == MAIN_PORT
        assert config["tls"]["method"] == "none" and config["public_origins"] == []
        assert config["linked_instances"] is restarted and config["linked_instances_peer_listener"] is restarted
        assert not (state / "config/tls").exists()
        application = await create_app(state, workspace=workspace, runtime=Runtime(), voice=False,
                                       preload_providers=False, background_updates=False,
                                       background_work=False, server_config=config)
        links = application["service"].linked_instances
        assert not application.get("linked_instance_peer_paths")
        runner = web.AppRunner(application, access_log=None, shutdown_timeout=2)
        try:
            await runner.setup()
            await web.SockSite(runner, listener).start()
            stop = asyncio.Event()
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
            base = f"http://127.0.0.1:{MAIN_PORT}"

            def health():
                request = Request(base + "/api/health",
                                  headers={"Authorization": "Bearer " + auth.control_token(state)})
                with urlopen(request, timeout=5) as response:
                    return json.load(response)

            observed = await asyncio.to_thread(health)
            assert links.setup()["listener"] == ("peer-only" if restarted else "none")
            assert (links.listener.runner is not None) is restarted
            assert links.setup()["lanExpiresAt"] is None
            publish(receipt_path, {"role": role, "base": base, "address": local,
                                   "mainPort": MAIN_PORT, "dataIdentity": observed["dataIdentity"],
                                   "runningInstance": observed["instanceId"], "restarted": restarted,
                                   "clientRevision": read(ROOT / "amplifier_web/static/client-component.json")["source"]["revision"]})
            await stop.wait()
            current = load_server_config(state, create=False)
            assert all(current[field] == config[field] for field in ("bind", "port", "tls", "public_origins"))
            assert not (state / "config/tls").exists()
        finally:
            receipt_path.unlink(missing_ok=True)
            await runner.cleanup()
            assert links.discovery.expires_at() is None


async def guest(role, home):
    own = directory(role)
    receipt = own / "fresh-ready.json"
    finished = own / "fresh-result.json"
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    os.environ.update(AMPLIFIER_HOME=str(home / "fresh-native"), AMPLIFIER_WEB_HOME=str(home / "fresh-app"),
                      AMPLIFIER_UNIFIED_IMPORT_HOME=str(home / "fresh-legacy"))

    async def launch(restarted=False):
        return await asyncio.create_subprocess_exec(sys.executable, __file__, "app", role, str(int(restarted)))

    async def terminate(process):
        if process is not None and process.returncode is None:
            process.terminate()
            async with asyncio.timeout(12):
                await process.wait()
        if process is not None:
            assert process.returncode == 0

    daemons = []
    child = None
    browser = None
    stage = "confined native D-Bus and Avahi readiness"
    try:
        await start_guest_discovery(role, home, daemons)
        stage = "initial authenticated host readiness"
        child = await launch()
        async with asyncio.timeout(60):
            while not receipt.is_file() and child.returncode is None and not stop.is_set():
                await asyncio.sleep(.1)
        assert receipt.is_file() and child.returncode is None
        stage = "wait for managed command"
        while not START.is_file() and not stop.is_set():
            assert child.returncode is None
            await asyncio.sleep(.1)
        if stop.is_set():
            return
        command = read(START)
        assert command["clientRevision"] == read(receipt)["clientRevision"] == CLIENT_REVISION
        stage = "browser guest"
        browser = await asyncio.create_subprocess_exec(
            "node", str(ROOT / "tests/web/linked-fresh-install-browser.mjs"), role, command["run"])
        restarted = False
        async with asyncio.timeout(190):
            while browser.returncode is None and not stop.is_set():
                assert child.returncode is None
                assert all(process.returncode is None for process in daemons)
                restart = own / "fresh-restart.json"
                if role == "source" and not restarted and restart.exists():
                    assert read(restart) == {"run": command["run"], "afterExplicitOwnerEnable": True}
                    stage = "owned host process restart"
                    await terminate(child)
                    child = await launch(restarted=True)
                    restarted = True
                    stage = "browser guest after restart"
                await asyncio.sleep(.1)
        if stop.is_set():
            return
        terminal = read(finished)
        assert browser.returncode == 0 and terminal["checkPassed"] is True and terminal["run"] == command["run"]
        assert restarted or role == "destination"
        stage = "confirmed local cleanup"
        await terminate(child)
        assert not receipt.exists()
        await stop_guest_daemons(daemons)
        publish(own / "fresh-settled.json", {"role": role, "cleanup": "confirmed", "run": command["run"]})
        await stop.wait()
    except Exception as exc:
        publish(own / "fresh-failed.json", {"role": role, "stage": stage, "error": type(exc).__name__})
        raise
    finally:
        if browser is not None and browser.returncode is None:
            browser.terminate()
            async with asyncio.timeout(10):
                await browser.wait()
        try:
            await terminate(child)
        finally:
            await stop_guest_daemons(daemons)


async def stop_guest_daemons(processes):
    for process in reversed(processes):
        if process.returncode is None:
            process.terminate()
            try:
                async with asyncio.timeout(5):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
                raise


def verify_static_package(package):
    descriptor = (package / "component.json").read_bytes()
    assert hashlib.sha256(descriptor).hexdigest() == STATIC_COMPONENT
    component = json.loads(descriptor)
    assert component["source"]["revision"] == CLIENT_REVISION
    assert component["pairedServer"]["revision"] == HOST_REVISION
    assert component["pairedServer"]["qualification"] == "unqualified"
    static = package / "static"
    inventory = {}
    for member in static.rglob("*"):
        assert not member.is_symlink()
        if member.is_file():
            content = member.read_bytes()
            inventory[member.relative_to(static).as_posix()] = {
                "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
            }
        else:
            assert member.is_dir()
    assert inventory == component["assets"]
    assert read(static / "build.json") == component["build"]
    return descriptor


def install_static():
    package = Path("/opt/paired/client-package")
    descriptor = verify_static_package(package)
    target = ROOT / "amplifier_web/static"
    assert not target.exists()
    shutil.copytree(package / "static", target)
    with (target / "client-component.json").open("xb") as file:
        file.write(descriptor)
    with Path("/opt/paired/verified-client.json").open("xb") as file:
        file.write(descriptor)


async def coordinate():
    artifact_dir = Path(os.environ.get("AMPLIFIER_TEST_ARTIFACTS", "/artifacts"))
    stage = "exact source selection"
    result = {"checkPassed": False, "limitations": [
        "Real app, peer TLS/transport and native Avahi mDNS on one isolated Linux guest bridge; guest PAM decision and model runtime are synthetic.",
        "No physical LAN, user device, native Windows/macOS or real Tailscale evidence.",
        "QR rendered and decoded as an image; the invitation field, not a camera, transfers the code.",
    ]}
    run = uuid.uuid4().hex
    try:
        verified = Path("/opt/paired/verified-client.json").read_bytes()
        assert hashlib.sha256(verified).hexdigest() == STATIC_COMPONENT
        assert json.loads(verified)["pairedServer"]["revision"] == HOST_REVISION
        client = json.loads(verified)["source"]["revision"]
        inputs = read(ROOT / "components/web-client.source.json")["inputsSha256"]
        manifest = hashlib.sha256((ROOT / "tests/web/linked-client-candidate/SHA256SUMS").read_bytes()).hexdigest()
        for observed, env in [(client, "CLIENT_REVISION"), (inputs, "INPUTS_SHA256"), (manifest, "MANIFEST_SHA256")]:
            assert observed == os.environ[f"AMPLIFIER_PAIRED_{env}"]
        assert client == CLIENT_REVISION and inputs == CLIENT_INPUTS
        assert os.environ["AMPLIFIER_PAIRED_HOST_REVISION"] == HOST_REVISION
        result.update(clientRevision=client, inputsSha256=inputs, manifestSha256=manifest,
                      hostRevision=os.environ["AMPLIFIER_PAIRED_HOST_REVISION"],
                      fixtureRevision=os.environ["AMPLIFIER_PAIRED_FIXTURE_REVISION"],
                      assemblySha256=hashlib.sha256(verified).hexdigest())
        assert all(len(result[key]) == 40 and all(c in "0123456789abcdef" for c in result[key])
                   for key in ("hostRevision", "fixtureRevision"))
        (artifact_dir / "verified-client.json").write_bytes(verified)
        stage = "two fresh independently authenticated guests"
        hosts = [await wait_for(directory(role) / "fresh-ready.json", 60) for role in ROLES]
        assert [host["role"] for host in hosts] == list(ROLES)
        assert all(host["clientRevision"] == client and not host["restarted"] for host in hosts)
        for key in ("address", "dataIdentity", "runningInstance"):
            assert hosts[0][key] != hosts[1][key]
        assert not COORDINATOR_START.exists(), "Do not replay a previous managed command"
        publish(COORDINATOR_START, {"clientRevision": client, "run": run})
        stage = "browser journeys and local cleanup"
        async with asyncio.timeout(210):
            while True:
                errors = {role: read(directory(role) / "fresh-failed.json") for role in ROLES
                          if (directory(role) / "fresh-failed.json").exists()}
                if errors:
                    result["guestErrors"] = errors
                    raise RuntimeError("Guest failed")
                if all((directory(role) / "fresh-settled.json").is_file() for role in ROLES):
                    assert not (directory("source") / "fresh-ready.json").exists()
                    assert not (directory("destination") / "fresh-ready.json").exists()
                    for role in ROLES:
                        assert read(directory(role) / "fresh-settled.json") == {
                            "role": role, "run": run, "cleanup": "confirmed",
                        }
                    break
                await asyncio.sleep(.2)
        result["checkPassed"] = True
    finally:
        result["stage"] = stage
        result["guests"] = {}
        for role in ROLES:
            path = directory(role) / "fresh-result.json"
            if path.is_file():
                report = read(path)
                if report.get("run") == run:
                    result["guests"][role] = {k: report[k] for k in ("role", "checkPassed", "stage", "checkpoints", "error", "browserVersion", "recovery", "enable")
                                              if k in report}
        publish(artifact_dir / "linked-fresh-install-result.json", result)


if __name__ == "__main__":
    operation, *args = sys.argv[1:]
    if operation == "coordinate" and not args:
        asyncio.run(coordinate())
    elif operation == "install_static" and not args:
        install_static()
    elif operation in {"guest", "app"} and args and args[0] in ROLES:
        role = args[0]
        uid = 24031 if role == "source" else 24032
        home = Path(os.environ["HOME"])
        assert home == Path("/home/fixture-" + role)
        assert os.getuid() == os.geteuid() == home.stat().st_uid == uid
        assert home.stat().st_mode & 0o077 == 0 and directory(role).stat().st_uid == uid
        if operation == "guest" and len(args) == 1:
            asyncio.run(guest(role, home))
        elif operation == "app" and len(args) == 2 and args[1] in {"0", "1"}:
            asyncio.run(app(role, home, args[1] == "1"))
        else:
            raise SystemExit("Use the managed fresh-install fixture")
    else:
        raise SystemExit("Use the managed fresh-install fixture")