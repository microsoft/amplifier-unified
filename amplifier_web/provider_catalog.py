"""Private model catalogs with expiry, restart reuse and single-flight refresh."""
import asyncio
import copy
import hashlib
import json
import time
from pathlib import Path


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class ProviderCatalog:
    def __init__(self, path=None, *, ttl=86400, retry_after=60, clock=time.time):
        self.entries = {}
        self.loaded_at = {}
        self.failed_at = {}
        self.pending = {}
        self.path = Path(path) if path else None
        self.ttl, self.retry_after, self.clock = ttl, retry_after, clock
        self.saved = {}
        if self.path:
            try:
                if self.path.stat().st_size <= 4_000_000:
                    data = json.loads(self.path.read_text())
                    if data.get('version') == 1:
                        self.saved = {key: row for key, row in data.get('entries', {}).items()
                                      if isinstance(row, dict) and isinstance(row.get('at'), (int, float)) and isinstance(row.get('result'), dict)
                                      and 0 <= clock() - row['at'] < 30 * 86400}
            except (OSError, ValueError, TypeError, AttributeError):
                pass

    def peek(self, key):
        if key not in self.entries:
            row = self.saved.get(fingerprint(key))
            if row:
                self.entries[key] = (copy.deepcopy(row['result']), None)
                self.loaded_at[key] = row['at']
        value, _ = self.entries.get(key, (None, None))
        return copy.deepcopy(value)

    def fresh(self, key):
        self.peek(key)
        return key in self.loaded_at and 0 <= self.clock() - self.loaded_at[key] < self.ttl

    def discard(self, key):
        self.entries.pop(key, None)
        self.loaded_at.pop(key, None)
        self.failed_at.pop(key, None)
        self.saved.pop(fingerprint(key), None)
        self._persist()

    def _persist(self):
        if not self.path:
            return
        # Bound private disk and memory usage; only digests and probe results are saved.
        rows = sorted(self.saved.items(), key=lambda item: item[1]['at'], reverse=True)
        self.saved = {}
        size = 0
        for key, row in rows[:128]:
            encoded = len(json.dumps(row).encode()) + len(key) + 8
            if size + encoded > 3_900_000:
                continue
            self.saved[key] = row
            size += encoded
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
                    self.entries[key] = (copy.deepcopy(result), None)
                    self.loaded_at[key] = self.clock()
                    self.failed_at.pop(key, None)
                    self.saved[fingerprint(key)] = {'at': self.loaded_at[key], 'result': copy.deepcopy(result)}
                    self._persist()
                    # Keep at most 256 inactive in-memory entries.
                    for old in list(self.entries)[:-256]:
                        if old not in self.pending:
                            self.entries.pop(old, None)
                            self.loaded_at.pop(old, None)
                            self.failed_at.pop(old, None)
                    return result
                except Exception as exc:
                    self.entries[key] = (self.peek(key), str(exc))
                    self.failed_at[key] = self.clock()
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
