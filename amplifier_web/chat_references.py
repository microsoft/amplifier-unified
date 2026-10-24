"""Message-bound existing-chat references. Publication is never navigation."""
import copy
import hashlib
import json
from pathlib import Path
import uuid

from .session_identity import reference

MAX_REFERENCES = 20


def label(value, fallback):
    # Labels remain data even in inert Markdown exports. Never accept labels
    # from the authoring action, and never send a target's conversation body.
    text = str(value or fallback)
    return ''.join(c for c in text if c.isprintable())[:200] or fallback


def source_identity(proof):
    """Persist a compact identity, not canonical transcript bodies or caches."""
    if proof.get('revision') is not None:
        return {'canonicalMessageId': proof['fact'][0]}
    row = proof['snapshot']['rows'][0]
    return {'savedMessageId': row['id'],
            'textSha256': hashlib.sha256((row.get('text') or '').encode()).hexdigest()}


def qualified_bindings(proof):
    """Worker-only exact alias digests after the existing identity owner qualified them.

    A streamed digest lets a web UUID survive canonical binding/retirement without
    storing the reply body or treating equal text at another position as identity.
    """
    from .media_saved_aliases import DisplayJSON
    from .resource_files import project_detached
    bindings = {}
    for row in [*proof['snapshot']['rows'], *proof.get('residentClaimants', [])]:
        bindings[row['id']] = hashlib.sha256((row.get('text') or '').encode()).hexdigest()
    if proof.get('detached') is not None:
        def project(stream):
            rows = []
            for row in DisplayJSON(stream).rows():
                if row.get('nativeIndex') == proof['fact'][3]:
                    if len(rows) == 2:
                        raise ValueError('Ambiguous saved assistant binding.')
                    rows.append(row)
            result = {}
            for row in rows:
                digest = hashlib.sha256()
                if 'textOffset' in row:
                    stream.seek(row['textOffset'])
                    DisplayJSON(stream).string(sink=lambda text: digest.update(text.encode()))
                result[row['id']] = digest.hexdigest()
            return result
        saved, signature = project_detached(proof['detached'], project)
        if signature != proof['signature']:
            raise ValueError('The saved assistant changed while qualifying its reference.')
        bindings.update(saved)
    return bindings


def source_matches(record, proof):
    saved = record.get('sourceIdentity', {})
    if 'canonicalMessageId' in saved:
        return saved['canonicalMessageId'] == proof['fact'][0]
    identity = saved.get('savedMessageId')
    return bool(identity and proof['referenceBindings'].get(identity) == saved.get('textSha256'))


def qualify_bindings(proof):
    bindings = qualified_bindings(proof)
    if proof.get('revision') is not None:
        from .automatic_history import revision
        from .media_message_identity import StaleMediaIdentityError
        if revision(proof['snapshot']) != proof['revision']:
            raise StaleMediaIdentityError('The saved assistant changed while qualifying its reference.')
    return bindings


class ChatReferences:
    def __init__(self, outputs):
        self.outputs = outputs
        self.app = outputs.app

    async def identity(self, sid, mid, **kwargs):
        from .resource_files import settle_worker
        proof = await self.outputs.media.identity(sid, mid, **kwargs)
        async with self.outputs.media.workers:
            bindings = await settle_worker(qualify_bindings, proof)
        async with self.app.lock:
            self.assistant(sid, mid, proof)
        return {**proof, 'referenceBindings': bindings}

    def client_scope(self, sid, origin='ui'):
        client = self.app.clients.record()
        if (origin != 'ui' or not client or client.get('kind') != 'web'
                or client.get('selectedSessionId') != sid):
            raise ValueError('Open chat requires a click in the browser displaying its original reply.')

    def target(self, identity, native_project=None):
        target = self.app.directory.resolve(identity, native_project)
        if (not target or target.get('sessionKind', 'root') != 'root'
                or target.get('workspaceAvailable') is False
                or target.get('nativeAvailable') is False):
            raise ValueError('This chat is unavailable. No other chat was opened.')
        return target

    def retained_target(self, record):
        if record.get('referenceImported'):
            raise ValueError('Imported chat references require a new validated attachment.')
        saved = record['target']
        target = self.target(saved['id'], saved['reference'].get('nativeProject'))
        if (target['id'] != saved['id'] or target.get('createdAt') != saved.get('createdAt')
                or reference(target) != saved['reference']):
            raise ValueError('This chat reference no longer identifies the saved destination.')
        return target

    def target_labels(self, target):
        workspace = next((row for row in self.app.state.get('workspaces', [])
                          if row['id'] == target.get('workspaceId')), {})
        name = (workspace.get('name') or workspace.get('label')
                or ('Managed chat' if target.get('location', {}).get('kind') == 'managed'
                    else Path(target.get('workspace') or '').name))
        return label(target.get('title'), 'Untitled chat'), label(name, 'Workspace')

    def descriptor(self, record, message_id=None):
        try:
            target = self.retained_target(record)
            title, workspace = self.target_labels(target)
            available = True
        except ValueError:
            target = None
            title, workspace = record['title'], record['workspace']
            available = False
        archived = self.app.state.get('conversationOrganization', {}).get('archived', {})
        return {key: copy.deepcopy(record[key]) for key in
                ('id', 'sessionId', 'version', 'revision')} | {
            'messageId': message_id or record['origin']['messageId'],
            'targetSessionId': record['target']['id'],
            'title': title, 'workspace': workspace, 'available': available,
            'originalTitle': label(record['title'], 'Untitled chat'),
            'archived': bool(target and target['id'] in archived),
        }

    def assistant(self, sid, message_id, proof):
        session = self.app._session(sid, hydrate=False)
        self.outputs.media.authority(session, 'ui')
        self.outputs.media.assistant(session, message_id, proof)
        if session.get('configurationBusy'):
            raise ValueError('Conversation settings are changing. Retry when they finish.')
        row = next((row for row in dict.get(session, 'messages', [])
                    if row.get('id') == message_id), {})
        if row.get('displayOnly') or row.get('ephemeral'):
            raise ValueError('Choose an original saved assistant reply.')
        return session

    async def attach(self, args, origin, command_id):
        if origin not in {'ui', 'agent'}:
            raise ValueError('Chat references require an authenticated author.')
        sid, mid = args['sessionId'], args['messageId']
        identity = command_id or uuid.uuid4().hex
        request = ['outputs.attachChatReference', args, origin]
        async with self.app.lock:
            session = self.app._session(sid, hydrate=False)
            self.outputs.media.authority(session, origin)
            fence = self.app.portability.write_context(sid)
        proof = await self.identity(sid, mid, origin=origin)
        async with self.app.lock:
            session = self.assistant(sid, mid, proof)
            self.outputs.media.authority(session, origin)
            if self.app.portability.write_context(sid) != fence:
                raise ValueError('The source conversation changed while saving its reference.')
            target = self.target(args['targetSessionId'], args.get('targetNativeProject'))
            previous = self.outputs.store.receipt(identity, request)
            if previous:
                return {'accepted': True, 'result': previous, 'effects': []}
            title, workspace = self.target_labels(target)
            old_revision = session.get('chatReferenceVersion', 0)
            owns = not self.app.db.in_transaction
            if owns:
                self.app.db.execute('BEGIN IMMEDIATE')
            self.app.db.execute('SAVEPOINT chat_reference_attach')
            try:
                record = self.outputs.store.create(sid, {
                    'kind': 'chat_reference', 'title': title, 'workspace': workspace,
                    'origin': {'source': origin, 'messageId': mid},
                    'canonicalMessageId': proof['fact'][0],
                    'sourceIdentity': source_identity(proof),
                    'target': {'id': target['id'], 'createdAt': target.get('createdAt'),
                               'reference': reference(target)},
                    'versionEvidence': 'host-validated-reference',
                })
                result = self.descriptor(record)
                self.outputs.store.remember(identity, request, result)
                session['chatReferenceVersion'] = old_revision + 1
                self.app.db.execute('RELEASE chat_reference_attach')
            except BaseException:
                self.app.db.execute('ROLLBACK TO chat_reference_attach')
                self.app.db.execute('RELEASE chat_reference_attach')
                if owns:
                    self.app.db.rollback()
                session['chatReferenceVersion'] = old_revision
                raise
            self.app._publish_changes(sessions={sid})
            return {'accepted': True, 'result': result, 'effects': []}

    def rows(self, sid, identities=None, *, limit=None):
        # No transcript reads here. Callers separately qualify canonical identity.
        query = """SELECT value FROM output_records WHERE session_id=?
            AND json_extract(value,'$.kind')='chat_reference'
            AND json_extract(value,'$.linked')=1
            AND COALESCE(json_extract(value,'$.referenceImported'),0)=0"""
        params = [sid]
        if identities is not None:
            names = sorted(set(identities))
            slots = ','.join('?' for _ in names)
            query += f""" AND (json_extract(value,'$.origin.messageId') IN ({slots})
                OR json_extract(value,'$.canonicalMessageId') IN ({slots}))"""
            params += names + names
        query += ' ORDER BY created,id'
        if limit is not None:
            query += ' LIMIT ?'
            params.append(limit)
        return [json.loads(row[0]) for row in self.app.db.execute(query, params)]

    async def describe(self, sid, mid, origin):
        async with self.app.lock:
            self.client_scope(sid, origin)
        proof = await self.identity(sid, mid, scoped=True)
        async with self.app.lock:
            self.client_scope(sid, origin)
            self.assistant(sid, mid, proof)
            rows = self.rows(sid, {mid, proof['fact'][0], *proof['referenceBindings']},
                             limit=MAX_REFERENCES + 1)
            # Retained references never migrate to a changed/replaced message.
            rows = [row for row in rows if source_matches(row, proof)]
            return {'accepted': True, 'result': {
                'items': [self.descriptor(row, mid) for row in rows[:MAX_REFERENCES]],
                'overflow': len(rows) > MAX_REFERENCES}, 'effects': []}

    async def preflight(self, args, origin):
        proof_args = args['chatReference']
        async with self.app.lock:
            self.client_scope(proof_args['sourceSessionId'], origin)
        return await self.identity(proof_args['sourceSessionId'],
                                   proof_args['messageId'], scoped=True)

    def guard(self, args, origin, proof):
        values = args['chatReference']
        sid, mid = values['sourceSessionId'], values['messageId']
        self.client_scope(sid, origin)
        self.assistant(sid, mid, proof)
        record = self.outputs.record(sid, values['id'])
        if (record.get('kind') != 'chat_reference' or not record.get('linked')
                or record.get('version') != values['version']
                or record.get('revision') != values['expectedRevision']
                or not source_matches(record, proof)):
            raise ValueError('This chat reference changed. Refresh the original reply.')
        target = self.retained_target(record)
        if args['id'] != target['id']:
            raise ValueError('The requested chat does not match this saved reference.')
        # The existing session.select owner performs dirty guards/navigation.
        # Do not prepare a runtime or mutate any client from this validator.

    async def export_references(self, sid, message_id=None):
        async with self.app.lock:
            records = self.rows(sid)
        result, proofs = [], {}
        for row in records:
            # Prefer the exact canonical identity when it was already qualified
            # at attachment. A retired display alias is not required to export
            # that unchanged canonical message. Provisional UUIDs never fall
            # back to text search or a guessed canonical position.
            mid = row.get('sourceIdentity', {}).get('canonicalMessageId') or row['origin']['messageId']
            if mid not in proofs:
                try:
                    proofs[mid] = await self.identity(sid, mid)
                except ValueError:
                    proofs[mid] = None
            proof = proofs[mid]
            if proof is None or not source_matches(row, proof):
                continue
            aliases = {mid, proof['fact'][0], *proof['referenceBindings']}
            if message_id is not None and message_id not in aliases:
                continue
            async with self.app.lock:
                self.assistant(sid, mid, proof)
                current = self.outputs.record(sid, row['id'])
                if current != row:
                    raise ValueError('A reference changed while preparing the export. Retry.')
                result.append({'messageIds': sorted(aliases),
                    'messageHashes': dict(proof['referenceBindings']),
                    'canonicalMessageId': proof['fact'][0] if proof.get('revision') is not None else None,
                    'sourceScope': {key: proof['snapshot'].get(key)
                                    for key in ('id', 'nativeIdentity', 'runtimeSessionId')},
                    'targetSessionId': row['target']['id'],
                    **{key: value for key, value in self.descriptor(row).items()
                       if key in {'title', 'workspace', 'available', 'archived'}}})
        return result
