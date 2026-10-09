"""Name existing unnamed chats without starting a conversation worker.

Each eligible chat gets one provider call, made in the isolated provider probe
subprocess with the user's default provider (no session, tools or replay). The
prompt, context sampling and response parsing are hooks-session-naming's own.
Results are written through naming.accept_generated, so a manual rename or
Auto=false made meanwhile always wins.
"""
import asyncio
import time

from .naming import accept_generated, automatic_metadata, directory_for, read, refresh
from .session_navigation import is_top_level

BUSY = {'starting', 'working', 'running', 'stopping'}
DEFAULT_LIMIT = 20
MAX_LIMIT = 200


def _hook():
    import amplifier_module_hooks_session_naming as module
    return module, module.SessionNamingHook(None, module.SessionNamingConfig())


def default_provider(setup, workspace):
    """The enabled provider Foundation would select first (lowest priority)."""
    config = setup.config(workspace)
    disabled = set(config.settings.get('configurator', {}).get('disabled', {}).get('providers', []))
    rows = []
    for row in config.providers:
        identity = row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-')
        if row.get('enabled', True) is False or identity in disabled:
            continue
        values = row.get('config') or {}
        priority = values.get('priority', 100)
        rows.append((priority if isinstance(priority, (int, float)) else 100, len(rows), identity, values))
    if not rows:
        raise ValueError('Configure a provider before generating chat names.')
    _, _, identity, values = min(rows)
    model = values.get('default_model') or values.get('model')
    return identity, model if isinstance(model, str) and model else None


class NamingBackfill:
    def __init__(self, service, *, complete=None, concurrency=2, timeout=120):
        self.service = service
        self.complete = complete or self._probe_complete
        self.concurrency = concurrency
        self.timeout = timeout
        self.active = False  # Set at admission, before the task starts.

    async def _probe_complete(self, session, prompt):
        from .setup import SetupManager
        setup = SetupManager(self.service.data_dir)
        workspace = session['workspace']
        identity, model = await asyncio.to_thread(default_provider, setup, workspace)
        result = await setup.probe('naming.complete', {'id': identity, 'model': model, 'prompt': prompt}, workspace)
        return result['text']

    @property
    def running(self):
        return self.active

    def eligibility(self, session, *, own_pending=False):
        """None when eligible, else a user-readable reason to skip."""
        if not is_top_level(session):
            return 'Only top-level chats are named.'
        if session.get('status') in BUSY or session.get('configurationBusy'):
            return 'The chat is busy; try again when it is idle.'
        if not own_pending and session.get('naming', {}).get('status') == 'working':
            return 'A name is already being generated.'
        if session.get('autoName') is False:
            return 'Automatic naming is off for this chat.'
        metadata = read(directory_for(self.service.data_dir, session))
        if not automatic_metadata(metadata):
            return 'Automatic naming is off for this chat.'
        if metadata.get('name_source') in {'generated', 'manual'}:
            return 'The chat already has a generated or chosen name.'
        if not session.get('workspace'):
            return 'The chat has no workspace folder.'
        return None

    def select(self, ids=None, limit=DEFAULT_LIMIT):
        sessions = self.service.state['sessions']
        if ids is not None:
            wanted = list(dict.fromkeys(ids))
            by_id = {row['id']: row for row in sessions}
            unknown = [identity for identity in wanted if identity not in by_id]
            candidates = [by_id[identity] for identity in wanted if identity in by_id]
        else:
            unknown = []
            candidates = sorted((row for row in sessions if is_top_level(row)),
                                key=lambda row: row.get('recentActivityAt') or row.get('createdAt') or 0, reverse=True)
        queued, skipped = [], [{'id': identity, 'reason': 'No chat has this ID.'} for identity in unknown]
        for session in candidates:
            reason = self.eligibility(session)
            if reason:
                # Browsing many chats must not produce an unbounded report.
                if ids is not None or len(skipped) < MAX_LIMIT:
                    skipped.append({'id': session['id'], 'reason': reason})
            elif len(queued) < limit:
                queued.append(session)
        return queued, skipped

    def start(self, args, command_id=None):
        """Admit a run under the service lock; returns the receipt result."""
        if self.running:
            raise ValueError('Chat name backfill is already running. Wait for it to finish.')
        limit = max(1, min(int(args.get('limit') or DEFAULT_LIMIT), MAX_LIMIT))
        queued, skipped = self.select(args.get('ids'), limit)
        self.active = bool(queued)
        for session in queued:
            session['naming'] = {'status': 'working'}
        self.service.state['namingBackfill'] = {
            'status': 'running' if queued else 'done', 'commandId': command_id, 'startedAt': time.time(),
            'queued': [session['id'] for session in queued], 'named': [], 'skipped': skipped, 'failed': []}
        if not queued:
            self.service.state['namingBackfill']['finishedAt'] = time.time()
        return {'queued': len(queued), 'skipped': len(skipped)}, [session['id'] for session in queued]

    async def run(self, identities):
        gate = asyncio.Semaphore(self.concurrency)

        async def one(identity):
            async with gate:
                await self._name(identity)

        try:
            await asyncio.gather(*(one(identity) for identity in identities))
        finally:
            async with self.service.lock:
                self.active = False
                report = self.service.state.get('namingBackfill', {})
                report.update(status='done', finishedAt=time.time())
                self.service._publish()

    async def _record(self, identity, bucket, status, reason=None):
        async with self.service.lock:
            report = self.service.state['namingBackfill']
            report[bucket].append(identity if bucket == 'named' else {'id': identity, 'reason': reason})
            session = next((row for row in self.service.state['sessions'] if row['id'] == identity), None)
            if session is None:
                self.service._publish()
                return  # Deleted meanwhile; the report still records it.
            if status is None:
                session.pop('naming', None)
            else:
                session['naming'] = {'status': status, **({'error': reason} if status == 'error' else {})}
            self.service._publish()

    async def _name(self, identity):
        try:
            async with self.service.lock:
                session = dict(self.service._session(identity))
                reason = self.eligibility(session, own_pending=True)
            if reason:
                return await self._record(identity, 'skipped', None, reason)
            directory = directory_for(self.service.data_dir, session)
            base = read(directory)
            module, hook = _hook()
            messages = await asyncio.to_thread(hook._read_transcript, directory)
            if not any(row.get('role') == 'user' and row.get('content') for row in messages):
                return await self._record(identity, 'skipped', None, 'The chat has no user message to name it from.')
            context = hook._extract_naming_context(messages, None, None)
            prompt = module.INITIAL_NAMING_PROMPT.format(context=context)
            try:
                text = await asyncio.wait_for(self.complete(session, prompt), self.timeout)
            except TimeoutError:
                return await self._record(identity, 'failed', 'error', 'Naming timed out; the current title was kept.')
            parsed = hook._parse_response(text or '') if text else None
            if not parsed:
                return await self._record(identity, 'failed', 'error', 'The naming model returned an unreadable reply.')
            if parsed.get('action') == 'defer' or not parsed.get('name'):
                return await self._record(identity, 'skipped', 'deferred', 'The naming model deferred; the current title was kept.')
            candidate = {'name': str(parsed['name'])[:hook.config.max_name_length],
                         'name_revision': base.get('name_revision', 0),
                         'name_policy_revision': base.get('name_policy_revision', 0)}
            if isinstance(parsed.get('description'), str):
                candidate['description'] = parsed['description'][:hook.config.max_description_length]
            async with self.service.lock:
                # Confirm the chat still exists before a metadata write can
                # recreate files removed while the provider call was pending.
                current = self.service._session(identity)
                accepted = accept_generated(directory, candidate)[1]
                refresh(self.service.data_dir, current)
            if accepted:
                await self._record(identity, 'named', 'ready')
            else:
                await self._record(identity, 'skipped', None, 'The name or Auto preference changed meanwhile; the newer choice was kept.')
        except Exception as exc:  # noqa: BLE001 - each chat reports its own failure.
            await self._record(identity, 'failed', 'error', str(exc) or type(exc).__name__)
