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
                return {"committer": {"id": self.account["id"]}}
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


async def prepare(app):
    row, _ = await stage(app)
    review = await command(app, "feedback.attachments.review", {"requestId": "review-request-1", "feedbackId": FID})
    assert review["status"] == "completed"
    args = {"requestId": "addition-request", "feedbackId": FID, "reviewRequestId": review["requestId"],
            "confirmedFiles": [{"id": row["id"], "sha256": row["sha256"]}], "comment": "Explicit file comment"}
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