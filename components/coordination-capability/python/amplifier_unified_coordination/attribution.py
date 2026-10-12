"""Bounded display proofs from saved delivery receipts, never imported prose."""
import hashlib
import json


def binding(text, envelope):
    return hashlib.sha256(json.dumps([text, envelope], sort_keys=True,
        separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def proofs(owner, session):
    rows = owner.db.execute("SELECT body FROM commands WHERE json_extract(body,'$.operation')='coordination.send' AND json_extract(body,'$.target.sessionId')=? ORDER BY rowid DESC LIMIT 401", (session,)).fetchall()
    result, seen, ambiguous = [], set(), set()
    for (body,) in rows[:400]:
        row = json.loads(body)
        identity, envelope = row.get('inputId'), row.get('peerEnvelope')
        if identity in seen: ambiguous.add(identity)
        seen.add(identity)
        origin = row.get('sourceOrigin')
        if origin not in {'ui', 'agent', 'system'} or not isinstance(envelope, dict): continue
        if (not isinstance(identity, str) or not 1 <= len(identity) <= 128
                or envelope.get('version') != 1 or envelope.get('requestId') != identity
                or envelope.get('recipientSessionId') != session
                or envelope.get('senderSessionId') != row.get('senderSessionId')
                or envelope.get('senderSessionId') == session
                or envelope.get('grantId') != row.get('grantId')
                or envelope.get('mode') != row.get('mode')
                or not isinstance(row.get('text'), str)): continue
        if row.get('mode') == 'notify':
            if row.get('status') != 'notified': continue
        elif (row.get('admission') or {}).get('accepted') is not True: continue
        result.append({'inputId': identity, 'origin': origin, 'binding': binding(row['text'], envelope)})
    # Check duplicates beyond the page too; a display read never chooses between
    # conflicting receipt identities and never reauthorizes or replays a send.
    if seen:
        ids = list(seen)
        query = "SELECT json_extract(body,'$.inputId') FROM commands WHERE json_extract(body,'$.operation')='coordination.send' AND json_extract(body,'$.inputId') IN (" + ','.join('?' for _ in ids) + ") GROUP BY json_extract(body,'$.inputId') HAVING count(*) != 1"
        ambiguous.update(row[0] for row in owner.db.execute(query, ids))
    return {'attributions': [row for row in result if row['inputId'] not in ambiguous],
            'attributionsTruncated': len(rows) > 400}
