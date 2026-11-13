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