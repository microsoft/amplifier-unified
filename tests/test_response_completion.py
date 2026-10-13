"""Completion provenance is independent of text guesses, idle and work order."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.response_completion import ResponseBlocks, apply, invalidate_removed
from amplifier_web.runtime import normalize_event
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppService


def blocks(tracker, texts, *, generation="g", start=10):
    content = [{"type": "text", "text": text} for text in texts]
    for index, block in enumerate(content):
        data = {"block_index": index, "total_blocks": len(content), "block": block}
        tracker.block("content_block:start", data, generation)
        tracker.block("content_block:end", data, generation)
        tracker.message({"generation_id": generation, "sequence": start + index, "text": block["text"]})
    return {"role": "assistant", "content": content}


def saved(sequence, text="same", generation="g"):
    return {"id": f"{generation}-{sequence}", "role": "assistant", "generationId": generation,
            "inputId": "input", "runtimeMessage": {"sequence": sequence}, "text": text}


def completion(sequences=(10, 11)):
    return {"event": "generation.finished", "generation_id": "g", "sessionId": "root",
            "rootSessionId": "root", "input_ids": ["input", "steer"], "text": "samesame",
            "finalMessageRefs": {"version": 1, "generationId": "g", "sequences": list(sequences)}}


def test_indexed_blocks_distinguish_repeated_text_and_preserve_multipart_final():
    tracker = ResponseBlocks()
    blocks(tracker, ["same"], start=1)
    final = blocks(tracker, ["same", "same"])
    assert tracker.terminal(final, "g")["sequences"] == [10, 11]
    assert tracker.terminal(final, "other") is None
    assert tracker.terminal({**final, "tool_calls": [{"id": "tool"}]}, "g") is None


def test_missing_or_mismatched_block_evidence_never_matches_a_text_suffix():
    tracker = ResponseBlocks()
    final = blocks(tracker, ["answer"])
    assert tracker.terminal({"role": "assistant", "content": "different"}, "g") is None
    tracker.reset()
    tracker.message({"generation_id": "g", "sequence": 99, "text": "answer"})
    assert tracker.terminal(final, "g") is None
    tracker.block("content_block:start", {"block_index": 1, "total_blocks": 2}, "g")
    assert tracker.terminal(final, "g") is None


def test_steering_replaces_candidate_group_without_finishing_generation():
    tracker = ResponseBlocks()
    first = blocks(tracker, ["first"], start=1)
    assert tracker.terminal(first, "g")["sequences"] == [1]
    second = blocks(tracker, ["later"], start=20)
    assert tracker.terminal(first, "g") is None
    assert tracker.terminal(second, "g")["sequences"] == [20]


def test_service_annotation_requires_all_exact_identities_and_is_idempotent():
    session = {"id": "root", "messages": [saved(1), saved(10)]}
    assert not apply(session, completion())
    assert not any(row.get("response") for row in session["messages"])
    session["messages"].extend([saved(11), saved(99, generation="other")])
    assert apply(session, completion())
    assert [row.get("response", {}).get("phase") for row in session["messages"]] == ["interim", "final", "final", None]
    before = deepcopy(session)
    assert apply(session, completion())
    assert session == before
    invalidate_removed(session, {"g-11"})
    assert not any(row.get("response") for row in session["messages"])


@pytest.mark.parametrize("change", [
    {"sessionId": "child"}, {"rootSessionId": "child"}, {"event": "generation.started"},
    {"finalMessageRefs": {}}, {"finalMessageRefs": {"version": 1, "generationId": "g", "sequences": [10, 10]}},
])
def test_nonterminal_child_and_invalid_receipts_are_not_eligible(change):
    session = {"id": "root", "messages": [saved(1), saved(10), saved(11)]}
    assert not apply(session, {**completion(), **change})
    assert not any(row.get("response") for row in session["messages"])


@pytest.mark.parametrize("event", ["generation.failed", "generation.detached"])
def test_terminal_failure_removes_earlier_completion_annotation(event):
    session = {"id": "root", "messages": [saved(1), saved(10), saved(11)]}
    apply(session, completion())
    apply(session, {**completion(), "event": event})
    assert not any(row.get("response") for row in session["messages"])


async def test_real_hook_order_checkpoint_and_runtime_normalization(monkeypatch):
    from amplifier_core.hooks import HookRegistry
    from amplifier_core import HookResult
    hooks = HookRegistry()
    worker = Worker()
    capabilities = {"live.checkpoint": AsyncMock(return_value=[])}
    coordinator = SimpleNamespace(session_id="root", hooks=hooks,
        get_capability=capabilities.get, register_capability=capabilities.__setitem__, get=lambda name: None)
    worker.session = SimpleNamespace(coordinator=coordinator)
    worker.runtime = SimpleNamespace(session_id="root", generation={"id": "g"})
    worker.install_activity(coordinator)
    worker.install_collaboration_checkpoint(coordinator)
    events = []
    monkeypatch.setattr("amplifier_web.runtime_worker.publish", events.append)
    sequence = 0

    async def loop_live_observer(event, data):
        nonlocal sequence
        block = data["block"]
        if block["type"] == "text" and block.get("text"):
            sequence += 1
            worker.observe({"type": "assistant.message", "generation_id": "g", "sequence": sequence,
                            "session_id": "root", "input_ids": ["input"], "text": block["text"]})
        return HookResult()

    hooks.register("content_block:end", loop_live_observer, name="live-manager-content_block:end")
    for texts in (["same"], ["same", "same"]):
        row = {"role": "assistant", "content": [{"type": "text", "text": text} for text in texts]}
        for index, block in enumerate(row["content"]):
            data = {"block_index": index, "total_blocks": len(texts), "block": block}
            await hooks.emit("content_block:start", data)
            await hooks.emit("content_block:end", data)
    # Reinstall with the exact persisted terminal row, using a fresh coordinator capability.
    capabilities.pop("web.collaboration.checkpoint")
    capabilities["live.checkpoint"] = AsyncMock(return_value=[row])
    worker.install_collaboration_checkpoint(coordinator)
    await capabilities["live.checkpoint"]()
    worker.observe({"type": "generation.finished", "generation_id": "g", "text": "samesame",
                    "input_ids": ["input"], "active_job_ids": ["still-running"]})
    event = next(event for event in reversed(events) if event["type"] == "generation.finished")
    assert event["finalMessageRefs"]["sequences"] == [2, 3]
    kind, payload = normalize_event(event, "root")
    assert kind == "runtime.generation" and payload["finalMessageRefs"] == event["finalMessageRefs"]


async def test_completion_before_message_reload_and_older_than_event_caps(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch("session.create", {})
        session = app._session()
        sid = session["id"]
        for sequence in (1, 10):
            await app.on_runtime_event("assistant.message", {"sessionId": sid, **saved(sequence)})
        event = {**completion(), "sessionId": sid, "rootSessionId": sid}
        await app.on_runtime_event("runtime.generation", event)
        assert not session["messages"][-1].get("response")
        await app.on_runtime_event("assistant.message", {"sessionId": sid, **saved(11)})
        assert [row["response"]["phase"] for row in session["messages"]] == ["interim", "final", "final"]
        for index in range(205):
            await app.on_runtime_event("runtime.generation", {"sessionId": sid, "rootSessionId": sid,
                "event": "generation.started", "generation_id": f"later-{index}"})
        from amplifier_web.browser_detail import page
        assert page(session, "messages")["items"][0]["response"]["phase"] == "interim"
        ids = [row["id"] for row in session["messages"]]
    finally:
        await app.close()
    restored = AppService(tmp_path, workspace=tmp_path)
    try:
        session = restored._session(sid)
        assert [row["id"] for row in session["messages"]] == ids
        assert session["messages"][0]["response"]["phase"] == "interim"
    finally:
        await restored.close()