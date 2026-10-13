"""Private model catalogs with expiry, restart reuse and single-flight refresh."""
import asyncio
import copy
from functools import lru_cache
import hashlib
import json
import os
import time
import uuid
from pathlib import Path


_UNQUALIFIED_GENERATION = uuid.uuid4().hex


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@lru_cache(maxsize=64)
def _receipt_digest(path,signature):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generation_identity(home):
    """Read-only identity of the qualified component generation and its graph."""
    from .updates import active_release
    from .runtime_environment import receipt_directory
    generation=active_release(home).get('current')
    receipt=receipt_directory(home,generation)
    files=[]
    for path in (receipt/'runtime.lock',receipt/'runtime-installed.json',receipt/'runtime-sources.json'):
        try:
            info=path.stat()
            signature=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
            files.append((path.name,_receipt_digest(str(path),signature)))
        except FileNotFoundError:files.append((path.name,None))
    # Before qualification there is no proof that an installed module stayed
    # at the same revision. Reuse locally, but not across worker/host restarts.
    qualified=any(name=='runtime-installed.json' and digest for name,digest in files)
    return fingerprint([generation,files,None if qualified else _UNQUALIFIED_GENERATION])


@lru_cache(maxsize=16)
def _qualified_provider_sources(home,identity):
    """Only installed provenance can equate an implicit import with a source."""
    from .updates import active_release
    from .runtime_environment import receipt_directory
    receipt=receipt_directory(home,active_release(home).get('current'))
    try:
        rows=json.loads((receipt/'runtime-installed.json').read_text())
        policy_file=receipt/'runtime-sources.json'
        policies=json.loads(policy_file.read_text()) if policy_file.exists() else {}
        result={}
        for row in rows:
            name=row['name']
            if not name.startswith('amplifier-module-provider-'):continue
            direct=row.get('directUrl',{});vcs=direct.get('vcs_info',{});cached=row.get('cacheSource',{})
            if cached and not cached.get('dirty'):
                url,ref,subdirectory=cached['url'],cached['ref'],cached.get('subdirectory','')
            elif vcs.get('vcs')=='git':
                url=direct['url'];ref=vcs.get('requested_revision') or vcs['commit_id']
                subdirectory=direct.get('subdirectory','')
                policy=policies.get(name,{})
                if policy.get('url')==url and policy.get('subdirectory','')==subdirectory:
                    ref=policy.get('ref') or ref
            else:continue  # Local/registry overrides cannot match a Git declaration.
            result[name.removeprefix('amplifier-module-')]='git+'+url+'@'+ref+(
                '#subdirectory='+subdirectory if subdirectory not in ('','.') else '')
        return result
    except (OSError,ValueError,TypeError,KeyError,AttributeError):return {}


def configuration_key(workspace, module, config, source=None, *, home=None):
    """Private, conservative identity shared by probes and mounted providers.

    Instance labels and priority do not change discovery. Every other provider
    setting, effective credential, credential file and source remains isolated.
    Missing environment values retain their reference, never match a mounted
    provider with a different materialized value, and never leak in the digest.
    """
    from .setup import PROVIDER_ENV, environment_credential
    from .host.config import expand_environment
    raw = copy.deepcopy(config or {})
    raw.pop('priority', None)
    credential = environment_credential(module, raw)
    if not raw.get(credential['field']) and credential['available']:
        raw[credential['field']] = '${' + credential['envVar'] + '}'
    def materialize(value):
        if isinstance(value, dict):return {key:materialize(item) for key,item in value.items()}
        if isinstance(value, list):return [materialize(item) for item in value]
        try:return expand_environment(value)
        except ValueError:return value
    raw = materialize(raw)
    # Ambient fallback credentials can affect providers even with an explicit
    # constructor field. Preserve those dependencies in both caller processes.
    environment = {name:os.environ.get(name) for name in PROVIDER_ENV.get(module, ())}
    def fields(value, prefix=''):
        if isinstance(value, dict):
            for name, item in value.items():yield from fields(item, prefix+'.'+name)
        elif isinstance(value, list):
            for index, item in enumerate(value):yield from fields(item, prefix+'.'+str(index))
        else:yield prefix, value
    leaves = list(fields(raw))
    files = {}
    for name,value in leaves:
        if ('token' in name or 'credential' in name) and ('file' in name or 'path' in name) and isinstance(value,str):
            try:
                path = Path(value).expanduser()
                if not path.is_absolute():path = Path(workspace)/path
                if path.is_file():
                    info = path.stat()
                    files[name] = fingerprint(path.read_bytes()) if info.st_size < 1_000_000 else (info.st_size,info.st_mtime_ns,info.st_ctime_ns)
            except (OSError,ValueError):
                files[name]='unreadable'  # Provider validation owns invalid paths.
    generation=generation_identity(home) if home is not None else None
    # Manifest defaults do not prove which implementation an implicit probe
    # imports. Unknown/local overrides deliberately keep a separate identity.
    source=source or (_qualified_provider_sources(str(home),generation).get(module) if home is not None else None)
    # Absolute/effective connection settings can share a catalog across projects.
    # Keep relative filesystem settings isolated by their resolution directory.
    relative=any(isinstance(value,str) and value and not Path(value).expanduser().is_absolute()
                 for name,value in leaves if any(part in name for part in ('path','file','directory')))
    scope=str(Path(workspace).resolve()) if relative or source and not str(source).startswith(('git+','https://','http://','/')) else None
    return fingerprint(['provider-catalog-v4',scope,module,source,generation,raw,environment,files])


def mounted_catalog_keys(home,workspace,rows,sources):
    return {row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-'):
        configuration_key(workspace,row['module'],row.get('config',{}),
            sources[row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-')],home=home)
        for row in rows}


def model_key(identity):
    return ('providers.models', identity)


class ProviderCatalog:
    def __init__(self, path=None, *, ttl=None, retry_after=60, clock=time.time,
                 max_entries=128, max_bytes=3_900_000, max_age=86400):
        self.entries = {}
        self.loaded_at = {}
        self.failed_at = {}
        self.pending = {}
        self.path = Path(path) if path else None
        self.ttl, self.retry_after, self.clock = ttl, retry_after, clock
        self.saved = {}
        self.max_entries, self.max_bytes, self.max_age = max_entries, max_bytes, max_age
        self.sizes = {}
        if self.path:
            try:
                if self.path.stat().st_size <= 4_000_000:
                    data = json.loads(self.path.read_text())
                    if data.get('version') == 1:
                        self.saved = {key: row for key, row in data.get('entries', {}).items()
                                      if isinstance(row, dict) and isinstance(row.get('at'), (int, float)) and isinstance(row.get('result'), dict)
                                      and 0 <= clock() - row['at'] < max_age}
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        self._trim()

    def _forget(self, key):
        self.entries.pop(key, None)
        self.loaded_at.pop(key, None)
        self.failed_at.pop(key, None)
        self.sizes.pop(key, None)

    def _trim(self):
        """Bound reusable discovery data, including direct puts and disk restores.

        Pending callers own their task's result even if it is too large to cache.
        Age is retention, not the independent freshness TTL or a history policy.
        """
        now = self.clock()
        # One identity budget spans lazy disk entries and resident failures.
        candidates = {identity: (row['at'], len(json.dumps(row).encode()) + len(identity) + 8)
                      for identity, row in self.saved.items()}
        for key in self.entries:
            identity = fingerprint(key)
            at = max(self.loaded_at.get(key, 0), self.failed_at.get(key, 0))
            candidates[identity] = (at, max(self.sizes.get(key, 0), candidates.get(identity, (0, 0))[1]))
        retained, size = set(), len(json.dumps({'version': 1, 'entries': {}}).encode())
        for identity, (at, cost) in sorted(reversed(list(candidates.items())), key=lambda item: item[1][0], reverse=True):
            if 0 <= now - at < self.max_age and len(retained) < self.max_entries and size + cost <= self.max_bytes:
                retained.add(identity)
                size += cost
        self.saved = {identity: row for identity, row in self.saved.items()
                      if identity in retained and 0 <= now - row['at'] < self.max_age}
        for key in list(self.entries):
            if fingerprint(key) not in retained:
                self._forget(key)

    def peek(self, key):
        at = self.loaded_at.get(key, self.failed_at.get(key))
        if at is not None and not 0 <= self.clock() - at < self.max_age:
            self._forget(key)
            self.saved.pop(fingerprint(key), None)
        if key not in self.entries:
            row = self.saved.get(fingerprint(key))
            if row and 0 <= self.clock() - row['at'] < self.max_age:
                self.entries[key] = (copy.deepcopy(row['result']), None)
                self.loaded_at[key] = row['at']
                self.sizes[key] = len(json.dumps(row).encode()) + len(fingerprint(key)) + 8
            elif row:
                self.saved.pop(fingerprint(key), None)
        value, _ = self.entries.get(key, (None, None))
        return copy.deepcopy(value)

    def fresh(self, key):
        self.peek(key)
        return key in self.loaded_at and (self.ttl is None or 0 <= self.clock() - self.loaded_at[key] < self.ttl)

    def discard(self, key):
        self._forget(key)
        self.saved.pop(fingerprint(key), None)
        self._persist()

    def put(self, key, result):
        """Accept an explicitly refreshed result with the same TTL as a load."""
        self.entries[key] = (copy.deepcopy(result), None)
        self.loaded_at[key] = self.clock()
        self.failed_at.pop(key, None)
        self.saved[fingerprint(key)] = {'at': self.loaded_at[key], 'result': copy.deepcopy(result)}
        self.sizes[key] = len(json.dumps(self.saved[fingerprint(key)]).encode()) + len(fingerprint(key)) + 8
        self._trim()
        self._persist()

    def _persist(self):
        if not self.path:
            return
        try:
            from .host.config import write_private
            write_private(self.path, json.dumps({'version': 1, 'entries': self.saved}))
        except OSError:
            pass  # A read-only cache must never prevent using a provider.

    async def get(self, key, loader, refresh=False):
        self.peek(key)
        if not refresh:
            if self.fresh(key):
                return self.peek(key)
            if key in self.failed_at and self.clock() - self.failed_at[key] < self.retry_after:
                _, error = self.entries[key]
                raise ValueError(error)
        if key not in self.pending:
            async def load():
                try:
                    result = await loader()
                    self.put(key, result)
                    return result
                except Exception as exc:
                    self.entries[key] = (self.peek(key), str(exc))
                    self.failed_at[key] = self.clock()
                    self.sizes[key] = len(json.dumps(self.entries[key]).encode()) + len(fingerprint(key)) + 8
                    self._trim()
                    raise
                finally:
                    self.pending.pop(key, None)
            self.pending[key] = asyncio.create_task(load())
        return copy.deepcopy(await asyncio.shield(self.pending[key]))

    async def close(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
