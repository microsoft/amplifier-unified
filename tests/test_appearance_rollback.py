"""Rollback preserves 0.78 data without activating or converting its profiles."""
from copy import deepcopy
import json

import pytest

from amplifier_web.service import ACTION_DEFINITIONS, AppError, AppService
from amplifier_web import appearance_packages, theme_library
from amplifier_web.shell_modules import DEFAULT
from amplifier_web.state_records import load


DEFAULT_CSS = '#amp-one{color:#112233;background:#ffffff}'
RETAINED_CSS = '#amp-one{color:#445566;background:#eeeeee}'


@pytest.mark.parametrize('legacy_theme', [False, True])
async def test_078_state_reopens_with_legacy_or_default_and_preserves_profiles(tmp_path, monkeypatch, legacy_theme):
    # The paired client owns the restored default asset. This host-only check
    # supplies its bytes without rebuilding or substituting the current bundle.
    monkeypatch.setattr(AppService, 'default_theme', lambda self: DEFAULT_CSS)
    directory = tmp_path / 'data'
    service = AppService(directory, workspace=tmp_path)
    service.clients.attach('old-document')
    with service.clients.bind('old-document'):
        await service.dispatch('view.update', {'patch': {
            'draft': 'Keep my unsent text', 'scheme': 'dark',
            'themeDraft': RETAINED_CSS, 'themeDraftName': 'Private editor',
        }})
    shared = {'owner': 'retained-owner', 'revision': 7, 'values': {
        'appearanceId': 'reference', 'scheme': 'light', 'layout': 'compact'}}
    service.shell.put('preferences', 'shared-reference-v1', shared)
    # Reference preferences actually use the reference-v1 key, not a migration.
    service.shell.put('preferences', 'reference-v1', shared)
    legacy = {'scheme': 'dark', 'decorations': False}
    service.shell.put('preferences', 'appearance', legacy)
    client = service.shell.client('old-document')
    client['preferenceCommand'] = {'id': 'retained-command', 'status': 'pending',
                                   'after': {'activeScope': 'client', 'values': shared['values']}}
    service.shell.put('client', 'old-document', client)
    if legacy_theme:
        service._state['theme'] = {'name': 'My retained skin', 'css': RETAINED_CSS}
    else:
        service._state.pop('theme', None)  # The 0.78 startup mutation.
    service._save()
    before_records = dict(service.db.execute(
        "SELECT id,value FROM shell_records WHERE kind='preferences'"))
    await service.close()

    restored = AppService(directory, workspace=tmp_path)
    try:
        expected = {'name': 'My retained skin', 'css': RETAINED_CSS} if legacy_theme else {
            'name': 'Amplifier Unified', 'css': DEFAULT_CSS}
        assert restored.state['theme'] == expected
        assert not hasattr(restored, 'appearance')
        assert not any(name.startswith('appearance.') for name in ACTION_DEFINITIONS)
        assert {'theme.apply', 'theme.preview', 'theme.revert', 'theme.reset'} <= ACTION_DEFINITIONS.keys()
        assert dict(restored.db.execute(
            "SELECT id,value FROM shell_records WHERE kind='preferences'")) == before_records
        assert restored.shell.get('client', 'old-document')['preferenceCommand'] == client['preferenceCommand']
        with restored.clients.bind('old-document'):
            snapshot = restored.browser_state()
            assert snapshot['theme'] == expected and 'appearance' not in snapshot
            assert snapshot['view']['scheme'] == 'dark'
            assert snapshot['view']['draft'] == 'Keep my unsent text'
            assert snapshot['view']['themeDraft'] == RETAINED_CSS
            assert snapshot['view']['themeDraftName'] == 'Private editor'
        restored.clients.attach('new-document')
        assert restored.shell.client('new-document')['composition']['presentation'] == legacy
        restored._save()
        assert load(restored.db)['theme'] == expected
        assert dict(restored.db.execute(
            "SELECT id,value FROM shell_records WHERE kind='preferences'")) == before_records
    finally:
        await restored.close()


async def test_format2_library_is_inert_but_preserved_and_format1_still_works(tmp_path, monkeypatch):
    monkeypatch.setattr(AppService, 'default_theme', lambda self: DEFAULT_CSS)
    service = AppService(tmp_path / 'data', workspace=tmp_path)
    root = theme_library.folder(service)
    root.mkdir()
    manifest = {'type': 'appearance', 'formatVersion': 2, 'base': 'reference-v1',
                'name': 'Retained Reference delta', 'stylesheet': 'theme.css'}
    newer = appearance_packages.encode({
        'appearance.json': json.dumps(manifest).encode(),
        'theme.css': b'#amp-one{--a-accent:#123456}',
    })
    path = root / 'retained-reference.zip'
    path.write_bytes(newer)
    try:
        listing = theme_library.listing(service)
        assert not any(row['id'] == 'saved:' + path.name for row in listing['items'])
        assert path.read_bytes() == newer
        with pytest.raises(AppError, match='formatVersion'):
            theme_library.read(service, 'saved:' + path.name)
        with pytest.raises(AppError, match='formatVersion'):
            theme_library.mutate(service, {
                'id': 'saved:' + path.name, 'expectedFingerprint': '0' * 64,
            }, deleting=True)
        assert path.read_bytes() == newer
        saved = theme_library.save(service, {'name': 'Legacy CSS', 'css': RETAINED_CSS})
        exported = theme_library.export(service, saved['id'], 'zip')
        package = appearance_packages.load(appearance_packages.decode_input(exported['base64']))
        assert package.manifest['formatVersion'] == 1
        assert RETAINED_CSS in package.css
        assert path.read_bytes() == newer
    finally:
        await service.close()


@pytest.mark.parametrize('action', ['shell.changes.apply', 'shell.changes.preview', 'shell.changes.revert'])
async def test_retired_profile_prepared_changes_cannot_activate(tmp_path, monkeypatch, action):
    monkeypatch.setattr(AppService, 'default_theme', lambda self: DEFAULT_CSS)
    service = AppService(tmp_path / 'data', workspace=tmp_path)
    service.clients.attach('document')
    before = service.shell.client('document')
    change = {'id': 'old-change', 'clientId': 'document', 'baseRevision': before['revision'],
              'before': deepcopy(DEFAULT), 'composition': deepcopy(DEFAULT),
              'preferenceFence': {'activeScope': 'shared', 'sharedRevision': 1},
              'status': 'prepared'}
    service.shell.put('change', 'old-change', change)
    try:
        with service.clients.bind('document'):
            with pytest.raises(AppError, match='retired appearance'):
                await service.dispatch(action, {'clientId': 'document',
                    'expectedRevision': before['revision'], 'changeId': 'old-change'})
        assert service.shell.get('change', 'old-change') == change
        assert service.shell.client('document') == before
    finally:
        await service.close()


async def test_invalid_mixed_view_preserves_draft_and_scheme(tmp_path, monkeypatch):
    monkeypatch.setattr(AppService, 'default_theme', lambda self: DEFAULT_CSS)
    service = AppService(tmp_path / 'data', workspace=tmp_path)
    service.clients.attach('document')
    try:
        with service.clients.bind('document'):
            await service.dispatch('view.update', {'patch': {'draft': 'Keep me', 'scheme': 'dark'}})
            for invalid in ([], {'delivery': []}):
                with pytest.raises(AppError):
                    await service.dispatch('view.update', {'patch': {
                        'draft': 'Must not land', 'scheme': 'light', 'canvasDraft': invalid}})
                assert service.state['view']['draft'] == 'Keep me'
                assert service.state['view']['scheme'] == 'dark'
    finally:
        await service.close()