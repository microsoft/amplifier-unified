"""One ordered shortcut list, with legacy per-kind pin vectors retained."""


def ordered_ids(state):
    from .workspace_navigation import workspace_pins

    current = [f'session:{sid}' for sid in state.get('pinnedSessionIds', [])]
    current += [f'workspace:{wid}' for wid in workspace_pins(state)]
    members = set(current)
    saved = state.get('pinnedItemIds', [])
    saved = saved if isinstance(saved, list) else []
    return list(dict.fromkeys([key for key in saved if isinstance(key, str) and key in members] + current))


def keep_order(state, previous):
    state['pinnedItemIds'] = ordered_ids({**state, 'pinnedItemIds': previous})


def reorder_kind(state, kind, ids):
    """Legacy controls reorder their slots without moving the other kind."""
    order = ordered_ids(state)
    replacements = iter(f'{kind}:{identity}' for identity in ids)
    state['pinnedItemIds'] = [next(replacements) if key.startswith(kind + ':') else key for key in order]


def reorder(state, ids):
    current = ordered_ids(state)
    if len(ids) != len(set(ids)) or set(ids) != set(current):
        raise ValueError('Include every currently pinned item exactly once; refresh if pins changed.')
    state['pinnedItemIds'] = list(ids)
    state['pinnedSessionIds'] = [key[len('session:'):] for key in ids if key.startswith('session:')]
    state['pinnedWorkspaceIds'] = [key[len('workspace:'):] for key in ids if key.startswith('workspace:')]
    state['pinOrderCustomized'] = True
    return {'ids': list(ids)}


def snapshot(state, chats, workspaces):
    """Page after combining both populations; hidden pins retain their slots."""
    rows = {f'session:{row["id"]}': {'id': f'session:{row["id"]}', 'kind': 'session', 'row': row}
            for row in chats if row['pinned']}
    rows.update({f'workspace:{row["workspaceId"]}': {
        'id': f'workspace:{row["workspaceId"]}', 'kind': 'workspace', 'row': row}
        for row in workspaces})
    items = [rows[key] for key in ordered_ids(state) if key in rows]
    size = 50
    pages = max(1, (len(items) + size - 1) // size)
    requested = state.get('view', {}).get('navPinnedPage', 0)
    index = max(0, min(pages - 1, requested))
    start, end = index * size, min(len(items), (index + 1) * size)
    return {'items': items[start:end], 'total': len(items), 'index': index,
            'pages': pages, 'start': start, 'end': end}