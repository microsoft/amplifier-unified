"""Reuse a qualified, updater-owned uv cache after an unreadable Git checkout."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path

from .staged_sources import owned_path


def _directory(path, home):
    if os.name == 'nt':
        from .windows_files import directory
        with directory(path, owned_root=home, create=True):
            pass
    else:
        owned_path(path, home).mkdir(mode=0o700, parents=True, exist_ok=True)


@dataclass
class CandidateCache:
    home: Path
    diagnostics: object
    recovered: set[Path] = field(default_factory=set)

    @property
    def root(self):
        return self.home / 'updates' / 'uv-recovery'

    async def location(self, argv, env, cwd, run, timeout):
        current = os.environ if env is None else env
        # Let uv resolve its default and config files, including edits to a
        # configured file at the same path. Healthy hosts never need this query.
        original = current.get('UV_CACHE_DIR')
        if not original or not os.path.isabs(original):
            options = []
            for index, value in enumerate(argv[1:], 1):
                flag = str(value).split('=', 1)[0]
                if flag in {'--project', '--directory', '--config-file'}:
                    options.append(value)
                    if str(value) == flag:
                        options.extend(argv[index + 1:index + 2])
                elif flag == '--no-config':
                    options.append(value)
            try:
                original = (await run(argv[0], 'cache', 'dir', *options, cwd=cwd, env=env,
                                      timeout=min(5, timeout), _redaction_env=dict(current))).strip()
            except (OSError, RuntimeError, TimeoutError, ValueError):
                return None
        if not os.path.isabs(original):
            return None
        key = hashlib.sha256(os.fsencode(os.path.normcase(os.path.normpath(original)))).hexdigest()[:32]
        return self.root / key / 'cache'

    def selected(self, cache):
        if cache is None:
            return False
        try:
            if cache in self.recovered or (cache.parent / 'qualified').is_dir():
                _directory(cache, self.home)
                return True
        except (OSError, ValueError):
            pass  # A stale or inaccessible cache hint cannot block a healthy update.
        return False

    async def prepare_retry(self, cache, argv, env, cwd, run, timeout, redaction_env):
        # A prior failed qualification may have left an unpublished cache.
        # Use uv's own locking to clear only this dedicated cache before reuse.
        already_exists = cache.exists()
        _directory(cache, self.home)
        used_private = (os.environ if env is None else env).get('UV_CACHE_DIR') == str(cache)
        if already_exists and (used_private or not self.selected(cache)):
            await self.diagnostics.run('ecosystem-uv-cache-clean', run, argv[0], 'cache', 'clean',
                                       '--cache-dir', str(cache), cwd=cwd, env=env, timeout=timeout,
                                       _redaction_env=redaction_env)


_candidate = ContextVar('_candidate_uv_cache', default=None)


@contextmanager
def candidate_cache(home, diagnostics):
    candidate = CandidateCache(Path(home), diagnostics)
    token = _candidate.set(candidate)
    try:
        yield
        # Publish after the entire validation succeeds. Remembering a cache is
        # an optimization: a marker write error cannot invalidate that result.
        for cache in candidate.recovered:
            try:
                _directory(cache.parent / 'qualified', candidate.home)
            except (OSError, ValueError):
                pass
    finally:
        _candidate.reset(token)


def for_command(argv, env):
    candidate = _candidate.get()
    if candidate is None or Path(argv[0]).name.lower() not in {'uv', 'uv.exe'}:
        return None
    current = os.environ if env is None else env
    if (any(str(arg).split('=', 1)[0] in {'--offline', '-n', '--no-cache', '--no-cache-dir',
                                           '--cache-dir'} for arg in argv[1:])
            or current.get('UV_OFFLINE', '').lower() in {'1', 'true', 'yes', 'on'}
            or current.get('UV_NO_CACHE', '').lower() in {'1', 'true', 'yes', 'on'}):
        return None
    return candidate


def environment(env, cache):
    return {**(os.environ if env is None else env), 'UV_CACHE_DIR': str(cache), 'UV_LINK_MODE': 'copy'}
