"""Compose the installed owner-logon setup from the public CLI boundaries.

No credential transfer, force stop, or task replacement is performed here. The
bootstrap owns verified package installation; Task Scheduler and the CLI keep
ownership of registration, readiness, serving, and removal. The only trust
change is the person's own Yes to Windows for this installation's key-less
local certificate (windows_trust.py); an identity with a signing key on disk
is never added, only shown for the person's own review.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
from urllib.parse import urlsplit


def owner_notice(message, *, confirm=False):
    """Ordinary signed-in-owner dialog; never a trust import or elevation."""
    import ctypes
    if os.name != "nt":
        raise RuntimeError("The installation dialog requires native Windows.")
    dialog = ctypes.WinDLL("user32", use_last_error=True).MessageBoxW
    dialog.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    dialog.restype = ctypes.c_int
    # In front of other windows: a windowless launcher's message that waits
    # behind the browser looks like nothing happened.
    result = dialog(None, message, "Amplifier Unified", 0x50040 | (0x4 if confirm else 0))
    if not result:
        raise OSError("The installation dialog could not be displayed.")
    return result == 6 if confirm else True


class AddressUnavailable(RuntimeError):
    """This installation's saved loopback port cannot be claimed right now.

    `kind` is `in-use` (another program listens there) or `reserved` (Windows
    excludes the port and no program holds it).
    """

    def __init__(self, kind, port):
        super().__init__("The installation address is unavailable; no existing host was adopted or stopped.")
        self.kind, self.port = kind, port


def address_message(problem):
    """Plain owner text for a taken saved port, ending in the repair question."""
    cause = (f"another program is using its local address (port {problem.port}).\n\n"
             "Close that program and open Amplifier again. Or select Yes to move "
             "Amplifier to a new local address now."
             if problem.kind == "in-use" else
             f"Windows has reserved its local address (port {problem.port}) for another "
             "feature. No other program is using it, so there is nothing to close.\n\n"
             "Select Yes to move Amplifier to a new local address now.")
    return ("Amplifier cannot start because " + cause + "\n\n"
            "Your chats, settings and accounts stay. If you installed Amplifier as an app "
            "from the browser, install it again afterwards. Messages that you typed but "
            "did not send are not carried over.")


def saved_port(home):
    """Return the port an earlier setup saved for this installation, else None.

    Setup saves the port together with its loopback origin. An owner who later
    lists only a remote address still has a saved port: it is not the default,
    and the app-owned HTTPS that setup configured is in place. Setup must not
    select again and overwrite that owner's addresses.
    """
    from .deployment import DEFAULT_SERVER, load_server_config
    config = load_server_config(home)
    port = config["port"]
    if (f"https://127.0.0.1:{port}" in config["public_origins"]
            or (port != DEFAULT_SERVER["port"] and config["tls"]["method"] == "ca")):
        return port
    return None


def address_kind(port, spec=None):
    """Classify a saved port as `free`, `in-use`, `reserved` or `own`.

    `own` is this installation's recorded host listening outside a running
    task. That is not another program, and never a reason to move the address.
    """
    from . import windows_ports
    kind = windows_ports.state(port)
    if kind == "in-use" and spec is not None and os.name == "nt":
        from .windows_task_runtime import listener_owned, observe
        record = observe(spec)
        if record is not None and listener_owned(record, "127.0.0.1", port):
            return "own"
    return kind


def _conflict(home):
    """After a failed start: is another owner holding this installation's port?"""
    from . import windows_service
    from .deployment import load_server_config
    port = load_server_config(home)["port"]
    kind = address_kind(port, windows_service._read(home)[1])
    return AddressUnavailable(kind, port) if kind in {"in-use", "reserved"} else None


def _moved(origin, old, new):
    parts = urlsplit(origin)
    if parts.port != old:
        return origin
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{parts.scheme}://{host}:{new}"


async def change_address(root, launcher, environment):
    """Owner-confirmed repair: save a new loopback port for this stopped installation.

    Origins that named the old port move with it in the same write. The
    certificate names no port, so its trust is unchanged. The browser sees a
    new site: an installed app window and unsent drafts do not carry over.
    """
    from . import windows_ports, windows_service
    from .deployment import load_server_config
    from .updates import process
    root, launcher = Path(root), Path(launcher)
    home = root / "app"
    task, spec = await asyncio.to_thread(windows_service._read, home)
    if spec is not None and (task.get("instances") != 0 or task.get("state") != 3):
        raise RuntimeError("The address can change only while this installation's host is stopped.")
    config = load_server_config(home)
    port = await asyncio.to_thread(windows_ports.select)
    origins = [_moved(origin, config["port"], port) for origin in config["public_origins"]]
    await process(str(launcher), "--data-dir", str(home), "--workspace", str(root / "workspace"),
                  "config", "set", "port", str(port), "public_origins", json.dumps(origins),
                  env=environment, cwd=root, timeout=90)
    return {"port": port, "previous": config["port"]}


def browser_trust_ready(urls, pin):
    """Read Windows ROOT trust and match the already verified private leaf.

    No bearer, grant, HTTP request, custom CA, environment override or trust
    mutation participates in this check. Browser policy can still refuse entry.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    for certificate, encoding, trust in ssl.enum_certificates("ROOT"):
        if encoding == "x509_asn" and (trust is True or ssl.Purpose.SERVER_AUTH.oid in trust):
            context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(certificate))
    for url in urls:
        address = urlsplit(url)
        if address.scheme != "https" or address.hostname not in {"127.0.0.1", "::1"}:
            raise ValueError("Browser trust requires a verified HTTPS loopback target.")
        try:
            with socket.create_connection((address.hostname, address.port), timeout=2) as transport:
                with context.wrap_socket(transport, server_hostname=address.hostname) as connection:
                    if hashlib.sha256(connection.getpeercert(binary_form=True)).digest() == pin.fingerprint:
                        return True
        except (OSError, ssl.SSLError):
            continue
    return False


def _export_certificate(root):
    """Save the public certificate where the person or their IT can read it."""
    from .tls import exported_ca_bytes
    from .windows_files import directory
    content = exported_ca_bytes(root / "app")
    with directory(root / "export", owned_root=root, create=True) as exported:
        exported.publish_private("local-ca.cer", content, replace=True)
    return content


def review_browser_trust(root, urls, pin, *, fresh=None):
    """Make this installation's HTTPS trusted before any browser grant exists.

    A key-less identity: explain once, then Windows asks the person its one
    standard question. `fresh` is the root this process created moments ago;
    that exact certificate is added. No is respected: the app keeps running
    and the next open asks again. An identity with a signing key on disk is
    never added; the person can review it in the Windows certificate viewer.
    """
    if browser_trust_ready(urls, pin):
        return True
    from . import windows_trust
    certificate = windows_trust.candidate(root / "app", pin, fresh)
    if certificate is None:
        return _review_manually(root, urls, pin)
    saved = str(root / "export" / "local-ca.cer")
    support = ("If your organization manages this PC, ask its IT support to trust this "
               "certificate for your account:\n" + saved)
    blocked = ("Amplifier is running, but this PC's security settings do not let it finish "
               "setting up its private connection to your browser.\n\n" + support + "\n\n"
               "Amplifier did not change any security setting.")
    if windows_trust.user_roots_disabled():
        _export_certificate(root)
        owner_notice(blocked)
        return False
    owner_notice(
        "Windows will now ask one question: whether to install a certificate named \""
        + windows_trust.ROOT_NAME + "\". Select Yes.\n\n"
        "Amplifier uses this certificate only for the private connection between your "
        "browser and Amplifier on this PC. It does not work for any website. Windows "
        "shows the same security warning for every certificate of this kind.\n\n"
        "Thumbprint in the Windows message:\n" + windows_trust.thumbprint(certificate))
    outcome = windows_trust.add_root(certificate)
    if outcome == "declined":
        owner_notice(
            "Amplifier is running, but it cannot open in your browser until Windows trusts "
            "its certificate. Nothing on this PC was changed.\n\n"
            "Open Amplifier again to see the Windows question once more, and select Yes.")
        return False
    if outcome in {"added", "present"}:
        if browser_trust_ready(urls, pin):
            return True
        # Windows holds the certificate, yet the connection is still refused.
        # Say that plainly; do not claim that nothing changed.
        _export_certificate(root)
        owner_notice(
            "Amplifier is running. Windows accepted its certificate, but this PC still does "
            "not trust the connection between your browser and Amplifier.\n\n"
            "Open Amplifier again. If this message returns, this PC's security settings "
            "override your choice.\n\n" + support)
        return False
    _export_certificate(root)
    owner_notice(blocked)
    return False


def _review_manually(root, urls, pin):
    """An identity with a signing key on disk: public review only, never an add."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    content = _export_certificate(root)
    fingerprint = x509.load_pem_x509_certificate(content).fingerprint(hashes.SHA256())
    if owner_notice(
            "Amplifier is running in the background. Could not verify trusted local HTTPS. "
            "The local certificate may need your review, or the connection may have changed. "
            "Open the certificate viewer to review it?\n\n"
            "Only if your device policy permits it, you can choose Current User > Trusted Root "
            "Certification Authorities in the viewer. This trusts certificates signed by this CA. "
            "Amplifier will not import it, request administrator access, change policy or bypass "
            "a browser warning. Select No to keep the app running without browser entry.\n\n"
            "Certificate SHA-256: " + ":".join(f"{byte:02X}" for byte in fingerprint),
            confirm=True):
        os.startfile(str(root / "export/local-ca.cer"))
        owner_notice("After your own certificate decision, select OK to recheck the local connection.")
        if browser_trust_ready(urls, pin):
            return True
    owner_notice("Amplifier is still running in the background. Browser entry is waiting for "
                 "trusted local HTTPS. No trust or security policy was changed by Amplifier. "
                 "Open Amplifier again after your certificate decision.")
    return False


async def prepare_background(root, launcher, environment, *, port=None, identity=None):
    """Prepare/start one owned logon host, or inspect/restart that exact host.

    A recorded task is never reconfigured or resynced by a shortcut click.
    Failed setup retains its task/state for normal status/stop and reconciliation.

    `identity`, when given, runs once in first setup before the CLI checks TLS.
    The consumer entry uses it to create a key-less identity in its own process.

    Without an explicit port, first setup selects a free loopback port and saves
    it; every later start reuses the saved port, because the browser origin
    (installed app, drafts) includes it. A saved port that cannot be claimed
    raises AddressUnavailable; only the owner's confirmed repair changes it.
    """
    from . import windows_ports, windows_service
    from .updates import process

    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise ValueError("Installation port must be a TCP port number.")
    root, launcher = Path(root), Path(launcher)
    home = root / "app"
    task, spec = await asyncio.to_thread(windows_service._read, home)
    scope = [str(launcher), "--data-dir", str(home), "--workspace", str(root / "workspace")]

    async def command(*args, timeout=90):
        return await process(*scope, *args, env=environment, cwd=root, timeout=timeout)

    preparation = None
    if spec is not None:
        if (Path(spec.python).parent != launcher.parent
                or Path(spec.workspace) != root / "workspace"):
            raise RuntimeError("The existing task belongs to another installed launcher.")
        if task.get("instances") == 0 and task.get("state") == 3:
            from .deployment import load_server_config
            saved = load_server_config(home)["port"]
            kind = await asyncio.to_thread(address_kind, saved, spec)
            if kind in {"in-use", "reserved"}:
                raise AddressUnavailable(kind, saved)
            if kind != "free":
                raise RuntimeError("The existing task is not in a safe start/reopen state.")
            await command("service", "start")
        elif task.get("instances") != 1 or task.get("state") != 4:
            raise RuntimeError("The existing task is not in a safe start/reopen state.")
    else:
        # An installation that already saved its port keeps it and its settings.
        saved = None if port is not None else await asyncio.to_thread(saved_port, home)
        known = port if port is not None else saved
        if known is not None:
            kind = await asyncio.to_thread(address_kind, known)
            if kind != "free":
                raise AddressUnavailable(kind, known)
        if saved is None:
            await command("config", "set", "bind", "[127.0.0.1]")
        if identity is not None:
            await asyncio.to_thread(identity)
        await command("setup-tls")
        preparation = json.loads(await command("runtime", "prepare", "--json", timeout=1800))
        if preparation.get("ok") is not True or preparation.get("prepared") is not True:
            raise RuntimeError("Runtime dependency preparation did not complete; no task was registered.")
        if saved is None:
            # Select last: the host's own listener claims the port seconds later.
            if port is None:
                port = await asyncio.to_thread(windows_ports.select)
            await command("config", "set", "port", str(port),
                          "public_origins", f"[https://127.0.0.1:{port}]")
        await command("service", "install")

    health = json.loads(await command("service", "status", "--wait", "60", "--json"))
    if not (health.get("ok") is True and health.get("processVerified") is True
            and health.get("responding") is True):
        # The test bind is not ownership. If another owner took the port before
        # this host's listener did, say so instead of a generic failure.
        conflict = await asyncio.to_thread(_conflict, home)
        if conflict is not None:
            raise conflict
        raise RuntimeError("The owned background host is not ready; retain its status and private logs.")
    return {"background": True, "created": spec is None, "health": health,
            "runtime": preparation}