"""Version 1 allow-list. Inputs are observations, never instructions/authority."""
import math
import re

SCHEMA_VERSION = 1
MAX_ITEMS = 200
MAX_SESSIONS = 256
MAX_BYTES = 65536
STATES = {"idle", "preparing", "thinking", "tool", "waiting_approval", "waiting_person",
          "waiting_workers", "stopping", "error", "interrupted", "unknown"}


def text(value, limit=200):
    return value[:limit] if isinstance(value, str) else None


def name(value):
    # Names, not arbitrary provider URLs, instructions, exception strings, etc.
    return value if isinstance(value, str) and re.fullmatch(r"[\w./:@+-]{1,160}", value) and "://" not in value else None


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def plan(items, *, include_text=False):
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        return {"state": "unavailable", "items": [], "counts": {}}
    rows, counts = [], {"pending": 0, "in_progress": 0, "completed": 0}
    for i, item in enumerate(items):
        if not isinstance(item, dict) or item.get("status") not in counts:
            return {"state": "unavailable", "items": [], "counts": {}}
        status = item["status"]
        counts[status] += 1
        row = {"index": i, "status": status}
        if include_text and isinstance(item.get("content"), str):
            row["text"] = item["content"][:160]  # explicit local text policy only
        rows.append(row)
    return {"state": "observed", "items": rows, "counts": counts,
            "currentSteps": [r["index"] for r in rows if r["status"] == "in_progress"]}


def snapshot(value, *, include_text=False):
    """Copy only bounded public facts. Never serialize raw caller dictionaries."""
    where = value.get("where") or {}
    now = value.get("now") or {}
    out = {
        "where": {key: text(where.get(key), 1024 if key in {"workspace", "cwd", "worktree"} else 200)
                  for key in ("sessionId", "parentSessionId", "workspace", "cwd", "worktree")},
        "now": {"status": now.get("status") if now.get("status") in STATES else "unknown",
                "turnId": text(now.get("turnId")), "since": number(now.get("since")),
                "tools": [], "workers": [],
                "pendingQuestions": number(now.get("pendingQuestions")),
                "pendingApprovals": number(now.get("pendingApprovals"))},
        "plan": plan(value.get("planItems"), include_text=include_text),
        "health": {"error": value.get("error") is True},
        "textPolicy": "plan_labels" if include_text else "redacted",
    }
    for key in ("bundle", "provider", "model"):
        out["where"][key] = name(where.get(key))
    for item in (now.get("tools") or [])[:32]:
        if isinstance(item, dict):
            out["now"]["tools"].append({"id": text(item.get("id")), "name": name(item.get("name")),
                                         "startedAt": number(item.get("startedAt"))})
    for item in (now.get("workers") or [])[:32]:
        if isinstance(item, dict):
            out["now"]["workers"].append({"sessionId": text(item.get("sessionId")),
                                           "status": name(item.get("status"))})
    # Usage is an explicitly labelled observation, never a guessed context size.
    usage = value.get("usage") or {}
    out["usage"] = {key: number(usage.get(key)) for key in
                    ("inputTokens", "outputTokens", "totalTokens", "costUsd")}
    out["usage"]["scope"] = value.get("usageScope") if value.get("usageScope") in {"last_call", "current_turn", "conversation"} else "unavailable"
    return out
