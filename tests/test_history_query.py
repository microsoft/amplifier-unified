import copy

import pytest
from amplifier_web.service import AppService
from amplifier_web.runtime import normalize_event


@pytest.mark.asyncio
async def test_search_and_read_are_passive_scoped_and_paginated(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "amplifier"))
    app = AppService(tmp_path / "app", workspace=tmp_path)
    try:
        root = app._new_session({"title": "Earlier", "workspace": str(tmp_path)})
        app.state["sessions"].append(root)
        root["messages"] = [{"id": "message", "role": "user", "text": "decision needle " + "x" * 6000}]
        root["title"] = "Earlier decision"
        other = {**copy.deepcopy(root), "id": "other", "workspace": "/another", "title": "Other workspace"}
        app.state["sessions"].append(other)
        await app.history.refresh()  # Discovery may update the derived index, never the selected chat.
        before = copy.deepcopy(app.state)
        result = await app.app_bridge("history", {"action": "search", "query": "needle"}, root["id"])
        assert [row["id"] for row in result["items"]] == [root["id"]]
        assert result["items"][0]["matches"][0]["message_id"] == "message"
        result = await app.app_bridge("history", {"action": "list", "scope": "all", "limit": 1}, root["id"])
        assert result["next_offset"] == 1
        result = await app.app_bridge("history", {"action": "read", "session_id": root["id"], "text_limit": 100}, root["id"])
        assert result["messages"][0]["next_text_offset"] == 100
        with pytest.raises(ValueError, match="scope"):
            await app.app_bridge("history", {"action": "read", "session_id": "other"}, root["id"])
        assert app.state['selectedSessionId'] == before['selectedSessionId']
        assert app.state['selectedWorkspaceId'] == before['selectedWorkspaceId']
        assert app.state['view'] == before['view']
        assert [row.get('messages') for row in app.state['sessions'] if row['id'] in {s['id'] for s in before['sessions']}] == [row.get('messages') for row in before['sessions']]
    finally:
        await app.close()


def test_stream_and_compaction_event_bridge():
    kind, value = normalize_event({"type": "assistant.delta", "text": "visible", "request_id": "r", "secret": "private"}, "s")
    assert kind == "assistant.delta"
    assert value == {"sessionId": "s", "text": "visible", "requestId": "r", "blockIndex": None}
    kind, value = normalize_event({"type": "runtime.activity", "phase": "compacting", "detail": "Making room"}, "s")
    assert kind == "runtime.status" and value["phase"] == "compacting"
