"""Opt-in Amplifier installation links. Status is the only peer capability."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import socket
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from filelock import FileLock, Timeout
from yaml import YAMLError

from .deployment import load_server_config, save_server_config, validate_origin, write_private
from .errors import AppError
from .linked_discovery import DEVICE, Discovery, Unavailable

PREFIX = "/api/linked-instances/v1"
PEER_PREFIX = PREFIX + "/peer"
PEER_PATHS = frozenset(PEER_PREFIX + "/" + operation for operation in ("enroll", "status", "identity"))
OFFER_PREFIX = "amp-link-v1."
OFFER_TTL = 300
MAX_BODY = 4096
MAX_STATE = 2 * 1024 * 1024
MAX_COMMANDS = 512
MAX_LINKS = 64
LOCAL_TLS = {"method": "ca", "cert": "config/tls/leaf.crt", "key": "config/tls/leaf.key"}
IDENTITY = r"[a-f0-9]{32}"
DIGEST = r"[a-f0-9]{64}"
SECRET = r"[A-Za-z0-9_-]{43}"
COMMAND = r"[A-Za-z0-9_.:-]{1,120}"

ERRORS = {
    "link_invalid": (400, "The link request is invalid."),
    "link_unsupported": (403, "This installation does not permit that link operation."),
    "link_not_found": (404, "No matching link was found."),
    "link_revoked": (403, "This link was revoked or is no longer authorized."),
    "link_offer_used": (409, "This offer was already used."),
    "link_offer_expired": (410, "This offer expired or is no longer valid."),
    "link_already_linked": (409, "These installations already have a link in this direction. Revoke the existing link on both installations before pairing again."),
    "link_ambiguous": (409, "More than one link is active in this direction. Revoke the extra links before checking availability."),
    "link_command_conflict": (409, "This command ID is already bound to a different request."),
    "link_verification_failed": (502, "The other Amplifier installation could not be verified."),
    "link_unavailable": (503, "The other Amplifier installation did not answer."),
    "link_join_unconfirmed": (503, "Joining was not confirmed. Reconcile with the same code and command ID."),
    "link_capacity": (409, "This installation reached its link or receipt limit."),
    "link_store_unavailable": (503, "The private link store is unavailable."),
    "link_setup_unavailable": (503, "Linking settings could not be saved. Read the settings again before retrying."),
}


def failure(code):
    status, message = ERRORS[code]
    return AppError(message, status, code=code)


def record(value, **fields):
    if not isinstance(value, dict) or value.keys() != fields.keys():
        return False
    return all(isinstance(value[key], str) and re.fullmatch(pattern, value[key])
               for key, pattern in fields.items())


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _origin(value):
    try:
        if (len(value) > 300 or not value.startswith("https://") or
                validate_origin(value) != value or any(ord(char) < 33 for char in value)):
            raise ValueError
    except (ValueError, TypeError):
        raise failure("link_invalid") from None
    return value


def _encode_offer(offer):
    return OFFER_PREFIX + base64.urlsafe_b64encode(_json(offer)).decode().rstrip("=")


def _parse_offer(code):
    if not isinstance(code, str) or not re.fullmatch(r"amp-link-v1\.[A-Za-z0-9_-]{1,1024}", code):
        raise failure("link_invalid")
    data = code[len(OFFER_PREFIX):]
    try:
        offer = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
    except (ValueError, RecursionError):
        raise failure("link_invalid") from None
    if (not record(offer, origin=r".{1,300}", instanceId=IDENTITY, fingerprint=DIGEST, secret=SECRET)
            or _encode_offer(offer) != code):
        raise failure("link_invalid")
    _origin(offer["origin"])
    return offer


@dataclass(frozen=True)
class DirectRoute:
    origin: str
    fingerprint: str


class _EnrollmentRefused(AppError):
    """A typed, pinned peer reply that settles this exact enrollment."""


class _EnrollmentPrecondition(AppError):
    """This attempt could not enroll, but that says nothing about a prior attempt."""


# The issuer checks the saved enrollment/refusal before testing the offer TTL.
# These replies apply to the exact enrollment payload, not another command.
_ENROLLMENT_REFUSALS = frozenset({
    "link_offer_used", "link_offer_expired", "link_revoked", "link_already_linked",
})
_ENROLLMENT_PRECONDITIONS = frozenset({"link_unsupported", "link_verification_failed"})


class DirectTransport:
    """Typed transport seam. Never forward generic URLs, methods or app actions."""

    async def enroll(self, route, payload):
        return await self._post(route, "enroll", payload)

    async def status(self, route, payload, credential):
        return await self._post(route, "status", payload, credential)

    async def discovered_status(self, route, saved, payload, current_credential):
        """Prove identity on the pinned connection before sending the grant."""
        authority = urlsplit(route.origin)
        origin = f"https://{saved['address']}:{saved['port']}"
        trace = aiohttp.TraceConfig()
        phase = "identity"

        async def open_identity(_session, _context, _params):
            nonlocal phase
            if phase != "identity":
                # In particular, refuse if the idle connection closed after
                # identity proof and the connector attempts a fresh TLS session.
                raise failure("link_unavailable")
            phase = "opened"

        async def reuse_for_status(_session, _context, _params):
            nonlocal phase
            if phase != "status":
                raise failure("link_verification_failed")
            phase = "reused"

        trace.on_connection_create_start.append(open_identity)
        trace.on_connection_reuseconn.append(reuse_for_status)

        def direct_socket(addr_info):
            family, kind, proto, _, _ = addr_info
            sock = socket.socket(family, kind, proto)
            try:
                if saved["deviceId"] is None and hasattr(socket, "SO_DONTROUTE"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_DONTROUTE, 1)
                elif saved.get("interface") == "tailscale0" and hasattr(socket, "SO_BINDTODEVICE"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"tailscale0\0")
                else:
                    raise failure("link_unavailable")
                return sock
            except BaseException:
                sock.close()
                raise

        try:
            async with asyncio.timeout(5):
                connector = aiohttp.TCPConnector(limit=1, socket_factory=direct_socket)
                async with aiohttp.ClientSession(connector=connector, trace_configs=[trace],
                                                 cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
                                                 auto_decompress=False) as client:
                    options = {"ssl": aiohttp.Fingerprint(bytes.fromhex(route.fingerprint)),
                               "allow_redirects": False, "server_hostname": authority.hostname}
                    host = {"Host": authority.netloc}
                    async with client.post(origin + PEER_PREFIX + "/identity",
                                           json={"instanceId": payload["instanceId"]}, headers=host,
                                           **options) as response:
                        if response.status != 200:
                            raise failure("link_verification_failed")
                        identity = await self._result(response)
                    if identity != {"instanceId": payload["instanceId"]} or phase != "opened":
                        raise failure("link_verification_failed")
                    phase = "status"
                    headers = {**host, "Authorization": "Bearer ali_" + current_credential()}
                    async with client.post(origin + PEER_PREFIX + "/status", json=payload, headers=headers,
                                           **options) as response:
                        if phase != "reused":
                            raise failure("link_verification_failed")
                        return await self._result(response)
        except (aiohttp.ServerFingerprintMismatch, aiohttp.ClientSSLError, ValueError, RecursionError):
            raise failure("link_verification_failed") from None
        except (aiohttp.ClientError, OSError, TimeoutError):
            raise failure("link_unavailable") from None

    @staticmethod
    async def _result(response, *, enrollment=False):
        statuses = {200, 403, 409, 410, 502} if enrollment else {200, 403, 409, 410}
        if response.status in statuses and response.content_type == "application/json":
            # Read to EOF even for a refusal. A valid JSON prefix doesn't make
            # an oversized or truncated response an authoritative refusal.
            data = bytearray()
            async for chunk in response.content.iter_chunked(MAX_BODY):
                data.extend(chunk)
                if len(data) > MAX_BODY:
                    raise failure("link_verification_failed")
            packet = json.loads(data)
            if response.status == 200 and isinstance(packet, dict) and set(packet) == {"result"}:
                return packet["result"]
            code = packet.get("code") if isinstance(packet, dict) else None
            if isinstance(code, str) and code in ERRORS and ERRORS[code][0] == response.status:
                if enrollment:
                    if (set(packet) == {"code", "error", "accepted"}
                            and packet["accepted"] is False and isinstance(packet["error"], str)):
                        if code in _ENROLLMENT_REFUSALS:
                            raise _EnrollmentRefused(ERRORS[code][1], response.status, code=code)
                        if code in _ENROLLMENT_PRECONDITIONS:
                            raise _EnrollmentPrecondition(ERRORS[code][1], response.status, code=code)
                elif code in _ENROLLMENT_REFUSALS | {"link_unsupported", "link_ambiguous"}:
                    raise failure(code)
        raise failure("link_verification_failed")

    async def _post(self, route, operation, payload, credential=None):
        try:
            headers = {"Authorization": "Bearer ali_" + credential} if credential is not None else {}
            # Fingerprint verification occurs on each new TLS connection before
            # aiohttp transmits HTTP headers or body. No ambient roots, cookies,
            # redirects, proxy environment, or shared connection pool.
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5),
                                             cookie_jar=aiohttp.DummyCookieJar(),
                                             trust_env=False, auto_decompress=False) as client:
                async with client.post(route.origin + PEER_PREFIX + "/" + operation,
                                       json=payload, headers=headers,
                                       ssl=aiohttp.Fingerprint(bytes.fromhex(route.fingerprint)),
                                       allow_redirects=False) as response:
                    return await self._result(response, enrollment=operation == "enroll")
        except (aiohttp.ServerFingerprintMismatch, aiohttp.ClientSSLError) as exc:
            if operation == "enroll":
                # These connector errors happen before the HTTP request is sent.
                status, message = ERRORS["link_verification_failed"]
                raise _EnrollmentPrecondition(message, status, code="link_verification_failed") from None
            if isinstance(exc, aiohttp.ServerFingerprintMismatch):
                raise failure("link_verification_failed") from None
            raise failure("link_unavailable") from None
        except (ValueError, RecursionError):
            raise failure("link_verification_failed") from None
        except (aiohttp.ClientError, OSError):
            raise failure("link_unavailable") from None


class LinkStore:
    """One private, versioned ledger. Never reconstruct authority after read failure."""

    def __init__(self, root):
        self.root = root
        self.path = root / "config" / "linked-instances" / "v1.json"

    def _read(self):
        if os.name == "nt":
            from .windows_files import directory
            with directory(self.path.parent, owned_root=self.root, create=False) as pinned:
                return pinned.read_private(self.path.name, limit=MAX_STATE)
        with self.path.open("rb") as stream:
            return stream.read(MAX_STATE + 1)

    @contextmanager
    def transaction(self):
        try:
            if os.name == "nt":
                from .windows_files import directory
                with directory(self.path.parent, owned_root=self.root, create=True):
                    pass
            else:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with FileLock(self.path.with_suffix(".lock"), timeout=0, mode=0o600):
                try:
                    data = self._read()
                except FileNotFoundError:
                    data = None
                if data is None:
                    state = {"version": 1, "instanceId": uuid.uuid4().hex, "commands": {}, "links": {}}
                else:
                    state = json.loads(data)
                    if (len(data) > MAX_STATE or type(state) is not dict
                            or type(state.get("version")) is not int or state["version"] != 1
                            or not re.fullmatch(IDENTITY, state.get("instanceId", ""))
                            or not all(type(state.get(key)) is dict for key in ("commands", "links"))):
                        raise failure("link_store_unavailable")
                before = _json(state)
                yield state
                after = _json(state)
                if len(after) > MAX_STATE:
                    raise failure("link_capacity")
                if data is None or before != after:
                    write_private(self.path, after, owned_root=self.root)
                    if hasattr(os, "O_DIRECTORY"):
                        descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
        except (OSError, Timeout, ValueError, TypeError, KeyError, RecursionError):
            raise failure("link_store_unavailable") from None


def _link(row):
    return {key: row[key] for key in ("grantId", "instanceId", "direction", "scope", "origin", "revoked")}


def _command(state, operation, args, *, create=False, reserve_revocation=False):
    identity = args["commandId"]
    digest = _digest(_json([operation, args]))
    saved = state["commands"].get(identity)
    if saved is not None:
        if saved["digest"] != digest:
            raise failure("link_command_conflict")
        return saved
    if create:
        # Never let ordinary commands or repeated revokes consume the first
        # revocation slot of an active grant.
        if len(state["commands"]) >= MAX_COMMANDS + (MAX_LINKS if reserve_revocation else 0):
            raise failure("link_capacity")
        saved = {"digest": digest}
        state["commands"][identity] = saved
    return saved


def _links_to(state, peer, direction):
    return [row for row in state["links"].values()
            if row["instanceId"] == peer and row["direction"] == direction]


def _active(rows):
    return [row for row in rows if not row["revoked"]]


def _reserve_join(state, saved):
    # Retain the same credential before the first network await and across a restart.
    occupied = len(state["links"]) + sum("pending" in cmd for cmd in state["commands"].values())
    if occupied >= MAX_LINKS:
        raise failure("link_capacity")
    saved["pending"] = {"credential": secrets.token_urlsafe(32), "id": uuid.uuid4().hex}


def _add_link(state, row):
    if _active(_links_to(state, row["instanceId"], row["direction"])):
        raise failure("link_already_linked")
    if len(state["links"]) >= MAX_LINKS:
        raise failure("link_capacity")
    if row["grantId"] in state["links"]:
        raise failure("link_command_conflict")
    state["links"][row["grantId"]] = row


def _new_link(grant_id, peer, direction, origin):
    return {"grantId": grant_id, "instanceId": peer, "direction": direction,
            "scope": "status", "origin": origin, "revoked": False}


def _leaf_fingerprint(root, config):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from . import tls
    # Existing app-owned leaf only. A changed key/leaf requires an explicit new
    # pair; no automatic trust-on-first-use or trust expansion on route changes.
    if config.get("tls") != LOCAL_TLS:
        raise failure("link_unsupported")
    try:
        if os.name == "nt":
            from .windows_files import directory
            with directory(tls.tls_dir(root), owned_root=root, create=False) as pinned:
                content = pinned.read_private("leaf.crt", limit=65536)
        else:
            with (tls.tls_dir(root) / "leaf.crt").open("rb") as stream:
                content = stream.read(65537)
        if len(content) > 65536:
            raise ValueError
        return x509.load_pem_x509_certificate(content).fingerprint(hashes.SHA256()).hex()
    except (OSError, ValueError):
        raise failure("link_unsupported") from None


class LinkedInstances:
    def __init__(self, root, config, *, transport=None, clock=time.time, discovery=None):
        self.config = config
        self.origins = tuple(origin for origin in config["public_origins"] if origin.startswith("https://"))
        self.enabled = config.get("linked_instances") is True and bool(self.origins) and config.get("tls") == LOCAL_TLS
        self.store = LinkStore(root)
        self.generation = uuid.uuid4().hex
        self.transport = DirectTransport() if transport is None else transport
        self.clock = clock
        self.discovery = Discovery(config, backend=discovery)
        self.route_writes = asyncio.Lock()
        self.route_checks = asyncio.Semaphore(4)

    def require_enabled(self):
        if not self.enabled:
            raise failure("link_unsupported")

    def setup(self, args=None):
        from .tls import tls_ready

        live = self.discovery.capabilities()
        enabled = self.config.get("linked_instances") is True
        blockers = []
        try:
            saved = load_server_config(self.store.root, create=False)
            if args is not None:
                discovery = saved["linked_instances_discovery"]
                if saved["linked_instances"] != args["enabled"] or any(
                    discovery[key] != args[key] for key in live
                ):
                    saved = save_server_config(self.store.root, {
                        **saved, "linked_instances": args["enabled"],
                        "linked_instances_discovery": {**discovery, **{key: args[key] for key in live}},
                    })
        except (OSError, ValueError, YAMLError, RecursionError):
            if args is not None:
                raise failure("link_setup_unavailable") from None
            saved = None
            blockers.append("saved_configuration_unavailable")
        choice = saved["linked_instances"] if saved is not None else enabled
        discovery = {key: saved["linked_instances_discovery"][key] for key in live} if saved is not None else live
        # Effective startup config can include explicit launch flags. They
        # apply over the saved settings, not the other way around. Do not copy
        # startup overrides into server.yaml or claim the socket was tested.
        if not self.origins:
            blockers.append("https_origin_required")
        if self.config.get("tls") != LOCAL_TLS:
            blockers.append("app_tls_required")
        elif not tls_ready(self.store.root, self.config):
            blockers.append("app_tls_unavailable")
        if "saved_configuration_unavailable" in blockers:
            status = "unavailable"
        elif not choice and not enabled:
            status = "off"
        elif not choice:
            status = "pending"
        elif blockers:
            status = "unavailable"
        elif enabled != choice or discovery != live:
            status = "pending"
        else:
            status = "active"
        return {"status": status, "enabled": choice, "discovery": discovery,
                "origins": list(self.origins), "blockers": blockers,
                "lanExpiresAt": self.discovery.expires_at()}

    def list(self, *, outgoing_only=False):
        self.require_enabled()
        with self.store.transaction() as state:
            result = {"instanceId": state["instanceId"], "origins": [] if outgoing_only else list(self.origins),
                      "links": [_link(row) for row in state["links"].values()
                                if not outgoing_only or row["direction"] == "outgoing"]}
            if not outgoing_only:
                result["discovery"] = self.discovery.capabilities()
            return result

    def offer(self, args):
        if not record(args, commandId=COMMAND, origin=r".{1,300}"):
            raise failure("link_invalid")
        origin = _origin(args["origin"])
        if origin not in self.origins:
            raise failure("link_unsupported")
        with self.store.transaction() as state:
            saved = _command(state, "offer", args, create=True)
            if "offer" not in saved:
                saved["offer"] = {"origin": origin, "instanceId": state["instanceId"],
                                  "fingerprint": _leaf_fingerprint(self.store.root, self.config),
                                  "secret": secrets.token_urlsafe(32)}
                saved["expiresAt"] = int(self.clock()) + OFFER_TTL
            code = _encode_offer(saved["offer"])
            expires = saved["expiresAt"]
        from qrcode import make
        from qrcode.image.pil import PilImage
        image = io.BytesIO()
        make(code, image_factory=PilImage, box_size=4, border=4).save(image, format="PNG")
        return {"code": code, "qrPayload": code, "expiresAt": expires,
                "qrPngBase64": base64.b64encode(image.getvalue()).decode()}

    async def join(self, args):
        fields = {"commandId": COMMAND, "code": r".{1,1100}"}
        if isinstance(args, dict) and "expectedInstanceId" in args:
            fields["expectedInstanceId"] = IDENTITY
        if not record(args, **fields):
            raise failure("link_invalid")
        offer = _parse_offer(args["code"])
        with self.store.transaction() as state:
            saved = _command(state, "join", args)
            if offer["instanceId"] != args.get("expectedInstanceId", offer["instanceId"]):
                raise failure("link_verification_failed")
            if offer["instanceId"] == state["instanceId"]:
                raise failure("link_invalid")
            if saved is None:
                saved = _command(state, "join", args, create=True)
            if "result" in saved:
                return saved["result"]
            retry = "pending" in saved
            if not retry and "conflict" not in saved:
                # A new invitation is not permission to substitute a route
                # for a grant already retained here. Keep the refusal bound
                # to this command even if the existing link is later revoked.
                if _active(_links_to(state, offer["instanceId"], "outgoing")):
                    saved["conflict"] = True
                else:
                    _reserve_join(state, saved)
            conflict = saved.get("conflict") is True
            pending = saved.get("pending")
            requester = state["instanceId"]
        if conflict:
            raise failure("link_already_linked")
        route = DirectRoute(offer["origin"], offer["fingerprint"])
        try:
            receipt = await self.transport.enroll(route, {
                "instanceId": offer["instanceId"], "requesterId": requester, "enrollmentId": pending["id"],
                "proof": offer["secret"], "credentialDigest": _digest(pending["credential"].encode()),
            })
        except _EnrollmentPrecondition:
            # The saved reservation has no outcome for the previous dispatch.
            # A subsequent TLS/admission failure cannot settle it.
            if retry:
                raise failure("link_join_unconfirmed") from None
            raise
        except _EnrollmentRefused as exc:
            if exc.code == "link_already_linked":
                # The authenticated issuer refused before adding authority.
                # Retire only this exact sender reservation, not the active link.
                try:
                    with self.store.transaction() as state:
                        saved = _command(state, "join", args)
                        if saved is not None and "result" in saved:
                            return saved["result"]
                        if saved is not None and saved.get("pending") == pending and state["instanceId"] == requester:
                            saved["conflict"] = True
                            del saved["pending"]
                except AppError:
                    raise failure("link_join_unconfirmed") from None
            raise
        except (AppError, OSError):
            raise failure("link_join_unconfirmed") from None
        if (not record(receipt, grantId=IDENTITY, instanceId=IDENTITY)
                or receipt["instanceId"] != offer["instanceId"]):
            raise failure("link_join_unconfirmed")
        try:
            with self.store.transaction() as state:
                saved = _command(state, "join", args)
                if saved is not None and "result" in saved:
                    return saved["result"]
                if (saved is None or saved.get("pending") != pending
                        or state["instanceId"] != requester):
                    raise failure("link_join_unconfirmed")
                row = _new_link(receipt["grantId"], offer["instanceId"], "outgoing", route.origin)
                _add_link(state, {**row, "fingerprint": route.fingerprint, "credential": pending["credential"]})
                saved["result"] = row
                del saved["pending"]
                return row
        except AppError:
            # The issuer may already have granted access, and the local atomic
            # replacement may have happened even if the subsequent sync failed.
            raise failure("link_join_unconfirmed") from None

    def enroll(self, args, origin):
        if not record(args, instanceId=IDENTITY, requesterId=IDENTITY, enrollmentId=IDENTITY,
                      proof=SECRET, credentialDigest=DIGEST):
            raise failure("link_invalid")
        with self.store.transaction() as state:
            if args["instanceId"] != state["instanceId"]:
                raise failure("link_verification_failed")
            proof = args["proof"].encode()
            saved = next((row for row in state["commands"].values() if "offer" in row
                          and hmac.compare_digest(row["offer"]["secret"].encode(), proof)), None)
            if not saved or saved["offer"]["origin"] != origin:
                raise failure("link_offer_expired")
            digest = _digest(_json(args))
            if "enrollment" in saved:
                prior = saved["enrollment"]
                if not hmac.compare_digest(prior["digest"], digest):
                    raise failure("link_offer_used")
                if state["links"][prior["grantId"]]["revoked"]:
                    raise failure("link_revoked")
                return {"grantId": prior["grantId"], "instanceId": state["instanceId"]}
            if "refusal" in saved:
                if not hmac.compare_digest(saved["refusal"], digest):
                    raise failure("link_offer_used")
            else:
                if self.clock() >= saved["expiresAt"]:
                    raise failure("link_offer_expired")
                if _active(_links_to(state, args["requesterId"], "incoming")):
                    saved["refusal"] = digest
                else:
                    grant_id = uuid.uuid4().hex
                    row = _new_link(grant_id, args["requesterId"], "incoming", None)
                    _add_link(state, {**row, "credentialDigest": args["credentialDigest"]})
                    saved["enrollment"] = {"digest": digest, "grantId": grant_id}
                    return {"grantId": grant_id, "instanceId": state["instanceId"]}
        raise failure("link_already_linked")

    def revoke(self, args):
        if not record(args, commandId=COMMAND, grantId=IDENTITY):
            raise failure("link_invalid")
        with self.store.transaction() as state:
            saved = _command(state, "revoke", args)
            if saved is not None:
                return saved["result"]
            row = state["links"].get(args["grantId"])
            if row is None:
                raise failure("link_not_found")
            saved = _command(state, "revoke", args, create=True, reserve_revocation=not row["revoked"])
            row["revoked"] = True
            row.pop("credential", None)
            row.pop("credentialDigest", None)
            row.pop("routes", None)
            row.pop("routeReceipts", None)
            saved["result"] = _link(row)
            return saved["result"]

    def _own_id(self):
        with self.store.transaction() as state:
            return state["instanceId"]

    @staticmethod
    def _outgoing(state, identity):
        rows = _links_to(state, identity, "outgoing")
        active = _active(rows)
        if not active:
            raise failure("link_revoked" if rows else "link_not_found")
        if len(active) != 1:
            raise failure("link_ambiguous")
        return active[0]

    def _recheck(self, state, row):
        active = self._outgoing(state, row["instanceId"])
        if active["grantId"] != row["grantId"] or active.get("credential") != row["credential"]:
            raise failure("link_revoked")
        if active.get("routes") != row.get("routes"):
            raise failure("link_command_conflict")
        return active

    async def _discovered_status(self, row, requester, saved):
        def current_credential():
            with self.store.transaction() as state:
                return self._recheck(state, row)["credential"]

        if saved["deviceId"] is not None:
            try:
                interface = await self.discovery.backend.tailscale_interface(saved["address"])
            except Unavailable:
                raise failure("link_unavailable") from None
            saved = {**saved, "interface": interface}
        result = await self.transport.discovered_status(
            DirectRoute(row["origin"], row["fingerprint"]), saved,
            {"instanceId": row["instanceId"], "requesterId": requester, "grantId": row["grantId"]},
            current_credential,
        )
        self._verified_status(result, row["instanceId"])
        return result

    @staticmethod
    def _verified_status(result, identity):
        if (not record(result, instanceId=IDENTITY, generationId=IDENTITY, availability="online")
                or result["instanceId"] != identity):
            raise failure("link_verification_failed")

    async def remember(self, args):
        if not record(args, candidateId=IDENTITY, grantId=IDENTITY):
            raise failure("link_invalid")
        try:
            async with asyncio.timeout(13), self.route_writes, self.route_checks:
                return await self._remember(args)
        except TimeoutError:
            raise failure("link_unavailable") from None

    async def _remember(self, args):
        with self.store.transaction() as state:
            row = state["links"].get(args["grantId"])
            if not row or row["direction"] != "outgoing":
                raise failure("link_not_found")
            row = self._outgoing(state, row["instanceId"])
            if row["grantId"] != args["grantId"]:
                raise failure("link_revoked")
            requester = state["instanceId"]
            for route, token in row.get("routeReceipts", {}).items():
                if token == args["candidateId"] and self.discovery.capabilities().get(route):
                    return {"grantId": row["grantId"], "instanceId": row["instanceId"], "route": route}
        candidate = self.discovery.candidate(args["candidateId"])
        if candidate is None:
            raise failure("link_not_found")
        if row["instanceId"] != candidate.endpoint.instance_id:
            raise failure("link_verification_failed")
        saved = candidate.saved()
        if not await self.discovery.usable(candidate.route, saved, row["instanceId"]):
            raise failure("link_unavailable")
        await self._discovered_status(row, requester, saved)
        with self.store.transaction() as state:
            active = self._recheck(state, row)
            active.setdefault("routes", {})[candidate.route] = saved
            # One current receipt per route; recover the last confirmed save
            # even after the discovery token expires or the process restarts.
            active.setdefault("routeReceipts", {})[candidate.route] = args["candidateId"]
        return {"grantId": row["grantId"], "instanceId": row["instanceId"], "route": candidate.route}

    async def status(self, args, *, owner=False):
        if not record(args, instanceId=IDENTITY):
            raise failure("link_invalid")
        identity = args["instanceId"]
        with self.store.transaction() as state:
            row = self._outgoing(state, identity)
            requester = state["instanceId"]
        unverified = False
        for route in ("lan", "tailscale"):
            saved = row.get("routes", {}).get(route)
            if saved is None:
                continue
            try:
                async with asyncio.timeout(12), self.route_checks:
                    if not await self.discovery.usable(route, saved, identity):
                        continue
                    result = await self._discovered_status(row, requester, saved)
            except TimeoutError:
                continue
            except AppError as exc:
                if exc.code == "link_verification_failed":
                    unverified = True
                elif exc.code != "link_unavailable":
                    raise
                continue
            break
        else:
            route = "configured"
            with self.store.transaction() as state:
                self._recheck(state, row)
            try:
                result = await self.transport.status(
                    DirectRoute(row["origin"], row["fingerprint"]),
                    {"instanceId": identity, "requesterId": requester, "grantId": row["grantId"]}, row["credential"])
            except AppError as exc:
                if unverified and exc.code == "link_unavailable":
                    raise failure("link_verification_failed") from None
                raise
            self._verified_status(result, identity)
        with self.store.transaction() as state:
            self._recheck(state, row)
        if owner and any(self.discovery.capabilities().values()):
            return {**result, "route": route}
        return result

    def peer_identity(self, args):
        if not record(args, instanceId=IDENTITY) or args["instanceId"] != self._own_id():
            raise failure("link_verification_failed")
        return args

    def peer_status(self, args, authorization):
        if not record(args, instanceId=IDENTITY, requesterId=IDENTITY, grantId=IDENTITY):
            raise failure("link_invalid")
        if not re.fullmatch("Bearer ali_" + SECRET, authorization):
            raise failure("link_revoked")
        digest = _digest(authorization.removeprefix("Bearer ali_").encode())
        with self.store.transaction() as state:
            row = state["links"].get(args["grantId"])
            if not (state["instanceId"] == args["instanceId"] and row and not row["revoked"]
                    and row["direction"] == "incoming" and row["scope"] == "status"
                    and row["instanceId"] == args["requesterId"]
                    and hmac.compare_digest(row["credentialDigest"], digest)):
                raise failure("link_revoked")
            if len(_active(_links_to(state, args["requesterId"], "incoming"))) != 1:
                raise failure("link_ambiguous")
            return {"instanceId": state["instanceId"], "generationId": self.generation, "availability": "online"}


async def agent_route(links, operation, args):
    # This adapter has no ambient owner credential, pair/revoke entry, or
    # access to a destination's app dispatcher.
    if operation != "get_state":
        raise failure("link_unsupported")
    if args == {"path": "/linkedInstances"}:
        return links.list(outgoing_only=True)
    if set(args) != {"target"}:
        raise failure("link_unsupported")
    links.require_enabled()
    return await links.status(args["target"])


async def _read_body(request):
    if request.content_type != "application/json" or request.headers.get("Content-Encoding", "identity") != "identity":
        raise failure("link_invalid")
    data = bytearray()
    async for chunk in request.content.iter_chunked(MAX_BODY):
        data.extend(chunk)
        if len(data) > MAX_BODY:
            raise failure("link_invalid")
    try:
        return json.loads(data)
    except (ValueError, RecursionError):
        raise failure("link_invalid") from None


def install(app, service):
    from .auth import allowed_origin
    from .browser_entry import _on_this_computer

    links = service.linked_instances
    app["linked_instance_peer_paths"] = PEER_PATHS

    async def owner(request):
        operation = request.match_info["operation"]
        setup = operation in {"setup", "setup-save"}
        # Only the owner control credential and regular authenticated browser
        # sessions get a history scope. Restricted/legacy devices never do.
        # A default HTTP install can stage a preference on actual loopback,
        # but never invoke pairing or discovery from that listener.
        if (not request.get("history_scope") or not (request.secure or (setup and _on_this_computer(request)))
                or len(request.headers.getall("Origin", [])) != 1 or not request.headers.get("Origin")
                or not allowed_origin(request)):
            raise failure("link_unsupported")
        try:
            async with asyncio.timeout(5):
                args = await _read_body(request)
        except TimeoutError:
            raise failure("link_invalid") from None
        if setup:
            if operation == "setup" and args == {}:
                result = links.setup()
            elif (operation == "setup-save" and isinstance(args, dict) and args.keys() == {"enabled", "lan", "tailscale"}
                  and all(type(value) is bool for value in args.values())):
                result = links.setup(args)
            else:
                raise failure("link_invalid")
            return web.json_response({"result": result})
        links.require_enabled()
        if operation == "list" and args == {}:
            result = links.list()
        elif operation == "offer":
            result = links.offer(args)
        elif operation == "join":
            result = await links.join(args)
        elif operation == "status":
            result = await links.status(args, owner=True)
        elif operation == "revoke":
            result = links.revoke(args)
        elif operation == "route-remember":
            result = await links.remember(args)
        elif operation in {"lan-browse", "lan-discoverable", "tailscale-devices", "tailscale-find"}:
            discovery = links.discovery
            if not discovery.capabilities()[operation.split("-", 1)[0]]:
                raise failure("link_unsupported")
            try:
                async with asyncio.timeout(8):
                    if operation == "lan-browse" and args == {}:
                        result = await discovery.browse(links._own_id())
                    elif (operation == "lan-discoverable" and isinstance(args, dict) and args.keys() == {"enabled"}
                          and type(args["enabled"]) is bool):
                        result = await discovery.discoverable(args["enabled"], links._own_id())
                    elif operation == "tailscale-devices" and args == {}:
                        result = await discovery.devices()
                    elif operation == "tailscale-find" and record(args, deviceId=DEVICE.pattern):
                        result = await discovery.find(args["deviceId"], links._own_id())
                    else:
                        raise failure("link_invalid")
            except TimeoutError:
                key, value = {"lan-discoverable": ("expiresAt", discovery.expires_at()),
                              "tailscale-devices": ("devices", [])}.get(operation, ("instances", []))
                result = {"status": "unavailable", key: value}
        else:
            raise failure("link_invalid")
        return web.json_response({"result": result})

    async def peer(request):
        links.require_enabled()
        if not request.secure or "Origin" in request.headers or "Cookie" in request.headers:
            raise failure("link_unsupported")
        try:
            origin = validate_origin(f"https://{request.host}")
        except ValueError:
            raise failure("link_unsupported") from None
        if origin not in links.origins:
            raise failure("link_unsupported")
        try:
            async with asyncio.timeout(5):
                args = await _read_body(request)
        except TimeoutError:
            raise failure("link_invalid") from None
        if request.path == PEER_PREFIX + "/identity":
            if "Authorization" in request.headers:
                raise failure("link_unsupported")
            result = links.peer_identity(args)
        elif request.path == PEER_PREFIX + "/enroll":
            result = links.enroll(args, origin)
        else:
            result = links.peer_status(args, request.headers.get("Authorization", ""))
        return web.json_response({"result": result})

    app.router.add_post(PREFIX + "/{operation:setup|setup-save|list|offer|join|status|revoke|"
                        "lan-browse|lan-discoverable|tailscale-devices|tailscale-find|route-remember}", owner)
    for path in sorted(PEER_PATHS):
        app.router.add_post(path, peer)
    app.on_shutdown.append(links.discovery.close)