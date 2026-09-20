"""Disposable indexes over native event logs. Payloads stay in events.jsonl.

The authenticated conversation API reads the user's recorded tool fields as-is.
This module never writes a capture, changes capture policy, or executes work.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import copy
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path

from .execution import LIVE_PHASES, refresh_usage
from .execution_events import public_usage
from .session_files import amplifier_home, project_slug

logger = logging.getLogger(__name__)


def same_model_call(observed, native):
    """Identify duplicate telemetry, allowing admission time before dispatch."""
    if observed.get('model') != native.get('model'):
        return False
    if not all(isinstance(row.get(key), (int, float)) for row in (observed, native) for key in ('startedAt', 'endedAt')):
        return False
    return (observed['startedAt'] <= native['startedAt'] + 1
            and abs(observed['endedAt'] - native['endedAt']) < 1
            and all((observed.get('usage') or {}).get(key) is None or (native.get('usage') or {}).get(key) is None or
                    observed['usage'][key] == native['usage'][key]
                    for key in ('inputTokens', 'outputTokens')))


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
        revision = (identity, stat.st_size, stat.st_mtime_ns)
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

    def field(self, node, field, data, keys, reference):
        key = next((key for key in keys if key in data), None)
        if key is None:
            return
        value = text(data[key])
        node[field] = value[:512]
        node.setdefault('_eventFields', {})[field] = {**reference, 'key': key}
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

    def associations(self, directory):
        """Use Foundation's exact transcript associations for undated history."""
        from amplifier_foundation.session.history import SessionHistoryStore, associate_events
        stamps = []
        for name in ('transcript.jsonl', 'transcript.jsonl.backup'):
            try:
                stat = (directory / name).stat()
                stamps.append((stat.st_ino, stat.st_mtime_ns, stat.st_size))
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

    def rows(self):
        rows = list(self.nodes.values())
        app = [row for row in rows if row.get('_appModel')]
        matched = set()
        def duplicate(row):
            if row['kind'] != 'llm' or row.get('_appModel'):
                return False
            matches = [other for other in app if other['id'] not in matched and
                       other.get('sessionId') == row.get('sessionId') and same_model_call(other, row)]
            if matches:
                closest = min(matches, key=lambda other: abs(other['endedAt'] - row['endedAt']))
                matched.add(closest['id'])
                return True
            return False
        return [copy.deepcopy(row) for row in rows if not duplicate(row)]


class EventLogView:
    def __init__(self, service):
        self.service = service
        self.indexes = OrderedDict()
        self.task = None
        self.lock = asyncio.Lock()

    def start(self):
        self.task = asyncio.create_task(self.loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def loop(self):
        while not self.service.closed:
            selected = {self.service.state.get('selectedSessionId')}
            selected.update(row.get('selectedSessionId') for row in self.service.clients.records.values())
            sessions = [row['id'] for row in self.service.state['sessions'] if row['id'] in selected
                        or row.get('status') in {'working', 'starting', 'stopping'}]
            for identity in sessions:
                try:
                    await self.refresh(identity)
                except (OSError, ValueError):
                    pass  # Keep the last readable view while a log is replaced.
                except Exception:
                    logger.exception('Could not refresh the event-log view for %s', identity)
            await asyncio.sleep(1)

    def read(self, session):
        root = session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']
        queue = [root]
        queue.extend(row.get('sessionId') or row.get('id') for row in session.get('workers', []))
        seen, indexes, nodes, workers = set(), [], [], {}
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
            if not index.refresh():
                continue
            indexes.append(index)
            nodes.extend(index.rows())
            workers.update(index.children)
            queue.extend(index.children)
        while len(self.indexes) > max(64, len(seen)):
            self.indexes.popitem(last=False)
        if not indexes:
            return None
        live = session.get('execution', {})
        live_nodes = live.get('nodes', [])
        live_by_id = {row['id']: row for row in live_nodes}
        live_by_source = {row.get('_canonicalId', row['id']): row for row in live_nodes}
        aliases = {root, session['id']}
        def call_key(row):
            sid = row.get('sessionId')
            return (root if sid in aliases else sid, row.get('toolCallId'))
        tool_ids = {call_key(row): row for row in live_nodes if row.get('kind') == 'tool'}
        turns = {row['id']: copy.deepcopy(row) for row in live.get('turns', [])}
        messages = session.get('messages', [])
        users = [row for row in messages if row.get('role') == 'user']
        from .automatic_history import directory
        root_index = next((index for index in indexes if index.identity == root), None)
        source = {**session, 'nativeProject': session.get('nativeProject') or project_slug(session['workspace'])}
        associations = root_index.associations(directory(source)) if root_index else {}
        native_messages = [row for row in messages if type(row.get('nativeIndex')) is int]
        remap = {}
        for node in nodes:
            node['_canonicalId'] = node['id']
            previous = tool_ids.get(call_key(node)) if node['kind'] == 'tool' else live_by_source.get(node['id']) or live_by_id.get(node['id'])
            if previous is None and node['kind'] == 'llm':
                candidates = [row for row in live_nodes if row.get('kind') == 'llm' and not row.get('canonicalHistory')
                    and (row.get('sessionId') == node.get('sessionId') or row.get('sessionId') in aliases and node.get('sessionId') in aliases)
                    and same_model_call(row, node)]
                if len(candidates) == 1:
                    previous = candidates[0]
            if previous:
                old = node['id'];node['id'] = previous['id'];remap[old] = node['id']
                for key in ('turnId', 'parentId', 'rootSessionId', 'liveObservation', 'lifecycle'):
                    if previous.get(key):node[key] = previous[key]
                if node['kind'] == 'llm' and previous.get('usage'):
                    recorded = node.get('usage') or {}
                    node['usage'] = {**previous['usage'], **recorded}
                    if recorded.get('costUsd') is None and previous['usage'].get('costUsd') is not None:
                        node['usage']['costType'] = previous['usage'].get('costType', 'reported')
                if node.get('phase') in LIVE_PHASES and isinstance(previous.get('endedAt'), (int, float)) and previous['endedAt'] >= (node.get('startedAt') or 0):
                    node.update(phase=previous.get('phase', 'recorded'), endedAt=previous['endedAt'])
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
                key = 'native-turn:' + (anchor['id'] if anchor else str(association['turn']))
                node['turnId'] = key
                turns.setdefault(key, {'id': key, 'anchorMessageId': anchor['id'] if anchor else None,
                                      'canonicalHistory': True, 'phase': 'completed'})
                if association['position'] is not None:
                    preceding = [row for row in native_messages if association['turn'] <= row['nativeIndex'] <= association['position']]
                    node['anchorMessageId'] = preceding[-1]['id'] if preceding else None
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
            for field in ('input', 'output', 'error'):
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
            if ends and not any(row.get('phase') in {'running', 'working', 'retrying'} and not row.get('endedAt') for row in members):
                turn.update(endedAt=max(ends), phase='completed')
        tree = {'nodes': nodes, 'turns': list(turns.values()), 'currentTurnId': live.get('currentTurnId'), 'source': 'events.jsonl'}
        refresh_usage(tree)
        return tree

    async def refresh(self, identity):
        async with self.lock:
            session = self.service._session(identity)
            previous = copy.deepcopy(session)
            tree = await asyncio.to_thread(self.read, previous)
            if tree is None or self.service.closed:
                return
            async with self.service.lock:
                session = self.service._session(identity)
                if session.get('execution') != previous.get('execution') or session.get('messages') != previous.get('messages'):
                    return  # A newer live update won; retry from it next tick.
                if session.get('execution') != tree:
                    session['execution'] = tree
                    self.service._publish()
