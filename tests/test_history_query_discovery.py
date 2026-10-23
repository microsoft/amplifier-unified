"""Small history reads retain coverage without repeating global issue samples."""
import copy
import json

import pytest

from amplifier_web.service import AppService


@pytest.mark.parametrize("action", ["list", "search", "read"])
async def test_discovery_samples_are_explicit_and_do_not_change_history(action, tmp_path, monkeypatch):
    app = AppService(tmp_path / "app", workspace=tmp_path)
    try:
        root = app._new_session({"title": "Owned history", "workspace": str(tmp_path)})
        root["messages"] = [{"id": "message", "role": "user", "text": "synthetic needle"}]
        app.state["sessions"].append(root)
        await app.history.refresh()

        async def refreshed(*, force=False):
            assert force is False

        monkeypatch.setattr(app.history, "refresh", refreshed)
        discovery = {
            "loading": False, "error": None, "issueCount": 838,
            "reconciliation": {"phase": "complete", "completedAt": 123},
            "issues": [{"path": f"synthetic/project-{i}/session", "error": "Retained metadata unavailable " + "x" * 80}
                       for i in range(100)],
        }
        app.state["sharedHistory"] = copy.deepcopy(discovery)
        before = copy.deepcopy(app.state)
        args = {"action": action, "limit": 1}
        if action == "read":
            args["session_id"] = root["id"]
        elif action == "search":
            args["query"] = "needle"

        compact = await app.app_bridge("history", args, root["id"])
        detailed = await app.app_bridge("history", {**args, "include_discovery_issues": True}, root["id"])
        assert compact["complete"] is detailed["complete"] is False
        assert "issues" not in compact["discovery"]
        assert compact["discovery"]["issueCount"] == 838
        assert compact["discovery"]["issueSamplesAvailable"] == 100
        assert compact["discovery"]["issuesOmitted"] is True
        assert "include_discovery_issues=true" in compact["discovery"]["detailsHint"]
        assert detailed["discovery"]["issues"] == discovery["issues"]
        assert detailed["discovery"]["issuesOmitted"] is False
        assert {k: v for k, v in compact.items() if k != "discovery"} == {
            k: v for k, v in detailed.items() if k != "discovery"}
        assert len(json.dumps(compact["discovery"])) < 500
        assert len(json.dumps(detailed["discovery"])) > 10000
        assert app.state == before
        # A caller cannot mutate the shared diagnostic state through a receipt.
        detailed["discovery"]["issues"][0]["error"] = "changed"
        assert app.state["sharedHistory"] == discovery
    finally:
        await app.close()


@pytest.mark.parametrize("diagnostics,complete", [
    ({"loading": False, "error": None, "issueCount": 0, "reconciliation": {"phase": "complete"}}, True),
    ({"loading": True, "issueCount": 0}, False),
    ({"error": "Could not refresh history", "issueCount": 0}, False),
    ({"issueCount": 838, "issues": []}, False),
    ({"issueCount": 0, "reconciliation": {"phase": "scanning"}}, False),
])
async def test_coverage_and_errors_survive_without_samples(diagnostics, complete, tmp_path, monkeypatch):
    app = AppService(tmp_path / "app", workspace=tmp_path)
    try:
        root = app._new_session({"title": "Owned history", "workspace": str(tmp_path)})
        app.state["sessions"].append(root)
        await app.history.refresh()

        async def refreshed(*, force=False):
            pass

        monkeypatch.setattr(app.history, "refresh", refreshed)
        app.state["sharedHistory"] = copy.deepcopy(diagnostics)
        result = await app.app_bridge("history", {"action": "list"}, root["id"])
        assert result["complete"] is complete
        for key in ("loading", "error", "issueCount", "reconciliation"):
            assert result["discovery"][key] == diagnostics.get(key)
        assert result["discovery"]["issueSamplesAvailable"] == 0
        assert result["discovery"]["issuesOmitted"] is False
    finally:
        await app.close()
