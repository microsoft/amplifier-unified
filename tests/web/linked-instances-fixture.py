"""One real Unified HTTPS server inside the managed paired browser test."""
import asyncio
from collections import Counter
from contextlib import ExitStack
import errno
import fcntl
import json
import os
from pathlib import Path
from shutil import copyfile
import signal
import socket
import ssl
import struct
import sys
import uuid
from urllib.request import Request, urlopen

from aiohttp import web


def publish(path, result, *, mode=0o644):
    temporary = path.with_suffix(".next")
    temporary.write_text(json.dumps(result))
    temporary.chmod(mode)
    temporary.replace(path)


def fail_after_first_enrollment(write_private, ledger, marker):
    """Run the real atomic write, then fail once for the new issuer grant."""
    fired = False

    def write(path, contents, **kwargs):
        nonlocal fired
        write_private(path, contents, **kwargs)
        if fired or path != ledger:
            return
        state = json.loads(contents)
        if len(state["links"]) != 1:
            return
        grant_id, row = next(iter(state["links"].items()))
        if (row["direction"], row["scope"], row["revoked"]) != ("incoming", "status", False):
            return
        if not any(cmd.get("enrollment", {}).get("grantId") == grant_id
                   for cmd in state["commands"].values()):
            return
        # Verify the published name, not just a returned call to the writer.
        if path.read_bytes() != contents:
            raise RuntimeError("Fixture ledger publication did not match the write")
        publish(marker, {"grantId": grant_id})
        fired = True
        raise OSError(errno.EIO, "Synthetic issuer failure after atomic ledger write")

    return write


def listen_on(stack, address, port):
    sock = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((address, port))
    return sock


async def control_routes(runner, context, addresses, sites, port, peer, stopped):
    """Stop or start only the fixture's named listeners, never change host routing."""
    revision = 0
    control = Path("/run/link-control/source.json")
    while not stopped.is_set():
        if control.is_file():
            desired = json.loads(control.read_text())
            if desired["revision"] != revision:
                for name in ("lan", "overlay"):
                    if desired[name] and name not in sites:
                        site = web.TCPSite(runner, addresses[name], port, ssl_context=context,
                                           reuse_address=True)
                        await site.start()
                        sites[name] = site
                    elif not desired[name] and name in sites:
                        await sites.pop(name).stop()
                revision = desired["revision"]
                publish(peer / "routes.json", {
                    "revision": revision,
                    **{name: name in sites for name in ("lan", "overlay")},
                })
        try:
            await asyncio.wait_for(stopped.wait(), timeout=0.05)
        except TimeoutError:
            pass


async def wait_for_restart(role, stopped):
    """Accept one test-owned restart request only, not a product-side restart."""
    request = Path(f"/run/link-control/restart-{role}")
    while not stopped.is_set():
        if request.is_file():
            return True
        try:
            await asyncio.wait_for(stopped.wait(), timeout=0.05)
        except TimeoutError:
            pass
    return False


class Advertisement:
    def __init__(self, path):
        self.path = path
        self.returncode = None
        self.ended = asyncio.Event()
        publish(path, {"active": True})

    def kill(self):
        self.returncode = -15
        self.path.unlink(missing_ok=True)
        self.ended.set()

    terminate = kill

    async def wait(self):
        await self.ended.wait()
        return self.returncode


class FixtureDiscovery:
    """Finite, guest-private discovery records, not a network discovery tool."""

    def __init__(self, role, directory, local, source):
        from amplifier_web.linked_discovery import Endpoint, Node

        self.Endpoint, self.Node = Endpoint, Node
        self.role, self.directory, self.local, self.source = role, directory, local, source
        self.counts = Counter()
        self.audit()

    def audit(self, operation=None):
        if operation:
            self.counts[operation] += 1
        publish(self.directory / "discovery-audit.json", self.counts)

    async def lan(self):
        self.audit("lan")
        if self.role != "destination":
            return []
        if not Path("/run/link-source/advertisement.json").exists():
            return []
        source, local = self.source, self.local
        if Path("/run/link-control/untrusted-route").exists():
            # A hint is not authority. This routes only to the other fixture's
            # genuine independent TLS identity, never a credential collector.
            return [self.Endpoint(source["linkInstanceId"], "Fixture rejected route",
                                  local["peerAddresses"]["lan"], local["port"])]
        return [
            self.Endpoint(source["linkInstanceId"], "Fixture source Amplifier",
                          source["peerAddresses"]["lan"], source["port"]),
            self.Endpoint(local["alternateInstanceId"], "Fixture unrelated Amplifier",
                          local["peerAddresses"]["lan"], local["port"]),
        ]

    async def lan_address(self, address):
        return address in {record["peerAddresses"]["lan"] for record in
                           (self.local, self.source) if record}

    async def roster(self):
        self.audit("roster")
        if self.role != "destination":
            return []
        return [
            self.Node("nFixturePeer", "Fixture peer device", True,
                      (self.source["peerAddresses"]["overlay"],)),
            self.Node("nFixtureOther", "Fixture unrelated device", True,
                      (self.local["peerAddresses"]["overlay"],)),
            self.Node("nFixtureOffline", "Fixture offline device", False,
                      (self.local["peerAddresses"]["overlay"],)),
            self.Node("nFixtureUnconfigured", "Fixture device without a service profile", True,
                      (self.source["peerAddresses"]["overlay"],)),
        ]

    async def tailscale_interface(self, address):
        from amplifier_web.linked_discovery import Unavailable

        if self.role != "destination" or address != self.source["peerAddresses"]["overlay"]:
            raise Unavailable
        local = self.local["peerAddresses"]["overlay"]
        # Read only this fixture namespace's interface addresses. The product
        # still binds its real socket to the identified egress interface.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for _, name in socket.if_nameindex():
                try:
                    info = fcntl.ioctl(sock, 0x8915, struct.pack("256s", name.encode()))
                except OSError:
                    continue
                if name == "tailscale0" and socket.inet_ntoa(info[20:24]) == local:
                    return name
        raise Unavailable

    async def publish(self, name, port, label):
        self.audit("publish")
        return Advertisement(self.directory / "advertisement.json")


async def source_readiness():
    path = Path("/run/link-source/ready.json")
    async with asyncio.timeout(30):
        while not path.is_file():
            await asyncio.sleep(0.05)
        return json.loads(path.read_text())


async def serve(role, uid, peer, report):
    home = Path(os.environ["HOME"]).resolve()
    if os.getuid() != uid or os.geteuid() != uid or uid == 0:
        raise RuntimeError("Paired fixture must run under its assigned guest UID")
    if home.stat().st_uid != uid or home.stat().st_mode & 0o077:
        raise RuntimeError("Paired fixture requires a private home owned by its UID")
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root), str(root / "tests" / "fixtures")]
    os.environ.update(AMPLIFIER_HOME=str(home / "native"), AMPLIFIER_WEB_HOME=str(home / "app"),
                      AMPLIFIER_UNIFIED_IMPORT_HOME=str(home / "legacy"))

    report("imports")
    from amplifier_web import auth, linked_instances
    from amplifier_web.auth import control_token
    from amplifier_web.deployment import load_server_config, save_server_config
    from amplifier_web.server import create_app
    from amplifier_web.tls import setup_local_ca, ssl_context, tls_dir
    from empty_host_ui_server import Runtime

    # Substitute only this disposable machine account's PAM decision and the
    # model runtime. Browser sessions, CSRF, pairing, app state and TLS are real.
    auth.authenticate_pam = lambda name, password: (name, password) == (
        f"fixture-{role}", f"synthetic-only-{role}"
    )
    state = home / "app"
    mode = (root / "tests/web/linked-client-candidate/fixture-mode").read_text().strip()
    if mode not in {"ready", "blocked"}:
        raise RuntimeError("Invalid paired fixture mode")
    boot_state = home / "fixture-boot.json"
    restarted = os.environ.get("LINKED_FIXTURE_RESTARTED") == "1"
    prior = json.loads(boot_state.read_text()) if restarted else None
    if restarted and mode != "ready":
        raise RuntimeError("Blocked lane must not restart")
    ledger_path = linked_instances.LinkStore(state).path
    if (not restarted and (boot_state.exists() or ledger_path.exists())
            or (peer / "post-persist-enrollment.json").exists()):
        raise RuntimeError("Paired fixture requires a fresh ledger and readiness volume")
    if role == "source":
        linked_instances.write_private = fail_after_first_enrollment(
            linked_instances.write_private, ledger_path, peer / "post-persist-enrollment.json",
        )
    workspace = home / "workspace"
    workspace.mkdir(mode=0o700, exist_ok=True)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopped.set)

    # One app/TLS identity on separate interfaces; browser/control remains up
    # when either peer-facing socket is stopped. DNS aliases come from this
    # private Compose project; no subnet or port probing.
    addresses = {
        name: socket.gethostbyname(f"linked-{role}-{name}")
        for name in ("lan", "overlay")
    }
    if len(set(addresses.values())) != len(addresses):
        raise RuntimeError("Peer routes must have different interface addresses")
    # The configured origin also resolves on peer (LAN). Resolve the browser
    # side via its dedicated alias so the app itself binds exactly once there.
    addresses["browser"] = socket.gethostbyname(f"linked-{role}-control")
    addresses["health"] = "127.0.0.1"
    if len(set(addresses.values())) != len(addresses):
        raise RuntimeError("Fixture requires distinct listening addresses")
    with ExitStack() as stack:
        local = listen_on(stack, addresses["health"], prior["port"] if restarted else 0)
        port = local.getsockname()[1]
        sockets = {"health": local, **{
            name: listen_on(stack, address, port) for name, address in addresses.items()
            if name != "health"
        }}
        # A known guest-owned port for a second advertised profile. It is only
        # a discovery hint: no request should be sent there.
        other = listen_on(stack, addresses["overlay"], prior["alternatePort"] if restarted else 0)
        origin = f"https://linked-{role}-browser:{port}"
        # The second allowed origin keeps the old invitation-origin selector
        # exercised. It never appears in the discovery route inventory.
        alternate = f"https://linked-{role}-control:{port}"
        with linked_instances.LinkStore(state).transaction() as ledger:
            local_id = ledger["instanceId"]
        local = {"linkInstanceId": local_id, "port": port,
                 "alternateInstanceId": prior["alternateInstanceId"] if restarted else uuid.uuid4().hex,
                 "alternatePort": other.getsockname()[1],
                 "peerAddresses": {name: addresses[name] for name in ("lan", "overlay")}}
        source = None
        if role == "destination" and not restarted:
            report("source-readiness")
            source = await source_readiness()
        # Inventory is owned fixture input; opting in to linking, LAN, or
        # Tailscale is not. Only the browser's owner actions enable them.
        discovery = {"lan": False, "tailscale": False, "label": f"Fixture {role} Amplifier"}
        if source:
            discovery["tailscale_endpoints"] = {
                "nFixturePeer": [
                    {"instanceId": source["linkInstanceId"], "label": "Fixture source Amplifier",
                     "port": source["port"]},
                    {"instanceId": source["alternateInstanceId"], "label": "Fixture second profile",
                     "port": source["alternatePort"]},
                ],
                "nFixtureOther": [
                    {"instanceId": local["alternateInstanceId"], "label": "Fixture unrelated Amplifier",
                     "port": local["alternatePort"]},
                ],
            }
        if restarted:
            source = prior["source"]
        else:
            report("tls")
            config = setup_local_ca(state, {
                "bind": ["0.0.0.0"], "port": port, "public_origins": [origin, alternate],
                "linked_instances": mode == "blocked", "linked_instances_discovery": discovery,
            })
            if mode == "blocked":
                # A real HTTPS listener with equivalent material on noncanonical
                # paths, which product linking must refuse. No HTTP mocks.
                for field, name in (("cert", "leaf"), ("key", "key")):
                    target = tls_dir(state) / f"fixture-{name}.pem"
                    copyfile(state / config["tls"][field], target)
                    target.chmod(0o600)
                    config["tls"][field] = str(target.relative_to(state))
                save_server_config(state, config)
            publish(boot_state, {**local, "source": source}, mode=0o600)
        config = load_server_config(state)
        if config["linked_instances"] != (mode == "blocked" or restarted):
            raise RuntimeError("Expected persisted owner setup, not fixture activation")
        report("app")
        app = await create_app(state, workspace=workspace, runtime=Runtime(), voice=False,
                               preload_providers=False, background_updates=False,
                               background_work=False, server_config=config)
        app["service"].linked_instances.discovery.backend = FixtureDiscovery(role, peer, local, source)
        calls = Counter()
        connections = Counter()
        connect_path = peer / "connect-audit.json"
        known_routes = {
            (host["peerAddresses"][route], host[port_field]): f"{owner}/{route}/{kind}"
            for owner, host in ((role, local), ("source", source))
            if host is not None
            for route in ("lan", "overlay")
            for port_field, kind in (("port", "app"), ("alternatePort", "decoy"))
        }
        publish(connect_path, connections)

        def observe_connect(event, args):
            if (event == "socket.connect" and isinstance(args[1], tuple)
                    and args[0].type == socket.SOCK_STREAM):
                route = known_routes.get(args[1][:2], "other/tcp")
                connections[route] += 1
                publish(connect_path, connections)

        # Witness TCP attempts, including pre-HTTP failures and other endpoints
        # (count only), without replacing the socket factory or TLS pin checks.
        sys.addaudithook(observe_connect)

        @web.middleware
        async def observe_peer(request, handler):
            # Record only the request class, destination interface and credential
            # presence, never a credential, cookie, body or arbitrary path.
            if request.path in app["linked_instance_peer_paths"]:
                connection = request.transport
                endpoint = connection.get_extra_info("sockname") if connection else None
                route = next((k for k, v in addresses.items() if endpoint and v == endpoint[0]), "unknown")
                kind = request.path.rsplit("/", 1)[-1]
                auth_present = bool(request.headers.get("Authorization"))
                calls[f"{route}/{kind}/{'auth' if auth_present else 'no-auth'}"] += 1
                publish(peer / "peer-audit.json", calls)
            return await handler(request)

        app.middlewares.insert(0, observe_peer)
        publish(peer / "peer-audit.json", calls)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
        try:
            report("listener")
            await runner.setup()
            context = ssl_context(state, config)
            sites = {}
            for name, sock in sockets.items():
                site = web.SockSite(runner, sock, ssl_context=context)
                await site.start()
                sites[name] = site

            def verify_health():
                tls = ssl.create_default_context(cafile=str(tls_dir(state) / "ca.crt"))
                request = Request(f"https://127.0.0.1:{port}/api/health",
                                  headers={"Authorization": "Bearer " + control_token(state),
                                           "Host": f"linked-{role}-browser:{port}"})
                with urlopen(request, context=tls, timeout=5) as result:
                    return json.load(result)

            report("authenticated-health")
            health = await asyncio.to_thread(verify_health)
            report("readiness-record")
            module = root / "amplifier_web" / "static" / "client-component.json"
            receipt = {"role": role, "base": origin, "uid": uid,
                       "instanceId": health["instanceId"], "dataIdentity": health["dataIdentity"],
                       **local,
                       "mode": mode, "restarted": restarted,
                       "clientRevision": json.loads(module.read_text())["source"]["revision"]}
            publish(peer / "ready.json", receipt)
            report("ready")
            if mode == "ready" and not restarted:
                should_restart = await wait_for_restart(role, stopped)
                if should_restart:
                    # Restart only after the browser explicitly requests it,
                    # and never bootstrap/rewrite the product configuration.
                    if not load_server_config(state)["linked_instances"]:
                        raise RuntimeError("Restart attempted before owner saved linking")
                    return True
            elif role == "source" and restarted:
                await control_routes(runner, context, addresses, sites, port, peer, stopped)
            else:
                await stopped.wait()
        finally:
            (peer / "ready.json").unlink(missing_ok=True)
            await runner.cleanup()


if __name__ == "__main__":
    role, uid, peer = sys.argv[1:]
    if role not in {"source", "destination"} or peer != f"/run/link-{role}":
        raise SystemExit("Use the managed paired browser test")
    peer = Path(peer)
    stage = "start"

    def report(next_stage, error=None):
        global stage
        stage = next_stage
        result = {"stage": stage}
        if error is not None:
            result.update(error=type(error).__name__, code=getattr(error, "errno", None))
            if isinstance(error, ModuleNotFoundError):
                result["module"] = error.name
        publish(peer / "diagnostic.json", result)

    try:
        report("identity")
        if asyncio.run(serve(role, int(uid), peer, report)):
            os.environ["LINKED_FIXTURE_RESTARTED"] = "1"
            os.execv(sys.executable, [sys.executable, *sys.argv])
    except Exception as error:
        report(stage, error)
        raise