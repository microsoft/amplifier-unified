"""Native record-to-display projection shared by history and worker checkpoints.

This module does not read transcripts, catalogs or host configuration. Raw native
anchors, public visibility and provenance retain their existing meanings.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json

from .shared_state_probe import text_content


def message_time(row):
    value = (row.get('metadata') or {}).get('timestamp')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None


def display_identity(session, index, role, text):
    key = json.dumps([session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id'], index, role, text], ensure_ascii=False)
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _public_row(row):
    return (isinstance(row, dict) and row.get('role') in {'user', 'assistant'}
            and not any((row.get('metadata') or {}).get(key) for key in
                        ('ephemeral', 'amplifier_visible_reference', 'amplifier_recovery_reference')))


def _private_block(block):
    return isinstance(block, dict) and block.get('visibility') in ('internal', 'developer')


def _public_blocks(row, *, text_only=False):
    """Select ordered native public blocks, without copying or resolving media.

    Returned blocks are read-only references to the selected raw record. This
    does not reconcile retained originals, assign message IDs or qualify media
    encodings. Unknown public kinds are diagnostics, never public payloads.
    """
    result = {'blocks': [], 'unsupported': []}
    if not _public_row(row):
        return result
    content = row.get('content')
    if isinstance(content, str):
        result['blocks'].append({'type': 'text', 'text': content})
        return result
    if not isinstance(content, list):
        result['unsupported'].append({'index': 0, 'type': None})
        return result
    for index, block in enumerate(content):
        if _private_block(block):
            continue
        kind = block.get('type') if isinstance(block, dict) else None
        if kind in ('text', 'output_text') and isinstance(block.get('text'), str):
            result['blocks'].append(block)
        elif text_only or kind in ('thinking', 'redacted_thinking', 'reasoning',
                                   'tool_call', 'tool_use', 'tool_result'):
            continue
        elif kind in ('image', 'image_url'):
            result['blocks'].append(block)
        else:
            result['unsupported'].append({'index': index, 'type': kind if isinstance(kind, str) else None})
    return result


def _public_text(row, raw_text):
    content = row.get('content')
    if not isinstance(content, list) or not any(_private_block(block) for block in content):
        return raw_text
    return text_content({'content': _public_blocks(row, text_only=True)['blocks']})


def native_input_id(row):
    """Validated input provenance, independent of whether its body is visible."""
    if not isinstance(row, dict) or row.get('role') != 'user':
        return None
    metadata = row.get('metadata')
    provenance = metadata.get('amplifier_input') if isinstance(metadata, dict) else None
    # Steering keeps the canonical input ID. Media-only and empty occurrences
    # also participate in uniqueness; display text never proves provenance.
    from .input_status import native_input_id as marker_input_id
    return marker_input_id(row['role'], provenance)


def display_message(row, index, session, *, include_internal=False):
    if not isinstance(row, dict) or row.get('role') not in {'user', 'assistant'}:
        return None
    if not include_internal and not _public_row(row):
        return None
    raw_text = text_content(row)
    text = raw_text if include_internal else _public_text(row, raw_text)
    if not text:
        return None
    metadata=row.get('metadata') or {}
    provenance=metadata.get('amplifier_input',{})
    recovery=metadata.get('live_recovery_job')
    if 'amplifier_input' not in metadata and isinstance(recovery,str) and 0<len(recovery)<=128:
        # Earlier loop-live versions recorded recovery ownership separately.
        # Read that host metadata; matching text alone is never provenance.
        provenance={'version':1,'kind':'service','id':recovery,'source':'local-job-recovery'}
    observation={}
    input_id = native_input_id(row)
    input_identity = {'nativeInputId': input_id} if input_id is not None else {}
    if isinstance(provenance,dict) and provenance.get('version')==1 and provenance.get('kind')=='service' and all(isinstance(provenance.get(key),str) and 0<len(provenance[key])<=128 for key in ('id','source')):
        observation={'observation':{key:provenance[key] for key in ('id','source','call_id') if key in provenance}}
        from .recovery_observations import recovery_facts
        facts = recovery_facts(metadata, text, provenance)
        if facts:
            observation['observation']['recovery'] = facts
    # Public block visibility changes the view, never its canonical raw anchor.
    identity = display_identity(session, index, row['role'], raw_text)
    voice = None
    if row['role'] == 'user' and not include_internal:
        from .voice_input_projection import public_input
        voice = public_input(text, provenance)
    voice_fields = ({'voiceDelegation': True, 'voiceCallId': voice['callId'],
                     'voiceInputId': provenance['id'],
                     'nativeMessageId': identity, 'inputOrigin': 'voice'} if voice else {})
    return {'id': identity, 'role': row['role'],
            'text': voice['text'] if voice else text, 'via': 'call' if voice else 'chat', 'source': 'native', 'nativeIndex': index,
            'createdAt': message_time(row) or session.get('createdAt', 0), 'timestampKnown': message_time(row) is not None,
            **({'nativeMessageId': identity} if text != raw_text else {}),
            **input_identity, **observation, **voice_fields}
