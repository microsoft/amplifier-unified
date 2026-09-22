"""A folder explorer projected from workspace registrations and chat summaries.

This is an index, not a filesystem browser: availability is maintained by the
workspace registry, and only folders leading to top-level chats are included.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import PurePosixPath, PureWindowsPath
import re

from .session_navigation import is_top_level
from .chat_navigation import recent_activity
from .navigation_summary import activity, path_labels

PAGE_SIZE = 100
NAV_KEYS = {'navWorkspacePath', 'navWorkspaceFilter', 'navWorkspacePage', 'navWorkspaceMode'}


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 4000 or '\0' in value:
        return None
    return _parsed_path(value)


@lru_cache(maxsize=8192)
def _parsed_path(value):
    kind = PureWindowsPath if re.match(r'^[a-zA-Z]:', value) or value.startswith('\\\\') else PurePosixPath
    path = kind(value)
    return path if path.is_absolute() and '..' not in path.parts else None


def _text(path):
    return str(path) if path is not None else ''


def _within(path, parent):
    if path is None or parent is None:
        return parent is None
    try:
        path.relative_to(parent)
        return True
    except (ValueError, TypeError):
        return False


def _root(paths):
    """Leave each workspace selectable, including filesystem-root workspaces."""
    return _common_root(tuple((type(path), str(path)) for path in paths))


@lru_cache(maxsize=4)
def _common_root(paths):
    # Folder geometry depends only on immutable paths, not chat activity or
    # client selection. Include exact spelling: Windows path equality folds
    # case, while navigation must retain the current registry's spelling.
    paths = [kind(value) for kind, value in paths]
    parents = [path.parent for path in paths]
    if not parents or any(path == path.parent for path in paths):
        return None
    root = parents[0]
    for parent in parents[1:]:
        while not _within(parent, root):
            if root == root.parent:
                return None  # Multiple drives, UNC shares, or path platforms.
            root = root.parent
    return root


def _ancestors(path, root):
    yield from _ancestor_paths((type(path), str(path)), (type(root), str(root)) if root is not None else None)


@lru_cache(maxsize=8192)
def _ancestor_paths(path, root):
    path = path[0](path[1])
    root = root[0](root[1]) if root is not None else None
    result = []
    while path != root:
        if path == path.parent:
            result.append(None)
            break
        path = path.parent
        result.append(path)
    return tuple(result)


@lru_cache(maxsize=4)
def _registry_labels(paths):
    # Labels depend on the full set of paths. Availability or registration
    # changes naturally select a different key; callers only read this map.
    return path_labels(paths)


def _index(state):
    registrations = [row for row in state.get('workspaces', []) if row.get('available') is True and _path(row.get('path')) is not None]
    by_id = {row['id']: row for row in registrations}
    by_path = {_path(row['path']): row for row in registrations}
    chats = {}
    seen = set()
    unread_sessions = state.get('attention', {}).get('sessions', {})
    for session in state.get('sessions', []):
        if not is_top_level(session) or session.get('id') in seen:
            continue
        seen.add(session.get('id'))
        workspace = by_id.get(session.get('workspaceId')) or by_path.get(_path(session.get('workspace')))
        if workspace is None:
            continue
        path = _path(workspace['path'])
        entry = chats.get(path)
        if entry is None:
            entry = chats[path] = {'workspace': workspace, 'workspaceSelections': {}, 'chatCount': 0, 'unread': 0, 'recentActivityAt': 0, 'activityCounts': dict.fromkeys(('attention', 'working', 'unread', 'idle'), 0)}
        entry['workspaceSelections'][workspace['id']] = workspace
        entry['chatCount'] += 1
        entry['unread'] += bool(unread_sessions.get(session.get('id')))
        entry['recentActivityAt'] = max(entry['recentActivityAt'], recent_activity(session))
        entry['activityCounts'][activity(session, bool(unread_sessions.get(session.get('id'))))['kind']] += 1

    root = _root(list(chats))
    nodes = {root: {'children': set(), 'descendantWorkspaceCount': 0, 'unread': 0}}
    for path, entry in chats.items():
        node = nodes.setdefault(path, {'children': set(), 'descendantWorkspaceCount': 0, 'unread': 0})
        node.update(workspace=entry['workspace'], workspaceSelections=entry['workspaceSelections'], chatCount=entry['chatCount'], recentActivityAt=entry['recentActivityAt'], activityCounts=entry['activityCounts'])
        node['unread'] += entry['unread']
        child = path
        for parent in _ancestors(path, root):
            node = nodes.setdefault(parent, {'children': set(), 'descendantWorkspaceCount': 0, 'unread': 0})
            node['children'].add(child)
            node['descendantWorkspaceCount'] += 1
            node['unread'] += entry['unread']
            child = parent
    return root, nodes, len(chats)


def _reachable(path, root, nodes):
    """Restore a saved location at its nearest surviving browsable ancestor."""
    if not _within(path, root):
        return root
    while path is not None:
        if path in nodes and (nodes[path]['children'] or path == root):
            return path
        if path == path.parent:
            break
        path = path.parent
    return root


def _location(state, root, nodes):
    view = state.get('view', {})
    scoped = view.get('navWorkspaceBrowseFor') == state.get('selectedWorkspaceId')
    if scoped and 'navWorkspacePath' in view:
        requested = _path(view['navWorkspacePath'])
    else:
        selected = next((row for row in state.get('workspaces', []) if row['id'] == state.get('selectedWorkspaceId')), {})
        selected_path = _path(selected.get('path'))
        requested = selected_path.parent if selected_path is not None and selected_path != selected_path.parent else root
    location = _reachable(requested, root, nodes)
    query = view.get('navWorkspaceFilter', '') if scoped else ''
    query = query.strip() if isinstance(query, str) else ''
    page = view.get('navWorkspacePage', 1) if scoped else 1
    page = page if type(page) is int and page >= 1 else 1
    return location, query, page


def _row(path, node, selected=None):
    workspace = node.get('workspaceSelections', {}).get(selected, node.get('workspace', {}))
    name = path.name or str(path)
    result = {'path': str(path), 'parentPath': _text(path.parent if path != path.parent else None), 'name': name, 'workspaceId': workspace.get('id'),
              'chatCount': node.get('chatCount', 0),
              'descendantWorkspaceCount': node['descendantWorkspaceCount'],
              'canBrowse': bool(node['children']), 'unread': node['unread']}
    result['recentActivityAt'] = node.get('recentActivityAt', 0)
    result['activityCounts'] = node.get('activityCounts', {})
    alias = workspace.get('name')
    if alias and alias != name:
        result['customName'] = alias
    return result


def _matches(row, query):
    terms = [row['path'], row['name'], row.get('customName', '')]
    query = query.casefold()
    if any(character in query for character in '*?['):
        return any(fnmatchcase(term.casefold(), query) for term in terms)
    return any(query in term.casefold() for term in terms)


def snapshot(state, *, index=None):
    """Return the same bounded explorer shown to users and agents."""
    root, nodes, total = _index(state) if index is None else index
    location, query, requested_page = _location(state, root, nodes)
    mode = state.get('view', {}).get('navWorkspaceMode', 'folders')
    paths = [path for path, node in nodes.items() if node.get('workspace')] if query or mode == 'recent' else nodes[location]['children']
    selected = state.get('selectedWorkspaceId')
    rows = [_row(path, nodes[path], selected) for path in paths]
    if query:
        rows = [row for row in rows if _matches(row, query)]
    labels = _registry_labels(frozenset(str(path) for path, node in nodes.items() if node.get('workspace')))
    for row in rows:
        row['pathLabel'] = labels.get(row['path'], row['path'])
    rows.sort(key=lambda row: ((-row['recentActivityAt'] if mode == 'recent' and not query else 0), row['name'].casefold(), row['path'].casefold(), row['path']))
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(requested_page, pages)
    trail = [location]
    if location != root:
        trail.extend(_ancestors(location, root))
    breadcrumbs = [{'path': _text(path), 'name': 'Workspaces' if path is None else (_text(path) if path == root else path.name or _text(path))} for path in reversed(trail)]
    parent = None if location == root else _text(location.parent if location.parent != location else None)
    return {'path': _text(location), 'parentPath': parent, 'rootPath': _text(root),
            'breadcrumbs': breadcrumbs, 'filter': query, 'mode': mode,
            'rows': rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE],
            'selected': next((_row(path, node, selected) for path, node in nodes.items() if selected in node.get('workspaceSelections', {})), None),
            'totalWorkspaces': total, 'totalRows': len(rows), 'page': page, 'pages': pages}


def view_patch(state, patch):
    """Validate browse controls and scope their durable state to the selection."""
    if not NAV_KEYS.intersection(patch):
        return patch
    result = dict(patch)
    root, nodes, _ = _index(state)
    if 'navWorkspaceMode' in patch and patch['navWorkspaceMode'] not in ('recent', 'folders'):
        raise ValueError('Workspace mode must be recent or folders.')
    if 'navWorkspacePath' in patch:
        value = patch['navWorkspacePath']
        path = _path(value)
        if not isinstance(value, str) or (value != '' and path is None):
            raise ValueError('Choose an absolute workspace folder path of up to 4000 characters.')
        if value == '' and root is not None:
            raise ValueError('Choose a folder from the workspace explorer.')
        if path not in nodes or (path != root and not nodes[path]['children']):
            raise ValueError('This folder has no deeper workspaces to browse. Refresh the workspace explorer.')
        result['navWorkspacePath'] = _text(path)
    if 'navWorkspaceFilter' in patch and (not isinstance(patch['navWorkspaceFilter'], str) or len(patch['navWorkspaceFilter']) > 500):
        raise ValueError('Workspace search must be text of up to 500 characters.')
    if 'navWorkspacePage' in patch and (type(patch['navWorkspacePage']) is not int or not 1 <= patch['navWorkspacePage'] <= 1_000_000):
        raise ValueError('Workspace page must be a positive integer up to 1000000.')
    view = state.get('view', {})
    if view.get('navWorkspaceBrowseFor') != state.get('selectedWorkspaceId'):
        location, _, _ = _location(state, root, nodes)
        result = {'navWorkspacePath': _text(location), 'navWorkspaceFilter': '', 'navWorkspacePage': 1, **result}
    if {'navWorkspacePath', 'navWorkspaceFilter', 'navWorkspaceMode'}.intersection(patch):
        result.setdefault('navWorkspacePage', 1)
    if 'navWorkspacePath' in patch:
        result.setdefault('navWorkspaceFilter', '')
    result['navWorkspaceBrowseFor'] = state.get('selectedWorkspaceId')
    return result
