"""Client-neutral, read-only observations over canonical session facts.

Pages inspect at most SCAN_BUDGET slots and retain at most a page. Cursors
seek directly into catalog/notice slots; no sorted copy or snapshot per client.
"""
import base64
import hashlib
import hmac
import json
import secrets

from aiohttp import web

from .attention import session_item_at, session_item_count
from .auth import data_identity
from .service import AppError
from .session_activity import activity_flags
from .session_navigation import is_top_level

SCAN_BUDGET = 1024
MAX_PAGE = 200
COUNT_BUDGET = 4096
CAPABILITIES = {
    'observations': {'version': 1, 'sessions': '/api/sessions?view=observation',
                     'attention': '/api/attention', 'activity': '/api/activity',
                     'maxPageSize': MAX_PAGE, 'conditionalRequests': True, 'incrementalCounts': True},
}


def included(row, scope):
    if row.get('sessionKind') == 'internal':
        return False
    root = is_top_level(row)
    return scope == 'all' or (root if scope == 'roots' else not root)


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
        self.pins_cache = None

    @property
    def revision(self):
        return f'{self.service.instance_id}:{self.service.projections.observation_generation}'

    def require_client(self):
        if self.service.clients.current.get() is None:
            raise AppError('Attach a client and send X-Amplifier-Client.', 400)

    def pins(self):
        if self.pins_cache is None or self.pins_cache[0] != self.revision:
            self.pins_cache = (self.revision, set(self.service._state.get('pinnedSessionIds', [])))
        return self.pins_cache[1]

    def cursor(self, query, position):
        raw = json.dumps([self.revision, query, position], separators=(',', ':'), sort_keys=True).encode()
        signature = hmac.digest(self.secret, raw, 'sha256')
        return base64.urlsafe_b64encode(signature + raw).decode().rstrip('=')

    def position(self, token, query):
        if not token:
            return [0, 0]
        try:
            if len(token) > 4096:
                raise ValueError()
            decoded = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
            signature, raw = decoded[:32], decoded[32:]
            if not hmac.compare_digest(signature, hmac.digest(self.secret, raw, 'sha256')):
                raise ValueError()
            revision, saved_query, position = json.loads(raw)
            if query != saved_query or len(position) != 2 or any(type(i) is not int or i < 0 for i in position):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise AppError('Invalid cursor or cursor query mismatch.', code='invalid_cursor') from None
        if revision != self.revision:
            raise AppError('The observation changed; restart from the first page.', 409, code='snapshot_changed')
        return position

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
        position, slot = self.position(params.get('cursor'), query)
        rows = self.service._state['sessions']
        read = self.service._state.get('attentionRead', {})
        pins = self.pins() if collection == 'sessions' else None
        items, inspected = [], 0
        while position < len(rows) and inspected < SCAN_BUDGET and len(items) < query['limit']:
            row = rows[position]
            inspected += 1
            if not included(row, query['scope']):
                position, slot = position + 1, 0
                continue
            if collection == 'sessions':
                position += 1
                flags, pinned = activity_flags(row), row['id'] in pins
                activity = query['activity']
                active = flags['execution'] or flags['preparing']
                matches = (activity == 'all' or flags.get(activity) or activity == 'idle' and not any(flags.values())
                           or activity == 'active' and active or activity == 'active-or-pinned' and (active or pinned))
                if not matches or query['pinned'] != 'all' and pinned != (query['pinned'] == 'true'):
                    continue
                if query['workspace'] is not None and row.get('workspace') != query['workspace']:
                    continue
                items.append({'id': row['id'], 'title': row.get('title', 'Chat')[:200],
                              'status': row.get('status', 'idle'), 'activity': flags, 'pinned': pinned,
                              'role': 'root' if is_top_level(row) else 'delegate'})
            else:
                item = session_item_at(row, read, slot)
                if item is not None and (query['read'] == 'all' or item['read'] == (query['read'] == 'true')):
                    items.append(public_notice(row, item, slot))
                slot += 1
                if slot >= session_item_count(row):
                    position, slot = position + 1, 0
        return {'revision': self.revision, 'items': items,
                'nextCursor': self.cursor(query, [position, slot]) if position < len(rows) else None}

    def activity(self, scope='roots', cursor=None):
        if scope not in {'roots', 'delegates', 'all'}:
            raise AppError('Unsupported activity scope.')
        query = {'collection': 'activity', 'scope': scope}
        self.position(cursor, query)  # Validate revision/query even when another reader advanced the scan.
        if self.counts_cache is None or self.counts_cache['revision'] != self.revision:
            self.counts_cache = {'revision': self.revision, 'position': 0, 'slot': -1,
                'counts': {role: dict(executionCount=0, preparingCount=0, loadingCount=0,
                                     attentionCount=0, requiresActionCount=0) for role in ('roots', 'delegates')}}
        cache = self.counts_cache
        rows = self.service._state['sessions']
        read = self.service._state.get('attentionRead', {})
        inspected = 0
        while cache['position'] < len(rows) and inspected < COUNT_BUDGET:
            row = rows[cache['position']]
            inspected += 1
            if row.get('sessionKind') == 'internal':
                cache['position'] += 1
                cache['slot'] = -1
                continue
            count = cache['counts']['roots' if is_top_level(row) else 'delegates']
            if cache['slot'] == -1:
                for flag, value in activity_flags(row).items():
                    count[flag + 'Count'] += int(value)
                cache['slot'] = 0
                continue
            item = session_item_at(row, read, cache['slot'])
            if item is not None and not item['read']:
                count['attentionCount'] += 1
                count['requiresActionCount'] += int(public_notice(row, item, cache['slot'])['requiresAction'])
            cache['slot'] += 1
            if cache['slot'] >= session_item_count(row):
                cache['position'] += 1
                cache['slot'] = -1
        if cache['position'] < len(rows):
            # Never present partial counts as exact totals. A shared on-demand
            # accumulator retains only ten counters and two positions.
            return {'revision': self.revision, 'status': 'collecting',
                    'nextCursor': self.cursor(query, [cache['position'], cache['slot'] + 1])}
        counts = cache['counts']
        value = counts[scope] if scope != 'all' else {k: counts['roots'][k] + counts['delegates'][k] for k in counts['roots']}
        return {'revision': self.revision, 'status': 'ready', 'nextCursor': None, **value}

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
