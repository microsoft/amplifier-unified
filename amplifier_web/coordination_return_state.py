"""Bounded, passive facts over existing request, outcome and notification receipts.

No authority, lifecycle or persistence lives here. The raw receipt/result remains
available; this projection never equates a saved wait, delivery or review with
model consumption or independently verified correctness.
"""
import json
import time

MAX_BYTES = 8192


def fields(row, *keys):
    return {key: row[key] for key in keys if key in row}


def bounded(value):
    """Keep references exact or explicitly omitted, never shortened into new IDs."""
    def size():
        # The conservative default encoding also bounds UTF-8 JSON delivery.
        return len(json.dumps(value).encode('utf-8'))

    artifacts = value['artifacts']
    while artifacts['items'] and size() > MAX_BYTES:
        artifacts['items'].pop()
        artifacts['omittedCount'] += 1
    if artifacts['omittedCount']:
        value['observation']['coverage'] = 'bounded'
    # Unusually long legacy IDs/reasons must not defeat the envelope budget.
    # Leave independent status facts intact and disclose each omitted exact ref.
    candidates = []

    def visit(row, path=''):
        for key, item in row.items():
            name = path + key
            if isinstance(item, dict):
                visit(item, name + '.')
            elif isinstance(item, str) and key not in {
                    'status', 'returnPolicy', 'coverage', 'consumption', 'basis'}:
                candidates.append((len(json.dumps(item)), row, key, name))

    visit(value)
    for _, row, key, name in sorted(candidates, key=lambda item: item[0], reverse=True):
        if size() <= MAX_BYTES:
            break
        row[key] = None
        value['observation']['coverage'] = 'bounded'
        value['observation']['omittedFields'].append(name)
    return value


def project(owner, request):
    from .collaboration import continuation_outcome, outcome_sender
    from .outcome_acknowledgement import acknowledged, reference

    policy = request.get('returnNotification')
    known = type(policy) is bool
    wait = request.get('subscription') or {}
    response = request.get('response') or {}
    outcome = continuation_outcome(request) or {}
    versioned = wait.get('notificationVersion') == 2
    revision = outcome.get('outcomeRevision')
    evidence = outcome or response
    outcome_source = ('response' if outcome is response else 'terminalOutcome') if outcome else None
    subscription = {
        'registered': True if wait else False if known else None,
        'status': wait.get('status', 'unknown') if wait else 'not_registered' if known else 'unknown',
        'supported': wait.get('supported'),
        **fields(wait, 'continuationId', 'notificationVersion', 'lastClaimedRevision', 'open'),
    }
    if wait.get('detail'):
        subscription['reason'] = wait['detail']
    elif not known and not wait:
        subscription['reason'] = 'No saved return policy or subscription evidence.'

    # claimed can be set by explicit review even when no notification exists.
    # Only the exact revision-specific command says whether input was queued.
    notification = {'status': 'unknown', 'receiptPresent': None,
                    'outcomeRevision': revision, 'consumption': 'unknown'}
    if versioned and wait.get('continuationId') and revision:
        identity = owner.continuation_identity(wait, revision)
        row = owner.service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
        receipt = json.loads(row[0]) if row else None
        notification.update(expectedNotificationId=identity, receiptPresent=bool(row))
        if receipt is None:
            notification['status'] = 'not_queued'
        elif (receipt.get('commandAction') == 'coordination.send'
                and receipt.get('dependencyRequestId') == request['requestId']
                and receipt.get('dependencyOutcomeRevision') == revision
                and receipt.get('target', {}).get('sessionId') == request.get('senderSessionId')
                and receipt.get('senderSessionId') == outcome_sender(request)):
            notification.update(status=receipt.get('delivery', 'unknown'),
                **fields(receipt, 'requestId', 'inputId', 'messageId', 'acknowledgementId'))
            if receipt.get('detail') or (receipt.get('admission') or {}).get('reason'):
                notification['reason'] = receipt.get('detail') or receipt['admission']['reason']
        else:
            notification['reason'] = 'The notification identity has no matching revision-specific receipt.'
    elif versioned or known and not wait:
        notification.update(status='not_queued', receiptPresent=False)
    else:
        notification['reason'] = 'Revision-specific notification evidence is unavailable.'

    review = reference(request)
    ack = wait.get('acknowledgedOutcome') or {}
    acknowledgement = {
        'status': ('recorded' if outcome and acknowledged(request, outcome) else
                   'not_recorded' if versioned or known else 'unknown'),
        'outcomeRevision': revision,
    }
    if review:
        acknowledgement['review'] = review['review']
    if ack:
        acknowledgement['lastRecorded'] = fields(
            ack, 'operationId', 'outcomeRevision', 'reviewKey', 'reviewedAt')

    saved = response.get('artifacts') or []
    retained = sum(item.get('status') == 'retained' for item in saved)
    verification = response.get('artifactVerification')
    artifact_status = ('retained' if saved and retained == len(saved) else
                       'partial' if retained else 'unavailable' if saved else
                       'not_requested' if verification == 'not_requested' else 'unknown')
    # These are saved retention receipts, not a fresh read/hash of their bytes.
    artifacts = {
        'status': artifact_status, 'basis': 'saved_retention_receipts',
        'responseRevision': response.get('responseRevision'),
        'operationId': response.get('operationId'), 'verification': verification,
        'retainedCount': retained, 'requestedCount': len(saved), 'omittedCount': 0,
        'items': [fields(item, 'reference', 'status', 'outputId', 'sha256', 'bytes') for item in saved],
    }
    saved_refs = {item.get('reference') for item in saved}
    for ref in response.get('references', []):
        if ref not in saved_refs:
            artifacts['items'].append({'reference': ref,
                'status': 'citation' if verification is not None else 'unknown'})
    artifacts['referenceCount'] = len(artifacts['items'])
    value = {
        'version': 1,
        'request': {**fields(request, 'requestId', 'senderSessionId', 'delivery'),
                    'recipientSessionId': request.get('target', {}).get('sessionId') or request.get('sessionId')},
        'resultRef': {'action': 'coordination.result', 'args': {'requestId': request['requestId']}},
        'returnPolicy': 'requested' if policy is True else 'none' if policy is False else 'legacy_unknown',
        'subscription': subscription,
        'response': {
            'status': response.get('status', 'unknown') if response else 'absent' if known else 'unknown',
            **fields(response, 'responseRevision', 'kind', 'outcome', 'operationId',
                     'generationId', 'terminalMessageId'),
        },
        'outcome': {
            'status': outcome.get('status', 'unknown') if outcome else 'absent' if known else 'unknown',
            'source': outcome_source,
            **fields(outcome, 'outcomeRevision', 'responseRevision', 'kind', 'outcome', 'operationId'),
        },
        'qualification': {
            'status': ('qualified' if evidence.get('qualified') is True else
                       'unqualified' if evidence.get('qualified') is False else 'unknown'),
            'basis': outcome_source or ('response' if response else 'unknown'),
            **fields(evidence, 'outcomeRevision', 'responseRevision', 'operationId',
                     'generationId', 'terminalMessageId'),
            'independentArtifactVerification': False,
        },
        'notification': notification,
        'acknowledgement': acknowledgement,
        'artifacts': artifacts,
        'observation': {
            'observedAt': time.time(), 'coverage': 'current_receipts', 'omittedFields': [],
            'responseRevision': request.get('responseRevision'),
            'outcomeRevision': request.get('outcomeRevision'),
            'meaning': 'Delivery is not consumption; qualification and acknowledgement are not independent acceptance.',
        },
    }
    return bounded(value)