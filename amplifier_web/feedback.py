"""Explicit, durable feedback submissions to the application's GitHub repository.

An accepted request is attempted at most once. GitHub's create-issue endpoint has
no idempotency key: an uncertain response is retained, never automatically posted
again. UI and agent callers share the same request identities and receipts.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import platform
import re
import shutil
import time

from . import __version__

REPOSITORY = "bkrabach/amplifier-unified"
ISSUES_URL = "https://github.com/" + REPOSITORY + "/issues"
CATEGORIES = {"bug": "Bug report", "idea": "Feature idea", "question": "Question", "other": "Other feedback"}
UNKNOWN = "GitHub may have received this feedback. Check the repository issues before starting a new submission; this request will not be posted again."


def definitions(schema, string):
    return {
        "feedback.submit": (
            "Create a GitHub issue in bkrabach/amplifier-unified using feedback the user asked to send. Include only the reviewed title/body; diagnostics are opt-in (app version and OS family). Reuse requestId and identical payload after a lost response; never create a new ID merely to retry. Read /feedback/requests for durable results. Unknown outcomes are not reposted.",
            schema({"requestId": {"type": "string", "pattern": "^[A-Za-z0-9_-]{8,100}$"},
                    "title": {**string(200), "minLength": 1}, "body": {**string(16000), "minLength": 1},
                    "category": {"enum": list(CATEGORIES)}, "includeDiagnostics": {"type": "boolean"}},
                   ["requestId", "title", "body", "category"]),
        ),
    }


async def create_issue(title, body):
    """Structured stdin keeps user text out of shell evaluation and process args."""
    executable = shutil.which("gh")
    if not executable:
        raise FileNotFoundError("GitHub CLI is not installed")
    child = await asyncio.create_subprocess_exec(
        executable, "api", "--hostname", "github.com", "--method", "POST",
        "repos/" + REPOSITORY + "/issues", "--input", "-",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"},
    )
    try:
        output, _ = await asyncio.wait_for(child.communicate(json.dumps({"title": title, "body": body}).encode()), 45)
    except BaseException:
        if child.returncode is None:
            child.kill()
        await child.wait()
        raise
    if child.returncode:
        # Do not publish CLI stderr, credentials, paths, or a false failure claim
        # after a request may already have reached GitHub.
        raise RuntimeError("GitHub did not acknowledge issue creation")
    result = json.loads(output)
    url = result.get("html_url", "")
    if not isinstance(url, str) or not re.fullmatch(re.escape(ISSUES_URL) + r"/[1-9][0-9]*", url):
        raise ValueError("GitHub returned an invalid issue receipt")
    return url


class Feedback:
    def __init__(self, service):
        self.service = service
        service.db.execute("CREATE TABLE IF NOT EXISTS feedback_requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, receipt TEXT NOT NULL)")
        for identity, text in service.db.execute("SELECT id,receipt FROM feedback_requests").fetchall():
            receipt = json.loads(text)
            if receipt["status"] in {"queued", "sending"}:
                receipt.update(status="unknown", message=UNKNOWN, updatedAt=time.time())
                service.db.execute("UPDATE feedback_requests SET receipt=? WHERE id=?", (json.dumps(receipt), identity))
        self.refresh()

    def refresh(self, identity=None):
        # Active sends must never disappear from the updater's idle checks.
        rows = self.service.db.execute("SELECT receipt FROM feedback_requests WHERE json_extract(receipt,'$.status') IN ('queued','sending') ORDER BY rowid DESC").fetchall()
        rows += self.service.db.execute("SELECT receipt FROM feedback_requests WHERE json_extract(receipt,'$.status') NOT IN ('queued','sending') ORDER BY rowid DESC LIMIT 20").fetchall()
        receipts = [json.loads(row[0]) for row in rows]
        if identity and not any(item['requestId'] == identity for item in receipts):
            row = self.service.db.execute("SELECT receipt FROM feedback_requests WHERE id=?", (identity,)).fetchone()
            if row:
                receipts.append(json.loads(row[0]))
        self.service.state["feedback"] = {"repository": REPOSITORY, "issuesUrl": ISSUES_URL,
            "diagnostics": {"appVersion": __version__, "osFamily": platform.system()},
            "requests": receipts}

    def accept(self, args):
        """Called under service.lock, committed with the shared action receipt."""
        from .service import AppError
        args = copy.deepcopy(args)
        if not args["title"].strip() or not args["body"].strip():
            raise AppError("Enter a title and feedback before sending.")
        identity = args["requestId"]
        fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        row = self.service.db.execute("SELECT fingerprint FROM feedback_requests WHERE id=?", (identity,)).fetchone()
        if row:
            if row[0] != fingerprint:
                raise AppError("This feedback request ID already belongs to different text. Start new feedback for a new intent.", 409)
            self.refresh(identity)
            return False
        active = self.service.db.execute("SELECT count(*) FROM feedback_requests WHERE json_extract(receipt,'$.status') IN ('queued','sending')").fetchone()[0]
        if active >= 8:
            raise AppError("Feedback is still sending. Wait for a submission to finish before sending more.", 409)
        receipt = {"requestId": identity, "title": args["title"], "category": args["category"],
                   "status": "queued", "message": "Sending feedback to GitHub…", "createdAt": time.time()}
        self.service.db.execute("INSERT INTO feedback_requests VALUES (?,?,?,?)", (identity, fingerprint, json.dumps(args), json.dumps(receipt)))
        self.refresh()
        return True

    async def update(self, identity, **fields):
        async with self.service.lock:
            row = self.service.db.execute("SELECT receipt FROM feedback_requests WHERE id=?", (identity,)).fetchone()
            receipt = {**json.loads(row[0]), **fields, "updatedAt": time.time()}
            self.service.db.execute("UPDATE feedback_requests SET receipt=? WHERE id=?", (json.dumps(receipt), identity))
            self.refresh(identity)
            self.service._publish()

    async def send(self, identity):
        row = self.service.db.execute("SELECT payload,receipt FROM feedback_requests WHERE id=?", (identity,)).fetchone()
        args, receipt = map(json.loads, row)
        if receipt["status"] != "queued":
            return
        if not shutil.which("gh"):
            await self.update(identity, status="failed", message="Install GitHub CLI and sign in with access to this private repository, then start a new submission. Nothing was sent.")
            return
        await self.update(identity, status="sending")
        body = args["body"] + "\n\n---\nCategory: " + CATEGORIES[args["category"]]
        if args.get("includeDiagnostics"):
            facts = self.service.state["feedback"]["diagnostics"]
            body += "\n\nApp version: " + facts["appVersion"] + "\nOS family: " + facts["osFamily"]
        body += "\n\n<!-- amplifier-feedback:" + identity + " -->"
        try:
            url = await create_issue(args["title"], body)
        except asyncio.CancelledError:
            await self.update(identity, status="unknown", message=UNKNOWN)
            raise
        except Exception:
            await self.update(identity, status="unknown", message=UNKNOWN)
        else:
            await self.update(identity, status="submitted", message="Feedback sent. Thank you.", url=url)
