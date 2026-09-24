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
from . import attachments, feedback_attachments, feedback_diagnostics

REPOSITORY = "microsoft/amplifier-unified"
ISSUES_URL = "https://github.com/" + REPOSITORY + "/issues"
# Existing receipts keep their original repository and issue identity.
RECEIPT_REPOSITORIES = (REPOSITORY, "bkrabach/amplifier-unified")
CATEGORIES = {"bug": "Bug report", "idea": "Feature idea", "question": "Question", "other": "Other feedback"}
UNKNOWN = "GitHub may have received this feedback. Check the repository issues before starting a new submission; this request will not be posted again."
UNKNOWN_FILES = "Files may have been stored in the repository, and an issue may have been created. Check the repository issues and attachment branch before starting a new submission; this request will not be posted again."


class ExcerptConsentError(ValueError):
    """A safe, actionable consent error with no user text or file paths."""


def definitions(schema, string):
    from .feedback_followup import definitions as followup_definitions
    request_id = {"type": "string", "pattern": "^[A-Za-z0-9_-]{8,100}$"}
    attachment_id = {"type": "string", "pattern": "^[a-f0-9]{32}$"}
    from .feedback_excerpts import definitions as excerpt_definitions
    return {
        **excerpt_definitions(schema, string),
        **followup_definitions(schema, string),
        "feedback.diagnostics": (
            "Preview complete allowlisted reproduction facts on demand, without posting feedback, reading logs or running a conversation. With requestId, read only the immutable diagnostics saved with that local feedback receipt (null when opted out). Host active component generation does not verify a running worker. No raw logs, message text, paths or credentials.",
            schema({"requestId": request_id, "deviceDiagnostics": feedback_diagnostics.DEVICE_SCHEMA}, []),
        ),
        "feedback.submit": (
            "Create a GitHub issue in microsoft/amplifier-unified using feedback the user asked to send. Include only reviewed title/body and explicit attachmentIds staged with feedback.attachment.add. Selected files upload to a feedback-assets branch and remain in repository history. Ordinary files require a private repository; reviewed excerpts use their explicitly approved public/private visibility. Allowlisted reproduction diagnostics are included by default; includeDiagnostics:false opts out. deviceDiagnostics contains only the submitting browser facts defined by its schema. Never pass raw logs, paths or credentials. Conversation text requires the explicit feedback.excerpt.review/stage flow and confirmExcerpts:true plus confirmedExcerpts:[{id,sha256}] for the exact complete staged excerpt set after user review. Read each hash from view.feedbackDraft.attachments[].excerpt.sha256; changes require fresh consent. Reuse requestId and identical payload after a lost response; never create a new ID merely to retry. Read /feedback/requests for durable results. Unknown outcomes are not reposted.",
            schema({"requestId": request_id,
                    "title": {**string(200), "minLength": 1}, "body": {**string(16000), "minLength": 1},
                    "category": {"enum": list(CATEGORIES)}, "confirmExcerpts": {"type": "boolean"}, "includeDiagnostics": {"type": "boolean"},
                    "confirmedExcerpts": {"type": "array", "maxItems": feedback_attachments.MAX_FILES, "uniqueItems": True,
                        "items": schema({"id": attachment_id, "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}}, ["id", "sha256"])},
                    "deviceDiagnostics": feedback_diagnostics.DEVICE_SCHEMA,
                    "attachmentIds": {"type": "array", "items": attachment_id, "maxItems": feedback_attachments.MAX_FILES, "uniqueItems": True}},
                   ["requestId", "title", "body", "category"]),
        ),
        "feedback.attachment.add": (
            "Stage a reviewed file/image locally in the shared feedback draft, up to 8 MB each, 8 files and 24 MB total. Nothing uploads until explicit feedback.submit. Pass base64 bytes, a display name, and a stable requestId; exact retries do not add twice. See view.feedbackDraft.attachments for preview metadata and IDs.",
            schema({"requestId": request_id, "name": {**string(200), "minLength": 1}, "base64": string(12000000)}, ["requestId", "name", "base64"]),
        ),
        "feedback.attachment.remove": (
            "Remove a locally staged file from the shared feedback draft before submitting. Submitted files cannot be removed this way; they are retained in repository history with the visibility approved at submission.",
            schema({"id": attachment_id}, ["id"]),
        ),
    }


async def github_api(endpoint, payload, *, method=None):
    """Structured stdin keeps user text out of shell evaluation and process args."""
    executable = shutil.which("gh")
    if not executable:
        raise FileNotFoundError("GitHub CLI is not installed")
    child = await asyncio.create_subprocess_exec(
        executable, "api", "--hostname", "github.com", "--method", method or ("POST" if payload is not None else "GET"),
        endpoint, *(["--input", "-"] if payload is not None else []),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"},
    )
    try:
        output, _ = await asyncio.wait_for(child.communicate(json.dumps(payload).encode() if payload is not None else None), 45)
    except BaseException:
        if child.returncode is None:
            child.kill()
        await child.wait()
        raise
    if child.returncode:
        # Do not publish CLI stderr, credentials, paths, or a false failure claim
        # after a request may already have reached GitHub.
        raise RuntimeError("GitHub did not acknowledge the request")
    result = json.loads(output)
    if not isinstance(result, (dict, list)):
        raise ValueError("GitHub returned an invalid receipt")
    return result


async def create_issue(title, body):
    result = await github_api("repos/" + REPOSITORY + "/issues", {"title": title, "body": body})
    url = result.get("html_url", "")
    if not isinstance(url, str) or not re.fullmatch(re.escape(ISSUES_URL) + r"/[1-9][0-9]*", url):
        raise ValueError("GitHub returned an invalid issue receipt")
    return url


class Feedback:
    def __init__(self, service):
        self.service = service
        service.db.execute("CREATE TABLE IF NOT EXISTS feedback_requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, receipt TEXT NOT NULL)")
        service.db.execute("CREATE TABLE IF NOT EXISTS feedback_attachments (request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, metadata TEXT NOT NULL)")
        for identity, text in service.db.execute("SELECT id,receipt FROM feedback_requests").fetchall():
            receipt = json.loads(text)
            if receipt["status"] in {"queued", "sending"}:
                receipt.update(status="unknown", message=UNKNOWN_FILES if receipt.get("attachments") else UNKNOWN, updatedAt=time.time())
                service.db.execute("UPDATE feedback_requests SET receipt=? WHERE id=?", (json.dumps(receipt), identity))
        from .feedback_followup import Followups
        self.followups = Followups(self)
        from .feedback_excerpts import Excerpts
        self.excerpts = Excerpts(self)
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
            "diagnostics": feedback_diagnostics.build_facts(),
            "requests": receipts}
        self.followups.refresh()

    def attachment_command(self, action, args):
        """Local-only staging; identical accepted add retries never resurrect removals."""
        from .service import AppError
        draft = self.service.state.setdefault("view", {}).setdefault("feedbackDraft", {})
        if draft.get("pending"):
            raise AppError("This submission is frozen. Start new feedback to change its attachments.", 409)
        selected = draft.setdefault("attachments", [])
        if action == "feedback.attachment.remove":
            draft["attachments"] = [row for row in selected if row["id"] != args["id"]]
            if len(draft["attachments"]) != len(selected):
                draft.update(confirmExcerpts=False, confirmedExcerpts=[])
            return
        fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        existing = self.service.db.execute("SELECT fingerprint FROM feedback_attachments WHERE request_id=?", (args["requestId"],)).fetchone()
        if existing:
            if existing[0] != fingerprint:
                raise AppError("This attachment request ID already belongs to a different file.", 409)
            return
        if len(selected) >= feedback_attachments.MAX_FILES:
            raise AppError("Attach up to 8 files per feedback submission.")
        try:
            decoded_size = len(args["base64"]) // 4 * 3 - (len(args["base64"]) - len(args["base64"].rstrip("=")))
            if sum(item["size"] for item in selected) + decoded_size > feedback_attachments.MAX_TOTAL_BYTES:
                raise ValueError("Feedback attachments can total up to 24 MB.")
            row = attachments.save(self.service.data_dir, args["name"], args["base64"])
            path, _ = attachments.file_path(self.service.data_dir, row["id"])
            row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, ValueError) as exc:
            raise AppError(str(exc)) from None
        self.service.db.execute("INSERT INTO feedback_attachments VALUES (?,?,?)", (args["requestId"], fingerprint, json.dumps(row)))
        selected.append({key: value for key, value in row.items() if key != "sha256"})
        draft.update(confirmExcerpts=False, confirmedExcerpts=[])

    def selected_files(self, args):
        selected = {row["id"] for row in self.service.state.get("view", {}).get("feedbackDraft", {}).get("attachments", [])}
        # Consent is a snapshot of the complete staged excerpt set. A boolean
        # alone could approve a later agent-added file the user never saw.
        approved = sorted((row["id"], row["sha256"]) for row in args.get("confirmedExcerpts", []))
        actual = []
        for identity in selected:
            saved = self.service.db.execute("SELECT metadata FROM feedback_attachments WHERE json_extract(metadata,'$.id')=?", (identity,)).fetchone()
            item = json.loads(saved[0]) if saved else {}
            if item.get("excerpt"):
                actual.append((identity, item["excerpt"]["sha256"]))
        if actual or approved:
            if (args.get("confirmExcerpts") is not True or approved != sorted(actual)
                    or not {identity for identity, _ in actual}.issubset(args.get("attachmentIds", []))):
                raise ExcerptConsentError("The staged excerpts changed. Review and explicitly confirm their exact files and hashes before sending.")
        rows = []
        for identity in args.get("attachmentIds", []):
            stored = self.service.db.execute("SELECT metadata FROM feedback_attachments WHERE json_extract(metadata,'$.id')=?", (identity,)).fetchone()
            if not stored or identity not in selected:
                raise ValueError("An attachment is no longer in this feedback draft. Remove it or attach it again.")
            row = json.loads(stored[0])
            feedback_attachments.read_verified(self.service.data_dir, row)
            if row.get('excerpt'):
                if args.get('confirmExcerpts') is not True or row['sha256'] != row['excerpt']['sha256']:
                    raise ValueError('Explicitly confirm the reviewed conversation excerpts before sending.')
            rows.append(row)
        if sum(row["size"] for row in rows) > feedback_attachments.MAX_TOTAL_BYTES:
            raise ValueError("Feedback attachments can total up to 24 MB.")
        return rows

    def diagnostics(self, args):
        """Read on demand; never put per-conversation facts in shared broadcasts."""
        if args.get('requestId'):
            row = self.service.db.execute("SELECT payload,receipt FROM feedback_requests WHERE id=?", (args['requestId'],)).fetchone()
            if row is None:
                raise ValueError('No saved feedback submission has that request ID.')
            payload, receipt = map(json.loads, row)
            return {'snapshot': 'accepted', 'capturedAt': receipt.get('createdAt'),
                    'diagnostics': payload.get('_diagnostics')}
        return {'snapshot': 'current', 'capturedAt': time.time(),
                'diagnostics': feedback_diagnostics.snapshot(self.service.state_context(), args.get('deviceDiagnostics'))}

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
        try:
            # Persist exactly the selected metadata/hash at acceptance. Changes
            # to the draft or file store after this point cannot alter a send.
            args["_attachments"] = self.selected_files(args)
            if args.get("includeDiagnostics", True):
                args["_diagnostics"] = feedback_diagnostics.snapshot(self.service.state_context(),args.get("deviceDiagnostics"))
        except ExcerptConsentError as exc:
            raise AppError(str(exc), 409) from None
        except (OSError, ValueError):
            raise AppError("An attachment changed or is unavailable. Remove it and attach it again.") from None
        receipt = {"requestId": identity, "title": args["title"], "category": args["category"],
                   "status": "queued", "message": "Sending feedback to GitHub…", "createdAt": time.time(),
                   "attachments": [{key: value for key, value in row.items() if key != "sha256"} for row in args["_attachments"]]}
        if args["_attachments"]:
            receipt["attachmentsUrl"] = "https://github.com/" + REPOSITORY + "/tree/feedback-assets/" + identity
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
        try:
            files = [(row, feedback_attachments.read_verified(self.service.data_dir, row)) for row in args.get("_attachments", [])]
        except (OSError, ValueError):
            await self.update(identity, status="failed", message="An attachment changed or is unavailable. Start new feedback and attach it again. Nothing was sent.")
            return
        body = args["body"] + "\n\n---\nCategory: " + CATEGORIES[args["category"]]
        if args.get("_diagnostics"):
            body += feedback_diagnostics.markdown(args["_diagnostics"])
        elif args.get("includeDiagnostics"):
            # Accepted payloads from older versions retain their original contract.
            facts = self.service.state["feedback"]["diagnostics"]
            body += "\n\nApp version: " + facts["appVersion"] + "\nOS family: " + facts["osFamily"]
        body += "\n\n<!-- amplifier-feedback:" + identity + " -->"
        try:
            if files:
                await self.update(identity, message="Uploading the selected, reviewed attachments…")
                uploaded = await feedback_attachments.upload(REPOSITORY, identity, files, github_api)
                await self.update(identity, attachments=uploaded, message="Creating the issue with your attachment links…")
                body += feedback_attachments.markdown(uploaded)
            url = await create_issue(args["title"], body)
        except feedback_attachments.BeforeUploadError as exc:
            await self.update(identity, status="failed", message=str(exc))
        except asyncio.CancelledError:
            await self.update(identity, status="unknown", message=UNKNOWN_FILES if files else UNKNOWN)
            raise
        except Exception:
            await self.update(identity, status="unknown", message=UNKNOWN_FILES if files else UNKNOWN)
        else:
            await self.update(identity, status="submitted", message="Feedback sent. Thank you.", url=url)
