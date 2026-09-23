import json
from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService, validate_theme
from test_canvas_apps import app, dispatch


async def test_bundled_library_previews_applies_and_reverts_without_changing_chat(app):
    original = dict(app.state['theme'])
    sid = app.state['selectedSessionId']
    listing = (await dispatch(app, 'theme.list', {}))['result']
    assert [row['name'] for row in listing['items']] == ['Amplifier', 'Aurora', 'Atelier', 'Graphite']
    assert not Path(listing['directory']).exists(), 'Browsing does not allocate user storage'
    for row in listing['items']:
        assert 'css' not in row
        value = (await dispatch(app, 'theme.read', {'id': row['id']}))['result']
        validate_theme(value['css'])
        await dispatch(app, 'theme.preview', {'name': value['name'], 'css': value['css']})
        assert app.state['theme'] == original
        assert not app.clients.records['two']['view'].get('themePreview')
        await dispatch(app, 'theme.apply', {'name': value['name'], 'css': value['css']})
        assert app.state['theme']['css'] == value['css']
        await dispatch(app, 'theme.revert', {})
        assert app.state['theme'] == original
    assert app.state['selectedSessionId'] == sid


async def test_saved_appearance_survives_restart_and_external_files_are_discovered(app, tmp_path):
    args = {'name': 'A shared look', 'css': '#amp-one{--a-accent:#123456}', 'description': 'Personal colors'}
    result = (await dispatch(app, 'theme.save', args, origin='agent'))['result']
    duplicate = (await dispatch(app, 'theme.save', args))['result']
    assert duplicate['id'] == result['id']
    root = Path(result['directory'])
    assert len(list(root.glob('*.css'))) == 1
    external = root / 'From a friend.amplifier.css'
    external.write_text('#amp-one{--a-accent:#654321}')
    listing = (await dispatch(app, 'theme.list', {}))['result']
    assert {r['name'] for r in listing['items']} >= {'A shared look', 'From a friend'}
    await app.close()
    restored = AppService(app.data_dir, workspace=tmp_path)
    try:
        value = (await restored.dispatch('theme.read', {'id': result['id']}))['result']
        assert value['name'] == args['name'] and value['css'].endswith(args['css'])
    finally:
        await restored.close()


async def test_bad_imports_do_not_replace_library_or_current_appearance(app, tmp_path):
    original = dict(app.state['theme'])
    with pytest.raises(AppError):
        await dispatch(app, 'theme.save', {'name': 'External', 'css': '@import "https://example.com/style.css";'})
    with pytest.raises(AppError):
        await dispatch(app, 'theme.read', {'id': 'saved:../outside.css'})
    root = app.data_dir / 'themes'
    root.mkdir()
    outside = tmp_path / 'outside.css'
    outside.write_text('#amp-one{color:red}')
    (root / 'linked.css').symlink_to(outside)
    (root / 'bad.css').write_text('@import "https://example.com/bad.css";')
    listing = (await dispatch(app, 'theme.list', {}))['result']
    assert len(listing['items']) == 4 and len(listing['errors']) == 2
    assert app.state['theme'] == original


async def test_name_cannot_escape_metadata_comment_and_reset_ends_preview(app):
    saved = (await dispatch(app, 'theme.save', {'name': 'Look */ unusual', 'css': '#amp-one{color:red}'}))['result']
    row = (await dispatch(app, 'theme.read', {'id': saved['id']}))['result']
    assert row['name'] == 'Look */ unusual'
    validate_theme(row['css'])
    await dispatch(app, 'theme.preview', {'name': row['name'], 'css': row['css']})
    await dispatch(app, 'theme.reset', {})
    assert app.clients.records['one']['view']['themePreview'] is False
