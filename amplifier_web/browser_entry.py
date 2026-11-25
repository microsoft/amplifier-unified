"""Owner-to-browser handoff, not remote pairing or a fresh Windows Hello check.

The foreground owner launcher reads existing private credentials only.
Listener authentication and grant exchange remain separate from that local read.
A browser on another device signs in with a one-use code that the owner makes
on this computer and types on that device, or with the password that the owner
set in the app on this computer (or the sign-in key that a command made there).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import hashlib
import hmac
import ipaddress
import math
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import unicodedata
from urllib.parse import urlsplit

ISSUE_PATH = "/api/browser-entry/issue"
REDEEM_PATH = "/api/browser-entry/redeem"
CODE_PATH = "/api/browser-entry/code"
CODE_REDEEM_PATH = "/api/browser-entry/code/redeem"
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # 32 symbols: no 0, 1, I or O.
CODE_LENGTH = 10
_CODE = re.compile(f"[{CODE_ALPHABET}]{{{CODE_LENGTH}}}")
KEY_REDEEM_PATH = "/api/browser-entry/key/redeem"
KEY_LENGTH = 25  # 125 bits, in the symbols of a code.
_KEY = re.compile(f"[{CODE_ALPHABET}]{{{KEY_LENGTH}}}")
PASSWORD_PATH = "/connect/sign-in"   # The owner's page on this computer.
# The way to that page in the app. The Settings pages are the web client's: when it renames them, change this text.
SETTINGS_PATH = 'Open Settings, then "Install app", then "Devices & apps". There, select "Set a password".'
PASSWORD_MIN, PASSWORD_MAX = 12, 256
_SCRYPT = "scrypt 15 8 1 "           # The start of a password's record: N = 2**15, r = 8, p = 1.
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
RELOAD_KEY = "Could not sign in. Open the sign-in page again and type the password."
NEW_KEY = "Then run this again: amplifier-unified browsers key."
GUIDE = ("https://github.com/microsoft/amplifier-unified/blob/main/docs/DEPLOYMENT.md"
         "#windows-host-reach-unified-from-another-device")
NOT_IN_USE = ("The running Amplifier does not use this address yet. Stop Amplifier and start it again. "
              "Then run this again: amplifier-unified browsers code.")


class BrowserEntryUnavailable(RuntimeError):
    pass


def is_windows() -> bool:
    return sys.platform == "win32"


def _local_sockets(request) -> bool:
    """Both real socket ends are this computer's own addresses."""
    if request.transport is None:
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
        code = _typed(text)
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


def _typed(text) -> str:
    """What a person typed, without case, spaces and hyphens; never a mapped look-alike."""
    return text.upper().replace(" ", "").replace("-", "") if isinstance(text, str) and text.isascii() else ""


def _password_bytes(text):
    """The bytes of a typed password; None for a text that cannot be one."""
    if not isinstance(text, str) or not 0 < len(text) <= PASSWORD_MAX:
        return None
    try:
        return unicodedata.normalize("NFKC", text).encode("utf-8")
    except UnicodeError:
        return None                                         # Half of a character pair: a form can send one.


def _scrypt(password: bytes, salt: bytes) -> str:
    # About a tenth of a second and 32 MiB for each call: never on the event loop.
    return hashlib.scrypt(password, salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32).hex()


class SavedKey:
    """The running host's view of the password or sign-in key that the owner saved.

    One private record holds either. A key comes from the owner's command: 25
    random symbols, shown once; the record has its SHA-256. A password is the
    owner's own text, set on the owner's page on this computer; the record
    has a salt and its scrypt hash. Neither is kept anywhere.

    A key has 125 bits, so a wrong key is only counted: no lockout and no
    delay, as for a code. A person chooses a password, so wrong passwords are
    slowed down. One password is checked at a time, away from the event loop.
    The wrong password number `FREE` in a row starts a wait of `FIRST_WAIT`
    seconds, each further one doubles it, to `LONGEST_WAIT` seconds at most,
    and during a wait no password is checked, also not the right one. `QUIET`
    seconds without a wrong password, a right one, or a new record start the
    count again.
    So a sender has about 300 guesses in a day at most: one in each
    `LONGEST_WAIT`, and a new count costs `QUIET`. The device cannot be
    told from the request (see remote_origin), so the wait is for everyone:
    a person who can reach the address can keep the owner from signing in
    with the password, and gets in no faster. A code still works then.

    The host reads the record again only when the file changed. So a wrong
    key costs one look at the file and no private read, once the host has
    read the record or found it malformed, and a new or removed key or
    password counts from the next request. A read that failed for another
    reason is made again at the next attempt, so that a passing fault does
    not end the key or password.
    """

    _UNREAD = object()
    FREE, FIRST_WAIT, LONGEST_WAIT, QUIET = 5, 2.0, 300.0, 3600.0

    def __init__(self, *, clock=time.monotonic):
        self.failures = 0
        self.clock = clock
        self._hint, self._record = self._UNREAD, None
        self._streak, self._last, self._until, self._checking = 0, 0.0, 0.0, False

    @staticmethod
    def display(key: str) -> str:
        return "-".join(key[index:index + 5] for index in range(0, len(key), 5))

    def _saved(self, data_dir):
        from . import auth
        hint = auth.sign_in_key_hint(data_dir)
        if hint is auth._UNSEEN:
            return None                                     # A look that failed never finds a key.
        if self._hint is self._UNREAD or hint != self._hint:
            try:
                record = auth.read_sign_in_key(data_dir)
            except ValueError:
                record = None                               # Malformed: no key, until the file changes.
            except OSError:
                return None                                 # Not kept: the record is read again next time.
            if record != self._record:
                self._streak, self._until = 0, 0.0          # A wait is for the password that it was counted for.
            self._hint, self._record = hint, record
        return self._record

    def current(self, app):
        """The saved record that no sign-out of all browsers ended, or None: (second, "password" or "key")."""
        from .auth import sign_in_key_is_current
        record = self._saved(app["service"].data_dir)
        if record is None or not sign_in_key_is_current(app, record[0]):
            return None
        return record[0], "password" if record[1].startswith(_SCRYPT) else "key"

    def wait(self) -> int:
        """Whole seconds until a password is checked again; 0 when one is checked now."""
        return max(0, math.ceil(self._until - self.clock()))

    async def accepts(self, app, text):
        """Is the typed text the saved password or key, and did no sign-out of all browsers end it?

        True or False; a False is counted. None for a password that was not
        checked, because of a wait or of another check that runs: that is not
        counted, and it says nothing about the text.
        """
        from .auth import sign_in_key_is_current
        data_dir = app["service"].data_dir
        record = self._saved(data_dir)
        if record is not None and record[1].startswith(_SCRYPT):
            now = self.clock()
            if self._streak and now - self._last > self.QUIET:
                self._streak = 0
            if self._checking or now < self._until:
                return None
            password, right = _password_bytes(text), False
            if password is not None:
                salt, digest = record[1][len(_SCRYPT):].split(" ")
                self._checking = True
                try:
                    right = hmac.compare_digest(await asyncio.to_thread(_scrypt, password, bytes.fromhex(salt)), digest)
                except (ValueError, MemoryError, OSError, AttributeError):
                    right = False                           # A hash that cannot be made opens nothing.
                finally:
                    self._checking = False
            # The record is looked at again: a password that was changed or removed during the check opens nothing.
            if right and self._saved(data_dir) == record and sign_in_key_is_current(app, record[0]):
                self._streak = 0
                return True
            self.failures += 1
            self._streak += 1
            self._last = self.clock()
            if self._streak >= self.FREE:
                self._until = self._last + min(self.LONGEST_WAIT,
                                               self.FIRST_WAIT * 2 ** min(self._streak - self.FREE, 16))
            return False
        key = _typed(text)
        if (record is None or not _KEY.fullmatch(key)
                or not hmac.compare_digest(hashlib.sha256(key.encode("ascii")).hexdigest(), record[1])
                or not sign_in_key_is_current(app, record[0])):
            self.failures += 1
            return False
        return True


class PasswordRefused(ValueError):
    """The text cannot be the password; the message says why, in words for the owner."""


class OwnerSignedOut(RuntimeError):
    """A sign-out of all browsers ended the session that asked for the change. Nothing of it counts."""


class ChangedMeanwhile(RuntimeError):
    """Another change of the record came first. This change was not made."""


def _save(data_dir: Path, value: str, again: str) -> bool:
    """The owner's command saves the record of a new key or password; True when it replaced one that worked.

    The record gets a second after the saved sign-out, so that this sign-out
    does not end it (see `auth.sign_in_key_is_current`).
    """
    from . import auth
    signed_out = auth.saved_sign_out(data_dir)
    try:
        earlier = auth.read_sign_in_key(data_dir)
        replaced = earlier is not None and earlier[0] > signed_out
    except (OSError, ValueError):
        replaced = False                                    # A record that nobody could read opened nothing.
    for _ in range(5):
        made = int(time.time())
        if made > signed_out:
            break
        time.sleep(0.25)                                    # The second of a sign-out that was just saved.
    else:
        raise BrowserEntryUnavailable("The clock of this PC is earlier than the last sign-out of all browsers. "
                                      "Set the clock. " + again)
    record = (made, value)
    auth.write_sign_in_key(data_dir, record)
    if auth.read_sign_in_key(data_dir) != record:
        raise OSError("The record was not saved.")
    return replaced


def _swap(data_dir: Path, issued: int, value) -> bool:
    """The owner's page puts a record in place, or none for `value` None; True when a record was there before.

    `issued` is the second of the browser session that asks. The sender of a
    form decides how long its request takes, and the owner can sign out all
    browsers at any moment of it. Two things keep a session that such a
    sign-out ended from a change that counts.

    The record gets the second of that session, not the present second. So
    the rule that ends a session ends its record (`auth.sign_in_key_is_current`):
    a sign-out that ends the session has a second that is not before it, and
    a sign-out that the host did not apply yet opens no session (`auth.sign_in`).
    No moment has a record that opens a session for a session that is ended.

    The record goes in place only where the file still holds the bytes that
    this call saw before it read the saved sign-out
    (`auth.swap_sign_in_key`). A record that came later, for example the
    owner's new password after that sign-out, is never replaced or removed.

    A sign-out that is saved while this call runs, whatever its second,
    refuses the change as well: the file of the sign-out is looked at before
    all of this and again after the record is in place. This call then
    removes its own record, by its exact bytes, and no other.
    """
    from . import auth
    signed_out = auth._epoch_hint(data_dir)                 # First of all: the sign-out record as it is now.
    before = auth.sign_in_key_bytes(data_dir)               # Then the record that this change is for.
    if signed_out is auth._UNSEEN or issued <= auth.saved_sign_out(data_dir):
        raise OwnerSignedOut()                              # The saved second, not the one that the host applied.
    there = before not in (None, auth._NO_SIGN_IN_KEY)
    if value is None and not there:
        return False
    record = None if value is None else (issued, value)
    if not auth.swap_sign_in_key(data_dir, before, record):
        raise ChangedMeanwhile()
    if not auth._same_record(signed_out, auth._epoch_hint(data_dir)):
        if record is not None:
            auth.swap_sign_in_key(data_dir, f"{record[0]} {record[1]}\n".encode("ascii"), None)
        raise OwnerSignedOut()
    if auth.read_sign_in_key(data_dir) != record:
        raise OSError("The record was not saved.")
    return there


def make_key(data_dir: Path) -> tuple[str, bool]:
    """Save the record of a new sign-in key; return the key, and whether it replaced a working key or password.

    Authority is the owner's own private folder, as for the sign-out of all
    browsers: no network request and no credential are involved. Only the
    caller shows the key, once, and only to the owner; never log it.
    """
    key = "".join(secrets.choice(CODE_ALPHABET) for _ in range(KEY_LENGTH))
    return key, _save(data_dir, hashlib.sha256(key.encode("ascii")).hexdigest(), NEW_KEY)


def set_password(data_dir: Path, text, issued=None) -> bool:
    """Save the record of the owner's new password; True when a password or key was there before.

    The caller is the owner's page on this computer, with the second of its
    session (see `_swap`). Without that second the record is saved as a
    command saves a key (see `_save`). The password is not kept and must never
    be logged or shown. This call takes about a tenth of a second: never on
    the event loop.
    """
    if not isinstance(text, str) or len(text) < PASSWORD_MIN:
        raise PasswordRefused(f"Use at least {PASSWORD_MIN} characters.")
    if len(text) > PASSWORD_MAX:
        raise PasswordRefused(f"Use at most {PASSWORD_MAX} characters.")
    password = _password_bytes(text)
    if password is None:
        raise PasswordRefused("This text has a character that cannot be saved. Use another password.")
    salt = secrets.token_bytes(16)
    value = _SCRYPT + salt.hex() + " " + _scrypt(password, salt)
    return _save(data_dir, value, "Then set the password again.") if issued is None else _swap(data_dir, issued, value)


def remove_key(data_dir: Path, issued=None) -> bool:
    """End the saved key or password; True when a record was there. Browsers that are signed in stay signed in.

    With `issued`, the second of the browser session that asks, the removal
    is the page's (see `_swap`).
    """
    from . import auth
    if issued is not None:
        return _swap(data_dir, issued, None)
    try:
        if auth.read_sign_in_key(data_dir) is None:
            return False
    except (OSError, ValueError):
        pass                                                # A record that cannot be read is replaced as well.
    auth.write_sign_in_key(data_dir, None)
    if auth.read_sign_in_key(data_dir) is not None:
        raise OSError("The record was not removed.")
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
    app["browser_entry"] = app["browser_codes"] = app["browser_key"] = None
    if not is_windows():
        return
    try:
        token = _control_token(app["service"].data_dir, current_owner_sid())
        if not hmac.compare_digest(token.encode(), app["control_token"].encode()):
            return
    except BrowserEntryUnavailable:
        return
    app["browser_entry"], app["browser_codes"], app["browser_key"] = GrantStore(), CodeStore(), SavedKey()


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


def _said(text) -> None:
    try:  # A log fault changes nothing.
        print(text, flush=True)
    except (OSError, ValueError):
        pass


async def key_redeem(request):
    """Public: the typed password or key is the proof. A key has no lockout and no delay; a password waits (see SavedKey).

    The address check is the one of a typed code (see remote_origin), and the
    session is marked remote as well. The password or key is not used up.
    """
    from aiohttp import web
    from .auth import CSRF_COOKIE, SESSION_COOKIE, sign_in
    origin = remote_origin(request)
    saved = request.app["browser_key"]
    if not origin or not is_windows() or saved is None or request.content_type != "application/x-www-form-urlencoded":
        return web.Response(text=RELOAD_KEY, status=403)
    text = await _signed_form(request, "password")
    if text is None:
        return web.Response(text=RELOAD_KEY, status=403)
    answer = await saved.accepts(request.app, text)
    if answer is None:
        raise web.HTTPSeeOther("/login?error=wait", headers=_PRIVATE)
    if not answer:
        if saved.failures == 1 or saved.failures % 100 == 0:
            # The count only; never the typed text.
            _said(f"Remote sign-in: {saved.failures} wrong passwords or keys since start.")
        raise web.HTTPSeeOther("/login?error=password", headers=_PRIVATE)
    try:
        # As for a live code: the owner credential and the host identity are confirmed before a session opens.
        _binding(request, origin)
        session = sign_in(request.app, remote=True)
    except BrowserEntryUnavailable:
        session = None
    if session is None:
        # The host cannot be confirmed now, or a saved sign-out waits for this host.
        # Nothing opens; this is not counted as a wrong password.
        raise web.HTTPSeeOther("/login?error=password", headers=_PRIVATE)
    _said("Remote sign-in: a browser signed in with the saved password or key.")
    response = web.HTTPSeeOther("/", headers=_PRIVATE)
    response.set_cookie(SESSION_COOKIE, session,
                        max_age=request.app["server_config"]["session_ttl_seconds"],
                        httponly=True, samesite="Strict", secure=True, path="/")
    response.del_cookie(CSRF_COOKIE, path="/")
    raise response


_ON_THE_PC = "In Amplifier on the PC, " + SETTINGS_PATH[0].lower() + SETTINGS_PATH[1:].replace("&", "&amp;")
_PASSWORD_FORM = f"""<form method="post" action="{KEY_REDEEM_PATH}">
<input type="hidden" name="csrf" value="__CSRF__">
<label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password" autocapitalize="none" autocorrect="off" spellcheck="false" aria-describedby="password-help" __FOCUS__required>
<button type="submit">Sign in</button>
</form>
"""
_CODE_FORM = f"""<form method="post" action="{CODE_REDEEM_PATH}">
<input type="hidden" name="csrf" value="__CSRF__">
<label for="code">Code</label>
<input id="code" name="code" autocomplete="one-time-code" autocapitalize="characters" autocorrect="off" spellcheck="false" aria-describedby="code-help" __FOCUS__required>
<button type="submit">__BUTTON__</button>
</form>
"""
_CODE_HELP = f"""<p id="code-help">To get a code, run this on the Windows PC that runs Amplifier: <code>amplifier-unified browsers code</code>.
Type the code here within two minutes.</p>
<p>If Windows does not find that command, use the full command from the
<a href="{GUIDE}" target="_blank" rel="noopener noreferrer">Windows steps of the deployment guide</a>.</p>
"""
_REMOTE_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in to Amplifier</title>
<link rel="stylesheet" href="/app-pages.css">
</head><body><main>
<h1>Sign in to Amplifier</h1>
<p class="error" role="alert">__ERROR__</p>
__FORMS__</main></body></html>
"""
_WITH_PASSWORD = (_PASSWORD_FORM.replace("__FOCUS__", "autofocus ")
                  + '<p class="note">Or sign in with a one-use code from the PC.</p>\n'
                  + _CODE_FORM.replace("__FOCUS__", "").replace("__BUTTON__", "Sign in with code")
                  + '<p id="password-help">Type the password that you set in Amplifier on the PC, '
                  'or the sign-in key that a command on the PC showed. To change the password: '
                  + _ON_THE_PC + "</p>\n" + _CODE_HELP)
_WITHOUT_PASSWORD = (_CODE_FORM.replace("__FOCUS__", "autofocus ").replace("__BUTTON__", "Sign in")
                     + _CODE_HELP
                     + '<p id="password-help">To sign in with a password when you are not at the PC, '
                     "set the password on the PC first. " + _ON_THE_PC + "</p>\n")


def _duration(seconds: int) -> str:
    if seconds < 60:
        return "1 second" if seconds == 1 else f"{seconds} seconds"
    minutes = math.ceil(seconds / 60)
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def remote_login_page(request):
    """Sign-in page for a browser on another device: no script, nothing reflected.

    The password form shows only while a password or key is saved; it is then
    the first form. The code form always shows.

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
    saved = request.app.get("browser_key")
    with_password = saved is not None and saved.current(request.app) is not None
    kind = request.query.get("error")
    if kind == "wait" and with_password:
        wait = saved.wait()
        error = (f"Too many wrong passwords were typed. No password is checked for {_duration(wait)}. "
                 "Then type the password again. A code from the PC works now."
                 if wait else "The password was not checked. Type it again.")
    elif kind in {"password", "wait"}:
        error = ("That password did not work. Check it and type it again. A password stops working when you "
                 "change it or remove it on the PC, or sign out all browsers there.")
    else:
        error = ("That code did not work. Check it and type it again. A code works for two minutes "
                 "and only once. After that, make a new code on the PC.") if kind else ""
    page = (_REMOTE_PAGE.replace("__FORMS__", _WITH_PASSWORD if with_password else _WITHOUT_PASSWORD)
            .replace("__ERROR__", error).replace("__CSRF__", csrf))
    page = request.app['service'].instance_branding.document(page, login=True)
    response = web.Response(text=page,
                            content_type="text/html",
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin"})
    response.set_cookie(CSRF_COOKIE, csrf, httponly=True, samesite="Strict", secure=request.secure, path="/")
    return response


# The owner's page on this computer: set, change or remove the password for other devices.

PASSWORD_CARD = (f'<section class="card"><h2>Sign in from a browser on another device</h2>'
                 "<p>Set a password on this PC. A browser on another device then signs in with it, "
                 "also when you are not at the PC.</p>"
                 f'<div class="actions"><a class="button" href="{PASSWORD_PATH}">Set a password</a></div></section>\n')
_OWNER_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Password for other devices \u00b7 Amplifier</title>
<link rel="stylesheet" href="/connect/assets/connect.css"></head><body>
<main><a class="back" href="/connect">\u2190 Devices &amp; apps</a>
<p class="eyebrow">SIGN IN FROM ANOTHER DEVICE</p>
<h1>Password for other devices</h1>
__BODY__</main></body></html>
"""
_NOT_HERE = ("<p>Open this page in Amplifier on the PC that runs it, in a browser that signed in there. "
             "A browser that signed in with a password, a key or a code cannot set the password.</p>")
_NOT_WINDOWS = ("<p>On this computer, a browser on another device signs in with the password of the "
                "host account. There is no other password to set.</p>")
_SIGNED_OUT = ("<p>A sign-out of all browsers ended this sign-in. This change does not count. "
               "Open Amplifier again on the PC.</p>")
_CHANGED = ("<p>Another change of the password or key came first, so this change was not made. "
            f'<a href="{PASSWORD_PATH}">Open this page again</a>.</p>')
_NOT_NOW = "<p>This Amplifier cannot set a password for other devices now. Start Amplifier again on the PC.</p>"
_UNCONFIRMED_SET = ("Amplifier could not confirm that the password is saved. A password or key that you had "
                    "before may no longer work. Set the password again.")
_UNCONFIRMED_REMOVE = "Amplifier could not confirm the removal. Remove it again."


def _session_here(request):
    """The session of a browser that signed in on this computer, at an address of this computer; else None."""
    from .auth import SESSION_COOKIE, session_is_remote, valid_session
    session = request.cookies.get(SESSION_COOKIE)
    if (not valid_session(request.app, session) or session_is_remote(session)
            or not request.secure or not _on_this_computer(request)):
        return None
    return session


def password_card(request) -> str:
    """The card for the page "Devices & apps": only where the owner's page works."""
    here = is_windows() and request.app.get("browser_key") is not None and _session_here(request) is not None
    return PASSWORD_CARD if here else ""


def _owner_csrf(request, session) -> str:
    return hmac.new(request.app["session_secret"].encode(), ("sign-in-password:" + session).encode(),
                    hashlib.sha256).hexdigest()


def _owner_body(request, session, *, notice="", error=""):
    import html
    app = request.app
    current = app["browser_key"].current(app)
    csrf = _owner_csrf(request, session)
    if current is None:
        state, title, button = "No password is set.", "Set a password", "Set password"
    elif current[1] == "password":
        state, title, button = "A password is set.", "Change the password", "Change password"
    else:
        state, title, button = ("A sign-in key from a command is set. A password replaces it.",
                                "Set a password", "Set password")
    remote = [origin for origin in app["server_config"].get("public_origins", ()) if _remote_address(app, origin)]
    if remote:
        where = ("<p>On the other device, open one of these addresses and type the password:</p><ul>"
                 + "".join("<li><code>" + html.escape(origin) + "</code></li>" for origin in remote) + "</ul>"
                 '<p class="muted">The other device must trust the certificate of this Amplifier. '
                 'See <a href="/setup" target="_blank" rel="noopener">HTTPS setup</a>.</p>')
    else:
        where = ("<p>This Amplifier has no address for other devices yet, so no other device can reach it. The "
                 f'<a href="{GUIDE}" target="_blank" rel="noopener noreferrer">deployment guide</a> has the steps.</p>')
    name = "" if current is None else "password" if current[1] == "password" else "sign-in key"
    remove = "" if current is None else (
        f'<section class="card"><h2>Remove the {name}</h2>'
        "<p>After that, a browser on another device needs a one-use code from this PC. "
        "Browsers that are signed in stay signed in.</p>"
        f'<form method="post"><input type="hidden" name="csrf" value="{csrf}">'
        f'<button name="action" value="remove">Remove {name}</button></form></section>\n')
    return (
        '<p class="lead">A browser on another device signs in to this Amplifier with this password. '
        "You can use it when you are not at this PC.</p>\n"
        f'<div id="notice" role="{"alert" if error else "status"}">{html.escape(error or notice)}</div>\n'
        f'<section class="card"><h2>{title}</h2><p>{state}</p>'
        f'<form method="post"><input type="hidden" name="csrf" value="{csrf}">'
        '<label for="password">New password</label>'
        f'<input id="password" name="password" type="password" autocomplete="new-password" minlength="{PASSWORD_MIN}" '
        f'maxlength="{PASSWORD_MAX}" aria-describedby="password-help" required>'
        '<label for="confirm">New password again</label>'
        f'<input id="confirm" name="confirm" type="password" autocomplete="new-password" minlength="{PASSWORD_MIN}" '
        f'maxlength="{PASSWORD_MAX}" required>'
        f'<button class="primary" name="action" value="set">{button}</button></form>'
        f'<p id="password-help" class="muted">Use at least {PASSWORD_MIN} characters. Everyone who has this password '
        "and can reach an address for other devices can sign in to this Amplifier. Do not use a password that you "
        "use for something else. A sign-out of all browsers also removes the password.</p></section>\n"
        f'<section class="card"><h2>Where the password works</h2>{where}</section>\n' + remove)


async def password_page(request):
    """The owner sets, changes or removes the password for other devices. Only on this computer.

    The authority is a browser that signed in on this computer: a session
    that a code, a key or a password opened is marked remote and gets no
    form. A POST also needs HTTPS, both socket ends local, the exact local
    Origin (see local_origin) and the form's own token. The session is
    checked again after the form is read, and the change is bound to it (see
    `_swap`): a session that a sign-out of all browsers ended sets nothing
    that counts and removes nothing that came after it.
    The page has no script and shows nothing from the request. The password
    is never logged.
    """
    from aiohttp import web
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "same-origin"}

    def page(body, status=200):
        return web.Response(text=_OWNER_PAGE.replace("__BODY__", body), status=status, content_type="text/html",
                            headers=headers)
    saved = request.app.get("browser_key")
    if not is_windows():
        return page(_NOT_WINDOWS, 200 if request.method == "GET" else 403)
    if saved is None:
        return page(_NOT_NOW, 503)                          # The host could not confirm its owner credential at its start.
    session = _session_here(request)
    if session is None:
        return page(_NOT_HERE, 403)
    if request.method == "GET":
        current = saved.current(request.app)
        done = {("set", "password"): "The password is set.",
                ("removed", None): "No password or key is set now."}.get(
                    (request.query.get("done"), current[1] if current else None), "")
        return page(_owner_body(request, session, notice=done))
    if not local_origin(request) or request.content_type != "application/x-www-form-urlencoded":
        return page(_NOT_HERE, 403)
    form = await request.clone(client_max_size=16384).post()
    # Again, after the wait for the form: its sender decides how long that wait is, and a sign-out of all
    # browsers can end the session meanwhile.
    from .auth import session_issued
    session = _session_here(request)
    issued = session_issued(request.app, session)
    if session is None or issued is None:
        return page(_NOT_HERE, 403)
    if (not hmac.compare_digest(str(form.get("csrf", "")).encode(), _owner_csrf(request, session).encode())
            or any(len(form.getall(key)) != 1 for key in form)):
        return web.Response(text="This page expired. Open it again.", status=403, headers=headers)
    data_dir = request.app["service"].data_dir
    action = form.get("action")
    if action == "remove" and set(form) == {"csrf", "action"}:
        try:
            await asyncio.to_thread(remove_key, data_dir, issued)
        except OwnerSignedOut:
            return page(_SIGNED_OUT, 403)
        except ChangedMeanwhile:
            return page(_CHANGED, 409)
        except (OSError, ValueError):
            return page(_owner_body(request, session, error=_UNCONFIRMED_REMOVE), 500)
        raise web.HTTPSeeOther(PASSWORD_PATH + "?done=removed", headers=headers)
    if action != "set" or set(form) != {"csrf", "action", "password", "confirm"}:
        return web.Response(text="This page expired. Open it again.", status=403, headers=headers)
    password = form["password"]
    if not isinstance(password, str) or password != form["confirm"]:
        return page(_owner_body(request, session, error="The two passwords are not the same. Type them again."), 400)
    try:
        await asyncio.to_thread(set_password, data_dir, password, issued)
    except OwnerSignedOut:
        return page(_SIGNED_OUT, 403)
    except ChangedMeanwhile:
        return page(_CHANGED, 409)
    except PasswordRefused as refused:
        return page(_owner_body(request, session, error=str(refused)), 400)
    except BrowserEntryUnavailable as unavailable:
        return page(_owner_body(request, session, error=str(unavailable)), 409)
    except (OSError, ValueError):
        return page(_owner_body(request, session, error=_UNCONFIRMED_SET), 500)
    raise web.HTTPSeeOther(PASSWORD_PATH + "?done=set", headers=headers)


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
