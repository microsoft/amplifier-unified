"""Bounded, requested event observations; never an execution/accounting tree.

This reader runs in the disposable history worker. It does not construct an
EventIndex, read a transcript, or follow a delegation. A cut prefix cannot prove
global call/prompt uniqueness. Raw observations retain their source identity;
only a complete bounded root plus existing host-index receipts can provide
separate display bindings. Neither path invents accounting or execution authority.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import sqlite3
import stat

from .session_files import amplifier_home, project_slug

MAX_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 512
MAX_ITEMS = 100
BLOCK_BYTES = 64 * 1024
PREVIEW_CHARS = 512
MAX_CURSOR_BYTES = 2048
FIELD_KEYS = frozenset({'tool_input', 'arguments', 'input', 'prompt', 'result',
                        'tool_result', 'error', 'error_message', 'raw', 'raw_request', 'raw_response'})


class StaleWindow(ValueError):
    """The exact owner, requested window or canonical source changed."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'), sort_keys=True).encode()


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _identity(value):
    return (isinstance(value, str) and 0 < len(value.encode('utf-8')) <= 512
            and value not in {'.', '..'} and not any(c in value for c in '/\\\0'))


def _source(session):
    public = session.get('id')
    identity = session.get('nativeIdentity') or session.get('runtimeSessionId') or public
    project = session.get('nativeProject')
    if project is None:
        workspace = session.get('workspace')
        if not isinstance(workspace, str) or not workspace:
            raise ValueError('Missing canonical workspace')
        project = project_slug(workspace)
    if not all(_identity(value) for value in (public, identity, project)):
        raise ValueError('Invalid canonical source identity')
    configured = os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', '').strip()
    base = Path(configured).expanduser() if configured and '${' not in configured else None
    base = base if base is not None and base.is_absolute() else amplifier_home() / 'projects'
    base = base.resolve()
    path = base / project / 'sessions' / identity / 'context-intelligence' / 'events.jsonl'
    if not path.resolve().is_relative_to(base):
        raise ValueError('Canonical events escaped their configured owner')
    return path, identity, _digest([public, identity, project, str(base)])


def _stamp(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _cursor(key, owner, window, stamp, end, aligned=True):
    raw = _json({'v': 1, 'owner': owner, 'window': window, 'source': stamp,
                 'end': end, 'aligned': aligned})
    return base64.urlsafe_b64encode(hmac.digest(key, raw, 'sha256') + raw).decode().rstrip('=')


def _end(before, key, owner, window, stamp):
    if before is None:
        return stamp[2], True
    if not isinstance(before, str) or not 0 < len(before) <= MAX_CURSOR_BYTES:
        raise StaleWindow('Invalid event window cursor')
    try:
        decoded = base64.b64decode(before + '=' * (-len(before) % 4), altchars=b'-_', validate=True)
        signature, raw = decoded[:32], decoded[32:]
        if not hmac.compare_digest(signature, hmac.digest(key, raw, 'sha256')):
            raise ValueError('Invalid signature')
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise StaleWindow('Invalid event window cursor') from exc
    if (not isinstance(value, dict) or set(value) != {'v', 'owner', 'window', 'source', 'end', 'aligned'}
            or type(value['v']) is not int or value['v'] != 1
            or value['owner'] != owner or value['window'] != window
            or value['source'] != stamp or type(value['end']) is not int
            or type(value['aligned']) is not bool
            or not 0 <= value['end'] <= stamp[2]):
        raise StaleWindow('The requested event window changed')
    return value['end'], value['aligned']


def _bounded_json(raw, guard):
    """Bound structural expansion before Python allocates decoded containers."""
    quoted = escaped = False
    depth = values = 0
    for position, byte in enumerate(raw):
        if position % 16384 == 0:
            guard()
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            values += 1
            if depth > 64:
                raise ValueError('Record shape exceeds this display window')
        elif byte in (93, 125):
            depth -= 1
        elif byte in (44, 58):
            values += 1
        if values > 65536:
            raise ValueError('Record shape exceeds this display window')
    def reject(_value):
        raise ValueError('Non-finite JSON value')
    return json.loads(raw, parse_constant=reject)


def _preview(value):
    if isinstance(value, str):
        return value[:PREVIEW_CHARS]
    pieces, left = [], PREVIEW_CHARS
    for piece in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(value):
        pieces.append(piece[:left])
        left -= len(piece)
        if left <= 0:
            break
    return ''.join(pieces)


def _scalar(value):
    return value if isinstance(value, str) and 0 < len(value.encode('utf-8')) <= 512 else None


def _time(value):
    if type(value) in (int, float):
        return value if math.isfinite(value) else None
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _observation(event, raw, offset, path, identity):
    if not isinstance(event, dict) or not isinstance(event.get('data'), dict):
        return None
    name, data = event.get('event'), event['data']
    if not isinstance(name, str) or len(name) > 128:
        return None
    if name.startswith('tool:'):
        kind = 'tool'
    elif name.startswith(('llm:', 'provider:')):
        kind = 'llm'
    elif name in {'delegate:agent_spawned', 'delegate:agent_resumed'}:
        kind = 'worker'
    elif name == 'prompt:submit':
        kind = 'prompt'
    else:
        return None
    sha = hashlib.sha256(raw).hexdigest()
    reference = {'path': str(path), 'offset': offset, 'bytes': len(raw), 'sha256': sha}
    row = {'id': f'event:{identity}:{offset}:{sha[:16]}', 'kind': kind,
           'event': name, 'sourceSessionId': identity,
           'sessionId': _scalar(data.get('session_id') or event.get('session_id')) or identity,
           'eventOrder': offset, 'phase': 'recorded', 'observation': True,
           'association': 'unverified', '_eventFields': {}}
    for target, keys in {
        'toolCallId': ('tool_call_id', 'call_id'), 'requestId': ('request_id',),
        'messageId': ('message_id',), 'inputId': ('input_id',),
        'childSessionId': ('sub_session_id',), 'parentSessionId': ('parent_session_id',),
        'provider': ('provider',), 'model': ('model',),
        'label': ('tool_name', 'tool', 'agent'),
    }.items():
        value = next((_scalar(data[key]) for key in keys if _scalar(data.get(key))), None)
        if value is not None:
            row[target] = value
    row.setdefault('label', {'llm': 'Model observation', 'tool': 'Tool observation',
                            'worker': 'Worker observation', 'prompt': 'Saved input'}[kind])
    at = _time(event.get('timestamp') if event.get('timestamp') is not None else data.get('timestamp'))
    if at is not None:
        row['createdAt'] = at
    for field, keys in {
        'input': ('tool_input', 'arguments', 'input', 'prompt'),
        'output': ('result', 'tool_result'), 'error': ('error', 'error_message'),
        'request': ('raw', 'raw_request') if name == 'llm:request' else (),
        'response': ('raw', 'raw_response') if name == 'llm:response' else (),
    }.items():
        key = next((key for key in keys if key in data), None)
        if key is None:
            continue
        row['_eventFields'][field] = {**reference, 'key': key}
        row[field + 'Detail'] = {'part': 'nodes', 'id': row['id'], 'field': field,
            'digest': hashlib.sha256(('event-field-v1:' + sha + ':' + key).encode()).hexdigest(),
            'digestKind': 'event-field-v1'}
        if field not in {'request', 'response'}:
            row[field] = _preview(data[key])
    # Neither usage nor lifecycle aggregates are derived from a partial range.
    return row


class _ReverseLines:
    def __init__(self, stream, end, guard, *, aligned=True, initial_bytes=0):
        self.stream, self.position, self.guard = stream, end, guard
        self.buffer, self.buffer_end = b'', end
        self.bytes_read = initial_bytes
        self.partial = False
        self.blocked = False
        self.initial = True
        self.discard = not aligned
        self.gaps = []
        self.continuation = None
        self.progress = False

    def __iter__(self):
        while True:
            self.guard()
            if self.discard and self.buffer:
                boundary = self.buffer.rfind(b'\n')
                if boundary >= 0 or self.position == 0:
                    start = self.position + boundary + 1
                    self.gaps.append({'start': start, 'end': self.buffer_end, 'reason': 'record_limit'})
                    self.buffer = self.buffer[:boundary + 1]
                    self.buffer_end = start
                    self.continuation = (start, True)
                    self.progress = True
                    self.discard = self.initial = False
            if self.initial and self.buffer:
                # An unfinished final record belongs to the next append. It is
                # never decoded or mistaken for a complete empty history.
                if self.buffer.endswith(b'\n'):
                    self.initial = False
                else:
                    self.partial = True
                    boundary = self.buffer.rfind(b'\n')
                    if boundary >= 0:
                        self.buffer = self.buffer[:boundary + 1]
                        self.buffer_end = self.position + boundary + 1
                        self.initial = False
                        self.partial = True
            if not self.initial and not self.discard and self.buffer:
                boundary = self.buffer.rfind(b'\n', 0, len(self.buffer) - 1)
                if boundary >= 0 or self.position == 0:
                    start = boundary + 1
                    raw = self.buffer[start:]
                    offset = self.buffer_end - len(raw)
                    self.buffer, self.buffer_end = self.buffer[:start], offset
                    self.progress = True
                    yield offset, raw
                    continue
            if self.position == 0:
                self.partial = self.partial or bool(self.buffer)
                self.continuation = (0, True)
                return
            size = min(BLOCK_BYTES, self.position, MAX_BYTES - self.bytes_read)
            if size == 0:
                if self.progress and not self.discard and not self.initial:
                    # This request spent part of its budget on newer rows or a
                    # huge-record continuation. Retry the next whole record on
                    # the next explicit page instead of dropping its suffix.
                    self.continuation = (self.buffer_end, True)
                    return
                self.blocked = True
                self.gaps.append({'start': self.position, 'end': self.buffer_end, 'reason': 'read_limit'})
                self.continuation = (self.position, False)
                return
            self.position -= size
            self.stream.seek(self.position)
            data = self.stream.read(size)
            self.bytes_read += len(data)
            if len(data) != size:
                raise StaleWindow('The event source changed during its read')
            self.buffer = data + self.buffer


def binding_revision(cache_home):
    """Bounded cache dependency for the existing index, including WAL commits."""
    if cache_home is None:
        return None
    home = Path(cache_home).resolve()
    directory = home / 'diagnostics'
    if directory.resolve() != directory:
        return None
    result = []
    for name in ('events.sqlite3', 'events.sqlite3-wal'):
        path = directory / name
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                return None
            result.append(_stamp(info))
        except FileNotFoundError:
            result.append(None)
        except OSError:
            return None
    return result


def _host_receipts(cache_home, session, path, identity, facts, guard):
    """Authenticate only existing local index entries, never JSONL marker text.

    cache_home is the worker's host-owned constructor argument, not a request
    path. Shared indexing does not index app-marked rows. Old unhashed migration
    entries and expired/missing receipts are insufficient provenance.
    """
    if cache_home is None:
        return set()
    home = Path(cache_home).resolve()
    database = home / 'diagnostics' / 'events.sqlite3'
    if database.resolve() != database or not database.is_file():
        return set()
    accepted = set()
    try:
        before = binding_revision(home)
        if before is None:
            return set()
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.05)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            steps = 0
            def progress():
                nonlocal steps
                steps += 1000
                guard()
                return int(steps > 100000)
            db.set_progress_handler(progress, 1000)
            for fact in facts:
                guard()
                data = fact['data']
                event_id = data.get('event_id')
                if (data.get('app') != 'amplifier-unified' or not _scalar(event_id)
                        or data.get('session_id') != identity
                        or data.get('sessionId') != identity
                        or data.get('rootSessionId') != identity
                        or data.get('appSessionId') != session.get('id')
                        or data.get('parentId') is not None):
                    continue
                record = db.execute(
                    'SELECT event,stream,session,workspace,data FROM records '
                    'WHERE id=? AND length(data)<=4096', (event_id,)).fetchone()
                stream = 'tools' if data.get('kind') == 'tool' else 'usage'
                if (record is None or tuple(record[:4]) !=
                        (fact['event'], stream, identity, session.get('workspace'))):
                    continue
                reference = json.loads(record[4])
                expected = {'$event': str(path), 'offset': fact['offset'],
                    'bytes': fact['bytes'], 'sha256': fact['sha256'], 'eventId': event_id}
                if reference == expected:
                    accepted.add(fact['id'])
            if binding_revision(home) != before:
                return set()
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return set()
    return accepted


def _bind_complete_root(session, path, identity, items, facts, cache_home, guard):
    """Conservative root tools only: four occurrences, one invocation, one turn.

    Call-ID reuse anywhere in this complete bounded source refuses the call.
    Models use only their own attested lifecycle pair, never inferred payloads.
    """
    attested = _host_receipts(cache_home, session, path, identity, facts, guard)
    by_call = {}
    for fact in facts:
        call = fact['data'].get('tool_call_id') or fact['data'].get('call_id')
        if _scalar(call):
            by_call.setdefault(call, []).append(fact)
    rows = {row['id']: row for row in items}
    result = []
    for call, occurrences in by_call.items():
        guard()
        # Count BEFORE filtering: an extra/suspicious record is a conflict.
        if len(occurrences) != 4:
            continue
        owners = [fact for fact in occurrences if fact['id'] in attested]
        bodies = [fact for fact in occurrences if fact['id'] not in attested]
        if len(owners) != 2 or len(bodies) != 2:
            continue
        def pair(values):
            starts = [f for f in values if f['event'] == 'tool:pre']
            ends = [f for f in values if f['event'] in {'tool:post', 'tool:error'}]
            return (starts[0], ends[0]) if len(starts) == len(ends) == 1 else (None, None)
        pre, post = pair(bodies)
        start, end = pair(owners)
        if any(f is None for f in (pre, post, start, end)):
            continue
        first, last = start['data'], end['data']
        logical = f'tool:{identity}:{call}'
        if (first.get('id') != logical or last.get('id') != logical
                or first.get('kind') != 'tool' or last.get('kind') != 'tool'
                or first.get('phase') != 'running'
                or last.get('phase') not in {'completed', 'error'}
                or not _scalar(first.get('turnId')) or first.get('turnId') != last.get('turnId')
                or first.get('revision') != 1 or last.get('revision') != 2
                or first.get('startedAt') != last.get('startedAt')):
            continue
        started, ended = first.get('startedAt'), last.get('endedAt')
        if (type(started) not in (int, float) or type(ended) not in (int, float)
                or not math.isfinite(started) or not math.isfinite(ended) or ended < started):
            continue
        if any(f['data'].get('session_id') != identity or f['data'].get('app') is not None
               or any(key in f['data'] for key in ('id', 'turnId', 'parentId'))
               for f in bodies):
            continue
        if pre['offset'] >= post['offset'] or start['offset'] >= end['offset']:
            continue
        a, b = rows.get(pre['id']), rows.get(post['id'])
        if a is None or b is None or not a.get('_eventFields', {}).get('input'):
            continue
        if not b.get('_eventFields', {}).get('output') and not b.get('_eventFields', {}).get('error'):
            continue
        node = {**b, 'turnId': first['turnId'], 'startedAt': started, 'endedAt': ended,
            'phase': last['phase'], 'association': 'host-bound',
            'bindingProvenance': 'host-diagnostics-receipt',
            'sourceOccurrenceIds': [f['id'] for f in sorted(occurrences, key=lambda f: f['offset'])],
            '_eventFields': {**b['_eventFields'], 'input': a['_eventFields']['input']},
            'input': a.get('input', ''),
            'inputDetail': {**a['inputDetail'], 'id': b['id']}}
        result.append(node)
    models = {}
    for fact in facts:
        logical = fact['data'].get('id')
        if isinstance(logical, str) and logical.startswith('llm:'):
            models.setdefault(logical, []).append(fact)
    for logical, occurrences in models.items():
        if len(occurrences) != 2 or any(f['id'] not in attested for f in occurrences):
            continue
        starts = [f for f in occurrences if f['event'] == 'provider:request']
        ends = [f for f in occurrences if f['event'] in {'llm:response', 'llm:error'}]
        if len(starts) != 1 or len(ends) != 1:
            continue
        first, last = starts[0]['data'], ends[0]['data']
        started, ended = first.get('startedAt'), last.get('endedAt')
        if (first.get('kind') != 'llm' or last.get('kind') != 'llm'
                or first.get('revision') != 1 or last.get('revision') != 2
                or first.get('phase') != 'running' or last.get('phase') not in {'completed', 'error'}
                or not _scalar(first.get('turnId')) or first['turnId'] != last.get('turnId')
                or started != last.get('startedAt') or type(started) not in (int, float)
                or type(ended) not in (int, float) or not math.isfinite(started)
                or not math.isfinite(ended) or ended < started
                or starts[0]['offset'] >= ends[0]['offset']):
            continue
        post = rows.get(ends[0]['id'])
        if post is not None:
            result.append({**post, 'turnId': first['turnId'], 'startedAt': started,
                'endedAt': ended, 'phase': last['phase'], 'association': 'host-bound',
                'bindingProvenance': 'host-diagnostics-receipt',
                'sourceOccurrenceIds': [starts[0]['id'], ends[0]['id']]})
    return sorted(result, key=lambda row: (row['startedAt'], row['eventOrder']))


def read_window(session, *, window_key, cursor_key, before=None, limit=MAX_ITEMS, check_budget=None,
                cache_home=None):
    """Read one explicit recent/older event page without loading its prefix.

    Returned references are private owner facts, never client-supplied paths.
    A caller must revalidate the owner/window and source receipt before publishing.
    Even an exhausted root file does not certify descendant or transcript coverage.
    """
    if not isinstance(window_key, str) or not 0 < len(window_key.encode('utf-8')) <= 256:
        raise ValueError('Invalid message window identity')
    if type(limit) is not int or not 1 <= limit <= MAX_ITEMS:
        raise ValueError('Choose 1 to 100 event observations')
    if not isinstance(cursor_key, bytes) or len(cursor_key) != 32:
        raise ValueError('A host-owned 32-byte cursor key is required')
    path, identity, owner = _source(session)
    guard = check_budget or (lambda: None)
    guard()
    items, facts, scanned, invalid, earliest, shape_invalid = [], [], 0, 0, None, False
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
                             | getattr(os, 'O_NOFOLLOW', 0))
        stream = os.fdopen(descriptor, 'rb')
    except FileNotFoundError:
        if before is not None:
            raise StaleWindow('The event source is no longer available')
        return {'status': 'ready', 'windowKey': window_key,
                'generation': _digest([owner, window_key, None]), 'items': [], 'before': None,
                'coverage': {'kind': 'event-window', 'historyComplete': False,
                    'rootExhausted': True, 'sourceCount': 0, 'rangeStart': 0, 'rangeEnd': 0,
                    'scannedEvents': 0, 'scannedBytes': 0, 'unassociatedCount': 0,
                    'partialRecord': False, 'pagingBlocked': False, 'invalidEvents': 0},
                'unavailableRanges': [], 'revisions': [[str(path), None]]}
    with stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Canonical events must be a regular file')
        stamp = _stamp(info)
        end, aligned = _end(before, cursor_key, owner, window_key, stamp)
        boundary_bytes = 0
        if before is not None and end and aligned:
            stream.seek(end - 1)
            boundary_bytes = 1
            if stream.read(1) != b'\n':
                raise StaleWindow('The event cursor is not a record boundary')
        lines = _ReverseLines(stream, end, guard, aligned=aligned, initial_bytes=boundary_bytes)
        for offset, raw in lines:
            scanned += 1
            earliest = offset
            try:
                event = _bounded_json(raw, guard)
                if (not isinstance(event, dict) or not isinstance(event.get('data'), dict)
                        or not isinstance(event.get('event'), str)
                        or len(event['event']) > 128):
                    shape_invalid = True
                row = _observation(event, raw, offset, path, identity)
                if isinstance(event, dict) and isinstance(event.get('data'), dict):
                    # Scalar collision/ownership facts only; bodies stay in their
                    # existing field references. Never retain whole decoded data.
                    sha = hashlib.sha256(raw).hexdigest()
                    if any(key in event['data'] and not _scalar(event['data'][key])
                           for key in ('tool_call_id', 'call_id')):
                        shape_invalid = True
                    if (event['data'].get('tool_call_id') and event['data'].get('call_id')
                            and event['data']['tool_call_id'] != event['data']['call_id']):
                        shape_invalid = True
                    metadata = {}
                    for key in ('event_id', 'app', 'session_id', 'sessionId', 'rootSessionId',
                                'appSessionId', 'kind', 'id', 'turnId', 'parentId', 'phase',
                                'revision', 'startedAt', 'endedAt', 'tool_call_id', 'call_id'):
                        if key not in event['data']:
                            continue
                        value = event['data'][key]
                        if value is None or type(value) in (int, float) or _scalar(value):
                            metadata[key] = value
                        else:
                            shape_invalid = True
                    facts.append({'id': f'event:{identity}:{offset}:{sha[:16]}',
                        'event': event.get('event') if isinstance(event.get('event'), str)
                                 and len(event['event']) <= 128 else None, 'offset': offset,
                        'bytes': len(raw), 'sha256': sha, 'data': metadata})
            except (ValueError, TypeError, OverflowError, RecursionError):
                invalid += 1
                lines.gaps.append({'start': offset, 'end': offset + len(raw),
                                   'reason': 'record_validation'})
                row = None
            if row is not None:
                items.append(row)
            if scanned >= MAX_EVENTS or len(items) >= limit:
                break
        if _stamp(os.fstat(stream.fileno())) != stamp or _stamp(path.stat()) != stamp:
            raise StaleWindow('The event source changed during its read')
    start = earliest if earliest is not None else end
    next_aligned = True
    if lines.continuation is not None and lines.continuation[0] < start:
        start, next_aligned = lines.continuation
    exhausted = start == 0 or (end == 0 and not lines.blocked)
    # Oversized records leave explicit gaps and a signed unaligned continuation;
    # older requests advance without decoding a suffix as a complete record.
    cursor = _cursor(cursor_key, owner, window_key, stamp, start, next_aligned) if start > 0 else None
    items.reverse()
    coverage = {'kind': 'event-window', 'historyComplete': False,
                'rootExhausted': exhausted, 'sourceCount': 1,
                'rangeStart': start, 'rangeEnd': end,
                'scannedEvents': scanned, 'scannedBytes': lines.bytes_read,
                'unassociatedCount': len(items), 'partialRecord': lines.partial,
                'pagingBlocked': lines.blocked, 'invalidEvents': invalid}
    complete = (before is None and start == 0 and end == stamp[2]
        and not invalid and not shape_invalid and not lines.partial
        and not lines.blocked and not lines.gaps)
    index_revision = binding_revision(cache_home)
    bound = (_bind_complete_root(session, path, identity, items, facts, cache_home, guard)
             if complete and index_revision is not None else [])
    if binding_revision(cache_home) != index_revision:
        raise StaleWindow('The binding index changed during its read')
    if _stamp(path.stat()) != stamp:
        raise StaleWindow('The event source changed during binding')
    used = {identity for node in bound for identity in node['sourceOccurrenceIds']}
    return {'status': 'ready', 'windowKey': window_key,
            '_boundWork': bound,
            '_bindingRevision': index_revision,
            'bindingCoverage': {'status': 'complete-root' if complete else 'partial-source',
                                'bound': len(bound), 'unassociated': len(items) - len(used)},
            'generation': _digest([owner, window_key, stamp, start, end]),
            'items': items, 'coverage': coverage, 'before': cursor,
            'unavailableRanges': list(reversed(lines.gaps)),
            'revisions': [[str(path), [[stamp[0], stamp[1]], *stamp[2:]]]]}


def read_field(session, reference, *, offset=0, limit=4096, check_budget=None):
    """Read a bounded text slice from an accepted private observation reference.

    The host resolves a row/field in its accepted window; HTTP callers never
    provide this reference. Exact canonical bytes remain the detail authority.
    """
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16384:
        raise ValueError('Invalid event field page')
    path, _, _ = _source(session)
    if (not isinstance(reference, dict) or set(reference) != {'path', 'offset', 'bytes', 'sha256', 'key'}
            or reference['path'] != str(path) or not isinstance(reference['key'], str)
            or reference['key'] not in FIELD_KEYS
            or type(reference['offset']) is not int or reference['offset'] < 0
            or type(reference['bytes']) is not int or not 0 < reference['bytes'] <= MAX_BYTES
            or not isinstance(reference['sha256'], str) or len(reference['sha256']) != 64):
        raise ValueError('Invalid owned event field reference')
    guard = check_budget or (lambda: None)
    guard()
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Canonical events must be a regular file')
        stamp = _stamp(info)
        if reference['offset'] + reference['bytes'] > stamp[2]:
            raise StaleWindow('The event field source changed')
        stream.seek(reference['offset'])
        raw = stream.read(reference['bytes'])
        if hashlib.sha256(raw).hexdigest() != reference['sha256'] or not raw.endswith(b'\n'):
            raise StaleWindow('The event field source changed')
        event = _bounded_json(raw, guard)
        if not isinstance(event, dict) or not isinstance(event.get('data'), dict):
            raise ValueError('Invalid canonical event field')
        value = event['data'][reference['key']]
        chunks = (value,) if isinstance(value, str) else json.JSONEncoder(
            ensure_ascii=False, indent=2, allow_nan=False).iterencode(value)
        parts, total, digest = [], 0, hashlib.sha256()
        for chunk in chunks:
            guard()
            digest.update(chunk.encode())
            start, end = max(0, offset - total), min(len(chunk), offset + limit - total)
            if start < end:
                parts.append(chunk[start:end])
            total += len(chunk)
        if _stamp(os.fstat(stream.fileno())) != stamp or _stamp(path.stat()) != stamp:
            raise StaleWindow('The event field source changed during its read')
    text = ''.join(parts)
    return {'text': text, 'offset': offset, 'total': total,
            'nextOffset': offset + len(text) if offset + len(text) < total else None,
            'digest': digest.hexdigest()}
