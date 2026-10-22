"""Bounded exact field leases owned by already-open resident Work groups.

Only descriptors/previews are retained. Native bytes remain the field authority;
latest-window turnover is neither evidence deletion nor a new tool attempt.
"""
from copy import deepcopy
import secrets

FIELDS = ('input', 'output', 'error', 'delegateRequest', 'delegateResponse', 'request', 'response')
from .resident_work_pages import MAX_STEPS

# At most seven 512-character previews per admitted row: 2,867,200
# characters per group at the existing 800-row bound, never complete bodies.
MAX_FIELDS = MAX_STEPS * len(FIELDS)


def binding(node):
    return {key: node.get(key) for key in ('id', 'kind', 'sessionId', 'rootSessionId',
        'parentId', 'turnId', 'toolCallId', 'toolObservationId', 'tool', 'label',
        'provider', 'model', 'startedAt')}


class OpenWorkFields:
    def __init__(self, resident):
        self.resident = resident

    def _node(self, session, entry, identity):
        from .cold_display import raw
        position = entry['positions'].get(identity)
        nodes = raw(session).get('execution', {}).get('nodes', [])
        node = nodes[position] if type(position) is int and 0 <= position < len(nodes) else None
        if node is None or node.get('id') != identity:
            raise ValueError('This saved detail moved. Reopen its Work steps.')
        return node

    def page(self, session, key):
        for (client, identity, group), entry in self.resident.groups.items():
            if client != self.resident._client() or identity != session['id']:
                continue
            for page in entry.get('fields', {}).values():
                if page['group'] != key:
                    continue
                self.resident._entry(session, group, reconcile=True)
                scope = page['_openScope']
                node = self._node(session, entry, scope['id'])
                if binding(node) != scope['binding'] or entry['positions'][scope['id']] != scope['position']:
                    raise ValueError('This saved detail changed. Reopen its Work steps.')
                admitted = entry['ids'].get(scope['id'])
                if admitted is None:
                    raise ValueError('This saved detail expired. Reopen its Work steps.')
                rows, _, _ = self.resident._rows(self.resident._source(session, *admitted), group)
                if not any(row['id'] == scope['id'] for row in rows):
                    raise ValueError('This saved detail moved. Reopen its Work steps.')
                return page
        raise ValueError('This saved detail expired. Reopen its Work steps.')

    def _candidate(self, session, node, field, detail):
        if not isinstance(detail, dict) or not str(detail.get('windowKey', '')).startswith(('event-window:', 'tool-window:')):
            return None
        windows = self.resident.windows
        try:
            page = windows._page(session['id'], detail['windowKey'])
        except ValueError:
            return None
        row = next((value for value in page['items'] if value['id'] == detail.get('id')), None)
        if row is None or row.get(field + 'Detail', {}).get('digest') != detail.get('digest'):
            return None
        if field not in row.get('_eventFields', {}):
            return None
        native = node.get('sessionId')
        if native == session['id']:
            native = session.get('nativeIdentity') or session.get('runtimeSessionId') or native
        if row.get('sessionId') != native:
            return None
        if node.get('kind') == 'tool':
            if (not node.get('toolObservationId') or row.get('_hostToolId') != node['toolObservationId']
                    or row.get('toolCallId') != node.get('toolCallId')
                    or row.get('label') != (node.get('tool') or node.get('label'))):
                return None
            attempt = None
        elif node.get('kind') == 'llm':
            if row.get('_hostCallId') != node['id'] or not row.get('requestId'):
                return None
            attempt = (row['requestId'], row.get('provider'), row.get('model'))
        else:
            return None
        return page, row, attempt

    def retain(self, session, entry, row, item):
        if row.get('kind') not in {'tool', 'llm'} or row['id'] not in entry['positions']:
            return item
        from .work_windows import source_identity
        from .browser_detail import detail_version
        node = self._node(session, entry, row['id'])
        current_binding = binding(node)
        retained = entry.setdefault('fields', {})
        own = [(key, retained[key]) for field in FIELDS
               if (key := (row['id'], field)) in retained]
        discard = item.get('contentAvailability', {}).get('status') == 'ambiguous'
        observed_attempt = None
        if node.get('kind') == 'llm':
            latest = self.resident.windows.pages.get(self.resident.windows.current.get(session['id']))
            if latest and latest['source'] == source_identity(session):
                native = node.get('sessionId')
                if native == session['id']:
                    native = session.get('nativeIdentity') or session.get('runtimeSessionId') or native
                observations = [value for value in latest['items'] if value.get('_hostCallId') == node['id']
                                and value.get('sessionId') == native and value.get('requestId')]
                attempts = {(value['requestId'], value.get('provider'), value.get('model')) for value in observations}
                discard |= len(attempts) > 1 or any(sum(field + 'Detail' in value for value in observations) > 1
                                                   for field in ('request', 'response'))
                observed_attempt = next(iter(attempts), None) if len(attempts) == 1 else None
        for key, page in own:
            if (discard or page['_openScope']['binding'] != current_binding
                    or observed_attempt is not None and page['_attempt'] != observed_attempt):
                retained.pop(key, None)
        if discard:
            return item
        used = []
        for field in FIELDS:
            key = (row['id'], field)
            previous = retained.get(key)
            detail = item.get(field + 'Detail')
            candidate = self._candidate(session, node, field, detail)
            if candidate:
                source_page, saved, attempt = candidate
                reference = saved['_eventFields'][field]
                if not (previous and previous['source'] == source_page['source'] and previous['_attempt'] == attempt
                        and previous['items'][0]['_eventFields'][field] == reference
                        and previous['items'][0][field + 'Detail']['digest'] == detail['digest']):
                    # One field keeps one exact event descriptor, never the full
                    # window or another call's payload. The lease key is opaque.
                    saved_field = {name: deepcopy(saved[name]) for name in
                        ('id', 'kind', 'sessionId', 'provider', 'model', field, field + 'Detail',
                         field + 'Capture', field + 'Recording') if name in saved}
                    saved_field['_eventFields'] = {field: deepcopy(reference)}
                    previous = {'group': 'open-work:' + secrets.token_urlsafe(24),
                        'sessionId': session['id'], 'source': deepcopy(source_page['source']),
                        'ownerSource': source_identity(session), 'items': [saved_field], '_attempt': attempt,
                        '_availability': deepcopy(item.get('contentAvailability')),
                        '_openScope': {'id': row['id'], 'binding': current_binding,
                                       'position': entry['positions'][row['id']]}}
                    retained[key] = previous
            elif detail is not None:
                # An existing nonwindow/native field owns its normal reader.
                continue
            if previous:
                saved = previous['items'][0]
                for suffix in ('', 'Detail', 'Capture', 'Recording'):
                    name = field + suffix
                    if name in saved:
                        item[name] = deepcopy(saved[name])
                item[field + 'Detail'].update(sessionId=session['id'], windowKey=previous['group'])
                used.append(previous)
        if used and item.get('contentAvailability', {}).get('status') == 'not-loaded':
            status = next((page['_availability'] for page in used if page['_availability']), None)
            if status:
                item['contentAvailability'] = deepcopy(status)
        # Latest window receipts are transport state, not content identity.
        item['detailVersion'] = str(detail_version({key: value for key, value in item.items() if key != 'detailVersion'}))
        return item
