"""An isolated uv cache for a candidate whose Git checkout cannot read a blob."""
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
from pathlib import Path
from tempfile import TemporaryDirectory


@dataclass
class _CandidateCache:
    temporary: TemporaryDirectory | None = None


_candidate = ContextVar('_candidate_uv_cache', default=None)


@asynccontextmanager
async def candidate_cache():
    # Tasks launched by this validation share its recovery cache. Concurrent
    # validations and unrelated installs keep their own policy.
    candidate = _CandidateCache()
    token = _candidate.set(candidate)
    try:
        yield
    finally:
        _candidate.reset(token)
        if candidate.temporary is not None:
            await asyncio.to_thread(candidate.temporary.cleanup)


def child_environment(argv, env):
    candidate = _candidate.get()
    if candidate and candidate.temporary and Path(argv[0]).name.lower() in {'uv', 'uv.exe'}:
        # Probe subprocesses can install additional dependencies. Materialize
        # files so no installed environment depends on this temporary cache.
        return {**(os.environ if env is None else env),
                'UV_CACHE_DIR': candidate.temporary.name, 'UV_LINK_MODE': 'copy'}
    return env


def recover_checkout(argv, env, stderr):
    candidate = _candidate.get()
    if (candidate is None or Path(argv[0]).name.lower() not in {'uv', 'uv.exe'}
            or len(argv) < 2 or argv[1] not in {'lock', 'sync'}
            or not (stderr.missing_git_blob and stderr.failed_git_reset)):
        return False
    current = os.environ if env is None else env
    if ('--offline' in argv or '--no-cache' in argv or '--no-cache-dir' in argv
            or any(str(arg).split('=', 1)[0] == '--cache-dir' for arg in argv)
            or current.get('UV_OFFLINE', '').lower() in {'1', 'true', 'yes', 'on'}
            or current.get('UV_NO_CACHE', '').lower() in {'1', 'true', 'yes', 'on'}):
        return False
    if candidate.temporary is None:
        try:
            candidate.temporary = TemporaryDirectory(prefix='amplifier-uv-recovery-')
        except OSError:
            return False  # Keep the original command failure and its receipt.
    return current.get('UV_CACHE_DIR') != candidate.temporary.name
