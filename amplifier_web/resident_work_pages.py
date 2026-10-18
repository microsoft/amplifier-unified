"""Explicit bounded resident Work pages; no native history or body cache."""
import asyncio
import base64
from collections import OrderedDict
from copy import deepcopy
import hashlib
import hmac
import json
import secrets

from .browser_detail import NODE_LIMIT, SUMMARY_LIMIT, compact, detail_version, owned_nodes, work_segments
from .cold_display import raw

MAX_GROUPS = 8
MAX_RANGES = 8
MAX_STEPS = NODE_LIMIT * MAX_RANGES
CANONICAL_FIELDS = ('input', 'output', 'error', 'request', 'response')


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
        if (self.windows.closed or self.windows.service.closed
                or self.windows._owner(session['id']) is not session
                or entry is None or entry['owner'] != self._owner_stamp(session)
                or not reconcile and entry['stamp'] != self._stamp(session)):
            raise ValueError('This Work window expired. Close and reopen its steps.')
        self.groups.move_to_end(self._key(session, group))
        return entry

    def revision_for(self, session, group, base_revision):
        """Wrap the live projection's base revision; never initiate a proof.

        WorkWindows calls this on projected live groups, and explicit reads use
        the same bounded status. An absent proof preserves the base revision.
        Physical source/index validation remains the binding helper's job.
        """
        state = self.windows._proof_state(session['id'], session)
        if state is None:
            return base_revision
        return hashlib.sha256(_encode([group, base_revision, state])).hexdigest()

    def _sync_revision(self, session, group, entry):
        """Pin every admitted physical row, not only the current100-node tail."""
        nodes = raw(session).get('execution', {}).get('nodes', [])
        positions = entry.get('sync_positions')
        if positions is None:
            positions = sorted(set(entry['positions'].values()) |
                               set(range(max(0, len(nodes) - NODE_LIMIT), len(nodes))))
        if len(positions) > MAX_STEPS or any(not 0 <= i < len(nodes) for i in positions):
            raise ValueError('This Work window expired.')
        facts = [(i, str(detail_version(nodes[i]))) for i in positions]
        _, _, latest = self._rows(self._source(session, max(0, len(nodes) - NODE_LIMIT), len(nodes)), group)
        return hashlib.sha256(_encode([self._stamp(session), self.windows._fence(session['id']),
            self.revision_for(session, group, (latest or {}).get('detailRevision')),
            facts])).hexdigest()

    def _render(self, session, row, anchor, entry, *, proof_id=None):
        # Never promote a resident _eventFields alias as canonical authority.
        # Inline text remains readable; canonical references come only from the
        # complete-proof matcher against the actual row at its accepted position.
        inline = {key: value for key, value in row.items() if key != '_eventFields'}
        item = compact(inline, session['id'], 'nodes', SUMMARY_LIMIT)
        fields = {}
        for name, value in item.items():
            if name.endswith('Detail') and isinstance(value, dict) and value.get('part') == 'nodes':
                fields[value['field']] = {'kind': 'inline', 'detail': deepcopy(value)}
        nodes = raw(session).get('execution', {}).get('nodes', [])
        position = entry['positions'][row['id']]
        if not 0 <= position < len(nodes):
            raise ValueError('This Work field expired. Close and reopen its steps.')
        actual = nodes[position]
        if actual.get('id') not in (row['id'], row.get('parentId')):
            raise ValueError('This Work field changed. Close and reopen its steps.')
        bound_id, admitted_proof = None, None
        # Synthetic returned-message observations are not root invocations.
        if actual.get('id') == row['id']:
            admitted_proof, bound = self.windows.live_binding(session, actual, proof_id=proof_id)
            if admitted_proof is not None and bound is not None:
                bound_id = bound['id']  # RAW occurrence identity, never resident ID.
                for field in CANONICAL_FIELDS:
                    reference = bound.get('_eventFields', {}).get(field)
                    detail = bound.get(field + 'Detail')
                    if reference and isinstance(detail, dict) and detail.get('digest'):
                        fields[field] = {'kind': 'canonical',
                            'reference': hashlib.sha256(_encode(reference)).hexdigest(),
                            'detail': {**deepcopy(detail), 'sessionId': session['id'],
                                       'part': 'nodes', 'id': row['id'], 'field': field}}
        lifecycle = {key: [key in actual, actual.get(key)] for key in
            ('rootSessionId', 'sessionId', 'id', 'kind', 'turnId', 'parentId',
             'revision', 'phase', 'status', 'startedAt', 'endedAt')}
        metadata = (detail_version(actual), detail_version(row) if actual.get('id') != row['id'] else None)
        receipt = {'metadata': str(detail_version(metadata)),
                   'lifecycle': hashlib.sha256(_encode(lifecycle)).hexdigest(), 'proof': admitted_proof,
                   'bound': bound_id, 'fields': fields}
        version = str(detail_version(receipt))
        receipt['version'] = version
        item.update(anchorMessageId=anchor, detailVersion=version)
        for field, record in fields.items():
            item[field + 'Detail'] = {**record['detail'], 'windowKey': entry['key'],
                                     'detailVersion': version}
        return item, receipt

    def _compact(self, session, row, anchor, entry):
        item, receipt = self._render(session, row, anchor, entry)
        entry['receipts'][row['id']] = receipt
        return item

    @staticmethod
    def _copy_entry(entry):
        return {**entry, **{key: dict(entry[key]) for key in ('ranges', 'ids', 'positions', 'receipts')}}

    @staticmethod
    def _normalize(entry):
        # Coalesce sliding latest-page reads without losing older admissions.
        merged = []
        for start, end in sorted(set(entry['ids'].values())):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        ranges = [(start, min(start + NODE_LIMIT, end))
                  for low, end in merged for start in range(low, end, NODE_LIMIT)]
        if len(ranges) > MAX_RANGES or len(entry['ids']) > MAX_STEPS:
            raise ValueError('This open Work window reached its page limit. Close and reopen its steps to release it.')
        entry['ranges'] = dict.fromkeys(ranges, True)
        entry['ids'] = {identity: next(scope for scope in ranges if scope[0] <= entry['positions'][identity] < scope[1])
                        for identity in entry['ids']}

    def read(self, session, group, before=None, revision=None, *, preserve=False):
        if not isinstance(group, str) or not 0 < len(group) <= 1024:
            raise ValueError('Choose a current Work group.')
        if (self.windows.closed or self.windows.service.closed
                or self.windows._owner(session['id']) is not session):
            raise ValueError('This Work window expired. Close and reopen its steps.')
        nodes = raw(session).get('execution', {}).get('nodes', [])
        stamp, fence = self._stamp(session), self.windows._fence(session['id'])
        retained = None
        sync_revision = None
        if before is None:
            start, end, within = max(0, len(nodes) - NODE_LIMIT), len(nodes), None
            if preserve and self._key(session, group) in self.groups:
                retained = self._entry(session, group, reconcile=True)
                if revision is not None and revision == retained.get('sync_revision'):
                    sync_revision = self._sync_revision(session, group, retained)
                    if revision != sync_revision:
                        raise ValueError('This Work changed during paged sync.')
                entry = self._copy_entry(retained)
                entry.update(stamp=stamp, fence=fence)
            else:
                nonce = secrets.token_hex(16)
                entry = {'stamp': stamp, 'fence': fence, 'nonce': nonce,
                         'key': 'resident-work:' + self._token(
                             [self._client(), session['id'], group, stamp, nonce]),
                         'owner': self._owner_stamp(session), 'ranges': {}, 'ids': {},
                         'positions': {}, 'receipts': {}, 'revision': None, 'base_revision': None}
        else:
            cursor = self._parse(before)
            retained = self._entry(session, group)
            if revision is not None and revision == retained.get('sync_revision'):
                sync_revision = self._sync_revision(session, group, retained)
                if revision != sync_revision:
                    raise ValueError('This Work changed during paged sync.')
            entry = self._copy_entry(retained)
            if (not isinstance(cursor, dict) or cursor.get('owner') != session['id'] or cursor.get('client') != self._client()
                    or cursor.get('group') != group or cursor.get('stamp') != stamp
                    or cursor.get('nonce') != entry['nonce']
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
            entry['base_revision'] = segment['detailRevision']
        stop = len(rows)
        if within is not None:
            stop = next((i for i, row in enumerate(rows) if row['id'] == within), None)
            if stop is None:
                raise ValueError('This Work changed. Close and reopen its steps.')
        begin = max(0, stop - NODE_LIMIT)
        selected = rows[begin:stop]
        scope = (start, end)
        if selected:
            candidates = [(index, row) for index, row in enumerate(nodes[start:end], start)
                if row.get('liveObservation') or not row.get('canonicalHistory') and not row.get('nativeHistory')]
            positions = {row['id']: index for index, row in candidates}
            if len(positions) != len(candidates):
                raise ValueError('This Work changed. Close and reopen its steps.')
            admitted = {row['id']: positions.get(row['id'], positions.get(row.get('parentId')))
                        for row in selected}
            if any(index is None for index in admitted.values()):
                raise ValueError('This Work changed. Close and reopen its steps.')
            entry['ranges'][scope] = True
            entry['ids'].update((row['id'], scope) for row in selected)
            entry['positions'].update(admitted)
        self._normalize(entry)
        items = [self._compact(session, row, anchors.get(row['id']), entry) for row in selected]
        # Binding may discover a stale physical source/index. Publish the status
        # it actually left, not the status preceding that validation.
        entry['revision'] = self.revision_for(session, group, entry['base_revision'])
        if sync_revision is not None:
            # Validate the same retained set after compacting (which can expire
            # proof support), before admitting the latest page or its cursor.
            if self._sync_revision(session, group, retained) != sync_revision:
                raise ValueError('This Work changed during paged sync.')
            entry['revision'] = sync_revision
            entry['sync_revision'] = sync_revision
            if before is not None:
                # Loading an older page extends admission, not the earlier
                # multi-request sync transaction. Issue a fresh token covering
                # the additional positions only after validating the old one.
                entry['sync_positions'] = sorted(set(entry['sync_positions']) |
                                                set(entry['positions'].values()))
                entry['revision'] = self._sync_revision(session, group, entry)
                entry['sync_revision'] = entry['revision']
        if revision is not None and sync_revision is None and revision != entry['revision']:
            raise ValueError('This Work changed. Reload its latest steps.')
        if begin:
            next_start, next_end, next_within = start, end, selected[0]['id']
        else:
            next_start, next_end, next_within = max(0, start - NODE_LIMIT), start, None
        if not (before is None and preserve and retained is not None):
            entry['older'] = (next_start, next_end, next_within) if begin or start else None
        if retained is not None:
            retained.update(entry)
            entry = retained
        self.groups.pop(self._key(session, group), None)
        self.groups[self._key(session, group)] = entry
        while len(self.groups) > MAX_GROUPS:
            self.groups.popitem(last=False)
        cursor = self._cursor(session, group, entry)
        return {'items': items,
            'mode': 'cursor', 'offset': None, 'total': None, 'before': cursor,
            'hasEarlier': cursor is not None, 'group': group, 'revision': entry['revision'],
            'coverage': {'kind': 'resident-live', 'historyComplete': False,
                         'sourceStart': start, 'sourceEnd': end}}

    def _cursor(self, session, group, entry):
        if entry.get('older') is None:
            return None
        start, end, within = entry['older']
        return self._token({'client': self._client(), 'owner': session['id'], 'group': group, 'stamp': entry['stamp'],
            'nonce': entry['nonce'], 'fence': entry['fence'], 'start': start, 'end': end, 'within': within})

    def sync(self, session, group, known, *, paged=False, revision=None):
        if (not isinstance(known, dict) or len(known) > (NODE_LIMIT if paged else MAX_STEPS)
                or not all(isinstance(key, str) and isinstance(value, str) for key, value in known.items())):
            raise ValueError('Choose a bounded open Work window.')
        entry = self._entry(session, group, reconcile=True)
        if not set(known) <= entry['ids'].keys():
            raise ValueError('This Work window expired. Close and reopen its steps.')
        if paged:
            return self._sync_page(session, group, known, entry, revision)
        retained, entry = entry, self._copy_entry(entry)
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
        entry['receipts'] = {key: value for key, value in entry['receipts'].items() if key in ids}
        entry['stamp'] = self._stamp(session)
        entry['fence'] = self.windows._fence(session['id'])
        entry['base_revision'] = (latest_segment or segment or {}).get('detailRevision', entry['base_revision'])
        items = [self._compact(session, row, anchors.get(row['id']), entry) for row in selected]
        entry['revision'] = self.revision_for(session, group, entry['base_revision'])
        if revision is not None and revision != entry['revision']:
            raise ValueError('This Work changed. Reload its latest steps.')
        retained.update(entry)
        entry = retained
        older = self._cursor(session, group, entry)
        return {'items': [row for row in items if known.get(row['id']) != row['detailVersion']],
            'order': [row['id'] for row in selected], 'offset': None, 'total': None, 'before': older,
            'group': group, 'revision': entry['revision'],
            'coverage': {'kind': 'resident-live', 'historyComplete': False}}

    def _sync_page(self, session, group, known, retained, revision):
        """Reconcile only this <=100-ID chunk; no implicit latest-page admission."""
        entry = self._copy_entry(retained)
        nodes = raw(session).get('execution', {}).get('nodes', [])
        if revision is None:
            entry['sync_positions'] = sorted(set(entry['positions'].values()) |
                set(range(max(0, len(nodes) - NODE_LIMIT), len(nodes))))
        elif revision != retained.get('sync_revision'):
            raise ValueError('This Work changed during paged sync.')
        indices = sorted({entry['positions'][identity] for identity in known})
        if any(not 0 <= index < len(nodes) for index in indices):
            raise ValueError('This Work changed. Close and reopen its steps.')
        source = self.windows.live_view(session)
        positions = {index: nodes[index] for index in indices}
        source['execution']['nodes'] = [row for row in positions.values()
            if row.get('liveObservation') or not row.get('canonicalHistory') and not row.get('nativeHistory')]
        rows, anchors, _ = self._rows(source, group)
        selected = [row for row in rows if row['id'] in known]
        by_id = {row['id']: index for index, row in positions.items()}
        if (len(selected) != len(known) or {row['id'] for row in selected} != set(known)
                or any(by_id.get(row['id'], by_id.get(row.get('parentId'))) != entry['positions'][row['id']]
                       for row in selected)):
            raise ValueError('This Work changed. Close and reopen its steps.')
        items = [self._compact(session, row, anchors.get(row['id']), entry) for row in selected]
        # The same bounded tail supplies the projection revision, not extra rows
        # to admit into this request's order or the retained window.
        _, _, latest = self._rows(self._source(session, max(0, len(nodes) - NODE_LIMIT), len(nodes)), group)
        entry['base_revision'] = (latest or {}).get('detailRevision', entry['base_revision'])
        entry['revision'] = self._sync_revision(session, group, entry)
        entry['sync_revision'] = entry['revision']
        if revision is not None and revision != entry['revision']:
            raise ValueError('This Work changed. Reload its latest steps.')
        entry.update(stamp=self._stamp(session), fence=self.windows._fence(session['id']))
        retained.update(entry)
        return {'items': [row for row in items if known[row['id']] != row['detailVersion']],
                'order': [row['id'] for row in selected], 'offset': None, 'total': None,
                'before': self._cursor(session, group, retained), 'group': group,
                'revision': entry['revision'],
                'coverage': {'kind': 'resident-live', 'historyComplete': False}}

    def _field_state(self, session, args, key, entry):
        """Check current authority and regenerate only the accepted row's view."""
        if (self._key(session, key[2]) != key or self.groups.get(key) is not entry
                or self._entry(session, key[2]) is not entry
                or entry['fence'] != self.windows._fence(session['id'])
                or args.get('windowKey') != entry['key']
                or args.get('sessionId') != session['id'] or args.get('part') != 'nodes'):
            raise ValueError('This Work field expired. Close and reopen its steps.')
        identity, field = args.get('id'), args.get('field')
        accepted = entry['receipts'].get(identity)
        if (identity not in entry['ids'] or accepted is None
                or args.get('detailVersion') != accepted['version']
                or field not in accepted['fields']
                or args.get('digest') != accepted['fields'][field]['detail'].get('digest')):
            raise ValueError('This Work field expired. Close and reopen its steps.')
        nodes = raw(session).get('execution', {}).get('nodes', [])
        position = entry['positions'][identity]
        if not 0 <= position < len(nodes):
            raise ValueError('This Work field expired. Close and reopen its steps.')
        # No historical graph scan: exact accepted physical position only.
        source = self._source(session, position, position + 1)
        rows, anchors, _ = self._rows(source, key[2])
        matches = [row for row in rows if row['id'] == identity]
        if len(matches) != 1:
            raise ValueError('This Work field changed. Close and reopen its steps.')
        row = matches[0]
        _, current = self._render(session, row, anchors.get(identity), entry, proof_id=accepted['proof'])
        if current != accepted:
            raise ValueError('This Work field changed. Close and reopen its steps.')
        return row, current

    async def read_text(self, session, args):
        from .browser_detail import read_text
        found = next(((key, value) for key, value in self.groups.items()
                      if key[0] == self._client() and key[1] == session['id']
                      and value['key'] == args.get('windowKey')), None)
        if found is None:
            raise ValueError('This Work field expired. Close and reopen its steps.')
        key, entry = found
        row, receipt = self._field_state(session, args, key, entry)
        stamp, fence = entry['stamp'], self.windows._fence(session['id'])
        field = args['field']
        if receipt['fields'][field]['kind'] == 'canonical':
            # The helper rechecks source/index/support before and after its own
            # await. Failure is terminal for this receipt; never fall back.
            result = await self.windows.read_proven_field(
                session['id'], receipt['proof'], receipt['bound'],
                {key: value for key, value in {**args, 'id': receipt['bound']}.items()
                 if key not in {'windowKey', 'detailVersion'}})
        else:
            # Legacy inline fields remain on the existing reader, off the event
            # loop. A resident event alias cannot bypass the proof-only route.
            source = {'id': session['id'], 'execution': {'nodes': [
                {name: value for name, value in row.items() if name != '_eventFields'}]}}
            result = await asyncio.to_thread(read_text, source, args)
        if stamp != self._stamp(session) or fence != self.windows._fence(session['id']):
            raise ValueError('This Work field expired. Close and reopen its steps.')
        _, current = self._field_state(session, args, key, entry)
        if current != receipt:
            raise ValueError('This Work field changed. Close and reopen its steps.')
        return result

    def retain_interest(self, identities):
        departed = self.interests - identities
        self.interests = set(identities)
        for key in tuple(self.groups):
            if key[1] in departed:
                self.groups.pop(key, None)
