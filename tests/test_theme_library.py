"""Reference library lifecycle; archive/security cases live in appearance_packages."""
import base64
from copy import deepcopy
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService, validate_theme
from amplifier_web import appearance_packages as packages, theme_library
from test_appearance_preferences import app, call, update


async def save(app, name='A shared look', css='#amp-one{--a-accent:#123456}'):
    return await call(app, 'theme.save', {'name': name, 'css': css})


async def export(app, row):
    return await call(app, 'theme.export', {'id': row['id']})


async def test_bundled_library_preview_select_cancel_does_not_change_chat(app):
    sid = app.clients.records['one']['selectedSessionId']
    listing = await call(app, 'theme.list')
    assert [row['name'] for row in listing['items']] == ['Amplifier', 'Aurora', 'Atelier', 'Graphite', 'Digital Rain', 'Lightline', 'Breadbin']
    assert not Path(listing['directory']).exists()
    for row in listing['items']:
        assert 'css' not in row
        value = await call(app, 'theme.read', {'id': row['id']})
        assert row['fingerprint'] == value['fingerprint']
        validate_theme(value['css'])
        before = deepcopy(app.appearance.shared())
        preview = await call(app, 'appearance.preview', {'name': value['name'], 'css': value['css']})
        assert app.appearance.read('one')['preview']['theme']['css'] == value['css']
        assert app.appearance.shared() == before
        assert app.appearance.read('two')['preview'] is None
        await call(app, 'appearance.preview.cancel')
        assert app.appearance.read('one')['preview'] is None
        await update(app, {'appearanceId': row['id']})
        assert app.appearance.read('one')['effective']['appearanceId'] == row['id']
    assert app.clients.records['one']['selectedSessionId'] == sid


async def test_saved_zip_survives_restart_and_loose_css_is_not_discovered(app, tmp_path):
    row = await save(app)
    assert (await save(app))['id'] == row['id']
    root = theme_library.folder(app)
    assert len(list(root.glob('*.zip'))) == 1
    (root / 'unselected.css').write_text('#amp-one{color:red}')
    assert {r['id'] for r in theme_library.listing(app)['items'] if r['source'] == 'saved'} == {row['id']}
    await app.close()
    restored = AppService(app.data_dir, workspace=tmp_path)
    try:
        assert theme_library.read(restored, row['id'])['name'] == 'A shared look'
        assert restored.appearance.shared()['revision'] == 0
    finally:
        await restored.close()


async def test_name_roundtrip_and_preview_cancel(app):
    row = await save(app, name='Look */ unusual é')
    value = await call(app, 'theme.read', {'id': row['id']})
    assert value['name'] == 'Look */ unusual é'
    validate_theme(value['css'])
    preview = await call(app, 'appearance.preview', {'name': value['name'], 'css': value['css']})
    assert app.appearance.read('one')['preview']['theme']['css'] == value['css']
    await call(app, 'appearance.preview.cancel')
    assert app.appearance.read('one')['preview'] is None


async def test_update_delete_fingerprints_and_unavailable_selection(app):
    row = await save(app)
    await update(app, {'appearanceId': row['id']})
    selected = deepcopy(app.appearance.shared())
    revised = await call(app, 'theme.update', {'id': row['id'], 'expectedFingerprint': row['fingerprint'],
        'name': 'Revised', 'css': '#amp-one{--a-accent:#654321}'})
    assert revised['id'] == row['id'] and revised['fingerprint'] != row['fingerprint']
    assert app.appearance.shared() == selected
    for action in ('theme.update', 'theme.delete'):
        args = {'id': row['id'], 'expectedFingerprint': row['fingerprint']}
        if action == 'theme.update':
            args.update(name='Stale', css='#amp-one{color:red}')
        with pytest.raises(AppError) as error:
            await call(app, action, args)
        assert error.value.status == 409
    assert (await call(app, 'theme.delete', {'id': revised['id'], 'expectedFingerprint': revised['fingerprint']}))['deleted']
    assert app.appearance.shared() == selected
    current = app.appearance.read('one')
    assert current['theme']['available'] is False and current['theme']['recoveryId'] == 'reference'
    assert not list(theme_library.folder(app).iterdir())


@pytest.mark.parametrize('deleted_slot', [1, 2])
async def test_zip_reimport_reuses_identical_collision_after_deleting_earlier_slot(app, deleted_slot):
    original = await save(app, name='Ocean', css='#amp-one{color:red}')
    raw = (await export(app, original))['base64']
    restored = original
    for index in range(1, deleted_slot + 1):
        revised = await call(app, 'theme.update', {'id': restored['id'], 'expectedFingerprint': restored['fingerprint'],
            'name': 'Ocean', 'css': f'#amp-one{{--collision:{index};color:blue}}'})
        restored = await call(app, 'theme.import', {'packageBase64': raw})
    await call(app, 'theme.delete', {'id': revised['id'], 'expectedFingerprint': revised['fingerprint']})
    for _ in range(2):
        assert (await call(app, 'theme.import', {'packageBase64': raw}))['id'] == restored['id']
    root = theme_library.folder(app)
    identical = [p.name for p in root.glob('*.zip') if p.read_bytes() == base64.b64decode(raw)]
    assert identical == [restored['id'][6:]]
    assert not (root / revised['id'][6:]).exists()


async def test_builtin_export_requires_id_never_exports_preview(app):
    before = deepcopy(app.appearance.shared())
    await call(app, 'appearance.preview', {'name': 'Unsaved', 'css': '#amp-one{color:red}'})
    with pytest.raises(AppError):
        await call(app, 'theme.export', {})
    value = await export(app, {'id': 'builtin:aurora'})
    package = packages.load(base64.b64decode(value['base64']))
    assert package.manifest['formatVersion'] == 2 and package.manifest['base'] == 'reference-v1'
    assert package.manifest['name'] == 'Aurora'
    assert app.appearance.shared() == before


async def test_external_change_and_invalid_edit_preserve_original(app):
    row = await save(app)
    path = theme_library.folder(app) / row['id'][6:]
    original = path.read_bytes()
    with pytest.raises(AppError):
        await call(app, 'theme.update', {'id': row['id'], 'expectedFingerprint': row['fingerprint'],
            'name': 'Invalid', 'css': '@import "https://example.com/style.css";'})
    assert path.read_bytes() == original
    changed = packages.encode(packages.from_css('#amp-one{color:blue}', 'External').files)
    path.write_bytes(changed)
    for action in ('theme.update', 'theme.delete'):
        args = {'id': row['id'], 'expectedFingerprint': row['fingerprint']}
        if action == 'theme.update':
            args.update(name='Stale', css='#amp-one{color:red}')
        with pytest.raises(AppError, match='changed'):
            await call(app, action, args)
    assert path.read_bytes() == changed and not list(path.parent.glob('*.tmp'))


@pytest.mark.parametrize('identity', ['builtin:default', 'builtin:digital-rain', 'saved:../outside.zip', 'saved:..\\outside.zip', 'saved:/tmp/outside.zip'])
async def test_only_saved_local_zips_can_be_mutated(app, identity):
    for action in ('theme.delete', 'theme.update'):
        args = {'id': identity, 'expectedFingerprint': '0' * 64}
        if action == 'theme.update':
            args.update(name='No', css='#amp-one{color:red}')
        with pytest.raises(AppError):
            await call(app, action, args)
    assert not theme_library.folder(app).exists()


async def test_library_links_and_special_files_are_rejected(app, tmp_path):
    row = await save(app)
    path = theme_library.folder(app) / row['id'][6:]
    outside = tmp_path / 'outside.zip'
    original = path.read_bytes()
    outside.write_bytes(original)
    path.unlink()
    path.symlink_to(outside)
    for action in ('theme.read', 'theme.export', 'theme.delete', 'theme.update'):
        args = {'id': row['id']}
        if action in ('theme.delete', 'theme.update'):
            args['expectedFingerprint'] = row['fingerprint']
        if action == 'theme.update':
            args.update(name='No', css='#amp-one{color:blue}')
        with pytest.raises(AppError):
            await call(app, action, args)
    assert outside.read_bytes() == original and path.is_symlink()
    if hasattr(os, 'mkfifo'):
        pipe = path.parent / 'pipe.zip'
        os.mkfifo(pipe)
        try:
            assert {r['name'] for r in theme_library.listing(app)['errors']} == {path.name, 'pipe.zip'}
        finally:
            pipe.unlink(missing_ok=True)


async def test_symlink_library_directory_cannot_be_read_or_written(app, tmp_path):
    outside = tmp_path / 'other-library'
    outside.mkdir()
    raw = packages.encode(packages.from_css('#amp-one{color:red}', 'Outside').files)
    (outside / 'outside.zip').write_bytes(raw)
    theme_library.folder(app).symlink_to(outside, target_is_directory=True)
    for action, args in [('theme.list', {}), ('theme.read', {'id': 'saved:outside.zip'}),
                         ('theme.save', {'name': 'No', 'css': '#amp-one{color:blue}'})]:
        with pytest.raises(AppError, match='real directory'):
            await call(app, action, args)
    assert (outside / 'outside.zip').read_bytes() == raw


@pytest.mark.parametrize('css', [
    '@import "https://example.com/style.css";', '#amp-one{background:url(https://example.com/a.png)}',
    '#amp-one{background:url("file:///tmp/a.png")}', '#amp-one{background:url("data:image/svg+xml;base64,abc")}',
    '@font-face{src:url("https://example.com/font.woff")}', '</style><script>alert(1)</script>',
    '#amp-one{background:image-set("https://example.com/a.png" 1x)}',
    '#amp-one{--asset:"https://example.com/a.png";background:image-set(var(--asset) 1x)}',
    '#amp-one{background:image("https://example.com/a.png")}',
])
async def test_save_uses_the_same_self_contained_validator(app, css):
    before = deepcopy(app.appearance.shared())
    with pytest.raises(AppError):
        await save(app, css=css)
    assert not theme_library.folder(app).exists() and app.appearance.shared() == before


async def test_cooperating_concurrent_updates_cannot_overwrite_each_other(app):
    row = await save(app)
    args = {'id': row['id'], 'expectedFingerprint': row['fingerprint'], 'name': row['name']}
    def mutate(color):
        try:
            return theme_library.mutate(app, {**args, 'css': '#amp-one{color:' + color + '}'})
        except AppError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(mutate, ['blue', 'green']))
    assert sum(isinstance(r, AppError) for r in results) == 1
    assert next(r for r in results if isinstance(r, AppError)).status == 409


async def test_duplicate_save_does_not_follow_replaced_file_symlink(app, tmp_path):
    row = await save(app)
    path = theme_library.folder(app) / row['id'][6:]
    outside = tmp_path / 'duplicate.zip'
    raw = path.read_bytes()
    outside.write_bytes(raw)
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(AppError):
        await save(app)
    assert path.is_symlink() and outside.read_bytes() == raw
