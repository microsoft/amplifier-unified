"""Read selections for host Work windows; no historical mutation admission.

Only bounded slices and exact already-admitted identities can read node bodies.
This object is local to one synchronous read. It is not persisted, cached across
requests, a mutable list, or an implicit complete-history iterator.
"""
from . import execution_records as records
from .cold_display import MARKER


def selection(session):
    reference = dict.get(session, MARKER, {}).get('execution')
    if not dict.__contains__(session, 'execution') and reference and reference.get('executionProjection') == 2:
        return ExecutionWindow(session, reference)
    return None


class ExecutionWindow:
    def __init__(self, session, reference):
        self.session, self.db, self.reference = session, session.db, reference
        self.owner = (session['id'],)
        self.identity = ('execution-records', session['id'])
        self.read = records._bounded_reader(self.db, 4 * 1024 * 1024)
        self.metadata = records.selection_metadata(self.db, reference, 'nodes', owner=self.owner, read=self.read)
        working = getattr(session, '_working_execution', None)
        self.fields = working.current if working is not None else self.metadata['fields']
        self.current = {records.key('nodes', row): row for row in working.current['nodes']} if working else {}
        self.removed = working.admitted['nodes'] - self.current.keys() if working else set()
        self.known = {}
        self.appended = [identity for identity in self.current if self._stored(identity) is None]
        self.deleted_positions = sorted(position for identity in self.removed
            if (entry := self._stored(identity)) is not None
            and (position := records.rank(self.db, reference, 'nodes', entry['ordinal'], owner=self.owner, read=self.read)) is not None)

    def _stored(self, identity):
        if identity not in self.known:
            self.known[identity] = records.read_entry(self.db, self.reference, 'nodes', identity, owner=self.owner, read=self.read)
        return self.known[identity]

    def __len__(self):
        return self.metadata['count'] - len(self.deleted_positions) + len(self.appended)

    def __iter__(self):
        raise TypeError('Select a bounded execution window or request an explicit complete snapshot.')

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, end, step = index.indices(len(self))
            if step != 1:
                raise ValueError('Choose a contiguous execution window.')
            return [entry['value'] for entry in self.entries(start, end)]
        if type(index) is not int:
            raise TypeError('Execution positions must be integers.')
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return self.entries(index, index + 1)[0]['value']

    def entries(self, start, end):
        if end - start > records.PAGE_ROWS or not 0 <= start <= end <= len(self):
            raise ValueError('Choose a bounded execution window.')
        result = []
        live_count = self.metadata['count'] - len(self.deleted_positions)
        stop = min(end, live_count)
        physical = start
        for deleted in self.deleted_positions:
            if deleted <= physical:
                physical += 1
            else:
                break
        while len(result) < stop - start:
            limit = min(records.PAGE_ROWS, stop - start - len(result) + len(self.deleted_positions))
            rows = records.read_window(self.db, self.reference, 'nodes', start=physical,
                                       limit=limit, owner=self.owner, read=self.read)['rows']
            if not rows:
                raise ValueError('Saved execution positions changed during selection.')
            physical += len(rows)
            for entry in rows:
                identity = records.key('nodes', entry['value'])
                if identity not in self.removed:
                    result.append({'ordinal': entry['ordinal'], 'value': self.current.get(identity, entry['value'])})
                    if len(result) == stop - start:
                        break
        for index in range(max(0, start - live_count), max(0, end - live_count)):
            result.append({'ordinal': self.metadata['next'] + index, 'value': self.current[self.appended[index]]})
        return result

    def exact(self, identity):
        key = records.key('nodes', {'id': identity})
        if key in self.removed:
            return None
        if key in self.appended:
            return {'ordinal': self.metadata['next'] + self.appended.index(key), 'value': self.current[key]}
        entry = self._stored(key)
        return {**entry, 'value': self.current.get(key, entry['value'])} if entry is not None else None

    def position(self, identity):
        entry = self.exact(identity)
        if entry is None:
            return None
        key = records.key('nodes', {'id': identity})
        if key in self.appended:
            return self.metadata['count'] - len(self.deleted_positions) + self.appended.index(key)
        position = records.rank(self.db, self.reference, 'nodes', entry['ordinal'], owner=self.owner, read=self.read)
        return position - sum(deleted < position for deleted in self.deleted_positions)

    def tree(self):
        turns = records.selection_metadata(self.db, self.reference, 'turns', owner=self.owner, read=self.read)
        selected = records.read_window(self.db, self.reference, 'turns',
                                       start=max(0, turns['count'] - 20), limit=20, owner=self.owner, read=self.read)['rows']
        values = {row['value']['id']: row['value'] for row in selected}
        working = getattr(self.session, '_working_execution', None)
        if working is not None:
            present = {row['id'] for row in working.current['turns']}
            for identity in working.admitted['turns'] - {records.key('turns', {'id': value}) for value in present}:
                import json
                values.pop(json.loads(identity)[0], None)
            for row in working.current['turns']:
                values[row['id']] = row
        return {**{key: value for key, value in self.fields.items() if key not in records.COLLECTIONS},
                'nodes': self, 'turns': list(values.values())[-20:]}
