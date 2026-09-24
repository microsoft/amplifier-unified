"""An inert exact-result candidate never grants authority to change a client."""
import copy
from urllib.parse import urlsplit

from amplifier_scheduling.store import fingerprint
from .observation_contract import text


def parsed_url(value):
    text(value, 'presentation URL', 4000)
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or '\\' in value or any(ord(c) < 33 for c in value)):
        raise ValueError('Presentation needs an http(s) URL without credentials')
    parsed.port  # Reject malformed/out-of-range ports, including in an origin.
    return parsed


def request(value):
    if value is None: return None
    if not isinstance(value, dict) or value.get('kind') != 'browser':
        raise ValueError('Only an explicitly requested browser presentation is supported')
    if set(value) == {'kind', 'urlPolicy'} and value['urlPolicy'] == 'loopback-with-explicit-port':
        return copy.deepcopy(value)
    if set(value) != {'kind', 'urlOrigins'} or not isinstance(value['urlOrigins'], list) or not 1 <= len(value['urlOrigins']) <= 8:
        raise ValueError('Review exact URL origins or the explicit-port loopback policy')
    for origin in value['urlOrigins']:
        parsed = parsed_url(origin)
        if parsed.path or parsed.query or parsed.fragment or '*' in origin:
            raise ValueError('Presentation origins cannot contain paths, queries, fragments or wildcards')
        if origin != parsed.scheme + '://' + parsed.netloc:
            raise ValueError('Use the exact normalized presentation origin')
    return copy.deepcopy(value)


def candidate(value, outcome):
    if value is None: return None
    if (outcome['status'] != 'actionable' or not isinstance(value, dict)
            or set(value) != {'kind', 'url', 'title', 'target', 'evidence'} or value['kind'] != 'browser'
            or value['target'] != outcome['target'] or value['evidence'] not in outcome['evidence']):
        raise ValueError('Presentation must identify the exact accepted target and evidence')
    parsed_url(value['url']); text(value['title'], 'presentation title', 200)
    return copy.deepcopy(value)


def permitted(value, policy):
    parsed = parsed_url(value['url'])
    if policy.get('urlPolicy') == 'loopback-with-explicit-port':
        return parsed.hostname in {'localhost', '127.0.0.1', '::1'} and parsed.port is not None
    return parsed.scheme + '://' + parsed.netloc in policy['urlOrigins']


def connection_binding(app, client, sid):
    # A disconnect/reconnect, including leave-and-return, cannot revive a grant.
    queues = sorted(app.queue_tokens[q] for q in app.queues if q in app.queue_tokens and app.queue_clients.get(q) == client and app.queue_sessions.get(q) in (None, sid))
    if not queues: raise ValueError('The original presentation client is disconnected')
    return {'hostInstance': app.instance_id, 'connections': queues}


def grant(observations, sid, args, origin):
    policy = request(args.get('presentationRequest'))
    if policy is None: return None
    app = observations.app
    source = next((m for m in app._session(sid).get('messages', []) if m.get('id') == args.get('sourceMessageId')), None)
    if (not source or source.get('role') != 'user' or source.get('inputOrigin') not in {'ui', 'user', 'voice'}
            or source.get('scheduledRunId') or source.get('observationId') or source.get('questionId')):
        raise ValueError('Presentation preview requires the actual human sourceMessageId')
    latest = next((m for m in reversed(app._session(sid).get('messages', []))
                   if m.get('role') == 'user' and m.get('inputOrigin') in {'ui', 'user', 'voice'} and not m.get('questionId')), None)
    if latest is not source: raise ValueError('Presentation requires the latest human request')
    client = app.clients.current.get()
    if origin != 'ui':
        bindings = observations.input_bindings.get() or []
        clients = {b['clientId'] for b in bindings if b.get('inputId') == source.get('inputId') and b.get('inputId') and b.get('clientId')}
        if len(clients) != 1 or client not in clients:
            raise ValueError('Presentation requires trusted input-to-client provenance; agent supplied client IDs do not grant it')
    if not client: raise ValueError('Presentation needs the original connected human client')
    from .agent_canvas import target
    target(app, sid, client, required=True, connected_only=True)
    return {'clientId': client, 'sourceMessageId': source['id'], 'inputId': source.get('inputId'),
            'textDigest': fingerprint(source.get('text', '')),
            'selectionRevision': app.clients.selection_revision(client), 'connection': connection_binding(app, client, sid), 'policy': policy}


def check_grant(observations, watch):
    app, value = observations.app, watch.get('presentationGrant')
    if not value or observations.clock() >= watch['expiresAt']:
        raise ValueError('Presentation authority is absent or expired')
    from amplifier_scheduling.store import fingerprint
    source = next((m for m in app._session(watch['sessionId']).get('messages', []) if m.get('id') == value['sourceMessageId']), None)
    if not source or source.get('inputId') != value['inputId'] or fingerprint(source.get('text', '')) != value['textDigest']:
        raise ValueError('The original presentation request changed')
    from .agent_canvas import target
    client = value['clientId']
    target(app, watch['sessionId'], client, required=True, connected_only=True)
    if (connection_binding(app, client, watch['sessionId']) != value['connection']
            or app.clients.selection_revision(client) != value['selectionRevision']):
        raise ValueError('The original client connection or selection changed')
    return value


async def present(observations, watch, item):
    """Called once for a terminal outbox. No source URL is fetched by the host."""
    app, store, now = observations.app, observations.store, observations.clock
    value = item['outcome'].get('presentation')
    if not value or not watch.get('presentationGrant'): return
    if item.get('presentationPhase'): return  # Includes unknown/claimed; never replay.
    try:
        check_grant(observations, watch)
        if not permitted(value, watch['presentationGrant']['policy']): raise ValueError('Unapproved presentation origin')
        def guard():
            current = store.get('watch', watch['id'])
            if current['status'] != 'ended' or current['revision'] != watch['revision']:
                raise ValueError('The watch changed before presentation')
            reason = observations.reason(current)
            if reason: raise ValueError(reason)
            check_grant(observations, current)
        fresh = await observations.read(watch, item['id'] + ':presentation', guard)
        # Heartbeats are immaterial; selected evidence, revision and semantic identity are not.
        for key in ('status', 'target', 'source', 'semanticKey', 'presentation'):
            if fresh.get(key) != item['outcome'].get(key): raise ValueError('The accepted source result changed')
        async with app.lock:
            guard()
            with app.clients.bind(watch['presentationGrant']['clientId']):
                app.canvas_views.guard_transition('canvas.show', {'sessionId': watch['sessionId']})
                claimed = store.outbox_phase(item['id'], ['submitting'], 'submitting', now(), presentationPhase='claimed')
                if not claimed: return
                from .workspace_canvas import canvas_command
                from .canvas_library import remember
                from .agent_canvas import scope
                sid = watch['sessionId']
                scoped = {**app.state, 'selectedSessionId': sid, 'selectedWorkspaceId': scope(app, sid)[1]}
                canvas_command(scoped, 'canvas.show', {'kind':'browser', 'url':value['url'], 'title':value['title']}, 'observation')
                remember(scoped, app.db)
                app.state['canvas'] = scoped['canvas']
                app.state['view'].setdefault('canvasDraft', {}).update(library=False, open=False, browser=False)
                app._publish()
                store.outbox_phase(item['id'], ['submitting'], 'submitting', now(), presentationPhase='opened', canvasId=scoped['canvas']['id'])
    except Exception:
        # A claim may already have reached Canvas. Preserve uncertainty rather than retry.
        current = store.get('outbox', item['id'])
        phase = 'unknown' if current.get('presentationPhase') == 'claimed' else 'skipped'
        store.outbox_phase(item['id'], ['submitting'], 'submitting', now(), presentationPhase=phase,
                           presentationDetail='Opening was not confirmed. Use the recorded reference; no automatic retry.')
