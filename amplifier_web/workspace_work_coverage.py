"""Nonsemantic capture coverage from retained host input facts, not an importer."""
from __future__ import annotations

import hashlib
import json
import time

from amplifier_operations.coordination import fingerprint

HUMAN_ORIGINS = frozenset({'ui', 'user', 'voice'})
INPUT = 'workspace.work.input'
REVIEW = 'workspace.work.inputReview'


def definitions(schema, string):
    scope = {'workspaceId': string(100), 'sessionId': string(200)}
    return {
        'workspace.work.coverage': ('Read observed human-input capture coverage. Counts are not a semantic task inventory; pre-observation/native-only history stays unreviewed. No transcript scan or worker startup.', schema({**scope,
            'after': string(2048), 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}}, ['workspaceId'])),
        'workspace.work.reviewInput': ('Record an explicit bounded capture review for one observed human input. It does not classify or import other history. The actor must be the source root.', schema({**scope,
            'inputId': string(200), 'sourceSha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
            'disposition': {'enum': ['ordinary_conversation', 'unresolved']},
            'reason': {**string(1000), 'minLength': 1}},
            ['workspaceId', 'sessionId', 'inputId', 'sourceSha256', 'disposition', 'reason'])),
    }


def initialize(work):
    work.db.execute("""CREATE INDEX IF NOT EXISTS work_input_scope ON commands(
        json_extract(receipt,'$.sessionId'),id)
        WHERE json_extract(receipt,'$.commandAction')='workspace.work.input'""")
    work.db.execute("""CREATE INDEX IF NOT EXISTS work_input_review ON commands(
        json_extract(receipt,'$.inputId'))
        WHERE json_extract(receipt,'$.commandAction')='workspace.work.inputReview'""")


def observe(work, session, message):
    if message.get('role') != 'user' or message.get('inputOrigin') not in HUMAN_ORIGINS:
        return
    # Only source facts; no message text, inferred promise or accepted scope.
    identity = 'work-input:' + fingerprint([session['id'], message['id']])
    row = {'commandAction': INPUT, 'id': identity, 'sessionId': session['id'],
           'workspace': session.get('workspace'), 'messageId': message['id'],
           'sourceSha256': hashlib.sha256(message.get('text', '').encode()).hexdigest(),
           'inputOrigin': message['inputOrigin'], 'observedAt': time.time()}
    # Queue only until the existing source-session save commits. Do not leave
    # uncommitted facts on the shared SQLite connection for an unrelated writer.
    work.pending_input_facts[identity] = row


def stage_saved_inputs(work, sessions, admitted, deleted=()):
    from .cold_display import MARKER
    staged = []
    for identity, row in list(work.pending_input_facts.items()):
        if row['sessionId'] in deleted:
            staged.append(identity)
            continue
        if row['sessionId'] not in admitted:
            continue
        session = sessions.get(row['sessionId'])
        if (session is None or 'messages' not in session
                or 'messages' in dict.get(session, MARKER, {})
                or session.get('historyManaged') and not session.get('historyLoaded')):
            continue
        message = next((m for m in session.get('messages', []) if m.get('id') == row['messageId']), None)
        # The admitted source snapshot is authoritative; metadata does not retain
        # an abandoned message body. A failed save still keeps the pending fact.
        if not message or message.get('role') != 'user' or message.get('inputOrigin') != row['inputOrigin']:
            staged.append(identity)
            continue
        saved_hash = hashlib.sha256(message.get('text', '').encode()).hexdigest()
        if saved_hash != row['sourceSha256']:
            row = {**row, 'observedSourceSha256': row['sourceSha256'], 'sourceSha256': saved_hash}
        if not work.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone():
            work.receipts.insert(identity, fingerprint(row), row)
        staged.append(identity)
    return staged


def acknowledge_saved_inputs(work, identities):
    for identity in identities:
        work.pending_input_facts.pop(identity, None)


def mapped(work, captured, item):
    identity = 'work-input:' + fingerprint([captured['sessionId'], captured['messageId']])
    row = work.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    if not row or json.loads(row[0])['sourceSha256'] != captured['sha256']:
        return  # Missing historical coverage is not backfilled by inference.
    review_id = 'work-input-map:' + fingerprint([identity, item['id'], item['promiseRevision']])
    if work.db.execute('SELECT 1 FROM commands WHERE id=?', (review_id,)).fetchone():
        return
    value = {'commandAction': REVIEW, 'inputId': identity, 'sessionId': captured['sessionId'],
             'sourceSha256': captured['sha256'], 'disposition': 'mapped', 'workItemId': item['id'],
             'promiseRevision': item['promiseRevision'], 'recordedAt': time.time()}
    work.receipts.insert(review_id, fingerprint(value), value)


def read(work, workspace, args):
    from .workspace_work import _encode, _decode
    root = args.get('sessionId')
    where = "json_extract(i.receipt,'$.commandAction')=? AND json_extract(i.receipt,'$.workspace')=?"
    params = [INPUT, workspace['path']]
    if root:
        where += " AND json_extract(i.receipt,'$.sessionId')=?"
        params.append(root)
    input_version = work.db.execute('SELECT coalesce(max(i.rowid),0) FROM commands i WHERE ' + where, params).fetchone()[0]
    review_version = work.db.execute("SELECT coalesce(max(r.rowid),0) FROM commands i JOIN commands r "
        "ON json_extract(r.receipt,'$.inputId')=i.id WHERE " + where +
        " AND json_extract(r.receipt,'$.commandAction')=?", [*params, REVIEW]).fetchone()[0]
    version = max(input_version, review_version)
    after_id = ''
    if args.get('after'):
        cursor = _decode(args['after'])
        if not isinstance(cursor, list) or len(cursor) != 4 or cursor[:3] != [workspace['id'], root, version] or not isinstance(cursor[3], str):
            work.error('Capture coverage changed; refresh its bounded page.')
        after_id = cursor[3]
    latest = "(SELECT json_extract(r.receipt,'$.disposition') FROM commands r WHERE json_extract(r.receipt,'$.commandAction')=? AND json_extract(r.receipt,'$.inputId')=i.id AND json_extract(r.receipt,'$.sourceSha256')=json_extract(i.receipt,'$.sourceSha256') ORDER BY r.rowid DESC LIMIT 1)"
    unresolved = "coalesce(" + latest + ",'unreviewed') NOT IN ('mapped','ordinary_conversation')"
    total = work.db.execute('SELECT count(*) FROM commands i WHERE ' + where, params).fetchone()[0]
    remaining = work.db.execute('SELECT count(*) FROM commands i WHERE ' + where + ' AND ' + unresolved, [*params, REVIEW]).fetchone()[0]
    limit = args.get('limit', 20)
    rows = work.db.execute('SELECT i.receipt FROM commands i WHERE ' + where + ' AND ' + unresolved + ' AND i.id>? ORDER BY i.id LIMIT ?',
                          [*params, REVIEW, after_id, limit + 1]).fetchall()
    items = [json.loads(row[0]) for row in rows[:limit]]
    for item in items:
        item.pop('commandAction', None)
        item.pop('workspace', None)
        item['readRef'] = {'action': 'coordination.read', 'args': {'sessionId': item['sessionId'], 'messageId': item['messageId']}}
        review = work.db.execute("SELECT receipt FROM commands WHERE json_extract(receipt,'$.commandAction')=? "
            "AND json_extract(receipt,'$.inputId')=? ORDER BY rowid DESC LIMIT 1", (REVIEW, item['id'])).fetchone()
        item['reviewDisposition'] = json.loads(review[0])['disposition'] if review else 'unreviewed'
    return {'observedInputs': total, 'unreviewedInputs': remaining, 'items': items,
            'truncated': len(rows) > limit,
            'nextAfter': _encode([workspace['id'], root, version, items[-1]['id']]) if len(rows) > limit else None,
            'historicalCoverage': 'incomplete',
            'detail': 'Only host-observed human inputs are counted. They may be requests, corrections or ordinary conversation. Older and native-only history is not inventoried.'}


async def review(work, workspace, args, origin, command_id, caller):
    actor = work.actor(args, origin, caller, workspace)
    if not command_id:
        work.error('Capture review requires a stable command ID.', 400)
    digest = fingerprint(['workspace.work.reviewInput', args, origin, actor['id']])
    async with work.service.lock:
        work.actor(args, origin, caller, workspace)
        prior = work.receipts.duplicate(command_id, digest)
        if prior:
            return prior
        value = work.receipts.receipt(args['inputId'])
        if value.get('commandAction') != INPUT or value.get('sessionId') != actor['id'] or value.get('workspace') != workspace['path']:
            work.error('Review only the calling root\'s exact observed human input.', 403)
        if value['sourceSha256'] != args['sourceSha256'] or not args['reason'].strip():
            work.error('Capture review source changed or reason is empty.')
        receipt = {'accepted': True, 'commandAction': REVIEW, 'inputId': args['inputId'],
                   'sessionId': actor['id'], 'sourceSha256': args['sourceSha256'],
                   'disposition': args['disposition'], 'reason': args['reason'],
                   'actor': origin, 'recordedAt': time.time(),
                   'result': {'inputId': args['inputId'], 'disposition': args['disposition']}}
        work.receipts.insert(command_id, digest, receipt)
        work.db.commit()
        return receipt
