"""Persist Unified's presentation beside, never instead of, shared transcripts."""
import json
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
        if not session.get('$view'):
            continue
        path = view_path(home, session)
        # Fail visibly rather than silently replacing lost chat history.
        value = json.loads(path.read_text())
        if value.get('id') != session['id'] or not isinstance(value.get('messages'), list):
            raise ValueError('A saved conversation view is invalid; its files were preserved.')
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
    for session in state.get('sessions', []):
        path = view_path(home, session)
        text = json.dumps(session, ensure_ascii=False)
        if cache.get(str(path)) != text:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            SessionStore._atomic(path, text)
            cache[str(path)] = text
        result['sessions'].append({**{key: session[key] for key in ('id', 'workspace', 'runtimeSessionId') if key in session}, '$view': 1})
    return result
