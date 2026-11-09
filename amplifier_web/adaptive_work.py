"""Bounded arrival views over the canonical chat catalog; no runtime hydration."""

WORK_QUERY_FIELDS = ('workHomeScope', 'workHomeQuery', 'workHomePages',
                     'workSurface', 'workWorkspaceId', 'workWorkspaceQuery', 'workWorkspacePages')
STATUS_RESOURCES = {
    'workStatusAttention': 'attention', 'workStatusUnread': 'unread',
    'workStatusWorking': 'working', 'workStatusIdle': 'idle',
    'workStatusWaiting': 'waiting', 'workStatusPaused': 'paused',
}


def work_home_view(view):
    """Workspace Chats owns its query and pins the shared view to that workspace."""
    if view.get('workSurface') == 'workspace':
        return {'workHomeScope': view.get('workWorkspaceId') or '',
                'workHomeQuery': view.get('workWorkspaceQuery'),
                'workHomePages': view.get('workWorkspacePages')}
    return view


def work_home_pin(view, pinned_workspace_id=None):
    return (view.get('workWorkspaceId') or ''
            if view.get('workSurface') == 'workspace' else pinned_workspace_id)


def work_home_context(state, *, pinned_workspace_id=None):
    """Current project identity, independent of asynchronous result pages."""
    source_view = state.get('view', {})
    view = work_home_view(source_view)
    requested_scope = view.get('workHomeScope') or 'all'
    pin = work_home_pin(source_view, pinned_workspace_id)
    scope = pin if pin is not None else requested_scope
    workspace = next((row for row in state.get('workspaces', [])
                      if row['id'] == scope and row.get('available') is True
                      and isinstance(row.get('path'), str) and row['path']), None)
    # An unavailable project must never widen silently to all work.
    unavailable = scope not in ('all', 'managed') and workspace is None
    return {'scope': scope, 'requestedScope': requested_scope,
            'workspace': {key: workspace.get(key) for key in ('id', 'name', 'path')} if workspace else None,
            'unavailable': unavailable}


def status_page(service, state, context, status, *, query='', index=0):
    """One existing catalog page, shared by Home and the independent sidebar."""
    scope, workspace = context['scope'], context['workspace']
    filters = {'navChatScope': 'workspace' if scope not in ('all', 'managed') else 'all',
               'navLocationFilter': 'managed' if scope == 'managed' else 'all',
               'navFilter': query, 'navArchive': 'active', 'navSort': 'activity',
               'navShowAgentCreated': True, 'navStatusFilter': status}
    scoped = {**state, 'selectedWorkspaceId': workspace['id'] if workspace else None}
    page = service.projections.chats({**scoped, 'view': filters}, section='recent')
    if index:
        filters['navChatPage'] = {**page['scope'], 'index': index}
        page = service.projections.chats({**scoped, 'view': filters}, section='recent')
    return page


def work_status(service, state, status, *, pinned_workspace_id=None):
    # Home search/scope and Browse filters never enter this independent query.
    context = work_home_context({**state, 'view': {}}, pinned_workspace_id=pinned_workspace_id)
    index = (state.get('view', {}).get('navStatusPages') or {}).get(status, 0)
    return {**status_page(service, state, context, status, index=index),
            'workScope': context['scope'], 'unavailable': context['unavailable'],
            'requestedIndex': index}


def work_home(service, state, *, pinned_workspace_id=None):
    context = work_home_context(state, pinned_workspace_id=pinned_workspace_id)
    view = work_home_view(state.get('view', {}))
    query = view.get('workHomeQuery') or ''
    pages = {status: status_page(service, state, context, status, query=query,
                                index=(view.get('workHomePages') or {}).get(status, 0))
             for status in STATUS_RESOURCES.values()}
    return {**context,
            'query': query, 'requestedPages': view.get('workHomePages') or {},
            'groups': pages,
            'items': [row for page in pages.values() for row in page['items']]}
