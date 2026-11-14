"""One read-only shared-session request in the isolated runtime.

Native transcript/metadata are authoritative; old common checkpoints are an
explicit fallback only. Browsing never acquires a lock or rewrites history.
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

    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_web.session_files import sessions_dir, validate_id
    from amplifier_web import committed_source
    root = sessions_dir(workspace)
    operation = request.get("op")
    if operation == "list":
        native = {path.name for path in root.iterdir() if path.is_dir() and not path.is_symlink()
                  and committed_source.present(path)} if root.exists() else set()
        return {"items": [
            {"id": identity, "workspace": str(workspace), "shared": True}
            for identity in sorted(native | set(SharedSessionStore.list_ids(workspace)))
        ]}
    if operation not in {"view", "open"}:
        raise ValueError("The shared-state operation is unsupported.")
    identity = request.get("sessionId")
    if not isinstance(identity, str) or not identity:
        raise ValueError("sessionId is required.")
    offset, limit = request.get("offset", 0), request.get("limit", 50)
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer.")
    if type(limit) is not int or not 1 <= limit <= MAX_MESSAGES:
        raise ValueError("limit must be an integer from 1 through 100.")
    validate_id(identity)
    if committed_source.versioned(root / identity):
        return _committed_query(root / identity, workspace, identity, operation, offset, limit)
    history = SessionHistoryStore(root / identity, session_id=identity)
    if committed_source.present(root / identity):
        value = committed_source.load(root / identity, session_id=identity)
        checkpoint = {"messages": value.messages, "metadata": value.metadata,
                      "bundle": value.metadata.get("bundle_name") or value.metadata.get("bundle")}
    else:
        checkpoint = SharedSessionStore(workspace, identity).read()
    if checkpoint is None:
        raise ValueError("The shared session was not found.")
    messages = [
        {"role": row["role"], "text": text_content(row)}
        for row in checkpoint["messages"]
        if isinstance(row, dict) and row.get("role") in {"user", "assistant"}
        and not (row.get("metadata") or {}).get("ephemeral") and text_content(row)
    ]
    if operation == "open":
        offset = max(0, len(messages) - limit)
    return {
        "id": identity,
        "workspace": str(workspace),
        "bundle": checkpoint["bundle"],
        "name": checkpoint["metadata"].get("name") or checkpoint["metadata"].get("title"),
        "messages": messages[offset:offset + limit],
        "offset": offset,
        "nextOffset": offset + limit if offset + limit < len(messages) else None,
        "totalMessages": len(messages),
    }


def _committed_query(directory, workspace, identity, operation, offset, limit):
    """Reuse the canonical checked source; retain only the requested text page."""
    from collections import deque
    from amplifier_web.committed_source import background_sources
    from amplifier_web.transcript_window import RECORD_LIMIT, _bounded_json, _OBJECT_LIMIT
    from amplifier_foundation.session.history import SessionHistoryStore
    owner = {'project': str(directory.parent), 'nativeId': identity}
    # This legacy shared API promises exact visible totals/offsets. Its explicit
    # scalar pass is linear, but never materializes full history or huge rows.
    # The ordinary cursor/window API is the O(page) path for selected browsing.
    with background_sources().source(directory, owner) as source:
        recent, page, total = deque(maxlen=limit), [], 0
        for index in range(source.commit.message_count):
            bounds = source.message_range(index)
            if bounds.end - bounds.start > RECORD_LIMIT:
                raise ValueError('The shared history contains a record outside the bounded view budget.')
            row = _bounded_json(b''.join(source.iter_bytes(bounds.start, bounds.end)))
            if row is _OBJECT_LIMIT:
                raise ValueError('The shared history contains a record outside the bounded object budget.')
            if (row.get('role') not in {'user', 'assistant'}
                    or (row.get('metadata') or {}).get('ephemeral') or not text_content(row)):
                continue
            visible = {'role': row['role'], 'text': text_content(row)}
            if operation == 'open':
                recent.append(visible)
            elif offset <= total < offset + limit:
                page.append(visible)
            total += 1
        metadata = SessionHistoryStore(directory, session_id=identity).load_metadata()
        source.read_range(0, 0)
    if operation == 'open':
        offset, page = max(0, total - limit), list(recent)
    return {'id': identity, 'workspace': str(workspace),
            'bundle': metadata.get('bundle_name') or metadata.get('bundle'),
            'name': metadata.get('name') or metadata.get('title'),
            'messages': page, 'offset': offset, 'totalMessages': total,
            'nextOffset': offset + limit if offset + limit < total else None}


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