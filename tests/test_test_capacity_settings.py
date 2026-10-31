"""Global policy updates must not retarget or cancel owned work."""
import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from amplifier_web import task_environments as environments
from amplifier_web import test_capacity_settings as policy
from amplifier_web import private_policy_file
from amplifier_web.service import AppError
from test_configuration_authority import app, grant
from test_coordination_generation import agent, until
from test_task_environments import backend, scope, start, act
from test_test_queue import queue, submit, drive, record


async def read(app):
    return (await app.dispatch('testing.capacity.read', {}))['result']


async def save(app, defaults, **args):
    current = await read(app)
    return (await app.dispatch('testing.capacity.save',
        {'expectedRevision': current['revision'], 'defaults': defaults, **args}))['result']


async def test_passive_defaults_disabled_and_private_global_save(app, monkeypatch):
    monkeypatch.setattr(environments, 'capacity', lambda *a: pytest.fail('settings must not probe'))
    before = await read(app)
    assert before['runner']['status'] == 'disabled'
    assert before['effective'] == {'maxRunning': 0, 'maxInstances': 0, 'minFreeBytes': 10*1024**3}
    after = await save(app, {'maxRunning': 7, 'minFreeBytes': 0})
    assert after['effective']['maxRunning'] == 7 and after['effective']['minFreeBytes'] == 0
    assert (app.data_dir / 'test-capacity.json').stat().st_mode & 0o777 == 0o600
    assert not (app.data_dir / 'test-environments.json').exists()


async def test_waiting_legacy_job_wakes_and_runs_with_new_limit(app, queue, backend, monkeypatch):
    config = environments.configuration(app)[0]
    key = environments.config_key(config)
    seen = []
    real = environments.invoke
    def invoke(selected, home, row, action, args):
        if action == 'start':
            seen.append(selected['maxRunning'])
        return real(selected, home, row, action, args)
    monkeypatch.setattr(environments, 'invoke', invoke)
    monkeypatch.setattr(environments, 'capacity', lambda app, selected: {
        'available': policy.apply(app, selected)['maxRunning'] >= 20,
        'limits': {'max_running': policy.apply(app, selected)['maxRunning']},
        'counts': {'active': 15}})
    await submit(app)
    original = copy.deepcopy(record(queue, app))
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'queued' and not backend
    assert queue.backoff
    await save(app, {'maxRunning': 20})
    assert not queue.backoff and queue.wakeup.is_set()
    waiting = record(queue, app)
    assert waiting['job']['backendKey'] == original['job']['backendKey'] == key
    assert environments.config_key(environments.configuration(app)[0]) == key
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'passed' and seen == [20]
    row = environments.get(app.data_dir, scope(app)['sessionId'], record(queue, app)['job']['resourceId'])
    assert row['backendKey'] == key and row['status'] == 'retired'


@pytest.mark.parametrize('queued', [False, True])
async def test_lowering_during_preparation_uses_current_policy_at_launch(app, queue, backend, monkeypatch, queued):
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = environments.invoke
    def delayed(config, home, row, action, args):
        if action == 'prepare':
            entered.set()
            assert release.wait(5)
        if action == 'start':
            calls.append(config['maxRunning'])
        return original(config, home, row, action, args)
    monkeypatch.setattr(environments, 'invoke', delayed)
    await save(app, {'maxRunning': 20})
    if queued:
        await submit(app)
        await queue.tick()
    else:
        work = asyncio.create_task(start(app))
    try:
        await until(entered.is_set)
        await save(app, {'maxRunning': 1})
    finally:
        release.set()
    if queued:
        await asyncio.gather(*queue.tasks.values())
        assert record(queue, app)['job']['status'] == 'passed'
    else:
        assert (await work)['request']['state'] == 'completed'
    assert calls == [1]


async def test_active_environment_keeps_exact_cleanup_identity_after_policy_change(app, backend):
    row = (await start(app))['resource']
    original = environments.get(app.data_dir, scope(app)['sessionId'], row['id'])
    await save(app, {'maxRunning': 1, 'maxInstances': 1, 'minFreeBytes': 0})
    unchanged = environments.get(app.data_dir, scope(app)['sessionId'], row['id'])
    assert unchanged == original
    row = (await act(app, 'stop', row, 'stop'))['resource']
    removed = await act(app, 'remove', row, 'remove')
    assert removed['request']['state'] == 'completed'
    assert [name for name, _ in backend] == ['prepare', 'start', 'stop', 'remove']


async def test_stale_policy_or_runner_save_cannot_overwrite(app, backend):
    first = await read(app)
    await save(app, {'maxRunning': 6})
    with pytest.raises(AppError, match='changed'):
        await app.dispatch('testing.capacity.save', {'expectedRevision': first['revision'], 'defaults': {'maxRunning': 7}})
    config = environments.configuration(app)[0]
    current = await read(app)
    config['dockerHost'] = 'unix:///different/docker.sock'
    with pytest.raises(AppError, match='changed'):
        await app.dispatch('testing.capacity.save', {'expectedRevision': current['revision'], 'defaults': {'maxRunning': 7}})
    assert (await read(app))['defaults'] == {'maxRunning': 6}


async def test_runner_override_inherits_global_and_can_be_removed(app, backend):
    before = await read(app)
    changed = await save(app, {'maxRunning': 8, 'minFreeBytes': 0},
        runnerId=before['runnerId'], runnerOverrides={'maxRunning': 3})
    assert changed['effective']['maxRunning'] == 3
    assert changed['sources']['maxRunning'] == 'runner'
    assert changed['sources']['minFreeBytes'] == 'global'
    restored = await save(app, {'maxRunning': 8}, runnerId=before['runnerId'], runnerOverrides={})
    assert restored['effective']['maxRunning'] == 8
    assert restored['sources']['maxRunning'] == 'global'
    with pytest.raises(AppError, match='runner changed'):
        await save(app, {}, runnerId='f'*64, runnerOverrides={'maxRunning': 1})


async def test_agents_need_global_host_defaults_authority(app, backend):
    source = app.state['sessions'][0]
    current = await read(app)
    args = {'expectedRevision': current['revision'], 'defaults': {'maxRunning': 8}}
    with pytest.raises(AppError, match='authority'):
        await agent(app, source, 'testing.capacity.save', args, 'no-grant')
    await grant(app, ['host_defaults'], ['global'])
    assert (await agent(app, source, 'testing.capacity.save', args, 'with-grant'))['accepted']


async def test_changed_backend_still_cancels_waiting_job(app, queue, backend):
    await submit(app)
    await save(app, {'maxRunning': 20})
    environments.configuration(app)[0]['dockerHost'] = 'unix:///other/docker.sock'
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'cancelled' and not backend


@pytest.mark.parametrize('value', [-1, True, 1.5, '2', 2**53])
def test_policy_integer_validation(value):
    with pytest.raises(ValueError):
        policy.validate_limits({'maxRunning': value})


@pytest.mark.parametrize('kind', ['symlink', 'public', 'malformed'])
async def test_invalid_policy_never_silently_falls_back(app, backend, kind, tmp_path):
    path = app.data_dir / 'test-capacity.json'
    if kind == 'symlink':
        other = tmp_path / 'other-policy'
        other.write_text('{}')
        path.symlink_to(other)
    else:
        path.write_text('{}' if kind == 'malformed' else json.dumps({'version': 1, 'defaults': {}, 'runners': {}}))
        path.chmod(0o644 if kind == 'public' else 0o600)
    with pytest.raises(AppError):
        await read(app)
    with pytest.raises(AppError):
        await start(app)
    assert not backend


def test_execution_key_excludes_only_capacity_and_overlay_preserves_original(tmp_path):
    config = {'backend': 'incus', 'python': '/qualified/python', 'incus': {
        'project': 'owned', 'image': 'a'*64, 'daemonFingerprint': 'b'*64,
        'configDir': '/approved/auth', 'executableSha256': 'c'*64, 'maxRunning': 2}}
    changed = copy.deepcopy(config)
    changed['incus']['maxRunning'] = 9
    assert policy.execution_key(changed) == policy.execution_key(config)
    for field in ('project', 'image', 'daemonFingerprint', 'configDir', 'executableSha256'):
        candidate = copy.deepcopy(config)
        candidate['incus'][field] += '-changed'
        assert policy.execution_key(candidate) != policy.execution_key(config)
    rules = {'defaults': {'maxRunning': 8}, 'runners': {policy.execution_key(config): {'maxInstances': 0}}}
    limits, sources = policy.effective(config, rules)
    assert limits['maxRunning'] == 8 and limits['maxInstances'] == 0
    assert config['incus']['maxRunning'] == 2


def test_settings_store_without_unix_filesystem_capabilities(tmp_path, monkeypatch):
    # Native Windows lacks these APIs and POSIX mode semantics. Do not mutate
    # global os.name (which also changes pathlib's platform selection).
    native = policy.os
    portable = SimpleNamespace(**{name: getattr(native, name) for name in dir(native)
        if name not in {'O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK', 'getuid', 'name'}}, name='nt')
    monkeypatch.setattr(policy, 'os', portable)
    monkeypatch.setattr(private_policy_file, 'os', portable)
    app = SimpleNamespace(data_dir=tmp_path)
    assert policy.load(app) == {'version': 1, 'defaults': {}, 'runners': {}}
    value = {'version': 1, 'defaults': {'maxRunning': 0}, 'runners': {}}
    policy.write(app, value)
    (tmp_path / 'test-capacity.json').chmod(0o666)
    assert policy.load(app) == value
    path = tmp_path / 'test-capacity.json'
    path.unlink()
    other = tmp_path / 'other'
    other.write_text(json.dumps(value))
    path.symlink_to(other)
    with pytest.raises(ValueError, match='link'):
        policy.load(app)


@pytest.mark.parametrize('backend', ['docker', 'incus'])
def test_default_counts_reach_backend_without_rewriting_config(tmp_path, backend):
    from amplifier_web import incus_environment_worker
    app = SimpleNamespace(data_dir=tmp_path)
    incus = {'incusExecutable': '/bin/incus', 'configDir': '/private/config',
             'project': 'tests', 'admissionDir': '/private/admission'}
    original = {'backend': backend, **({'incus': incus} if backend == 'incus' else {})}
    applied = policy.apply(app, original)
    target = applied['incus'] if backend == 'incus' else applied
    assert target['maxRunning'] == target['maxInstances'] == 0
    assert 'maxRunning' not in (original.get('incus') or original)
    if backend == 'incus':
        env = incus_environment_worker.environment(target, tmp_path)
        assert env['AMPLIFIER_DTU_MAX_RUNNING'] == env['AMPLIFIER_DTU_MAX_INSTANCES'] == '0'
