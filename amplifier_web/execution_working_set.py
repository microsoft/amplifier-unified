"""Canonical current mutation separate from detached historical execution reads.

Only ColdRecord-owned, unexposed durable bodies enter this path. Ordinary
mutable full dictionaries keep their existing save semantics. No recursive
mutation wrappers or unverified dirty flags are involved: every resident current
record is projected afresh, including nested aliases, on its owner's save.
"""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
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
    def __init__(self, session, reference, *, legacy=None):
        self.db = session.db
        self.owner = _owner(session)
        self.reference = copy.deepcopy(reference)
        self._legacy_read = None
        if reference.get('executionProjection') == 2:
            self.current = records.read_active(self.db, reference, owner=self.owner)
        else:
            # One legacy ownership transition, not a repeated current update.
            value = _stored(self.db, reference) if legacy is None else legacy
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
            if legacy is not None:
                self.current = copy.deepcopy(self.current)
        self.admitted = {kind: {records.key(kind, row) for row in self.current[kind]}
                         for kind in records.COLLECTIONS}
        self.usage_prefix = None
        self._usage_stage = None

    def _stored(self, *, detach=True):
        if self._legacy_read is not None and self._legacy_read[0] == self.reference:
            # Each public read used to receive an independent JSON value.
            # Only private read-only projections may borrow this verified input.
            return copy.deepcopy(self._legacy_read[1]) if detach else self._legacy_read[1]
        return _stored(self.db, self.reference)

    def record(self, kind, identity, *, derived=False):
        present = next((row for row in self.current[kind] if records.key(kind, row) == identity), None)
        if present is not None:
            return present
        if self.reference.get('executionProjection') == 2:
            value = records.read_record(self.db, self.reference, kind, identity, owner=self.owner)
        else:
            value = next((row for row in self._stored().get(kind, [])
                          if records.key(kind, row) == identity), None)
        if value is not None:
            if kind in ('nodes', 'turns') and not derived:
                # Absence is an ordinary append lookup. Only obtaining a real
                # historical mutable row invalidates the earlier contribution.
                self.usage_prefix = None
            self.current[kind].append(value)
            self.admitted[kind].add(identity)
        return value

    def _complete(self, *, detach_current=False, collections=records.COLLECTIONS, borrow_stored=False):
        current = copy.deepcopy(self.current) if detach_current else self.current
        value = {name: item for name, item in current.items() if name not in records.COLLECTIONS}
        if self.reference.get('executionProjection') == 2 and collections != records.COLLECTIONS:
            stored = {kind: records.read_collection(self.db, self.reference, kind, owner=self.owner)
                      for kind in collections}
        else:
            stored = self._stored(detach=not borrow_stored)
        for kind in collections:
            changes = {records.key(kind, row): row for row in current[kind]}
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
        # Durable JSON reads are already detached. Copy only the mutable current
        # overlay, preserving its internal aliases, not the completed ledger.
        return self._complete(detach_current=True)

    def _native_activity_input(self):
        # Native selection/rollup only read rows and replace the returned outer
        # aggregate. No historical row escapes to a mutable caller or owner.
        # Without a matching operation-local binding, the normal read detaches.
        return self._complete(detach_current=True, borrow_stored=True)

    def usage_input(self):
        """Complete ordered accounting inputs, keeping current aliases live."""
        kinds = ('nodes', 'turns')
        if self.reference.get('executionProjection') != 2 or any(
                type(row) is not dict or type(row.get('id')) is not str or not row['id']
                for kind in kinds for row in self.current[kind]):
            return self._complete(collections=kinds), False
        stored = records.read_usage(self.db, self.reference, owner=self.owner)
        value = {name: item for name, item in self.current.items() if name not in records.COLLECTIONS}
        for kind in kinds:
            # Node and turn identities are their validated IDs. Avoid encoding
            # every immutable ID again on each usage update; receipt identities
            # remain separately keyed by session and never enter this reader.
            changes = {row['id']: row for row in self.current[kind]}
            admitted = {json.loads(identity)[0] for identity in self.admitted[kind]}
            rows = []
            for row in stored[kind]:
                identity = row['id']
                if identity in changes or identity not in admitted:
                    rows.append(changes.pop(identity, row))
            rows.extend(changes.values())
            value[kind] = rows
        return value, True

    def detached(self):
        """Freeze the current overlay while leaving its immutable history cold."""
        value = copy.copy(self)
        value.reference = copy.deepcopy(self.reference)
        value.current = copy.deepcopy(self.current)
        value.admitted = {kind: set(identities) for kind, identities in self.admitted.items()}
        value.usage_prefix = None
        value._usage_stage = None
        value._legacy_read = None
        return value

    def expose(self):
        """Transfer to the ordinary complete mutable contract without losing aliases."""
        return self._complete()

    def stage(self):
        """Project resident records; immutable historical pages are not revisited."""
        from .session_projection import accounting_projection, _stored_nodes
        from .execution_usage_prefix import preserves_contribution, Rebuild
        preserves_usage = self.usage_prefix is not None
        reference = self.reference
        if reference.get('executionProjection') != 2:
            from .session_projection import stored_execution
            reference = records.import_tree(self.db, self.owner, stored_execution(self._stored(detach=False)))
        changes = {kind: {identity: None for identity in self.admitted[kind]
                          - {records.key(kind, row) for row in self.current[kind]}}
                   for kind in records.COLLECTIONS}
        for row in self.current['retiredUsageNodes']:
            changes['retiredUsageNodes'][records.key('retiredUsageNodes', row)] = row
        turns = {row['id'] for row in self.current['turns']}
        for row in self.current['turns']:
            changes['turns'][records.key('turns', row)] = (
                row if not row.get('nativeHistory') and not row.get('canonicalHistory') else None)
            if not records.is_active(row) and changes['turns'][records.key('turns', row)] is None:
                preserves_usage = False
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
            if preserves_usage and not records.is_active(row):
                try:
                    preserves_usage = preserves_contribution(row, nodes[0] if nodes else None)
                except Rebuild:
                    preserves_usage = False
        fields = {name: value for name, value in self.current.items() if name not in {*records.COLLECTIONS, 'plan'}}
        selected = records.replace_records(self.db, reference, changes, fields=fields, owner=self.owner)
        self._usage_stage = (selected, preserves_usage)
        return selected

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
            if self._usage_stage != (reference, True):
                self.usage_prefix = None
            if self.usage_prefix is not None:
                from .execution_usage_prefix import Rebuild
                try:
                    self.usage_prefix.retired(self.current)
                except Rebuild:
                    self.usage_prefix = None
            for kind in records.COLLECTIONS:
                self.current[kind] = [row for row in self.current[kind] if records.is_active(row)]
        self.admitted = {kind: {records.key(kind, row) for row in self.current[kind]}
                         for kind in records.COLLECTIONS}
        self._usage_stage = None


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


def working(session, *, _legacy=None):
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
            owner = session._working_execution = WorkingExecution(session, reference, legacy=_legacy)
        except LegacyExecution:
            session['execution']  # Transfer the exact old mutable representation.
            return None
    return owner


@contextmanager
def native_refresh(session):
    """Share one verified v1 read through synchronous native merge/publication.

    The caller holds the session writer lock and must not await in this scope.
    No historical read binding survives the operation or enters detached views.
    Durable selection still changes only after the existing successful commit.
    """
    from .cold_display import ColdRecord, MARKER
    reference = (dict.get(session, MARKER, {}).get('execution')
                 if isinstance(session, ColdRecord) and not dict.__contains__(session, 'execution') else None)
    if reference is None or reference.get('executionProjection') != 1:
        yield
        return
    value = _stored(session.db, reference)
    owner = working(session, _legacy=value)
    if owner is None:
        yield
        return
    previous = owner._legacy_read
    owner._legacy_read = (copy.deepcopy(reference), value)
    try:
        yield
    finally:
        owner._legacy_read = previous


def current(session):
    owner = working(session)
    return owner.current if owner is not None else session.get('execution', {})


def exact(session, kind, value):
    owner = working(session)
    wanted = value.get('id') if owner is None and type(value) is dict else None
    identity = records.key(kind, value)
    if owner is not None:
        return owner.record(kind, identity)
    rows = session.get('execution', {}).get(kind, [])
    if type(kind) is str and kind in ('nodes', 'turns') and type(wanted) is str:
        # These keys encode only the ID. Keep the ordered, borrowed-row lookup
        # without JSON-encoding every ordinary row; other values retain validation.
        for row in rows:
            if type(row) is dict and type(row.get('id')) is str and row['id']:
                if row['id'] == wanted:
                    return row
            elif records.key(kind, row) == identity:
                return row
        return None
    return next((row for row in rows
                 if records.key(kind, row) == identity), None)


def turns_for_inputs(session, identities):
    """All exact input aliases, including inactive historical display turns.

    This compatibility join reads only turn records. An input ID need not equal
    a display-turn ID, and one input may have several retained display turns.
    """
    if not identities:
        return []
    owner = working(session)
    if owner is None:
        return [row for row in session.get('execution', {}).get('turns', [])
                if row.get('inputId', row['id']) in identities]
    rows = (records.read_collection(owner.db, owner.reference, 'turns', owner=owner.owner)
            if owner.reference.get('executionProjection') == 2 else _stored(owner.db, owner.reference).get('turns', []))
    current = {records.key('turns', row): row for row in owner.current['turns']}
    result, seen = [], set()
    for stored in rows:
        identity = records.key('turns', stored)
        if identity in owner.admitted['turns'] and identity not in current:
            continue
        row = current.get(identity, stored)
        seen.add(identity)
        if row.get('inputId', row['id']) in identities:
            if identity not in current:
                owner.current['turns'].append(row)
                owner.admitted['turns'].add(identity)
            result.append(row)
    result.extend(row for identity, row in current.items() if identity not in seen
                  and row.get('inputId', row['id']) in identities)
    return result


def recent_turns(session, limit=80):
    """Bounded ordered input for duration hints, with current replacements."""
    owner = working(session)
    if owner is None:
        return session.get('execution', {}).get('turns', [])[-limit:]
    if owner.reference.get('executionProjection') != 2:
        return owner.snapshot().get('turns', [])[-limit:]
    current = {records.key('turns', row): row for row in owner.current['turns']}
    deleted = owner.admitted['turns'] - current.keys()
    metadata = records.selection_metadata(owner.db, owner.reference, 'turns', owner=owner.owner)
    start = max(0, metadata['count'] - limit - len(deleted))
    selected = []
    for position in range(start, metadata['count'], records.PAGE_ROWS):
        entries = records.read_window(owner.db, owner.reference, 'turns', start=position,
                                      limit=min(records.PAGE_ROWS, metadata['count'] - position), owner=owner.owner)['rows']
        for entry in entries:
            identity = records.key('turns', entry['value'])
            if identity not in deleted:
                selected.append(current.get(identity, entry['value']))
    for identity, row in current.items():
        if records.read_entry(owner.db, owner.reference, 'turns', identity, owner=owner.owner) is None:
            selected.append(row)
    return selected[-limit:]


def snapshot(session):
    owner = working(session)
    return owner.snapshot() if owner is not None else copy.deepcopy(session.get('execution', {}))


def readonly(session):
    """Complete nonmutating consumer; ordinary mutable callers keep their form."""
    from .cold_display import ColdRecord, MARKER, load
    if isinstance(session, ColdRecord) and not dict.__contains__(session, 'execution'):
        owner = getattr(session, '_working_execution', None)
        if owner is not None:
            return owner.snapshot()
        reference = dict.get(session, MARKER, {}).get('execution')
        return load(session.db, reference) if reference is not None else {}
    return session.get('execution', {})


def refresh_usage(session):
    """Continue exact ordered totals, rebuilding for earlier/unsupported edits."""
    from .execution import refresh_usage as refresh, _reduce_usage
    from .execution_usage_prefix import UsagePrefix, Rebuild
    owner = working(session)
    if owner is None:
        return refresh(session.get('execution', {}))
    if owner.usage_prefix is not None:
        try:
            owner.usage_prefix.refresh(owner)
            return
        except Rebuild:
            owner.usage_prefix = None
    value, indexed = owner.usage_input()
    def identity(kind, row):
        return row['id'] if indexed else records.key(kind, row)
    before = {kind: {identity(kind, row): (row.get('turnId'), row.get('aggregateUsage'))
                     for row in value.get(kind, [])} for kind in ('nodes', 'turns')}
    groups = _reduce_usage(value)
    owner.current['aggregateUsage'] = value['aggregateUsage']
    for kind in ('nodes', 'turns'):
        resident = {identity(kind, row) for row in owner.current[kind]}
        for row in value[kind]:
            row_id = identity(kind, row)
            prior_turn, prior_usage = before[kind][row_id]
            # Canonical host placements intentionally do not persist display
            # aggregates. Rebuilding that disposable field must not promote all
            # completed placements into the mutable working set.
            usage_owned = kind == 'turns' or not any(row.get(flag) for flag in (
                'liveObservation', 'canonicalHistory', 'nativeHistory'))
            changed = prior_turn != row.get('turnId') or usage_owned and prior_usage != row.get('aggregateUsage')
            if row_id not in resident and changed:
                record_key = records.key(kind, row)
                if indexed:
                    full = owner.record(kind, record_key)
                    if full is None:
                        raise ValueError('A changed historical execution record is unavailable.')
                    if prior_turn != row.get('turnId'):
                        full['turnId'] = row['turnId']
                    if prior_usage != row.get('aggregateUsage'):
                        full['aggregateUsage'] = row['aggregateUsage']
                else:
                    owner.current[kind].append(row)
                    owner.admitted[kind].add(record_key)
    if indexed:
        try:
            owner.usage_prefix = UsagePrefix.build(owner, value, groups)
        except Rebuild:
            owner.usage_prefix = None
