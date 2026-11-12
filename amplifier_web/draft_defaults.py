"""Resolve draft choices in an isolated probe, without a conversation or inference."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
from .probe_process import spawn, stop

async def resolve_defaults(home, workspace, bundle=None, app_bundle=None, *, global_only=False, session_id=None, catalog=False, refresh=False, replacement=None, desired_bundle=False, catalog_cache=None, instance=None, selected_only=False, runtime_session=None):
    if catalog:
        from .setup import SetupManager
        manager = SetupManager(home, catalog=catalog_cache, global_only=global_only, allow_missing_workspace=True)
        return await manager.discover_catalog(workspace, refresh=refresh, instance=instance, selected_only=selected_only)
    from .bundle_selection import defaults
    from .runtime import RuntimeManager
    chosen=bundle or defaults(home,workspace,app_bundle,global_only=global_only)['effective']
    from .host.config import worker_environment
    from .updates import active_release
    generation = active_release(home).get('current')
    reservations = []
    process = None
    spawn_failed = False
    try:
        if runtime_session is not None:
            # Only genuine input preparation qualifies a candidate. Read-only
            # UI previews neither install dependencies nor alter a conversation.
            from .runtime_profiles import ensure
            from .generation_leases import acquire
            reservations.append(acquire(home, generation, os.getpid()))
            # Qualification needs identity and placement, never saved history.
            candidate = {key: runtime_session[key] for key in (
                'id', 'workspace', 'workingDirectory', 'runtimeSessionId', 'nativeIdentity')
                if key in runtime_session}
            candidate.update(bundle=chosen, bundleReplacement={'bundle': chosen})
            generation = await asyncio.wait_for(ensure(home, generation, candidate),
                                               RuntimeManager().preparation_timeout)
            reservations.append(acquire(home, generation, os.getpid()))
        command=RuntimeManager()._command(generation, home=home)[:-1]+[str(Path(__file__).with_name('draft_defaults_probe.py'))]
        cwd=Path(workspace).expanduser().resolve()
        while not cwd.is_dir() and cwd.parent!=cwd:cwd=cwd.parent
        environment = {**worker_environment(), 'AMPLIFIER_WEB_HOME': str(home),
                       'AMPLIFIER_UNIFIED_RELEASE': generation or ''}
        try:
            process=await spawn(*command,owned_git=True,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,cwd=cwd,env=environment)
        except BaseException:
            # A native spawn failure can retain unconfirmed process custody.
            # Keep the lease conservatively; never infer no process from None.
            spawn_failed = True
            raise
        output,_=await asyncio.wait_for(process.communicate(json.dumps({'home':str(home),'workspace':workspace,'bundle':chosen,'globalOnly':global_only,'sessionId':session_id,'catalog':catalog,'refresh':refresh,'replacement':replacement,'desiredBundle':desired_bundle}).encode()),150 if catalog or replacement is not None else 45)
        result=json.loads(output)
        if process.returncode or result.get('error'):raise ValueError('Draft default resolution failed')
        return {**result,'bundle':chosen,'globalOnly':global_only}
    finally:
        try:
            if process is not None:
                await stop(process)
        finally:
            # If stop itself is unconfirmed, retain generation protection.
            if not spawn_failed and (process is None or process.returncode is not None):
                for reservation in reservations:
                    if reservation:
                        try:
                            reservation.unlink(missing_ok=True)
                        except OSError:
                            pass  # Retain protection, never mask the original.
