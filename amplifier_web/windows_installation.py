"""Compose the installed owner-logon setup from the public CLI boundaries.

No trust-store import, credential transfer, force stop, or task replacement is
performed here. The bootstrap owns verified package installation; Task Scheduler
and the CLI keep ownership of registration, readiness, serving, and removal.
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
    result = dialog(None, message, "Amplifier Unified", 0x40 | (0x4 if confirm else 0))
    if not result:
        raise OSError("The installation dialog could not be displayed.")
    return result == 6 if confirm else True


def listener_present(port=8942):
    if os.name == "nt":
        from .windows_task_runtime import listener_owners
        return bool(listener_owners("127.0.0.1", port))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except ConnectionRefusedError:
        return False
    except OSError as error:
        raise RuntimeError("Cannot establish whether the installation address is occupied.") from error


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


def review_browser_trust(root, urls, pin):
    """Offer public-certificate review; only the human can change trust."""
    if browser_trust_ready(urls, pin):
        return True
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from .tls import exported_ca_bytes
    from .windows_files import directory
    content = exported_ca_bytes(root / "app")
    fingerprint = x509.load_pem_x509_certificate(content).fingerprint(hashes.SHA256())
    with directory(root / "export", owned_root=root, create=True) as exported:
        exported.publish_private("local-ca.cer", content, replace=True)
    if owner_notice(
            "Amplifier is running in the background. Windows does not yet trust its local "
            "HTTPS certificate. Open the certificate viewer to review it?\n\n"
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


async def prepare_background(root, launcher, environment, *, port=8942):
    """Prepare/start one owned logon host, or inspect/restart that exact host.

    A recorded task is never reconfigured or resynced by a shortcut click.
    Failed setup retains its task/state for normal status/stop and reconciliation.
    """
    from . import windows_service
    from .updates import process

    if type(port) is not int or not 1 <= port <= 65535:
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
            await command("service", "start")
        elif task.get("instances") != 1 or task.get("state") != 4:
            raise RuntimeError("The existing task is not in a safe start/reopen state.")
    else:
        if await asyncio.to_thread(listener_present, port):
            raise RuntimeError("The installation address is occupied; no existing host was adopted or stopped.")
        for key, value in (("port", str(port)), ("bind", "[127.0.0.1]"),
                           ("public_origins", f"[https://127.0.0.1:{port}]")):
            await command("config", "set", key, value)
        await command("setup-tls")
        preparation = json.loads(await command("runtime", "prepare", "--json", timeout=1800))
        if preparation.get("ok") is not True or preparation.get("prepared") is not True:
            raise RuntimeError("Runtime dependency preparation did not complete; no task was registered.")
        await command("service", "install")

    health = json.loads(await command("service", "status", "--wait", "60", "--json"))
    if not (health.get("ok") is True and health.get("processVerified") is True
            and health.get("responding") is True):
        raise RuntimeError("The owned background host is not ready; retain its status and private logs.")
    return {"background": True, "created": spec is None, "health": health,
            "runtime": preparation}