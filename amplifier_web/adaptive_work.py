"""Bounded arrival views over the canonical chat catalog; no runtime hydration."""

WORK_QUERY_FIELDS = ('workHomeScope', 'workHomeQuery', 'workHomePages')


def work_home_context(state, *, pinned_workspace_id=None):
    """Current project identity, independent of asynchronous result pages."""
    view = state.get('view', {})
    requested_scope = view.get('workHomeScope') or 'all'
    scope = pinned_workspace_id or requested_scope
    workspace = next((row for row in state.get('workspaces', [])
                      if row['id'] == scope and row.get('available') is True
                      and isinstance(row.get('path'), str) and row['path']), None)
    # An unavailable project must never widen silently to all work.
    unavailable = scope not in ('all', 'managed') and workspace is None
    return {'scope': scope, 'requestedScope': requested_scope,
            'workspace': {key: workspace.get(key) for key in ('id', 'name', 'path')} if workspace else None,
            'unavailable': unavailable}


def work_home(service, state, *, pinned_workspace_id=None):
    context = work_home_context(state, pinned_workspace_id=pinned_workspace_id)
    scope, workspace = context['scope'], context['workspace']
    view = state.get('view', {})
    query = view.get('workHomeQuery') or ''
    base = {'navChatScope': 'workspace' if scope not in ('all', 'managed') else 'all',
            'navLocationFilter': 'managed' if scope == 'managed' else 'all',
            'navFilter': query, 'navArchive': 'active', 'navSort': 'activity',
            'navShowAgentCreated': True}
    scoped = {**state, 'selectedWorkspaceId': workspace['id'] if workspace else None}
    pages = {}
    for status in ('attention', 'unread', 'working', 'idle'):
        filters = {**base, 'navStatusFilter': status}
        page = service.projections.chats({**scoped, 'view': filters}, section='recent')
        index = (view.get('workHomePages') or {}).get(status, 0)
        if index:
            filters['navChatPage'] = {**page['scope'], 'index': index}
            page = service.projections.chats({**scoped, 'view': filters}, section='recent')
        pages[status] = page
    return {**context,
            'query': query, 'requestedPages': view.get('workHomePages') or {},
            'groups': pages,
            'items': [row for page in pages.values() for row in page['items']]}
