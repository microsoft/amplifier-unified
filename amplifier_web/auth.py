"""aiohttp-native PAM authentication and independent control credentials."""
from __future__ import annotations

import base64
import hashlib
import html
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import quote, urlsplit

from aiohttp import web
from filelock import FileLock

from .deployment import validate_origin, write_private

SESSION_COOKIE = "amplifier_unified_session"
CSRF_COOKIE = "amplifier_unified_csrf"


def auth_dir(data_dir: Path) -> Path:
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


def session_secret(data_dir: Path) -> str:
    return _read_or_create(_secret_path(data_dir))


def control_token(data_dir: Path) -> str:
    """Return the process-owner's local control token without displaying it."""
    return _read_or_create(control_token_path(data_dir))


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


def _verified(secret: str, value: str | None, ttl: int, kind: str) -> bool:
    if not value or "." not in value:
        return False
    encoded, signature = value.rsplit(".", 1)
    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        issued = payload["issued"]
        if type(issued) is not int:
            return False
        age = time.time() - issued
        return payload["kind"] == kind and 0 <= age <= ttl
    except (OverflowError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def new_session(secret: str) -> str:
    return _signed(secret, {"kind": "session", "issued": int(time.time()), "nonce": secrets.token_urlsafe(16)})


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


@web.middleware
async def auth_required(request: web.Request, handler):
    if request.headers.get("Sec-Fetch-Site") == "cross-site" or not allowed_origin(request):
        return web.json_response({"error": "Cross-origin requests are not permitted."}, status=403)
    public = {"/login", "/setup", "/api/ca", "/ca.crt", "/api/health"}
    if request.path in public and request.method in {"GET", "HEAD"}:
        return await handler(request)
    if request.path == "/login" and request.method == "POST":
        csrf = request.cookies.get(CSRF_COOKIE)
        submitted = (await request.post()).get("csrf", "")
        secret = request.app["session_secret"]
        if not allowed_origin(request) or not csrf or not hmac.compare_digest(str(submitted), csrf) or not _verified(secret, csrf, 900, "csrf"):
            return web.Response(text="Login failed.", status=403)
        return await handler(request)
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer ") and hmac.compare_digest(authorization[7:], request.app["control_token"]):
        return await handler(request)
    if _verified(request.app["session_secret"], request.cookies.get(SESSION_COOKIE), request.app["server_config"]["session_ttl_seconds"], "session"):
        return await handler(request)
    if is_api_request(request):
        return web.json_response({"error": "Authentication required."}, status=401)
    requested = request.path + (("?" + request.query_string) if request.query_string else "")
    raise web.HTTPTemporaryRedirect(login_url(requested))


async def login_page(request: web.Request) -> web.Response:
    secret = request.app["session_secret"]
    csrf = new_csrf(secret)
    page = (Path(__file__).parent / "static" / "login.html").read_text()
    next_path = html.escape(validate_next_path(request.query.get("next")), quote=True)
    response = web.Response(text=page.replace("__CSRF__", csrf).replace("__NEXT__", next_path), content_type="text/html")
    response.set_cookie(CSRF_COOKIE, csrf, httponly=True, samesite="Strict", secure=request.secure, path="/")
    return response


async def post_login(request: web.Request) -> web.Response:
    form = await request.post()
    if not authenticate_pam(str(form.get("username", "")), str(form.get("password", ""))):
        raise web.HTTPSeeOther(login_url(request.query.get("next")) + ("&" if request.query.get("next") else "?") + "error=1")
    response = web.HTTPSeeOther(validate_next_path(request.query.get("next")))
    response.set_cookie(SESSION_COOKIE, new_session(request.app["session_secret"]), max_age=request.app["server_config"]["session_ttl_seconds"],
                        httponly=True, samesite="Strict", secure=request.secure, path="/")
    response.del_cookie(CSRF_COOKIE, path="/")
    raise response