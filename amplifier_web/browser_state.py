"""Bounded browser projections over the complete in-memory history catalog.

The native catalog is an index, not browser state. Actions and JSON Pointer
reads still address the complete catalog; browsing only publishes one page.
"""
from copy import deepcopy
from .browser_detail import project
from .chat_navigation import snapshot as chat_snapshot, _matches, recent_activity
from .session_navigation import is_top_level

SUMMARY_FIELDS = ('id', 'title', 'titleSource', 'description', 'status', 'workspace',
    'workspaceId', 'workspaceAvailable', 'bundle', 'createdAt', 'recentActivityAt',
    'sessionKind', 'parentId', 'nativeParentId', 'nativeIdentity', 'nativeProject',
    'runtimeSessionId', 'historyManaged', 'historyLoaded', 'historyReadOnlyReason',
    'sharedHistoryTotal', 'turnCount', 'unreadCompletion')
ACTIVE = {'starting', 'working', 'running', 'stopping'}


def summary(session):
    return {**{key: session[key] for key in SUMMARY_FIELDS if key in session},
            'messages': [], 'workers': [], 'approvals': []}


def direct_child(row, parent):
    if not parent or row.get('id') == parent.get('id') or is_top_level(row):
        return False
    if row.get('parentId') == parent.get('id'):
        return True
    aliases = {parent.get(key) for key in ('id', 'nativeIdentity', 'runtimeSessionId')} - {None}
    if row.get('nativeParentId') not in aliases:
        return False
    return (row.get('nativeProject') == parent.get('nativeProject') if row.get('nativeProject') and parent.get('nativeProject')
            else row.get('workspace') == parent.get('workspace'))


def navigation(state):
    chat = chat_snapshot(state)
    header_view = {**state.get('view', {}), 'navChatScope': 'workspace', 'navFilter': '', 'navArchive': 'active', 'navCollection': None}
    scope = {'mode': 'workspace', 'workspaceId': state.get('selectedWorkspaceId'),
             'filter': '', 'selectedSessionId': state.get('selectedSessionId')}
    header_view['navChatPage'] = {**scope, 'index': 0}
    header = chat_snapshot({**state, 'view': header_view})
    selected = next((row for row in state.get('sessions', []) if row['id'] == state.get('selectedSessionId')), None)
    if selected and is_top_level(selected) and selected.get('workspaceId') == scope['workspaceId'] and all(row['id'] != selected['id'] for row in header['items']):
        header['items'] = header['items'][:99] + [{**summary(selected), 'pinned': selected['id'] in state.get('pinnedSessionIds', [])}]
    requested = state.get('view', {}).get('subagentHistory', {})
    requested = requested if isinstance(requested, dict) else {}
    parent = requested.get('sessionId') or state.get('selectedSessionId')
    query = requested.get('filter', '')
    query = query if isinstance(query, str) else ''
    parent_row = next((row for row in state.get('sessions', []) if row['id'] == parent), {})
    all_children = [row for row in state.get('sessions', []) if direct_child(row, parent_row)]
    children = [row for row in all_children if _matches((row.get('title', ''), row.get('description', ''), row['id'], row.get('nativeIdentity', '')), query)]
    children.sort(key=lambda row: -recent_activity(row))
    pages = max(1, (len(children) + 49) // 50)
    index = requested.get('index', 0)
    index = max(0, min(pages - 1, index)) if type(index) is int else 0
    start, end = index * 50, min(len(children), (index + 1) * 50)
    workers = {'items': [summary(row) for row in children[start:end]], 'total': len(children), 'unfilteredTotal': len(all_children),
               'index': index, 'pages': pages, 'start': start, 'end': end,
               'scope': {'sessionId': parent, 'filter': query}}
    return {'chatNavigation': chat, 'headerChatNavigation': {'items': header['items'], 'total': header['total']},
            'subagentNavigation': workers}


def snapshot(state, derived, *, session_id=None):
    result = dict(state)
    result.update(derived)
    visible = {row['id'] for name in ('chatNavigation', 'headerChatNavigation', 'subagentNavigation')
               for row in derived[name]['items']}
    selected = state.get('selectedSessionId')
    selected_row = next((row for row in state.get('sessions', []) if row['id'] == selected), {})
    visible.add(selected_row.get('parentId'))
    full = {selected, session_id, state.get('voice', {}).get('sessionId')}
    for row in state.get('sessions', []):
        if row.get('status') in ACTIVE or row.get('historyLoading') or row.get('configurationBusy'):
            full.add(row['id'])
    visible.update(full)
    visible.add(derived['subagentNavigation']['scope']['sessionId'])
    result['sessions'] = [{**((row if row['id']==session_id else project(row)) if row['id'] in full else summary(row)),
                           **({'subagentCount': sum(direct_child(child, row) for child in state.get('sessions', []))} if row['id'] == selected else {})}
                          for row in state.get('sessions', []) if row['id'] in visible]
    from .conversation_library import projection as organization_projection
    result['conversationOrganization'] = organization_projection(state, visible)
    workspace_ids = {row.get('workspaceId') for row in result['sessions']} | {state.get('selectedWorkspaceId')}
    explorer = derived.get('workspaceExplorer', {})
    workspace_ids.update(row.get('workspaceId') for row in explorer.get('rows', []))
    result['workspaces'] = [row for row in state.get('workspaces', []) if row['id'] in workspace_ids]
    result['runtimeControl'] = {key: value for key, value in state.get('runtimeControl', {}).items() if key in full}
    result['canvasArtifacts'] = [row for row in state.get('canvasArtifacts', [])
                                 if row.get('sessionId') == selected or row.get('id') == state.get('canvas', {}).get('id')]
    result['devices'] = {key: {field: row[field] for field in ('clientId', 'updatedAt', 'online', 'viewport', 'capabilities') if field in row}
                         for key, row in state.get('devices', {}).items()}
    notifications = [{**{key: message[key] for key in ('id', 'role', 'via', 'createdAt') if key in message},
                      'sessionId': row['id'],
                      **({'text': message.get('text', '')[:500]} if state.get('notificationSettings', {}).get('preview', True) else {})}
                     for row in state.get('sessions', []) for message in row.get('messages', [])
                     if message.get('role') == 'assistant' and message.get('via') == 'text']
    notifications.sort(key=lambda row: row.get('createdAt', 0))
    result['notificationMessages'] = notifications[-100:]
    roots = (row for row in state.get('sessions', []) if is_top_level(row))
    first_root = next(roots, None)
    selected_root = next((row for row in state.get('sessions', []) if row['id'] == selected and is_top_level(row)), None)
    continuation = selected_root or first_root
    result['library'] = {'sessionCount': len(state.get('sessions', [])), 'workspaceCount': len(state.get('workspaces', [])),
                         'continueSessionId': continuation['id'] if continuation else None,
                         'bounded': True, 'detailPath': '/api/state/detail'}
    for key in ('attentionRead', 'nativePresentation', 'conversationExports'):
        result.pop(key, None)
    return deepcopy(result)
