"""App-owned organization and deliberately published immutable snapshots."""
from __future__ import annotations

import hashlib
import html
import json
import secrets
import time
import uuid


def definitions(schema, string):
    identity = string(200)
    ids = {'type': 'array', 'maxItems': 10000, 'uniqueItems': True, 'items': identity}
    return {
        'session.archive': ('Archive a root conversation without stopping its work, changing selection, or deleting history.', schema({'id': identity})),
        'session.restore': ('Restore an archived conversation to the active library without replaying work.', schema({'id': identity})),
        'session.pinOrder': ('Reorder every currently pinned root chat; include every ID exactly once.', schema({'ids': ids})),
        'session.sharePreview': ('Freeze a readable conversation snapshot for inspection. Creates no public link. Includes visible text and voice history; artifact/attachment references do not embed their contents.', schema({'sessionId': identity})),
        'session.shareRead': ('Read a bounded page of an immutable snapshot by ID.', schema({'id': identity, 'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 16000}}, ['id'])),
        'session.shareCreate': ('Publish the exact preview to anyone who has the link and can reach this host. Requires its content hash; later chat edits never alter the snapshot.', schema({'id': identity, 'contentHash': string(64), 'visibility': {'const': 'anyone_with_link'}, 'expiresInSeconds': {'type': 'integer', 'minimum': 60, 'maximum': 31536000}}, ['id', 'contentHash', 'visibility'])),
        'session.shareList': ('List snapshots and their link/revocation/expiry status for a conversation.', schema({'sessionId': identity, 'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, ['sessionId'])),
        'session.shareRevoke': ('Revoke a shared link. Existing copies held by recipients cannot be recalled; source history remains intact.', schema({'id': identity})),
    }


def organization(state):
    return state.setdefault('conversationOrganization', {'archived': {}, 'collections': []})


def projection(state, session_ids=()):
    value = organization(state)
    return {'archivedCount': len(value['archived']),
        'archived': {sid: value['archived'][sid] for sid in session_ids if sid in value['archived']}}


def _same_ids(actual, expected):
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError('Include every current ID exactly once; refresh and retry if the list changed.')


class ConversationLibrary:
    def __init__(self, service):
        self.service = service
        service.db.execute('CREATE TABLE IF NOT EXISTS conversation_shares (id TEXT PRIMARY KEY, token TEXT UNIQUE, session_id TEXT NOT NULL, created_at REAL NOT NULL, value TEXT NOT NULL)')
        service.db.execute('CREATE INDEX IF NOT EXISTS conversation_shares_session ON conversation_shares(session_id,created_at DESC,id DESC)')
        organization(service.state)

    def root(self, sid):
        from .session_navigation import is_top_level
        row = self.service._session(sid)
        if not is_top_level(row):
            raise ValueError('Organize the parent conversation; worker history stays with its parent.')
        return row

    def snapshot(self, identity):
        row = self.service.db.execute('SELECT token,value FROM conversation_shares WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise ValueError('Snapshot is unavailable.')
        return row[0], json.loads(row[1])

    def save(self, row, token=None):
        self.service.db.execute('INSERT OR REPLACE INTO conversation_shares VALUES (?,?,?,?,?)',
            (row['id'], token, row['sessionId'], row['createdAt'], json.dumps(row)))

    def describe(self, row, token=None):
        result = {key: row[key] for key in ('id', 'sessionId', 'title', 'createdAt', 'contentHash', 'characters', 'sharedAt', 'expiresAt', 'revokedAt') if key in row}
        result['status'] = ('revoked' if row.get('revokedAt') else 'expired' if row.get('expiresAt', float('inf')) <= time.time()
            else 'shared' if row.get('sharedAt') else 'preview')
        if token and result['status'] == 'shared':
            result['path'] = '/share/' + token
        return result

    def perform(self, action, args, prepared=None):
        state, db = self.service.state, self.service.db
        value = organization(state)
        if action in {'session.archive', 'session.restore'}:
            row = self.root(args['id'])
            if action == 'session.archive':
                value['archived'].setdefault(row['id'], time.time())
            else:
                value['archived'].pop(row['id'], None)
            return {'id': row['id'], 'archived': row['id'] in value['archived']}
        if action == 'session.pinOrder':
            _same_ids(args['ids'], state.get('pinnedSessionIds', []))
            state['pinnedSessionIds'] = list(args['ids'])
            state['pinOrderCustomized'] = True
            return {'ids': list(args['ids'])}
        if action == 'session.sharePreview':
            source, content = prepared
            if len(content.encode('utf-8')) > 10_000_000:
                raise ValueError('The snapshot exceeds 10 MB. Export the conversation as a file instead.')
            from .resource_files import put
            row = {'id': uuid.uuid4().hex, 'sessionId': source['id'], 'title': source['title'],
                'createdAt': time.time(), 'contentHash': hashlib.sha256(content.encode()).hexdigest(),
                'characters': len(content), 'content': put(db, content)}
            self.save(row)
            return {**self.describe(row), 'text': content[:16000], 'nextOffset': 16000 if len(content) > 16000 else None}
        if action == 'session.shareList':
            self.service._session(args['sessionId'])
            offset, limit = args.get('offset', 0), args.get('limit', 20)
            rows = list(db.execute('SELECT token,value FROM conversation_shares WHERE session_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (args['sessionId'], limit+1, offset)))
            return {'items': [self.describe(json.loads(text), token) for token, text in rows[:limit]],
                'nextOffset': offset+limit if len(rows) > limit else None}
        token, row = self.snapshot(args['id'])
        if action == 'session.shareRead':
            from .state_storage import resource
            content = resource(db, row['content']['$resource'])
            offset, limit = args.get('offset', 0), args.get('limit', 16000)
            return {**self.describe(row, token), 'offset': offset, 'text': content[offset:offset+limit],
                'nextOffset': offset+limit if offset+limit < len(content) else None}
        if action == 'session.shareCreate':
            if args['contentHash'] != row['contentHash']:
                raise ValueError('The preview hash does not match. Inspect the exact snapshot before sharing.')
            if row.get('revokedAt') or row.get('expiresAt', float('inf')) <= time.time():
                raise ValueError('This link was revoked or expired. Create a new preview to share again.')
            if not token:
                token = secrets.token_urlsafe(32)
                row.update(sharedAt=time.time(), expiresAt=time.time()+args.get('expiresInSeconds', 604800))
                self.save(row, token)
            return self.describe(row, token)
        if action == 'session.shareRevoke':
            if not row.get('revokedAt'):
                row['revokedAt'] = time.time()
                self.save(row, token)
            return self.describe(row)
        raise ValueError('Unknown conversation library action.')

    def public_snapshot(self, token):
        saved = self.service.db.execute('SELECT value FROM conversation_shares WHERE token=?', (token,)).fetchone()
        if saved is None:
            return None
        row = json.loads(saved[0])
        if self.describe(row)['status'] != 'shared':
            return None
        from .state_storage import resource
        content = resource(self.service.db, row['content']['$resource'])
        # Plain escaped source prevents a shared message from running scripts,
        # loading tracking images, or issuing requests with the viewer's account.
        return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<meta name="robots" content="noindex,nofollow,noarchive"><title>'+html.escape(row['title'])+'</title>'
            '<style>body{font:16px system-ui;margin:2rem auto;padding:0 1rem;max-width:54rem;color:#202435}'
            'pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}p{color:#536076}</style>'
            '<h1>'+html.escape(row['title'])+'</h1><p>Immutable conversation snapshot. Later edits are not included. '
            'Attachment and artifact contents are not embedded.</p><pre>'+html.escape(content)+'</pre></html>')
