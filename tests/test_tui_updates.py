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


@pytest.mark.parametrize('previous,current', [([], ['tui']), (['tui'], [])])
async def test_changed_extras_can_restage_through_normal_app_command(tmp_path, monkeypatch, previous, current):
    service, manager, _ = await prepared_activation(tmp_path, monkeypatch)
    service.state['updates']['application'] = service.state['updates']['pendingApp']
    marker = manager.directory / 'applications' / ('a' * 40) / 'validated.json'
    marker.write_text(json.dumps({**json.loads(marker.read_text()), 'extras': previous}))
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: current)
    process = AsyncMock()
    close = AsyncMock(return_value=None)
    monkeypatch.setattr(app_updates, 'process', process)
    monkeypatch.setattr(service.runtime, 'close', close)
    try:
        with pytest.raises(ValueError, match='Optional clients changed'):
            await manager.app()
        process.assert_not_awaited()
        close.assert_not_awaited()
        assert service.state['updates']['phase'] == 'error'
        assert service.state['updates']['pendingApp'] is None
        assert service.state['updates']['appAvailable']
        assert 'Install the update again' in service.state['updates']['detail']
        calls = []

        async def install(*args, **kwargs):
            calls.append(args)
            return '99.0.0' if '-c' in args else ''

        monkeypatch.setattr(app_updates, 'process', install)
        monkeypatch.setattr(app_updates.shutil, 'which', lambda _: '/fixture/uv')
        monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed', lambda _: True)
        monkeypatch.setattr(manager, 'busy', lambda: True)
        await manager.app()
        assert json.loads(marker.read_text())['extras'] == current
        assert service.state['updates']['pendingApp']
        assert len(calls) == 2  # Candidate install and probe only; work stays open.
        close.assert_not_awaited()
        monkeypatch.setattr(manager, 'busy', lambda: False)
        await manager.app()
        close.assert_awaited_once()
        assert service.state['updates']['pendingRestart']['version'] == '99.0.0'
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
