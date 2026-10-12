"""Client-neutral, read-only observations over canonical session facts.

One shared public-facts index feeds bounded, leased snapshots. Semantic
revisions ignore presentation/transcript changes; cursors finish a coherent
collection even when its live facts change. No snapshot is retained per query.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict

from aiohttp import web

from .attention import session_item_at, session_item_count
from .auth import data_identity
from .service import AppError
from .session_activity import activity_flags
from .session_navigation import is_top_level

SCAN_BUDGET = 1024
MAX_PAGE = 200
COUNT_BUDGET = 4096
SNAPSHOT_SECONDS = 600
MAX_SNAPSHOTS = 8
CAPABILITIES = {
    'observations': {'version': 1, 'sessions': '/api/sessions?view=observation',
                     'attention': '/api/attention', 'activity': '/api/activity',
                     'maxPageSize': MAX_PAGE, 'conditionalRequests': True, 'incrementalCounts': True},
}


def public_notice(row, item, slot):
    prefix, identity = item['id'].split(':', 1)
    kind = {'session': 'session-error', 'unread': 'manual-unread'}.get(prefix, prefix)
    target = {'type': 'session', 'sessionId': row['id']}
    timestamp = None
    requires_action = kind in {'question', 'approval', 'session-error'}
    if prefix == 'question':
        question = row['questions'][slot]
        target['questionId'] = identity
        timestamp = question.get('updatedAt', question.get('createdAt'))
        requires_action = bool(question.get('required') or question.get('status') != 'pending')
    elif prefix == 'approval':
        approval = row['approvals'][slot - len(row.get('questions', [])) - 3]
        target['approvalId'] = identity
        timestamp = approval.get('createdAt')
    elif prefix == 'completion':
        timestamp = row['completion'].get('at')
    elif prefix == 'session':
        timestamp = row.get('errorAt')
    # Only allowlisted labels, never canonical detail/prompt/tool arguments.
    summary = 'Approval requested' if prefix == 'approval' else item['title'][:200]
    return {'id': item['id'], 'fingerprint': item['fingerprint'], 'kind': kind,
            'target': target, 'requiresAction': requires_action, 'read': item['read'],
            'summary': summary, 'title': row.get('title', 'Chat')[:200],
            'timestamp': timestamp if type(timestamp) in {int, float} else None}


class Observations:
    def __init__(self, service):
        self.service = service
        self.secret = secrets.token_bytes(32)
        self.counts_cache = None
        self.facts = None
        self.order = ()
        self.dirty = set()
        self.all_dirty = True
        self.pending = True
        self.generations = dict(sessions=0, attention=0, activity=0)
        self.snapshots = OrderedDict()
        # No catalog work on hosts that never read an observation resource.
        service.projections.observation_listener = self.invalidate

    def invalidate(self, state=None, session_ids=None, detail_only=False):
        if detail_only and session_ids is not None:
            # The service's narrow publication contract has already compared
            # notice/lifecycle facts. Transcript deltas cannot affect this index.
            return
        self.pending = True
        if session_ids is None or self.facts is None:
            self.all_dirty = True
            self.dirty.clear()
        elif not self.all_dirty:
            self.dirty.update(session_ids)

    def row_facts(self, row, read, pins):
        role = 'internal' if row.get('sessionKind') == 'internal' else 'root' if is_top_level(row) else 'delegate'
        session = {'id': row['id'], 'title': row.get('title', 'Chat')[:200],
                   'status': row.get('status', 'idle'), 'activity': activity_flags(row),
                   'pinned': row['id'] in pins, 'role': role}
        notices = tuple(public_notice(row, item, slot)
                        for slot in range(session_item_count(row))
                        if (item := session_item_at(row, read, slot)) is not None) if role != 'internal' else ()
        return (session, row.get('workspace'), notices)

    def refresh(self):
        """One shared public-facts index; retain unchanged rows by identity.

        Cold construction and unscoped saves cost O(catalog + notices). Scoped
        saves inspect only their rows; proven transcript-only saves cost zero.
        No transcript, prompt, tool argument or error body is retained here.
        """
        if not self.pending:
            return
        state = self.service._state
        pins = frozenset(state.get('pinnedSessionIds', ()))
        read = state.get('attentionRead', {})
        if self.facts is None:
            self.facts = {}
            self.saved_pins, self.saved_read = frozenset(), {}
        pins_changed = pins != self.saved_pins
        read_changed = read != self.saved_read
        if not (self.all_dirty or self.dirty or pins_changed or read_changed):
            self.pending = False
            return
        # Global receipts/pins affect public facts even on an otherwise scoped save.
        full = self.all_dirty or pins_changed or read_changed
        rows = state['sessions']
        if full:
            order = tuple(row['id'] for row in rows)
            candidates = rows
        else:
            index = self.service.projections.sessions(state)
            order = self.order
            candidates = [index.by_id[sid] for sid in self.dirty if sid in index.by_id]
            # Membership changes must use catalog order, not dirty-set order.
            if any(sid not in self.facts or sid not in index.by_id for sid in self.dirty):
                order = tuple(row['id'] for row in rows)
        changed = set()
        if order != self.order:
            changed.update(self.generations)
            retained = set(order)
            self.facts = {sid: facts for sid, facts in self.facts.items() if sid in retained}
        for row in candidates:
            new = self.row_facts(row, read, pins)
            old = self.facts.get(row['id'])
            if old == new:
                continue
            if old is None or old[:2] != new[:2]:
                changed.add('sessions')
            if old is None or (old[0]['role'], old[2]) != (new[0]['role'], new[2]):
                changed.add('attention')
            if old is None or self.count_facts(old) != self.count_facts(new):
                changed.add('activity')
            self.facts[row['id']] = new
        for collection in changed:
            self.generations[collection] += 1
        self.order = order
        self.saved_pins, self.saved_read = pins, dict(read)
        self.all_dirty = False
        self.pending = False
        self.dirty.clear()

    @staticmethod
    def count_facts(facts):
        session, _, notices = facts
        return (session['role'], session['activity'],
                sum(not item['read'] for item in notices),
                sum(not item['read'] and item['requiresAction'] for item in notices))

    @property
    def revision(self):
        self.refresh()
        return self.revision_for('activity')

    def revision_for(self, collection):
        return f'{self.service.instance_id}:{collection}:{self.generations[collection]}'

    def require_client(self):
        if self.service.clients.current.get() is None:
            raise AppError('Attach a client and send X-Amplifier-Client.', 400)

    def pins(self):
        self.refresh()
        return self.saved_pins

    def cursor(self, query, position, revision):
        self.snapshots[revision]['leased'] = True
        raw = json.dumps([revision, query, position], separators=(',', ':'), sort_keys=True).encode()
        signature = hmac.digest(self.secret, raw, 'sha256')
        return base64.urlsafe_b64encode(signature + raw).decode().rstrip('=')

    def snapshot(self, token, query):
        self.refresh()
        now = time.monotonic()
        for key, value in list(self.snapshots.items()):
            if now >= value['expires']:
                del self.snapshots[key]
        revision = self.revision_for(query['collection'])
        position = [0, 0]
        if token:
            try:
                if len(token) > 4096:
                    raise ValueError()
                decoded = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
                signature, raw = decoded[:32], decoded[32:]
                if not hmac.compare_digest(signature, hmac.digest(self.secret, raw, 'sha256')):
                    raise ValueError()
                revision, saved_query, position = json.loads(raw)
                if (query != saved_query or not isinstance(revision, str) or len(position) != 2
                        or any(type(i) is not int or i < 0 for i in position)):
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise AppError('Invalid cursor or cursor query mismatch.', code='invalid_cursor') from None
            if revision not in self.snapshots:
                raise AppError('The observation snapshot expired; restart from the first page.',
                               409, code='snapshot_changed')
        if revision not in self.snapshots:
            # Completed, cursor-free responses need no historical lease.
            for key, value in list(self.snapshots.items()):
                if not value['leased'] and key != self.revision_for(key.split(':')[-2]):
                    del self.snapshots[key]
            if len(self.snapshots) >= MAX_SNAPSHOTS:
                # Never evict an in-flight reader to admit a new revision.
                raise AppError('Observation snapshots are busy; retry after a lease expires.',
                               503, code='observation_busy')
            self.snapshots[revision] = {'revision': revision, 'expires': now + SNAPSHOT_SECONDS,
                                       'rows': tuple(self.facts[sid] for sid in self.order), 'counts': None, 'leased': False}
        return self.snapshots[revision], position

    def query(self, params, collection):
        allowed = {'cursor', 'limit', 'scope', 'clientId'}
        allowed |= {'workspace', 'activity', 'pinned', 'view'} if collection == 'sessions' else {'read'}
        if set(params) - allowed or any(len(params.getall(k)) != 1 for k in params if hasattr(params, 'getall')):
            raise AppError('Unsupported or repeated observation parameter.')
        try:
            limit = int(params.get('limit', 100))
        except (TypeError, ValueError):
            raise AppError('Invalid page limit.') from None
        scope = params.get('scope', 'roots')
        if not 1 <= limit <= MAX_PAGE or scope not in {'roots', 'delegates', 'all'}:
            raise AppError('Choose limit 1–200 and scope roots, delegates or all.')
        query = {'collection': collection, 'scope': scope, 'limit': limit}
        if collection == 'sessions':
            activity = params.get('activity', 'all')
            pinned = params.get('pinned', 'all')
            if activity not in {'all', 'execution', 'preparing', 'loading', 'idle', 'active', 'active-or-pinned'} or pinned not in {'all', 'true', 'false'}:
                raise AppError('Unsupported activity or pinned filter.')
            if params.get('view', 'observation') != 'observation':
                raise AppError('Unsupported observation view.')
            workspace = params.get('workspace')
            if workspace is not None and len(workspace.encode()) > 512:
                raise AppError('Workspace filter is too long.')
            query.update(activity=activity, pinned=pinned, workspace=workspace)
        else:
            if params.get('read', 'false') not in {'all', 'true', 'false'}:
                raise AppError('Unsupported read filter.')
            query['read'] = params.get('read', 'false')
        return query

    def page(self, collection, params):
        query = self.query(params, collection)
        snapshot, (position, slot) = self.snapshot(params.get('cursor'), query)
        rows = snapshot['rows']
        items, inspected = [], 0
        while position < len(rows) and inspected < SCAN_BUDGET and len(items) < query['limit']:
            session, workspace, notices = rows[position]
            inspected += 1
            if not self.in_scope(session, query['scope']):
                position, slot = position + 1, 0
                continue
            if collection == 'sessions':
                position += 1
                flags, pinned = session['activity'], session['pinned']
                activity = query['activity']
                active = flags['execution'] or flags['preparing']
                matches = (activity == 'all' or flags.get(activity) or activity == 'idle' and not any(flags.values())
                           or activity == 'active' and active or activity == 'active-or-pinned' and (active or pinned))
                if not matches or query['pinned'] != 'all' and pinned != (query['pinned'] == 'true'):
                    continue
                if query['workspace'] is not None and workspace != query['workspace']:
                    continue
                items.append(session)
            else:
                if slot < len(notices):
                    item = notices[slot]
                    if query['read'] == 'all' or item['read'] == (query['read'] == 'true'):
                        items.append(item)
                slot += 1
                if slot >= len(notices):
                    position, slot = position + 1, 0
        return {'revision': snapshot['revision'], 'items': items,
                'nextCursor': self.cursor(query, [position, slot], snapshot['revision']) if position < len(rows) else None}

    @staticmethod
    def in_scope(session, scope):
        return session['role'] != 'internal' and (scope == 'all' or
            session['role'] == ('root' if scope == 'roots' else 'delegate'))

    def activity(self, scope='roots', cursor=None):
        if scope not in {'roots', 'delegates', 'all'}:
            raise AppError('Unsupported activity scope.')
        query = {'collection': 'activity', 'scope': scope}
        snapshot, _ = self.snapshot(cursor, query)
        if snapshot['counts'] is None:
            snapshot['counts'] = {'position': 0, 'slot': -1,
                'counts': {role: dict(executionCount=0, preparingCount=0, loadingCount=0,
                                     attentionCount=0, requiresActionCount=0) for role in ('roots', 'delegates')}}
        cache = self.counts_cache = snapshot['counts']
        rows = snapshot['rows']
        inspected = 0
        while cache['position'] < len(rows) and inspected < COUNT_BUDGET:
            session, _, notices = rows[cache['position']]
            inspected += 1
            if session['role'] == 'internal':
                cache['position'] += 1
                cache['slot'] = -1
                continue
            count = cache['counts']['roots' if session['role'] == 'root' else 'delegates']
            if cache['slot'] == -1:
                for flag, value in session['activity'].items():
                    count[flag + 'Count'] += int(value)
                cache['slot'] = 0
                continue
            if cache['slot'] < len(notices):
                item = notices[cache['slot']]
                if not item['read']:
                    count['attentionCount'] += 1
                    count['requiresActionCount'] += int(item['requiresAction'])
            cache['slot'] += 1
            if cache['slot'] >= len(notices):
                cache['position'] += 1
                cache['slot'] = -1
        if cache['position'] < len(rows):
            return {'revision': snapshot['revision'], 'status': 'collecting',
                    'nextCursor': self.cursor(query, [cache['position'], cache['slot'] + 1], snapshot['revision'])}
        counts = cache['counts']
        value = counts[scope] if scope != 'all' else {k: counts['roots'][k] + counts['delegates'][k] for k in counts['roots']}
        return {'revision': snapshot['revision'], 'status': 'ready', 'nextCursor': None, **value}

    def response(self, request, value, status=200):
        etag = '"' + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() + '"'
        headers = {'ETag': etag, 'Cache-Control': 'private, no-cache', 'Vary': 'Authorization, Cookie, X-Amplifier-Client'}
        if etag in [part.strip() for part in request.headers.get('If-None-Match', '').split(',')]:
            return web.Response(status=304, headers=headers)
        return web.json_response(value, headers=headers, status=status)

    async def sessions(self, request):
        self.require_client()
        return self.response(request, self.page('sessions', request.query))

    async def attention(self, request):
        self.require_client()
        return self.response(request, self.page('attention', request.query))

    async def summary(self, request):
        self.require_client()
        if set(request.query) - {'scope', 'clientId', 'cursor'} or any(len(request.query.getall(k)) != 1 for k in request.query):
            raise AppError('Unsupported activity parameter.')
        value = self.activity(request.query.get('scope', 'roots'), request.query.get('cursor'))
        return self.response(request, value, 202 if value['status'] == 'collecting' else 200)


def discovery(service):
    return {'hostIdentity': data_identity(service.data_dir), 'capabilities': CAPABILITIES}


def setup_routes(app):
    observations = Observations(app['service'])
    app['observations'] = observations
    app.router.add_get('/api/activity', observations.summary)
    app.router.add_get('/api/attention', observations.attention)
