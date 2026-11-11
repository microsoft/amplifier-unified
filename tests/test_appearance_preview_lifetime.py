"""A new document never inherits another document's appearance preview."""
from copy import deepcopy

from test_canvas_apps import app, dispatch


async def test_resumed_client_ends_preview_without_changing_source_or_drafts(app):
    committed = app.appearance.shared()
    await dispatch(app, 'view.update', {'patch': {'draft': 'An unfinished conversation', 'navWidth': 230}})
    await dispatch(app, 'appearance.preview', {'name': 'Temporary', 'css': '#amp-one{color:red}'})
    source = deepcopy(app.clients.records['one'])
    preview = deepcopy(app.appearance.read('one')['preview'])
    resumed = app.clients.attach('reloaded', resume='one')
    assert app.appearance.read('reloaded')['preview'] is None
    assert app.appearance.read('one')['preview'] == preview
    assert app.clients.records['one'] == source
    for key in ('draft', 'navWidth'):
        assert resumed['view'][key] == source['view'][key]
    assert resumed['selectedSessionId'] == source['selectedSessionId']
    assert app.appearance.shared() == committed
    await dispatch(app, 'appearance.preview', {'name': 'New preview', 'css': '#amp-one{color:blue}'}, client='reloaded')
    own = app.appearance.read('reloaded')['preview']
    app.clients.attach('reloaded')
    assert app.appearance.read('reloaded')['preview'] == own