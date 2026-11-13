"""Transient connector settings follow native worker admission and cleanup."""
import asyncio
import copy
from contextlib import asynccontextmanager
import json
import sys
from types import SimpleNamespace

import pytest

from amplifier_web.execution_owner import ExecutionOwner
from amplifier_web.host.initialization import initialization_hooks
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService


SERVERS = {"context": {"type": "http", "url": "http://127.0.0.1/context/mcp",
                       "headers": {"Authorization": "Bearer synthetic-private-lease"}}}
WORKER = r'''
import json,sys
pending=None
for line in sys.stdin:
    row=json.loads(line)
    if row['op']=='start':
        print(json.dumps({'type':'runtime.ready','report':{}}),flush=True)
    elif row['op']=='send':
        pending=row['id']
        print(json.dumps({'type':'input.delivered','input_id':row['input_id']}),flush=True)
    elif row['op']=='dependencies':
        if pending:
            print(json.dumps({'op':'reply','id':pending,'result':{'accepted':True}}),flush=True)
            pending=None
        print(json.dumps({'op':'reply','id':row['id'],'result':{}}),flush=True)
    elif row['op']=='control':
        print(json.dumps({'op':'reply','id':row['id'],'result':{'operation':row['operation']}}),flush=True)
    elif row['op']=='stop':break
'''


async def test_initialization_supplement_is_not_session_configuration():
    capabilities, order = {}, []
    session = SimpleNamespace(config={"tools": [{"module": "tool-mcp"}]},
        coordinator=SimpleNamespace(register_capability=capabilities.__setitem__))
    hooks = initialization_hooks(isolation=None, decision_accounts=SimpleNamespace(records=[]),
        routing=None, retain=lambda value: order.append(value), ephemeral_servers=SERVERS)
    await hooks['before_initialize'](session)
    assert order == [session]  # Partial initialization has an owner before mount can fail.
    assert capabilities['mcp.ephemeral_servers'] == SERVERS
    assert 'synthetic-private-lease' not in json.dumps(session.config)


async def test_metadata_attachment_keeps_credentials_out_of_durable_owner(tmp_path):
    runtime = RuntimeManager(command=[sys.executable, '-u', '-c', WORKER])
    app = AppService(tmp_path/'app', runtime, workspace=tmp_path)
    owner = ExecutionOwner(app, workspace=tmp_path, bundle='foundation', origin='ui', enable_mcp=True)
    try:
        identity = await owner.create(str(tmp_path), mcp_servers=SERVERS)
        assert not runtime.workers
        assert 'synthetic-private-lease' not in json.dumps(app.state)
        assert 'synthetic-private-lease' not in '\n'.join(app.db.iterdump())
        await owner.release(identity)
        assert identity not in runtime._ephemeral_mcp
        assert await owner.attach(identity, str(tmp_path), mcp_servers=SERVERS) == identity
        assert not runtime.workers
        assert 'synthetic-private-lease' not in '\n'.join(app.db.iterdump())
    finally:
        await owner.close()
    assert not runtime._ephemeral_mcp


async def test_release_keeps_admitted_work_but_prevents_live_credential_reuse(tmp_path):
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', WORKER], startup_timeout=3,
                             retention={'prewarm_on_select': False})
    manager.home = tmp_path/'app'
    session = {'id': 'owned', 'workspace': str(tmp_path), 'bundle': 'fixture'}
    events, writes = [], []
    delivered = asyncio.Event()
    async def emit(kind, payload):
        events.append((kind, payload))
        if kind == 'runtime.status' and payload.get('event') == 'input.delivered':delivered.set()
    original = manager._write
    async def write(row, value):
        writes.append(copy.deepcopy(value))
        await original(row, value)
    manager._write = write
    servers = copy.deepcopy(SERVERS)
    await manager.bind_ephemeral_mcp('owned', 'attachment', servers)
    servers['context']['headers']['Authorization'] = 'caller mutated'
    pending = asyncio.create_task(manager.send(session, 'work', 'input', emit))
    try:
        await asyncio.wait_for(delivered.wait(), 4)
        assert writes[0]['session']['ephemeralMcpServers'] == SERVERS
        assert 'synthetic-private-lease' not in json.dumps(manager.workers['owned']['start_session'])
        assert 'synthetic-private-lease' not in json.dumps(session)
        manager.release_ephemeral_mcp('owned', 'foreign')
        assert 'owned' in manager._ephemeral_mcp
        manager.release_ephemeral_mcp('owned', 'attachment')
        assert not pending.done()
        assert not any(row['op'] == 'stop' for row in writes)
        await manager._request('owned', 'dependencies')
        assert await pending == {'accepted': True}
        with pytest.raises(RuntimeError, match='released'):
            await manager.send(session, 'new work', 'new-input', emit)
        assert await manager.control('owned', 'capacity.read', {}) == {'operation':'capacity.read'}
        with pytest.raises(RuntimeError, match='released'):
            await manager.control('owned', 'tool.invoke', {'name':'context_read'})
        with pytest.raises(RuntimeError, match='released'):
            await manager.message_worker('owned', 'child', 'new input', 'child-input')
        with pytest.raises(ValueError, match='another MCP attachment'):
            await manager.bind_ephemeral_mcp('owned', 'next', SERVERS)
        assert [row['op'] for row in writes].count('send') == 1
        assert 'synthetic-private-lease' not in json.dumps(events)
        await manager.stop('owned')
        await manager.bind_ephemeral_mcp('owned', 'next', SERVERS)
        await manager.start(session, emit)
        assert len([row for row in writes if row['op']=='start']) == 2
    finally:
        await manager.close()
        await asyncio.gather(pending, return_exceptions=True)
    assert not manager._ephemeral_mcp


async def test_release_during_startup_never_sends_transient_settings(tmp_path):
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', WORKER], startup_timeout=3)
    manager.home = tmp_path/'app'
    entered, proceed = asyncio.Event(), asyncio.Event()
    writes=[]
    original=manager._write
    async def write(row,value):
        writes.append(copy.deepcopy(value));await original(row,value)
    manager._write=write
    async def emit(kind,payload):
        if kind=='runtime.status' and payload.get('status')=='starting':
            entered.set();await proceed.wait()
    await manager.bind_ephemeral_mcp('owned','attachment',SERVERS)
    pending=asyncio.create_task(manager.start({'id':'owned','workspace':str(tmp_path),'bundle':'fixture'},emit))
    try:
        await asyncio.wait_for(entered.wait(),3)
        manager.release_ephemeral_mcp('owned','attachment');proceed.set()
        with pytest.raises(RuntimeError,match='released'):
            await asyncio.wait_for(pending,5)
        assert all(row['op']!='start' for row in writes)
        assert not manager.workers
    finally:
        proceed.set();await manager.close()
        await asyncio.gather(pending,return_exceptions=True)


@pytest.mark.parametrize('disposition', ['retired', 'busy', 'protected', 'connected', 'refused'])
async def test_reconnect_retires_only_released_settled_mcp_worker(tmp_path, disposition):
    # A real subprocess acknowledges/refuses canonical retirement. No model or
    # mocked stop path stands in for the exclusive worker's reply.
    reply = repr(disposition != 'refused')
    worker = WORKER.replace("    elif row['op']=='stop':break", f"""    elif row['op']=='retire':
        print(json.dumps({{'op':'reply','id':row['id'],'result':{{'retired':{reply}}}}}),flush=True)
    elif row['op']=='stop':break""")
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', worker], startup_timeout=3)
    manager.home = tmp_path/'app'
    session = {'id': 'owned', 'workspace': str(tmp_path), 'bundle': 'fixture'}
    writes = []
    original = manager._write
    async def write(row, value):
        writes.append(value['op'])
        await original(row, value)
    async def emit(*_):
        pass
    manager._write = write
    try:
        await manager.bind_ephemeral_mcp('owned', 'first', SERVERS)
        await manager.start(session, emit)
        first = manager.workers['owned']
        first['idle'] = disposition != 'busy'
        manager.retention.protected = lambda sid: disposition == 'protected'
        if disposition != 'connected':
            manager.release_ephemeral_mcp('owned', 'first')
        if disposition == 'retired':
            await manager.bind_ephemeral_mcp('owned', 'second', SERVERS)
            assert first['process'].returncode is not None
            assert 'owned' not in manager.workers
            assert manager._ephemeral_mcp['owned'] == ('second', SERVERS)
            assert writes.count('retire') == 1
            await manager.start(session, emit)
            assert manager.workers['owned']['process'].pid != first['process'].pid
            assert writes.count('start') == 2
        else:
            with pytest.raises(ValueError, match='MCP attachment|retirement'):
                await manager.bind_ephemeral_mcp('owned', 'second', SERVERS)
            assert manager.workers['owned'] is first and first['process'].returncode is None
            assert manager._ephemeral_mcp.get('owned') == (('first', SERVERS) if disposition == 'connected' else None)
            assert writes.count('retire') == (1 if disposition == 'refused' else 0)
            assert 'stop' not in writes
        assert 'send' not in writes
    finally:
        await manager.close()


async def retiring_attachment(tmp_path):
    worker = WORKER.replace("    elif row['op']=='stop':break", """    elif row['op']=='retire':
        print(json.dumps({'op':'reply','id':row['id'],'result':{'retired':True}}),flush=True)
    elif row['op']=='stop':break""")
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', worker], startup_timeout=3)
    manager.home = tmp_path/'app'
    entered, proceed = asyncio.Event(), asyncio.Event()
    writes = []
    original = manager._write
    async def write(row, value):
        if value['op'] == 'retire':
            entered.set()
            await proceed.wait()
        writes.append(value['op'])
        await original(row, value)
    async def emit(*_):
        pass
    manager._write = write
    try:
        await manager.bind_ephemeral_mcp('owned', 'first', SERVERS)
        await manager.start({'id': 'owned', 'workspace': str(tmp_path), 'bundle': 'fixture'}, emit)
    except BaseException:
        await manager.close()
        raise
    row = manager.workers['owned']
    row['idle'] = True
    manager.release_ephemeral_mcp('owned', 'first')
    return manager, row, entered, proceed, writes


@pytest.mark.parametrize('interruption', ['cancelled', 'timeout'])
async def test_detached_reconnect_cannot_cancel_retirement_or_publish_late_binding(tmp_path, interruption):
    manager, row, entered, proceed, writes = await retiring_attachment(tmp_path)
    if interruption == 'timeout':
        manager.retention.reply_timeout = .02
    binding = asyncio.create_task(manager.bind_ephemeral_mcp('owned', 'detached', SERVERS))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        retirement = row['retirement_task']
        if interruption == 'cancelled':
            binding.cancel()
        with pytest.raises(asyncio.CancelledError if interruption == 'cancelled' else TimeoutError):
            await binding
        assert not retirement.done() and row['closing']
        assert manager.workers['owned'] is row and row['process'].returncode is None
        assert not manager._ephemeral_mcp
        assert 'stop' not in writes and 'send' not in writes

        proceed.set()
        await asyncio.wait_for(asyncio.shield(retirement), 2)
        assert row['process'].returncode is not None
        assert 'owned' not in manager.workers and not manager._ephemeral_mcp
        assert writes.count('retire') == 1 and writes.count('stop') == 1
        assert 'send' not in writes  # Neither timeout nor detachment replays work.
    finally:
        proceed.set()
        await manager.close()
        await asyncio.gather(binding, return_exceptions=True)


async def test_retirement_wait_rechecks_an_attachment_that_won_admission(tmp_path):
    manager, row, entered, proceed, writes = await retiring_attachment(tmp_path)
    other_bound = asyncio.Event()
    admission = manager._admission
    first = second = None
    @asynccontextmanager
    async def competing_admission(identity):
        # Let the other already waiting attachment win after the actual worker
        # retirement. The older caller must recheck, not overwrite that winner.
        if asyncio.current_task() is first and entered.is_set():
            await other_bound.wait()
        async with admission(identity):
            yield
    manager._admission = competing_admission
    async def connect_other():
        await manager.bind_ephemeral_mcp('owned', 'winner', SERVERS)
        other_bound.set()
    try:
        first = asyncio.create_task(manager.bind_ephemeral_mcp('owned', 'older', SERVERS))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(connect_other())
        proceed.set()
        await asyncio.wait_for(second, 2)
        with pytest.raises(ValueError, match='owner changed'):
            await asyncio.wait_for(first, 2)
        assert manager._ephemeral_mcp['owned'] == ('winner', SERVERS)
        assert row['process'].returncode is not None and 'owned' not in manager.workers
        assert writes.count('retire') == 1 and writes.count('stop') == 1
        assert 'send' not in writes
    finally:
        proceed.set()
        other_bound.set()
        await manager.close()
        await asyncio.gather(*(task for task in (first, second) if task), return_exceptions=True)
