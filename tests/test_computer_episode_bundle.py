"""Opt-in frozen-bundle pairing, entirely inert (no OS backend or model calls).

Set COMPUTER_USE_TEST_SOURCE to an exact archived tool-computer-use directory.
This is candidate-graph qualification, not installed/native platform acceptance.
"""
import asyncio
import importlib
import os
from pathlib import Path
import threading
from unittest.mock import AsyncMock

import pytest

from test_computer_episodes import Coordinator, tick
from amplifier_web.computer_episodes import ComputerEpisodes


@pytest.fixture
def bundle(monkeypatch, tmp_path):
    source = os.environ.get("COMPUTER_USE_TEST_SOURCE")
    if not source:
        pytest.skip("Explicit frozen computer-use source required for candidate pairing")
    monkeypatch.syspath_prepend(source)
    cu = importlib.import_module("amplifier_module_tool_computer_use")
    assert Path(cu.__file__).resolve().is_relative_to(Path(source).resolve())
    monkeypatch.setattr(cu, "SHOT_DIR", tmp_path / "shots")
    from amplifier_module_tool_computer_use.backend import ScreenGeometry

    class Backend:
        name = "inert-host-pairing"
        is_remote = False
        def screen_geometry(self):
            return ScreenGeometry(1600, 1200, 0, 0)
        def list_monitors(self):
            raise cu.BackendError("fixture has no monitor enumeration")
        def get_clipboard(self):
            return "fixture clipboard"

    class Indicator:
        def __init__(self):
            self.states = []
        def start(self, state="viewing"):
            return self.update(state)
        def update(self, state):
            self.states.append(state)
            return self.status()
        def status(self):
            return {"available": True, "ready": True, "healthy": True}
        def close(self):
            return {"closed": True, "surface_absent": True, "process_settled": True,
                    "helper_exit": 0 if self.states[-1] == "released" else 2}

    indicator = Indicator()
    tool = cu.ComputerTool(Backend(), {"_indicator_factory": lambda: indicator})
    tool._episode_enabled = True
    tool._announced = True
    tool.resolve_display()
    yield cu, tool, indicator
    if tool._episode_identity is not None:
        tool.end_episode(interrupted=True)
    with cu._episode_registry_lock:
        cu._episode_registry.clear()


async def test_real_bundle_normal_and_stale_controller_pairing(bundle):
    cu, tool, indicator = bundle
    controller = cu.EpisodeController(tool)
    coordinator = Coordinator(controller)
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: ("generation", lambda: True))
    desktop = cu.DesktopTool(tool)
    result = await sponsor.invoke("ui_use", {"surface": "computer"},
        lambda arguments: sponsor.invoke("desktop", {"action": "get_clipboard"}, desktop.execute))
    assert result.success and result.output
    assert sponsor.last_status["state"] == "released"
    assert sponsor.last_status["workers"] == 0
    assert sponsor.last_status["release_confirmed"]
    old = sponsor.last_status["identity"]["generation"]
    await sponsor.invoke("desktop", {"action": "get_clipboard"}, desktop.execute)
    with pytest.raises(cu.BackendError, match="stale"):
        controller.fence(old)
    assert tool.episode_status()["admission_open"]
    await registry.close()


async def test_real_bundle_native_thread_outlives_cancel_and_immediate_stop(bundle):
    cu, tool, indicator = bundle
    entered, proceed = threading.Event(), threading.Event()
    def read():
        entered.set()
        assert proceed.wait(10)
        return "fixture only"
    tool._backend.get_clipboard = read
    controller = cu.EpisodeController(tool)
    coordinator = Coordinator(controller)
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: ("generation", lambda: True))
    desktop = cu.DesktopTool(tool)
    task = asyncio.create_task(sponsor.invoke("ui_use", {"surface": "computer"},
        lambda arguments: sponsor.invoke("desktop", {"action": "get_clipboard"}, desktop.execute)))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        registry.fence(halt=True)
        assert not sponsor.last_status["admission_open"]
        assert not sponsor.last_status["release_confirmed"]
        task.cancel()
        await tick()
        assert "released" not in indicator.states
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert registry.pending
    finally:
        proceed.set()
        await registry.close()
    assert sponsor.last_status["workers"] == 0
    assert sponsor.last_status["state"] == "interrupted"
    assert "released" not in indicator.states


@pytest.mark.parametrize("action", ["episode_status", "diagnostic_snapshot", "doctor"])
async def test_real_bundle_fresh_metadata_queries_never_acquire_or_disclose(bundle, monkeypatch, action):
    cu, tool, indicator = bundle
    controller = cu.EpisodeController(tool)
    begin = AsyncMock(wraps=controller.begin)
    monkeypatch.setattr(controller, "begin", begin)
    def refuse_disclosure():
        raise AssertionError("A metadata query must not disclose or acquire")
    monkeypatch.setattr(tool, "_ensure_announced", refuse_disclosure)
    coordinator = Coordinator(controller)
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: ("generation", lambda: True))
    try:
        result = await sponsor.invoke("desktop", {"action": action}, cu.DesktopTool(tool).execute)
        assert result.success
        begin.assert_not_awaited()
        assert tool._episode_identity is None and indicator.states == []
        assert not sponsor.episodes and not sponsor.lazy and not registry.pending
    finally:
        await registry.close()


async def test_real_bundle_episode_status_after_bounded_completion_keeps_released_identity(bundle):
    cu, tool, indicator = bundle
    controller = cu.EpisodeController(tool)
    coordinator = Coordinator(controller)
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: ("same-native-generation", lambda: True))
    desktop = cu.DesktopTool(tool)
    try:
        await sponsor.invoke("ui_use", {"surface": "computer"},
            lambda arguments: sponsor.invoke("desktop", {"action": "get_clipboard"}, desktop.execute))
        released = tool.episode_status()
        assert released["state"] == "released"
        states = list(indicator.states)
        result = await sponsor.invoke("desktop", {"action": "episode_status"}, desktop.execute)
        assert result.success and result.output["identity"] == released["identity"]
        assert result.output["state"] == "released"
        assert indicator.states == states
        assert not sponsor.episodes and not sponsor.lazy and not registry.pending
    finally:
        await registry.close()


async def test_real_bundle_cached_snapshot_after_host_stop_does_not_reopen_and_input_stays_blocked(bundle):
    cu, tool, indicator = bundle
    controller = cu.EpisodeController(tool)
    coordinator = Coordinator(controller)
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: ("generation", lambda: True))
    desktop = cu.DesktopTool(tool)
    registry.fence(halt=True)
    try:
        result = await sponsor.invoke("desktop", {"action": "diagnostic_snapshot"}, desktop.execute)
        assert result.success and tool._episode_identity is None and indicator.states == []
        for name, arguments in (("computer", {"action": "screenshot"}),
                                ("desktop", {"action": "get_clipboard"}),
                                ("desktop", {"action": "begin_episode"}),
                                ("desktop", {"action": "retarget", "target": "local"})):
            with pytest.raises(asyncio.CancelledError):
                await sponsor.invoke(name, arguments, AsyncMock())
        assert not sponsor.episodes and not registry.pending
    finally:
        await registry.close()


async def test_real_bundle_end_without_existing_episode_never_acquires(bundle, monkeypatch):
    cu, tool, indicator = bundle
    controller = cu.EpisodeController(tool)
    begin = AsyncMock(wraps=controller.begin)
    monkeypatch.setattr(controller, "begin", begin)
    coordinator = Coordinator(controller)
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: ("generation", lambda: True))
    try:
        result = await sponsor.invoke("desktop", {"action": "end_episode", "generation": "old"},
                                      cu.DesktopTool(tool).execute)
        assert result.success and result.output["state"] == "idle"
        begin.assert_not_awaited()
        assert tool._episode_identity is None and indicator.states == []
    finally:
        await registry.close()


async def test_real_bundle_end_existing_episode_has_one_settlement_and_no_reacquisition(bundle, monkeypatch):
    cu, tool, indicator = bundle
    controller = cu.EpisodeController(tool)
    begin, end = AsyncMock(wraps=controller.begin), AsyncMock(wraps=controller.end)
    monkeypatch.setattr(controller, "begin", begin)
    monkeypatch.setattr(controller, "end", end)
    coordinator = Coordinator(controller)
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: ("generation", lambda: True))
    desktop = cu.DesktopTool(tool)
    try:
        await sponsor.invoke("desktop", {"action": "get_clipboard"}, desktop.execute)
        generation = tool.episode_status()["identity"]["generation"]
        episode = next(iter(sponsor.episodes))
        heartbeat = episode.heartbeat
        # Invalid/stale ends must not fence this actual owner.
        for arguments in ({"action": "end_episode"}, {"action": "end_episode", "generation": 7},
                          {"action": "end_episode", "generation": generation, "owner": "forged"},
                          {"action": "end_episode", "generation": "stale"}):
            result = await sponsor.invoke("desktop", arguments, desktop.execute)
            assert not result.success
            assert tool.episode_status()["admission_open"] and not episode.closing
            assert not heartbeat.cancelling()
        # Only the controller is used for an existing host episode, never the
        # DesktopTool's separate raw end path.
        raw = AsyncMock(side_effect=AssertionError("duplicate raw end"))
        result = await sponsor.invoke("desktop", {"action": "end_episode", "generation": generation}, raw)
        assert result.success and result.output["state"] == "released"
        assert result.output["release_confirmed"] and result.output["indicator_settled"]
        assert heartbeat.cancelling() and not sponsor.lazy and not sponsor.episodes
        begin.assert_awaited_once()
        end.assert_awaited_once_with(generation, interrupted=False)
        repeated = await sponsor.invoke("desktop", {"action": "end_episode", "generation": generation}, raw)
        assert repeated.success and repeated.output["state"] == "released"
        assert end.await_count == 1 and begin.await_count == 1
        # Explicit begin is still a sponsored input lifetime, not a passive
        # escape; same native generation can deliberately start a new episode.
        opened = await sponsor.invoke("desktop", {"action": "begin_episode"}, desktop.execute)
        assert opened.success and opened.output["identity"]["generation"] != generation
        assert begin.await_count == 2
    finally:
        await registry.close()


@pytest.mark.parametrize("action", ["episode_status", "diagnostic_snapshot", "doctor"])
@pytest.mark.parametrize("state", ["no_owner", "active", "stopped", "released"])
async def test_real_bundle_metadata_matrix_preserves_native_state(bundle, monkeypatch, action, state):
    cu, tool, indicator = bundle
    controller = cu.EpisodeController(tool)
    coordinator = Coordinator(controller)
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: None if state == "no_owner" else ("generation", lambda: True))
    desktop = cu.DesktopTool(tool)
    try:
        if state in {"active", "released"}:
            await sponsor.invoke("desktop", {"action": "get_clipboard"}, desktop.execute)
        if state == "released":
            await sponsor.invoke("desktop", {"action": "end_episode",
                "generation": tool.episode_status()["identity"]["generation"]}, desktop.execute)
        if state == "stopped":
            registry.fence(halt=True)
        before, states = tool.episode_status(), list(indicator.states)
        begin = AsyncMock(side_effect=AssertionError("metadata acquired"))
        monkeypatch.setattr(controller, "begin", begin)
        monkeypatch.setattr(tool, "_ensure_announced", lambda: pytest.fail("metadata disclosed"))
        result = await sponsor.invoke("desktop", {"action": action}, desktop.execute)
        assert result.success and tool.episode_status() == before and indicator.states == states
        begin.assert_not_awaited()
    finally:
        await registry.close()


async def test_real_bundle_cached_snapshot_after_cleanup_is_backend_free(bundle, monkeypatch):
    cu, tool, _ = bundle
    coordinator = Coordinator(cu.EpisodeController(tool))
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: None)
    registry.fence(halt=True)
    tool._cleanup_started = True
    monkeypatch.setattr(tool, "_ensure_announced", lambda: pytest.fail("disclosed"))
    monkeypatch.setattr(tool._backend, "screen_geometry", lambda: pytest.fail("backend sampled"))
    try:
        result = await sponsor.invoke("desktop", {"action": "diagnostic_snapshot"}, cu.DesktopTool(tool).execute)
        assert result.success and tool._episode_identity is None
        malformed = await sponsor.invoke("desktop", {"action": "diagnostic_snapshot", "text": "not allowed"},
                                         cu.DesktopTool(tool).execute)
        assert not malformed.success
    finally:
        await registry.close()


async def test_real_bundle_end_after_stop_joins_existing_settlement_without_restarting(bundle, monkeypatch):
    cu, tool, indicator = bundle
    controller = cu.EpisodeController(tool)
    begin, end = AsyncMock(wraps=controller.begin), AsyncMock(wraps=controller.end)
    monkeypatch.setattr(controller, "begin", begin)
    monkeypatch.setattr(controller, "end", end)
    coordinator = Coordinator(controller)
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: ("generation", lambda: True))
    desktop = cu.DesktopTool(tool)
    try:
        await sponsor.invoke("desktop", {"action": "get_clipboard"}, desktop.execute)
        generation = tool.episode_status()["identity"]["generation"]
        registry.fence(halt=True)
        result = await sponsor.invoke("desktop", {"action": "end_episode", "generation": generation}, desktop.execute)
        assert result.success and result.output["state"] == "interrupted"
        assert result.output["workers"] == 0 and not result.output["admission_open"]
        await tick()
        assert begin.await_count == end.await_count == 1
        assert not sponsor.episodes and not sponsor.lazy and sponsor.halted
        assert "released" not in indicator.states
    finally:
        await registry.close()


async def test_real_bundle_stale_end_during_readiness_cannot_fence_new_episode(bundle, monkeypatch):
    cu, tool, indicator = bundle
    controller = cu.EpisodeController(tool)
    ready, proceed = asyncio.Event(), asyncio.Event()
    original = controller.begin
    async def delayed_begin():
        ready.set()
        await proceed.wait()
        return await original()
    monkeypatch.setattr(controller, "begin", delayed_begin)
    coordinator = Coordinator(controller)
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: ("generation", lambda: True))
    desktop = cu.DesktopTool(tool)
    task = asyncio.create_task(sponsor.invoke("desktop", {"action": "get_clipboard"}, desktop.execute))
    try:
        await ready.wait()
        result = await sponsor.invoke("desktop", {"action": "end_episode", "generation": "stale"}, desktop.execute)
        assert result.success and result.output["state"] == "idle"
        assert tool._episode_identity is None and not indicator.states
        proceed.set()
        assert (await task).success
        assert tool.episode_status()["admission_open"]
        assert not next(iter(sponsor.episodes)).closing
    finally:
        proceed.set()
        await asyncio.gather(task, return_exceptions=True)
        await registry.close()


async def test_real_bundle_metadata_exemption_does_not_bypass_read_only_input(bundle):
    from dataclasses import replace
    cu, tool, _ = bundle
    tool._binding = replace(tool._binding, read_only=True)
    coordinator = Coordinator(cu.EpisodeController(tool))
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: ("generation", lambda: True))
    desktop = cu.DesktopTool(tool)
    try:
        result = await sponsor.invoke("desktop", {"action": "diagnostic_snapshot"}, desktop.execute)
        assert result.success and tool._episode_identity is None
        denied = await sponsor.invoke("desktop", {"action": "set_clipboard", "text": "not sent"},
                                      desktop.execute)
        assert not denied.success and "read_only" in denied.error["message"]
    finally:
        await registry.close()


async def test_real_bundle_host_stop_resume_requires_fresh_explicit_generation(bundle):
    """Run with the F1-repaired frozen bundle; never drive native input."""
    from amplifier_web.runtime_worker import Worker
    cu, tool, _ = bundle
    controller = cu.EpisodeController(tool)
    coordinator = Coordinator(controller)
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: ("generation", lambda: True))
    desktop = cu.DesktopTool(tool)
    worker = Worker()
    worker.computer_episodes = registry
    try:
        await sponsor.invoke("desktop", {"action": "get_clipboard"}, desktop.execute)
        old = tool.episode_status()["identity"]["generation"]
        captured = sponsor.capture_invocation(sponsor.capture())
        await worker.command({"op": "computer.stop"})
        await registry.join()
        prior = tool.episode_status()
        assert prior["state"] == "interrupted" and prior["workers"] == 0
        assert prior["release_confirmed"] and prior["indicator_settled"]
        implicit = await desktop.execute({"action": "get_clipboard"})
        assert not implicit.success  # Native implicit calls cannot restart.
        await worker.command({"op": "computer.resume"})
        assert not captured[1]()  # UI Resume cannot revive the old invocation.
        opened = await sponsor.invoke("desktop", {"action": "begin_episode"}, desktop.execute)
        assert opened.success and opened.output["identity"]["generation"] != old
        assert tool.episode_status()["admission_open"]
        with pytest.raises(cu.BackendError, match="stale"):
            controller.fence(old)
        assert tool.episode_status()["admission_open"]
    finally:
        await registry.close()


async def test_real_native_thread_survives_bounded_close_without_false_handback(bundle, monkeypatch):
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .02)
    cu, tool, indicator = bundle
    entered, proceed = threading.Event(), threading.Event()
    def blocked_read():
        entered.set()
        assert proceed.wait(10)
        return "inert native thread"
    tool._backend.get_clipboard = blocked_read
    controller = cu.EpisodeController(tool)
    coordinator = Coordinator(controller)
    registry = ComputerEpisodes()
    sponsor = registry.install(coordinator, lambda: ("generation", lambda: True))
    desktop = cu.DesktopTool(tool)
    task = asyncio.create_task(sponsor.invoke("ui_use", {"surface": "computer"},
        lambda arguments: sponsor.invoke("desktop", {"action": "get_clipboard"}, desktop.execute)))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=.15)
        assert done and task.cancelled()  # Only the awaiter stopped.
        receipt = await registry.close()
        assert not receipt["settled"] and registry.pending and registry.unresolved
        status = tool.episode_status()
        assert status["workers"] == 1 and not status["release_confirmed"]
        assert not status["admission_open"] and "released" not in indicator.states
        registry.resume()
        with pytest.raises(asyncio.CancelledError):
            await sponsor.invoke("desktop", {"action": "retarget", "target": "local"}, desktop.execute)
    finally:
        proceed.set()
        await asyncio.gather(task, return_exceptions=True)
        await registry.join()
        assert (await registry.close())["settled"]
    assert tool.episode_status()["workers"] == 0
    assert tool.episode_status()["state"] == "interrupted"
    assert "released" not in indicator.states


async def test_local_other_owner_refusal_has_no_host_cleanup_obligation(bundle, monkeypatch):
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .02)
    cu, parent, indicator = bundle
    parent_controller = cu.EpisodeController(parent)
    owned = await parent_controller.begin()
    child = cu.ComputerTool(parent._backend, {"_indicator_factory": lambda: type(indicator)()})
    child._episode_enabled, child._announced = True, True
    controller = cu.EpisodeController(child)
    registry = ComputerEpisodes()
    sponsor = registry.install(Coordinator(controller), lambda: ("child", lambda: True))
    try:
        with pytest.raises(cu.BackendError, match="another controller"):
            await sponsor.invoke("desktop", {"action": "get_clipboard"}, cu.DesktopTool(child).execute)
        await asyncio.gather(*tuple(registry.pending), return_exceptions=True)
        assert controller.cached_status()["identity"] is None
        assert not registry.unresolved and not sponsor.unconfirmed
        assert parent.episode_status()["identity"] == owned["identity"]
        assert parent.episode_status()["admission_open"]
        assert (await sponsor.close())["settled"]  # No end of the parent's owner.
        await parent_controller.end(owned["identity"]["generation"])
        # Fresh trusted sponsor/controller after definitive refusal can acquire.
        replacement = ComputerEpisodes()
        resumed = replacement.install(Coordinator(controller), lambda: ("resumed-child", lambda: True))
        assert (await resumed.invoke("desktop", {"action": "get_clipboard"},
                                    cu.DesktopTool(child).execute)).success
        assert (await replacement.close())["settled"]
    finally:
        parent.end_episode(interrupted=True)
        if child._episode_identity is not None:
            child.end_episode(interrupted=True)


async def test_disclosure_readiness_is_not_limited_by_cleanup_wait(bundle, monkeypatch):
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .01)
    cu, tool, _ = bundle
    entered, proceed = threading.Event(), threading.Event()
    def disclosure():
        entered.set()
        assert proceed.wait(5)
    monkeypatch.setattr(tool, "_ensure_announced", disclosure)
    registry = ComputerEpisodes()
    sponsor = registry.install(Coordinator(cu.EpisodeController(tool)), lambda: ("root", lambda: True))
    body = AsyncMock()
    task = asyncio.create_task(sponsor.invoke("computer", {}, body))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.sleep(.03)  # Three cleanup wait budgets, not a real OS dialog.
        assert not task.done() and not registry.unresolved
        assert tool._episode_identity is None
        proceed.set()
        await task
        body.assert_awaited_once()
    finally:
        proceed.set()
        await asyncio.gather(task, return_exceptions=True)
        await registry.close()


@pytest.mark.parametrize("persistent", [False, True])
async def test_real_child_local_no_acquisition_refusal_cleans_up_and_can_resume(bundle, tmp_path, persistent):
    from dataclasses import dataclass
    import importlib.util
    from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_module_tool_delegate import DelegateTool
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    cu, parent_tool, indicator = bundle
    parent_controller = cu.EpisodeController(parent_tool)
    owned = await parent_controller.begin()
    registry = ComputerEpisodes()
    child_tools = []
    class Loop:
        async def execute(self, prompt, context, providers, tools, hooks, coordinator):
            runtime = coordinator.get_capability("live.runtime")
            if runtime is not None:
                await runtime.emit("session.ready")
                _, command = await runtime.inbox.get()
                runtime.queued_inputs -= 1
            sponsor = coordinator.get_capability("web.computer_episodes")
            child_tool = child_tools[-1]
            result = await sponsor.invoke("desktop", {"action": "get_clipboard"}, cu.DesktopTool(child_tool).execute)
            if runtime is not None:
                await runtime.emit("session.closed", status="completed")
            return result.output
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
                        Bundle(name="refused-fixture", session=plan["session"], agents=plan["agents"]))
    parent = await prepared.create_session(session_id="refusal-parent")
    children = await install_children(parent, prepared, Runtime("refusal-parent"),
                                      SessionStore(tmp_path / "children"), None)
    def capture():
        async def install(coordinator):
            tool = cu.ComputerTool(parent_tool._backend, {"_indicator_factory": lambda: type(indicator)()})
            tool._episode_enabled, tool._announced = True, True
            child_tools.append(tool)
            coordinator.register_capability("computer_use.episodes", cu.EpisodeController(tool))
            registry.install(coordinator, lambda: ("child", lambda: True))
        return install
    parent.coordinator.register_capability("web.app_access.install", capture)
    await parent.coordinator.mount("tools", DelegateTool(parent.coordinator, {}), name="delegate")
    await children.install(parent, prepared)
    delegate = parent.coordinator.get("tools")["delegate"]
    try:
        failed = await delegate.execute({"agent": "worker", "instruction": "inert", "persistent": persistent})
        assert not failed.success and "another controller" in str(failed.error)
        await asyncio.gather(*tuple(registry.pending), return_exceptions=True)
        assert not registry.unresolved and not registry.cleanups
        assert set(children.sessions) == {parent.session_id}
        assert all(tool._episode_identity is None for tool in child_tools)
        assert parent_tool.episode_status()["identity"] == owned["identity"]
        assert parent_tool.episode_status()["admission_open"]
        identity = next(iter(children.rows))
        await parent_controller.end(owned["identity"]["generation"])
        resumed = await children.resume(identity, "after parent settlement", parent)
        assert resumed["output"] == "fixture clipboard"
        assert not registry.unresolved and set(children.sessions) == {parent.session_id}
    finally:
        await parent.cleanup()
        for tool in child_tools:
            if tool._episode_identity is not None:
                tool.end_episode(interrupted=True)


async def test_declined_root_disclosure_retires_only_non_acquisition_and_stays_sticky(bundle, monkeypatch):
    cu, tool, _ = bundle
    tool._announced = False
    attempts = []
    refusal = cu.AnnouncementRefused("inert disclosure decline")
    def decline(*args):
        attempts.append(True)
        raise refusal
    monkeypatch.setattr(cu, "_build_announcement", decline)
    registry = ComputerEpisodes()
    sponsor = registry.install(Coordinator(cu.EpisodeController(tool)), lambda: ("root", lambda: True))
    try:
        for _ in range(2):
            with pytest.raises(cu.AnnouncementRefused) as error:
                await sponsor.invoke("computer", {}, AsyncMock())
            assert error.value is refusal
            await asyncio.gather(*tuple(registry.pending), return_exceptions=True)
            assert not registry.unresolved and not sponsor.unconfirmed
        assert attempts == [True] and tool._episode_identity is None
        assert (await registry.close())["settled"]
    finally:
        await registry.close()


@pytest.mark.parametrize("interrupt", ["stop", "cancel"])
async def test_interrupted_disclosure_retains_actual_pending_begin_and_fences_late_token(
        bundle, monkeypatch, interrupt):
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .01)
    cu, tool, _ = bundle
    entered, proceed = threading.Event(), threading.Event()
    def disclosure():
        entered.set()
        assert proceed.wait(5)
    monkeypatch.setattr(tool, "_ensure_announced", disclosure)
    registry = ComputerEpisodes()
    sponsor = registry.install(Coordinator(cu.EpisodeController(tool)), lambda: ("root", lambda: True))
    body = AsyncMock()
    operation = asyncio.create_task(sponsor.invoke("computer", {}, body))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        if interrupt == "stop":
            registry.fence(halt=True)
        else:
            operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, .2)
        receipt = await registry.close()
        assert not receipt["settled"] and registry.pending and registry.unresolved
        assert tool._episode_identity is None
        body.assert_not_awaited()
    finally:
        proceed.set()
        await asyncio.gather(operation, return_exceptions=True)
        await registry.join()
        assert (await registry.close())["settled"]
    assert tool.episode_status()["state"] == "interrupted"
    assert not tool.episode_status()["admission_open"]


async def test_unknown_remote_begin_reply_retains_identity_and_never_replays_end(bundle, monkeypatch):
    from amplifier_module_tool_computer_use.remote_backend import RemoteBackend
    from amplifier_web import computer_episodes
    monkeypatch.setattr(computer_episodes, "SETTLEMENT_WAIT_SECONDS", .01)
    cu, local_tool, _ = bundle
    backend = RemoteBackend()
    backend.handshake = {"episode_target": "inert remote target"}
    backend.screen_geometry = local_tool._backend.screen_geometry
    backend.list_monitors = local_tool._backend.list_monitors
    backend._episode_notice = lambda *args, **kwargs: None
    recovered = threading.Event()
    calls = []
    def exchange(op, **kwargs):
        calls.append(op)
        if not recovered.is_set():
            raise cu.BackendError("inert reply lost; target adoption unknown")
        return {"identity": kwargs["identity"], "state": "interrupted", "workers": 0,
                "admission_open": False, "release_confirmed": True, "indicator_settled": True}
    backend._call = exchange  # No transport, network or native APIs exist.
    tool = cu.ComputerTool(backend, {})
    tool._episode_enabled, tool._announced = True, True
    controller = cu.EpisodeController(tool)
    registry = ComputerEpisodes()
    sponsor = registry.install(Coordinator(controller), lambda: ("remote-root", lambda: True))
    try:
        with pytest.raises(cu.BackendError, match="reply lost"):
            await sponsor.invoke("computer", {}, AsyncMock())
        receipt = await registry.close()
        assert not receipt["settled"] and registry.unresolved
        assert controller.cached_status()["identity"] == tool._episode_identity
        assert not controller.cached_status()["release_confirmed"]
        assert calls.count("episode_begin") == 1
    finally:
        recovered.set()
        await asyncio.wait_for(registry.join(), 2)
        assert (await registry.close())["settled"]
    assert calls.count("episode_begin") == 1 and calls.count("episode_end") == 1


async def test_cancellation_during_readiness_helper_cleanup_fences_acquired_episode(bundle, monkeypatch):
    from amplifier_web import computer_episodes
    cu, tool, _ = bundle
    entered = asyncio.Event()
    class AsyncioProxy:
        def __getattr__(self, name):
            return getattr(asyncio, name)
        async def gather(self, *tasks, **kwargs):
            if (len(tasks) == 1 and isinstance(tasks[0], asyncio.Task)
                    and tasks[0].get_coro().__qualname__ == "Event.wait"):
                entered.set()
                await asyncio.Event().wait()  # Cancellation exactly at helper join.
            return await asyncio.gather(*tasks, **kwargs)
    monkeypatch.setattr(computer_episodes, "asyncio", AsyncioProxy())
    registry = ComputerEpisodes()
    sponsor = registry.install(Coordinator(cu.EpisodeController(tool)), lambda: ("root", lambda: True))
    body = AsyncMock()
    operation = asyncio.create_task(sponsor.invoke("computer", {}, body))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert tool.episode_status()["admission_open"]
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert not tool.episode_status()["admission_open"]
        body.assert_not_awaited()
        assert all(episode.closing and episode.settlement is not None for episode in sponsor.episodes)
    finally:
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        await registry.close()