"""Private app utility requests, independent of conversation execution."""
import asyncio
from collections import OrderedDict
from contextvars import ContextVar
import time
import uuid
import json
import math
import re

from .errors import AppError
from .suggestion_settings import digest, selected
from .suggestion_source import Unavailable, capture, witness, eligible, changed_component, SOURCE_REASONS
from .suggestion_provider import failure, FAILURE_REASONS, parse

MAX_RETAINED = 128
TTL = 60
DEADLINE = 15
CACHE_PER_PRINCIPAL = 32
CACHE_TOTAL = 128
CACHE_ENTRY_BYTES = 4096
USAGE_FIELDS = frozenset({'input_tokens', 'output_tokens', 'total_tokens',
                          'cache_read_input_tokens', 'cache_creation_input_tokens'})
CURRENT_REASONS = frozenset('current_' + name for name in (
    'closed', 'auth', 'epoch', 'audience', 'selected', 'config', 'preference', 'session',
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
            schema({**owner, 'expectedLastMessageId': string(200)})),
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
        # Old hashes have no recoverable owner/outcome and may contain a
        # process-salted discovery identity. Never treat a new-domain miss as
        # proof that an old input was unpaid. Record one exact migration fence.
        service.db.execute('''CREATE TABLE IF NOT EXISTS suggestion_cache_policy (
            version INTEGER PRIMARY KEY CHECK(version=1), legacy INTEGER NOT NULL,
            command_floor INTEGER NOT NULL)''')
        if not service.db.execute('SELECT 1 FROM suggestion_cache_policy WHERE version=1').fetchone():
            legacy = bool(service.db.execute('SELECT 1 FROM suggestion_requests LIMIT 1').fetchone())
            has_commands = service.db.execute("SELECT 1 FROM sqlite_master WHERE name='commands'").fetchone()
            floor = service.db.execute('SELECT COALESCE(MAX(rowid),0) FROM commands').fetchone()[0] if has_commands else 0
            service.db.execute('INSERT INTO suggestion_cache_policy VALUES (1,?,?)', (legacy, floor))
        service.db.execute('''CREATE TABLE IF NOT EXISTS suggestion_fresh_completion (
            session_id TEXT PRIMARY KEY, message_key TEXT NOT NULL, observed INTEGER NOT NULL)''')
        # Separate from client_views/drafts and state_records/shared readback.
        # No input bodies, request IDs, provider config or credentials persist.
        service.db.execute('''CREATE TABLE IF NOT EXISTS suggestion_success_cache (
            principal TEXT NOT NULL, session_id TEXT NOT NULL, input_key TEXT NOT NULL,
            value TEXT NOT NULL, used_at INTEGER NOT NULL,
            PRIMARY KEY(principal, session_id))''')
        service.db.execute('CREATE INDEX IF NOT EXISTS suggestion_success_lru '
                           'ON suggestion_success_cache(used_at)')
        service.db.execute('CREATE INDEX IF NOT EXISTS suggestion_success_principal_lru '
                           'ON suggestion_success_cache(principal, used_at)')
        # Canonical deletion/archive/Stop writes clean bodies in the same DB
        # transaction. No dependency on resident session membership or hydration.
        if service.db.execute("SELECT 1 FROM sqlite_master WHERE name='state_records'").fetchone():
            service.db.execute('''CREATE TRIGGER IF NOT EXISTS suggestion_success_session_delete
                AFTER DELETE ON state_records WHEN OLD.kind='session'
                BEGIN DELETE FROM suggestion_success_cache WHERE session_id=OLD.id;
                      DELETE FROM suggestion_fresh_completion WHERE session_id=OLD.id; END''')
            service.db.execute('''CREATE TRIGGER IF NOT EXISTS suggestion_success_session_change
                AFTER UPDATE OF value ON state_records WHEN NEW.kind='session' AND (
                    NEW.value='null' OR json_extract(NEW.value,'$.archived')=1 OR
                    json_extract(NEW.value,'$._deleting')=1 OR
                    COALESCE(json_extract(NEW.value,'$.interruptionRevision'),0) !=
                    COALESCE(json_extract(OLD.value,'$.interruptionRevision'),0))
                BEGIN DELETE FROM suggestion_success_cache WHERE session_id=NEW.id;
                      DELETE FROM suggestion_fresh_completion WHERE session_id=NEW.id; END''')
        self._trim_cache()
        service.db.commit()

    def observe_message(self, session, message):
        """Exact host-observed response to a newly admitted UI input, not text/time inference."""
        if (message.get('role') != 'assistant' or not message.get('inputId')
                or message.get('source') not in {'amplifier', 'external'}):
            return
        db = self.service.db
        legacy, floor = db.execute('SELECT legacy,command_floor FROM suggestion_cache_policy WHERE version=1').fetchone()
        if not legacy:
            return
        # Exact indexed receipt, never an event/transcript scan. Host command
        # row identity is post-migration; message-provided timestamps prove nothing.
        row = db.execute('SELECT rowid,receipt FROM commands WHERE id=?', (message['inputId'],)).fetchone()
        if row is None or row[0] <= floor:
            return
        try:
            receipt = json.loads(row[1])
        except (ValueError, TypeError):
            return
        post = receipt.get('navigationPost') or {}
        # navigationPost is authored only by ordinary host-trusted UI send,
        # not caller fields, imported history or an agent-origin command.
        if (receipt.get('accepted') is not True or receipt.get('inputId') != message['inputId']
                or receipt.get('sessionId') != session['id'] or not isinstance(post, dict)
                or post.get('sessionId') != session['id'] or post.get('inputId') != message['inputId']
                or not isinstance(post.get('fence'), str) or not post['fence']
                or post.get('disposition') == 'rejected'):
            return
        db.execute('INSERT OR REPLACE INTO suggestion_fresh_completion VALUES (?,?,?)',
                   (session['id'], digest(message['id']), time.time_ns()))
        db.execute('''DELETE FROM suggestion_fresh_completion WHERE rowid IN (
            SELECT rowid FROM suggestion_fresh_completion ORDER BY observed DESC,rowid DESC LIMIT -1 OFFSET ?)''',
                   (CACHE_TOTAL,))
        # The caller commits this alongside the canonical assistant message.

    def _migration_allows(self, record, rows):
        db = self.service.db
        legacy = db.execute('SELECT legacy FROM suggestion_cache_policy WHERE version=1').fetchone()[0]
        if not legacy:
            return True
        proof = db.execute('SELECT message_key FROM suggestion_fresh_completion WHERE session_id=?',
                           (record['sessionId'],)).fetchone()
        return bool(proof and rows and proof[0] == digest(rows[-1]['id']))

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

    def enabled(self):
        return getattr(self.service, '_state', {}).get('settings', {}).get('suggestedMessages') is True

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
            if not self.enabled():
                return 'current_preference'
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

    def _retire(self, *, reserve=False):
        now = time.monotonic()
        for record in self.records.values():
            if now - record['created'] > TTL and record['status'] not in {'expired', 'ready'}:
                self._expire(record)
        while len(self.records) > MAX_RETAINED - int(reserve):
            key = next((key for key, row in self.records.items()
                        if not row.get('task') or row['task'].done()), None)
            if key is None:
                return
            self.records.pop(key)['expiry'].cancel()

    def _cache_key(self, record, config, rows):
        from .auth import _read_epoch
        session = self.service._session(record['sessionId'], hydrate=False)
        # Catalog discovery has a process salt before runtime qualification.
        # Cache validity instead uses ordinary configured connection identity
        # and saved preference intent, not a disposable discovery/reader owner.
        if 'revision' in config:
            manager, workspace = self.manager, str(self.service.data_dir)
            with manager.read_snapshot(workspace):
                settings = manager.config(workspace).settings.get('prompt_suggestions') or {}
                selection = {key: value for key, value in config.items() if key != 'revision'}
                binding = manager.catalog_key({'id': config['provider']}, workspace, binding=True)
            config_key = digest([settings, selection, binding])
        else:
            config_key = record['configRevision']  # Injected offline test selection.
        # Paid identity is deliberately less strict than cache eligibility.
        # A Stop/owner/auth change invalidates saved assistance but cannot buy
        # another call for an already-attempted input after discovery restart.
        record['_paidKey'] = digest(['suggestion-durable-automatic-v1', record['principal'],
                                     record['sessionId'], config_key, rows])
        owner = [session.get(key) for key in ('id', 'nativeProject', 'nativeIdentity', 'createdAt',
                                               'interruptionRevision')]
        return digest(['suggestion-success-v1', record['principal'], owner,
                       config_key, _read_epoch(self.service.data_dir), rows])

    @staticmethod
    def _success(value):
        if not isinstance(value, dict) or set(value) != {'status', 'suggestion', 'usage', 'cost', 'generationId'}:
            raise ValueError('Invalid cached result')
        if value['status'] not in {'ready', 'empty'}:
            raise ValueError('Invalid cached result')
        text = parse(json.dumps({'suggestion': value['suggestion']}, ensure_ascii=False))
        if (value['status'] == 'ready') != (text is not None):
            raise ValueError('Invalid cached result')
        if not isinstance(value['generationId'], str) or not re.fullmatch('[0-9a-f]{32}', value['generationId']):
            raise ValueError('Invalid cached result')
        usage = value['usage']
        if usage is not None and (not isinstance(usage, dict) or not set(usage) <= USAGE_FIELDS
                or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in usage.values())):
            raise ValueError('Invalid cached usage')
        cost = value['cost']
        if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
            raise ValueError('Invalid cached cost')
        return value

    def _trim_cache(self):
        db = self.service.db
        # Limits bound bytes too: at most 128 rows of <=4096 bytes; one latest
        # result per conversation. LRU uses a persistent clock, not RAM order.
        db.execute('''DELETE FROM suggestion_success_cache WHERE rowid IN (
            SELECT rowid FROM (SELECT rowid, row_number() OVER (
                PARTITION BY principal ORDER BY used_at DESC, rowid DESC) AS n
                FROM suggestion_success_cache) WHERE n>?)''', (CACHE_PER_PRINCIPAL,))
        db.execute('''DELETE FROM suggestion_success_cache WHERE rowid IN (
            SELECT rowid FROM suggestion_success_cache ORDER BY used_at DESC, rowid DESC LIMIT -1 OFFSET ?)''',
                   (CACHE_TOTAL,))

    def _cached(self, record):
        db = self.service.db
        row = db.execute('''SELECT input_key, CASE WHEN length(CAST(value AS BLOB))<=?
            THEN value END FROM suggestion_success_cache WHERE principal=? AND session_id=?''',
            (CACHE_ENTRY_BYTES, record['principal'], record['sessionId'])).fetchone()
        if row is None:
            return False
        try:
            if row[0] != record['_cacheKey'] or row[1] is None:
                raise ValueError('Changed cached input')
            value = self._success(json.loads(row[1]))
        except (ValueError, TypeError, OverflowError, RecursionError):
            db.execute('DELETE FROM suggestion_success_cache WHERE principal=? AND session_id=?',
                       (record['principal'], record['sessionId']))
            db.commit()
            return False
        db.execute('UPDATE suggestion_success_cache SET used_at=? WHERE principal=? AND session_id=?',
                   (time.time_ns(), record['principal'], record['sessionId']))
        db.commit()
        record.update(value, reason=None, reused=True)
        if record['status'] == 'ready':
            self._ready(record)
        return True

    def _save_success(self, record, result):
        value = self._success({key: result.get(key) if key != 'generationId' else record[key]
                               for key in ('status', 'suggestion', 'usage', 'cost', 'generationId')})
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(raw.encode()) > CACHE_ENTRY_BYTES:
            raise ValueError('Cached result exceeds budget')
        db = self.service.db
        with db:
            db.execute('INSERT OR REPLACE INTO suggestion_success_cache VALUES (?,?,?,?,?)',
                       (record['principal'], record['sessionId'], record['_cacheKey'], raw, time.time_ns()))
            self._trim_cache()
        return value

    def _absence(self, record, source):
        # Content-free dispositions only; no other request/client identity or
        # error prose. SQL alone cannot attest an evicted prior outcome.
        for old in reversed(self.records.values()):
            if (old is not record and old.get('_inputSource') == source
                    and old.get('principal') == record['principal']
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
            if 'regenerate' in args:
                raise AppError('Prompt suggestions are automatic only.')
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
                if record.get('expectedLastMessageId') != args['expectedLastMessageId']:
                    raise AppError('This suggestion request ID has different inputs.', 409)
                self.records.move_to_end(key)
                if record['status'] in {'pending', 'ready'} and not self.current(record):
                    self._finish(record, 'stale')
                    if record.get('task') and not record['task'].cancelling():
                        record['task'].cancel()
                return self.wire(record)
            record = {**args,
                      'audience': audience, 'client': self.service.clients.current.get(),
                      'principal': BROWSER.get()['principal'], 'authenticated': BROWSER.get()['valid'],
                      'created': time.monotonic(), 'status': 'pending', 'suggestion': None,
                      'expiresAt': time.time() * 1000 + TTL * 1000,
                      'sourceKey': None, 'configRevision': None, 'usage': None, 'cost': None,
                      'retention': None, 'reused': False, 'generationId': None}
            record['_expiresAt'] = record['expiresAt']
            record['_cacheOnly'] = bool(self.service.db.execute(
                'SELECT 1 FROM suggestion_requests WHERE identity=?', (key,)).fetchone())
            record['expiry'] = asyncio.get_running_loop().call_later(TTL, self._expire, record)
            config, provider, revision = self.settings()
            record['configRevision'] = revision
            if self.closed or self.service.closed:
                self._finish(record, 'cancelled')
            elif not self.enabled() or not config['enabled']:
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
            self._retire(reserve=True)
            if record['status'] == 'pending':
                # Exact request tombstone precedes capture and every effect.
                # Its domain-separated provisional source is replaced only
                # after a bounded canonical input has passed fresh admission.
                record['identity'] = key
                if not record['_cacheOnly']:
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
                    record['_cacheKey'] = self._cache_key(record, config, rows)
                    if self._cached(record):
                        return
                    if record['_cacheOnly']:
                        self._finish(record, 'expired', reason='prior_attempt_unavailable')
                        return
                    stable_source = record['_paidKey']
                    if self.service.db.execute('SELECT 1 FROM suggestion_requests WHERE source IN (?,?)',
                                               (source, stable_source)).fetchone():
                        # A demonstrable old-domain match can safely acquire
                        # its stable alias without changing that original claim.
                        with self.service.db:
                            self.service.db.execute('INSERT OR IGNORE INTO suggestion_requests VALUES (?,?)',
                                (digest(['suggestion-automatic-anchor-v1', stable_source]), stable_source))
                        self._finish(record, 'duplicate_source', reason=self._absence(record, source))
                        return
                    if not self._migration_allows(record, rows):
                        self._finish(record, 'duplicate_source', reason='migration_prior_attempt_unavailable')
                        return
                    # Preserve the original paid domain and every old claim;
                    # an additional stable identity prevents a discovery-process
                    # salt from buying another call after future cache eviction.
                    with self.service.db:
                        self.service.db.execute('INSERT OR IGNORE INTO suggestion_requests VALUES (?,?)',
                                                (digest(['suggestion-automatic-anchor-v1', stable_source]), stable_source))
                        self.service.db.execute('UPDATE suggestion_requests SET source=? WHERE identity=?',
                                                (source, record['identity']))
                    record['generationId'] = uuid.uuid4().hex
                provider_started = True
                result = await self.complete(record, config, rows)
                if record['status'] == 'pending':
                    if self.current(record):
                        if result.get('status') in {'ready', 'empty'}:
                            # Durable commit precedes READY/EMPTY publication.
                            # Failure leaves the once-only paid claim intact.
                            result = self._save_success(record, result)
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

    def invalidate(self, *, clear_cache=True):
        if clear_cache:
            self.service.db.execute('DELETE FROM suggestion_success_cache')
            self.service.db.commit()
        for record in self.records.values():
            self._finish(record, 'stale', reason=record.get('_currentFailure') or 'current_invalidated')
            if record.get('task') and not record['task'].cancelling():
                record['task'].cancel()

    async def close(self):
        self.closed = True
        self.invalidate(clear_cache=False)
        if self.jobs:
            await asyncio.gather(*tuple(self.jobs), return_exceptions=True)
        for record in self.records.values():
            record['expiry'].cancel()
        self.records.clear()
        if self._manager is not None:
            await self._manager.close()