"""Persist Unified's presentation beside, never instead of, shared transcripts."""
import json
import copy
import uuid
from pathlib import Path

from .host.storage import SessionStore


def view_path(home, session):
    store = SessionStore.for_app(home, session['workspace'])
    return store.directory(session.get('runtimeSessionId') or session['id']) / 'unified' / 'view.json'


def hydrate(home, state, db):
    from .cold_display import ColdRecord, MARKER
    canvas = state.get('canvas', {})
    reference = canvas.pop('$body', None)
    if reference and not canvas.get('contentResource'):
        from .canvas_library import restore_body
        canvas['contentResource'] = reference
        # Global legacy snapshots kept small document bodies inline. Preserve
        # that contract; explicitly compact large documents stay indirect.
        restore_body(canvas, db, inline_documents=True)
    for index, session in enumerate(state.get('sessions', [])):
        if session.pop('$native', False):
            session.update(workers=[], approvals=[], status='idle', historyLoaded=False, historyLoading=False)
            if 'messages' not in session.get(MARKER, {}):
                session['messages'] = []
            state['sessions'][index] = ColdRecord(session, db)
            continue
        if not session.get('$view'):
            state['sessions'][index] = ColdRecord(session, db)
            continue
        path = view_path(home, session)
        # Fail visibly rather than silently replacing lost chat history.
        if session.get('$viewPayload'):
            from .cold_display import load
            value = load(db, session['$viewPayload'])
        else:
            value = json.loads(path.read_text())
        if value.get('id') != session['id'] or not (isinstance(value.get('messages'), list)
                or isinstance(value.get(MARKER, {}).get('messages'), dict)):
            raise ValueError('A saved conversation view is invalid; its files were preserved.')
        if value.get('nativeProject'):
            # Reconcile older UI copies with the current display policy on open,
            # even when the canonical transcript has not changed since restart.
            value['historyLoaded'] = False
        state['sessions'][index] = ColdRecord(value, db)
    state['runtimeControl'] = {identity: ColdRecord(value, db)
                               for identity, value in state.get('runtimeControl', {}).items()}


def migrate(home, state):
    """Migrate legacy roots and children once, without overwriting shared files."""
    workspaces = {s.get('runtimeSessionId') or s['id']: s['workspace'] for s in state.get('sessions', [])}
    for path in (Path(home) / 'sessions').glob('*/checkpoint.json'):
        payload = json.loads(path.read_text())
        meta = payload.get('metadata', {})
        workspace = meta.get('working_dir') or meta.get('workspace') or workspaces.get(path.parent.name) or workspaces.get(meta.get('parent_id'))
        if workspace:
            SessionStore.for_app(home, workspace)._migrate(path.parent.name)


# Admission accounting must survive even when display observations are rebuilt
# from native events. Keep metadata in the existing accounting projection only;
# never persist action bodies or lazy event-file references here.
ACCOUNTING_FIELDS = {'id', 'revision', 'producerId', 'budgetRevision', 'admittedAt',
    'parentId', 'turnId', 'sessionId', 'rootSessionId', 'kind', 'phase', 'provider',
    'model', 'startedAt', 'endedAt', 'usage', 'lifecycle'}
ACCOUNTING_USAGE_FIELDS = {'inputTokens', 'outputTokens', 'totalTokens',
    'cacheReadTokens', 'cacheWriteTokens', 'reasoningTokens', 'grossInputTokens',
    'grossTotalTokens', 'costUsd', 'costType', 'costSource'}


def accounting_projection(tree):
    scalar_types = (str, int, float, bool, type(None))
    rows = [(row, False) for row in tree.get('retiredUsageNodes', [])]
    rows.extend((row, True) for row in tree.get('nodes', [])
                if row.get('liveObservation') and row.get('kind') in {'llm', 'worker'}
                and not row.get('nativeHistory'))
    saved = {}
    for row, display in rows:
        if row.get('kind') not in {'llm', 'worker'} or not row.get('id'):
            continue
        key = (row.get('sessionId'), row['id'])
        prior = saved.get(key)
        if display and row.get('canonicalHistory') and (
            prior is None or row.get('rootSessionId') != prior.get('rootSessionId')
            or row.get('producerId') != prior.get('producerId')
            or row.get('revision', 0) <= prior.get('revision', 0)
        ):
            # A display row may acquire a later live observation, but a lazy
            # canonical read cannot mint admission or replace saved accounting.
            continue
        if prior and (row.get('revision', 0), bool(row.get('endedAt'))) < (prior.get('revision', 0), bool(prior.get('endedAt'))):
            continue
        # Scalar fields are already immutable. Retain deep isolation for any
        # structured/custom value, but avoid copying every primitive in every
        # saved accounting row on each publication.
        record = {}
        for field, value in row.items():
            if field not in ACCOUNTING_FIELDS:
                continue
            if field == 'usage':
                detached = type(value) is not dict or any(
                    type(name) is not str or type(amount) not in scalar_types
                    for name, amount in value.items())
                usage = copy.deepcopy(value) if detached else value
                if isinstance(usage, dict):
                    # Plain usage is already scalar; custom keys/values retain
                    # deepcopy behavior before the existing exact-type filter.
                    record[field] = {name: amount for name, amount in usage.items()
                                   if name in ACCOUNTING_USAGE_FIELDS and type(amount) in (str, int, float)}
            else:
                record[field] = value if type(value) in scalar_types else copy.deepcopy(value)
        saved[key] = record
    return list(saved.values())


def persist(home, state, cache, *, session_ids=None, by_id=None, references=None, scoped_result=False,
            undo=None, db=None):
    """SQLite keeps only the session list; presentation files change on demand."""
    from .automatic_history import INDEX_FIELDS
    result = dict(state)
    canvas = state.get('canvas', {})
    artifact = next((a for a in state.get('canvasArtifacts', []) if a['id'] == canvas.get('id')), None)
    if artifact and artifact.get('body'):
        result['canvas'] = {key: value for key, value in canvas.items() if key not in {'content', 'surface'}}
        from .canvas_versions import definition
        result['canvas']['$body'] = definition(artifact, canvas.get('selectedVersion'))['body']
    result['sessions'] = []
    retained = set(state.get('pinnedSessionIds', [])) | {state.get('selectedSessionId')}
    library = state.get('conversationOrganization', {})
    retained.update(library.get('archived', {}))
    retained.update(sid for row in library.get('collections', []) for sid in row['sessionIds'])
    if references is not None:
        if session_ids is None:
            references.clear()
    sessions = (state.get('sessions', []) if session_ids is None else
                (by_id[identity] for identity in session_ids if identity in by_id))
    for session in sessions:
        from .cold_display import MARKER
        if session.get('historyManaged'):
            # Rebuild native catalog rows from their source. Persist only local
            # presentation overrides and startup selection/pin references.
            native_id = session.get('_catalogId') or uuid.uuid5(uuid.NAMESPACE_URL, f"amplifier-session:{session.get('nativeProject')}/{session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']}").hex
            revision = session.get('nativeRevision') or [0]
            baseline = session.get('_catalogRecentAt', revision[0] / 1e9)
            customized = (session['id'] in retained or session['id'] != native_id
                or session.get('titleSource') not in {None, 'native'}
                or bool(session.get('draftAttachments')) or bool(session.get('draft'))
                or bool(session.get('messageAnnotations')) or bool(session.get('messageQuotes'))
                or session.get('recentActivityAt', 0) > baseline
                or bool(dict.get(session, MARKER)))
            if customized:
                result['sessions'].append({**{key: session[key] for key in INDEX_FIELDS if key in session}, '$native': True,
                    **({MARKER: copy.deepcopy(dict.get(session, MARKER)),
                        '_coldMessageCount': session.get('_coldMessageCount', 0),
                        '_coldNotifications': copy.deepcopy(session.get('_coldNotifications', []))}
                       if dict.get(session, MARKER) else {})})
            continue
        path = view_path(home, session)
        # Native event activity is a lazy view, never another persisted event
        # or message cache. Preserve pre-existing legacy records, but never write
        # new live observations or event-log-derived action bodies here.
        from .cold_display import saved as saved_cold, MARKER
        value = {key: item for key, item in saved_cold(session).items()
                 if key not in {'historyActivity', 'questions'}}
        if 'execution' in value:
            value['execution'] = {**value['execution'],
                'retiredUsageNodes': accounting_projection(value['execution']),
                'nodes': [row for row in value['execution'].get('nodes', []) if not any(row.get(key) for key in ('nativeHistory', 'canonicalHistory', 'liveObservation'))],
                'turns': [row for row in value['execution'].get('turns', []) if not any(row.get(key) for key in ('nativeHistory', 'canonicalHistory'))]}
        text = json.dumps(value, ensure_ascii=False)
        prior = references.get(session['id'], {}) if references is not None else {}
        payload = prior.get('$viewPayload') if cache.get(str(path)) == text else None
        if payload is None and db is not None:
            from .resource_files import put
            # SQLite's committed pointer owns the exact immutable projection.
            # view.json remains a compatible presentation, never the authority
            # for a newer transaction that failed before its pointer committed.
            payload = put(db, value)
        if cache.get(str(path)) != text:
            if undo is not None:
                # Restore the pre-transaction presentation if SQLite rejects its
                # matching manifest. Canonical transcript/event files are never
                # part of this rollback list.
                undo.append((path, path.read_text() if path.exists() else None))
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            SessionStore._atomic(path, text)
            cache[str(path)] = text
        result['sessions'].append({**{key: session[key] for key in ('id', 'workspace', 'runtimeSessionId') if key in session}, '$view': 1,
                                   **({'$viewPayload': payload} if payload else {}),
                                   **({MARKER: copy.deepcopy(value[MARKER])} if value.get(MARKER) else {})})
    if references is not None:
        if session_ids is not None:
            retained_ids = {row['id'] for row in result['sessions']}
            for identity in session_ids - retained_ids:
                references.pop(identity, None)
        references.update((row['id'], row) for row in result['sessions'])
        if not scoped_result:
            result['sessions'] = list(references.values())
    return result
