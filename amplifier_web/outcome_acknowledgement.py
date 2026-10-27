"""Explicit requester review of an exact outcome, never inferred from a read."""
import copy
import time
import uuid

from amplifier_operations.coordination import fingerprint

from .task_binding import matches as task_matches


REVIEWABLE_ACTIONS = {'coordination.send', 'workspace.tests.submit'}


def review_key(request, outcome):
    # Sealing a negative report does not change its meaning. Qualification,
    # report identity, content and linked evidence do; retain those in the CAS.
    return fingerprint([request['requestId'], request.get('workspace'),
        request.get('senderSessionId'), request.get('target'),
        {key: outcome.get(key) for key in ('outcomeRevision', 'responseRevision',
            'operationId', 'outcome', 'qualified', 'text', 'references',
            'messageIds', 'terminalMessageId', 'artifactVerification')}])


def acknowledged(request, outcome):
    value = (request.get('subscription') or {}).get('acknowledgedOutcome') or {}
    return (value.get('outcomeRevision') == outcome.get('outcomeRevision')
        and value.get('reviewKey') == review_key(request, outcome))


def reference(request):
    from .collaboration import PROTOCOL, continuation_outcome
    outcome = continuation_outcome(request)
    wait = request.get('subscription') or {}
    if (request.get('commandAction') not in REVIEWABLE_ACTIONS
            or request.get('protocol') != PROTOCOL or not outcome
            or not outcome.get('outcomeRevision') or wait.get('notificationVersion') != 2):
        return None
    if (request.get('commandAction') == 'workspace.tests.submit'
            and wait.get('status') not in {'waiting', 'claimed', 'backpressured'}):
        return None
    return {'acknowledged': acknowledged(request, outcome),
        'review': {'action': 'coordination.acknowledge', 'args': {
            'sessionId': request['senderSessionId'], 'requestId': request['requestId'],
            'outcomeRevision': outcome['outcomeRevision'], 'reviewKey': review_key(request, outcome)}},
        'meaning': 'Use only after reviewing this exact result. Retires its unsubmitted notification, not the request or any work. Later changed outcomes still notify.'}


async def acknowledge(owner, source, args, origin, command_id):
    from .collaboration import PROTOCOL, continuation_outcome
    service = owner.service
    identity = command_id or str(uuid.uuid4())
    digest = fingerprint(['coordination.acknowledge', args, origin, source['id']])
    async with service.lock:
        duplicate = owner.duplicate(identity, digest)
        if duplicate:
            return duplicate
        lineage = owner.lineage(source, origin)
        request = owner.receipt(args['requestId'])
        if (args['sessionId'] != source['id'] or request.get('commandAction') not in REVIEWABLE_ACTIONS
                or request.get('senderSessionId') != source['id'] or request.get('protocol') != PROTOCOL
                or request.get('workspace') != source['workspace']):
            owner.error('Acknowledge only your own request in its current workspace.')
        if request.get('commandAction') == 'coordination.send':
            owner.request_target(source, request, hydrate=False)
        else:
            owner.authorize(source, request['target'], hydrate=False)
        if request.get('commandAction') == 'workspace.tests.submit':
            # Test waits belong to this root's exact placement and cancellation
            # boundary, in addition to the shared task/Stop guards below.
            if (request['target'].get('sessionId') != source['id']
                    or service.test_queue.continuation_guard(request)):
                owner.error('The saved test notification boundary changed.', 409)
        wait = request.get('subscription') or {}
        task = source.get('task') or service.coordination.task(source['id'])
        if (wait.get('notificationVersion') != 2
                or wait.get('status') not in {'waiting', 'claimed', 'backpressured'}
                or wait.get('interruptionRevision') != source.get('interruptionRevision', 0)
                or not task_matches(task, wait)
                or task.get('status') in {'paused', 'blocked', 'completed'}
                or service.assignments.blocked(source, request.get('assignmentId'))):
            owner.error('The saved notification task or interruption boundary changed.', 409)
        outcome = continuation_outcome(request)
        if (not outcome or type(args.get('outcomeRevision')) is not int
                or args['outcomeRevision'] != outcome.get('outcomeRevision')
                or args.get('reviewKey') != review_key(request, outcome)):
            owner.error('The exact result changed; inspect the current result before acknowledging it.', 409)
        notification_id = owner.continuation_identity(wait, outcome['outcomeRevision'])
        row = service.db.execute('SELECT receipt FROM commands WHERE id=?', (notification_id,)).fetchone()
        notification = owner.receipt(notification_id) if row else None
        if notification and (notification.get('dependencyRequestId') != request['requestId']
                or notification.get('target', {}).get('sessionId') != source['id']
                or not owner.claim_matches(request, notification)):
            owner.error('The retained notification no longer matches this exact result.', 409)
        original, prior_notification = copy.deepcopy(request), copy.deepcopy(notification)
        service.db.commit()
        try:
            acknowledgement = {'operationId': identity, 'outcomeRevision': outcome['outcomeRevision'],
                'reviewKey': args['reviewKey'], 'reviewedAt': time.time(), **lineage}
            wait['acknowledgedOutcome'] = acknowledgement
            # An interim report remains subscribed to later revisions. A final
            # report already reviewed needs no reserved inbox slot.
            # The review itself claims this outcome even when an inbox-full
            # attempt never created its notification. Keeping backpressured or
            # waiting would retain a slot despite open=False. Claimed remains
            # eligible for changed later outcomes, with interim slots retained.
            wait.update(status='claimed',
                open=outcome.get('outcome') in {'deferred', 'question', 'conflict'})
            wait.pop('detail', None)
            retired = notification is not None and notification.get('delivery') == 'queued'
            if retired:
                notification.update(delivery='suppressed', acknowledgementId=identity,
                    detail='The requester reviewed this exact result before its automatic notification was admitted.')
                owner.save(notification_id, notification)
            owner.save(request['requestId'], request)
            answer = {'accepted': True, 'commandAction': 'coordination.acknowledge',
                'senderSessionId': source['id'], 'target': request['target'],
                'result': {'requestId': request['requestId'], **acknowledgement,
                    'notificationId': notification_id, 'notificationRetired': retired,
                    'notificationDelivery': notification.get('delivery') if notification else 'not_queued',
                    'workCancelled': False, 'resultQualificationChanged': False}}
            owner.insert(identity, digest, answer)
            owner.publish_receipt(answer)
            if retired:
                owner.cancel_expiry(notification_id)
            return copy.deepcopy(answer)
        except BaseException:
            if service.db.in_transaction:
                service.db.rollback()
                owner.refresh_reference(original)
                if prior_notification:
                    owner.refresh_reference(prior_notification)
            raise
