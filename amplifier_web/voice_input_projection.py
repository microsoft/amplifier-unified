"""Public presentation of host-authored voice delegation inputs.

Canonical input text and IDs stay unchanged. Text alone is never voice provenance.
"""
import json

PREFIX = 'This is a user message arriving through the voice interface of this same Amplifier conversation. '
START = '\n<voice_reference>\n'
END = '\n</voice_reference>\nCurrent spoken user request:\n'


def decode(text, input_id):
    if not isinstance(input_id, str) or len(input_id) > 200:
        return None
    parts = input_id.split(':', 2)
    if len(parts) != 3 or parts[0] != 'voice' or not all(parts[1:]):
        return None
    if not isinstance(text, str) or not text.startswith(PREFIX):
        return None
    try:
        _, body = text.split(START, 1)
        reference, request = body.split(END, 1)
        history = json.loads(reference)
    except (ValueError, TypeError):
        return None
    if (not request or not isinstance(history, list) or any(
            not isinstance(row, dict) or row.get('role') not in {'user', 'assistant'}
            or not isinstance(row.get('text'), str) for row in history)):
        return None
    return {'callId': parts[1], 'text': request, 'history': history}


def public_input(text, provenance):
    if (not isinstance(provenance, dict) or provenance.get('version') != 1
            or provenance.get('kind') != 'user' or provenance.get('source') != 'user'):
        return None
    return decode(text, provenance.get('id'))


def align(session, native):
    """Bind only an explicitly identified retained utterance from the same call.

    Repeated speech or multiple delegations remain distinct: never guess which
    utterance owns a native boundary. Exact already-bound identities survive.
    """
    from .automatic_history import display_identity
    current = session.get('messages', [])
    by_id = {row['id']: row for row in native if row.get('voiceDelegation')}
    candidates, utterances = {}, {}
    for row in native:
        if row.get('voiceDelegation') and not row.get('nativeInputAmbiguous'):
            candidates.setdefault(row['nativeInputId'], []).append(row)
    for row in current:
        if row.get('role') == 'user' and row.get('voiceInputId') and row.get('source') != 'native':
            utterances.setdefault(row['voiceInputId'], []).append(row)
    bindings = {}
    for key, rows in candidates.items():
        spoken = utterances.get(key, [])
        if len(rows) != 1 or len(spoken) != 1:
            continue
        saved, original = rows[0], spoken[0]
        if (original.get('voiceId') != saved['voiceCallId'] or original.get('text') != saved['text']
                or original.get('nativeMessageId') not in (None, saved['id'])
                or original.get('nativeIndex') not in (None, saved['nativeIndex'])):
            continue
        bindings[saved['id']] = {**original, 'nativeMessageId': saved['id'],
                                'nativeIndex': saved['nativeIndex']}
    by_visible_id = {row['id']: row for row in bindings.values()}
    result = []
    removed_copies = set()
    for row in current:
        saved = by_id.get(row.get('nativeMessageId') or row.get('id'))
        if saved and row.get('source') == 'native' and row.get('nativeIndex') == saved['nativeIndex']:
            # Upgrade only the exact old raw display copy, or an already public
            # copy. A similar user message never qualifies.
            exact = display_identity(session, saved['nativeIndex'], row.get('role'), row.get('text', '')) == saved['id']
            if exact or row.get('voiceDelegation') and row.get('text') == saved['text']:
                if saved['id'] not in bindings:
                    result.append(saved)
                else:
                    removed_copies.add(saved['id'])
                continue
        bound = by_visible_id.get(row.get('id'))
        # A new binding must pass through ordered merge so intervening native
        # replies are inserted. Only replace an index that already existed.
        result.append({**row, 'nativeMessageId': bound['nativeMessageId']} if bound else row)
    for row in result:
        if row.get('nativeMessageId') in removed_copies:
            row['nativeIndex'] = bindings[row['nativeMessageId']]['nativeIndex']
    return result, [bindings.get(row['id'], row) for row in native]
