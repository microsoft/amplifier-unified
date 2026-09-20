"""Read positive delivery evidence without treating missing history as non-delivery."""
import json


def find_message(session, input_id):
    return next((m for m in session.get('messages', [])
                 if m.get('role') == 'user' and m.get('inputId') == input_id), None)


def contains_input(messages, input_id):
    for message in messages:
        if not isinstance(message, dict) or message.get('role') != 'user':
            continue
        metadata = message.get('metadata')
        marker = metadata.get('amplifier_input') if isinstance(metadata, dict) else None
        if isinstance(marker, dict) and marker.get('id') == input_id:
            return True
    return False


def saved_delivery(session, input_id):
    from .session_files import sessions_dir, validate_id
    identity = session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']
    validate_id(identity)
    path = sessions_dir(session['workspace']) / identity / 'transcript.jsonl'
    try:
        with path.open() as stream:
            for line in stream:
                if contains_input([json.loads(line)], input_id):
                    return 'accepted'
    except (OSError, ValueError, TypeError):
        pass
    # Absence is not proof: an old worker may have acted before checkpointing.
    return 'unknown'
