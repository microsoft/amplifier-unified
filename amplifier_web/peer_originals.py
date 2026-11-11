"""Demanded canonical peer originals, not a second history or authority store.

The caller supplies at most 100 wanted rows with the ORIGINAL message ``id``
(from the receipt, not a native display alias) and its input identity. A native
alias alone cannot establish an original ID's global uniqueness in one bounded
metadata walk: a colliding ID may precede the original.

Cold reads walk all metadata once, then read only unique requested non-native
rows. resource_files.project_detached also performs its independent full hash
pass. Warm file hits do neither whole-resource pass: fresh path signatures bind
bounded immutable cached bytes, decoded anew on every return. Inline hits still
fingerprint the inline index. No Detached/inline body is retained in the cache.

Counts include ALL roles, using PeerAttribution's input-identity precedence.
Consumers must join these counts, not pass originals as a complete transcript:
missing count keys mean UNPROVEN, not zero. Receipt/body/authority checks remain
PeerAttribution's responsibility. Callers own current index/owner admission and
must recheck it after asynchronous work; resident calls require writer exclusion.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
from dataclasses import dataclass
import math
import threading

from .media_saved_aliases import DisplayJSON, OVERSIZED, _indexed_evidence, valid_id
from .peer_attribution import input_identity
from .resource_files import detached_signature, project_detached
from .saved_message_window import MAX_BYTES, MAX_RECORD, resident_rows
from .transcript_window import _bounded_json, _OBJECT_LIMIT

ERROR = 'Complete canonical peer originals are unavailable.'
FIELDS = ('id', 'role', 'source', 'inputId', 'nativeInputId')
MAX_WANTED = 100
MAX_ROWS = 100_000


@dataclass(frozen=True)
class Proof:
    """Fresh originals and complete counts for exactly the requested keys.

    ``originals`` maps globally unique requested IDs to exact non-native rows.
    ``input_counts`` counts non-native occurrences; ``native_counts`` counts
    native occurrences. Both include non-user roles. No title or author is
    inferred here. Dictionaries/row bodies belong to the caller, not the cache.
    ``revision`` is exact cold source/index/signature evidence, or None for a
    freshly scanned mutable resident collection (never a reusable revision).
    """
    originals: dict
    id_counts: dict
    input_counts: dict
    native_counts: dict
    revision: tuple | None


def _metadata(row):
    if type(row) is not dict or not valid_id(dict.get(row, 'id')):
        raise ValueError(ERROR)
    result = {name: dict.get(row, name) for name in FIELDS}
    if any(type(value) not in (str, int, float, bool, type(None))
           or isinstance(value, str) and len(value) > 200
           or isinstance(value, float) and not math.isfinite(value)
           for value in result.values()):
        raise ValueError(ERROR)
    return result


def _wanted(rows):
    ids, inputs = set(), set()
    for index, row in enumerate(rows):
        if index >= MAX_WANTED:
            raise ValueError('Choose at most 100 canonical peer originals.')
        metadata = _metadata(row)
        ids.add(metadata['id'])
        identity = input_identity(metadata)
        if identity is not None:
            if not valid_id(identity):
                raise ValueError(ERROR)
            inputs.add(identity)
    return tuple(sorted(ids)), tuple(sorted(inputs))


class _Selection:
    """O(K) counts and first unique ID locators, never all-history ID sets."""
    def __init__(self, wanted):
        ids, inputs = wanted
        self.ids = dict.fromkeys(ids, 0)
        self.inputs = dict.fromkeys(inputs, 0)
        self.native = dict.fromkeys(inputs, 0)
        self.selected = {}

    def add(self, row, locator):
        message_id = row['id']
        if message_id in self.ids:
            self.ids[message_id] += 1
            if self.ids[message_id] == 1 and row.get('source') != 'native':
                self.selected[message_id] = locator
            else:
                self.selected.pop(message_id, None)
        identity = input_identity(row)
        if identity in self.inputs:
            counts = self.native if row.get('source') == 'native' else self.inputs
            counts[identity] += 1

    def proof(self, originals, revision):
        return Proof(originals, dict(self.ids), dict(self.inputs), dict(self.native), revision)


class _ScanStream:
    """Bound the metadata walk even if a file grows after its initial stat."""
    def __init__(self, stream, limit):
        self.stream, self.limit, self.read_bytes = stream, limit, 0

    def tell(self):
        return self.stream.tell()

    def read(self, size):
        raw = self.stream.read(min(size, self.limit - self.read_bytes + 1))
        self.read_bytes += len(raw)
        if self.read_bytes > self.limit:
            raise ValueError('Peer original scan exceeds the byte budget.')
        return raw


class _Rows(DisplayJSON):
    """Reuse the streaming scalar decoder; reject duplicate keys at every depth."""
    def __init__(self, stream):
        super().__init__(stream)
        self.nodes = 0

    def _step(self, depth):
        self.nodes += 1
        if depth > 64 or self.nodes > 100_000:
            raise ValueError('Peer original exceeds the object budget.')

    def _object(self, depth, fields=()):
        self.expect(b'{')
        names, result = set(), {}
        self.space()
        if self.peek() == b'}':
            self.take()
            return result
        while True:
            self._step(depth)
            self.space()
            name = self.string(capture=True)
            if name is OVERSIZED or name in names or len(names) >= 256:
                raise ValueError(ERROR)
            names.add(name)
            self.space()
            self.expect(b':')
            item = self.value(depth + 1, capture=name in fields)
            if name in fields:
                result[name] = item
            self.space()
            separator = self.take()
            if separator == b'}':
                return result
            if separator != b',':
                raise ValueError(ERROR)

    def value(self, depth=0, *, capture=False):
        self._step(depth)
        self.space()
        char = self.peek()
        if char == b'{':
            self._object(depth)
            return OVERSIZED
        if char == b'[':
            self.take()
            self.space()
            if self.peek() == b']':
                self.take()
                return OVERSIZED
            while True:
                self.value(depth + 1)
                self.space()
                separator = self.take()
                if separator == b']':
                    return OVERSIZED
                if separator != b',':
                    raise ValueError(ERROR)
        # Even skipped numeric values are small; refuse nonfinite conversion.
        item = super().value(depth, capture=capture or char != b'"')
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(ERROR)
        return item

    def rows(self):
        self.space()
        self.expect(b'[')
        self.space()
        if self.peek() == b']':
            self.take()
        else:
            while True:
                self.space()
                start = self.offset()
                self.nodes = 0
                row = self._object(0, FIELDS)
                size = self.offset() - start
                if size > MAX_RECORD:
                    raise ValueError('Peer original exceeds the record budget.')
                yield _metadata(row), (start, size)
                self.space()
                separator = self.take()
                if separator == b']':
                    break
                if separator != b',':
                    raise ValueError(ERROR)
        self.space()
        if self.peek():
            raise ValueError(ERROR)


def _read_selected(stream, selection):
    bodies, retained = {}, 0
    # Forward order also bounds inline seek work; never rewind once per key.
    for message_id, (offset, size) in sorted(selection.selected.items(), key=lambda item: item[1]):
        retained += size
        if size > MAX_RECORD or retained > MAX_BYTES:
            raise ValueError('Peer originals exceed the retained byte budget.')
        stream.seek(offset)
        chunks, remaining = [], size
        while remaining:
            chunk = stream.read(min(64 * 1024, remaining))
            if not chunk:
                raise ValueError(ERROR)
            chunks.append(chunk)
            remaining -= len(chunk)
        bodies[message_id] = b''.join(chunks)
    return bodies


def _decode(bodies):
    originals = {}
    for message_id, raw in bodies.items():
        row = _bounded_json(raw)
        if row is _OBJECT_LIMIT or type(row) is not dict or row.get('id') != message_id:
            raise ValueError(ERROR)
        originals[message_id] = row
    return originals


class PeerOriginals:
    """One instance-local, count/byte bounded cache; no live DB or file handles.

    Budgets reuse the saved-window 8 MiB scan and 4 MiB record limits, but not
    its 100-row history cutoff. MAX_ROWS also bounds fresh resident metadata
    walks. Exhaustion rejects the entire proof; gaps are never treated as empty.
    Constructor knobs may lower, not raise, these bounds.
    """
    def __init__(self, *, max_entries=32, max_cache_bytes=MAX_BYTES,
                 max_scan_bytes=MAX_BYTES, max_rows=MAX_ROWS):
        for value, minimum, maximum in (
                (max_entries, 0, 32), (max_cache_bytes, 0, MAX_BYTES),
                (max_scan_bytes, 1, MAX_BYTES), (max_rows, 1, MAX_ROWS)):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError('Invalid peer original budget.')
        self.max_entries, self.max_cache_bytes = max_entries, max_cache_bytes
        self.max_scan_bytes, self.max_rows = max_scan_bytes, max_rows
        self.cache = OrderedDict()
        self.retained_bytes = 0
        self.lock = threading.Lock()

    def _scan(self, stream, wanted):
        selection = _Selection(wanted)
        for index, (row, locator) in enumerate(_Rows(_ScanStream(stream, self.max_scan_bytes)).rows()):
            if index >= self.max_rows:
                raise ValueError('Peer original scan exceeds the row budget.')
            selection.add(row, locator)
        bodies = _read_selected(stream, selection)
        selection.selected.clear()  # Cache only counts and selected immutable bytes.
        return selection, bodies

    def cold(self, source, wanted_rows):
        wanted = _wanted(wanted_rows)
        with self.lock:
            signature = detached_signature(source)
            if len(source.indexed) > self.max_scan_bytes:
                raise ValueError('Peer original index exceeds the byte budget.')
            indexed_hash, indexed_bytes = _indexed_evidence(source.indexed)
            inline = signature == ('inline', source.identity)
            if (indexed_bytes > self.max_scan_bytes
                    or not inline and signature[2] > self.max_scan_bytes):
                raise ValueError('Peer original scan exceeds the byte budget.')
            if inline and indexed_hash != source.identity:
                raise ValueError('Artifact content conflicts with its identity.')
            identity = (str(source.directory) if source.directory is not None else None,
                        source.identity, indexed_hash, indexed_bytes)
            revision = (identity, signature)
            key = (revision, wanted)
            entry = self.cache.get(key)
            if entry is None:
                # Preview guards ask for one original from the displayed batch.
                # Reuse its complete counts, not another whole-file scan per link.
                for prior, candidate in reversed(self.cache.items()):
                    if (prior[0] == revision and set(wanted[0]).issubset(prior[1][0])
                            and set(wanted[1]).issubset(prior[1][1])):
                        key, entry = prior, candidate
                        break
            if entry is None:
                entry, validated_signature = project_detached(
                    source, lambda stream: self._scan(stream, wanted))
                if validated_signature != signature:
                    raise ValueError('Peer original source changed.')
            selection, bodies = entry
            result = Proof(_decode({mid: raw for mid, raw in bodies.items() if mid in wanted[0]}),
                           {mid: selection.ids[mid] for mid in wanted[0]},
                           {iid: selection.inputs[iid] for iid in wanted[1]},
                           {iid: selection.native[iid] for iid in wanted[1]}, revision)
            if detached_signature(source) != signature:
                raise ValueError('Peer original source changed.')
            if key in self.cache:
                self.cache.move_to_end(key)
            else:
                size = sum(map(len, bodies.values()))
                if self.max_entries and size <= self.max_cache_bytes:
                    while self.cache and (len(self.cache) >= self.max_entries
                                          or self.retained_bytes + size > self.max_cache_bytes):
                        _, (_, removed) = self.cache.popitem(last=False)
                        self.retained_bytes -= sum(map(len, removed.values()))
                    self.cache[key] = entry
                    self.retained_bytes += size
            return result

    def resident(self, rows, wanted_rows):
        """Fresh batch metadata walk; copy ONLY selected originals, never cache.

        The caller retains writer exclusion. A same-length edit, including a
        nested body/envelope edit, is observed on the next call. Passive reuse
        requires a content revision managed by the canonical owner, not this
        helper's object identity, collection length or last returned Proof.
        """
        wanted = _wanted(wanted_rows)
        if type(rows) not in (list, tuple) or len(rows) > self.max_rows:
            raise ValueError('Peer original scan exceeds the row budget.')
        selection = _Selection(wanted)
        for row in rows:
            selection.add(_metadata(row), row)
        selected = list(selection.selected.values())
        bounded, gaps, start = resident_rows(selected, len(selected), MAX_WANTED)
        if gaps or start or len(bounded) != len(selected):
            raise ValueError('Peer originals exceed the retained record/object budget.')
        originals = {row['id']: copy.deepcopy(row) for row in bounded}
        return selection.proof(originals, None)