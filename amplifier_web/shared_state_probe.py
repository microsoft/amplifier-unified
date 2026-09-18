"""One read-only shared-session request in the isolated runtime.

The HTTP host never imports Foundation.  This small JSON-lines process is its
only path to enumerate or display Foundation's common checkpoint schema.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

MAX_REQUEST_BYTES = 64 * 1024
MAX_MESSAGES = 100


def text_content(message):
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") in {"text", "output_text"}
            and isinstance(block.get("text"), str)
        )
    return ""


def query(request):
    if set(request) - {"version", "op", "workspace", "sessionId", "offset", "limit"}:
        raise ValueError("The shared-state request contains unsupported fields.")
    if request.get("version") != 1:
        raise ValueError("The shared-state request version is unsupported.")
    workspace = Path(request.get("workspace", "")).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("workspace must be a directory.")
    from amplifier_foundation.session.shared_state import SharedSessionStore

    operation = request.get("op")
    if operation == "list":
        return {"items": [
            {"id": identity, "workspace": str(workspace), "shared": True}
            for identity in SharedSessionStore.list_ids(workspace)
        ]}
    if operation != "view":
        raise ValueError("The shared-state operation is unsupported.")
    identity = request.get("sessionId")
    if not isinstance(identity, str) or not identity:
        raise ValueError("sessionId is required.")
    offset, limit = request.get("offset", 0), request.get("limit", 50)
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer.")
    if type(limit) is not int or not 1 <= limit <= MAX_MESSAGES:
        raise ValueError("limit must be an integer from 1 through 100.")
    checkpoint = SharedSessionStore(workspace, identity).read()
    if checkpoint is None:
        raise ValueError("The shared session was not found.")
    messages = [
        {"role": row["role"], "text": text_content(row)}
        for row in checkpoint["messages"]
        if isinstance(row, dict) and row.get("role") in {"user", "assistant"}
        and text_content(row)
    ]
    return {
        "id": identity,
        "workspace": str(workspace),
        "bundle": checkpoint["bundle"],
        "messages": messages[offset:offset + limit],
        "offset": offset,
        "nextOffset": offset + limit if offset + limit < len(messages) else None,
        "totalMessages": len(messages),
    }


def main():
    output = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("The shared-state request is too large.")
        result = query(json.loads(raw.decode("utf-8")))
        reply = {"ok": True, "result": result}
    except Exception as exc:
        reply = {"ok": False, "error": {"code": type(exc).__name__, "message": str(exc)[:500]}}
    output.write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()