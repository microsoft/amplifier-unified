"""Owned-report boundaries and at-most-once comments; never contact GitHub."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web import feedback
from amplifier_web.service import AppError, AppService
from amplifier_web.updates import UpdateManager

FEEDBACK_ID = "feedback-original"
URL = feedback.ISSUES_URL + "/42"


async def settle(app):
    if app.tasks:
        await asyncio.gather(*list(app.tasks))


async def seed(app):
    app.feedback.accept({"requestId": FEEDBACK_ID, "title": "Original report", "body": "Original body", "category": "bug", "includeDiagnostics": False})
    await app.feedback.update(FEEDBACK_ID, status="submitted", url=URL)


@pytest.fixture
def api(monkeypatch):
    issue = {"number": 42, "html_url": URL, "user": {"id": 7, "login": "owner"}, "title": "Original report", "state": "open",
             "body": "Original body\n<!-- amplifier-feedback:" + FEEDBACK_ID + " -->", "updated_at": "2026-09-20T00:00:00Z"}
    async def response(endpoint, payload):
        if endpoint == "user":
            return {"id": 7, "login": "owner"}
        if payload is not None:
            return {"id": 123, "html_url": URL + "#issuecomment-123", "user": {"id": 7}, "created_at": "2026-09-20T00:01:00Z"}
        if "/comments?" in endpoint:
            return [{"id": 99, "body": "A maintainer response", "user": {"id": 8, "login": "maintainer"}}]
        return issue
    mock = AsyncMock(side_effect=response)
    mock.issue = issue
    monkeypatch.setattr(feedback, "github_api", mock)
    return mock


def args(**changes):
    return {"requestId": "followup-comment-1", "feedbackId": FEEDBACK_ID, "body": "More reproduction details.\nLiteral `text` $(no-command)", **changes}


async def test_agent_comment_ui_retry_and_restart_share_one_post(tmp_path, api):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await seed(app)
        await app.dispatch("session.create", {"title": "Private title"})
        response = await app.app_bridge("dispatch", {"action": "feedback.comment", "args": args(), "id": "agent-followup"}, app._session()["id"])
        await settle(app)
        assert response["accepted"]
        result = app.state["feedback"]["followups"][0]
        assert result["status"] == "submitted" and result["author"]["id"] == 7
        assert result["commentUrl"] == URL + "#issuecomment-123"
        await app.dispatch("feedback.comment", args(), command_id="ui-retry")
        await settle(app)
        posts = [call for call in api.call_args_list if call.args[1] is not None]
        assert len(posts) == 1
        assert posts[0].args == ("repos/bkrabach/amplifier-unified/issues/42/comments", {"body": args()["body"] + "\n\n<!-- amplifier-feedback-comment:followup-comment-1 -->"})
        assert "Private title" not in json.dumps(posts[0].args)
        assert {"feedback.get", "feedback.comment"} <= {item["name"] for item in app.get_actions()}
        with pytest.raises(AppError, match="different contents"):
            await app.dispatch("feedback.comment", args(body="Changed"))
    finally:
        await app.close()
    reopened = AppService(tmp_path, workspace=tmp_path)
    try:
        before = api.call_count
        await reopened.dispatch("feedback.comment", args())
        await settle(reopened)
        assert api.call_count == before
        assert reopened.state["feedback"]["followups"][0]["status"] == "submitted"
    finally:
        await reopened.close()


async def test_get_reads_report_and_comments_with_explicit_pagination(tmp_path, api):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await seed(app)
        request = {"requestId": "followup-read-1", "feedbackId": FEEDBACK_ID, "page": 2}
        await app.dispatch("feedback.get", request)
        await settle(app)
        report = app.state["feedback"]["report"]
        assert report["page"] == 2 and not report["hasMore"]
        assert report["comments"][0]["author"]["login"] == "maintainer"
        assert report["body"] == api.issue["body"]
        assert all(call.args[1] is None for call in api.call_args_list)
        assert api.call_args.args[0].endswith("/comments?per_page=20&page=2")
        assert "report" not in app.state["feedback"]["followups"][0]
        before = api.call_count
        api.issue["body"] = "Changed remotely"
        await app.dispatch("feedback.get", request)
        await settle(app)
        assert api.call_count == before
        assert app.state["feedback"]["report"]["body"] != "Changed remotely"
    finally:
        await app.close()


@pytest.mark.parametrize("change", [{"user": {"id": 8}}, {"body": "Missing marker"}, {"html_url": "https://example.com/42"}, {"number": 41}, {"pull_request": {}}])
async def test_report_provenance_and_signed_in_owner_are_required(tmp_path, api, change):
    api.issue.update(change)
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await seed(app)
        await app.dispatch("feedback.comment", args())
        await settle(app)
        result = app.state["feedback"]["followups"][0]
        assert result["status"] == "failed" and result["code"] == "feedback_owner_mismatch"
        assert all(call.args[1] is None for call in api.call_args_list)
    finally:
        await app.close()


async def test_arbitrary_issue_ids_and_unsubmitted_reports_are_rejected_locally(tmp_path, api):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        with pytest.raises(AppError):
            await app.dispatch("feedback.comment", args(feedbackId="arbitrary-request"))
        with pytest.raises(AppError):
            await app.dispatch("feedback.get", {"requestId": "read-request", "feedbackId": "42"})
        app.feedback.accept({"requestId": FEEDBACK_ID, "title": "T", "body": "B", "category": "bug", "includeDiagnostics": False})
        with pytest.raises(AppError):
            await app.dispatch("feedback.comment", args())
        api.assert_not_awaited()
    finally:
        await app.close()


async def test_uncertain_post_is_not_repeated_or_reported_as_failure(tmp_path, api):
    original = api.side_effect
    async def lose_response(endpoint, payload):
        if payload is not None:
            raise TimeoutError("secret-error-fixture")
        return await original(endpoint, payload)
    api.side_effect = lose_response
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await seed(app)
        await app.dispatch("feedback.comment", args())
        await settle(app)
        assert app.state["feedback"]["followups"][0]["status"] == "unknown"
        assert "secret-error-fixture" not in json.dumps(app.state)
        await app.dispatch("feedback.comment", args())
        await settle(app)
        assert sum(call.args[1] is not None for call in api.call_args_list) == 1
    finally:
        await app.close()


async def test_crash_receipt_blocks_repost_and_updates_wait_for_followups(tmp_path, api):
    app = AppService(tmp_path, workspace=tmp_path)
    await seed(app)
    app.feedback.followups.accept("feedback.comment", args(), "ui")
    assert UpdateManager(app).busy()
    app._save()
    await app.close()
    reopened = AppService(tmp_path, workspace=tmp_path)
    try:
        assert reopened.state["feedback"]["followups"][0]["status"] == "unknown"
        await reopened.dispatch("feedback.comment", args())
        await settle(reopened)
        api.assert_not_awaited()
        reopened.state["updates"] = {"phase": "activating"}
        with pytest.raises(AppError, match="activating"):
            await reopened.dispatch("feedback.comment", args(requestId="new-comment-request"))
    finally:
        await reopened.close()


async def test_read_failure_is_definite_and_never_sends_comment(tmp_path, api):
    api.side_effect = RuntimeError("secret-auth-error")
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await seed(app)
        await app.dispatch("feedback.comment", args())
        await settle(app)
        row = app.state["feedback"]["followups"][0]
        assert row["status"] == "failed" and "Nothing was posted" in row["message"]
        assert "secret-auth-error" not in json.dumps(app.state)
        assert api.call_count == 1
    finally:
        await app.close()


async def test_late_older_page_cannot_replace_newer_read_and_explicit_retry_restores_snapshot(tmp_path, api):
    original = api.side_effect
    waiting, release = asyncio.Event(), asyncio.Event()
    async def delayed(endpoint, payload):
        if endpoint.endswith("page=1"):
            waiting.set()
            await release.wait()
        return await original(endpoint, payload)
    api.side_effect = delayed
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await seed(app)
        older = {"requestId": "read-older-page", "feedbackId": FEEDBACK_ID, "page": 1}
        await app.dispatch("feedback.get", older)
        await asyncio.wait_for(waiting.wait(), 3)
        await app.dispatch("feedback.get", {**older, "requestId": "read-newer-page", "page": 2})
        for _ in range(100):
            if app.state["feedback"].get("report"):
                break
            await asyncio.sleep(.01)
        assert app.state["feedback"]["report"]["page"] == 2
        release.set()
        await settle(app)
        assert app.state["feedback"]["report"]["requestId"] == "read-newer-page"
        before = api.call_count
        await app.dispatch("feedback.get", older)
        await settle(app)
        assert app.state["feedback"]["report"]["page"] == 1
        assert api.call_count == before
    finally:
        release.set()
        await app.close()


async def test_clients_keep_independent_report_pages_and_retries(tmp_path, api):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await seed(app)
        for client, page in (("reader-a", 1), ("reader-b", 2)):
            app.clients.attach(client)
            with app.clients.bind(client):
                await app.dispatch("feedback.get", {"requestId": client + "-read", "feedbackId": FEEDBACK_ID, "page": page})
            await settle(app)
        with app.clients.bind("reader-a"):
            assert app.browser_state()["feedback"]["report"]["page"] == 1
            before = api.call_count
            await app.dispatch("feedback.get", {"requestId": "reader-a-read", "feedbackId": FEEDBACK_ID, "page": 1})
            assert api.call_count == before
        with app.clients.bind("reader-b"):
            snapshot = app.browser_state()["feedback"]
            assert snapshot["readRequestId"] == "reader-b-read"
            assert snapshot["report"]["page"] == 2
        app.clients.attach("reader-copy", resume="reader-a")
        with app.clients.bind("reader-copy"):
            assert app.browser_state()["feedback"]["report"]["page"] == 1
    finally:
        await app.close()
