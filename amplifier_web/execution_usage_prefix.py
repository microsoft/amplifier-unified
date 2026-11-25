"""Bounded continuation of the canonical ordered usage fold.

This is disposable derived state of one current execution owner. Durable rows,
receipt admission and the complete reducer remain authoritative. A late edit,
unsupported topology or an overlarge live suffix simply rebuilds the fold.
"""
from __future__ import annotations

import copy
import json

from . import execution_records as records

# One existing ordered selection envelope, never a limit on accepted work.
MAX_ITEMS = records.PAGE_ROWS
MAX_BYTES = 4 * 1024 * 1024
SHAPE = ('id', 'kind', 'parentId', 'turnId')
FOLD = ('id', 'phase', 'status', 'endedAt', 'usage')


class Rebuild(Exception):
    """The ordinary complete reducer is required for this input."""


def _shape(row):
    if type(row) is not dict or type(row.get('id')) is not str or not row['id']:
        raise Rebuild()
    value = {key: row[key] for key in SHAPE if key in row}
    if any(type(item) not in (str, type(None)) for item in value.values()):
        raise Rebuild()
    return value


def _fold(row):
    # Unknown/custom numeric and container behavior stays with the full reducer.
    value = {key: row[key] for key in FOLD if key in row}
    usage = value.get('usage')
    if usage is not None and (type(usage) is not dict or any(
            type(key) is not str or type(item) not in (str, int, float, bool, type(None))
            for key, item in usage.items())):
        raise Rebuild()
    if any(type(value[key]) not in (str, int, float, bool, type(None))
           for key in value if key != 'usage'):
        raise Rebuild()
    return copy.deepcopy(value)


def _settled(row):
    from .execution import LIVE_PHASES
    return not (row.get('phase', row.get('status')) in LIVE_PHASES and not row.get('endedAt'))


def _usage_owned(row):
    return not any(row.get(flag) for flag in ('liveObservation', 'canonicalHistory', 'nativeHistory'))


def _same_fold(left, right):
    # Python numeric equality alone conflates int/float inputs whose later
    # additions need not round alike. Fold witnesses preserve scalar types.
    return left.keys() == right.keys() and all(type(value) is type(right[key]) and (
        _same_fold(value, right[key]) if type(value) is dict else value == right[key])
        for key, value in left.items())


def preserves_contribution(before, after):
    """Projection may omit irrelevant status, but cannot change a retired fold."""
    from .execution import USAGE_KEYS
    from .token_usage import with_gross_tokens
    if after is None or _shape(before) != _shape(after):
        return False
    if before.get('kind') != 'llm':
        return True
    def contribution(row):
        _fold(row)  # The same plain scalar domain as the continuation.
        usage = with_gross_tokens(row.get('usage') or {})
        keys = (*USAGE_KEYS, 'voiceSeconds', 'usageFinalized', 'costUsd', 'costType', 'costUnavailableReason')
        return (_settled(row), [(key, type(usage[key]), usage[key]) for key in keys if key in usage])
    return contribution(before) == contribution(after)


class UsagePrefix:
    @classmethod
    def build(cls, owner, tree, groups):
        """Seed only from an exact complete reduction, before discarding its input."""
        from .execution import rollup
        calls, descendants, by_turn, lookup = groups
        # A further ordered pass may repair a child even without a new event.
        if any(not row.get('turnId') for row in tree['nodes']):
            raise Rebuild()
        self = cls()
        current = {row['id'] for row in owner.current['nodes']}
        cut = next((i for i, row in enumerate(calls)
                    if row['id'] in current or not _settled(row)), len(calls))
        suffix = calls[cut:]
        if len(suffix) > MAX_ITEMS:
            raise Rebuild()
        self.suffix = [row['id'] for row in suffix]
        self.folds = {row['id']: _fold(row) for row in suffix}
        self.shapes = {}
        self.usage_owned = {}
        for row in [*owner.current['nodes'], *suffix]:
            cursor, seen = row, set()
            while cursor:
                identity = cursor['id']
                if identity in seen:
                    raise Rebuild()
                seen.add(identity)
                self.shapes[identity] = _shape(cursor)
                self.usage_owned[identity] = _usage_owned(cursor)
                cursor = lookup.get(cursor.get('parentId'))
        turns = {row['id'] for row in tree['turns']}
        self.missing_parents = {row['parentId'] for row in tree['nodes']
                                if row.get('parentId') and row['parentId'] not in lookup}
        self.missing_turns = {row.get('turnId') for row in calls if row.get('turnId') not in turns}
        suffix_ids = set(self.suffix)
        self.closed_folds = {identity: _fold(lookup[identity]) for identity in self.shapes
                             if lookup[identity].get('kind') == 'llm' and identity not in suffix_ids}
        self.root = rollup(calls[:cut])
        self.nodes = {identity: rollup([row for row in descendants[identity]
                                       if row['id'] not in suffix_ids]) for identity in self.shapes}
        needed_turns = {row['id'] for row in owner.current['turns']} | {
            row.get('turnId') for row in suffix if row.get('turnId') in turns}
        self.turns = {identity: rollup([row for row in by_turn.get(identity, [])
                                       if row['id'] not in suffix_ids]) for identity in needed_turns}
        self.current_nodes = [row['id'] for row in owner.current['nodes']]
        self.current_turns = [row['id'] for row in owner.current['turns']]
        self._bounded()
        return self

    def _bounded(self):
        if any(len(value) > MAX_ITEMS for value in (
                self.suffix, self.shapes, self.turns, self.missing_parents, self.missing_turns,
                self.current_nodes, self.current_turns)):
            raise Rebuild()
        try:
            size = len(json.dumps({**vars(self), 'missing_parents': list(self.missing_parents),
                                  'missing_turns': list(self.missing_turns)},
                                 ensure_ascii=False, allow_nan=False).encode())
        except (ValueError, TypeError):
            raise Rebuild() from None
        if size > MAX_BYTES:
            raise Rebuild()

    def _chain(self, identity):
        seen = set()
        while identity in self.shapes:
            if identity in seen:
                raise Rebuild()
            seen.add(identity)
            yield identity
            identity = self.shapes[identity].get('parentId')

    def _append(self, owner):
        from .execution import rollup
        current = owner.current
        nodes = {row['id']: row for row in current['nodes']}
        turns = {row['id']: row for row in current['turns']}
        if len(nodes) != len(current['nodes']) or len(turns) != len(current['turns']):
            raise Rebuild()
        # Canonical appends preserve order. An explicit deletion/reorder is an
        # earlier correction, not an opportunity to subtract old totals.
        if list(nodes)[:len(self.current_nodes)] != self.current_nodes:
            raise Rebuild()
        if list(turns)[:len(self.current_turns)] != self.current_turns:
            raise Rebuild()
        for identity in list(turns)[len(self.current_turns):]:
            if identity in self.missing_turns or records.read_entry(owner.db, owner.reference,
                    'turns', records.key('turns', turns[identity]), owner=owner.owner) is not None:
                raise Rebuild()
            self.turns[identity] = rollup([])
        for identity, row in nodes.items():
            shape = _shape(row)
            self.usage_owned[identity] = _usage_owned(row)
            if identity in self.shapes:
                if shape != self.shapes[identity]:
                    raise Rebuild()
                if identity in self.closed_folds and not _same_fold(_fold(row), self.closed_folds[identity]):
                    # An ancestor's own earlier call can be borrowed after its
                    # derived aggregate was admitted. Its contribution is not
                    # mutable suffix work, so any late edit rebuilds exactly.
                    raise Rebuild()
                continue
            if identity in self.missing_parents or records.read_entry(owner.db, owner.reference,
                    'nodes', records.key('nodes', row), owner=owner.owner) is not None:
                raise Rebuild()
            parent = row.get('parentId')
            if parent and parent not in self.shapes:
                raise Rebuild()
            if not row.get('turnId'):
                # The same single ordered inheritance as the full reducer.
                row['turnId'] = self.shapes.get(parent, {}).get('turnId') or current.get('currentTurnId')
                shape = _shape(row)
            if row.get('turnId') not in self.turns:
                raise Rebuild()
            self.shapes[identity] = shape
            self.nodes[identity] = rollup([])
            list(self._chain(identity))  # Cycles retain the complete seen-set path.
            if row.get('kind') == 'llm':
                self.suffix.append(identity)
        self.current_nodes = list(nodes)
        self.current_turns = list(turns)
        for identity in self.suffix:
            if identity in nodes:
                self.folds[identity] = _fold(nodes[identity])
        self._bounded()
        return nodes, turns

    def refresh(self, owner):
        from .execution import _append_rollup
        nodes, turns = self._append(owner)
        calls = [self.folds[identity] for identity in self.suffix]
        by_node = {identity: [] for identity in self.shapes}
        by_turn = {identity: [] for identity in self.turns}
        for identity, call in zip(self.suffix, calls):
            for ancestor in self._chain(identity):
                by_node[ancestor].append(call)
            turn = self.shapes[identity].get('turnId')
            if turn in by_turn:
                by_turn[turn].append(call)
        # Historical derived writes join the exact full row. Thin fold inputs
        # never replace a durable node or lose its source/payload fields.
        for kind, prefixes, groups, resident in (
                ('nodes', self.nodes, by_node, nodes), ('turns', self.turns, by_turn, turns)):
            for identity, prefix in prefixes.items():
                usage = _append_rollup(prefix, groups.get(identity, []))
                row = resident.get(identity)
                if row is None and kind == 'nodes' and not self.usage_owned[identity]:
                    continue
                if row is None and groups.get(identity):
                    row = owner.record(kind, records.key(kind, {'id': identity}), derived=True)
                    if row is None:
                        raise Rebuild()
                if row is not None:
                    row['aggregateUsage'] = usage
        owner.current['aggregateUsage'] = _append_rollup(self.root, calls)
        self.current_nodes = [row['id'] for row in owner.current['nodes']]
        self.current_turns = [row['id'] for row in owner.current['turns']]
        self._bounded()

    def retired(self, current):
        """Called only after a successful commit and actual exclusive ownership."""
        from .execution import _append_rollup
        resident = {row['id']: row for row in current['nodes']}
        if list(resident) != self.current_nodes or [row['id'] for row in current['turns']] != self.current_turns:
            raise Rebuild()
        for identity, row in resident.items():
            if _shape(row) != self.shapes[identity] or (
                    identity in self.closed_folds and not _same_fold(_fold(row), self.closed_folds[identity])):
                raise Rebuild()
            self.usage_owned[identity] = _usage_owned(row)
        retained = {row['id'] for row in current['nodes'] if records.is_active(row)}
        for identity in self.suffix:
            if identity in resident:
                if _shape(resident[identity]) != self.shapes[identity]:
                    raise Rebuild()
                self.folds[identity] = _fold(resident[identity])
        while self.suffix:
            identity = self.suffix[0]
            call = self.folds[identity]
            if identity in retained or not _settled(call):
                break
            self.root = _append_rollup(self.root, [call])
            for ancestor in self._chain(identity):
                self.nodes[ancestor] = _append_rollup(self.nodes[ancestor], [call])
            turn = self.shapes[identity].get('turnId')
            if turn in self.turns:
                self.turns[turn] = _append_rollup(self.turns[turn], [call])
            self.suffix.pop(0)
            del self.folds[identity]
            self.closed_folds[identity] = call
        needed = set()
        for identity in [*retained, *self.suffix]:
            needed.update(self._chain(identity))
        self.shapes = {identity: row for identity, row in self.shapes.items() if identity in needed}
        self.usage_owned = {identity: value for identity, value in self.usage_owned.items() if identity in needed}
        self.nodes = {identity: value for identity, value in self.nodes.items() if identity in needed}
        self.closed_folds = {identity: value for identity, value in self.closed_folds.items() if identity in needed}
        needed_turns = {row['id'] for row in current['turns'] if records.is_active(row)} | {
            self.shapes[identity].get('turnId') for identity in self.suffix}
        self.turns = {identity: value for identity, value in self.turns.items() if identity in needed_turns}
        self.current_nodes = [row['id'] for row in current['nodes'] if records.is_active(row)]
        self.current_turns = [row['id'] for row in current['turns'] if records.is_active(row)]
        self._bounded()
