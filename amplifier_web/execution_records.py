"""Versioned host execution records in the existing immutable resource graph.

A changed record replaces one bounded leaf and its index path. Old view roots
still name their exact records, including after failed publication. This module
owns no transaction, replay, native history, cache or process lifecycle.
"""
from __future__ import annotations

import copy
import hashlib
import json

from . import resource_files

COLLECTIONS = ('nodes', 'turns', 'retiredUsageNodes')
PAGE_ROWS = 128
FORMAT = resource_files.EXECUTION_ROOT_FORMAT


def key(kind, row):
    """Keep the existing receipt identity; turn IDs already belong to the owner."""
    identity = row.get('id')
    if not isinstance(identity, str) or not identity:
        raise ValueError('An execution record needs its exact identity.')
    return json.dumps([row.get('sessionId'), identity] if kind == 'retiredUsageNodes' else [identity],
                      ensure_ascii=False, separators=(',', ':'))


def _hash(identity):
    return hashlib.sha256(identity.encode()).hexdigest()


def _read(db, reference):
    return resource_files.read_detached(resource_files.detach(db, reference['$resource']))


def _facts(kind, rows):
    from .cold_display import pending_execution_nodes
    from .execution import LIVE_PHASES
    if kind == 'turns':
        return [False, any(row.get('phase') in LIVE_PHASES for row in rows),
                any(row.get('lifecycle') == 'voice' and row.get('phase') in LIVE_PHASES for row in rows), False]
    observation, voice = pending_execution_nodes(rows)
    return [observation, False, voice, kind == 'nodes' and any(not row.get('turnId') for row in rows)]


def _page(db, kind, records, depth=0):
    if len(records) <= PAGE_ROWS:
        return resource_files.put_execution_index(db, {'records': records})
    if depth >= 64:
        raise ValueError('Execution record keys cannot be separated.')
    groups = {}
    for identity, row in records.items():
        groups.setdefault(_hash(identity)[depth], {})[identity] = row
    return _branch(db, {prefix: _page(db, kind, rows, depth + 1)
                        for prefix, rows in groups.items()})


def _branch(db, children):
    return resource_files.put_execution_index(db, {'children': children})


def _find(db, reference, identity, depth=0, *, read=None):
    if reference is None:
        return None
    page = (read or _read)(db, reference)
    if 'records' in page:
        return page['records'].get(identity)
    if depth >= 64:
        raise ValueError('Invalid execution index depth.')
    return _find(db, page['children'].get(_hash(identity)[depth]), identity, depth + 1, read=read)


def _replace(db, kind, reference, identity, entry, depth=0):
    page = _read(db, reference) if reference is not None else {'records': {}}
    if 'records' in page:
        records = page['records']
        if entry is None:
            records.pop(identity, None)
        else:
            records[identity] = entry
        return _page(db, kind, records, depth)
    if depth >= 64:
        raise ValueError('Invalid execution index depth.')
    children = page['children']
    prefix = _hash(identity)[depth]
    children[prefix] = _replace(db, kind, children.get(prefix), identity, entry, depth + 1)
    return _branch(db, children)


def _ordered_branch(db, children):
    facts = [False, False, False, False]
    for child in children.values():
        facts = [old or value for old, value in zip(facts, child['executionFacts'], strict=True)]
    count = sum(child['count'] for child in children.values())
    return {**resource_files.put_execution_index(db, {'children': children, 'count': count, 'facts': facts}),
            'count': count, 'executionFacts': facts}


def _ordered(db, kind, entries, height):
    """Canonical bodies in ordinal-contiguous pages, with direct radix seek."""
    if not entries:
        return None
    if height == 0:
        facts = _facts(kind, [entry['value'] for _, entry in entries])
        page = {'order': sorted(entries), 'count': len(entries), 'facts': facts}
        return {**resource_files.put(db, page),
                'count': len(entries), 'executionFacts': facts}
    else:
        groups = {}
        for ordinal, entry in entries:
            digit = (ordinal // (PAGE_ROWS * 16 ** (height - 1))) % 16
            groups.setdefault(str(digit), []).append((ordinal, entry))
        return _ordered_branch(db, {digit: _ordered(db, kind, rows, height - 1)
                                    for digit, rows in groups.items()})


def _order_replace(db, kind, reference, height, ordinal, value):
    page = _read(db, reference) if reference else {}
    if height == 0:
        entries = dict(page.get('order', []))
        if value is None:
            entries.pop(ordinal, None)
        else:
            entries[ordinal] = value
        return _ordered(db, kind, list(entries.items()), 0)
    children = page.get('children', {})
    digit = str((ordinal // (PAGE_ROWS * 16 ** (height - 1))) % 16)
    changed = _order_replace(db, kind, children.get(digit), height - 1, ordinal, value)
    if changed is None:
        children.pop(digit, None)
    else:
        children[digit] = changed
    if not children:
        return None
    return _ordered_branch(db, children)


def _order_update(db, kind, collection, ordinal, value):
    height = collection['orderHeight']
    reference = collection['order']
    while ordinal >= PAGE_ROWS * 16 ** height:
        if reference:
            reference = _ordered_branch(db, {'0': reference})
        height += 1
    collection['orderHeight'] = height
    collection['order'] = _order_replace(db, kind, reference, height, ordinal, value)


def _ordered_entry(db, collection, identity, metadata, read=None):
    if metadata is None:
        return None
    ordinal = metadata['ordinal']
    reference, height = collection['order'], collection['orderHeight']
    while reference:
        page = (read or _read)(db, reference)
        if height == 0:
            entry = next((value for number, value in page['order']
                          if number == ordinal), None)
            if entry is None or entry['identity'] != identity:
                break
            return {'ordinal': ordinal, 'value': entry['value']}
        digit = str((ordinal // (PAGE_ROWS * 16 ** (height - 1))) % 16)
        reference = page['children'].get(digit)
        height -= 1
    raise ValueError('Saved execution lookup no longer matches its record.')


def _order_window(db, reference, start, stop, read):
    if reference is None or start >= stop:
        return []
    page = read(db, reference)
    if 'order' in page:
        return page['order'][start:stop]
    result, offset = [], 0
    for digit in sorted(page['children'], key=int):
        child = page['children'][digit]
        end = offset + child['count']
        if start < end and stop > offset:
            result.extend(_order_window(db, child, max(0, start - offset), stop - offset, read))
        offset = end
        if offset >= stop:
            break
    return result


def _bounded_reader(db, max_bytes):
    """Request-local immutable page reuse, bounded by actual backing bytes."""
    remaining, pages = max_bytes, {}
    def read(_db, reference):
        nonlocal remaining
        identity = reference['$resource']
        if identity in pages:
            return pages[identity]
        source = resource_files.detach(db, identity)
        signature = resource_files.detached_signature(source)
        size = signature[2] if len(signature) == 5 else len(source.indexed.encode())
        if size > remaining:
            raise ValueError('Saved execution selection exceeds its byte budget.')
        page = resource_files.read_detached(source, max_bytes=remaining)
        remaining -= size
        pages[identity] = page
        return page
    return read


def _root(db, reference, owner=None, *, read=None):
    root = (read or _read)(db, reference)
    if (root.get('format') != FORMAT or set(root.get('collections', {})) != set(COLLECTIONS)
            or owner is not None and root.get('owner') != list(owner)):
        raise ValueError('Execution records belong to a different owner or unsupported format.')
    return root


def _store(db, root):
    facts = [False, False, False, False]
    for collection in root['collections'].values():
        if collection['order'] is not None:
            facts = [old or value for old, value in zip(facts, collection['order']['executionFacts'], strict=True)]
    reference = resource_files.put_execution_root(db, root)
    return {**reference, 'executionProjection': 2, 'pendingObservation': facts[0],
            'pendingWork': facts[1], 'pendingVoice': facts[2], 'unassignedNodes': facts[3]}


def import_tree(db, owner, tree):
    """Adopt a complete canonical snapshot once; do not detach issued aliases.

    Callers retain their old mutable object until their ownership boundary says
    it is safe to replace. Resource writes here never acknowledge that transfer.
    """
    collections, active = {}, {}
    for kind in COLLECTIONS:
        records, entries = {}, []
        for ordinal, row in enumerate(tree.get(kind, [])):
            identity = key(kind, row)
            if identity in records:
                raise ValueError('Duplicate execution identities require the legacy projection.')
            records[identity] = {'ordinal': ordinal}
            entries.append((ordinal, {'identity': identity, 'value': row}))
        height = 0
        while len(records) > PAGE_ROWS * 16 ** height:
            height += 1
        collections[kind] = {'root': _page(db, kind, records), 'next': len(records),
                             'order': _ordered(db, kind, entries, height),
                             'orderHeight': height}
        active[kind] = [entry['identity'] for _, entry in entries if is_active(entry['value'])]
    fields = {name: value for name, value in tree.items() if name not in COLLECTIONS}
    return _store(db, {'format': FORMAT, 'owner': list(owner),
                       'fields': fields, 'collections': collections, 'active': active})


def read_record(db, reference, kind, identity, *, owner=None):
    """Detached exact-record read. It does not admit historical mutable state."""
    row = read_entry(db, reference, kind, identity, owner=owner)
    return row['value'] if row is not None else None


def read_entry(db, reference, kind, identity, *, owner=None, read=None):
    """Exact identity plus its stable ordinal for an admitted historical read."""
    root = _root(db, reference, owner, read=read)
    collection = root['collections'][kind]
    return _ordered_entry(db, collection, identity, _find(db, collection['root'], identity, read=read), read)


def read_window(db, reference, kind, *, start=0, limit=100, owner=None, max_bytes=4 * 1024 * 1024, read=None):
    """Seek an ordered live range directly, skipping arbitrarily large gaps.

    A same-ID correction retains its ordinal. Deletion removes membership;
    reinsertion gets a new ordinal. Consumers bind exact ordinal/ID pairs.
    """
    if type(start) is not int or type(limit) is not int or start < 0 or not 0 <= limit <= PAGE_ROWS:
        raise ValueError('Choose a bounded execution window.')
    read = read or _bounded_reader(db, max_bytes)
    collection = _root(db, reference, owner, read=read)['collections'][kind]
    selected = _order_window(db, collection['order'], start, start + limit, read)
    rows = []
    for ordinal, entry in selected:
        identity = entry['identity']
        metadata = _find(db, collection['root'], identity, read=read)
        if metadata is None or metadata['ordinal'] != ordinal or key(kind, entry['value']) != identity:
            raise ValueError('Saved execution order no longer matches its record.')
        rows.append({'ordinal': ordinal, 'value': entry['value']})
    return {'rows': rows, 'total': collection['order']['count'] if collection['order'] else 0}


def read_fields(db, reference, *, owner=None):
    return _root(db, reference, owner)['fields']


def selection_metadata(db, reference, kind, *, owner=None, read=None):
    root = _root(db, reference, owner, read=read)
    collection = root['collections'][kind]
    return {'fields': root['fields'], 'next': collection['next'],
            'count': collection['order']['count'] if collection['order'] else 0}


def rank(db, reference, kind, ordinal, *, owner=None, read=None):
    """Current ordered position of an exact ordinal, without walking gaps."""
    collection = _root(db, reference, owner, read=read)['collections'][kind]
    reference, height, position = collection['order'], collection['orderHeight'], 0
    while reference:
        page = (read or _read)(db, reference)
        if height == 0:
            return next((position + i for i, (value, _) in enumerate(page['order']) if value == ordinal), None)
        digit = (ordinal // (PAGE_ROWS * 16 ** (height - 1))) % 16
        position += sum(child['count'] for key, child in page['children'].items() if int(key) < digit)
        reference = page['children'].get(str(digit))
        height -= 1
    return None


def replace_records(db, reference, changes, *, fields=None, owner=None):
    """Stage exact replacements/deletions, preserving order and earlier roots.

    The enclosing session commit selects the returned root. Late corrections
    retain the same identity/ordinal; new records append. No status declares a
    record permanently immutable, and no absent change implies deletion.
    """
    root = _root(db, reference, owner)
    if fields is not None:
        if set(fields) & set(COLLECTIONS):
            raise ValueError('Execution fields cannot replace record collections.')
        root['fields'] = copy.deepcopy(fields)
    for kind, values in changes.items():
        collection = root['collections'][kind]
        for identity, value in values.items():
            if value is not None and key(kind, value) != identity:
                raise ValueError('An execution replacement cannot change its identity.')
            previous = _find(db, collection['root'], identity)
            if previous is None and value is None:
                continue
            ordinal = previous['ordinal'] if previous is not None else collection['next']
            if previous is None:
                collection['next'] += 1
            _order_update(db, kind, collection, ordinal,
                          {'identity': identity, 'value': value} if value is not None else None)
            if previous is None or value is None:
                entry = {'ordinal': ordinal} if value is not None else None
                collection['root'] = _replace(db, kind, collection['root'], identity, entry)
            active = root.setdefault('active', {}).setdefault(kind, [])
            if value is not None and is_active(value):
                if identity not in active:
                    active.append(identity)
            elif identity in active:
                active.remove(identity)
    return _store(db, root)


def materialize(db, reference, *, owner=None):
    """Explicit complete legacy/export view, with ordinary detached containers."""
    root = _root(db, reference, owner)
    result = root['fields']
    for kind, collection in root['collections'].items():
        result[kind] = _collection(db, collection)
    return result


def _collection(db, collection):
    count = collection['order']['count'] if collection['order'] else 0
    return [entry['value'] for _, entry in _order_window(db, collection['order'], 0, count, _read)]


def read_collection(db, reference, kind, *, owner=None):
    """An explicitly requested record family, without other historical bodies."""
    return _collection(db, _root(db, reference, owner)['collections'][kind])


def read_usage(db, reference, *, owner=None):
    """Complete reducer/recovery input, without the separate admission ledger."""
    root = _root(db, reference, owner)
    return {kind: _collection(db, root['collections'][kind])
            for kind in ('nodes', 'turns')}


def is_active(row):
    from .execution import LIVE_PHASES
    return row.get('phase') in LIVE_PHASES


def read_active(db, reference, *, owner=None):
    """Only pending records are automatically admitted into the working set."""
    root = _root(db, reference, owner)
    result = root['fields']
    for kind in COLLECTIONS:
        collection = root['collections'][kind]
        rows = [_ordered_entry(db, collection, identity, _find(db, collection['root'], identity))
                for identity in root.get('active', {}).get(kind, [])]
        if any(row is None for row in rows):
            raise ValueError('A pending execution record is unavailable.')
        result[kind] = [row['value'] for row in sorted(rows, key=lambda row: row['ordinal'])]
    return result
