"""Withdraw one peer request without borrowing control of its recipient.

The command journal owns cancellation intent. Native execution owns effects:
an admitted mixed-input turn cannot honestly be interrupted per request.
"""
import copy
import json
import time
import uuid

from amplifier_operations.coordination import fingerprint


def terminal_report(response):
    return (response.get('status') == 'sealed' and response.get('kind') == 'result'
        and response.get('outcome') in {'success', 'failed', 'cancelled', 'declined'})


def status(request):
    value = request.get('cancellation')
    if not value:
        return None
    result = copy.deepcopy(value)
    response = request.get('response') or {}
    if request.get('delivery') in {'not_sent', 'suppressed', 'expired', 'held'}:
        result.update(status='withdrawn', detail='The host confirmed this input was not admitted. Its original receipt and any earlier effects remain saved.')
    if terminal_report(response):
        result.update(status='finished', detail='The recipient retained a terminal report. Inspect it for effects completed before cancellation.')
    if response.get('outcome') == 'cancelled':
        phase = response.get('status')
        result['status'], result['detail'] = {
            'sealed': ('reported', 'The recipient reported cancellation. Inspect retained results for completed effects.'),
            'staged': ('reported_pending', 'The recipient reported cancellation; its turn has not settled yet.'),
        }.get(phase, ('reported_unconfirmed',
            'A cancellation report was saved, but completion could not be confirmed. Inspect the retained result before deciding what to do next.'))
    return result


def references(owner, sid):
    # Pending cancellations must not fall out of the latest-eight projection
    # when other chats send messages. Bound the payload, disclose any overflow.
    # Always include the current generation's bounded input set before older
    # uncertain requests, so retained historical work cannot starve live intent.
    active = (owner.service._session(sid, hydrate=False).get('collaborationGeneration') or {}).get('inputIds', [])[-128:]
    priority = ','.join('?' for _ in active) or 'NULL'
    rows = owner.service.db.execute(f"""SELECT receipt FROM commands
        WHERE json_extract(receipt,'$.commandAction')='coordination.send'
        AND json_extract(receipt,'$.target.sessionId')=?
        AND json_extract(receipt,'$.cancellation.status')='requested'
        AND json_extract(receipt,'$.delivery') IN ('submitting','accepted','applied','unknown')
        AND NOT (coalesce(json_extract(receipt,'$.response.kind'),'')='result'
            AND coalesce(json_extract(receipt,'$.response.status'),'')='sealed'
            AND coalesce(json_extract(receipt,'$.response.outcome'),'') IN ('success','failed','cancelled','declined'))
        ORDER BY CASE WHEN id IN ({priority}) THEN 0 ELSE 1 END, rowid LIMIT 161""", (sid, *active)).fetchall()
    values = [json.loads(row[0]) for row in rows[:160]]
    return {'items': [{'requestId': row['requestId'], 'senderSessionId': row['senderSessionId'],
        **status(row)} for row in values], 'truncated': len(rows) > 160,
        'instruction': 'Stop only the named requested work at a safe boundary. Preserve completed effects and unrelated work. Report outcome=cancelled with coordination.reply when settled; cancellation intent is not proof of stopped execution.'}


async def cancel(owner, source, args, origin, command_id):
    service = owner.service
    identity = command_id or str(uuid.uuid4())
    digest = fingerprint(['coordination.cancel', args, origin, source['id']])
    async with service.lock:
        duplicate = owner.duplicate(identity, digest)
        if duplicate:
            return duplicate
        owner.lineage(source, origin)  # Current native root for agent calls.
        request = owner.receipt(args['requestId'])
        from .collaboration import PROTOCOL
        if (args['sessionId'] != source['id'] or request.get('commandAction') != 'coordination.send'
                or request.get('senderSessionId') != source['id'] or request.get('protocol') != PROTOCOL
                or request.get('workspace') != source['workspace']):
            owner.error('Cancel only a request sent by this conversation in its current workspace.')
        owner.request_target(source, request, hydrate=False)
        original = copy.deepcopy(request)
        service.db.commit()
        try:
            retired_returns = []
            existing = status(request)
            response = request.get('response') or {}
            if existing:
                result = existing
            elif terminal_report(response):
                result = {'status': 'finished', 'detail': 'A terminal report is already retained. Completed effects were not undone.'}
            elif request.get('delivery') in {'not_sent', 'suppressed', 'expired', 'held', 'notified', 'cancelled'}:
                result = {'status': 'not_running', 'detail': 'This request has no admitted work to cancel. Its original receipt is unchanged.'}
            else:
                withdrawn = request.get('delivery') in {'queued', 'pending_steer'}
                result = {'status': 'withdrawn' if withdrawn else 'requested',
                    'operationId': identity, 'requestedAt': time.time(), 'reason': args.get('reason', ''),
                    'detail': ('Withdrawn before execution. Other requests continue normally.' if withdrawn else
                        'Cancellation requested. The recipient will see it at its next coordination check; stopped execution is not yet confirmed. No other work was interrupted.')}
                request['cancellation'] = result
                if withdrawn:
                    request['delivery'] = 'cancelled'
                    request['terminalOutcome'] = {'status': 'observed', 'kind': 'admission',
                        'outcome': 'cancelled', 'qualified': False, 'requestId': request['requestId'],
                        'observedAt': time.time(), 'text': result['detail']}
                    owner.record_outcome(request, request['terminalOutcome'])
                # Withdraw only this request's future automatic return. A return
                # already admitted remains owned by its runtime, never force-stopped.
                if request.get('subscription'):
                    request['subscription'].update(status='cancelled', open=False,
                        detail='The requester cancelled this request; no new automatic return is admitted.')
                retired_returns = retire_queued_returns(owner, request,
                    'The originating request was cancelled before this automatic return was admitted.')
                owner.save(request['requestId'], request)
            # Handling or finishing the peer message does not consume its narrow
            # resume scope. Retain revocation even on the finished/not-running
            # branches above, and let final member admission recheck it.
            if request.get('resumeHandoff') and not request.get('cancellation'):
                request['cancellation'] = {'status': 'requested', 'operationId': identity,
                    'requestedAt': time.time(), 'reason': args.get('reason', ''),
                    'detail': 'Resume handoff revoked for any member input not yet admitted. Completed effects remain.'}
                owner.save(request['requestId'], request)
                result = {**result, 'resumeHandoffRevoked': True}
            answer = {'accepted': True, 'commandAction': 'coordination.cancel',
                'senderSessionId': source['id'], 'target': request['target'],
                'result': {'requestId': request['requestId'], **result, 'interruptsOtherWork': False}}
            owner.insert(identity, digest, answer)
            owner.publish_receipt(answer)
            if result['status'] == 'withdrawn':
                owner.cancel_expiry(request['requestId'])
            for pending_id in retired_returns:
                owner.cancel_expiry(pending_id)
            return copy.deepcopy(answer)
        except BaseException:
            if service.db.in_transaction:
                service.db.rollback()
                owner.refresh_reference(original)
                for pending_id in retired_returns:
                    owner.refresh_reference(owner.receipt(pending_id))
            raise


def retire_queued_returns(owner, request, detail):
    """Mutate only positively queued returns, in bounded pages under the lock.

    The caller owns the transaction and cancels expiry only after durable commit.
    Related in-flight receipts are evidence, not candidates for this transition.
    """
    retired = []
    while rows := owner.service.db.execute("""SELECT id,receipt FROM commands
            WHERE json_extract(receipt,'$.dependencyRequestId')=?
            AND json_extract(receipt,'$.delivery')='queued'
            AND json_extract(receipt,'$.target.sessionId')=?
            ORDER BY rowid LIMIT 64""", (request['requestId'], request['senderSessionId'])).fetchall():
        for identity, encoded in rows:
            pending = json.loads(encoded)
            pending.update(delivery='suppressed', detail=detail)
            owner.save(identity, pending)
            retired.append(identity)
    return retired


def return_summary(owner, request):
    """Scalar indexed facts only; count overflow instead of imposing a limit."""
    where = """FROM commands WHERE json_extract(receipt,'$.dependencyRequestId')=?
        AND json_extract(receipt,'$.target.sessionId')=?
        AND json_extract(receipt,'$.delivery') IN ('submitting','accepted','applied','unknown')"""
    parameters = (request['requestId'], request['senderSessionId'])
    count = owner.service.db.execute('SELECT count(*) ' + where, parameters).fetchone()[0]
    rows = owner.service.db.execute(
        "SELECT id,json_extract(receipt,'$.delivery') " + where + ' ORDER BY rowid LIMIT 16',
        parameters).fetchall()
    return {'items': [{'requestId': identity, 'delivery': delivery} for identity, delivery in rows],
            'count': count, 'truncated': count > len(rows)}


async def unsubscribe(owner, source, args, origin, command_id):
    """Withdraw the wait, never cancellation intent or recipient execution."""
    from .collaboration import PROTOCOL
    service = owner.service
    identity = command_id or str(uuid.uuid4())
    digest = fingerprint(['coordination.unsubscribe', args, origin, source['id']])
    async with service.lock:
        duplicate = owner.duplicate(identity, digest)
        if duplicate:
            return duplicate
        lineage = owner.lineage(source, origin)
        request = owner.receipt(args['requestId'])
        if (args['sessionId'] != source['id'] or request.get('commandAction') != 'coordination.send'
                or request.get('senderSessionId') != source['id'] or request.get('protocol') != PROTOCOL
                or request.get('workspace') != source['workspace']):
            owner.error('Withdraw only your own request wait in its current workspace.')
        owner.request_target(source, request, hydrate=False)
        wait = request.get('subscription')
        if not wait:
            owner.error('This request has no saved subscription to withdraw.', 409)
        original = copy.deepcopy(request)
        retired = []
        service.db.commit()
        try:
            # Orthogonal and irreversible: cancel/review can change status but
            # cannot erase withdrawal or turn it into execution cancellation.
            inputs = lineage.get('sourceInputIds', [])
            wait.setdefault('withdrawal', {'operationId': identity, 'withdrawnAt': time.time(),
                'reason': args.get('reason', ''), **lineage,
                'sourceInputIds': inputs[-16:], 'sourceInputIdsCount': len(inputs)})
            wait['open'] = False
            retired = retire_queued_returns(owner, request,
                'The requester withdrew this wait before its automatic return was admitted.')
            in_flight = return_summary(owner, request)
            owner.save(request['requestId'], request)
            answer = {'accepted': True, 'commandAction': 'coordination.unsubscribe',
                'senderSessionId': source['id'], 'target': request['target'],
                'result': {'requestId': request['requestId'], 'withdrawal': copy.deepcopy(wait['withdrawal']),
                    'originalDelivery': request.get('delivery'), 'cancellation': status(request),
                    'suppressedReturns': {'requestIds': retired[:16], 'count': len(retired),
                                          'truncated': len(retired) > 16},
                    'inFlightReturns': in_flight, 'executionStopped': False,
                    'detail': 'No longer awaiting this request. Already submitting, admitted or unknown returns may still arrive. No execution was stopped; uncertain effects remain uncertain.'}}
            owner.insert(identity, digest, answer)
            owner.publish_receipt(answer)
            for pending_id in retired:
                owner.cancel_expiry(pending_id)
            return copy.deepcopy(answer)
        except BaseException:
            if service.db.in_transaction:
                service.db.rollback()
                owner.refresh_reference(original)
                for pending_id in retired:
                    owner.refresh_reference(owner.receipt(pending_id))
            raise

