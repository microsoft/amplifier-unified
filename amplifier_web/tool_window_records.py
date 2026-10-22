# Presentation-only links to exact saved tool observations.
from copy import deepcopy

FIELDS = ('input', 'output', 'error', 'delegateRequest', 'delegateResponse')
EVENTS = {'tool:pre', 'tool:post', 'tool:error'}


def records(page):
    calls = {}
    for row in page['items']:
        if row.get('kind') == 'tool' and row.get('toolCallId') and row.get('event') in EVENTS:
            calls.setdefault((row.get('sessionId'), row['toolCallId']), []).append(row)
    return calls


def _fields(page, row):
    result = {'_eventFields': {}}
    for field in FIELDS:
        if field + 'Detail' in row and field in row.get('_eventFields', {}):
            if field in row:
                result[field] = deepcopy(row[field])
            result['_eventFields'][field] = deepcopy(row['_eventFields'][field])
            result[field + 'Detail'] = {**deepcopy(row[field + 'Detail']),
                'sessionId': page['sessionId'], 'windowKey': page['group']}
    for key in ('inputSummary', 'outcomeSummary', 'inputPreview', 'actionPath'):
        if key in row:
            result[key] = deepcopy(row[key])
    return result


def _inside_interval(row, node):
    # Timing only excludes clearly unrelated records. It never establishes
    # identity: old records without a host observation ID stay unverified.
    at = row.get('createdAt')
    if type(at) not in (int, float):
        return True
    start, end = node.get('startedAt'), node.get('endedAt')
    return not (type(start) in (int, float) and at < start
        or type(end) in (int, float) and at > end)


def linked(page, session, node):
    identity = node.get('sessionId')
    if identity == session['id']:
        identity = page['source'].get('nativeIdentity') or page['source'].get('runtimeSessionId') or session['id']
    rows = page.get('toolRecords', {}).get((identity, node.get('toolCallId')), [])
    token = node.get('toolObservationId')
    label = node.get('tool') or node.get('label')
    if token:
        exact = [row for row in rows if row.get('_hostToolId') == token]
        if not exact:
            return None
        if len(exact) != 1 or exact[0].get('label') != label:
            return {'contentAvailability': {'status': 'ambiguous'}}
        row = exact[0]
        fields = _fields(page, row)
        fields['contentAvailability'] = {'status': ('partial' if row['event'] == 'tool:pre'
            else 'linked') if fields['_eventFields'] else 'unrecorded'}
        return fields
    candidates = [row for row in rows if row.get('label') == label and _inside_interval(row, node)]
    if not candidates:
        return None
    observations = []
    for row in candidates:
        fields = _fields(page, row)
        observations.append({**{key: deepcopy(row[key]) for key in
            ('id', 'kind', 'event', 'label', 'toolCallId', 'sessionId', 'createdAt') if key in row},
            **{key: value for key, value in fields.items() if not key.startswith('_')},
            'phase': 'recorded', 'association': 'unverified', 'observation': True})
    # Never graft a guessed pre/post pair onto a host attempt. The user can
    # inspect these independently, including duplicate/retried call IDs.
    return {'contentAvailability': {'status': 'unverified'}, 'savedObservations': observations}


def attach(node, fields):
    if fields is None:
        return {**node, 'contentAvailability': {'status': 'linked' if any(field in node or node.get(field + 'Detail') for field in FIELDS) else 'not-loaded'}}
    result = {key: value for key, value in node.items() if key not in
        {'_eventFields', 'savedObservations'} and key not in FIELDS
        and key not in {field + 'Detail' for field in FIELDS}}
    # Bounded producer hints belong to the host row itself. Canonical hints can
    # replace them only through an exact observation binding.
    result.update(fields)
    return result
