"""Selected session asset closure, never filesystem paths as transfer authority."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import io
from datetime import datetime, timezone
import os
import tempfile
import shutil
from pathlib import Path

from . import attachments
from .host.storage import SessionStore

MEDIA = 'experimental.media.v1'
UPLOADS = 'amplifier_attachments'


def message_refs(messages, session_id):
    """Read only canonical metadata, never parse arbitrary text/tool output."""
    refs, seen = [], set()
    for message in messages:
        metadata = message.get('metadata') or {}
        if MEDIA not in metadata:
            continue
        from amplifier_foundation.session.media import validate_blob_ref
        carrier = metadata[MEDIA]
        if not isinstance(carrier, dict) or set(carrier) != {'refs'} or not isinstance(carrier['refs'], list):
            raise ValueError('Malformed canonical media carrier')
        for candidate in carrier['refs']:
            ref = validate_blob_ref(candidate)
            if ref['session_id'] != session_id:
                raise ValueError('Media reference belongs to another session')
            key = ref_key(ref)
            if key not in seen:
                seen.add(key)
                refs.append(ref)
    return refs


def upload_rows(messages=(), visible=(), drafts=()):
    """Exact owner-defined display positions; no recursive JSON discovery."""
    result = {}
    candidates = list(drafts)
    for message in messages:
        carrier = (message.get('metadata') or {}).get(UPLOADS)
        if carrier is not None:
            if not isinstance(carrier, dict) or set(carrier) != {'v', 'items'} or type(carrier['v']) is not int or carrier['v'] != 1 or not isinstance(carrier['items'], list):
                raise ValueError('Malformed saved attachment carrier')
            candidates.extend(carrier['items'])
    for message in visible:
        candidates.extend(message.get('attachments') or [])
    for row in candidates:
        if not isinstance(row, dict):
            raise ValueError('Malformed saved attachment')
        identity = row.get('id')
        attachments.location(Path('/unused'), identity)
        if (set(row) - {'id', 'name', 'size', 'mime', 'url', 'source', 'preview'}
                or not isinstance(row.get('name'), str) or len(row['name']) > 200
                or row.get('mime') not in attachments._MIMES
                or type(row.get('size')) is not int or not 0 < row['size'] <= attachments.MAX_BYTES
                or row.get('url') != '/api/attachments/' + identity):
            raise ValueError('Malformed saved attachment')
        if identity in result and result[identity] != row:
            raise ValueError('Conflicting saved attachment identity')
        result[identity] = copy.deepcopy(row)
    return result


def packed(data):
    return {'data': base64.b64encode(data).decode('ascii'),
            'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def unpacked(value):
    if not isinstance(value, dict) or set(value) != {'data', 'sha256', 'bytes'} or type(value['bytes']) is not int or not 0 < value['bytes'] <= attachments.MAX_BYTES:
        raise ValueError('Malformed asset transport')
    if not isinstance(value['data'], str) or len(value['data']) > attachments.MAX_ENCODED_BYTES:
        raise ValueError('Malformed asset transport')
    data = base64.b64decode(value['data'], validate=True)
    if len(data) != value['bytes'] or hashlib.sha256(data).hexdigest() != value['sha256']:
        raise ValueError('Transferred asset integrity check failed')
    return data


def capture_uploads(home, selected):
    result = {}
    for identity, expected in selected.items():
        with attachments.open_file(home, identity) as (stream, row):
            if row != expected:
                raise ValueError('Saved attachment metadata changed before transfer')
            result[identity] = {'metadata': row, 'content': packed(stream.read(attachments.MAX_BYTES + 1))}
    return result


def validate_uploads(manifest, selected):
    if not isinstance(manifest, dict) or set(manifest) != set(selected):
        raise ValueError('Transferred attachments do not close the selected history')
    for identity, expected in selected.items():
        record = manifest[identity]
        if not isinstance(record, dict) or set(record) != {'metadata', 'content'} or record['metadata'] != expected:
            raise ValueError('Transferred attachment metadata differs from history')
        if len(unpacked(record['content'])) != expected['size']:
            raise ValueError('Transferred attachment size differs from history')


def preflight_uploads(home, manifest, selected):
    validate_uploads(manifest, selected)
    for identity, record in manifest.items():
        directory = attachments.location(home, identity)
        if directory.exists():
            with attachments.open_file(home, identity) as (stream, row):
                if row != record['metadata'] or stream.read(attachments.MAX_BYTES + 1) != unpacked(record['content']):
                    raise ValueError('Existing attachment conflicts with transferred evidence')


def install_uploads(home, manifest, selected):
    """Assets first. Existing UUIDs are verified, never overwritten."""
    preflight_uploads(home, manifest, selected)
    for identity, record in manifest.items():
        directory = attachments.location(home, identity)
        if directory.exists():
            continue
        directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Never expose a partial UUID directory. A failed staging write has no
        # published identity; the same admitted import can retry unchanged.
        temporary = Path(tempfile.mkdtemp(prefix='.import-', dir=directory.parent))
        try:
            SessionStore._atomic(temporary / 'content', unpacked(record['content']))
            SessionStore._atomic(temporary / 'metadata.json', json.dumps(record['metadata']))
            fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            from filelock import FileLock
            with FileLock(str(directory.parent / '.import.lock')):
                preflight_uploads(home, {identity: record}, {identity: selected[identity]})
                if not directory.exists():
                    os.rename(temporary, directory)
            fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)


def expired(ref):
    value = ref['provenance']['expires_at']
    return value is not None and datetime.fromisoformat(value.replace('Z', '+00:00')) <= datetime.now(timezone.utc)


def ref_key(ref):
    return hashlib.sha256(json.dumps(ref, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def capture_media(store, refs):
    """Capture only selected owned records; original expiry is never renewed."""
    result = {}
    for ref in refs:
        store.validate_published_ref(ref, check_expiry=False)
        record = {'ref': ref, 'state': 'expired' if expired(ref) else 'available'}
        if record['state'] == 'available':
            with store.resolve_published_ref(ref) as stream:
                record['content'] = packed(stream.read(attachments.MAX_BYTES + 1))
        result[ref_key(ref)] = record
    return result


def source_reader_guard(guard, refs):
    """Exact selected canonical/recovery refs plus unchanged native companions."""
    selected = {ref_key(ref) for ref in refs}
    def verify(ref, *, purpose):
        guard.check()
        return purpose == 'transfer' and ref_key(ref) in selected
    return verify


def validate_media(manifest, refs):
    if not isinstance(manifest, dict) or set(manifest) != {ref_key(ref) for ref in refs}:
        raise ValueError('Transferred media does not close the selected history')
    for ref in refs:
        record = manifest[ref_key(ref)]
        if not isinstance(record, dict) or record.get('ref') != ref:
            raise ValueError('Transferred media identity differs from history')
        if record.get('state') == 'expired':
            if set(record) != {'ref', 'state'} or not expired(ref):
                raise ValueError('Invalid expired media record')
        elif record.get('state') == 'available':
            if set(record) != {'ref', 'state', 'content'}:
                raise ValueError('Invalid available media record')
            raw = unpacked(record['content'])
            if len(raw) != ref['bytes'] or hashlib.sha256(raw).hexdigest() != ref['sha256']:
                raise ValueError('Transferred media integrity differs from history')
        else:
            raise ValueError('Unsupported transferred media state')


def adopt_messages(messages, source_store, target_store):
    """The caller filters the prefix before this one explicit linear operation."""
    refs = message_refs(messages, source_store.session_id)
    if not refs:
        return messages
    adopted = target_store.adopt_sync(refs, source_store)
    replacements = {ref_key({**ref, 'session_id': source_store.session_id}): ref for ref in adopted}
    result = []
    for message in messages:
        carrier = (message.get('metadata') or {}).get(MEDIA)
        if carrier is None:
            result.append(message)
            continue
        row = {**message, 'metadata': {**message['metadata'], MEDIA: {
            'refs': [replacements[ref_key(ref)] for ref in carrier['refs']]}}}
        result.append(row)
    return result


def install_media(store, manifest, refs, *, receipt, verifier):
    """Call only after the signed transfer owner authorized its exact package."""
    validate_media(manifest, refs)
    for ref in refs:
        record = manifest[ref_key(ref)]
        if record['state'] == 'expired' or expired(ref):
            store.import_verified(ref, None, receipt=receipt, verifier=verifier)
        else:
            store.import_verified(ref, io.BytesIO(unpacked(record['content'])),
                                  receipt=receipt, verifier=verifier)


def release_comparison(value):
    """Expiry changes eligibility, not source history or the frozen package.

    Both sides are compared at the same time. Once an original observation has
    expired, only its original reference/expiry participates; its pixels cannot
    authorize new delivery or make an unchanged transfer appear edited.
    """
    result = dict(value)
    if 'assets' not in value:
        return result
    media = {}
    for key, record in value['assets']['media'].items():
        media[key] = ({'ref': record['ref'], 'state': 'expired'}
                      if expired(record['ref']) else record)
    result['assets'] = {**value['assets'], 'media': media}
    return result


def resolve_upload_origin(message, *, session_id, attachment_id, input_id, media_store):
    """Host-only exact admitted-input/UUID provenance, never inferred history.

    The caller owns the unchanged source record/prefix receipt. Legacy UUID
    metadata alone has no creation/expiry witness and deliberately returns None.
    This is not an adapter for arbitrary ImageBlock or diagnostic images.
    """
    marker = (message.get('metadata') or {}).get('amplifier_input')
    if (message.get('role') != 'user' or not isinstance(marker, dict)
            or marker.get('version') != 1 or marker.get('kind') not in {'user', 'steer'}
            or not isinstance(input_id, str) or not input_id or marker.get('id') != input_id):
        return None
    if attachment_id not in upload_rows([message]):
        return None
    matches = [ref for ref in message_refs([message], session_id)
               if ref['provenance']['kind'] == 'upload'
               and ref['provenance']['id'] == attachment_id]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError('Ambiguous upload provenance in admitted input')
    ref = media_store.validate_published_ref(matches[0], check_expiry=False)
    return copy.deepcopy(ref['provenance'])