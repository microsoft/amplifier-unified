"""Fixture-only subprocess guard: exact, owned display readers is allowed."""
import asyncio
from contextvars import ContextVar
from pathlib import Path
import re
import subprocess
import sys


def install(cache_home, forbidden, observed):
    home = Path(cache_home).resolve()
    original_exec, original_shell, original_popen = (
        asyncio.create_subprocess_exec, asyncio.create_subprocess_shell, subprocess.Popen)
    expected = ContextVar('inline_media_display_spawn', default=None)

    def valid(args, kwargs):
        if (len(args) != 9 or args[:2] != (sys.executable, '-m')
                or args[2] not in {'amplifier_web.history_projection_worker',
                                   'amplifier_web.shell_query_worker'}):
            return False
        if args[3] != '--spool' or args[5] != '--cache-home' or args[7:] != ('--memory-limit', '805306368'):
            return False
        if kwargs != dict(stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                          stderr=asyncio.subprocess.DEVNULL, limit=4096):
            return False
        try:
            spool, cache = Path(args[4]), Path(args[6])
            return (cache == home and cache.resolve(strict=True) == home
                    and spool.is_dir() and spool.resolve(strict=True) == spool
                    and spool.parent.parent == home
                    and spool.parent.name.startswith('projection-spool-')
                    and bool(re.fullmatch('[0-9a-f]{32}', spool.name)))
        except (OSError, TypeError, ValueError):
            return False

    async def guarded_exec(*args, **kwargs):
        if not valid(args, kwargs):
            return forbidden('subprocess')
        token = expected.set(args)
        try:
            process = await original_exec(*args, **kwargs)
            observed()
            return process
        finally:
            expected.reset(token)

    async def guarded_shell(*args, **kwargs):
        return forbidden('subprocess')

    def guarded_popen(args, *extra, **kwargs):
        # asyncio's transport calls Popen within this task's context. Other
        # tasks, direct Popen callers, and substituted commands remain denied.
        if expected.get() is None or tuple(args) != expected.get():
            return forbidden('subprocess')
        return original_popen(args, *extra, **kwargs)

    asyncio.create_subprocess_exec = guarded_exec
    asyncio.create_subprocess_shell = guarded_shell
    subprocess.Popen = guarded_popen

    def restore():
        asyncio.create_subprocess_exec = original_exec
        asyncio.create_subprocess_shell = original_shell
        subprocess.Popen = original_popen

    return restore
