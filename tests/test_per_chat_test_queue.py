"""Real submissions and owned effects; only physical backend calls are controlled."""
import asyncio
import threading

import pytest

from amplifier_web import task_environments as environments
from amplifier_web.test_queue import TestQueue
from amplifier_web.service import AppError
from test_test_queue import app, queue, backend, submit, drive, record
from test_test_execution_settings import save
from test_task_environments import scope
from test_coordination_generation import until


async def configure(app, count=3):
    await save(app, {'maxPending': 200, 'maxPendingPerChat': count, 'defaultWaitSeconds': 0})


def remote_backend(app, monkeypatch):
    config = {**environments.configuration(app)[0], 'backend': 'incus', 'incus': {'project': 'test'},
              'remoteRunner': {'id': 'runner', 'name': 'Other machine', 'machineId': 'a'*32, 'incus': {'project': 'test'}}}
    monkeypatch.setattr(environments, 'configuration', lambda app: (config, 'ready', 'fixture'))
    invoke = environments.invoke
    def remote_invoke(*args):
        result = invoke(*args)
        result['backendIdentity'] = {'backend': 'incus', 'daemon': 'owned-daemon', 'project': 'test'}
        return result
    monkeypatch.setattr(environments, 'invoke', remote_invoke)


async def test_explicit_limit_and_full_per_chat_count_beyond_page(app, queue, backend):
    await configure(app,1)
    await submit(app, 'first')
    with pytest.raises(AppError, match='pending check limit'):
        await submit(app, 'second')
    await configure(app, 70)
    for index in range(69):
        await submit(app, 'queued-'+str(index))
    assert len(queue.rows(active=True)) == 64
    assert queue.pending_count(sid=scope(app)['sessionId']) == 70
    with pytest.raises(AppError, match='pending check limit'):
        await submit(app, 'over-limit')
    # Replays neither count twice nor inherit a subsequently lower limit.
    await configure(app, 1)
    assert (await submit(app, 'first'))['duplicate']
    assert queue.pending_count(sid=scope(app)['sessionId']) == 70
    assert not backend


@pytest.mark.parametrize('remote', [False, True])
async def test_same_chat_waits_through_running_and_removal(app, queue, backend, monkeypatch, remote):
    if remote:
        remote_backend(app, monkeypatch)
    await configure(app)
    entered, release = threading.Event(), threading.Event()
    removing, removed = threading.Event(), threading.Event()
    invoke = environments.invoke
    first = True
    def controlled(config, home, row, action, args):
        nonlocal first
        if action == 'execute' and first:
            first = False; entered.set(); assert release.wait(5)
        if action == 'remove' and not removed.is_set():
            removing.set(); assert removed.wait(5)
        return invoke(config, home, row, action, args)
    monkeypatch.setattr(environments, 'invoke', controlled)
    await submit(app, 'first'); await submit(app, 'second')
    await asyncio.gather(queue.tick(), queue.tick())
    await until(entered.is_set)
    try:
        await queue.tick()
        second = record(queue, app, 'second')['job']
        assert second['status'] == 'queued' and 'earlier check' in second['detail']
        assert [a for a, _ in backend].count('prepare') == 1
        release.set(); await until(removing.is_set)
        await queue.tick()
        assert record(queue, app, 'second')['job']['status'] == 'queued'
        assert [a for a, _ in backend].count('prepare') == 1
    finally:
        release.set(); removed.set()
        await asyncio.gather(*queue.tasks.values())
    await drive(queue)
    assert [record(queue, app, name)['job']['status'] for name in ('first', 'second')] == ['passed', 'passed']
    assert [a for a, _ in backend].count('execute') == 2
    rows = [environments.get(app.data_dir, scope(app)['sessionId'], record(queue, app, name)['job']['resourceId']) for name in ('first', 'second')]
    assert all(row['status'] == 'retired' for row in rows)
    assert bool(rows[0].get('testMachine')) == remote


async def test_cancelled_head_and_known_failure_release_next_without_resubmission(app, queue, backend, monkeypatch):
    await configure(app)
    await submit(app, 'cancelled'); await submit(app, 'failed'); await submit(app, 'next')
    await app.dispatch('workspace.tests.cancel', {**scope(app), 'requestId': 'cancelled'})
    invoke = environments.invoke
    fail = True
    def missing(config, home, row, action, args):
        nonlocal fail
        if action == 'start' and fail:
            fail = False
            return {'ok': False, 'effectAdmitted': False, 'status': 'not-started', 'code': 'approved_image_missing'}
        return invoke(config, home, row, action, args)
    monkeypatch.setattr(environments, 'invoke', missing)
    await drive(queue); await drive(queue)
    assert [record(queue, app, name)['job']['status'] for name in ('cancelled', 'failed', 'next')] == ['cancelled', 'failed', 'passed']
    assert [a for a, _ in backend].count('execute') == 1


@pytest.mark.parametrize('remote', [False, True])
async def test_unknown_owner_blocks_after_restart_until_confirmed_cleanup(app, queue, backend, monkeypatch, remote):
    if remote:
        remote_backend(app, monkeypatch)
    await configure(app)
    invoke = environments.invoke
    unknown = True
    def uncertain(config, home, row, action, args):
        if unknown and action == 'execute':
            backend.append((action, args)); raise TimeoutError('lost acknowledgement')
        if unknown and action == 'job_read':
            return {'ok': True, 'job': {'id': args['requestId'], 'state': 'unknown', 'terminal': False}}
        return invoke(config, home, row, action, args)
    monkeypatch.setattr(environments, 'invoke', uncertain)
    await submit(app, 'first'); await submit(app, 'second')
    await drive(queue)
    assert record(queue, app, 'first')['job']['status'] == 'unknown'
    recovered = app.test_queue = TestQueue(app); recovered.start = lambda: None
    await drive(recovered)
    second = record(recovered, app, 'second')['job']
    assert second['status'] == 'queued' and 'cleanup to be confirmed' in second['detail']
    assert [a for a, _ in backend].count('execute') == 1
    # Recovery observes the same existing guest and performs owned cleanup.
    # It never repeats the uncertain command, and then the next check can start.
    unknown = False
    await recovered.recover(record(recovered, app, 'first')['requestId'])
    await drive(recovered)
    assert record(recovered, app, 'second')['job']['status'] == 'passed'
    assert [a for a, _ in backend].count('execute') == 2


async def test_restart_preserves_fifo_when_scanning_later_page(app, queue, backend):
    await configure(app, 70)
    for index in range(70):
        await submit(app, 'queued-'+str(index))
    recovered = app.test_queue = TestQueue(app); recovered.start = lambda: None
    # Rotating scan reaches the second page before the first has been admitted.
    recovered.active_page()
    await drive(recovered)
    assert not backend
    assert record(recovered, app, 'queued-64')['job']['status'] == 'queued'
    await drive(recovered)
    assert record(recovered, app, 'queued-0')['job']['status'] == 'passed'
    assert [a for a, _ in backend].count('execute') == 1


async def test_concurrent_capacity_ticks_only_schedule_one_exact_job(app, queue, backend, monkeypatch):
    await configure(app)
    entered, release = threading.Event(), threading.Event()
    def capacity(*args):
        entered.set(); assert release.wait(5)
        return {'available': True, 'limits': {'max_running': 10}, 'counts': {'active': 0}}
    monkeypatch.setattr(environments, 'capacity', capacity)
    await submit(app, 'first'); await submit(app, 'second')
    ticks = [asyncio.create_task(queue.tick()) for _ in range(2)]
    await until(entered.is_set)
    release.set(); await asyncio.gather(*ticks)
    await asyncio.gather(*queue.tasks.values())
    await drive(queue)
    assert [record(queue, app, name)['job']['status'] for name in ('first', 'second')] == ['passed', 'passed']
    assert [a for a, _ in backend].count('execute') == 2


async def test_cancel_during_allocation_keeps_next_waiting_until_owner_retired(app, queue, backend, monkeypatch):
    await configure(app)
    entered, release = threading.Event(), threading.Event()
    prepare = environments.prepare
    def delayed(*args):
        row = prepare(*args); entered.set(); assert release.wait(5); return row
    monkeypatch.setattr(environments, 'prepare', delayed)
    await submit(app, 'first'); await submit(app, 'second')
    await queue.tick(); await until(entered.is_set)
    await app.dispatch('workspace.tests.cancel', {**scope(app), 'requestId': 'first'})
    await queue.tick()
    assert record(queue, app, 'second')['job']['status'] == 'queued' and not backend
    release.set(); await asyncio.gather(*queue.tasks.values())
    await drive(queue)
    assert record(queue, app, 'first')['job']['status'] == 'cancelled'
    assert record(queue, app, 'second')['job']['status'] == 'passed'
    assert [a for a, _ in backend].count('execute') == 1


@pytest.mark.parametrize('remote', [False, True])
async def test_original_job_reclaims_allocation_before_queue_link_after_restart(app, queue, backend, monkeypatch, remote):
    if remote:
        remote_backend(app, monkeypatch)
    await configure(app)
    await submit(app, 'first'); await submit(app, 'second')
    receipt = record(queue, app, 'first'); job = receipt['job']
    config = environments.configuration(app)[0]
    allocated = environments.prepare(app.data_dir, receipt['senderSessionId'], scope(app)['workspaceId'],
        job['actor'], job['source'], job['backendKey'], receipt['requestId']+':start:0',
        config.get('installationId'), config, job['profileId'], job['resourceRequest'], job['timingRequest'])
    assert not job.get('resourceId') and not backend
    recovered = app.test_queue = TestQueue(app); recovered.start = lambda: None
    await drive(recovered)
    assert record(recovered, app, 'first')['job']['resourceId'] == allocated['id']
    assert record(recovered, app, 'first')['job']['status'] == 'passed'
    await drive(recovered)
    assert record(recovered, app, 'second')['job']['status'] == 'passed'
    assert [a for a, _ in backend].count('execute') == 2
@pytest.mark.parametrize('end', ['cancel', 'expire', 'stop'])
@pytest.mark.parametrize('advanced', ['none', 'effect-intent', 'local-effect', 'owner-lock'])
async def test_restart_cancel_or_expiry_only_retires_proven_initial_allocation(app, queue, backend, monkeypatch, end, advanced):
    import time
    remote_backend(app, monkeypatch)
    await configure(app)
    await submit(app, 'first'); await submit(app, 'second')
    receipt = record(queue, app, 'first'); job = receipt['job']; config = environments.configuration(app)[0]
    command = receipt['requestId']+':start:0'
    allocated = environments.prepare(app.data_dir, receipt['senderSessionId'], scope(app)['workspaceId'],
        job['actor'], job['source'], job['backendKey'], command, config.get('installationId'), config,
        job['profileId'], job['resourceRequest'], job['timingRequest'])
    held = None
    if advanced == 'effect-intent':
        app.operations.requests.begin(receipt['senderSessionId'], 'workspace.environments.start', {'requestId': command}, job['actor'])
    elif advanced == 'local-effect':
        environments.storage.allocate(app.data_dir, allocated)
    elif advanced == 'owner-lock':
        held = environments.FileLock(str(app.data_dir / 'workspace-resources' / ('environment-'+allocated['id']+'.lock')), timeout=0)
        held.acquire()
    recovered = app.test_queue = TestQueue(app); recovered.start = lambda: None
    try:
        if end == 'cancel':
            await app.dispatch('workspace.tests.cancel', {**scope(app), 'requestId': 'first'})
        elif end == 'expire':
            receipt['job']['expiresAt'] = time.time()-1; recovered.save(receipt)
        else:
            await app.dispatch('conversation.stop', {'sessionId': scope(app)['sessionId']})
        await drive(recovered)
    finally:
        if held: held.release()
    owner = environments.get(app.data_dir, scope(app)['sessionId'], allocated['id'])
    assert (owner['status'] == 'retired') == (advanced == 'none')
    if end == 'stop':
        assert record(recovered, app, 'second')['job']['status'] == 'cancelled'
    elif advanced != 'none':
        second = record(recovered, app, 'second')['job']
        assert second['status'] == 'queued' and 'cleanup' in second['detail']
    else:
        await drive(recovered)
        assert record(recovered, app, 'second')['job']['status'] == 'passed'
    assert not [a for a, _ in backend] if advanced != 'none' or end == 'stop' else True


async def test_default_uncapped_per_chat_pending_keeps_global_bound_and_original_expiry(app,queue,backend):
    await submit(app,'finite-wait',waitSeconds=20)
    original=record(queue,app,'finite-wait')['job'].copy()
    for index in range(63):
        await submit(app,f'uncapped-{index}',independent=True)
    assert queue.pending_count(sid=scope(app)['sessionId'])==64
    assert record(queue,app,'uncapped-62')['job']['expiresAt'] is None
    with pytest.raises(AppError,match='saved queue limit'):
        await submit(app,'global-bound')
    assert record(queue,app,'finite-wait')['job']==original
    assert not backend

@pytest.mark.parametrize('remote',[False,True])
async def test_zero_environment_count_allows_independent_overlap(app,queue,backend,monkeypatch,remote):
    if remote:
        remote_backend(app,monkeypatch)
    # Explicit controlled physical capacity is independent of the per-chat policy.
    monkeypatch.setattr(environments,'capacity',lambda *a:{'available':True,'limits':{'max_running':0},'counts':{'active':0}})
    entered=[];release=threading.Event();invoke=environments.invoke
    def controlled(config,home,row,action,args):
        if action=='execute':
            entered.append(row['id']);assert release.wait(10)
        return invoke(config,home,row,action,args)
    monkeypatch.setattr(environments,'invoke',controlled)
    for name in ('first','second','third'):
        await submit(app,name,independent=True)
    try:
        await asyncio.gather(queue.tick(),queue.tick())
        await until(lambda:len(entered)==3)
        assert len(set(entered))==3
    finally:
        release.set();await asyncio.gather(*queue.tasks.values())
    assert all(record(queue,app,name)['job']['status']=='passed' for name in ('first','second','third'))
    assert all(environments.get(app.data_dir,scope(app)['sessionId'],resource)['status']=='retired' for resource in entered)
