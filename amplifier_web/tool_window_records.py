"""Attach exact saved tool fields; host lifecycle and ownership remain authoritative."""
from copy import deepcopy

FIELDS = ('input', 'output', 'error', 'delegateRequest', 'delegateResponse')


def records(page):
    calls = {}
    for row in page['items']:
        if row.get('kind') == 'tool' and row.get('toolCallId') and row.get('event') in {'tool:pre', 'tool:post', 'tool:error'}:
            calls.setdefault((row.get('sessionId'), row['toolCallId']), []).append(row)
    result = {}
    for key, rows in calls.items():
        starts = [row for row in rows if row['event'] == 'tool:pre']
        ends = [row for row in rows if row['event'] != 'tool:pre']
        # A cut window is not evidence of a unique invocation. An exact start
        # must be present, with at most one terminal record in source order.
        if len(starts) != 1 or len(ends) > 1:
            result[key] = {'contentAvailability': {'status': 'ambiguous' if len(starts) > 1 or len(ends) > 1 else 'not-loaded'}}
            continue
        start = starts[0]
        label = start.get('label')
        if (not label or label == 'Tool observation' or
                any(row.get('label') not in {label, 'Tool observation', None} or row['eventOrder'] <= start['eventOrder'] for row in ends)):
            result[key] = {'contentAvailability': {'status': 'ambiguous'}}
            continue
        selected = {'_toolLabel': label, '_eventFields': {}, 'contentAvailability': {'status': 'linked'}}
        for row, fields in [(start, ('input', 'delegateRequest')), *[(end, ('output', 'error', 'delegateResponse')) for end in ends]]:
            for field in fields:
                if field + 'Detail' not in row:
                    continue
                if field in row:
                    selected[field] = row[field]
                selected['_eventFields'][field] = deepcopy(row['_eventFields'][field])
                selected[field + 'Detail'] = {**deepcopy(row[field + 'Detail']), 'sessionId': page['sessionId'], 'windowKey': page['group']}
        if 'inputSummary' in start:
            selected['inputSummary'] = deepcopy(start['inputSummary'])
        if ends and 'outcomeSummary' in ends[0]:
            selected['outcomeSummary'] = deepcopy(ends[0]['outcomeSummary'])
        if not selected['_eventFields']:
            selected['contentAvailability'] = {'status': 'unrecorded' if ends else 'not-loaded'}
        result[key] = selected
    return result


def linked(page, session, node):
    identity = node.get('sessionId')
    if identity == session['id']:
        identity = page['source'].get('nativeIdentity') or page['source'].get('runtimeSessionId') or session['id']
    fields = page.get('toolRecords', {}).get((identity, node.get('toolCallId')))
    if fields is None:
        return None
    label = node.get('tool') or node.get('label')
    if fields.get('_toolLabel') not in {None, label}:
        return {'contentAvailability': {'status': 'ambiguous'}}
    return {key: deepcopy(value) for key, value in fields.items() if key != '_toolLabel'}


def attach(node, fields):
    if fields is None:
        return {**node, 'contentAvailability': {'status': 'linked' if any(field in node or node.get(field + 'Detail') for field in FIELDS) else 'not-loaded'}}
    # Never inherit payloads from a previous attempt when the exact current
    # saved records are incomplete or ambiguous. This changes presentation only.
    result = {key: value for key, value in node.items() if key not in {'_eventFields', 'inputSummary', 'outcomeSummary'} and key not in FIELDS and key not in {field + 'Detail' for field in FIELDS}}
    result.update(fields)
    return result
