"""Local historical ranges for comparable human turns, never task deadlines."""
import math


def shape(session, identity, text):
    message = next((row for row in reversed(session.get('messages', []))
                    if row.get('inputId') == identity), None)
    selection = session.get('selection') or {}
    if (not message or message.get('role') != 'user'
            or message.get('inputOrigin') not in {'ui', 'user'}
            or message.get('questionId') or message.get('scheduledRunId')
            or message.get('steering') or not text
            or not selection.get('instance') or not selection.get('model')):
        return None
    return {'version': 1, 'instance': selection['instance'], 'model': selection['model'],
            'effort': selection.get('effort'), 'bundle': session.get('bundle'),
            'workspace': session.get('workspace'),
            'inputSize': int(math.log2(max(64, len(text)))),
            'attachments': sorted(str(row.get('mimeType', row.get('type', 'unknown')))
                                  for row in message.get('attachments', []))}


def estimate(turns, current_shape):
    if current_shape is None:
        return None
    samples = []
    # No transcript IO or provider calls; bound work independently of history size.
    for row in reversed(turns[-80:]):
        if row.get('durationShape') != current_shape or row.get('phase') != 'completed':
            continue
        start, end = row.get('startedAt'), row.get('endedAt')
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (start, end)):
            continue
        seconds = end - start
        if 1 <= seconds <= 7200:
            samples.append(seconds)
        if len(samples) == 20:
            break
    if len(samples) < 5:
        return None
    samples.sort()
    low, high = samples[int((len(samples)-1)*.2)], samples[math.ceil((len(samples)-1)*.8)]
    if high > max(low, 1) * 6:
        return None  # The observed history is too variable to offer a useful range.
    return {'lowerSeconds': max(5, math.floor(low*.75/5)*5),
            'upperSeconds': max(10, math.ceil(high*1.25/5)*5), 'sampleCount': len(samples),
            'basis': 'recent_similar_turns', 'scope': 'turn', 'version': 1}
