"""Confirmed native process exit releases only its own unfinished worker rows."""
import asyncio
import copy
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService
from amplifier_web.update_blockers import blockers


FIXTURE = r'''
import json, sys, time
from pathlib import Path
release = Path(sys.argv[1])
def emit(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    command = json.loads(line)
    if command['op'] == 'start':
        emit({'type': 'runtime.ready', 'report': {}})
    elif command['op'] == 'control' and command['operation'] == 'fixture.delegate':
        emit({'type': 'job.queued', 'job_id': 'queued-job', 'call_id': 'delegate-call'})
        emit({'type': 'child.updated', 'sessionId': 'owned-child', 'runId': 'child-run',
              'callId': 'delegate-call', 'status': 'running'})
        emit({'type': 'child.updated', 'sessionId': 'persistent-child', 'runId': 'persistent-run',
              'status': 'idle', 'persistent': True, 'report': 'Previous result', 'reportId': 'saved'})
        emit({'op': 'reply', 'id': command['id'], 'result': {'ok': True}})
    elif command['op'] == 'stop' or command.get('operation') == 'fixture.exit':
        if command['op'] == 'stop': emit({'type': 'session.closed'})
        else: emit({'op': 'reply', 'id': command['id'], 'result': {'ok': True}})
        while not release.exists(): time.sleep(.01)
        break
'''


@pytest.mark.parametrize('reason', ['stopped', 'shutdown', 'unexpected'])
async def test_exit_settles_owned_rows_only_after_process_is_reaped(tmp_path, reason):
    fixture, release = tmp_path / 'worker.py', tmp_path / 'release'
    fixture.write_text(FIXTURE)
    runtime = RuntimeManager(command=[sys.executable, str(fixture), str(release)])
    service = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    session = service._new_session({'title': 'Owned runtime'})
    peer = service._new_session({'title': 'Independent peer'})
    service.state['sessions'] = [session, peer]
    peer['status'] = 'working'
    peer['workers'] = [{'id': 'independent-child', 'status': 'running'}]
    peer_before = copy.deepcopy(peer)
    closed, ended = asyncio.Event(), asyncio.Event()
    async def emit(kind, payload):
        await service.on_runtime_event(kind, payload)
        if kind == 'runtime.status' and payload.get('event') == 'session.closed': closed.set()
        if kind == 'runtime.ended': ended.set()
    try:
        await runtime.start(session, emit)
        process = runtime.workers[session['id']]['process']
        await runtime.control(session['id'], 'fixture.delegate')
        assert len(session['workers']) == 3
        target = {'sessionId': session['id'], 'workerId': 'owned-child'}
        before = (await service.app_bridge('dispatch', {'action': 'coordination.wait',
            'args': {'targets': [target], 'waitMs': 0}}, session['id']))['result']
        if reason == 'unexpected':
            await runtime.control(session['id'], 'fixture.exit')
            stopping = None
        else:
            stopping = asyncio.create_task(runtime.stop(session['id'], reason=reason))
            if reason == 'stopped': await asyncio.wait_for(closed.wait(), 5)
        assert process.returncode is None
        assert any(row.get('sessionId') == session['id'] and row['kind'] == 'worker' for row in blockers(service))
        release.touch()
        await asyncio.wait_for(ended.wait(), 5)
        if stopping: await asyncio.wait_for(stopping, 5)
        assert process.returncode == 0
        assert {worker['status'] for worker in session['workers']} == {'interrupted'}
        assert session['workers'][2]['report'] == 'Previous result'
        assert session['workers'][2]['reportId'] == 'saved'
        after = (await service.app_bridge('dispatch', {'action': 'coordination.wait', 'args': {
            'targets': [{**target, 'afterCursor': before['targets'][0]['nextCursor']}],
            'waitMs': 0}}, session['id']))['result']
        assert after['changed']
        assert after['targets'][0]['status'] == 'interrupted'
        assert after['targets'][0]['results'] == []
        assert not any(row.get('sessionId') == session['id'] and row['kind'] == 'worker' for row in blockers(service))
        assert peer == peer_before
        assert any(row.get('sessionId') == peer['id'] for row in blockers(service))
        peer['status'] = 'idle'
        peer['workers'][0]['status'] = 'completed'
        assert not blockers(service)
    finally:
        release.touch()
        await service.close()


@pytest.mark.parametrize('ending', [{'backgroundOnly': True}, {'sessionId': 'other-root'}])
async def test_non_root_or_background_exit_cannot_settle_children(tmp_path, ending):
    service = AppService(tmp_path, SimpleNamespace(close=AsyncMock()), workspace=tmp_path)
    session = service._new_session({'title': 'Owned workers'})
    service.state['sessions'] = [session]
    session['workers'] = [{'id': 'owned-child', 'status': 'running', 'runtimeOwnerId': 'process'}]
    before = copy.deepcopy(session['workers'])
    try:
        await service.on_runtime_event('runtime.ended', {'sessionId': session['id'],
            'rootSessionId': session['id'], 'runtimeOwnerId': 'process', **ending})
        assert session['workers'] == before
    finally:
        await service.close()


@pytest.mark.parametrize('owner', [None, 'old-process'])
async def test_exit_preserves_unbound_replaced_and_terminal_rows(tmp_path, owner):
    service = AppService(tmp_path, SimpleNamespace(close=AsyncMock()), workspace=tmp_path)
    session = service._new_session({'title': 'Retained workers'})
    service.state['sessions'] = [session]
    session['workers'] = [
        {'id': 'unknown-owner', 'status': 'running'},
        {'id': 'replacement', 'status': 'running', 'runtimeOwnerId': 'new-process', 'runId': 'new-run'},
        *({'id': status, 'status': status, 'runtimeOwnerId': 'old-process', 'report': 'Saved result'}
          for status in ('completed', 'error', 'cancelled', 'interrupted', 'outcome_unknown')),
    ]
    before = copy.deepcopy(session['workers'])
    try:
        await service.on_runtime_event('runtime.ended', {'sessionId': session['id'],
            'status': 'interrupted', **({'runtimeOwnerId': owner} if owner else {})})
        assert session['workers'] == before
    finally:
        await service.close()
