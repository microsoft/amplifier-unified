"""Shared exact-state display receipts; never read/approval/execution authority."""
import base64
import hashlib
import html
import json
import re

from .errors import AppError
from .session_activity import activity_flags

MAX_ITEMS = 100
MAX_RECEIPTS = 100000


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS client_presentation_receipts '
               '(id TEXT PRIMARY KEY,owner TEXT NOT NULL,fingerprint TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS client_presentation_owner ON client_presentation_receipts(owner)')
    db.execute('CREATE TABLE IF NOT EXISTS client_presentation_revision (id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL)')
    db.execute('INSERT OR IGNORE INTO client_presentation_revision VALUES(1,0)')


def revision(db):
    return db.execute('SELECT revision FROM client_presentation_revision WHERE id=1').fetchone()[0]


def plain(value, limit=240):
    if not isinstance(value, str):
        return ''
    value = value[:2048]
    value = re.sub(r'!?\[([^\]]+)\]\([^)]*\)', r'\1', value)
    value = re.sub(r'\*\*|__|`{1,3}|(?m:^\s{0,3}#{1,6}\s+)', '', value)
    return ' '.join(html.unescape(value).split())[:limit]


def key(kind, session_id, notice_id=None):
    return base64.urlsafe_b64encode(json.dumps([kind, session_id, notice_id], separators=(',', ':')).encode()).decode().rstrip('=')


def decode(identity):
    try:
        if not isinstance(identity, str) or not 0 < len(identity) <= 1024:
            raise ValueError()
        parts = json.loads(base64.b64decode(identity + '=' * (-len(identity) % 4), altchars=b'-_', validate=True))
        if (not isinstance(parts, list) or len(parts) != 3 or parts[0] not in {'activity', 'attention'}
                or not isinstance(parts[1], str) or not 0 < len(parts[1]) <= 200
                or (parts[0] == 'activity' and parts[2] is not None)
                or (parts[0] == 'attention' and (not isinstance(parts[2], str) or not 0 < len(parts[2]) <= 300))
                or identity != key(*parts)):
            raise ValueError()
        return parts
    except (ValueError, TypeError, UnicodeError):
        raise AppError('Invalid presentation identity.') from None


def descriptor(kind, row, notice=None):
    if notice is not None:
        fingerprint = notice['fingerprint']
    else:
        if not any(activity_flags(row)[k] for k in ('execution', 'preparing')):
            return None
        from .navigation_waiting import input_boundary
        boundary = input_boundary(row)
        activity = row.get('activityPresentation') or {}
        # Progress timestamps/text/revisions do not define a new action. Persisted
        # input/generation/action identity distinguishes a subsequent run or phase.
        identity = [row.get('status'), row.get('executionRevision', 0),
                    boundary, row.get('turnCount'), activity.get('identity')]
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'id': key(kind, row['id'], notice['id'] if notice else None), 'fingerprint': fingerprint, 'dismissed': False}


def project_receipts(db, session, notices):
    """Bounded point reads on the collection's existing leased SQLite snapshot."""
    def project(item):
        value = item.get('dismissal')
        if not value:
            return item
        saved = db.execute('SELECT fingerprint FROM client_presentation_receipts WHERE id=?', (value['id'],)).fetchone()
        return {**item, 'dismissal': {**value, 'dismissed': bool(saved and saved[0] == value['fingerprint'])}}
    return project(session), tuple(project(item) for item in notices)


def observe_execution(session, node, tree=None):
    """Update one scalar display projection at the canonical execution boundary.

    Prefer active tools to a model wait; inspect only a bounded current node tail.
    Never copy raw tool arguments/results or use labels as execution identity.
    """
    from .execution import LIVE_PHASES
    tree = tree if tree is not None else (dict.get(session, 'execution') or {})
    current_turn = tree.get('currentTurnId')
    if current_turn and node.get('turnId') and node['turnId'] != current_turn:
        return
    nodes = tree.get('nodes') or []
    active = [n for n in nodes[-128:] if n.get('phase') in LIVE_PHASES and n.get('kind') in {'tool', 'llm', 'worker'}
              and n.get('turnId') == node.get('turnId')]
    selected = next((n for n in reversed(active) if n.get('kind') == 'tool'), active[-1] if active else node)
    if selected.get('phase') not in LIVE_PHASES:
        selected = node
    kind = selected.get('kind')
    label = 'Thinking…' if kind == 'llm' else plain(selected.get('label') or selected.get('tool') or selected.get('name'))
    hints = [plain(selected.get(k)) for k in ('actionPath', 'inputSummary', 'summary')]
    detail = next((hint for hint in hints if hint and hint != label), '')
    identity = [selected.get(k) for k in ('id', 'turnId', 'generationId', 'runId', 'toolObservationId', 'kind', 'phase')]
    session['activityPresentation'] = {'identity': identity, 'kind': kind,
        'label': label or 'Working…', 'summary': plain(label + (' · ' + detail if detail else ''))}


def dismiss(service, items):
    """Validate the entire bounded batch under dispatch's mutation lock first."""
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise AppError('Choose between 1 and 100 observed updates.')
    decoded = []
    for item in items:
        if (not isinstance(item, dict) or set(item) != {'id', 'fingerprint'}
                or not isinstance(item['fingerprint'], str) or not 0 < len(item['fingerprint']) <= 100):
            raise AppError('Use exact observed presentation identities and fingerprints.')
        decoded.append((item, decode(item['id'])))
    rows = {r['id']: r for r in service.directory.summary_ids({p[1] for _, p in decoded}, state=service.state)}
    from .client_observations import Observations
    targets = {}
    for sid, row in rows.items():
        session, _, notices = Observations.row_facts(None, row, service.state.get('attentionRead', {}), frozenset())
        if session['role'] == 'internal':
            continue
        targets[sid] = {value['id']: value for item in (session, *notices) if (value := item.get('dismissal'))}
    for item, parts in decoded:
        current = targets.get(parts[1], {}).get(item['id'])
        if current is None or current['fingerprint'] != item['fingerprint']:
            raise AppError('This update changed. Refresh before dismissing it.', 409, code='presentation_changed')
    db = service.db
    additions = sum(not db.execute('SELECT 1 FROM client_presentation_receipts WHERE id=?', (item['id'],)).fetchone() for item, _ in decoded)
    if additions and db.execute('SELECT COUNT(*) FROM client_presentation_receipts').fetchone()[0] + additions > MAX_RECEIPTS:
        raise AppError('Presentation receipt storage is full.', 503, code='presentation_full')
    changed = False
    for item, parts in decoded:
        old = db.execute('SELECT fingerprint FROM client_presentation_receipts WHERE id=?', (item['id'],)).fetchone()
        if old and old[0] == item['fingerprint']:
            continue
        db.execute('INSERT INTO client_presentation_receipts VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET fingerprint=excluded.fingerprint',
                   (item['id'], parts[1], item['fingerprint']))
        changed = True
    if changed:
        db.execute('UPDATE client_presentation_revision SET revision=revision+1 WHERE id=1')
