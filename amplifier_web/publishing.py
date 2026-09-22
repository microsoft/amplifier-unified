"""Conversation policy over the portable, local publishing lifecycle."""

import asyncio
import hashlib
import json
import re
from pathlib import Path

from amplifier_publishing import Publisher, PublishingError

from .host_identity import local_host_identity, require_local_host


def definitions(schema, string):
    task = {'sessionId': string(200)}
    site = {**task, 'siteId': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9-]{0,62}$'}}
    request = {'requestId': {**string(200), 'pattern': '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$'}}
    release = {**task, 'releaseId': {**string(100), 'minLength': 1}}
    revision = {'expectedRevision': {'type': 'integer', 'minimum': 0}}
    return {
        'publishing.list': ('Inspect this task’s immutable static releases, local deployments and durable receipts. URLs are reachable only on the server host; this does not start or replay work.', schema(task)),
        'publishing.build': ('Package an existing built static output directory inside this task’s execution folder into an immutable release. Run the project’s own build first. No build command is executed, no model starts, and no site is deployed. Reuse requestId only for an exact retry.', schema({**site, **request, 'sourcePath': {**string(4000), 'minLength': 1}})),
        'publishing.preview': ('Start a separate loopback preview of an exact saved release. It has no access to the app origin. Preview is not review or deployment. Reuse requestId for exact retry and inspect current status separately.', schema({**release, **request})),
        'publishing.review': ('Record a review note on the exact immutable release after inspecting its content. This does not prove visual inspection or deploy it.', schema({**release, **request, 'note': {**string(4000), 'minLength': 1}})),
        'publishing.deploy': ('Explicitly activate a reviewed static release on this server’s loopback interface. Requires the current deployment revision (0 for a new site). It never makes a public or remote URL. Historical receipts are not current liveness evidence.', schema({**site, **release, **request, **revision})),
        'publishing.rollback': ('Explicitly restore a previously deployed, reviewed immutable release using the current site revision. Does not rerun a build or replay unknown effects.', schema({**site, **release, **request, **revision})),
        'publishing.status': ('Read current local deployment status, release identity, access policy and URL. Does not start a listener.', schema(site)),
        'publishing.logs': ('Read durable lifecycle receipts for this task and site. HTTP bodies, credentials and build environment values are not captured.', schema(site)),
        'publishing.stop': ('Stop this site’s local listeners, retaining immutable releases and durable receipts. Requires current revision.', schema({**site, **request, **revision})),
        'publishing.remove': ('Remove this local deployment and previews, retaining releases and receipts for audit. Source files are preserved. Requires current revision.', schema({**site, **request, **revision})),
    }


class Publishing:
    def __init__(self, app):
        self.app = app
        self.store = Publisher(app.data_dir / 'publishing')
        self.lock = asyncio.Lock()
        self.app.db.execute('CREATE TABLE IF NOT EXISTS publishing_build_requests (session TEXT, request TEXT, signature TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(session,request))')
        self.app.db.commit()

    @staticmethod
    def target():
        return {'id': 'loopback', 'accessPolicy': 'loopback-only', 'host': local_host_identity(),
                'detail': 'URLs are reachable only on this server. External hosting is not configured.'}

    def snapshot(self, sid):
        return {'sites': self.store.list(sid), 'releases': self.store.releases(sid),
                'receipts': self.store.receipts(sid), 'target': self.target()}

    def owns_records(self, sid):
        return bool(self.store.releases(sid) or self.store.list(sid) or self.store.receipts(sid)
                    or self.app.db.execute('SELECT 1 FROM publishing_build_requests WHERE session=? LIMIT 1', (sid,)).fetchone())

    def build_source(self, session, args):
        """Bind logical UI requests to their first execution folder durably.

        A retry retrieves a receipt even when a handoff moved the execution
        folder or the original source disappeared. New requests still require
        source admission before any snapshot is made.
        """
        sid, rid = session['id'], args['requestId']
        if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}', rid):
            raise PublishingError('invalid_argument', 'requestId must be a bounded plain identifier')
        signature = json.dumps(args, sort_keys=True, separators=(',', ':'))
        previous = self.app.db.execute('SELECT signature,source FROM publishing_build_requests WHERE session=? AND request=?', (sid, rid)).fetchone()
        if previous:
            if previous[0] != signature:
                raise PublishingError('request_conflict', 'requestId was already used with different build arguments')
            if not any(r['requestId'] == rid for r in self.store.receipts(sid)):
                raise PublishingError('unknown_outcome', 'Build admission was recorded without a publishing receipt. It will not be replayed; inspect the retained request.')
            return Path(previous[1])
        if any(r['requestId'] == rid for r in self.store.receipts(sid)):
            raise PublishingError('request_conflict', 'requestId was already used by another publishing action')
        host = session.get('executionHost')
        if host:
            if host.get('scope') != 'local':
                raise ValueError('Publishing requires a local execution folder; remote hosting is not configured.')
            require_local_host(host.get('id'))
        if session.get('configurationBusy') or session.get('_deleting'):
            raise ValueError('Wait for this task’s execution folder change to finish before publishing.')
        source = self.source(session, args['sourcePath'])
        self.app.db.execute('INSERT INTO publishing_build_requests VALUES (?,?,?,?)', (sid, rid, signature, str(source)))
        self.app.db.commit()
        return source

    def admission_receipts(self, sid, receipts):
        known = {r['requestId'] for r in receipts}
        for rid, signature in self.app.db.execute('SELECT request,signature FROM publishing_build_requests WHERE session=?', (sid,)):
            if rid not in known:
                args = json.loads(signature)
                receipts.append({'id': hashlib.sha256((sid + ':' + rid).encode()).hexdigest(), 'requestId': rid,
                                 'sessionId': sid, 'siteId': args['siteId'], 'action': 'build', 'state': 'unknown',
                                 'error': {'code': 'unknown_outcome', 'message': 'Admission was saved without a publishing receipt; no work was replayed.'}})
        return receipts

    def source(self, session, value):
        relative = Path(value)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Choose a built output directory inside this task’s execution folder.')
        workspace = Path(session.get('workingDirectory') or session['workspace']).resolve()
        source = workspace / relative
        current = source
        while current != workspace:
            if current.is_symlink():
                raise ValueError('Publishing source paths cannot contain symbolic links.')
            current = current.parent
        if not source.resolve().is_relative_to(workspace):
            raise ValueError('Choose a built output directory inside this task’s execution folder.')
        return source

    async def dispatch(self, action, args, origin, command_id):
        # One serialized adapter path is shared by UI and agent callers. The
        # library supplies cross-process locking and durable request receipts.
        async with self.lock:
            session = self.app._session(args['sessionId'])
            sid = session['id']
            if action == 'publishing.list':
                result = await asyncio.to_thread(self.snapshot, sid)
                result['receipts'] = self.admission_receipts(sid, result['receipts'])
                return result
            if action == 'publishing.status':
                return await asyncio.to_thread(self.store.status, args['siteId'], sid)
            if action == 'publishing.logs':
                records = await asyncio.to_thread(self.store.receipts, sid)
                self.admission_receipts(sid, records)
                return {'items': [r for r in records if r.get('siteId') == args['siteId']], 'target': self.target()}
            kw = {'session_id': sid, 'request_id': args['requestId']}
            if action != 'publishing.build' and self.app.db.execute('SELECT 1 FROM publishing_build_requests WHERE session=? AND request=?', (sid, args['requestId'])).fetchone():
                raise PublishingError('request_conflict', 'requestId was already used by a build request')
            if action == 'publishing.build':
                result = await asyncio.to_thread(self.store.build, self.build_source(session, args), site_id=args['siteId'], **kw)
            elif action == 'publishing.review':
                result = await asyncio.to_thread(self.store.review, args['releaseId'], note=args['note'], **kw)
            elif action == 'publishing.preview':
                result = await asyncio.to_thread(self.store.preview, args['releaseId'], **kw)
            elif action in {'publishing.deploy', 'publishing.rollback'}:
                operation = self.store.deploy if action == 'publishing.deploy' else self.store.rollback
                result = await asyncio.to_thread(operation, args['releaseId'], site_id=args['siteId'], expected_revision=args['expectedRevision'], **kw)
            elif action in {'publishing.stop', 'publishing.remove'}:
                operation = self.store.stop if action == 'publishing.stop' else self.store.remove
                result = await asyncio.to_thread(operation, args['siteId'], expected_revision=args['expectedRevision'], **kw)
            else:
                raise ValueError('Unknown publishing action.')
            return result

    async def close(self):
        async with self.lock:
            await asyncio.to_thread(self.store.close)
