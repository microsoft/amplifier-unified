"""Owner-to-browser handoff, not remote pairing or a fresh Windows Hello check.

The foreground owner launcher reads existing private credentials only.
Listener authentication and grant exchange remain separate from that local read.
A browser on another device signs in with a one-use code that the owner makes
on this computer and types on that device.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import hashlib
import hmac
import ipaddress
from pathlib import Path
import re
import secrets
import sys
import threading
import time
from urllib.parse import urlsplit

ISSUE_PATH = "/api/browser-entry/issue"
REDEEM_PATH = "/api/browser-entry/redeem"
CODE_PATH = "/api/browser-entry/code"
CODE_REDEEM_PATH = "/api/browser-entry/code/redeem"
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # 32 symbols: no 0, 1, I or O.
CODE_LENGTH = 10
_CODE = re.compile(f"[{CODE_ALPHABET}]{{{CODE_LENGTH}}}")
# What a person does when the browser on this computer is not signed in
# (`next_step`). A text says only what this host knows. On Windows no text names
# a command: `amplifier-unified` is on the PATH only of an installation by hand,
# and the command also needs that installation's data folder. A full command
# with both paths would put the Windows user name on a page that needs no
# sign-in; that was decided against. macOS and Linux have no Start-menu entry.
START_MENU = ("Open Amplifier from the Start menu: Amplifier Unified. "
              "Amplifier then opens in your browser.")
ON_THE_PC = "Open Amplifier again on the Windows PC that runs it."
RUN_OPEN = "To open Amplifier, run this: amplifier-unified open."
# What `amplifier-unified open` says on Windows when it gets no ticket. It never
# names itself or the Start menu: its reader ran this command, or the launcher
# ran it and shows its own message.
NO_ANSWER = ("No Amplifier of this version and this data folder answered on this PC. "
             "Make sure that Amplifier is running. If it is updating, wait one minute. Then try again.")
REFUSED = "Amplifier refused the request. Try again."
UNCONFIRMED = "Amplifier's answer was not confirmed. The request was not repeated. Try again."
BUSY = "Amplifier has too many browser openings in progress. Wait one minute. Then try again."
# The line that `amplifier-unified open` prints after the browser accepted the
# address. The Windows launcher reads it, because the exit status says nothing
# to a launcher from a setup file before pull request 2270: there a command
# that an app generation takes over ends with status 0 for its caller, whatever
# it did. The launcher is the code that setup installed and an
# update cannot change it, so this text must stay as it is.
OPENED = "Amplifier opened in your browser."
# How long `open` waits for this installation's host. The host restarts to
# finish an update, also one that it found at its first start; it is then away
# for some seconds, and before that the earlier version still answers.
HOST_WAIT = 60.0
NEW_CODE = "Make sure that Amplifier is running on this PC. Then run this again: amplifier-unified browsers code."
RELOAD = "Could not sign in. Open the sign-in page again and type the code."
GUIDE = ("https://github.com/microsoft/amplifier-unified/blob/main/docs/DEPLOYMENT.md"
         "#windows-host-reach-unified-from-another-device")
NOT_IN_USE = ("The running Amplifier does not use this address yet. Stop Amplifier and start it again. "
              "Then run this again: amplifier-unified browsers code.")


class BrowserEntryUnavailable(RuntimeError):
    pass


def is_windows() -> bool:
    return sys.platform == "win32"


def _local_sockets(request) -> bool:
    """Both real socket ends are this computer's own addresses.

    A request relayed by a configured trusted proxy is never local: the proxy
    connects from this computer on behalf of a browser that may be anywhere.
    """
    from .forwarding import VIA_PROXY
    if request.transport is None or request.get(VIA_PROXY):
        return False
    for name in ("peername", "sockname"):
        address = request.transport.get_extra_info(name)
        if not address or not loopback_host(address[0]) or address[0] == "localhost":
            return False
    return True


def _on_this_computer(request) -> bool:
    """The socket ends and the name in use are this computer's own.

    The name is read as the host boundary reads it. A forward that the owner
    made connects from this computer too, and this check cannot tell it apart.
    """
    from .deployment import canonical_host, is_loopback
    try:
        return _local_sockets(request) and is_loopback(
            canonical_host(urlsplit("//" + request.host).hostname or ""))
    except ValueError:
        return False


def _start_menu_opens(app) -> bool:
    """This installation's Start-menu entry exists, and it opens this host.

    Setup's entry starts the launcher of `<root>\\tools\\amplifier-unified` with
    `--root <root>`. That launcher opens the browser for the data folder
    `<root>\\app`, and only when the start-at-sign-in task of that launcher and
    of the workspace `<root>\\workspace` runs (`prepare_background`).

    This host knows the task that started it: it compared that definition with
    the Task Scheduler's before it served (`windows_service._serve_host`), and
    it serves that task's data folder. A host that a person started has none.
    The host does not ask the Task Scheduler again, because that starts a
    program. So it does not see a task that was changed, stopped or removed
    while it runs; the entry then shows its own message.

    `listed` reads that the entry's file is there, not what it starts. The
    launcher's own messages use the same test.
    """
    task = app.get("owner_task")
    if task is None:
        return False
    try:
        home = Path(app["service"].data_dir)
        root = home.parent
        if (home != root / "app" or Path(task.python).parent != root / "tools/amplifier-unified/Scripts"
                or Path(task.workspace) != root / "workspace"):
            return False
        from .windows_consumer_shell import listed
        return listed(root)
    except (OSError, ValueError, RuntimeError):
        return False


def next_step(request) -> str:
    """The next step for a browser on this computer that is not signed in.

    Windows: the Start menu, when this host can give a ticket, the request is
    from this computer, and this installation's entry opens this host. A host
    without a ticket store answers the entry's `open` with a refusal, so it
    does not name the entry. Else one sentence that names no way, because this
    host knows none that works. The tests read only this host's memory and one
    small private record; a request that is not from this computer reads no
    file.
    """
    if not is_windows():
        return RUN_OPEN
    app = request.app
    return START_MENU if (app.get("browser_entry") is not None and _on_this_computer(request)
                          and _start_menu_opens(app)) else ON_THE_PC


def current_owner_sid() -> str:
    """Use the public process TokenUser seam, not a username or another API."""
    if not is_windows():
        raise BrowserEntryUnavailable("Windows process-token identity is unavailable.")
    from .windows_files import current_process_sid
    try:
        sid = current_process_sid()
        if (not isinstance(sid, str) or not re.fullmatch(r"S-1-(?:\d+-)+\d+", sid)
                or sid in {"S-1-5-18", "S-1-5-19", "S-1-5-20"}):
            raise ValueError("An owner user token is required")
        return sid
    except (OSError, ValueError, RuntimeError, AttributeError):
        raise BrowserEntryUnavailable("Cannot verify the Windows owner process-token SID.") from None


def read_owner_control_token(data_dir: Path, owner_sid: str) -> str:
    """Read the existing owner credential through the native private boundary."""
    return _read_private_owner_control_token(data_dir, owner_sid)


def _read_private_owner_control_token(data_dir: Path, owner_sid: str) -> str:
    """Existing-only read of both credentials under one directory pin."""
    from . import windows_files
    from .auth import _windows_credential_value
    try:
        if not hmac.compare_digest(owner_sid.encode(), current_owner_sid().encode()):
            raise ValueError("Owner SID mismatch")
        root = windows_files.address(data_dir)
        with windows_files.directory(root / "config" / "auth", owned_root=root, create=False) as pinned:
            _windows_credential_value(pinned.read_private("session-secret", limit=65))
            return _windows_credential_value(pinned.read_private("control-token", limit=65))
    except (OSError, ValueError, RuntimeError, AttributeError, TypeError):
        raise BrowserEntryUnavailable("Cannot verify the existing private owner credentials.") from None


def _owner_probe_targets(data_dir: Path, config: dict):
    """Build HTTPS loopback targets and pin a privately read app-owned leaf.

    Do not use the generic probe's pathname read or a listener-supplied cert.
    The Windows TLS adapter supports exactly this app-owned fixed layout.
    """
    import aiohttp
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from . import windows_files
    tls = config.get("tls", {})
    if (tls.get("method") != "ca" or tls.get("cert") != "config/tls/leaf.crt"
            or tls.get("key") != "config/tls/leaf.key"):
        raise BrowserEntryUnavailable("Windows owner browser entry requires app-owned leaf-pinned HTTPS.")
    try:
        root = windows_files.address(data_dir)
        with windows_files.directory(root / "config" / "tls", owned_root=root, create=False) as pinned:
            certificate = x509.load_pem_x509_certificate(pinned.read_private("leaf.crt", limit=65536))
            pin = aiohttp.Fingerprint(certificate.fingerprint(hashes.SHA256()))
        urls = []
        for bind in config["bind"]:
            host = {"0.0.0.0": "127.0.0.1", "::": "::1", "localhost": "127.0.0.1"}.get(bind, bind)
            if host not in {"127.0.0.1", "::1"}:
                continue
            authority = f"[{host}]" if ":" in host else host
            urls.append(f"https://{authority}:{config['port']}/api/health")
        if not urls:
            raise ValueError("No HTTPS loopback bind")
        return list(dict.fromkeys(urls)), pin
    except (OSError, ValueError, RuntimeError, AttributeError, TypeError, KeyError):
        raise BrowserEntryUnavailable("Cannot verify a private SHA-256 TLS leaf pin and HTTPS loopback listener.") from None


def _control_token(data_dir: Path, owner_sid: str) -> str:
    try:
        token = read_owner_control_token(data_dir, owner_sid)
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{64}", token):
            raise ValueError("Malformed credential")
        return token
    except (OSError, ValueError):
        raise BrowserEntryUnavailable("Cannot read the verified private owner credential.") from None


def _package_identity(identity):
    """Browser-entry comparison only; not updater/release or wheel-byte proof.

    The canonical running_identity owner reports revision=None for wheels and
    unknown source. Require the exact package version and explicit unknown on
    BOTH ends, or identical valid VCS revisions. Never turn missing/malformed
    provenance or a known/unknown mismatch into an unknown-source match.
    Private leaf-pinned HTTPS authenticates the owned listener independently.
    """
    from . import __version__
    if (not isinstance(identity, dict) or identity.get("version") != __version__
            or "revision" not in identity):
        return None
    revision = identity["revision"]
    if revision is not None and (not isinstance(revision, str) or not re.fullmatch("[a-f0-9]{40}", revision)):
        return None
    return __version__, revision


@dataclass(frozen=True)
class Binding:
    instance_id: str
    data_identity: str
    origin: str
    owner_sid: str


class GrantStore:
    """Bounded hash-only verifiers; lock the whole consume operation."""

    def __init__(self, *, ttl=60, capacity=32, clock=time.monotonic):
        if not 0 < ttl <= 60 or not 1 <= capacity <= 128:
            raise ValueError("Choose a TTL up to 60 seconds and capacity up to 128.")
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self._grants = {}
        self._lock = threading.Lock()

    def issue(self, binding: Binding, next_path="/") -> str:
        from .auth import validate_next_path
        with self._lock:
            now = self.clock()
            self._grants = {key: record for key, record in self._grants.items() if record[0] > now}
            if len(self._grants) >= self.capacity:
                raise BrowserEntryUnavailable(BUSY)
            ticket = secrets.token_urlsafe(32)
            self._grants[hashlib.sha256(ticket.encode("ascii")).digest()] = (
                now + self.ttl, binding, validate_next_path(next_path))
            return ticket

    def clear(self) -> None:
        """Drop every pending ticket: a sign-out must not be followed by an old one."""
        with self._lock:
            self._grants.clear()

    def consume(self, ticket: str, binding: Binding) -> str | None:
        if not isinstance(ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket):
            return None
        key = hashlib.sha256(ticket.encode("ascii")).digest()
        with self._lock:
            record = self._grants.get(key)
            if record is None:
                return None
            if record[0] <= self.clock():
                del self._grants[key]
                return None
            if record[1] != binding:
                return None
            del self._grants[key]
            return record[2]


class CodeStore:
    """Typed one-use codes for a browser on another device; hash-only, bounded.

    Fifty bits, at most eight codes and two minutes carry the guess resistance.
    So there is no lockout and no delay: a wrong code is counted and never
    removes a real one, and nobody can keep the owner out by guessing.
    """

    def __init__(self, *, ttl=120, capacity=8, clock=time.monotonic):
        if not 0 < ttl <= 120 or not 1 <= capacity <= 8:
            raise ValueError("Choose a TTL up to 120 seconds and capacity up to 8.")
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self.failures = 0
        self._codes = {}
        self._lock = threading.Lock()

    @staticmethod
    def display(code: str) -> str:
        return code[:5] + "-" + code[5:]

    def issue(self, binding: Binding) -> str:
        with self._lock:
            now = self.clock()
            self._codes = {key: record for key, record in self._codes.items() if record[0] > now}
            if len(self._codes) >= self.capacity:
                raise BrowserEntryUnavailable("Too many codes are waiting. Wait two minutes, then make a new code.")
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            self._codes[hashlib.sha256(code.encode("ascii")).digest()] = (now + self.ttl, binding)
            return code

    def clear(self) -> None:
        """Drop every pending code: a sign-out must not be followed by an old one."""
        with self._lock:
            self._codes.clear()

    def consume(self, text: str, binding) -> bool:
        """Accept case, spaces and hyphens only; never map a look-alike symbol.

        `binding` may be a function that returns the current binding. It runs
        only for a live code, so a wrong code costs no private-file read.
        """
        code = (text.upper().replace(" ", "").replace("-", "")
                if isinstance(text, str) and text.isascii() else "")
        key = hashlib.sha256(code.encode("ascii")).digest() if _CODE.fullmatch(code) else None
        with self._lock:
            record = self._codes.get(key)
            if record is not None and record[0] <= self.clock():
                del self._codes[key]
                record = None
            try:
                current = None if record is None else binding() if callable(binding) else binding
            except BrowserEntryUnavailable:
                current = None  # The host identity cannot be confirmed now; the code stays.
            if record is None or record[1] != current:
                self.failures += 1
                return False
            del self._codes[key]
            return True


def loopback_host(host: str | None) -> bool:
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return host == "localhost"


def local_origin(request) -> str | None:
    """Require HTTPS, both real socket ends local, and the explicit exact Origin."""
    from .auth import allowed_origin
    from .deployment import validate_origin
    if not request.secure or not _local_sockets(request):
        return None
    try:
        origin = validate_origin(request.headers.get("Origin", ""))
        if (origin != f"{request.scheme}://{request.host}" or not allowed_origin(request)
                or not loopback_host(urlsplit(origin).hostname)):
            return None
        return origin
    except ValueError:
        return None


def _remote_address(app, origin) -> bool:
    """An exact configured HTTPS address of this host, not this computer itself."""
    if not isinstance(origin, str) or origin not in app["server_config"].get("public_origins", ()):
        return False
    parsed = urlsplit(origin)
    return parsed.scheme == "https" and not loopback_host(parsed.hostname)


def remote_origin(request) -> str | None:
    """Require HTTPS and the explicit exact Origin of one configured remote address.

    The peer address is deliberately not read. A Tailscale TCP forward connects
    from 127.0.0.1, so the socket does not say where the browser is. The typed
    one-use code is the proof; this check only fixes the address it is for.
    """
    from .deployment import validate_origin
    if not request.secure:
        return None
    try:
        origin = validate_origin(request.headers.get("Origin", ""))
    except ValueError:
        return None
    if origin != f"{request.scheme}://{request.host}" or not _remote_address(request.app, origin):
        return None
    return origin


def initialize(app) -> None:
    app["browser_entry"] = app["browser_codes"] = None
    if not is_windows():
        return
    try:
        token = _control_token(app["service"].data_dir, current_owner_sid())
        if not hmac.compare_digest(token.encode(), app["control_token"].encode()):
            return
    except BrowserEntryUnavailable:
        return
    app["browser_entry"], app["browser_codes"] = GrantStore(), CodeStore()


def _binding(request, origin):
    from .auth import data_identity
    service = request.app["service"]
    sid = current_owner_sid()
    if not hmac.compare_digest(_control_token(service.data_dir, sid).encode(), request.app["control_token"].encode()):
        raise BrowserEntryUnavailable("The owner control credential changed.")
    try:
        identity = data_identity(service.data_dir)
    except (OSError, ValueError):
        raise BrowserEntryUnavailable("The private owner data identity is unavailable.") from None
    return Binding(service.update_manager.running_identity["instanceId"], identity, origin, sid)


async def _owner_issue(request, name, extra, mint, again=None):
    """The one owner-only local check that a ticket and a typed code share.

    `again` is the next step of the typed-code command. A ticket has none here:
    `open` prints these answers, and it never names itself.
    """
    from aiohttp import web
    again = " " + again if again else ""
    authorization = request.headers.get("Authorization", "")
    if not (authorization.startswith("Bearer ") and
            hmac.compare_digest(authorization[7:].encode(), request.app["control_token"].encode())):
        return web.json_response({"error": "The owner control credential is required."}, status=403)
    origin = local_origin(request)
    if not origin:
        return web.json_response({"error": "Use the exact HTTPS local owner origin."}, status=403)
    store = request.app[name]
    if not is_windows() or store is None:
        return web.json_response({"error": "Windows owner browser entry is not available." + again}, status=503)
    try:
        if request.content_type != "application/json":
            return web.json_response({"error": "Use a JSON owner entry request."}, status=400)
        body = await request.clone(client_max_size=4096).json()
        # Observe credential/host changes after the body-read await as well.
        if not hmac.compare_digest(authorization[7:].encode(), request.app["control_token"].encode()):
            return web.json_response({"error": "The owner control credential changed."}, status=403)
        binding = _binding(request, origin)
        identity = request.app["service"].update_manager.running_identity
        package = _package_identity(identity)
        if package is None:
            raise BrowserEntryUnavailable("Cannot verify the running package identity for browser entry.")
        if (not isinstance(body, dict) or set(body) - {"instanceId", "dataIdentity", "version", "revision", "ownerSid", extra}
                or _package_identity(body) != package
                or any(body.get(key) != value for key, value in {
                    "instanceId": binding.instance_id, "dataIdentity": binding.data_identity,
                    "ownerSid": binding.owner_sid}.items())):
            return web.json_response({"error": "The owner host identity changed." + again}, status=409)
        result = mint(store, binding, body.get(extra))
    except BrowserEntryUnavailable as exc:
        return web.json_response({"error": str(exc)}, status=503)
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid owner entry request." + again}, status=400)
    return web.json_response({**result, "expiresIn": store.ttl},
                             headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


async def issue(request):
    return await _owner_issue(request, "browser_entry", "next",
                              lambda store, binding, target: {"ticket": store.issue(binding, target)})


async def code_issue(request):
    """Owner only and this computer only, exactly as for a ticket.

    The code is bound to the one address the owner named: an exact configured
    HTTPS address of this host that is not this computer itself.
    """
    def mint(store, binding, origin):
        if not _remote_address(request.app, origin):
            raise ValueError("Not a configured remote address")
        return {"code": store.issue(replace(binding, origin=origin))}
    return await _owner_issue(request, "browser_codes", "origin", mint, NEW_CODE)


async def _signed_form(request, field):
    """Return the one named form value, or None unless the signed CSRF pair matches."""
    from .auth import CSRF_COOKIE, _verified
    form = await request.clone(client_max_size=4096).post()
    csrf = request.cookies.get(CSRF_COOKIE)
    try:
        valid_csrf = (csrf and hmac.compare_digest(str(form.get("csrf", "")).encode(), csrf.encode())
                      and _verified(request.app["session_secret"], csrf, 900, "csrf"))
    except (TypeError, ValueError):
        valid_csrf = False
    if (not valid_csrf or set(form) != {"csrf", field}
            or any(len(form.getall(key)) != 1 for key in form)):
        return None
    return form.get(field)


async def redeem(request):
    from aiohttp import web
    from .auth import CSRF_COOKIE, SESSION_COOKIE, sign_in
    origin = local_origin(request)
    store = request.app["browser_entry"]
    if not origin or not is_windows() or store is None or request.content_type != "application/x-www-form-urlencoded":
        return web.Response(text=next_step(request), status=403)
    ticket = await _signed_form(request, "ticket")
    if ticket is None:
        return web.Response(text=next_step(request), status=403)
    try:
        target = store.consume(ticket, _binding(request, origin))
    except BrowserEntryUnavailable:
        target = None
    # A ticket from before a sign-out that this host has not applied yet opens nothing.
    session = None if target is None else sign_in(request.app)
    if session is None:
        return web.Response(text=next_step(request), status=403)
    response = web.HTTPSeeOther(target)
    response.set_cookie(SESSION_COOKIE, session,
                        max_age=request.app["server_config"]["session_ttl_seconds"],
                        httponly=True, samesite="Strict", secure=True, path="/")
    response.del_cookie(CSRF_COOKIE, path="/")
    raise response


_PRIVATE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


async def code_redeem(request):
    """Public: the typed code is the proof. No lockout and no delay (see CodeStore).

    Only the address the browser used is checked here, never the peer address
    (see remote_origin). A session from a code is marked remote.
    """
    from aiohttp import web
    from .auth import CSRF_COOKIE, SESSION_COOKIE, sign_in
    origin = remote_origin(request)
    store = request.app["browser_codes"]
    if not origin or not is_windows() or store is None or request.content_type != "application/x-www-form-urlencoded":
        return web.Response(text=RELOAD, status=403)
    code = await _signed_form(request, "code")
    if code is None:
        return web.Response(text=RELOAD, status=403)
    # The current host identity is read only for a live code: any device that
    # reaches this address can send wrong codes, and each must stay cheap.
    if not store.consume(code, lambda: _binding(request, origin)):
        if store.failures == 1 or store.failures % 100 == 0:
            try:  # The count only; never the typed text. A log fault changes nothing.
                print(f"Remote sign-in: {store.failures} wrong codes since start.", flush=True)
            except (OSError, ValueError):
                pass
        raise web.HTTPSeeOther("/login?error=1", headers=_PRIVATE)
    session = sign_in(request.app, remote=True)
    if session is None:
        # The code is from before a sign-out that this host has not applied yet.
        # It is used up and opens nothing; this is not counted as a wrong code.
        raise web.HTTPSeeOther("/login?error=1", headers=_PRIVATE)
    response = web.HTTPSeeOther("/", headers=_PRIVATE)
    response.set_cookie(SESSION_COOKIE, session,
                        max_age=request.app["server_config"]["session_ttl_seconds"],
                        httponly=True, samesite="Strict", secure=True, path="/")
    response.del_cookie(CSRF_COOKIE, path="/")
    raise response


_CODE_PAGE = f"""<!doctype html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in to Amplifier</title>
<link rel="stylesheet" href="/app-pages.css">
</head><body><main>
<h1>Sign in to Amplifier</h1>
<p class="error" role="alert">__ERROR__</p>
<form method="post" action="{CODE_REDEEM_PATH}">
<input type="hidden" name="csrf" value="__CSRF__">
<label for="code">Code</label>
<input id="code" name="code" autocomplete="one-time-code" autocapitalize="characters" autocorrect="off" spellcheck="false" aria-describedby="code-help" autofocus required>
<button type="submit">Sign in</button>
</form>
<p id="code-help">To get a code, run this on the Windows PC that runs Amplifier: <code>amplifier-unified browsers code</code>.
Type the code here within two minutes.</p>
<p>If Windows does not find that command, use the full command from the
<a href="{GUIDE}" target="_blank" rel="noopener noreferrer">Windows steps of the deployment guide</a>.</p>
</main></body></html>
"""


def remote_login_page(request):
    """Code form for a browser on another device: no script, nothing reflected.

    Referrer-Policy is same-origin, not no-referrer: from a no-referrer page a
    browser sends "Origin: null" with the form, and the redeem check refuses it.
    """
    from aiohttp import web
    from .auth import CSRF_COOKIE, _verified, new_csrf
    secret = request.app["session_secret"]
    csrf = request.cookies.get(CSRF_COOKIE)
    # Preserve valid issuance for favicon redirects and concurrent login forms.
    if not _verified(secret, csrf, 900, "csrf"):
        csrf = new_csrf(secret)
    error = ("That code did not work. Check it and type it again. A code works for two minutes "
             "and only once. After that, make a new code on the PC.") if request.query.get("error") else ""
    page = _CODE_PAGE.replace("__ERROR__", error).replace("__CSRF__", csrf)
    page = request.app['service'].instance_branding.document(page, login=True)
    response = web.Response(text=page,
                            content_type="text/html",
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin"})
    response.set_cookie(CSRF_COOKIE, csrf, httponly=True, samesite="Strict", secure=request.secure, path="/")
    return response


async def owner_browser_url(data_dir: Path, config: dict, *, wait=0.0, pause=1.0, waiting=None) -> str:
    """Pinned owner request; no redirect/proxy/HTTP fallback or uncertain reissue.

    The returned URL contains a single-use grant, never the control bearer.
    Callers must not print it or include browser backend exceptions in output.

    For up to `wait` seconds, a host that does not answer, or that answers as
    another package version, is asked again: it is restarting. `waiting` is
    called once before the first pause. Only the health request is repeated.
    The grant request is sent once, to the first host that matches; a refused
    or unconfirmed grant request ends the call.

    The first round asks every local address, as a call without a wait does.
    After it, a health request starts only before the end of the wait. A
    request can take 5 seconds and the grant request 5 more, so the requests
    need about 10 seconds after the wait. That is not a limit for the whole
    call: a pause that ends late (a PC that slept) ends the call late.
    """
    origin, ticket = await _owner_post(data_dir, config, ISSUE_PATH, {}, "ticket", r"[A-Za-z0-9_-]{43}",
                                       wait=wait, pause=pause, waiting=waiting)
    return origin + "/login#ticket=" + ticket


async def owner_browser_code(data_dir: Path, config: dict, origin: str) -> str:
    """The same pinned owner request, for a code to type at one remote address.

    Only the caller shows the code, and only to the owner; never log it.
    """
    _, code = await _owner_post(data_dir, config, CODE_PATH, {"origin": origin}, "code", _CODE.pattern, NEW_CODE,
                                invalid=NOT_IN_USE)
    return code


async def _refusal(response) -> str:
    """The host's own reason for a refused owner request, or "" when it gave none.

    The reply came over the pinned connection from a host of this package
    version, so the reason is one of the host's own fixed sentences.
    """
    import aiohttp
    try:
        value = await response.json()
    except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
        return ""
    reason = value.get("error") if isinstance(value, dict) else None
    return reason if isinstance(reason, str) and 0 < len(reason) <= 300 and reason.isprintable() else ""


async def _owner_post(data_dir: Path, config: dict, path: str, extra: dict, field: str, pattern: str, again=None,
                      *, invalid=None, wait=0.0, pause=1.0, waiting=None):
    """Pin the privately read leaf before reading or sending the control bearer.

    Returns the local origin and the one named reply value. No redirect, no
    proxy, no HTTP fallback, and an unconfirmed POST is never sent again.
    `invalid` is the text for the host's reply that the request itself is not
    acceptable (400): for a code, the running host does not have the address.

    `wait`, `pause` and `waiting` are the wait of owner_browser_url for a host
    that is restarting. Only the health request is repeated, never the POST.
    Without a wait, every local address is asked once, with no pause.

    `again` is the next step of the typed-code command, and its texts stay as
    they are. Without it the caller is `open`: it prints the host's own reason
    for a refusal, and it never names itself or the Start menu.
    """
    import aiohttp
    from .auth import data_identity
    from .update_readiness import running_identity
    urls, pin = _owner_probe_targets(data_dir, config)
    if not isinstance(pin, aiohttp.Fingerprint) or len(pin.fingerprint) != 32:
        raise BrowserEntryUnavailable("Windows owner browser entry requires an exact SHA-256 TLS leaf pin.")
    urls = [url for url in urls if urlsplit(url).scheme == "https"
            and urlsplit(url).hostname in {"127.0.0.1", "::1"}]
    if not urls:
        raise BrowserEntryUnavailable("No pinned HTTPS loopback listener is configured.")
    sid = current_owner_sid()
    token = _control_token(data_dir, sid)
    package = _package_identity(running_identity())
    if package is None:
        raise BrowserEntryUnavailable("Cannot verify the installed package identity for browser entry.")
    version, revision = package
    headers = {"Authorization": "Bearer " + token}
    deadline = time.monotonic() + wait
    repeated = False
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5), trust_env=False) as client:
        while True:
            for url in urls:
                if repeated and time.monotonic() >= deadline:
                    break  # The wait is over: no further health request starts.
                origin = url.removesuffix("/api/health")
                try:
                    async with client.get(url, ssl=pin, allow_redirects=False, headers=headers) as response:
                        if response.status != 200:
                            continue
                        health = await response.json()
                    if (not isinstance(health, dict) or health.get("ok") is not True
                            or health.get("app") != "amplifier-unified" or _package_identity(health) != package
                            or health.get("dataIdentity") != data_identity(data_dir)
                            or not isinstance(health.get("instanceId"), str)
                            or not re.fullmatch("[a-f0-9]{32}", health["instanceId"])):
                        continue
                except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
                    continue
                try:
                    async with client.post(origin + path, ssl=pin, allow_redirects=False,
                                           headers={**headers, "Origin": origin}, json={
                                               "instanceId": health["instanceId"], "dataIdentity": health["dataIdentity"],
                                               "version": version, "revision": revision, "ownerSid": sid,
                                               **extra}) as response:
                        if response.status != 200:
                            if invalid and response.status == 400:
                                raise BrowserEntryUnavailable(invalid)
                            if again:
                                raise BrowserEntryUnavailable("The owner host refused browser entry. " + again)
                            raise BrowserEntryUnavailable(await _refusal(response) or REFUSED)
                        result = await response.json()
                    value = result.get(field) if isinstance(result, dict) else None
                    if not isinstance(value, str) or not re.fullmatch(pattern, value):
                        raise ValueError("Invalid reply")
                except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
                    raise BrowserEntryUnavailable(
                        "Browser entry response was not confirmed; no retry was made. " + again
                        if again else UNCONFIRMED) from None
                return origin, value
            repeated = True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if waiting is not None:
                waiting()
                waiting = None
            await asyncio.sleep(min(pause, remaining))
    raise BrowserEntryUnavailable(
        "The owned local host is not reachable or its identity does not match. " + again if again else NO_ANSWER)
