"""APP-004 behavior contract; original29 proved missing-feature RED in hosted CI.

Public action spellings are implementation hypotheses, not approved architecture.
Every refusal test first proves the feature exists through a successful attach.
Missing implementation must fail, never xfail/skip or pass as a refusal.
"""
import asyncio
import json
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


def publication_state(app):
    """Promised state, plus complete client records (including every cache)."""
    return deepcopy({
        "clients": app.clients.records,
        "shared": {key: app._state.get(key) for key in
                   ("selectedSessionId", "selectedWorkspaceId", "view", "canvas", "canvasArtifacts")},
        "sessions": {row["id"]: {key: row.get(key) for key in
                    ("messages", "execution", "approvals", "draft", "draftAttachments")}
                     for row in app._state["sessions"]},
        "effects": effects(app),
    })


def expected_after_client_save(before, dirty):
    """Only the exact existing save-derived cache entry, from PRE-action bytes.

    This is not normalization of the observed result. Any changed selection,
    input, editor, visible Canvas, other cached chat or extra field still fails.
    """
    expected = deepcopy(before)
    for identity in dirty:
        source = before["clients"][identity]
        sid = source.get("selectedSessionId")
        if sid:
            canvas = source.get("canvas", {})
            expected["clients"][identity].setdefault("chatCanvas", {})[sid] = {
                "canvas": {key: deepcopy(canvas[key]) for key in
                           ("id", "open", "selectedVersion", "view") if key in canvas},
                "view": {key: deepcopy(value) for key, value in source["view"].items()
                         if key.startswith("canvas")},
            }
    return expected


def assert_publication_unchanged(before, after, dirty=()):
    assert after == expected_after_client_save(before, dirty)


async def test_reference_attach_is_idempotent_exact_and_never_navigates(reference_app):
    app, args, other = reference_app
    before = publication_state(app)
    assert all("chatCanvas" not in row for row in before["clients"].values())
    dirty = set(app.clients.dirty)
    expected = expected_after_client_save(before, dirty)
    record = await attach(app, args, command_id="reference-once")
    assert_publication_unchanged(before, publication_state(app), dirty)
    duplicate = await attach(app, args, command_id="reference-once")
    assert duplicate["id"] == record["id"] and duplicate["duplicate"]
    assert publication_state(app) == expected
    saved = outputs(app)
    with pytest.raises(AppError):
        await app.dispatch("outputs.attachChatReference",
                           {**args, "targetSessionId": other}, command_id="reference-once")
    assert outputs(app) == saved
    assert publication_state(app) == expected
    assert effects(app) == [[], [], [], []]


async def test_generic_output_save_has_the_same_exact_cache_derivation(reference_app, monkeypatch):
    app, args, _ = reference_app
    warmups = []
    async def unexpected_warmup(*values, **kwargs):
        warmups.append((values, kwargs))
    monkeypatch.setattr(app.warmup, "schedule", unexpected_warmup)
    for identity, row in app.clients.records.items():
        # Explicit server-held input/editor state, not a claim about unsaved DOM.
        row["drafts"][args["sessionId"]] = f"Private {identity} draft"
        row["attachments"][args["sessionId"]] = [{"id": identity, "name": f"{identity}.txt"}]
        row["view"].update(draft=f"Private {identity} draft",
                           messageEdit={"sessionId": args["sessionId"], "messageId": args["messageId"],
                                        "text": f"Unsent {identity} edit"},
                           canvasDraft=f"Unsent {identity} Canvas edit",
                           providerEditor={"id": f"Unsaved {identity} configuration"})
        row["chatCanvas"] = {"other-cached-chat": {"canvas": {"id": "retained", "open": True}, "view": {}}}
    before, dirty = publication_state(app), set(app.clients.dirty)
    # Ordinary reference publication shares _save/clients.save. No fetch, model
    # or APP-004 action is needed to reproduce the exact derived cache entry.
    result = await app.dispatch("outputs.attach", {"sessionId": args["sessionId"],
        "kind": "external_document", "title": "Generic reference",
        "url": "https://example.test/reference"})
    assert result["accepted"]
    assert_publication_unchanged(before, publication_state(app), dirty)
    assert args["sessionId"] in app.clients.records["one"]["chatCanvas"]
    initialized = publication_state(app)
    await attach(app, args)
    async with asyncio.timeout(5):
        while app.tasks:
            await asyncio.gather(*list(app.tasks))
    assert_publication_unchanged(initialized, publication_state(app))
    assert warmups == [] and effects(app) == [[], [], [], []]


async def test_registered_browser_initializes_cache_before_reference_publication(tmp_path, authenticated_client):
    from amplifier_web.server import create_app
    web = await create_app(tmp_path / "web", workspace=tmp_path, runtime=NoExecution(),
                           voice=False, background_updates=False, preload_providers=False)
    app = web["service"]
    await app.dispatch("session.create", {"title": "Registered source"})
    sid = app._session()["id"]
    await app.on_runtime_event("assistant.message", {"sessionId": sid, "text": "Saved source"})
    mid = app._session(sid)["messages"][-1]["id"]
    await app.dispatch("session.create", {"title": "Registered destination"})
    tid = app._session()["id"]
    await app.dispatch("session.select", {"id": sid})
    client = await authenticated_client(web)
    for identity in ("registered-one", "registered-two"):
        response = await client.post("/api/clients/attach",
            json={"clientId": identity, "kind": "web", "protocolVersion": 1},
            headers={"X-Amplifier-Client": identity, "X-Amplifier-State-Transport": "delta-v1"})
        assert response.status == 200
        assert "state" not in await response.json()  # Still saved before response.
        row = app.clients.records[identity]
        assert row["chatCanvas"][sid] == {
            "canvas": {key: deepcopy(row["canvas"][key]) for key in
                       ("id", "open", "selectedVersion", "view") if key in row["canvas"]},
            "view": {key: deepcopy(value) for key, value in row["view"].items()
                     if key.startswith("canvas")},
        }
    before = publication_state(app)
    response = await client.post("/api/actions", json={"action": "outputs.attachChatReference",
        "args": {"sessionId": sid, "messageId": mid, "targetSessionId": tid}},
        headers={"X-Amplifier-Client": "registered-one", "X-Amplifier-State-Transport": "delta-v1"})
    assert response.status == 200 and (await response.json())["accepted"]
    assert_publication_unchanged(before, publication_state(app))


@pytest.mark.parametrize("mutation", ["selection", "draft", "canvas"])
async def test_publication_oracle_rejects_real_semantic_mutations(reference_app, mutation):
    app, args, _ = reference_app
    await attach(app, args)  # Actual positive feature witness.
    before, dirty = publication_state(app), set(app.clients.dirty)
    if mutation == "selection":
        await command(app, "session.select", {"id": args["targetSessionId"]})
    elif mutation == "draft":
        await command(app, "view.update", {"patch": {"draft": "Unexpected changed input"}})
    else:
        await command(app, "canvas.show", {"kind": "text", "content": "Unexpected changed Canvas"})
    with pytest.raises(AssertionError):
        assert_publication_unchanged(before, publication_state(app), dirty)


async def test_publication_oracle_rejects_private_state_and_cache_corruption(reference_app):
    app, args, _ = reference_app
    before, dirty = publication_state(app), set(app.clients.dirty)
    good = expected_after_client_save(before, dirty)
    assert_publication_unchanged(before, good, dirty)
    # Detached negative controls prove the allowance cannot mask cache/input or
    # execution corruption; the three tests above exercise real host mutations.
    mutations = [
        lambda value: value["clients"]["two"]["drafts"].update({args["sessionId"]: "Lost peer draft"}),
        lambda value: value["clients"]["one"]["attachments"].update({args["sessionId"]: [{"id": "unexpected"}]}),
        lambda value: value["clients"]["one"]["view"].update(messageEdit={"text": "Wrong edit"}),
        lambda value: value["clients"]["one"]["view"].update(providerEditor={"id": "Wrong settings"}),
        lambda value: value["clients"]["one"]["view"].update(canvasDraft="Wrong pending Canvas"),
        lambda value: value["clients"]["one"].update(canvasViews={"preferences": {"primary:wrong": {"dirty": True}}}),
        lambda value: value["clients"]["one"]["chatCanvas"][args["sessionId"]]["canvas"].update(open=True),
        lambda value: value["clients"]["one"]["chatCanvas"].update(other={"canvas": {"id": "wrong"}}),
        lambda value: value["clients"]["one"].update(selectionRevision=999),
        lambda value: value["effects"][0].append("unexpected runtime start"),
        lambda value: value["effects"][1].append("unexpected send"),
        lambda value: value["effects"][3].append("unexpected approval or prewarm"),
        lambda value: value["sessions"][args["sessionId"]]["messages"].append({"id": "unexpected message"}),
        lambda value: value["sessions"][args["sessionId"]].update(approvals=[{"id": "unexpected"}]),
    ]
    for mutate in mutations:
        changed = deepcopy(good)
        mutate(changed)
        with pytest.raises(AssertionError):
            assert_publication_unchanged(before, changed, dirty)


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


async def test_reference_metadata_is_current_bounded_and_message_owned(reference_app):
    app, args, _ = reference_app
    record = await attach(app, args)
    read = {"sessionId": args["sessionId"], "messageId": args["messageId"]}
    result = await command(app, "outputs.chatReferences", read)
    item = result["result"]["items"][0]
    assert item["id"] == record["id"] and item["messageId"] == args["messageId"]
    assert item["available"] and item["workspace"] and not result["result"]["overflow"]
    assert item["originalTitle"] == item["title"] == "Investigation destination"
    await app.dispatch("session.rename", {"id": args["targetSessionId"], "title": "Current destination"})
    await app.dispatch("session.archive", {"id": args["targetSessionId"]})
    current = (await command(app, "outputs.chatReferences", read))["result"]["items"][0]
    assert current["title"] == "Current destination" and current["archived"]
    assert current["originalTitle"] == item["title"]
    assert current["id"] == item["id"] and current["revision"] == item["revision"]
    # Renaming again must not replace the original host-saved label.
    await app.dispatch("session.rename", {"id": args["targetSessionId"], "title": "Latest destination"})
    latest = (await command(app, "outputs.chatReferences", read))["result"]["items"][0]
    assert latest["title"] == "Latest destination" and latest["originalTitle"] == item["title"]
    with pytest.raises(AppError):
        await command(app, "outputs.chatReferences", {**read, "messageId": "not-an-assistant"})
    await app.dispatch("outputs.unlink", {"sessionId": args["sessionId"], "id": record["id"],
                                          "expectedRevision": record["revision"]})
    assert (await command(app, "outputs.chatReferences", read))["result"]["items"] == []


async def test_chat_reference_revision_tracks_committed_archive_and_restore(reference_app):
    app, args, _ = reference_app
    record = await attach(app, args)
    sid, tid = args["sessionId"], args["targetSessionId"]
    read = {"sessionId": sid, "messageId": args["messageId"]}

    def projected():
        with app.clients.bind("one"):
            snapshot = app.browser_state()
            assert snapshot["selectedSessionId"] == sid
            source = next(row for row in snapshot["sessions"] if row["id"] == sid)
            assert source["linkedChatReferencesAvailable"]
            return source["chatReferenceRevision"]

    before = projected()
    directory = (app.directory.navigation_revision, app.directory.workspace_revision)
    version = app._session(sid).get("chatReferenceVersion")
    for action, archived in (("session.archive", True), ("session.restore", False)):
        receipt = await command(app, action, {"id": tid})
        assert receipt["accepted"]
        assert (tid in app.state["conversationOrganization"]["archived"]) is archived
        after = projected()
        # The source and directory did not change; organization owns this refresh.
        assert (app.directory.navigation_revision, app.directory.workspace_revision) == directory
        assert app._session(sid).get("chatReferenceVersion") == version
        assert after != before
        item = (await command(app, "outputs.chatReferences", read))["result"]["items"][0]
        assert item["archived"] is archived and item["id"] == record["id"]
        assert item["revision"] == record["revision"]
        assert projected() == after
        before = after
    assert effects(app) == [[], [], [], []]


async def test_chat_reference_revision_includes_unknown_organization_invalidation(reference_app):
    app, args, _ = reference_app
    await attach(app, args)
    sid = args["sessionId"]

    def projected():
        # A fresh projection, not a cached browser frame: an unknown epoch must
        # invalidate the token even without an app-wide revision increment.
        with app.clients.bind("one"):
            source = next(row for row in app.get_state()["sessions"] if row["id"] == sid)
            return source["chatReferenceRevision"]

    before = projected()
    revision = app.state["revision"]
    unknown_epoch = app.shell.resources.unknown_epoch
    organization_epoch = app.shell.resources.epochs["conversationOrganization"]
    directory = (app.directory.navigation_revision, app.directory.workspace_revision)
    app.shell.resources.invalidate()
    assert app.state["revision"] == revision
    assert app.shell.resources.epochs["conversationOrganization"] == organization_epoch
    assert (app.directory.navigation_revision, app.directory.workspace_revision) == directory
    after = projected()
    assert after != before and projected() == after
    before_parts, after_parts = json.loads(before), json.loads(after)
    # Existing directory tuples already contain SHA-256 digests. Bound the
    # added dependency's shape, not the byte size of those unchanged tuples.
    assert len(before_parts) == len(after_parts) == 4
    assert after_parts[:3] == before_parts[:3]
    assert before_parts[3] == [unknown_epoch, [["conversationOrganization", organization_epoch]]]
    assert after_parts[3] == [unknown_epoch + 1, [["conversationOrganization", organization_epoch]]]
    for suffix in (before_parts[3], after_parts[3]):
        assert type(suffix[0]) is int and suffix[0] >= 0
        assert type(suffix[1][0][1]) is int and suffix[1][0][1] >= 0
    assert args["targetSessionId"] not in after
    assert effects(app) == [[], [], [], []]


async def test_chat_reference_revision_does_not_churn_on_saved_drafts(reference_app):
    app, args, other = reference_app
    await attach(app, args)
    sid = args["sessionId"]

    def projected():
        with app.clients.bind("one"):
            snapshot = app.browser_state()
            assert snapshot["selectedSessionId"] == sid
            return next(row["chatReferenceRevision"] for row in snapshot["sessions"] if row["id"] == sid)

    before = projected()
    revision = app.state["revision"]
    for target, text in ((other, "Unrelated saved draft"), (sid, "Retained source draft")):
        receipt = await command(app, "view.update", {"sessionId": target, "patch": {"draft": text}})
        assert receipt["accepted"] and app.clients.records["one"]["drafts"][target] == text
        assert app.state["revision"] > revision
        assert projected() == before
        revision = app.state["revision"]
    assert effects(app) == [[], [], [], []]


@pytest.mark.parametrize("defect", ["missing", "worker", "foreign-host", "changed-source"])
async def test_changed_reference_fails_closed_without_substitute(reference_app, defect):
    app, args, _ = reference_app
    if defect == "missing":
        # Deletion is supported only for genuinely host-owned managed chats,
        # not a workspace chat with a hand-edited ownership flag.
        created = await command(app, "session.create", {
            "title": "Deletable reference destination", "location": {"kind": "managed"}})
        args = {**args, "targetSessionId": created["sessionId"]}
        await command(app, "session.select", {"id": args["sessionId"]})
    record = await attach(app, args)
    assert (await command(app, "session.select", click(args, record)))["accepted"]
    await command(app, "session.select", {"id": args["sessionId"]})
    if defect == "missing":
        preview = (await app.dispatch("session.deletePreview", {"id": args["targetSessionId"]}))["result"]
        await app.dispatch("session.delete", {"id": args["targetSessionId"],
                                              "confirmationToken": preview["confirmationToken"]})
        async with asyncio.timeout(5):
            while app.tasks:
                await asyncio.gather(*list(app.tasks))
        assert app.directory.resolve(args["targetSessionId"]) is None
    elif defect == "worker":
        app._session(args["targetSessionId"])["sessionKind"] = "worker"
    elif defect == "foreign-host":
        stored = app.outputs.record(args["sessionId"], record["id"])
        stored["target"]["reference"]["hostId"] = "different-host"
        app.db.execute("UPDATE output_records SET value=? WHERE id=?", (json.dumps(stored), record["id"]))
    else:
        source = app._session(args["sessionId"])
        next(m for m in source["messages"] if m["id"] == args["messageId"])["text"] = "Different saved body"
    before = deepcopy(app.clients.records)
    before_effects = effects(app)
    with pytest.raises(AppError):
        await command(app, "session.select", click(args, record))
    assert app.clients.records == before
    assert effects(app) == before_effects


async def test_same_chat_reference_still_uses_sidebar_guard_and_view_transition(reference_app, monkeypatch):
    app, args, _ = reference_app
    args = {**args, "targetSessionId": args["sessionId"]}
    record = await attach(app, args)
    await command(app, "view.update", {"patch": {"workSurface": "workspaces"}})
    calls = []
    original = app.canvas_views.guard_transition
    def guard(action, values):
        calls.append((action, deepcopy(values)))
        return original(action, values)
    monkeypatch.setattr(app.canvas_views, "guard_transition", guard)
    result = await command(app, "session.select", click(args, record))
    assert result["accepted"] and result["effects"] == []
    assert any(action == "session.select" and values == click(args, record) for action, values in calls)
    assert app.clients.records["one"]["view"]["workSurface"] == "chat"


async def test_reference_copy_export_escapes_labels_and_keeps_range_boundary(reference_app):
    app, args, _ = reference_app
    await app.dispatch("session.rename", {"id": args["targetSessionId"],
        "title": "[Do not execute](javascript:alert(1)) <button> https://example.invalid"})
    await attach(app, args)
    result = await command(app, "message.copy", {"sessionId": args["sessionId"], "messageId": args["messageId"]})
    fallback = next(e["chatReferenceText"] for e in result["effects"] if e["type"] == "message.copy")
    assert fallback.startswith("Chat reference: ")
    assert "<button>" not in fallback and "(javascript:" not in fallback
    assert args["targetSessionId"] not in fallback and "TARGET_ONLY_TRANSCRIPT_SENTINEL" not in fallback
    await app.on_runtime_event("assistant.message", {"sessionId": args["sessionId"], "text": "Later reply"})
    mid = app._session(args["sessionId"])["messages"][-1]["id"]
    receipt = await app.dispatch("session.export", {"id": args["sessionId"], "format": "markdown",
        "destination": "none", "scope": "range", "fromMessageId": mid, "throughMessageId": mid})
    assert "Chat reference:" not in exported(app, receipt)


async def test_reference_browser_origin_not_supplied_by_payload(reference_app):
    app, args, _ = reference_app
    record = await attach(app, args)
    app.clients.attach("terminal-client", kind="tui")
    with pytest.raises(AppError):
        await command(app, "session.select", click(args, record), client="terminal-client")
    with pytest.raises(AppError):
        await command(app, "session.select", {**click(args, record), "clientId": "one"})


@pytest.mark.parametrize("cold", [False, True])
async def test_reference_canonical_authoring_can_be_opened_through_verified_alias(reference_app, monkeypatch, cold):
    app, args, _ = reference_app
    source, _, _, directory, canonical = cold_alias_corpus(app, native_message_id=False)
    record = await attach(app, {**args, "messageId": canonical})
    original = (directory / "transcript.jsonl").read_bytes()
    if cold:
        app._state["selectedSessionId"] = None
        for client in app.clients.records.values():
            client["selectedSessionId"] = None
        monkeypatch.setattr(app.cold_display, "RECENT_LIMIT", 0)
        assert source["id"] in app.cold_display.retire(force=True)
        app.clients.records["one"]["selectedSessionId"] = source["id"]
        assert not dict.__contains__(source, "messages")
    alias_args = {**args, "messageId": "saved-web-assistant"}
    listed = await command(app, "outputs.chatReferences",
                           {"sessionId": args["sessionId"], "messageId": alias_args["messageId"]},
                           include_state=False)
    assert listed["result"]["items"][0]["id"] == record["id"]
    assert listed["result"]["items"][0]["messageId"] == alias_args["messageId"]
    if cold:
        assert not dict.__contains__(source, "messages")
    assert (await command(app, "session.select", click(alias_args, record)))["accepted"]
    assert (directory / "transcript.jsonl").read_bytes() == original


async def test_event_reference_survives_real_canonical_binding_and_cold_reopen(reference_app, monkeypatch):
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_files import project_slug
    from amplifier_web.automatic_history import read_transcript, merge_web_history, directory
    app, args, _ = reference_app
    record = await attach(app, args)
    source = app._session(args["sessionId"])
    saved = next(m for m in source["messages"] if m["id"] == args["messageId"])
    assert "nativeIndex" not in saved
    corpus = [{"role": "user" if i % 2 == 0 else "assistant",
               "content": saved["text"] if i == 1 else f"Body {i} " + ("retained " * 300)}
              for i in range(240)]
    store = SessionStore.for_app(app.data_dir, source["workspace"])
    store.save(source["id"], corpus, {"bundle_name": source["bundle"],
               "working_dir": source["workspace"]}, preserve_system=True)
    source.update(nativeProject=project_slug(source["workspace"]), nativeIdentity=source["id"],
                  runtimeSessionId=source["id"], historyManaged=False, status="idle")
    page = read_transcript(source, limit=300)
    merge_web_history(source, page["messages"])
    source["nativeRevision"] = page["revision"]
    assert next(m for m in source["messages"] if m["id"] == args["messageId"])["nativeIndex"] == 1
    # The real retirement guard correctly retains externally borrowed mutable
    # rows. Do not let this fixture's own aliases defeat the path being tested.
    del saved, page
    read = {"sessionId": source["id"], "messageId": args["messageId"]}
    assert (await command(app, "outputs.chatReferences", read))["result"]["items"][0]["id"] == record["id"]
    raw = (directory(source) / "transcript.jsonl").read_bytes()
    app._state["selectedSessionId"] = None
    for client in app.clients.records.values():
        client["selectedSessionId"] = None
    monkeypatch.setattr(app.cold_display, "RECENT_LIMIT", 0)
    assert source["id"] in app.cold_display.retire(force=True)
    markers = deepcopy(dict.get(source, "_coldFields"))
    app.clients.records["one"]["selectedSessionId"] = source["id"]
    listed = await command(app, "outputs.chatReferences", read, include_state=False)
    assert listed["result"]["items"][0]["id"] == record["id"]
    assert not dict.__contains__(source, "messages") and dict.get(source, "_coldFields") == markers
    home, workspace = app.data_dir, source["workspace"]
    await app.close()
    reopened = AppService(home, workspace=workspace, runtime=NoExecution())
    try:
        reopened.clients.attach("reopened-reference", resume="one", kind="web")
        assert (await command(reopened, "outputs.chatReferences", read,
                              client="reopened-reference"))["result"]["items"][0]["id"] == record["id"]
        export = await reopened.dispatch("session.export", {"id": source["id"], "format": "markdown",
                                             "destination": "none", "includeReferenceIds": False})
        assert "Chat reference: Investigation destination" in exported(reopened, export)
        assert (await command(reopened, "session.select", click(args, record),
                              client="reopened-reference"))["accepted"]
        assert (directory(source) / "transcript.jsonl").read_bytes() == raw
    finally:
        await reopened.close()


async def test_changed_source_does_not_export_or_copy_a_stale_reference(reference_app):
    app, args, _ = reference_app
    await attach(app, args)
    source = app._session(args["sessionId"])
    next(m for m in source["messages"] if m["id"] == args["messageId"])["text"] = "Changed reply"
    copied = await command(app, "message.copy", {"sessionId": source["id"], "messageId": args["messageId"]})
    assert all("chatReferenceText" not in effect for effect in copied["effects"])
    receipt = await app.dispatch("session.export", {"id": source["id"], "format": "markdown", "destination": "none"})
    assert "Changed reply" in exported(app, receipt)
    assert "Chat reference:" not in exported(app, receipt)


async def test_portable_reference_is_retained_inert_until_fresh_attachment(reference_app):
    from amplifier_web.portability_data import capture, install_outputs
    app, args, _ = reference_app
    source, _, _, _, canonical = canonical_corpus(app)
    source["selection"] = {"provider": "synthetic", "model": "synthetic"}
    args = {**args, "messageId": canonical}
    record = await attach(app, args)
    payload = capture(app, source)
    install_outputs(app, payload)
    saved = (await app.dispatch("outputs.read",
                               {"sessionId": source["id"], "id": record["id"]}))["result"]
    assert not saved["linked"] and not saved["available"]
    assert saved["revision"] == record["revision"] + 1
    with pytest.raises(AppError):
        await command(app, "session.select", click(args, saved))
    with pytest.raises(AppError):
        await app.dispatch("outputs.relink", {"sessionId": source["id"], "id": saved["id"],
                                              "expectedRevision": saved["revision"]})
    fresh = await attach(app, args)
    assert fresh["id"] != record["id"]
    assert (await command(app, "session.select", click(args, fresh)))["accepted"]
    assert effects(app) == [[], [], [], []]


async def test_reference_rejects_canonical_rewrite_during_binding_worker(reference_app, monkeypatch):
    from amplifier_web import chat_references
    app, args, _ = reference_app
    source, corpus, store, _, canonical = canonical_corpus(app)
    args = {**args, "messageId": canonical}
    record = await attach(app, args)
    original = chat_references.qualified_bindings
    def rewrite(proof):
        bindings = original(proof)
        changed = deepcopy(corpus)
        changed[1]["content"] = "Canonical source changed during reference qualification"
        store.save(source["id"], changed, {"bundle_name": source["bundle"],
                   "working_dir": source["workspace"]}, preserve_system=True)
        return bindings
    monkeypatch.setattr(chat_references, "qualified_bindings", rewrite)
    before = deepcopy(app.clients.records)
    with pytest.raises(AppError, match="changed"):
        await command(app, "session.select", click(args, record))
    assert app.clients.records == before


async def test_alias_origin_reference_exports_when_only_canonical_source_remains(reference_app):
    from amplifier_web.automatic_history import read_transcript
    app, args, _ = reference_app
    source, _, _, _, canonical = cold_alias_corpus(app, native_message_id=False)
    record = await attach(app, {**args, "messageId": "saved-web-assistant"})
    page = read_transcript(source, limit=300)
    source.update(messages=page["messages"], historyManaged=True, nativeRevision=page["revision"])
    assert all(m["id"] != "saved-web-assistant" for m in source["messages"])
    assert (await command(app, "outputs.chatReferences",
                           {"sessionId": source["id"], "messageId": canonical}))["result"]["items"][0]["id"] == record["id"]
    receipt = await app.dispatch("session.export", {"id": source["id"], "format": "markdown", "destination": "none"})
    assert "Chat reference: Investigation destination" in exported(app, receipt)


async def test_target_native_alias_requires_exact_project_and_never_substitutes(reference_app):
    app, args, other = reference_app
    await attach(app, args)
    first, second = app._session(args["targetSessionId"]), app._session(other)
    first.update(nativeIdentity="shared-native-reference", nativeProject="first-project")
    second.update(nativeIdentity="shared-native-reference", nativeProject="second-project")
    ambiguous = {**args, "targetSessionId": "shared-native-reference"}
    with pytest.raises(AppError, match="more than one"):
        await app.dispatch("outputs.attachChatReference", ambiguous)
    record = await attach(app, {**ambiguous, "targetNativeProject": "first-project"})
    assert record["targetSessionId"] == first["id"]
    with pytest.raises(AppError):
        await command(app, "session.select", click({**args, "targetSessionId": other}, record))
    assert (await command(app, "session.select", click(args, record)))["accepted"]
    assert app.clients.records["one"]["selectedSessionId"] == first["id"]