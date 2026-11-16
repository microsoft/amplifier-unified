"""Private app utility requests, independent of conversation execution."""
import asyncio
from collections import OrderedDict
from contextvars import ContextVar
import time

from .errors import AppError
from .suggestion_settings import digest, selected
from .suggestion_source import Unavailable, capture, witness, eligible

MAX_RETAINED = 128
TTL = 60
DEADLINE = 15
PROVIDER_REASONS = frozenset({'custom_source_not_prepared'})
# Set only by the authenticated HTTP adapter, never action arguments or client
# registration. The closure rechecks cookie expiry/sign-out after async work.
BROWSER = ContextVar('suggestion_browser', default=None)


def definitions(schema, string):
    owner = {'sessionId': string(200), 'requestId': {**string(100), 'minLength': 1}}
    return {
        'session.suggestion.request': (
            'Request one private app-model suggestion from bounded display transcript. '
            'Requires the attached browser displaying this chat. Never sends a chat turn.',
            schema({**owner, 'expectedLastMessageId': string(200)})),
        'session.suggestion.status': (
            'Read this browser’s private suggestion receipt. Never starts or retries inference.',
            schema(owner)),
        'session.suggestion.cancel': (
            'Withdraw this browser’s suggestion and cancel its owned utility call. '
            'Cancellation does not undo provider effects; no automatic retry.',
            schema(owner)),
    }


class PromptSuggestions:
    def __init__(self, service):
        self.service = service
        self.records = OrderedDict()
        self.jobs = set()
        self.closed = False
        self._manager = None
        # Retain fixed-size, indexed content-free claims. Candidate memory is
        # bounded separately; historical claims neither load into memory nor
        # impose a lifetime request quota. Never age out uncertain paid effects.
        service.db.execute('CREATE TABLE IF NOT EXISTS suggestion_requests '
                           '(identity TEXT PRIMARY KEY, source TEXT NOT NULL UNIQUE)')

    @property
    def manager(self):
        if self._manager is None:
            from .setup import SetupManager
            self._manager = SetupManager(self.service.data_dir, global_only=True)
        return self._manager

    def settings(self):
        return selected(self.manager, str(self.service.data_dir))

    def audience(self, sid, origin):
        clients = self.service.clients
        identity, record = clients.current.get(), clients.record()
        browser = BROWSER.get()
        if (origin != 'ui' or not browser or not browser['valid']()
                or not identity or not record or record.get('kind') != 'web'
                or record.get('selectedSessionId') != sid):
            raise AppError('Prompt suggestions belong to the browser displaying this conversation.', 403)
        # deviceOwner comes from authenticated HTTP attachment, never args.
        # No synthetic tenant ACL: today's app has one authenticated principal.
        return digest([browser['principal'], record.get('deviceOwner', 'local-owner'), identity])

    def _epoch(self):
        from .auth import _epoch_hint, _UNSEEN
        value = _epoch_hint(self.service.data_dir)
        if value is _UNSEEN:
            raise Unavailable('authentication_unavailable')
        return value

    def current(self, record):
        try:
            config, _, revision = self.settings()
            client = self.service.clients.records.get(record['client'], {})
            if (self.closed or self.service.closed or not record['authenticated']()
                    or record['epoch'] != self._epoch()
                    or digest([record['principal'], client.get('deviceOwner', 'local-owner'),
                               record['client']]) != record['audience']
                    or client.get('selectedSessionId') != record['sessionId']
                    or record['configRevision'] != revision):
                return False
            session = self.service._session(record['sessionId'], hydrate=False)
            return (not session.get('archived') and not session.get('_deleting')
                    and eligible(self.service, session)
                    and session.get('status') in {'idle', 'ready'}
                    and witness(self.service, record['sessionId'], config) == record['sourceKey'])
        except (ValueError, KeyError, OSError, AppError):
            return False

    @staticmethod
    def wire(record):
        return {key: record.get(key) for key in
                ('requestId', 'sessionId', 'status', 'suggestion', 'sourceKey',
                 'configRevision', 'usage', 'cost', 'reason', 'expiresAt')}

    def _finish(self, record, status, **values):
        record.update(status=status, suggestion=None, **values)

    def _expire(self, record):
        self._finish(record, 'expired')
        if record.get('task') and not record['task'].done() and not record['task'].cancelling():
            record['task'].cancel()

    def _retire(self):
        now = time.monotonic()
        for record in self.records.values():
            if now - record['created'] > TTL and record['status'] != 'expired':
                self._expire(record)
        while len(self.records) >= MAX_RETAINED:
            key = next((key for key, row in self.records.items()
                        if not row.get('task') or row['task'].done()), None)
            if key is None:
                return
            self.records.pop(key)['expiry'].cancel()

    async def dispatch(self, action, args, origin, admission_check=None):
        sid, request_id = args['sessionId'], args['requestId']
        async with self.service.lock:
            if admission_check:
                admission_check()
            audience = self.audience(sid, origin)
            self._retire()
            key = digest([audience, sid, request_id])
            record = self.records.get(key)
            if action != 'session.suggestion.request':
                if record is None:
                    return self.wire(dict(args, status='expired'))
                if action.endswith('.cancel'):
                    self._finish(record, 'cancelled')
                    if record.get('task') and not record['task'].cancelling():
                        record['task'].cancel()
                elif record['status'] in {'pending', 'ready'} and not self.current(record):
                    self._finish(record, 'stale')
                    if record.get('task') and not record['task'].cancelling():
                        record['task'].cancel()
                return self.wire(record)
            if record is not None:
                if record.get('expectedLastMessageId') != args['expectedLastMessageId']:
                    raise AppError('This suggestion request ID has different inputs.', 409)
                if record['status'] in {'pending', 'ready'} and not self.current(record):
                    self._finish(record, 'stale')
                    if record.get('task') and not record['task'].cancelling():
                        record['task'].cancel()
                return self.wire(record)
            record = {**args, 'audience': audience, 'client': self.service.clients.current.get(),
                      'principal': BROWSER.get()['principal'], 'authenticated': BROWSER.get()['valid'],
                      'created': time.monotonic(), 'status': 'pending', 'suggestion': None,
                      'expiresAt': time.time() * 1000 + TTL * 1000,
                      'sourceKey': None, 'configRevision': None, 'usage': None, 'cost': None}
            if self.service.db.execute('SELECT 1 FROM suggestion_requests WHERE identity=?', (key,)).fetchone():
                return self.wire({**record, 'status': 'expired'})
            record['expiry'] = asyncio.get_running_loop().call_later(TTL, self._expire, record)
            config, provider, revision = self.settings()
            record['configRevision'] = revision
            if self.closed or self.service.closed:
                self._finish(record, 'cancelled')
            elif not config['enabled']:
                self._finish(record, 'disabled')
            elif not provider or not config['model']:
                self._finish(record, 'missing_selection')
            else:
                session = self.service._session(sid, hydrate=False)
                if not eligible(self.service, session):
                    self._finish(record, 'unsupported', reason='conversation_unavailable')
                elif session.get('archived') or session.get('_deleting'):
                    self._finish(record, 'stale')
                elif session.get('status') not in {'idle', 'ready'}:
                    self._finish(record, 'busy')
                elif len(self.jobs) >= 2 or any(row['audience'] == audience and row['sessionId'] == sid
                                               and row.get('task') and not row['task'].done()
                                               for row in self.records.values()):
                    self._finish(record, 'busy')
                else:
                    try:
                        record['epoch'] = self._epoch()
                        record['sourceKey'] = witness(self.service, sid, config)
                    except (ValueError, OSError):
                        self._finish(record, 'unsupported', reason='transcript_unavailable')
            if record['status'] == 'pending':
                # Exact request tombstone precedes capture and every effect.
                # Its domain-separated provisional source is replaced only
                # after a bounded canonical input has passed fresh admission.
                record['identity'] = key
                self.service.db.execute('INSERT INTO suggestion_requests VALUES (?,?)',
                                        (key, digest(['suggestion-request-v1', key])))
                self.service.db.commit()
                task = asyncio.create_task(self.run(record, config))
                record['task'] = task
                self.jobs.add(task)
                task.add_done_callback(self._done)
            self.records[key] = record
            return self.wire(record)

    def _done(self, task):
        self.jobs.discard(task)
        if not task.cancelled():
            task.exception()

    async def complete(self, record, config, transcript):
        return await self.manager.probe('suggestion.complete', {
            'id': config['provider'], 'model': config['model'], 'transcript': transcript,
            # Revalidate after subprocess creation, immediately before writing
            # its private request pipe; a changed source never reaches transport.
            '_admit': lambda: self.current(record) and record['status'] == 'pending'},
            str(self.service.data_dir))

    async def run(self, record, config):
        try:
            async with asyncio.timeout(DEADLINE):
                rows, source_key = await capture(self.service, record['sessionId'], config,
                                                 record['expectedLastMessageId'])
                async with self.service.lock:
                    if (source_key != record['sourceKey'] or not self.current(record)
                            or record['status'] != 'pending'):
                        if record['status'] == 'pending':
                            self._finish(record, 'stale')
                        return
                    # Paid identity is the exact bounded utility input, not the
                    # current resident/native/cold storage representation or a
                    # disposable reader fence. Rows are detached capture output.
                    source = digest(['suggestion-input-v1', record['principal'],
                                     record['sessionId'], record['configRevision'], rows])
                    if self.service.db.execute('SELECT 1 FROM suggestion_requests WHERE source=?',
                                               (source,)).fetchone():
                        self._finish(record, 'duplicate_source')
                        return
                    # Retain uncertainty forever once transport can occur.
                    # Never authorize a provider before this claim commits.
                    self.service.db.execute('UPDATE suggestion_requests SET source=? WHERE identity=?',
                                            (source, record['identity']))
                    self.service.db.commit()
                result = await self.complete(record, config, rows)
                if record['status'] == 'pending':
                    if self.current(record):
                        record.update({key: result.get(key) for key in ('status', 'suggestion', 'usage', 'cost')})
                        reason = result.get('reason')
                        record['reason'] = reason if isinstance(reason, str) and reason in PROVIDER_REASONS else None
                    else:
                        self._finish(record, 'stale')
        except asyncio.CancelledError:
            if record['status'] == 'pending':
                self._finish(record, 'cancelled')
        except TimeoutError:
            if record['status'] == 'pending':
                self._finish(record, 'timeout')
        except Unavailable as exc:
            if record['status'] == 'pending':
                self._finish(record, 'stale' if str(exc) == 'source_changed' else 'unsupported', reason=str(exc))
        except Exception:
            if record['status'] == 'pending':
                # Do not retain exception strings: SDK errors may quote bodies,
                # credentials, endpoint URLs or other private response content.
                self._finish(record, 'failed')

    def invalidate(self):
        for record in self.records.values():
            self._finish(record, 'stale')
            if record.get('task') and not record['task'].cancelling():
                record['task'].cancel()

    async def close(self):
        self.closed = True
        self.invalidate()
        if self.jobs:
            await asyncio.gather(*tuple(self.jobs), return_exceptions=True)
        for record in self.records.values():
            record['expiry'].cancel()
        self.records.clear()
        if self._manager is not None:
            await self._manager.close()