"""Shared, bounded chat navigation over existing workspace/session summaries."""
from fnmatch import fnmatchcase
import math
import time

from .session_navigation import is_top_level

PAGE_SIZE = 100


def timestamp(value):
    return float(value) if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None


def recent_activity(session):
    """Migrate old views from conversation timestamps, never UI/lifecycle edits."""
    saved = timestamp(session.get('recentActivityAt'))
    if saved is not None:
        return saved
    messages = [timestamp(row.get('createdAt')) or 0 for row in session.get('messages', [])]
    native = session.get('nativeRevision')
    if isinstance(native, (list, tuple)) and native and timestamp(native[0]) is not None:
        messages.append(native[0] / 1e9)
    return max(messages + [timestamp(session.get('createdAt')) or 0])


def touch(session):
    session['recentActivityAt'] = max(recent_activity(session), time.time())


def runtime_activity(session, kind, payload):
    """Activity for sorting excludes lifecycle/setup and background naming."""
    active = False
    if kind == 'assistant.delta':
        active = bool(payload.get('text') or payload.get('delta'))
    elif kind == 'execution.event':
        active = bool(payload.get('id')) and payload.get('kind') in {'llm', 'tool', 'worker'} and payload.get('phase') != 'interrupted' and payload.get('label') != 'Session naming'
    elif kind == 'runtime.status':
        active = payload.get('status') == 'working' and (not payload.get('activityOnly') or session.get('status') in {'working', 'starting'})
    elif kind == 'runtime.generation':
        active = payload.get('event') in {'generation.started', 'generation.finished', 'generation.failed', 'generation.detached'}
    elif kind == 'worker.updated':
        active = payload.get('status') in {'running', 'working', 'queued', 'completed', 'error'} and payload.get('event') != 'job.recovered'
    elif kind == 'runtime.tool':
        active = payload.get('phase') in {'pre', 'post', 'error'}
    elif kind == 'runtime.error':
        active = session.get('status') == 'working'
    elif kind == 'approval.requested':
        active = True
    elif kind == 'approval.resolved':
        active = payload.get('decision') in {'allow', 'deny', 'approve', 'reject'}
    if active:
        touch(session)


def initialize(state):
    roots = {row['id'] for row in state.get('sessions', []) if is_top_level(row)}
    pins = state.get('pinnedSessionIds', [])
    state['pinnedSessionIds'] = list(dict.fromkeys(identity for identity in pins
        if isinstance(identity, str) and identity in roots)) if isinstance(pins, list) else []
    if state.setdefault('view', {}).get('navChatScope') not in ('workspace', 'all'):
        state['view']['navChatScope'] = 'workspace'
    for session in state.get('sessions', []):
        session['recentActivityAt'] = recent_activity(session)


def view_patch(patch):
    if 'navChatScope' in patch and patch['navChatScope'] not in ('workspace', 'all'):
        raise ValueError('navChatScope must be workspace or all.')
    if 'navFilter' in patch and (not isinstance(patch['navFilter'], str) or len(patch['navFilter']) > 500):
        raise ValueError('Chat search must be text of at most 500 characters.')
    if 'navChatPage' in patch:
        page = patch['navChatPage']
        if not isinstance(page, dict) or type(page.get('index')) is not int or not 0 <= page['index'] <= 1_000_000:
            raise ValueError('Chat page index must be a nonnegative integer.')
        if page.get('mode') not in ('workspace', 'all'):
            raise ValueError('Chat page scope must be workspace or all.')
        for key in ('workspaceId', 'selectedSessionId'):
            if page.get(key) is not None and (not isinstance(page[key], str) or len(page[key]) > 200):
                raise ValueError('Invalid chat page scope.')
        if not isinstance(page.get('filter'), str) or len(page['filter']) > 500:
            raise ValueError('Invalid chat page search.')
    return patch


def _matches(terms, query):
    query = query.strip().casefold()
    if any(character in query for character in '*?['):
        return any(fnmatchcase(str(term).casefold(), query) for term in terms)
    return any(query in str(term).casefold() for term in terms)


def snapshot(state):
    workspaces = [row for row in state.get('workspaces', [])
                  if row.get('available') is True and isinstance(row.get('path'), str) and row['path']]
    by_id = {row['id']: row for row in workspaces}
    by_path = {row['path']: row for row in workspaces}
    selected = by_id.get(state.get('selectedWorkspaceId'))
    view = state.get('view', {})
    mode = 'all' if view.get('navChatScope') == 'all' else 'workspace'
    query = view.get('navFilter', '')
    query = query if isinstance(query, str) else ''
    scope = {'mode': mode, 'workspaceId': selected['id'] if mode == 'workspace' and selected else None,
             'filter': query, 'selectedSessionId': state.get('selectedSessionId')}
    pins = set(state.get('pinnedSessionIds', []))
    rows = []
    for session in state.get('sessions', []):
        if not is_top_level(session):
            continue
        workspace = by_id.get(session.get('workspaceId')) or by_path.get(session.get('workspace'))
        if workspace is None or (mode == 'workspace' and (selected is None or workspace['id'] != selected['id'])):
            continue
        title = session.get('title') or 'Untitled conversation'
        description = session.get('description') or ''
        if not _matches((title, description, session['id'], workspace['path'], workspace.get('name', '')), query):
            continue
        rows.append({'id': session['id'], 'title': title, 'description': description,
                     'status': session.get('status', 'idle'), 'workspace': workspace['path'],
                     'workspaceId': workspace['id'], 'pinned': session['id'] in pins,
                     'recentActivityAt': recent_activity(session)})
    # Python's stable sort preserves source-array order for equal timestamps.
    rows.sort(key=lambda row: (not row['pinned'], -row['recentActivityAt']))
    saved = view.get('navChatPage')
    matched = isinstance(saved, dict) and all(saved.get(key) == value for key, value in scope.items())
    inferred = next((i // PAGE_SIZE for i, row in enumerate(rows) if row['id'] == scope['selectedSessionId']), 0) if mode == 'workspace' else 0
    requested = saved['index'] if matched and type(saved.get('index')) is int else inferred
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    index = max(0, min(pages - 1, requested))
    start, end = index * PAGE_SIZE, min(len(rows), (index + 1) * PAGE_SIZE)
    return {'items': rows[start:end], 'total': len(rows), 'index': index, 'pages': pages,
            'start': start, 'end': end, 'scope': scope}
