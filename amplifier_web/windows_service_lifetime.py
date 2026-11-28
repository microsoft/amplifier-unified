"""One bounded owner choice and rolling start limit. No sign-in tracking.

Legacy dispatch is unchanged until the paired launcher/task migration. Stop is
persistent. Only the person's explicit Start clears it. Paused ticks are reads.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import time
import uuid

RECORD = "task-lifetime.json"
LOCK = "task-lifetime.lock"
LIMIT = 4096
WINDOW = 6 * 60 * 60
STARTS = 3
STOPPED = "Amplifier is stopped by you. Use Amplifier's Start to start it again."
BUDGET = "Amplifier paused after three automatic starts in six hours. Use Amplifier's Start to try again."
UNREADABLE = "Amplifier could not read its saved start choice. Use Amplifier's Start to try again."


class Paused(RuntimeError):
    """An automatic entry cannot start. This is not a new app failure."""


def supported(spec):
    """Do not add a new state requirement to an unrecognised launcher."""
    if not spec.supervised:
        return False
    if spec.repeat_start is not None:
        return True
    from .application_generations import supports
    return supports(Path(spec.python).parent.parent)


def validate(value, spec):
    fields = {"schema", "registration", "ownerSid", "intent", "stopped", "manual", "starts", "lastTime"}
    if (not isinstance(value, dict) or set(value) != fields or type(value["schema"]) is not int
            or value["schema"] != 1 or value["registration"] != spec.registration_id
            or value["ownerSid"] != spec.sid or not isinstance(value["intent"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", value["intent"])
            or type(value["stopped"]) is not bool or type(value["manual"]) is not bool
            or value["stopped"] and value["manual"]
            or type(value["lastTime"]) is not int or not 0 <= value["lastTime"] < 2**53
            or not isinstance(value["starts"], list) or len(value["starts"]) > STARTS
            or any(type(n) is not int or not 0 <= n <= value["lastTime"] for n in value["starts"])
            or value["starts"] != sorted(value["starts"])):
        raise ValueError(UNREADABLE)
    return value


@contextmanager
def store(spec, *, writing=True):
    from .windows_files import address, directory
    root = address(spec.data_dir)
    with directory(root / "config/services", owned_root=root, create=writing) as pinned:
        if writing:
            from filelock import FileLock
            with FileLock(pinned.path / LOCK, timeout=5, mode=0o600):
                yield pinned
        else:
            yield pinned


def _closed(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("The saved start choice has a repeated field.")
        value[key] = item
    return value


def read(pinned, spec):
    return validate(json.loads(pinned.read_private(RECORD, limit=LIMIT), object_pairs_hook=_closed), spec)


def write(pinned, spec, value):
    raw = json.dumps(validate(value, spec), sort_keys=True).encode("utf-8")
    if len(raw) > LIMIT:
        raise ValueError("The saved start choice is too large.")
    pinned.publish_private(RECORD, raw, replace=True)


def owner_choice(spec, *, stop, manual=True):
    """An explicit Start need not accept the old record's content."""
    if not supported(spec):
        return None
    with store(spec) as pinned:
        try:
            previous = read(pinned, spec)
        except Exception:
            previous = None
        now = int(time.time())
        value = {
            "schema": 1, "registration": spec.registration_id, "ownerSid": spec.sid,
            "intent": uuid.uuid4().hex, "stopped": stop,
            "manual": bool(manual and not stop), "starts": previous["starts"] if previous else [],
            "lastTime": max(now, previous["lastTime"] if previous else 0),
        }
        write(pinned, spec, value)
        return value["intent"]


def pause_reason(value, now):
    if value["stopped"]:
        return STOPPED
    if not value["manual"]:
        if now < value["lastTime"]:
            return UNREADABLE
        if len([at for at in value["starts"] if at > now - WINDOW]) >= STARTS:
            return BUDGET
    return None


def status(spec):
    """Read-only status, not health. Legacy state is only a migration hint."""
    if spec.repeat_start is None:
        return None
    try:
        with store(spec, writing=False) as pinned:
            return pause_reason(read(pinned, spec), int(time.time()))
    except Exception:
        return UNREADABLE


def admit(spec):
    """Read a paused tick without a lock write. Recheck under lock before reserve."""
    if spec.repeat_start is None or not supported(spec):
        return None
    try:
        with store(spec, writing=False) as pinned:
            reason = pause_reason(read(pinned, spec), int(time.time()))
            if reason:
                raise Paused(reason)
        with store(spec) as pinned:
            value, now = read(pinned, spec), int(time.time())
            reason = pause_reason(value, now)
            if reason:
                raise Paused(reason)
            if value["manual"]:
                value = {**value, "manual": False}
            else:
                starts = [at for at in value["starts"] if at > now - WINDOW]
                value = {**value, "starts": [*starts, now], "lastTime": now}
            write(pinned, spec, value)
            return value["intent"]
    except Paused:
        raise
    except Exception as error:
        raise Paused(UNREADABLE) from error


def allowed(spec, intent, *, exact=False):
    """Only at launcher transitions, never in the host's quarter-second watch."""
    if intent is None:
        return True
    try:
        with store(spec, writing=False) as pinned:
            value = read(pinned, spec)
            return not value["stopped"] and (not exact or value["intent"] == intent)
    except Exception:
        return False
