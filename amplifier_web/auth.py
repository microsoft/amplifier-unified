"""aiohttp-native PAM authentication and independent control credentials."""
from __future__ import annotations

import base64
import getpass
import hashlib
import html
import hmac
import json
import os
import re
from pathlib import Path
import secrets
import time
from urllib.parse import quote, urlsplit

from aiohttp import web
from filelock import FileLock

from .deployment import canonical_host, is_loopback, validate_origin, write_private
from . import browser_entry

SESSION_COOKIE = "amplifier_unified_session"
CSRF_COOKIE = "amplifier_unified_csrf"
_WINDOWS = os.name == "nt"


def validated_data_dir(data_dir: Path, *, create: bool = False) -> Path:
    """Keep the original Windows spelling until the native boundary checks it."""
    if not _WINDOWS:
        return Path(data_dir).expanduser().resolve()
    from . import windows_files
    root = windows_files.address(data_dir)
    with windows_files.directory(root, owned_root=root, create=create):
        return root


def auth_dir(data_dir: Path) -> Path:
    if _WINDOWS:
        from . import windows_files
        root = windows_files.address(data_dir)
        path = root / "config" / "auth"
        with windows_files.directory(path, owned_root=root, create=True):
            return path
    path = Path(data_dir).expanduser().resolve() / "config" / "auth"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _secret_path(data_dir: Path) -> Path:
    return auth_dir(data_dir) / "session-secret"


def control_token_path(data_dir: Path) -> Path:
    return auth_dir(data_dir) / "control-token"


def _read_or_create(path: Path) -> str:
    # Concurrent startup must converge on one credential rather than letting
    # two processes generate and retain different in-memory values.
    with FileLock(str(path) + ".lock"):
        try:
            os.chmod(path, 0o600)
            value = path.read_text().strip()
        except FileNotFoundError:
            value = secrets.token_urlsafe(48)
            write_private(path, value + "\n")
        if value:
            return value
        raise ValueError(f"Private credential is empty: {path}")


def _windows_credential_value(content: bytes) -> str:
    # The existing token_urlsafe(48) format, with one optional final LF.
    if not re.fullmatch(rb"[A-Za-z0-9_-]{64}\n?", content):
        raise ValueError("Existing Windows private credential is empty or malformed.")
    return content.removesuffix(b"\n").decode("ascii")


def _windows_credential(data_dir: Path, name: str) -> str:
    from . import windows_files
    root = windows_files.address(data_dir)
    with windows_files.directory(root / "config" / "auth", owned_root=root, create=True) as pinned:
        try:
            return _windows_credential_value(pinned.read_private(name, limit=65))
        except FileNotFoundError:
            value = secrets.token_urlsafe(48)
            try:
                pinned.publish_private(name, (value + "\n").encode("ascii"), replace=False)
            except FileExistsError:
                # Only a clean no-replace collision converges. An uncertain
                # publication/cleanup failure must propagate, not be replayed.
                pass
            return _windows_credential_value(pinned.read_private(name, limit=65))


def session_secret(data_dir: Path) -> str:
    if _WINDOWS:
        return _windows_credential(data_dir, "session-secret")
    return _read_or_create(_secret_path(data_dir))


def control_token(data_dir: Path) -> str:
    """Return the process-owner's local control token without displaying it."""
    if _WINDOWS:
        return _windows_credential(data_dir, "control-token")
    return _read_or_create(control_token_path(data_dir))


def data_identity(data_dir: Path) -> str:
    """Return the stable, private-data-directory identity used by local clients."""
    if _WINDOWS:
        return hashlib.sha256(str(validated_data_dir(data_dir)).encode()).hexdigest()
    return hashlib.sha256(str(Path(data_dir).expanduser().resolve()).encode()).hexdigest()


def history_scope(secret: str, credential: str, data_dir: Path) -> str:
    """Opaque presentation partition, not a credential or a navigation record.

    Only call after authentication. Reload/reconnect keep the same partition;
    a new sign-in, process owner, data root or secret gets a different one.
    Domain separation prevents this value from authenticating as a session.
    """
    owner = (browser_entry.current_owner_sid() if _WINDOWS else
             os.geteuid() if hasattr(os, "geteuid") else getpass.getuser())
    payload = json.dumps(["navigation-history-v1", owner,
                          data_identity(data_dir), credential], separators=(",", ":"))
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def with_history_scope(request: web.Request, snapshot: dict) -> dict:
    """Decorate this request's browser response, never shared/persisted state."""
    scope = request.get("history_scope")
    if scope and snapshot.get("client", {}).get("kind") == "web":
        return {**snapshot, "client": {**snapshot["client"], "historyScope": scope}}
    return snapshot


def validate_next_path(value: str | None) -> str:
    if not value or not isinstance(value, str) or any(ord(char) < 32 for char in value):
        return "/"
    if "\\" in value or not value.startswith("/") or value.startswith("//"):
        return "/"
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or ".." in parsed.path.split("/"):
        return "/"
    if "://" in value.lower() or any(scheme in value.lower() for scheme in ("javascript:", "data:", "file:")):
        return "/"
    return value


def login_url(next_path: str | None) -> str:
    target = validate_next_path(next_path)
    return "/login" if target == "/" else "/login?next=" + quote(target, safe="")


def _signed(secret: str, payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def _issued(secret: str, value: str | None, ttl: int, kind: str) -> int | None:
    """Return the signed issue second of a valid, unexpired value, else None."""
    if not value or "." not in value:
        return None
    encoded, signature = value.rsplit(".", 1)
    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        issued = payload["issued"]
        if type(issued) is not int:
            return None
        age = time.time() - issued
        return issued if payload["kind"] == kind and 0 <= age <= ttl else None
    except (OverflowError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _verified(secret: str, value: str | None, ttl: int, kind: str) -> bool:
    return _issued(secret, value, ttl, kind) is not None


def valid_session(app, value: str | None) -> bool:
    """The one check for a browser session cookie.

    A session from before the owner's last "sign out all browsers" is refused,
    whatever its remaining lifetime. A session issued in the same second as
    that sign-out is refused as well.
    """
    issued = _issued(app["session_secret"], value, app["server_config"]["session_ttl_seconds"], "session")
    return issued is not None and issued > getattr(app.get("sessions"), "epoch", 0)


class Sessions:
    """The running host's sign-out time and the change hint of its saved record.

    A holder object: the keys of an application are fixed once it has started,
    and this value changes while the host runs.
    """

    def __init__(self, epoch=0, hint=None):
        self.epoch, self.hint = epoch, hint

    @classmethod
    def load(cls, data_dir):
        hint = _epoch_hint(data_dir)  # Read first: a change during the read is seen next time.
        return cls(session_epoch(data_dir), hint)


_EPOCH = "session-epoch"


def _epoch_path(data_dir: Path) -> Path:
    if _WINDOWS:
        from . import windows_files
        return windows_files.address(data_dir) / "config" / "auth" / _EPOCH
    return Path(data_dir).expanduser().resolve() / "config" / "auth" / _EPOCH


def _read_epoch(data_dir: Path) -> int:
    """Read the saved sign-out second. Missing means no sign-out yet (0)."""
    try:
        if _WINDOWS:
            from . import windows_files
            root = windows_files.address(data_dir)
            with windows_files.directory(root / "config" / "auth", owned_root=root) as pinned:
                content = pinned.read_private(_EPOCH, limit=32)
        else:
            content = _epoch_path(data_dir).read_bytes()[:33]
    except FileNotFoundError:
        return 0
    if not re.fullmatch(rb"[0-9]{1,12}\n?", content):
        raise ValueError("The saved sign-out time is malformed.")
    return int(content)


def _write_epoch(data_dir: Path, value: int) -> None:
    if _WINDOWS:
        write_private(_epoch_path(data_dir), f"{value}\n", owned_root=validated_data_dir(data_dir, create=True))
    else:
        auth_dir(data_dir)
        write_private(_epoch_path(data_dir), f"{value}\n")


def session_epoch(data_dir: Path) -> int:
    """The second before which every browser session is refused.

    An unreadable or malformed record never revives a session: it is replaced
    by the present moment, once, so that everyone signs in again. A missing
    record means no sign-out has happened. Anyone who could delete it can also
    read the session secret, so its absence is not treated as an attack.
    """
    try:
        return _read_epoch(data_dir)
    except (OSError, ValueError):
        now = int(time.time())
        try:
            _write_epoch(data_dir, now)
        except OSError:
            pass  # Still refused in memory; the next read repeats this.
        return now


class SignOutUncertain(OSError):
    """The sign-out could not be confirmed.

    A save was too slow, failed after the record had changed, or could not be
    looked at. The record may have changed. A sign-in at the same moment may
    have a session that this sign-out does not end.
    """


def sign_out_all(data_dir: Path) -> int:
    """Sign out every browser of this installation; return the saved second.

    Authority is the owner's own private folder: no network request and no
    credential are involved. A running host applies the change within a few
    seconds. The time never moves backward, and a write failure is an error,
    never a silent success.

    The saved second is never earlier than the second in which the record
    was saved. A session that the host made before the record was there
    carries that second or an earlier one, so the host refuses it (see
    `sign_in`). A write that ends in a later second than its value is
    therefore made again with the later second, after a short pause.

    The host tells a new record from the file's time, size and identity,
    not from its content. So each save is checked: a save that the host
    could not tell from the record before it is made again as well.
    """
    try:
        saved = _read_epoch(data_dir)
    except (OSError, ValueError):
        saved = 0
    published = False
    for _ in range(3):
        before = _epoch_hint(data_dir)
        value = max(int(time.time()), saved)
        try:
            _write_epoch(data_dir, value)
            if _read_epoch(data_dir) != value:
                raise OSError("The sign-out time was not saved.")
        except (OSError, ValueError):
            if published or not _same_record(before, _epoch_hint(data_dir)):
                # A save of this run is on disk, or the record is not known to
                # be the one from before: this is not "nothing was changed".
                raise SignOutUncertain("The sign-out time was not confirmed, and the record may have changed.") from None
            raise
        published = True
        # Done when the host can see this save (both looks worked and differ),
        # and no later second began before the save was there.
        if _new_record(before, _epoch_hint(data_dir)) and int(time.time()) <= value:
            return value
        saved = value
        time.sleep(0.05)
    raise SignOutUncertain("A sign-out time was saved, but not within one second or not visibly to the host.")


def _epoch_hint(data_dir: Path):
    """A cheap change hint for the saved record; never its value.

    Every save puts a new file in place, so the file identity changes with
    each save, also when two saves have the same file time and size.

    None means that there is no record: no sign-out yet. A look that fails
    for any other reason gives `_UNSEEN`. That is never "no record" and never
    "the same record"; compare two looks only with `_same_record` and
    `_new_record`.
    """
    try:
        info = os.stat(_epoch_path(data_dir))
    except FileNotFoundError:
        return None
    except OSError:
        return _UNSEEN
    return info.st_mtime_ns, info.st_size, info.st_ino


_UNSEEN = object()  # The saved record could not be looked at.


def _same_record(first, second) -> bool:
    """Both looks worked, and they found the same record or both found none."""
    return first is not _UNSEEN and second is not _UNSEEN and first == second


def _new_record(first, second) -> bool:
    """Both looks worked, and the second found another record than the first."""
    return first is not _UNSEEN and second is not _UNSEEN and first != second


def refresh_sessions(app, streams=()) -> bool:
    """Apply a changed saved sign-out to the running host; True for a newer time.

    Pending sign-in tickets and codes are dropped for every changed record,
    also when its second is the one already applied: a second sign-out in the
    same second must still drop a ticket or code that was made in between.
    For a newer time, open state streams are ended as well, so that a
    signed-out browser cannot keep watching. A stream that reconnects must
    pass the session check again.
    """
    data_dir, state = app["service"].data_dir, app["sessions"]
    hint = _epoch_hint(data_dir)
    if hint is _UNSEEN:
        # Nothing is applied and nothing is kept from a look that failed.
        raise OSError("The saved sign-out record could not be looked at.")
    if _same_record(hint, state.hint):
        return False
    value = session_epoch(data_dir)
    # Kept only now: a record that could not be read is read again next time,
    # and until then no ticket or code opens a session (see sign_in).
    state.hint = hint
    for store in (app.get("browser_entry"), app.get("browser_codes")):
        if store is not None:
            store.clear()
    if value <= state.epoch:
        # No session is older than before: one that is valid now was made
        # after the second of this record, so no stream needs to end.
        return False
    state.epoch = value
    for task in list(streams):
        task.cancel()
    return True


async def watch_sessions(app, streams, *, interval=2.0):
    """Notice the owner's sign-out while the host runs."""
    import asyncio
    reported = False
    while True:
        await asyncio.sleep(interval)
        try:
            refresh_sessions(app, streams)
        except Exception as error:  # A watcher fault must never stop the host.
            if not reported:
                # Say it once in the host log: a sign-out that the running
                # host cannot apply must not fail in silence.
                reported = True
                try:
                    print("Sign-out of all browsers could not be checked: " + type(error).__name__
                          + ". Until it can be checked, no browser can sign in with a ticket or a code.", flush=True)
                except (OSError, ValueError):
                    pass


def new_session(secret: str, *, remote: bool = False) -> str:
    """Mark a session from a typed remote code; a local session is unchanged."""
    payload = {"kind": "session", "issued": int(time.time()), "nonce": secrets.token_urlsafe(16)}
    return _signed(secret, {**payload, "via": "remote"} if remote else payload)


def sign_in(app, *, remote: bool = False) -> str | None:
    """A session for a redeemed ticket or code, or None.

    None while a saved sign-out waits for this host to apply it. The ticket or
    code is then from before that sign-out, and it must not open a session
    after it.

    The order matters. The session's second is read first; the saved record
    is looked at after that. So a session that is returned was made before
    any record that is saved later, and `sign_out_all` saves no second that
    is earlier than its own record. The host therefore refuses this session
    when it applies that later sign-out.

    The record is only looked at here. The watcher applies it, and it alone
    ends the open streams. A look that fails opens no session: it is not
    known then that no sign-out waits.
    """
    value = new_session(app["session_secret"], remote=remote)
    if not _same_record(_epoch_hint(app["service"].data_dir), app["sessions"].hint):
        return None
    return value


def new_csrf(secret: str) -> str:
    return _signed(secret, {"kind": "csrf", "issued": int(time.time()), "nonce": secrets.token_urlsafe(16)})


def authenticate_pam(username: str, password: str) -> bool:
    """PAM is deliberately restricted to the account owning this process."""
    try:
        import pwd
        import pam
        if username != pwd.getpwuid(os.geteuid()).pw_name:
            return False
        return bool(pam.authenticate(username, password, service="login"))
    except Exception:
        return False


def allowed_origin(request: web.Request) -> bool:
    """Require exact configured origins; never trust proxy forwarding headers."""
    origin = request.headers.get("Origin")
    if not origin:
        return True
    try:
        origin = validate_origin(origin)
        current = validate_origin(f"{request.scheme}://{request.host}")
    except ValueError:
        return False
    return origin == current and origin in request.app["allowed_origins"]


def is_api_request(request: web.Request) -> bool:
    return request.path.startswith("/api/")


def is_entry_navigation(request: web.Request) -> bool:
    """Allow external links to entry pages, never cross-site API or form calls.

    A browser link and its /login redirect can both carry cross-site Fetch
    Metadata. They still pass the normal Host, Origin, and authentication gates.
    Chromium clears destination to the literal "empty" when our service worker
    forwards a document navigation with fetch(event.request). Accept that only
    for the two entry paths the shipped worker forwards, not APIs or callbacks.
    Missing destination is not "empty"; ordinary script fetches cannot choose
    navigate mode. The worker excludes frames, and CSP still blocks embedding.
    """
    return (
        request.method in {"GET", "HEAD"}
        and request.path in {"/", "/login", "/setup", "/oauth/mcp/callback", "/oauth/mcp/complete", "/devices/pair", "/devices",
                             "/connect", "/connect/verify", "/connect/code"}
        and "Origin" not in request.headers
        and request.headers.get("Sec-Fetch-Mode") == "navigate"
        and (
            request.headers.get("Sec-Fetch-Dest") == "document"
            or (request.path in {"/", "/login"} and request.headers.get("Sec-Fetch-Dest") == "empty")
        )
    )


@web.middleware
async def auth_required(request: web.Request, handler):
    from .device_connections import PREFIX, extension_request, permits_device
    extension = extension_request(request)

    async def authenticated(credential):
        scope = history_scope(request.app["session_secret"], credential,
                              request.app["service"].data_dir)
        request["history_scope"] = scope
        response = await handler(request)
        if not response.prepared:
            response.headers["X-Amplifier-History-Scope"] = scope
        return response

    cross_site = request.headers.get("Sec-Fetch-Site") == "cross-site"
    if not extension and ((cross_site and not is_entry_navigation(request)) or not allowed_origin(request)):
        return web.json_response({"error": "Cross-origin requests are not permitted."}, status=403)
    if request.path == '/api/room-context/mcp' and request.method == 'POST':
        # This exact handler checks a room/source-bound transport capability.
        # Neither that capability nor an owner cookie grants other API access.
        return await handler(request)
    if request.path in {browser_entry.ISSUE_PATH, browser_entry.CODE_PATH}:
        authorization = request.headers.get("Authorization", "")
        if (request.method != "POST" or not authorization.startswith("Bearer ")
                or not hmac.compare_digest(authorization[7:].encode(), request.app["control_token"].encode())):
            return web.json_response({"error": "The owner control credential is required."}, status=403)
        return await handler(request)
    if request.path in {browser_entry.REDEEM_PATH, browser_entry.CODE_REDEEM_PATH} and request.method == "POST":
        # Only these exact public mutations. Each handler requires HTTPS, Origin
        # and signed form CSRF before atomically consuming the one-use ticket
        # or typed code.
        return await handler(request)
    if extension and request.method == 'OPTIONS':
        return await handler(request)
    if ((request.path in {PREFIX, PREFIX + '/qr'} and request.method in {'GET', 'HEAD'})
            or (request.path in {PREFIX + '/enroll', PREFIX + '/token'} and request.method == 'POST')):
        return await handler(request)
    if extension and not request.headers.get('Authorization', '').lower().startswith('bearer amd_'):
        return web.json_response({'error': 'An app credential is required.'}, status=401)
    # Public UI resources and bounded raster branding only. Authentication still gates the SPA,
    # every API, and all user-authored files/canvas documents.
    public = {"/oauth/mcp/callback", "/oauth/mcp/complete", "/login", "/setup", "/api/ca", "/ca.crt", "/api/health",
              "/manifest.webmanifest", "/sw.js", "/pwa.js", "/offline.html", "/app-pages.css", "/favicon.ico", "/browser-entry.js",
              "/branding/favicons/favicon.ico", "/branding/favicons/favicon-32.png",
              "/branding/favicons/apple-touch-icon.png", "/branding/icons/amplifier-icon-128.png",
              "/branding/pwa/pwa-192.png", "/branding/pwa/pwa-512.png"}
    public.update(f'/instance-icon/{size}.png' for size in (32, 180, 192, 512))
    if request.path in public and request.method in {"GET", "HEAD"}:
        return await handler(request)
    # Deliberately published snapshots have their own unguessable bearer link.
    # This exemption grants no app/API/file authority; the handler checks expiry
    # and revocation on every request and serves escaped inert snapshot content.
    if request.method in {'GET', 'HEAD'} and re.fullmatch(r'/share/[A-Za-z0-9_-]{43}', request.path):
        return await handler(request)
    if request.path in {'/api/terminal/redeem', '/api/devices/pair', '/api/devices/pair/token'} and request.method == 'POST':
        # This narrowly scoped endpoint verifies a short-lived, single-use setup
        # grant in its handler. It is never an alternate general API credential.
        return await handler(request)
    if request.path == "/login" and request.method == "POST":
        csrf = request.cookies.get(CSRF_COOKIE)
        submitted = (await request.post()).get("csrf", "")
        secret = request.app["session_secret"]
        if not csrf or not hmac.compare_digest(str(submitted), csrf) or not _verified(secret, csrf, 900, "csrf"):
            return web.Response(text="Login failed.", status=403)
        return await handler(request)
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer ") and hmac.compare_digest(authorization[7:], request.app["control_token"]):
        return await authenticated(authorization[7:])
    if authorization.lower().startswith(('bearer amt_', 'bearer amc_', 'bearer amd_')):
        devices = request.app['terminal_setup'].devices
        identity = devices.identify(authorization[7:])
        if identity:
            device = devices.read()['devices'].get(identity)
            if device is None:
                return web.json_response({'error': 'Device access was removed.'}, status=401)
            # Observe revocation before any await, including the JSON body read.
            with devices.connection(identity):
                scope = device.get('scope')
                if scope == 'device-v1':
                    request['connection_device'] = identity
                    if not permits_device(request):
                        return web.json_response({'error': 'This connection cannot access that operation.'}, status=403)
                elif scope is not None:
                    request['companion_device'] = identity
                    from .device_pairing import SCOPE, permits_companion
                    if scope != SCOPE or not await permits_companion(request):
                        return web.json_response({'error': 'This device is not permitted to perform that operation.'}, status=403)
                if devices.identify(authorization[7:]) != identity:
                    return web.json_response({'error': 'Device access was removed.'}, status=401)
                return await handler(request)
        return web.json_response({'error': 'Device access was removed or is invalid.'}, status=401)
    if valid_session(request.app, request.cookies.get(SESSION_COOKIE)):
        return await authenticated(request.cookies[SESSION_COOKIE])
    if is_api_request(request):
        return web.json_response({"error": "Authentication required."}, status=401)
    requested = request.path + (("?" + request.query_string) if request.query_string else "")
    raise web.HTTPTemporaryRedirect(login_url(requested))


def _other_device_address(request: web.Request) -> bool:
    """The address in use is not this computer's own.

    The name is read exactly as the host boundary reads it, so that
    `localhost.` and `localhost` get the same page.
    """
    try:
        return not is_loopback(canonical_host(urlsplit("//" + request.host).hostname or ""))
    except ValueError:
        return False  # The host boundary refuses such a name before any page.


async def login_page(request: web.Request) -> web.Response:
    if browser_entry.is_windows() and _other_device_address(request):
        # Another device cannot use the local launcher; it types an owner-made code.
        return browser_entry.remote_login_page(request)
    secret = request.app["session_secret"]
    csrf = request.cookies.get(CSRF_COOKIE)
    # Preserve valid issuance for favicon redirects and concurrent login forms.
    if not _verified(secret, csrf, 900, "csrf"):
        csrf = new_csrf(secret)
    filename = "windows-login.html" if browser_entry.is_windows() else "login.html"
    page = (Path(__file__).parent / "static" / filename).read_text(encoding="utf-8")
    if "__REOPEN__" in page:
        # The page's own place for the next step. First of all: no value of the
        # request is in the page yet. A page without the mark is not changed.
        page = page.replace("__REOPEN__", html.escape(browser_entry.next_step(request), quote=True))
    next_path = html.escape(validate_next_path(request.query.get("next")), quote=True)
    error = "Could not sign in. Check your host username and password and try again." if request.query.get("error") else ""
    page = page.replace("__CSRF__", csrf).replace("__NEXT__", next_path).replace("__ERROR__", error)
    page = request.app['service'].instance_branding.document(page, login=True)
    response = web.Response(text=page, content_type="text/html")
    response.set_cookie(CSRF_COOKIE, csrf, httponly=True, samesite="Strict", secure=request.secure, path="/")
    return response


async def post_login(request: web.Request) -> web.Response:
    if browser_entry.is_windows():
        return web.Response(text=browser_entry.next_step(request), status=403)
    form = await request.post()
    username, password = str(form.get("username", "")), str(form.get("password", ""))
    bridge = request.app["server_config"].get("host_pam")
    if bridge is not None:
        from .host_pam import authenticate
        accepted = await authenticate(bridge, username, password)
    else:
        accepted = authenticate_pam(username, password)
    if not accepted:
        url = login_url(request.query.get("next"))
        raise web.HTTPSeeOther(url + ("&" if "?" in url else "?") + "error=1")
    response = web.HTTPSeeOther(validate_next_path(request.query.get("next")))
    response.set_cookie(SESSION_COOKIE, new_session(request.app["session_secret"]), max_age=request.app["server_config"]["session_ttl_seconds"],
                        httponly=True, samesite="Strict", secure=request.secure, path="/")
    response.del_cookie(CSRF_COOKIE, path="/")
    raise response
