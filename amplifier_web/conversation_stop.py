"""Passive receipt recovery for the bundled client's exact Stop command."""
import json


def stop_status(service, args):
    """Caller holds service.lock. Acceptance never means execution has ended."""
    from .service import AppError, command_fingerprint

    if (not isinstance(args, dict) or set(args) != {'sessionId', 'commandId', 'clientId'}
            or any(not isinstance(args[key], str) or not 0 < len(args[key]) <= limit
                   for key, limit in [('sessionId', 200), ('commandId', 200), ('clientId', 100)])):
        raise AppError('Choose an exact Stop command and its originating client.')
    # client_context binds an attached header/query identity. Do not accept a
    # body claiming a different browser, or treat that identity as authentication.
    client_id = service.clients.current.get()
    if client_id is None:
        raise AppError('Attach a client and send X-Amplifier-Client.')
    if client_id != args['clientId']:
        raise AppError('This Stop lookup belongs to a different client.', 409)

    service._session(args['sessionId'], hydrate=False)
    previous = service.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?',
                                  (args['commandId'],)).fetchone()
    if previous and previous[0] != command_fingerprint(
            'conversation.stop', {'sessionId': args['sessionId']}, 'ui', args['clientId']):
        raise AppError('This receipt does not belong to that Stop command.', 409)
    result = {**args, 'status': 'unknown'}
    if previous:
        receipt = json.loads(previous[1])
        if isinstance(receipt, dict) and receipt.get('accepted') is True:
            # The same connection can see a receipt from a failed save. As for
            # reaction_status, commit pending progress under the caller's lock,
            # without an await, before allowing that receipt to settle an intent.
            if getattr(service, '_progress_dirty', False):
                service._commit_pending_progress()
            result['status'] = 'accepted'
    return result


def install_routes(app):
    from aiohttp import web

    async def status(request):
        args = await request.json()
        # Keep the common authentication, origin and attached-client middleware.
        async with app['service'].lock:
            result = stop_status(app['service'], args)
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    app.router.add_post('/api/conversation/stop-status', status)