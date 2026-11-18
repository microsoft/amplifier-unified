"""Optional immutable historical payloads. No transport or execution authority."""
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

from .capsule import _local_path, validate_bytes, validate_public
from .evidence import canonical_bytes, _decode

CHUNK_BYTES = 256 * 1024
MAX_RESOURCE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_ITEMS = 10000
PAGE_ITEMS = 25
PAGE_BYTES = 256 * 1024
PLAN_BYTES = 2 * 1024 * 1024


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Exact SHA-256 required')
    return value


def _integer(value, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('Invalid payload byte/count bound')
    return value


class PayloadStore:
    """Caller-owned content-addressed disk store; all returned evidence is inactive."""

    def __init__(self, directory):
        self.directory = _local_path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def put(self, raw, expected_sha256=None):
        if not isinstance(raw, bytes) or len(raw) > PAGE_BYTES:
            raise ValueError('Payload chunk exceeds bounded read')
        validate_bytes(raw)
        identity = _hash(raw)
        if expected_sha256 is not None and identity != _digest(expected_sha256):
            raise ValueError('Payload integrity mismatch')
        path = _local_path(self.directory / identity)
        if path.exists():
            if self.read(identity, len(raw)) != raw:
                raise ValueError('Immutable payload collision')
            return identity
        fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=self.directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # Link is atomic and refuses to replace an already committed object.
            try:
                os.link(temporary, path)
            except FileExistsError:
                if self.read(identity, len(raw)) != raw:
                    raise ValueError('Immutable payload collision')
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            os.unlink(temporary)
        return identity

    def read(self, identity, size):
        from .capsule import _regular_bytes
        _integer(size, PAGE_BYTES)
        raw, _ = _regular_bytes(self.directory / _digest(identity), PAGE_BYTES)
        if len(raw) != size or _hash(raw) != identity:
            raise ValueError('Payload integrity mismatch')
        return raw

    def build(self, owner, revision, entries):
        """Stream explicit selections; missing content must be an explicit omission.

        Each selection is {id, path} or {id, reason}. Paths are never manifested.
        Source mutation refuses sealing; failures leave only inactive orphan blobs.
        """
        from .evidence import _validate_owner
        _validate_owner(owner, revision, 1)
        pages, rows, seen = [], [], set()
        total = count = omitted = 0

        def flush():
            if rows:
                raw = canonical_bytes(rows)
                pages.append({'sha256': self.put(raw), 'bytes': len(raw), 'items': len(rows)})
                rows.clear()

        for entry in entries:
            if not isinstance(entry, dict) or set(entry) not in ({'id', 'path'}, {'id', 'reason'}):
                raise ValueError('Explicit payload selection or omission required')
            identity = entry['id']
            if not isinstance(identity, str) or not identity or len(identity.encode()) > 1024 or identity in seen:
                raise ValueError('Unique bounded resource identity required')
            validate_public(identity)
            seen.add(identity)
            count += 1
            if count > MAX_ITEMS:
                raise ValueError('Payload manifest item bound exceeded')
            row = {'id': identity, 'executionAuthority': False}
            if 'reason' in entry:
                reason = entry['reason']
                if not isinstance(reason, str) or not reason.strip() or len(reason.encode()) > 4096:
                    raise ValueError('Explicit bounded omission reason required')
                validate_public(reason)
                row.update(disposition='omitted', reason=reason)
                omitted += 1
            else:
                path = _local_path(entry['path'])
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    before = os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_RESOURCE_BYTES:
                        raise ValueError('Regular bounded resource required; record explicit omission')
                    chunks, size, digest, tail = [], 0, hashlib.sha256(), b''
                    with os.fdopen(fd, 'rb', closefd=False) as stream:
                        while raw := stream.read(CHUNK_BYTES):
                            size += len(raw)
                            if size > MAX_RESOURCE_BYTES or total + size > MAX_TOTAL_BYTES:
                                raise ValueError('Payload byte budget exceeded; record explicit omission')
                            # Inspect signatures split across chunk boundaries too.
                            validate_bytes(tail + raw)
                            tail = raw[-4096:]
                            digest.update(raw)
                            chunks.append({'sha256': self.put(raw), 'bytes': len(raw)})
                    after = os.fstat(fd)
                    current = path.lstat()
                    if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino) or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != before.st_size:
                        raise ValueError('Source resource changed during capture')
                finally:
                    os.close(fd)
                total += size
                row.update(disposition='included', bytes=size, sha256=digest.hexdigest(), chunks=chunks)
            if rows and (len(rows) == PAGE_ITEMS or len(canonical_bytes([*rows, row])) > PAGE_BYTES):
                flush()
            rows.append(row)
        flush()
        body = {'version': 1, 'owner': owner, 'revision': revision, 'items': count,
                'bytes': total, 'omitted': omitted, 'pages': pages,
                'executionAuthority': False, 'disposition': 'partial' if omitted else 'complete'}
        raw = canonical_bytes(body)
        if len(raw) > PLAN_BYTES:
            raise ValueError('Payload root manifest exceeds bound')
        return {'sha256': _hash(raw), 'bodyJson': raw.decode('utf-8')}

    @staticmethod
    def verify_plan(plan, *, accept_partial=False):
        from .evidence import _validate_owner
        if not isinstance(plan, dict) or set(plan) != {'sha256', 'bodyJson'}:
            raise ValueError('Exact payload plan required')
        body = _decode(plan['bodyJson'], PLAN_BYTES)
        if _hash(plan['bodyJson'].encode()) != _digest(plan['sha256']):
            raise ValueError('Payload plan integrity mismatch')
        if not isinstance(body, dict) or set(body) != {'version', 'owner', 'revision', 'items', 'bytes', 'omitted', 'pages', 'executionAuthority', 'disposition'} or type(body['version']) is not int or body['version'] != 1 or body['executionAuthority'] is not False:
            raise ValueError('Historical payload manifest required')
        _validate_owner(body['owner'], body['revision'], 1)
        _integer(body['items'], MAX_ITEMS); _integer(body['bytes'], MAX_TOTAL_BYTES)
        _integer(body['omitted'], body['items'])
        if not isinstance(body['pages'], list) or len(body['pages']) > MAX_ITEMS:
            raise ValueError('Bounded manifest pages required')
        for page in body['pages']:
            if not isinstance(page, dict) or set(page) != {'sha256', 'bytes', 'items'}:
                raise ValueError('Exact manifest page descriptor required')
            _digest(page['sha256']); _integer(page['bytes'], PAGE_BYTES)
            if not 1 <= _integer(page['items'], PAGE_ITEMS):
                raise ValueError('Empty manifest page refused')
        if sum(page['items'] for page in body['pages']) != body['items'] or body['disposition'] != ('partial' if body['omitted'] else 'complete'):
            raise ValueError('Payload manifest counts/disposition mismatch')
        if body['omitted'] and not accept_partial:
            raise ValueError('Partial payload requires explicit preview acceptance')
        return body

    def page(self, plan, *, cursor=None, accept_partial=False):
        body = self.verify_plan(plan, accept_partial=accept_partial)
        position = 0
        if cursor is not None:
            if not isinstance(cursor, str) or not re.fullmatch(re.escape(plan['sha256']) + ':[0-9]{1,5}', cursor):
                raise ValueError('Cursor belongs to a different immutable plan')
            position = int(cursor.split(':')[1])
        if not body['pages'] and position == 0:
            return {'items': [], 'nextCursor': None, 'executionAuthority': False}
        if position >= len(body['pages']):
            raise ValueError('Manifest cursor out of range')
        descriptor = body['pages'][position]
        rows = _decode(self.read(descriptor['sha256'], descriptor['bytes']).decode(), PAGE_BYTES)
        if not isinstance(rows, list) or len(rows) != descriptor['items']:
            raise ValueError('Manifest page count mismatch')
        for row in rows:
            self._validate_row(row)
        return {'items': rows, 'nextCursor': f"{plan['sha256']}:{position + 1}" if position + 1 < len(body['pages']) else None, 'executionAuthority': False}

    @staticmethod
    def _validate_row(row):
        if not isinstance(row, dict) or row.get('executionAuthority') is not False or not isinstance(row.get('id'), str) or not row['id'] or len(row['id'].encode()) > 1024:
            raise ValueError('Invalid historical resource row')
        if row.get('disposition') == 'omitted':
            if set(row) != {'id', 'executionAuthority', 'disposition', 'reason'} or not isinstance(row['reason'], str) or not row['reason'].strip() or len(row['reason'].encode()) > 4096:
                raise ValueError('Explicit omission required')
        elif row.get('disposition') == 'included':
            if set(row) != {'id', 'executionAuthority', 'disposition', 'bytes', 'sha256', 'chunks'}:
                raise ValueError('Exact resource manifest required')
            _integer(row['bytes'], MAX_RESOURCE_BYTES); _digest(row['sha256'])
            if not isinstance(row['chunks'], list) or len(row['chunks']) > MAX_RESOURCE_BYTES // CHUNK_BYTES:
                raise ValueError('Resource chunk bound exceeded')
            for chunk in row['chunks']:
                if not isinstance(chunk, dict) or set(chunk) != {'sha256', 'bytes'}:
                    raise ValueError('Exact chunk descriptor required')
                _digest(chunk['sha256'])
                if not 1 <= _integer(chunk['bytes'], CHUNK_BYTES):
                    raise ValueError('Empty chunk refused')
            if sum(chunk['bytes'] for chunk in row['chunks']) != row['bytes']:
                raise ValueError('Resource byte count mismatch')
        else:
            raise ValueError('Explicit resource disposition required')

    def verify_resource(self, row):
        """Read only this resource, verifying whole bytes without replay or activation."""
        self._validate_row(row)
        validate_public(row)
        if row['disposition'] == 'omitted':
            return {'id': row['id'], 'status': 'omitted', 'reason': row['reason'], 'executionAuthority': False}
        digest, tail = hashlib.sha256(), b''
        for chunk in row['chunks']:
            raw = self.read(chunk['sha256'], chunk['bytes'])
            validate_bytes(tail + raw)
            tail = raw[-4096:]
            digest.update(raw)
        if digest.hexdigest() != row['sha256']:
            raise ValueError('Whole resource integrity mismatch')
        return {'id': row['id'], 'status': 'verified', 'bytes': row['bytes'], 'sha256': row['sha256'], 'executionAuthority': False}

    def audit(self, plan, *, accept_partial=False):
        """Explicit streaming review of every selected body; never an admission scan."""
        body = self.verify_plan(plan, accept_partial=accept_partial)
        cursor, seen, total, omitted = None, set(), 0, 0
        while True:
            page = self.page(plan, cursor=cursor, accept_partial=accept_partial)
            for row in page['items']:
                if row['id'] in seen:
                    raise ValueError('Duplicate resource identity across manifest pages')
                seen.add(row['id'])
                total += row.get('bytes', 0)
                omitted += row['disposition'] == 'omitted'
                if total > MAX_TOTAL_BYTES or total > body['bytes']:
                    raise ValueError('Manifest aggregate byte budget exceeded')
            cursor = page['nextCursor']
            if cursor is None:
                break
        if total != body['bytes'] or omitted != body['omitted'] or len(seen) != body['items']:
            raise ValueError('Manifest aggregate counts mismatch')
        # All immutable metadata must qualify before even the first body read.
        # Re-read bounded pages rather than retaining every resource descriptor.
        cursor = None
        while True:
            page = self.page(plan, cursor=cursor, accept_partial=accept_partial)
            for row in page['items']:
                self.verify_resource(row)
            cursor = page['nextCursor']
            if cursor is None:
                break
        return {'sha256': plan['sha256'], 'items': len(seen), 'bytes': total,
                'omitted': omitted, 'disposition': body['disposition'],
                'executionAuthority': False, 'replayed': False}
