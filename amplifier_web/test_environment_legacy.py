"""Recognize exact returned preservation failures, never transport uncertainty.

The original host helper (before phase receipts) returned this exact envelope
only after a synchronous DTU export rejection. The export-limit producer killed
and reaped its copy process; the host drained and reaped the helper before saving
its result. `destroy` followed the two exports and was not reached on this error.

This is qualification of retained host evidence, not a guessed phase record or a
conversion of UNKNOWN history to success. Current ownership and full preservation
remain mandatory in the existing cleanup worker.

The phase-aware helper also returned a bounded verify-exports/not_dispatched
failure after exports, before destroy. Only its exact saved response qualifies;
archives, message text and private diagnostic files alone cannot prove return.
"""
from __future__ import annotations

import hashlib
import json
import re


def returned_preservation_failure(row, original):
    """Return an exact continuation binding, or None for every unqualified case."""
    if row.get('guestRemoved') or row.get('status') == 'retired':
        return None
    return _returned_binding(row, original)


def returned_export_limit(row, original):
    """Retain the narrower legacy predicate for existing callers."""
    proof = returned_preservation_failure(row, original)
    return proof if proof and proof['kind'] == 'returned-legacy-export-limit' else None


def completed_preservation_failure(row, original, removal):
    """Revalidate the saved exception for dependency-only cleanup.

    The caller must first verify the committed guest removal. This never
    qualifies a new guest effect or manufactures the original qualification.
    """
    if row.get('guestRemoved') is not True:
        return None
    proof = _returned_binding(row, original)
    return proof if proof and removal.get('legacyCleanupQualification') == proof else None


def _returned_binding(row, original):
    if (not isinstance(original, dict)
            or original.get('action') != 'workspace.environments.remove'
            or original.get('state') != 'outcome_unknown'
            or original.get('resourceId') != row.get('id')
            or original.get('sessionId') != row.get('sessionId')
            or not isinstance(original.get('requestId'), str) or not original['requestId']
            or row.get('backendType') != 'docker'
            or row.get('backendAdmitted') is not True
            or row.get('backendConfigRetained') is not True):
        return None
    identity = row.get('backendIdentity')
    if (not isinstance(identity, dict) or set(identity) != {'backend', 'daemon'}
            or identity['backend'] != 'docker' or not isinstance(identity['daemon'], str)
            or not identity['daemon']):
        return None
    if any(not isinstance(row.get(k), str) or not row[k]
           for k in ('id', 'sessionId', 'workspaceId', 'backendKey', 'backendInstallation',
                     'universeId', 'launchOperationId', 'source')):
        return None
    snapshot = row.get('snapshot')
    if (not isinstance(snapshot, dict)
            or not re.fullmatch(r'[a-f0-9]{64}', str(snapshot.get('archive_sha256', '')))
            or any(not re.fullmatch(r'[a-f0-9]{40}', str(snapshot.get(k, '')))
                   for k in ('snapshot_commit', 'snapshot_tree'))):
        return None
    result = original.get('result')
    # invoke() saves structured results only after draining/reaping the helper.
    # Its exception/restart/timeout path does not manufacture these envelopes.
    if (not isinstance(result, dict) or result.get('ok') is not False
            or result.get('status') != 'unknown'
            or result.get('universeId') != row['universeId']
            or not isinstance(result.get('message'), str)):
        return None
    if (set(result) == {'ok', 'status', 'universeId', 'snapshot', 'code', 'message'}
            and result['code'] == 'export-too-large' and result['snapshot'] == snapshot):
        kind = 'returned-legacy-export-limit'
    elif (set(result) == {'ok', 'status', 'universeId', 'removal', 'message'}
            and isinstance(result['removal'], dict)
            and type(result['removal'].get('version')) is int
            and result['removal'] == {'version': 1, 'phase': 'verify-exports',
                                     'code': 'phase_failed', 'destroyEffect': 'not_dispatched'}):
        kind = 'returned-archive-verification-failure'
    else:
        return None
    owner = row.get('owner')
    if (not isinstance(owner, dict) or set(owner) != {'sessionId', 'principal'}
            or owner.get('sessionId') != row['sessionId']
            or not isinstance(owner.get('principal'), str) or not owner['principal']):
        return None
    payload = json.dumps(original, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return {'version': 1, 'kind': kind,
            'originalRequestId': original['requestId'],
            'originalSha256': hashlib.sha256(payload).hexdigest(),
            'resourceId': row['id'], 'sessionId': row['sessionId'], 'owner': owner,
            'backendKey': row['backendKey'], 'backendIdentity': identity,
            'backendInstallation': row['backendInstallation'],
            'launchOperationId': row['launchOperationId'], 'universeId': row['universeId'],
            'source': row['source'], 'snapshot': snapshot}
