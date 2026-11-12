"""New documents restore confirmed profiles and drafts, never temporary previews."""
from copy import deepcopy

from test_appearance_preferences import app, call, report, update, ack


async def test_resumed_client_ends_preview_without_changing_source_or_drafts(app):
    await update(app, {'scheme': 'dark'}, scope='client')
    await ack(app)
    confirmed = deepcopy(app.appearance.read('one'))
    await call(app, 'view.update', {'patch': {'draft': 'An unfinished conversation', 'navWidth': 230}})
    await call(app, 'appearance.preview', {'name': 'Temporary', 'css': '#amp-one{color:red}'})
    source = deepcopy(app.clients.records['one'])
    preview = deepcopy(app.appearance.read('one')['preview'])
    resumed = app.clients.attach('reloaded', resume='one')
    assert app.appearance.read('reloaded')['preview'] is None
    assert app.appearance.read('one')['preview'] == preview
    assert app.clients.records['one'] == source
    for key in ('draft', 'navWidth'):
        assert resumed['view'][key] == source['view'][key]
    assert resumed['selectedSessionId'] == source['selectedSessionId']
    await report(app, client='reloaded', activeScope='client',
                 values=confirmed['local']['values'], storageVersion=confirmed['local']['storageVersion'])
    assert app.appearance.read('reloaded')['effective'] == confirmed['effective']
    await call(app, 'appearance.preview', {'name': 'New preview', 'css': '#amp-one{color:blue}'}, client='reloaded')
    active = deepcopy(app.appearance.read('reloaded')['preview'])
    app.clients.attach('reloaded')
    assert app.appearance.read('reloaded')['preview'] == active
