"""Shared, bounded chat navigation over existing workspace/session summaries."""
from fnmatch import fnmatchcase
import math
import time

from .session_navigation import is_top_level
from .navigation_summary import activity, path_labels

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
    workers = {row['id'] for row in state.get('sessions', []) if not is_top_level(row)}
    pins = state.get('pinnedSessionIds', [])
    state['pinnedSessionIds'] = list(dict.fromkeys(identity for identity in pins
        if isinstance(identity, str) and identity and identity not in workers)) if isinstance(pins, list) else []
    if state.setdefault('view', {}).get('navChatScope') not in ('workspace', 'all'):
        state['view']['navChatScope'] = 'workspace'
    for session in state.get('sessions', []):
        session['recentActivityAt'] = recent_activity(session)


def view_patch(patch):
    if 'navArchive' in patch and patch['navArchive'] not in ('active', 'archived', 'all'):
        raise ValueError('Choose active, archived or all conversations.')
    if 'navCollection' in patch and patch['navCollection'] is not None and (not isinstance(patch['navCollection'], str) or len(patch['navCollection']) > 200):
        raise ValueError('Choose a valid collection ID or null.')
    if 'subagentHistory' in patch:
        value = patch['subagentHistory']
        if not isinstance(value, dict) or set(value) - {'sessionId', 'filter', 'index'}:
            raise ValueError('Invalid worker history page.')
        if 'sessionId' in value and (not isinstance(value['sessionId'], str) or not 1 <= len(value['sessionId']) <= 200):
            raise ValueError('Choose a conversation for worker history.')
        if 'filter' in value and (not isinstance(value['filter'], str) or len(value['filter']) > 500):
            raise ValueError('Worker history search must be text of at most 500 characters.')
        if 'index' in value and (type(value['index']) is not int or not 0 <= value['index'] <= 1_000_000):
            raise ValueError('Worker history page index must be a nonnegative integer.')
    if 'navWorkspaceList' in patch and type(patch['navWorkspaceList']) is not bool:
        raise ValueError('navWorkspaceList must be a boolean.')
    if 'navStatusFilter' in patch and patch['navStatusFilter'] not in ('all', 'attention', 'working', 'unread'):
        raise ValueError('Choose all, attention, working, or unread conversations.')
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


def registry(state):
    """Shared workspace resolution and root grouping for navigation queries."""
    workspaces = [row for row in state.get('workspaces', [])
                  if row.get('available') is True and isinstance(row.get('path'), str) and row['path']]
    by_id = {row['id']: row for row in workspaces}
    by_path = {row['path']: row for row in workspaces}
    labels = path_labels(by_path)
    roots, grouped = [], {}
    for row in state.get('sessions', []):
        if is_top_level(row):
            roots.append(row)
            workspace = by_id.get(row.get('workspaceId')) or by_path.get(row.get('workspace'))
            if workspace is not None:
                grouped.setdefault(workspace['id'], []).append(row)
    return by_id, by_path, labels, roots, grouped


def catalog(state, *, indexed=None):
    """Filter/sort once; selected chat and pagination belong to each client."""
    by_id, by_path, labels, roots, grouped = registry(state) if indexed is None else indexed
    selected = by_id.get(state.get('selectedWorkspaceId'))
    view = state.get('view', {})
    mode = 'all' if view.get('navChatScope') == 'all' else 'workspace'
    query = view.get('navFilter', '')
    query = query if isinstance(query, str) else ''
    scope = {'mode': mode, 'workspaceId': selected['id'] if mode == 'workspace' and selected else None,
             'filter': query, 'selectedSessionId': state.get('selectedSessionId')}
    status_filter = view.get('navStatusFilter', 'all')
    if status_filter != 'all':
        scope['statusFilter'] = status_filter
    counts = dict.fromkeys(('attention', 'working', 'unread', 'idle'), 0)
    unread = state.get('attention', {}).get('sessions', {})
    pins = set(state.get('pinnedSessionIds', []))
    pin_order = {sid: i for i, sid in enumerate(state.get('pinnedSessionIds', []))}
    organization = state.get('conversationOrganization', {})
    archived = organization.get('archived', {})
    archive_filter = view.get('navArchive', 'active')
    if archive_filter != 'active':
        scope['archive'] = archive_filter
    collection_id = view.get('navCollection')
    collection = next((row for row in organization.get('collections', []) if row['id'] == collection_id), None)
    collection_order = {sid: i for i, sid in enumerate(collection['sessionIds'])} if collection else {}
    if collection_id:
        scope['collectionId'] = collection_id
    memberships = {sid: row['id'] for row in organization.get('collections', []) for sid in row['sessionIds']}
    rows = []
    for session in roots if mode == 'all' else grouped.get(selected['id'], []) if selected else []:
        is_archived = session['id'] in archived
        if (archive_filter == 'active' and is_archived) or (archive_filter == 'archived' and not is_archived):
            continue
        if collection_id and session['id'] not in collection_order:
            continue
        workspace = by_id.get(session.get('workspaceId')) or by_path.get(session.get('workspace'))
        if workspace is None or (mode == 'workspace' and (selected is None or workspace['id'] != selected['id'])):
            continue
        title = session.get('title') or 'Untitled conversation'
        description = session.get('description') or ''
        shared_id = session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']
        if not _matches((title, description, session['id'], shared_id, workspace['path'], workspace.get('name', '')), query):
            continue
        summary = activity(session, bool(unread.get(session['id'])))
        counts[summary['kind']] += 1
        if status_filter != 'all' and summary['kind'] != status_filter:
            continue
        rows.append({'id': session['id'], 'title': title, 'description': description,
                     'status': session.get('status', 'idle'), 'workspace': workspace['path'],
                     'workspaceId': workspace['id'], 'workspaceName': workspace.get('name', ''),
                     'workspaceLabel': labels[workspace['path']], 'activity': summary,
                     'runtimeSessionId': session.get('runtimeSessionId') or session.get('nativeIdentity'),
                     'createdAt': timestamp(session.get('createdAt')), 'pinned': session['id'] in pins,
                     **({'archived': True} if is_archived else {}),
                     **({'collectionId': memberships[session['id']]} if session['id'] in memberships else {}),
                     'recentActivityAt': recent_activity(session)})
    # Python's stable sort preserves source-array order for equal timestamps.
    rows.sort(key=lambda row: (not row['pinned'],
        pin_order.get(row['id'], 0) if row['pinned'] and state.get('pinOrderCustomized')
        else collection_order.get(row['id'], 0) if collection_id and not row['pinned'] else -row['recentActivityAt']))
    return rows, scope, counts


def snapshot(state, *, indexed=None):
    rows, scope, counts = catalog(state) if indexed is None else indexed
    scope = {**scope, 'selectedSessionId': state.get('selectedSessionId')}
    mode = scope['mode']
    view = state.get('view', {})
    saved = view.get('navChatPage')
    matched = isinstance(saved, dict) and all(saved.get(key) == value for key, value in scope.items())
    inferred = next((i // PAGE_SIZE for i, row in enumerate(rows) if row['id'] == scope['selectedSessionId']), 0) if mode == 'workspace' else 0
    requested = saved['index'] if matched and type(saved.get('index')) is int else inferred
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    index = max(0, min(pages - 1, requested))
    start, end = index * PAGE_SIZE, min(len(rows), (index + 1) * PAGE_SIZE)
    return {'items': rows[start:end], 'total': len(rows), 'index': index, 'pages': pages,
            'start': start, 'end': end, 'scope': scope, 'activityCounts': counts}
