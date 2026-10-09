"""DTU-only synthetic large-chat persistence comparison; no provider calls."""
import asyncio
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import statistics
import sys
import time

source, output = (Path(p).resolve() for p in sys.argv[1:3])
message_size = int(sys.argv[3]) if len(sys.argv) > 3 else 12000
accounting_count = int(sys.argv[4]) if len(sys.argv) > 4 else 0
assert source.is_relative_to('/opt/unified-validation') and output.is_relative_to('/opt/unified-validation')
assert not output.exists()
output.mkdir(parents=True)
os.environ.update(HOME=str(output/'home'), AMPLIFIER_HOME=str(output/'native'),
                  AMPLIFIER_WEB_HOME=str(output/'app'), AMPLIFIER_UNIFIED_IMPORT_HOME=str(output/'legacy'))
os.environ.pop('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', None)
sys.path.insert(0, str(source))
from amplifier_web.server import create_app
from amplifier_web import session_projection, state_records, cold_display
from amplifier_web.host.storage import SessionStore
spec = importlib.util.spec_from_file_location('fixture', source/'tests/fixtures/library_performance_server.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

async def main():
    catalog = fixture.make_catalog(output/'workspaces', 24000, 4800, 4800)
    runtime = fixture.NoModelRuntime()
    app = await create_app(output/'app', workspace=catalog['workspaces'][0]['path'], runtime=runtime,
                           voice=False, background_updates=False, preload_providers=False)
    service = app['service']
    await service.history.close()
    await service.event_log_view.close()
    await service.diagnostics.close()
    service.history.index.scan_if_changed = lambda **kwargs: (object(), copy.deepcopy(catalog))
    await service.history.refresh()
    service.history.index.needs_scan = lambda: False
    rows, queues = [], []
    for number in range(2):
        row = service._new_session({'workspace': catalog['workspaces'][0]['path'], 'title': f'Fixture {number}'})
        row.update(status='working', historyLoaded=True)
        row['messages'] = [{'id': f'm-{number}-{i}', 'role': 'user' if i%2==0 else 'assistant', 'via':'text',
                            'text': f'Synthetic {number}:{i} '+('x'*message_size), 'createdAt': float(i+1)}
                           for i in range(400)]
        row['execution'] = {'nodes': [], 'turns': [], 'retiredUsageNodes': [
            {'id':f'call-{i:06d}-'+'a'*48, 'revision':1,
             'producerId':'producer-'+'b'*48, 'budgetRevision':1, 'admittedAt':float(i),
             'parentId':'parent-'+'c'*36, 'turnId':'turn-'+'d'*36,
             'sessionId':row['id'], 'rootSessionId':row['id'], 'kind':'llm',
             'phase':'completed', 'provider':'synthetic', 'model':'synthetic-model',
             'startedAt':float(i), 'endedAt':float(i)+1,
             'usage':{'inputTokens':1000,'outputTokens':100,'totalTokens':1100,
                      'cacheReadTokens':500,'cacheWriteTokens':0,'reasoningTokens':50,
                      'grossInputTokens':1500,'grossTotalTokens':1600,'costUsd':0.01,
                      'costType':'measured','costSource':'fixture'}}
            for i in range(accounting_count)]}
        service._state['sessions'].append(row)
        rows.append(row)
        client = str(number)
        service.clients.attach(client)
        with service.clients.bind(client):
            service.clients.records[client].update(selectedSessionId=row['id'], selectedWorkspaceId=catalog['workspaces'][0]['id'])
            service.clients.draft(row['id'], 'private-'+client)
            queues.append(service.subscribe())
    service._publish()
    for client in ('0', '1'):
        with service.clients.bind(client):
            service.browser_state()
    before_b = copy.deepcopy(rows[1])
    old_frame = service._client_snapshots['1']
    old_frame_revision = old_frame['revision']
    # Separate canonical files from display outputs; they are not work inputs.
    from amplifier_web.session_files import sessions_dir
    canonicals = []
    for row in rows:
        path = sessions_dir(row['workspace'])/row['id']/'transcript.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'role':'user','content':'canonical fixture, must remain unchanged'})+'\n')
        canonicals.append((path, hashlib.sha256(path.read_bytes()).hexdigest()))
    from aiohttp.test_utils import TestClient, TestServer
    client = TestClient(TestServer(app))
    await client.start_server()
    client.session.headers.update({'Authorization':'Bearer '+app['control_token'],
                                   'X-Amplifier-Client':'0','X-Amplifier-State-Transport':'delta-v1'})
    original_dumps = json.dumps
    original_atomic = SessionStore._atomic
    original_persist = session_projection.persist
    current = None
    samples = []
    def atomic(path, text):
        result = original_atomic(path, text)
        if current is not None:
            key = 'view' if Path(path).name == 'view.json' else 'resource' if Path(path).parent.name=='artifacts' else 'other'
            entry = current['atomicWrites'].setdefault(key, {'calls':0, 'bytes':0})
            entry['calls'] += 1
            entry['bytes'] += Path(path).stat().st_size
        return result
    def persist(*args, **kwargs):
        begin = time.thread_time()
        try:
            return original_persist(*args, **kwargs)
        finally:
            if current is not None:
                current['persistCPUSeconds'] += time.thread_time()-begin
    # Do not wrap encoding for timings: extra UTF-8 conversion costs would
    # disproportionately penalize the baseline's larger serialized objects.
    SessionStore._atomic = staticmethod(atomic)
    session_projection.persist = persist
    mixes = {'active': ['delta']*6+['metadata']*2+['accounting_edit','noop'],
             'balanced': ['noop']*5+['delta']*3+['metadata','accounting_edit'],
             'noop_heavy': ['noop']*9+['delta']}
    try:
        for mix, operations in mixes.items():
            for iteration, phase in enumerate(operations*4):
                for queue in queues:
                    while not queue.empty():
                        queue.get_nowait()
                current = {'mix':mix, 'phase':phase, 'iteration':iteration, 'atomicWrites':{}, 'persistCPUSeconds':0}
                begin_cpu, begin_wall = time.process_time(), time.perf_counter()
                # Start an unrelated browser layout command while persistence is
                # busy. End-to-end latency includes time waiting for the event loop.
                async def interact():
                    response = await client.post('/api/actions', json={'id':f'{mix}-{iteration}',
                        'action':'view.update','args':{'patch':{'navExpanded':bool(iteration%2)}}})
                    assert response.status == 200, await response.text()
                    receipt = await response.json()
                    assert receipt['accepted']
                    current['httpInteractionMs'] = 1000*(time.perf_counter()-begin_wall)
                pending_interaction = asyncio.create_task(interact())
                if phase == 'delta':
                    await service.on_runtime_event('assistant.delta', {'sessionId':rows[0]['id'], 'text':f'chunk-{iteration} '})
                    await service._flush_pending_progress()
                else:
                    if phase == 'metadata':
                        rows[0]['streaming'] = f'progress-{iteration}'
                    if phase == 'accounting_edit' and accounting_count:
                        rows[0]['execution']['retiredUsageNodes'][0]['usage']['inputTokens'] += 1
                        rows[0]['execution']['retiredUsageNodes'][0]['usage']['totalTokens'] += 1
                    service._publish(session_ids={rows[0]['id']}, detail_only=True, record_only=True)
                await pending_interaction
                current.update(cpuSeconds=time.process_time()-begin_cpu, wallSeconds=time.perf_counter()-begin_wall,
                               queueLengths=[queue.qsize() for queue in queues])
                assert max(current['queueLengths']) <= 4
                samples.append(current)
                print(original_dumps(current), flush=True)
                current = None
    finally:
        SessionStore._atomic = staticmethod(original_atomic)
        session_projection.persist = original_persist
    expected = copy.deepcopy(rows[0]['messages'])
    reference = next(row['$viewPayload'] for row in state_records.load(service.db)['sessions'] if row['id']==rows[0]['id'])
    durable = cold_display.materialize(cold_display.load(service.db, reference), service.db)
    checks = {'durable_exact_messages': durable['messages']==expected,
              'durable_exact_accounting': durable['execution']==session_projection.stored_execution(rows[0]['execution']),
              'B_unchanged': rows[1]==before_b,
              'old_issued_frame_unchanged': old_frame['revision']==old_frame_revision,
              'private_drafts': all(service.clients.records[str(i)]['drafts'][row['id']]=='private-'+str(i) for i,row in enumerate(rows)),
              'canonical_unchanged': all(hashlib.sha256(path.read_bytes()).hexdigest()==digest for path,digest in canonicals),
              'no_model_calls': not runtime.calls}
    assert all(checks.values()), checks
    from amplifier_web import storage_migration
    service._last_storage_sweep = 0
    gc_cpu, gc_wall = time.process_time(), time.perf_counter()
    storage_migration.maintenance(service)
    gc_sample = {'cpuSeconds':time.process_time()-gc_cpu,
                 'wallSeconds':time.perf_counter()-gc_wall}
    report = {'status':'measured', 'source':str(source), 'messageBytesEach':message_size,
              'messagesPerChat':400, 'accountingRowsPerChat':accounting_count,
              'catalogRows':24000, 'workspaces':4800, 'samples':samples,
              'checks':checks, 'peakRSSKiB':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'separateMaintenance':gc_sample,
              'savedManifestBytes': reference['bytes'],
              'sourceHashes': {name:hashlib.sha256((source/'amplifier_web'/name).read_bytes()).hexdigest()
                               for name in ('session_projection.py','resource_files.py','cold_display.py','service.py')},
              'summary':{mix:{'cpuTotalSeconds':sum(row['cpuSeconds'] for row in samples if row['mix']==mix),
                                'wallTotalSeconds':sum(row['wallSeconds'] for row in samples if row['mix']==mix),
                                'httpMedianMs':statistics.median(row['httpInteractionMs'] for row in samples if row['mix']==mix),
                                'httpP95Ms':sorted(row['httpInteractionMs'] for row in samples if row['mix']==mix)[37],
                                'atomicBytes':sum(v['bytes'] for row in samples if row['mix']==mix for v in row['atomicWrites'].values())}
                         for mix in mixes},
              'limits':['Synthetic current-source AppService/SQLite/private browser subscribers; no provider calls',
                        'Background readers stopped equally; does not measure total live CPU or paint',
                        'Atomic byte counter adds one stat per successful write; no extra encoding. HTTP includes event-loop wait, not paint.',
                        'Three declared workload mixes are sensitivity tests, not claimed production frequencies',
                        'Process peak RSS is fixture-specific, not a production memory or leak finding']}
    (output/'receipt.json').write_text(original_dumps(report,indent=2)+'\n')
    print(original_dumps({'summary':report['summary'], 'checks':checks}),flush=True)
    await client.close()

asyncio.run(main())
