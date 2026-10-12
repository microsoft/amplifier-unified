"""Small observation surface for desktop companions; never hydrates history."""
from aiohttp import web

from .auth import data_identity
from .service import AppError


def snapshot(service):
    state = service.state
    index = service.projections.sessions(state)
    pins = set(state.get('pinnedSessionIds', []))
    roots = lambda sid: sid in index.by_id and index._membership[sid][2]
    active = {sid for sid in index.active if roots(sid)}
    items = []
    for item in service.projections.attention(state)['items']:
        sid = item.get('sessionId')
        if item.get('read') or not sid or not roots(sid):
            continue
        kind = item['id'].split(':', 1)[0]
        if kind not in {'completion', 'question', 'approval', 'session'}:
            continue
        items.append({'id': item['id'], 'fingerprint': item['fingerprint'],
                      'sessionId': sid, 'title': index.by_id[sid].get('title', 'Chat')[:200],
                      'kind': kind, 'summary': item['title'][:200], 'pinned': sid in pins})
    # Completion content, prompts, tool arguments, paths and private client views
    # deliberately do not cross this observation surface.
    items.sort(key=lambda item: (item['kind'] == 'completion', not item['pinned'], item['id']))
    targets = sorted(active | pins, key=lambda sid: (sid not in active, sid))
    sessions = [{'id': sid, 'title': index.by_id[sid].get('title', 'Chat')[:200],
                 'status': index.by_id[sid].get('status', 'idle'), 'pinned': sid in pins}
                for sid in targets if roots(sid)]
    return {'protocolVersion': 1, 'revision': state['revision'],
            'workingCount': len(active), 'attentionCount': len(items),
            'sessions': sessions[:50], 'items': items[:200],
            'sessionsTruncated': len(sessions) > 50, 'itemsTruncated': len(items) > 200}


def setup_routes(app):
    service = app['service']
    identity = data_identity(service.data_dir)

    async def observe(request):
        if service.clients.current.get() is None:
            raise AppError('Attach a client and send X-Amplifier-Client.', 400)
        value = service.projections.get(('companion',), lambda: snapshot(service))
        return web.json_response({**value, 'hostIdentity': identity,
                                  'hostInstanceId': service.instance_id})

    app.router.add_get('/api/companion', observe)
