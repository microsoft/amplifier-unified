"""Bounded, ephemeral display policy over Foundation's native CI reader.

No payload bodies or inferred model reasoning are exposed. Exact associations
come from Foundation; incomplete and unassociated observations stay explicit.
"""
from datetime import datetime
from itertools import islice

from amplifier_foundation.session.history import associate_events

MAX_SCAN_EVENTS = 5000
MAX_SCAN_BYTES = 16 * 1024 * 1024
MAX_ACTIVITY_EVENTS = 2000
_FIELDS = {'tool_call_id', 'call_id', 'message_id', 'tool_name', 'tool', 'name',
           'purpose', 'origin_module', 'prompt', 'provider', 'model', 'usage'}


def _time(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return None


def activity_page(reader, messages, visible):
    """Scan only on page opening; bound rows and discard raw API payloads."""
    events = []
    scanned = 0
    iterator = reader.iter_events(max_lines=MAX_SCAN_EVENTS, max_bytes=MAX_SCAN_BYTES)
    try:
        for event in islice(iterator, MAX_SCAN_EVENTS + 1):
            scanned += 1
            if scanned > MAX_SCAN_EVENTS:
                break
            if event['event'] not in {'prompt:submit', 'tool:pre', 'tool:post', 'tool:error',
                                      'llm:request', 'llm:response', 'llm:error'}:
                continue
            if len(events) >= MAX_ACTIVITY_EVENTS:
                break
            data = event.get('data') or {}
            events.append({**{key: event.get(key) for key in ('event', 'session_id', 'timestamp', 'line')},
                           'data': {key: value for key, value in data.items() if key in _FIELDS}})
    finally:
        iterator.close()
    diagnostics = [dict(code=d.code, source=d.source, line=d.line, severity=d.severity) for d in reader.diagnostics]
    if scanned > MAX_SCAN_EVENTS or len(events) >= MAX_ACTIVITY_EVENTS:
        diagnostics.append({'code': 'activity_scan_limit', 'source': 'events', 'severity': 'info', 'line': None})
    visible_by_index = {row['nativeIndex']: row for row in visible}
    nodes, turns = {}, {}
    unassociated = auxiliary = 0
    for association in associate_events(messages, events):
        event = events[association.event_index]
        if association.auxiliary:
            auxiliary += 1
            continue
        if association.turn_index is None:
            unassociated += 1
            continue
        anchor = visible_by_index.get(association.turn_message_index)
        if anchor is None or event['event'] == 'prompt:submit':
            continue
        data = event['data']
        # Only tool lifecycle IDs are complete enough to combine observations.
        # LLM rows without a stable call association remain observations instead
        # of inventing calls, duration, usage or a running state from old logs.
        call = data.get('tool_call_id') or data.get('call_id')
        if not event['event'].startswith('tool:') or not isinstance(call, str):
            unassociated += 1
            continue
        turn_id = 'native-turn:' + anchor['id']
        turns.setdefault(turn_id, {'id': turn_id, 'inputId': turn_id, 'anchorMessageId': anchor['id'],
                                  'phase': 'completed', 'nativeHistory': True, 'nativeIndex': anchor['nativeIndex'], 'label': 'Saved activity'})
        identity = 'native-tool:' + anchor['id'] + ':' + call
        node = nodes.setdefault(identity, {'id': identity, 'turnId': turn_id, 'kind': 'tool',
            'sessionId': reader.session_id, 'nativeHistory': True, 'toolCallId': call,
            'label': str(data.get('tool_name') or data.get('tool') or data.get('name') or 'Tool')[:160]})
        at = _time(event.get('timestamp'))
        if event['event'] == 'tool:pre':
            node.setdefault('phase', 'recorded')
            if at is not None:
                node.setdefault('startedAt', at)
        else:
            node['phase'] = 'error' if event['event'] == 'tool:error' else 'completed'
            if at is not None:
                node['endedAt'] = at
    return {'nodes': list(nodes.values()), 'turns': list(turns.values()), 'diagnostics': diagnostics,
            'unassociatedEvents': unassociated, 'auxiliaryEvents': auxiliary, 'scannedEvents': min(scanned, MAX_SCAN_EVENTS)}


def apply_activity(session, activity, *, append=False):
    from .execution import ingest, rollup
    tree = session.setdefault('execution', {'nodes': [], 'turns': [], 'currentTurnId': None})
    if not append:
        for key in ('nodes', 'turns'):
            tree[key] = [row for row in tree[key] if not row.get('nativeHistory')]
    # Existing live/web work is richer. Do not show a second activity group for
    # the same anchored turn, or count the same tool calls twice.
    actual_ids = {row.get('nativeIndex'): row['id'] for row in session.get('messages', []) if 'nativeIndex' in row}
    activity = {**activity, 'turns': [{**row, 'anchorMessageId': actual_ids.get(row.get('nativeIndex'), row['anchorMessageId'])} for row in activity['turns']]}
    web_anchors = {row.get('anchorMessageId') for row in tree['turns'] if not row.get('nativeHistory')}
    allowed = {row['id'] for row in activity['turns'] if row['anchorMessageId'] not in web_anchors}
    for turn in activity['turns']:
        if turn['id'] in allowed and not any(row['id'] == turn['id'] for row in tree['turns']):
            tree['turns'].append({**turn, 'aggregateUsage': rollup([])})
    for node in activity['nodes']:
        if node['turnId'] in allowed:
            ingest(session, node)
            next(row for row in tree['nodes'] if row['id'] == node['id'])['nativeHistory'] = True
    tree['aggregateUsage'] = rollup([row for row in tree['nodes'] if row.get('kind') == 'llm'])
    session['historyActivity'] = {key: value for key, value in activity.items() if key not in {'nodes', 'turns'}}
