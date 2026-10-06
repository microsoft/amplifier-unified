"""Host-owned voice input identity; never infer ownership from prompt text."""
from __future__ import annotations

import hashlib
import json
import re

_PART = re.compile(r'[A-Za-z0-9_-]{1,96}', re.ASCII)
_MARKER = 'unified.voice.v1:'


def voice_input_id(call_id, delegation_id):
    if not all(isinstance(value, str) and _PART.fullmatch(value)
               for value in (call_id, delegation_id)):
        raise ValueError('Voice call and delegation identities must be bounded identifiers.')
    return f'voice:{call_id}:{delegation_id}'


def voice_provenance(input_id):
    if not isinstance(input_id, str):
        return None
    parts = input_id.split(':')
    if len(parts) != 3 or parts[0] != 'voice':
        return None
    try:
        if voice_input_id(parts[1], parts[2]) != input_id:
            return None
    except ValueError:
        return None
    return {'version': 1, 'kind': 'user', 'id': input_id, 'source': 'user',
            'call_id': _MARKER + input_id}


def validated_voice_provenance(value, input_id):
    expected = voice_provenance(input_id)
    if (expected is None or not isinstance(value, dict)
            or type(value.get('version')) is not int or value['version'] != 1
            or any(value.get(key) != item for key, item in expected.items())):
        return None
    return expected


def is_internal_voice_input(row):
    """Only canonical user metadata with the supported exact host marker hides."""
    if not isinstance(row, dict) or row.get('role') != 'user':
        return False
    metadata = row.get('metadata')
    value = metadata.get('amplifier_input') if isinstance(metadata, dict) else None
    return (isinstance(value, dict)
            and validated_voice_provenance(value, value.get('id')) is not None)


def voice_fingerprint(session_id, text):
    return hashlib.sha256(json.dumps(['voice_delegate', session_id, text]).encode()).hexdigest()


def presentation_proof(session_id, text, input_id):
    provenance = voice_provenance(input_id)
    if provenance is None:
        raise ValueError('Invalid voice input identity.')
    return {'version': 1, 'sessionId': session_id, 'inputId': input_id,
            'fingerprint': voice_fingerprint(session_id, text),
            'call_id': provenance['call_id']}


class _VoiceSnapshot(dict):
    """In-process capability: JSON/client snapshot fields cannot impersonate it."""
    def __init__(self, session, proof):
        super().__init__(session)
        self._voice_proof = dict(proof)


def private_voice_snapshot(session, text, input_id, fingerprint, receipt):
    """Bind a private snapshot only to an exact durable host presentation proof."""
    expected = presentation_proof(session['id'], text, input_id) if voice_provenance(input_id) else None
    proof = receipt.get('voicePresentation') if isinstance(receipt, dict) else None
    if (expected is None or not isinstance(proof, dict)
            or type(proof.get('version')) is not int or proof != expected
            or fingerprint != expected['fingerprint']
            or receipt.get('inputId') != input_id or receipt.get('sessionId') != session['id']):
        return dict(session)
    return _VoiceSnapshot(session, proof)


def private_voice_provenance(session, text, input_id):
    if not isinstance(session, _VoiceSnapshot):
        return None
    expected = presentation_proof(session['id'], text, input_id) if voice_provenance(input_id) else None
    if expected is None or session._voice_proof != expected:
        return None
    return voice_provenance(input_id)