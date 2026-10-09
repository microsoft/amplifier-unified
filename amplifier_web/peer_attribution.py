"""Instance-local, receipt-bound display provenance. Never execution authority.

No source conversation is opened, no grant is re-authorized, and no native file
is read here. Call on the owning thread after selecting the display page.
"""
from collections import Counter, defaultdict
import json

from amplifier_operations.coordination import fingerprint, peer_input

AGENT_CAPTION = "Sent by Amplifier from another chat"
CHAT_CAPTION = "From another chat"
DERIVED_FIELDS = {"attribution", "attributionCaption", "attributionOrigin", "origin"}


def neutral(row):
    """Saved/imported display assertions are never a trusted input."""
    return {key: value for key, value in row.items() if key not in DERIVED_FIELDS}


def derive(session, rows, resolver=None):
    rows = [neutral(row) for row in rows]
    return resolver(session, rows) if resolver is not None else rows


def display_annotations(session):
    """Only supported reaction annotations may overlay a derived message."""
    return {identity: {"reactions": value["reactions"]}
            for identity, value in session.get("messageAnnotations", {}).items()
            if isinstance(value, dict) and "reactions" in value}


def input_identity(row):
    value = row.get("nativeInputId") or row.get("inputId")
    return value if isinstance(value, str) and 0 < len(value) <= 200 else None


class PeerAttribution:
    def __init__(self, db):
        self.db = db

    def _receipts(self, identities, recipient):
        """Bound IN queries; duplicate/conflicting receipt IDs fail closed."""
        found = defaultdict(list)
        identities = sorted(identities)
        for start in range(0, len(identities), 400):
            batch = identities[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            for key, encoded in self.db.execute(f"""SELECT id,receipt FROM commands
                    WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                    AND json_extract(receipt,'$.target.sessionId')=?
                    AND (id IN ({placeholders})
                         OR json_extract(receipt,'$.inputId') IN ({placeholders}))""",
                    (recipient, *batch, *batch)).fetchall():
                try:
                    receipt = json.loads(encoded)
                except (ValueError, TypeError):
                    continue
                if isinstance(receipt, dict):
                    found[receipt.get("inputId")].append((key, receipt))
        return {identity: values[0][1] for identity, values in found.items()
                if len(values) == 1 and values[0][0] == identity}

    @staticmethod
    def _bound(receipt, message, recipient):
        envelope = message.get("peerEnvelope")
        if (not isinstance(envelope, dict) or message.get("role") != "user"
                or not isinstance(message.get("text"), str)
                or receipt.get("accepted") is not True
                or receipt.get("commandAction") != "coordination.send"
                or not receipt.get("senderSessionId")
                or receipt["senderSessionId"] == recipient
                or receipt.get("target", {}).get("sessionId") != recipient
                or receipt.get("messageId") != message.get("id")
                or receipt.get("requestId") != receipt.get("inputId")
                or receipt.get("inputId") != message.get("inputId")
                or not receipt.get("displayBinding")):
            return False
        fields = {"senderSessionId": receipt["senderSessionId"],
                  "recipientSessionId": recipient, "requestId": receipt["requestId"],
                  "inputId": receipt["inputId"], "grantId": receipt.get("grantId"),
                  "grantRevision": receipt.get("grantRevision"), "mode": receipt.get("mode")}
        return (all(envelope.get(key) == value for key, value in fields.items())
                and receipt["displayBinding"] == fingerprint([envelope, message["text"]]))

    @staticmethod
    def _continuation(receipt, request):
        """A host-sealed wait, not a caller's host-looking prefix."""
        if not request:
            return False
        response = request.get("response") or {}
        wait = request.get("subscription") or {}
        anchor = response.get("nativeTerminal") or {}
        from .automatic_history import display_identity
        return (request.get("accepted") is True
                and request.get("requestId") == receipt.get("dependencyRequestId")
                and response.get("status") == "sealed" and response.get("qualified") is True
                and response.get("kind") == "result" and response.get("outcome") == "success"
                and anchor.get("messageId") == response.get("terminalMessageId")
                and bool(anchor.get("messageId")) and bool(anchor.get("rootSessionId"))
                and anchor.get("generationId") == response.get("generationId")
                and bool(response.get("generationId")) and type(anchor.get("nativeIndex")) is int
                and anchor["nativeIndex"] >= 0 and isinstance(anchor.get("nativeText"), str)
                and anchor.get("textDigest") == fingerprint(anchor["nativeText"])
                and anchor["messageId"] == display_identity({"id": anchor["rootSessionId"]},
                    anchor["nativeIndex"], "assistant", anchor["nativeText"])
                and wait.get("continuationId") == receipt.get("inputId")
                and request.get("target", {}).get("sessionId") == receipt.get("senderSessionId")
                and request.get("senderSessionId") == receipt.get("target", {}).get("sessionId"))

    def resolve(self, session, rows):
        result = [neutral(row) for row in rows]
        identities = {input_identity(row) for row in rows if row.get("role") == "user"} - {None}
        if not identities:
            return result
        recipient = session["id"]
        receipts = self._receipts(identities, recipient)
        retained = defaultdict(list)
        retained_native_counts = Counter()
        ids = Counter()
        for message in session.get("messages", []):
            identity = input_identity(message)
            if identity in identities:
                if message.get("source") == "native":
                    retained_native_counts[identity] += 1
                else:
                    retained[identity].append(message)
            if isinstance(message.get("id"), str):
                ids[message["id"]] += 1
        native_counts = Counter(input_identity(row) for row in rows if row.get("source") == "native")
        page_ids = Counter(row["id"] for row in rows if isinstance(row.get("id"), str))
        dependencies = {value["dependencyRequestId"] for value in receipts.values()
                        if value.get("origin") == "system" and value.get("dependencyRequestId")}
        # Continuations reverse the target, so their sealed requests are looked
        # up by exact command identity, not a source-chat or lineage scan.
        requests = {}
        if dependencies:
            for start in range(0, len(dependencies), 400):
                batch = sorted(dependencies)[start:start + 400]
                marks = ",".join("?" for _ in batch)
                for key, encoded in self.db.execute(
                        f"SELECT id,receipt FROM commands WHERE id IN ({marks})", batch).fetchall():
                    requests[key] = json.loads(encoded)
        for row in result:
            identity = input_identity(row)
            receipt = receipts.get(identity)
            candidates = retained.get(identity, [])
            if (row.get("role") != "user" or not receipt or len(candidates) != 1
                    or not isinstance(row.get("id"), str) or not row["id"]
                    or page_ids[row["id"]] != 1 or row.get("nativeInputAmbiguous")):
                continue
            original = candidates[0]
            if ids[original.get("id")] != 1 or not self._bound(receipt, original, recipient):
                continue
            if row.get("source") == "native":
                if (not row.get("nativeInputId") or native_counts[identity] != 1
                        or retained_native_counts[identity] > 1
                        or row.get("text") != peer_input(original["peerEnvelope"], original["text"])):
                    continue
            elif (row.get("id") != original["id"] or row.get("text") != original["text"]
                    or row.get("peerEnvelope") != original.get("peerEnvelope")):
                continue
            actor = receipt.get("origin")
            if actor == "system" and not self._continuation(receipt, requests.get(receipt.get("dependencyRequestId"))):
                continue
            row["attribution"] = {"caption": AGENT_CAPTION if actor in {"agent", "system"} else CHAT_CAPTION}
        return result