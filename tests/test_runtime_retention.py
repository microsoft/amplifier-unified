import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from amplifier_web.deployment import validate_server
from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_worker import Worker

SCRIPT = r'''
import json,sys,time
parked=False
for line in sys.stdin:
    data=json.loads(line); op=data['op']; result={}
    if op=='start':
        if data['session']['id'].startswith('slow'): time.sleep(.1)
        print(json.dumps({'type':'runtime.ready','report':{'session_id':data['session']['id']}}),flush=True)
        continue
    if op=='stop': break
    if op=='park':
        parked=True
        print(json.dumps({'type':'runtime.parked'}),flush=True)
        result={'parked':True}
    if op=='send':
        parked=False;result={'accepted':True}
    if op=='control':
        parked=False
        if data['operation']=='held':continue
        result={'ok':True}
    if op=='retire':
        time.sleep(.02)
        result={'retired':parked}
    print(json.dumps({'op':'reply','id':data['id'],'result':result}),flush=True)
    if op=='retire' and parked:break
'''


@pytest.fixture
async def manager():
    events = []
    async def emit(kind, data): events.append((kind, data))
    manager = RuntimeManager(command=[sys.executable, '-c', SCRIPT])
    manager.retention.wake = lambda: None
    manager.retention.clock = lambda: manager.now
    manager.now = 0
    manager.events, manager.emit = events, emit
    yield manager
    await manager.close()


async def warm(manager, identity):
    await manager.prewarm({'id': identity, 'workspace': '/tmp'}, manager.emit)
    return manager.workers[identity]['process']


async def test_count_evicts_oldest_settled_worker_and_input_restarts_without_replay(manager):
    manager.retention.settings['max_warm_workers'] = 2
    first = await warm(manager, 'a')
    manager.now = 10
    await warm(manager, 'b')
    manager.now = 20
    await warm(manager, 'c')
    await manager.retention.sweep()
    assert first.returncode == 0
    assert set(manager.workers) == {'b', 'c'}
    assert ('runtime.warmth', {'sessionId': 'a', 'status': 'cold'}) in manager.events
    assert not any(kind == 'runtime.status' and data.get('status') == 'stopped' for kind, data in manager.events)
    # Models the race where preparation completed just before retirement.
    result = await manager._request('a', 'send', text='new input', input_id='one')
    assert result['accepted']
    assert manager.workers['a']['process'] is not first
    assert not [event for event in manager.events if event[0] == 'runtime.error']


async def test_idle_deadline_and_navigation_do_not_extend_retention(manager):
    first = await warm(manager, 'a')
    manager.now = 12 * 3600 - 1
    await manager.prewarm({'id': 'a'}, manager.emit)
    await manager.retention.sweep()
    assert first.returncode is None
    assert manager.workers['a']['parked_at'] == 0
    manager.now += 1
    await manager.retention.sweep()
    assert first.returncode == 0


async def test_active_and_unanswered_commands_are_protected(manager):
    first = await warm(manager, 'a')
    await manager.send({'id': 'a'}, 'work', 'one', manager.emit)
    manager.retention.settings['max_warm_workers'] = 0
    manager.now = 100000
    await manager.retention.sweep()
    assert first.returncode is None
    held = asyncio.create_task(manager.control('a', 'held'))
    await asyncio.sleep(.03)
    held.cancel()
    await asyncio.gather(held, return_exceptions=True)
    row = manager.workers['a']
    assert row['inflight']
    row['parked'] = True  # A delayed status must not override admitted work.
    await manager.retention.sweep()
    assert first.returncode is None


async def test_worker_final_check_refuses_stale_parked_status(manager):
    first = await warm(manager, 'a')
    await manager.send({'id': 'a'}, 'work', 'one', manager.emit)
    manager.workers['a']['parked'] = True
    manager.retention.settings['max_warm_workers'] = 0
    await manager.retention.sweep()
    assert first.returncode is None
    assert not manager.workers['a']['closing']


async def test_uncertain_transport_write_keeps_retirement_fence(manager):
    process = await warm(manager, 'a')
    async def broken(*args): raise ConnectionResetError('fixture transport')
    with patch.object(manager, '_write', broken), pytest.raises(ConnectionResetError):
        await manager.control('a', 'tool.invoke')
    row = manager.workers['a']
    assert row['inflight']
    row['parked'] = True
    manager.retention.settings['max_warm_workers'] = 0
    await manager.retention.sweep()
    assert process.returncode is None


async def test_send_racing_retirement_waits_and_restarts(manager):
    first = await warm(manager, 'a')
    manager.retention.settings['max_warm_workers'] = 0
    retire = asyncio.create_task(manager.retention.sweep())
    while not manager.workers['a']['closing']:
        await asyncio.sleep(0)
    sent = asyncio.create_task(manager.send({'id': 'a'}, 'work', 'one', manager.emit))
    await retire
    assert (await sent)['accepted']
    assert first.returncode == 0
    assert manager.workers['a']['process'] is not first


async def test_background_startup_is_bounded_and_disabled_policy_does_not_start(manager):
    pending = [asyncio.create_task(warm(manager, f'slow-{i}')) for i in range(4)]
    await asyncio.sleep(.04)
    assert len(manager.workers) == 2
    await asyncio.gather(*pending)
    manager.retention.settings['prewarm_on_select'] = False
    await manager.prewarm({'id': 'not-started'}, manager.emit)
    assert 'not-started' not in manager.workers


async def test_disabling_preparation_skips_starts_already_waiting_in_queue(manager):
    await manager.retention.background.acquire()
    await manager.retention.background.acquire()
    queued = asyncio.create_task(manager.prewarm({'id': 'queued'}, manager.emit))
    await asyncio.sleep(0)
    manager.configure_retention({**manager.retention.settings, 'prewarm_on_select': False})
    manager.retention.background.release()
    manager.retention.background.release()
    await queued
    assert not manager.workers


async def test_startup_approval_can_resolve_before_initialization_is_ready():
    script = r'''
import json,sys
for line in sys.stdin:
    data=json.loads(line)
    if data['op']=='start':
        print(json.dumps({'type':'approval.requested','id':'setup','prompt':'Initialize fixture?','options':['allow','deny']}),flush=True)
    elif data['op']=='approval':
        print(json.dumps({'type':'runtime.ready','report':{}}),flush=True)
        print(json.dumps({'op':'reply','id':data['id'],'result':{'resolved':True}}),flush=True)
    elif data['op']=='stop':break
'''
    runtime = RuntimeManager(command=[sys.executable, '-c', script])
    requested = asyncio.Event()
    async def emit(kind, payload):
        if kind == 'approval.requested': requested.set()
    pending = asyncio.create_task(runtime.start({'id': 'approval-at-start'}, emit))
    try:
        await asyncio.wait_for(requested.wait(), 2)
        reply = await asyncio.wait_for(runtime.approval('approval-at-start', 'setup', 'allow'), 2)
        assert reply['resolved']
        await asyncio.wait_for(pending, 2)
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await runtime.close()


@pytest.mark.parametrize('pending', ['none', 'approval', 'bridge', 'generation', 'queued', 'inbox', 'job', 'remount', 'yielding'])
async def test_actual_worker_retirement_requires_settled_state(pending):
    worker = Worker()
    worker.parked = True
    worker.runtime = SimpleNamespace(queued_inputs=0, inbox=asyncio.Queue(), generation=None)
    loop = SimpleNamespace(pending=[], _active_jobs=lambda: [])
    worker.session = SimpleNamespace(coordinator={'orchestrator': loop})
    if pending == 'approval': worker.approvals['a'] = object()
    if pending == 'bridge': worker.bridges['a'] = object()
    if pending == 'generation': worker.runtime.generation = object()
    if pending == 'queued': worker.runtime.queued_inputs = 1
    if pending == 'inbox': worker.runtime.inbox.put_nowait(object())
    if pending == 'job': loop._active_jobs = lambda: ['job']
    if pending == 'remount': worker.remounting = True
    if pending == 'yielding': worker.ownership.yielding = True
    with patch('amplifier_web.runtime_worker.publish') as publish:
        await worker.command({'op': 'retire', 'id': 'retirement'})
        assert publish.call_args.args[0]['result']['retired'] == (pending == 'none')
        assert worker.shutdown.is_set() == (pending == 'none')


def test_defaults_and_configuration_roundtrip():
    from amplifier_web.deployment import setting_set
    config = validate_server({})
    assert config['runtime']['max_warm_workers'] == 32
    assert config['runtime']['idle_timeout_hours'] == 12
    config = setting_set(config, 'runtime.idle_timeout_hours', 24 * 14)
    assert config['runtime']['idle_timeout_hours'] == 336
    assert validate_server({'runtime': {'max_warm_workers': 100}})['runtime']['idle_timeout_hours'] == 12


async def test_shared_settings_action_persists_and_applies_without_restart(tmp_path):
    from amplifier_web.service import AppService
    from amplifier_web.deployment import load_server_config
    runtime = RuntimeManager()
    service = AppService(tmp_path/'app', runtime, workspace=tmp_path)
    try:
        result = await service.dispatch('runtime.retention.update', {'patch': {'max_warm_workers': 100, 'idle_timeout_hours': 336}})
        assert result['accepted']
        assert result['state']['runtime']['retention']['max_warm_workers'] == 100
        assert runtime.retention.settings['idle_timeout_hours'] == 336
        assert load_server_config(service.data_dir)['runtime']['max_warm_workers'] == 100
        assert not runtime.workers
    finally:
        await service.close()


@pytest.mark.parametrize('setting,value', [('max_warm_workers', -1), ('max_warm_workers', True),
    ('idle_timeout_hours', float('nan')), ('idle_timeout_hours', float('inf')), ('idle_timeout_hours', -1),
    ('idle_timeout_hours', True), ('max_background_starts', 0), ('prewarm_on_select', 'true')])
def test_invalid_limits(setting, value):
    with pytest.raises(ValueError): validate_server({'runtime': {setting: value}})
