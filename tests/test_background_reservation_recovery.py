"""Actual maintenance entry point; only the exact native observation is controlled."""
import asyncio
from contextlib import closing
import copy
import json
import subprocess
import sys
import threading
import time
import uuid

from filelock import FileLock
import pytest

from amplifier_web import task_environments as owner
from amplifier_web import test_host_resources as host
from test_configuration_authority import app
from test_task_environments import backend, start, scope
from test_test_host_resources import machine, native_backend
from test_stopped_reservation_recovery import lost_check, inspect
from test_environment_recovery import observation


@pytest.fixture(autouse=True)
def local_fixture(monkeypatch):
    # The shared controlled backend is selected through configuration(); expose
    # that same qualified fixture as local, without widening production lookup.
    monkeypatch.setattr(owner, 'local_configuration', lambda app: owner.configuration(app))


def fillers(app, row, count):
    with closing(owner.storage.connect(app.data_dir)) as db, db:
        for _ in range(count):
            value = {**row, 'id': str(uuid.uuid4()), 'backendAdmitted': False}
            owner.storage.save(db, value)


@pytest.mark.parametrize('state', ['stopped', 'removed'])
async def test_idle_maintenance_reaches_old_off_page_owner_without_waking_chat(app, native_backend, monkeypatch, state):
    row, original = await lost_check(app, monkeypatch)
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    fillers(app, raw, 12)
    with closing(owner.storage.connect(app.data_dir)) as db:
        latest = [item[0] for item in db.execute('SELECT id FROM allocations ORDER BY rowid DESC LIMIT 10')]
    assert row['id'] not in latest
    # The user is looking at a different root; there is no queued check/model.
    app.state['selectedSessionId'] = app.state['sessions'][1]['id']
    calls = inspect(monkeypatch, state)
    before = copy.deepcopy(app.operations.requests.list(row['sessionId']))
    runtime = copy.deepcopy(app.runtime.calls)
    monkeypatch.setattr(app, '_session', lambda *a, **kw: pytest.fail('maintenance hydrated a chat'))
    await app.test_queue.tick()
    assert host.snapshot()['reserved']['count'] == 0
    assert calls == ['reconcile']
    assert app.runtime.calls == runtime
    assert app.operations.requests.list(row['sessionId']) == before
    saved = owner.get(app.data_dir, row['sessionId'], row['id'])
    assert saved['lastOperation'] == original and saved['status'] != 'retired'
    assert saved['environmentState'] == raw['environmentState']
    assert saved['reservationRecovery']['state'] == 'released'
    assert owner.storage.observe(app.data_dir, saved)['observation'] == 'present'
    await app.test_queue.tick()
    assert calls == ['reconcile']


async def test_rotates_bounded_ledger_pages_and_does_not_scan_session_history(app, native_backend, monkeypatch):
    row, _ = await lost_check(app, monkeypatch)
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    fillers(app, raw, 35)
    # Saving the exact retained owner moves its SQLite row past the first page.
    owner.save(app.data_dir, raw, reason='retained owner')
    with closing(owner.storage.connect(app.data_dir)) as db:
        first_page = db.execute("SELECT rowid FROM allocations WHERE status!='retired' ORDER BY rowid LIMIT 32").fetchall()
    calls = inspect(monkeypatch)
    maintenance = app.test_queue.reservations
    await app.test_queue.tick()
    assert calls == [] and host.snapshot()['reserved']['count'] == 1
    assert len(first_page) == 32 and maintenance.cursor == first_page[-1][0]
    maintenance.next_due = 0
    await app.test_queue.tick()
    assert calls == ['reconcile'] and host.snapshot()['reserved']['count'] == 0


@pytest.mark.parametrize('status,changes', [
    ('running', {}), ('stopped', {'admissionSettled': False}),
    ('removed', {'absenceConfirmed': False}), ('stopped', {'rootOwner': 'foreign'}),
    ('stopped', {'operationId': 'new-launch'}), ('stopped', {'universeId': 'different'}),
    ('stopped', {'backendIdentity': {'backend': 'docker', 'daemon': 'different'}}),
])
async def test_maintenance_keeps_running_unsettled_and_foreign_leases(app, native_backend, monkeypatch, status, changes):
    row, original = await lost_check(app, monkeypatch)
    calls = inspect(monkeypatch, status, **changes)
    await app.test_queue.tick()
    assert calls == ['reconcile'] and host.snapshot()['reserved']['count'] == 1
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original
    assert 'reservationRecovery' not in owner.get(app.data_dir, row['sessionId'], row['id'])


@pytest.mark.parametrize('held', ['preparing', 'pending', 'locked', 'remote', 'stale-config', 'corrupt-lease', 'changed-row'])
async def test_maintenance_retains_unproven_owners(app, native_backend, monkeypatch, held):
    row, _ = await lost_check(app, monkeypatch)
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    if held == 'preparing': owner.save(app.data_dir, raw, preparationPending=True)
    if held == 'pending': owner.save(app.data_dir, raw, pendingRequest='in-flight')
    if held == 'remote': owner.save(app.data_dir, raw, testMachine={'id': 'registered'})
    if held == 'stale-config':
        path = app.data_dir / 'workspace-resources/backend-configs' / (raw['backendKey'] + '.json')
        value = json.loads(path.read_text())
        value['configuration']['python'] = '/changed/runtime'
        path.chmod(0o600)
        path.write_text(json.dumps(value))
    if held == 'corrupt-lease':
        with host.locked() as folder:
            path = folder / ('lease-' + host.digest(host.binding(raw)) + '.json')
            value = host.read(path)
            host.write(path, {**value, 'resources': {**value['resources'], 'cpus': 99}})
    calls = inspect(monkeypatch)
    if held == 'changed-row':
        original = owner.invoke
        def changed(config, home, value, action, args):
            result = original(config, home, value, action, args)
            owner.save(home, value, launchOperationId='replacement')
            return result
        monkeypatch.setattr(owner, 'invoke', changed)
    lock = FileLock(str(app.data_dir / 'workspace-resources' / ('environment-' + row['id'] + '.lock')))
    if held == 'locked':
        with lock: await app.test_queue.tick()
    else:
        await app.test_queue.tick()
    assert host.snapshot()['reserved']['count'] == 1
    assert calls == (['reconcile'] if held == 'changed-row' else [])


@pytest.mark.parametrize('failure', ['timeout', 'invalid'])
async def test_at_most_one_native_probe_even_if_it_fails(app, native_backend, monkeypatch, failure):
    first = await start(app)
    second = await start(app, 'second')
    calls = []
    def failed(config, home, row, action, args):
        calls.append(row['id'])
        assert action == 'reconcile'
        if failure == 'timeout': raise TimeoutError('unavailable')
        return observation(row, rootOwner='foreign')
    monkeypatch.setattr(owner, 'invoke', failed)
    await app.test_queue.tick()
    await app.test_queue.tick()
    assert calls == [first['resource']['id']]
    app.test_queue.reservations.next_due = 0
    await app.test_queue.tick()
    assert calls == [first['resource']['id'], second['resource']['id']]
    assert host.snapshot()['reserved']['count'] == 2


@pytest.mark.parametrize('paused', ['update', 'closed', 'queue-closing', 'lifecycle'])
async def test_maintenance_skips_host_transition(app, native_backend, monkeypatch, paused):
    await lost_check(app, monkeypatch)
    calls = inspect(monkeypatch)
    if paused == 'update': app.state['updates']['phase'] = 'activating'
    if paused == 'closed': app.closed = True
    if paused == 'queue-closing': app.test_queue.closing = True
    if paused == 'lifecycle': await app.runtime_lifecycle_lock.acquire()
    try:
        await app.test_queue.tick()
        assert not calls and host.snapshot()['reserved']['count'] == 1
    finally:
        if paused == 'lifecycle': app.runtime_lifecycle_lock.release()
        app.closed = False
        app.test_queue.closing = False


@pytest.mark.parametrize('change', ['cancel', 'update', 'shutdown'])
async def test_inflight_observation_cannot_settle_after_transition(app, native_backend, monkeypatch, change):
    row, _ = await lost_check(app, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    def blocked(config, home, value, action, args):
        entered.set()
        assert release.wait(5)
        return observation(value, 'stopped')
    monkeypatch.setattr(owner, 'invoke', blocked)
    task = asyncio.create_task(app.test_queue.tick())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        if change == 'cancel': task.cancel()
        elif change == 'update': app.state['updates']['phase'] = 'activating'
        else: app.begin_shutdown()
        await asyncio.sleep(0)
        assert app.runtime_lifecycle_lock.locked()
    finally:
        release.set()
    if change == 'cancel':
        with pytest.raises(asyncio.CancelledError): await task
    else: await task
    assert not app.runtime_lifecycle_lock.locked()
    assert host.snapshot()['reserved']['count'] == 1
    assert 'reservationRecovery' not in owner.get(app.data_dir, row['sessionId'], row['id'])
    app.closed = False


@pytest.mark.parametrize('boundary', ['before-probe', 'before-settle'])
async def test_busy_machine_authority_never_strands_maintenance_or_lifecycle(app, native_backend, monkeypatch, boundary):
    row, original = await lost_check(app, monkeypatch)
    processes = []
    def hold():
        # A different process owns the real flock. It self-releases after a
        # short deadline so the failing-before fixture cannot hang shutdown.
        process = subprocess.Popen([sys.executable, '-c',
            'import fcntl,sys,time; f=open(sys.argv[1],"r+"); fcntl.flock(f,fcntl.LOCK_EX); print("held",flush=True); time.sleep(2)',
            str(host.directory() / 'authority.lock')], stdout=subprocess.PIPE, text=True)
        processes.append(process)
        assert process.stdout.readline().strip() == 'held'
    calls = []
    def inspect_owner(config, home, value, action, args):
        calls.append(action)
        assert action == 'reconcile'
        if boundary == 'before-settle': hold()
        return observation(value, 'stopped')
    monkeypatch.setattr(owner, 'invoke', inspect_owner)
    if boundary == 'before-probe': hold()
    try:
        started = time.monotonic()
        await app.test_queue.tick()
        assert time.monotonic() - started < 1
        assert not app.runtime_lifecycle_lock.locked()
        assert calls == ([] if boundary == 'before-probe' else ['reconcile'])
    finally:
        for process in processes:
            process.terminate()
            process.wait(timeout=3)
            process.stdout.close()
    assert host.snapshot()['reserved']['count'] == 1
    saved = owner.get(app.data_dir, row['sessionId'], row['id'])
    assert saved.get('reservationRecovery', {}).get('state') != 'released'
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original
