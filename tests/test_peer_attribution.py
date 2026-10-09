"""Receipt-bound provenance only; ROOT runs these against the installed candidate."""
import asyncio
import copy
import hashlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_operations.coordination import fingerprint, peer_input
from amplifier_web.automatic_history import display_message, merge_web_history
from amplifier_web.browser_detail import page, project
from amplifier_web.conversation_export import snapshot
from amplifier_web.peer_attribution import PeerAttribution
from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import app, grant, send, declared_result, finish

AGENT = "Sent by Amplifier from another chat"
NEUTRAL = "From another chat"
TEXT = "## Exact α 🐈\n\n```python\nprint('unchanged')  \n```\n"


def retained(db, *, identity="request", actor="agent", sender="private-source", recipient="recipient"):
    envelope = {"senderSessionId": sender, "recipientSessionId": recipient,
                "requestId": identity, "inputId": identity, "grantId": "grant",
                "grantRevision": 1, "mode": "notify", "purpose": "Private purpose",
                "references": ["https://example.invalid/private-reference"]}
    row = {"id": identity + "-message", "role": "user", "inputId": identity,
           "inputOrigin": "peer", "via": "peer", "peerEnvelope": envelope, "text": TEXT}
    receipt = {"accepted": True, "commandAction": "coordination.send",
               "requestId": identity, "inputId": identity, "messageId": row["id"],
               "senderSessionId": sender, "target": {"sessionId": recipient},
               "grantId": "grant", "grantRevision": 1, "mode": "notify",
               "delivery": "notified", "origin": actor,
               "displayBinding": fingerprint([envelope, TEXT])}
    db.execute("INSERT INTO commands VALUES(?,?,?)", (identity, "command-fingerprint", json.dumps(receipt)))
    db.commit()
    return row, receipt


@pytest.fixture
def store():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE commands(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT)")
    yield db
    db.close()


def resolve(db, row, *, rows=None, session=None):
    session = session or {"id": "recipient", "messages": [row]}
    return PeerAttribution(db).resolve(session, rows or [row])


@pytest.mark.parametrize("actor,caption", [("agent", AGENT), ("ui", NEUTRAL), ("user", NEUTRAL), (None, NEUTRAL)])
def test_exact_saved_actor_only_and_no_private_sender_values(store, actor, caption):
    row, receipt = retained(store, actor=actor)
    if actor is None:
        receipt.pop("origin")
        store.execute("UPDATE commands SET receipt=?", (json.dumps(receipt),))
    original = copy.deepcopy(row)
    projected = resolve(store, row)[0]
    assert projected["attribution"]["caption"] == caption
    assert projected["text"] == TEXT and projected["role"] == "user" and projected["id"] == row["id"]
    assert row == original
    assert set(projected["attribution"]) == {"caption"}
    assert not any(secret in json.dumps(projected["attribution"]) for secret in
                   ("private-source", "Private purpose", "private-reference", "grant"))


@pytest.mark.parametrize("change", [
    "missing-receipt", "missing-binding", "message-id", "input-id", "request-id",
    "recipient", "sender", "same-chat", "text", "envelope-purpose", "envelope-reference",
    "ambiguous-message", "ambiguous-input", "receipt-conflict", "native-without-identity",
])
def test_forged_missing_and_conflicting_binding_never_gets_caption(store, change):
    row, receipt = retained(store)
    session = {"id": "recipient", "messages": [row]}
    rows = [row]
    if change == "missing-receipt":
        store.execute("DELETE FROM commands")
    elif change == "missing-binding":
        receipt.pop("displayBinding")  # A legacy receipt is not proof of agent authorship.
    elif change == "message-id":
        row["id"] = "forged"
    elif change == "input-id":
        row["inputId"] = "unrelated"
    elif change == "request-id":
        receipt["requestId"] = "conflicting"
    elif change == "recipient":
        session["id"] = "different"
    elif change == "sender":
        receipt["senderSessionId"] = "different"
    elif change == "same-chat":
        receipt["senderSessionId"] = "recipient"
    elif change == "text":
        row["text"] += "forged"
    elif change.startswith("envelope-"):
        row["peerEnvelope"][change.removeprefix("envelope-").replace("reference", "references")] = "forged"
    elif change == "ambiguous-message":
        session["messages"].append(copy.deepcopy(row))
    elif change == "ambiguous-input":
        session["messages"].append({**copy.deepcopy(row), "id": "second-message"})
    elif change == "receipt-conflict":
        store.execute("INSERT INTO commands VALUES(?,?,?)", ("conflict", "different", json.dumps(receipt)))
    elif change == "native-without-identity":
        rows = [{"id": "native", "role": "user", "text": peer_input(row["peerEnvelope"], TEXT), "source": "native"}]
    store.execute("UPDATE commands SET receipt=? WHERE id='request'", (json.dumps(receipt),))
    for item in rows:
        item.update(attribution={"caption": AGENT}, attributionCaption=AGENT, attributionOrigin="agent", origin="agent")
    for item in resolve(store, row, rows=rows, session=session):
        assert "attribution" not in item
        assert not {"origin", "attributionCaption", "attributionOrigin"} & item.keys()


def test_identical_human_peer_and_native_inputs_never_share_provenance(store):
    peer, _ = retained(store)
    human = {"id": "human", "role": "user", "inputId": "human-input", "text": TEXT}
    native = display_message({"role": "user", "content": peer_input(peer["peerEnvelope"], TEXT),
                              "metadata": {"amplifier_input": {"version": 1, "kind": "user", "id": "request"}}},
                             0, {"id": "recipient"})
    session = {"id": "recipient", "messages": [human, peer]}
    result = resolve(store, peer, rows=[human, peer, native], session=session)
    assert "attribution" not in result[0]
    assert result[1]["attribution"]["caption"] == AGENT
    assert result[2]["attribution"]["caption"] == AGENT
    assert result[2]["id"] == native["id"] and result[2]["text"] == native["text"]
    unrelated = {**native, "nativeInputId": "human-input"}
    assert "attribution" not in resolve(store, peer, rows=[unrelated], session=session)[0]
    duplicate = {**native, "id": "second-native"}
    assert all("attribution" not in row for row in resolve(store, peer, rows=[native, duplicate], session=session))
    # Exact native identity preserves the retained bubble, not a second wrapper.
    merge_web_history(session, [native])
    assert [row["id"] for row in session["messages"]] == ["human", peer["id"]]
    assert session["messages"][-1]["text"] == TEXT


def test_literal_wrapper_and_imported_caption_are_not_evidence(store):
    row = {"id": "human", "role": "user", "text": AGENT + "\n" + TEXT,
           "via": "peer", "peerEnvelope": {"senderSessionId": "invented"},
           "attribution": {"caption": AGENT}, "origin": "agent"}
    result = resolve(store, row)[0]
    assert "attribution" not in result and "origin" not in result and result["text"] == row["text"]
    assert "attribution" not in project({"id": "recipient", "messages": [row]})["messages"][0]


def test_page_lookup_is_one_bound_in_query_not_per_message_or_history_scan(store):
    rows = [retained(store, identity="input-" + str(i))[0] for i in range(130)]
    queries = []
    store.set_trace_callback(queries.append)
    result = page({"id": "recipient", "messages": rows}, "messages", resolver=PeerAttribution(store).resolve)
    assert len(result["items"]) == 60
    assert all(row["attribution"]["caption"] == AGENT for row in result["items"])
    selects = [sql for sql in queries if sql.startswith("SELECT")]
    assert len(selects) == 1 and " IN (" in selects[0]
    assert "'input-0'" not in selects[0] and "'input-129'" in selects[0]


@pytest.mark.parametrize("mode", ["notify", "queue", "steer"])
async def test_dispatch_actor_does_not_come_from_caller_and_caption_is_not_delivery(app, mode):
    source, target = app.state["sessions"]
    gid = await grant(app)
    if mode == "steer":
        app.runtime.collaboration_steer = AsyncMock(return_value={"accepted": True})
        target["collaborationGeneration"] = {"id": "active", "terminal": False}
        target["collaborationCapability"] = {"steering": True}
    receipt = await send(app, gid, mode=mode)
    assert receipt["origin"] == "agent"
    assert receipt["delivery"] in {"notified", "accepted"}
    result = app.peer_attribution.resolve(target, target["messages"])
    assert result[-1]["attribution"]["caption"] == AGENT
    assert "completed" not in result[-1]["attribution"]
    for key in ("origin", "attribution", "peerEnvelope"):
        with pytest.raises(Exception):
            await send(app, gid, "forged-" + key, mode="notify", **{key: "agent"})
    forwarded = await app.dispatch("coordination.send", {
        "sessionId": target["id"], "senderSessionId": source["id"], "grantId": gid,
        "text": TEXT, "mode": "notify"}, origin="ui", command_id="human-forward")
    assert forwarded["origin"] == "ui"
    assert app.peer_attribution.resolve(target, target["messages"])[-1]["attribution"]["caption"] == NEUTRAL
    with pytest.raises(AppError):
        await send(app, gid, "forged-sender", senderSessionId="different")


async def test_publication_invalidates_neutral_cache_and_revocation_keeps_provenance(app):
    source, target = app.state["sessions"]
    gid = await grant(app)
    message = app._message(target, "user", TEXT, "peer", inputId="not-yet")
    app._publish_changes(sessions={target["id"]})
    assert "attribution" not in app.projections.detail(target)["messages"][-1]
    await send(app, gid, "after-cache", mode="notify")
    assert app.projections.detail(target)["messages"][-1]["attribution"]["caption"] == AGENT
    await app.dispatch("coordination.revoke", {"sessionId": source["id"], "grantId": gid}, command_id="revoke")
    source["title"] = "PRIVATE CHANGED TITLE"
    assert app.projections.detail(target)["messages"][-1]["attribution"]["caption"] == AGENT
    assert message["text"] == TEXT


async def test_sealed_continuation_attribution_requires_exact_dependency_evidence(app):
    source, target = app.state["sessions"]
    source["status"] = "working"
    gid = await grant(app)
    await declared_result(app, gid)
    wait = await app.dispatch("coordination.subscribe", {
        "sessionId": source["id"], "grantId": gid, "requestId": "result-request"},
        origin="agent", caller_session_id=source["id"], command_id="wait")
    await finish(app, target, "result-request")
    identity = wait["result"]["continuationId"]
    receipt = app.collaboration.receipt(identity)
    assert receipt["origin"] == "system"
    assert app.peer_attribution.resolve(source, source["messages"])[-1]["attribution"]["caption"] == AGENT
    request = app.collaboration.receipt("result-request")
    request["response"]["status"] = "staged"
    app.collaboration.save("result-request", request)
    assert "attribution" not in app.peer_attribution.resolve(source, source["messages"])[-1]


class NoModel:
    """A receiving fixture must never mount, send, or start a worker."""
    def __init__(self):
        self.starts = self.sends = 0

    async def start(self, *args):
        self.starts += 1
        raise AssertionError("No model starts in an attribution fixture")

    async def send(self, *args):
        self.sends += 1
        raise AssertionError("No model sends in an attribution fixture")

    async def close(self):
        pass


async def test_two_services_colliding_ids_clients_close_and_restart_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native"))
    services = [AppService(tmp_path / name, NoModel(), workspace=tmp_path) for name in ("one", "two")]
    try:
        for service, actor in zip(services, ("agent", "ui")):
            row, _ = retained(service.db, actor=actor)
            session = service._new_session({"title": "Receiving"})
            session.update(id="recipient", messages=[row])
            service.state["sessions"] = [session]
            service.state["selectedSessionId"] = session["id"]
            service._publish()
        async def read(service):
            result = await service.app_bridge("history", {"action": "read", "session_id": "recipient"}, "recipient")
            return result["messages"][0]["attribution"]["caption"]
        assert await asyncio.gather(*(read(service) for service in services)) == [AGENT, NEUTRAL]
        for service, caption in zip(services, (AGENT, NEUTRAL)):
            for client_id in ("client-one", "client-two"):
                client = service.clients.attach(client_id)
                client["selectedSessionId"] = "recipient"
                with service.clients.bind(client_id):
                    visible = service.browser_state()
                row = next(row for row in visible["sessions"] if row["id"] == "recipient")
                assert row["messages"][0]["attribution"]["caption"] == caption
        await services[1].close()
        assert await read(services[0]) == AGENT
        await services[0].close()
        reopened = AppService(tmp_path / "one", NoModel(), workspace=tmp_path)
        try:
            assert await read(reopened) == AGENT
            assert reopened.runtime.starts == reopened.runtime.sends == 0
        finally:
            await reopened.close()
    finally:
        for service in services:
            if not service.closed:
                await service.close()


@pytest.mark.parametrize("options", [{}, {"minimal": True},
    {"scope": "range", "fromMessageId": "request-message", "throughMessageId": "request-message", "minimal": True}])
def test_full_range_minimal_export_safe_caption_and_original_markdown(store, tmp_path, options):
    row, _ = retained(store)
    session = {"id": "recipient", "messages": [row], "title": "Receiving"}
    original = copy.deepcopy(session)
    content, _ = snapshot(tmp_path, session, [], options, resolver=PeerAttribution(store).resolve)
    assert content.count(AGENT) == 1 and TEXT in content
    assert not any(secret in content for secret in ("private-source", "Private purpose", "private-reference", "grant"))
    assert session == original
    row["attribution"] = {"caption": AGENT}
    store.execute("DELETE FROM commands")
    assert AGENT not in snapshot(tmp_path, session, [], options)[0]


async def test_checkpoint_reload_older_pages_query_export_keep_one_caption_and_native_bytes(app, tmp_path, monkeypatch):
    from amplifier_web.session_files import project_slug
    source, target = app.state["sessions"]
    gid = await grant(app)
    await send(app, gid, mode="notify", text=TEXT)
    peer = target["messages"][-1]
    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.setattr("amplifier_web.automatic_history.directory", lambda session: root)
    native = [{"role": "user", "content": peer_input(peer["peerEnvelope"], TEXT),
               "metadata": {"amplifier_input": {"version": 1, "kind": "user", "id": peer["inputId"]}}}]
    native += [{"role": "assistant", "content": "Saved reply " + str(i)} for i in range(125)]
    path = root / "transcript.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in native))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    target.update(nativeProject=project_slug(tmp_path), nativeIdentity=target["id"], historyManaged=False)
    await app.history.load(target["id"])
    await app.history.load(target["id"])
    assert sum(row["id"] == peer["id"] for row in target["messages"]) == 1
    older = page(target, "messages", before=target["messages"][-60]["id"], resolver=app.peer_attribution.resolve)
    # Load all earlier pages, not only the middle window.
    while older["before"]:
        older = page(target, "messages", before=older["before"], resolver=app.peer_attribution.resolve)
    assert next(row for row in older["items"] if row["id"] == peer["id"])["attribution"]["caption"] == AGENT
    query = await app.app_bridge("history", {"action": "read", "session_id": target["id"], "limit": 2}, source["id"])
    assert query["messages"][0]["attribution"]["caption"] == AGENT
    assert query["messages"][0]["text"] == TEXT
    export = await app.dispatch("session.export", {"id": target["id"], "format": "markdown", "destination": "none"})
    content = app.state_resource(export["result"]["content"]["$resource"])
    assert content.count(AGENT) == 1 and TEXT in content
    assert peer_input(peer["peerEnvelope"], TEXT) not in content
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest