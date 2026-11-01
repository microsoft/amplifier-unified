"""Cooperative app replacement using the existing update and input receipts.

Staging does not pause work. Only positively unsubmitted messages wait during
actual replacement and restart. Older pre-replacement holds can be withdrawn
through their existing receipts. No input or uncertain effect is replayed.
"""
import copy
import json


class UpdateDeferred(Exception):
    """Raised only before transport admission, never after a partial write."""
    def __init__(self, attempt_id):
        super().__init__("Message saved while the app update finishes.")
        self.attempt_id = attempt_id


def held(service):
    return (service.state.get('updates', {}).get('drain') or {}).get('phase') in {'draining', 'replacing', 'releasing'}


def new_work(action, args):
    """The updater's existing new-work categories, shared by both phases."""
    return (action in {'question.answer', 'conversation.send', 'conversation.retry', 'session.takeover',
        'worker.spawn', 'worker.steer', 'worker.message', 'call.start', 'feedback.submit', 'feedback.comment',
        'feedback.get', 'feedback.reconcile', 'feedback.update', 'feedback.close', 'feedback.reopen',
        'feedback.attachments.review', 'feedback.attachments.add', 'feedback.attachments.reconcile'}
        or action == 'session.naming' and args.get('regenerate') or action == 'session.naming.backfill'
        or action.startswith('smartTools.') and action not in {'smartTools.context', 'smartTools.result'})


def input_gate(service, sid=None, identity=None):
    current = service.state.get('updates', {}).get('drain') or {}
    if (sid and identity and current.get('phase') == 'releasing'
            and current.get('releasingInput') == [sid, identity]):
        return
    if held(service):
        raise UpdateDeferred(service.state['updates']['drain']['attemptId'])


def retains_worker(service, runtime, sid):
    drain = service.state.get('updates', {}).get('drain') or {}
    boundary = drain.get('workers', {}).get(sid) or {}
    worker = getattr(runtime, 'workers', {}).get(sid) or {}
    return (drain.get('phase') == 'draining' and boundary and not boundary.get('ready')
            and worker.get('runtimeOwnerId') == boundary.get('owner'))


def bind(service, runtime):
    if runtime is None:
        return
    runtime.update_input_gate = lambda sid=None, identity=None: input_gate(service, sid, identity)
    def message_guard(sid, message):
        proof = message.get('updateAdmission')
        if proof and proof['interruptionRevision'] != service._session(sid).get('interruptionRevision', 0):
            from .runtime import RuntimeStartupError
            raise RuntimeStartupError('Stopped after this message was saved. Nothing was sent.')
    runtime.update_message_guard = message_guard
    if retention := getattr(runtime, 'retention', None):
        existing = retention.protected
        retention.protected = lambda sid: existing(sid) or retains_worker(service, runtime, sid)


def binding(session):
    return {key: copy.deepcopy(session.get(key, default)) for key, default in (
        ('interruptionRevision', 0), ('executionRevision', 0), ('workspace', None),
        ('workingDirectory', None))}


def same_owner(service, row):
    session = service._session(row['sessionId'])
    return (binding(session) == row['binding']
            and not service.assignments.blocked(session))


def hold_message(service, session, message):
    """Called under the host lock, before this input's first transport attempt."""
    drain = service.state['updates']['drain']
    identity = message['inputId']
    key = [session['id'], identity]
    if key not in drain['messages']:
        drain['messages'].append(key)
    message.pop('steering', None)  # Only ordinary allow-next-turn messages enter.
    message['updateAdmission'] = {'interruptionRevision': session.get('interruptionRevision', 0)}
    message['delivery'] = {'status': 'queued_for_update', 'attemptId': drain['attemptId'],
                           'interruptionRevision': session.get('interruptionRevision', 0)}
    # A preceding dispatch may have published a not-yet-admitted turn before
    # the drain began. It is not native work and must not block its own update.
    turn = next((row for row in session.get('execution', {}).get('turns', []) if row['id'] == identity), None)
    if turn and not any(node.get('turnId') == identity for node in session['execution'].get('nodes', [])):
        turn['phase'] = 'queued'
    active = session.get('collaborationGeneration') or {}
    if not active.get('id') or active.get('terminal'):
        session['status'] = 'idle'
    receipt = service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    if receipt:
        value = json.loads(receipt[0]); value['delivery'] = 'queued_for_update'
        service.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(value), identity))


async def prepare(manager, release):
    """Observe idle readiness without reserving workers or withholding input."""
    service = manager.service
    prior = service.state['updates'].get('drain') or {}
    if prior.get('phase') == 'draining':
        # Older hosts could freeze all work before a restart was possible.
        # Withdraw only that pre-replacement boundary using its saved receipts.
        # A released continuation may not have reached admission yet: do not
        # replace the host in the same observation that releases its work.
        await finish(manager, 'withdrawn')
        return False
    return not held(service) and not manager.busy()


def begin_replacement(manager, release):
    """Close admission under the final activation lock, never during staging."""
    service = manager.service
    identity = manager.diagnostics.state['attemptId']
    service.state['updates']['drain'] = {
        'attemptId': identity,
        'target': {'attemptId': identity, 'revision': release['revision'],
                   'generation': release.get('generation')},
        'phase': 'replacing', 'hostInstanceId': service.instance_id,
        'workers': {}, 'messages': []}


def latest_input(session, excluding_request=None):
    # The existing bounded message view is enough: a new saved input appears
    # before native generation events. Ignore only positively unsent inputs
    # and this update's own deterministic retained-outcome message.
    return next((message['inputId'] for message in reversed(session.get('messages', []))
        if message.get('role') == 'user' and message.get('inputId')
        and message.get('delivery', {}).get('status') not in {'failed', 'held'}
        and not (excluding_request and (message.get('peerEnvelope') or {}).get('replyToRequestId') == excluding_request)), None)


def continuation_guard(service, request):
    row = request['updateYield']
    if not same_owner(service, row):
        return 'Stop, assignment or execution placement changed after the update boundary.'
    session = service._session(row['sessionId'])
    if 'generationId' in row and (session.get('collaborationGeneration') or {}).get('id') != row['generationId']:
        return 'New input has already continued this conversation after its saved update boundary.'
    if 'lastInputId' in row and latest_input(session, request.get('requestId')) != row['lastInputId']:
        return 'New input has already been saved after this conversation’s update boundary.'
    task = session.get('task') or service.coordination.task(row['sessionId'])
    from .task_binding import matches
    if not matches(task, row['taskBinding']) or task.get('status') != 'active':
        return 'The saved task changed or is no longer active.'
    if session.get('configurationBusy'):
        return 'Conversation settings are still being applied.'


def enqueue_goal(service, drain, row):
    goal, task = row.get('yieldedGoal'), row.get('task')
    if not goal or not task or task.get('status') != 'active':
        return
    if goal.get('cap') is not None and goal.get('turns_used', 0) >= goal['cap']:
        return
    sid = row['sessionId']
    session = service._session(sid)
    from .task_binding import continuation_epoch
    proof = {**row, 'taskBinding': {'taskId': task['id'], 'taskRevision': task['revision'],
                                  'taskContinuationEpoch': continuation_epoch(task)}}
    if continuation_guard(service, {'updateYield': proof}):
        return
    # Existing messages take precedence, without minting another wakeup.
    from .message_delivery import find_message
    if any(owner == sid and (find_message(session, identity) or {}).get('delivery', {}).get('status')
           in {'accepted', 'sending', 'unknown', 'queued_for_update'} for owner, identity in drain['messages']):
        return
    for identity, encoded in service.collaboration.fair_rows(sid):
        receipt = json.loads(encoded)
        if not service.collaboration.guard(receipt):
            return
    identity = 'update-wait:' + drain['attemptId'] + ':' + sid
    owner = service.collaboration
    saved = service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    if saved:
        request = json.loads(saved[0])
    else:
        from .collaboration import PROTOCOL
        wait = owner.saved_wait(session, identity)
        wait.update(proof['taskBinding'])
        request = {'accepted': True, 'commandAction': 'updates.app', 'requestId': identity,
            'senderSessionId': sid, 'target': {'sessionId': sid}, 'workspace': session['workspace'],
            'origin': 'system', 'protocol': PROTOCOL, 'subscription': wait, 'updateYield': proof,
            'terminalOutcome': {'status': 'observed', 'kind': 'observation', 'outcome': 'update-ready',
                'qualified': False, 'outcomeRevision': 1,
                'text': ('The updated app is ready. ' if drain.get('result') == 'installed' else 'The app update was withdrawn before replacement. ') +
                        'Continue the existing task from its saved progress. '
                        'Keep its used budget and current instructions. Do not repeat previous inputs or completed or uncertain effects.'}}
        owner.insert(identity, identity, request)
        service.db.commit()
    owner.enqueue_continuation(request)


async def finish(manager, reason):
    """Release known unattempted inputs, serially, after this attempt settles."""
    service = manager.service
    drain = service.state['updates'].get('drain')
    if (not drain or drain.get('phase') not in {'draining', 'replacing', 'releasing'}
            or getattr(manager, '_releasing_update_inputs', False)):
        return
    if reason == 'installed':
        result = service.state['updates'].get('applicationResult') or {}
        if any(result.get(key) != drain['target'].get(key) for key in ('attemptId', 'revision')):
            return
    else:
        from .updates import work_paused
        if work_paused(service.state):
            return  # Replacement uncertainty is not a presumed withdrawal.
    manager._releasing_update_inputs = True
    try:
        for sid, row in drain['workers'].items():
            worker = getattr(service.runtime, 'workers', {}).get(sid)
            if worker and worker.get('runtimeOwnerId') == row['owner']:
                await service.runtime.handoff_boundary(sid, row['boundaryId'], cancel=True)
        async with service.lock:
            drain.update(phase='releasing', result=reason)
            service._publish_changes(globals={'updates'})
        from .message_delivery import find_message
        from .execution import ensure_turn
        for sid, identity in drain['messages']:
            async with service.lock:
                session = service._session(sid)
                message = find_message(session, identity)
                if not message or message.get('delivery', {}).get('status') != 'queued_for_update':
                    continue
                if (message['delivery'].get('interruptionRevision') != session.get('interruptionRevision', 0)
                        or service.assignments.blocked(session)):
                    service._delivery(session, identity, 'held')
                    message['delivery']['reason'] = 'Stopped after this message was saved. Nothing was sent.'
                    service._publish_changes(sessions={sid})
                    continue
                # Claim only this first admission before the await. A crash
                # leaves this input uncertain and the remaining inputs queued.
                drain['releasingInput'] = [sid, identity]
                service._delivery(session, identity, 'sending')
                active = session.get('collaborationGeneration') or {}
                if active.get('id') and not active.get('terminal'):
                    message['steering'] = {'generationId': active['id'], 'allowNextTurn': True,
                                           'disposition': 'sending'}
                else:
                    session['status'] = 'working'
                    ensure_turn(session, identity, message['text'])
                service._publish_changes(sessions={sid}, globals={'updates'})
                captured = copy.deepcopy(session)
            try:
                await service._send(captured, message['text'], identity, preserve_draft=True)
            except Exception:
                # _send records its exact failure/unknown disposition. Never
                # retry that input or prevent other saved messages from settling.
                pass
        async with service.lock:
            for row in drain['workers'].values():
                enqueue_goal(service, drain, row)
            drain.pop('releasingInput', None)
            drain['phase'] = 'released'
            service._publish_changes(globals={'updates'})
        targets = service.db.execute("""SELECT DISTINCT json_extract(receipt,'$.target.sessionId') FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.delivery')='queued'""").fetchall()
        for (sid,) in targets:
            service._task(service.collaboration.drain(sid))
    finally:
        manager._releasing_update_inputs = False


async def defer_send(service, sid, identity, preserve_draft=False):
    from .message_delivery import find_message
    async with service.lock:
        session = service._session(sid)
        message = find_message(session, identity)
        if held(service):
            # Adopt the currently owning attempt only while its gate is active.
            # The earlier exception proves no transport attempt has occurred.
            hold_message(service, session, message)
            service._clear_sent_draft(session, message, message['text'], preserve_draft)
            service._publish_changes(sessions={sid}, globals={'updates'})
            return {'accepted': True, 'inputId': identity, 'delivery': 'queued_for_update'}
        # The gate released while we awaited the commit lock. Continue that
        # same first admission; never append to an already released drain.
        active = session.get('collaborationGeneration') or {}
        message.pop('steering', None)
        if active.get('id') and not active.get('terminal'):
            message['steering'] = {'generationId': active['id'], 'allowNextTurn': True,
                                   'disposition': 'sending'}
        captured = copy.deepcopy(session)
    return await service._send(captured, message['text'], identity, preserve_draft=preserve_draft)
