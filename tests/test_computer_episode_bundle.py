"""Opt-in frozen-bundle pairing, entirely inert (no OS backend or model calls).

Set COMPUTER_USE_TEST_SOURCE to an exact archived tool-computer-use directory.
This is candidate-graph qualification, not installed/native platform acceptance.
"""
import asyncio
import importlib
import os
from pathlib import Path
import threading

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