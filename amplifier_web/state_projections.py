"""Read-only derived indexes shared within one saved state generation.

AppService clears this cache on every save, including saves without a revision
change. Client selection/filter keys are explicit; drafts never enter shared
projections. The full agent state path remains an uncached read of live state.
"""
import hashlib
import json


class StateProjections:
    def __init__(self):
        self.values = {}

    def get(self, key, build):
        if key not in self.values:
            # A long-lived revision can still receive many different queries.
            if len(self.values) >= 256:
                self.values.clear()
            self.values[key] = build()
        return self.values[key]

    def attention(self, state):
        from .attention import snapshot
        return self.get(('attention',), lambda: snapshot(state))

    @staticmethod
    def view_scope(state, keys):
        view = state.get('view', {})
        return json.dumps({key: view[key] for key in keys if key in view}, sort_keys=True)

    @classmethod
    def chat_scope(cls, state):
        return (state.get('selectedSessionId'), state.get('selectedWorkspaceId'),
                cls.view_scope(state, ('navChatScope', 'navFilter', 'navStatusFilter', 'navLocationFilter',
                                      'navArchive', 'navCollection', 'navChatPage')))

    @classmethod
    def workspace_scope(cls, state):
        return (state.get('selectedWorkspaceId'),
                cls.view_scope(state, ('navWorkspaceBrowseFor', 'navWorkspacePath',
                                      'navWorkspaceFilter', 'navWorkspacePage', 'navWorkspaceMode')))

    def workspaces(self, state):
        from .workspace_navigation import _index, snapshot
        index = self.get(('workspace-index',), lambda: _index(state))
        return self.get(('workspaces', *self.workspace_scope(state)), lambda: snapshot(state, index=index))

    def chats(self, state):
        from .chat_navigation import catalog, registry, snapshot
        view = state.get('view', {})
        workspace = None if view.get('navChatScope') == 'all' else state.get('selectedWorkspaceId')
        filters = {key: view.get(key) for key in ('navChatScope', 'navFilter', 'navStatusFilter', 'navLocationFilter', 'navArchive', 'navCollection')}
        registrations = self.get(('chat-registry',), lambda: registry(state))
        index = self.get(('chat-index', workspace, json.dumps(filters, sort_keys=True)), lambda: catalog(state, indexed=registrations))
        return self.get(('chats', *self.chat_scope(state)), lambda: snapshot(state, indexed=index))

    def browser(self, state):
        from .browser_state import navigation
        attention = self.attention(state)
        scoped = {**state, 'attention': attention}
        # Layout/drafts do not affect navigation. Workspace controls belong to
        # their own explorer query, while worker history only affects this one.
        key = ('browser-navigation', *self.chat_scope(state), self.view_scope(state, ('subagentHistory',)))
        navigation_state = self.get(key, lambda: navigation(scoped, chats=self.chats))
        return {**navigation_state, 'attention': attention, 'workspaceExplorer': self.workspaces(scoped)}

    def shell_key(self, state):
        """Invalidate shell queries for their data, not unrelated host telemetry.

        Include the whole navigation catalog: a change outside the browser's
        current page can affect a pinned or independently filtered shell module.
        Selection, canvas and selected-chat summaries are added by the client.
        """
        def build():
            from .chat_navigation import recent_activity
            from .navigation_summary import activity
            from .session_navigation import is_top_level
            attention = self.attention(state)
            fields = ('id', 'title', 'description', 'status', 'workspace', 'workspaceId', 'location',
                      'runtimeSessionId', 'nativeIdentity', 'createdAt')
            rows = [([row.get(key) for key in fields], recent_activity(row),
                     activity(row, bool(attention['sessions'].get(row['id']))))
                    for row in state.get('sessions', []) if is_top_level(row)]
            facts = [rows, state.get('workspaces', []), state.get('pinnedSessionIds'),
                     state.get('pinOrderCustomized'), state.get('conversationOrganization'),
                     {key: attention.get(key) for key in ('total', 'unread', 'sections', 'sessions')},
                     {key: state.get('sharedHistory', {}).get(key) for key in ('loading', 'refreshing', 'error')},
                     state.get('locationListing'), state.get('actionStatus', {}).get('locations.list'), state.get('actionStatus', {}).get('locations.create')]
            return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
        return self.get(('shell-data-key',), build)
