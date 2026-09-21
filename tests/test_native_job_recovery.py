"""Native wire calls cross the host boundary into the actual loop job ledger."""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("amplifier_module_provider_openai.native")
pytest.importorskip("amplifier_module_loop_live")

from amplifier_core import AmplifierSession, HookResult, ToolResult
from amplifier_core.message_models import ChatRequest, Message, ToolSpec
from amplifier_core.llm_errors import LLMError
from amplifier_module_loop_live.job_store import JobStore
from amplifier_module_loop_live.runtime import Runtime
from amplifier_module_loop_live.scope import LIVE_OWNER, NATIVE_REQUEST as LOOP_REQUEST
from amplifier_module_provider_openai import OpenAIProvider
from amplifier_module_provider_openai.native import NativeResponsesProvider, NATIVE_REQUEST as PROVIDER_REQUEST
from websockets.protocol import State

from amplifier_web.host.session import SelectedProvider
from amplifier_web.native_provider import install_native


@pytest.fixture
async def native_host(tmp_path, monkeypatch):
    async def deny_network(*args, **kwargs):
        raise AssertionError("Native recovery fixtures must not use a network transport")

    monkeypatch.setattr("httpx.AsyncClient.send", deny_network)
    monkeypatch.setattr("websockets.connect", deny_network)
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "web"))
    session = AmplifierSession({
        "session": {
            "orchestrator": {"module": "loop-live", "config": {
                "configured_bundle": True, "native_provider": True,
                "background_delegate": True, "min_delay_between_calls_ms": 0,
            }},
            "context": {"module": "context-simple"},
        }, "providers": [],
    })
    await session.initialize()
    coordinator = session.coordinator
    loop = coordinator.get("orchestrator")
    provider = OpenAIProvider(api_key="fixture", config={"default_model": "gpt-6-astra"})
    await coordinator.mount("providers", provider, name="configured")
    selected = SelectedProvider(provider, {
        "instance": "configured", "model": "gpt-6-astra", "effort": "high",
    })
    loop.root_provider = selected
    loop.coordinator = coordinator
    loop.context = coordinator.get("context")
    loop.tools = coordinator.get("tools")
    loop.hooks = coordinator.hooks
    loop.runtime = Runtime(session_id=coordinator.session_id)
    coordinator.register_capability("live.runtime", loop.runtime)
    ledger = JobStore(tmp_path / "jobs")
    coordinator.register_capability("live.jobs", ledger)
    await install_native(loop, coordinator, coordinator.get("providers"))
    native = coordinator.get_capability("web.native_provider").provider
    # The real serializer and native frame processor run; no SDK/network call can.
    client = SimpleNamespace()
    client.with_options = lambda **kwargs: client
    native._client = client
    native._provider_count_available = lambda: False
    native._guard_assembled_params_with_provider_count = AsyncMock(return_value=None)
    native._connect = AsyncMock()
    try:
        yield SimpleNamespace(loop=loop, coordinator=coordinator, native=native,
                              selected=selected, ledger=ledger)
    finally:
        for job in loop.jobs.values():
            loop.cancel_job(job)
        await asyncio.gather(*(job["task"] for job in loop.jobs.values()), return_exceptions=True)
        ledger.close()
        # The fixture has no real SDK client to close.
        native._client = None
        await session.cleanup()


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
async def test_native_frames_persist_exact_wire_call_through_real_loop_and_restart(native_host, outcome):
    fixture = native_host
    native, loop, ledger = fixture.native, fixture.loop, fixture.ledger
    executions, approvals = [], []

    class Delegate:
        name = "delegate"
        description = "Synthetic bounded work"
        input_schema = {"type": "object", "properties": {"instruction": {"type": "string"}}}

        async def execute(self, arguments):
            executions.append(copy.deepcopy(arguments))
            return ToolResult(success=True, output={"report": "original result"})

    async def before_tool(event, data):
        approvals.append(data["tool_call_id"])
        return HookResult()

    await fixture.coordinator.mount("tools", Delegate(), name="delegate")
    loop.tools = fixture.coordinator.get("tools")
    fixture.coordinator.hooks.register("tool:pre", before_tool, name="native-job-test")
    wire = {"type": "function_call", "id": "wire-item-1", "call_id": "native-call-1",
            "name": "delegate", "arguments": '{ "instruction" : "original work" }',
            "async": True, "status": "completed"}
    frames = [
        {"type": "response.created", "response": {"id": "response-1"}},
        {"type": "response.output_item.done", "item": wire},
    ]
    if outcome == "completed":
        frames.append({"type": "response.completed", "response": {
            "id": "response-1", "status": "completed", "model": "gpt-6-astra",
            "output": [wire], "usage": {"input_tokens": 1, "output_tokens": 1},
        }})
    elif outcome == "failed":
        frames.append({"type": "response.failed", "response": {"error": {"code": "fixture_failure"}}})

    async def receive():
        if frames:
            return json.dumps(frames.pop(0))
        raise asyncio.CancelledError

    socket = SimpleNamespace(state=State.OPEN, send=AsyncMock(), close=AsyncMock(), recv=receive)
    native.socket = socket
    request = ChatRequest(messages=[Message(role="user", content="synthetic work")], tools=[
        ToolSpec(name="delegate", parameters=Delegate.input_schema, description=Delegate.description),
    ], max_output_tokens=32)
    outer_loop, outer_provider = SimpleNamespace(async_calls={}), object()
    loop_token = LOOP_REQUEST.set(outer_loop)
    provider_token = PROVIDER_REQUEST.set(outer_provider)
    owner_token = LIVE_OWNER.set(loop)
    try:
        if outcome == "completed":
            await fixture.selected.complete(request)
        else:
            with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else LLMError):
                await fixture.selected.complete(request)
        assert LOOP_REQUEST.get() is outer_loop
        assert PROVIDER_REQUEST.get() is outer_provider
        assert native.owner is None
    finally:
        LIVE_OWNER.reset(owner_token)
        PROVIDER_REQUEST.reset(provider_token)
        LOOP_REQUEST.reset(loop_token)

    await asyncio.wait_for(asyncio.gather(*(job["task"] for job in loop.jobs.values())), 3)
    assert approvals == [wire["call_id"]] and executions == [{"instruction": "original work"}]
    assert socket.send.await_count == 1  # No retry after failure/cancellation.
    assert native.request_uncertain is (outcome != "completed")
    assert ledger.rows[wire["call_id"]]["native_call"] == wire
    ledger.close()
    reopened = JobStore(ledger.directory)
    try:
        recovered, reports = reopened.recover([])
        assistant = next(message for message in recovered if message["role"] == "assistant")
        assert assistant["metadata"]["converge_native_async_calls"] == [wire]
        assert reports[0]["status"] == "returned"
        assert reopened.recover(recovered) == (recovered, [])
        assert len(executions) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("fallback", ["utility", "no_thinking", "model", "foreign_owner", "selection"])
@pytest.mark.parametrize("outcome", ["returned", "failed", "cancelled"])
async def test_regular_call_clears_inherited_native_scope_and_restores_it(native_host, monkeypatch, fallback, outcome):
    fixture = native_host
    native, loop = fixture.native, fixture.loop
    observed = []

    async def ordinary(self, request, **kwargs):
        observed.append((LOOP_REQUEST.get(), PROVIDER_REQUEST.get()))
        if outcome == "failed":
            raise ValueError("ordinary failure")
        if outcome == "cancelled":
            raise asyncio.CancelledError
        return "ordinary"

    monkeypatch.setattr(OpenAIProvider, "complete", ordinary)
    request = ChatRequest(messages=[Message(role="user", content="utility")])
    options = {}
    owner = loop
    if fallback == "utility":
        request.metadata = {"stream": False}
    elif fallback == "no_thinking":
        options["extended_thinking"] = False
    elif fallback == "model":
        fixture.selected.selection["model"] = "gpt-5.6-terra"
    elif fallback == "foreign_owner":
        owner = SimpleNamespace(coordinator=object())
    else:
        fixture.selected.selection["effort"] = "low"
    owner_token = LIVE_OWNER.set(owner)
    loop_token = LOOP_REQUEST.set(native)
    provider_token = PROVIDER_REQUEST.set(native)
    try:
        if outcome == "returned":
            assert await fixture.selected.complete(request, **options) == "ordinary"
        else:
            with pytest.raises(ValueError if outcome == "failed" else asyncio.CancelledError):
                await fixture.selected.complete(request, **options)
        assert observed == [(None, None)]
        assert LOOP_REQUEST.get() is native and PROVIDER_REQUEST.get() is native
        assert not native.request_uncertain and not fixture.ledger.rows
        assert not list(fixture.ledger.directory.glob("job-*.json"))
    finally:
        PROVIDER_REQUEST.reset(provider_token)
        LOOP_REQUEST.reset(loop_token)
        LIVE_OWNER.reset(owner_token)


async def test_private_native_method_without_provider_scope_cannot_claim_job_identity(native_host, monkeypatch):
    native = native_host.native
    observed = []

    async def response(self, params):
        observed.append(LOOP_REQUEST.get())
        return "private fixture"

    monkeypatch.setattr(NativeResponsesProvider, "_native_response", response)
    loop_token = LOOP_REQUEST.set(native)
    provider_token = PROVIDER_REQUEST.set(None)
    try:
        assert await native._native_response({}) == "private fixture"
        assert observed == [None]
        assert LOOP_REQUEST.get() is native
    finally:
        PROVIDER_REQUEST.reset(provider_token)
        LOOP_REQUEST.reset(loop_token)
