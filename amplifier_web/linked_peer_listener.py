"""Private HTTPS serving owned by linking, not by the browser app."""
from __future__ import annotations

import errno
import ipaddress
import os
import socket
import ssl
import stat
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .deployment import validate_origin, write_private
from .errors import AppError
from .linked_discovery import TAILSCALE, Unavailable, _json, _private_interfaces


class Blocked(Exception):
    """A bounded setup blocker, without an OS error or private path in the wire."""


@dataclass(frozen=True)
class Binding:
    interface: str
    local: ipaddress.IPv4Interface
    route: str

    def accepts(self, transport, port):
        if transport is None:
            return False
        try:
            local = transport.get_extra_info("sockname")
            remote = ipaddress.IPv4Address(transport.get_extra_info("peername")[0])
        except (TypeError, ValueError, IndexError):
            return False
        network = TAILSCALE if self.route == "tailscale" else self.local.network
        return (local == (str(self.local.ip), port) and remote in network
                and (network.prefixlen >= 31 or remote not in (network.network_address, network.broadcast_address)))


async def select_binding(*, tailscale):
    # Keep the admission as narrow as the existing Linux discovery adapter.
    # A missing inventory or device-bind primitive is not permission to open
    # on every interface or to infer eligibility from a hostname.
    if sys.platform != "linux" or not hasattr(socket, "SO_BINDTODEVICE"):
        raise Blocked("peer_listener_unsupported")
    try:
        rows = await _json("ip", "-j", "-4", "addr", "show", "up", "scope", "global")
        private = _private_interfaces(rows)
    except Unavailable:
        raise Blocked("peer_interface_unavailable") from None
    lan, tail = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name, flags = row.get("ifname"), row.get("flags")
        if (not isinstance(name, str) or not name or not name.isascii() or "\0" in name or len(name) > 15
                or not isinstance(flags, list) or "UP" not in flags or "LOOPBACK" in flags):
            continue
        if name == "tailscale0":
            if not tailscale or "POINTOPOINT" not in flags:
                continue
            addresses = row.get("addr_info")
            for addr in addresses if isinstance(addresses, list) else ():
                try:
                    local = ipaddress.IPv4Interface(f"{addr['local']}/{addr['prefixlen']}")
                except (KeyError, TypeError, ValueError):
                    continue
                if local.ip in TAILSCALE:
                    tail.add(Binding(name, local, "tailscale"))
        elif "MULTICAST" in flags:
            for local in private.get(name, ()):
                network = local.network
                if network.prefixlen >= 31 or local.ip not in (network.network_address, network.broadcast_address):
                    lan.add(Binding(name, local, "lan"))
    return lan, tail


def _select(choices, saved):
    lan, tail = choices
    eligible = lan or tail
    if saved is not None:
        # Honor the existing exact endpoint, even if an unrelated interface
        # appeared. Never follow the address onto another interface silently.
        eligible = {item for item in lan | tail if _address(item) == saved["binding"]}
    if not eligible:
        raise Blocked("peer_interface_unavailable")
    if len(eligible) != 1:
        raise Blocked("peer_interface_ambiguous")
    return next(iter(eligible))


def _address(binding):
    return {"interface": binding.interface, "local": str(binding.local), "route": binding.route}


def _bind(binding, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, binding.interface.encode("ascii") + b"\0")
        # Allow restart with connections in TIME_WAIT, not a second listener.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((str(binding.local.ip), port))
        return sock
    except OSError as exc:
        sock.close()
        code = {errno.EADDRINUSE: "peer_port_unavailable",
                errno.EADDRNOTAVAIL: "peer_interface_unavailable",
                errno.ENODEV: "peer_interface_unavailable"}
        raise Blocked(code.get(exc.errno, "peer_listener_unavailable")) from None
    except BaseException:
        sock.close()
        raise


def _new_identity(instance):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Amplifier Peer {instance}")])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM)
            + key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()))


def _identity(store, instance, expected):
    path = store.path.with_name("peer-identity.pem")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        if expected is not None:
            raise ValueError("Established peer identity is missing.") from None
        write_private(path, _new_identity(instance), owned_root=store.root)
        fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError("Peer identity is not a private app-owned file.")
        # Read the same object that OpenSSL opens; never use a replacement
        # inode for TLS after hashing the original.
        pem = stream.read(8193)
        cert_pem, key_pem = pem.split(b"-----END CERTIFICATE-----\n", 1)
        if (len(pem) > 8192 or not cert_pem.startswith(b"-----BEGIN CERTIFICATE-----\n")
                or cert_pem.count(b"-----") != 2
                or not key_pem.startswith(b"-----BEGIN PRIVATE KEY-----\n")
                or not key_pem.endswith(b"-----END PRIVATE KEY-----\n")
                or key_pem.count(b"-----") != 4):
            raise ValueError("Invalid peer identity.")
        cert = x509.load_pem_x509_certificate(cert_pem + b"-----END CERTIFICATE-----\n")
        fingerprint = cert.fingerprint(hashes.SHA256()).hex()
        name = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if (expected is not None and fingerprint != expected
                or not cert.not_valid_before_utc <= datetime.now(UTC) < cert.not_valid_after_utc
                or len(name) != 1 or name[0].value != f"Amplifier Peer {instance}"):
            raise ValueError("Peer identity does not match.")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(f"/proc/self/fd/{fd}")
    return context, fingerprint


def _metadata(state):
    saved = state.get("peerListener")
    if "peerListener" in state and (
        not isinstance(saved, dict) or set(saved) != {"binding", "port", "fingerprint"}
        or type(saved["port"]) is not int or not 1 <= saved["port"] <= 65535
        or not isinstance(saved["binding"], dict)
        or not isinstance(saved["fingerprint"], str)
    ):
        raise Blocked("peer_identity_unavailable")
    return saved


def prepare(store, choices):
    """Reserve a bound socket and save its exact identity before accepting."""
    sock = None
    try:
        # Commit the installation ID before publishing a cert named for it.
        # After a crash between cert and ledger publication, reuse that cert
        # and ID; no peer could have connected before both were committed.
        with store.transaction() as state:
            _select(choices, _metadata(state))
        with store.transaction() as state:
            saved = _metadata(state)
            binding = _select(choices, saved)
            sock = _bind(binding, saved["port"] if saved else 0)
            try:
                context, fingerprint = _identity(store, state["instanceId"], saved["fingerprint"] if saved else None)
            except (OSError, ValueError):
                raise Blocked("peer_identity_unavailable") from None
            port = sock.getsockname()[1]
            if saved is None:
                state["peerListener"] = {"binding": _address(binding), "port": port, "fingerprint": fingerprint}
        return sock, binding, port, context, fingerprint
    except BaseException:
        if sock is not None:
            sock.close()
        raise


class Listener:
    def __init__(self, store):
        self.store = store
        self.runner = None
        self.binding = None
        self.port = None
        self.origin = None
        self.fingerprint = None
        self.blocker = None

    async def open(self, paths, handler, *, tailscale):
        self.blocker = None
        sock, runner = None, None
        started = False
        try:
            choices = await select_binding(tailscale=tailscale)
            sock, binding, port, context, fingerprint = prepare(self.store, choices)
            app = web.Application(client_max_size=4096)
            for path in sorted(paths):
                app.router.add_post(path, handler)
            runner = web.AppRunner(app, access_log=None, shutdown_timeout=1,
                                   handler_cancellation=True, auto_decompress=False, keepalive_timeout=5)
            await runner.setup()
            site = web.SockSite(runner, sock, ssl_context=context, backlog=32)
            await site.start()
            started = True
        except (Blocked, AppError, OSError) as exc:
            self.blocker = (str(exc) if isinstance(exc, Blocked) else
                            "peer_identity_unavailable" if isinstance(exc, AppError) else
                            "peer_port_unavailable" if exc.errno == errno.EADDRINUSE else
                            "peer_listener_unavailable")
        finally:
            if not started:
                try:
                    if runner is not None:
                        await runner.cleanup()
                finally:
                    if sock is not None:
                        sock.close()
        if started:
            self.runner, self.binding, self.port = runner, binding, port
            self.origin = validate_origin(f"https://{binding.local.ip}:{port}")
            self.fingerprint = fingerprint

    async def close(self):
        runner, self.runner = self.runner, None
        # Fence admissions, including existing keep-alive connections, before
        # awaiting any cleanup.
        self.origin = self.fingerprint = self.binding = self.port = None
        self.blocker = None
        if runner is not None:
            await runner.cleanup()