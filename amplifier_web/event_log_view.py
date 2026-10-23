"""Disposable indexes over native event logs. Payloads stay in events.jsonl.

The authenticated conversation API reads the user's recorded tool fields as-is.
This module never writes a capture, changes capture policy, or executes work.
"""
from __future__ import annotations

import asyncio
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from contextlib import ExitStack
import copy
from datetime import datetime
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sys
import threading
import time
import weakref

from .history_read_retry import retry_indexed_read
from .performance_diagnostics import measured, measure, count, TimedLock
from .execution import LIVE_PHASES, refresh_usage
from .execution_events import public_usage
from .session_files import amplifier_home, project_slug

logger = logging.getLogger(__name__)


def source_change_reasons(previous, current):
    """Classify already-observed stamps without paths, contents or new reads."""
    before, after = dict(previous), dict(current)
    for path in before.keys() | after.keys():
        if path not in before or path not in after:
            # A superseded projection can change the tracked source set. A
            # newly tracked missing file is not evidence that it appeared.
            reason = 'tracked'
        else:
            old, new = before[path], after[path]
            if old == new:
                continue
            if old is None:
                reason = 'appeared'
            elif new is None:
                reason = 'disappeared'
            elif old[0] != new[0]:
                reason = 'replaced'
            elif old[1] != new[1]:
                reason = 'resized'
            else:
                reason = 'metadata'
        kind = 'events' if Path(path).name == 'events.jsonl' else 'transcript'
        yield kind, reason


def same_model_call(observed, native):
    """Identify duplicate telemetry, allowing admission time before dispatch."""
    if observed.get('model') != native.get('model') or (observed.get('provider') and native.get('provider') and observed['provider'] != native['provider']):
        return False
    if not observed.get('endedAt') and not native.get('endedAt'):
        return (observed.get('phase') in LIVE_PHASES and native.get('phase') in LIVE_PHASES
                and observed.get('provider') in (None, native.get('provider'))
                and isinstance(observed.get('startedAt'), (int, float))
                and isinstance(native.get('startedAt'), (int, float))
                and observed['startedAt'] <= native['startedAt'])
    if not all(isinstance(row.get(key), (int, float)) for row in (observed, native) for key in ('startedAt', 'endedAt')):
        return False
    return (observed['startedAt'] <= native['startedAt'] + 1
            and abs(observed['endedAt'] - native['endedAt']) < 1
            and all((observed.get('usage') or {}).get(key) is None or (native.get('usage') or {}).get(key) is None or
                    observed['usage'][key] == native['usage'][key]
                    for key in ('inputTokens', 'outputTokens')))


class CompletedModelCalls:
    """Disposable lookup for the necessary end-time condition, never identity."""
    def __init__(self, nodes):
        self.buckets = {}
        for node in nodes:
            self.add(node)

    def add(self, node):
        ended = node.get('endedAt')
        if node.get('kind') == 'llm' and isinstance(ended, (int, float)) and math.isfinite(ended):
            self.buckets.setdefault(math.floor(ended), []).append(node)

    def candidates(self, row):
        ended = row.get('endedAt')
        if not isinstance(ended, (int, float)) or not math.isfinite(ended):
            return ()
        bucket = math.floor(ended)
        return (node for key in (bucket - 1, bucket, bucket + 1)
                for node in self.buckets.get(key, ()))


class FallbackTurnLookup:
    """Index legacy presentation order, without inferring call ownership.

    User rows need not be timestamp ordered: the old fallback takes the last
    eligible row in presentation order, then the first matching saved turn.
    A prefix maximum over timestamp-sorted positions preserves that behavior.
    """
    def __init__(self, users, turns, *, native_messages):
        self.users, self.turns, self.native_messages = users, turns, native_messages
        dated = sorted((at, position) for position, row in enumerate(users)
                       if isinstance(at := row.get('createdAt'), (int, float))
                       and at == at and row.get('source') != 'native')
        self.times, self.anchors = [], []
        latest = -1
        for at, position in dated:
            latest = max(latest, position)
            self.times.append(at)
            self.anchors.append(users[latest])
        self.by_anchor, self.by_input, self.indexed = {}, {}, True
        for position, turn in enumerate(turns.values()):
            self._index(turn, position)

    def _index(self, turn, position):
        try:
            self.by_anchor.setdefault(turn.get('anchorMessageId'), (position, turn))
            if turn.get('inputId'):
                self.by_input.setdefault(turn['inputId'], (position, turn))
        except TypeError:
            # Preserve inspection of malformed legacy identifiers rather than
            # making the optimization a new validation or migration boundary.
            self.indexed = False

    def anchor(self, at):
        if isinstance(at, (int, float)) and at == at:
            position = bisect_right(self.times, at) - 1
            if position >= 0:
                return self.anchors[position]
        return self.users[0] if self.users and not self.native_messages else None

    def turn(self, anchor):
        if not anchor:
            return None
        if self.indexed:
            try:
                matches = [match for match in (self.by_anchor.get(anchor['id']),
                           self.by_input.get(anchor.get('inputId')) if anchor.get('inputId') else None)
                           if match is not None]
                return min(matches, key=lambda match: match[0])[1] if matches else None
            except TypeError:
                pass
        return next((turn for turn in self.turns.values() if
                     turn.get('anchorMessageId') == anchor['id'] or
                     turn.get('inputId') == anchor.get('inputId') and anchor.get('inputId')), None)

    def ensure(self, key, turn):
        if key not in self.turns:
            self.turns[key] = turn
            self._index(turn, len(self.turns) - 1)


def merge_model_observations(rows, *, aliases=()):
    """Join host/provider display telemetry across a resumed root's log files.

    Only this root's explicit app/native IDs are aliases. Child sessions remain
    separate, and timing matches never create admission-accounting authority.
    """
    app = [row for row in rows if row.get('_appModel')]
    # Terminal observations can match only within one second. Index that
    # necessary condition before the identity/usage checks instead of comparing
    # every historical host call with every provider call on each refresh.
    # Pending observations retain the existing ambiguity rules below.
    def finite_timestamp(value):
        return isinstance(value, (int, float)) and (not isinstance(value, float) or math.isfinite(value))
    ended = sorted((row['endedAt'], position) for position, row in enumerate(app)
                   if finite_timestamp(row.get('endedAt')))
    times = [at for at, _ in ended]
    pending = [position for position, row in enumerate(app) if not row.get('endedAt')]
    pairs = []
    for row in rows:
        if row['kind'] != 'llm' or row.get('_appModel'):
            continue
        end = row.get('endedAt')
        positions = pending if not end else []
        if finite_timestamp(end):
            positions = [*positions, *(position for _, position in
                         ended[bisect_left(times, end - 1):bisect_right(times, end + 1)])]
        # Keep original host order for equally close matches and include zero
        # timestamps in both pending and terminal cases without duplicating them.
        candidates = (app[position] for position in sorted(set(positions)))
        matches = [other for other in candidates if (other.get('sessionId') == row.get('sessionId') or
                       other.get('sessionId') in aliases and row.get('sessionId') in aliases)
                   and same_model_call(other, row)]
        if row.get('endedAt') is not None or len(matches) == 1:
            for other in matches:
                distance = abs((other.get('endedAt') or other.get('startedAt') or 0) -
                               (row.get('endedAt') or row.get('startedAt') or 0))
                pairs.append((distance, row, other))
    matched, omitted = set(), set()
    # A host call can span provider retries. Match its nearest terminal
    # native attempt once; preserve earlier attempts and their own details.
    for _, row, closest in sorted(pairs, key=lambda pair: pair[0]):
        if row['id'] in omitted or closest['id'] in matched:
            continue
        matched.add(closest['id']);omitted.add(row['id'])
        for field in ('requestInfo', 'requestDetail', 'requestCapture', 'requestRecording', 'responseDetail', 'responseCapture', 'responseRecording', '_eventFields', 'error', 'errorDetail'):
            if field in row:closest[field] = row[field]
        for field in ('request', 'response'):
            if closest.get(field + 'Detail'):closest[field + 'Detail']['id'] = closest['id']
    return [row for row in rows if row['id'] not in omitted]

def text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)


def timestamp(value):
    if isinstance(value, (int, float)):
        return value
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return None


def event_path(session, identity):
    project = session.get('nativeProject') or project_slug(session['workspace'])
    if Path(project).name != project or project in {'', '.', '..'}:
        raise ValueError('Invalid native project identity')
    if not isinstance(identity, str) or identity in {'', '.', '..'} or '/' in identity or '\\' in identity or '\x00' in identity:
        raise ValueError('Invalid native session identity')
    configured = os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', '').strip()
    root = Path(configured).expanduser() if configured and '${' not in configured else None
    root = root if root is not None and root.is_absolute() else amplifier_home() / 'projects'
    return root / project / 'sessions' / identity / 'context-intelligence' / 'events.jsonl'


def read_event_data(reference):
    """References originate in this server's index, never in request parameters."""
    try:
        with Path(reference['path']).open('rb') as stream:
            stream.seek(reference['offset'])
            raw = stream.read(reference['bytes'])
    except OSError as exc:
        raise ValueError('The recorded action is temporarily unavailable. Try loading it again.') from exc
    if hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise ValueError('The event log changed. Refresh this conversation to read its current content.')
    return json.loads(raw)['data']


def event_field_digest(reference):
    """Opaque validator binds exact source bytes and selected field."""
    return hashlib.sha256(('event-field-v1:' + reference['sha256'] + ':' + reference['key']).encode()).hexdigest()


def read_field(reference):
    value = read_event_data(reference)[reference['key']]
    if reference.get('delegateField'):
        from .delegate_exchange import select
        value = select(value, reference['delegateField'])
        if value is None:
            raise ValueError('This delegation message is no longer available.')
    return text(value)


class EventIndex:
    def __init__(self, path, identity):
        self.path, self.identity = path, identity
        self.reset()

    def reset(self):
        self.read_bytes = 0
        self.prefix_digest = hashlib.sha256(b'').hexdigest()
        self._checkpoint_signature = None
        self.offset = 0
        self.file_id = None
        self.nodes = OrderedDict()
        self.children = {}
        self.worker_runs = {}
        self.tool_owners = {}
        self.pending = {}
        self.last = None
        self.revision = None
        self.diagnostics = []
        self.association_events = []
        self.association_revision = None
        self.association_cache = {}

    def refresh(self):
        self.read_bytes = 0
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            if self.file_id is not None:
                self.reset()
            return False
        identity = (stat.st_dev, stat.st_ino)
        revision = (identity, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if revision == self.revision:
            return True
        reset = self.file_id != identity or stat.st_size <= self.offset
        prefix = hashlib.sha256()
        if not reset and self.offset:
            with self.path.open('rb') as stream:
                remaining = self.offset
                while remaining:
                    if guard := getattr(self, '_budget_guard', None):
                        guard()
                    raw = stream.read(min(remaining, 65536))
                    if not raw:
                        break
                    prefix.update(raw)
                    remaining -= len(raw)
            # File identity and an unchanged last line do not prove that an
            # external writer left earlier bytes untouched before appending.
            reset = bool(remaining) or prefix.hexdigest() != self.prefix_digest
        if reset:
            self.reset()
            prefix = hashlib.sha256()
        self.file_id = identity
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            # Parsing runs off the app's event loop. A partial final line is
            # retried on the next append, never committed to the index.
            lines = 0
            while raw := stream.readline():
                lines += 1
                if lines % 128 == 0 and (guard := getattr(self, '_budget_guard', None)):
                    guard()
                self.read_bytes += len(raw)
                offset = self.offset
                if not raw.endswith(b'\n'):
                    break
                self.offset = stream.tell()
                prefix.update(raw)
                reference = {'path': str(self.path), 'offset': offset, 'bytes': len(raw),
                             'sha256': hashlib.sha256(raw).hexdigest()}
                self.last = reference
                try:
                    row = json.loads(raw)
                    if isinstance(row, dict) and isinstance(row.get('data'), dict):
                        self.ingest(row, reference)
                except (ValueError, TypeError, KeyError):
                    if len(self.diagnostics) < 20:
                        self.diagnostics.append({'code': 'invalid_event', 'offset': offset})
        self.prefix_digest = prefix.hexdigest()
        self.revision = revision
        return True

    def field(self, node, field, data, keys, reference, *, preview=True):
        key = next((key for key in keys if key in data), None)
        if key is None:
            return
        node.setdefault('_eventFields', {})[field] = {**reference, 'key': key}
        # A model recording is not visible until explicitly opened. Bind its
        # reference to the original event bytes and field instead of formatting
        # every raw request/response just to count/hash the unseen pretty JSON.
        value = text(data[key]) if preview else None
        if preview:
            node[field] = value[:512]
        if field in ('request', 'response'):
            from .provider_payloads import metadata
            files = data.get('provider_payloads')
            payload = metadata(files.get(key)) if isinstance(files, dict) else None
            if payload:
                node[field + 'Recording'] = payload
        if not preview:
            node[field + 'Detail'] = {'part': 'nodes', 'id': node['id'], 'field': field,
                'digest': event_field_digest(node['_eventFields'][field]), 'digestKind': 'event-field-v1'}
        else:
            node[field + 'Detail'] = {'part': 'nodes', 'id': node['id'], 'field': field,
                'digest': hashlib.sha256(value.encode()).hexdigest(), 'length': len(value)} if len(value) > 512 else None

    def delegate_field(self, node, field, data, keys, reference):
        from .delegate_exchange import select
        key = next((key for key in keys if key in data), None)
        if key is None:
            return
        value = select(data[key], field)
        if value is None:
            return
        node[field] = value[:512]
        node.setdefault('_eventFields', {})[field] = {**reference, 'key': key, 'delegateField': field}
        node[field + 'Detail'] = {'part': 'nodes', 'id': node['id'], 'field': field,
            'digest': hashlib.sha256(value.encode()).hexdigest(), 'length': len(value)} if len(value) > 512 else None

    def ingest(self, event, reference):
        name, data = event.get('event'), event['data']
        if not isinstance(name, str):
            return
        sid = data.get('session_id') or event.get('session_id') or self.identity
        at = timestamp(event.get('timestamp') or data.get('timestamp'))
        # Older app-only diagnostic receipts have no tool body. They must not
        # restart a completed native tool or replace its precise timestamps.
        if data.get('kind') == 'tool' and isinstance(data.get('id'), str):
            # App receipts contribute exact ownership only. Never use their
            # content or lifecycle in place of the native tool event.
            call = data.get('toolCallId')
            owner_sid = data.get('sessionId') or sid
            if isinstance(call, str) and isinstance(data.get('parentId'), str):
                self.tool_owners[f'tool:{owner_sid}:{call}'] = {
                    key: data[key] for key in ('parentId', 'turnId') if data.get(key)}
            return
        if name == 'prompt:submit' or name.startswith(('tool:', 'llm:', 'provider:')):
            from amplifier_foundation.session.history import event_association_record
            self.association_events.append(event_association_record(
                {'event': name, 'session_id': sid, 'offset': reference['offset'], 'data': data}))
        if name in {'tool:pre', 'tool:post', 'tool:error'}:
            call = data.get('tool_call_id') or data.get('call_id')
            if not isinstance(call, str):
                return
            key = f'tool:{sid}:{call}'
            node = self.nodes.setdefault(key, {'id': key, 'kind': 'tool', 'sessionId': sid,
                'toolCallId': call, 'label': data.get('tool_name') or data.get('tool') or 'Tool',
                'canonicalHistory': True, 'eventOrder': reference['offset']})
            if name == 'tool:pre' and node.get('endedAt') is not None:
                for field in ('output', 'error', 'delegateResponse'):
                    node.pop(field, None)
                    node.pop(field + 'Detail', None)
                    node.get('_eventFields', {}).pop(field, None)
                node.pop('endedAt', None)
                node['startedAt'] = at
            from .tool_observations import input_hints, outcome_summary
            input_key = next((name for name in ('tool_input', 'arguments', 'input') if name in data), None)
            if input_key is not None or name == 'tool:pre':
                node.update(input_hints(data.get('tool_name') or data.get('tool') or node.get('label'),
                    data.get(input_key), present=input_key is not None))
            node['outcomeSummary'] = outcome_summary(name, data)
            self.field(node, 'input', data, ('tool_input', 'arguments', 'input'), reference)
            from .delegate_exchange import tool_name
            if tool_name(node) == 'delegate':
                self.delegate_field(node, 'delegateRequest', data, ('tool_input', 'arguments', 'input'), reference)
            elif tool_name(node) == 'live_worker':
                from .delegate_exchange import parsed, exchange_fields
                args = parsed(data.get('tool_input', data.get('arguments', data.get('input'))))
                if input_key is not None:
                    node.pop('exchangeOwnerId', None)
                    node.pop('delegateRequest', None)
                    node.pop('delegateRequestDetail', None)
                    node.get('_eventFields', {}).pop('delegateRequest', None)
                if exchange_fields('live_worker', args):
                    self.delegate_field(node, 'delegateRequest', data, ('tool_input', 'arguments', 'input'), reference)
                    target = args.get('session_id')
                    owner = self.children.get(target) if isinstance(target, str) else None
                    if owner:
                        node['exchangeOwnerId'] = owner['id']
            if at is not None:
                node.setdefault('startedAt', at)
            node['phase'] = 'running' if name == 'tool:pre' else 'error' if name == 'tool:error' else 'completed'
            if name != 'tool:pre':
                node['endedAt'] = at
                self.field(node, 'output', data, ('result', 'tool_result'), reference)
                self.field(node, 'error', data, ('error', 'error_message'), reference)
                if tool_name(node) == 'delegate':
                    if name == 'tool:error' or data.get('error') or data.get('error_message'):
                        # A successful-looking inner value can be a partial
                        # result. Keep the raw result, not a typed final answer.
                        node.pop('delegateResponse', None)
                        node.pop('delegateResponseDetail', None)
                        node.get('_eventFields', {}).pop('delegateResponse', None)
                    else:
                        self.delegate_field(node, 'delegateResponse', data, ('result', 'tool_result'), reference)
                result = data.get('result', data.get('tool_result'))
                if isinstance(result, dict) and result.get('success') is False:
                    node['phase'] = 'error'
            from .image_generation import observe
            observe(node, name, data)
            return
        if name in {'delegate:agent_spawned', 'delegate:agent_resumed'}:
            child = data.get('sub_session_id') if name == 'delegate:agent_spawned' else data.get('session_id')
            if isinstance(child, str):
                from .delegate_exchange import worker_id
                parent_sid = data.get('parent_session_id') or (self.identity if name == 'delegate:agent_resumed' else sid)
                call = data.get('tool_call_id')
                self.children[child] = {'id': worker_id(child, call), 'kind': 'worker', 'sessionId': child,
                    'parentId': f"tool:{parent_sid}:{call}", 'toolCallId': call,
                    'parentSessionId': parent_sid, 'label': data.get('agent') or 'Worker',
                    'startedAt': at, 'phase': 'recorded', 'canonicalHistory': True}
                self.worker_runs[self.children[child]['id']] = self.children[child]
            return
        if data.get('kind') == 'llm' and isinstance(data.get('id'), str):
            # App lifecycle IDs are also recorded in the canonical log. Keep
            # those exact IDs; observers/accounting can refer to the same call.
            key = data['id']
            node = self.nodes.setdefault(key, {'id': key, 'kind': 'llm', 'label': 'Model call', 'canonicalHistory': True,
                                              'eventOrder': reference['offset'], '_appModel': True})
            node.update({key: data[key] for key in ('sessionId', 'parentId', 'turnId', 'label', 'provider',
                'model', 'startedAt', 'endedAt', 'phase', 'usage', 'lifecycle') if key in data})
            node.setdefault('sessionId', sid)
            return
        if name not in {'llm:request', 'llm:response', 'llm:error'}:
            return
        request = data.get('request_id') or data.get('call_id')
        naming = data.get('purpose') == 'session-naming' or data.get('origin_module') == 'hooks-session-naming'
        scope = (sid, data.get('provider'), data.get('model'), naming)
        if name == 'llm:request':
            key = f'llm:{sid}:{request or reference["offset"]}'
            node = {'id': key, 'kind': 'llm', 'sessionId': sid, 'label': 'Model call',
                'provider': data.get('provider'), 'model': data.get('model'), 'startedAt': at,
                'phase': 'running', 'canonicalHistory': True, 'eventOrder': reference['offset']}
            if naming:node.update(label='Session naming', lifecycle='background')
            raw = data.get('raw', data.get('raw_request'))
            options = raw if isinstance(raw, dict) else {}
            keys = ('message_count', 'has_instructions', 'has_system', 'reasoning_enabled', 'thinking_enabled',
                    'thinking_budget', 'background_mode', 'stream', 'max_tokens', 'max_output_tokens',
                    'temperature', 'top_p', 'parallel_tool_calls', 'tool_choice', 'purpose')
            node['requestInfo'] = {key: value for key in keys
                if isinstance(value := data.get(key, options.get(key)), (str, int, float, bool))
                and (not isinstance(value, str) or len(value) <= 512)}
            messages = options.get('messages', options.get('input'))
            if isinstance(messages, list):node['requestInfo'].setdefault('message_count', len(messages))
            if isinstance(options.get('tools'), list):node['requestInfo']['tool_count'] = len(options['tools'])
            if isinstance(options.get('reasoning'), dict) and isinstance(options['reasoning'].get('effort'), str):
                node['requestInfo']['reasoning_effort'] = options['reasoning']['effort'][:100]
            if raw is not None:
                self.field(node, 'request', data, ('raw', 'raw_request'), reference, preview=False)
                capture = data.get('request_capture', {})
                if isinstance(capture, dict):
                    node['requestCapture'] = {key: capture[key] for key in ('redacted', 'truncated')
                                              if type(capture.get(key)) is bool}
                    if type(capture.get('limit_chars')) is int:
                        node['requestCapture']['limit_chars'] = capture['limit_chars']
            self.pending.setdefault(scope, []).append(node)
            if request:
                self.nodes[key] = node
            return
        key = f'llm:{sid}:{request}' if request else None
        pending = self.pending.get(scope, [])
        match = next((row for row in pending if row['id'] == key), None)
        if key not in self.nodes:
            match = pending[0] if len(pending) == 1 and not request else None
            key = match['id'] if match else f'llm:{sid}:response:{request or reference["offset"]}'
            if match:
                self.nodes[key] = match
        if match:
            pending.remove(match)
        elif not request:
            # Without IDs, concurrent requests cannot be paired by arrival.
            # Count the responses; duration metadata supplies timing if present.
            pending.clear()
        node = self.nodes.setdefault(key, {'id': key, 'kind': 'llm', 'sessionId': sid,
            'label': 'Model call', 'canonicalHistory': True, 'eventOrder': reference['offset']})
        if naming:node.update(label='Session naming', lifecycle='background')
        duration = data.get('duration_ms')
        if 'startedAt' not in node and at is not None and isinstance(duration, (int, float)):
            node['startedAt'] = at - duration / 1000
        node.update(provider=data.get('provider'), model=data.get('model'), endedAt=at,
                    phase='error' if name == 'llm:error' or data.get('status') == 'error' else 'completed',
                    usage=public_usage(data.get('usage')))
        if name == 'llm:response' and data.get('raw', data.get('raw_response')) is not None:
            # Keep the provider's recorded response out of summaries and state.
            # The authenticated detail endpoint reads it only when requested.
            self.field(node, 'response', data, ('raw', 'raw_response'), reference, preview=False)
            capture = data.get('response_capture', data.get('request_capture', {}))
            if isinstance(capture, dict):
                node['responseCapture'] = {key: capture[key] for key in ('redacted', 'truncated')
                                           if type(capture.get(key)) is bool}
                if type(capture.get('limit_chars')) is int:
                    node['responseCapture']['limit_chars'] = capture['limit_chars']
        self.field(node, 'error', data, ('error', 'error_message'), reference)

    @retry_indexed_read
    def associations(self, directory):
        """Use Foundation's exact transcript associations for undated history."""
        from amplifier_foundation.session.history import SessionHistoryStore, associate_events
        stamps = []
        for name in ('transcript.jsonl', 'transcript.jsonl.backup', 'transcript.jsonl.append-pending'):
            try:
                stat = (directory / name).stat()
                stamps.append(((stat.st_dev, stat.st_ino), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            except FileNotFoundError:
                stamps.append(None)
        revision = (self.revision, tuple(stamps))
        if revision == self.association_revision:
            return self.association_cache
        messages = SessionHistoryStore(directory, session_id=self.identity).indexed_messages() if any(stamps) else []
        associations = associate_events(messages, self.association_events)
        current, result = None, {}
        for association in associations:
            event = self.association_events[association.event_index]
            if event['event'] == 'prompt:submit':
                current = association.turn_message_index
            turn = association.turn_message_index if association.turn_message_index is not None else current
            if turn is not None and not association.auxiliary:
                result[event['offset']] = {'turn': turn, 'position': min(association.message_indices) if association.message_indices else None}
        self.association_revision, self.association_cache = revision, result
        return result

    def rows(self, model_binding=None, *, coalesce=True):
        pending = [items[0] for items in self.pending.values() if len(items) == 1 and items[0]['id'] not in self.nodes]
        rows = copy.deepcopy([*self.nodes.values(), *pending])
        for row in rows:
            if row.get('kind') == 'tool':
                row.update(self.tool_owners.get(row['id'], {}))
        app = [row for row in rows if row.get('_appModel')]
        if model_binding:
            for row in app:
                binding = model_binding(row)
                if binding and binding.get('kind') == 'llm':
                    # The host completion can precede its lifecycle log flush.
                    # Pair with its latest state, retaining native session IDs
                    # here; full accounting metadata is joined later by ID.
                    row.update({key: copy.deepcopy(binding[key]) for key in
                                ('provider', 'model', 'startedAt', 'endedAt', 'phase', 'usage') if key in binding})
        return merge_model_observations(rows) if coalesce else rows


def retained_index_size(index, *, limit=None):
    """Charge built-in index payloads once, including association bodies.

    This excludes Path internals, cache bookkeeping, RSS and allocator overhead.
    Shared objects across indexes are charged separately (a conservative bound).
    Only changed indexes are measured; an unchanged refresh reuses its charge.
    With a limit, stop once the partial charge already proves rejection. Such
    a result is never used as the retained charge of an admitted index.
    """
    seen, pending, total = set(), [vars(index), str(index.path)], sys.getsizeof(index)
    while pending:
        value = pending.pop()
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        total += sys.getsizeof(value)
        if limit is not None and total > limit:
            return total
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, (list, tuple, set, frozenset)):
            pending.extend(value)
    return total


def refresh_turn_lifecycle(tree, session, *, host_turns=None):
    """Recompute display timing without taking manager lifecycle authority."""
    if host_turns is None:
        host_turns = {row['id'] for row in session.get('execution', {}).get('turns', ())
                      if not row.get('canonicalHistory') and not row.get('nativeHistory')}
    foreground_by_turn = {}
    for row in tree.get('nodes', ()):
        if row.get('lifecycle') != 'background':
            foreground_by_turn.setdefault(row.get('turnId'), []).append(row)
    for turn in tree.get('turns', ()):
        # Naming and other auxiliary calls retain their own lifecycle and
        # usage, but cannot reopen or extend a completed foreground turn.
        members = foreground_by_turn.get(turn['id'], [])
        starts = [row['startedAt'] for row in members if isinstance(row.get('startedAt'), (int, float))]
        ends = [row['endedAt'] for row in members if isinstance(row.get('endedAt'), (int, float))]
        if starts:turn['startedAt'] = min(starts)
        # Call outcomes cannot overwrite a manager failure, or settle the
        # currently admitted input between calls. Logs enrich action rows;
        # the host lifecycle owns when its current turn has stopped.
        if turn['id'] in host_turns and (turn.get('status') or turn.get('phase')) in {'error','failed','cancelled','interrupted','stopped'}:
            continue
        current_live = (turn['id'] in host_turns and turn['id'] == tree.get('currentTurnId')
                        and session.get('status') in {'working','starting','running','stopping'})
        if current_live or any(row.get('phase') in LIVE_PHASES and not row.get('endedAt') for row in members):
            # A later observed call can continue this input after an earlier
            # projection saw only completed members. Discard that old end.
            turn['phase'] = 'running'
            turn.pop('endedAt', None)
        elif ends:
            turn.update(endedAt=max(ends), phase='completed')


class EventLogView:
    # Shared recent working set, not a pin for every conversation ever visited.
    # Either limit can retire an index; canonical files remain authoritative.
    MAX_INDEXES = 256
    MAX_INDEX_BYTES = 64 * 1024 * 1024
    MAX_SIZE_RECEIPTS = 256

    def __init__(self, service):
        self.service = service
        from .event_index_checkpoint import Checkpoints
        self.checkpoints = Checkpoints(service.data_dir) if getattr(service, 'data_dir', None) else None
        self.indexes = OrderedDict()
        self._index_sizes = {}
        # Negative admission receipts retain only source revisions and a budget,
        # never descriptors or an index. Restored object sharing can differ, so
        # a receipt may only reject retention, never supply an admitted charge.
        self._oversized = OrderedDict()
        self.retained_index_bytes = 0
        # Cancellation releases the async refresh lock before to_thread ends.
        # Keep cache accounting serialized until the actual reader has exited.
        self._read_lock = threading.RLock()
        self.task = None
        self.lock = TimedLock(service, 'events.lock.wait')
        self.projected = OrderedDict()
        # Passive polling receipts are only publication-boundary hints. Explicit
        # refreshes always compare the actual graph, including unpublished aliases.
        # Weak owners must not keep retired conversations and their bodies alive.
        self._clean_inputs = OrderedDict()
        self.read_paths = {}
        self.read_revisions = {}
        self.read_proofs = {}
        self.projector = None
        self._pending_inputs = {}
        self._process_tasks = {}
        self._interest_cursor = 0
        self._retry_at = OrderedDict()
        self._refused = OrderedDict()  # At most eight small deterministic-cap receipts.
        self._work_generation = 0
        self._pending_work_notifications = {}
        from .work_windows import WorkWindows
        self.windows = WorkWindows(service)

    def invalidate(self, session_ids=None, *, notify=True):
        """Invalidate before mutations are published, including deferred/failed saves."""
        # The same bounded weak owners that justify passive reuse own the public
        # completion receipt. Never leave a retained generation labelled current
        # after a writer invalidates it, and never admit an owner to update it.
        # A writer invalidates immediately, but its canonical publication owns
        # notification after commit. Otherwise shell.notify can snapshot the
        # in-progress mutation with the previous revision.
        self.windows.invalidate(session_ids, notify=notify)
        transcript = getattr(getattr(self.service, 'history', None), 'windows', None)
        if transcript is not None:
            transcript.invalidate(session_ids)
        for identity, (reference, _) in self._clean_inputs.items():
            if session_ids is None or identity in session_ids:
                owner = reference()
                if owner is not None and owner.get('workProjection', {}).get('current') is True:
                    self._set_work_status(owner, 'loading')
        for identity, fence in self._pending_inputs.items():
            if session_ids is None or identity in session_ids:
                fence[0] = True
        for identity in tuple(self._refused):
            if (session_ids is None or identity in session_ids) and self._refused[identity]['revisions'] is None:
                self._refused.pop(identity, None)
        if session_ids is None:
            self._clean_inputs.clear()
            self._retry_at.clear()
        else:
            for identity in session_ids:
                self._clean_inputs.pop(identity, None)
                self._retry_at.pop(identity, None)

    @staticmethod
    def _input_identity(session):
        return tuple(session.get(key) for key in ('id', 'workspace', 'nativeProject',
                     'nativeIdentity', 'runtimeSessionId', 'status'))

    def _remember_clean(self, session):
        try:
            owner = weakref.ref(session)
        except TypeError:
            # Plain mappings supplied by embeddings retain exact comparisons;
            # never substitute id(object), which can be reused after retirement.
            self._clean_inputs.pop(session['id'], None)
            return
        self._clean_inputs[session['id']] = (owner, self._input_identity(session))
        self._clean_inputs.move_to_end(session['id'])
        # Residency/cold-field retirement can discard projected independently.
        # Keep this metadata bound independent of that cache's eviction path.
        while len(self._clean_inputs) > 64:
            self.invalidate({next(iter(self._clean_inputs))})

    def _new_index(self, path, identity):
        index = EventIndex(path, identity)
        if self.checkpoints:
            with measure(self.service, 'events.checkpoint.load', synchronous=True):
                hit = self.checkpoints.load(index)
            count(self.service, 'events.checkpoint.hit' if hit else 'events.checkpoint.miss')
        return index

    def _checkpoint(self, index):
        if self.checkpoints:
            try:
                with measure(self.service, 'events.checkpoint.save', synchronous=True):
                    self.checkpoints.save(index)
            except (OSError, ValueError, TypeError, MemoryError):
                # A disposable cache failure cannot fail a canonical read.
                count(self.service, 'events.checkpoint.write_failed')

    def _forget_index(self, key):
        self.indexes.pop(key, None)
        previous = self._index_sizes.pop(key, None)
        if previous is not None:
            self.retained_index_bytes -= previous[1]

    @staticmethod
    def _charge_signature(index):
        # Source/association revisions own descriptor mutations. Checkpointing
        # and per-read counters also change values traversed by the size walker.
        return (index.revision, index.association_revision,
                index._checkpoint_signature, index.read_bytes)

    def _retain_index(self, index, *, receipt=None):
        key = str(index.path)
        previous = self._index_sizes.get(key) if self.indexes.get(key) is index else None
        signature = (index.revision, index.association_revision)
        charge_signature = self._charge_signature(index)
        if (previous is None and receipt is not None and receipt[0] is index
                and receipt[1][0] == charge_signature):
            # A prior retention in this read may have evicted this exact object
            # while the read still held it. Never transfer a charge to a newly
            # restored object, even when its canonical revisions match.
            previous = receipt[1]
        rejected = self._oversized.get(key)
        try:
            if previous and previous[0] == charge_signature:
                size = previous[1]
            elif rejected and rejected[0] == signature and self.MAX_INDEX_BYTES <= rejected[1]:
                size = self.MAX_INDEX_BYTES + 1
                count(self.service, 'events.index.size_rejected_reused')
            else:
                with measure(self.service, 'events.index.size', synchronous=True):
                    size = retained_index_size(index, limit=self.MAX_INDEX_BYTES)
        except BaseException:
            self._forget_index(key)
            raise
        self._forget_index(key)
        # A large history can still be read exactly for this request, but it
        # cannot permanently displace the entire bounded recent working set.
        if size > self.MAX_INDEX_BYTES:
            count(self.service, 'events.index.oversize')
            self._oversized[key] = (signature, self.MAX_INDEX_BYTES)
            self._oversized.move_to_end(key)
            while len(self._oversized) > self.MAX_SIZE_RECEIPTS:
                self._oversized.popitem(last=False)
            self._checkpoint(index)
            return
        self._oversized.pop(key, None)
        self.indexes[key] = index
        self._index_sizes[key] = (charge_signature, size)
        self.retained_index_bytes += size
        while len(self.indexes) > self.MAX_INDEXES or self.retained_index_bytes > self.MAX_INDEX_BYTES:
            count(self.service, 'events.index.evicted')
            retired = next(iter(self.indexes))
            self._checkpoint(self.indexes[retired])
            self._forget_index(retired)

    @staticmethod
    def projection_input(session):
        """Only canonical association/lifecycle inputs, never draft or progress."""
        result = {key: session[key] for key in ('id', 'workspace', 'nativeProject', 'nativeIdentity',
                  'runtimeSessionId', 'status') if key in session}
        if 'execution' in session:
            from .session_projection import ACCOUNTING_FIELDS
            tree = session['execution']
            fields = ACCOUNTING_FIELDS | {'_canonicalId', 'toolCallId', 'canonicalHistory',
                'nativeHistory', 'liveObservation', 'label', 'runId', 'routing', 'parentProvider'}
            # Canonical payloads and derived summaries are rebuilt from events.
            # Keep every historical join, completion and accounting identity:
            # filtering to active rows would split old host turns on refresh.
            result['execution'] = {key: tree[key] for key in
                ('turns', 'currentTurnId', 'retiredUsageNodes') if key in tree}
            if 'nodes' in tree:
                result['execution']['nodes'] = [
                    {key: value for key, value in row.items() if key in fields}
                    if row.get('canonicalHistory') or row.get('nativeHistory') else row
                    for row in tree['nodes']]
            # App-only/legacy rows remain complete: the reader preserves them
            # when a native log is incomplete. The caller still detaches this
            # input and rechecks it before publishing a threaded projection.
        result['messages'] = [{key: row[key] for key in ('id', 'role', 'createdAt', 'nativeIndex', 'inputId', 'source')
                               if key in row} for row in session.get('messages', [])]
        result['workers'] = [{key: row[key] for key in ('id', 'sessionId', 'status') if key in row}
                            for row in session.get('workers', [])]
        # Retain only lifecycle identities needed to repair old display
        # outcomes, never generation text or another provider payload.
        result['generations'] = [{key: row[key] for key in
            ('event', 'sessionId', 'rootSessionId', 'generation_id', 'initial_input_id', 'input_ids', 'at') if key in row}
            for row in session.get('generations', [])
            if row.get('event') in {'generation.started', 'generation.finished', 'generation.failed', 'generation.detached'}]
        return result

    def start(self):
        # Qualify each platform's process-memory policy before enabling it.
        # Other supported hosts retain the exact existing threaded reader.
        if sys.platform.startswith('linux') and getattr(self.service, 'data_dir', None) is not None:
            from .history_projection_process import ProcessProjector
            self.projector = ProcessProjector(self.service.data_dir)
        self.task = asyncio.create_task(self.loop())

    async def close(self):
        await self.windows.close()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        tasks = tuple(self._process_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.projector is not None:
            await self.projector.close()

    def _process_refresh_task(self, identity):
        if self.service.closed:
            return None
        identity = self.service._session(identity)['id']
        if self._retry_at.get(identity, 0) > time.monotonic():
            return None
        task = self._process_tasks.get(identity)
        if task is not None:
            return task
        if len(self._process_tasks) >= 8:
            count(self.service, 'events.process.admission_deferred')
            return None
        task = asyncio.create_task(self._refresh_process(identity))
        self._process_tasks[identity] = task
        def completed(value):
            if self._process_tasks.get(identity) is value:
                self._process_tasks.pop(identity, None)
            if not value.cancelled() and value.exception() is not None:
                error = value.exception()
                logger.error('History display projection failed', exc_info=(type(error), error, error.__traceback__))
        task.add_done_callback(completed)
        return task

    async def loop(self):
        while not self.service.closed:
            # The bundled browser requests recent observations only. Active
            # execution already publishes its authoritative live overlay; it
            # must not accidentally request an exhaustive descendant import.
            web, complete = set(), set()
            for queue, client in tuple(self.service.queue_clients.items()):
                record = self.service.clients.records.get(client, {}) if client else self.service._state
                identity = self.service.queue_sessions.get(queue) or record.get('selectedSessionId')
                if identity:
                    (web if client and record.get('kind') == 'web' else complete).add(identity)
            self.windows.retain_interest(web)
            requested = sorted(web)
            if requested:
                offset = self._interest_cursor % len(requested)
                requested = requested[offset:] + requested[:offset]
                self._interest_cursor += 1
            for identity in requested:
                self.windows.request(identity)
            for identity in complete:
                if self.projector is not None:
                    self._process_refresh_task(identity)
                else:
                    try:
                        await self.refresh(identity, passive=True)
                    except (OSError, ValueError):
                        pass
                    except Exception:
                        logger.exception('Could not refresh explicit event view for %s', identity)
            await asyncio.sleep(1)

    @measured('events.read', session=True)
    def read(self, session):
        with self._read_lock, ExitStack() as stages:
            self._read_touched = set()
            try:
                return self._read(session, stages)
            except BaseException:
                # Discard potentially grown indexes from this read, without
                # forcing every unrelated chat to reparse its whole history.
                for key in self._read_touched:
                    self._forget_index(key)
                raise
            finally:
                self._read_touched = set()

    @measured('events.project', session=True)
    def _read(self, session, stages):
        def stage(name):
            if guard := getattr(self, '_budget_guard', None):
                if label := getattr(guard, 'stage', None):
                    label('source_' + name.removeprefix('events.'))
                guard()
            stages.close()
            stages.enter_context(measure(self.service, name, synchronous=True, session_id=session['id']))

        stage('events.scan')
        root = session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']
        queue = [root, session['id']]
        queue.extend(row.get('sessionId') or row.get('id') for row in session.get('workers', []))
        seen, indexes, nodes, workers = set(), [], [], {}
        inputs = []
        while queue:
            sid = queue.pop(0)
            if not sid or sid in seen:
                continue
            seen.add(sid)
            try:
                path = event_path(session, sid)
            except ValueError:
                if sid == root:raise
                continue
            index = self.indexes.get(str(path))
            count(self.service, 'events.index.hit' if index is not None else 'events.index.miss')
            if index is None:
                index = self._new_index(path, sid)
            self._read_touched.add(str(path))
            index._budget_guard = getattr(self, '_budget_guard', None)
            try:
                available = index.refresh()
                count(self.service, 'events.canonical.bytes_read', index.read_bytes)
            except BaseException:
                # A failed refresh may already have grown the mutable index.
                # Do not retain an unmeasured partial result over the budget.
                self._forget_index(str(path))
                raise
            inputs.append((path, index.revision))
            if not available:
                self._forget_index(str(path))
                continue
            indexes.append(index)
            workers.update(index.worker_runs)
            queue.extend(index.children)
        self.read_paths[session['id']] = tuple(path for path, _ in inputs)
        self.read_revisions[session['id']] = tuple((str(path), stamp) for path, stamp in inputs)
        self.read_proofs[session['id']] = {str(index.path): {
            'identity': index.identity, 'offset': index.offset, 'last': index.last,
            'sha256': index.prefix_digest} for index in indexes}
        count(self.service, 'events.project.indexes', len(indexes))
        count(self.service, 'events.project.paths', len(inputs))
        count(self.service, 'events.project.workers', len(workers))
        if not indexes:
            return None
        stage('events.bindings')
        live = session.get('execution', {})
        from .session_projection import accounting_projection, ACCOUNTING_FIELDS
        accounting = accounting_projection(live)
        live_nodes = live.get('nodes', [])
        aliases = {root, session['id']}
        def model_key(row, field='id'):
            sid = row.get('sessionId')
            return (session['id'] if sid in aliases else sid, row.get(field, row['id']))
        live_by_id = {model_key(row): row for row in live_nodes}
        live_by_source = {model_key(row, '_canonicalId'): row for row in live_nodes}
        bound_sessions = {session['id']}
        bound_sessions.update(row.get('sessionId') for row in accounting
                              if row.get('kind') == 'worker' and row.get('rootSessionId') == session['id'])
        bound_sessions.update(row.get('sessionId') for row in session.get('workers', []) if row.get('sessionId'))
        bindings = {model_key(row): row for row in accounting
                    if row.get('rootSessionId') == session['id'] and row.get('sessionId') in bound_sessions}
        stage('events.rows')
        for index in indexes:
            nodes.extend(index.rows(lambda row: bindings.get(model_key(row)), coalesce=False))
        nodes = merge_model_observations(nodes, aliases=aliases)
        count(self.service, 'events.project.nodes', len(nodes))
        stage('events.associate')
        def call_key(row):
            sid = row.get('sessionId')
            return (root if sid in aliases else sid, row.get('toolCallId'))
        tool_ids = {call_key(row): row for row in live_nodes if row.get('kind') == 'tool'}
        from .execution import generation_turns
        turns = {row['id']: copy.deepcopy(row) for row in generation_turns(session)}
        input_turns, message_turns, host_turns = {}, {}, set()
        for turn in turns.values():
            if turn.get('canonicalHistory') or turn.get('nativeHistory'):
                continue
            host_turns.add(turn['id'])
            for identity in {turn['id'], turn.get('inputId')} - {None}:
                input_turns.setdefault(identity, set()).add(turn['id'])
            for identity in {turn.get('messageId'), turn.get('userMessageId')} - {None}:
                message_turns.setdefault(identity, set()).add(turn['id'])
        messages = session.get('messages', [])
        users = [row for row in messages if row.get('role') == 'user']
        from .automatic_history import directory
        root_index = next((index for index in indexes if index.identity == root), None)
        source = {**session, 'nativeProject': session.get('nativeProject') or project_slug(session['workspace'])}
        try:
            associations = root_index.associations(directory(source)) if root_index else {}
        except BaseException:
            if root_index:
                self._forget_index(str(root_index.path))
            raise
        if root_index:
            transcript_paths = tuple(directory(source) / name for name in ('transcript.jsonl', 'transcript.jsonl.backup', 'transcript.jsonl.append-pending'))
            self.read_paths[session['id']] += transcript_paths
            self.read_revisions[session['id']] += tuple(
                (str(path), stamp) for path, stamp in zip(transcript_paths, root_index.association_revision[1]))
        stage('events.join')
        native_messages = [row for row in messages if type(row.get('nativeIndex')) is int]
        native_by_index = {}
        for row in native_messages:
            native_by_index.setdefault(row['nativeIndex'], row)
        native_positions = [row['nativeIndex'] for row in native_messages]
        native_ordered = all(a <= b for a, b in zip(native_positions, native_positions[1:]))
        fallback_turns = FallbackTurnLookup(users, turns, native_messages=bool(native_messages))

        def preceding_native(start, stop):
            if native_ordered:
                position = bisect_right(native_positions, stop) - 1
                return native_messages[position] if position >= 0 and native_positions[position] >= start else None
            # Preserve original presentation order for older unsorted windows.
            return next((row for row in reversed(native_messages)
                         if start <= row['nativeIndex'] <= stop), None)

        associated_turns = {}
        for node in nodes:
            association = associations.get(node.get('eventOrder')) if node.get('sessionId') in aliases else None
            if not association:
                continue
            previous = tool_ids.get(call_key(node)) if node['kind'] == 'tool' else live_by_source.get(model_key(node)) or live_by_id.get(model_key(node))
            candidates = (bindings.get(model_key(node)), previous, node)
            identity = next((row['turnId'] for row in candidates if row and row.get('turnId') in host_turns), None)
            if identity:
                # Exact host call IDs also link sibling native events while the
                # latest user message is waiting for its transcript index.
                associated_turns.setdefault(association['turn'], set()).add(identity)
        remap, assigned_models = {}, set()
        native_models = {model_key(row) for row in nodes if row['kind'] == 'llm'}
        for node in nodes:
            binding = bindings.get(model_key(node))
            if binding and binding.get('kind') != node.get('kind'):
                binding = None
            node['_canonicalId'] = node['id']
            previous = tool_ids.get(call_key(node)) if node['kind'] == 'tool' else live_by_source.get(model_key(node)) or live_by_id.get(model_key(node))
            if previous and node['kind'] == 'llm':
                key = model_key(previous)
                if key in assigned_models or (key != model_key(node) and key in native_models):
                    previous = None  # Another canonical attempt owns this identity.
            if previous is None and node['kind'] == 'llm':
                candidates = [row for row in live_nodes if row.get('kind') == 'llm' and not row.get('canonicalHistory')
                    and (row.get('sessionId') == node.get('sessionId') or row.get('sessionId') in aliases and node.get('sessionId') in aliases)
                    and model_key(row) not in native_models and model_key(row) not in assigned_models
                    and same_model_call(row, node)]
                if len(candidates) == 1:
                    previous = candidates[0]
            if previous:
                old = node['id'];node['id'] = previous['id'];remap[old] = node['id']
                for key in ('turnId', 'parentId', 'lifecycle'):
                    if previous.get(key):node[key] = previous[key]
                if previous.get('lifecycle') == 'background' and previous.get('label') == 'Session naming':
                    node['label'] = 'Session naming'
                if node['kind'] == 'tool' and previous.get('liveObservation'):
                    node['liveObservation'] = True
                if node['kind'] == 'llm' and previous.get('usage'):
                    recorded = node.get('usage') or {}
                    node['usage'] = {**previous['usage'], **recorded}
                    if recorded.get('costUsd') is None and previous['usage'].get('costUsd') is not None:
                        node['usage']['costType'] = previous['usage'].get('costType', 'reported')
                if node.get('phase') in LIVE_PHASES and isinstance(previous.get('endedAt'), (int, float)) and previous['endedAt'] >= (node.get('startedAt') or 0):
                    node.update(phase=previous.get('phase', 'recorded'), endedAt=previous['endedAt'])
            if node['kind'] == 'llm':
                assigned_models.add(model_key(node))
            if binding:
                # Exact host-owned call identity joins display and accounting.
                # Native timing similarity alone is never budget authority, and
                # stale log snapshots cannot replace newer observed revisions.
                node.update({key: copy.deepcopy(value) for key, value in binding.items() if key in ACCOUNTING_FIELDS})
                node['liveObservation'] = True
            if node.get('phase') in LIVE_PHASES and not node.get('endedAt'):
                observed = previous and previous.get('liveObservation') and previous.get('phase') in LIVE_PHASES and not previous.get('endedAt')
                active = session.get('status') in {'working', 'starting', 'stopping'} or node.get('lifecycle') == 'background'
                active = active or any((row.get('sessionId') or row.get('id')) == node.get('sessionId') and row.get('status') in LIVE_PHASES for row in session.get('workers', []))
                if not observed or not active:
                    node['phase'] = 'recorded'  # A historical start is not proof work is still running.
            at = node.get('startedAt')
            association = associations.get(node.get('eventOrder')) if node.get('sessionId') in aliases else None
            if association and native_messages:
                anchor = native_by_index.get(association['turn'])
                # Native history and live admission describe the same input.
                # Preserve its host turn rather than leaving an empty running
                # placeholder beside a second canonical work group. An anchor
                # alone is insufficient: independent voice turns can share one.
                matches = associated_turns.get(association['turn'], set()) | (
                    (input_turns.get(anchor.get('inputId'), set()) | message_turns.get(anchor['id'], set())) if anchor else set())
                # A transcript association must not replace a known host turn:
                # a later live event would restore just that call and split the
                # uninterrupted model/tool work into two visible groups.
                own_turn = node.get('turnId') if node.get('turnId') in host_turns else None
                key = own_turn or (next(iter(matches)) if len(matches) == 1 else
                                   'native-turn:' + (anchor['id'] if anchor else str(association['turn'])))
                node['turnId'] = key
                fallback_turns.ensure(key, {'id': key, 'anchorMessageId': anchor['id'] if anchor else None,
                                           'canonicalHistory': True, 'phase': 'completed'})
                if association['position'] is not None:
                    preceding = preceding_native(association['turn'], association['position'])
                    if preceding is not None:
                        node['anchorMessageId'] = preceding['id']
                    elif key in host_turns:
                        node.pop('anchorMessageId', None)  # Keep live message/timing placement until its index arrives.
                    else:
                        node['anchorMessageId'] = None
            if node.get('turnId') not in turns:
                anchor = fallback_turns.anchor(at)
                turn = fallback_turns.turn(anchor)
                key = turn['id'] if turn else 'native-turn:' + (anchor['id'] if anchor else root)
                node['turnId'] = key
                if native_messages and not anchor:
                    node['anchorMessageId'] = None  # Undated, unassociated evidence stays before the page.
                fallback_turns.ensure(key, {'id': key, 'anchorMessageId': anchor['id'] if anchor else None,
                                           'canonicalHistory': True, 'phase': 'completed'})
            for field in ('input', 'output', 'error', 'request', 'response', 'delegateRequest', 'delegateResponse'):
                if node.get(field + 'Detail'):
                    node[field + 'Detail'].update(id=node['id'], sessionId=session['id'])
        by_id = {row['id']: row for row in nodes}
        nodes_by_session, runs_by_session = {}, {}
        for row in nodes:
            nodes_by_session.setdefault(row.get('sessionId'), []).append(row)
        for row in workers.values():
            runs_by_session.setdefault(row.get('sessionId'), []).append(row)
        for worker in workers.values():
            child = worker['sessionId']
            worker = copy.deepcopy(worker)
            worker['parentId'] = remap.get(worker['parentId'], worker['parentId'])
            runs = runs_by_session[child]
            # Keep exact old accounting identities when unambiguous. A resumed
            # session with several admissions must never borrow this fallback.
            legacy = bindings.get(model_key({'id': 'worker:' + child, 'sessionId': child}))
            if len(runs) == 1 and legacy and legacy.get('parentId') == worker['parentId']:
                worker['id'] = legacy['id']
            parent = by_id.get(worker['parentId'])
            if parent:
                binding = bindings.get(model_key(worker))
                bound_turn = binding.get('turnId') if binding and binding.get('kind') == 'worker' else None
                bound_turn = bound_turn if bound_turn in host_turns else None
                worker['turnId'] = bound_turn or parent['turnId']
                # Recovery/steering observations can look like new native user
                # turns. A recorded host admission keeps its delegated work on
                # the original turn when this disposable view is rebuilt.
                if bound_turn and parent.get('turnId') not in host_turns:
                    parent['turnId'] = bound_turn
                    if parent.get('anchorMessageId') is None:
                        parent.pop('anchorMessageId', None)
                members = [row for row in nodes_by_session.get(child, ()) if
                           (row.get('parentId') == worker['id'] or len(runs) == 1 and not row.get('parentId'))]
                for row in members:
                    row['parentId'] = worker['id']
                    member_binding = bindings.get(model_key(row))
                    member_turn = member_binding.get('turnId') if member_binding and member_binding.get('kind') == row.get('kind') else None
                    row['turnId'] = member_turn if member_turn in host_turns else worker['turnId']
                ends = [row['endedAt'] for row in members if isinstance(row.get('endedAt'), (int, float))]
                if ends:worker.update(endedAt=max(ends), phase='completed')
                if binding and binding.get('kind') == 'worker':
                    for field in ('phase', 'endedAt'):
                        if field in binding:
                            worker[field] = binding[field]
                live_worker = live_by_id.get(model_key(worker))
                if live_worker:
                    # Exact worker/run identity keeps an observed worker in the
                    # bounded live view after its native log replaces the node.
                    # Unobserved sibling runs remain historical.
                    for field in ('phase', 'endedAt', 'runId', 'routing', 'parentProvider', 'provider', 'model', 'liveObservation'):
                        if field in live_worker:
                            worker[field] = live_worker[field]
                nodes.append(worker)
                nodes_by_session.setdefault(worker.get('sessionId'), []).append(worker)
        # Metadata may arrive just before the logging hook flushes. It can
        # describe in-flight work, but never supplies an alternate tool body.
        ids = {row['id'] for row in nodes}
        completed_calls = CompletedModelCalls(nodes)
        for row in live_nodes:
            if row['id'] not in ids and not row.get('canonicalHistory') and not row.get('nativeHistory'):
                if row.get('kind') == 'llm' and row.get('endedAt') and any(
                    node['kind'] == 'llm' and same_model_call(row, node) and
                    (node.get('sessionId') == row.get('sessionId') or node.get('sessionId') in aliases and row.get('sessionId') in aliases)
                    for node in completed_calls.candidates(row)):
                    continue  # Ambiguous parallel IDs: show the canonical calls, never both sets.
                if row.get('liveObservation'):
                    nodes.append({key: value for key, value in row.items() if key not in
                                  {'input', 'output', 'error', 'inputDetail', 'outputDetail', 'errorDetail', '_eventFields'}})
                else:
                    # Do not erase older saved history when its native log is
                    # incomplete. This is pre-existing data, never a new capture.
                    nodes.append(copy.deepcopy(row))
                completed_calls.add(nodes[-1])
        nodes.sort(key=lambda row: (row.get('startedAt') or 0, row.get('eventOrder', 0)))
        tree = {'nodes': nodes, 'turns': list(turns.values()), 'currentTurnId': live.get('currentTurnId'), 'source': 'events.jsonl',
                'retiredUsageNodes': accounting}
        refresh_turn_lifecycle(tree, session, host_turns=host_turns)
        stage('events.plan')
        from .chat_plan import latest_plan
        tree['plan'] = latest_plan(nodes, aliases, read_field)
        stage('events.usage')
        refresh_usage(tree)
        stage('events.retain')
        # Charges live only for this retention pass. Entries earlier in this
        # list can evict later entries from the bounded RAM cache before their
        # turn; the read already owns those objects and their immutable receipt.
        receipts = {str(index.path): (index, self._index_sizes[str(index.path)])
                    for index in indexes if self.indexes.get(str(index.path)) is index
                    and str(index.path) in self._index_sizes}
        for index in indexes:
            self._retain_index(index, receipt=receipts.get(str(index.path)))
        stages.close()
        return tree

    def read_plan(self, session, *, include_items=False):
        """Read root todo receipts without child trees or transcript association.

        Workspace summaries share the incremental event index, but do not
        hydrate a saved execution graph or build the full chat display model.
        """
        from .chat_plan import overview
        identity = session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']
        path = event_path(session, identity)
        key = str(path)
        with self._read_lock:
            index = self.indexes.get(key) or self._new_index(path, identity)
            try:
                available = index.refresh()
                count(self.service, 'events.canonical.bytes_read', index.read_bytes)
                if not available:
                    self._forget_index(key)
                    return {'sessionId': session['id'], 'plan': None}
                aliases = {identity, session['id']}
                nodes = [row for row in index.nodes.values()
                         if row.get('kind') == 'tool' and row.get('label') == 'todo'
                         and row.get('sessionId') in aliases]
                result = overview({**session, 'execution': {'nodes': nodes}}, include_items=include_items)
                self._retain_index(index)
                return result
            except BaseException:
                self._forget_index(key)
                raise

    @measured('events.refresh', session=True)
    async def refresh(self, identity, *, passive=False):
        """Refresh exact inputs, or reuse a publication-fenced passive receipt.

        Internal writers invalidate at publication/progress and runtime ingress.
        Code mutating issued aliases without publishing must use the default
        explicit refresh (or invalidate); passive=True belongs to the host loop.
        """
        if self.projector is not None and passive:
            task = self._process_refresh_task(identity)
            return await asyncio.shield(task) if task is not None else None
        async with self.lock:
            session = self.service._session(identity)
            identity = session['id']
            cached = self.projected.get(identity)
            clean = self._clean_inputs.get(identity) if passive else None
            paths = self.read_paths.get(identity, ())
            def stamps():
                result = []
                for path in paths:
                    try:
                        info = path.stat()
                        result.append((str(path), ((info.st_dev, info.st_ino), info.st_size, info.st_mtime_ns, info.st_ctime_ns)))
                    except FileNotFoundError:
                        result.append((str(path), None))
                return tuple(result)
            before = await asyncio.to_thread(stamps)
            # A writer or admission can run while stat waits. Re-resolve the
            # owner and verify the same receipt after that boundary.
            session = self.service._session(identity)
            # Compare detached inputs before copying or rebuilding the model
            # tree. External appends/replacements and live metadata still win.
            root = session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']
            same_root = paths and paths[0] == event_path(session, root)
            same_sources = bool(cached and same_root and cached[1] == before)
            if (same_sources and clean is not None
                    and self._clean_inputs.get(identity) is clean
                    and clean[0]() is session
                    and clean[1] == self._input_identity(session)):
                count(self.service, 'events.projection.reused')
                count(self.service, 'events.projection.passive_reused')
                return
            self._clean_inputs.pop(identity, None)
            inputs = self.projection_input(session)
            same_inputs = bool(cached and same_root and cached[0] == inputs)
            if same_inputs and same_sources:
                self._remember_clean(session)
                count(self.service, 'events.projection.reused')
                return
            count(self.service, 'events.projection.rebuilt')
            if not cached:
                count(self.service, 'events.rebuild.cold')
            elif not same_root:
                count(self.service, 'events.rebuild.root')
            else:
                if not same_inputs:
                    count(self.service, 'events.rebuild.input')
                if not same_sources:
                    count(self.service, 'events.rebuild.source')
                    for kind, reason in source_change_reasons(cached[1], before):
                        count(self.service, 'events.source.' + kind + '.' + reason)
            previous = copy.deepcopy(inputs)
            tree = await asyncio.to_thread(self.read, previous)
            if self.service.closed:
                return
            async with self.service.lock:
                session = self.service._session(identity)
                if self.projection_input(session) != previous:
                    count(self.service, 'events.projection.superseded')
                    return  # A newer live update won; retry from it next tick.
                if tree is not None and session.get('execution') != tree:
                    session['execution'] = tree
                    # Only this chat's execution changed. A global publication
                    # also serializes unrelated state and invalidates the native
                    # catalog, forcing its next scan to reconcile every chat.
                    # The save guard widens if shared navigation facts changed.
                    self.service._publish(session_ids={session['id']}, record_only=True, detail_only=True)
                # Use the signatures actually read, not a later stat that may
                # already describe bytes appended after the projection.
                self.projected[identity] = (copy.deepcopy(self.projection_input(session)), self.read_revisions[identity])
                self._remember_clean(session)
                self.projected.move_to_end(identity)
                while len(self.projected) > 64:
                    expired, _ = self.projected.popitem(last=False)
                    self.invalidate({expired})
                    self._clean_inputs.pop(expired, None)
                    self.read_paths.pop(expired, None)
                    self.read_revisions.pop(expired, None)
                    self.read_proofs.pop(expired, None)

    def _set_work_status(self, session, status, reason=None, *, receipt=None):
        value = {'status': status, 'current': receipt is not None}
        retained = receipt or session.get('workProjection', {}).get('receipt')
        if (isinstance(retained, dict) and retained.get('sessionId') == session['id']
                and retained.get('hostInstanceId') == getattr(self.service, 'instance_id', None)):
            value['receipt'] = retained
        if reason is not None:
            value['reason'] = reason
        if session.get('workProjection') == value:
            return False
        session['workProjection'] = value
        return True

    def _accept_work(self, session, revisions):
        """Constant-size evidence of this host's already-validated read scope.

        Counts describe the accepted tree, not rendered groups or an independent
        inventory of canonical history. No body walk, encoding or I/O is needed.
        """
        try:
            weakref.ref(session)
        except TypeError:
            # Embedders with plain dict owners have no weak lifetime proof.
            # Keep their display status, without claiming a current receipt.
            return self._set_work_status(session, 'ready')
        self._work_generation += 1
        execution = session.get('execution') or {}
        receipt = {'hostInstanceId': getattr(self.service, 'instance_id', None),
                   'sessionId': session['id'], 'generation': self._work_generation,
                   'sourceCount': len(revisions), 'nodeCount': len(execution.get('nodes', ())),
                   'turnCount': len(execution.get('turns', ()))}
        return self._set_work_status(session, 'ready', receipt=receipt)

    def _notify_work(self, identity):
        """Ephemeral status uses the existing bounded, loss-safe web lane."""
        shell = getattr(self.service, 'shell', None)
        if shell is None:
            return
        # A web subscription exists before shell-resource registration. Include
        # connected/current readers as TranscriptWindows does; notify still
        # filters to this selected owner and uses the existing loss-safe lane.
        clients = self.service.clients
        identities = set(shell.resources.clients)
        identities.update(getattr(self.service, 'queue_clients', {}).values())
        current = getattr(clients, 'current', None)
        identities.add(current.get() if current is not None else None)
        # Retain at most one notice per interested client. A read of B can
        # complete while A has unsaved progress; A's narrow publication need not
        # include B. Do not leak dirty state or lose B's eventual ready notice.
        self._pending_work_notifications = {
            client: owner for client, owner in self._pending_work_notifications.items()
            if client in identities and clients.records.get(client, {}).get('selectedSessionId') == owner}
        for client in identities:
            record = clients.records.get(client, {})
            if record.get('kind') == 'web' and record.get('selectedSessionId') == identity:
                if getattr(self.service, '_progress_dirty', False):
                    self._pending_work_notifications[client] = identity
                else:
                    shell.notify(client)

    def flush_work_notifications(self):
        """After a successful canonical commit, deliver retained read notices."""
        pending, self._pending_work_notifications = self._pending_work_notifications, {}
        for identity in set(pending.values()):
            self._notify_work(identity)

    async def _refresh_process(self, identity):
        """Publish only an exact, still-owned display read from the subprocess.

        The service lock protects only admission/publication. Input construction,
        source checks, hashing, delta materialization and comparisons run off-loop.
        The bounded in-flight fence catches published mutations across awaits.
        This path is internal/passive: explicit reads retain the exact reference
        path, including its unpublished-alias comparison under the service lock.
        """
        from .history_read_model import input_digest, materialize_result, source_stamp, tuple_tree
        session = self.service._session(identity)
        identity = session['id']
        if identity not in self.projected and identity not in self._refused:
            async with self.service.lock:
                if self.service.closed:
                    return
                session = self.service._session(identity)
                if self._set_work_status(session, 'loading'):
                    self._notify_work(identity)
        owner = session
        fence = [False]
        self._pending_inputs[identity] = fence
        try:
            cached = self.projected.get(identity)
            clean = self._clean_inputs.get(identity)
            refused = self._refused.get(identity)
            paths = tuple(row[0] for row in refused['revisions']) if refused and refused['revisions'] is not None else self.read_paths.get(identity, ())
            before = await asyncio.to_thread(lambda: tuple((str(path), source_stamp(path)) for path in paths))
            session = self.service._session(identity)
            if session is not owner or fence[0]:
                count(self.service, 'events.projection.superseded')
                return
            root = session.get('nativeIdentity') or session.get('runtimeSessionId') or identity
            same_root = bool(paths and paths[0] == event_path(session, root))
            same_sources = bool(cached and same_root and cached[1] == before)
            same_refusal = bool(refused and (refused['revisions'] is None or refused['revisions'] == before))
            if (same_refusal and clean is not None and self._clean_inputs.get(identity) is clean
                    and clean[0]() is session and clean[1] == self._input_identity(session)):
                count(self.service, 'events.process.refusal_reused')
                return
            if (same_sources and clean is not None and self._clean_inputs.get(identity) is clean
                    and clean[0]() is session and clean[1] == self._input_identity(session)
                    and session.get('workProjection', {}).get('current') is True):
                count(self.service, 'events.projection.passive_reused')
                return
            self._clean_inputs.pop(identity, None)
            if not same_refusal:
                async with self.service.lock:
                    if fence[0] or self.service.closed or self.service._session(identity) is not owner:
                        return
                    if self._set_work_status(owner, 'loading'):
                        self._notify_work(identity)
            def prepare():
                value = copy.deepcopy(self.projection_input(owner))
                return value, input_digest(value)
            inputs, digest = await asyncio.to_thread(prepare)
            if fence[0] or self.service._session(identity) is not owner:
                count(self.service, 'events.projection.superseded')
                return
            if same_refusal and refused['digest'] == digest:
                self._remember_clean(owner)
                count(self.service, 'events.process.refusal_reused')
                return
            self._refused.pop(identity, None)
            if same_sources and cached[0] == digest:
                # A published mutation can invalidate a receipt without changing
                # its actual inputs. Restore coverage once after exact validation;
                # the quiet weak-receipt path above makes no publication.
                async with self.service.lock:
                    if fence[0] or self.service.closed or self.service._session(identity) is not owner:
                        return
                    if owner.get('workProjection', {}).get('current') is not True:
                        self._accept_work(owner, before)
                        self._notify_work(identity)
                self._remember_clean(owner)
                count(self.service, 'events.projection.reused')
                return
            result = await self.projector.project(inputs, key=identity)
            if self.service.closed or fence[0] or self.service._session(identity) is not owner:
                count(self.service, 'events.projection.superseded')
                return
            if result.get('status') != 'complete':
                from .history_projection_process import REASONS
                reason = result.get('reason')
                reason = reason if reason in REASONS else 'worker_failed'
                count(self.service, 'events.process.retry.' + reason)
                diagnostics = getattr(self.service, 'performance_diagnostics', None)
                if diagnostics is not None and result.get('failure') is not None:
                    diagnostics.record_projection_failure(reason, result['failure'], session_id=identity)
                refused_revisions = None
                deterministic = reason in {'input_limit', 'output_limit'}
                if reason == 'input_limit':
                    refused_revisions = ()  # Encoded input size cannot change when its exact digest does not.
                elif reason == 'output_limit' and 'revisions' in result:
                    from .history_read_model import SelectedHistoryReadModel
                    candidate = tuple((path, tuple_tree(stamp)) for path, stamp in result['revisions'])
                    scoped = SelectedHistoryReadModel._valid_sources(inputs, {
                        'paths': [path for path, _ in candidate], 'revisions': candidate})
                    if scoped:
                        current = await asyncio.to_thread(lambda: tuple((path, source_stamp(path)) for path, _ in candidate))
                        if current != candidate:
                            count(self.service, 'events.projection.superseded')
                            return
                        refused_revisions = candidate
                async with self.service.lock:
                    if fence[0] or self.service.closed or self.service._session(identity) is not owner:
                        return
                    if self._set_work_status(owner, 'unavailable' if deterministic else 'retrying', reason):
                        self._notify_work(identity)
                    if deterministic:
                        self._refused[identity] = {'reason': reason, 'digest': digest, 'revisions': refused_revisions}
                        self._refused.move_to_end(identity)
                        while len(self._refused) > 8:
                            self._refused.popitem(last=False)
                        self._remember_clean(owner)
                        return  # Retry this refusal only when its inputs/sources change.
                self._retry_at[identity] = time.monotonic() + (30 if reason == 'memory_limit' else 3)
                self._retry_at.move_to_end(identity)
                while len(self._retry_at) > 64:
                    self._retry_at.popitem(last=False)
                return  # Keep selection and its last readable Work; the interest loop retries.
            revisions = tuple((path, tuple_tree(stamp)) for path, stamp in result['revisions'])
            def validate():
                if input_digest(self.projection_input(owner)) != digest:
                    return False, None, False
                if any(source_stamp(path) != stamp for path, stamp in revisions):
                    return False, None, False
                tree = materialize_result(owner, result)
                return True, tree, tree is not None and owner.get('execution') != tree
            valid, tree, changed = await asyncio.to_thread(validate)
            async with self.service.lock:
                if (not valid or fence[0] or self.service.closed
                        or self.service._session(identity) is not owner):
                    count(self.service, 'events.projection.superseded')
                    return
                if changed:
                    owner['execution'] = tree
                status_changed = self._accept_work(owner, revisions)
                if changed:
                    self.service._publish(session_ids={identity}, record_only=True, detail_only=True)
                elif status_changed:
                    self._notify_work(identity)
                self._retry_at.pop(identity, None)
                self.projected[identity] = (result['inputDigest'], revisions)
                self.read_paths[identity] = tuple(Path(path) for path in result['paths'])
                self.read_revisions[identity] = revisions
                self._remember_clean(owner)
                self.projected.move_to_end(identity)
                while len(self.projected) > 64:
                    retired, _ = self.projected.popitem(last=False)
                    self.invalidate({retired})
                    self._clean_inputs.pop(retired, None)
                    self.read_paths.pop(retired, None)
                    self.read_revisions.pop(retired, None)
                for name, amount in result.get('diagnostics', {}).get('counters', {}).items():
                    if name.startswith('events.') and type(amount) is int:
                        count(self.service, name, amount)
                count(self.service, 'events.process.completed')
        finally:
            self._pending_inputs.pop(identity, None)
