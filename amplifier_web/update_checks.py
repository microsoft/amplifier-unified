"""Availability cache only. Installation always verifies its own exact inputs.

Manual checks bypass saved results, but join in-flight requests. Automatic
checks may reuse successes within their configured interval. Neither a cached
success nor a transport failure is evidence of a qualified installation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit

_scope = ContextVar("update_check_scope", default=None)


def access_scope():
    """Opaque access identity; never persist tokens, account names or key paths."""
    digest = hashlib.sha256()
    for name in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_HOST",
        "GIT_SSH_COMMAND",
        "SSH_AUTH_SOCK",
        "GH_CONFIG_DIR",
    ):
        digest.update(name.encode() + b"\0" + os.environ.get(name, "").encode() + b"\0")
    config = Path(os.environ.get("GH_CONFIG_DIR", Path.home() / ".config/gh"))
    for path in (
        config / "hosts.yml",
        config / "config.yml",
        Path.home() / ".gitconfig",
        Path.home() / ".git-credentials",
    ):
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unavailable>")
    return digest.hexdigest()


def identity(url):
    parsed = urlsplit(url)
    # Credentials remain part of the hashed identity, never the public label.
    return parsed._replace(
        path=parsed.path.rstrip("/").removesuffix(".git"), fragment=""
    ).geturl()


class CheckCache:
    def __init__(self, directory, *, clock=time.time):
        self.path = Path(directory) / "check-cache.json"
        self.clock = clock
        self.tasks = {}
        self.entries = {}
        self.semaphore = asyncio.Semaphore(5)
        self.client = None
        self.pending_refs = {}
        self.ref_batches = set()
        self.batch_tasks = set()
        self.stats = {}
        try:
            saved = json.loads(self.path.read_text())
            if (
                isinstance(saved, dict)
                and saved.get("version") == 1
                and isinstance(saved.get("entries"), dict)
            ):
                self.entries = {
                    key: value
                    for key, value in saved["entries"].items()
                    if isinstance(key, str)
                    and isinstance(value, dict)
                    and isinstance(value.get("checkedAt"), (int, float))
                    and "value" in value
                }
        except (OSError, ValueError, KeyError, TypeError):
            pass

    async def get(self, key, loader, *, fresh, ttl, scope):
        token = hashlib.sha256(
            json.dumps([scope, key], sort_keys=True).encode()
        ).hexdigest()
        active = _scope.get()
        seen = active[4] if active and active[0] is self else set()
        fresh = fresh and token not in seen
        # Cancellation of one UI request must not cancel work shared by another.
        if token in self.tasks:
            self.stats["joined"] = self.stats.get("joined", 0) + 1
            return await asyncio.shield(self.tasks[token])
        entry = self.entries.get(token)
        if not fresh and entry and 0 <= self.clock() - entry["checkedAt"] < ttl:
            self.stats["cached"] = self.stats.get("cached", 0) + 1
            return json.loads(json.dumps(entry["value"]))

        async def fetch():
            self.stats["requests"] = self.stats.get("requests", 0) + 1
            try:
                value = await loader()
                seen.add(token)
                self.entries[token] = {"checkedAt": self.clock(), "value": value}
                self.entries = dict(
                    sorted(self.entries.items(), key=lambda pair: pair[1]["checkedAt"])[
                        -512:
                    ]
                )
                from .deployment import write_private

                write_private(
                    self.path, json.dumps({"version": 1, "entries": self.entries})
                )
                return value
            except BaseException:
                # A failed fresh lookup cannot leave a reusable success behind.
                self.entries.pop(token, None)
                try:
                    from .deployment import write_private

                    write_private(
                        self.path, json.dumps({"version": 1, "entries": self.entries})
                    )
                except OSError:
                    pass
                raise
            finally:
                self.tasks.pop(token, None)

        task = self.tasks[token] = asyncio.create_task(fetch())
        # A request can outlive every UI waiter. Retrieve its exception even
        # then; remaining waiters still receive the same failure from await.
        task.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )
        return await asyncio.shield(task)

    async def git(self, url, ref, runner, environment, *, fresh, ttl, scope):
        effective = os.environ if environment is None else environment
        access = {
            name: value
            for name, value in effective.items()
            if name
            in {
                "HOME",
                "GIT_CONFIG_GLOBAL",
                "GIT_CONFIG_SYSTEM",
                "GIT_CONFIG_NOSYSTEM",
                "GIT_SSH_COMMAND",
                "GIT_SSH",
                "SSH_AUTH_SOCK",
                "SSH_ASKPASS",
                "GIT_ASKPASS",
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "GH_HOST",
                "GH_CONFIG_DIR",
            }
            or name.startswith("GIT_CONFIG_")
        }
        scope = hashlib.sha256(
            json.dumps([scope, access], sort_keys=True).encode()
        ).hexdigest()
        ref = ref.removeprefix("refs/heads/")
        pattern = (
            ref if ref == "HEAD" or ref.startswith("refs/") else "refs/heads/" + ref
        )

        async def load():
            key = (scope, identity(url))
            future = asyncio.get_running_loop().create_future()
            self.pending_refs.setdefault(key, []).append((pattern, future))
            if key not in self.ref_batches:
                self.ref_batches.add(key)
                task = asyncio.create_task(
                    self._git_batch(key, url, runner, environment)
                )
                self.batch_tasks.add(task)
                task.add_done_callback(self.batch_tasks.discard)
            return await future

        return await self.get(
            ["git", identity(url), ref], load, fresh=fresh, ttl=ttl, scope=scope
        )

    async def _git_batch(self, key, url, runner, environment):
        await asyncio.sleep(0)  # Gather sibling refs into one ls-remote request.
        requests = self.pending_refs.pop(key)
        self.ref_batches.remove(key)
        try:
            patterns = sorted({pattern for pattern, _ in requests})
            async with self.semaphore:
                output = await runner(
                    "git", "ls-remote", url, *patterns, env=environment, timeout=35
                )
            found = {}
            for line in output.splitlines():
                pair = line.split()
                if len(pair) == 2 and re.fullmatch("[a-f0-9]{40}", pair[0]):
                    found[pair[1]] = pair[0]
            for pattern, future in requests:
                if future.done():
                    continue
                if pattern in found:
                    future.set_result(found[pattern])
                else:
                    future.set_exception(ValueError("Remote ref unavailable"))
        except BaseException as error:  # noqa: BLE001 -- fan out transport failure/cancellation to every waiter
            for _, future in requests:
                if not future.done():
                    if isinstance(error, asyncio.CancelledError):
                        future.cancel()
                    else:
                        future.set_exception(error)

    async def index(self, name):
        import aiohttp

        if self.client is None or self.client.closed:
            self.client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        async with (
            self.semaphore,
            self.client.get("https://pypi.org/pypi/" + name + "/json") as response,
        ):
            response.raise_for_status()
            value = (await response.json())["info"]["version"]
            if not re.fullmatch(r"\d+\.\d+\.\d+", value):
                raise ValueError("Unsupported component release version")
            return value

    async def close(self):
        tasks = list(self.tasks.values()) + list(self.batch_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.client:
            await self.client.close()


@asynccontextmanager
async def checking(cache, *, fresh=True, ttl=14400):
    cache.stats = {}
    token = _scope.set((cache, fresh, ttl, access_scope(), set()))
    try:
        yield cache
    finally:
        _scope.reset(token)


async def cached(key, loader):
    scope = _scope.get()
    if scope is None:
        return await loader()
    cache, fresh, ttl, access, _ = scope
    return await cache.get(key, loader, fresh=fresh, ttl=ttl, scope=access)


async def git_revision(url, ref, runner, environment=None):
    scope = _scope.get()
    if scope is None:
        pattern = (
            ref if ref == "HEAD" or ref.startswith("refs/") else "refs/heads/" + ref
        )
        output = await runner(
            "git", "ls-remote", url, pattern, env=environment, timeout=35
        )
        for line in output.splitlines():
            pair = line.split()
            if (
                len(pair) == 2
                and pair[1] == pattern
                and re.fullmatch("[a-f0-9]{40}", pair[0])
            ):
                return pair[0]
        raise ValueError("Remote ref unavailable")
    cache, fresh, ttl, access, _ = scope
    return await cache.git(
        url, ref, runner, environment, fresh=fresh, ttl=ttl, scope=access
    )


async def index_latest(name, loader):
    scope = _scope.get()
    if scope is None:
        return await loader()
    cache = scope[0]

    async def load():
        import aiohttp

        try:
            return await cache.index(name)
        except (TimeoutError, aiohttp.ClientError, KeyError, TypeError) as error:
            raise ValueError("Component release could not be checked") from error

    return await cached(["registry", name], load)
