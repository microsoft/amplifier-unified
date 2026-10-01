"""Keep empty projections cheap without weakening save-generation correctness."""
import json

from amplifier_web.browser_state import SessionIndex
from amplifier_web.session_navigation import is_top_level
from test_automatic_history import app_factory
from test_browser_state import catalog


async def test_empty_questions_are_independent_and_stale_rows_clear(app_factory):
    app = app_factory()
    rows = catalog(app, count=20, workers=2, workspaces=2)
    first = rows[0]["questions"]
    app.questions.sync()
    assert rows[0]["questions"] is first
    assert rows[0]["questions"] is not rows[1]["questions"]
    rows[1]["questions"] = [{"id": "stale"}]
    del rows[2]["questions"]
    rows[3] = {**rows[3], "questions": None}
    app.questions.sync()
    assert rows[1]["questions"] == rows[2]["questions"] == rows[3]["questions"] == []
    rows[0]["questions"].append({"id": "independent"})
    assert rows[1]["questions"] == []
    app.questions.sync()
    assert rows[0]["questions"] == []


async def test_worktree_grouping_reads_external_handoffs_each_time(app_factory, monkeypatch):
    app = app_factory()
    rows = catalog(app, count=3, workers=1, workspaces=2)
    records = [
        {"id": "a", "sessionId": rows[1]["id"]},
        {"id": "b", "sessionId": rows[0]["id"]},
        {"id": "c", "sessionId": rows[1]["id"]},
    ]
    monkeypatch.setattr(app.worktrees.git, "records", lambda: records)
    receipts = app.worktrees.receipt_dir
    receipts.mkdir(parents=True, exist_ok=True)
    path = receipts / "fixture.json"
    path.write_text(json.dumps({"id": "pending", "sessionId": rows[1]["id"], "phase": "unknown"}))
    rows[2]["worktrees"] = [{"id": "stale"}]
    rows[2]["worktreeHandoffs"] = [{"id": "stale"}]
    app.worktrees.sync()
    assert [r["id"] for r in rows[1]["worktrees"]] == ["a", "c"]
    assert rows[1]["configurationBusy"]
    assert not rows[2]["worktrees"] and not rows[2]["worktreeHandoffs"]
    empty = rows[2]["worktrees"]
    app.worktrees.sync()
    assert rows[2]["worktrees"] is empty
    path.write_text(json.dumps({"id": "pending", "sessionId": rows[1]["id"], "phase": "applied"}))
    app.worktrees.sync()
    assert rows[1]["worktreeHandoffs"][0]["phase"] == "applied"
    assert rows[1]["configurationBusy"]  # another operation may own the busy flag
    path.unlink()
    records.clear()
    app.worktrees.sync()
    assert not rows[1]["worktrees"] and not rows[1]["worktreeHandoffs"]


async def test_root_counts_and_shell_key_observe_save_without_revision(app_factory):
    app = app_factory()
    rows = catalog(app, count=100, workers=10, workspaces=4)
    index = app.projections.sessions(app.state)
    assert len(index.roots) == 100
    assert [row["id"] for row in index.roots] == [row["id"] for row in rows[:100]]
    key = app.projections.shell_key(app.state)
    for i in range(2):
        app.clients.attach(f"root-client-{i}")
        with app.clients.bind(f"root-client-{i}"):
            assert app.browser_state()["library"]["sessionCount"] == 100
            assert app.projections.sessions(app.state) is index
    revision = app.state["revision"]
    rows[-1].update(sessionKind="root", parentId=None, nativeParentId=None)
    app._save()
    assert app.state["revision"] == revision
    assert len(app.projections.sessions(app.state).roots) == 101
    assert app.browser_state()["library"]["sessionCount"] == 101
    promoted = app.projections.shell_key(app.state)
    assert promoted != key  # classification alone changes the semantic key
    from amplifier_web.browser_state import navigation
    expected = navigation({**app.state, "attention": app.projections.attention(app.state)})
    assert app.browser_state()["chatNavigation"] == expected["chatNavigation"]
    rows[-1].update(sessionKind="worker", parentId=rows[0]["id"], nativeParentId=rows[0]["nativeIdentity"])
    app._save()
    assert len(app.projections.sessions(app.state).roots) == 100
    assert app.projections.shell_key(app.state) == key
    rows[8]["title"] = "off-page change"
    app._save()
    assert app.projections.shell_key(app.state) != key


def test_root_index_counts_sequence_not_deduplicated_ids():
    rows = [{"id": "same", "sessionKind": "root"}, {"id": "same", "sessionKind": "root"},
            {"id": "worker", "sessionKind": "worker", "parentId": "same"},
            {"id": "internal", "sessionKind": "internal"}]
    index = SessionIndex({"sessions": rows})
    assert len(index.roots) == sum(is_top_level(row) for row in rows) == 2


def test_attention_workspace_aggregation_matches_unread_outcomes():
    from amplifier_web.attention import snapshot
    state = {"workspaces": [{"id": "a", "path": "/same"}, {"id": "alias", "path": "/same"},
                            {"id": "empty", "path": "/empty"}, {"id": "missing", "path": None}],
             "sessions": [
                 {"id": "a", "workspace": "/same", "completion": {"id": "one"}},
                 {"id": "b", "workspace": "/same", "error": "failed"},
                 {"id": "c", "workspace": None, "completion": {"id": "three"}},
             ]}
    first = snapshot(state)
    assert first["workspaces"] == {"a": 2, "alias": 2, "empty": 0, "missing": 1}
    item = next(row for row in first["items"] if row["id"] == "completion:a")
    state["attentionRead"] = {item["id"]: item["fingerprint"]}
    second = snapshot(state)
    assert second["workspaces"] == {"a": 1, "alias": 1, "empty": 0, "missing": 1}
    assert second["unread"] == first["unread"] - 1