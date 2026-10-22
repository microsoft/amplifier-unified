"""Lossless agent JSON Pointer reads without materializing the history catalog.

The global /sessions indices use the directory's stable order, independent of
which owners are resident. Only an explicitly requested page or owner hydrates
its saved presentation; global/Canvas reads never touch dormant session bodies.
"""
import copy
import hashlib
import json

from .performance_diagnostics import measured


def _metadata(directory, keys):
    """Decode only the bounded selected positions, preserving global ordinals."""
    from .history_directory import COLUMNS
    if not keys:
        return {}
    keys = list(dict.fromkeys(keys))
    sql, bound = directory.effective(public_ids=list(dict.fromkeys(key[0] for key in keys)))
    rows = directory.db.execute(sql + ' SELECT ' + ','.join(COLUMNS) + """ FROM directory_rows
        WHERE (id,ordinal) IN (SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]')
                              FROM json_each(?))""", (*bound, json.dumps(keys)))
    return {(row[0], row[COLUMNS.index('ordinal')]): directory.decode(row) for row in rows}


def _ranked(directory, where='1', params=(), *, limit=50, offset=0):
    sql, bound = directory.effective(fields=('id', 'ordinal', 'session_kind'))
    # Rank membership scalars before filtering. Resolving every historical
    # parent/body/activity while ranking made a small pointer read catalog-sized.
    sql += ', ranked AS (SELECT id,ordinal,session_kind,ROW_NUMBER() OVER(ORDER BY ordinal,id)-1 AS position FROM directory_rows)'
    rows = directory.db.execute(sql + ' SELECT id,ordinal,position FROM ranked WHERE '
        + where + ' ORDER BY position LIMIT ? OFFSET ?', (*bound, *params, limit, offset)).fetchall()
    metadata = _metadata(directory, [(row[0], row[1]) for row in rows])
    return [(position, metadata[(identity, ordinal)]) for identity, ordinal, position in rows]


def _overview_rows(directory, selected):
    children, total = [], 0
    if selected is not None:
        sql, bound = directory.effective(exact_parent=selected, fields=('id', 'ordinal'))
        selected_rows = directory.db.execute(sql + ' SELECT id,ordinal,count(*) OVER() FROM directory_rows ORDER BY ordinal,id LIMIT 20', bound).fetchall()
        total = selected_rows[0][2] if selected_rows else 0
        children = [(identity, ordinal) for identity, ordinal, _ in selected_rows]
    sql, bound = directory.effective(fields=('id', 'ordinal', 'session_kind'))
    sql += ", ranked AS (SELECT id,ordinal,session_kind,ROW_NUMBER() OVER(ORDER BY ordinal,id)-1 AS position FROM directory_rows)"
    rows = directory.db.execute(sql + """
        SELECT 0,id,ordinal,position FROM (SELECT * FROM ranked WHERE session_kind='root' ORDER BY position LIMIT 50)
        UNION ALL
        SELECT 1,id,ordinal,position FROM ranked WHERE id=? OR (id,ordinal) IN (
            SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]') FROM json_each(?))
        ORDER BY 1,4""", (*bound, selected, json.dumps(children))).fetchall()
    metadata = _metadata(directory, [(row[1], row[2]) for row in rows])
    roots = [(position, metadata[(identity, ordinal)]) for kind, identity, ordinal, position in rows if kind == 0]
    positions = {(identity, ordinal): position for _, identity, ordinal, position in rows}
    owner = next(((position, metadata[(identity, ordinal)]) for kind, identity, ordinal, position in rows
                  if kind == 1 and identity == selected), None)
    return owner, roots, [(positions[key], metadata[key]) for key in children], total


def _count(directory, where='1', params=()):
    sql, bound = directory.effective(fields=('id',) if where == '1' else None)
    return directory.db.execute(sql + ' SELECT count(*) FROM directory_rows WHERE ' + where,
                                (*bound, *params)).fetchone()[0]


def _owner(directory, metadata, state, session_id=None):
    from .cold_display import detached
    service = directory.service
    resident = next((row for row in service._state['sessions'] if row['id'] == metadata['id']), None)
    if resident is not None:
        row = detached(resident)
    else:
        from .state_records import load_owner
        from .session_projection import hydrate
        saved, _ = load_owner(directory.db, metadata['id'])
        if saved is None:
            row = {**metadata, 'messages': [], 'workers': [], 'approvals': []}
        else:
            context = {'sessions': [saved]}
            hydrate(service.data_dir, context, directory.db)
            row = context['sessions'][0]
            row.update(metadata)
    if session_id is not None:
        from .agent_private_state import session
        return session(row)
    client = service.clients.records.get(state.get('client', {}).get('id'))
    if client is not None:
        row['draft'] = client.get('drafts', {}).get(row['id'], '')
        row['draftAttachments'] = copy.deepcopy(client.get('attachments', {}).get(row['id'], []))
    return row


def _limits(args):
    offset, limit = args.get('offset', 0), args.get('limit', 50)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16000:
        raise ValueError('Use a nonnegative integer offset and a limit between 1 and 16000.')
    return offset, min(limit, 50)


@measured('directory.overview')
def _overview(directory, state, session_id):
    from .agent_state import overview
    selected = session_id or state.get('selectedSessionId')
    selected_row, roots, children, total = _overview_rows(directory, selected)
    # Build the existing caller-scoped overview from one owner, then attach
    # SQL-paged metadata. Its pointers keep the complete catalog's ordinals.
    owner = _owner(directory, selected_row[1], state, session_id) if selected_row else None
    result = overview({**state, 'sessions': [owner] if owner is not None else []}, selected,
                      positions={selected: selected_row[0]} if selected_row else {})
    result['conversations'] = [{'id': row['id'], 'title': row.get('title'), 'status': row.get('status'),
        'messageCount': row.get('sharedHistoryTotal', row.get('displayMessageCount') if row.get('historyLoaded', True) else None),
        'historyLoaded': row.get('historyLoaded', True), 'workspaceId': row.get('workspaceId'),
        '$statePath': '/sessions/' + str(position)}
        for position, row in roots]
    result['subagentChats'] = {'total': total,
        'items': [{'id': row['id'], 'title': row.get('title'), '$statePath': '/sessions/' + str(position)}
                  for position, row in children]}
    return result


def _sessions(directory, state, args, resolve, reference_key, session_id):
    from .agent_state import read_state, _preview, _size
    from .configuration_state import project
    path = args['path']
    tokens = path.split('/')[1:]
    if len(tokens) == 1:
        offset, limit = _limits(args)
        total, items, used = _count(directory), [], 0
        for position, metadata in _ranked(directory, limit=limit, offset=offset):
            pointer = '/sessions/' + str(position)
            item = {'key': position, 'path': pointer,
                    'value': _preview(project(_owner(directory, metadata, state, session_id),
                                              pointer, reference_key=reference_key),
                                      pointer, 2500, reference_key=reference_key)}
            size = _size(item)
            if items and used + size > 24000:
                break
            items.append(item); used += size
        end = offset + len(items)
        return {'path': path, 'revision': state.get('revision'), 'offset': offset, 'type': 'array',
                'items': items, 'total': total, 'nextOffset': end if end < total else None}
    try:
        position = int(tokens[1])
    except ValueError:
        raise ValueError('That state path does not exist.') from None
    # Retain the legacy list's negative-index semantics for direct reads.
    if position < 0:
        position += _count(directory)
    rows = _ranked(directory, 'position=?', (position,), limit=1)
    if not rows:
        raise ValueError('That state path does not exist.')
    # A one-key mapping resolves this exact pointer without allocating a sparse
    # list or rewriting any paths inside user-provided JSON values.
    return read_state({**state, 'sessions': {tokens[1]: _owner(directory, rows[0][1], state, session_id)}},
                      args, session_id=session_id, resolve=resolve, reference_key=reference_key,
                      configuration_projection=True)


def _runtime(directory, state, args, resolve, reference_key, session_id):
    from .agent_state import read_state
    from .cold_display import ColdRecord, detached
    controls = directory.service._state.get('runtimeControl', {})
    path = args['path']
    if path == '/runtimeControl':
        offset, limit = _limits(args)
        sql = "WITH owners AS (SELECT id FROM state_records WHERE kind='runtime' AND value!='null' UNION SELECT value FROM json_each(?))"
        params = (json.dumps(list(controls)),)
        total = directory.db.execute(sql + ' SELECT count(*) FROM owners', params).fetchone()[0]
        ids = [row[0] for row in directory.db.execute(sql + ' SELECT id FROM owners ORDER BY id LIMIT ? OFFSET ?', (*params, limit, offset))]
    else:
        offset = 0
        ids = [path.split('/')[2].replace('~1', '/').replace('~0', '~')]
    values = {}
    for identity in ids:
        if identity in controls:
            values[identity] = detached(controls[identity])
        else:
            from .state_records import load_owner
            _, value = load_owner(directory.db, identity)
            if value is not None:
                values[identity] = ColdRecord(value, directory.db)
    result = read_state({**state, 'runtimeControl': values}, {**args, **({'offset': 0} if path == '/runtimeControl' else {})},
                        session_id=session_id, resolve=resolve, reference_key=reference_key,
                        configuration_projection=True)
    if path == '/runtimeControl':
        end = offset + len(result['items'])
        result.update(offset=offset, total=total, nextOffset=end if end < total else None)
    return result


def _read(directory, state, args, *, session_id=None, resolve=None, reference_key='$statePath'):
    path = args.get('path')
    if path is None:
        return True, _overview(directory, state, session_id)
    if not isinstance(path, str):
        return False, None
    if path == '/sessions' or path.startswith('/sessions/'):
        return True, _sessions(directory, state, args, resolve, reference_key, session_id)
    if path == '/runtimeControl' or path.startswith('/runtimeControl/'):
        return True, _runtime(directory, state, args, resolve, reference_key, session_id)
    if path == '':
        # A root page advertises the complete catalog, not just its residents.
        from .agent_state import read_state
        # Replace these collections before previewing, not after loading them.
        bounded = {**state, 'sessions': [], 'runtimeControl': {}}
        result = read_state(bounded, args, session_id=session_id, resolve=resolve, reference_key=reference_key,
                            configuration_projection=True)
        for item in result.get('items', []):
            if item['key'] == 'sessions':
                item['value'] = {reference_key: '/sessions', 'type': 'list', 'size': _count(directory)}
            elif item['key'] == 'runtimeControl':
                item['value'] = {reference_key: '/runtimeControl', 'type': 'dict'}
        return True, result
    return False, None


def read(directory, state, args, *, session_id=None, resolve=None, reference_key='$statePath'):
    path = args.get('path')
    indexed = isinstance(path, str) and (path == '/sessions' or path.startswith('/sessions/'))
    uses_directory = (path is None or path == '' or indexed or isinstance(path, str)
                      and (path == '/runtimeControl' or path.startswith('/runtimeControl/')))
    if not uses_directory:
        return False, None
    # Native discovery can commit before the application publishes its integer
    # revision. Never certify a global ordinal using that integer alone. Derive
    # the directory token from the same pinned WAL views used for this read.
    reader = directory.read_snapshot(directory.service._state)
    try:
        token = hashlib.sha256(json.dumps(reader.observation_revision['navigation'],
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        expected = args.get('directoryRevision')
        if expected is not None and expected != token:
            raise ValueError('Saved chat directory changed. Read the current directory revision before continuing pages.')
        if indexed and args.get('revision') is not None and expected is None:
            raise ValueError('Include directoryRevision with revision when following saved chat indices. Read the current overview first.')
        handled, result = _read(reader, state, args, session_id=session_id, resolve=resolve, reference_key=reference_key)
        if handled:
            result['directoryRevision'] = token
            if path is None:
                result['_stateAccess']['directoryRevision'] = token
                result['_stateAccess']['note'] += (' For /sessions pages and indexed paths, pass both revision and '
                    'directoryRevision from this response; native discovery can change directory order independently. '
                    'Without revision, a read explicitly uses the current directory.')
        return handled, result
    finally:
        reader.close()
