"""Bounded local discovery; never imports hosts or opens their history/config."""
import json
import os
from pathlib import Path
import re
import stat
import time
import hashlib

from .schema import MAX_BYTES, SCHEMA_VERSION, number

DEFAULT_DIRECTORY = Path.home() / ".amplifier" / "emitter"


def discover(directory=None, *, now=None, limit=512, instance=None):
    root = Path(directory or os.environ.get("AMPLIFIER_STATUS_DIR") or DEFAULT_DIRECTORY).expanduser()
    now = time.time() if now is None else now
    limit = min(4096, max(1, limit))
    result = {"schemaVersion": SCHEMA_VERSION, "sessions": [], "truncated": False, "invalid": 0,
              "scope": "instance" if instance else "directory", "complete": True}
    scanned = 0
    try:
        # Exact producer lookup never walks accumulated earlier instances.
        with os.scandir(root if not instance else root / hashlib.sha256(instance.encode()).hexdigest()[:32]) as entries:
            producers = entries if not instance else [None]
            for producer_index, producer in enumerate(producers):
                if producer_index >= limit:
                    result["truncated"] = True
                    result["complete"] = False
                    break
                if producer is not None and (not re.fullmatch(r"[a-f0-9]{32}", producer.name) or not producer.is_dir(follow_symlinks=False)):
                    continue
                with os.scandir(producer.path if producer else root / hashlib.sha256(instance.encode()).hexdigest()[:32]) as files:
                    for entry in files:
                        scanned += 1
                        if scanned > limit:
                            result["truncated"] = True
                            result["complete"] = False
                            return result
                        if not re.fullmatch(r"[a-f0-9]{64}\.json", entry.name):
                            continue
                        try:
                            # O_NOFOLLOW blocks symlink substitution where supported.
                            if not entry.is_file(follow_symlinks=False):
                                raise ValueError("Snapshot must be a regular file")
                            fd = os.open(entry.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                            with os.fdopen(fd, "rb") as stream:
                                info = os.fstat(stream.fileno())
                                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                                    raise ValueError("Invalid snapshot file")
                                if hasattr(os, "getuid") and info.st_uid != os.getuid():
                                    raise ValueError("Foreign snapshot")
                                if os.name != "nt" and info.st_mode & 0o077:
                                    raise ValueError("Snapshot is not private")
                                value = json.loads(stream.read(MAX_BYTES + 1))
                            if value.get("schemaVersion") != SCHEMA_VERSION or value.get("lifecycle") not in {"live", "ended", "unobserved"}:
                                raise ValueError("Unsupported status schema")
                            stamp, ttl = number(value.get("observedAt")), number(value.get("staleAfterSeconds"))
                            if stamp is None or ttl is None or not 0 < ttl <= 60:
                                raise ValueError("Invalid freshness")
                            value["freshness"] = (value["lifecycle"] if value["lifecycle"] != "live" else
                                                  "stale" if now < stamp - ttl or now - stamp > ttl else "live")
                            value["ageSeconds"] = max(0, now - stamp)
                            for tool in value.get("now", {}).get("tools", []):
                                started = number(tool.get("startedAt"))
                                tool["elapsedSeconds"] = max(0, now - started) if started is not None and value["freshness"] == "live" else None
                            if value.get("coverage", {}).get("complete") is False:
                                result["complete"] = False
                            result["sessions"].append(value)
                        except (OSError, ValueError, TypeError, AttributeError):
                            result["invalid"] += 1
                            result["complete"] = False
    except OSError:
        result["complete"] = False
    return result
