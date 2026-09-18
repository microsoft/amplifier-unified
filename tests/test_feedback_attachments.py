"""Local files and mocked GitHub only; no live uploads or issue creation."""
import base64
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web import attachments, feedback, feedback_attachments
from amplifier_web.service import AppError, AppService


def submission(**values):
    return {"requestId": "attachment-feedback-1", "title": "Screenshot report", "body": "Please inspect these files.", "category": "bug", **values}


async def settle(app):
    if app.tasks:
        import asyncio
        await asyncio.gather(*list(app.tasks))


async def stage(app, data=b"private-file-data", name="report.txt", identity="stage-request-1"):
    args = {"requestId": identity, "name": name, "base64": base64.b64encode(data).decode()}
    await app.dispatch("feedback.attachment.add", args)
    return app.state["view"]["feedbackDraft"]["attachments"][-1], args


@pytest.fixture
def github(monkeypatch):
    calls = []
    async def api(endpoint, payload):
        calls.append((endpoint, payload))
        if payload is None:
            return {"private": True}
        if endpoint.endswith("/git/refs"):
            return {"object": {"sha": "c" * 40}}
        return {"sha": "c" * 40 if endpoint.endswith("/git/commits") else "a" * 40}
    mocked = AsyncMock(side_effect=api)
    issue = AsyncMock(return_value=feedback.ISSUES_URL + "/42")
    monkeypatch.setattr(feedback, "github_api", mocked)
    monkeypatch.setattr(feedback, "create_issue", issue)
    monkeypatch.setattr(feedback.shutil, "which", lambda _: "/fixture/gh")
    return mocked, issue, calls


async def test_staging_is_local_shared_durable_idempotent_and_does_not_touch_chat(tmp_path, github):
    api, issue, _ = github
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch("session.create", {"title": "Separate chat"})
        row, args = await stage(app)
        assert row["url"] == "/api/attachments/" + row["id"]
        assert "sha256" not in row
        assert b"private-file-data" not in json.dumps(app.get_state()).encode()
        assert not app._session().get("draftAttachments")
        await app.app_bridge("dispatch", {"action": "feedback.attachment.add", "args": args, "id": "agent-retry"}, app._session()["id"])
        assert len(app.state["view"]["feedbackDraft"]["attachments"]) == 1
        with pytest.raises(AppError, match="different file"):
            await app.dispatch("feedback.attachment.add", {**args, "name": "different.txt"})
        api.assert_not_awaited()
        issue.assert_not_awaited()
    finally:
        await app.close()
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        assert app.state["view"]["feedbackDraft"]["attachments"] == [row]
        await app.dispatch("feedback.attachment.remove", {"id": row["id"]})
        await app.dispatch("feedback.attachment.add", args)
        assert not app.state["view"]["feedbackDraft"]["attachments"]
    finally:
        await app.close()


async def test_explicit_submission_uploads_only_selected_bytes_in_isolated_private_commit(tmp_path, github):
    api, issue, calls = github
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        included, _ = await stage(app, b"reviewed only", "../report [one].txt")
        excluded, _ = await stage(app, b"not included", "secret.txt", "stage-request-2")
        args = submission(attachmentIds=[included["id"]])
        await app.dispatch("feedback.submit", args)
        await settle(app)
        assert app.state["feedback"]["requests"][0]["status"] == "submitted"
        blobs = [value for path, value in calls if path.endswith("/git/blobs")]
        assert len(blobs) == 1 and base64.b64decode(blobs[0]["content"]) == b"reviewed only"
        commit = next(value for path, value in calls if path.endswith("/git/commits"))
        assert commit["parents"] == []
        ref = next(value for path, value in calls if path.endswith("/git/refs"))
        assert ref["ref"] == "refs/heads/feedback-assets/attachment-feedback-1"
        body = issue.call_args.args[1]
        assert included["id"] in body and excluded["id"] not in body
        assert "report \\[one\\].txt" in body and "report%20%5Bone%5D.txt" in body
        assert "/blob/" + "c" * 40 + "/" in body
        assert str(tmp_path) not in body and "token=" not in body
        count = api.await_count
        await app.dispatch("feedback.submit", args)
        await settle(app)
        assert api.await_count == count
        issue.assert_awaited_once()
    finally:
        await app.close()


async def test_arbitrary_chat_files_cannot_be_attached_by_forging_draft_metadata(tmp_path, github):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        row = attachments.save(tmp_path, "chat-private.txt", base64.b64encode(b"private chat file").decode())
        app.state["view"]["feedbackDraft"] = {"attachments": [row]}
        with pytest.raises(AppError, match="attachment"):
            await app.dispatch("feedback.submit", submission(attachmentIds=[row["id"]]))
        assert not app.state["feedback"]["requests"]
        github[0].assert_not_awaited()
        github[1].assert_not_awaited()
    finally:
        await app.close()


@pytest.mark.parametrize("change", ["bytes", "symlink", "parent-symlink"])
async def test_changed_or_escaped_attachment_rejected_before_send(tmp_path, github, change):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        row, _ = await stage(app, b"original")
        path, _ = attachments.file_path(tmp_path, row["id"])
        if change == "bytes":
            path.write_bytes(b"changed!")
        elif change == "symlink":
            outside = tmp_path / "outside.txt"
            outside.write_bytes(b"original")
            path.unlink()
            path.symlink_to(outside)
        else:
            old = path.parent
            moved = tmp_path / "moved"
            old.rename(moved)
            old.symlink_to(moved, target_is_directory=True)
        with pytest.raises(AppError, match="attachment"):
            await app.dispatch("feedback.submit", submission(attachmentIds=[row["id"]]))
        github[0].assert_not_awaited()
    finally:
        await app.close()


async def test_after_acceptance_changes_fail_without_upload_and_frozen_draft_blocks_remove(tmp_path, github):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        row, _ = await stage(app, b"original")
        args = submission(attachmentIds=[row["id"]])
        app.feedback.accept(args)
        app.state["view"]["feedbackDraft"]["pending"] = args
        with pytest.raises(AppError, match="frozen"):
            await app.dispatch("feedback.attachment.remove", {"id": row["id"]})
        path, _ = attachments.file_path(tmp_path, row["id"])
        path.write_bytes(b"changed!")
        await app.feedback.send(args["requestId"])
        receipt = app.state["feedback"]["requests"][0]
        assert receipt["status"] == "failed" and "Nothing was sent" in receipt["message"]
        github[0].assert_not_awaited()
        github[1].assert_not_awaited()
    finally:
        await app.close()


async def test_lost_upload_response_keeps_unknown_receipt_and_never_replays(tmp_path, github):
    api, issue, _ = github
    async def lost(endpoint, payload):
        if payload is None:
            return {"private": True}
        raise TimeoutError("private credential-bearing exception")
    api.side_effect = lost
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        row, _ = await stage(app)
        args = submission(attachmentIds=[row["id"]])
        await app.dispatch("feedback.submit", args)
        await settle(app)
        receipt = app.state["feedback"]["requests"][0]
        assert receipt["status"] == "unknown"
        assert receipt["attachmentsUrl"].endswith("/tree/feedback-assets/attachment-feedback-1")
        assert "credential-bearing" not in json.dumps(app.state)
        await app.dispatch("feedback.submit", args)
        await settle(app)
        assert api.await_count == 2
        issue.assert_not_awaited()
    finally:
        await app.close()


async def test_public_destination_refused_before_upload(tmp_path, github):
    api, issue, _ = github
    api.side_effect = None
    api.return_value = {"private": False}
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        row, _ = await stage(app)
        await app.dispatch("feedback.submit", submission(attachmentIds=[row["id"]]))
        await settle(app)
        receipt = app.state["feedback"]["requests"][0]
        assert receipt["status"] == "failed" and "Nothing was sent" in receipt["message"]
        api.assert_awaited_once_with("repos/" + feedback.REPOSITORY, None)
        issue.assert_not_awaited()
    finally:
        await app.close()


async def test_file_count_and_total_limits_before_extra_storage(tmp_path, github, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        monkeypatch.setattr(feedback_attachments, "MAX_TOTAL_BYTES", 10)
        await stage(app, b"12345678")
        with pytest.raises(AppError, match="24 MB"):
            await stage(app, b"123", identity="stage-request-2")
        assert len(list((tmp_path / "attachments").iterdir())) == 1
        monkeypatch.setattr(feedback_attachments, "MAX_FILES", 1)
        with pytest.raises(AppError, match="8 files"):
            await stage(app, b"x", identity="stage-request-3")
        github[0].assert_not_awaited()
    finally:
        await app.close()


def test_verified_read_returns_exact_bytes_and_metadata_hash(tmp_path):
    data = b"sample bytes"
    row = attachments.save(tmp_path, "report.txt", base64.b64encode(data).decode())
    row["sha256"] = hashlib.sha256(data).hexdigest()
    assert feedback_attachments.read_verified(tmp_path, row) == data


def test_directory_swap_between_validation_and_open_cannot_escape_store(tmp_path, monkeypatch):
    data = b"approved bytes"
    row = attachments.save(tmp_path, "report.txt", base64.b64encode(data).decode())
    row["sha256"] = hashlib.sha256(data).hexdigest()
    directory = attachments.location(tmp_path, row["id"])
    moved = tmp_path / "moved"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "content").write_bytes(b"secret outside")
    actual_open = feedback_attachments.os.open
    def race(path, flags, **kwargs):
        if path == row["id"] and kwargs.get("dir_fd") is not None:
            directory.rename(moved)
            directory.symlink_to(outside, target_is_directory=True)
        return actual_open(path, flags, **kwargs)
    monkeypatch.setattr(feedback_attachments.os, "open", race)
    with pytest.raises(OSError):
        feedback_attachments.read_verified(tmp_path, row)
