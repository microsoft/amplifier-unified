"""Persist Unified's presentation beside, never instead of, shared transcripts."""
import json
import uuid
from pathlib import Path

from .host.storage import SessionStore


def view_path(home, session):
    store = SessionStore.for_app(home, session['workspace'])
    return store.directory(session.get('runtimeSessionId') or session['id']) / 'unified' / 'view.json'


def hydrate(home, state, db):
    canvas = state.get('canvas', {})
    reference = canvas.pop('$body', None)
    if reference and not canvas.get('contentResource'):
        from .state_storage import resource
        canvas.update(resource(db, reference['$resource']))
    for index, session in enumerate(state.get('sessions', [])):
        if session.pop('$native', False):
            session.update(messages=[], workers=[], approvals=[], status='idle', historyLoaded=False, historyLoading=False)
            continue
        if not session.get('$view'):
            continue
        path = view_path(home, session)
        # Fail visibly rather than silently replacing lost chat history.
        value = json.loads(path.read_text())
        if value.get('id') != session['id'] or not isinstance(value.get('messages'), list):
            raise ValueError('A saved conversation view is invalid; its files were preserved.')
        if value.get('nativeProject'):
            # Reconcile older UI copies with the current display policy on open,
            # even when the canonical transcript has not changed since restart.
            value['historyLoaded'] = False
        state['sessions'][index] = value


def migrate(home, state):
    """Migrate legacy roots and children once, without overwriting shared files."""
    workspaces = {s.get('runtimeSessionId') or s['id']: s['workspace'] for s in state.get('sessions', [])}
    for path in (Path(home) / 'sessions').glob('*/checkpoint.json'):
        payload = json.loads(path.read_text())
        meta = payload.get('metadata', {})
        workspace = meta.get('working_dir') or meta.get('workspace') or workspaces.get(path.parent.name) or workspaces.get(meta.get('parent_id'))
        if workspace:
            SessionStore.for_app(home, workspace)._migrate(path.parent.name)


def persist(home, state, cache):
    """SQLite keeps only the session list; presentation files change on demand."""
    result = dict(state)
    canvas = state.get('canvas', {})
    artifact = next((a for a in state.get('canvasArtifacts', []) if a['id'] == canvas.get('id')), None)
    if artifact and artifact.get('body'):
        result['canvas'] = {key: value for key, value in canvas.items() if key not in {'content', 'surface'}}
        result['canvas']['$body'] = artifact['body']
    result['sessions'] = []
    retained = set(state.get('pinnedSessionIds', [])) | {state.get('selectedSessionId')}
    for session in state.get('sessions', []):
        if session.get('historyManaged'):
            from .automatic_history import INDEX_FIELDS
            # Rebuild native catalog rows from their source. Persist only local
            # presentation overrides and startup selection/pin references.
            native_id = session.get('_catalogId') or uuid.uuid5(uuid.NAMESPACE_URL, f"amplifier-session:{session.get('nativeProject')}/{session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']}").hex
            revision = session.get('nativeRevision') or [0]
            baseline = session.get('_catalogRecentAt', revision[0] / 1e9)
            customized = (session['id'] in retained or session['id'] != native_id
                or session.get('titleSource') not in {None, 'native'}
                or bool(session.get('draftAttachments'))
                or session.get('recentActivityAt', 0) > baseline)
            if customized:
                result['sessions'].append({**{key: session[key] for key in INDEX_FIELDS if key in session}, '$native': True})
            continue
        path = view_path(home, session)
        # Native event activity is a lazy view, never another persisted event
        # or message cache. Runtime-owned execution nodes retain their history.
        value = {key: item for key, item in session.items() if key != 'historyActivity'}
        if 'execution' in value:
            value['execution'] = {**value['execution'],
                'nodes': [row for row in value['execution'].get('nodes', []) if not row.get('nativeHistory')],
                'turns': [row for row in value['execution'].get('turns', []) if not row.get('nativeHistory')]}
        text = json.dumps(value, ensure_ascii=False)
        if cache.get(str(path)) != text:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            SessionStore._atomic(path, text)
            cache[str(path)] = text
        result['sessions'].append({**{key: session[key] for key in ('id', 'workspace', 'runtimeSessionId') if key in session}, '$view': 1})
    return result
