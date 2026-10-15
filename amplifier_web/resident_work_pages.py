"""Explicit bounded resident Work pages; no native history or body cache."""
import base64
from collections import OrderedDict
import hashlib
import hmac
import json

from .browser_detail import NODE_LIMIT, SUMMARY_LIMIT, compact, detail_version, owned_nodes, work_segments
from .cold_display import raw

MAX_GROUPS = 8
MAX_RANGES = 8
MAX_STEPS = NODE_LIMIT * MAX_RANGES


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


class ResidentWorkPages:
    def __init__(self, windows):
        self.windows = windows
        self.groups = OrderedDict()
        self.interests = set()

    def _client(self):
        clients = getattr(self.windows.service, 'clients', None)
        return clients.current.get() if clients is not None else None

    def _key(self, session, group):
        return self._client(), session['id'], group

    def _source(self, session, start, end):
        source = self.windows.live_view(session)
        nodes = raw(session).get('execution', {}).get('nodes', [])
        source['execution']['nodes'] = [row for row in nodes[start:end]
            if row.get('liveObservation') or not row.get('canonicalHistory') and not row.get('nativeHistory')]
        return source

    def _stamp(self, session):
        from .work_windows import source_identity
        source = self.windows.live_view(session)
        # Stable resident positions and bounded grouping facts, never node bodies.
        facts = [id(session), id(raw(session).get('execution', {}).get('nodes')),
            source_identity(session),
            [{key: row.get(key) for key in ('id', 'createdAt', 'timestampKnown')}
             for row in source['messages']],
            [{key: row.get(key) for key in ('id', 'anchorMessageId', 'startedAt')}
             for row in source['execution']['turns']]]
        return hashlib.sha256(_encode(facts)).hexdigest()

    def _owner_stamp(self, session):
        from .work_windows import source_identity
        return hashlib.sha256(_encode([id(session), source_identity(session)])).hexdigest()

    def _token(self, value):
        body = _encode(value)
        return base64.urlsafe_b64encode(hmac.digest(self.windows.key, body, 'sha256') + body).decode()

    def _parse(self, token):
        try:
            if not isinstance(token, str) or not 0 < len(token) <= 4096:
                raise ValueError()
            value = base64.b64decode(token, altchars=b'-_', validate=True)
            if not hmac.compare_digest(value[:32], hmac.digest(self.windows.key, value[32:], 'sha256')):
                raise ValueError()
            return json.loads(value[32:])
        except (ValueError, TypeError):
            raise ValueError('This Work cursor expired. Close and reopen its steps.') from None

    @staticmethod
    def _rows(source, group):
        rows = owned_nodes(source)
        anchors, segments = work_segments(source, rows,
            select_groups=lambda values: [row for row in values if row['id'] == group])
        selected = [row for row in rows if row['id'] in anchors
            and str(row.get('turnId')) + '@' + (anchors[row['id']] or 'start') == group]
        return selected, anchors, next(iter(segments), None)

    def _entry(self, session, group, *, reconcile=False):
        entry = self.groups.get(self._key(session, group))
        if (entry is None or entry['owner'] != self._owner_stamp(session)
                or not reconcile and entry['stamp'] != self._stamp(session)):
            raise ValueError('This Work window expired. Close and reopen its steps.')
        self.groups.move_to_end(self._key(session, group))
        return entry

    def _compact(self, session, row, anchor, entry):
        item = compact(row, session['id'], 'nodes', SUMMARY_LIMIT)
        item.update(anchorMessageId=anchor, detailVersion=str(detail_version(row)))
        for key, value in item.items():
            if key.endswith('Detail') and isinstance(value, dict) and value.get('part') == 'nodes':
                item[key] = {**value, 'windowKey': entry['key']}
        return item

    def read(self, session, group, before=None, revision=None):
        if not isinstance(group, str) or not 0 < len(group) <= 1024:
            raise ValueError('Choose a current Work group.')
        nodes = raw(session).get('execution', {}).get('nodes', [])
        stamp, fence = self._stamp(session), self.windows._fence(session['id'])
        if before is None:
            start, end, within = max(0, len(nodes) - NODE_LIMIT), len(nodes), None
            entry = {'stamp': stamp, 'key': 'resident-work:' + self._token([self._client(), session['id'], group, stamp]),
                     'owner': self._owner_stamp(session), 'ranges': {}, 'ids': {}, 'positions': {}, 'revision': None}
        else:
            cursor = self._parse(before)
            entry = self._entry(session, group)
            if (not isinstance(cursor, dict) or cursor.get('owner') != session['id'] or cursor.get('client') != self._client()
                    or cursor.get('group') != group or cursor.get('stamp') != stamp
                    or cursor.get('fence') != fence):
                raise ValueError('This Work changed. Close and reopen its latest steps.')
            start, end, within = cursor.get('start'), cursor.get('end'), cursor.get('within')
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start <= end <= len(nodes) or end - start > NODE_LIMIT):
                raise ValueError('Invalid resident Work range.')
        rows, anchors, segment = self._rows(self._source(session, start, end), group)
        if before is None:
            if segment is None:
                raise ValueError('This Work is no longer available. Refresh this conversation.')
            entry['revision'] = segment['detailRevision']
        if revision is not None and revision != entry['revision']:
            raise ValueError('This Work changed. Reload its latest steps.')
        stop = len(rows)
        if within is not None:
            stop = next((i for i, row in enumerate(rows) if row['id'] == within), None)
            if stop is None:
                raise ValueError('This Work changed. Close and reopen its steps.')
        begin = max(0, stop - NODE_LIMIT)
        selected = rows[begin:stop]
        scope = (start, end)
        ranges = set(entry['ranges']) | ({scope} if selected else set())
        identities = set(entry['ids']) | {row['id'] for row in selected}
        if len(ranges) > MAX_RANGES or len(identities) > MAX_STEPS:
            raise ValueError('This open Work window reached its page limit. Close and reopen its steps to release it.')
        if selected:
            positions = {row['id']: index for index, row in enumerate(nodes[start:end], start)}
            admitted = {row['id']: positions.get(row['id'], positions.get(row.get('parentId')))
                        for row in selected}
            if any(index is None for index in admitted.values()):
                raise ValueError('This Work changed. Close and reopen its steps.')
            entry['ranges'][scope] = True
            entry['ids'].update((row['id'], scope) for row in selected)
            entry['positions'].update(admitted)
        self.groups.pop(self._key(session, group), None)
        self.groups[self._key(session, group)] = entry
        while len(self.groups) > MAX_GROUPS:
            self.groups.popitem(last=False)
        if begin:
            next_start, next_end, next_within = start, end, selected[0]['id']
        else:
            next_start, next_end, next_within = max(0, start - NODE_LIMIT), start, None
        entry['older'] = (next_start, next_end, next_within) if begin or start else None
        cursor = self._cursor(session, group, entry)
        return {'items': [self._compact(session, row, anchors.get(row['id']), entry) for row in selected],
            'mode': 'cursor', 'offset': None, 'total': None, 'before': cursor,
            'hasEarlier': cursor is not None, 'group': group, 'revision': entry['revision'],
            'coverage': {'kind': 'resident-live', 'historyComplete': False,
                         'sourceStart': start, 'sourceEnd': end}}

    def _cursor(self, session, group, entry):
        if entry.get('older') is None:
            return None
        start, end, within = entry['older']
        return self._token({'client': self._client(), 'owner': session['id'], 'group': group, 'stamp': entry['stamp'],
            'fence': self.windows._fence(session['id']), 'start': start, 'end': end, 'within': within})

    def sync(self, session, group, known):
        if (not isinstance(known, dict) or len(known) > MAX_STEPS
                or not all(isinstance(key, str) and isinstance(value, str) for key, value in known.items())):
            raise ValueError('Choose a bounded open Work window.')
        entry = self._entry(session, group, reconcile=True)
        if not set(known) <= entry['ids'].keys():
            raise ValueError('This Work window expired. Close and reopen its steps.')
        nodes = raw(session).get('execution', {}).get('nodes', [])
        tail_scope = (max(0, len(nodes) - NODE_LIMIT), len(nodes))
        scopes = set(entry['ids'][key] for key in known) | {tail_scope}
        source = self.windows.live_view(session)
        positions = {}
        for start, end in sorted(scopes):
            if end > len(nodes):
                raise ValueError('This Work changed. Close and reopen its steps.')
            positions.update((i, row) for i, row in enumerate(nodes[start:end], start)
                if row.get('liveObservation') or not row.get('canonicalHistory') and not row.get('nativeHistory'))
        source['execution']['nodes'] = [positions[i] for i in sorted(positions)]
        rows, anchors, segment = self._rows(source, group)
        latest, _, latest_segment = self._rows(self._source(session, *tail_scope), group)
        latest = latest[-NODE_LIMIT:]
        desired = set(known) | {row['id'] for row in latest}
        selected = [row for row in rows if row['id'] in desired]
        # Appends slide the tail by one position. Coalesce overlapping physical
        # ranges instead of spending a new page receipt for each small update.
        merged = []
        for start, end in sorted(scopes):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        normalized = [(start, min(start + NODE_LIMIT, end))
                      for low, end in merged for start in range(low, end, NODE_LIMIT)]
        by_id = {row['id']: index for index, row in positions.items()}
        ids = {}
        current_positions = {}
        for row in selected:
            # Returned delegate observations use their admitted worker's range;
            # their actual message field remains owned by the message reader.
            index = by_id.get(row['id'], by_id.get(row.get('parentId')))
            scope = next((value for value in normalized if index is not None and value[0] <= index < value[1]), None)
            if scope is None:
                raise ValueError('This Work changed. Close and reopen its steps.')
            ids[row['id']] = scope
            current_positions[row['id']] = index
        # A new message or canonical reconciliation may replace the list without
        # moving these steps. Revalidate only the admitted ranges: the same IDs
        # must still occupy their accepted positions in this exact group. Never
        # search the historical graph to recover a moved or replaced window.
        if any(current_positions.get(identity) != entry['positions'][identity] for identity in known):
            raise ValueError('This Work changed. Close and reopen its steps.')
        ranges = dict.fromkeys(ids.values(), True)
        if len(selected) > MAX_STEPS or len(ranges) > MAX_RANGES:
            raise ValueError('This open Work window reached its page limit. Close and reopen its steps to release it.')
        entry['ids'], entry['ranges'], entry['positions'] = ids, ranges, current_positions
        entry['stamp'] = self._stamp(session)
        entry['revision'] = (latest_segment or segment or {}).get('detailRevision', entry['revision'])
        older = self._cursor(session, group, entry)
        return {'items': [self._compact(session, row, anchors.get(row['id']), entry)
                         for row in selected if known.get(row['id']) != str(detail_version(row))],
            'order': [row['id'] for row in selected], 'offset': None, 'total': None, 'before': older,
            'group': group, 'revision': entry['revision'],
            'coverage': {'kind': 'resident-live', 'historyComplete': False}}

    def read_text(self, session, args):
        from .browser_detail import read_text
        reference = str(args.get('windowKey', ''))
        entry = next((value for (client, identity, _), value in self.groups.items()
                      if client == self._client() and identity == session['id'] and value['key'] == reference), None)
        if entry is None or entry['stamp'] != self._stamp(session) or args.get('id') not in entry['ids']:
            raise ValueError('This Work field expired. Close and reopen its steps.')
        return read_text(self._source(session, *entry['ids'][args['id']]), args)

    def retain_interest(self, identities):
        departed = self.interests - identities
        self.interests = set(identities)
        for key in tuple(self.groups):
            if key[1] in departed:
                self.groups.pop(key, None)
