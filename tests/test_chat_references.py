"""APP-004 test-first contract; unexecuted until isolated checks are admitted.

Public action spellings are implementation hypotheses, not approved architecture.
Every refusal test first proves the feature exists through a successful attach.
Missing implementation must fail, never xfail/skip or pass as a refusal.
"""
import asyncio
from copy import deepcopy

import pytest

from amplifier_web.service import AppError, AppService
from test_canvas_views import command, target as view_target, view
from test_conversation_export import exported
from test_coordination_generation import agent
from test_output_media import canonical_corpus, cold_alias_corpus
from test_service import Runtime


class NoExecution(Runtime):
    def __init__(self):
        super().__init__()
        self.other_effects = []

    async def control(self, *args, **kwargs):
        self.other_effects.append("control")

    async def approval(self, *args, **kwargs):
        self.other_effects.append("approval")

    async def prewarm(self, *args, **kwargs):
        self.other_effects.append("prewarm")


@pytest.fixture
async def reference_app(tmp_path):
    app = AppService(tmp_path / "app", workspace=tmp_path, runtime=NoExecution())
    await app.dispatch("session.create", {"title": "Source"})
    sid = app._session()["id"]
    await app.on_runtime_event("assistant.message", {
        "sessionId": sid, "text": "Open the investigation.", "generationId": "saved-generation",
    })
    mid = app._session(sid)["messages"][-1]["id"]
    await app.dispatch("session.create", {"title": "Investigation destination"})
    tid = app._session()["id"]
    await app.on_runtime_event("assistant.message", {
        "sessionId": tid, "text": "TARGET_ONLY_TRANSCRIPT_SENTINEL", "generationId": "target-generation",
    })
    await app.dispatch("session.create", {"title": "Other"})
    other = app._session()["id"]
    await app.dispatch("session.select", {"id": sid})
    app.clients.attach("one", kind="web")
    app.clients.attach("two", kind="web")
    try:
        yield app, {"sessionId": sid, "messageId": mid, "targetSessionId": tid}, other
    finally:
        for queue in list(app.queue_clients):
            app.unsubscribe(queue)
        await app.close()


async def attach(app, args, **kwargs):
    catalog = {row["name"]: row["inputSchema"] for row in app.get_actions()}
    assert "outputs.attachChatReference" in catalog, "Typed reference attachment not implemented"
    assert "chatReference" in catalog["session.select"]["properties"]
    result = await app.dispatch("outputs.attachChatReference", args, **kwargs)
    assert result["accepted"] is True
    record = result["result"]
    assert record["id"] and record["version"] >= 1 and record["revision"] >= 1
    return record


def click(args, record):
    return {"id": args["targetSessionId"], "chatReference": {
        "id": record["id"], "version": record["version"],
        "sourceSessionId": args["sessionId"], "messageId": args["messageId"],
        "expectedRevision": record["revision"],
    }}


def outputs(app):
    return list(app.db.execute("SELECT id,value FROM output_records ORDER BY id"))


def effects(app):
    return deepcopy([app.runtime.started, app.runtime.sent,
                     app.runtime.stopped, app.runtime.other_effects])


async def test_reference_attach_is_idempotent_exact_and_never_navigates(reference_app):
    app, args, other = reference_app
    before = deepcopy(app.clients.records)
    record = await attach(app, args, command_id="reference-once")
    assert app.clients.records == before
    duplicate = await attach(app, args, command_id="reference-once")
    assert duplicate["id"] == record["id"] and duplicate["duplicate"]
    saved = outputs(app)
    with pytest.raises(AppError):
        await app.dispatch("outputs.attachChatReference",
                           {**args, "targetSessionId": other}, command_id="reference-once")
    assert outputs(app) == saved
    assert effects(app) == [[], [], [], []]


@pytest.mark.parametrize("defect", ["user", "observation", "missing", "foreign", "action", "title"])
async def test_reference_rejects_nonassistant_or_executable_authoring(reference_app, defect):
    app, args, other = reference_app
    await attach(app, args)
    row = next(m for m in app._session(args["sessionId"])["messages"] if m["id"] == args["messageId"])
    invalid = deepcopy(args)
    if defect == "user":
        row["role"] = "user"
    elif defect == "observation":
        row["observation"] = True
    elif defect == "missing":
        invalid["messageId"] = "missing-assistant"
    elif defect == "foreign":
        invalid["sessionId"] = other
    elif defect == "action":
        invalid["action"] = "approval.respond"
    else:
        invalid["title"] = "<img src=x onerror=alert(1)>"
    before = outputs(app)
    with pytest.raises(AppError):
        await app.dispatch("outputs.attachChatReference", invalid)
    assert outputs(app) == before


@pytest.mark.parametrize("defect", ["id", "version", "source", "message", "revision", "target", "unlinked", "source-role"])
async def test_click_revalidates_exact_tuple_before_navigation(reference_app, defect):
    app, args, other = reference_app
    record = await attach(app, args)
    valid = click(args, record)
    assert (await command(app, "session.select", valid))["accepted"]
    await command(app, "session.select", {"id": args["sessionId"]})
    invalid = deepcopy(valid)
    if defect == "target":
        invalid["id"] = other
    elif defect == "unlinked":
        await app.dispatch("outputs.unlink", {"sessionId": args["sessionId"], "id": record["id"],
                                              "expectedRevision": record["revision"]})
    elif defect == "source-role":
        row = next(m for m in app._session(args["sessionId"])["messages"] if m["id"] == args["messageId"])
        row["role"] = "user"
    else:
        key = {"source": "sourceSessionId", "message": "messageId",
               "revision": "expectedRevision"}.get(defect, defect)
        invalid["chatReference"][key] = 999 if key in {"version", "expectedRevision"} else "not-the-saved-identity"
    before = deepcopy(app.clients.records)
    with pytest.raises(AppError):
        await command(app, "session.select", invalid)
    assert app.clients.records == before
    assert effects(app) == [[], [], [], []]


@pytest.mark.parametrize("origin", ["agent", "unbound", "different-chat"])
async def test_click_requires_authenticated_source_browser(reference_app, origin):
    app, args, other = reference_app
    record = await attach(app, args)
    valid = click(args, record)
    assert (await command(app, "session.select", valid))["accepted"]
    await command(app, "session.select", {"id": args["sessionId"]})
    if origin == "different-chat":
        await command(app, "session.select", {"id": other})
    before = deepcopy(app.clients.records)
    with pytest.raises(AppError):
        if origin == "unbound":
            await app.dispatch("session.select", valid)
        else:
            await command(app, "session.select", valid, origin="agent" if origin == "agent" else "ui",
                          caller_session_id=args["sessionId"] if origin == "agent" else None)
    assert app.clients.records == before


async def test_click_keeps_peer_input_and_has_no_execution_effects(reference_app, monkeypatch):
    app, args, other = reference_app
    warmups = []
    async def unexpected_warmup(*values, **kwargs):
        warmups.append(values)
    monkeypatch.setattr(app.warmup, "schedule", unexpected_warmup)
    for client, draft in [("one", "Source draft"), ("two", "Independent peer draft")]:
        await command(app, "session.select", {"id": args["sessionId"]}, client=client)
        await command(app, "view.update", {"patch": {"draft": draft}}, client=client)
        await command(app, "attachment.add", {"name": client + ".txt", "base64": "ZXhhY3QgYnl0ZXM="}, client=client)
    peer = deepcopy(app.clients.records["two"])
    own = deepcopy(app.clients.records["one"])
    histories = {row["id"]: deepcopy({key: row.get(key) for key in ("messages", "execution", "approvals")})
                 for row in app._state["sessions"]}
    record = await attach(app, args)
    result = await command(app, "session.select", click(args, record))
    assert result["accepted"] and result["effects"] == []
    assert app.clients.records["one"]["selectedSessionId"] == args["targetSessionId"]
    assert app.clients.records["one"]["drafts"] == own["drafts"]
    assert app.clients.records["one"]["attachments"] == own["attachments"]
    assert app.clients.records["two"] == peer
    async with asyncio.timeout(5):
        while app.tasks:
            await asyncio.gather(*list(app.tasks))
    assert effects(app) == [[], [], [], []] and warmups == []
    assert {row["id"]: {key: row.get(key) for key in ("messages", "execution", "approvals")}
            for row in app._state["sessions"]} == histories


async def test_reference_keeps_real_dirty_view_guard(reference_app):
    app, args, _ = reference_app
    record = await attach(app, args)
    await command(app, "canvas.show", {"kind": "markdown", "content": "# Retained"})
    current = view_target(view(app))
    await command(app, "canvas.views.dirty", {**current, "dirty": True})
    before = deepcopy(app.clients.records)
    with pytest.raises(AppError) as error:
        await command(app, "session.select", click(args, record))
    assert error.value.code == "canvas_view_dirty"
    assert app.clients.records == before
    await command(app, "canvas.views.dirty", {**current, "dirty": False})
    assert (await command(app, "session.select", click(args, record)))["accepted"]


async def test_agent_attach_needs_active_root_transport_not_caller_id(reference_app):
    app, args, _ = reference_app
    await attach(app, args)
    source = app._session(args["sessionId"])
    source["runtimeSessionId"] = "reference-runtime-root"
    source["collaborationGeneration"] = {"id": "active-generation", "inputIds": ["input-one"], "terminal": False}
    good = await agent(app, source, "outputs.attachChatReference", args, "agent-attach",
                       _runtimeSessionId="reference-runtime-root")
    assert good["accepted"]
    for index, transport in enumerate([
        {"_runtimeSessionId": "child-runtime"}, {"_generationId": "stale-generation"},
    ]):
        with pytest.raises(AppError):
            await agent(app, source, "outputs.attachChatReference", args, "agent-bad-" + str(index),
                        **{"_runtimeSessionId": "reference-runtime-root", **transport})
    with pytest.raises(AppError):
        await app.dispatch("outputs.attachChatReference", args, origin="agent", caller_session_id=source["id"])
    source["collaborationGeneration"]["terminal"] = True
    with pytest.raises(AppError):
        await agent(app, source, "outputs.attachChatReference", args, "terminal-attach",
                    _runtimeSessionId="reference-runtime-root")


@pytest.mark.parametrize("source_kind", ["old-canonical", "retained-alias", "cold-alias"])
async def test_saved_canonical_and_cold_source_without_transcript_rewrite(reference_app, monkeypatch, source_kind):
    app, args, _ = reference_app
    await attach(app, args)  # Witness before constructing canonical boundary cases.
    if source_kind == "old-canonical":
        source, _, _, directory, message = canonical_corpus(app)
    else:
        source, _, _, directory, _ = cold_alias_corpus(app, native_message_id=False)
        message = "saved-web-assistant"
    raw = (directory / "transcript.jsonl").read_bytes()
    if source_kind == "cold-alias":
        app._state["selectedSessionId"] = None
        for client in app.clients.records.values():
            client["selectedSessionId"] = None
        monkeypatch.setattr(app.cold_display, "RECENT_LIMIT", 0)
        assert source["id"] in app.cold_display.retire(force=True)
        assert not dict.__contains__(source, "messages")
    markers = deepcopy(dict.get(source, "_coldFields"))
    await attach(app, {**args, "messageId": message}, include_state=False)
    assert (directory / "transcript.jsonl").read_bytes() == raw
    if source_kind == "cold-alias":
        assert not dict.__contains__(source, "messages")
        assert dict.get(source, "_coldFields") == markers


async def test_reference_survives_reopen_and_has_inert_export(reference_app):
    app, args, _ = reference_app
    export_args = {"id": args["sessionId"], "format": "markdown", "destination": "none",
                   "includeReferenceIds": False}
    old_export = await app.dispatch("session.export", export_args)
    old_bytes = exported(app, old_export)
    record = await attach(app, args)
    fresh = await app.dispatch("session.export", export_args)
    text = exported(app, fresh)
    assert "Open the investigation." in text
    assert "Chat reference:" in text and "Investigation destination" in text
    assert "TARGET_ONLY_TRANSCRIPT_SENTINEL" not in text
    assert "<button" not in text and "javascript:" not in text
    assert args["targetSessionId"] not in text
    assert exported(app, old_export) == old_bytes
    home, workspace = app.data_dir, app._session(args["sessionId"])["workspace"]
    await app.close()
    reopened = AppService(home, workspace=workspace, runtime=NoExecution())
    try:
        reopened.clients.attach("one-reopened", resume="one", kind="web")
        saved = await reopened.dispatch("outputs.read", {"sessionId": args["sessionId"], "id": record["id"]})
        assert saved["result"]["id"] == record["id"]
        assert saved["result"]["version"] == record["version"]
        assert saved["result"]["revision"] == record["revision"]
        await command(reopened, "session.select", {"id": args["sessionId"]}, client="one-reopened")
        assert (await command(reopened, "session.select", click(args, record), client="one-reopened"))["accepted"]
        assert effects(reopened) == [[], [], [], []]
    finally:
        await reopened.close()


@pytest.mark.parametrize("defect", ["missing", "worker", "internal"])
async def test_reference_target_must_be_an_available_ordinary_root(reference_app, defect):
    app, args, other = reference_app
    await attach(app, args)
    bad = {**args, "targetSessionId": "missing-target" if defect == "missing" else other}
    if defect != "missing":
        app._session(other)["sessionKind"] = defect
    before = outputs(app)
    with pytest.raises(AppError):
        await app.dispatch("outputs.attachChatReference", bad)
    assert outputs(app) == before
    assert effects(app) == [[], [], [], []]


async def test_renamed_archived_target_opens_exact_id_without_restore(reference_app):
    app, args, _ = reference_app
    record = await attach(app, args)
    await app.dispatch("session.rename", {"id": args["targetSessionId"], "title": "Renamed destination"})
    await app.dispatch("session.archive", {"id": args["targetSessionId"]})
    archived = deepcopy(app.state["conversationOrganization"]["archived"])
    assert args["targetSessionId"] in archived
    assert (await command(app, "session.select", click(args, record)))["accepted"]
    assert app.clients.records["one"]["selectedSessionId"] == args["targetSessionId"]
    assert app.state["conversationOrganization"]["archived"] == archived
    assert effects(app) == [[], [], [], []]