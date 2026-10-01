"""Read-only derived indexes shared within one saved state generation.

AppService invalidates live facts on every save, including saves without a
revision change. Navigation indexes survive only when their complete semantic
key is unchanged. Client query keys are explicit; drafts never enter shared
projections. The full agent state path remains an uncached read of live state.
"""
import hashlib
import json
from collections import OrderedDict
from copy import deepcopy


class StateProjections:
    def __init__(self):
        self.values = {}
        self.previous_navigation = None
        self.detail_bodies = OrderedDict()

    def invalidate(self, *, state=None, session_ids=None, detail_only=False):
        if session_ids is None:
            self.detail_bodies.clear()
        else:
            for identity in session_ids:
                self.detail_bodies.pop(identity, None)
        if detail_only and session_ids is not None:
            index = self.values.get(('session-index',))
            if index is not None and index.patch(state, session_ids):
                parents = {index.by_id[key].get('parentId') for key in session_ids
                           if key in index.by_id and not index._membership[key][2]} - {None}
                for key in list(self.values):
                    if key[0] == 'browser-navigation' and (key[1] in parents
                            or any(parent in str(key[-1]) for parent in parents)):
                        self.values.pop(key, None)
                # Streaming text does not alter navigation, attention or shell
                # evidence. Keep those immutable facts; detail is built anew.
                return
        # Keep only navigation results, not active sessions, notifications, or
        # worker pages. Those must observe each saved generation independently.
        if self.previous_navigation is None:
            retained = {key: value for key, value in self.values.items()
                        if key[0] in {'workspace-index', 'workspaces', 'chat-registry', 'chat-index', 'chats'}}
            self.previous_navigation = (self.values.get(('shell-data-key',)), retained)
        index = self.values.get(('session-index',))
        self.values = {}
        if index is not None and session_ids is not None and index.patch(state, session_ids):
            self.values[('session-index',)] = index

    def refresh_navigation(self, state):
        if self.previous_navigation is not None:
            previous, retained = self.previous_navigation
            self.previous_navigation = None
            # The key covers all roots, including off-page errors, permissions,
            # organization, workspace metadata, and stable navigation recency.
            if previous is not None and self.shell_key(state) == previous:
                self.values.update(retained)

    def get(self, key, build):
        if key not in self.values:
            # A long-lived revision can still receive many different queries.
            if len(self.values) >= 256:
                self.values.clear()
            self.values[key] = build()
        return self.values[key]

    def sessions(self, state):
        from .browser_state import SessionIndex
        return self.get(('session-index',), lambda: SessionIndex(state))

    def detail(self, row):
        """Reuse only unchanged bounded bodies; current scalar facts stay live.

        A scoped commit evicts its identities; an unknown save evicts all.
        No client draft/selection is retained in this shared body cache.
        """
        fields = ('messages', 'messageWindow', 'sharedHistoryUserTurnOffset',
                  'execution', 'executionWindow', 'workers', 'generations', 'historyActivity')
        body = self.detail_bodies.pop(row['id'], None)
        if body is None:
            from .browser_state import project
            projected = project(row)
            body = deepcopy({key: projected[key] for key in fields if key in projected})
        self.detail_bodies[row['id']] = body
        while len(self.detail_bodies) > 32:
            self.detail_bodies.popitem(last=False)
        result = {key: value for key, value in row.items()
                  if key not in fields and key != 'messageQuotes'}
        result.update(body)
        return result

    def attention(self, state):
        from .attention import snapshot
        index = self.sessions(state)
        return self.get(('attention',), lambda: snapshot({
            **state, 'sessions': [index.by_id[key] for key in index.attention_ids]}))

    @staticmethod
    def view_scope(state, keys):
        view = state.get('view', {})
        return json.dumps({key: view[key] for key in keys if key in view}, sort_keys=True)

    @classmethod
    def chat_scope(cls, state):
        return (state.get('selectedSessionId'), state.get('selectedWorkspaceId'),
                cls.view_scope(state, ('navChatScope', 'navSort', 'navFilter', 'navStatusFilter', 'navLocationFilter',
                                      'navArchive', 'navCollection', 'navChatPage', 'navPinnedPage')))

    @classmethod
    def workspace_scope(cls, state):
        return (state.get('selectedWorkspaceId'),
                cls.view_scope(state, ('navWorkspaceBrowseFor', 'navWorkspacePath',
                                      'navWorkspaceFilter', 'navWorkspacePage', 'navWorkspaceMode')))

    def workspaces(self, state):
        self.refresh_navigation(state)
        from .workspace_navigation import _index, snapshot
        index = self.get(('workspace-index',), lambda: _index(state))
        return self.get(('workspaces', *self.workspace_scope(state)), lambda: snapshot(state, index=index))

    def chats(self, state, *, section=None):
        self.refresh_navigation(state)
        from .chat_navigation import catalog, registry, snapshot
        view = state.get('view', {})
        workspace = None if view.get('navChatScope') == 'all' else state.get('selectedWorkspaceId')
        filters = {key: view.get(key) for key in ('navChatScope', 'navSort', 'navFilter', 'navStatusFilter', 'navLocationFilter', 'navArchive', 'navCollection')}
        if section:
            filters['navStatusFilter'] = 'all'
        catalog_state = {**state, 'view': {**view, 'navStatusFilter': 'all'}} if section else state
        registrations = self.get(('chat-registry',), lambda: registry(state))
        index = self.get(('chat-index', workspace, json.dumps(filters, sort_keys=True)), lambda: catalog(catalog_state, indexed=registrations))
        return self.get(('chats', section, *self.chat_scope(state)), lambda: snapshot(state, indexed=index, section=section))

    def browser(self, state):
        from .browser_state import navigation
        attention = self.attention(state)
        scoped = {**state, 'attention': attention}
        # Layout/drafts do not affect navigation. Workspace controls belong to
        # their own explorer query, while worker history only affects this one.
        key = ('browser-navigation', *self.chat_scope(state), self.view_scope(state, ('subagentHistory',)))
        navigation_state = self.get(key, lambda: navigation(scoped, chats=self.chats, index=self.sessions(state)))
        return {**navigation_state, 'attention': attention, 'workspaceExplorer': self.workspaces(scoped)}

    def shell_key(self, state):
        """Invalidate shell queries for their data, not unrelated host telemetry.

        Include the whole navigation catalog: a change outside the browser's
        current page can affect a pinned or independently filtered shell module.
        Selection, canvas and selected-chat summaries are added by the client.
        """
        def build():
            from .attention import reviewed_session_errors
            from .chat_navigation import navigation_activity
            from .navigation_summary import activity, task_blocked
            attention = self.attention(state)
            reviewed_errors = reviewed_session_errors(attention)
            fields = ('id', 'title', 'description', 'status', 'workspace', 'workspaceId', 'location',
                      'titleSource', 'nativeNameSource', 'autoName', 'naming', 'configurationBusy',
                      'runtimeSessionId', 'nativeIdentity', 'createdAt')
            rows = [([row.get(key) for key in fields], navigation_activity(row),
                     activity(row, bool(attention['sessions'].get(row['id'])),
                              error_reviewed=row['id'] in reviewed_errors,
                              blocked=task_blocked(state, row['id'])))
                    for row in self.sessions(state).roots]
            facts = [rows, state.get('settings', {}).get('workspaces'), state.get('workspaceDefaults'), state.get('workspaces', []), state.get('pinnedSessionIds'),
                     state.get('pinOrderCustomized'), state.get('conversationOrganization'),
                     {key: attention.get(key) for key in ('total', 'unread', 'sections', 'sessions')},
                     [[item['id'], item['fingerprint'], item['read']] for item in attention['items']
                      if item.get('sessionId') and item['id'] == 'session:' + item['sessionId']],
                     {key: state.get('sharedHistory', {}).get(key) for key in ('loading', 'refreshing', 'error')},
                     state.get('locationListing'), state.get('actionStatus', {}).get('locations.list'), state.get('actionStatus', {}).get('locations.create')]
            return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return self.get(('shell-data-key',), build)
