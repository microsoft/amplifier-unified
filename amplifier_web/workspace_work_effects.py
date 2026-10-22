"""Private Work precondition checks, never ordinary tool permission grants.

The authenticated worker supplies its single-use originating-call witness. This
module does not mint, consume or reconstruct that witness from model arguments.
Effect owners must call the check at final admission; a result is not a lease.
"""
from __future__ import annotations

import hashlib
import json

from .collaboration import BINDING

# Explicit recovery surface. Do not infer read-only behavior from action suffixes
# or ACTION_POLICIES (which describes ownership, not effect safety).
SAFE_ACTIONS = frozenset({
    'workspace.work.list', 'workspace.work.read', 'workspace.work.source',
    'workspace.work.history', 'workspace.work.context', 'workspace.work.wait',
    'workspace.work.invocation', 'workspace.work.promise',
    'workspace.work.report', 'workspace.work.resource', 'workspace.work.publication',
    'coordination.context', 'coordination.list', 'coordination.read',
    'coordination.result', 'coordination.artifact', 'coordination.review',
    'coordination.awareness', 'coordination.intent.read', 'coordination.diagnostics',
    'coordination.reply', 'coordination.cancel', 'coordination.wait',
    'task.get', 'task.context', 'task.source', 'task.history',
    'operations.list', 'operations.read', 'operations.wait',
    'workspace.tests.read', 'workspace.tests.result', 'workspace.tests.cancel',
    'workspace.environments.read', 'workspace.environments.reconcile',
})
SAFE_TOOLS = frozenset({'read_file', 'grep', 'glob'})


def recovery_read(tool_name, tool_input):
    if tool_name in SAFE_TOOLS:
        return True
    if tool_name != 'app_control':
        return False
    operation = tool_input.get('operation')
    if operation in {'get_state', 'list_actions', 'history', 'context.read'}:
        return True
    return operation == 'dispatch' and (tool_input.get('args') or {}).get('action') in SAFE_ACTIONS


def _receipt(invocation, identity):
    row = invocation.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    return json.loads(row[0]) if row else None


def applicability(invocation, target):
    """Inspect exact host-retained input/creation owners, never a model Work list."""
    active = target.get('collaborationGeneration') or {}
    inputs = active.get('inputIds', [])
    if not isinstance(inputs, list) or any(not isinstance(i, str) for i in inputs):
        return True, 'Host delivered-input identity is unavailable.'
    creation_id = (target.get('collaboration') or {}).get('requestId')
    creation = _receipt(invocation, creation_id) if creation_id else None
    if creation_id and (not creation or creation.get('commandAction') != 'coordination.create'
                        or creation.get('sessionId') != target['id']):
        return True, 'Commissioning receipt is unavailable; Work applicability is unknown.'
    if creation and creation.get('workReferences') and (
            creation.get('initialInputId') in inputs or len(inputs) > 16 or not active.get('id')):
        return True, None
    for identity in inputs:
        row = _receipt(invocation, identity)
        if not row:
            if creation and creation.get('workReferences'):
                return True, 'Delivered input receipt is unavailable; Work applicability is unknown.'
            continue
        if row.get('workAssociationRef'):
            return True, None
        operation_id = row.get('creationOperationId')
        if operation_id:
            operation = _receipt(invocation, operation_id)
            if not operation:
                return True, 'Input creation receipt is unavailable; Work applicability is unknown.'
            if operation.get('workReferences') or operation.get('workAssociation'):
                return True, 'Delivered Work association is missing or incomplete.'
    return False, None


def check(invocation, sid, args):
    """Return a current Work precondition only; no I/O, admission, or permission."""
    from .service import AppError
    if (set(args) != {'toolName', 'toolInput', 'toolCallId'}
            or not isinstance(args['toolName'], str) or not 1 <= len(args['toolName']) <= 200
            or not isinstance(args['toolInput'], dict)
            or not isinstance(args['toolCallId'], str) or not 1 <= len(args['toolCallId']) <= 200):
        raise AppError('Work effect checks require the exact tool name, input and executing call ID.')
    target = invocation.bound_root(sid)
    result = {'applicable': True, 'allowed': False, 'reason': None,
              'requiredVersion': None, 'effectAuthorized': False}
    if recovery_read(args['toolName'], args['toolInput']):
        return {**result, 'allowed': True, 'reason': 'Exact read or safe reporting; ordinary authority still applies.'}
    try:
        applicable, reason = applicability(invocation, target)
        result.update(applicable=applicable, reason=reason)
        if not applicable:
            return {**result, 'allowed': True, 'reason': 'No commissioned Work association in this host input set; ordinary authority still applies.'}
        if reason:
            return result
        required, _ = invocation.required(sid)
        result['requiredVersion'] = required.get('version')
        if required.get('status') != 'available' or not required.get('coverage', {}).get('complete'):
            return {**result, 'reason': required.get('reason') or 'Current Work requirements are unavailable.'}
        witness = (BINDING.get() or {}).get('_workOrigin')
        if not isinstance(witness, dict):
            return {**result, 'reason': 'No authenticated originating-call coverage at this effect boundary.'}
        origin = witness.get('origin')
        active = target.get('collaborationGeneration') or {}
        exact_inputs = sorted(set(active.get('inputIds', [])))
        if (not isinstance(origin, dict) or origin.get('schemaVersion') != 1
                or origin.get('rootSessionId') != sid
                or origin.get('runtimeSessionId') != (BINDING.get() or {}).get('_runtimeSessionId')
                or origin.get('generationId') != required.get('generationId')
                or origin.get('stopRevision') != target.get('interruptionRevision', 0)
                or origin.get('inputCoverage') != 'complete'
                or origin.get('inputIds') != exact_inputs):
            return {**result, 'reason': 'Originating call scope no longer matches this root, generation, Stop or complete input set.'}
        encoded = json.dumps([args['toolName'], args['toolInput']], sort_keys=True,
                             ensure_ascii=False, allow_nan=False).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        if (witness.get('toolCallId') != args['toolCallId']
                or witness.get('toolName') != args['toolName'] or witness.get('argsSha256') != digest
                or witness.get('rootSessionId') != sid or witness.get('generationId') != required['generationId']
                or witness.get('inputIds') != exact_inputs or witness.get('version') != required['version']
                or witness.get('status') != 'available' or witness.get('complete') is not True
                or witness.get('effectAuthorized') is not False
                or witness.get('requirementCount') != required['requirementCount']
                or witness.get('coveredCount') != required['requirementCount']):
            return {**result, 'reason': 'Exact call, arguments or current required-content coverage does not match.'}
        for key in ('admittedCallId', 'attemptId', 'fittedSha256'):
            if not isinstance(origin.get(key), str) or not origin[key]:
                return {**result, 'reason': 'Host admission and fitted-request identity are unavailable.'}
        if (origin['attemptId'] != witness.get('attemptId')
                or origin['fittedSha256'] != witness.get('requestSha256')):
            return {**result, 'reason': 'Admission, request and originating attempt are not joined.'}
        return {**result, 'allowed': True,
                'reason': 'Current Work precondition checked; ordinary effect-owner permissions and admission still apply.'}
    except (AppError, KeyError, TypeError, ValueError):
        return {**result, 'allowed': False, 'reason': 'Work effect precondition is unavailable; no effect was authorized.'}
