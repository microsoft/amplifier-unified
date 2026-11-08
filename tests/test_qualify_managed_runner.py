"""No-replay and private storage controls for the opt-in native qualifier."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import test_environment_setup as setup

spec = importlib.util.spec_from_file_location('qualifier', Path(__file__).parents[1] / 'scripts/qualify_managed_runner.py')
qualifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualifier)


def test_private_home_refuses_public_existing_directory_and_symlink(tmp_path):
    folder = tmp_path / 'public'
    folder.mkdir(mode=0o755)
    with pytest.raises(ValueError, match='private owned'):
        qualifier.private_home(folder)
    assert not list(folder.iterdir())
    link = tmp_path / 'link'
    link.symlink_to(folder, target_is_directory=True)
    with pytest.raises(ValueError, match='symlinks'):
        qualifier.private_home(link)
    assert qualifier.private_home(tmp_path / 'private').stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('recorded', [False, True])
async def test_interrupted_staging_never_reinstalls(tmp_path, monkeypatch, recorded):
    app = SimpleNamespace(data_dir=tmp_path)
    identity = 'a' * 32
    attempt = {'runnerUpdate': identity, 'previous': {'old': True}, 'previousNormalized': {'normalized': True}}
    setup.write_json(tmp_path / 'qualification-update.json', attempt)
    path = qualifier.updates.record_path(app, identity)
    if recorded:
        setup.write_json(path, {'state': 'installing', 'previous': attempt['previous']})
    install = AsyncMock()
    monkeypatch.setattr(qualifier.updates, 'stage', install)
    with pytest.raises(ValueError, match='without replay'):
        await qualifier.stage_once(app, 'b' * 40)
    install.assert_not_called()


async def test_staging_intent_precedes_dispatch_and_keeps_predecessor_on_reentry(tmp_path, monkeypatch):
    app = SimpleNamespace(data_dir=tmp_path)
    old, new = {'installationId': 'old'}, {'installationId': 'new'}
    selected = old
    source = {'runnerSelection': 'selection', 'eligible': True, 'runnerPolicy': {'origin': 'app-setup'}}
    monkeypatch.setattr(qualifier.updates, 'raw_configuration', lambda _: selected)
    monkeypatch.setattr(qualifier.updates, 'update_sources', lambda _: [dict(source)])
    monkeypatch.setattr(qualifier.environments, 'validate_configuration_value', lambda app, config: ({**config, 'normalized': True}, 'ready', ''))
    async def stage(app, source):
        identity = qualifier.updates.intent_id(source)
        assert setup.read_json(tmp_path / 'qualification-update.json')['runnerUpdate'] == identity
        setup.write_json(qualifier.updates.record_path(app, identity), {
            'previous': old, 'revision': 'b' * 40, 'candidate': new,
            'normalized': new, 'runtime': {'hash': 'retained'}})
        return {'runnerUpdate': identity}
    dispatch = AsyncMock(side_effect=stage)
    monkeypatch.setattr(qualifier.updates, 'stage', dispatch)
    first = await qualifier.stage_once(app, 'b' * 40)
    selected = new
    assert await qualifier.stage_once(app, 'b' * 40) == first
    assert first['previous'] == old and first['previousNormalized']['installationId'] == 'old'
    dispatch.assert_awaited_once()
