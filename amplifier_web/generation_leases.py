"""Private durable references from live worker processes to source generations."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


def acquire(home, generation, pid):
    from .host.config import write_private

    path = Path(home) / "updates/worker-leases" / (uuid.uuid4().hex + ".json")
    write_private(path, json.dumps({"schema": 1, "generation": generation, "pid": pid}))
    return path


def references(home):
    generations = set()
    for path in (Path(home) / "updates/worker-leases").glob("*.json"):
        record = json.loads(path.read_text())
        if (
            record.get("schema") != 1
            or type(record.get("pid")) is not int
            or record["pid"] <= 0
        ):
            raise ValueError("Unrecognized worker lease; storage retained")
        try:
            os.kill(record["pid"], 0)
        except ProcessLookupError:
            # Crash recovery: no live holder remains. PID reuse can only retain
            # too much storage, never remove a live generation.
            path.unlink(missing_ok=True)
            continue
        except PermissionError:
            pass
        generations.add(record["generation"])
    return generations
