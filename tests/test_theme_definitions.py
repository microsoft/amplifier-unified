from copy import deepcopy

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.themes import TOKENS
from test_canvas_apps import app, dispatch, create, edit


def definition():
    return {'version': 1, 'palette': {
        'light': {key: '#eef8f6' if key == 'bg' else '#245b53' for key in TOKENS},
        'dark': {key: '#102c28' if key == 'bg' else '#b4e4d8' for key in TOKENS}},
        'background': {'light': 'linear-gradient(135deg, #b4e4d8, #eef8f6)',
                       'dark': 'radial-gradient(ellipse at top, #245b53, #102c28)'}}


async def test_complete_theme_preview_apply_revert_and_restart(app, tmp_path):
    before = deepcopy(app.state['theme'])
    await dispatch(app, 'view.update', {'patch': {'draft': 'Unsent draft'}})
    value = definition()
    await dispatch(app, 'theme.preview', {'name': 'Quiet water', 'definition': value})
    preview = app.clients.records['one']['view']['themeDraft']
    assert app.state['theme'] == before
    assert not app.clients.records['two']['view'].get('themePreview')
    await dispatch(app, 'theme.apply', {'name': 'Quiet water', 'definition': value})
    assert app.state['theme']['css'] == preview
    assert app.state['theme']['definition'] == value
    assert app.clients.records['one']['view']['draft'] == 'Unsent draft'
    applied = deepcopy(app.state['theme'])
    await app.close()
    restored = AppService(app.data_dir, workspace=tmp_path)
    try:
        assert restored.state['theme'] == applied
        assert restored.clients.records['one']['view']['draft'] == 'Unsent draft'
        await dispatch(restored, 'theme.revert', {})
        assert restored.state['theme'] == before
    finally:
        await restored.close()


async def test_whole_theme_does_not_inherit_previous_background(app):
    await dispatch(app, 'theme.apply', {'name': 'Old', 'css': '#amp-one{background-image:linear-gradient(red, purple)}'})
    value = definition()
    value.pop('background')
    await dispatch(app, 'theme.apply', {'name': 'Flat water', 'definition': value})
    assert 'linear-gradient(red, purple)' not in app.state['theme']['css']
    assert app.state['theme']['css'].count('background-image:none') == 2
    assert 'data-theme-scheme="dark"' in app.state['theme']['css']


async def test_palette_patch_preserves_definition_without_css_growth(app):
    await dispatch(app, 'theme.apply', {'name': 'Water', 'definition': definition()})
    for index in range(10):
        await dispatch(app, 'theme.apply', {'name': 'Water', 'tokens': {'accent': '#123456'}})
    assert app.state['theme']['definition']['background'] == definition()['background']
    assert app.state['theme']['definition']['palette']['dark']['accent'] == '#123456'
    assert app.state['theme']['css'].count('[data-theme-scheme="dark"]') == 1
    await dispatch(app, 'theme.apply', {'name': 'Legacy', 'css': '#amp-one{font-size:18px}'})
    for index in range(10):
        await dispatch(app, 'theme.apply', {'name': 'Legacy', 'tokens': {'accent': '#123456'}})
    await dispatch(app, 'theme.apply', {'name': 'Legacy', 'tokens': {'ink': '#abcdef'}})
    assert app.state['theme']['css'].count('palette-patch:start') == 1
    assert '--a-accent:#123456' in app.state['theme']['css']
    assert '--a-ink:#abcdef' in app.state['theme']['css']


@pytest.mark.parametrize('background', ['url(https://example.com/art.png)', 'none;position:fixed',
    'none}body{display:none}', 'url(data:image/svg+xml;base64,PHN2Zz4=)', 'none!important', '</style>'])
async def test_background_rejects_external_artwork_and_rule_injection(app, background):
    value = definition()
    value['background']['light'] = background
    before = deepcopy(app.state['theme'])
    with pytest.raises(AppError):
        await dispatch(app, 'theme.apply', {'name': 'Bad background', 'definition': value})
    assert app.state['theme'] == before


async def test_structured_canvas_request_uses_same_compiled_theme(app):
    row = await create(app)
    row = await edit(app, row, 'request', name='apply', input={'name': 'Water', 'definition': definition()})
    row = await edit(app, row, 'resolve', requestId=row['app']['requests'][-1]['id'], approve=True)
    assert app.state['theme']['definition'] == definition()
    assert row['app']['requests'][-1]['status'] == 'applied'


async def test_decoration_is_client_local_and_retains_theme(app):
    await dispatch(app, 'theme.apply', {'name': 'Water', 'definition': definition()})
    before = deepcopy(app.state['theme'])
    client = app.shell.client('one')
    composition = deepcopy(client['composition'])
    composition['presentation']['decorations'] = False
    prepared = await dispatch(app, 'shell.changes.prepare', {'clientId': 'one', 'expectedRevision': 0, 'composition': composition})
    await dispatch(app, 'shell.changes.apply', {'clientId': 'one', 'expectedRevision': 0, 'changeId': prepared['result']['id']})
    assert app.shell.client('one')['composition']['presentation']['decorations'] is False
    assert 'decorations' not in app.shell.client('two')['composition']['presentation']
    assert app.state['theme'] == before
