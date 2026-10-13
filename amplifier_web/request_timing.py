"""Bounded request metadata for support, without query strings or payloads."""
from collections import deque
import time
import uuid
from aiohttp import web


@web.middleware
async def request_timing(request, handler):
    started = time.monotonic()
    identity = uuid.uuid4().hex
    response = None
    try:
        response = await handler(request)
        if request.path.startswith('/api/') and not response.prepared:
            response.headers['Server-Timing'] = f'app;dur={(time.monotonic() - started) * 1000:.2f}'
            response.headers['X-Amplifier-Request-Id'] = identity
        return response
    finally:
        service = request.app.get('service')
        if service is not None and request.path.startswith('/api/') and not (response is not None and response.prepared):
            rows = getattr(service, 'request_timings', None)
            if rows is None:
                rows = service.request_timings = deque(maxlen=60)
            route = request.match_info.route.resource
            rows.append({'id': identity, 'at': time.time(), 'method': request.method,
                'route': route.canonical if route is not None else 'unmatched',
                'elapsedMs': round((time.monotonic() - started) * 1000, 2),
                'status': response.status if response is not None else None})
