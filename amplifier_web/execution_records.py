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
FORMAT = 'unified-execution-records/1'


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
        rows = [entry['value'] for entry in records.values()]
        facts = _facts(kind, rows)
        return {**resource_files.put(db, {'records': records, 'facts': facts}), 'executionFacts': facts}
    if depth >= 64:
        raise ValueError('Execution record keys cannot be separated.')
    groups = {}
    for identity, row in records.items():
        groups.setdefault(_hash(identity)[depth], {})[identity] = row
    return _branch(db, {prefix: _page(db, kind, rows, depth + 1)
                        for prefix, rows in groups.items()})


def _branch(db, children):
    facts = [False, False, False, False]
    for reference in children.values():
        facts = [old or value for old, value in zip(facts, reference['executionFacts'], strict=True)]
    return {**resource_files.put(db, {'children': children, 'facts': facts}), 'executionFacts': facts}


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


def _entries(db, reference):
    page = _read(db, reference)
    if 'records' in page:
        yield from page['records'].values()
    else:
        for child in page['children'].values():
            yield from _entries(db, child)


def _ordered(db, entries, height):
    """Direct ordinal radix index; leaves hold identities, never record bodies."""
    if not entries:
        return None
    if height == 0:
        value = {'order': sorted(entries), 'count': len(entries)}
    else:
        groups = {}
        for ordinal, identity in entries:
            digit = (ordinal // (PAGE_ROWS * 16 ** (height - 1))) % 16
            groups.setdefault(str(digit), []).append((ordinal, identity))
        children = {digit: _ordered(db, rows, height - 1) for digit, rows in groups.items()}
        value = {'children': children, 'count': sum(row['count'] for row in children.values())}
    return {**resource_files.put(db, value), 'count': value['count']}


def _order_replace(db, reference, height, ordinal, identity):
    page = _read(db, reference) if reference else {}
    if height == 0:
        entries = dict(page.get('order', []))
        if identity is None:
            entries.pop(ordinal, None)
        else:
            entries[ordinal] = identity
        return _ordered(db, list(entries.items()), 0)
    children = page.get('children', {})
    digit = str((ordinal // (PAGE_ROWS * 16 ** (height - 1))) % 16)
    changed = _order_replace(db, children.get(digit), height - 1, ordinal, identity)
    if changed is None:
        children.pop(digit, None)
    else:
        children[digit] = changed
    if not children:
        return None
    count = sum(row['count'] for row in children.values())
    return {**resource_files.put(db, {'children': children, 'count': count}), 'count': count}


def _order_update(db, collection, ordinal, identity):
    height = collection['orderHeight']
    reference = collection['order']
    while ordinal >= PAGE_ROWS * 16 ** height:
        if reference:
            reference = {**resource_files.put(db, {'children': {'0': reference}, 'count': reference['count']}),
                         'count': reference['count']}
        height += 1
    collection['orderHeight'] = height
    collection['order'] = _order_replace(db, reference, height, ordinal, identity)


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
        facts = [old or value for old, value in zip(facts, collection['root']['executionFacts'], strict=True)]
    reference = resource_files.put(db, root)
    return {**reference, 'executionProjection': 2, 'pendingObservation': facts[0],
            'pendingWork': facts[1], 'pendingVoice': facts[2], 'unassignedNodes': facts[3]}


def import_tree(db, owner, tree):
    """Adopt a complete canonical snapshot once; do not detach issued aliases.

    Callers retain their old mutable object until their ownership boundary says
    it is safe to replace. Resource writes here never acknowledge that transfer.
    """
    collections, active = {}, {}
    for kind in COLLECTIONS:
        records = {}
        for ordinal, row in enumerate(tree.get(kind, [])):
            identity = key(kind, row)
            if identity in records:
                raise ValueError('Duplicate execution identities require the legacy projection.')
            records[identity] = {'ordinal': ordinal, 'value': row}
        height = 0
        while len(records) > PAGE_ROWS * 16 ** height:
            height += 1
        collections[kind] = {'root': _page(db, kind, records), 'next': len(records),
                             'order': _ordered(db, [(row['ordinal'], identity) for identity, row in records.items()], height),
                             'orderHeight': height}
        active[kind] = [identity for identity, row in records.items() if is_active(row['value'])]
    fields = {name: value for name, value in tree.items() if name not in COLLECTIONS}
    return _store(db, {'format': FORMAT, 'owner': list(owner),
                       'fields': fields, 'collections': collections, 'active': active})


def read_record(db, reference, kind, identity, *, owner=None):
    """Detached exact-record read. It does not admit historical mutable state."""
    row = read_entry(db, reference, kind, identity, owner=owner)
    return row['value'] if row is not None else None


def read_entry(db, reference, kind, identity, *, owner=None):
    """Exact identity plus its stable ordinal for an admitted historical read."""
    root = _root(db, reference, owner)
    return _find(db, root['collections'][kind]['root'], identity)


def read_window(db, reference, kind, *, start=0, limit=100, owner=None, max_bytes=4 * 1024 * 1024):
    """Seek an ordered live range directly, skipping arbitrarily large gaps.

    A same-ID correction retains its ordinal. Deletion removes membership;
    reinsertion gets a new ordinal. Consumers bind exact ordinal/ID pairs.
    """
    if type(start) is not int or type(limit) is not int or start < 0 or not 0 <= limit <= PAGE_ROWS:
        raise ValueError('Choose a bounded execution window.')
    read = _bounded_reader(db, max_bytes)
    collection = _root(db, reference, owner, read=read)['collections'][kind]
    selected = _order_window(db, collection['order'], start, start + limit, read)
    rows = []
    for ordinal, identity in selected:
        row = _find(db, collection['root'], identity, read=read)
        if row is None or row['ordinal'] != ordinal or key(kind, row['value']) != identity:
            raise ValueError('Saved execution order no longer matches its record.')
        rows.append(row)
    return {'rows': rows, 'total': collection['order']['count'] if collection['order'] else 0}


def read_fields(db, reference, *, owner=None):
    return _root(db, reference, owner)['fields']


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
            if previous is None or value is None:
                _order_update(db, collection, ordinal, identity if value is not None else None)
            entry = {'ordinal': ordinal, 'value': value} if value is not None else None
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
        result[kind] = [row['value'] for row in sorted(
            _entries(db, collection['root']), key=lambda row: row['ordinal'])]
    return result


def is_active(row):
    from .execution import LIVE_PHASES
    return row.get('phase') in LIVE_PHASES


def read_active(db, reference, *, owner=None):
    """Only pending records are automatically admitted into the working set."""
    root = _root(db, reference, owner)
    result = root['fields']
    for kind in COLLECTIONS:
        rows = [_find(db, root['collections'][kind]['root'], identity)
                for identity in root.get('active', {}).get(kind, [])]
        if any(row is None for row in rows):
            raise ValueError('A pending execution record is unavailable.')
        result[kind] = [row['value'] for row in sorted(rows, key=lambda row: row['ordinal'])]
    return result
