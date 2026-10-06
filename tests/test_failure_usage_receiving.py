"""Failure receipts stay on their exact open call, with unknowns still absent."""
import asyncio
import contextvars
from types import SimpleNamespace

import pytest

from amplifier_web.execution_events import CALL_PURPOSE, CURRENT_CALL, ExecutionEvents, public_usage
from amplifier_web.execution import ingest
from amplifier_web.browser_detail import compact, project


REQUEST = SimpleNamespace(model="fixture", metadata={})
PRIVATE = "private-nested-receipt-sentinel"


def summary(inputs=125, outputs=None, *, complete=False):
    return dict(input_tokens=inputs, output_tokens=outputs,
                total_tokens=inputs + outputs if inputs is not None and outputs is not None else None,
                cache_read_tokens=None, cache_write_tokens=None, reasoning_tokens=None,
                cost_usd="0.01" if complete else None, cost_known_subtotal_usd="0.01",
                cost_scope="reported_attempts" if complete else "known_attempts",
                cost_complete=complete, cost_is_estimate=True,
                attempts=[{"input_tokens": 10, "output_tokens": 5, "secret": PRIVATE}])


def error(usage=None, cls=RuntimeError):
    exc = cls("primary-stop")
    if usage is not None:
        exc.usage = usage
    return exc


class Provider:
    def get_info(self):
        return SimpleNamespace(id="fixture", defaults={})


def last(events):
    return [row for row in events.nodes.values() if row["kind"] == "llm"][-1]


async def fail(events, exc, hook=None, sid="root"):
    async def invoke():
        if hook is not None:
            events.hook(sid, "llm:response", {"usage": hook, "status": "error"})
            assert last(events)["phase"] == "running"
            assert last(events).get("endedAt") is None
        raise exc
    with pytest.raises(type(exc)) as caught:
        await events.provider_call(sid, Provider(), REQUEST, invoke)
    assert caught.value is exc
    return last(events)


async def test_ordinary_exception_partial_unknown_and_public_projection():
    events = ExecutionEvents("root", lambda value: None)
    row = await fail(events, error({"input_tokens": 125, "output_tokens": None,
                                   "vendor": {"messages": PRIVATE}}))
    assert row["phase"] == "error"
    assert row["usage"] == {"inputTokens": 125, "grossInputTokens": 125, "costType": "unavailable"}
    assert PRIVATE not in str(events.usage())
    session = {"id": "root"}
    ingest(session, row)
    assert compact(row, "root", "nodes", 512)["usage"] == row["usage"]
    view = project(session)
    assert view["execution"]["nodes"][0]["usage"] == row["usage"]
    assert "outputTokens" not in view["execution"]["nodes"][0]["usage"]


async def test_normal_hook_precedes_error_and_later_cumulative_replaces_not_adds():
    events = ExecutionEvents("root", lambda value: None)
    row = await fail(events, error({"input_tokens": 125, "output_tokens": 7}),
                     {"input_tokens": 10, "output_tokens": 5})
    assert row["usage"]["inputTokens"] == 125
    assert row["usage"]["totalTokens"] == 132
    assert events.usage()["calls"] == 1


@pytest.mark.parametrize("receipt", [None, {}, {"input_tokens": -1}, {"input_tokens": True},
                                    {"input_tokens": float("nan")}, {"input_tokens": 1}])
async def test_missing_malformed_or_unscoped_partial_preserves_better_hook(receipt):
    events = ExecutionEvents("root", lambda value: None)
    row = await fail(events, error(receipt), {"input_tokens": 125, "output_tokens": 5})
    assert row["usage"]["totalTokens"] == 130


@pytest.mark.parametrize("reverse", [False, True])
async def test_scoped_unknown_invalidates_earlier_totals_and_resists_unscoped_in_both_orders(reverse):
    events = ExecutionEvents("root", lambda value: None)
    async def invoke():
        first, second = (summary(None), {"input_tokens": 10, "output_tokens": 5}) if reverse else (
            {"input_tokens": 10, "output_tokens": 5, "cost_usd": ".01"}, summary(None))
        for receipt in (first, second):
            events.hook("root", "llm:response", {"status": "cancelled", "usage": receipt})
        raise error()
    with pytest.raises(RuntimeError):
        await events.provider_call("root", Provider(), REQUEST, invoke)
    assert last(events)["usage"] == {"costType": "unavailable"}
    assert PRIVATE not in str(events.nodes)


async def test_stream_iterator_exception_receipt_and_stale_hook_noop():
    events = ExecutionEvents("root", lambda value: None)
    exc = error(summary())
    contexts = []
    class StreamProvider(Provider):
        async def complete(self, request):
            return None
        async def stream(self, request):
            contexts.append(contextvars.copy_context())
            yield {"usage": {"input_tokens": 10, "output_tokens": 5}}
            raise exc
    provider = events.instrument_provider("root", StreamProvider())
    with pytest.raises(RuntimeError) as caught:
        async for _ in provider.stream(REQUEST):
            pass
    assert caught.value is exc
    row = dict(last(events))
    assert row["phase"] == "error" and row["usage"]["inputTokens"] == 125
    assert "outputTokens" not in row["usage"]
    contexts[0].run(events.hook, "root", "llm:response", {"usage": {"input_tokens": 999}})
    assert last(events) == row
    assert not events.streaming_calls and not events.cumulative_usage


async def test_concurrent_parent_two_children_and_naming_have_exact_identity():
    events = ExecutionEvents("root", lambda value: None)
    for sid in ("child-a", "child-b"):
        events.lifecycle({"type": "child.updated", "sessionId": sid, "parentSessionId": "root",
                          "callId": sid, "status": "running"})
    gate = asyncio.Event()
    started = []
    stale = []
    async def one(sid, count, naming=False):
        token = CALL_PURPOSE.set({"label": "Naming", "purpose": "naming"}) if naming else None
        async def invoke():
            started.append(CURRENT_CALL.get())
            stale.append((contextvars.copy_context(), sid))
            if len(started) == 4:
                gate.set()
            await gate.wait()
            # Wrong sid and payload identity cannot move the mutation target.
            events.hook("foreign", "llm:response", {"usage": {"input_tokens": 999}})
            events.hook(sid, "llm:response", {"session_id": "foreign", "usage": {"input_tokens": 999}})
            events.hook(sid, "llm:response", {"request_id": "other", "usage": {"input_tokens": count}})
            raise error({"input_tokens": count})
        try:
            with pytest.raises(RuntimeError):
                await events.provider_call(sid, Provider(), REQUEST, invoke)
        finally:
            if token is not None:
                CALL_PURPOSE.reset(token)
    await asyncio.gather(one("root", 1), one("child-a", 2), one("child-b", 3), one("root", 4, True))
    assert len(set(started)) == 4
    rows = {row["usage"]["inputTokens"]: dict(row) for row in events.nodes.values() if row["kind"] == "llm"}
    assert rows[2]["parentId"] == "worker:child-a"
    assert rows[3]["parentId"] == "worker:child-b"
    assert rows[4]["label"] == "Naming" and rows[4]["parentId"] is None
    for context, sid in stale:
        context.run(events.hook, sid, "llm:response", {"usage": {"input_tokens": 999}})
    assert rows == {row["usage"]["inputTokens"]: row for row in events.nodes.values() if row["kind"] == "llm"}
    assert CURRENT_CALL.get() is None


@pytest.mark.parametrize("where", ["getter", "dump"])
@pytest.mark.parametrize("secondary", [RuntimeError, asyncio.CancelledError])
async def test_secondary_metadata_failure_does_not_mask_primary_cancel(where, secondary):
    events = ExecutionEvents("root", lambda value: None)
    class BadUsage:
        def model_dump(self):
            raise secondary("metadata")
    class Primary(asyncio.CancelledError):
        @property
        def usage(self):
            if where == "getter":
                raise secondary("metadata")
            return BadUsage()
    primary = Primary("primary-stop")
    task = asyncio.create_task(fail(events, primary, {"input_tokens": 125}))
    row = await task
    assert task.cancelling() == 0
    assert row["phase"] == "cancelled" and row["usage"]["inputTokens"] == 125


async def test_genuine_new_cancellation_in_getter_is_observable_without_uncancel():
    events = ExecutionEvents("root", lambda value: None)
    class Primary(RuntimeError):
        @property
        def usage(self):
            asyncio.current_task().cancel("new-stop")
            raise asyncio.CancelledError("new-stop")
    async def invoke():
        raise Primary("old-error")
    task = asyncio.create_task(events.provider_call("root", Provider(), REQUEST, invoke))
    with pytest.raises(asyncio.CancelledError, match="new-stop"):
        await task
    assert task.cancelling() == 1


async def test_real_caller_cancel_preserves_object_message_count_and_measured_zero():
    events = ExecutionEvents("root", lambda value: None)
    entered = asyncio.Event()
    caught = []
    async def invoke():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            exc.usage = {"input_tokens": 0}
            caught.append(exc)
            raise
    task = asyncio.create_task(events.provider_call("root", Provider(), REQUEST, invoke))
    await entered.wait()
    task.cancel("caller-stop")
    with pytest.raises(asyncio.CancelledError) as result:
        await task
    assert result.value is caught[0] and str(result.value) == "caller-stop"
    assert task.cancelling() == 1 and last(events)["usage"]["inputTokens"] == 0
    assert "outputTokens" not in last(events)["usage"]


async def test_model_dump_excludes_unset_core_defaults():
    from amplifier_core.message_models import Usage
    row = await fail(ExecutionEvents("root", lambda value: None), error(Usage(input_tokens=125, output_tokens=0, total_tokens=125)))
    assert row["usage"]["outputTokens"] == 0
    assert "cacheReadTokens" not in row["usage"]


async def test_failure_cache_aliases_and_complete_estimate_do_not_change_success():
    events = ExecutionEvents("root", lambda value: None)
    receipt = {"input_tokens": 125, "output_tokens": 7, "cache_read_input_tokens": 10,
               "cache_creation_input_tokens": 3, "usage_complete": True,
               "cost_callback_state": "returned", "cost_usd": ".003",
               "service_tier": "standard", "speed": "standard"}
    row = await fail(events, error(receipt))
    assert row["usage"]["cacheReadTokens"] == 10 and row["usage"]["cacheWriteTokens"] == 3
    assert row["usage"]["grossInputTokens"] == 128
    assert row["usage"]["costType"] == "estimated"
    successful = public_usage(receipt)
    assert "cacheReadTokens" not in successful and successful["costType"] == "reported"
    assert public_usage({"input_tokens": 125, "cache_read_tokens": 10, "cache_write_tokens": 3})["grossInputTokens"] == 128


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
async def test_partial_cost_never_promotes_estimate_or_subtotal(kind):
    receipt = summary() if kind == "openai" else dict(
        input_tokens=125, output_tokens=None, usage_complete=False,
        cost_callback_state="unknown", cost_usd=".01")
    receipt["cost_usd"] = ".01"
    row = await fail(ExecutionEvents("root", lambda value: None), error(receipt))
    assert "costUsd" not in row["usage"] and row["usage"]["costType"] == "unavailable"


async def test_complete_scoped_estimate_and_generic_cost_rules():
    row = await fail(ExecutionEvents("root", lambda value: None), error(summary(125, 7, complete=True)))
    assert row["usage"]["costType"] == "estimated" and row["usage"]["costUsd"] == .01
    row = await fail(ExecutionEvents("root", lambda value: None), error({"cost_usd": ".01"}))
    assert row["usage"]["costType"] == "reported"