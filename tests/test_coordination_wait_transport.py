"""Exercise app tools through real worker futures and host wait handling."""
import asyncio
from copy import deepcopy
import json
import time
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_worker
from amplifier_web.runtime_protocol import encode_message
from amplifier_web.service import AppService
from test_app_control_validation import mounted_tool


class Transport:
    """JSON-lines loopback, retaining the production bridge and reply handler."""
    def __init__(self, app, worker, root):
        self.app, self.worker, self.root = app, worker, root
        self.requests, self.replies, self.tasks, self.exceptions = [], [], [], []
        self.delay = 0
        self.drop_response = False

    def write(self, line):
        request = json.loads(line)
        if request.get("type") == "runtime.bridge":
            return
        assert request["op"] == "bridge"
        self.requests.append(request)
        self.tasks.append(asyncio.create_task(self.respond(request)))

    async def respond(self, request):
        await asyncio.sleep(self.delay)
        try:
            result = await self.app.app_bridge(
                request["operation"], request["args"], self.root["id"])
            reply = {"op": "bridge.result", "id": request["id"], "result": result}
        except Exception as exc:
            reply = {"op": "bridge.result", "id": request["id"], "error": str(exc)}
        self.replies.append(reply)
        if not self.drop_response:
            await self.worker.command(json.loads(encode_message(reply)))

    async def bridge(self, operation, args):
        try:
            return await self.worker.app_access_bridge(
                self.worker.session.coordinator)(operation, args)
        except Exception as exc:
            self.exceptions.append({"type": type(exc).__name__, "message": str(exc)})
            raise


@pytest.fixture
async def transport(tmp_path, monkeypatch):
    app = AppService(tmp_path / "app", workspace=tmp_path)
    root = app._new_session({"title": "Synthetic wait target", "workspace": str(tmp_path)})
    root["runtimeSessionId"] = "native-root"
    app.state["sessions"].append(root)
    app.state["selectedSessionId"] = root["id"]
    worker = runtime_worker.Worker()
    capabilities = {}
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(
        session_id="native-root", get_capability=capabilities.get,
        register_capability=capabilities.__setitem__))
    wire = Transport(app, worker, root)
    monkeypatch.setattr(runtime_worker, "_PROTOCOL", wire)
    try:
        yield wire
    finally:
        for task in wire.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*wire.tasks, return_exceptions=True)
        await app.close()
        assert not worker.bridges


async def wait_request(wire, **values):
    initial = await wire.app.coordination.wait(
        {"targets": [{"sessionId": wire.root["id"]}]})
    target = initial["targets"][0]
    return {"operation": "dispatch", "args": {
        "action": "coordination.wait", "id": "stable-wait-command",
        "args": {"targets": [{**target["target"], "afterCursor": target["nextCursor"]}],
                 **values}}}


async def test_real_maximum_wait_survives_processing_overhead(transport):
    tool = await mounted_tool(transport.bridge)
    request = await wait_request(transport, waitMs=60000)
    original = deepcopy(request)
    transport.delay = .2  # Time in transit/preprocessing, before the host wait starts.
    start = time.monotonic()
    result = await tool.execute(request)
    elapsed = time.monotonic() - start
    await asyncio.gather(*transport.tasks)  # Retain the late baseline host receipt too.
    receipt = {
        "elapsed": elapsed, "success": result.success, "error": result.error,
        "output": result.output, "exceptions": transport.exceptions,
        "requestCount": len(transport.requests), "replies": transport.replies,
        "pendingFutures": len(transport.worker.bridges),
    }
    print("REAL_WAIT_RECEIPT=" + json.dumps(receipt, sort_keys=True))
    assert request == original
    assert len(transport.requests) == 1
    assert transport.requests[0]["args"]["id"] == "stable-wait-command"
    assert not transport.worker.bridges
    assert result.success, receipt
    assert result.output["accepted"]
    assert result.output["result"]["timedOut"]
    assert not result.output["result"]["changed"]
    assert 60 <= elapsed < 65


@pytest.mark.parametrize("values", [{}, {"waitMs": 0}, {"waitMs": 20}])
async def test_default_zero_and_short_waits(transport, values):
    tool = await mounted_tool(transport.bridge)
    result = await tool.execute(await wait_request(transport, **values))
    assert result.success
    assert result.output["result"]["timedOut"] == (values.get("waitMs", 0) > 0)
    assert not result.output["result"]["changed"]
    assert len(transport.requests) == 1 and not transport.worker.bridges


async def test_max_wait_returns_early_on_notification(transport):
    tool = await mounted_tool(transport.bridge)
    request = await wait_request(transport, waitMs=60000)
    task = asyncio.create_task(tool.execute(request))
    # Establish that the actual host wait is listening before notifying it.
    for _ in range(100):
        if transport.app.coordination.signal.event._waiters:
            break
        await asyncio.sleep(.001)
    assert transport.app.coordination.signal.event._waiters
    transport.root["messages"].append(
        {"id": "report-one", "role": "assistant", "text": "Synthetic result"})
    transport.app.coordination.notify()
    result = await asyncio.wait_for(task, 2)
    assert result.success and result.output["result"]["changed"]
    assert not result.output["result"]["timedOut"]
    assert result.output["result"]["targets"][0]["results"][0]["id"] == "report-one"
    assert len(transport.requests) == 1 and not transport.worker.bridges


@pytest.mark.parametrize("value", [-1, 60001, True, False, "60000", None, 60000.5, {}, []])
async def test_invalid_wait_values_still_fail_host_validation(transport, value):
    tool = await mounted_tool(transport.bridge)
    result = await tool.execute(await wait_request(transport, waitMs=value))
    assert not result.success and result.error["message"].strip()
    assert len(transport.requests) == 1 and not transport.worker.bridges


@pytest.mark.parametrize("operation,args,expected", [
    ("dispatch", {"action": "coordination.wait", "args": {"waitMs": 60000}}, 65),
    ("dispatch", {"action": "coordination.wait", "args": {"waitMs": 59000}}, 64),
    ("dispatch", {"action": "coordination.wait", "args": {"waitMs": 1}}, 60),
    ("dispatch", {"action": "coordination.wait", "args": {}}, 60),
    ("dispatch", {"action": "coordination.wait", "args": {"waitMs": True}}, 60),
    ("dispatch", {"action": "coordination.wait", "args": {"waitMs": "60000"}}, 60),
    ("dispatch", {"action": "coordination.wait", "args": {"waitMs": 60001}}, 60),
    ("dispatch", {"action": "coordination.wait", "args": {"waitMs": -1}}, 60),
    ("dispatch", {"action": "coordination.wait", "args": {"waitMs": 60000.0}}, 60),
    ("dispatch", {"action": "coordination.wait", "args": None}, 60),
    ("dispatch", {"action": "session.rename", "args": {"waitMs": 60000}}, 60),
    ("history", {"action": "coordination.wait", "args": {"waitMs": 60000}}, 60),
])
async def test_only_valid_long_polls_receive_bounded_margin(monkeypatch, operation, args, expected):
    # Inspect the supplied budget, but expire the actual future, not a stub result.
    real_wait_for = asyncio.wait_for
    budgets, published = [], []
    async def expire(future, timeout):
        budgets.append(timeout)
        return await real_wait_for(future, .001)
    monkeypatch.setattr(runtime_worker.asyncio, "wait_for", expire)
    monkeypatch.setattr(runtime_worker, "publish", published.append)
    worker = runtime_worker.Worker()
    original = deepcopy(args)
    with pytest.raises(TimeoutError, match="transport timeout"):
        await worker.bridge(operation, args)
    assert budgets == [expected]
    requests = [row for row in published if row.get("op") == "bridge"]
    assert len(requests) == 1 and requests[0]["args"] == original == args
    assert [row["phase"] for row in published if row.get("type") == "runtime.bridge"] == ["requested", "timeout"]
    assert not worker.bridges
    await worker.command({"op": "bridge.result", "id": requests[0]["id"],
                          "result": {"late": True}})
    assert len(published) == 3 and not worker.bridges


async def test_mutation_response_timeout_is_unknown_and_never_replayed(transport, monkeypatch):
    real_wait_for = asyncio.wait_for
    budgets = []
    async def expire(future, timeout):
        budgets.append(timeout)
        return await real_wait_for(future, .1)
    monkeypatch.setattr(runtime_worker.asyncio, "wait_for", expire)
    transport.drop_response = True
    tool = await mounted_tool(transport.bridge)
    request = {"operation": "dispatch", "args": {"action": "session.rename",
               "id": "stable-mutation", "args": {"id": transport.root["id"], "title": "Changed"}}}
    result = await tool.execute(request)
    await asyncio.gather(*transport.tasks)
    assert budgets == [60]
    assert transport.root["title"] == "Changed"  # Effect happened; response was lost.
    assert not result.success
    assert "transport timeout" in result.error["message"]
    assert "outcome is unknown" in result.error["message"]
    assert "do not automatically replay" in result.error["message"]
    assert result.error.get("effect") != "none"
    assert len(transport.requests) == 1
    assert transport.requests[0]["args"]["id"] == "stable-mutation"
    assert not transport.worker.bridges
    await transport.worker.command(transport.replies[0])
    assert len(transport.requests) == 1 and not transport.worker.bridges


async def test_cancellation_propagates_and_late_reply_is_ignored(transport):
    tool = await mounted_tool(transport.bridge)
    task = asyncio.create_task(tool.execute(await wait_request(transport, waitMs=60000)))
    for _ in range(100):
        if transport.worker.bridges:
            break
        await asyncio.sleep(.001)
    assert transport.worker.bridges
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not transport.worker.bridges
    await transport.worker.command({"op": "bridge.result", "id": transport.requests[0]["id"],
                                    "result": {"late": True}})
    assert len(transport.requests) == 1 and not transport.worker.bridges


async def test_publish_disconnection_is_actionable_and_cleans_future(monkeypatch):
    calls = []
    def disconnected(request):
        calls.append(request)
        raise BrokenPipeError()
    monkeypatch.setattr(runtime_worker, "publish", disconnected)
    worker = runtime_worker.Worker()
    tool = await mounted_tool(worker.bridge)
    result = await tool.execute({"operation": "dispatch", "args": {
        "action": "session.rename", "id": "stable-mutation", "args": {"title": "Private value"}}})
    assert not result.success
    assert "connection was lost" in result.error["message"]
    assert "outcome is unknown" in result.error["message"]
    assert "do not automatically replay" in result.error["message"]
    assert "Private value" not in str(result.error)
    assert len([row for row in calls if row.get("op") == "bridge"]) == 1
    assert not worker.bridges

@pytest.mark.parametrize("outcome", ["returned", "failed", "cancelled"])
async def test_diagnostic_failure_cannot_change_request_outcome(monkeypatch, outcome):
    worker = runtime_worker.Worker()
    requests, futures = [], []
    error = ValueError("original host failure")
    def publish(row):
        if row.get("type") == "runtime.bridge":
            raise OSError("diagnostics unavailable")
        requests.append(row)
        future = worker.bridges[row["id"]]
        futures.append(future)
        if outcome == "returned":
            future.set_result({"accepted": True})
        elif outcome == "failed":
            future.set_exception(error)
        else:
            future.cancel()
    monkeypatch.setattr(runtime_worker, "publish", publish)
    if outcome == "returned":
        assert await worker.bridge("dispatch", {}) == {"accepted": True}
    elif outcome == "failed":
        with pytest.raises(ValueError) as caught:
            await worker.bridge("dispatch", {})
        assert caught.value is error
    else:
        with pytest.raises(asyncio.CancelledError):
            await worker.bridge("dispatch", {})
    assert len(requests) == 1 and all(f.done() for f in futures)
    assert not worker.bridges
