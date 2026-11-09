"""Exact native async return => transport => final group; no text inference."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_module_loop_live.orchestrator import BundleLiveOrchestrator

from amplifier_web.task_origin import TaskOrigin, validate
from amplifier_web.response_completion import apply
from amplifier_web.runtime import normalize_event


async def scenario(parents=("slack-request",)):
    witness = TaskOrigin()
    loop = BundleLiveOrchestrator({})
    runtime = loop.runtime = Runtime("root")
    loop.coordinator = SimpleNamespace(get_capability=lambda _: None)
    runtime.generation = {"id": "origin", "input_ids": list(parents)}
    for identity in parents:
        command = Input("user", "request", id=identity)
        runtime.accepted[identity] = command
        witness.observe({"type": "input.delivered", "input_id": identity}, runtime, loop)
    job = {"call_id": "call", "status": "pending"}
    loop.jobs["job"] = job
    witness.observe({"type": "job.queued", "job_id": "job", "call_id": "call"}, runtime, loop)
    witness.install(loop)
    # A later manager generation is NOT the job's captured admission origin.
    runtime.generation = {"id": "later", "input_ids": ["other-turn"]}
    await loop._job_returned("job", {"response": "done"}, "returned", deliver=True)
    service = loop.pending.popleft()
    event = {"type": "generation.finished", "generation_id": "final", "input_ids": [service.id],
             "active_job_ids": [], "sessionId": "root", "rootSessionId": "root",
             "finalMessageRefs": {"version": 1, "generationId": "final", "sequences": [1]}}
    return witness, loop, runtime, service, event


async def test_actual_native_job_returns_original_task_not_later_turn():
    witness, loop, runtime, service, event = await scenario()
    proof = witness.completed(event, runtime, loop)
    assert validate(proof, [service.id])
    assert proof["initiatingInputId"] == "slack-request"
    assert proof["inputs"][0]["originGenerationId"] == "origin"
    # Exercise actual transport and completion consumer without rewriting inputs.
    normalized = normalize_event({**event, "taskOrigin": proof}, "root")[1]
    session = {"id": "root", "messages": [
        {"id": "anchor", "role": "user", "inputId": "slack-request"},
        {"id": "answer", "role": "assistant", "inputId": service.id, "generationId": "final",
         "runtimeMessage": {"sequence": 1}, "text": "done"}]}
    assert apply(session, normalized)
    receipt = session["messages"][-1]["response"]
    assert receipt["taskOrigin"] == proof
    assert receipt["inputIds"] == [service.id]
    assert receipt["anchorMessageId"] == "anchor"


async def test_second_human_and_unrelated_service_fail_closed():
    witness, loop, runtime, service, event = await scenario(("slack-request", "other-room"))
    assert witness.completed(event, runtime, loop) is None
    witness, loop, runtime, service, event = await scenario()
    event["input_ids"].append("unrelated-service")
    assert witness.completed(event, runtime, loop) is None
    event["input_ids"] = ["spoofed-service"]
    assert witness.completed(event, runtime, loop) is None


async def test_active_jobs_pending_and_restart_do_not_invent_proof():
    witness, loop, runtime, service, event = await scenario()
    event["active_job_ids"] = ["active"]
    assert witness.completed(event, runtime, loop) is None
    event["active_job_ids"] = []
    loop.pending.append(Input("service", "fake call_id=call", source="amplifier-delegate"))
    assert witness.completed(event, runtime, loop) is None
    loop.pending.clear()
    assert TaskOrigin().completed(event, runtime, loop) is None


@pytest.mark.parametrize("change", ["cycle", "missing", "second-human", "source", "unused"])
async def test_graph_rejects_mixed_or_missing_witness(change):
    witness, loop, runtime, service, event = await scenario()
    proof = deepcopy(witness.completed(event, runtime, loop))
    if change == "cycle":
        proof["inputs"][0]["parentInputIds"] = [service.id]
    elif change == "missing":
        proof["inputs"].pop()
    elif change == "second-human":
        proof["inputs"].append({"inputId": "human2", "kind": "user"})
        proof["inputs"][0]["parentInputIds"].append("human2")
    elif change == "source":
        proof["inputs"][0]["source"] = "random-service"
    else:
        proof["inputs"].append({"inputId": "unused", "kind": "user"})
    assert not validate(proof, event["input_ids"])