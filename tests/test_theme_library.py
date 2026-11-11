"""ZIP-only Reference library ownership, independent of scoped selection."""
import base64
from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService, ACTION_DEFINITIONS
from amplifier_web import appearance_packages as packages, theme_library
from test_canvas_apps import app, dispatch


async def save(app, name='Paper', css='#amp-one{--a-accent:#123456}'):
    return (await dispatch(app, 'theme.save', {'name': name, 'css': css}))['result']


async def test_builtins_are_external_deltas_and_reference_is_advanced_not_a_gallery_card(app):
    listing = (await dispatch(app, 'theme.list', {}))['result']
    assert [row['id'] for row in listing['items']] == ['builtin:' + key for key in theme_library.BUILTINS]
    assert not listing['errors'] and not Path(listing['directory']).exists()
    for item in listing['items']:
        row = (await dispatch(app, 'theme.read', {'id': item['id']}))['result']
        source = Path(theme_library.__file__).parent / 'appearances' / (item['id'][8:] + '.amplifier.css')
        assert row['css'] == source.read_text() and row['base'] == 'reference-v1'
        assert row['fingerprint'] == item['fingerprint'] and 'css' not in item
    advanced = (await dispatch(app, 'appearance.preferences.update', {
        'scope': 'shared', 'expectedRevision': 0, 'patch': {'appearanceId': 'reference'}}))['result']
    assert advanced['stored'] and not advanced['activated']
    assert app.appearance.read('one')['theme']['id'] == 'reference'


async def test_named_save_zip_export_and_import_do_not_select_or_change_preferences(app):
    before = app.appearance.shared()
    saved = await save(app, name='Unusual */ name')
    assert saved['id'].endswith('.zip') and 'css' not in saved
    read = (await dispatch(app, 'theme.read', {'id': saved['id']}))['result']
    assert read['name'] == 'Unusual */ name' and read['css'] == '#amp-one{--a-accent:#123456}'
    exported = (await dispatch(app, 'theme.export', {'id': saved['id']}))['result']
    package = packages.load(base64.b64decode(exported['base64']))
    assert package.manifest['formatVersion'] == 2 and package.manifest['base'] == 'reference-v1'
    repeated = (await dispatch(app, 'theme.import', {'packageBase64': exported['base64']}))['result']
    assert repeated['id'] == saved['id']
    renamed = (await dispatch(app, 'theme.import', {'packageBase64': exported['base64'], 'name': 'Copy'}))['result']
    assert renamed['id'] != saved['id'] and renamed['name'] == 'Copy'
    assert app.appearance.shared() == before


async def test_update_delete_cas_and_missing_selection_recovery_survive_restart(app, tmp_path):
    saved = await save(app)
    await dispatch(app, 'appearance.preferences.update', {'scope': 'shared', 'expectedRevision': 0,
        'patch': {'appearanceId': saved['id'], 'scheme': 'dark'}})
    before = app.appearance.shared()
    old_identity = app.appearance.read('one')['composition']['id']
    updated = (await dispatch(app, 'theme.update', {'id': saved['id'], 'expectedFingerprint': saved['fingerprint'],
        'name': 'Renamed', 'css': '#amp-one{--a-accent:#654321}'}))['result']
    assert updated['id'] == saved['id'] and updated['fingerprint'] != saved['fingerprint']
    assert app.appearance.read('one')['composition']['id'] != old_identity
    for action in ('theme.update', 'theme.delete'):
        args = {'id': saved['id'], 'expectedFingerprint': saved['fingerprint']}
        if action == 'theme.update':
            args.update(name='Stale', css='#amp-one{color:red}')
        with pytest.raises(AppError) as error:
            await dispatch(app, action, args)
        assert error.value.status == 409
    await dispatch(app, 'theme.delete', {'id': saved['id'], 'expectedFingerprint': updated['fingerprint']})
    current = app.appearance.read('one')
    assert app.appearance.shared() == before and current['theme']['id'] == saved['id']
    assert current['theme']['available'] is False and current['theme']['css'] == ''
    assert current['theme']['recoveryId'] == 'reference'
    await app.close()
    restored = AppService(app.data_dir, workspace=tmp_path)
    try:
        assert restored.appearance.shared() == before
        assert restored.appearance.read()['theme']['available'] is False
        assert 'theme' not in restored._state
    finally:
        await restored.close()


@pytest.mark.parametrize('args', [{}, {'format': 'css'}, {'css': '#amp-one{}'}, {'id': 'builtin:default', 'format': 'zip'}])
async def test_export_requires_exact_library_id_and_has_no_format_branch(app, args):
    with pytest.raises(AppError):
        await dispatch(app, 'theme.export', args)


async def test_legacy_files_ignored_not_converted_or_deleted_and_no_legacy_actions(app):
    root = theme_library.folder(app)
    root.mkdir()
    legacy = root / 'unrelated.amplifier.css'
    legacy.write_text('#amp-one{color:red}')
    value = (await dispatch(app, 'theme.list', {}))['result']
    assert not any(row['id'].endswith('.css') for row in value['items'])
    assert legacy.read_text() == '#amp-one{color:red}'
    assert not {'theme.apply', 'theme.preview', 'theme.reset', 'theme.revert'} & ACTION_DEFINITIONS.keys()
    for action in ('theme.read', 'theme.delete'):
        with pytest.raises(AppError):
            await dispatch(app, action, {'id': 'saved:' + legacy.name,
                **({'expectedFingerprint': '0' * 64} if action.endswith('delete') else {})})


async def test_selected_resolution_reuses_only_exact_bytes_not_mtime_size_or_history(app, monkeypatch):
    saved = await save(app)
    await dispatch(app, 'appearance.preferences.update', {'scope': 'shared', 'expectedRevision': 0,
        'patch': {'appearanceId': saved['id']}})
    app._resolved_appearances.clear()
    calls, original = [], packages.load
    def counted(raw):
        calls.append(len(raw))
        return original(raw)
    monkeypatch.setattr(packages, 'load', counted)
    for _ in range(4):
        app.appearance.read('one')
    assert len(calls) == 1
    path = Path(saved['directory']) / saved['id'][6:]
    previous = path.read_bytes()
    package = original(previous)
    changed = packages.with_metadata(package, css='#amp-one{--a-accent:#654321}')
    path.write_bytes(packages.encode(changed.files))
    app.appearance.read('one')
    assert len(calls) == 2 and len(app._resolved_appearances) <= 8
    theme_library.read(app, saved['id'])
    assert len(calls) == 3, 'An explicit full read must revalidate even warm selected bytes'