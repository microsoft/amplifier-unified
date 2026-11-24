"""Actual process/host transport; scripted native endpoint, no provider calls."""
import asyncio
import copy
import json
import sys
from types import SimpleNamespace

import pytest

from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService
from test_collaborative_workspaces import app, agent_action, generation, settled


SCRIPT = r'''
import json, os, sys, time
from pathlib import Path
log = Path(sys.argv[1])
for line in sys.stdin:
    data = json.loads(line); op = data['op']; result = {}
    if op == 'start':
        print(json.dumps({'type': 'runtime.ready'}), flush=True)
        print(json.dumps({'type': 'session.idle'}), flush=True)
        continue
    if op == 'stop':
        print(json.dumps({'type': 'session.closed'}), flush=True)
        break
    if op == 'park':
        print(json.dumps({'type': 'runtime.parked'}), flush=True)
        result = {'parked': True}
    if op == 'retire':
        time.sleep(.04)
        result = {'retired': True}
    if op == 'control' and data['operation'] in {'coordination.submit', 'coordination.deliver'}:
        args = data['arguments']
        print(json.dumps({'op': 'bridge', 'id': 'admit', 'operation': 'coordination.admit', 'args': args}), flush=True)
        answer = json.loads(sys.stdin.readline())
        assert answer['op'] == 'bridge.result' and 'error' not in answer, answer
        permission = answer['result']
        result = {'accepted': permission['admitted'], 'inputId': args['inputId'], 'reason': permission.get('reason')}
        if result['accepted']:
            with log.open('a') as output:
                output.write(json.dumps({'pid': os.getpid(), 'inputId': args['inputId'], 'message': permission['message']}) + '\n')
            print(json.dumps({'type': 'input.delivered', 'input_id': args['inputId']}), flush=True)
    print(json.dumps({'op': 'reply', 'id': data['id'], 'result': result}), flush=True)
    if op == 'retire':
        print(json.dumps({'type': 'session.closed'}), flush=True)
        break
'''


@pytest.mark.parametrize('restart', [False, True])
async def test_human_resume_starts_fresh_process_without_replaying_prior_input(tmp_path, monkeypatch, restart):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'native'))
    log = tmp_path/'resumed.jsonl'
    def create():
        runtime = RuntimeManager(command=[sys.executable, '-c', SCRIPT, str(log)])
        runtime.retention.wake = lambda: None
        service = AppService(tmp_path/'app', runtime, workspace=tmp_path)
        runtime.app_bridge = service.app_bridge
        return service, runtime
    app, runtime = create()
    try:
        source, target = [app._new_session({'title': title}) for title in ('Manager', 'Member')]
        app.state['sessions'] = [source, target]
        assignment = app.assignments.ensure(source, 'fixture-assignment')
        assignment['members'][target['id']] = {'status': 'bound', 'role': 'member'}
        target['assignmentId'] = assignment['id']
        app.assignments.save(assignment); app._publish()
        await runtime.prewarm(target, app.on_runtime_event)
        original = runtime.workers[target['id']]['process']
        await app.dispatch('conversation.stop', {'sessionId': target['id']}, command_id='human-stop', include_state=False)
        async with asyncio.timeout(5):
            while original.returncode is None or target['status'] != 'stopped':
                await asyncio.sleep(.01)
        human = app._message(source, 'user', 'Resume my paused member.',
                             inputId='fresh-human', inputOrigin='ui')
        source_id, target_id = source['id'], target['id']
        app._publish()
        if restart:
            await app.close()
            app, runtime = create()
            source, target = app._session(source_id), app._session(target_id)
        await generation(app, source, 'manager-resume', ['fresh-human'])
        args = {'sessionId': target_id, 'text': 'Reconcile and continue.',
                'resume': {'sourceMessageId': human['id'], 'expectedInterruptionRevision': 1}}
        await agent_action(app, source, 'coordination.send', args, 'fresh-resume')
        await settled(app, 'fresh-resume', 'accepted')
        await agent_action(app, source, 'coordination.send', args, 'fresh-resume')
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert len(rows) == 1 and rows[0]['pid'] != original.pid
        assert rows[0]['message']['inputOrigin'] == 'peer'
        assert rows[0]['message']['peerEnvelope']['humanResume']['sourceMessageId'] == human['id']
        assert target['interruptionRevision'] == 1
    finally:
        await app.close()


@pytest.mark.parametrize('boundary', ['retired', 'retiring', 'host-restarted', 'user-stopped'])
async def test_peer_request_after_idle_retirement_needs_no_human_relay(tmp_path, monkeypatch, boundary):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'native'))
    log = tmp_path/'admitted.jsonl'

    def create():
        runtime = RuntimeManager(command=[sys.executable, '-c', SCRIPT, str(log)])
        runtime.retention.wake = lambda: None
        service = AppService(tmp_path/'app', runtime, workspace=tmp_path)
        runtime.app_bridge = service.app_bridge
        return service, runtime

    app, runtime = create()
    sweep = None
    try:
        source, target = [app._new_session({'title': title}) for title in ('Manager', 'Integrator')]
        app.state['sessions'] = [source, target]
        app.state['selectedSessionId'] = source['id']
        source_id, target_id = source['id'], target['id']
        await generation(app, source, 'source-turn', ['existing-authorized-input'])
        app._message(target, 'assistant', 'Previous work is preserved.')
        await runtime.prewarm(target, app.on_runtime_event)
        # Process readiness completes prewarm before the loop's idle event is
        # necessarily projected. Retirement needs that actual lifecycle, not a
        # status inferred from having a prepared worker.
        async with asyncio.timeout(3):
            while target['status'] != 'idle':
                await asyncio.sleep(0)
        assert target['status'] == 'idle'
        original = runtime.workers[target_id]['process']
        # Version refresh also retires an otherwise retained idle worker.
        runtime.workers[target_id]['update_pending'] = True
        sweep = asyncio.create_task(runtime.retention.sweep())
        async with asyncio.timeout(3):
            while not runtime.workers[target_id]['closing']:
                await asyncio.sleep(0)
        if boundary != 'retiring':
            await sweep
            assert original.returncode == 0
            assert target['status'] == 'idle'
            assert target['preparation']['status'] == 'cold'
        if boundary == 'host-restarted':
            await app.close()
            app, runtime = create()
            source, target = app._session(source_id), app._session(target_id)
            assert target['status'] == 'idle'
            await generation(app, source, 'fresh-source-turn', ['new-authorized-input'])
        if boundary == 'user-stopped':
            await app.dispatch('conversation.stop', {'sessionId': target_id}, command_id='explicit-stop', include_state=False)
            # Stop admission schedules its settlement. This case sends AFTER
            # a user-stopped boundary, not concurrently with the pending Stop.
            async with asyncio.timeout(3):
                while target['status'] != 'stopped':
                    await asyncio.sleep(0)
            assert target_id not in runtime.workers
            assert target['interruptionRevision'] == 1

        args = {'sessionId': target_id, 'mode': 'queue', 'text': 'Continue with the next check.'}
        await agent_action(app, source, 'coordination.send', args, 'new-peer-request')
        await settled(app, 'new-peer-request', 'suppressed' if boundary == 'user-stopped' else 'accepted')
        await sweep
        # An idempotent retry observes the same receipt and never submits twice.
        await agent_action(app, source, 'coordination.send', args, 'new-peer-request')
        await app.collaboration.drain(target_id)
        rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        if boundary == 'user-stopped':
            assert not rows
            assert target['interruptionRevision'] == 1
            assert target['status'] == 'stopped'
            assert target_id not in runtime.workers
        else:
            assert len(rows) == 1 and rows[0]['inputId'] == 'new-peer-request'
            assert rows[0]['pid'] != original.pid
            assert rows[0]['message']['inputOrigin'] == 'peer'
            assert rows[0]['message']['peerEnvelope']['senderSessionId'] == source_id
            assert target['status'] == 'working'
            assert not target.get('interruptionRevision')
        assert not [m for m in target['messages'] if m.get('inputOrigin') == 'ui']
        assert target['messages'][0]['text'] == 'Previous work is preserved.'
    finally:
        if sweep: await asyncio.gather(sweep, return_exceptions=True)
        await app.close()


@pytest.mark.parametrize('ordering', ['stop-then-peer', 'peer-during-stop'])
async def test_queued_peer_cannot_strand_stop_settlement(tmp_path, monkeypatch, ordering):
    """Same retired owner and commands; only the Stop/queue ordering differs."""
    from amplifier_web.message_delivery import execution_boundary

    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    log = tmp_path / 'race-admitted.jsonl'
    runtime = RuntimeManager(command=[sys.executable, '-c', SCRIPT, str(log)])
    runtime.retention.wake = lambda: None
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    runtime.app_bridge = app.app_bridge
    entered, release = asyncio.Event(), asyncio.Event()
    stop_task = None
    original_stop = runtime.stop
    try:
        source, target = [app._new_session({'title': title}) for title in ('Manager', 'Retired member')]
        app.state['sessions'] = [source, target]
        app.state['selectedSessionId'] = source['id']
        sid = target['id']
        await generation(app, source, 'source-turn', ['authorized-source-input'])
        app._message(target, 'assistant', 'Previous work is preserved.')
        await runtime.prewarm(target, app.on_runtime_event)
        async with asyncio.timeout(3):
            while target['status'] != 'idle':
                await asyncio.sleep(0)
        row = runtime.workers[sid]
        owner, process = row['runtimeOwnerId'], row['process']
        row['update_pending'] = True
        await asyncio.wait_for(runtime.retention.sweep(), 3)
        assert process.returncode == 0 and sid not in runtime.workers
        assert target['status'] == 'idle' and target['preparation']['status'] == 'cold'

        async def hold_stop(identity, **kwargs):
            nonlocal stop_task
            assert identity == sid
            stop_task = asyncio.current_task()
            entered.set()
            await release.wait()
            return await original_stop(identity, **kwargs)

        monkeypatch.setattr(runtime, 'stop', hold_stop)
        await app.dispatch('conversation.stop', {'sessionId': sid},
                           command_id='held-stop', include_state=False)
        await asyncio.wait_for(entered.wait(), 3)
        assert target['status'] == 'stopping' and target['interruptionRevision'] == 1
        stopped_boundary = execution_boundary(target)

        async def finish_stop():
            release.set()
            done, pending = await asyncio.wait({stop_task}, timeout=3)
            assert not pending, 'The held Stop task did not settle after its barrier was released'
            for completed in done:
                completed.result()

        if ordering == 'stop-then-peer':
            await finish_stop()
            assert target['status'] == 'stopped'
        args = {'sessionId': sid, 'mode': 'queue', 'text': 'New peer observation, not a human resume.'}
        await agent_action(app, source, 'coordination.send', args, 'race-peer')
        # Exercise the ordinary queue reducer while Stop is held; do not
        # manufacture a delivery receipt or move the message into the worker.
        await app.collaboration.drain(sid)
        before_release = {'status': target['status'], 'boundary': execution_boundary(target),
                          'delivery': app.collaboration.receipt('race-peer')['delivery']}
        if ordering == 'peer-during-stop':
            assert not stop_task.done()
            await finish_stop()
        await app.collaboration.drain(sid)
        receipt = app.collaboration.receipt('race-peer')
        # An exact duplicate is a receipt read, never a second input admission.
        await agent_action(app, source, 'coordination.send', args, 'race-peer')
        await app.collaboration.drain(sid)
        assert app.collaboration.receipt('race-peer') == receipt
        rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        observed = {'ordering': ordering, 'originalOwner': owner,
                    'originalPid': process.pid, 'originalExit': process.returncode,
                    'stopBoundary': stopped_boundary, 'beforeRelease': before_release,
                    'afterRelease': {'status': target['status'], 'boundary': execution_boundary(target),
                                     'delivery': receipt['delivery'], 'detail': receipt.get('detail'),
                                     'receiptInterruptionRevision': receipt['interruptionRevision']},
                    'stopTaskDone': stop_task.done(), 'workerPresent': sid in runtime.workers,
                    'admittedInputs': [row['inputId'] for row in rows]}
        print('STOP_QUEUE_BOUNDARY ' + json.dumps(observed, sort_keys=True))
        assert not rows and sid not in runtime.workers
        assert target['interruptionRevision'] == receipt['interruptionRevision'] == 1
        assert target['messages'][0]['text'] == 'Previous work is preserved.'
        peer, = [message for message in target['messages'] if message.get('inputId') == 'race-peer']
        assert peer['inputOrigin'] == 'peer' and 'humanResume' not in peer['peerEnvelope']
        assert not [message for message in target['messages'] if message.get('inputOrigin') == 'ui']
        assert target['status'] == 'stopped', observed
        assert receipt['delivery'] == 'suppressed', observed
    finally:
        release.set()
        try:
            if stop_task is not None and not stop_task.done():
                done, pending = await asyncio.wait({stop_task}, timeout=3)
                assert not pending, 'Stop cleanup remains unsettled'
        finally:
            await app.close()


@pytest.mark.parametrize('stage', ['before-runtime', 'during-runtime'])
@pytest.mark.parametrize('change', [
    'new-owner', 'new-preparation', 'new-runtime', 'new-generation',
    'admitted-input', 'new-stop', 'human-then-peer',
])
async def test_old_stop_cannot_cross_replacement_or_admission(app, monkeypatch, stage, change):
    source, target = app.state['sessions']
    sid, runtime = target['id'], app.runtime
    runtime.workers = {}
    runtime._preparations = {}
    await generation(app, target, 'original-generation', ['original-input'])
    entered, release = asyncio.Event(), asyncio.Event()
    captured, stopped = [], []
    drain = app.collaboration.drain

    async def held_drain(identity):
        if identity == sid and stage == 'before-runtime':
            captured.append(asyncio.current_task())
            entered.set()
            await release.wait()
            return
        await drain(identity)

    async def held_stop(identity):
        stopped.append(identity)
        captured.append(asyncio.current_task())
        entered.set()
        if stage == 'during-runtime':
            await release.wait()

    monkeypatch.setattr(app.collaboration, 'drain', held_drain)
    monkeypatch.setattr(runtime, 'stop', held_stop)
    await app.dispatch('conversation.stop', {'sessionId': sid},
                       command_id='original-stop', include_state=False)
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if change == 'new-owner':
            runtime.workers[sid] = {'runtimeOwnerId': 'replacement'}
        elif change == 'new-preparation':
            runtime._preparations[sid] = {'runtimeOwnerId': 'replacement-preparation'}
        elif change == 'new-runtime':
            app.runtime = SimpleNamespace(workers={}, _preparations={}, close=runtime.close)
        elif change == 'new-generation':
            await generation(app, target, 'replacement-generation', ['new-input'], interruption_revision=1)
        elif change == 'admitted-input':
            await app.on_runtime_event('runtime.status', {
                'sessionId': sid, 'status': 'working', 'event': 'input.delivered', 'inputId': 'new-input'})
        elif change == 'new-stop':
            # Simulate the second already-committed Stop while the first is held.
            target['interruptionRevision'] = 2
            target['lastInterruption'] = {'commandId': 'new-stop', 'at': 2, 'origin': 'ui'}
        else:
            app._message(target, 'user', 'A genuinely new human input.', inputId='human', inputOrigin='ui')
            await agent_action(app, source, 'coordination.send', {
                'sessionId': sid, 'mode': 'queue', 'text': 'Peer after human.'}, 'queued-after-human')
        before = copy.deepcopy({key: target.get(key) for key in
                                ('status', 'lastInterruption', 'collaborationGeneration')})
        release.set()
        done, pending = await asyncio.wait({captured[0]}, timeout=3)
        assert not pending
        for task in done:
            task.result()
        assert {key: target.get(key) for key in before} == before
        assert stopped == ([] if stage == 'before-runtime' else [sid])
        assert not runtime.inputs
    finally:
        release.set()
        app.runtime = runtime
        runtime.workers.clear()
        runtime._preparations.clear()


@pytest.mark.parametrize('change', [
    'none', 'missing-receipt', 'wrong-message', 'wrong-target', 'submitting',
    'unknown', 'admitted', 'human-resume', 'null-receipt', 'list-receipt', 'null-target',
    'list-delivery', 'object-delivery',
])
async def test_stop_exempts_only_exact_unadmitted_peer_receipts(app, monkeypatch, change):
    source, target = app.state['sessions']
    sid = target['id']
    entered, release = asyncio.Event(), asyncio.Event()
    stop_tasks = []
    async def held_stop(identity):
        assert identity == sid
        stop_tasks.append(asyncio.current_task())
        entered.set()
        await release.wait()
    monkeypatch.setattr(app.runtime, 'stop', held_stop)
    await app.dispatch('conversation.stop', {'sessionId': sid},
                       command_id='receipt-stop', include_state=False)
    try:
        await asyncio.wait_for(entered.wait(), 3)
        for identity in ('queued-one', 'queued-two'):
            await agent_action(app, source, 'coordination.send', {
                'sessionId': sid, 'mode': 'queue', 'text': 'Retained peer input.'}, identity)
        receipt = app.collaboration.receipt('queued-one')
        if change == 'missing-receipt':
            app.db.execute('DELETE FROM commands WHERE id=?', ('queued-one',))
            app.db.commit()
        elif change in {'null-receipt', 'list-receipt'}:
            app.db.execute('UPDATE commands SET receipt=? WHERE id=?',
                           ('null' if change == 'null-receipt' else '[]', 'queued-one'))
            app.db.commit()
        elif change == 'null-target':
            # Corrupt retained bytes at the read boundary, as with null/list
            # receipts above. The ordinary writer requires a shaped target and
            # cannot construct this malformed-record negative control.
            receipt['target'] = None
            app.db.execute('UPDATE commands SET receipt=? WHERE id=?',
                           (json.dumps(receipt), 'queued-one'))
            app.db.commit()
            assert app.collaboration.receipt('queued-one')['target'] is None
        elif change in {'list-delivery', 'object-delivery'}:
            receipt['delivery'] = [] if change == 'list-delivery' else {}
            app.db.execute('UPDATE commands SET receipt=? WHERE id=?',
                           (json.dumps(receipt), 'queued-one'))
            app.db.commit()
            assert app.collaboration.receipt('queued-one')['delivery'] == receipt['delivery']
        elif change != 'none':
            if change == 'wrong-message':
                receipt['messageId'] = 'another-message'
            elif change == 'wrong-target':
                receipt['target']['sessionId'] = source['id']
            elif change in {'submitting', 'unknown'}:
                receipt['delivery'] = change
            elif change == 'admitted':
                receipt['admittedAt'] = 1
            else:
                receipt['humanResume'] = {'sourceMessageId': 'not-authorized'}
            app.collaboration.save('queued-one', receipt)
            app.db.commit()
        release.set()
        done, pending = await asyncio.wait({stop_tasks[0]}, timeout=3)
        assert not pending
        for task in done:
            task.result()
        assert target['status'] == ('stopped' if change == 'none' else 'stopping')
        if change == 'none':
            for identity in ('queued-one', 'queued-two'):
                assert app.collaboration.receipt(identity)['delivery'] == 'suppressed'
        assert not app.runtime.inputs
        assert target['interruptionRevision'] == 1
    finally:
        release.set()


@pytest.mark.parametrize('delivery', [
    'saved-before-stop', 'human-after-stop', 'new-generation', 'list-input', 'object-input',
])
async def test_delayed_reader_delivery_does_not_cancel_its_original_stop(tmp_path, monkeypatch, delivery):
    """Real reader/reducers; only the worker pipe and Stop effect are scripted."""
    runtime = RuntimeManager(command=['unused'])
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    entered, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    stop_tasks, calls, observations = [], [], []
    reader = None
    try:
        await app.dispatch('session.create', {})
        target = app._session()
        sid = target['id']
        for identity in ('A', 'B'):
            app._message(target, 'user', identity, inputId=identity, inputOrigin='ui',
                         interruptionRevision=0)
        await generation(app, target, 'original-generation', ['A'])
        stream = asyncio.StreamReader()
        ready = asyncio.get_running_loop().create_future()
        ready.set_result({})
        async def emit(kind, payload):
            await app.on_runtime_event(kind, payload)
            if kind == 'runtime.status' and payload.get('event') == 'input.delivered':
                observations.append(copy.deepcopy(payload))
                delivered.set()
        row = {'process': SimpleNamespace(stdout=stream, returncode=None),
               'emit': emit, 'closing': False, 'pending': {}, 'ready': ready,
               'runtime_id': sid, 'runtimeOwnerId': 'original-owner', 'inputId': 'A',
               'bridge_tasks': set(), 'backgroundCalls': set()}
        runtime.workers[sid] = row
        reader = asyncio.create_task(runtime._read(sid, row))
        original_drain = app.collaboration.drain

        async def held_drain(identity):
            if identity == sid and not entered.is_set():
                stop_tasks.append(asyncio.current_task())
                entered.set()
                await release.wait()
            await original_drain(identity)

        async def stop(identity):
            assert identity == sid and runtime.workers[sid] is row
            calls.append((identity, row['runtimeOwnerId'], row['inputId']))
            row['closing'] = True
            runtime.workers.pop(sid)

        monkeypatch.setattr(app.collaboration, 'drain', held_drain)
        monkeypatch.setattr(runtime, 'stop', stop)
        await app.dispatch('conversation.stop', {'sessionId': sid},
                           command_id='stop-original', include_state=False)
        await asyncio.wait_for(entered.wait(), 3)
        assert target['interruptionRevision'] == 1 and row['inputId'] == 'A'
        assert target['collaborationGeneration']['inputIds'] == ['A']
        if delivery == 'human-after-stop':
            app._message(target, 'user', 'New human input', inputId='C', inputOrigin='ui',
                         interruptionRevision=1)
        elif delivery == 'new-generation':
            # Membership alone would qualify after B arrives. Only the changed
            # generation identity/epoch must reject this older Stop.
            await generation(app, target, 'new-generation', ['A'], interruption_revision=1)
        identity = ([] if delivery == 'list-input' else {} if delivery == 'object-input'
                    else 'C' if delivery == 'human-after-stop' else 'B')
        frame = {'type': 'input.delivered', 'input_id': identity,
                 'interruptionRevision': 0 if delivery == 'saved-before-stop' else 1}
        stream.feed_data(json.dumps(frame).encode() + b'\n')
        await asyncio.wait_for(delivered.wait(), 3)
        assert observations[-1]['runtimeOwnerId'] == 'original-owner'
        assert observations[-1]['inputId'] == row['inputId'] == identity
        assert observations[-1]['interruptionRevision'] == frame['interruptionRevision']
        if delivery in {'list-input', 'object-input'}:
            assert target['collaborationGeneration']['inputIds'] == ['A']
        else:
            assert identity in target['collaborationGeneration']['inputIds']
        release.set()
        done, pending = await asyncio.wait({stop_tasks[0]}, timeout=3)
        assert not pending
        for task in done:
            task.result()
        observed = {'delivery': delivery, 'stopCalls': calls, 'status': target['status'],
                    'interruptionRevision': target['interruptionRevision'],
                    'generation': target['collaborationGeneration'],
                    'readerEvent': observations[-1], 'workerPresent': sid in runtime.workers}
        print('DELAYED_STOP_BOUNDARY ' + json.dumps(observed, sort_keys=True))
        if delivery == 'saved-before-stop':
            assert calls == [(sid, 'original-owner', 'B')], observed
            assert target['status'] == 'stopped' and sid not in runtime.workers
        else:
            assert calls == [] and runtime.workers[sid] is row
            assert target['status'] == 'working'
        assert target['interruptionRevision'] == 1
        assert [message['inputId'] for message in target['messages'] if message['role'] == 'user'] == (
            ['A', 'B', 'C'] if delivery == 'human-after-stop' else ['A', 'B'])
    finally:
        release.set()
        try:
            if stop_tasks and not stop_tasks[0].done():
                _, pending = await asyncio.wait({stop_tasks[0]}, timeout=3)
                assert not pending, 'Stop fixture cleanup remains unsettled'
        finally:
            if reader:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            runtime.workers.clear()
            await app.close()


@pytest.mark.parametrize('where', ['generation', 'preparation'])
@pytest.mark.parametrize('identity', [[], {}], ids=['list', 'object'])
async def test_stop_saved_input_membership_rejects_malformed_id(app, where, identity):
    from amplifier_web.message_delivery import execution_boundary

    target = app.state['sessions'][1]
    sid, runtime = target['id'], app.runtime
    runtime.workers = {}
    runtime._preparations = {}
    await generation(app, target, 'same-generation', ['A'])
    app._message(target, 'user', 'Saved B', inputId='B', inputOrigin='ui')
    target['lastInterruption'] = {'commandId': 'stop', 'at': 1, 'origin': 'ui'}
    target['interruptionRevision'] = 1
    if where == 'generation':
        runtime.workers[sid] = {'runtimeOwnerId': 'owner', 'inputId': 'A'}
    else:
        runtime._preparations[sid] = {'runtimeOwnerId': 'owner'}
    current = app._capture_stop_guard(target, execution_boundary(target))
    assert current() is True
    try:
        if where == 'generation':
            target['collaborationGeneration']['inputIds'].append(identity)
        else:
            runtime.workers[sid] = {'runtimeOwnerId': 'owner', 'inputId': identity}
        assert current() is False
        assert not runtime.inputs
    finally:
        runtime.workers.clear()
        runtime._preparations.clear()


async def test_stop_context_resets_in_throwing_observer_task_and_cannot_cross_child_task(app):
    from amplifier_web.execution_owner import ensure_observers
    from amplifier_web.message_delivery import execution_boundary
    from amplifier_web.service import _STOP_OBSERVATION

    target = app.state['sessions'][1]
    sid = target['id']
    target['status'] = 'stopping'
    target['interruptionRevision'] = 1
    boundary = execution_boundary(target)
    child_status = []
    observers = ensure_observers(app)

    class Observer:
        async def observe(self, kind, payload):
            if payload.get('sessionId') != sid or payload.get('stopBoundary') != boundary:
                return
            if asyncio.current_task() is owner_task:
                assert _STOP_OBSERVATION.get()[1] is owner_task
                # Reuse the exact payload identity in an inherited ContextVar.
                # Only the owning task can carry the host-private exception.
                target['status'] = 'stopping'
                app._message(target, 'user', 'new human input', inputId='new', inputOrigin='ui')
                async def child():
                    await app.on_runtime_event(kind, payload)
                    child_status.append(target['status'])
                await asyncio.create_task(child())
                raise RuntimeError('Synthetic observer failure')

    observer = Observer()
    observers.attachments.add(observer)
    owner_task = asyncio.current_task()
    prior = _STOP_OBSERVATION.get()
    try:
        with pytest.raises(RuntimeError, match='Synthetic observer failure'):
            await app._observe_stop('runtime.status',
                {'sessionId': sid, 'status': 'stopped', 'stopBoundary': boundary}, lambda: True)
        assert _STOP_OBSERVATION.get() is prior
        assert child_status == ['stopping']
        assert target['status'] == 'stopping'
        assert not app.runtime.inputs
    finally:
        observers.remove(observer)


@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('failed', [False, True])
async def test_stop_with_queued_peer_preserves_observers_and_failure(app, monkeypatch, wrapped, failed):
    source, target = app.state['sessions']
    sid = target['id']
    observed = []
    if wrapped:
        from amplifier_web.execution_owner import ensure_observers
        class Observer:
            async def observe(self, kind, payload):
                if payload.get('sessionId') == sid and 'stopBoundary' in payload:
                    json.dumps(payload)  # No host closures in the public event.
                    observed.append((kind, target['status'], copy.deepcopy(payload)))
        ensure_observers(app).attachments.add(Observer())
    entered, release = asyncio.Event(), asyncio.Event()
    stop_tasks = []
    async def failed_stop(identity):
        assert identity == sid
        stop_tasks.append(asyncio.current_task())
        entered.set()
        await release.wait()
        if failed:
            raise RuntimeError('Synthetic Stop did not confirm retirement')
    monkeypatch.setattr(app.runtime, 'stop', failed_stop)
    await app.dispatch('conversation.stop', {'sessionId': sid},
                       command_id='failed-stop', include_state=False)
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await agent_action(app, source, 'coordination.send', {
            'sessionId': sid, 'mode': 'queue', 'text': 'Cannot resume uncertain Stop.'}, 'held-peer')
        release.set()
        done, pending = await asyncio.wait({stop_tasks[0]}, timeout=3)
        assert not pending
        for task in done:
            task.result()
        assert target['status'] == ('error' if failed else 'stopped')
        assert target['lastInterruption']['commandId'] == 'failed-stop'
        if failed:
            assert target['lastInterruption']['outcome'] == 'unconfirmed'
            assert 'Stop could not be confirmed' in target['error']
        else:
            assert not target.get('error')
        if wrapped:
            assert len(observed) == 1
            assert observed[0][:2] == (('runtime.error', 'error') if failed else ('runtime.status', 'stopped'))
        from amplifier_web.service import _STOP_OBSERVATION
        assert _STOP_OBSERVATION.get() is None
        await app.collaboration.drain(sid)
        assert app.collaboration.receipt('held-peer')['delivery'] == 'suppressed'
        assert not app.runtime.inputs
    finally:
        release.set()
