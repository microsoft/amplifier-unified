"""Trusted frame boundary: CSP restricts navigation of the untrusted child.

An opaque sandbox alone does not prevent its own location from navigating.
Keep authored code in a nested opaque frame whose parent permits only the
exact canvas document path. The outer document contains only host-owned code.
"""
import json
from pathlib import Path
import secrets

from aiohttp import web


def document_response(canvas, request):
    url = request.url.with_path('/api/canvas/' + canvas['id'] + '/document', keep_query=True)
    nonce = secrets.token_urlsafe(24)
    script = (Path(__file__).parent / 'canvas_app_host.js').read_text()
    script = script.replace('__CANVAS_ID__', json.dumps(canvas['id']))
    script = script.replace('__DOCUMENT_URL__', json.dumps(str(url)).replace('<', '\\u003c'))
    html = ('<!doctype html><html><head><meta charset="utf-8">'
            '<style>html,body,iframe{margin:0;border:0;width:100%;height:100%;display:block;overflow:hidden}</style>'
            '</head><body><script nonce="' + nonce + '">' + script + '</script></body></html>')
    return web.Response(text=html, content_type='text/html', headers={
        'Content-Security-Policy': "default-src 'none'; script-src 'nonce-" + nonce + "'; style-src 'unsafe-inline'; "
            'frame-src ' + str(url.with_query(None)) + "; connect-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'",
        'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), clipboard-read=(), clipboard-write=()'})
