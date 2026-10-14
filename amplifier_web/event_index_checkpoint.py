"""Bounded, disposable event-index checkpoints in the host's cache directory.

Canonical events remain the authority. A checkpoint retains parsed descriptors,
not raw provider bodies, so evicting a large RAM index need not parse the entire
log again. EventIndex still validates source identity/tail before incremental use.
"""
from collections import OrderedDict
import gzip
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import zlib

SCHEMA = 2
MAX_DECODED = 64 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_FILES = 1024
WRITE_BUFFER = 64 * 1024
MAX_SCALAR_CHARS = 256 * 1024
MAX_DEPTH = 64


class CheckpointTooLarge(ValueError):
    """The optional checkpoint exceeds a streaming serialization bound."""


def check_scalars(value, depth=0):
    # iterencode streams containers, but encodes each string as one chunk.
    # Bound that chunk before encoding, with only a bounded recursion stack.
    if depth > MAX_DEPTH:
        raise CheckpointTooLarge
    if isinstance(value, str):
        if len(value) > MAX_SCALAR_CHARS:
            raise CheckpointTooLarge
    elif isinstance(value, dict):
        for key, item in value.items():
            check_scalars(key, depth + 1)
            check_scalars(item, depth + 1)
    elif isinstance(value, (tuple, list)):
        for item in value:
            check_scalars(item, depth + 1)


class BoundedOutput:
    """Count gzip headers, compressed chunks and trailer before writing."""
    def __init__(self, stream):
        self.stream, self.written = stream, 0

    def write(self, value):
        if self.written + len(value) > MAX_FILE:
            raise CheckpointTooLarge
        written = self.stream.write(value)
        self.written += written
        return written

    def flush(self):
        self.stream.flush()


def tuples(value):
    return tuple(tuples(item) for item in value) if isinstance(value, list) else value


def encode(index):
    events = []
    for row in index.association_events:
        data = row.get('data', {})
        digest = data.get('_prompt_digest')
        events.append({**row, 'data': {**data, '_prompt_digest': digest.hex()}}
                      if isinstance(digest, bytes) else row)
    return {'schema': SCHEMA, 'path': str(index.path), 'identity': index.identity,
        **{key: getattr(index, key) for key in ('offset', 'file_id', 'revision', 'last', 'prefix_digest',
           'children', 'worker_runs', 'tool_owners', 'diagnostics', 'association_revision', 'association_cache')},
        'nodes': list(index.nodes.items()), 'pending': list(index.pending.items()), 'association_events': events}


def restore(index, value):
    if value.get('schema') != SCHEMA or value.get('path') != str(index.path) or value.get('identity') != index.identity:
        raise ValueError('Different index source or format')
    if type(value.get('offset')) is not int or value['offset'] < 0:
        raise ValueError('Invalid index offset')
    index.offset = value['offset']
    if (not isinstance(value.get('prefix_digest'), str) or len(value['prefix_digest']) != 64
            or any(char not in '0123456789abcdef' for char in value['prefix_digest'])):
        raise ValueError('Invalid committed prefix digest')
    index.prefix_digest = value['prefix_digest']
    for key in ('file_id', 'revision', 'association_revision'):
        setattr(index, key, tuples(value[key]))
    for key in ('last', 'children', 'worker_runs', 'tool_owners', 'diagnostics', 'association_events'):
        setattr(index, key, value[key])
    index.nodes = OrderedDict(value['nodes'])
    index.pending = {tuple(key): [index.nodes.get(row['id'], row) for row in rows]
                     for key, rows in value['pending']}
    # These maps originally share the same worker descriptor objects.
    index.children = {key: index.worker_runs.get(row['id'], row) for key, row in index.children.items()}
    index.association_cache = {int(key): row for key, row in value['association_cache'].items()}
    for row in index.association_events:
        digest = row.get('data', {}).get('_prompt_digest')
        if isinstance(digest, str):
            if len(digest) > 128:
                raise ValueError('Invalid prompt fingerprint')
            row['data']['_prompt_digest'] = bytes.fromhex(digest)
    # A cache cannot redirect an exact payload read to another source file.
    for row in [*index.nodes.values(), *(row for rows in index.pending.values() for row in rows)]:
        for reference in row.get('_eventFields', {}).values():
            if reference.get('path') != str(index.path):
                raise ValueError('Invalid indexed event source')
    if index.last and index.last.get('path') != str(index.path):
        raise ValueError('Invalid indexed tail source')
    index._checkpoint_signature = (index.revision, index.association_revision)


class Checkpoints:
    def __init__(self, home):
        self.directory = Path(home) / 'cache' / 'event-index-v1'

    def path(self, source, identity):
        key = hashlib.sha256((str(source) + '\0' + identity).encode()).hexdigest()
        return self.directory / (key + '.json.gz')

    def load(self, index):
        path = self.path(index.path, index.identity)
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
                return False
            with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as stream:
                with gzip.GzipFile(fileobj=stream) as compressed:
                    raw = compressed.read(MAX_DECODED + 1)
            if len(raw) > MAX_DECODED:
                return False
            restore(index, json.loads(raw))
            os.utime(path, None, follow_symlinks=False)
            return True
        except (OSError, ValueError, TypeError, KeyError, AttributeError, EOFError, zlib.error):
            index.reset()
            return False

    def save(self, index):
        signature = (index.revision, index.association_revision)
        if getattr(index, '_checkpoint_signature', None) == signature:
            return
        payload = encode(index)
        try:
            check_scalars(payload)
        except CheckpointTooLarge:
            return
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.path(index.path, index.identity)
        descriptor, temporary = tempfile.mkstemp(prefix='.index-', dir=self.directory)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                with gzip.GzipFile(filename='', mode='wb', fileobj=BoundedOutput(stream),
                                   compresslevel=1, mtime=0) as compressed:
                    decoded, buffer = 0, bytearray()
                    encoder = json.JSONEncoder(separators=(',', ':'), ensure_ascii=False)
                    for chunk in encoder.iterencode(payload):
                        piece = chunk.encode()
                        decoded += len(piece)
                        if decoded > MAX_DECODED:
                            raise CheckpointTooLarge
                        buffer.extend(piece)
                        if len(buffer) >= WRITE_BUFFER:
                            compressed.write(buffer)
                            buffer.clear()
                    if buffer:
                        compressed.write(buffer)
            os.replace(temporary, path)
        except CheckpointTooLarge:
            return
        finally:
            Path(temporary).unlink(missing_ok=True)
        index._checkpoint_signature = signature
        self.prune()

    def prune(self):
        rows = []
        for path in self.directory.glob('*.json.gz'):
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode):
                    rows.append((info.st_mtime_ns, info.st_size, path))
            except FileNotFoundError:
                pass
        size, count = 0, 0
        for _, length, path in sorted(rows, reverse=True):
            size += length
            count += 1
            if size > MAX_TOTAL or count > MAX_FILES:
                path.unlink(missing_ok=True)
