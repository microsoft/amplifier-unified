"""Accepted jobs keep their budgets; operator settings apply only to new work."""
import copy
import json
import time
from types import SimpleNamespace

import pytest
from amplifier_web import task_environments as environments
from amplifier_web import test_execution_settings as settings
from amplifier_web import test_environment_profiles as profiles
from amplifier_web.service import AppError
from test_configuration_authority import app, grant
from test_coordination_generation import agent
from test_task_environments import backend, scope, start, act
from test_test_queue import queue, submit, drive, record

async def save(app, queue=None, profiles=None):
    current = (await app.dispatch('testing.execution.read', {}))['result']
    return (await app.dispatch('testing.execution.save', {'expectedRevision': current['revision'],
        'queue': queue or {}, 'profiles': profiles or {}}))['result']

async def test_passive_private_global_policy_and_stale_writer(app, monkeypatch):
    monkeypatch.setattr(environments, 'capacity', lambda *a: pytest.fail('not an admission'))
    first = (await app.dispatch('testing.execution.read', {}))['result']
    assert first['effectiveQueue'] == {'maxPending': 0, 'maxPendingPerChat': 0, 'defaultWaitSeconds': 0}
    result = await save(app, {'maxPending': 100, 'defaultWaitSeconds': 0}, {'linux-python': {'cpus': 3}})
    assert result['effectiveProfiles'][0]['cpus'] == 3
    assert (app.data_dir / 'test-execution.json').stat().st_mode & 0o777 == 0o600
    with pytest.raises(AppError, match='changed'):
        await app.dispatch('testing.execution.save', {'expectedRevision': first['revision'], 'queue': {}, 'profiles': {}})
    assert settings.load(app)['queue']['maxPending'] == 100

async def test_agent_needs_global_settings_authority_but_routine_check_does_not(app, queue, backend):
    source = app.state['sessions'][0]
    current = (await app.dispatch('testing.execution.read', {}))['result']
    args = {'expectedRevision': current['revision'], 'queue': {'maxPending': 90}, 'profiles': {}}
    with pytest.raises(AppError, match='authority'):
        await agent(app, source, 'testing.execution.save', args, 'denied')
    await grant(app, ['host_defaults'], ['global'])
    assert (await agent(app, source, 'testing.execution.save', args, 'allowed'))['accepted']
    await submit(app)
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'passed'

async def test_new_policy_and_existing_queued_budget_survive_settings_change(app, queue, backend):
    await save(app, {'defaultWaitSeconds': 0}, {'linux-python': {'cpus': 3, 'memoryMiB': 3072,
        'defaultTimeoutSeconds': 3601, 'maxTimeoutSeconds': 7200, 'preparationTimeoutSeconds': 5000}})
    await submit(app)
    accepted = copy.deepcopy(record(queue, app)['job'])
    assert accepted['expiresAt'] is None and accepted['timeoutSeconds'] == 3601
    await save(app, {'maxPending': 1, 'defaultWaitSeconds': 20}, {'linux-python': {'cpus': 1, 'maxTimeoutSeconds': 300}})
    assert record(queue, app)['job'] == accepted
    from amplifier_web.test_queue import TestQueue
    recovered = app.test_queue = TestQueue(app)
    recovered.start = lambda: None
    await drive(recovered)
    job = record(recovered, app)['job']
    assert job['status'] == 'passed'
    row = environments.get(app.data_dir, scope(app)['sessionId'], job['resourceId'])
    assert row['resourceRequest']['cpus'] == 3
    assert profiles.timing('linux-python', row['timingRequest'])['preparationTimeoutSeconds'] == 5000
    await submit(app, 'fresh')
    fresh = record(queue, app, 'fresh')['job']
    assert fresh['resourceRequest']['cpus'] == 1 and fresh['timeoutSeconds'] == 300
    assert fresh['expiresAt'] > time.time()

async def test_direct_environment_uses_saved_profile_not_changed_defaults(app, backend):
    await save(app, profiles={'linux-python': {'defaultTimeoutSeconds': 3600, 'maxTimeoutSeconds': 7200, 'cpus': 3}})
    resource = (await start(app))['resource']
    await save(app)
    result = await act(app, 'execute', resource, 'long-check', command='true', timeoutSeconds=3600)
    assert result['request']['state'] == 'completed'
    assert backend[-1][1]['timeoutSeconds'] == 3600

async def test_full_sql_count_and_rotating_scan_beyond_64(app, queue, backend, monkeypatch):
    await submit(app)
    original = record(queue, app)
    # Seed distinct durable receipts, including running jobs behind the first
    # page, without fabricating dozens of conversations or backend effects.
    for number in range(1, 140):
        row = copy.deepcopy(original)
        row['requestId'] = f'job-{number}'
        row['senderSessionId'] = f'owner-{number}'
        row['job']['status'] = 'starting' if number >= 100 else 'queued'
        app.collaboration.insert(row['requestId'], row['requestId'], row)
    app.db.commit()
    assert queue.pending_count() == 140
    assert queue.pending_count(preparing_backend=original['job']['backendKey']) == 40
    pages = [queue.active_page() for _ in range(3)]
    assert list(map(len, pages)) == [64, 64, 12]
    assert len({r['requestId'] for p in pages for r in p}) == 140
    assert queue.active_page()[0]['requestId'] == original['requestId']
    # The default admits beyond the old global ceiling, with bounded reads intact.
    source = copy.deepcopy(app.state['sessions'][0]); source['id'] = 'extra-owner'
    app.state['sessions'].append(source)
    await app.dispatch('workspace.tests.submit', {**scope(app), 'sessionId': source['id'], 'requestId': 'uncapped',
        'profileId': 'linux-python', 'command': 'true', 'notify': False})
    assert queue.pending_count() == 141
    # A lower cap changes new admission, never cancels these records.
    await save(app, {'maxPending': 100})
    assert queue.pending_count() == 141
    with pytest.raises(AppError, match='queue limit'):
        await app.dispatch('workspace.tests.submit', {**scope(app), 'sessionId': source['id'], 'requestId': 'extra',
            'profileId': 'linux-python', 'command': 'true', 'notify': False})

async def test_zero_wait_still_obeys_stop_and_explicit_wait_limit(app, queue, backend):
    await submit(app, waitSeconds=0)
    assert record(queue, app)['job']['expiresAt'] is None
    app.state['sessions'][0]['interruptionRevision'] = 99
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'cancelled' and not backend
    await submit(app, 'bounded', waitSeconds=20)
    assert 0 < record(queue, app, 'bounded')['job']['expiresAt'] - time.time() <= 20

@pytest.mark.parametrize('value', [
    {'queue': {'maxPending': -1}}, {'queue': {'maxPendingPerChat': -1}}, {'queue': {'maxPendingPerChat': True}}, {'queue': {'defaultWaitSeconds': -1}},
    {'profiles': {'linux-python': {'cpus': True}}}, {'profiles': {'browser-test': {'memoryMiB': 0}}},
    {'profiles': {'python-project': {'defaultTimeoutSeconds': 9000, 'maxTimeoutSeconds': 100}}},
])
def test_invalid_policy_rejected(value):
    with pytest.raises(ValueError):
        settings.validate({'version': 1, 'queue': {}, 'profiles': {}, **value})

@pytest.mark.parametrize('kind', ['symlink', 'public', 'invalid'])
async def test_invalid_disk_policy_never_silently_falls_back(app, backend, kind, tmp_path):
    path = app.data_dir / 'test-execution.json'
    if kind == 'symlink':
        target = tmp_path / 'target'; target.write_text('{}'); path.symlink_to(target)
    else:
        path.write_text(json.dumps({'version': 1, 'queue': {}, 'profiles': {}}) if kind == 'public' else '{}')
        path.chmod(0o644 if kind == 'public' else 0o600)
    with pytest.raises(AppError):
        await app.dispatch('testing.execution.read', {})
    with pytest.raises(AppError):
        await start(app)
    assert not backend

def test_resources_are_independent_of_timeout_policy():
    fields = {'cpus': 3, 'maxTimeoutSeconds': 4000}
    assert profiles.freeze('python-project', fields) == profiles.freeze('python-project', {**fields, 'maxTimeoutSeconds': 5000})
    assert profiles.freeze_timing('python-project', fields) != profiles.freeze_timing('python-project', {**fields, 'maxTimeoutSeconds': 5000})
    damaged = profiles.freeze_timing('python-project'); damaged['maxTimeoutSeconds'] += 1
    with pytest.raises(ValueError): profiles.validate_timeout('python-project', 300, damaged)
    damaged_resources = profiles.freeze('python-project'); damaged_resources['cpus'] += 1
    with pytest.raises(ValueError): profiles.resources('python-project', damaged_resources)


def test_long_guest_deadline_matches_public_schema():
    from amplifier_web import test_job_guest as guest
    from amplifier_web.service import ACTION_DEFINITIONS
    payload = {'command': 'true', 'timeoutSeconds': 3600, 'rootOwner': 'owner',
        'operationId': 'operation', 'universeId': 'guest', 'jobId': 'check'}
    assert guest.bound(payload)['timeoutSeconds'] == 3600
    for action in ('workspace.tests.submit', 'workspace.environments.execute'):
        assert ACTION_DEFINITIONS[action][1]['properties']['timeoutSeconds']['maximum'] == profiles.MAX_SECONDS
    payload['timeoutSeconds'] = profiles.MAX_SECONDS + 1
    with pytest.raises(ValueError): guest.bound(payload)

async def test_historical_direct_environment_keeps_sixty_second_default(app, backend, monkeypatch):
    # This checks historical timeout semantics, not contention with the queue's
    # reservation maintenance. Keep real settings/dispatch and capacity checks.
    monkeypatch.setattr(app.test_queue, 'start', lambda: None)
    first = (await start(app))['resource']
    row = environments.get(app.data_dir, scope(app)['sessionId'], first['id'])
    row = environments.save(app.data_dir, row, timingRequest=None)
    await save(app, profiles={'linux-python': {'defaultTimeoutSeconds': 1200, 'maxTimeoutSeconds': 1800}})
    result = await act(app, 'execute', environments.public(app.data_dir, row), 'legacy-check', command='true')
    assert result['request']['state'] == 'completed', result
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute'], backend
    assert backend[-1][1]['timeoutSeconds'] == 60


async def test_optional_zero_counts_are_public_and_saved_positive_settings_are_not_rewritten(app,queue):
    from amplifier_web.service import ACTION_DEFINITIONS
    schema=ACTION_DEFINITIONS['testing.execution.save'][1]['properties']
    for key in settings.CONCURRENCY_DEFAULTS:
        assert schema['concurrency']['properties'][key]['minimum']==0
    assert all(field['minimum']==0 for field in schema['queue']['properties'].values())
    current=(await app.dispatch('testing.execution.read',{}))['result']
    finite={key:5 for key in settings.CONCURRENCY_DEFAULTS}
    await app.dispatch('testing.execution.save',{'expectedRevision':current['revision'],'queue':{'maxPending':7,'maxPendingPerChat':1,'defaultWaitSeconds':3600},'profiles':{},'concurrency':finite})
    path=app.data_dir/'test-execution.json';before=path.read_bytes()
    current=(await app.dispatch('testing.execution.read',{}))['result']
    assert current['concurrency']==finite and current['effectiveQueue']['defaultWaitSeconds']==3600
    assert current['effectiveQueue']['maxPending']==7
    assert path.read_bytes()==before
    zero=(await app.dispatch('testing.execution.save',{'expectedRevision':current['revision'],'queue':{'maxPending':0,'maxPendingPerChat':0},'profiles':{},'concurrency':dict.fromkeys(finite,0)}))['result']
    assert zero['effectiveQueue']=={'maxPending':0,'maxPendingPerChat':0,'defaultWaitSeconds':0}
    assert zero['effectiveConcurrency']==dict.fromkeys(settings.CONCURRENCY_DEFAULTS,0)
