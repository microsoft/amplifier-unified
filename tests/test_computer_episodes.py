"""Offline host-lifecycle checks; no desktop backend or provider is executed."""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.computer_episodes import ComputerEpisodes
from amplifier_web.runtime_worker import Worker


class Controller:
    def __init__(self):
        self.calls = []
        self.current = None
        self.bound = None
        self.ready = asyncio.Event()
        self.ready.set()
        self.drained = asyncio.Event()
        self.drained.set()
        self.release = True
        self.interrupted = False

    def state(self, **extra):
        return {"identity": {"owner": "mount", "target": "target", "generation": self.current},
                "state": "viewing", "workers": 0, "admission_open": True,
                "release_confirmed": False, **extra}

    async def begin(self):
        await self.ready.wait()
        self.current = f"episode-{sum(c[0] == 'begin' for c in self.calls)}"
        self.interrupted = False
        self.calls.append(("begin", self.current))
        return self.state()

    @contextmanager
    def bind(self, generation):
        assert generation == self.current
        self.bound = generation
        try:
            yield
        finally:
            self.bound = None

    def fence(self, generation, *, interrupted=True):
        self.calls.append(("fence", generation, interrupted))
        assert generation == self.current
        self.interrupted |= interrupted
        return self.state(state="stopping", admission_open=False)

    async def end(self, generation, *, interrupted=False):
        self.calls.append(("end", generation, interrupted))
        await self.drained.wait()
        assert generation == self.current
        return self.state(state="interrupted" if interrupted or self.interrupted else "done",
                          admission_open=False, release_confirmed=self.release)

    async def heartbeat(self, generation):
        self.calls.append(("heartbeat", generation))

    def cached_status(self):
        return self.state()


class Coordinator:
    def __init__(self, controller=None):
        self.session_id = "root"
        self.session_state = {}
        self.capabilities = {"computer_use.episodes": controller} if controller else {}
        self.tools = {}
        self.cancellation = SimpleNamespace(is_cancelled=False)

    def get_capability(self, name):
        return self.capabilities.get(name)

    def register_capability(self, name, value):
        self.capabilities[name] = value

    def get(self, name):
        return self.tools if name == "tools" else None

    async def mount(self, point, tool, name=None):
        self.tools[name or tool.name] = tool


def fixture():
    controller = Controller()
    coordinator = Coordinator(controller)
    events = []
    registry = ComputerEpisodes(events.append)
    current = {"key": "native-generation", "valid": True}
    sponsor = registry.install(coordinator, lambda: (
        current["key"], lambda: current["valid"]))
    return controller, coordinator, registry, sponsor, current, events


async def tick():
    for _ in range(8):
        await asyncio.sleep(0)


@pytest.mark.parametrize("uncertain", [
    "workers", "missing_indicator", "false_indicator", "unconfirmed_release",
    "open_admission", "unknown_before", "unknown_after", "cancelled_begin", "replacement",
])
async def test_no_acquisition_requires_complete_prior_proof_same_controller_and_definitive_after(uncertain):
    controller, coordinator, registry, sponsor, _, _ = fixture()
    before = {"state": "released", "identity": {"owner": "old", "target": "target", "generation": "old"},
              "workers": 0, "admission_open": False, "release_confirmed": True, "indicator_settled": True}
    after = {"state": "idle", "identity": None, "workers": 0, "release_confirmed": False}
    if uncertain == "workers":
        before["workers"] = 1
    elif uncertain == "missing_indicator":
        before.pop("indicator_settled")
    elif uncertain == "false_indicator":
        before["indicator_settled"] = False
    elif uncertain == "unconfirmed_release":
        before["release_confirmed"] = False
    elif uncertain == "open_admission":
        before["admission_open"] = True
    elif uncertain == "unknown_before":
        before["state"] = "unknown"
    elif uncertain == "unknown_after":
        after["state"] = "unknown"
    cache = {"value": before}
    controller.cached_status = lambda: cache["value"]
    async def refuse():
        cache["value"] = after
        if uncertain == "replacement":
            coordinator.register_capability("computer_use.episodes", Controller())
        if uncertain == "cancelled_begin":
            raise asyncio.CancelledError()
        raise ValueError("inert begin uncertainty")
    controller.begin = refuse
    with pytest.raises((ValueError, asyncio.CancelledError)):
        await sponsor.invoke("computer", {}, AsyncMock())
    await asyncio.gather(*tuple(registry.pending), return_exceptions=True)
    assert registry.unresolved and sponsor.unconfirmed
    assert all(not episode.not_acquired for episode in registry.unresolved.values())
    assert not any(call[0] == "end" for call in controller.calls)
    assert not (await registry.close())["settled"]  # No false release proof.


async def test_cleanup_receipt_excludes_normal_readiness_but_not_closing_or_unknown_work():
    controller, _, registry, sponsor, _, _ = fixture()
    controller.ready.clear()
    task = asyncio.create_task(sponsor.invoke("computer", {}, AsyncMock()))
    await tick()
    try:
        assert not registry.receipt()["settled"]
        assert registry.receipt(settlement_only=True)["settled"]
        registry.fence()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not registry.receipt(settlement_only=True)["settled"]
        assert registry.pending and registry.unresolved
    finally:
        controller.ready.set()
        await asyncio.gather(task, return_exceptions=True)
        await registry.join()
        await registry.close()
    assert registry.receipt(settlement_only=True)["settled"]


@pytest.mark.parametrize("cancel_cleanup", [False, True])
async def test_cleanup_receipt_keeps_failed_or_cancelled_obligation_after_task_done(cancel_cleanup):
    registry = ComputerEpisodes()
    async def uncertain_cleanup():
        if cancel_cleanup:
            raise asyncio.CancelledError()
        raise OSError("inert uncertain cleanup")
    task = registry.retain_cleanup(object(), "run", uncertain_cleanup)
    await asyncio.gather(task, return_exceptions=True)
    await tick()
    assert not registry.pending and registry.cleanups
    receipt = registry.receipt(settlement_only=True)
    assert not receipt["settled"] and receipt["scope"] == "cleanup"
    assert receipt["episodes"][0]["reason"] == "child_cleanup_unconfirmed"


@pytest.mark.parametrize("outcome", ["completed", "blocked", "budget", "unknown", "denied"])
async def test_whole_bounded_task_and_nested_operations_keep_original_result(outcome):
    controller, _, registry, sponsor, _, _ = fixture()
    result = {"status": outcome}
    async def nested(arguments):
        assert controller.bound == controller.current
        assert not any(c[0] == "end" for c in controller.calls)
    async def whole(arguments):
        await sponsor.invoke("computer", {"action": "observe"}, nested)
        await asyncio.sleep(0)  # Selector reasoning is inside the outer episode.
        await sponsor.invoke("computer", {"action": "act"}, nested)
        await asyncio.sleep(0)
        await sponsor.invoke("computer", {"action": "observe"}, nested)  # Verification.
        return result
    assert await sponsor.invoke("ui_use", {"surface": "computer"}, whole) is result
    assert sum(c[0] == "begin" for c in controller.calls) == 1
    assert sum(c[0] == "end" for c in controller.calls) == 1
    assert sponsor.last_status["release_confirmed"]
    await registry.close()


async def test_direct_calls_retain_episode_through_reasoning_until_exact_terminal():
    controller, _, registry, sponsor, current, _ = fixture()
    execute = AsyncMock(return_value="unchanged")
    await sponsor.invoke("computer", {"owner": "forged", "generation": "forged"}, execute)
    await sponsor.invoke("desktop", {"action": "list_windows"}, execute)
    assert not any(c[0] == "end" for c in controller.calls)
    sponsor.observe({"type": "generation.finished", "generation_id": "stale"})
    await tick()
    assert not any(c[0] == "end" for c in controller.calls)
    sponsor.observe({"type": "generation.finished", "generation_id": current["key"]})
    await tick()
    assert controller.calls[-1] == ("end", "episode-0", False)
    with pytest.raises(asyncio.CancelledError):
        await sponsor.invoke("computer", {}, execute)
    await registry.close()


async def test_stop_overtakes_readiness_and_never_executes():
    controller, _, registry, sponsor, _, _ = fixture()
    controller.ready.clear()
    execute = AsyncMock()
    task = asyncio.create_task(sponsor.invoke("ui_use", {"surface": "computer"}, execute))
    await tick()
    registry.fence()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    controller.ready.set()
    await registry.close()
    execute.assert_not_awaited()
    assert ("end", "episode-0", True) in controller.calls


async def test_cancellation_does_not_release_while_native_workers_outlive_awaiter():
    controller, _, registry, sponsor, _, events = fixture()
    controller.drained.clear()
    entered = asyncio.Event()
    async def operation(arguments):
        entered.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(sponsor.invoke("ui_use", {"surface": "computer"}, operation))
    await entered.wait()
    task.cancel()
    await tick()
    assert sponsor.last_status["state"] == "stopping"
    assert not sponsor.last_status["release_confirmed"]
    task.cancel()  # A second cancellation must not cancel native drain.
    with pytest.raises(asyncio.CancelledError):
        await task
    assert registry.pending
    assert not any(e.get("release_confirmed") for e in events)
    controller.drained.set()
    await registry.close()
    assert sponsor.last_status["release_confirmed"]


async def test_error_and_unconfirmed_release_never_replace_original_exception():
    controller, _, registry, sponsor, _, _ = fixture()
    controller.release = False
    async def fail(arguments):
        raise LookupError("original")
    with pytest.raises(LookupError, match="original"):
        await sponsor.invoke("ui_use", {"surface": "computer"}, fail)
    assert not sponsor.last_status["release_confirmed"]
    await registry.close()


async def test_wrapping_late_mount_is_idempotent_and_forwards_policy_setters():
    controller, coordinator, registry, sponsor, _, _ = fixture()
    native = SimpleNamespace(name="computer", execute=AsyncMock())
    await coordinator.mount("tools", native, name="computer")
    wrapped = coordinator.tools["computer"]
    sponsor.wrap_tools()
    assert coordinator.tools["computer"] is wrapped
    callback = object()
    wrapped.authorize = callback
    assert native.authorize is callback
    await wrapped.execute({})
    assert controller.calls[0][0] == "begin"
    await registry.close()


async def test_browser_and_explicit_old_bundle_remain_untouched():
    controller, _, registry, sponsor, _, _ = fixture()
    execute = AsyncMock(return_value="browser")
    assert await sponsor.invoke("ui_use", {"surface": "browser"}, execute) == "browser"
    assert not controller.calls
    coordinator = Coordinator()
    old = registry.install(coordinator, lambda: None)
    assert await old.invoke("computer", {}, execute) == "browser"
    await registry.close()


async def test_worker_stop_fences_before_any_serialized_admission(monkeypatch):
    worker = Worker()
    controller, coordinator, registry, sponsor, _, _ = fixture()
    worker.computer_episodes = registry
    await sponsor.invoke("computer", {}, AsyncMock())
    entered = asyncio.Event()
    async def serial(data):
        entered.set()
        assert worker.stop_revision == 1
        assert sponsor.last_status["admission_open"] is False
    monkeypatch.setattr(worker, "_command_serial", serial)
    await worker.command({"op": "stop"})
    assert entered.is_set()
    await registry.close()


async def test_ui_stop_halts_reasoning_gap_and_cannot_resume_old_token():
    controller, _, registry, sponsor, _, _ = fixture()
    await sponsor.invoke("computer", {}, AsyncMock())
    worker = Worker()
    worker.computer_episodes = registry
    await worker.command({"op": "computer.stop"})
    assert sponsor.last_status["admission_open"] is False
    with pytest.raises(asyncio.CancelledError):
        await sponsor.invoke("computer", {}, AsyncMock())
    await registry.close()


@pytest.mark.parametrize("windows", [False, True])
def test_host_ui_stop_enqueues_without_starting_or_acquiring_worker(windows):
    import json
    from amplifier_web.runtime import RuntimeManager
    writes = []
    pipes = SimpleNamespace(stdin=SimpleNamespace(write=writes.append))
    row = {"process": SimpleNamespace(returncode=None, stdin=pipes.stdin)}
    if windows:
        row["windows_owner"] = SimpleNamespace(job=pipes)
        row["process"].stdin = None
    manager = object.__new__(RuntimeManager)
    manager.workers = {"own": row}
    manager.stop_computer("absent")
    manager.stop_computer("own")
    assert [json.loads(value) for value in writes] == [{"op": "computer.stop"}]


async def test_retarget_retires_lazy_identity_before_new_target_operation():
    controller, _, registry, sponsor, _, _ = fixture()
    await sponsor.invoke("computer", {}, AsyncMock())
    async def retarget(arguments):
        assert controller.bound is None
        assert controller.calls[-1][0] == "end"
    await sponsor.invoke("desktop", {"action": "retarget"}, retarget)
    await sponsor.invoke("computer", {}, AsyncMock())
    assert sum(c[0] == "begin" for c in controller.calls) == 2
    await registry.close()


async def test_late_old_invocation_cannot_use_replacement_controller_or_generation():
    controller, coordinator, registry, sponsor, current, _ = fixture()
    await sponsor.invoke("computer", {}, AsyncMock())
    old = next(iter(sponsor.episodes))
    sponsor.finish(current["key"])
    await tick()
    replacement = Controller()
    coordinator.register_capability("computer_use.episodes", replacement)
    current["key"] = "replacement"
    await sponsor.invoke("computer", {}, AsyncMock())
    sponsor.settle(old, interrupted=True)
    await tick()
    assert not any(c[0] == "end" for c in replacement.calls)
    await registry.close()


async def test_live_heartbeat_stops_after_terminal_and_new_sponsors_inherit_ui_halt():
    controller, _, registry, sponsor, _, _ = fixture()
    await sponsor.invoke("computer", {}, AsyncMock())
    await asyncio.sleep(1.05)
    assert any(c[0] == "heartbeat" for c in controller.calls)
    registry.fence(halt=True)
    await tick()
    count = sum(c[0] == "heartbeat" for c in controller.calls)
    newer = registry.install(Coordinator(Controller()), lambda: ("newer", lambda: True))
    with pytest.raises(asyncio.CancelledError):
        await newer.invoke("computer", {}, AsyncMock())
    await asyncio.sleep(1.05)
    assert count == sum(c[0] == "heartbeat" for c in controller.calls)
    await registry.close()


async def test_real_ui_use_tool_retains_episode_through_selector_and_final_verification():
    """Real selected runner/controller/verifier, with inert dispatch and model."""
    from amplifier_module_tool_ui_use import UIUseTool
    controller, coordinator, registry, sponsor, _, _ = fixture()
    selecting, selection, verifying, verification = (asyncio.Event() for _ in range(4))
    observed = 0
    closed = []
    async def native(arguments):
        nonlocal observed
        assert controller.bound == controller.current
        assert not any(c[0] == "end" for c in controller.calls)
        if arguments["action"] == "act":
            return {"status": "completed"}
        observed += 1
        if observed > 1:
            verifying.set()
            await verification.wait()
        return {"revision": str(observed), "state": {"text": "finished" if observed > 1 else "waiting"},
                "candidates": [{"id": "click", "operation": "CLICK", "name": "Next"}]}
    async def call(name, arguments):
        return {"status": "completed", "output": await sponsor.invoke(name, arguments, native)}
    lease = SimpleNamespace(tools=["computer"], call=call, close=lambda: closed.append(True))
    coordinator.register_capability("tools.dispatch", SimpleNamespace(bind=lambda: lease))
    coordinator.hooks = SimpleNamespace(emit=AsyncMock())
    tool = UIUseTool(coordinator, {})
    async def complete(prompt):
        selecting.set()
        await selection.wait()
        return '{"candidate":"click"}'
    tool._complete = complete
    task = asyncio.create_task(sponsor.invoke("ui_use", {"surface": "computer", "task": "Click next",
        "success": {"text": "finished"}}, tool.execute))
    await selecting.wait()
    assert not any(c[0] == "end" for c in controller.calls)
    selection.set()
    await verifying.wait()
    assert not task.done()
    assert not any(c[0] == "end" for c in controller.calls)
    verification.set()
    result = await task
    assert result.output["status"] == "completed" and closed == [True]
    assert sum(c[0] == "begin" for c in controller.calls) == 1
    assert controller.calls[-1] == ("end", "episode-0", False)
    await registry.close()


async def test_stop_overtakes_two_queued_calls_and_resume_never_reopens_old_admission():
    controller, _, registry, sponsor, _, _ = fixture()
    controller.ready.clear()
    execute = AsyncMock()
    first = asyncio.create_task(sponsor.invoke("computer", {}, execute))
    await tick()
    second = asyncio.create_task(sponsor.invoke("computer", {}, execute))
    await tick()
    old_capture = sponsor.capture_invocation(sponsor.capture())
    registry.fence(halt=True)
    registry.resume()
    assert old_capture[1]() is False
    controller.ready.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(r, asyncio.CancelledError) for r in results)
    execute.assert_not_awaited()
    assert sum(c[0] == "begin" for c in controller.calls) == 1
    await tick()
    await sponsor.invoke("computer", {}, execute)
    assert sum(c[0] == "begin" for c in controller.calls) == 2
    await registry.close()


async def test_idle_root_runtime_control_captures_before_hook_and_binds_actual_invocation():
    from amplifier_web.runtime_controls import RuntimeControls
    from amplifier_core import HookResult
    hooks = {}
    class Hooks:
        def register(self, event, function, **kwargs):
            hooks.setdefault(event, []).append(function)
        async def emit(self, name, data):
            for function in hooks.get(name, []):
                await function(name, data)
            return HookResult()
    controller = Controller()
    coordinator = Coordinator(controller)
    coordinator.hooks = Hooks()
    coordinator.process_hook_result = AsyncMock(side_effect=lambda result, *args: result)
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=coordinator)
    worker.runtime = SimpleNamespace(generation=None, closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.shared_handle = object()
    worker.activation = object()
    controls = object.__new__(RuntimeControls)
    controls.coordinator = coordinator
    controls.runtime = worker.runtime
    controls._tool_invocation = worker.tool_invocation
    worker.controls = controls
    registry = worker.computer_episodes
    sponsor = registry.install(coordinator, lambda: worker.computer_execution(coordinator))
    native = SimpleNamespace(name="computer", input_schema={}, execute=AsyncMock(return_value="exact"))
    await coordinator.mount("tools", native, name="computer")
    assert (await controls.invoke({"name": "computer", "arguments": {"owner": "forged"}}, checkpoint=False))["result"] == "exact"
    native.execute.assert_awaited_once_with({"owner": "forged"})
    assert controller.calls[-1] == ("end", "episode-0", False)
    assert sponsor.last_status["release_confirmed"]
    await registry.close()


async def test_child_stop_fences_only_named_child_before_command_lock():
    worker = Worker()
    root = Coordinator()
    _, child, registry, sponsor, _, _ = fixture()
    child.session_id = "child"
    worker.computer_episodes = registry
    worker.session = SimpleNamespace(coordinator=root)
    root.register_capability("live.children", SimpleNamespace(rows={"child": {"runId": "run"}},
        sessions={"child": SimpleNamespace(coordinator=child)}))
    await sponsor.invoke("computer", {}, AsyncMock())
    await worker.command_lock.acquire()
    task = asyncio.create_task(worker.command({"op": "worker.stop", "worker_id": "child"}))
    await tick()
    assert sponsor.last_status["admission_open"] is False
    task.cancel()
    worker.command_lock.release()
    await asyncio.gather(task, return_exceptions=True)
    await registry.close()


@pytest.mark.parametrize("persistent", [True, False])
async def test_rejected_finish_cannot_fence_continuing_child(persistent):
    from amplifier_web.host.children import Children
    controller, coordinator, registry, sponsor, _, _ = fixture()
    await sponsor.invoke("computer", {}, AsyncMock())
    children = object.__new__(Children)
    children.rows = {"child": {"status": "running", "persistent": persistent}}
    children.sessions = {"child": SimpleNamespace(coordinator=coordinator)}
    with pytest.raises(ValueError):
        await children.control("child", "finish")
    assert not any(c[0] == "fence" for c in controller.calls)
    await registry.close()


async def test_pending_controller_drain_retains_settlement_until_actual_workers_end():
    controller, _, registry, sponsor, _, _ = fixture()
    actual = asyncio.Event()
    original_end = controller.end
    async def end(generation, *, interrupted=False):
        if not actual.is_set():
            return controller.state(state="stopping", workers=1, admission_open=False)
        return await original_end(generation, interrupted=interrupted)
    async def status(generation):
        return controller.state(state="stopping", workers=0 if actual.is_set() else 1,
                                admission_open=False)
    controller.end, controller.status = end, status
    operation = asyncio.create_task(sponsor.invoke("ui_use", {"surface": "computer"}, AsyncMock()))
    await tick()
    assert not operation.done() and registry.pending
    assert not sponsor.last_status["release_confirmed"]
    actual.set()
    await operation
    assert sponsor.last_status["release_confirmed"]
    await registry.close()


async def test_real_foundation_finite_resumed_and_persistent_children_own_distinct_lifetimes(tmp_path):
    """Real session creation/execution/cleanup; inert computer and orchestrator."""
    from dataclasses import dataclass
    import importlib.util
    from pathlib import Path
    from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    episodes = ComputerEpisodes()
    runs = []
    class Loop:
        async def execute(self, prompt, context, providers, tools, hooks, coordinator):
            sponsor = coordinator.get_capability("web.computer_episodes")
            runtime = coordinator.get_capability("live.runtime")
            async def operate(text):
                await context.add_message({"role": "user", "content": text})
                await sponsor.invoke("computer", {}, AsyncMock())
                await context.add_message({"role": "assistant", "content": "fixture"})
            if runtime is None:
                await operate(prompt)
                return "finite"
            await runtime.emit("session.ready")
            generation = 0
            while True:
                _, command = await runtime.inbox.get()
                runtime.queued_inputs -= 1
                if command.kind == "stop":
                    await runtime.emit("session.closed", status="completed")
                    return "persistent"
                generation += 1
                await runtime.emit("generation.started", generation_id=f"child-turn-{generation}")
                await operate(command.text)
                await runtime.emit("generation.finished")
                await runtime.emit("session.idle", text="fixture")
    @dataclass
    class Prepared(PreparedBundle):
        async def create_session(self, **kwargs):
            session = await super().create_session(**kwargs)
            await session.coordinator.mount("orchestrator", Loop())
            return session
    plan = {"session": {"orchestrator": {"module": "loop-live"}, "context": {"module": "context-simple"}},
            "agents": {"worker": {}}}
    paths = {name: Path(importlib.util.find_spec("amplifier_module_" + name.replace("-", "_")).origin).parent
             for name in ("loop-live", "context-simple")}
    prepared = Prepared(plan, BundleModuleResolver(paths),
                        Bundle(name="computer-fixture", session=plan["session"], agents=plan["agents"]))
    parent = await prepared.create_session(session_id="computer-parent")
    children = await install_children(parent, prepared, Runtime("computer-parent"),
                                      SessionStore(tmp_path / "children"), None)
    def capture():
        async def install(coordinator):
            controller = Controller()
            coordinator.register_capability("computer_use.episodes", controller)
            def owner():
                runtime = coordinator.get_capability("live.runtime")
                generation = getattr(runtime, "generation", None)
                key = generation["id"] if generation else coordinator.get_capability("web.worker_run")
                return key, lambda: not coordinator.cancellation.is_cancelled
            sponsor = episodes.install(coordinator, owner)
            runs.append((coordinator, controller, sponsor))
        return install
    parent.coordinator.register_capability("web.app_access.install", capture)
    try:
        await children.spawn("worker", "first", parent, sub_session_id="finite")
        assert runs[-1][2].last_status["release_confirmed"]
        first = runs[-1][0]
        await children.resume("finite", "second", parent)
        assert runs[-1][0] is not first
        assert runs[-1][2].last_status["release_confirmed"]
        from amplifier_module_tool_delegate import DelegateTool
        await parent.coordinator.mount("tools", DelegateTool(parent.coordinator,
            {"features": {"self_delegation": {"enabled": True}}}), name="delegate")
        await children.install(parent, prepared)
        delegate = parent.coordinator.get("tools")["delegate"]
        task = asyncio.create_task(delegate.execute({"agent": "worker", "instruction": "live", "persistent": True}))
        async with asyncio.timeout(5):
            while not any(r.get("persistent") and r["status"] == "idle" for r in children.rows.values()):
                await asyncio.sleep(.01)
        row = next(r for r in children.rows.values() if r.get("persistent"))
        coordinator, controller, sponsor = runs[-1]
        await tick()
        assert sponsor.last_status["release_confirmed"]  # Idle cannot retain custody.
        await children.control(row["sessionId"], "message", "another turn")
        async with asyncio.timeout(5):
            while sum(c[0] == "begin" for c in controller.calls) < 2 or row["status"] != "idle":
                await asyncio.sleep(.01)
        await children.control(row["sessionId"], "finish")
        await task
        assert coordinator not in episodes.sponsors
    finally:
        await episodes.close()
        await parent.cleanup()


@pytest.mark.parametrize("outcome", ["result", "error", "cancel"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_real_child_original_outcome_survives_bounded_deferred_cleanup(
        tmp_path, monkeypatch, outcome, cleanup_fails):
    from dataclasses import dataclass
    import importlib.util
    from pathlib import Path
    from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .01)
    events = []
    registry = ComputerEpisodes(events.append)
    proceed = asyncio.Event()
    cleanup_calls = []
    original_error = ValueError("original child execution error")
    observed_error = []
    executing = asyncio.Event()
    class Loop:
        async def execute(self, *args, **kwargs):
            if outcome == "error":
                raise original_error
            if outcome == "cancel":
                raise asyncio.CancelledError("original child cancellation")
            return "original child result"
    class Child:
        def __init__(self, original):
            self.original = original
        def __getattr__(self, name):
            return getattr(self.original, name)
        async def execute(self, instruction):
            if outcome == "cancel":
                executing.set()
                await asyncio.Event().wait()  # Cancel the actual host child awaiter.
            try:
                return await self.original.execute(instruction)
            except Exception as exc:
                observed_error.append(exc)
                raise
        async def cleanup(self):
            cleanup_calls.append(True)
            await proceed.wait()
            if cleanup_fails:
                raise OSError("inert backend cleanup failed")
            await self.original.cleanup()
    @dataclass
    class Prepared(PreparedBundle):
        async def create_session(self, **kwargs):
            session = await super().create_session(**kwargs)
            await session.coordinator.mount("orchestrator", Loop())
            return Child(session) if kwargs.get("parent_id") else session
    plan = {"session": {"orchestrator": {"module": "loop-live"}, "context": {"module": "context-simple"}},
            "agents": {"worker": {}}}
    paths = {name: Path(importlib.util.find_spec("amplifier_module_" + name.replace("-", "_")).origin).parent
             for name in ("loop-live", "context-simple")}
    prepared = Prepared(plan, BundleModuleResolver(paths),
                        Bundle(name="outcome-fixture", session=plan["session"], agents=plan["agents"]))
    parent = await prepared.create_session(session_id="outcome-parent")
    children = await install_children(parent, prepared, Runtime("outcome-parent"),
                                      SessionStore(tmp_path / "children"), None)
    def capture():
        async def install(coordinator):
            registry.install(coordinator, lambda: ("child", lambda: True))
        return install
    parent.coordinator.register_capability("web.app_access.install", capture)
    root_controller = Controller()
    root_sponsor = registry.install(Coordinator(root_controller), lambda: ("independent-root", lambda: True))
    await root_sponsor.invoke("computer", {}, AsyncMock())
    try:
        operation = asyncio.create_task(children.spawn("worker", "inert", parent,
                                        sub_session_id="outcome-child"))
        if outcome == "cancel":
            await executing.wait()
            operation.cancel()
        results = await asyncio.gather(operation, return_exceptions=True)
        if outcome == "result":
            assert results[0]["output"] == "original child result"
        elif outcome == "error":
            # Preserve the exact public Core error, not an invented orchestrator
            # exception shape or a cleanup-related replacement.
            assert results[0] is observed_error[0] and "original child execution error" in str(results[0])
        else:
            assert isinstance(results[0], asyncio.CancelledError)
        row = children.rows["outcome-child"]
        assert any(event.get("type") == "computer.settlement" and not event["settled"] for event in events)
        assert row.get("_computerCleanup") is not None and registry.cleanups
        assert children.sessions.get("outcome-child") is not None
        with pytest.raises(ValueError, match="cleanup is unconfirmed"):
            await children.resume("outcome-child", "must not execute", parent)
        proceed.set()
        await asyncio.gather(*tuple(registry.pending), return_exceptions=True)
        await tick()
        assert cleanup_calls == [True]
        if cleanup_fails:
            assert registry.cleanups and not registry.receipt()["settled"]
            assert row.get("_computerCleanup") is not None
            assert not events[-1]["settled"]
        else:
            assert not registry.cleanups and not registry.receipt()["settled"]
            assert row.get("_computerCleanup") is None
            assert events[-1]["settled"] and events[-1].get("scope") == "cleanup"
        assert not any(call[0] == "end" for call in root_controller.calls)
    finally:
        proceed.set()
        await asyncio.gather(*tuple(registry.pending), return_exceptions=True)
        child = children.sessions.get("outcome-child")
        if child:
            await child.original.cleanup()  # Test-only teardown of intentional failure.
        await root_sponsor.close()
        await parent.cleanup()


async def test_worker_captures_child_run_and_own_turn_not_later_root_generation():
    worker = Worker()
    root, child = Coordinator(), Coordinator()
    child.session_id = "child"
    worker.session = SimpleNamespace(coordinator=root)
    worker.runtime = SimpleNamespace(generation={"id": "root-turn", "input_ids": ["input"]},
                                     closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.activation = object()
    worker.start_config = {}
    row = {"runId": "child-run", "status": "running"}
    root.register_capability("live.children", SimpleNamespace(rows={"child": row},
        sessions={"child": SimpleNamespace(coordinator=child)}))
    child.register_capability("web.worker_run", "child-run")
    worker.app_access_bridge(child, assignment=(["input"], {}, "root-turn", 0))
    # Finite run remains valid across an unrelated root turn.
    key, valid = worker.computer_execution(child)
    assert key == "child-run" and valid()
    worker.runtime.generation = {"id": "unrelated", "input_ids": ["later"]}
    assert valid()
    # Persistent child captures its own turn rather than the assignment/root.
    local = SimpleNamespace(generation={"id": "child-turn"})
    child.register_capability("live.runtime", local)
    key, turn_valid = worker.computer_execution(child)
    assert key == "child-turn" and turn_valid()
    local.generation = {"id": "new-child-turn"}
    assert not turn_valid()
    # A resumed same-ID coordinator cannot inherit an old run.
    row["runId"] = "resumed-run"
    assert not valid()


async def test_ui_resume_cannot_undo_pending_child_cancel_before_first_operation():
    _, _, registry, sponsor, _, _ = fixture()
    registry.fence(sponsor.coordinator, terminate=True)
    registry.resume()
    with pytest.raises(asyncio.CancelledError):
        await sponsor.invoke("computer", {}, AsyncMock())
    await registry.close()


async def test_cancel_during_final_drain_promotes_interruption_without_cancelling_settlement():
    controller, _, registry, sponsor, _, _ = fixture()
    controller.drained.clear()
    task = asyncio.create_task(sponsor.invoke("ui_use", {"surface": "computer"}, AsyncMock()))
    await tick()
    assert ("end", "episode-0", False) in controller.calls
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert controller.interrupted
    controller.drained.set()
    await registry.close()
    assert sponsor.last_status["state"] == "interrupted"


async def test_retarget_stop_during_drain_never_executes_target_change():
    controller, _, registry, sponsor, _, _ = fixture()
    await sponsor.invoke("computer", {}, AsyncMock())
    controller.drained.clear()
    change = AsyncMock()
    task = asyncio.create_task(sponsor.invoke("desktop", {"action": "retarget"}, change))
    await tick()
    registry.fence(halt=True)
    controller.drained.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    change.assert_not_awaited()
    await registry.close()


async def test_transient_status_failure_keeps_actual_controller_until_workers_settle():
    controller, _, registry, sponsor, _, _ = fixture()
    original_end = controller.end
    polled = asyncio.Event()
    calls = []
    async def end(generation, *, interrupted=False):
        if not calls:
            return controller.state(state="stopping", workers=1, admission_open=False)
        return await original_end(generation, interrupted=interrupted)
    async def status(generation):
        calls.append(generation)
        if len(calls) == 1:
            polled.set()
            raise ConnectionError("fixture transient")
        return controller.state(state="stopping", workers=0, admission_open=False)
    controller.end, controller.status = end, status
    task = asyncio.create_task(sponsor.invoke("ui_use", {"surface": "computer"}, AsyncMock()))
    await polled.wait()
    await tick()
    assert registry.pending and sponsor.episodes and not task.done()
    await task
    assert sponsor.last_status["release_confirmed"]
    await registry.close()


@pytest.mark.parametrize("phase", ["cold", "replacement"])
async def test_saved_ui_stop_applies_before_new_worker_sponsor_admission(tmp_path, phase):
    import json
    (tmp_path / "ui-use-stopped.json").write_text(json.dumps(["chat"]), encoding="utf-8")
    registry = ComputerEpisodes()
    if phase == "replacement":
        registry.install(Coordinator(Controller()), lambda: ("old", lambda: True))
        await registry.close()
    registry.restore_stop(tmp_path, "chat")
    sponsor = registry.install(Coordinator(Controller()), lambda: ("new", lambda: True))
    with pytest.raises(asyncio.CancelledError):
        await sponsor.invoke("computer", {}, AsyncMock())
    assert not sponsor.episodes
    await registry.close()


async def test_child_stop_during_preparation_is_retained_for_exact_run(monkeypatch):
    worker = Worker()
    root = Coordinator()
    worker.session = SimpleNamespace(coordinator=root)
    worker.runtime = SimpleNamespace(generation={"id": "root", "input_ids": ["input"]},
        closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.activation = object()
    worker.start_config = {}
    rows = {"preparing": {"runId": "run-one", "status": "starting"}}
    children = SimpleNamespace(rows=rows, sessions={})
    root.register_capability("live.children", children)
    worker.app_access_bridge(root)
    install = root.get_capability("web.app_access.install")()
    await worker.command_lock.acquire()
    stop = asyncio.create_task(worker.command({"op": "worker.stop", "worker_id": "preparing"}))
    await tick()
    assert rows["preparing"]["_computer_stopped"] == "run-one"
    monkeypatch.setattr("amplifier_web.app_guidance.install_app_access", AsyncMock())
    child = Coordinator(Controller())
    child.session_id = "preparing"
    child.register_capability("web.worker_run", "run-one")
    children.sessions["preparing"] = SimpleNamespace(coordinator=child)
    await install(child)
    sponsor = child.get_capability("web.computer_episodes")
    assert sponsor.terminated
    worker.computer_episodes.resume()
    with pytest.raises(asyncio.CancelledError):
        await sponsor.invoke("computer", {}, AsyncMock())
    stop.cancel()
    worker.command_lock.release()
    await asyncio.gather(stop, return_exceptions=True)
    # Resuming the same native ID is a distinct coordinator/run, not a revival.
    rows["preparing"] = {"runId": "run-two", "status": "starting"}
    resumed = Coordinator(Controller())
    resumed.session_id = "preparing"
    resumed.register_capability("web.worker_run", "run-two")
    children.sessions["preparing"] = SimpleNamespace(coordinator=resumed)
    await install(resumed)
    assert not resumed.get_capability("web.computer_episodes").terminated
    await worker.computer_episodes.close()


async def test_direct_script_sponsor_keeps_native_tool_inspection_after_app_bootstrap(monkeypatch):
    """The constructor's sibling import and later package import are distinct."""
    import importlib.util
    from pathlib import Path
    from amplifier_core import HookRegistry
    from amplifier_web.app_guidance import install_app_access
    path = Path(__file__).resolve().parents[1] / "amplifier_web/computer_episodes.py"
    spec = importlib.util.spec_from_file_location("computer_episodes", path)
    direct = importlib.util.module_from_spec(spec)
    import sys
    monkeypatch.setitem(sys.modules, "computer_episodes", direct)
    spec.loader.exec_module(direct)
    coordinator = Coordinator(Controller())
    coordinator.hooks = HookRegistry()
    coordinator.register_capability("web.work_effect_check", AsyncMock(return_value={"allowed": True}))
    await install_app_access(coordinator, AsyncMock(return_value={}))
    registry = direct.ComputerEpisodes()
    registry.install(coordinator, lambda: ("turn", lambda: True))
    native = SimpleNamespace(name="computer", execute=AsyncMock())
    await coordinator.mount("tools", native, name="computer")
    mounted = coordinator.tools["computer"]
    assert coordinator.get_capability("web.work_inspect_tool")("computer", mounted) is native
    await registry.close()


@pytest.mark.parametrize("name,arguments,allowed", [
    ("desktop", {"action": action}, True) for action in ("episode_status", "diagnostic_snapshot", "doctor")
] + [
    ("desktop", {"action": "end_episode", "generation": "exact"}, True),
    *[("desktop", {"action": action}, False) for action in
      ("begin_episode", "retarget", "get_clipboard", "set_clipboard", "list_windows", "screen_info")],
    *[("computer", {"action": action}, False) for action in
      ("observe", "screenshot", "doctor", "episode_status", "diagnostic_snapshot", "act", "type")],
    ("computer", {"version": 1, "actions": [{"action": "screenshot"}], "capture_after": True}, False),
    ("ui_use", {"surface": "computer", "action": "diagnostic_snapshot"}, False),
])
async def test_host_stop_hook_allows_only_desktop_non_acquiring_routes(name, arguments, allowed):
    from amplifier_core import HookRegistry
    from amplifier_web.app_guidance import install_app_access
    coordinator = Coordinator()
    coordinator.hooks = HookRegistry()
    bridge = AsyncMock(return_value={"stopped": True})
    await install_app_access(coordinator, bridge)
    result = await coordinator.hooks.emit("tool:pre", {"tool_name": name, "tool_input": arguments})
    assert (result.action != "deny") is allowed
    if allowed:
        bridge.assert_not_awaited()


async def test_passive_route_still_runs_work_check_and_preserves_native_inspection():
    from amplifier_core import HookRegistry, ToolResult
    from amplifier_web.app_guidance import install_app_access
    coordinator = Coordinator(Controller())
    coordinator.hooks = HookRegistry()
    check = AsyncMock(return_value={"allowed": False})
    coordinator.register_capability("web.work_effect_check", check)
    await install_app_access(coordinator, AsyncMock(return_value={}))
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: None)
    native = SimpleNamespace(name="desktop", execute=AsyncMock(return_value=ToolResult(success=True)))
    await coordinator.mount("tools", native, name="desktop")
    mounted = coordinator.tools["desktop"]
    try:
        result = await mounted.execute({"action": "diagnostic_snapshot"})
        assert not result.success
        check.assert_awaited_once()
        native.execute.assert_not_awaited()
        assert coordinator.get_capability("web.work_inspect_tool")("desktop", mounted) is native
        assert not sponsor.episodes
    finally:
        await registry.close()


async def test_close_has_bounded_unconfirmed_receipt_and_keeps_recoverable_custody(monkeypatch):
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .02, raising=False)
    controller, _, registry, sponsor, _, events = fixture()
    recover = asyncio.Event()
    async def end(generation, *, interrupted=False):
        return controller.state(state="interrupted", workers=0 if recover.is_set() else 1,
            admission_open=False, release_confirmed=recover.is_set(), indicator_settled=recover.is_set())
    async def status(generation):
        if not recover.is_set():
            raise ConnectionError("inert dead transport")
        return await end(generation, interrupted=True)
    controller.end, controller.status = end, status
    await sponsor.invoke("computer", {}, AsyncMock())
    closing = asyncio.create_task(registry.close())
    try:
        done, _ = await asyncio.wait({closing}, timeout=.15)
        assert done, "close waited indefinitely for a disconnected pending native worker"
        receipt = closing.result()
        assert receipt["settled"] is False and receipt["state"] == "stopping"
        assert receipt["episodes"] and not receipt["episodes"][0]["release_confirmed"]
        assert registry.pending and sponsor.episodes and registry.sponsors
        assert registry.unresolved
        registry.resume()
        with pytest.raises(asyncio.CancelledError):
            await sponsor.invoke("computer", {}, AsyncMock())
        assert not any(event.get("release_confirmed") for event in events)
    finally:
        recover.set()
        await asyncio.wait_for(asyncio.shield(closing), 2)
        await asyncio.sleep(.7)
        await registry.close()


async def test_replacement_cleanup_refuses_with_bounded_unconfirmed_receipt(monkeypatch):
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .02, raising=False)
    controller, _, registry, sponsor, _, _ = fixture()
    controller.drained.clear()
    await sponsor.invoke("computer", {}, AsyncMock())
    worker = Worker()
    worker.computer_episodes = registry
    worker.session = SimpleNamespace(cleanup=AsyncMock())
    worker.controls = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(worker, "start", AsyncMock())
    closing = asyncio.create_task(registry.close())
    try:
        done, _ = await asyncio.wait({closing}, timeout=.15)
        assert done, "cleanup blocked forever instead of retaining unresolved desktop custody"
        assert closing.result()["settled"] is False
        worker.session.cleanup.assert_not_awaited()
        worker.controls.close.assert_not_awaited()
        worker.start.assert_not_awaited()
        assert registry.pending and registry.unresolved
    finally:
        controller.drained.set()
        await asyncio.gather(closing, return_exceptions=True)
        await registry.close()


async def test_unchanged_unconfirmed_status_is_not_republished():
    _, _, registry, sponsor, _, events = fixture()
    episode = SimpleNamespace(status={"identity": {"generation": "old"}, "state": "unknown",
                                    "workers": 1, "release_confirmed": False})
    for _ in range(10):
        sponsor.publish(episode)
    assert len(events) == 1, "unchanged unknown custody flooded lifecycle publication"
    await registry.close()


async def test_operation_returns_original_after_bounded_wait_but_native_drain_and_target_stay_fenced(monkeypatch):
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .02)
    controller, _, registry, sponsor, _, events = fixture()
    controller.drained.clear()
    result = {"status": "unknown", "effects": "not_rolled_back"}
    try:
        assert await asyncio.wait_for(sponsor.invoke("ui_use", {"surface": "computer"},
            AsyncMock(return_value=result)), .15) is result
        assert registry.pending and registry.unresolved and sponsor.episodes
        assert not sponsor.last_status["release_confirmed"]
        change = AsyncMock()
        with pytest.raises(computer_episodes.SettlementPending):
            await sponsor.invoke("desktop", {"action": "retarget", "target": "local"}, change)
        change.assert_not_awaited()
        registry.resume()
        with pytest.raises(computer_episodes.SettlementPending):
            await sponsor.invoke("computer", {"action": "type", "text": "not sent"}, change)
        assert not any(e.get("release_confirmed") for e in events)
    finally:
        controller.drained.set()
        await registry.join()
        assert not registry.unresolved
        assert (await registry.close())["settled"]


async def test_cancelled_close_waiter_does_not_erase_cleanup_obligation(monkeypatch):
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .1)
    controller, _, registry, sponsor, _, _ = fixture()
    controller.drained.clear()
    await sponsor.invoke("computer", {}, AsyncMock())
    close = asyncio.create_task(registry.close())
    await tick()
    close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close
    assert registry.pending and registry.unresolved and sponsor.terminated
    assert not any(task.cancelled() for task in registry.pending)
    controller.drained.set()
    await registry.join()
    assert (await registry.close())["settled"]


async def test_worker_shutdown_retains_backend_and_shared_owner_until_real_drain(monkeypatch):
    from amplifier_web import computer_episodes, runtime_worker
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .02)
    controller, _, registry, sponsor, _, _ = fixture()
    controller.drained.clear()
    await sponsor.invoke("computer", {}, AsyncMock())
    events = []
    monkeypatch.setattr(runtime_worker, "publish", events.append)
    worker = Worker()
    worker.computer_episodes = registry
    worker.controls = SimpleNamespace(close=AsyncMock())
    worker.session = SimpleNamespace(cleanup=AsyncMock())
    release = AsyncMock()
    worker.shared_handle = SimpleNamespace(release=lambda: release())
    worker.checkpoint_terminal_outcome = AsyncMock()
    worker.read = AsyncMock(side_effect=lambda: asyncio.Event().wait())
    async def read():
        await asyncio.Event().wait()
    worker.read = read
    worker.shutdown.set()
    running = asyncio.create_task(worker._run())
    try:
        await asyncio.sleep(.08)
        assert not running.done()
        assert any(e.get("type") == "computer.settlement" and not e["settled"] for e in events)
        worker.session.cleanup.assert_not_awaited()
        worker.controls.close.assert_not_awaited()
        release.assert_not_called()
        assert registry.pending and registry.unresolved
    finally:
        controller.drained.set()
        # No runtime/session fixture state is needed beyond retained cleanup.
        worker.controls = None
        worker.shared_handle = None
        await asyncio.wait_for(running, 2)
        worker.session.cleanup.assert_awaited_once()


@pytest.fixture
async def parking_worker(tmp_path, monkeypatch):
    from amplifier_foundation.session.activation import ActivationGate
    from amplifier_foundation.session import shared_state
    worker = Worker()
    worker.workspace, worker.home = tmp_path / "workspace", tmp_path / "home"
    worker.workspace.mkdir()
    worker.home.mkdir()
    worker.runtime = SimpleNamespace(session_id="desktop-parking", queued_inputs=0,
                                    inbox=asyncio.Queue(), generation=None)
    worker.shared_store = shared_state.SharedSessionStore(
        worker.workspace, "desktop-parking", root=tmp_path / "shared")
    worker.shared_store_stamp = shared_state.file_stamp
    worker.shared_handle = worker.shared_store.acquire(app="amplifier-unified", fixture=True)
    worker.activation_gate = ActivationGate()
    worker.activation = worker.activation_gate.activate()
    worker.checkpoint = AsyncMock()
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda _: None,
        get_capability=lambda name: worker.checkpoint if name == "live.checkpoint" else None))
    monkeypatch.setattr("amplifier_web.runtime_worker.publish", lambda _: None)
    try:
        yield worker
    finally:
        worker.shutdown.set()
        for task in tuple(worker.tasks):
            task.cancel()
        await asyncio.gather(*tuple(worker.tasks), return_exceptions=True)
        if worker.shared_handle:
            worker.shared_handle.release()


async def test_quiet_desktop_settlement_allows_retirement_without_idle_release(parking_worker):
    worker = parking_worker
    controller, _, registry, sponsor, _, _ = fixture()
    worker.computer_episodes = registry
    controller.drained.clear()
    await sponsor.invoke("computer", {}, AsyncMock())
    sponsor.finish()
    assert not await worker.checkpoint_idle()
    assert not await worker.checkpoint_idle()
    assert not registry._settled_callbacks and not worker.tasks
    assert worker.shared_handle.active
    worker.checkpoint.assert_not_awaited()
    controller.drained.set()
    await registry.join()
    assert worker.shared_handle.active and not worker.shutdown.is_set()
    assert await worker.checkpoint_idle()
    assert worker.shared_handle.active
    worker.checkpoint.assert_awaited_once_with("completed")


@pytest.mark.parametrize("change", ["session", "runtime", "activation", "shutdown", "stop", "new_input"])
async def test_desktop_retirement_rechecks_owner_after_awaited_save(
        parking_worker, change):
    worker = parking_worker
    controller, _, registry, sponsor, _, _ = fixture()
    worker.computer_episodes = registry
    controller.drained.clear()
    await sponsor.invoke("computer", {}, AsyncMock())
    sponsor.finish()
    controller.drained.set()
    await registry.join()
    async def checkpoint(*_):
        if change == "session":
            worker.session = SimpleNamespace(coordinator=worker.session.coordinator)
        elif change == "runtime":
            worker.runtime = SimpleNamespace(**vars(worker.runtime))
        elif change == "activation":
            worker.activation = object()
        elif change == "shutdown":
            worker.shutdown.set()
        elif change == "stop":
            worker.stop_revision += 1
        elif change == "new_input":
            worker.runtime.inbox.put_nowait("late observation")
    worker.checkpoint.side_effect = checkpoint
    assert not await worker.checkpoint_idle()
    assert worker.shared_handle.active
    assert not registry._settled_callbacks


async def test_desktop_retirement_rechecks_foreground_input(parking_worker):
    worker = parking_worker
    controller, _, registry, sponsor, _, _ = fixture()
    worker.computer_episodes = registry
    controller.drained.clear()
    await sponsor.invoke("computer", {}, AsyncMock())
    sponsor.finish()
    assert not await worker.checkpoint_idle()
    async with worker.command_lock:
        controller.drained.set()
        await registry.join()
        worker.runtime.queued_inputs = 1
        worker.runtime.inbox.put_nowait("new input")
    await asyncio.gather(*tuple(worker.tasks), return_exceptions=True)
    assert worker.shared_handle.active
    worker.checkpoint.assert_not_awaited()
    worker.runtime.queued_inputs = 0
    worker.runtime.inbox.get_nowait()
    assert await worker.checkpoint_idle()
    assert worker.shared_handle.active


async def test_unconfirmed_settlement_blocks_retirement_until_passive_confirmation(parking_worker):
    worker = parking_worker
    controller, _, registry, sponsor, _, _ = fixture()
    worker.computer_episodes = registry
    controller.release = False
    await sponsor.invoke("computer", {}, AsyncMock())
    sponsor.finish()
    assert not await worker.checkpoint_idle()
    await asyncio.gather(*tuple(registry.pending), return_exceptions=True)
    await tick()
    assert registry.unresolved and worker.shared_handle.active and not worker.tasks
    assert not registry._settled_callbacks
    controller.release = True
    registry.reconcile(controller, controller.state(workers=0, admission_open=False,
                       release_confirmed=True, indicator_settled=True))
    await asyncio.gather(*tuple(worker.tasks), return_exceptions=True)
    assert await worker.checkpoint_idle()
    assert worker.shared_handle.active
    worker.checkpoint.assert_awaited_once()


async def test_failed_child_cleanup_completion_never_allows_retirement(parking_worker):
    worker = parking_worker
    registry = worker.computer_episodes
    proceed = asyncio.Event()
    async def failed_cleanup():
        await proceed.wait()
        raise OSError("inert child backend cleanup uncertainty")
    cleanup = registry.retain_cleanup(object(), "child", failed_cleanup)
    assert not await worker.checkpoint_idle()
    proceed.set()
    await asyncio.gather(cleanup, return_exceptions=True)
    await tick()
    assert registry.cleanups and not registry.pending and not worker.tasks
    assert not await worker.checkpoint_idle()
    assert worker.shared_handle.active
    worker.checkpoint.assert_not_awaited()


async def test_desktop_settlement_with_other_active_sponsor_blocks_retirement(parking_worker):
    worker = parking_worker
    controller, _, registry, sponsor, _, _ = fixture()
    worker.computer_episodes = registry
    other = registry.install(Coordinator(Controller()), lambda: ("other", lambda: True))
    await sponsor.invoke("computer", {}, AsyncMock())
    await other.invoke("computer", {}, AsyncMock())
    sponsor.finish()
    assert not await worker.checkpoint_idle()
    await asyncio.gather(*tuple(registry.pending), return_exceptions=True)
    await tick()
    assert worker.shared_handle.active and not worker.tasks
    other.finish()
    await registry.join()
    await asyncio.gather(*tuple(worker.tasks), return_exceptions=True)
    assert await worker.checkpoint_idle()
    assert worker.shared_handle.active


async def test_desktop_retirement_checkpoint_failure_retains_writer(parking_worker):
    worker = parking_worker
    registry = worker.computer_episodes
    proceed = asyncio.Event()
    registry.retain(proceed.wait())
    worker.checkpoint.side_effect = OSError("inert checkpoint failure")
    assert not await worker.checkpoint_idle()
    proceed.set()
    await registry.join()
    await asyncio.gather(*tuple(worker.tasks), return_exceptions=True)
    await tick()
    with pytest.raises(OSError, match="checkpoint failure"):
        await worker.checkpoint_idle()
    assert not worker.shutdown.is_set() and worker.shared_handle.active
    assert not registry.pending and registry.receipt()["settled"]


async def test_stop_during_retirement_checkpoint_cannot_release_writer(parking_worker):
    worker = parking_worker
    controller, _, registry, sponsor, _, _ = fixture()
    worker.computer_episodes = registry
    controller.drained.clear()
    entered, proceed = asyncio.Event(), asyncio.Event()
    async def checkpoint(*_):
        entered.set()
        await proceed.wait()
    worker.checkpoint.side_effect = checkpoint
    await sponsor.invoke("computer", {}, AsyncMock())
    sponsor.finish()
    controller.drained.set()
    await registry.join()
    saving = asyncio.create_task(worker.checkpoint_idle())
    await entered.wait()
    await worker.command({"op": "computer.stop"})
    proceed.set()
    assert not await saving
    assert worker.shared_handle.active


async def test_desktop_retirement_waits_for_pending_naming(parking_worker):
    worker = parking_worker
    registry = worker.computer_episodes
    proceed, named = asyncio.Event(), asyncio.Event()
    cleanup = registry.retain(proceed.wait())
    worker.naming = SimpleNamespace(pending=asyncio.create_task(named.wait()))
    try:
        assert not await worker.checkpoint_idle()
        proceed.set()
        await cleanup
        await tick()
        assert not await worker.checkpoint_idle()
        assert not worker.tasks
        await worker.command({"op": "computer.stop"})
        named.set()
        await asyncio.gather(*tuple(worker.tasks), return_exceptions=True)
        assert worker.shared_handle.active
        worker.checkpoint.assert_not_awaited()
    finally:
        named.set()
        await worker.naming.pending

async def test_replacement_close_retains_native_task_until_settlement(monkeypatch):
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .02)
    controller, _, registry, sponsor, _, _ = fixture()
    controller.drained.clear()
    entered = asyncio.Event()
    async def body(arguments):
        entered.set()
        await asyncio.Event().wait()
    operation = asyncio.create_task(sponsor.invoke("ui_use", {"surface": "computer"}, body))
    await entered.wait()
    worker = Worker()
    worker.computer_episodes = registry
    worker.execution = operation
    worker.controls = SimpleNamespace(close=AsyncMock())
    worker.session = SimpleNamespace(cleanup=AsyncMock())
    try:
        assert not (await asyncio.wait_for(registry.close(), .2))["settled"]
        assert registry.pending and registry.unresolved
        worker.session.cleanup.assert_not_awaited()
        assert operation.cancelling() <= 1
    finally:
        controller.drained.set()
        await asyncio.gather(operation, return_exceptions=True)
        await registry.join()
        await registry.close()


async def test_close_bounds_hanging_status_without_cancelling_its_owner(monkeypatch):
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .02)
    controller, _, registry, sponsor, _, _ = fixture()
    entered, recover = asyncio.Event(), asyncio.Event()
    status_cancelled = []
    async def end(generation, *, interrupted=False):
        return controller.state(state="stopping", workers=1 if not recover.is_set() else 0,
                                admission_open=False, release_confirmed=recover.is_set())
    async def status(generation):
        entered.set()
        try:
            await recover.wait()
        except asyncio.CancelledError:
            status_cancelled.append(True)
            raise
        return await end(generation)
    controller.end, controller.status = end, status
    await sponsor.invoke("computer", {}, AsyncMock())
    sponsor.finish(interrupted=True)
    await entered.wait()
    receipt = await registry.close()
    assert not receipt["settled"] and registry.unresolved and registry.pending
    assert not status_cancelled
    recover.set()
    await registry.join()
    assert (await registry.close())["settled"]
    assert not status_cancelled


async def test_stop_during_readiness_wakes_caller_but_late_acquired_identity_still_settles(monkeypatch):
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .02)
    controller, _, registry, sponsor, _, _ = fixture()
    controller.ready.clear()
    body = AsyncMock()
    task = asyncio.create_task(sponsor.invoke("computer", {}, body))
    await tick()
    registry.fence(halt=True)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, .2)
    assert registry.receipt()["episodes"][0]["release_confirmed"] is False
    assert registry.pending and registry.unresolved and not controller.calls
    body.assert_not_awaited()
    controller.ready.set()
    await registry.join()
    assert ("end", "episode-0", True) in controller.calls
    assert (await registry.close())["settled"]


async def test_queued_call_cannot_acquire_over_cancelled_readiness(monkeypatch):
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .02)
    controller, _, registry, sponsor, _, _ = fixture()
    controller.ready.clear()
    attempts = []
    begin = controller.begin
    async def counted_begin():
        attempts.append(True)
        return await begin()
    controller.begin = counted_begin
    execute = AsyncMock()
    first = asyncio.create_task(sponsor.invoke("desktop", {}, execute))
    while not sponsor.episodes:
        await asyncio.sleep(0)
    queued = asyncio.create_task(sponsor.invoke("desktop", {}, execute))
    try:
        await tick()
        first.cancel()
        results = await asyncio.gather(first, queued, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert isinstance(results[1], computer_episodes.SettlementPending)
        assert len(attempts) == 1  # Cancelled readiness retains exclusive custody.
        execute.assert_not_called()
    finally:
        controller.ready.set()
        await asyncio.gather(*tuple(registry.pending), return_exceptions=True)
        await registry.close()


async def test_confirmed_replacement_uses_fresh_worker_without_reviving_old_sponsor(monkeypatch):
    from amplifier_web import runtime_worker
    monkeypatch.setattr(runtime_worker, "publish", lambda event: None)
    controller, _, registry, sponsor, _, _ = fixture()
    await sponsor.invoke("computer", {}, AsyncMock())
    worker = Worker()
    worker.computer_episodes = registry
    worker.session = SimpleNamespace(cleanup=AsyncMock())
    worker.controls = SimpleNamespace(close=AsyncMock())
    worker.start_config = {}
    worker.start = AsyncMock()
    assert (await registry.close())["settled"]
    replacement = Worker()
    assert replacement.computer_episodes is not registry
    assert not replacement.computer_episodes.terminated
    assert sponsor.terminated and not registry.sponsors and not registry.unresolved


async def test_late_body_finalizer_cannot_resurrect_already_completed_settlement():
    controller, _, registry, sponsor, _, _ = fixture()
    entered, proceed = asyncio.Event(), asyncio.Event()
    async def body(arguments):
        entered.set()
        await proceed.wait()
    task = asyncio.create_task(sponsor.invoke("ui_use", {"surface": "computer"}, body))
    await entered.wait()
    registry.fence()
    await registry.join()
    count = len(controller.calls)
    proceed.set()
    await task
    assert len(controller.calls) == count
    assert not registry.unresolved and not registry.pending and not sponsor.unconfirmed
    await registry.close()


async def test_unknown_end_is_not_replayed_when_passive_status_reports_workers_zero(monkeypatch):
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .02)
    controller, _, registry, sponsor, _, _ = fixture()
    recovered = asyncio.Event()
    ends = []
    async def end(generation, *, interrupted=False):
        ends.append(generation)
        raise ConnectionError("end reply lost")
    async def status(generation):
        return controller.state(state="unknown", workers=0, admission_open=False,
            release_confirmed=recovered.is_set(), indicator_settled=recovered.is_set())
    controller.end, controller.status = end, status
    await sponsor.invoke("computer", {}, AsyncMock())
    sponsor.finish(interrupted=True)
    await asyncio.sleep(.35)
    assert ends == ["episode-0"] and registry.pending and registry.unresolved
    recovered.set()
    await registry.join()
    assert ends == ["episode-0"]
    await registry.close()


async def test_scoped_child_close_does_not_wait_for_unrelated_sponsor():
    _, _, registry, busy, _, _ = fixture()
    await busy.invoke("computer", {}, AsyncMock())
    child = registry.install(Coordinator(Controller()), lambda: ("child", lambda: True))
    receipt = await child.close()
    assert receipt["settled"] and not child.episodes
    assert not registry.receipt()["settled"]  # The unrelated owner is still active.
    await registry.close()


@pytest.mark.parametrize("cancel_waiter", [False, True])
async def test_retained_child_cleanup_failure_or_cancelled_waiter_never_means_settled(cancel_waiter):
    from amplifier_web.computer_episodes import wait_settlement
    registry = ComputerEpisodes()
    coordinator = Coordinator()
    entered, proceed = asyncio.Event(), asyncio.Event()
    calls = []
    async def cleanup():
        calls.append(True)
        entered.set()
        await proceed.wait()
        if not cancel_waiter:
            raise OSError("inert cleanup failure")
    task = registry.retain_cleanup(coordinator, "child-run", cleanup)
    waiter = asyncio.create_task(wait_settlement({task}))
    await entered.wait()
    if cancel_waiter:
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not task.cancelled() and registry.cleanups
    proceed.set()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.gather(waiter, return_exceptions=True)
    await tick()
    if cancel_waiter:
        assert registry.receipt()["settled"]
    else:
        receipt = await registry.close()
        assert not receipt["settled"]
        assert receipt["episodes"][0]["reason"] == "child_cleanup_unconfirmed"
        assert registry.retain_cleanup(coordinator, "child-run", cleanup) is task
        assert calls == [True]  # No uncertain cleanup replay.


@pytest.mark.parametrize("fail_cleanup", [False, True])
@pytest.mark.parametrize("checkpoint_window", [False, True])
async def test_real_child_cleanup_is_owned_before_cancelled_wait_and_failed_cleanup_is_retained(
        tmp_path, monkeypatch, fail_cleanup, checkpoint_window):
    from dataclasses import dataclass
    import importlib.util
    from pathlib import Path
    from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .05)
    registry = ComputerEpisodes()
    controller = Controller()
    controller.drained.clear()
    installed = asyncio.Event()
    calls = []
    constructions = []
    checkpoint_entered, checkpoint_proceed = asyncio.Event(), asyncio.Event()
    class Loop:
        async def execute(self, prompt, context, providers, tools, hooks, coordinator):
            if prompt != "eviction probe":
                await coordinator.get_capability("web.computer_episodes").invoke("computer", {}, AsyncMock())
            return "body completed"
    class Child:
        def __init__(self, original):
            self.original = original
        def __getattr__(self, name):
            return getattr(self.original, name)
        async def cleanup(self):
            if self.original.session_id == "cleanup-child":
                calls.append("cleanup")
            if fail_cleanup and self.original.session_id == "cleanup-child":
                raise OSError("inert real child cleanup failure")
            await self.original.cleanup()
    @dataclass
    class Prepared(PreparedBundle):
        async def create_session(self, **kwargs):
            constructions.append(kwargs.get("session_id"))
            session = await super().create_session(**kwargs)
            await session.coordinator.mount("orchestrator", Loop())
            if checkpoint_window and kwargs.get("session_id") == "cleanup-child":
                context = session.coordinator.get("context")
                get_messages = context.get_messages
                async def messages():
                    if children.rows["cleanup-child"]["status"] in {"completed", "cancelled", "error"}:
                        checkpoint_entered.set()
                        await checkpoint_proceed.wait()
                    return await get_messages()
                context.get_messages = messages
            return Child(session) if kwargs.get("parent_id") else session
    plan = {"session": {"orchestrator": {"module": "loop-live"}, "context": {"module": "context-simple"}},
            "agents": {"worker": {}}}
    paths = {name: Path(importlib.util.find_spec("amplifier_module_" + name.replace("-", "_")).origin).parent
             for name in ("loop-live", "context-simple")}
    prepared = Prepared(plan, BundleModuleResolver(paths),
                        Bundle(name="cleanup-fixture", session=plan["session"], agents=plan["agents"]))
    parent = await prepared.create_session(session_id="cleanup-parent")
    children = await install_children(parent, prepared, Runtime("cleanup-parent"),
                                      SessionStore(tmp_path / "children"), None)
    def capture():
        async def install(coordinator):
            coordinator.register_capability("computer_use.episodes", controller)
            registry.install(coordinator, lambda: ("finite", lambda: True))
            installed.set()
        return install
    parent.coordinator.register_capability("web.app_access.install", capture)
    operation = asyncio.create_task(children.spawn("worker", "inert", parent, sub_session_id="cleanup-child"))
    try:
        await installed.wait()
        if checkpoint_window:
            await checkpoint_entered.wait()
            assert children.rows["cleanup-child"]["status"] == "completed"
            assert "_computerCleanup" not in children.rows["cleanup-child"]
            old_row = children.rows["cleanup-child"]
            for index in range(255):
                children.rows[f"old-terminal-{index}"] = {"status": "completed"}
            await children.spawn("worker", "eviction probe", parent, sub_session_id="checkpoint-eviction")
            assert children.rows.get("cleanup-child") is old_row
            with pytest.raises(ValueError, match="already active"):
                await children.resume("cleanup-child", "eviction probe", parent)
            assert constructions.count("cleanup-child") == 1
            checkpoint_proceed.set()
        async with asyncio.timeout(2):
            while not registry.cleanups:
                await asyncio.sleep(0)
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        assert registry.cleanups and registry.pending and not calls
        # Terminal rows are a bounded display index, but retained native/backend
        # custody must remain an admission barrier at the eviction threshold.
        old_row = children.rows["cleanup-child"]
        if not checkpoint_window:
            for index in range(255):
                children.rows[f"old-terminal-{index}"] = {"status": "completed"}
        await children.spawn("worker", "eviction probe", parent, sub_session_id="eviction-child")
        assert children.rows.get("cleanup-child") is old_row
        assert "old-terminal-0" not in children.rows and len(children.rows) == 256
        before_resume = list(constructions)
        with pytest.raises(ValueError, match="cleanup is unconfirmed"):
            await children.resume("cleanup-child", "must not run", parent)
        assert constructions == before_resume  # No replacement session was built.
        controller.drained.set()
        async with asyncio.timeout(2):
            while not calls or registry.pending:
                await asyncio.sleep(.001)
        if fail_cleanup:
            assert not registry.receipt()["settled"] and registry.cleanups
            assert children.sessions.get("cleanup-child") is not None
            assert calls == ["cleanup"]
            await children.spawn("worker", "eviction probe", parent, sub_session_id="post-failure-eviction")
            assert children.rows.get("cleanup-child") is old_row
            with pytest.raises(ValueError, match="cleanup is unconfirmed"):
                await children.resume("cleanup-child", "eviction probe", parent)
            assert constructions.count("cleanup-child") == 1
        else:
            assert registry.receipt()["settled"] and not children.sessions.get("cleanup-child")
            await children.resume("cleanup-child", "eviction probe", parent)
            assert constructions.count("cleanup-child") == 2  # New run only after actual cleanup.
    finally:
        checkpoint_proceed.set()
        controller.drained.set()
        await asyncio.gather(operation, return_exceptions=True)
        child = children.sessions.get("cleanup-child")
        if child:
            # Test-only deterministic teardown bypasses the intentionally failed
            # wrapper, not a production uncertain-cleanup retry.
            await child.original.cleanup()
        await parent.cleanup()