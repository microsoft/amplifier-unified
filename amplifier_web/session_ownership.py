"""Conversation ownership is local interaction state, not a setup failure."""


def owner_label(owner):
    app = owner.get("app") or "another application"
    return {"amplifier-cli": "Amplifier CLI", "amplifier-unified": "Amplifier Unified"}.get(app, app)


def blocked(session, owner, detail=None, *, keep_pending=False):
    session["lockOwner"] = owner
    session["status"] = "read-only"
    session.pop("error", None)
    session.pop("progress", None)
    if session.get("ownership", {}).get("status") in {"yielding", "yielded", "yield-failed"}:
        return
    if keep_pending and session.get("ownership", {}).get("status") == "taking-over":
        return
    descriptor = owner.get("handoff") or {}
    descriptor = descriptor if isinstance(descriptor, dict) else {}
    session["ownership"] = {
        "status": "blocked", "source": owner_label(owner),
        "supportsTakeover": descriptor.get("version") == 1 and descriptor.get("transport") == "unix",
        **({"detail": detail} if detail else {}),
    }


def restore(session):
    """Upgrade only the old structured lock conflict; preserve unrelated errors."""
    owner, error = session.get("lockOwner"), session.get("error", "")
    if isinstance(owner, dict) and isinstance(error, str) and error.startswith("This conversation is in use"):
        blocked(session, owner)
