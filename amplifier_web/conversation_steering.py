"""Human corrections belong to one live generation, never an implicit next run."""
import copy
import json

from .message_delivery import find_message


def target(session, expected=None):
    from .service import AppError
    active = session.get('collaborationGeneration') or {}
    identity = active.get('id') if not active.get('terminal') else None
    if expected is not None and identity != expected:
        raise AppError('The run you were steering has ended or changed. Your correction was not sent.',
                       409, code='steering_target_changed')
    return identity


def record(service, session, input_id, disposition, reason=None):
    message = find_message(session, input_id)
    if not message or not message.get('steering'):
        return
    steering = message['steering']
    # Runtime evidence can arrive before the transport acknowledgement.
    if steering['disposition'] == 'applied' or (
            steering['disposition'] in {'held', 'unknown'} and disposition == 'queued'):
        return
    steering['disposition'] = disposition
    if reason:
        steering['reason'] = reason
    else:
        steering.pop('reason', None)
    delivery = 'accepted' if disposition in {'queued', 'applied'} else 'failed' if disposition == 'held' else 'unknown'
    message['delivery'] = {'status': delivery}
    row = service.db.execute('SELECT receipt FROM commands WHERE id=?', (input_id,)).fetchone()
    if row:
        receipt = json.loads(row[0])
        receipt.update(delivery=delivery, steering=copy.deepcopy(steering))
        service.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(receipt), input_id))


def observe(service, session, kind, payload):
    if kind == 'runtime.error' or (kind == 'runtime.generation' and
            payload.get('event') in {'generation.finished', 'generation.failed', 'generation.detached'} and
            payload.get('sessionId', session['id']) == session['id'] and
            payload.get('rootSessionId', session['id']) == session['id']):
        for message in session.get('messages', []):
            steering = message.get('steering') or {}
            if steering.get('disposition') not in {'sending', 'queued', 'unknown'}:
                continue
            if kind == 'runtime.generation' and steering.get('generationId') != payload.get('generation_id'):
                continue
            applied = kind == 'runtime.generation' and message['inputId'] in payload.get('input_ids', [])
            record(service, session, message['inputId'], 'applied' if applied else 'unknown',
                   None if applied else 'The run ended before steering delivery was confirmed. Nothing was resent.')
        return
    if kind != 'runtime.steering':
        return
    message = find_message(session, payload.get('input_id'))
    if not message or (message.get('steering') or {}).get('generationId') != payload.get('target_generation_id'):
        return
    disposition = {'steering.applied': 'applied', 'steering.held': 'held',
                   'steering.unknown': 'unknown'}.get(payload.get('event'))
    if disposition:
        record(service, session, message['inputId'], disposition, payload.get('reason'))


async def send(service, session, text, input_id, preserve_draft):
    message = find_message(session, input_id)
    try:
        sender = getattr(service.runtime, 'steer', None)
        if sender is None:
            result = {'accepted': False, 'reason': 'This runtime does not support steering an active run.'}
        else:
            result = await sender(session, text, input_id, service.on_runtime_event)
        if not isinstance(result, dict) or type(result.get('accepted')) is not bool:
            raise RuntimeError('Steering acknowledgement was not recognized.')
        disposition = result.get('disposition', 'queued') if result['accepted'] else 'held'
        if disposition not in {'queued', 'applied', 'held', 'unknown'}:
            raise RuntimeError('Steering disposition was not recognized.')
        reason = result.get('reason')
    except Exception:
        # A lost acknowledgement says nothing about effects. Do not mark the
        # running conversation failed, restart its worker, or replay the input.
        disposition, reason = 'unknown', 'Delivery could not be confirmed. This correction will not be sent again automatically.'
    async with service.lock:
        current = service._session(session['id'])
        record(service, current, input_id, disposition, reason)
        saved = find_message(current, input_id)
        if saved['steering']['disposition'] in {'queued', 'applied'}:
            service._clear_sent_draft(current, message, text, preserve_draft)
        service._publish()
        return {'delivery': saved['delivery']['status'], 'steering': copy.deepcopy(saved['steering'])}


async def submit(controls, runtime, args, activation, stop_epoch):
    """Worker boundary: preserve normal input preparation and exact ownership."""
    from .collaboration_input import _steering_capability
    from .message_interactions import prepare_input
    from amplifier_module_loop_live.runtime import Input

    epoch = stop_epoch()
    text = await prepare_input(controls.coordinator, args['text'], args.get('reply_context'),
                               max_chars=runtime.max_input_chars)
    capability = _steering_capability(controls)
    if capability is None:
        return {'accepted': False, 'reason': 'This runtime does not support steering an active run.'}
    if (runtime.closed or stop_epoch() != epoch or
            (runtime.generation or {}).get('id') != args['targetGenerationId']):
        return {'accepted': False, 'reason': 'The run you were steering has ended or changed.'}
    # The capability checks the generation again before admission. User input
    # does not use the automatic-task continuation guard: a paused task must
    # still be able to receive the user's correction during its current run.
    return await capability['submit'](Input('steer', text, id=args['inputId'],
        attachments=tuple(args.get('attachments', [])), activation=activation), args['targetGenerationId'])
