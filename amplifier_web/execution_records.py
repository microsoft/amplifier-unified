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
    return json.dumps([identity] if kind == 'turns' else [row.get('sessionId'), identity],
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
                any(row.get('lifecycle') == 'voice' and row.get('phase') in LIVE_PHASES for row in rows)]
    observation, voice = pending_execution_nodes(rows)
    return [observation, False, voice]


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
    facts = [False, False, False]
    for reference in children.values():
        facts = [old or value for old, value in zip(facts, reference['executionFacts'], strict=True)]
    return {**resource_files.put(db, {'children': children, 'facts': facts}), 'executionFacts': facts}


def _find(db, reference, identity, depth=0):
    if reference is None:
        return None
    page = _read(db, reference)
    if 'records' in page:
        return page['records'].get(identity)
    if depth >= 64:
        raise ValueError('Invalid execution index depth.')
    return _find(db, page['children'].get(_hash(identity)[depth]), identity, depth + 1)


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


def _root(db, reference, owner=None):
    root = _read(db, reference)
    if (root.get('format') != FORMAT or set(root.get('collections', {})) != set(COLLECTIONS)
            or owner is not None and root.get('owner') != list(owner)):
        raise ValueError('Execution records belong to a different owner or unsupported format.')
    return root


def _store(db, root):
    facts = [False, False, False]
    for collection in root['collections'].values():
        facts = [old or value for old, value in zip(facts, collection['root']['executionFacts'], strict=True)]
    reference = resource_files.put(db, root)
    return {**reference, 'executionProjection': 2, 'pendingObservation': facts[0],
            'pendingWork': facts[1], 'pendingVoice': facts[2]}


def import_tree(db, owner, tree):
    """Adopt a complete canonical snapshot once; do not detach issued aliases.

    Callers retain their old mutable object until their ownership boundary says
    it is safe to replace. Resource writes here never acknowledge that transfer.
    """
    collections = {}
    for kind in COLLECTIONS:
        records = {}
        for ordinal, row in enumerate(tree.get(kind, [])):
            identity = key(kind, row)
            if identity in records:
                raise ValueError('Duplicate execution identities require the legacy projection.')
            records[identity] = {'ordinal': ordinal, 'value': row}
        collections[kind] = {'root': _page(db, kind, records), 'next': len(records)}
    fields = {name: value for name, value in tree.items() if name not in COLLECTIONS}
    return _store(db, {'format': FORMAT, 'owner': list(owner),
                       'fields': fields, 'collections': collections})


def read_record(db, reference, kind, identity, *, owner=None):
    """Detached exact-record read. It does not admit historical mutable state."""
    root = _root(db, reference, owner)
    row = _find(db, root['collections'][kind]['root'], identity)
    return row['value'] if row is not None else None


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
            entry = {'ordinal': ordinal, 'value': value} if value is not None else None
            collection['root'] = _replace(db, kind, collection['root'], identity, entry)
    return _store(db, root)


def materialize(db, reference, *, owner=None):
    """Explicit complete legacy/export view, with ordinary detached containers."""
    root = _root(db, reference, owner)
    result = root['fields']
    for kind, collection in root['collections'].items():
        result[kind] = [row['value'] for row in sorted(
            _entries(db, collection['root']), key=lambda row: row['ordinal'])]
    return result
