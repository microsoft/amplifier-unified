"""A new document restores presentation/drafts, never a previous theme preview."""
from copy import deepcopy

import pytest

from test_canvas_apps import app, dispatch


async def test_resumed_client_ends_theme_preview_without_changing_source_or_drafts(app):
    committed = deepcopy(app.state['theme'])
    await dispatch(app, 'view.update', {'patch': {
        'draft': 'An unfinished conversation', 'scheme': 'dark', 'navWidth': 230,
    }})
    await dispatch(app, 'theme.preview', {'name': 'Temporary', 'css': '#amp-one{color:red}'})
    source = deepcopy(app.clients.records['one'])
    resumed = app.clients.attach('reloaded', resume='one')
    assert resumed['view']['themePreview'] is False
    assert app.clients.records['one'] == source, 'Another document keeps its own active preview'
    for key in ('draft', 'scheme', 'navWidth', 'themeDraft', 'themeDraftName'):
        assert resumed['view'][key] == source['view'][key]
    assert resumed['selectedSessionId'] == source['selectedSessionId']
    assert app.state['theme'] == committed
    # Reattaching the same live document must not end a newly requested preview.
    await dispatch(app, 'theme.preview', {'name': 'New preview', 'css': '#amp-one{color:blue}'}, client='reloaded')
    assert app.clients.attach('reloaded')['view']['themePreview'] is True