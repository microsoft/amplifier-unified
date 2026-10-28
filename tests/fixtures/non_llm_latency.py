"""Isolated real-pipe tool responsiveness fixture, without model inference.

The setup and finite command driver are synthetic. Storage, history query,
publication, Work checks, skill loader and both sides of the pipe are real.
Run only with a fresh task-owned output directory; evidence survives failure.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from amplifier_web.service import AppService
from amplifier_web.runtime import RuntimeManager
from amplifier_web import performance_diagnostics as timings
from amplifier_web import cold_display, state_records, session_projection


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def complete_timings(rows, identities):
    """Every call must join its own worker bridge, host bridge and tool row."""
    for identity in identities:
        tools = [r for r in rows if r.get('toolCallId') == identity and r['kind'] == 'tool']
        bridges = [r for r in rows if r.get('toolCallId') == identity and r['kind'] == 'bridge']
        if len(tools) != 1 or not bridges:
            return False
        if not {'start', 'body.start', 'body.end', 'end'} <= tools[0]['marks'].keys():
            return False
        for bridge in bridges:
            hosts = [r for r in rows if r['kind'] == 'host.bridge' and r.get('bridgeId') == bridge['bridgeId']]
            if len(hosts) != 1 or hosts[0].get('invocationId') != bridge.get('invocationId') or bridge.get('invocationId') != tools[0].get('invocationId'):
                return False
            if not {'start', 'write.start', 'write.end', 'received', 'resumed', 'end'} <= bridge['marks'].keys():
                return False
            if not {'dequeued', 'start', 'handler.start', 'handler.end', 'reply.start', 'reply.end', 'end'} <= hosts[0]['marks'].keys():
                return False
    return True


async def run(output, *, loaded=False, probes=True, large=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    for key, relative in {'HOME': 'home', 'AMPLIFIER_HOME': 'native', 'AMPLIFIER_WEB_HOME': 'app',
                          'AMPLIFIER_SESSION_STATE_HOME': 'sessions', 'XDG_CACHE_HOME': 'cache'}.items():
        os.environ[key] = str(output / relative)
    # No providers are mounted; remove credentials inherited by the guest.
    for key in list(os.environ):
        if key.endswith(('_API_KEY', '_TOKEN')) or key == 'SSH_AUTH_SOCK':
            os.environ.pop(key, None)
    workspace = output / 'workspace'
    skill = workspace / '.amplifier/skills/latency-control'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: latency-control\ndescription: Local fixture skill.\n---\nLATENCY_SKILL_OK\n')
    timings.TIMING_ENABLED = probes
    os.environ['AMPLIFIER_RUNTIME_TIMING'] = '1' if probes else '0'
    app = AppService(output / 'app', workspace=workspace)
    manager = RuntimeManager(app.app_bridge,
        command=[sys.executable, '-B', str(Path(__file__).with_name('non_llm_latency_worker.py'))],
        startup_timeout=15, retention={'prewarm_on_select': False},
        performance_diagnostics=app.performance_diagnostics)
    manager.home = output / 'app'
    report = {'status': 'incomplete', 'loaded': loaded, 'probes': probes, 'large': large,
              'checks': {}, 'events': [], 'limits': [
                  'Synthetic two-chat workload, not a production latency estimate',
                  'Fixed local shell command tool is synthetic; real skill tool and app bridge',
                  'Ordinary unassociated Work path; commissioned-origin/Stop tests are separate',
                  'Existing probe transport deadline is 30 seconds; bridge deadline is 60 seconds',
                  'No provider transport, inference or paid model calls',
                  'No browser paint measurement; subscriber delivery is server-side']}
    begin = time.monotonic()
    task = None
    consumers = []
    queues = []
    latest = {}
    revisions = [[], []]
    shell_notifications = [0, 0]
    request = None
    worker_row = None
    try:
        rows = []
        for number in range(2):
            row = app._new_session({'title': 'Synthetic ' + str(number), 'workspace': str(workspace)})
            count = 400 if large and number == 0 else 10
            size = 12000 if large and number == 0 else 100
            row['messages'] = [{'id': f'm-{number}-{i}', 'role': 'user' if i % 2 == 0 else 'assistant',
                'text': f'Synthetic {number}:{i} ' + 'x' * size, 'createdAt': float(i + 1)} for i in range(count)]
            row.update(status='ready', historyLoaded=True)
            node_count = 13500 if large and number == 0 else 10
            row['execution'] = {'nodes': [{'id': f'node-{i}', 'kind': 'tool', 'phase': 'completed',
                'nativeHistory': True, 'sessionId': row['id'], 'parentId': None,
                'input': {'files': [{'name': f'file-{j}', 'options': [1, 2, 3]} for j in range(4)]}}
                for i in range(node_count)], 'turns': [], 'retiredUsageNodes': [
                    {'id': f'accounting-{i}', 'kind': 'llm', 'revision': 1,
                     'sessionId': row['id'], 'rootSessionId': row['id'],
                     'producerId': 'fixture', 'phase': 'completed', 'startedAt': float(i), 'endedAt': float(i + 1),
                     'usage': {'inputTokens': 1000, 'outputTokens': 100, 'totalTokens': 1100,
                               'costUsd': .01, 'costType': 'measured', 'costSource': 'fixture'}}
                    for i in range(4693 if large and number == 0 else 10)]}
            app._state['sessions'].append(row)
            rows.append(row)
            app.clients.attach(str(number))
            with app.clients.bind(str(number)):
                app.clients.records[str(number)]['selectedSessionId'] = row['id']
                app.clients.records[str(number)]['view']['workSurface'] = 'chat'
                app.clients.draft(row['id'], f'private-{number}')
                queues.append(app.subscribe())
        target, probe = rows
        app._state['selectedSessionId'] = probe['id']
        app._publish()
        await app.history.refresh(force=False)
        before = [digest(row['messages']) for row in rows]
        accounting_before = digest(target['execution']['retiredUsageNodes'])
        first_frames = []
        for i in range(2):
            with app.clients.bind(str(i)):
                first_frames.append(app.browser_state())
        first_digests = [digest(frame) for frame in first_frames]
        while any(not q.empty() for q in queues):
            for q in queues:
                if not q.empty(): q.get_nowait()
        async def emit(kind, payload):
            await app.on_runtime_event(kind, payload)
        # The source is stable and fully prepared before measured work starts.
        probe['collaborationGeneration'] = {'id': 'fixture-generation', 'inputIds': [], 'status': 'working'}
        await manager.start(probe, emit)
        worker_row = manager.workers[probe['id']]
        await app._flush_pending_progress()
        app.performance_diagnostics.runtime_timings.clear()
        async def consume(number, queue):
            while True:
                frame = await queue.get()
                # Match browser_events.serve: shell notifications share this
                # queue but are not state snapshots and carry no revision.
                if 'shellClientId' in frame:
                    assert frame == {'shellClientId': str(number)}
                    shell_notifications[number] += 1
                    continue
                latest[number] = frame
                revisions[number].append(frame['revision'])
        consumers = [asyncio.create_task(consume(i, q)) for i, q in enumerate(queues)]
        app.performance_diagnostics.start()
        await asyncio.sleep(0)  # Arm the existing loop-delay observer in every arm.
        batch = uuid.uuid4().hex
        identities = [batch + '-' + label for label in ('command', 'skill', 'actions', 'history')]
        cpu = time.process_time_ns()
        stamp = time.monotonic_ns()
        async def background():
            for i in range(8):
                due = stamp + i * 25_000_000
                await asyncio.sleep(max(0, (due - time.monotonic_ns()) / 1e9))
                started = time.monotonic_ns()
                await app.on_runtime_event('execution.event', {'sessionId': target['id'],
                    'rootSessionId': target['id'], 'id': f'background-{i}', 'kind': 'tool',
                    'phase': 'completed', 'revision': 1, 'startedAt': i, 'endedAt': i + 1})
                report['events'].append({'index': i, 'offeredNs': due, 'startedNs': started, 'endedNs': time.monotonic_ns(),
                                         'revision': app.state['revision']})
        request = asyncio.create_task(manager._request(probe['id'], 'probe', batch=batch, timing=probes, historyId=target['id']))
        task = asyncio.create_task(background()) if loaded else None
        report['worker'] = await request  # Keep RuntimeManager's existing 30-second deadline.
        report['probeEndNs'] = time.monotonic_ns()
        if task:
            await task
        await app._flush_pending_progress()
        # Reader can still be consuming trailing bounded timing records.
        async def drain_timings():
            while probes and not complete_timings(list(app.performance_diagnostics.runtime_timings), identities):
                await asyncio.sleep(.005)
        await asyncio.wait_for(drain_timings(), 3)
        async def drain_queues():
            while any(not q.empty() for q in queues):
                for consumer in consumers:
                    if consumer.done():
                        consumer.result()  # Surface the actual error, not a drain timeout.
                await asyncio.sleep(0)
        await asyncio.wait_for(drain_queues(), 1)
        report.update(startNs=stamp, endNs=time.monotonic_ns(), hostCpuNs=time.process_time_ns()-cpu,
                      hostPeakRSSKiB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                      clockDomain=timings.clock_domain(), diagnostics=app.performance_diagnostics.snapshot())
        calls = {r['callId'].removeprefix(batch + '-'): r for r in report['worker']['calls']}
        report['checks']['command_body_executed'] = calls['command']['result']['stdout'] == 'LATENCY_COMMAND_OK'
        report['checks']['real_skill_loaded'] = 'LATENCY_SKILL_OK' in calls['skill']['result']['content']
        report['checks']['actions_returned'] = bool(calls['actions']['result']['items'])
        expected = [{'index': i, 'message_id': row['id'], 'role': row['role'], 'text': row['text'][:100],
                     'text_offset': 0, 'next_text_offset': 100} for i, row in enumerate(target['messages'][:5])]
        report['checks']['exact_history_page'] = calls['history']['result']['messages'] == expected
        report['checks']['messages_unchanged'] = [digest(row['messages']) for row in rows] == before
        report['checks']['private_drafts'] = all(app.clients.records[str(i)]['drafts'][row['id']] == f'private-{i}' for i, row in enumerate(rows))
        saved = state_records.load(app.db)
        target_saved = next(row for row in saved['sessions'] if row['id'] == target['id'])
        materialized = cold_display.materialize(cold_display.load(app.db, target_saved['$viewPayload']), app.db)
        report['checks']['durable_messages'] = digest(materialized['messages']) == before[0]
        report['checks']['durable_execution'] = materialized['execution'] == session_projection.stored_execution(target['execution'])
        report['checks']['background_completed'] = len(report['events']) == (8 if loaded else 0)
        actual = {r['id']: r for r in target['execution']['nodes']}
        report['checks']['background_ingested'] = all(
            actual[f'background-{i}']['revision'] == 1 and actual[f'background-{i}']['phase'] == 'completed'
            for i in range(8 if loaded else 0))
        report['checks']['accounting_unchanged'] = digest(target['execution']['retiredUsageNodes']) == accounting_before
        report['checks']['issued_frames_unchanged'] = [digest(frame) for frame in first_frames] == first_digests
        report['checks']['timing_join'] = complete_timings(report['diagnostics']['runtimeTimings'], identities) if probes else not report['diagnostics']['runtimeTimings']
        if loaded:
            frame = latest[0]
            selected = next(r for r in frame['sessions'] if r['id'] == target['id'])
            report['checks']['final_subscriber_revision'] = frame['revision'] >= report['events'][-1]['revision']
            report['checks']['final_subscriber_content'] = selected['messages'][-1]['id'] == target['messages'][-1]['id']
        for number, frame in latest.items():
            selected = next(r for r in frame['sessions'] if r['id'] == rows[number]['id'])
            report['checks'][f'client_{number}_private'] = (
                frame['selectedSessionId'] == rows[number]['id'] and selected.get('draft') == f'private-{number}')
        report['subscriberRevisions'] = revisions
        report['shellNotifications'] = shell_notifications
        report['checks']['subscribers_alive'] = all(not consumer.done() for consumer in consumers)
        report['checks']['no_provider'] = report['worker']['providers'] == 0
        tool_nodes = {r.get('toolCallId'): r for r in probe['execution']['nodes'] if r.get('toolCallId') in identities}
        report['checks']['tool_completion_observed'] = set(tool_nodes) == set(identities) and all(
            row['phase'] == 'completed' and row.get('endedAt') is not None for row in tool_nodes.values())
        report['toolObservations'] = {call: {k: r.get(k) for k in ('startedAt', 'endedAt', 'revision', 'phase')}
                                      for call, r in tool_nodes.items()}
        report['checks']['same_clock_domain'] = bool(report['clockDomain']) and report['clockDomain'] == report['worker']['clockDomain']
        report['subscriberQueueLengths'] = [q.qsize() for q in queues]
        report['sourceShape'] = {'messages': [len(row['messages']) for row in rows],
                                 'nodes': [len(row['execution']['nodes']) for row in rows]}
        assert all(report['checks'].values()), report['checks']
        report['status'] = 'passed'
    except BaseException as exc:
        report.update(status='failed', errorType=type(exc).__name__, error=str(exc))
        raise
    finally:
        # Preserve partial evidence before cleanup, including on transport timeout.
        report.setdefault('diagnostics', app.performance_diagnostics.snapshot())
        report['subscriberRevisions'] = revisions
        report['shellNotifications'] = shell_notifications
        report['consumerErrors'] = [repr(c.exception()) for c in consumers
                                    if c.done() and not c.cancelled() and c.exception() is not None]
        try:
            for pending in [task, request, *consumers]:
                if pending and not pending.done():
                    pending.cancel()
            await asyncio.gather(*(p for p in [task, request, *consumers] if p), return_exceptions=True)
            await manager.close()
            await app.close()
        except BaseException as exc:
            report['cleanupError'] = repr(exc)
            report['status'] = 'failed'
            raise
        finally:
            # RuntimeManager's stderr is redacted/bounded by the real reader;
            # retain it before the row becomes unreachable, including failures.
            report['workerStderr'] = list(worker_row['stderr']) if worker_row is not None else None
            report['wholeSeconds'] = time.monotonic() - begin
            report['cleanupWorkers'] = len(manager.workers)
            (output / 'result.json').write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('output')
    parser.add_argument('--loaded', action='store_true')
    parser.add_argument('--large', action='store_true')
    parser.add_argument('--probes-off', action='store_true')
    args = parser.parse_args()
    result = asyncio.run(run(args.output, loaded=args.loaded, large=args.large, probes=not args.probes_off))
    print(json.dumps({'status': result['status'], 'checks': result['checks'], 'wholeSeconds': result['wholeSeconds']}))
