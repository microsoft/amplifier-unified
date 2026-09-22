"""Resolve draft choices in an isolated probe, without a conversation or inference."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import signal

async def resolve_defaults(home, workspace, bundle=None, app_bundle=None, *, global_only=False):
    from .bundle_selection import defaults
    from .runtime import RuntimeManager
    chosen=bundle or defaults(home,workspace,app_bundle,global_only=global_only)['effective']
    command=RuntimeManager()._command(home=home)[:-1]+[str(Path(__file__).with_name('draft_defaults_probe.py'))]
    cwd=Path(workspace).expanduser().resolve()
    while not cwd.is_dir() and cwd.parent!=cwd:cwd=cwd.parent
    process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,start_new_session=True,cwd=cwd,env={**os.environ,'AMPLIFIER_WEB_HOME':str(home)})
    try:
        output,_=await asyncio.wait_for(process.communicate(json.dumps({'home':str(home),'workspace':workspace,'bundle':chosen,'globalOnly':global_only}).encode()),45)
        result=json.loads(output)
        if process.returncode or result.get('error'):raise ValueError('Draft default resolution failed')
        return {**result,'bundle':chosen,'globalOnly':global_only}
    finally:
        if process.returncode is None:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            await process.wait()
