"""Explicit, bounded saved-field lookup for one admitted tool step."""
from copy import deepcopy
import secrets

from .tool_window_records import records, linked, attach

MAX_OBSERVATIONS = 100


class ToolPayloadWindows:
    def __init__(self, windows):
        self.windows = windows

    def scope(self, owner, group, identity):
        from .cold_display import raw
        from .work_windows import source_identity
        pages = self.windows.live_pages
        entry = pages._entry(owner, group, reconcile=True)
        position = entry['positions'].get(identity)
        nodes = self.windows.resident_tree(owner, touch=True).get('nodes', [])
        node = nodes[position] if type(position) is int and 0 <= position < len(nodes) else None
        if (node is None or node.get('id') != identity or node.get('kind') != 'tool'
                or not node.get('toolCallId') or not node.get('sessionId')
                or node.get('canonicalHistory') or node.get('nativeHistory')):
            raise ValueError('This tool step moved. Reopen its Work steps.')
        admitted_range = entry['ids'].get(identity)
        if admitted_range is None:
            raise ValueError('This tool step moved. Reopen its Work steps.')
        selected, _, _ = pages._rows(pages._source(owner, *admitted_range), group)
        if not any(row['id'] == identity for row in selected):
            raise ValueError('This tool step moved. Reopen its Work steps.')
        binding = {key: deepcopy(node.get(key)) for key in
            ('id', 'toolCallId', 'toolObservationId', 'sessionId', 'rootSessionId', 'label',
             'tool', 'turnId', 'parentId', 'startedAt', 'endedAt')}
        scope = {'owner': pages._owner_stamp(owner), 'client': pages._client(), 'group': group,
                 'id': identity, 'position': position, 'binding': binding,
                 'placement': deepcopy(raw(owner).get('_coldFields', {}).get('execution'))}
        source = source_identity(owner)
        native = node['sessionId']
        if native == owner['id']:
            native = source.get('nativeIdentity') or source.get('runtimeSessionId') or native
        # This is the one child namespace already present in the host's admitted
        # tool row, never an HTTP path or a descendant-directory search.
        effective_native = source.get('nativeIdentity') or source.get('runtimeSessionId') or owner['id']
        if native != effective_native:
            source['nativeIdentity'] = native
        return scope, source, node

    def check(self, owner, page):
        expected = page['_toolScope']
        actual, source, _ = self.scope(owner, expected['group'], expected['id'])
        if actual != expected or source != page['source']:
            raise ValueError('This saved tool lookup expired. Reopen its Work steps.')

    async def read(self, owner, args):
        from .work_windows import source_identity, MAX_PAGES
        windows = self.windows
        scope, source, node = self.scope(owner, args.get('group'), args.get('id'))
        lookup = args.get('lookupKey')
        previous = None
        before = args.get('before')
        if lookup is not None:
            previous = windows._page(owner['id'], lookup)
            if (previous.get('_toolScope') != scope or previous.get('source') != source
                    or before != previous.get('before') or before is None):
                raise ValueError('This saved tool lookup changed. Reopen its Work steps.')
        elif before is not None:
            raise ValueError('Choose a current saved tool lookup.')
        key = lookup or 'tool-window:' + secrets.token_urlsafe(24)
        result = await windows._worker().project({'operation':'window', 'session':source,
            'windowKey': windows._window_key(source), 'before':before, 'cursorKey':windows.key.hex()},
            key=owner['id'] + ':tool:' + node['id'])
        if (windows.closed or windows._owner(owner['id']) is not owner
                or self.scope(owner, scope['group'], scope['id'])[0] != scope
                or previous is not None and windows.pages.get(key) is not previous):
            raise ValueError('This saved tool lookup expired. Reopen its Work steps.')
        if result.get('status') != 'complete':
            windows.field_failure(result)
        scanned = result['page']
        if not await __import__('asyncio').to_thread(windows._matches, source, scanned.get('revisions')):
            raise ValueError('Saved work changed during the lookup. Try again.')
        if (windows.closed or windows._owner(owner['id']) is not owner
                or self.scope(owner, scope['group'], scope['id'])[0] != scope
                or previous is not None and windows.pages.get(key) is not previous):
            raise ValueError('This saved tool lookup expired. Reopen its Work steps.')
        target = (source.get('nativeIdentity') or source.get('runtimeSessionId') or source['id'], node['toolCallId'])
        found = [row for row in scanned['items'] if row.get('kind') == 'tool'
                 and (row.get('sessionId'), row.get('toolCallId')) == target]
        existing = previous['items'] if previous else []
        unique = {row['id']: row for row in [*existing, *found]}
        # Bounded retained observations, never a whole-history search or body cache.
        # Historical rows remain individually inspectable, never coalesced.
        limited = len(unique) >= MAX_OBSERVATIONS
        items = sorted(unique.values(), key=lambda row: row['eventOrder'])[-MAX_OBSERVATIONS:]
        page = {'sessionId':owner['id'], 'source':source, 'ownerSource':source_identity(owner),
                '_toolScope':scope, 'group':key, 'items':items, 'before':scanned.get('before'),
                'generation':scanned['generation'], 'coverage':scanned['coverage']}
        page['toolRecords'] = records(page)
        fields = linked(page, owner, node)
        item = attach(node, fields)
        status = item['contentAvailability']['status']
        # Exact observation identity ends this lookup. Legacy records are only
        # related observations; older attempts can still be inspected explicitly.
        if node.get('toolObservationId') and status != 'not-loaded' or limited:
            page['before'] = None
        if limited:
            page['coverage'] = {**page['coverage'], 'lookupLimitReached': True}
        if not page['before'] and fields is None:
            item['contentAvailability'] = {'status': 'unrecorded'}
        windows.pages[key] = page
        windows.pages.move_to_end(key)
        while len(windows.pages) > MAX_PAGES:
            retired, _ = windows.pages.popitem(last=False)
            for sid, current in tuple(windows.current.items()):
                if current == retired:
                    windows.current.pop(sid, None)
                    windows.status.pop(sid, None)
        from .browser_detail import owned_nodes
        source_view = {'id': owner['id'], 'execution': {'nodes':[item]}, 'messages':[]}
        projected = owned_nodes(source_view)[0]
        entry = windows.live_pages._entry(owner, scope['group'], reconcile=True)
        item = windows.live_pages._compact(owner, projected, None, entry)
        return {'item':item, 'lookupKey':key, 'before':page['before'], 'coverage':page['coverage']}
