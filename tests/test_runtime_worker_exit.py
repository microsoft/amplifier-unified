"""Confirmed native process exit releases only its own unfinished worker rows."""
import asyncio
import copy
import json
import sys
import time
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


async def test_idle_old_process_exit_does_not_interrupt_preparing_next_input(tmp_path):
    """Match ordinary send: the new public turn precedes replacement prep."""
    from amplifier_web.execution import ensure_turn, finish
    runtime = RuntimeManager(command=['unused'])
    service = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    session = service._new_session({'title': 'Next input'})
    service.state['sessions'] = [session]
    ensure_turn(session, 'old-input')
    finish(session)
    ensure_turn(session, 'new-input')
    session['status'] = 'working'
    session['workers'] = [{'id': 'old-child', 'status': 'running', 'runtimeOwnerId': 'old-owner'}]
    try:
        # Actual runtime producer and service consumer, not a reason-only bypass.
        await runtime._execution_ended(session['id'], {'emit': service.on_runtime_event,
            'runtimeOwnerId': 'old-owner', 'inputId': 'old-input', 'stop_reason': 'configuration'}, 'stopped')
        turn = next(turn for turn in session['execution']['turns'] if turn['id'] == 'new-input')
        assert turn['phase'] == 'running'
        assert session['status'] == 'working' and 'failure' not in session and 'error' not in session
        assert session['workers'][0]['status'] == 'interrupted'
    finally:
        await service.close()


@pytest.mark.parametrize('reason', ['configuration', 'stopped', 'shutdown', None])
async def test_exit_settles_only_bound_foreground_and_keeps_unknown_delivery(tmp_path, reason):
    from amplifier_web.execution import ensure_turn
    runtime = RuntimeManager(command=['unused'])
    service = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    session = service._new_session({'title': 'Owned foreground'})
    service.state['sessions'] = [session]
    ensure_turn(session, 'owned-input')
    session['status'] = 'working'
    message = service._message(session, 'user', 'Unknown prior effects', inputId='owned-input',
                               delivery={'status': 'unknown'})
    try:
        await service.on_runtime_event('runtime.generation', {'sessionId': session['id'],
            'rootSessionId': session['id'], 'event': 'generation.started',
            'generation_id': 'owned-generation', 'initial_input_id': 'owned-input', 'observedAt':time.time(),
            'runtimeOwnerId': 'owned-process'})
        await runtime._execution_ended(session['id'], {'emit': service.on_runtime_event,
            'runtimeOwnerId': 'owned-process', **({'stop_reason': reason} if reason else {})}, 'interrupted')
        assert session['execution']['turns'][0]['phase'] == 'interrupted'
        assert session['failure']['category'] == 'execution_interrupted'
        assert message['delivery']['status'] == 'unknown'  # No inferred non-delivery/replay.
        assert session['collaborationGeneration']['terminal'] is True
        assert not runtime.workers
    finally:
        await service.close()


@pytest.mark.parametrize('generation_started', [False, True])
async def test_late_old_exit_cannot_settle_reused_input_new_attempt(tmp_path, generation_started):
    from amplifier_web.execution import ensure_turn
    runtime = RuntimeManager(command=['unused'])
    service = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    session = service._new_session({'title': 'Retry binding'})
    service.state['sessions'] = [session]
    tree = ensure_turn(session, 'same-input')
    session['status'] = 'working'
    turn = tree['turns'][0]
    turn.update(runtimeOwnerId='old-process', generationId='old-generation', phase='interrupted')
    # Ordinary explicit retry reopens this row before a replacement starts.
    turn.update(phase='running', retriedAt=time.time())
    turn.pop('runtimeOwnerId')
    turn.pop('generationId')
    if generation_started:
        await service.on_runtime_event('runtime.generation', {'sessionId': session['id'],
            'rootSessionId': session['id'], 'event': 'generation.started',
            'generation_id': 'new-generation', 'initial_input_id': 'same-input', 'observedAt':time.time(),
            'runtimeOwnerId': 'new-process'})
    tree['nodes'] = [
        {'id': 'old-child-call', 'kind': 'tool', 'phase': 'running', 'runtimeOwnerId': 'old-process',
         'turnId': 'same-input', 'sessionId': 'child', 'rootSessionId': session['id']},
        {'id': 'old-terminal', 'kind': 'tool', 'phase': 'completed', 'endedAt': 1,
         'runtimeOwnerId': 'old-process', 'turnId': 'same-input'},
        {'id': 'new-call', 'kind': 'llm', 'phase': 'running', 'runtimeOwnerId': 'new-process',
         'turnId': 'same-input'},
        {'id': 'old-background', 'kind': 'llm', 'phase': 'running', 'lifecycle': 'background',
         'runtimeOwnerId': 'old-process', 'turnId': 'same-input'},
        {'id': 'voice:session', 'kind': 'llm', 'phase': 'running', 'lifecycle': 'voice',
         'runtimeOwnerId': 'old-process', 'turnId': 'voice'},
    ]
    session['streamingText'] = 'Replacement stream'
    active = copy.deepcopy(session.get('collaborationGeneration'))
    try:
        await runtime._execution_ended(session['id'], {'emit': service.on_runtime_event,
            'runtimeOwnerId': 'old-process', 'stop_reason': 'configuration',
            'backgroundCalls': {'old-background'}}, 'stopped')
        assert turn['phase'] == 'running'
        assert session['status'] == 'working' and 'failure' not in session
        assert session['streamingText'] == 'Replacement stream'
        assert session.get('collaborationGeneration') == active
        assert tree['nodes'][0]['phase'] == 'interrupted'
        assert tree['nodes'][1]['phase'] == 'completed'
        assert tree['nodes'][2]['phase'] == 'running'
        assert tree['nodes'][3]['phase'] == 'stopped'  # Exact listed background receipt.
        assert tree['nodes'][4]['phase'] == 'running'
    finally:
        await service.close()


async def test_listed_background_stop_precedes_generic_owner_loss(tmp_path):
    from amplifier_web.execution import ensure_turn, ingest
    runtime = RuntimeManager(command=['unused'])
    service = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    session = service._new_session({'title': 'Exact naming Stop'})
    service.state['sessions'] = [session]
    ensure_turn(session, 'input')
    for identity, owner, lifecycle in [
        ('listed', 'old-process', 'background'),
        ('unlisted', 'old-process', 'background'),
        ('foreground', 'old-process', None),
        ('replacement', 'new-process', 'background'),
        ('terminal', 'old-process', 'background'),
    ]:
        ingest(session, {'id': identity, 'kind': 'llm', 'runtimeOwnerId': owner,
            'phase': 'completed' if identity == 'terminal' else 'running',
            **({'endedAt': 2} if identity == 'terminal' else {}),
            'lifecycle': lifecycle, 'sessionId': session['id'],
            'rootSessionId': session['id'], 'startedAt': 1})
    nodes = {node['id']: node for node in session['execution']['nodes']}
    terminal = copy.deepcopy(nodes['terminal'])
    replacement = copy.deepcopy(nodes['replacement'])
    payload = {'sessionId': session['id'], 'runtimeOwnerId': 'old-process',
               'status': 'stopped', 'reason': 'stopped',
               'backgroundCallIds': ['listed', 'terminal']}
    try:
        await service.on_runtime_event('runtime.ended', payload)
        assert nodes['listed']['phase'] == 'stopped'
        assert nodes['unlisted']['phase'] == nodes['foreground']['phase'] == 'interrupted'
        assert nodes['terminal'] == terminal and nodes['replacement'] == replacement
        assert session['execution']['aggregateUsage']['costPendingCalls'] == 1
        ended = {name: (node.get('phase'), node.get('endedAt')) for name, node in nodes.items()}
        await service.on_runtime_event('runtime.ended', payload)
        assert {name: (node.get('phase'), node.get('endedAt')) for name, node in nodes.items()} == ended
        assert session['execution']['aggregateUsage']['costPendingCalls'] == 1
    finally:
        await service.close()


@pytest.mark.parametrize('before_exit',['error','closed','completed-parent'])
async def test_process_cleanup_survives_terminal_display(tmp_path,monkeypatch,before_exit):
    from amplifier_web.execution import ensure_turn
    service=AppService(tmp_path/'app',SimpleNamespace(close=AsyncMock()),workspace=tmp_path)
    session=service._new_session({'title':'Terminal parent'})
    service.state['sessions']=[session]
    ensure_turn(session,'owned-input')
    session['status']='working'
    start={'sessionId':session['id'],'rootSessionId':session['id'],'event':'generation.started',
           'initial_input_id':'owned-input','generation_id':'owned-generation','observedAt':time.time(),
           'runtimeOwnerId':'owned-process'}
    await service.on_runtime_event('runtime.generation',start)
    # Verify process cleanup, not a second interruption of the public turn.
    calls=[]
    monkeypatch.setattr(service.operations,'interrupted',lambda sid:calls.append(('operations',sid)))
    monkeypatch.setattr(service.schedules,'runtime_ended',lambda s:calls.append(('schedule',s['id'])))
    try:
        if before_exit=='error':
            await service.on_runtime_event('runtime.error',{'sessionId':session['id'],'error':'synthetic crash'})
        elif before_exit=='closed':
            await service.on_runtime_event('runtime.status',{'sessionId':session['id'],'event':'session.closed','status':'stopped'})
        else:
            await service.on_runtime_event('runtime.status',{'sessionId':session['id'],'event':'session.idle','status':'idle'})
        phase=session['execution']['turns'][0]['phase']
        session['workers']=[{'id':'unfinished-job','kind':'job','status':'running','runtimeOwnerId':'owned-process'}]
        await service.on_runtime_event('runtime.ended',{'sessionId':session['id'],'runtimeOwnerId':'owned-process'})
        assert calls==[('operations',session['id']),('schedule',session['id'])]
        assert session['execution']['turns'][0]['phase']==phase
        assert session['workers'][0]['status']=='interrupted'
        assert session['collaborationGeneration']['terminal']
    finally:
        await service.close()


@pytest.mark.parametrize('late',['start','admission'])
@pytest.mark.parametrize('replacement',[False,True])
async def test_stale_owner_cannot_rebind_retry_or_replacement(tmp_path,late,replacement):
    from amplifier_web.execution import ensure_turn
    from amplifier_web.input_status import interrupt
    service=AppService(tmp_path/'app',SimpleNamespace(close=AsyncMock()),workspace=tmp_path)
    session=service._new_session({'title':'Ordered owner'})
    service.state['sessions']=[session]
    ensure_turn(session,'same-input');session['status']='working'
    old={'sessionId':session['id'],'rootSessionId':session['id'],'event':'generation.started',
         'initial_input_id':'same-input','generation_id':'old-generation','observedAt':time.time(),
         'runtimeOwnerId':'old-process'}
    try:
        await service.on_runtime_event('runtime.generation',old)
        interrupt(session)
        turn=session['execution']['turns'][0]
        turn.update(phase='running',retriedAt=time.time())
        turn.pop('runtimeOwnerId',None);turn.pop('generationId',None)
        if replacement:
            await service.on_runtime_event('runtime.generation',{**old,'generation_id':'replacement-generation',
                'runtimeOwnerId':'replacement-process','observedAt':time.time()})
        before=copy.deepcopy(turn)
        if late=='start':
            await service.on_runtime_event('runtime.generation',old)
        else:
            await service.on_runtime_event('runtime.status',{'sessionId':session['id'],'status':'working',
                'event':'input.delivered','inputId':'same-input','runtimeOwnerId':'old-process'})
        assert turn==before
        await service.on_runtime_event('runtime.ended',{'sessionId':session['id'],'runtimeOwnerId':'old-process'})
        assert turn['phase']=='running'
    finally:
        await service.close()


@pytest.mark.parametrize('replacement',[False,True])
async def test_confirmed_current_process_cleans_controls_before_generation(tmp_path,monkeypatch,replacement):
    runtime=RuntimeManager(command=['unused'])
    service=AppService(tmp_path/'app',runtime,workspace=tmp_path)
    session=service._new_session({'title':'Controls before generation'})
    service.state['sessions']=[session]
    calls=[]
    monkeypatch.setattr(service.operations,'interrupted',lambda sid:calls.append('operations'))
    monkeypatch.setattr(service.schedules,'runtime_ended',lambda s:calls.append('schedules'))
    row={'runtimeOwnerId':'old-process','emit':service.on_runtime_event,'executionEnded':False}
    runtime.workers[session['id']]=({'runtimeOwnerId':'replacement'} if replacement else row)
    try:
        await runtime._execution_ended(session['id'],row,'interrupted')
        assert calls==([] if replacement else ['operations','schedules'])
        assert not session.get('execution',{}).get('turns')
    finally:
        runtime.workers.clear()
        await service.close()


async def test_current_process_exit_preserves_terminal_operation_and_marks_unfinished_unknown(tmp_path):
    from test_operations import event, output, final
    runtime=RuntimeManager(command=['unused'])
    service=AppService(tmp_path/'app',runtime,workspace=tmp_path)
    session=service._new_session({'title':'Idle operations'})
    service.state['sessions']=[session];sid=session['id']
    row={'runtimeOwnerId':'process','emit':service.on_runtime_event}
    runtime.workers[sid]=row
    try:
        await service.operations.observe(sid,sid,event())
        await service.operations.observe(sid,sid,output())
        await service.operations.observe(sid,sid,final())
        await service.operations.observe(sid,sid,event('unfinished'))
        await runtime._execution_ended(sid,row,'interrupted')
        assert service.operations.status(sid,'p1')['state']=='completed'
        assert service.operations.status(sid,'unfinished')['state']=='outcome_unknown'
    finally:
        runtime.workers.clear()
        await service.close()


async def test_accepted_schedule_before_start_is_settled_by_current_process(tmp_path,monkeypatch):
    from test_schedules import fixture, schedule
    service,runtime,now,sid=await fixture(tmp_path,monkeypatch)
    manager=RuntimeManager(command=['unused'])
    row={'runtimeOwnerId':'process','emit':service.on_runtime_event}
    try:
        record=await schedule(service,sid,now[0])
        now[0]+=61;await service.schedules.tick()
        run=service.schedules.store.runs(sid)[0]
        assert run['phase'] in {'accepted','running'}
        # Preserve the fixture's task-control object through schedule admission;
        # only process-exit delivery uses the real RuntimeManager owner map.
        service.runtime=manager
        manager.workers[sid]=row
        await manager._execution_ended(sid,row,'interrupted')
        assert service.schedules.store.run(sid,run['id'])['phase']=='unknown'
        assert service.schedules.store.get(sid,record['id'])['status']=='needs_review'
    finally:
        manager.workers.clear()
        service.runtime=runtime
        await service.close()


async def test_delayed_start_cannot_cross_stop_epoch(tmp_path):
    from amplifier_web.execution import ensure_turn
    service=AppService(tmp_path/'app',SimpleNamespace(close=AsyncMock()),workspace=tmp_path)
    session=service._new_session({'title':'Stop epoch'})
    service.state['sessions']=[session];ensure_turn(session,'input')
    session['interruptionRevision']=1
    start={'sessionId':session['id'],'rootSessionId':session['id'],'event':'generation.started',
           'initial_input_id':'input','generation_id':'old-generation','observedAt':time.time(),
           'runtimeOwnerId':'old-process','interruptionRevision':0}
    try:
        await service.on_runtime_event('runtime.generation',start)
        turn=session['execution']['turns'][0]
        assert not turn.get('runtimeOwnerId')
        await service.on_runtime_event('runtime.generation',{**start,'generation_id':'new-generation',
            'runtimeOwnerId':'new-process','interruptionRevision':1,'observedAt':time.time()})
        assert turn['runtimeOwnerId']=='new-process'
    finally:
        await service.close()


async def test_late_terminal_owner_cannot_settle_replacement_controls(tmp_path,monkeypatch):
    from amplifier_web.execution import ensure_turn
    runtime=RuntimeManager(command=['unused'])
    service=AppService(tmp_path/'app',runtime,workspace=tmp_path)
    session=service._new_session({'title':'Replacement controls'})
    service.state['sessions']=[session];sid=session['id']
    tree=ensure_turn(session,'old-input')
    tree['turns'][0].update(runtimeOwnerId='old-process',generationId='old-generation',phase='completed')
    runtime.workers[sid]={'runtimeOwnerId':'replacement-process'}
    calls=[]
    monkeypatch.setattr(service.operations,'interrupted',lambda s:calls.append('operations'))
    monkeypatch.setattr(service.schedules,'runtime_ended',lambda s:calls.append('schedules'))
    try:
        await runtime._execution_ended(sid,{'runtimeOwnerId':'old-process','emit':service.on_runtime_event},'stopped')
        assert not calls
        assert tree['turns'][0]['phase']=='completed'
    finally:
        runtime.workers.clear()
        await service.close()
