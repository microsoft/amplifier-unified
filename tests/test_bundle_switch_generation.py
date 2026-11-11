"""Immediate switching must render its guard with the selected candidate graph."""
from copy import deepcopy
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest
import yaml


@pytest.mark.parametrize("recovering", [False, True])
@pytest.mark.parametrize("reviewed", [False, True])
@pytest.mark.parametrize("fault", [None, "source", "configuration", "placement", "stop"])
async def test_immediate_switch_qualifies_candidate_before_guard(tmp_path, monkeypatch, recovering, reviewed, fault):
    from amplifier_web import bundle_actions, runtime_profiles, updates
    from amplifier_web.bundle_selection import begin_replacement, inspect_replacement
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.management import Management
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.service import AppService

    home, shared = tmp_path / "app", tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    monkeypatch.setenv("AMPLIFIER_HOME", str(shared))
    monkeypatch.setenv("AMPLIFIER_UNIFIED_RELEASE", "a" * 32)
    monkeypatch.setenv("AMPLIFIER_SOURCE_STORE", str(tmp_path / "source-store"))
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(yaml.safe_dump({
        "bundle": {"name": "candidate", "version": SNAPSHOT_VERSION},
        "session": {"orchestrator": {"module": "loop-live"}, "context": {"module": "context-simple"}},
        "providers": [{"module": "provider-fixture", "instance_id": "fixture"}],
        "tools": [{"module": "tool-fixture", "source": "${AMPLIFIER_SOURCE_STORE}/fixture",
                   "config": {"generation": "${AMPLIFIER_UNIFIED_RELEASE}"}}],
    }))
    monkeypatch.setattr(updates, "active_release", lambda home: {"current": "a" * 32})
    app = source = None
    async def qualified(*args):
        if fault == "source":
            plan = yaml.safe_load(candidate.read_text())
            plan["tools"][0]["config"]["changed"] = True
            candidate.write_text(yaml.safe_dump(plan))
        elif fault == "configuration":
            app.state["configurationRevision"] = app.state.get("configurationRevision", 0) + 1
        elif fault == "placement":
            source["executionRevision"] = source.get("executionRevision", 0) + 1
        elif fault == "stop":
            source["interruptionRevision"] = source.get("interruptionRevision", 0) + 1
        return "b" * 32
    ensure = AsyncMock(side_effect=qualified)
    monkeypatch.setattr(runtime_profiles, "ensure", ensure)
    monkeypatch.setattr("amplifier_web.generation_leases.acquire", lambda *a: None)
    monkeypatch.setattr(RuntimeManager, "_command", lambda *a, **k: [sys.executable, "worker.py"])
    class Runtime:
        def __init__(self):
            self.stops = []
        async def control(self, sid, operation, args):
            if operation in {"bundle.prepare", "bundle.preview"}:
                checked, _ = await inspect_replacement(home, tmp_path, sid, str(candidate))
                if operation == "bundle.prepare":
                    return {"replacement": {**checked, "resetModel": False}}
                return {**checked, "previewId": "mounted-preview"}
            return {"plan": {}} if operation == "configuration.inspect" else {"providers": []}
        async def stop(self, sid, *, reason="stopped"):
            self.stops.append(reason)
        async def close(self):
            pass
    runtime = Runtime()
    app = AppService(home, workspace=tmp_path, runtime=runtime)
    app.management = Management(app)
    await app.dispatch("session.create", {"bundle": "old"})
    source = app._session()
    source["selection"] = None
    if recovering:
        source["errorType"] = "RuntimeStartupError"
    mounted = []
    async def ensure_runtime(session, *, bundle_replacement=None, restart_guard=None):
        if restart_guard:
            restart_guard()
        if bundle_replacement:
            # Real candidate guard, rendered in the graph selected at startup.
            with monkeypatch.context() as environment:
                environment.setenv("AMPLIFIER_UNIFIED_RELEASE", "b" * 32)
                journal, _ = await begin_replacement(home, tmp_path, {
                    "id": session["id"], "bundleReplacement": bundle_replacement,
                    "selection": session.get("selection"),
                })
                journal.restore()
            mounted.append(deepcopy(bundle_replacement))
    monkeypatch.setattr(app.management, "ensure_runtime", ensure_runtime)
    try:
        args = {"sessionId": source["id"], "bundle": str(candidate)}
        if reviewed:
            await bundle_actions.preview_saved(app.management, args)
            args["previewId"] = source["bundlePreview"]["previewId"]
        if fault:
            with pytest.raises(ValueError, match="changed|stopped"):
                await bundle_actions.perform(app.management, "bundle.switch", args)
            assert not mounted and not runtime.stops
            assert source["bundle"] == "old"
            return
        await bundle_actions.perform(app.management, "bundle.switch", args)
        assert len(mounted) == 1 and mounted[0]["bundle"] == str(candidate)
        assert ensure.await_count == 1
        assert source["bundle"] == str(candidate)
        assert not source["configurationBusy"]
    finally:
        await app.close()