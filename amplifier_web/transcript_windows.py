"""Host-owned disposable message windows, separate from authoritative messages."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import copy
import hashlib
import json

from .transcript_window import TranscriptWindowReader

MAX_PAGES = 32
MAX_BYTES = 32 * 1024 * 1024


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _source(session):
    return {key: session.get(key) for key in
            ('id', 'nativeProject', 'nativeIdentity', 'runtimeSessionId', 'createdAt')}


def _visible(value):
    from .shared_state_probe import text_content
    metadata = value.get('metadata') or {}
    return (value.get('role') in {'user', 'assistant'}
            and isinstance(metadata, dict)
            and not any(metadata.get(key) for key in
                        ('ephemeral', 'amplifier_visible_reference', 'amplifier_recovery_reference'))
            and bool(text_content(value)))


def _native_page(reader, source, before, limit):
    from .automatic_history import directory
    from .shared_state_probe import text_content
    from .session_store import message_time
    result = reader.read_window(directory(source), {
        'project': source['nativeProject'],
        'nativeId': source.get('nativeIdentity') or source.get('runtimeSessionId') or source['id']},
        before=before, limit=limit, visible=_visible)
    if result['status'] != 'ready':
        raise ValueError('Saved message window is unavailable: ' + result['reason'])
    window = result['messageWindow']
    items = []
    for item in result['rows']:
        value = item['value']
        at = message_time(value)
        text = text_content(value)
        items.append({'id': item['displayId'], 'role': value['role'],
            '_windowDigest': hashlib.sha256(text.encode()).hexdigest(),
            'text': text, 'source': 'native', 'via': 'chat',
            'createdAt': at or source.get('createdAt', 0), 'timestampKnown': at is not None,
            'displayOnly': True, 'windowKey': window['windowKey'],
            'sourceReference': {'kind': 'transcript-window', 'byteOffset': item['byteOffset'],
                                'revision': window['revision'], 'restoreCursor': window['restoreCursor']}})
    return {'items': items, **window, 'coverage': {'kind': 'native-saved', **result['coverage']},
            'unavailableRanges': result['unavailableRanges'], '_owner': source}


class TranscriptWindows:
    def __init__(self, service):
        self.service = service
        self.reader = TranscriptWindowReader()
        self.pages = OrderedDict()
        self.initial = OrderedDict()
        self.bytes = 0
        self.jobs = set()
        self.closed = False
        self._epoch = 0
        self._fences = OrderedDict()

    def _fence(self, identity):
        value = self._fences.pop(identity, None)
        if value is None:
            self._epoch += 1
            value = self._epoch
        self._fences[identity] = value
        while len(self._fences) > 64:
            self._fences.popitem(last=False)
        return value

    def invalidate(self, session_ids=None):
        for identity in tuple(self._fences):
            if session_ids is None or identity in session_ids:
                self._epoch += 1
                self._fences[identity] = self._epoch

    def _read_finished(self, task):
        self.jobs.discard(task)
        if not task.cancelled():
            task.exception()  # A cancelled HTTP waiter must not orphan failures.

    async def close(self):
        self.closed = True
        # File reads have fixed byte/object bounds. Cancellation of an HTTP
        # waiter never releases ownership while its physical read still runs.
        if self.jobs:
            await asyncio.gather(*tuple(self.jobs), return_exceptions=True)
        self.pages.clear()
        self.initial.clear()
        self.bytes = 0

    def bound_web(self):
        clients = getattr(self.service, 'clients', None)
        identity = clients.current.get() if clients else None
        return bool(identity and clients.records.get(identity, {}).get('kind') == 'web')

    def observed(self, session_id):
        service = self.service
        for queue, identity in getattr(service, 'queue_clients', {}).items():
            record = service.clients.records.get(identity, {})
            if (service.queue_sessions.get(queue) is None and record.get('kind') == 'web'
                    and record.get('selectedSessionId') == session_id):
                return True
        return False

    def _notify(self, session_id):
        service = self.service
        projections = getattr(service, 'projections', None)
        if projections:
            projections.detail_bodies.pop(session_id, None)
        clients = getattr(service, 'clients', None)
        if not clients:
            return
        identities = set(getattr(service, 'queue_clients', {}).values())
        identities.add(clients.current.get())
        for identity in identities:
            record = clients.records.get(identity, {})
            if record.get('kind') == 'web' and record.get('selectedSessionId') == session_id:
                service.shell.notify(identity)

    def _retain(self, session_id, page, encoded_size):
        if encoded_size > MAX_BYTES:
            raise ValueError('Saved message window exceeds the display budget.')
        key = (session_id, page['windowKey'])
        old = self.pages.pop(key, None)
        if old:
            self.bytes -= old[1]
        self.pages[key] = (page, encoded_size)
        self.bytes += encoded_size
        while len(self.pages) > MAX_PAGES or self.bytes > MAX_BYTES:
            removed_key, (_, size) = self.pages.popitem(last=False)
            self.bytes -= size
            entry = self.initial.get(removed_key[0])
            if entry and entry.get('key') == removed_key:
                entry.update(key=None, status='unavailable', error='This message window was retired. Select the chat again to reload it.')
                self._notify(removed_key[0])
        return key

    def _cold_source(self, session):
        reference = dict.get(session, '_coldFields', {}).get('messages')
        if reference and not dict.__contains__(session, 'messages'):
            from .resource_files import detach
            return detach(self.service.db, reference['$resource'])
        return None

    def _saved_stamp(self, session):
        rows = session.get('messages', [])
        return [session['id'], self._fence(session['id']), len(rows), rows[0].get('id') if rows else None,
                rows[-1].get('id') if rows else None, session.get('nativeRevision')]

    def exact_saved_originals(self, session):
        """Fresh complete bounded originals for a synchronous receipt guard.

        Called under writer exclusion. Reuse the canonical immutable-resource
        reader and its byte/object/file fences; never hydrate a ColdRecord or
        treat a prior display-page cache as current authority. Partial sets
        cannot establish global uniqueness, even if they contain the wanted ID.
        """
        cold = self._cold_source(session)
        if cold is None:
            return dict.get(session, 'messages', ())
        from .saved_message_window import read_saved_window
        identity, source, fence = session['id'], _source(session), self._fence(session['id'])
        page = read_saved_window(cold, self.reader, session_id=identity, fence=fence, limit=100)
        current = self.service._session(identity, hydrate=False)
        if (_source(current) != source or self._fence(identity) != fence
                or self._cold_source(current) != cold or page['hasEarlier'] or page['unavailableRanges']):
            raise ValueError('The complete original source is unavailable or changed.')
        return page['items']

    def _saved_page(self, session, before, limit):
        stamp = self._saved_stamp(session)
        source = _digest(stamp)
        rows = session.get('messages', [])
        end = len(rows)
        if before is not None:
            try:
                cursor = self.reader._parse(before)
            except Exception:
                raise ValueError('The saved message cursor is no longer valid.') from None
            if (cursor.get('kind') != 'saved-web' or cursor.get('source') != source
                    or cursor.get('sessionId') != session['id']
                    or type(cursor.get('end')) is not int or not 0 <= cursor['end'] <= end):
                raise ValueError('The saved messages changed. Refresh before paging.')
            end = cursor['end']
        from .saved_message_window import resident_rows
        selected, gaps, start = resident_rows(rows, end, limit)
        token = self.reader._token({'kind': 'saved-web', 'source': source,
            'sessionId': session['id'], 'end': start}) if start else None
        return {'items': selected, 'mode': 'cursor', 'before': token,
                'hasEarlier': bool(start), 'total': None, 'offset': None, 'scope': 'recent',
                'revision': source, 'windowKey': _digest([source, start, end]),
                'coverage': {'kind': 'saved-web', 'historyComplete': False}, 'unavailableRanges': gaps,
                '_savedComplete': start == 0 and end == len(rows) and not gaps}

    async def read(self, session_id, *, before=None, limit=100, native=False):
        if self.closed:
            raise ValueError('Saved message readers are closed.')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Choose 1 to 100 messages.')
        async with self.service.lock:
            session = self.service._session(session_id, hydrate=False)
            source = _source(session)
            fence = self._fence(session_id)
            use_native = native or session.get('historyManaged') is True
            cold = self._cold_source(session) if not use_native else None
            if not use_native and cold is None:
                # Canonical saved IDs, attachments and action authority survive.
                page = self._saved_page(session, before, limit)
        if use_native or cold is not None:
            if self.closed or len(self.jobs) >= 8:
                raise ValueError('Saved message readers are busy. Try again.')
            if use_native:
                work = asyncio.to_thread(_native_page, self.reader, source, before, limit)
            else:
                from .saved_message_window import read_saved_window
                work = asyncio.to_thread(read_saved_window, cold, self.reader, session_id=session_id,
                                         fence=fence, before=before, limit=limit)
            task = asyncio.create_task(work)
            self.jobs.add(task)
            task.add_done_callback(self._read_finished)
            page = await asyncio.shield(task)
        if not use_native:
            if cold is not None:
                page['_savedComplete'] = before is None and not page['hasEarlier'] and not page['unavailableRanges']
            page['_savedOwner'] = {'source': source, 'fence': fence,
                                   'resource': cold.identity if cold is not None else None}
        # Detach/size only this selected page outside writer exclusion.
        def prepare():
            detached = copy.deepcopy(page)
            size = len(json.dumps(detached, ensure_ascii=False, separators=(',', ':')).encode())
            return detached, size
        page, size = await asyncio.to_thread(prepare)
        if size > MAX_BYTES:
            raise ValueError('Saved message window exceeds the display budget.')
        async with self.service.lock:
            current = self.service._session(session_id, hydrate=False)
            if (_source(current) != source or self._fence(session_id) != fence
                    or cold is not None and self._cold_source(current) != cold):
                raise ValueError('The selected message source changed.')
            self._retain(session_id, page, size)
        return self._wire(page, session_id)

    def _wire(self, page, session_id):
        result = {key: copy.deepcopy(value) for key, value in page.items() if key != 'items'}
        from .peer_attribution import derive
        # Attribution requires global duplicate checks. Only a complete bounded
        # saved set supplies that proof; neither a native suffix nor a partial
        # saved page may promote a single local match into global uniqueness.
        complete = result.pop('_savedComplete', False) is True
        resolver = getattr(self.service.projections, 'resolver', None) if complete else None
        rows = derive({'id': session_id, 'messages': page['items']}, page['items'], resolver)
        result['items'] = rows
        result.pop('_owner', None)
        saved_owner = result.pop('_savedOwner', None)
        # Reactions belong to the canonical saved message ID, independently of
        # the disposable body window. Look up only admitted IDs and never copy
        # unsupported annotation fields or decorate native display-only rows.
        current = self.service._session(session_id, hydrate=False)
        annotations = dict.get(current, 'messageAnnotations', {})
        for index, row in enumerate(result['items']):
            if not row.get('displayOnly'):
                from .browser_detail import compact
                result['items'][index] = compact(row, session_id, 'messages', 4096)
                annotation = annotations.get(row.get('id'), {})
                if isinstance(annotation, dict):
                    for field in ('reactions', 'assistantReactions'):
                        if field in annotation:
                            result['items'][index][field] = copy.deepcopy(annotation[field])
                if row.get('attribution'):
                    result['items'][index]['attribution'] = row['attribution']
                if saved_owner and result['items'][index].get('textDetail'):
                    result['items'][index]['textDetail']['windowKey'] = page['windowKey']
                continue
            text = row.get('text')
            if isinstance(text, str) and len(text) > 4096:
                row['text'] = text[:4096]
                row['textDetail'] = {'sessionId': session_id, 'part': 'messages',
                    'id': row['id'], 'field': 'text', 'windowKey': page['windowKey'],
                    'digest': row.get('_windowDigest'), 'length': len(text)}
            row.pop('_windowDigest', None)
        return result

    async def load(self, session_id, *, only_if_changed=False):
        try:
            await self._load(session_id, only_if_changed=only_if_changed)
        except asyncio.CancelledError:
            self.initial.pop(session_id, None)
            raise
        except (ValueError, OSError):
            async with self.service.lock:
                session = self.service._session(session_id, hydrate=False)
                reference = dict.get(session, '_coldFields', {}).get('messages', {}).get('$resource')
                self.initial[session_id] = {'status': 'unavailable', 'source': _source(session),
                    'savedSource': [reference, self._fence(session_id)],
                    'error': 'Could not read this saved message window. Original files are unchanged.'}
            while len(self.initial) > MAX_PAGES:
                self.initial.popitem(last=False)
            self._notify(session_id)

    async def _load(self, session_id, *, only_if_changed=False):
        # Existing AutomaticHistory per-session lock coalesces initial readers.
        from .automatic_history import revision
        async with self.service.lock:
            session = self.service._session(session_id, hydrate=False)
            source = _source(session)
            managed = session.get('historyManaged') is True
            previous_native = session.get('nativeRevision')
            cold = self._cold_source(session) if not managed else None
            saved_source = [cold.identity if cold is not None else None, self._fence(session_id)]
        stamp = await asyncio.to_thread(revision, source) if source.get('nativeProject') else None
        entry = self.initial.get(session_id)
        if (only_if_changed and entry and entry.get('stamp') == stamp
                and entry.get('source') == source and entry.get('status') == 'ready'
                and (managed or entry.get('savedSource') == saved_source)):
            return
        if not managed:
            page = await self.read(session_id) if cold is not None else None
            self.initial[session_id] = {'status': 'ready', 'stamp': stamp,
                'source': source, 'savedSource': saved_source,
                **({'key': (session_id, page['windowKey'])} if page else {}),
                'continuation': {'status': 'current' if stamp is not None and stamp == previous_native else
                                 'available' if stamp is not None else 'unknown'}}
            while len(self.initial) > MAX_PAGES:
                self.initial.popitem(last=False)
            self._notify(session_id)
            return
        self.initial[session_id] = {'status': 'loading', 'stamp': stamp}
        self._notify(session_id)
        try:
            page = await self.read(session_id)
            self.initial[session_id] = {'status': 'ready', 'stamp': stamp, 'source': source,
                                        'key': (session_id, page['windowKey'])}
        except asyncio.CancelledError:
            self.initial.pop(session_id, None)
            raise
        except (ValueError, OSError):
            self.initial[session_id] = {'status': 'unavailable', 'stamp': stamp,
                'error': 'Could not read this saved message window. Original files are unchanged.'}
        while len(self.initial) > MAX_PAGES:
            self.initial.popitem(last=False)
        self._notify(session_id)

    def project(self, session):
        result = self._project(session)
        result.update(sharedHistoryOffset=None, sharedHistoryUserTurnOffset=None)
        result['messageWindow']['globalInputUniqueness'] = 'unknown'
        return result

    def _project(self, session):
        entry = self.initial.get(session['id'], {})
        if not session.get('historyManaged'):
            if dict.get(session, '_coldFields', {}).get('messages') and not dict.__contains__(session, 'messages'):
                reference = dict.get(session, '_coldFields', {})['messages']['$resource']
                retained = self.pages.get(entry.get('key'))
                if retained is None or entry.get('savedSource') != [reference, self._fence(session['id'])]:
                    failed = (entry.get('status') == 'unavailable' and entry.get('savedSource') == [reference, self._fence(session['id'])])
                    return {'messages': [], 'messageWindow': {'mode': 'cursor', 'before': None,
                        'hasEarlier': False, 'total': None, 'offset': None},
                        'historyLoading': not failed, 'historyError': entry.get('error') if failed else None}
                page = self._wire(retained[0], session['id'])
            else:
                page = self._wire(self._saved_page(session, None, 100), session['id'])
            return {'messages': page.pop('items'), 'messageWindow': page,
                    'nativeContinuation': entry.get('continuation', {'status': 'unknown'}),
                    'historyLoading': False, 'historyError': None}
        if entry.get('source') is not None and entry.get('source') != _source(session):
            entry = {}
        retained = self.pages.get(entry.get('key'))
        if retained:
            page = self._wire(retained[0], session['id'])
            return {'messages': page.pop('items'), 'messageWindow': page,
                    'historyLoading': False, 'historyError': None}
        return {'messages': [], 'messageWindow': {'mode': 'cursor', 'before': None,
                'hasEarlier': False, 'total': None, 'offset': None, 'status': entry.get('status', 'loading')},
                'historyLoading': entry.get('status', 'loading') == 'loading', 'historyError': entry.get('error')}

    async def read_text(self, session_id, args):
        key = (session_id, args.get('windowKey'))
        retained = self.pages.get(key)
        if not retained or args.get('field') != 'text':
            raise ValueError('This message window expired. Reload its page.')
        async with self.service.lock:
            current = self.service._session(session_id, hydrate=False)
            source = _source(current)
        saved_owner = retained[0].get('_savedOwner')
        if saved_owner:
            async with self.service.lock:
                current = self.service._session(session_id, hydrate=False)
                cold = self._cold_source(current)
                if (saved_owner != {'source': _source(current), 'fence': self._fence(session_id),
                                    'resource': cold.identity if cold is not None else None}
                        or self.pages.get(key) is not retained):
                    raise ValueError('The saved message source changed.')
        else:
            if source != retained[0].get('_owner'):
                raise ValueError('The saved message owner changed.')
            from .automatic_history import directory
            owner = {'project': source['nativeProject'], 'nativeId': source.get('nativeIdentity') or source.get('runtimeSessionId') or source['id']}
            revision = await asyncio.to_thread(self.reader.source_revision, directory(source), owner)
            async with self.service.lock:
                current = self.service._session(session_id, hydrate=False)
                if (_source(current) != source or self.pages.get(key) is not retained
                        or revision != retained[0]['revision']):
                    raise ValueError('The saved message source changed.')
        row = next((row for row in retained[0]['items'] if row.get('id') == args.get('id')), None)
        if not row or (not saved_owner and not row.get('displayOnly')):
            raise ValueError('This message window no longer owns that text.')
        text = row['text']
        digest = hashlib.sha256(text.encode()).hexdigest()
        if args.get('digest') != digest:
            raise ValueError('This message text changed.')
        start, limit = args.get('offset', 0), args.get('limit', 16000)
        if isinstance(start, str) and start.isdecimal() and len(start) <= 10:
            start = int(start)
        if isinstance(limit, str) and limit.isdecimal() and len(limit) <= 6:
            limit = int(limit)
        if type(start) is not int or not 0 <= start <= len(text) or type(limit) is not int or not 1 <= limit <= 50000:
            raise ValueError('Choose a bounded text range.')
        end = min(len(text), start + limit)
        return {'value': text[start:end], 'nextOffset': end if end < len(text) else None,
                'total': len(text)}
