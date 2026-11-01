"""Background owners cannot commit another operation's retained file transaction."""
import asyncio
import json
import sqlite3
import threading
import time

import pytest

from amplifier_web import resource_files, task_environments as environments
from test_result_artifacts import app, setup, declare, read
from test_collaborative_workspaces import send, settled
from test_task_environments import backend
from test_test_queue import queue, submit, record


def notifications(app, identity):
    return [json.loads(row[0]) for row in app.db.execute(
        "SELECT receipt FROM commands WHERE json_extract(receipt,'$.dependencyRequestId')=?",
        (identity,))]


async def retain_while(app, peer, args, monkeypatch, competing, *, fail=False, contenders=()):
    """Pause the real file worker after SQLite stages its resource savepoint."""
    entered, release = threading.Event(), threading.Event()
    write = resource_files.write_prepared
    captured = []
    def blocked(directory, prepared):
        if entered.is_set():
            return write(directory, prepared)
        captured.append(prepared.identity)
        entered.set()
        assert release.wait(10)
        if fail:
            raise OSError('controlled artifact write failure')
        return write(directory, prepared)
    monkeypatch.setattr(resource_files, 'write_prepared', blocked)
    reply = asyncio.create_task(declare(app, peer, args))
    other = None
    waiting = None
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert app.lock.locked() and app.db.in_transaction
        attempted = asyncio.Event()
        acquire = app.lock.acquire
        participants = set(contenders)
        async def observed_acquire():
            if asyncio.current_task() in participants and app.lock.locked():
                attempted.set()
            return await acquire()
        monkeypatch.setattr(app.lock, 'acquire', observed_acquire)
        # A broken owner may commit before reaching another locked backend
        # callback. Observe that exact SQL boundary without a timing delay.
        app.db.set_trace_callback(lambda sql: attempted.set() if sql.strip().upper() == 'COMMIT' else None)
        other = asyncio.create_task(competing())
        participants.add(other)
        waiting = asyncio.create_task(attempted.wait())
        done, _ = await asyncio.wait({other, waiting}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        assert done, 'The competing owner did not reach publication or completion'
        assert app.db.in_transaction, 'A background owner committed the retained reply transaction'
        with sqlite3.connect(f'file:{app.data_dir / "app.sqlite3"}?mode=ro', uri=True) as observer:
            assert observer.execute('SELECT 1 FROM state_resources WHERE id=?', (captured[0],)).fetchone() is None
        release.set()
        if fail:
            with pytest.raises(OSError, match='controlled artifact write failure'):
                await reply
            assert app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (captured[0],)).fetchone() is None
            assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 0
            assert not app.collaboration.receipt(args['requestId']).get('response')
        else:
            answer = await reply
            assert answer['result']['artifactVerification'] == 'verified'
            assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 1
        await asyncio.wait_for(other, 10)
    finally:
        release.set()
        app.db.set_trace_callback(None)
        if waiting:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        await asyncio.gather(reply, *([other] if other else []), return_exceptions=True)


@pytest.mark.parametrize('owner', ['continuation', 'test-notification', 'test-expiry'])
@pytest.mark.parametrize('fail', [False, True])
async def test_background_outcomes_wait_for_retained_reply_transaction(app, queue, backend, tmp_path, monkeypatch, owner, fail):
    source, peer, file, args = await setup(app, tmp_path)
    queue.reservations.next_due = time.monotonic() + 1000
    if owner == 'continuation':
        await send(app, 'waiting-request', mode='auto', returnNotification=True)
        await settled(app, 'waiting-request', 'accepted')
        async with app.lock:
            request = app.collaboration.receipt('waiting-request')
            request['subscription']['status'] = 'backpressured'
            request['terminalOutcome'] = {'status': 'observed', 'kind': 'result', 'outcome': 'failed',
                                         'text': 'Retained failure', 'qualified': False, 'outcomeRevision': 1}
            app.collaboration.save(request['requestId'], request)
            app.db.commit()
        identity = 'waiting-request'
        async def competing():
            await app.collaboration.drain(source['id'])
    else:
        await submit(app, notify=True)
        async with app.lock:
            receipt = record(queue, app)
            identity = receipt['requestId']
            if owner == 'test-expiry':
                receipt['job']['expiresAt'] = time.time() - 1
            else:
                receipt['job']['status'] = 'failed'
                receipt['terminalOutcome'] = {'status': 'observed', 'kind': 'test', 'outcome': 'failed',
                                             'text': 'Retained check failure', 'qualified': False, 'outcomeRevision': 1}
            queue.save(receipt)
        async def competing():
            await queue.tick()
    await retain_while(app, peer, args, monkeypatch, competing, fail=fail)
    assert file.read_bytes() == b'Original exact bytes'
    if not fail:
        assert (await read(app, source, args))['text'] == 'Original exact bytes'
        duplicate = await declare(app, peer, args)
        assert duplicate['duplicate']
    assert len(notifications(app, identity)) == 1
    await competing()
    assert len(notifications(app, identity)) == 1
    assert not backend  # Retention and outcome delivery do not replay a test.


@pytest.mark.parametrize('phase', ['allocation', 'start', 'execute', 'observe', 'stop', 'remove'])
async def test_test_lifecycle_publishes_after_retained_writer(app, queue, backend, tmp_path, monkeypatch, phase):
    source, peer, _, args = await setup(app, tmp_path)
    queue.reservations.next_due = time.monotonic() + 1000
    await submit(app)
    entered, release = asyncio.Event(), asyncio.Event()
    effect = queue.effect
    prepare = environments.prepare
    allocation_entered, allocation_release = threading.Event(), threading.Event()
    if phase in {'allocation', 'observe'}:
        operation = prepare if phase == 'allocation' else environments.observe_job
        def delayed(*values, **kwargs):
            result = operation(*values, **kwargs)
            allocation_entered.set()
            assert allocation_release.wait(10)
            return result
        monkeypatch.setattr(environments, 'prepare' if phase == 'allocation' else 'observe_job', delayed)
        if phase == 'observe':
            invoke = environments.invoke
            def running(config, home, row, action, args):
                result = invoke(config, home, row, action, args)
                if action == 'execute':
                    result['job'].update(state='running', terminal=False)
                return result
            monkeypatch.setattr(environments, 'invoke', running)
            queue.poll_seconds = 0
    else:
        async def delayed(receipt, config, action, **kwargs):
            result = await effect(receipt, config, action, **kwargs)
            if action == phase:
                entered.set()
                await release.wait()
            return result
        monkeypatch.setattr(queue, 'effect', delayed)
    await queue.tick()
    task, = queue.tasks.values()
    try:
        if phase in {'allocation', 'observe'}:
            assert await asyncio.to_thread(allocation_entered.wait, 10)
        else:
            await asyncio.wait_for(entered.wait(), 10)
        async def competing():
            allocation_release.set()
            release.set()
            await task
        await retain_while(app, peer, args, monkeypatch, competing, contenders={task})
        assert record(queue, app)['job']['status'] == 'passed'
        assert (await read(app, source, args))['text'] == 'Original exact bytes'
        assert [action for action, _ in backend] == ['prepare', 'start', 'execute', *(['job_read'] if phase == 'observe' else []), 'stop', 'remove']
    finally:
        allocation_release.set()
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_retired_test_recovery_waits_for_retained_writer(app, queue, backend, tmp_path, monkeypatch):
    source, peer, _, args = await setup(app, tmp_path)
    queue.reservations.next_due = time.monotonic() + 1000
    await submit(app)
    await queue.tick()
    await asyncio.gather(*queue.tasks.values())
    before = list(backend)
    identity = record(queue, app)['requestId']
    async def competing():
        await queue.recover(identity)
    await retain_while(app, peer, args, monkeypatch, competing)
    assert record(queue, app)['job']['status'] == 'passed'
    assert backend == before
    assert (await read(app, source, args))['text'] == 'Original exact bytes'


@pytest.mark.parametrize('missing_plan', [False, True])
async def test_task_creation_publication_waits_for_retained_writer(app, tmp_path, monkeypatch, missing_plan):
    from test_peer_creation_operation import plan, commission, initial_accepted
    source, peer, _, args = await setup(app, tmp_path)
    plan(app, source)
    ready, release = asyncio.Event(), asyncio.Event()
    original = app.collaboration.finish_create
    async def delayed(identity):
        ready.set()
        await release.wait()
        await original(identity)
    monkeypatch.setattr(app.collaboration, 'finish_create', delayed)
    created = await commission(app, source)
    await ready.wait()
    task, = [task for task in app.tasks if task.get_coro().__name__ == 'delayed']
    if missing_plan:
        async with app.lock:
            app.db.execute('DELETE FROM collaboration_preparations WHERE request_id=?', (created['requestId'],))
            app.db.commit()
    async def competing():
        release.set()
        await task
    await retain_while(app, peer, args, monkeypatch, competing, contenders={task})
    retained = app.collaboration.receipt(created['requestId'])
    if missing_plan:
        assert retained['delivery'] == 'unknown'
        assert len(app.state['sessions']) == 2
        assert len(notifications(app, created['requestId'])) == 1
    else:
        await initial_accepted(app, created['initialInputId'])
        assert retained['phase'] == 'input_committed'
        assert len(app.state['sessions']) == 3
    assert (await read(app, source, args))['text'] == 'Original exact bytes'
    assert (await commission(app, source))['duplicate']
