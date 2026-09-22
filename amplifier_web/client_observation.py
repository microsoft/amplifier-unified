"""Renderer evidence belongs to an attached view, not the conversation catalog."""
import copy
import json


def update(service, action, args, command_id, fingerprint, *, include_state):
    identity = service.clients.current.get()
    client = service.clients.record()
    previous = copy.deepcopy(client)
    revision = service._state['revision']
    changed = False
    try:
        if action == 'canvas.views.status':
            _, preference = service.canvas_views.target(args)
            activation = {'status': args['status'], 'message': args['message']}
            changed = preference.get('activation') != activation
            preference['activation'] = activation
            result = {'status': 'updated'}
        else:
            # This validates generation/content/edit order and satisfies any
            # explicit checkpoint. Its bounded evidence remains agent-readable.
            result = service.surface_context.observe(args)
        receipt = {'accepted': True, 'revision': revision + int(changed), 'effects': [], 'result': result}
        if command_id:
            service.db.execute('INSERT INTO commands VALUES (?,?,?)', (command_id, fingerprint, json.dumps(receipt)))
        if changed:
            service._state['revision'] = revision + 1
            service.clients.save(identity)
        service.db.commit()
    except Exception:
        service.db.rollback()
        service.clients.records[identity] = previous
        service.clients.saved.pop(identity, None)
        service.clients.dirty.add(identity)
        service._state['revision'] = revision
        raise
    if changed:
        service._client_snapshots.pop(identity, None)
        for key, snapshot in list(service._client_snapshots.items()):
            if snapshot['revision'] == revision:
                service._client_snapshots[key] = {**snapshot, 'revision': revision + 1}
        for queue in service.queues:
            if service.queue_clients.get(queue) != identity:
                continue
            session_id = service.queue_sessions.get(queue)
            snapshot = service.session_state(session_id) if session_id is not None else service.browser_state()
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(snapshot)
    return {**receipt, **({'state': service.browser_state()} if include_state else {})}
