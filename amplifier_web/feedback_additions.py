"""Explicit ordinary-file additions to a locally receipted private issue.

Separate from initial uploads: every POST has a durable sending fence. A crash
or uncertain response stops the entire request, including not-started phases.
Reconciliation only reads; it neither resumes nor creates a new upload intent.
Audit uses the existing follow-up store, never a second delivery journal.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import re
import time
from urllib.parse import quote

from . import attachments, feedback, feedback_attachments as transport
from .feedback_lifecycle import owned

READS = {"feedback.attachments.review", "feedback.attachments.reconcile"}
ACTIONS = READS | {"feedback.attachments.add"}
DISCLOSURE = (
    "Only these ordinary files will be stored in this private repository and "
    "linked in a new comment on this exact issue. Repository access is required. "
    "Files remain in repository history; removing a local file does not delete "
    "uploaded history. Privacy is checked before upload, not guaranteed "
    "atomically against later repository visibility changes."
)
UNKNOWN = "Delivery is uncertain. Use the read-only file delivery check; no upload or comment will be repeated."
MANIFEST_KEYS = ("id", "name", "mime", "size", "sha256")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def manifest(rows):
    return sorted(({key: row[key] for key in MANIFEST_KEYS} for row in rows), key=lambda row: row["id"])


def definitions(schema, string):
    identity = {"type": "string", "pattern": "^[A-Za-z0-9_-]{8,100}$"}
    common = {"requestId": identity, "feedbackId": identity}
    confirmed = schema({"id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                        "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}}, ["id", "sha256"])
    return {
        "feedback.attachments.review": (
            "Read-only review of the complete ordinary-file follow-up draft for this attached client and original feedbackId. Freezes the exact private issue URL, repository/account IDs, manifest and retention disclosure. Does not upload or consent. Changes require a fresh review requestId.",
            schema(common, ["requestId", "feedbackId"])),
        "feedback.attachments.add": (
            "Only after explicit user confirmation, add the complete reviewed ordinary-file set to that same private issue as immutable file links in a marked append-only comment. Pass reviewRequestId and exact confirmedFiles [{id,sha256}]. No issue PATCH, new issue, excerpt, main-branch write or deletion. Every phase is attempted once; same requestId/payload reads its receipt, unknown phases require read-only reconciliation, never a new ID to retry.",
            schema({**common, "reviewRequestId": identity,
                    "confirmedFiles": {"type": "array", "minItems": 1, "maxItems": transport.MAX_FILES,
                                       "uniqueItems": True, "items": confirmed},
                    "comment": string(16000)}, ["requestId", "feedbackId", "reviewRequestId", "confirmedFiles"])),
        "feedback.attachments.reconcile": (
            "Read-only delivery check for additionRequestId on this client's exact feedbackId. Only uniquely verified objects/comment can acknowledge attempted phases. Absence, ambiguity or access failure leaves uncertainty; not-started phases stay not started. Never resumes uploads.",
            schema({**common, "additionRequestId": identity}, ["requestId", "feedbackId", "additionRequestId"])),
    }


def git_sha(kind, data):
    return hashlib.sha1(kind.encode() + b" " + str(len(data)).encode() + b"\0" + data).hexdigest()


def tree_binding(rows, blobs):
    """Exact root tree for one directory per staged ID; no base/main tree."""
    entries = []
    for row in rows:
        path = row["id"] + "/" + row["name"]
        blob = blobs[row["id"]]
        subtree = git_sha("tree", b"100644 " + row["name"].encode() + b"\0" + bytes.fromhex(blob))
        entries.extend([{"path": row["id"], "mode": "040000", "type": "tree", "sha": subtree},
                        {"path": path, "mode": "100644", "type": "blob", "sha": blob}])
    root = b"".join(b"40000 " + row["id"].encode() + b"\0" + bytes.fromhex(
        next(item["sha"] for item in entries if item["path"] == row["id"])) for row in sorted(rows, key=lambda row: row["id"]))
    return git_sha("tree", root), entries


class Additions:
    def __init__(self, owner):
        self.owner, self.service = owner, owner.service
        for identity, raw in self.service.db.execute("SELECT id,receipt FROM feedback_followups").fetchall():
            receipt = json.loads(raw)
            if receipt.get("action") != "feedback.attachments.add":
                continue
            audit = receipt.get("audit", {})
            for phase in audit.get("phases", {}).values():
                if phase["status"] == "sending":
                    phase["status"] = "unknown"
            if receipt["status"] == "unknown":
                self.summarize(receipt)
                self.store(identity, receipt)

    def client(self):
        from .service import AppError
        client = self.service.clients.current.get()
        if not client:
            raise AppError("Attach the intended browser before staging or reviewing follow-up files.", 409)
        return client

    def load(self, identity):
        raw = self.service.db.execute("SELECT payload,receipt FROM feedback_followups WHERE id=?", (identity,)).fetchone()
        if not raw:
            raise ValueError("No matching file review or addition exists.")
        return tuple(map(json.loads, raw))

    def store(self, identity, receipt):
        self.service.db.execute("UPDATE feedback_followups SET receipt=? WHERE id=?", (json.dumps(receipt), identity))

    def scoped_rows(self, client, feedback_id):
        rows = [json.loads(raw[0]) for raw in self.service.db.execute(
            "SELECT metadata FROM feedback_attachments WHERE json_extract(metadata,'$._clientId')=? AND json_extract(metadata,'$._feedbackId')=?",
            (client, feedback_id))]
        return sorted((row for row in rows if row.get("_selected") is True), key=lambda row: row["id"])

    def frozen(self, client, feedback_id):
        return self.service.db.execute(
            "SELECT id FROM feedback_followups WHERE json_extract(receipt,'$.action')='feedback.attachments.add' "
            "AND json_extract(receipt,'$.clientId')=? AND json_extract(receipt,'$.feedbackId')=? "
            "AND json_extract(receipt,'$.status') IN ('queued','sending','unknown','partial') LIMIT 1",
            (client, feedback_id)).fetchone()

    def files(self, client, feedback_id):
        rows = self.scoped_rows(client, feedback_id)
        if (not 1 <= len(rows) <= transport.MAX_FILES or any(row.get("excerpt") or not 0 < row["size"] <= transport.MAX_FILE_BYTES for row in rows)
                or sum(row["size"] for row in rows) > transport.MAX_TOTAL_BYTES):
            raise ValueError("Choose ordinary files only: up to 8 files, 8 MiB each and 24 MiB total.")
        return [(row, transport.read_verified(self.service.data_dir, row)) for row in rows]

    def stage(self, action, args):
        from .service import AppError
        client, feedback_id = self.client(), args["feedbackId"]
        self.owner.followups.target(feedback_id)
        fingerprint = digest([client, args])
        if action == "feedback.attachment.add":
            previous = self.service.db.execute("SELECT fingerprint FROM feedback_attachments WHERE request_id=?", (args["requestId"],)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise AppError("This attachment request ID already belongs to a different file or scope.", 409)
                return
        if self.frozen(client, feedback_id):
            raise AppError("This file addition is frozen. Unknown phases require a read-only check, not another upload.", 409)
        rows = self.scoped_rows(client, feedback_id)
        if action == "feedback.attachment.remove":
            saved = self.service.db.execute("SELECT metadata FROM feedback_attachments WHERE json_extract(metadata,'$.id')=?", (args["id"],)).fetchone()
            metadata = json.loads(saved[0]) if saved else {}
            if metadata.get("_clientId") != client or metadata.get("_feedbackId") != feedback_id:
                raise AppError("This file does not belong to the selected client's feedback draft.", 409)
            for row in rows:
                if row["id"] == args["id"]:
                    row["_selected"] = False
                    self.service.db.execute("UPDATE feedback_attachments SET metadata=? WHERE json_extract(metadata,'$.id')=?",
                                            (json.dumps(row), row["id"]))
            return
        if len(rows) >= transport.MAX_FILES:
            raise AppError("Attach up to 8 files per feedback addition.")
        try:
            size = len(args["base64"]) // 4 * 3 - (len(args["base64"]) - len(args["base64"].rstrip("=")))
            if sum(row["size"] for row in rows) + size > transport.MAX_TOTAL_BYTES:
                raise ValueError("Feedback attachments can total up to 24 MiB.")
            row = attachments.save(self.service.data_dir, args["name"], args["base64"], max_bytes=transport.MAX_FILE_BYTES)
            # New scope only. Initial staging and its wire/metadata remain unchanged.
            row["sha256"] = hashlib.sha256(base64.b64decode(args["base64"], validate=True)).hexdigest()
            row.update(_clientId=client, _feedbackId=feedback_id, _selected=True)
            transport.read_verified(self.service.data_dir, row)
        except (OSError, ValueError):
            raise AppError("The ordinary file could not be staged. Choose a nonempty file up to 8 MiB, within the 24 MiB total limit.") from None
        self.service.db.execute("INSERT INTO feedback_attachments VALUES (?,?,?)", (args["requestId"], fingerprint, json.dumps(row)))

    def project(self):
        client = self.service.clients.current.get()
        staging = []
        for request_id, raw in self.service.db.execute(
                "SELECT request_id,metadata FROM feedback_attachments WHERE json_extract(metadata,'$._clientId')=?", (client,)):
            row = json.loads(raw)
            staging.append({"requestId": request_id, "feedbackId": row["_feedbackId"],
                            "id": row["id"], "size": row["size"], "sha256": row["sha256"],
                            "selected": row["_selected"]})
        drafts = {}
        for raw in self.service.db.execute("SELECT metadata FROM feedback_attachments WHERE json_extract(metadata,'$._clientId')=? AND json_extract(metadata,'$._selected')=1", (client,)):
            row = json.loads(raw[0])
            drafts.setdefault(row["_feedbackId"], []).append({**{key: row[key] for key in MANIFEST_KEYS}, "url": row["url"]})
        receipts = []
        where = "json_extract(receipt,'$.clientId')=? AND json_extract(receipt,'$.action') LIKE 'feedback.attachments.%'"
        active = self.service.db.execute("SELECT receipt FROM feedback_followups WHERE " + where +
            " AND json_extract(receipt,'$.status') IN ('queued','sending','unknown','partial') ORDER BY rowid DESC", (client,)).fetchall()
        terminal = self.service.db.execute("SELECT receipt FROM feedback_followups WHERE " + where +
            " AND json_extract(receipt,'$.status') NOT IN ('queued','sending','unknown','partial') ORDER BY rowid DESC LIMIT 20", (client,)).fetchall()
        for raw in active + terminal:
            row = json.loads(raw[0])
            receipts.append({key: value for key, value in row.items() if key != "audit"})
        return {"attachmentDrafts": drafts, "additions": receipts, "stagingReceipts": staging}

    def accept(self, action, args, origin):
        from .service import AppError
        client = self.client()
        fingerprint = digest([action, args, client])
        previous = self.service.db.execute("SELECT fingerprint FROM feedback_followups WHERE id=?", (args["requestId"],)).fetchone()
        if previous:
            if previous[0] != fingerprint:
                raise AppError("This file request ID already belongs to different contents or client.", 409)
            return False
        url, _ = self.owner.followups.target(args["feedbackId"])
        receipt = {"requestId": args["requestId"], "feedbackId": args["feedbackId"], "clientId": client,
                   "action": action, "origin": origin, "status": "queued", "url": url,
                   "createdAt": time.time(), "message": "Checking the exact private file destination…"}
        try:
            if action == "feedback.attachments.reconcile":
                _, addition = self.load(args["additionRequestId"])
                if (addition["action"] != "feedback.attachments.add" or addition["clientId"] != client or
                        addition["feedbackId"] != args["feedbackId"] or addition["status"] not in {"unknown", "partial"}):
                    raise ValueError("Choose an uncertain file addition for this client and report.")
            else:
                if self.frozen(client, args["feedbackId"]):
                    raise ValueError("This report's file addition is frozen; use a read-only delivery check.")
                files = self.files(client, args["feedbackId"])
                receipt["audit"] = {"manifest": manifest([row for row, _ in files])}
                if action == "feedback.attachments.add":
                    _, review = self.load(args["reviewRequestId"])
                    approved = sorted((row["id"], row["sha256"]) for row in args["confirmedFiles"])
                    actual = [(row["id"], row["sha256"]) for row in receipt["audit"]["manifest"]]
                    if (review["action"] != "feedback.attachments.review" or review["status"] != "completed" or
                            review["clientId"] != client or review["feedbackId"] != args["feedbackId"] or
                            review.get("consumedBy") or review["review"]["manifest"] != receipt["audit"]["manifest"] or
                            review["review"]["url"] != url or approved != actual):
                        raise ValueError("The complete file set changed or its review was already used. Review the exact files again.")
                    receipt["audit"].update(review=copy.deepcopy(review["review"]), phases={})
                    prefix = "repos/" + review["review"]["repository"]["full_name"]
                    phases = [("blob:" + row["id"], prefix + "/git/blobs") for row, _ in files]
                    phases += [("tree", prefix + "/git/trees"), ("commit", prefix + "/git/commits"),
                               ("ref", prefix + "/git/refs"), ("comment", "repos/" + url.removeprefix("https://github.com/") + "/comments")]
                    receipt["audit"]["phases"] = {key: {"phaseId": args["requestId"] + ":" + key, "status": "not_started", "endpoint": endpoint} for key, endpoint in phases}
                    self.summarize(receipt)
        except (ValueError, OSError, KeyError, TypeError):
            raise AppError("The private destination or exact ordinary-file review is unavailable, changed, frozen, or belongs to another scope. Review again; nothing was sent.", 409) from None
        active = self.service.db.execute("SELECT count(*) FROM feedback_followups WHERE json_extract(receipt,'$.status') IN ('queued','sending')").fetchone()[0]
        if active >= 8:
            raise AppError("Wait for a feedback check or follow-up to finish.", 409)
        if action == "feedback.attachments.add":
            review["consumedBy"] = args["requestId"]
            self.store(args["reviewRequestId"], review)
        self.service.db.execute("INSERT INTO feedback_followups VALUES (?,?,?,?)", (args["requestId"], fingerprint, json.dumps(args), json.dumps(receipt)))
        self.owner.refresh()
        return True

    async def destination(self, feedback_id):
        url, number = self.owner.followups.target(feedback_id)
        repository = url.removeprefix("https://github.com/").split("/issues/")[0]
        user = await feedback.github_api("user", None)
        issue = await feedback.github_api("repos/" + url.removeprefix("https://github.com/"), None)
        repo = await feedback.github_api("repos/" + repository, None)
        if (not owned(issue, user, feedback_id, url) or issue["number"] != number or type(issue.get("id")) is not int or
                repo.get("private") is not True or repo.get("full_name") != repository or type(repo.get("id")) is not int):
            raise ValueError("Unverified private issue destination.")
        return {"url": url, "issueId": issue["id"], "issueNumber": number,
                "repository": {"id": repo["id"], "full_name": repository, "private": True},
                "account": {"id": user["id"], "login": str(user.get("login", ""))}, "disclosure": DISCLOSURE}

    @staticmethod
    def summarize(receipt):
        phases = receipt.get("audit", {}).get("phases", {})
        receipt["phases"] = [{"phaseId": row["phaseId"], "status": row["status"]} for row in phases.values()]
        ref = phases.get("ref", {})
        receipt["filesStored"] = ref.get("status") == "succeeded"
        receipt["commentStatus"] = phases.get("comment", {}).get("status", "not_started")

    @staticmethod
    def merge_reconciled(latest, observed):
        """Merge positive read evidence, never an older uncertain snapshot.

        Called under the service lock, with no network await. Immutable intent
        and each attempted phase's input binding must still match disk.
        """
        for key in ("requestId", "feedbackId", "clientId", "action", "url"):
            if latest[key] != observed[key]:
                raise ValueError("Reconciliation scope changed.")
        for key in ("manifest", "review"):
            if latest["audit"][key] != observed["audit"][key]:
                raise ValueError("Reconciliation intent changed.")
        current, incoming = latest["audit"]["phases"], observed["audit"]["phases"]
        if current.keys() != incoming.keys():
            raise ValueError("Reconciliation phases changed.")
        for key, phase in incoming.items():
            saved = current[key]
            for field in ("phaseId", "endpoint", "inputFingerprint", "prerequisites"):
                if saved.get(field) != phase.get(field):
                    raise ValueError("Reconciliation input binding changed.")
            if phase["status"] != "succeeded":
                continue
            if saved["status"] not in {"unknown", "succeeded"}:
                raise ValueError("An unattempted phase cannot be acknowledged.")
            if saved.get("result") is not None and saved["result"] != phase["result"]:
                raise ValueError("Reconciliation result conflicts with known evidence.")
            if saved["status"] == "unknown":
                current[key] = copy.deepcopy(phase)
        if current["ref"]["status"] == "succeeded" and observed.get("attachments"):
            latest["attachments"] = observed["attachments"]
        if current["comment"]["status"] == "succeeded":
            latest.update(current["comment"]["result"])
        all_done = all(phase["status"] == "succeeded" for phase in current.values())
        latest.update(status="submitted" if all_done else "partial" if current["ref"]["status"] == "succeeded" else "unknown",
                      message="File addition delivery verified by read-only checks." if all_done else UNKNOWN)
        return latest

    async def persist(self, identity, receipt, *, reconciled=False):
        async with self.service.lock:
            if reconciled:
                _, latest = self.load(identity)
                receipt = self.merge_reconciled(latest, receipt)
            receipt["updatedAt"] = time.time()
            if receipt["action"] == "feedback.attachments.add":
                self.summarize(receipt)
            self.store(identity, receipt)
            self.owner.refresh()
            # This commits the SQLite fence BEFORE control reaches the POST.
            self.service._publish_changes(globals={"feedback"})
        return receipt

    async def phase(self, identity, receipt, key, payload, prerequisites, validate, verify=None):
        phase = receipt["audit"]["phases"][key]
        if phase["status"] != "not_started":
            raise ValueError("An attempted phase cannot be replayed.")
        phase.update(status="sending", inputFingerprint=digest(payload), prerequisites=prerequisites)
        await self.persist(identity, receipt)
        result = await feedback.github_api(phase["endpoint"], payload)
        phase["result"] = validate(result)
        # Retain immutable returned evidence before any follow-up await. A
        # returned object is not yet an acknowledgement of actor/account.
        await self.persist(identity, receipt)
        if verify is not None:
            await verify(phase["result"])
        # Do not mark the working copy succeeded until the acknowledgement is
        # durably saved. A failed save must stop later phases, even in-process.
        acknowledged = copy.deepcopy(receipt)
        acknowledged["audit"]["phases"][key]["status"] = "succeeded"
        await self.persist(identity, acknowledged)
        phase["status"] = "succeeded"
        return phase["result"]

    @staticmethod
    def object(result, repository, kind, expected=None):
        sha = transport.object_sha(result)
        if (expected is not None and sha != expected or
                result.get("url") != f"https://api.github.com/repos/{repository}/git/{kind}/{sha}"):
            raise ValueError("Invalid object binding.")
        return {"sha": sha, "url": result["url"]}

    @staticmethod
    def comment(result, review, body):
        identity = result.get("id")
        url = review["url"] + f"#issuecomment-{identity}"
        if (type(identity) is not int or identity <= 0 or result.get("html_url") != url or
                result.get("issue_url") != "https://api.github.com/repos/" + review["url"].removeprefix("https://github.com/") or
                result.get("user", {}).get("id") != review["account"]["id"] or result.get("body") != body):
            raise ValueError("Invalid comment binding.")
        return {"commentId": identity, "commentUrl": url}

    async def commit_actor(self, repository, sha, account_id):
        result = await feedback.github_api("repos/" + repository + "/commits/" + sha, None)
        if (result.get("sha") != sha or result.get("url") != f"https://api.github.com/repos/{repository}/commits/{sha}" or
                type(result.get("committer", {}).get("id")) is not int or result["committer"]["id"] != account_id):
            raise ValueError("Unverified commit actor.")

    async def run(self, identity):
        # Atomically claim the one queued execution, including direct callers.
        async with self.service.lock:
            args, receipt = self.load(identity)
            if receipt["status"] != "queued":
                return
            receipt["status"] = "sending"
            self.store(identity, receipt)
            self.owner.refresh()
            self.service._publish_changes(globals={"feedback"})
        try:
            if receipt["action"] == "feedback.attachments.reconcile":
                await self.reconcile(args, receipt)
            elif receipt["action"] == "feedback.attachments.review":
                target = await self.destination(args["feedbackId"])
                async with self.service.lock:
                    current = manifest([row for row, _ in self.files(receipt["clientId"], args["feedbackId"])])
                    if current != receipt["audit"]["manifest"] or self.frozen(receipt["clientId"], args["feedbackId"]):
                        raise ValueError("Draft changed during review.")
                receipt.update(status="completed", review={**target, "manifest": current}, message="Exact private destination and files reviewed. Nothing uploaded; confirmation is still required.")
            else:
                await self.send(args, receipt)
        except BaseException as exc:
            if not isinstance(exc, (Exception, asyncio.CancelledError)):
                raise
            if receipt["action"] == "feedback.attachments.add":
                phases = receipt["audit"]["phases"]
                _, durable = self.load(identity)
                for key, phase in phases.items():
                    saved = durable["audit"]["phases"][key]
                    if saved["status"] == "succeeded":
                        phases[key] = saved
                        continue
                    if phase["status"] == "sending":
                        phase["status"] = "unknown"
                attempted = any(phase["status"] != "not_started" for phase in phases.values())
                receipt.update(status=("partial" if phases["ref"]["status"] == "succeeded" else "unknown") if attempted else "failed",
                               message=UNKNOWN if attempted else "The exact private destination or staged bytes changed or could not be verified. Nothing was uploaded. Review again.")
            else:
                receipt.update(status="failed", message="The read-only check could not verify delivery or the private destination. No upload or comment was sent.")
            await self.persist(identity, receipt)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return
        await self.persist(identity, receipt)

    async def send(self, args, receipt):
        identity, audit = args["requestId"], receipt["audit"]
        target = await self.destination(args["feedbackId"])
        files = self.files(receipt["clientId"], args["feedbackId"])
        if {**target, "manifest": manifest([row for row, _ in files])} != audit["review"]:
            raise ValueError("Fresh review required.")
        # No await between complete byte/scope verification and the first fence.
        review, repository = audit["review"], target["repository"]["full_name"]
        blobs, tree = {}, []
        for row, data in files:
            expected = git_sha("blob", data)
            result = await self.phase(identity, receipt, "blob:" + row["id"],
                {"encoding": "base64", "content": base64.b64encode(data).decode("ascii")},
                {"id": row["id"], "sha256": row["sha256"], "expectedSha": expected},
                lambda result, expected=expected: self.object(result, repository, "blobs", expected))
            blobs[row["id"]] = result["sha"]
            tree.append({"path": row["id"] + "/" + row["name"], "mode": "100644", "type": "blob", "sha": result["sha"]})
        expected_tree, entries = tree_binding(audit["manifest"], blobs)
        tree_result = await self.phase(identity, receipt, "tree", {"tree": tree},
            {"expectedSha": expected_tree, "entries": entries},
            lambda result: self.object(result, repository, "trees", expected_tree))
        commit_payload = {"message": "Feedback attachments " + identity, "tree": tree_result["sha"], "parents": []}
        def validate_commit(result):
            if result.get("tree", {}).get("sha") != tree_result["sha"] or result.get("parents") != [] or result.get("message") != commit_payload["message"]:
                raise ValueError("Invalid isolated commit.")
            return self.object(result, repository, "commits")
        commit = await self.phase(identity, receipt, "commit", commit_payload, commit_payload, validate_commit,
                                  lambda result: self.commit_actor(repository, result["sha"], review["account"]["id"]))
        ref_name = "refs/heads/feedback-assets/" + identity
        def validate_ref(result):
            if (result.get("ref") != ref_name or result.get("url") != f"https://api.github.com/repos/{repository}/git/refs/heads/feedback-assets/{identity}" or
                    result.get("object", {}).get("type") != "commit" or transport.object_sha(result.get("object", {})) != commit["sha"]):
                raise ValueError("Invalid isolated attachment ref.")
            return {"ref": ref_name, "sha": commit["sha"], "url": result["url"]}
        await self.phase(identity, receipt, "ref", {"ref": ref_name, "sha": commit["sha"]}, {"commit": commit["sha"], "ref": ref_name}, validate_ref)
        receipt["attachments"] = [{**row, "visibility": "private",
            "url": f"https://github.com/{repository}/blob/{commit['sha']}/" + quote(row["id"] + "/" + row["name"], safe="/")} for row in audit["manifest"]]
        body = args.get("comment", "").strip() + transport.markdown(receipt["attachments"])
        body += f"\n\n<!-- amplifier-feedback-attachments:{identity} -->"
        audit["commentBody"] = body
        result = await self.phase(identity, receipt, "comment", {"body": body},
            {"issueId": review["issueId"], "accountId": review["account"]["id"], "bodyFingerprint": digest(body)},
            lambda result: self.comment(result, review, body))
        receipt.update(result, status="submitted", message="Reviewed files linked in a new comment. Original report and maintainer edits were preserved.")
        # Successful files leave the staging selection, not repository history.
        async with self.service.lock:
            for row, _ in files:
                row["_selected"] = False
                self.service.db.execute("UPDATE feedback_attachments SET metadata=? WHERE json_extract(metadata,'$.id')=?", (json.dumps(row), row["id"]))

    async def reconcile(self, args, check):
        _, receipt = self.load(args["additionRequestId"])
        audit, identity = receipt["audit"], receipt["requestId"]
        review = audit["review"]
        if await self.destination(args["feedbackId"]) != {key: value for key, value in review.items() if key != "manifest"}:
            raise ValueError("Destination/account changed.")
        repository, phases = review["repository"]["full_name"], audit["phases"]
        prefix = "repos/" + repository
        verified = []
        # Objects with exact known SHA/input can be verified independently.
        # A lost commit reply with no known SHA stays unknown.
        for key, phase in phases.items():
            if phase["status"] != "unknown" or key in {"commit", "ref", "comment"}:
                continue
            try:
                expected = phase["prerequisites"]["expectedSha"]
                if key.startswith("blob:"):
                    result = await feedback.github_api(prefix + "/git/blobs/" + expected, None)
                    self.object(result, repository, "blobs", expected)
                    if result.get("encoding") != "base64":
                        raise ValueError("Invalid blob encoding.")
                    data = base64.b64decode("".join(result["content"].split()), validate=True)
                    row = next(row for row in audit["manifest"] if row["id"] == phase["prerequisites"]["id"])
                    if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"] or git_sha("blob", data) != expected:
                        raise ValueError("Invalid blob bytes.")
                else:
                    result = await feedback.github_api(prefix + "/git/trees/" + expected + "?recursive=1", None)
                    self.object(result, repository, "trees", expected)
                    entries = [{key: item[key] for key in ("path", "mode", "type", "sha")} for item in result["tree"]]
                    if result.get("truncated") is not False or sorted(entries, key=lambda item: item["path"]) != sorted(phase["prerequisites"]["entries"], key=lambda item: item["path"]):
                        raise ValueError("Invalid manifest tree.")
                phase.update(status="succeeded", result={"sha": expected, "url": result["url"]}, reconciledBy=args["requestId"])
                verified.append(key)
            except Exception:
                pass  # Absence/inaccessibility is uncertainty, never failure/resend.
        commit_phase = phases["commit"]
        if commit_phase["status"] == "unknown" and commit_phase.get("result"):
            try:
                sha = transport.object_sha(commit_phase["result"])
                commit = await feedback.github_api(prefix + "/git/commits/" + sha, None)
                result = self.object(commit, repository, "commits", sha)
                binding = commit_phase["prerequisites"]
                if (commit.get("tree", {}).get("sha") != binding["tree"] or
                        commit.get("parents") != [] or commit.get("message") != binding["message"] or
                        phases["tree"]["status"] != "succeeded"):
                    raise ValueError("Invalid isolated commit binding.")
                await self.commit_actor(repository, sha, review["account"]["id"])
                commit_phase.update(status="succeeded", result=result, reconciledBy=args["requestId"])
                verified.append("commit")
            except Exception:
                pass
        if phases["ref"]["status"] in {"unknown", "succeeded"}:
            try:
                ref_name = "refs/heads/feedback-assets/" + identity
                ref = await feedback.github_api(prefix + "/git/ref/heads/feedback-assets/" + identity, None)
                sha = transport.object_sha(ref["object"])
                if (ref.get("ref") != ref_name or ref["object"].get("type") != "commit" or
                        ref.get("url") != f"https://api.github.com/repos/{repository}/git/refs/heads/feedback-assets/{identity}" or
                        sha != phases["ref"]["prerequisites"]["commit"]):
                    raise ValueError("Invalid ref binding.")
                commit = await feedback.github_api(prefix + "/git/commits/" + sha, None)
                self.object(commit, repository, "commits", sha)
                binding = phases["commit"]["prerequisites"]
                if (phases["commit"]["status"] != "succeeded" or commit.get("tree", {}).get("sha") != binding["tree"] or
                        commit.get("parents") != [] or commit.get("message") != binding["message"]):
                    raise ValueError("Invalid commit binding.")
                await self.commit_actor(repository, sha, review["account"]["id"])
                # Re-read the full known tree and every blob; ref alone is not proof.
                tree = await feedback.github_api(prefix + "/git/trees/" + binding["tree"] + "?recursive=1", None)
                self.object(tree, repository, "trees", binding["tree"])
                entries = [{key: item[key] for key in ("path", "mode", "type", "sha")} for item in tree["tree"]]
                if tree.get("truncated") is not False or sorted(entries, key=lambda item: item["path"]) != sorted(phases["tree"]["prerequisites"]["entries"], key=lambda item: item["path"]):
                    raise ValueError("Invalid full manifest.")
                for row in audit["manifest"]:
                    expected = phases["blob:" + row["id"]]["result"]["sha"]
                    blob = await feedback.github_api(prefix + "/git/blobs/" + expected, None)
                    self.object(blob, repository, "blobs", expected)
                    data = base64.b64decode("".join(blob["content"].split()), validate=True)
                    if blob.get("encoding") != "base64" or len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                        raise ValueError("Invalid stored bytes.")
                if phases["ref"]["status"] == "unknown":
                    phases["ref"].update(status="succeeded", result={"ref": ref_name, "sha": sha, "url": ref["url"]}, reconciledBy=args["requestId"])
                    verified.append("ref")
                receipt["attachments"] = [{**row, "visibility": "private", "url": f"https://github.com/{repository}/blob/{sha}/" + quote(row["id"] + "/" + row["name"], safe="/")} for row in audit["manifest"]]
            except Exception:
                pass
        comment_phase = phases["comment"]
        if comment_phase["status"] == "unknown":
            matches, complete = {}, False
            endpoint = "repos/" + review["url"].removeprefix("https://github.com/") + "/comments"
            try:
                for page in range(1, 6):
                    rows = await feedback.github_api(endpoint + f"?per_page=100&page={page}", None)
                    if not isinstance(rows, list) or len(rows) > 100:
                        raise ValueError("Invalid comments page.")
                    for row in rows:
                        if f"<!-- amplifier-feedback-attachments:{identity} -->" in (row.get("body") or ""):
                            result = self.comment(row, review, audit["commentBody"])
                            matches[result["commentId"]] = result
                    if len(rows) < 100:
                        complete = True
                        break
                if complete and len(matches) == 1:
                    result = next(iter(matches.values()))
                    comment_phase.update(status="succeeded", result=result, reconciledBy=args["requestId"])
                    receipt.update(result)
                    verified.append("comment")
            except Exception:
                pass
        all_done = all(phase["status"] == "succeeded" for phase in phases.values())
        receipt.update(status="submitted" if all_done else "partial" if phases["ref"]["status"] == "succeeded" else "unknown",
                       message="File addition delivery verified by read-only checks." if all_done else UNKNOWN)
        receipt = await self.persist(identity, receipt, reconciled=True)
        all_done = receipt["status"] == "submitted"
        if all_done:
            async with self.service.lock:
                for row in self.scoped_rows(receipt["clientId"], args["feedbackId"]):
                    if row["id"] in {item["id"] for item in audit["manifest"]}:
                        row["_selected"] = False
                        self.service.db.execute("UPDATE feedback_attachments SET metadata=? WHERE json_extract(metadata,'$.id')=?",
                                                (json.dumps(row), row["id"]))
        check.update(status="completed", verifiedPhases=verified,
                     message="Read-only delivery check complete. Not-started phases remain stopped; nothing was uploaded or reposted.")