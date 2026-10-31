"""Actual process/host transport; scripted native endpoint, no provider calls."""
import asyncio
import json
import sys

import pytest

from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService
from test_collaborative_workspaces import agent_action, generation, settled


SCRIPT = r'''
import json, os, sys, time
from pathlib import Path
log = Path(sys.argv[1])
for line in sys.stdin:
    data = json.loads(line); op = data['op']; result = {}
    if op == 'start':
        print(json.dumps({'type': 'runtime.ready'}), flush=True)
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
