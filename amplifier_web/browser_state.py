"""Bounded browser projections over the complete in-memory history catalog.

The native catalog is an index, not browser state. Actions and JSON Pointer
reads still address the complete catalog; browsing only publishes one page.
"""
from copy import deepcopy
from collections import OrderedDict
from .browser_detail import project
from .chat_navigation import snapshot as chat_snapshot, _matches, recent_activity
from .session_navigation import is_top_level

SUMMARY_FIELDS = ('location', 'id', 'title', 'titleSource', 'nativeNameSource', 'autoName', 'naming', 'configurationBusy', 'description', 'status', 'workspace', 'workingDirectory', 'executionRevision',
    'workspaceId', 'workspaceAvailable', 'bundle', 'createdAt', 'recentActivityAt', 'navigationActivityAt',
    'sessionKind', 'sessionPurpose', 'parentId', 'nativeParentId', 'nativeIdentity', 'nativeProject',
    'runtimeSessionId', 'historyManaged', 'historyLoaded', 'historyReadOnlyReason',
    'sharedHistoryTotal', 'turnCount', 'unreadCompletion', 'creationCommandId')
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


class SessionIndex:
    """Shared catalog facts for one saved generation; no client drafts or views."""
    def __init__(self, state):
        self.by_id = {}
        self.positions = {}
        self.parents = {}
        self.native_parents = {}
        self.active = set()
        self.notifications = []
        self.first_root = None
        for position, row in enumerate(state.get('sessions', [])):
            identity = row['id']
            self.by_id[identity] = row
            self.positions[identity] = position
            if row.get('status') in ACTIVE or row.get('historyLoading') or row.get('configurationBusy'):
                self.active.add(identity)
            if is_top_level(row):
                if self.first_root is None:
                    self.first_root = row
            else:
                self.parents.setdefault(row.get('parentId'), set()).add(identity)
                self.native_parents.setdefault(row.get('nativeParentId'), set()).add(identity)
            if row.get('messages') and row.get('sessionKind') != 'internal':
                self.notifications.extend(
                    {**{key: message[key] for key in ('id', 'role', 'via', 'createdAt') if key in message},
                     'sessionId': identity, 'text': message.get('text', '')[:500]}
                    for message in row.get('messages', [])
                    if message.get('role') == 'assistant' and message.get('via') == 'text')
        self.notifications.extend(state.get('scheduleNotifications', []))
        self.notifications.sort(key=lambda row: row.get('createdAt', 0))
        self.notifications = self.notifications[-100:]

    def children(self, parent):
        if not parent:
            return []
        candidates = set(self.parents.get(parent.get('id'), ()))
        for alias in (parent.get('id'), parent.get('nativeIdentity'), parent.get('runtimeSessionId')):
            if alias is not None:
                candidates.update(self.native_parents.get(alias, ()))
        return [self.by_id[key] for key in sorted(candidates, key=self.positions.__getitem__)
                if direct_child(self.by_id[key], parent)]


def navigation(state, *, chats=chat_snapshot, index=None):
    index = index or SessionIndex(state)
    chat = chats(state)
    header_view = {**state.get('view', {}), 'navChatScope': 'workspace', 'navFilter': '', 'navArchive': 'active', 'navCollection': None}
    scope = {'mode': 'workspace', 'workspaceId': state.get('selectedWorkspaceId'),
             'filter': '', 'selectedSessionId': state.get('selectedSessionId')}
    header_view['navChatPage'] = {**scope, 'index': 0}
    header = chats({**state, 'view': header_view})
    selected = index.by_id.get(state.get('selectedSessionId'))
    if selected and is_top_level(selected) and selected.get('workspaceId') == scope['workspaceId'] and all(row['id'] != selected['id'] for row in header['items']):
        header = {**header, 'items': header['items'][:99] + [{**summary(selected), 'pinned': selected['id'] in state.get('pinnedSessionIds', [])}]}
    requested = state.get('view', {}).get('subagentHistory', {})
    requested = requested if isinstance(requested, dict) else {}
    parent = requested.get('sessionId') or state.get('selectedSessionId')
    query = requested.get('filter', '')
    query = query if isinstance(query, str) else ''
    parent_row = index.by_id.get(parent, {})
    all_children = index.children(parent_row)
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


class SnapshotCopies:
    """Reuse detached, read-only sections, never references into mutable state.

    Keep one previous frame per recent browser, not a history of revisions. A
    full value comparison detects in-place edits and saves without a revision
    change. Independent browsers never share this cache entry.
    """
    def __init__(self, limit=8):
        self.limit = limit
        self.frames = OrderedDict()

    def detach(self, value, identity):
        previous = self.frames.pop(identity, {})
        result = {key: previous[key] if key in previous and previous[key] == item
                  else deepcopy(item) for key, item in value.items()}
        # Callers add client-specific top-level fields after detaching. Keep a
        # separate dictionary so those overlays cannot change this baseline.
        self.frames[identity] = dict(result)
        while len(self.frames) > self.limit:
            self.frames.popitem(last=False)
        return result


def snapshot(state, derived, *, session_id=None, index=None, copies=None, client_id=None):
    index = index or SessionIndex(state)
    result = dict(state)
    result.update(derived)
    visible = {row['id'] for name in ('chatNavigation', 'headerChatNavigation', 'subagentNavigation')
               for row in derived[name]['items']}
    selected = state.get('selectedSessionId')
    selected_row = index.by_id.get(selected, {})
    visible.add(selected_row.get('parentId'))
    full = {selected, session_id, state.get('voice', {}).get('sessionId')}
    full.update(index.active)
    visible.update(full)
    visible.add(derived['subagentNavigation']['scope']['sessionId'])
    workers = derived['subagentNavigation']
    selected_children = (workers['unfilteredTotal'] if workers['scope']['sessionId'] == selected
                         else len(index.children(selected_row))) if selected_row else 0
    result['sessions'] = [{**((row if row['id']==session_id else project(row)) if row['id'] in full else summary(row)),
                           **({'subagentCount': selected_children} if row['id'] == selected else {})}
                          for row in (index.by_id[key] for key in sorted(visible & index.by_id.keys(), key=index.positions.__getitem__))]
    from .conversation_library import projection as organization_projection
    result['conversationOrganization'] = organization_projection(state, visible)
    # Report history is read through bounded coordination cursors, never copied
    # into every browser progress snapshot. The worker's latest report remains.
    result['sessions'] = [{**row, 'workers': [{key: value for key, value in worker.items() if key != 'reportReceipts'}
                           for worker in row.get('workers', [])]} for row in result['sessions']]
    workspace_ids = {row.get('workspaceId') for row in result['sessions']} | {
        state.get('selectedWorkspaceId'), state.get('view', {}).get('workWorkspaceId')}
    explorer = derived.get('workspaceExplorer', {})
    workspace_ids.update(row.get('workspaceId') for row in explorer.get('rows', []))
    result['workspaces'] = [row for row in state.get('workspaces', []) if row['id'] in workspace_ids]
    result['runtimeControl'] = {key: value for key, value in state.get('runtimeControl', {}).items() if key in full}
    result['canvasArtifacts'] = [row for row in state.get('canvasArtifacts', [])
                                 if row.get('sessionId') == selected or row.get('id') == state.get('canvas', {}).get('id')]
    result['devices'] = {key: {field: row[field] for field in ('clientId', 'updatedAt', 'online', 'viewport', 'capabilities') if field in row}
                         for key, row in state.get('devices', {}).items()}
    result['notificationMessages'] = [{key: value for key, value in row.items()
                                      if key != 'text' or state.get('notificationSettings', {}).get('preview', True)}
                                     for row in index.notifications]
    selected_root = selected_row if selected_row and is_top_level(selected_row) else None
    continuation = selected_root or index.first_root
    result['library'] = {'sessionCount': sum(is_top_level(row) for row in state.get('sessions', [])), 'workspaceCount': len(state.get('workspaces', [])),
                         'continueSessionId': continuation['id'] if continuation else None,
                         'bounded': True, 'detailPath': '/api/state/detail'}
    for key in ('attentionRead', 'nativePresentation', 'conversationExports'):
        result.pop(key, None)
    return copies.detach(result, client_id) if copies is not None else deepcopy(result)
