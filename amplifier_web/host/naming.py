"""Adapt Foundation's naming hook to foreground and ongoing work lifecycles.

The community hook owns prompting, context sampling, routing, parsing and retry
policy. This adapter supplies lifecycle scheduling and app-owned metadata I/O.
"""
import asyncio
import importlib
import logging
import hashlib
import math
from pathlib import Path

from ..naming import read, automatic_metadata, accept_generated
from amplifier_foundation.session.metadata import has_generated_or_manual_name

log=logging.getLogger(__name__)
HOOK_MODULE='amplifier_module_hooks_session_naming'


class _LastWarning(logging.Handler):
    """Keep the naming hook's latest warning, redacted and bounded."""
    def __init__(self):
        super().__init__(logging.WARNING)
        self.message=None

    def emit(self,record):
        try:
            from ..worker_diagnostics import redact_diagnostic
            self.message=' '.join(redact_diagnostic(record.getMessage()).split())[:500]
        except Exception:  # A diagnostic must never break naming.
            pass


def claim_root_lifecycle(coordinator):
    """Take scheduling before root execute; never mutate an imported module.

    New hooks expose a session-local handoff. Known older pinned hooks use
    public registry operations for one recognized scheduler. Older registries
    cannot reliably remove duplicate handler names, so those require the public
    handoff. Cleanup drains stay registered.
    """
    control = getattr(coordinator, 'get_capability', lambda _: None)('session.naming.lifecycle')
    if control is not None:
        if getattr(control, 'version', None) != 1:
            raise ValueError('The naming module has an unsupported lifecycle handoff.')
        control.use_external()
        if control.mode != 'external':
            raise ValueError('The naming module did not transfer its automatic lifecycle.')
        return
    expected = sum(row.get('module') == 'hooks-session-naming' and row.get('enabled', True)
                   for row in coordinator.config.get('hooks', []))
    registry = coordinator.hooks
    handlers = registry.list_handlers()
    if (expected != 1 or handlers.get('prompt:complete', []).count('session-naming') != expected
            or handlers.get('session:end', []).count('session-naming-drain') != expected
            or any('session-naming' in names for event, names in handlers.items() if event != 'prompt:complete')):
        raise ValueError('This pinned naming module does not expose a recognized lifecycle. Update it before using app naming.')
    registry.unregister('session-naming')
    if any('session-naming' in names for names in registry.list_handlers().values()):
        raise ValueError('The naming module did not release its automatic lifecycle. App naming remains unavailable.')


class LiveSessionNaming:
    def __init__(self,coordinator,home,publish,completed_inputs=()):
        self.coordinator=coordinator
        from .storage import SessionStore
        workspace=getattr(coordinator, "get_capability", lambda _: None)("web.history_workspace") or coordinator.config.get("project_dir") or coordinator.config.get("working_dir") or Path.cwd()
        self.store=SessionStore.for_app(home, workspace)
        self.directory=self.store.directory(coordinator.session_id)
        self.publish=publish
        self.completed=set(read(self.directory).get('naming_completed_inputs',completed_inputs))
        self.delivered=set()
        self.pending=None
        self.turn_id=None
        self.hook=None
        self.last_attempt=len(self.completed)
        self.active=False
        self.closed=False
        self.timer=None
        self.last_context=None
        self.refresh_seconds=300
        rows=coordinator.config.get('hooks',[])
        row=next((r for r in rows if r.get('module')=='hooks-session-naming' and r.get('enabled',True)),None)
        # Naming belongs to the app, including for saved plans and bundles
        # without a naming hook. Do not insert modules into the saved plan.
        # If a bundle did mount its own scheduler, transfer lifecycle ownership
        # before execution so it cannot bypass Auto=false or double-charge.
        if row:
            claim_root_lifecycle(coordinator)
        self.unavailable='The configured automatic naming module could not be initialized. Check its configuration and installation.'
        try:
            module=importlib.import_module(HOOK_MODULE)
            # The app default uses the conversation's selected provider. An
            # explicitly configured naming module may opt into a model role.
            config=dict(row.get('config') or {}) if row else {'model_role': None}
            # Unified owns the lifecycle for every bundle: name after the
            # first completed user turn so one-turn chats are named too. A
            # configured bundle keeps its other settings but cannot delay the
            # first name past turn 1.
            config['initial_trigger_turn']=min(config.get('initial_trigger_turn',1),1)
            self.refresh_seconds=float(config.get('update_interval_seconds', 300))
            if not math.isfinite(self.refresh_seconds) or self.refresh_seconds < 1:raise ValueError('Invalid naming refresh interval')
            keys=('initial_trigger_turn','update_interval_turns','max_name_length','max_description_length','max_retries','model_role')
            settings=module.SessionNamingConfig(**{k:config[k] for k in keys if k in config})
            if settings.initial_trigger_turn<1 or settings.update_interval_turns<1:raise ValueError('Invalid naming intervals')
            adapter=self
            class AppNamingHook(module.SessionNamingHook):
                def _select_session_provider(self, providers):
                    # Unified's live pin is an orchestrator selection, not the
                    # Foundation CLI's conversation.provider_pin capability.
                    loop = coordinator.get('orchestrator')
                    selected = getattr(loop, 'root_provider', None)
                    if selected is not None:
                        from .session import SelectedProvider
                        original = selected.original if isinstance(selected, SelectedProvider) else selected
                        for name, provider in providers.items():
                            if provider is original:
                                return name, provider
                        raise ValueError('The selected naming provider is no longer mounted. Choose a provider before naming.')
                    return super()._select_session_provider(providers)
                async def _call_provider(self, prompt, *args, **kwargs):
                    if getattr(self, 'refresh_title', False):
                        prompt = ('Keep the current title when it still describes the conversation. '
                                  'Only change it when the main topic or goal has meaningfully changed.\n\n' + prompt)
                    return await super()._call_provider(prompt, *args, **kwargs)
                def _get_session_dir(self,session_id):return adapter.directory
                def _load_metadata(self,session_dir):
                    from .storage import SessionStore
                    saved=adapter.store.load(coordinator.session_id)
                    return {**(saved[1] if saved else {}),**read(session_dir)}
                def _save_metadata(self,session_dir,metadata):
                    # Cached bundles can still supply an older naming hook.
                    # Enforce the shared writer even before that cache updates.
                    from amplifier_foundation.session.metadata import SessionMetadataStore
                    store=SessionMetadataStore(session_dir)
                    if metadata.get('name'):
                        if 'name_auto' in store.read():
                            return accept_generated(session_dir, metadata)[0]
                        return store.set_name(metadata['name'],source='generated',
                            description=metadata.get('description'),
                            expected_revision=metadata.get('name_revision',0))
                    return store.update({key:metadata[key] for key in ('description','description_updated_at') if key in metadata})
            self.hook=AppNamingHook(coordinator,settings)
            async def result(event,data):
                from amplifier_core import HookResult
                if data.get('session_id')==coordinator.session_id:
                    accepted=read(adapter.directory)
                    self.publish({'type':'session.naming','name':accepted.get('name'),'description':accepted.get('description'),'nameRevision':accepted.get('name_revision')})
                return HookResult()
            coordinator.hooks.register('session-naming:set',result,name='unified-session-naming')
            coordinator.register_cleanup(self.close)
        except (ImportError,AttributeError,TypeError,ValueError):
            log.warning('Configured session naming is unavailable; the conversation can continue.',exc_info=True)

    def observe(self,event):
        if not self.hook or self.closed:return
        kind=event.get('type')
        if kind in {'generation.started', 'input.delivered'}:
            self.active=True
            if self.timer is None:
                self.timer=asyncio.create_task(self._periodic())
        elif kind in {'generation.failed', 'generation.detached', 'session.idle', 'session.closed'}:
            self.active=False
        if kind=='input.delivered' and event.get('source','user')=='user':
            if event.get('input_id'):self.delivered.add(event['input_id'])
        if kind=='generation.finished':
            self.active=bool(event.get('active_job_ids'))
        if not self.active and self.timer:
            self.timer.cancel()
            self.timer=None
        if kind!='generation.finished':return
        fresh=set(event.get('input_ids') or ()) & self.delivered - self.completed
        if not fresh:return
        self.turn_id=next((i for i in reversed(event.get('input_ids') or []) if i in fresh),None)
        self.completed.update(fresh)
        self.publish({'type':'session.naming.progress','completedInputs':sorted(self.completed)})
        self.schedule()

    async def _periodic(self):
        """Check ongoing work only; unchanged snapshots never spend a model call."""
        while not self.closed:
            await asyncio.sleep(self.refresh_seconds)
            if self.active:
                self.schedule(periodic=True)

    def schedule(self, *, periodic=False):
        if self.closed or (self.pending and not self.pending.done()):return
        count=len(self.completed)
        if not periodic and count<=self.last_attempt:return
        metadata=self.hook._load_metadata(self.directory)
        if not automatic_metadata(metadata):
            self.last_attempt=count
            return
        named=has_generated_or_manual_name(metadata)
        config=self.hook.config
        initial=not named and count>=config.initial_trigger_turn and self.hook._defer_counts.get(self.coordinator.session_id,0)<config.max_retries
        update=named and count>=config.initial_trigger_turn and (periodic or (count>=config.update_interval_turns and count//config.update_interval_turns>self.last_attempt//config.update_interval_turns))
        if not (initial or update):return
        self.last_attempt=count
        async def generate():
            try:
                if self.closed or (periodic and not self.active):return
                hook=await self._snapshot_hook(metadata, skip_unchanged=True)
                if self.closed or (periodic and not self.active):return
                if hook is not None:
                    await self._generate(hook)
            except Exception:
                log.warning('Session naming failed; keeping the existing title.',exc_info=True)
        self.pending=asyncio.create_task(generate())
        self.pending.add_done_callback(lambda _:self.schedule())

    async def _snapshot_hook(self, metadata, *, skip_unchanged=False):
        """Freeze sampled text and the selected connection without editing context."""
        hook=type(self.hook)(self.coordinator,self.hook.config)
        hook._defer_counts=getattr(self.hook, '_defer_counts', {})
        if hasattr(self.hook, '_stamped_providers'):
            hook._stamped_providers=self.hook._stamped_providers
        hook.refresh_title=has_generated_or_manual_name(metadata)
        # The naming hook produces a bounded bookend/sample string. Copy it
        # before the provider call so a concurrent user turn cannot change it.
        sample=getattr(hook,'_get_conversation_context',None)
        if callable(sample):
            context=await sample(self.directory,None,None)
            digest=hashlib.sha256((context or '').encode()).hexdigest()
            if not context or (skip_unchanged and digest==self.last_context):return None
            self.last_context=digest
            if metadata.get('name'):
                context=f"Current session name: {metadata['name']}\n\n{context}"
            async def snapshot(*args):return context
            hook._get_conversation_context=snapshot
        hook._load_metadata=lambda _:dict(metadata)
        get=getattr(self.coordinator,'get',None)
        if callable(get) and (providers:=get('providers')):
            selected=hook._select_session_provider(providers)
            hook._select_session_provider=lambda _:selected
        return hook

    async def _generate(self, hook):
        from ..execution_events import CALL_PURPOSE
        token=CALL_PURPOSE.set({'label':'Session naming','turnId':self.turn_id,'lifecycle':'background'})
        try:
            # Upstream's update mode changes descriptions only. Auto in the
            # app refreshes both title and description, with a stability hint.
            await hook._generate_name(self.coordinator.session_id,self.directory,is_update=False)
        finally:
            CALL_PURPOSE.reset(token)

    async def suggest(self):
        """One explicit naming call, with no transcript input or metadata write."""
        if not self.hook or self.closed:
            raise ValueError(self.unavailable)
        if self.pending and not self.pending.done():
            raise ValueError('A chat name is already being generated. Try again when it finishes.')
        before=self.hook._load_metadata(self.directory)
        candidate={}
        async def generate():
            hook=await self._snapshot_hook(before)
            if hook is None:return
            def capture(directory, metadata):
                candidate.update(metadata)
                return metadata
            hook._save_metadata=capture
            await self._generate(hook)
        # Surface the naming hook's own reason when it returns nothing.
        warnings=_LastWarning()
        hook_log=logging.getLogger(HOOK_MODULE)
        hook_log.addHandler(warnings)
        try:
            self.pending=asyncio.create_task(generate())
            await asyncio.shield(self.pending)
        finally:
            hook_log.removeHandler(warnings)
        if not candidate.get('name'):
            detail=f' Naming reported: {warnings.message}' if warnings.message else ''
            raise ValueError('No new name was returned. Keep the current name and try again later.'+detail)
        return {key:candidate[key] for key in ('name','description') if key in candidate} | {
            'name_revision':before.get('name_revision',0),'name_policy_revision':before.get('name_policy_revision',0)}

    async def close(self):
        self.closed=True
        if self.timer:
            self.timer.cancel()
            await asyncio.gather(self.timer,return_exceptions=True)
        if self.pending and not self.pending.done():
            try:await asyncio.wait_for(self.pending,15)
            except (asyncio.TimeoutError,asyncio.CancelledError):pass
