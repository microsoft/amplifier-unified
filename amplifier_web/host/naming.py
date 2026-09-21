"""Adapt Foundation's naming hook to completed loop-live user generations.

The community hook owns prompting, context sampling, routing, parsing and retry
policy. This adapter supplies lifecycle scheduling and app-owned metadata I/O.
"""
import asyncio
import importlib
import logging
from pathlib import Path

from ..naming import read, automatic_metadata, accept_generated
from amplifier_foundation.session.metadata import has_generated_or_manual_name

log=logging.getLogger(__name__)


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
        rows=coordinator.config.get('hooks',[])
        row=next((r for r in rows if r.get('module')=='hooks-session-naming' and r.get('enabled',True)),None)
        if not row:return
        try:
            module=importlib.import_module('amplifier_module_hooks_session_naming')
            config=row.get('config') or {}
            keys=('initial_trigger_turn','update_interval_turns','max_name_length','max_description_length','max_retries','model_role')
            settings=module.SessionNamingConfig(**{k:config[k] for k in keys if k in config})
            if settings.initial_trigger_turn<1 or settings.update_interval_turns<1:raise ValueError('Invalid naming intervals')
            adapter=self
            class AppNamingHook(module.SessionNamingHook):
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
        if not self.hook:return
        if event.get('type')=='input.delivered' and event.get('source','user')=='user':
            if event.get('input_id'):self.delivered.add(event['input_id'])
        if event.get('type')!='generation.finished':return
        fresh=set(event.get('input_ids') or ()) & self.delivered - self.completed
        if not fresh:return
        self.turn_id=next((i for i in reversed(event.get('input_ids') or []) if i in fresh),None)
        self.completed.update(fresh)
        self.publish({'type':'session.naming.progress','completedInputs':sorted(self.completed)})
        self.schedule()

    def schedule(self):
        if self.pending and not self.pending.done():return
        count=len(self.completed)
        if count<=self.last_attempt:return
        metadata=self.hook._load_metadata(self.directory)
        # A custom name is a user choice, including legacy names without an
        # explicit source. Do not spend a model call proposing its replacement.
        if not automatic_metadata(metadata):
            self.last_attempt=count
            return
        named=has_generated_or_manual_name(metadata)
        config=self.hook.config
        initial=not named and count>=config.initial_trigger_turn and self.hook._defer_counts.get(self.coordinator.session_id,0)<config.max_retries
        update=named and count>=config.update_interval_turns and count//config.update_interval_turns>self.last_attempt//config.update_interval_turns
        if not (initial or update):return
        self.last_attempt=count
        turn_id=self.turn_id
        async def generate():
            from ..execution_events import CALL_PURPOSE
            token=CALL_PURPOSE.set({'label':'Session naming','turnId':turn_id,'lifecycle':'background'})
            try:
                await self.hook._generate_name(self.coordinator.session_id,self.directory,is_update=named)
            except Exception:
                log.warning('Session naming failed; keeping the existing title.',exc_info=True)
            finally:CALL_PURPOSE.reset(token)
        self.pending=asyncio.create_task(generate())
        self.pending.add_done_callback(lambda _:self.schedule())

    async def suggest(self):
        """One explicit naming call, with no transcript input or metadata write."""
        if not self.hook:
            raise ValueError('This conversation bundle does not provide automatic naming.')
        if self.pending and not self.pending.done():
            raise ValueError('A chat name is already being generated. Try again when it finishes.')
        before = self.hook._load_metadata(self.directory)
        candidate = {}
        hook = type(self.hook)(self.coordinator, self.hook.config)
        hook._load_metadata = lambda directory: {**before, 'name_source': 'fallback'}
        def capture(directory, metadata):
            candidate.update(metadata)
            return metadata
        hook._save_metadata = capture
        from ..execution_events import CALL_PURPOSE
        token = CALL_PURPOSE.set({'label': 'Session naming', 'turnId': self.turn_id, 'lifecycle': 'background'})
        try:
            await hook._generate_name(self.coordinator.session_id, self.directory, is_update=False)
        finally:
            CALL_PURPOSE.reset(token)
        if not candidate.get('name'):
            raise ValueError('No new name was returned. Keep the current name and try again later.')
        return {key: candidate[key] for key in ('name', 'description') if key in candidate} | {
            'name_revision': before.get('name_revision', 0), 'name_policy_revision': before.get('name_policy_revision', 0)}

    async def close(self):
        if self.pending and not self.pending.done():
            try:await asyncio.wait_for(self.pending,15)
            except (asyncio.TimeoutError,asyncio.CancelledError):pass
