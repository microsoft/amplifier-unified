"""Private app utility requests, independent of conversation execution."""
import asyncio
from collections import OrderedDict
from contextvars import ContextVar
import time
import uuid

from .errors import AppError
from .suggestion_settings import digest, selected
from .suggestion_source import Unavailable, capture, witness, eligible, changed_component, SOURCE_REASONS
from .suggestion_provider import failure, FAILURE_REASONS

MAX_RETAINED = 128
TTL = 60
DEADLINE = 15
CURRENT_REASONS = frozenset('current_' + name for name in (
    'closed', 'auth', 'epoch', 'audience', 'selected', 'config', 'session',
    'archived', 'deleting', 'eligibility', 'busy', 'sourcewitness')) | SOURCE_REASONS
STALE_REASONS = CURRENT_REASONS | {'capture_source_changed', 'current_invalidated'}
PROVIDER_REASONS = frozenset({'custom_source_not_prepared', 'runtime_not_prepared',
                              'qualified_source_mismatch'}) | FAILURE_REASONS | STALE_REASONS
# Set only by the authenticated HTTP adapter, never action arguments or client
# registration. The closure rechecks cookie expiry/sign-out after async work.
BROWSER = ContextVar('suggestion_browser', default=None)


def definitions(schema, string):
    owner = {'sessionId': string(200), 'requestId': {**string(100), 'minLength': 1}}
    return {
        'session.suggestion.request': (
            'Request one private app-model suggestion from bounded display transcript. '
            'Requires the attached browser displaying this chat. Never sends a chat turn.',
            schema({**owner, 'expectedLastMessageId': string(200),
                    'regenerate': {'type': 'boolean'}},
                   ['sessionId', 'requestId', 'expectedLastMessageId'])),
        'session.suggestion.status': (
            'Read this browser’s private suggestion receipt. Never starts or retries inference.',
            schema(owner)),
        'session.suggestion.cancel': (
            'Withdraw this browser’s suggestion and cancel its owned utility call. '
            'Exact authenticated ownership still permits cancellation after leaving the chat. '
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
        catalog = getattr(getattr(self.service, 'management', None), 'provider_catalog', None)
        if self._manager is None:
            from .setup import SetupManager
            self._manager = SetupManager(self.service.data_dir, global_only=True, catalog=catalog)
        elif catalog is not None:
            # Early headless settings reads can precede Management assembly.
            # Borrow its real app-wide discovery owner once available; closing
            # this utility manager never closes a catalog supplied by its host.
            self._manager.catalog = catalog
        return self._manager

    def settings(self):
        return selected(self.manager, str(self.service.data_dir))

    def audience(self, sid, origin, *, require_selected=True):
        clients = self.service.clients
        identity, record = clients.current.get(), clients.record()
        browser = BROWSER.get()
        if (origin != 'ui' or not browser or not browser['valid']()
                or not identity or not record or record.get('kind') != 'web'
                or (require_selected and record.get('selectedSessionId') != sid)):
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
        reason = self.current_failure(record)
        if reason is not None:
            # Private finite observation only. Retain the FIRST failed gate,
            # including a final transport grant that later status cannot replay.
            record.setdefault('_currentFailure', reason)
        return reason is None

    def current_failure(self, record):
        dimension = 'current_config'
        try:
            config, _, revision = self.settings()
            dimension = 'current_audience'
            client = self.service.clients.records.get(record['client'], {})
            if self.closed or self.service.closed:
                return 'current_closed'
            dimension = 'current_auth'
            if not record['authenticated']():
                return dimension
            dimension = 'current_epoch'
            if record['epoch'] != self._epoch():
                return dimension
            dimension = 'current_audience'
            if digest([record['principal'], client.get('deviceOwner', 'local-owner'),
                       record['client']]) != record['audience']:
                return dimension
            if client.get('selectedSessionId') != record['sessionId']:
                return 'current_selected'
            if record['configRevision'] != revision:
                return 'current_config'
            dimension = 'current_session'
            session = self.service._session(record['sessionId'], hydrate=False)
            if session.get('archived'):
                return 'current_archived'
            if session.get('_deleting'):
                return 'current_deleting'
            dimension = 'current_eligibility'
            if not eligible(self.service, session):
                return dimension
            if session.get('status') not in {'idle', 'ready'}:
                return 'current_busy'
            dimension = 'current_sourcewitness'
            components = {}
            if witness(self.service, record['sessionId'], config, components=components) != record['sourceKey']:
                return changed_component(record.get('_sourceComponents'), components)
            return None
        except (ValueError, KeyError, OSError, AppError):
            return dimension

    @staticmethod
    def wire(record):
        return {key: record.get(key) for key in
                ('requestId', 'sessionId', 'status', 'suggestion', 'sourceKey',
                 'configRevision', 'usage', 'cost', 'reason', 'expiresAt',
                 'retention', 'reused', 'generationId')}

    def _finish(self, record, status, **values):
        if status == 'stale' and 'reason' not in values:
            observed = record.get('_currentFailure')
            values['reason'] = observed if observed in CURRENT_REASONS else 'capture_source_changed'
        record.update(status=status, suggestion=None, retention=None,
                      expiresAt=record.get('_expiresAt', record.get('expiresAt')), **values)

    def _ready(self, record):
        record.update(retention='source', expiresAt=None)
        record['expiry'].cancel()

    def _expire(self, record):
        if record['status'] == 'ready':
            return
        self._finish(record, 'expired')
        record.pop('_sourceComponents', None)
        if record.get('task') and not record['task'].done() and not record['task'].cancelling():
            record['task'].cancel()

    def _retire(self, *, reserve=False, protect=None):
        now = time.monotonic()
        for record in self.records.values():
            if now - record['created'] > TTL and record['status'] not in {'expired', 'ready'}:
                self._expire(record)
        while len(self.records) > MAX_RETAINED - int(reserve):
            key = next((key for key, row in self.records.items()
                        if key != protect and (not row.get('task') or row['task'].done())), None)
            if key is None:
                return
            self.records.pop(key)['expiry'].cancel()

    def _same_input(self, old, record, source):
        # NEW authority/capture are checked separately. Old tab selection is not
        # a cache authority; original source/Stop/epoch/config still must agree.
        return (old.get('_inputSource') == source
                and all(old.get(key) == record.get(key) for key in
                        ('principal', 'sessionId', 'configRevision', 'epoch', 'sourceKey')))

    def _prior(self, record, source):
        for key, old in reversed(self.records.items()):
            if old is not record and self._same_input(old, record, source):
                if old['status'] == 'ready':
                    self.records.move_to_end(key)
                    record.update({name: old.get(name) for name in
                                   ('suggestion', 'usage', 'cost', 'generationId')},
                                  status='ready', reason=None, reused=True)
                    self._ready(record)
                    return True
        return False

    def _absence(self, record, source):
        # Content-free dispositions only; no other request/client identity or
        # error prose. SQL alone cannot attest an evicted prior outcome.
        for old in reversed(self.records.values()):
            if (old is not record and self._same_input(old, record, source)
                    and old['status'] in {'empty', 'failed', 'timeout', 'cancelled', 'stale', 'unsupported'}):
                return 'prior_attempt_' + old['status']
        return 'prior_attempt_unavailable'

    async def dispatch(self, action, args, origin, admission_check=None):
        sid, request_id = args['sessionId'], args['requestId']
        async with self.service.lock:
            if admission_check:
                admission_check()
            # Navigation can precede its best-effort cancellation request.
            # Only cancellation omits display selection; the lookup below
            # remains exact principal/device/client/session/request ownership.
            audience = self.audience(sid, origin, require_selected=action != 'session.suggestion.cancel')
            regenerate = args.get('regenerate', False)
            if action.endswith('.request') and type(regenerate) is not bool:
                raise AppError('Regenerate must be a boolean.')
            self._retire()
            key = digest([audience, sid, request_id])
            record = self.records.get(key)
            if action != 'session.suggestion.request':
                if record is None:
                    return self.wire(dict(args, status='expired'))
                self.records.move_to_end(key)
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
                if (record.get('expectedLastMessageId') != args['expectedLastMessageId']
                        or record['regenerate'] != regenerate):
                    raise AppError('This suggestion request ID has different inputs.', 409)
                self.records.move_to_end(key)
                if record['status'] in {'pending', 'ready'} and not self.current(record):
                    self._finish(record, 'stale')
                    if record.get('task') and not record['task'].cancelling():
                        record['task'].cancel()
                return self.wire(record)
            record = {**args, 'regenerate': regenerate,
                      'audience': audience, 'client': self.service.clients.current.get(),
                      'principal': BROWSER.get()['principal'], 'authenticated': BROWSER.get()['valid'],
                      'created': time.monotonic(), 'status': 'pending', 'suggestion': None,
                      'expiresAt': time.time() * 1000 + TTL * 1000,
                      'sourceKey': None, 'configRevision': None, 'usage': None, 'cost': None,
                      'retention': None, 'reused': False, 'generationId': None}
            record['_expiresAt'] = record['expiresAt']
            if self.service.db.execute('SELECT 1 FROM suggestion_requests WHERE identity=?', (key,)).fetchone():
                return self.wire({**record, 'status': 'expired', 'reason': 'prior_attempt_unavailable'})
            # One explicit gesture ID cannot become new paid authority merely
            # by changing tab, source text, settings or the in-memory owner.
            intent = digest(['suggestion-explicit-intent-v1', record['principal'], sid, request_id])
            if self.service.db.execute(
                    'SELECT 1 FROM suggestion_requests WHERE identity=?', (intent,)).fetchone():
                return self.wire({**record, 'status': 'expired', 'reason': 'prior_attempt_unavailable'})
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
                elif len(self.jobs) >= 2 or any(row['principal'] == record['principal'] and row['sessionId'] == sid
                                               and row.get('task') and not row['task'].done()
                                               for row in self.records.values()):
                    self._finish(record, 'busy')
                else:
                    try:
                        record['epoch'] = self._epoch()
                        components = {}
                        record['sourceKey'] = witness(self.service, sid, config, components=components)
                        record['_sourceComponents'] = components
                    except (ValueError, OSError):
                        self._finish(record, 'unsupported', reason='transcript_unavailable')
            # Keep an exact READY source hot until the bounded capture can
            # rebind it; no second candidate cache or durable body is created.
            protect = next((old_key for old_key, old in reversed(self.records.items())
                            if old['status'] == 'ready' and all(
                                old.get(name) == record.get(name) for name in
                                ('principal', 'sessionId', 'configRevision', 'epoch', 'sourceKey'))), None)
            self._retire(reserve=True, protect=protect)
            if record['status'] == 'pending':
                # Exact request tombstone precedes capture and every effect.
                # Its domain-separated provisional source is replaced only
                # after a bounded canonical input has passed fresh admission.
                record['identity'] = key
                self.service.db.execute('INSERT INTO suggestion_requests VALUES (?,?)',
                                        (key, digest(['suggestion-request-v1', key])))
                if regenerate:
                    self.service.db.execute('INSERT INTO suggestion_requests VALUES (?,?)',
                                            (intent, digest(['suggestion-explicit-request-v1', intent])))
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
            'effort': config.get('effort'),
            **({'prompt': config['prompt']} if 'prompt' in config else {}),
            # Revalidate after subprocess creation, immediately before writing
            # its private request pipe; a changed source never reaches transport.
            '_admit': lambda: self.current(record) and record['status'] == 'pending'},
            str(self.service.data_dir))

    async def run(self, record, config):
        provider_started = False
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
                    record['_inputSource'] = source
                    if not record['regenerate'] and self._prior(record, source):
                        return
                    paid_source = (digest(['suggestion-explicit-input-v1', record['principal'],
                                           record['sessionId'], record['configRevision'], rows,
                                           record['requestId']]) if record['regenerate'] else source)
                    if self.service.db.execute('SELECT 1 FROM suggestion_requests WHERE source=?',
                                               (paid_source,)).fetchone():
                        self._finish(record, 'duplicate_source', reason=self._absence(record, source))
                        return
                    if record['regenerate']:
                        # A manual-first attempt also spends the automatic base.
                        # Do not remove or overwrite any original/unknown claim.
                        self.service.db.execute('INSERT OR IGNORE INTO suggestion_requests VALUES (?,?)',
                                                (digest(['suggestion-base-anchor-v1', source]), source))
                    # Retain uncertainty forever once transport can occur.
                    # Never authorize a provider before this claim commits.
                    self.service.db.execute('UPDATE suggestion_requests SET source=? WHERE identity=?',
                                            (paid_source, record['identity']))
                    self.service.db.commit()
                    record['generationId'] = uuid.uuid4().hex
                provider_started = True
                result = await self.complete(record, config, rows)
                if record['status'] == 'pending':
                    if self.current(record):
                        record.update({key: result.get(key) for key in ('status', 'suggestion', 'usage', 'cost')})
                        reason = (record.get('_currentFailure') if record['status'] == 'stale'
                                  and record.get('_currentFailure') in CURRENT_REASONS else result.get('reason'))
                        record['reason'] = reason if isinstance(reason, str) and reason in PROVIDER_REASONS else None
                        if record['status'] == 'ready':
                            self._ready(record)
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
                self._finish(record, 'stale' if str(exc) == 'source_changed' else 'unsupported',
                             reason='capture_source_changed' if str(exc) == 'source_changed' else str(exc))
        except Exception as exc:
            if record['status'] == 'pending':
                # Do not retain exception strings: SDK errors may quote bodies,
                # credentials, endpoint URLs or other private response content.
                if provider_started:
                    record.update(failure(exc, 'construction'))
                    if record.get('_currentFailure') in CURRENT_REASONS:
                        record['reason'] = record['_currentFailure']
                else:
                    self._finish(record, 'failed')

    def invalidate(self):
        for record in self.records.values():
            self._finish(record, 'stale', reason=record.get('_currentFailure') or 'current_invalidated')
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