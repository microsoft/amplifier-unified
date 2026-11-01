"""Browser history demand must not rewrite saved task authority or current work."""
from copy import deepcopy
import json

import pytest

from amplifier_web.browser_demand import project
from amplifier_web.task_continuity import _read
from test_live_clients import live, command, snapshot


def task_state(sid="chat"):
    task = {"id": "task-current", "revision": 3, "appliedRevision": 2,
            "status": "active", "continuationEpoch": 7,
            "objective": "Current objective " + "o" * 3000,
            "originalObjective": "Original objective",
            "constraints": ["Retain current constraints"], "blockedReason": None,
            "corrections": [{"revision": 2, "at": 1, "origin": "human",
                             "text": "Current correction " + "c" * 5000}],
            "completionEvidence": ["Exact evidence"], "questionIds": ["question"],
            "operationIds": ["operation"], "artifactRefs": ["artifact"],
            "maxTurns": 10, "goalState": {"native_state": "g" * 200000},
            "changes": [{"revision": 3, "at": 2, "origin": "agent",
                         "fields": {"goalState": {"native_state": "h" * 200000}}}]}
    control = {"task": deepcopy(task), "history": [{"id": "old-task", "body": "p" * 200000}],
               "goal": {"task_id": task["id"], "task_revision": 3, "cap": 10},
               "continuity": {"supported": True, "status": "ready"},
               "continuationSupported": True}
    state = {"selectedSessionId": sid, "view": {"panel": "chat-work"},
             "sessions": [{"id": sid, "task": task}],
             "runtimeControl": {sid: {"task.get": control, "budget.get": {"maxIterations": 10}}}}
    return state


@pytest.mark.parametrize("panel", ["runtime", "session-details", "chat-work", "chat-models",
                                   "chat-context", "chat-computer"])
def test_task_history_is_not_repeated_in_browser_runtime_panels(panel):
    state = task_state()
    state["view"]["panel"] = panel
    original = deepcopy(state)
    result = project(dict(state))
    assert state == original
    current = state["sessions"][0]["task"]
    for projected in (result["sessions"][0]["task"], result["runtimeControl"]["chat"]["task.get"]["task"]):
        assert not {"changes", "goalState", "originalObjective"} & projected.keys()
        assert {key: projected[key] for key in current if key not in {"changes", "goalState", "originalObjective"}} == {
            key: value for key, value in current.items() if key not in {"changes", "goalState", "originalObjective"}}
        assert projected["changeCount"] == 1
        assert projected["historyRef"] == {"action": "task.history", "args": {"sessionId": "chat", "taskId": current["id"]}}
        assert projected["originalObjectiveAvailable"] is True
        assert projected["originalObjectiveRef"] == {"action": "task.detail", "args": {
            "sessionId": "chat", "taskId": current["id"], "expectedRevision": 3, "field": "originalObjective"}}
    control = result["runtimeControl"]["chat"]["task.get"]
    assert "history" not in control and control["historyCount"] == 1
    assert control["historyRef"] == {"action": "task.history", "args": {"sessionId": "chat"}}
    for key in ("goal", "continuity", "continuationSupported"):
        assert control[key] == state["runtimeControl"]["chat"]["task.get"][key]
    assert result["runtimeControl"]["chat"]["budget.get"] == {"maxIterations": 10}
    assert "detailRequired" not in control, "Current fields stay complete; do not disable task completion"
    assert len(json.dumps(result)) < len(json.dumps(state)) / 20


def test_browser_history_handles_read_exact_canonical_records():
    state = task_state()
    original = deepcopy(state)
    task = state["sessions"][0]["task"]
    projected = project(dict(state))["sessions"][0]["task"]
    def read(ref):
        return _read(ref["action"], ref["args"], task=task, history=[], receipts={}, sid="chat")
    history = read(projected["historyRef"])
    assert history["total"] == 2
    change = next(row for row in history["items"] if row["kind"] == "change")
    parts, offset = [], 0
    while True:
        ref = deepcopy(change["detailRef"])
        ref["args"]["offset"] = offset
        page = read(ref)
        parts.append(page["text"])
        if not page["truncated"]:
            break
        offset = page["nextOffset"]
    assert json.loads("".join(parts)) == task["changes"][0]["fields"]
    assert read(projected["originalObjectiveRef"])["text"] == task["originalObjective"]
    assert state == original


def test_browser_task_copies_keep_their_own_revision_and_do_not_invent_coverage():
    state = task_state()
    other = state["runtimeControl"]["chat"]["task.get"]["task"]
    other.update(id="other-task", revision=9, objective="Independently observed current objective")
    result = project(dict(state))
    first, second = result["sessions"][0]["task"], result["runtimeControl"]["chat"]["task.get"]["task"]
    assert first["originalObjectiveRef"]["args"]["expectedRevision"] == 3
    assert second["originalObjectiveRef"]["args"]["expectedRevision"] == 9
    assert second["historyRef"]["args"]["taskId"] == "other-task"
    for missing in (None, {}, {"status": "blocked", "changes": None}):
        state["sessions"][0]["task"] = missing
        task = project(dict(state))["sessions"][0]["task"]
        if task is not None:
            assert "historyRef" not in task and "changeCount" not in task


async def test_client_task_projection_survives_updates_and_reattach_without_changing_authority(live):
    service, first, _ = live
    data = task_state(first)
    task, control = data["sessions"][0]["task"], data["runtimeControl"][first]
    service._session(first)["task"] = task
    service._state.setdefault("runtimeControl", {})[first] = control
    before = deepcopy(data)
    await command(service, "browser-a", "session.select", {"id": first})
    await command(service, "browser-a", "view.update", {"patch": {"panel": "chat-work"}})
    await command(service, "browser-b", "session.select", {"id": first})
    await command(service, "browser-b", "view.update", {"patch": {"panel": None}})
    state = snapshot(service, "browser-a")
    assert "changes" not in next(row for row in state["sessions"] if row["id"] == first)["task"]
    assert "history" not in state["runtimeControl"][first]["task.get"]
    assert "task.get" not in snapshot(service, "browser-b")["runtimeControl"][first]
    with service.clients.bind("browser-a"):
        full = service.get_state()
        assert full["runtimeControl"][first] == control
        assert next(row for row in full["sessions"] if row["id"] == first)["task"] == task
    assert task == before["sessions"][0]["task"]
    assert control == before["runtimeControl"][first]
    task.update(objective="New current objective", revision=4, appliedRevision=4, status="blocked", blockedReason="Current blocker")
    task["changes"].append({"revision": 4, "fields": {"objective": task["objective"]}})
    control["task.get"]["task"] = deepcopy(task)
    service._publish_changes(sessions={first})
    service.clients.attach("browser-a")
    reconnected = snapshot(service, "browser-a")
    displayed = reconnected["runtimeControl"][first]["task.get"]["task"]
    assert displayed["objective"] == "New current objective"
    assert displayed["revision"] == displayed["appliedRevision"] == 4
    assert displayed["status"] == "blocked" and displayed["blockedReason"] == "Current blocker"
    assert displayed["changeCount"] == 2
    assert task["changes"] == control["task.get"]["task"]["changes"]
