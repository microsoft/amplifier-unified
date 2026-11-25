"""Read-only display pages for the legacy single-object execution resource."""
from itertools import batched
import json
import zlib

from . import resource_files
from .session_projection import stored_execution

PAGE_BYTES = 4 * 1024 * 1024
SOURCE_BYTES = 64 * 1024 * 1024
CHUNK_ROWS = 32
ROW_BYTES = 16 * 1024


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def _metadata(row):
    # Old app-only records can embed an entire tool or provider exchange.
    # A large body must not hide adjacent calls. Keep its identity and metrics
    # so that separately retained canonical recordings can still be inspected.
    fields = ('id', 'turnId', 'anchorMessageId', 'parentId', 'sessionId', 'rootSessionId',
              'producerId', 'generationId', 'admissionInputId', 'kind', 'label', 'phase',
              'status', 'startedAt', 'endedAt', 'tool', 'toolCallId', 'toolObservationId',
              'provider', 'model', 'revision', 'liveObservation', 'lifecycle', 'purpose')
    result = {key: row[key] for key in fields if key in row
              and (row[key] is None or type(row[key]) in (bool, int, float)
                   or isinstance(row[key], str) and len(row[key]) <= 200)}
    usage = row.get('usage')
    if isinstance(usage, dict):
        from .session_projection import ACCOUNTING_USAGE_FIELDS
        result['usage'] = {key: value for key, value in usage.items() if key in ACCOUNTING_USAGE_FIELDS
                           and (type(value) in (int, float) or isinstance(value, str) and len(value) <= 200)}
    # Keep exact placement identities, even for older app-only records
    # whose IDs were longer than the current live observation schema.
    for key in ('id', 'turnId', 'anchorMessageId', 'parentId', 'sessionId', 'rootSessionId'):
        if key in row and isinstance(row[key], str):
            result[key] = row[key]
    return result


def _bounded_row(row):
    encoded = _encoded(row)
    if len(encoded) <= ROW_BYTES:
        return encoded, False
    encoded = _encoded(_metadata(row))
    return (encoded if len(encoded) <= ROW_BYTES else None), True


class SavedRows:
    """Bounded immutable pages. Browser reads never touch the source or database."""

    def __init__(self, source_id, rows, budget):
        self.identity = ('saved-execution', source_id)
        self.chunks = []
        self.count = self.bytes = 0
        self.partial = False
        self.cached = None
        # Keep the latest complete pages if the retained display budget fills.
        def entries():
            for row in reversed(rows):
                value, partial = _bounded_row(row)
                self.partial |= partial
                if value is not None:
                    yield value

        for chunk in batched(entries(), CHUNK_ROWS):
            packed = zlib.compress(b'[' + b','.join(reversed(chunk)) + b']', 1)
            if self.bytes + len(packed) > budget:
                self.partial = True
                break
            self.chunks.append(packed)
            self.count += len(chunk)
            self.bytes += len(packed)

    def __len__(self):
        return self.count

    def __iter__(self):
        raise TypeError('Select a bounded saved Work range.')

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(self.count)
            if step != 1 or stop - start > 128:
                raise ValueError('Choose a bounded saved Work range.')
            return [self[index] for index in range(start, stop)]
        if type(key) is not int:
            raise TypeError('Saved Work positions must be integers.')
        index = key + self.count if key < 0 else key
        if not 0 <= index < self.count:
            raise IndexError(key)
        chunk, offset = divmod(self.count - index - 1, CHUNK_ROWS)
        if self.cached is None or chunk != self.cached[0]:
            self.cached = chunk, json.loads(zlib.decompress(self.chunks[chunk]))
        return self.cached[1][-offset - 1]


def source_size(source, signature):
    if signature[0] != 'inline':
        return signature[2]
    text = source.indexed
    if len(text) > SOURCE_BYTES or text.isascii():
        return len(text)
    # The SQLite representation is Unicode, while the resource limit is bytes.
    return sum(len(text[start:start + 8192].encode()) for start in range(0, len(text), 8192))


def open_tree(source, signature, size):
    """Hash-check and normalize v1 once on demand, retaining only display pages."""
    if size > SOURCE_BYTES:
        return {}, signature, 'input_limit'
    try:
        value = resource_files.read_detached(source, max_bytes=SOURCE_BYTES)
        if resource_files.detached_signature(source) != signature or not isinstance(value, dict):
            raise ValueError('Saved Work source changed.')
        normalized = stored_execution(value)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        # A separate native event page has already been admitted. An invalid
        # legacy resource supplies no steps and cannot suppress that page.
        return {}, signature, 'source_unavailable'
    fields = {key: normalized[key] for key in ('aggregateUsage', 'usage', 'currentTurnId')
              if key in normalized}
    size = len(_encoded(fields))
    partial = size > ROW_BYTES
    if partial:
        fields, size = {}, 0
    turns = []
    for row in reversed(normalized.get('turns', [])):
        encoded, shortened = _bounded_row(row)
        partial |= shortened
        if encoded is None:
            continue
        if shortened:
            row = json.loads(encoded)
        count = len(encoded)
        if size + count > PAGE_BYTES // 4:
            partial = True
            break
        size += count
        turns.append(row)
    turns.reverse()
    nodes = SavedRows(source.identity, normalized.get('nodes', []), PAGE_BYTES - size)
    tree = {**fields, 'turns': turns, 'nodes': nodes, '_savedTurns': {row['id']: row for row in turns}}
    return tree, signature, 'window_limit' if partial or nodes.partial else None
