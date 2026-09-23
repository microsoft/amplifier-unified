"""Disposable indexes over native event logs. Payloads stay in events.jsonl.

The authenticated conversation API reads the user's recorded tool fields as-is.
This module never writes a capture, changes capture policy, or executes work.
"""
from __future__ import annotations

import asyncio
from bisect import bisect_left, bisect_right
from collections import OrderedDict
import copy
from datetime import datetime
import hashlib
import json
import logging
import math
import os
from pathlib import Path

from .execution import LIVE_PHASES, refresh_usage
from .execution_events import public_usage
from .session_files import amplifier_home, project_slug

logger = logging.getLogger(__name__)


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
        for field in ('requestInfo', 'requestDetail', '_eventFields', 'error', 'errorDetail'):
            if field in row:closest[field] = row[field]
        if closest.get('requestDetail'):closest['requestDetail']['id'] = closest['id']
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


def read_field(reference):
    """References originate in this server's index, never in request parameters."""
    try:
        with Path(reference['path']).open('rb') as stream:
            stream.seek(reference['offset'])
            raw = stream.read(reference['bytes'])
    except OSError as exc:
        raise ValueError('The recorded action is temporarily unavailable. Try loading it again.') from exc
    if hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise ValueError('The event log changed. Refresh this conversation to read its current content.')
    return text(json.loads(raw)['data'][reference['key']])


class EventIndex:
    def __init__(self, path, identity):
        self.path, self.identity = path, identity
        self.reset()

    def reset(self):
        self.offset = 0
        self.file_id = None
        self.nodes = OrderedDict()
        self.children = {}
        self.pending = {}
        self.last = None
        self.revision = None
        self.diagnostics = []
        self.association_events = []
        self.association_revision = None
        self.association_cache = {}

    def refresh(self):
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
        if not reset and self.last:
            with self.path.open('rb') as stream:
                stream.seek(self.last['offset'])
                reset = hashlib.sha256(stream.read(self.last['bytes'])).hexdigest() != self.last['sha256']
        if reset:
            self.reset()
        self.file_id = identity
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            # Parsing runs off the app's event loop. A partial final line is
            # retried on the next append, never committed to the index.
            while raw := stream.readline():
                offset = self.offset
                if not raw.endswith(b'\n'):
                    break
                self.offset = stream.tell()
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
        self.revision = revision
        return True

    def field(self, node, field, data, keys, reference, *, preview=True):
        key = next((key for key in keys if key in data), None)
        if key is None:
            return
        value = text(data[key])
        if preview:
            node[field] = value[:512]
        node.setdefault('_eventFields', {})[field] = {**reference, 'key': key}
        node[field + 'Detail'] = {'part': 'nodes', 'id': node['id'], 'field': field,
                                 'digest': hashlib.sha256(value.encode()).hexdigest(), 'length': len(value)} if not preview or len(value) > 512 else None
        if field == 'request':node[field + 'Detail']['lines'] = value.count('\n') + 1

    def ingest(self, event, reference):
        name, data = event.get('event'), event['data']
        if not isinstance(name, str):
            return
        sid = data.get('session_id') or event.get('session_id') or self.identity
        at = timestamp(event.get('timestamp') or data.get('timestamp'))
        # Older app-only diagnostic receipts have no tool body. They must not
        # restart a completed native tool or replace its precise timestamps.
        if data.get('kind') == 'tool' and isinstance(data.get('id'), str):
            return
        if name == 'prompt:submit' or name.startswith(('tool:', 'llm:', 'provider:')):
            self.association_events.append({'event': name, 'session_id': sid, 'offset': reference['offset'],
                'data': {key: data[key] for key in ('tool_call_id', 'call_id', 'message_id', 'prompt', 'purpose', 'origin_module') if key in data}})
        if name in {'tool:pre', 'tool:post', 'tool:error'}:
            call = data.get('tool_call_id') or data.get('call_id')
            if not isinstance(call, str):
                return
            key = f'tool:{sid}:{call}'
            node = self.nodes.setdefault(key, {'id': key, 'kind': 'tool', 'sessionId': sid,
                'toolCallId': call, 'label': data.get('tool_name') or data.get('tool') or 'Tool',
                'canonicalHistory': True, 'eventOrder': reference['offset']})
            if name == 'tool:pre' and node.get('endedAt') is not None:
                for field in ('output', 'error'):
                    node.pop(field, None)
                    node.pop(field + 'Detail', None)
                    node.get('_eventFields', {}).pop(field, None)
                node.pop('endedAt', None)
                node['startedAt'] = at
            self.field(node, 'input', data, ('tool_input', 'arguments', 'input'), reference)
            if at is not None:
                node.setdefault('startedAt', at)
            node['phase'] = 'running' if name == 'tool:pre' else 'error' if name == 'tool:error' else 'completed'
            if name != 'tool:pre':
                node['endedAt'] = at
                self.field(node, 'output', data, ('result', 'tool_result'), reference)
                self.field(node, 'error', data, ('error', 'error_message'), reference)
                result = data.get('result', data.get('tool_result'))
                if isinstance(result, dict) and result.get('success') is False:
                    node['phase'] = 'error'
            return
        if name == 'delegate:agent_spawned':
            child = data.get('sub_session_id')
            if isinstance(child, str):
                self.children[child] = {'id': 'worker:' + child, 'kind': 'worker', 'sessionId': child,
                    'parentId': f"tool:{sid}:{data.get('tool_call_id')}", 'label': data.get('agent') or 'Worker',
                    'startedAt': at, 'phase': 'recorded', 'canonicalHistory': True}
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
        scope = (sid, data.get('provider'), data.get('model'))
        if name == 'llm:request':
            key = f'llm:{sid}:{request or reference["offset"]}'
            node = {'id': key, 'kind': 'llm', 'sessionId': sid, 'label': 'Model call',
                'provider': data.get('provider'), 'model': data.get('model'), 'startedAt': at,
                'phase': 'running', 'canonicalHistory': True, 'eventOrder': reference['offset']}
            raw = data.get('raw')
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
                self.field(node, 'request', data, ('raw',), reference, preview=False)
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
        duration = data.get('duration_ms')
        if 'startedAt' not in node and at is not None and isinstance(duration, (int, float)):
            node['startedAt'] = at - duration / 1000
        node.update(provider=data.get('provider'), model=data.get('model'), endedAt=at,
                    phase='error' if name == 'llm:error' or data.get('status') == 'error' else 'completed',
                    usage=public_usage(data.get('usage')))
        self.field(node, 'error', data, ('error', 'error_message'), reference)

    def associations(self, directory):
        """Use Foundation's exact transcript associations for undated history."""
        from amplifier_foundation.session.history import SessionHistoryStore, associate_events
        stamps = []
        for name in ('transcript.jsonl', 'transcript.jsonl.backup'):
            try:
                stat = (directory / name).stat()
                stamps.append(((stat.st_dev, stat.st_ino), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            except FileNotFoundError:
                stamps.append(None)
        revision = (self.revision, tuple(stamps))
        if revision == self.association_revision:
            return self.association_cache
        messages = SessionHistoryStore(directory, session_id=self.identity).load(include_events=False).messages if any(stamps) else []
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


class EventLogView:
    def __init__(self, service):
        self.service = service
        self.indexes = OrderedDict()
        self.task = None
        self.lock = asyncio.Lock()
        self.projected = OrderedDict()
        self.read_paths = {}
        self.read_revisions = {}

    @staticmethod
    def projection_input(session):
        """Only canonical association/lifecycle inputs, never draft or progress."""
        result = {key: session[key] for key in ('id', 'workspace', 'nativeProject', 'nativeIdentity',
                  'runtimeSessionId', 'status', 'execution') if key in session}
        result['messages'] = [{key: row[key] for key in ('id', 'role', 'createdAt', 'nativeIndex', 'inputId', 'source')
                               if key in row} for row in session.get('messages', [])]
        result['workers'] = [{key: row[key] for key in ('id', 'sessionId', 'status') if key in row}
                            for row in session.get('workers', [])]
        return result

    def start(self):
        self.task = asyncio.create_task(self.loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def loop(self):
        while not self.service.closed:
            from .history_demand import event_sessions
            for identity in event_sessions(self.service):
                try:
                    await self.refresh(identity)
                except (OSError, ValueError):
                    pass  # Keep the last readable view while a log is replaced.
                except Exception:
                    logger.exception('Could not refresh the event-log view for %s', identity)
            await asyncio.sleep(1)

    def read(self, session):
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
            index = self.indexes.setdefault(str(path), EventIndex(path, sid))
            self.indexes.move_to_end(str(path))
            available = index.refresh()
            inputs.append((path, index.revision))
            if not available:
                continue
            indexes.append(index)
            workers.update(index.children)
            queue.extend(index.children)
        while len(self.indexes) > max(64, len(seen)):
            self.indexes.popitem(last=False)
        self.read_paths[session['id']] = tuple(path for path, _ in inputs)
        self.read_revisions[session['id']] = tuple((str(path), stamp) for path, stamp in inputs)
        if not indexes:
            return None
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
        for index in indexes:
            nodes.extend(index.rows(lambda row: bindings.get(model_key(row)), coalesce=False))
        nodes = merge_model_observations(nodes, aliases=aliases)
        def call_key(row):
            sid = row.get('sessionId')
            return (root if sid in aliases else sid, row.get('toolCallId'))
        tool_ids = {call_key(row): row for row in live_nodes if row.get('kind') == 'tool'}
        turns = {row['id']: copy.deepcopy(row) for row in live.get('turns', [])}
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
        associations = root_index.associations(directory(source)) if root_index else {}
        if root_index:
            transcript_paths = tuple(directory(source) / name for name in ('transcript.jsonl', 'transcript.jsonl.backup'))
            self.read_paths[session['id']] += transcript_paths
            self.read_revisions[session['id']] += tuple(
                (str(path), stamp) for path, stamp in zip(transcript_paths, root_index.association_revision[1]))
        native_messages = [row for row in messages if type(row.get('nativeIndex')) is int]
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
                anchor = next((row for row in native_messages if row['nativeIndex'] == association['turn']), None)
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
                turns.setdefault(key, {'id': key, 'anchorMessageId': anchor['id'] if anchor else None,
                                      'canonicalHistory': True, 'phase': 'completed'})
                if association['position'] is not None:
                    preceding = [row for row in native_messages if association['turn'] <= row['nativeIndex'] <= association['position']]
                    if preceding:
                        node['anchorMessageId'] = preceding[-1]['id']
                    elif key in host_turns:
                        node.pop('anchorMessageId', None)  # Keep live message/timing placement until its index arrives.
                    else:
                        node['anchorMessageId'] = None
            if node.get('turnId') not in turns:
                preceding = [row for row in users if isinstance(at, (int, float)) and isinstance(row.get('createdAt'), (int, float))
                             and row['createdAt'] <= at and row.get('source') != 'native']
                anchor = preceding[-1] if preceding else users[0] if users and not native_messages else None
                turn = next((row for row in turns.values() if anchor and (row.get('anchorMessageId') == anchor['id']
                    or row.get('inputId') == anchor.get('inputId') and anchor.get('inputId'))), None)
                key = turn['id'] if turn else 'native-turn:' + (anchor['id'] if anchor else root)
                node['turnId'] = key
                if native_messages and not anchor:
                    node['anchorMessageId'] = None  # Undated, unassociated evidence stays before the page.
                turns.setdefault(key, {'id': key, 'anchorMessageId': anchor['id'] if anchor else None,
                                      'canonicalHistory': True, 'phase': 'completed'})
            for field in ('input', 'output', 'error', 'request'):
                if node.get(field + 'Detail'):
                    node[field + 'Detail'].update(id=node['id'], sessionId=session['id'])
        by_id = {row['id']: row for row in nodes}
        for child, worker in workers.items():
            worker = copy.deepcopy(worker)
            worker['parentId'] = remap.get(worker['parentId'], worker['parentId'])
            parent = by_id.get(worker['parentId'])
            if parent:
                worker['turnId'] = parent['turnId']
                members = [row for row in nodes if row.get('sessionId') == child]
                for row in members:
                    row['parentId'] = worker['id'];row['turnId'] = parent['turnId']
                ends = [row['endedAt'] for row in members if isinstance(row.get('endedAt'), (int, float))]
                if ends:worker.update(endedAt=max(ends), phase='completed')
                nodes.append(worker)
        # Metadata may arrive just before the logging hook flushes. It can
        # describe in-flight work, but never supplies an alternate tool body.
        ids = {row['id'] for row in nodes}
        for row in live_nodes:
            if row['id'] not in ids and not row.get('canonicalHistory') and not row.get('nativeHistory'):
                if row.get('kind') == 'llm' and row.get('endedAt') and any(
                    node['kind'] == 'llm' and same_model_call(row, node) and
                    (node.get('sessionId') == row.get('sessionId') or node.get('sessionId') in aliases and row.get('sessionId') in aliases)
                    for node in nodes):
                    continue  # Ambiguous parallel IDs: show the canonical calls, never both sets.
                if row.get('liveObservation'):
                    nodes.append({key: value for key, value in row.items() if key not in
                                  {'input', 'output', 'error', 'inputDetail', 'outputDetail', 'errorDetail', '_eventFields'}})
                else:
                    # Do not erase older saved history when its native log is
                    # incomplete. This is pre-existing data, never a new capture.
                    nodes.append(copy.deepcopy(row))
        nodes.sort(key=lambda row: (row.get('startedAt') or 0, row.get('eventOrder', 0)))
        for turn in turns.values():
            members = [row for row in nodes if row.get('turnId') == turn['id']]
            starts = [row['startedAt'] for row in members if isinstance(row.get('startedAt'), (int, float))]
            ends = [row['endedAt'] for row in members if isinstance(row.get('endedAt'), (int, float))]
            if starts:turn['startedAt'] = min(starts)
            if any(row.get('phase') in LIVE_PHASES and not row.get('endedAt') for row in members):
                # A later observed call can continue this input after an earlier
                # projection saw only completed members. Discard that old end.
                turn['phase'] = 'running'
                turn.pop('endedAt', None)
            elif ends:
                turn.update(endedAt=max(ends), phase='completed')
        tree = {'nodes': nodes, 'turns': list(turns.values()), 'currentTurnId': live.get('currentTurnId'), 'source': 'events.jsonl',
                'retiredUsageNodes': accounting}
        refresh_usage(tree)
        return tree

    async def refresh(self, identity):
        async with self.lock:
            session = self.service._session(identity)
            inputs = self.projection_input(session)
            cached = self.projected.get(identity)
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
            # Compare detached inputs before copying or rebuilding the model
            # tree. External appends/replacements and live metadata still win.
            root = session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']
            same_root = paths and paths[0] == event_path(session, root)
            if cached and same_root and cached[0] == inputs and cached[1] == before:
                return
            previous = copy.deepcopy(inputs)
            tree = await asyncio.to_thread(self.read, previous)
            if self.service.closed:
                return
            async with self.service.lock:
                session = self.service._session(identity)
                if self.projection_input(session) != previous:
                    return  # A newer live update won; retry from it next tick.
                if tree is not None and session.get('execution') != tree:
                    session['execution'] = tree
                    self.service._publish()
                # Use the signatures actually read, not a later stat that may
                # already describe bytes appended after the projection.
                self.projected[identity] = (copy.deepcopy(self.projection_input(session)), self.read_revisions[identity])
                self.projected.move_to_end(identity)
                while len(self.projected) > 64:
                    expired, _ = self.projected.popitem(last=False)
                    self.read_paths.pop(expired, None)
                    self.read_revisions.pop(expired, None)
