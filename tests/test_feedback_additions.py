"""Author-boundary candidate checks; mocked GitHub only.

Call the helper seam until the independently owned service routing is merged.
The manager runs these serially in the retained DTU, never on the source host.
"""
import asyncio
import base64
import copy
import hashlib
import json
import sqlite3

import pytest

from amplifier_web import feedback, feedback_additions as additions, feedback_attachments
from amplifier_web.service import AppError, AppService

FID = "original-feedback"
REPO = feedback.REPOSITORY
URL = "https://github.com/" + REPO + "/issues/42"


class PowerLoss(BaseException):
    pass


class Wire:
    def __init__(self, app):
        self.app = app
        self.repository = REPO
        self.account = {"id": 7, "login": "fixture-owner"}
        self.repo = {"id": 101, "full_name": REPO, "private": True}
        self.issue = {"id": 42, "number": 42, "html_url": URL, "user": {"id": 7},
                      "body": f"Original\n<!-- amplifier-feedback:{FID} -->", "title": "Original", "state": "open"}
        self.calls, self.remote, self.comments = [], {}, []
        self.lose = None
        self.crash = False
        self.after_processing = False
        self.maintainer_edit = False
        self.bad_receipt = None

    async def __call__(self, endpoint, payload, *, method=None):
        assert method is None, "This increment must never PATCH an issue."
        self.calls.append((endpoint, copy.deepcopy(payload)))
        prefix = "repos/" + self.repository
        api_url = "https://api.github.com/" + prefix
        if payload is None:
            if endpoint == "user":
                return copy.deepcopy(self.account)
            if endpoint == prefix:
                return copy.deepcopy(self.repo)
            if endpoint == prefix + "/issues/42":
                return copy.deepcopy(self.issue)
            if "/comments?" in endpoint:
                return copy.deepcopy(self.comments)
            if endpoint.startswith(prefix + "/commits/"):
                sha = endpoint.rsplit("/", 1)[1]
                return {"sha": sha, "url": api_url + "/commits/" + sha, "committer": {"id": self.account["id"]}}
            return copy.deepcopy(self.remote[endpoint])
        # Verify the *committed* sending fence from a second SQLite connection.
        with sqlite3.connect(self.app.data_dir / "app.sqlite3") as db:
            receipts = [json.loads(row[0]) for row in db.execute("SELECT receipt FROM feedback_followups")]
        fences = [phase for row in receipts for phase in row.get("audit", {}).get("phases", {}).values()
                  if phase.get("status") == "sending" and phase["endpoint"] == endpoint]
        assert len(fences) == 1 and fences[0]["inputFingerprint"] == additions.digest(payload)
        key = fences[0]["phaseId"].removeprefix("addition-request:")
        if key == self.lose and not self.after_processing:
            raise PowerLoss() if self.crash else TimeoutError("credential-bearing-error-fixture")
        if endpoint.endswith("/git/blobs"):
            data = base64.b64decode(payload["content"])
            sha = additions.git_sha("blob", data)
            result = {"sha": sha, "url": api_url + "/git/blobs/" + sha, **payload}
            self.remote[prefix + "/git/blobs/" + sha] = result
            if self.maintainer_edit:
                self.issue["body"] += "\nMaintainer concurrent edit"
        elif endpoint.endswith("/git/trees"):
            rows = [{"id": item["path"].split("/", 1)[0], "name": item["path"].split("/", 1)[1]} for item in payload["tree"]]
            blobs = {item["path"].split("/", 1)[0]: item["sha"] for item in payload["tree"]}
            sha, entries = additions.tree_binding(rows, blobs)
            result = {"sha": sha, "url": api_url + "/git/trees/" + sha, "tree": entries, "truncated": False}
            self.remote[prefix + "/git/trees/" + sha + "?recursive=1"] = result
        elif endpoint.endswith("/git/commits"):
            sha = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            result = {"sha": sha, "url": api_url + "/git/commits/" + sha,
                      **payload, "tree": {"sha": payload["tree"]}}
            self.remote[prefix + "/git/commits/" + sha] = result
        elif endpoint.endswith("/git/refs"):
            result = {"ref": payload["ref"], "url": api_url + "/git/" + payload["ref"],
                      "object": {"sha": payload["sha"], "type": "commit"}}
            self.remote[prefix + "/git/ref/" + payload["ref"].removeprefix("refs/")] = result
        elif endpoint.endswith("/issues/42/comments"):
            result = {"id": 123, "html_url": self.issue["html_url"] + "#issuecomment-123",
                      "issue_url": api_url + "/issues/42", "user": {"id": self.account["id"]}, **payload}
            self.comments.append(result)
        else:
            raise AssertionError("Unexpected write endpoint")
        if key == self.lose and self.after_processing:
            raise PowerLoss() if self.crash else TimeoutError("credential-bearing-error-fixture")
        if self.bad_receipt == key:
            result = {**result, "url": "https://invalid.example/receipt", "html_url": "https://invalid.example/comment"}
        return copy.deepcopy(result)

    @property
    def writes(self):
        return [(endpoint, payload) for endpoint, payload in self.calls if payload is not None]


async def command(app, action, args):
    async with app.lock:
        queued = app.feedback.followups.accept(action, args, "ui")
        app._save_changes(globals={"feedback"})
    if queued:
        await app.feedback.followups.run(args["requestId"])
    return app.feedback.additions.load(args["requestId"])[1]


async def seed(app):
    app.feedback.accept({"requestId": FID, "title": "Original", "body": "Original", "category": "bug", "includeDiagnostics": False})
    await app.feedback.update(FID, status="submitted", url=URL)
    app.clients.attach("client-a")
    app.clients.attach("client-b")


async def stage(app, identity="stage-request-1", data=b"reviewed fixture"):
    args = {"requestId": identity, "feedbackId": FID, "name": "fixture [file].txt", "base64": base64.b64encode(data).decode()}
    await app.dispatch("feedback.attachment.add", args)
    return app.feedback.additions.project()["attachmentDrafts"][FID][-1], args


async def prepare(app, file_count=1):
    row, _ = await stage(app)
    for index in range(1, file_count):
        await stage(app, "stage-request-" + str(index + 1), ("fixture " + str(index)).encode())
    review = await command(app, "feedback.attachments.review", {"requestId": "review-request-1", "feedbackId": FID})
    assert review["status"] == "completed"
    args = {"requestId": "addition-request", "feedbackId": FID, "reviewRequestId": review["requestId"],
            "confirmedFiles": [{"id": item["id"], "sha256": item["sha256"]} for item in review["review"]["manifest"]],
            "comment": "Explicit file comment"}
    return row, args


async def test_exact_wire_private_legacy_destination_no_patch_and_idempotent(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        # Verify historical destination is not globally retargeted.
        wire.repository = feedback.RECEIPT_REPOSITORIES[1]
        wire.repo["full_name"] = wire.repository
        wire.issue["html_url"] = f"https://github.com/{wire.repository}/issues/42"
        await app.feedback.update(FID, url=wire.issue["html_url"])
        with app.clients.bind("client-a"):
            row, args = await prepare(app)
            assert not wire.writes  # review is not upload or consent
            wire.maintainer_edit = True
            receipt = await command(app, "feedback.attachments.add", args)
            assert receipt["status"] == "submitted" and receipt["filesStored"]
            assert receipt["commentStatus"] == "succeeded"
            assert receipt["commentUrl"] == wire.issue["html_url"] + "#issuecomment-123"
            assert len(wire.writes) == 5
            assert all(endpoint.startswith("repos/" + wire.repository + "/") for endpoint, _ in wire.writes)
            blob, tree, commit, ref, comment = [payload for _, payload in wire.writes]
            assert base64.b64decode(blob["content"]) == b"reviewed fixture"
            assert tree["tree"][0]["path"] == row["id"] + "/" + row["name"]
            assert commit["parents"] == [] and "base_tree" not in tree
            assert ref["ref"] == "refs/heads/feedback-assets/addition-request"
            assert "<!-- amplifier-feedback-attachments:addition-request -->" in comment["body"]
            assert "Maintainer concurrent edit" in wire.issue["body"]
            assert wire.issue["title"] == "Original" and wire.issue["state"] == "open"
            count = len(wire.calls)
            assert (await command(app, "feedback.attachments.add", args))["status"] == "submitted"
            assert len(wire.calls) == count
            with pytest.raises(AppError):
                await command(app, "feedback.attachments.add", {**args, "comment": "changed"})
            assert not app.feedback.additions.project()["attachmentDrafts"]
    finally:
        await app.close()


@pytest.mark.parametrize("phase", ["blob", "tree", "commit", "ref", "comment"])
@pytest.mark.parametrize("after_processing", [False, True])
@pytest.mark.parametrize("crash", [False, True])
async def test_loss_or_crash_at_every_phase_restart_and_exact_retry_never_write_again(tmp_path, monkeypatch, phase, after_processing, crash):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    await seed(app)
    with app.clients.bind("client-a"):
        row, args = await prepare(app)
        wire.lose = "blob:" + row["id"] if phase == "blob" else phase
        wire.after_processing, wire.crash = after_processing, crash
        if crash:
            with pytest.raises(PowerLoss):
                await command(app, "feedback.attachments.add", args)
        else:
            receipt = await command(app, "feedback.attachments.add", args)
            assert receipt["status"] == ("partial" if phase == "comment" else "unknown")
        snapshot = app.feedback.additions.project()
        assert "credential-bearing-error-fixture" not in json.dumps(snapshot)
        assert "reviewed fixture" not in json.dumps(snapshot)
        assert str(tmp_path) not in json.dumps(snapshot)
    await app.close()
    reopened = AppService(tmp_path, workspace=tmp_path)
    wire.app = reopened
    try:
        with reopened.clients.bind("client-a"):
            count = len(wire.calls)
            receipt = await command(reopened, "feedback.attachments.add", args)
            assert len(wire.calls) == count
            phases = receipt["audit"]["phases"]
            assert phases[wire.lose]["status"] == "unknown"
            assert all(item["status"] != "sending" for item in phases.values())
            assert phases["comment"]["status"] == ("unknown" if phase == "comment" else "not_started")
            with pytest.raises(AppError):
                await command(reopened, "feedback.attachments.add", {**args, "requestId": "no-new-id-retry"})
            wire.lose = None
            check = await command(reopened, "feedback.attachments.reconcile",
                {"requestId": "read-only-check", "feedbackId": FID, "additionRequestId": args["requestId"]})
            assert check["status"] == "completed"
            assert len(wire.writes) == {"blob": 1, "tree": 2, "commit": 3, "ref": 4, "comment": 5}[phase]
            final = reopened.feedback.additions.load(args["requestId"])[1]
            assert final["status"] == ("submitted" if phase == "comment" and after_processing else "partial" if phase in {"ref", "comment"} and (after_processing or phase == "comment") else "unknown")
            assert all(item["status"] == "not_started" for key, item in final["audit"]["phases"].items()
                       if list(phases).index(key) > list(phases).index("blob:" + row["id"] if phase == "blob" else phase))
    finally:
        await reopened.close()


# Corrective falsifiers: exact snapshots, never an ancestor-client query grant.
# These are authored here and executed only by the manager in the retained DTU.
async def attach_copy(app, identity, source):
    async with app.lock:
        app.clients.attach(identity, source)
        app._save_changes()


async def save_file_intent(app, value):
    await app.dispatch("view.update", {"patch": {"feedbackFollowupDraft": {
        "feedbackId": FID, "fileAdditions": {FID: value}}}})


async def test_attach_captures_exact_refs_not_future_source_operations(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        with app.clients.bind("client-a"):
            row, args = await prepare(app)
            await save_file_intent(app, {"reviewRequestId": args["reviewRequestId"]})
        before = app.db.execute("SELECT metadata FROM feedback_attachments").fetchall()
        await attach_copy(app, "reload-a", "client-a")
        await attach_copy(app, "duplicate-a", "client-a")
        with app.clients.bind("reload-a"):
            snapshot = app.browser_state()
            assert additions.REFERENCE_KEY not in json.dumps(snapshot)
            assert snapshot["feedback"]["attachmentDrafts"] == {}
            recovery = snapshot["feedback"]["attachmentRecovery"][FID]
            assert recovery["files"][0]["id"] == row["id"]
            assert recovery["files"][0]["readOnly"] is True
            assert app.feedback.additions.accept("feedback.attachments.review",
                {"requestId": args["reviewRequestId"], "feedbackId": FID}, "ui") is False
            with pytest.raises(AppError):
                await command(app, "feedback.attachments.add", args)  # old review is not consent
            own, _ = await stage(app, "reload-own-stage")
            assert own["id"] != row["id"]
        assert app.db.execute("SELECT metadata FROM feedback_attachments WHERE request_id='stage-request-1'").fetchall() == before
        with app.clients.bind("client-a"):
            await stage(app, "future-original-stage", b"future original")
            await command(app, "feedback.attachments.review",
                          {"requestId": "future-original-review", "feedbackId": FID})
        with app.clients.bind("duplicate-a"):
            projection = app.feedback.additions.project()
            assert not projection["attachmentDrafts"]
            assert {r["requestId"] for r in projection["additions"]} == {"review-request-1"}
            assert [r["id"] for r in projection["attachmentRecovery"][FID]["files"]] == [row["id"]]
            with pytest.raises(AppError):
                app.feedback.additions.accept("feedback.attachments.review",
                    {"requestId": "future-original-review", "feedbackId": FID}, "ui")
        # Attaching an existing identity must not expand its old exact snapshot.
        await attach_copy(app, "duplicate-a", "client-a")
        with app.clients.bind("duplicate-a"):
            assert len(app.feedback.additions.project()["attachmentRecovery"][FID]["files"]) == 1
        assert not wire.writes
    finally:
        await app.close()


@pytest.mark.parametrize("accepted", [False, True])
async def test_inherited_missing_or_unknown_addition_blocks_without_replay(tmp_path, monkeypatch, accepted):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    await seed(app)
    with app.clients.bind("client-a"):
        row, args = await prepare(app)
        await save_file_intent(app, {"pending": args, "reviewRequestId": args["reviewRequestId"]})
        if accepted:
            wire.lose = "blob:" + row["id"]
            await command(app, "feedback.attachments.add", args)
    await attach_copy(app, "reload-a", "client-a")
    # Loss-before-acceptance: the complete saved payload is present at attach,
    # not fabricated after the new browser has already resumed.
    assert app.clients.records["reload-a"]["view"]["feedbackFollowupDraft"]["fileAdditions"][FID]["pending"] == args
    await attach_copy(app, "reload-again", "reload-a")
    await app.close()
    app = AppService(tmp_path, workspace=tmp_path)
    wire.app = app
    try:
        with app.clients.bind("reload-again"):
            projection = app.feedback.additions.project()
            assert projection["attachmentRecovery"][FID]["blocked"]
            await save_file_intent(app, {})  # writable state cannot erase the blocker
            with pytest.raises(AppError):
                await stage(app, "must-not-restage")
            with pytest.raises(AppError):
                await command(app, "feedback.attachments.add", args)
            with pytest.raises(AppError):
                app.feedback.additions.accept("feedback.attachments.add",
                                              {**args, "requestId": "no-new-upload-id"}, "ui")
        if not accepted:
            with app.clients.bind("client-a"):
                wire.lose = "blob:" + row["id"]
                await command(app, "feedback.attachments.add", args)  # original caller, late acceptance
        with app.clients.bind("reload-again"):
            before = len(wire.calls)
            assert app.feedback.additions.accept("feedback.attachments.add", args, "ui") is False
            assert len(wire.calls) == before
            with pytest.raises(AppError):
                app.feedback.additions.accept("feedback.attachments.add", {**args, "comment": "altered"}, "ui")
            assert any(r["requestId"] == args["requestId"] and r["readOnly"]
                       for r in app.feedback.additions.project()["additions"])
            app.feedback.accept({"requestId": "other-feedback", "title": "Other", "body": "Other",
                                 "category": "bug", "includeDiagnostics": False})
            await app.feedback.update("other-feedback", status="submitted", url=URL.replace("/42", "/43"))
            await app.dispatch("feedback.attachment.add", {"requestId": "independent-stage",
                "feedbackId": "other-feedback", "name": "own.txt", "base64": base64.b64encode(b"own").decode()})
        assert len(wire.writes) == 1
    finally:
        await app.close()


@pytest.mark.parametrize("change", ["owner", "report", "payload", "fingerprint", "action", "removed"])
async def test_forged_pending_hint_cannot_expose_foreign_or_changed_receipt(tmp_path, monkeypatch, change):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        with app.clients.bind("client-a"):
            _, args = await prepare(app)
            await command(app, "feedback.attachments.add", args)
        with app.clients.bind("client-b"):
            # Neither a writable owner/lineage nor a naked review ID is authority.
            await save_file_intent(app, {"pending": args, "reviewRequestId": args["reviewRequestId"],
                                        "clientId": "client-a", "feedbackLineage": ["client-a"]})
        await attach_copy(app, "forged-clone", "client-b")
        with app.clients.bind("forged-clone"):
            projection = app.feedback.additions.project()
            assert not projection["additions"]
            assert not projection["attachmentRecovery"][FID]["files"]
            assert "commentUrl" not in json.dumps(projection)
            with pytest.raises(AppError):
                app.feedback.additions.accept("feedback.attachments.add", args, "ui")
        await attach_copy(app, "real-clone", "client-a")
        fp, payload, raw = app.db.execute("SELECT fingerprint,payload,receipt FROM feedback_followups WHERE id=?",
                                         (args["requestId"],)).fetchone()
        receipt, payload = json.loads(raw), json.loads(payload)
        if change == "owner":
            receipt["clientId"] = "client-b"
        elif change == "report":
            receipt["feedbackId"] = "other-feedback"
        elif change == "payload":
            payload["comment"] = "changed"
        elif change == "fingerprint":
            fp = "0" * 64
        elif change == "action":
            receipt["action"] = "feedback.comment"
        if change == "removed":
            app.db.execute("DELETE FROM feedback_followups WHERE id=?", (args["requestId"],))
        else:
            app.db.execute("UPDATE feedback_followups SET fingerprint=?,payload=?,receipt=? WHERE id=?",
                           (fp, json.dumps(payload), json.dumps(receipt), args["requestId"]))
        with app.clients.bind("real-clone"):
            projection = app.feedback.additions.project()
            assert not any(r["requestId"] == args["requestId"] for r in projection["additions"])
            assert "commentUrl" not in json.dumps(projection)
            assert projection["attachmentRecovery"][FID]["blocked"]
            with pytest.raises(AppError):
                app.feedback.additions.accept("feedback.attachments.add", args, "ui")
    finally:
        await app.close()


@pytest.mark.parametrize("change", [None, "name", "size", "sha256", "binding", "owner", "report"])
async def test_late_stage_ack_matches_host_binding_with_sniffed_mime(tmp_path, change):
    app = AppService(tmp_path, workspace=tmp_path)
    await seed(app)
    data = b"%PDF-1.7\nfixture"
    args = {"requestId": "late-stage-request", "feedbackId": FID, "name": "../folder/fi\x01le.pdf",
            "base64": base64.b64encode(data).decode()}
    hint = {"requestId": args["requestId"], "feedbackId": FID, "name": args["name"],
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "mime": "attacker/type"}
    with app.clients.bind("client-a"):
        await save_file_intent(app, {"staging": [hint]})
    await attach_copy(app, "reload-a", "client-a")
    assert app.clients.records["reload-a"]["view"]["feedbackFollowupDraft"]["fileAdditions"][FID]["staging"] == [hint]
    with app.clients.bind("reload-a"):
        projection = app.feedback.additions.project()
        assert not projection["attachmentRecovery"][FID]["files"]
        assert projection["attachmentRecovery"][FID]["blocked"]
        with pytest.raises(AppError):
            await app.dispatch("feedback.attachment.add", args)
    with app.clients.bind("client-a"):
        await app.dispatch("feedback.attachment.add", args)
    raw = app.db.execute("SELECT metadata FROM feedback_attachments WHERE request_id=?", (args["requestId"],)).fetchone()[0]
    metadata = json.loads(raw)
    assert metadata["_submittedBinding"] == {"name": args["name"], "size": len(data), "sha256": hint["sha256"]}
    assert metadata["_storedBinding"]["name"] == "file.pdf"
    assert metadata["_storedBinding"]["mime"] == "application/pdf"
    if change in {"name", "size", "sha256"}:
        metadata[change] = "changed.pdf" if change == "name" else len(data) + 1 if change == "size" else "0" * 64
    elif change == "binding":
        metadata.pop("_submittedBinding")
    elif change == "owner":
        metadata["_clientId"] = "client-b"
    elif change == "report":
        metadata["_feedbackId"] = "other-feedback"
    if change:
        app.db.execute("UPDATE feedback_attachments SET metadata=? WHERE request_id=?", (json.dumps(metadata), args["requestId"]))
    await attach_copy(app, "reload-again", "reload-a")
    await app.close()
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        with app.clients.bind("reload-again"):
            recovery = app.feedback.additions.project()["attachmentRecovery"][FID]
            if change:
                assert not recovery["files"] and recovery["blocked"]
                assert all("url" not in row for row in recovery["hints"])
            else:
                assert recovery["files"][0]["name"] == "file.pdf"
                assert recovery["files"][0]["mime"] == "application/pdf"
                assert not recovery["blocked"]
                before = app.db.execute("SELECT metadata FROM feedback_attachments").fetchall()
                await app.dispatch("feedback.attachment.add", args)  # exact inherited lookup only
                assert app.db.execute("SELECT metadata FROM feedback_attachments").fetchall() == before
            with pytest.raises(AppError):
                await app.dispatch("feedback.attachment.add", {**args, "name": "changed.pdf"})
    finally:
        await app.close()


@pytest.mark.parametrize("changed_destination", [None, "account", "private", "marker"])
async def test_inherited_reconcile_preserves_owner_selection_and_positive_phases(tmp_path, monkeypatch, changed_destination):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        with app.clients.bind("client-a"):
            _, args = await prepare(app)
            wire.lose, wire.after_processing = "comment", True
            await command(app, "feedback.attachments.add", args)
        await attach_copy(app, "reload-a", "client-a")
        before = app.db.execute("SELECT metadata FROM feedback_attachments").fetchall()
        if changed_destination == "account":
            wire.account["id"] = 8
        elif changed_destination == "private":
            wire.repo["private"] = False
        elif changed_destination == "marker":
            wire.issue["body"] = "missing marker"
        with app.clients.bind("reload-a"):
            check = await command(app, "feedback.attachments.reconcile",
                {"requestId": "inherited-read-check", "feedbackId": FID, "additionRequestId": args["requestId"]})
            assert check["clientId"] == "reload-a"
            assert check["status"] == ("failed" if changed_destination else "completed")
        final = app.feedback.additions.load(args["requestId"])[1]
        assert final["clientId"] == "client-a"
        assert final["status"] == ("partial" if changed_destination else "submitted")
        assert app.db.execute("SELECT metadata FROM feedback_attachments").fetchall() == before
        assert len(wire.writes) == 5
    finally:
        await app.close()


async def test_inherited_absent_check_cannot_downgrade_original_late_positive_completion(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        with app.clients.bind("client-a"):
            _, args = await prepare(app)
            wire.lose, wire.after_processing = "comment", True
            await command(app, "feedback.attachments.add", args)
        await attach_copy(app, "reload-a", "client-a")
        before = app.db.execute("SELECT metadata FROM feedback_attachments").fetchall()
        wire.comments.clear()
        waiting, release = asyncio.Event(), asyncio.Event()
        persist = app.feedback.additions.persist

        async def wait_for_original(identity, receipt, **kwargs):
            if kwargs.get("reconciled"):
                waiting.set()
                await release.wait()
            return await persist(identity, receipt, **kwargs)

        monkeypatch.setattr(app.feedback.additions, "persist", wait_for_original)
        with app.clients.bind("reload-a"):
            task = asyncio.create_task(command(app, "feedback.attachments.reconcile",
                {"requestId": "clone-absent-check", "feedbackId": FID, "additionRequestId": args["requestId"]}))
        try:
            await asyncio.wait_for(waiting.wait(), 3)
            # A late durable original acknowledgement wins over an older absent
            # read. No selection cleanup is licensed to the clone by that fact.
            async with app.lock:
                _, positive = app.feedback.additions.load(args["requestId"])
                phase = positive["audit"]["phases"]["comment"]
                phase.update(status="succeeded", result={"commentId": 123, "commentUrl": URL + "#issuecomment-123"})
                positive.update(status="submitted", commentId=123, commentUrl=URL + "#issuecomment-123")
                app.feedback.additions.summarize(positive)
                app.feedback.additions.store(args["requestId"], positive)
                app._save_changes(globals={"feedback"})
        finally:
            release.set()
            await asyncio.wait_for(task, 3)
        _, final = app.feedback.additions.load(args["requestId"])
        assert final["status"] == "submitted"
        assert final["audit"]["phases"]["comment"]["status"] == "succeeded"
        assert final["clientId"] == "client-a"
        assert app.db.execute("SELECT metadata FROM feedback_attachments").fetchall() == before
        assert len(wire.writes) == 5
    finally:
        await app.close()


async def test_captured_history_stays_readable_beyond_terminal_window(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        with app.clients.bind("client-a"):
            await stage(app)
            for index in range(25):
                await command(app, "feedback.attachments.review",
                              {"requestId": f"history-review-{index:02}", "feedbackId": FID})
            projection = app.feedback.additions.project()
            assert len(projection["additions"]) == 20
            await save_file_intent(app, {"pending": {"requestId": "unobserved-history-add", "feedbackId": FID,
                "reviewRequestId": "history-review-00", "confirmedFiles": [], "comment": "frozen"}})
        await attach_copy(app, "reload-a", "client-a")
        with app.clients.bind("reload-a"):
            captured = app.feedback.additions.project()
            assert len(captured["additions"]) == 20
            assert captured["attachmentRecovery"][FID]["blocked"]
        with app.clients.bind("client-a"):
            await command(app, "feedback.attachments.review",
                          {"requestId": "future-history-review", "feedbackId": FID})
        await attach_copy(app, "reload-again", "reload-a")
        with app.clients.bind("reload-again"):
            assert app.feedback.additions.project()["additions"] == captured["additions"]
            assert app.feedback.additions.project()["attachmentRecovery"][FID]["blocked"]
    finally:
        await app.close()


@pytest.mark.parametrize("change", [
    ("account", {"id": 8}), ("issue", {"user": {"id": 8}}), ("issue", {"body": "no original marker"}),
    ("issue", {"number": 43}), ("issue", {"id": 999}), ("issue", {"pull_request": {}}),
    ("repo", {"private": False}), ("repo", {"private": None}), ("repo", {"id": 999}),
    ("repo", {"full_name": "foreign/repo"}), ("issue", {"html_url": "https://github.com/foreign/repo/issues/42"}),
])
async def test_preflight_changes_after_review_refuse_before_blob(tmp_path, monkeypatch, change):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        with app.clients.bind("client-a"):
            _, args = await prepare(app)
            getattr(wire, change[0]).update(change[1])
            receipt = await command(app, "feedback.attachments.add", args)
            assert receipt["status"] == "failed" and not wire.writes
            assert all(row["status"] == "not_started" for row in receipt["audit"]["phases"].values())
    finally:
        await app.close()


@pytest.mark.parametrize("change", ["bytes", "symlink", "parent-symlink", "hash", "excerpt", "manifest", "set"])
async def test_changed_staged_bytes_and_manifest_no_follow_and_complete_set(tmp_path, monkeypatch, change):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        with app.clients.bind("client-a"):
            row, args = await prepare(app)
            path = app.data_dir / "attachments" / row["id"] / "content"
            if change == "bytes":
                path.write_bytes(b"changed bytes")
            elif change == "symlink":
                outside = tmp_path / "outside"
                outside.write_bytes(b"reviewed fixture")
                path.unlink()
                path.symlink_to(outside)
            elif change == "parent-symlink":
                moved = tmp_path / "moved"
                path.parent.rename(moved)
                path.parent.symlink_to(moved, target_is_directory=True)
            elif change == "set":
                await stage(app, "stage-request-2", b"new file")
            else:
                raw = app.db.execute("SELECT metadata FROM feedback_attachments WHERE json_extract(metadata,'$.id')=?", (row["id"],)).fetchone()
                metadata = json.loads(raw[0])
                metadata.update({"sha256": "0" * 64} if change == "hash" else {"excerpt": {"sha256": row["sha256"]}} if change == "excerpt" else {"name": "unreviewed.txt"})
                app.db.execute("UPDATE feedback_attachments SET metadata=? WHERE json_extract(metadata,'$.id')=?", (json.dumps(metadata), row["id"]))
            with pytest.raises(AppError):
                await command(app, "feedback.attachments.add", args)
            assert not wire.writes
    finally:
        await app.close()


async def test_scope_caps_initial_isolation_remove_retry_and_client_restart(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        with app.clients.bind("client-a"):
            row, stage_args = await stage(app)
            initial = app.state["view"].get("feedbackDraft", {})
            assert not initial.get("attachments")
            review = await command(app, "feedback.attachments.review", {"requestId": "review-request-1", "feedbackId": FID})
            assert review["review"]["manifest"][0]["sha256"] == row["sha256"]
            app.state["view"]["feedbackDraft"] = {"attachments": [row]}
            with pytest.raises(ValueError):
                app.feedback.selected_files({"attachmentIds": [row["id"]]})
            app.feedback.attachment_command("feedback.attachment.remove", {"feedbackId": FID, "id": row["id"]})
            app.feedback.attachment_command("feedback.attachment.add", stage_args)
            assert not app.feedback.additions.project()["attachmentDrafts"]
        with app.clients.bind("client-b"):
            assert not app.feedback.additions.project()["attachmentDrafts"]
            assert not app.feedback.additions.project()["additions"]
            with pytest.raises(AppError):
                app.feedback.attachment_command("feedback.attachment.add", stage_args)
            with pytest.raises(AppError):
                app.feedback.attachment_command("feedback.attachment.remove", {"feedbackId": FID, "id": row["id"]})
            row_b, _ = await stage(app, "client-b-stage")
            assert row_b["id"] != row["id"]
            monkeypatch.setattr(feedback_attachments, "MAX_FILES", 1)
            with pytest.raises(AppError, match="8 files"):
                await stage(app, "over-file-cap")
            monkeypatch.setattr(feedback_attachments, "MAX_FILES", 8)
            monkeypatch.setattr(feedback_attachments, "MAX_TOTAL_BYTES", row_b["size"])
            with pytest.raises(AppError):
                await stage(app, "over-total-cap", b"x")
        assert not wire.writes
        app._save()
    finally:
        await app.close()
    reopened = AppService(tmp_path, workspace=tmp_path)
    try:
        with reopened.clients.bind("client-b"):
            assert reopened.feedback.additions.project()["attachmentDrafts"][FID] == [row_b]
        with reopened.clients.bind("client-a"):
            assert not reopened.feedback.additions.project()["attachmentDrafts"]
    finally:
        await reopened.close()


@pytest.mark.parametrize("evidence", ["absent", "ambiguous", "foreign", "wrong-body", "inaccessible"])
async def test_comment_uncertainty_is_read_only_not_failure_or_success(tmp_path, monkeypatch, evidence):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        with app.clients.bind("client-a"):
            _, args = await prepare(app)
            wire.lose, wire.after_processing = "comment", True
            receipt = await command(app, "feedback.attachments.add", args)
            assert receipt["status"] == "partial" and receipt["filesStored"]
            if evidence == "absent":
                wire.comments.clear()
            elif evidence == "ambiguous":
                wire.comments.append({**wire.comments[0], "id": 124, "html_url": URL + "#issuecomment-124"})
            elif evidence == "foreign":
                wire.comments[0]["user"] = {"id": 8}
            elif evidence == "wrong-body":
                wire.comments[0]["body"] += "Changed after posting"
            else:
                original = wire.__call__
                async def blocked(endpoint, payload, **kwargs):
                    if "/comments?" in endpoint:
                        raise PermissionError("private credentials")
                    return await original(endpoint, payload, **kwargs)
                monkeypatch.setattr(feedback, "github_api", blocked)
            count = len(wire.writes)
            await command(app, "feedback.attachments.reconcile", {"requestId": "read-only-check", "feedbackId": FID, "additionRequestId": args["requestId"]})
            assert len(wire.writes) == count
            final = app.feedback.additions.load(args["requestId"])[1]
            assert final["status"] == "partial" and final["commentStatus"] == "unknown"
    finally:
        await app.close()


async def test_concurrent_direct_run_only_one_claim_and_frozen_remove(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        with app.clients.bind("client-a"):
            row, args = await prepare(app)
            async with app.lock:
                assert app.feedback.followups.accept("feedback.attachments.add", args, "ui")
                app._save_changes(globals={"feedback"})
            with pytest.raises(AppError, match="frozen"):
                app.feedback.attachment_command("feedback.attachment.remove", {"feedbackId": FID, "id": row["id"]})
            await asyncio.gather(app.feedback.additions.run(args["requestId"]), app.feedback.additions.run(args["requestId"]))
            assert len(wire.writes) == 5
    finally:
        await app.close()


@pytest.mark.parametrize("phase", ["blob", "tree", "commit", "ref", "comment"])
async def test_invalid_ack_binding_remains_uncertain_and_never_replays(tmp_path, monkeypatch, phase):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        with app.clients.bind("client-a"):
            row, args = await prepare(app)
            wire.bad_receipt = "blob:" + row["id"] if phase == "blob" else phase
            receipt = await command(app, "feedback.attachments.add", args)
            assert receipt["status"] in {"unknown", "partial"}
            assert receipt["audit"]["phases"][wire.bad_receipt]["status"] == "unknown"
            count = len(wire.calls)
            await command(app, "feedback.attachments.add", args)
            assert len(wire.calls) == count
    finally:
        await app.close()


@pytest.mark.parametrize("after_processing", [False, True])
async def test_second_blob_loss_keeps_first_succeeded_and_remaining_phases_stopped(tmp_path, monkeypatch, after_processing):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        with app.clients.bind("client-a"):
            await stage(app)
            await stage(app, "second-blob-stage", b"second fixture")
            review = await command(app, "feedback.attachments.review", {"requestId": "review-request-1", "feedbackId": FID})
            rows = review["review"]["manifest"]
            args = {"requestId": "addition-request", "feedbackId": FID, "reviewRequestId": review["requestId"],
                    "confirmedFiles": [{"id": row["id"], "sha256": row["sha256"]} for row in rows]}
            wire.lose, wire.after_processing = "blob:" + rows[1]["id"], after_processing
            receipt = await command(app, "feedback.attachments.add", args)
            phases = receipt["audit"]["phases"]
            assert phases["blob:" + rows[0]["id"]]["status"] == "succeeded"
            assert phases[wire.lose]["status"] == "unknown"
            assert phases["tree"]["status"] == "not_started"
            wire.lose = None
            await command(app, "feedback.attachments.reconcile", {"requestId": "read-only-check", "feedbackId": FID, "additionRequestId": args["requestId"]})
            assert len(wire.writes) == 2
            final = app.feedback.additions.load(args["requestId"])[1]
            assert final["status"] == "unknown"
            assert final["audit"]["phases"]["tree"]["status"] == "not_started"
    finally:
        await app.close()


async def test_foreign_unknown_local_feedback_ids_and_forged_confirmed_set_refused(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        with app.clients.bind("client-a"):
            with pytest.raises(AppError):
                await command(app, "feedback.attachments.review", {"requestId": "foreign-feedback-read", "feedbackId": "not-local-feedback"})
            assert not wire.calls
            row, args = await prepare(app)
            for confirmed in [[], [{"id": "f" * 32, "sha256": row["sha256"]}],
                              [{"id": row["id"], "sha256": "0" * 64}],
                              [*args["confirmedFiles"], *args["confirmedFiles"]]]:
                with pytest.raises(AppError):
                    await command(app, "feedback.attachments.add", {**args, "confirmedFiles": confirmed})
            await app.feedback.update(FID, status="unknown")
            with pytest.raises(AppError):
                await stage(app, "unknown-feedback-stage")
            assert not wire.writes
    finally:
        await app.close()


def test_git_blob_sha_known_independent_fixture():
    assert additions.git_sha("blob", b"test content\n") == "d670460b4b4aece5915caf5c68d12f560a9fe3e4"


@pytest.mark.parametrize("phase", ["blob-first", "blob-second", "tree", "commit", "ref", "comment"])
@pytest.mark.parametrize("boundary", ["returned_evidence", "succeeded_receipt"])
@pytest.mark.parametrize("crash", [False, True])
async def test_response_received_but_local_receipt_lost_at_each_phase(tmp_path, monkeypatch, phase, boundary, crash):
    """Distinct from wire loss: GitHub returns, then local persistence fails."""
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    await seed(app)
    with app.clients.bind("client-a"):
        _, args = await prepare(app, file_count=2)
        _, review = app.feedback.additions.load(args["reviewRequestId"])
        keys = ["blob:" + row["id"] for row in review["review"]["manifest"]] + ["tree", "commit", "ref", "comment"]
        key = keys[0] if phase == "blob-first" else keys[1] if phase == "blob-second" else phase
        original = app.feedback.additions.persist
        armed = True

        async def lose_local(identity, receipt, **kwargs):
            nonlocal armed
            candidate = receipt.get("audit", {}).get("phases", {}).get(key, {})
            wanted = "sending" if boundary == "returned_evidence" else "succeeded"
            if armed and identity == args["requestId"] and candidate.get("status") == wanted and candidate.get("result"):
                armed = False
                raise PowerLoss() if crash else OSError("fixture local receipt unavailable")
            return await original(identity, receipt, **kwargs)

        monkeypatch.setattr(app.feedback.additions, "persist", lose_local)
        if crash:
            with pytest.raises(PowerLoss):
                await command(app, "feedback.attachments.add", args)
        else:
            receipt = await command(app, "feedback.attachments.add", args)
            assert receipt["audit"]["phases"][key]["status"] == "unknown"
        assert not armed
        assert len(wire.writes) == keys.index(key) + 1
    await app.close()
    reopened = AppService(tmp_path, workspace=tmp_path)
    wire.app = reopened
    try:
        with reopened.clients.bind("client-a"):
            count = len(wire.calls)
            receipt = await command(reopened, "feedback.attachments.add", args)
            assert len(wire.calls) == count
            phases = receipt["audit"]["phases"]
            assert [phases[item]["status"] for item in keys] == (
                ["succeeded"] * keys.index(key) + ["unknown"] + ["not_started"] * (len(keys) - keys.index(key) - 1))
            if boundary == "succeeded_receipt":
                assert phases[key]["result"]  # returned evidence survived before acknowledgement
            await command(reopened, "feedback.attachments.reconcile",
                          {"requestId": "receipt-loss-read", "feedbackId": FID, "additionRequestId": args["requestId"]})
            assert len(wire.writes) == keys.index(key) + 1
            final = reopened.feedback.additions.load(args["requestId"])[1]
            assert all(final["audit"]["phases"][item]["status"] == "not_started" for item in keys[keys.index(key) + 1:])
            if key == "comment":
                assert final["status"] == "submitted"
    finally:
        await reopened.close()


@pytest.mark.parametrize("evidence", ["inaccessible", "foreign", "wrong-sha", "wrong-url", "crash"])
async def test_commit_ack_waits_for_actor_and_retains_sha_before_actor_await(tmp_path, monkeypatch, evidence):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    original = wire.__call__
    seen = []

    async def actor_failure(endpoint, payload, **kwargs):
        if "/commits/" in endpoint and "/git/commits/" not in endpoint:
            with sqlite3.connect(app.data_dir / "app.sqlite3") as db:
                receipt = json.loads(db.execute("SELECT receipt FROM feedback_followups WHERE id='addition-request'").fetchone()[0])
            phases = receipt["audit"]["phases"]
            commit = phases["commit"]
            assert commit["status"] == "sending"
            assert commit["result"]["sha"] == endpoint.rsplit("/", 1)[1]
            assert phases["ref"]["status"] == phases["comment"]["status"] == "not_started"
            seen.append(commit["result"]["sha"])
            if evidence == "crash":
                raise PowerLoss()
            if evidence == "inaccessible":
                raise PermissionError("fixture unavailable account")
            result = await original(endpoint, payload, **kwargs)
            result.update({"committer": {"id": 8}} if evidence == "foreign" else
                          {"sha": "0" * 40} if evidence == "wrong-sha" else {"url": "https://invalid.example/commit"})
            return result
        return await original(endpoint, payload, **kwargs)

    monkeypatch.setattr(feedback, "github_api", actor_failure)
    await seed(app)
    with app.clients.bind("client-a"):
        _, args = await prepare(app)
        if evidence == "crash":
            with pytest.raises(PowerLoss):
                await command(app, "feedback.attachments.add", args)
        else:
            result = await command(app, "feedback.attachments.add", args)
            assert result["audit"]["phases"]["commit"]["status"] == "unknown"
        assert len(seen) == 1 and len(wire.writes) == 3
    await app.close()
    reopened = AppService(tmp_path, workspace=tmp_path)
    wire.app = reopened
    try:
        with reopened.clients.bind("client-a"):
            before = len(wire.calls)
            result = await command(reopened, "feedback.attachments.add", args)
            assert len(wire.calls) == before
            phases = result["audit"]["phases"]
            assert phases["commit"]["status"] == "unknown" and phases["commit"]["result"]["sha"] == seen[0]
            assert phases["ref"]["status"] == phases["comment"]["status"] == "not_started"
            monkeypatch.setattr(feedback, "github_api", wire)
            await command(reopened, "feedback.attachments.reconcile",
                          {"requestId": "verified-actor-read", "feedbackId": FID, "additionRequestId": args["requestId"]})
            final = reopened.feedback.additions.load(args["requestId"])[1]
            assert final["audit"]["phases"]["commit"]["status"] == "succeeded"
            assert final["audit"]["phases"]["ref"]["status"] == "not_started"
            assert final["status"] == "unknown" and len(wire.writes) == 3
    finally:
        await reopened.close()


async def test_concurrent_opposite_reconcile_evidence_cannot_regress_newer_success(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    await seed(app)
    try:
        with app.clients.bind("client-a"):
            _, args = await prepare(app)
            wire.lose, wire.after_processing = "comment", True
            assert (await command(app, "feedback.attachments.add", args))["status"] == "partial"
            delivered = copy.deepcopy(wire.comments)
            wire.comments.clear()
            waiting, release = asyncio.Event(), asyncio.Event()
            original = app.feedback.additions.persist

            async def delay_older(identity, receipt, **kwargs):
                if kwargs.get("reconciled") and receipt["audit"]["phases"]["comment"]["status"] == "unknown":
                    waiting.set()
                    await release.wait()
                return await original(identity, receipt, **kwargs)

            monkeypatch.setattr(app.feedback.additions, "persist", delay_older)
            older = asyncio.create_task(command(app, "feedback.attachments.reconcile",
                {"requestId": "older-absent-read", "feedbackId": FID, "additionRequestId": args["requestId"]}))
            try:
                await asyncio.wait_for(waiting.wait(), 3)
                wire.comments[:] = delivered
                await asyncio.wait_for(command(app, "feedback.attachments.reconcile",
                    {"requestId": "newer-positive-read", "feedbackId": FID, "additionRequestId": args["requestId"]}), 3)
                assert app.feedback.additions.load(args["requestId"])[1]["status"] == "submitted"
            finally:
                release.set()
                await asyncio.wait_for(older, 3)
            final = app.feedback.additions.load(args["requestId"])[1]
            assert final["status"] == "submitted" and final["commentStatus"] == "succeeded"
            assert final["audit"]["phases"]["comment"]["reconciledBy"] == "newer-positive-read"
            assert final["commentUrl"] == URL + "#issuecomment-123"
            assert len(wire.writes) == 5
    finally:
        await app.close()
    reopened = AppService(tmp_path, workspace=tmp_path)
    try:
        assert reopened.feedback.additions.load(args["requestId"])[1]["status"] == "submitted"
    finally:
        await reopened.close()


def test_reconcile_merge_refuses_changed_phase_or_destination_binding():
    phase = {"phaseId": "addition:comment", "status": "unknown", "endpoint": "fixture",
             "prerequisites": {"issueId": 42}, "inputFingerprint": "exact-input"}
    receipt = {"requestId": "addition", "feedbackId": FID, "clientId": "client-a", "action": "feedback.attachments.add",
               "url": URL, "audit": {"manifest": [], "review": {"url": URL},
               "phases": {"comment": phase, "ref": {**phase, "phaseId": "addition:ref"}}}}
    for mutate in (
        lambda row: row.update(clientId="client-b"),
        lambda row: row["audit"]["review"].update(url="https://invalid.example/issue"),
        lambda row: row["audit"]["phases"]["comment"].update(inputFingerprint="changed"),
        lambda row: row["audit"]["phases"]["comment"].update(prerequisites={"issueId": 43}),
    ):
        changed = copy.deepcopy(receipt)
        mutate(changed)
        with pytest.raises(ValueError):
            additions.Additions.merge_reconciled(copy.deepcopy(receipt), changed)


async def test_initial_upload_and_correction_comment_wire_remain_unchanged(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    calls = []
    prefix = "repos/" + REPO
    url = URL
    async def initial_wire(endpoint, payload, *, method=None):
        assert method is None
        calls.append((endpoint, copy.deepcopy(payload)))
        if payload is None:
            return {"private": True}
        if endpoint.endswith("/git/refs"):
            return {"object": {"sha": "c" * 40}}
        if endpoint.endswith("/issues"):
            return {"html_url": url}
        return {"sha": "c" * 40 if endpoint.endswith("/git/commits") else "a" * 40}
    monkeypatch.setattr(feedback, "github_api", initial_wire)
    monkeypatch.setattr(feedback.shutil, "which", lambda _: "/fixture/gh")
    try:
        await app.dispatch("feedback.attachment.add", {"requestId": "initial-stage-file", "name": "initial.txt",
                            "base64": base64.b64encode(b"initial exact bytes").decode()})
        row = app.state["view"]["feedbackDraft"]["attachments"][0]
        args = {"requestId": FID, "title": "Original", "body": "Original", "category": "bug",
                "includeDiagnostics": False, "attachmentIds": [row["id"]]}
        app.feedback.accept(args)
        await app.feedback.send(FID)
        links = [{"id": row["id"], "name": "initial.txt", "mime": row["mime"], "size": len(b"initial exact bytes"),
                  "visibility": "private", "url": "https://github.com/" + REPO + "/blob/" + "c" * 40 + "/" + row["id"] + "/initial.txt"}]
        body = "Original\n\n---\nCategory: Bug report\n\n<!-- amplifier-feedback:" + FID + " -->" + feedback_attachments.markdown(links)
        expected = [(prefix, None), (prefix + "/git/blobs", {"encoding": "base64", "content": base64.b64encode(b"initial exact bytes").decode()}),
            (prefix + "/git/trees", {"tree": [{"path": row["id"] + "/initial.txt", "mode": "100644", "type": "blob", "sha": "a" * 40}]}),
            (prefix + "/git/commits", {"message": "Feedback attachments " + FID, "tree": "a" * 40, "parents": []}),
            (prefix + "/git/refs", {"ref": "refs/heads/feedback-assets/" + FID, "sha": "c" * 40}),
            (prefix + "/issues", {"title": "Original", "body": body})]
        assert json.dumps(calls, sort_keys=True) == json.dumps(expected, sort_keys=True)
        # Existing correction rendering and its POST marker are not repurposed
        # as original-body edits or optional file-addition payloads.
        from amplifier_web.feedback_lifecycle import revision
        wire = Wire(app)
        wire.issue["updated_at"] = "fixture-revision"
        async def correction_wire(endpoint, payload, **kwargs):
            if payload is not None:
                assert kwargs.get("method") is None and endpoint == prefix + "/issues/42/comments"
                wire.calls.append((endpoint, copy.deepcopy(payload)))
                return {"id": 123, "html_url": URL + "#issuecomment-123", "user": {"id": 7}}
            return await wire(endpoint, payload, **kwargs)
        monkeypatch.setattr(feedback, "github_api", correction_wire)
        async with app.lock:
            assert app.feedback.followups.accept("feedback.update",
                {"requestId": "correction-unchanged", "feedbackId": FID, "title": "Corrected", "body": "Details",
                 "expectedRevision": revision(wire.issue)}, "ui")
            app._save_changes(globals={"feedback"})
        await app.feedback.followups.run("correction-unchanged")
        assert len(wire.writes) == 1 and wire.writes[0][0] == prefix + "/issues/42/comments"
        saved = json.loads(app.db.execute("SELECT receipt FROM feedback_followups WHERE id='correction-unchanged'").fetchone()[0])
        assert saved["status"] == "submitted"
        source = revision(wire.issue)
        assert wire.writes[0][1] == {"body": "## Feedback correction\n\n### Updated title\nCorrected\n\n### Updated description\nDetails"
            "\n\nOriginal report retained. This is a correction version, not an overwrite."
            "\n\n<!-- amplifier-feedback-correction:correction-unchanged source:" + source + " -->"
            "\n\n<!-- amplifier-feedback-comment:correction-unchanged -->"}
        assert wire.issue["body"].startswith("Original\n")
    finally:
        await app.close()


async def test_addition_review_cannot_be_retargeted_to_other_client_or_report(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    try:
        await seed(app)
        app.feedback.accept({"requestId": "other-feedback", "title": "Other", "body": "Other", "category": "bug", "includeDiagnostics": False})
        await app.feedback.update("other-feedback", status="submitted", url=URL.replace("/42", "/43"))
        with app.clients.bind("client-a"):
            _, args = await prepare(app)
            with pytest.raises(AppError):
                await command(app, "feedback.attachments.add", {**args, "feedbackId": "other-feedback"})
            assert app.feedback.additions.project()["stagingReceipts"][0]["feedbackId"] == FID
        with app.clients.bind("client-b"):
            await stage(app, "other-client-stage", b"other client bytes")
            with pytest.raises(AppError):
                await command(app, "feedback.attachments.add", args)
            assert all(row["requestId"] == "other-client-stage" for row in app.feedback.additions.project()["stagingReceipts"])
        assert not wire.writes
    finally:
        await app.close()


async def test_lost_stage_ack_reads_same_binding_after_restart_without_resurrection(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    wire = Wire(app)
    monkeypatch.setattr(feedback, "github_api", wire)
    await seed(app)
    with app.clients.bind("client-a"):
        row, args = await stage(app)
        observed = app.feedback.additions.project()["stagingReceipts"]
        assert observed == [{"requestId": args["requestId"], "feedbackId": FID, "id": row["id"],
                             "size": row["size"], "sha256": row["sha256"], "selected": True}]
    await app.close()
    reopened = AppService(tmp_path, workspace=tmp_path)
    try:
        with reopened.clients.bind("client-a"):
            assert reopened.feedback.additions.project()["stagingReceipts"] == observed
            await reopened.dispatch("feedback.attachment.add", args)
            assert reopened.feedback.additions.project()["attachmentDrafts"][FID] == [row]
            with pytest.raises(AppError):
                await reopened.dispatch("feedback.attachment.add", {**args, "base64": base64.b64encode(b"changed bytes").decode()})
            await reopened.dispatch("feedback.attachment.remove", {"feedbackId": FID, "id": row["id"]})
            await reopened.dispatch("feedback.attachment.add", args)
            assert not reopened.feedback.additions.project()["attachmentDrafts"]
            assert reopened.feedback.additions.project()["stagingReceipts"][0]["selected"] is False
        with reopened.clients.bind("client-b"):
            assert not reopened.feedback.additions.project()["stagingReceipts"]
        assert not wire.calls
    finally:
        await reopened.close()