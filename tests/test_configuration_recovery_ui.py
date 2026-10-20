"""Observational UI eligibility uses the same original-operation validation.

The frozen 127-case native/regression suite remains separate and unchanged.
"""
import asyncio
import copy
import json
import time

import pytest

from amplifier_web.configuration_recovery import receipt_path, seal
from amplifier_web.service import AppError
from test_configuration_authority import revoke
from test_queued_configuration_boundary import (
    boundary, native_refusal, operation, recovery, recovery_http_boundary,
    terminal_refusal, http_recovery,
)


async def status(b, **extra):
    args = {"sessionId": b.owner["id"], "includeRecovery": True, **extra}
    return (await b.app.dispatch("configuration.operation.read", args))["result"]


async def test_optional_status_retains_default_shape_and_never_warms(boundary):
    b = boundary
    saved = await native_refusal(b)
    before = copy.deepcopy(saved)
    calls, tasks = list(b.runtime.calls), set(b.app.tasks)
    grants = copy.deepcopy(b.app.configuration_authority.rows(b.owner["id"]))
    raw = (await recovery(b, "read"))["result"]
    assert "recovery" not in raw
    assert (await status(b, includeRecovery=False)) == raw
    view = await status(b)
    assert {k: v for k, v in view.items() if k != "recovery"} == raw
    assert view["recovery"]["canReconcile"] and not view["recovery"]["canResume"]
    assert operation(b) == before and b.runtime.calls == calls and set(b.app.tasks) == tasks
    assert b.app.configuration_authority.rows(b.owner["id"]) == grants
    await recovery(b, "reconcile")
    assert (await status(b))["recovery"]["canResume"]
    assert b.runtime.calls == calls


@pytest.mark.parametrize("invalid", ["revoked", "expired", "stop", "execution", "payload", "task"])
async def test_status_reflects_existing_guards_and_old_eligibility_cannot_authorize(boundary, invalid):
    b = boundary
    await native_refusal(b)
    await recovery(b, "reconcile")
    assert (await status(b))["recovery"]["canResume"]
    blocked = None
    try:
        if invalid == "revoked":
            await revoke(b.app, b.authorized)
        elif invalid == "expired":
            row = b.app.configuration_authority.record(b.authorized["id"])
            row["expiresAt"] = time.time() - 1
            b.app.configuration_authority.save(row)
            b.app.db.commit()
        elif invalid == "stop":
            b.owner["interruptionRevision"] = 1
        elif invalid == "execution":
            b.owner["executionRevision"] = 1
        elif invalid == "payload":
            b.app.management.queued_path(b.owner["id"]).write_text('{"private":"SYNTHETIC_PRIVATE_MARKER"}')
        else:
            blocked = asyncio.create_task(asyncio.Event().wait())
            b.app.management.pending_config_tasks[b.owner["id"]] = blocked
        calls = list(b.runtime.calls)
        view = await status(b)
        assert not view["recovery"]["canResume"] and not view["recovery"]["canReconcile"]
        assert "SYNTHETIC_PRIVATE_MARKER" not in json.dumps(view)
        with pytest.raises(AppError):
            await recovery(b, "resume")
        assert b.runtime.calls == calls
    finally:
        if blocked is not None:
            blocked.cancel()
            await asyncio.gather(blocked, return_exceptions=True)
            b.app.management.pending_config_tasks.pop(b.owner["id"], None)


@pytest.mark.parametrize("fault", ["legacy", "missing", "malformed", "malformed-state", "malformed-history", "receipt-forged", "receipt-nonterminal", "receipt-saved", "receipt-unknown"])
async def test_unverified_or_saved_state_never_enables_resume(boundary, fault):
    b = boundary
    saved = await native_refusal(b)
    calls = list(b.runtime.calls)
    path = b.app.management.configuration_record_path(b.owner["id"], saved["id"])
    if fault == "legacy":
        b.owner["pendingConfiguration"] = {"phase": "error", "error": "SYNTHETIC_PRIVATE_MARKER"}
    elif fault == "missing":
        path.unlink()
    elif fault == "malformed":
        path.write_text('SYNTHETIC_PRIVATE_MARKER /private/synthetic')
    elif fault in {"malformed-state", "malformed-history"}:
        saved["state" if fault == "malformed-state" else "history"] = None
        path.write_text(json.dumps(saved))
    else:
        attempt = saved["attempts"][-1]
        payload = seal(attempt, {"receipt-saved": "saved_not_mounted", "receipt-unknown": "unknown"}.get(fault, "none"), terminal=fault != "receipt-nonterminal")
        if fault == "receipt-forged":
            payload["signature"] = "SYNTHETIC_PRIVATE_MARKER"
        receipt_path(attempt).write_text(json.dumps(payload))
    view = await status(b)
    assert not view["recovery"]["canResume"]
    if fault not in {"receipt-saved", "receipt-unknown"}:
        assert not view["recovery"]["canReconcile"]
    assert "SYNTHETIC_PRIVATE_MARKER" not in json.dumps(view)
    assert "/private/synthetic" not in json.dumps(view)
    assert b.runtime.calls == calls


async def test_terminal_owner_cookie_status_is_reachable_but_agent_resume_is_not(recovery_http_boundary):
    b = recovery_http_boundary
    saved = await terminal_refusal(b)
    calls = list(b.runtime.calls)
    response = await b.client.post("/api/actions", headers=b.headers, json={
        "action": "configuration.operation.read", "args": {
            "sessionId": b.owner["id"], "operationId": saved["id"], "includeRecovery": True}})
    assert response.status == 200
    view = (await response.json())["result"]
    assert view["sessionId"] == b.owner["id"] != b.peer["id"]
    assert view["recovery"]["canReconcile"]
    assert (await http_recovery(b, "reconcile")).status == 200
    assert (await status(b))["recovery"]["canResume"]
    with pytest.raises(AppError, match="transport-bound"):
        await recovery(b, "resume")
    assert b.runtime.calls == calls and operation(b)["request"] == saved["request"]


async def test_projection_cannot_be_borrowed_by_a_peer(boundary):
    b = boundary
    await native_refusal(b)
    with pytest.raises(AppError):
        await recovery(b, "read", actor=b.peer, includeRecovery=True)
