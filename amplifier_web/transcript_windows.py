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
        from .peer_originals import PeerOriginals
        self.peer_originals = PeerOriginals()
        self._peer_resident = OrderedDict()
        self._peer_failed = set()

    def peer_save_failed(self, identities):
        entries = getattr(getattr(self.service, '_message_preparation', None), 'entries', {})
        self._peer_failed.intersection_update(entries)
        self._peer_failed.update(set(identities).intersection(entries))

    def peer_save_committed(self, identities):
        self._peer_failed.difference_update(identities)

    def _database_epoch(self):
        db = self.service.db
        if db.in_transaction:
            return None  # A rollback is not a reusable index witness.
        return (id(db), db.execute('PRAGMA main.data_version').fetchone()[0], db.total_changes)

    @staticmethod
    def _peer_wanted(rows):
        from .peer_attribution import input_identity
        return [row for row in rows if row.get('role') == 'user'
                and row.get('source') != 'native' and input_identity(row)
                and (row.get('via') == 'peer' or row.get('inputOrigin') == 'peer')]

    def _committed_messages(self, session):
        entries = getattr(getattr(self.service, '_message_preparation', None), 'entries', {})
        entry = entries.get(session['id'])
        return entry if (entry and entry.owner == id(session)
                         and session['id'] not in self._peer_failed) else None

    def _resident_proof(self, rows, wanted, revision=None):
        if not wanted:
            return None
        key = (revision, tuple((row['id'], row.get('inputId'), row.get('nativeInputId'))
                               for row in wanted)) if revision is not None else None
        if key in self._peer_resident:
            self._peer_resident.move_to_end(key)
            return self._peer_resident[key][0]
        try:
            proof = self.peer_originals.resident(rows, wanted)
        except ValueError:
            if key is None:
                raise
            # A deterministic refusal for immutable committed bytes is reusable
            # too. Do not rescan the same malformed prefix on each scalar tick.
            proof = None
        if key is not None:
            # Only canonical committed, detached snapshots supply revision.
            # Mutable resident callers always do a fresh explicit-read scan.
            size = len(json.dumps(proof.originals, ensure_ascii=False).encode()) if proof else 0
            self._peer_resident[key] = (proof, size)
            while self._peer_resident and (len(self._peer_resident) > 16
                    or sum(value[1] for value in self._peer_resident.values()) > 8 * 1024 * 1024):
                self._peer_resident.popitem(last=False)
        return proof

    def exact_peer_original(self, session, message_id, input_id):
        """Explicit preview guard: fresh current originals, never a display cache."""
        wanted = [{'id': message_id, 'inputId': input_id}]
        cold = self._cold_source(session)
        if cold is None:
            return self.peer_originals.resident(dict.get(session, 'messages', ()), wanted)
        source, fence = _source(session), self._fence(session['id'])
        proof = self.peer_originals.cold(cold, wanted)
        current = self.service._session(session['id'], hydrate=False)
        if (_source(current) != source or self._fence(session['id']) != fence
                or self._cold_source(current) != cold):
            raise ValueError('The original source changed.')
        return proof

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

    async def _read_job(self, work):
        # Admission and registration have no intervening await. One job owns
        # window read, peer qualification and detachment until physical return.
        if self.closed:
            raise ValueError('Saved message readers are closed.')
        if len(self.jobs) >= 8:
            raise ValueError('Saved message readers are busy. Try again.')
        task = asyncio.create_task(asyncio.to_thread(work))
        self.jobs.add(task)
        task.add_done_callback(self._read_finished)
        return await asyncio.shield(task)

    async def close(self):
        self.closed = True
        # File reads have fixed byte/object bounds. Cancellation of an HTTP
        # waiter never releases ownership while its physical read still runs.
        if self.jobs:
            await asyncio.gather(*tuple(self.jobs), return_exceptions=True)
        self.pages.clear()
        self.initial.clear()
        self._peer_resident.clear()
        self._peer_failed.clear()
        self.peer_originals.cache.clear()
        self.peer_originals.retained_bytes = 0
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

    def _saved_page(self, session, before, limit, *, committed=None):
        stamp = self._saved_stamp(session)
        if committed is not None:
            stamp = [session['id'], committed.prepared.identity]
        source = _digest(stamp)
        rows = committed.snapshot if committed is not None else session.get('messages', [])
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
                '_savedComplete': start == 0 and end == len(rows) and not gaps,
                **({'_committedRevision': committed.prepared.identity,
                    'coverage': {'kind': 'saved-web', 'historyComplete': False,
                        'provenance': {'basis': 'committed-snapshot',
                            'revision': committed.prepared.identity,
                            'countsScope': 'requested-originals-global',
                            'liveHistoryChecked': False}}} if committed is not None else {})}

    async def read(self, session_id, *, before=None, limit=100, native=False):
        if self.closed:
            raise ValueError('Saved message readers are closed.')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Choose 1 to 100 messages.')
        async with self.service.lock:
            if self.closed:
                raise ValueError('Saved message readers are closed.')
            session = self.service._session(session_id, hydrate=False)
            source = _source(session)
            fence = self._fence(session_id)
            use_native = native or session.get('historyManaged') is True
            cold = self._cold_source(session) if not use_native else None
            committed = None
            if not use_native and cold is None:
                if before is not None:
                    cursor = self.reader._parse(before)
                    entry = self._committed_messages(session)
                    if entry is not None and cursor.get('source') == _digest([session_id, entry.prepared.identity]):
                        committed = entry
                # Canonical saved IDs, attachments and action authority survive.
                page = self._saved_page(session, before, limit, committed=committed)
        def read_and_prepare():
            if use_native:
                result = _native_page(self.reader, source, before, limit)
            elif cold is not None:
                from .saved_message_window import read_saved_window
                result = read_saved_window(cold, self.reader, session_id=session_id,
                                           fence=fence, before=before, limit=limit)
            else:
                result = page
            if not use_native:
                if cold is not None:
                    result['_savedComplete'] = before is None and not result['hasEarlier'] and not result['unavailableRanges']
                result['_savedOwner'] = {'source': source, 'fence': fence,
                                        'resource': cold.identity if cold is not None else None}
            proof = None
            wanted = self._peer_wanted(result['items']) if not use_native else []
            if wanted and cold is not None:
                # This second phase stays in the SAME admitted physical job:
                # cancellation/shutdown never loses it or admits a ninth worker.
                try:
                    proof = self.peer_originals.cold(cold, wanted)
                except (ValueError, OSError):
                    pass
            detached = copy.deepcopy(result)
            size = len(json.dumps(detached, ensure_ascii=False, separators=(',', ':')).encode())
            return detached, size, proof, wanted
        page, size, proof, wanted = await self._read_job(read_and_prepare)
        if size > MAX_BYTES:
            raise ValueError('Saved message window exceeds the display budget.')
        async with self.service.lock:
            if self.closed:
                raise ValueError('Saved message readers are closed.')
            current = self.service._session(session_id, hydrate=False)
            database_epoch = self._database_epoch() if cold is not None else None
            if (_source(current) != source or self._fence(session_id) != fence
                    or cold is not None and self._cold_source(current) != cold
                    or committed is not None and self._committed_messages(current) is not committed):
                raise ValueError('The selected message source changed.')
            if wanted and cold is None:
                try:
                    proof = self._resident_proof(
                        committed.snapshot if committed is not None else dict.get(current, 'messages', ()),
                        wanted, (session_id, committed.prepared.identity) if committed is not None else None)
                except (ValueError, OSError):
                    proof = None
            page['_peerChecked'] = bool(wanted)
            if proof is not None:
                page['_peerProof'] = proof
                # Do not retain Detached.indexed: legacy inline resources can
                # contain the entire history. Proof retains compact witnesses.
                page['_peerSource'] = cold.identity if cold is not None else None
                if cold is not None and proof.revision[1][0] == 'inline':
                    # Validate bytes at this explicit read; passive projections
                    # only reuse when no DB write could have changed the index.
                    # The exact index was just recaptured and compared to the
                    # validated source under this exclusion. An unrelated write
                    # during the worker need not suppress that fresh equality.
                    # External writes racing this final recapture still fence it.
                    page['_peerIndexEpoch'] = (database_epoch if database_epoch is not None
                        and database_epoch == self._database_epoch() else None)
                size += len(json.dumps(proof.originals, ensure_ascii=False).encode())
            self._retain(session_id, page, size)
        return self._wire(page, session_id)

    def _wire(self, page, session_id):
        result = {key: copy.deepcopy(value) for key, value in page.items()
                  if key not in {'items', '_peerProof', '_peerSource', '_peerChecked',
                                 '_peerIndexEpoch', '_committedRevision'}}
        from .peer_attribution import derive
        # Attribution requires global duplicate checks. Only a complete bounded
        # saved set supplies that proof; neither a native suffix nor a partial
        # saved page may promote a single local match into global uniqueness.
        complete = result.pop('_savedComplete', False) is True
        resolver = getattr(self.service.projections, 'resolver', None)
        proof = page.get('_peerProof')
        if proof is not None and page.get('_peerSource') is not None:
            from .resource_files import detached_signature
            from .media_saved_aliases import _indexed_evidence
            current = self.service._session(session_id, hydrate=False)
            try:
                reference = dict.get(current, '_coldFields', {}).get('messages', {}).get('$resource')
                if dict.__contains__(current, 'messages') or reference != page['_peerSource']:
                    proof = None
                elif proof.revision[1][0] == 'inline':
                    if page.get('_peerIndexEpoch') is None or page['_peerIndexEpoch'] != self._database_epoch():
                        proof = None
                else:
                    cold = self._cold_source(current)
                    if (cold is None or cold.identity != page['_peerSource']
                        or (str(cold.directory) if cold.directory is not None else None,
                            cold.identity, *_indexed_evidence(cold.indexed)) != proof.revision[0]
                        or detached_signature(cold) != proof.revision[1]):
                        proof = None
            except (ValueError, OSError):
                proof = None
        # Never expose a committed snapshot's nested dictionaries to a reader.
        selected = copy.deepcopy(page['items'])
        owner = {'id': session_id, 'messages': selected}
        if proof is not None and resolver is not None:
            rows = resolver(owner, selected, proof=proof)
        else:
            rows = derive(owner, selected,
                          resolver if complete and not page.get('_peerChecked') else None)
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
        if self.closed:
            return
        try:
            await self._load(session_id, only_if_changed=only_if_changed)
        except asyncio.CancelledError:
            self.initial.pop(session_id, None)
            raise
        except (ValueError, OSError):
            async with self.service.lock:
                if self.closed:
                    return
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
        if self.closed:
            return
        entry = self.initial.get(session_id)
        if (only_if_changed and entry and entry.get('stamp') == stamp
                and entry.get('source') == source and entry.get('status') == 'ready'
                and (managed or entry.get('savedSource') == saved_source)):
            return
        if not managed:
            page = await self.read(session_id) if cold is not None else None
            if self.closed:
                return
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
            if self.closed:
                return
            self.initial[session_id] = {'status': 'ready', 'stamp': stamp, 'source': source,
                                        'key': (session_id, page['windowKey'])}
        except asyncio.CancelledError:
            self.initial.pop(session_id, None)
            raise
        except (ValueError, OSError):
            if self.closed:
                return
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
                committed = self._committed_messages(session)
                raw_page = self._saved_page(session, None, 100)
                wanted = self._peer_wanted(raw_page['items'])
                if wanted and committed is not None:
                    raw_page['_peerChecked'] = True
                    try:
                        saved_page = self._saved_page(session, None, 100, committed=committed)
                        from .message_preparation import _same
                        # Visibility check only, not a live global-uniqueness
                        # witness. Return snapshot rows WITH snapshot counts.
                        # Unpublished off-page edits do not relabel that snapshot
                        # as current; explicit reads/actions always revalidate.
                        if (len(dict.get(session, 'messages', ())) == len(committed.snapshot)
                                and raw_page['unavailableRanges'] == saved_page['unavailableRanges']
                                and _same(raw_page['items'], saved_page['items'], [100_000])):
                            raw_page = saved_page
                            raw_page['_peerProof'] = self._resident_proof(
                                committed.snapshot, wanted,
                                (session['id'], committed.prepared.identity))
                        else:
                            raw_page['_peerChecked'] = True
                    except (ValueError, OSError):
                        pass
                page = self._wire(raw_page, session['id'])
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
