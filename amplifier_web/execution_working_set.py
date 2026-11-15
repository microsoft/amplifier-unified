"""Canonical current mutation separate from detached historical execution reads.

Only ColdRecord-owned, unexposed durable bodies enter this path. Ordinary
mutable full dictionaries keep their existing save semantics. No recursive
mutation wrappers or unverified dirty flags are involved: every resident current
record is projected afresh, including nested aliases, on its owner's save.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from . import execution_records as records


def _owner(session):
    # Runtime replacement and moving a chat do not replace its history owner.
    # Individual rows retain their existing source/producer/account identities.
    return (session['id'],)


class LegacyExecution(ValueError):
    """An older complete mutable representation cannot use keyed records."""


def _stored(db, reference):
    from .cold_display import load
    if reference.get('executionProjection') == 2:
        return records.materialize(db, reference)
    return load(db, reference)


class WorkingExecution:
    def __init__(self, session, reference):
        self.db = session.db
        self.owner = _owner(session)
        self.reference = copy.deepcopy(reference)
        if reference.get('executionProjection') == 2:
            self.current = records.read_active(self.db, reference, owner=self.owner)
        else:
            # One legacy ownership transition, not a repeated current update.
            value = _stored(self.db, reference)
            for kind in records.COLLECTIONS:
                try:
                    identities = [records.key(kind, row) for row in value.get(kind, [])]
                except (ValueError, AttributeError):
                    raise LegacyExecution() from None
                if len(identities) != len(set(identities)):
                    raise LegacyExecution()
            self.current = {name: value for name, value in value.items() if name not in records.COLLECTIONS}
            self.current.update({kind: [row for row in value.get(kind, []) if records.is_active(row)]
                                 for kind in records.COLLECTIONS})
        self.admitted = {kind: {records.key(kind, row) for row in self.current[kind]}
                         for kind in records.COLLECTIONS}

    def record(self, kind, identity):
        present = next((row for row in self.current[kind] if records.key(kind, row) == identity), None)
        if present is not None:
            return present
        if self.reference.get('executionProjection') == 2:
            value = records.read_record(self.db, self.reference, kind, identity, owner=self.owner)
        else:
            value = next((row for row in _stored(self.db, self.reference).get(kind, [])
                          if records.key(kind, row) == identity), None)
        if value is not None:
            self.current[kind].append(value)
            self.admitted[kind].add(identity)
        return value

    def _complete(self):
        value = {name: item for name, item in self.current.items() if name not in records.COLLECTIONS}
        stored = _stored(self.db, self.reference)
        for kind in records.COLLECTIONS:
            changes = {records.key(kind, row): row for row in self.current[kind]}
            rows = []
            for row in stored.get(kind, []):
                identity = records.key(kind, row)
                if identity in changes or identity not in self.admitted[kind]:
                    rows.append(changes.pop(identity, row))
            rows.extend(changes.values())
            value[kind] = rows
        return value

    def snapshot(self):
        """Full detached read, without handing historical mutation to a caller."""
        return copy.deepcopy(self._complete())

    def detached(self):
        """Freeze the current overlay while leaving its immutable history cold."""
        value = copy.copy(self)
        value.reference = copy.deepcopy(self.reference)
        value.current = copy.deepcopy(self.current)
        value.admitted = {kind: set(identities) for kind, identities in self.admitted.items()}
        return value

    def expose(self):
        """Transfer to the ordinary complete mutable contract without losing aliases."""
        return self._complete()

    def stage(self):
        """Project resident records; immutable historical pages are not revisited."""
        from .session_projection import accounting_projection, _stored_nodes
        reference = self.reference
        if reference.get('executionProjection') != 2:
            from .session_projection import stored_execution
            reference = records.import_tree(self.db, self.owner, stored_execution(_stored(self.db, reference)))
        changes = {kind: {identity: None for identity in self.admitted[kind]
                          - {records.key(kind, row) for row in self.current[kind]}}
                   for kind in records.COLLECTIONS}
        for row in self.current['retiredUsageNodes']:
            changes['retiredUsageNodes'][records.key('retiredUsageNodes', row)] = row
        turns = {row['id'] for row in self.current['turns']}
        for row in self.current['turns']:
            changes['turns'][records.key('turns', row)] = (
                row if not row.get('nativeHistory') and not row.get('canonicalHistory') else None)
        for row in self.current['nodes']:
            turn = row.get('turnId')
            if turn and turn not in turns and records.read_record(
                    self.db, reference, 'turns', records.key('turns', {'id': turn}), owner=self.owner) is not None:
                turns.add(turn)
            receipts = {}
            if row.get('kind') in {'llm', 'worker'}:
                identity = records.key('retiredUsageNodes', row)
                prior = changes['retiredUsageNodes'].get(identity)
                if identity not in changes['retiredUsageNodes']:
                    prior = records.read_record(self.db, reference, 'retiredUsageNodes', identity, owner=self.owner)
                projected = accounting_projection({'retiredUsageNodes': [prior] if prior is not None else [],
                                                    'nodes': [row]})
                if projected:
                    receipt = projected[0]
                    changes['retiredUsageNodes'][identity] = receipt
                    receipts[(receipt.get('sessionId'), receipt['id'])] = receipt
            nodes = _stored_nodes([row], turns, receipts)
            changes['nodes'][records.key('nodes', row)] = nodes[0] if nodes else None
        fields = {name: value for name, value in self.current.items() if name not in {*records.COLLECTIONS, 'plan'}}
        return records.replace_records(self.db, reference, changes, fields=fields, owner=self.owner)

    def committed(self, reference):
        self.reference = copy.deepcopy(reference)
        # Named canonical writers borrow this whole working set synchronously.
        # If a caller retains any nested alias, keep that exact graph resident.
        # No guessed revision/dirty bit replaces fresh projection of that graph.
        from .cold_display import exclusively_owned
        holder = {'current': self.current}
        self.current = None
        try:
            exclusive = exclusively_owned(holder, 'current')
        finally:
            self.current = holder['current']
        if exclusive:
            for kind in records.COLLECTIONS:
                self.current[kind] = [row for row in self.current[kind] if records.is_active(row)]
        self.admitted = {kind: {records.key(kind, row) for row in self.current[kind]}
                         for kind in records.COLLECTIONS}


@dataclass
class PendingExecution:
    owner: WorkingExecution
    reference: dict

    def committed(self, session):
        from .cold_display import MARKER
        if (getattr(session, '_working_execution', None) is self.owner
                and not dict.__contains__(session, 'execution') and _owner(session) == self.owner.owner):
            dict.setdefault(session, MARKER, {})['execution'] = copy.deepcopy(self.reference)
            self.owner.committed(self.reference)


def working(session):
    """Acquire only at an unexposed ColdRecord boundary; plain callers stay plain."""
    from .cold_display import ColdRecord, MARKER
    if not isinstance(session, ColdRecord) or dict.__contains__(session, 'execution'):
        return None
    reference = dict.get(session, MARKER, {}).get('execution')
    if not reference:
        return None
    owner = getattr(session, '_working_execution', None)
    if owner is None or owner.owner != _owner(session):
        try:
            owner = session._working_execution = WorkingExecution(session, reference)
        except LegacyExecution:
            session['execution']  # Transfer the exact old mutable representation.
            return None
    return owner


def current(session):
    owner = working(session)
    return owner.current if owner is not None else session.get('execution', {})


def exact(session, kind, value):
    owner = working(session)
    identity = records.key(kind, value)
    if owner is not None:
        return owner.record(kind, identity)
    return next((row for row in session.get('execution', {}).get(kind, [])
                 if records.key(kind, row) == identity), None)


def snapshot(session):
    owner = working(session)
    return owner.snapshot() if owner is not None else copy.deepcopy(session.get('execution', {}))


def refresh_usage(session):
    """Explicit complete accounting read; retain only changed derived records.

    Tool progress with unchanged topology does not enter this path. Model usage
    and topology changes still run the existing complete reducer, without
    transferring historical mutation authority into the current working set.
    """
    from .execution import refresh_usage as refresh
    owner = working(session)
    if owner is None:
        return refresh(session.get('execution', {}))
    value = owner.expose()
    before = {kind: {records.key(kind, row): (row.get('turnId'), row.get('aggregateUsage'))
                     for row in value.get(kind, [])} for kind in ('nodes', 'turns')}
    refresh(value)
    owner.current['aggregateUsage'] = value['aggregateUsage']
    for kind in ('nodes', 'turns'):
        resident = {records.key(kind, row) for row in owner.current[kind]}
        for row in value[kind]:
            identity = records.key(kind, row)
            prior_turn, prior_usage = before[kind][identity]
            # Canonical host placements intentionally do not persist display
            # aggregates. Rebuilding that disposable field must not promote all
            # completed placements into the mutable working set.
            usage_owned = kind == 'turns' or not any(row.get(flag) for flag in (
                'liveObservation', 'canonicalHistory', 'nativeHistory'))
            changed = prior_turn != row.get('turnId') or usage_owned and prior_usage != row.get('aggregateUsage')
            if identity not in resident and changed:
                owner.current[kind].append(row)
                owner.admitted[kind].add(identity)
