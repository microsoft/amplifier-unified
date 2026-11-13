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