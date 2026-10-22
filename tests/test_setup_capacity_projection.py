"""Setup observations and launch admission use the same mutable capacity policy."""
import asyncio
import copy
import threading

import pytest

from amplifier_web import test_environment_setup as setup
from amplifier_web import task_environments as owner
from amplifier_web import test_capacity_settings as capacity
from test_test_environment_setup import app


def configured(app, monkeypatch):
    config = {'enabled': True, 'backend': 'docker', 'maxRunning': 2,
              'maxInstances': 4, 'minFreeBytes': 4096}
    setup.write_json(app.data_dir / 'test-environments.json', config)
    setup.retain_verification(app, config, {'probeRemoved': True})
    monkeypatch.setattr(owner, 'configuration', lambda _: (config, 'ready', 'Configured'))
    calls = []
    def probe(selected, home, row, action, args):
        assert action == 'capacity' and not row and not args
        calls.append(copy.deepcopy(selected))
        blockers = []
        if selected['maxRunning'] and 1 >= selected['maxRunning']:
            blockers.append({'code': 'execution_capacity'})
        if selected['maxInstances'] and 6 >= selected['maxInstances']:
            blockers.append({'code': 'retained_capacity'})
        if 8192 < selected['minFreeBytes']:
            blockers.append({'code': 'storage_pressure'})
        return {'ok': True, 'capacity': {'counts': {'active': 1, 'total': 6},
            'limits': {k: selected[k] for k in capacity.LIMITS}, 'blockers': blockers}}
    monkeypatch.setattr(owner, 'invoke', probe)
    return config, calls


@pytest.mark.parametrize('limits,expected', [
    ({'maxRunning': 0, 'maxInstances': 0}, []),
    ({'maxRunning': 1, 'maxInstances': 9}, ['execution_capacity']),
    ({'maxRunning': 0, 'maxInstances': 0, 'minFreeBytes': 16384}, ['storage_pressure']),
])
async def test_setup_reports_current_policy_without_rewriting_backend(app, monkeypatch, limits, expected):
    config, calls = configured(app, monkeypatch)
    before = (app.data_dir / 'test-environments.json').read_bytes()
    # The selected-runner override wins over global values, exactly as at launch.
    capacity.write(app, {'version': 1, 'defaults': {'maxRunning': 5, 'maxInstances': 20},
                        'runners': {capacity.execution_key(config): limits}})
    result = await setup.read(app)
    assert result['status'] == 'ready'
    assert [row['code'] for row in result['capacity']['blockers']] == expected
    assert result['available'] is (not expected)
    assert calls == [capacity.apply(app, config)]
    assert (app.data_dir / 'test-environments.json').read_bytes() == before
    assert config['maxRunning'] == 2 and config['maxInstances'] == 4
    app.smart_tools.install.assert_not_called()


async def test_saved_policy_change_invalidates_setup_cache_immediately(app, monkeypatch):
    config, calls = configured(app, monkeypatch)
    first = await setup.readiness(app)
    assert first['capacity']['blockers'] == [{'code': 'retained_capacity'}]
    assert await setup.readiness(app) == first and len(calls) == 1
    capacity.write(app, {'version': 1, 'defaults': {'maxRunning': 0, 'maxInstances': 0}, 'runners': {}})
    second = await setup.readiness(app)
    assert second['available'] is True and len(calls) == 2
    assert second['capacity']['limits'] == {key: value for key, value in capacity.apply(app, config).items() if key in capacity.LIMITS}
    assert await setup.readiness(app) == second and len(calls) == 2


async def test_policy_change_during_probe_cannot_publish_old_capacity(app, monkeypatch):
    config, calls = configured(app, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = owner.invoke
    def held(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(owner, 'invoke', held)
    pending = asyncio.create_task(setup.readiness(app))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        capacity.write(app, {'version': 1, 'defaults': {'maxRunning': 0, 'maxInstances': 0}, 'runners': {}})
    finally:
        release.set()
    stale = await pending
    assert stale['status'] == 'unavailable' and 'changed' in stale['reason']
    assert 'capacity' not in stale
    assert not getattr(app, '_test_setup_readiness', None)
    assert (await setup.readiness(app))['available'] is True


async def test_invalid_policy_cannot_reuse_successful_readiness(app, monkeypatch):
    _, calls = configured(app, monkeypatch)
    capacity.write(app, {'version': 1, 'defaults': {'maxInstances': 0}, 'runners': {}})
    assert (await setup.readiness(app))['available'] is True
    setup.write_json(app.data_dir / 'test-capacity.json', {'version': 99})
    result = await setup.readiness(app)
    assert result['status'] == 'unavailable' and 'capacity settings' in result['reason']
    assert 'capacity' not in result and len(calls) == 1
