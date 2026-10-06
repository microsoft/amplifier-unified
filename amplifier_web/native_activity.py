"""Bounded, ephemeral display policy over Foundation's native CI reader.

No payload bodies or inferred model reasoning are exposed. Exact associations
come from Foundation; incomplete and unassociated observations stay explicit.
"""
from collections import Counter
from datetime import datetime
from itertools import islice
import uuid

from amplifier_foundation.session.history import associate_events
from amplifier_foundation.session.messages import is_real_user_message

from .voice_messages import is_internal_voice_input

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


def _tool_result(message):
    content = message.get('content')
    return bool(message.get('tool_call_id')) or (isinstance(content, list) and any(
        isinstance(block, dict) and block.get('type') in {'tool_result', 'function_call_output'}
        for block in content))


def _voice_turns(reader, messages, projection, page_start, page_end, tail, diagnostics):
    """Place exact hidden-input turns from the full public projection, not time."""
    boundaries = [index for index, row in enumerate(messages)
                  if is_real_user_message(row)
                  and not (row.get('metadata') if isinstance(row.get('metadata'), dict) else {}).get('ephemeral')
                  and not _tool_result(row)]
    inputs = {index: messages[index]['metadata']['amplifier_input']['id']
              for index in boundaries if is_internal_voice_input(messages[index])}
    counts = Counter(inputs.values())
    turns = {}
    for number, index in enumerate(boundaries):
        input_id = inputs.get(index)
        if input_id is None:
            continue
        if counts[input_id] != 1:
            diagnostics.append({'code': 'ambiguous_voice_input', 'source': 'transcript',
                                'severity': 'info', 'line': index + 1})
            continue
        end = boundaries[number + 1] if number + 1 < len(boundaries) else len(messages)
        anchor = next((row for row in projection if index < row['nativeIndex'] < end
                       and not _tool_result(messages[row['nativeIndex']])), None)
        # A gap-only turn is owned by the next public position (or the tail).
        # Its anchor stays null; it never pretends that next turn is its input.
        owner = anchor or next((row for row in projection if row['nativeIndex'] >= index), None)
        position = owner['position'] if owner else len(projection)
        if not (page_start <= position < page_end or (tail and position == len(projection))):
            continue
        turn_id = 'native-voice-turn:' + uuid.uuid5(
            uuid.NAMESPACE_URL, f'{reader.session_id}:voice-input:{input_id}').hex
        turns[index] = {'id': turn_id, 'inputId': input_id,
                        'anchorMessageId': anchor['id'] if anchor else None,
                        'phase': 'completed', 'nativeHistory': True,
                        'nativeIndex': anchor['nativeIndex'] if anchor else None,
                        'label': 'Saved voice activity'}
    return turns


def activity_page(reader, messages, visible, *, projection=None, page_start=0,
                  page_end=None, tail=True):
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
    projection = projection if projection is not None else [
        {key: row[key] for key in ('id', 'nativeIndex')} | {'position': number}
        for number, row in enumerate(visible)]
    voice_turns = _voice_turns(reader, messages, projection, page_start,
                              len(projection) if page_end is None else page_end, tail, diagnostics)
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
        voice_turn = voice_turns.get(association.turn_message_index)
        if (anchor is None and voice_turn is None) or event['event'] == 'prompt:submit':
            continue
        data = event['data']
        # Only tool lifecycle IDs are complete enough to combine observations.
        # LLM rows without a stable call association remain observations instead
        # of inventing calls, duration, usage or a running state from old logs.
        call = data.get('tool_call_id') or data.get('call_id')
        if not event['event'].startswith('tool:') or not isinstance(call, str):
            unassociated += 1
            continue
        if voice_turn is not None and association.method not in {'tool_call_id', 'message_id'}:
            unassociated += 1
            continue
        turn_id = voice_turn['id'] if voice_turn is not None else 'native-turn:' + anchor['id']
        turns.setdefault(turn_id, voice_turn if voice_turn is not None else {
                                  'id': turn_id, 'inputId': turn_id, 'anchorMessageId': anchor['id'],
                                  'phase': 'completed', 'nativeHistory': True, 'nativeIndex': anchor['nativeIndex'], 'label': 'Saved activity'})
        identity = 'native-tool:' + turn_id + ':' + call
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
    actual_ids = {row['nativeIndex']: row['id'] for row in session.get('messages', [])
                  if type(row.get('nativeIndex')) is int}
    activity = {**activity, 'turns': [{**row, 'anchorMessageId': actual_ids.get(row.get('nativeIndex'), row['anchorMessageId'])} for row in activity['turns']]}
    web_turns = [row for row in tree['turns'] if not row.get('nativeHistory')]
    reconciled = {}
    ambiguous = set()
    for turn in activity['turns']:
        if not turn['id'].startswith('native-voice-turn:'):
            continue
        matches = [row for row in web_turns if row.get('inputId') == turn['inputId']]
        if len(matches) == 1:
            # Exact accepted input owns work independently of its placement.
            # A live turn precedes its answer; keep that immutable live anchor.
            reconciled[turn['id']] = matches[0]
        elif len(matches) > 1:
            ambiguous.add(turn['id'])
            activity['diagnostics'].append({'code': 'ambiguous_live_voice_input',
                'source': 'execution', 'severity': 'info', 'line': None})
    def already_live(turn):
        if turn['id'].startswith('native-voice-turn:'):
            return turn['id'] in reconciled or turn['id'] in ambiguous
        return any(row.get('anchorMessageId') == turn['anchorMessageId'] for row in web_turns)
    allowed = {row['id'] for row in activity['turns'] if not already_live(row)}
    for turn in activity['turns']:
        if turn['id'] in allowed and not any(row['id'] == turn['id'] for row in tree['turns']):
            tree['turns'].append({**turn, 'aggregateUsage': rollup([])})
    for node in activity['nodes']:
        target = reconciled.get(node['turnId'])
        if target is not None and any(row.get('kind') == 'tool'
                and row.get('turnId') == target['id']
                and row.get('toolCallId') == node['toolCallId']
                and not row.get('nativeHistory') for row in tree['nodes']):
            continue
        if node['turnId'] in allowed or target is not None:
            incoming = {**node, 'turnId': target['id']} if target is not None else node
            ingest(session, incoming)
            saved = next(row for row in tree['nodes'] if row['id'] == node['id'])
            saved['nativeHistory'] = True
            saved['anchorMessageId'] = (target.get('anchorMessageId') if target is not None else next(
                turn['anchorMessageId'] for turn in activity['turns'] if turn['id'] == node['turnId']))
    tree['aggregateUsage'] = rollup([row for row in tree['nodes'] if row.get('kind') == 'llm'])
    session['historyActivity'] = {key: value for key, value in activity.items() if key not in {'nodes', 'turns'}}
