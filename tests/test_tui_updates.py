"""Application updates retain and validate an installed optional client."""
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_updates
from test_app_updates import prepared_activation


@pytest.mark.parametrize('extras', [[], ['tui']])
async def test_update_stages_and_replaces_the_same_optional_install(tmp_path, monkeypatch, extras):
    service, manager, _ = await prepared_activation(tmp_path, monkeypatch)
    service.state['updates']['application'] = service.state['updates']['pendingApp']
    calls = []
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: extras)
    monkeypatch.setattr(app_updates.shutil, 'which', lambda _: '/fixture/uv')
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed', lambda _: True)

    async def process(*args, **kwargs):
        calls.append(args)
        return '99.0.0' if '-c' in args else ''

    monkeypatch.setattr(app_updates, 'process', process)
    try:
        await app_updates.stage(manager)
        marker = manager.directory / 'applications' / ('a' * 40) / 'validated.json'
        assert json.loads(marker.read_text())['extras'] == extras
        await app_updates.activate(manager)
        installs = [args for args in calls if 'install' in args]
        assert len(installs) == 2 and installs[0][-1] == installs[1][-1]
        assert ('amplifier-unified[tui] @ ' in installs[0][-1]) is bool(extras)
        probes = [args for args in calls if '-c' in args]
        assert len(probes) == 2
        assert all(list(args[args.index(app_updates.PROBE) + 1:]) == extras for args in probes)
        assert service.state['updates']['pendingRestart']['version'] == '99.0.0'
    finally:
        await service.close()


async def test_changed_extras_require_revalidation_before_closing_work(tmp_path, monkeypatch):
    service, manager, _ = await prepared_activation(tmp_path, monkeypatch)
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: ['tui'])
    process = AsyncMock()
    close = AsyncMock()
    monkeypatch.setattr(app_updates, 'process', process)
    monkeypatch.setattr(service.runtime, 'close', close)
    try:
        with pytest.raises(ValueError, match='Optional clients changed'):
            await app_updates.activate(manager)
        process.assert_not_awaited()
        close.assert_not_awaited()
        assert service.state['updates']['phase'] != 'activating'
    finally:
        await service.close()


@pytest.mark.parametrize('present', [False, True])
def test_detect_optional_client_without_loading_it(monkeypatch, present):
    def distribution(name):
        assert name == 'amplifier-app-tui'
        if not present:
            raise app_updates.metadata.PackageNotFoundError(name)
        return object()
    monkeypatch.setattr(app_updates.metadata, 'distribution', distribution)
    assert app_updates.installed_extras() == (['tui'] if present else [])
