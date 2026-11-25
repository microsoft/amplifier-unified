"""Real host/storage/browser fixture with a controlled, no-model runtime.

The seed is host-owned synthetic history, not a native-worker timing claim.
Only external provider discovery is replaced. All dispatch, execution reducers,
persistence, window reads and application shutdown use their normal owners.
"""
import argparse
import asyncio
import copy
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import time
import uuid
from functools import wraps


TOKEN = 'fixture-execution-switching'
COUNTS = dict(messages=2901, nodes=23195, receipts=11582, workers=506)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


class Counts:
    """Fixture-only counters around unchanged source functions, no body reads."""
    def __init__(self):
        self.rows, self.originals, self.enabled = {}, [], False
        for module, names in (
            ('cold_display', ('load',)), ('session_projection', ('persist',)),
            ('resource_files', ('marked_references',)),
            ('execution_records', ('materialize', '_read')),
            ('execution_working_set', ('refresh_usage',)),
        ):
            try:
                owner = importlib.import_module('amplifier_web.' + module)
            except ModuleNotFoundError as error:
                if error.name != 'amplifier_web.' + module:
                    raise
                self.rows[module] = {'available': False}
                continue
            for name in names:
                original = getattr(owner, name)
                label = module + '.' + name
                self.rows[label] = dict(available=True, calls=0, wallMs=0., cpuMs=0.)
                def counted(function, label):
                    @wraps(function)
                    def invoke(*args, **kwargs):
                        if not self.enabled:
                            return function(*args, **kwargs)
                        started, cpu = time.perf_counter(), time.process_time()
                        try:
                            return function(*args, **kwargs)
                        finally:
                            row = self.rows[label]
                            row['calls'] += 1
                            row['wallMs'] += (time.perf_counter() - started) * 1000
                            row['cpuMs'] += (time.process_time() - cpu) * 1000
                    return invoke
                wrapper = counted(original, label)
                setattr(owner, name, wrapper)
                self.originals.append((owner, name, original))
                if label == 'execution_working_set.refresh_usage':
                    execution = importlib.import_module('amplifier_web.execution')
                    if getattr(execution, 'refresh_session_usage', None) is original:
                        execution.refresh_session_usage = wrapper
                        self.originals.append((execution, 'refresh_session_usage', original))

    def restore(self):
        for owner, name, original in self.originals:
            setattr(owner, name, original)


def message(index):
    return dict(id=f'message-{index:05}', role='user' if index % 2 == 0 else 'assistant',
                text=f'Saved synthetic message {index:05}. ' + ('Retained project evidence. ' * 72),
                inputId=f'input-{index // 2:05}', createdAt=1700000000 + index)


def populate(owner):
    sid = owner['id']
    owner['messages'] = [message(i) for i in range(COUNTS['messages'])]
    owner['workers'] = [dict(id=f'worker-{i}', sessionId=f'worker-{i}', status='completed',
        task=f'Synthetic assignment {i}. ' + 'Retained task. ' * 40,
        reportReceipts=[dict(id=f'report-{i}', text='Retained result. ' * 40)])
        for i in range(COUNTS['workers'])]
    receipts = [dict(id=f'llm-{i}', kind='llm', revision=1, producerId='fixture-producer',
        turnId=f'input-{i // 8:05}', sessionId=sid, rootSessionId=sid,
        phase='completed', provider='synthetic', model='no-model',
        startedAt=1700000000 + i / 8, endedAt=1700000000.5 + i / 8,
        usage=dict(inputTokens=1000, outputTokens=100, totalTokens=1100, costUsd=.01))
        for i in range(COUNTS['receipts'])]
    nodes = [dict(row, liveObservation=True) for row in receipts]
    nodes.extend(dict(id=f'tool-{i}', kind='tool', revision=1, producerId='fixture-producer',
        turnId=f'input-{i // 8:05}', sessionId=sid, rootSessionId=sid,
        tool='fixture-read', toolCallId=f'call-{i}',
        phase='completed', startedAt=1700000000 + i / 8,
        endedAt=1700000000.75 + i / 8, input=f'Synthetic request {i}',
        output=f'Synthetic result {i}. ' + 'Retained tool detail. ' * 12)
        for i in range(COUNTS['nodes'] - len(nodes)))
    # One ordinary large field makes the public Work page issue a real detail
    # descriptor; the browser must not manufacture a bare legacy field lookup.
    nodes[-94]['output'] += 'Retained exact detail. ' * 240
    owner['execution'] = dict(currentTurnId=None, nodes=nodes, retiredUsageNodes=receipts,
        turns=[dict(id=f'input-{i:05}', inputId=f'input-{i:05}',
            anchorMessageId=f'message-{i * 2:05}', phase='completed',
            startedAt=1700000000 + i * 2, endedAt=1700000001 + i * 2)
            for i in range((COUNTS['messages'] + 1) // 2)])
    # Settle canonical derived values before freezing the seed oracle; the
    # first real receipt must not look like corruption of an uninitialized row.
    from amplifier_web.execution import refresh_usage
    refresh_usage(owner['execution'])


class Runtime:
    """One admitted input, controlled execution receipts, one explicit Stop."""
    def __init__(self, home):
        self.home, self.sent, self.stopped = home, [], []
        self.started, self.pulses, self.event_costs = [], [], []
        self.admissions = []
        self.call = None
        self.ready = False
        self.runtime_owner = None
        self.release = asyncio.Event()
        self.emit = self.session = self.input_id = None

    def ledger(self, operation, **data):
        with (self.home / 'runtime-attempts.jsonl').open('a') as stream:
            stream.write(json.dumps(dict(operation=operation, **data)) + '\n')

    async def start(self, session, emit):
        self.started.append(session['id'])

    async def send(self, session, text, input_id, emit):
        self.ledger('send', sessionId=session['id'], inputId=input_id, text=text)
        self.sent.append((session['id'], input_id))
        assert len(self.sent) == 1, 'Unexpected replay or additional input'
        self.session, self.input_id, self.emit = session['id'], input_id, emit
        native = session.get('runtimeSessionId') or session['id']
        generation = 'fixture-generation-' + input_id
        self.runtime_owner = str(uuid.uuid5(uuid.NAMESPACE_URL, 'switching-runtime-' + input_id))
        observed = time.time()
        # The controlled transport reports the same lifecycle/binding envelope
        # as the native runtime. It does not create or relax admission policy.
        await emit('runtime.generation', dict(sessionId=self.session, rootSessionId=self.session,
            event='generation.started', generation_id=generation,
            initial_input_id=input_id, observedAt=observed, runtimeOwnerId=self.runtime_owner,
            interruptionRevision=session.get('interruptionRevision', 0)))
        await emit('runtime.status', dict(sessionId=self.session, status='working',
            event='input.delivered', inputId=input_id, runtimeOwnerId=self.runtime_owner))
        # Inspect only bounded canonical binding metadata, never the execution
        # tree/messages. An allowed legacy fallback would not prove this scope.
        async with self.service.lock:
            current = self.service._session(self.session, hydrate=False)
            active = current.get('collaborationGeneration') or {}
            status = current.get('messageAnnotations', {}).get('input-status:' + input_id, {}).get('inputStatus', {})
            assert active.get('id') == generation and active.get('inputIds') == [input_id]
            assert active.get('terminal') is False
            assert (current.get('runtimeSessionId') or current['id']) == native
            assert status.get('inputId') == input_id and status.get('generationId') == generation
            assert status.get('basis') == 'generation_started' and status.get('startedAt') == observed
            bound = dict(generationId=active['id'], inputIds=list(active['inputIds']),
                interruptionRevision=current.get('interruptionRevision', 0),
                configurationRevision=current.get('configurationPendingRevision', 0),
                inputStatusGenerationId=status['generationId'], runtimeOwnerId=self.runtime_owner)
            del current, active, status
        self.call = dict(id='fixture-current-llm', kind='llm', revision=1,
            producerId='fixture-producer', sessionId=native, rootSessionId=native,
            turnId=input_id, admissionInputId=input_id, generationId=generation,
            runtimeOwnerId=self.runtime_owner,
            phase='running', liveObservation=True, provider='synthetic', model='no-model',
            startedAt=1700010000)
        self.ledger('capacity.admit', sessionId=self.session, callId=self.call['id'], inputId=input_id)
        started, cpu = time.perf_counter(), time.process_time()
        admission = await self.service.app_bridge('capacity.admit', {
            'call': self.call, '_generationId': generation, '_runtimeSessionId': native,
            '_runtimeOwnerId': self.runtime_owner, 'waitForCapacity': True}, self.session)
        self.admissions.append(dict(callId=self.call['id'], inputId=input_id,
            runtimeSessionId=native, generationId=generation, result=admission,
            observedBinding=bound,
            wallMs=(time.perf_counter() - started) * 1000,
            cpuMs=(time.process_time() - cpu) * 1000))
        assert admission['allowed'] is True, admission
        self.call.update(admittedAt=admission['admittedAt'], budgetRevision=admission['budgetRevision'])
        await self.event(1)
        self.ready = True
        await self.release.wait()

    async def event(self, revision):
        started, cpu = time.perf_counter(), time.process_time()
        await self.emit('execution.event', dict(id='fixture-current-tool', kind='tool',
            revision=revision, producerId='fixture-producer', sessionId=self.session,
            rootSessionId=self.session, turnId=self.input_id,
            runtimeOwnerId=self.runtime_owner,
            tool='fixture-controlled', toolCallId='fixture-call',
            toolObservationId=str(uuid.uuid5(uuid.NAMESPACE_URL, 'switching-current')),
            phase='running', liveObservation=True, startedAt=1700010000,
            summary=f'Controlled execution update {revision}', input='No external operation'))
        self.event_costs.append(dict(revision=revision,
            wallMs=(time.perf_counter() - started) * 1000,
            cpuMs=(time.process_time() - cpu) * 1000))

    async def pulse(self):
        assert self.ready and self.emit and not self.release.is_set()
        for _ in range(3):
            await self.event(len(self.event_costs) + 1)
            revision = len(self.event_costs)
            started, cpu = time.perf_counter(), time.process_time()
            assert len(self.admissions) == 1 and self.admissions[0]['result']['allowed'] is True
            await self.emit('execution.event', dict(self.call, revision=revision,
                usage=dict(inputTokens=1000, outputTokens=revision, totalTokens=1000 + revision)))
            self.event_costs.append(dict(kind='llm-usage', revision=revision,
                callId=self.call['id'], admissionInputId=self.call['admissionInputId'],
                wallMs=(time.perf_counter() - started) * 1000,
                cpuMs=(time.process_time() - cpu) * 1000))
            await asyncio.sleep(.1)
        # Append and complete a genuinely new step, in addition to revisions.
        identity = f'fixture-completed-{len(self.pulses)}'
        started, cpu = time.perf_counter(), time.process_time()
        await self.emit('execution.event', dict(id=identity, kind='tool', revision=1,
            producerId='fixture-producer', sessionId=self.session, rootSessionId=self.session,
            runtimeOwnerId=self.runtime_owner,
            turnId=self.input_id, tool='fixture-read', toolCallId=identity,
            toolObservationId=str(uuid.uuid5(uuid.NAMESPACE_URL, identity)),
            phase='completed', liveObservation=True, startedAt=1700010000,
            endedAt=1700010001, output='New synthetic completion'))
        self.event_costs.append(dict(kind='new-completion', id=identity,
            wallMs=(time.perf_counter() - started) * 1000,
            cpuMs=(time.process_time() - cpu) * 1000))

    async def stop(self, sid, *, reason='stopped'):
        self.ledger('stop', sessionId=sid, reason=reason)
        self.stopped.append(sid)
        assert self.stopped == [self.session], 'Unexpected Stop replay'
        self.release.set()

    async def control(self, *args, **kwargs):
        return {}

    async def close(self):
        self.release.set()
        await asyncio.gather(*self.pulses)


async def main(home, restart, seed_only):
    home.mkdir(parents=True, exist_ok=True)
    for name in ('shared', 'app', 'workspace', 'cache', 'tmp', 'ownership', 'imports'):
        (home / name).mkdir(exist_ok=True)
    os.environ.update(AMPLIFIER_HOME=str(home / 'shared'), AMPLIFIER_WEB_HOME=str(home / 'app'),
        AMPLIFIER_SESSION_STATE_HOME=str(home / 'ownership'), AMPLIFIER_UNIFIED_IMPORT_HOME=str(home / 'imports'),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(home / 'capture'),
        XDG_CACHE_HOME=str(home / 'cache'), TMPDIR=str(home / 'tmp'))
    # HOME is deliberately inherited; this fixture never reads ambient accounts.
    from aiohttp import web
    from configured_browser import seed_connection, synthetic_discovery
    from amplifier_web.server import create_app
    from amplifier_web import cold_display
    from amplifier_web.session_projection import stored_execution
    import amplifier_web
    package = Path(amplifier_web.__file__).resolve().parent
    expected = Path(os.environ['AMPLIFIER_TEST_PACKAGE_ROOT']).resolve()
    assert package == expected
    static = package / 'static'
    loaded = dict(package=str(package), build=json.loads((static / 'build.json').read_text()),
        staticSha256=digest({str(p.relative_to(static)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted(static.rglob('*')) if p.is_file()}),
        files={name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in
               ('service.py', 'execution.py', 'cold_display.py', 'session_projection.py')})
    source_root = package.parent
    platform_paths = dict(amplifier_operations='amplifier_operations', amplifier_kernels='amplifier_kernels',
        amplifier_recall='components/recall-delivery/amplifier_recall', amplifier_scheduling='components/scheduling-delivery/amplifier_scheduling',
        amplifier_outputs='components/outputs-delivery/amplifier_outputs',
        amplifier_workspace_metadata='components/workspace-metadata-delivery/amplifier_workspace_metadata',
        amplifier_worktrees='components/worktrees-delivery/amplifier_worktrees',
        amplifier_publishing='components/publishing-delivery/amplifier_publishing',
        amplifier_portability='components/portability-delivery/amplifier_portability')
    loaded['platformImports'] = {}
    for name, relative in platform_paths.items():
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(source_root / relative), (name, path, relative)
        loaded['platformImports'][name] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    loaded['retainedDependencyVersions'] = {}
    for name in ('amplifier-unified', 'amplifier-core', 'amplifier-foundation', 'aiohttp', 'pydantic', 'PyYAML'):
        try:
            distribution = importlib.metadata.distribution(name)
            loaded['retainedDependencyVersions'][name] = dict(version=distribution.version,
                metadataRoot=str(distribution.locate_file('')))
        except importlib.metadata.PackageNotFoundError:
            loaded['retainedDependencyVersions'][name] = {'metadataAvailable': False}
    loaded['scope'] = 'Current selected source with retained dependencies; distribution metadata is not an installed release claim.'
    runtime = Runtime(home)
    seed_connection(home / 'shared')
    counts = Counts()
    ready_started = time.perf_counter()
    with synthetic_discovery():
        app = await create_app(home / 'app', workspace=str(home / 'workspace'), runtime=runtime,
            voice=False, background_updates=False, background_work=False, preload_providers=False)
        app['control_token'] = TOKEN
        service = app['service']
        runtime.service = service
        request_costs = []

        @web.middleware
        async def request_cost(request, handler):
            # Same-thread server handler envelope, including any queue wait;
            # CPU includes other event-loop work and is not exclusive attribution.
            started, cpu = time.perf_counter(), time.process_time()
            try:
                return await handler(request)
            finally:
                if request.path == '/api/actions' and len(request_costs) < 256:
                    request_costs.append(dict(path=request.path,
                        wallMs=(time.perf_counter() - started) * 1000,
                        cpuMs=(time.process_time() - cpu) * 1000))

        app.middlewares.append(request_cost)
        oracle_path = home / 'oracle.json'
        if seed_only:
            assert not oracle_path.exists(), 'Each variant needs fresh synthetic state'
            ids = []
            for number, title in enumerate(('Retained manager', 'Switch destination', 'Second destination')):
                row = service._new_session({'title': title})
                row['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, f'execution-switching-{number}'))
                if number == 0:
                    populate(row)
                else:
                    row['messages'] = [dict(id=f'small-{number}', role='assistant',
                        text=f'Small conversation {number}', createdAt=1700000000)]
                service._state['sessions'].append(row)
                ids.append(row['id'])
            service._publish_changes(sessions=set(ids))
            large = service._session(ids[0])
            seed_tree = stored_execution(large['execution'])
            seeded_nodes = seed_tree['nodes']
            seeded_receipts = seed_tree['retiredUsageNodes']
            oracle = dict(ids=ids, counts=COUNTS, seedMessagesSha256=digest(large['messages']),
                # This unchanged step begins inside the last-100 resident tail
                # and is displaced by the input's new tool/LLM plus six appends.
                workersSha256=digest(large['workers']), oldTool=copy.deepcopy(seeded_nodes[-94]),
                unchangedSeedNodesSha256=digest([n for n in seeded_nodes if n['id'] != 'llm-0']),
                unchangedSeedReceiptsSha256=digest([n for n in seeded_receipts if n['id'] != 'llm-0']),
                seedLlmNode=copy.deepcopy(next(n for n in seeded_nodes if n['id'] == 'llm-0')),
                seedLlmReceipt=copy.deepcopy(next(n for n in seeded_receipts if n['id'] == 'llm-0')),
                oldestMessage=message(0), bytes=dict(messages=len(encoded(large['messages'])),
                    execution=len(encoded(seed_tree))))
            oracle_path.write_text(json.dumps(oracle))
            del large, seed_tree, seeded_nodes, seeded_receipts
        else:
            assert oracle_path.exists(), 'Seed must have completed in a separate exited process'
            oracle = json.loads(oracle_path.read_text())
            ids = oracle['ids']

        def committed_shape():
            # Detached read of the actual committed owner, never borrowing or
            # warming the resident session's mutable execution object.
            from amplifier_web.state_records import load_owner
            descriptor, _ = load_owner(service.db, ids[0])
            row = cold_display.load(service.db, descriptor['$viewPayload'])
            for key, reference in row.get('_coldFields', {}).items():
                row[key] = cold_display.load(service.db, reference)
            tree = row['execution']
            actual = dict(messages=len(row['messages']), nodes=len(tree['nodes']),
                receipts=len(tree['retiredUsageNodes']), workers=len(row['workers']))
            if not restart:
                assert actual == COUNTS, (actual, COUNTS)
            assert digest(row['messages'][:COUNTS['messages']]) == oracle['seedMessagesSha256']
            return actual

        actual_committed_counts = committed_shape()
        if seed_only:
            for sid in ids:
                await service.dispatch('session.pin', {'id': sid, 'pinned': True})
            await service.dispatch('view.update', {'patch': {'setupJourney': 'done', 'navPinned': True}})
            await service.dispatch('session.select', {'id': ids[0]})
            seed_runner = web.AppRunner(app)
            await seed_runner.setup()
            await seed_runner.cleanup()
            counts.restore()
            print(json.dumps(dict(seeded=True, counts=actual_committed_counts,
                seedBytes=oracle['bytes'], loaded=loaded)), flush=True)
            return

        def verify():
            row = service._session(ids[0])
            assert digest(row['messages'][:COUNTS['messages']]) == oracle['seedMessagesSha256']
            assert digest(row['workers']) == oracle['workersSha256']
            tree = stored_execution(row['execution'])
            old = next(n for n in tree['nodes'] if n['id'] == oracle['oldTool']['id'])
            assert old == oracle['oldTool']
            seed_node_ids = [*(f'llm-{i}' for i in range(COUNTS['receipts'])),
                *(f'tool-{i}' for i in range(COUNTS['nodes'] - COUNTS['receipts']))]
            seed_receipt_ids = [f'llm-{i}' for i in range(COUNTS['receipts'])]
            seed_node_set, seed_receipt_set = set(seed_node_ids), set(seed_receipt_ids)
            nodes = [n for n in tree['nodes'] if n['id'] in seed_node_set]
            receipts = [n for n in tree['retiredUsageNodes'] if n['id'] in seed_receipt_set]
            assert [n['id'] for n in nodes] == seed_node_ids
            assert [n['id'] for n in receipts] == seed_receipt_ids
            assert digest([n for n in nodes if n['id'] != 'llm-0']) == oracle['unchangedSeedNodesSha256']
            assert digest([n for n in receipts if n['id'] != 'llm-0']) == oracle['unchangedSeedReceiptsSha256']
            correction = dict(revision=2, endedAt=1700000001,
                usage=dict(inputTokens=1000, outputTokens=101, totalTokens=1101, costUsd=.01))
            assert nodes[0] == {**oracle['seedLlmNode'], **correction}
            assert receipts[0] == {**oracle['seedLlmReceipt'], **correction}
            additions = [n for n in tree['nodes'] if n['id'] not in seed_node_set]
            added_receipts = [n for n in tree['retiredUsageNodes'] if n['id'] not in seed_receipt_set]
            expected_additions = {'fixture-current-tool', 'fixture-current-llm',
                *(f'fixture-completed-{i}' for i in range(1, 7))}
            assert len(additions) == 8 and {n['id'] for n in additions} == expected_additions
            assert [n['id'] for n in added_receipts] == ['fixture-current-llm']
            assert all(n['phase'] == 'completed' for n in additions if n['id'].startswith('fixture-completed-'))
            assert len(tree['nodes']) == COUNTS['nodes'] + 8
            assert len(tree['retiredUsageNodes']) == COUNTS['receipts'] + 1
            facts = dict(messages=len(row['messages']), messageSha256=digest(row['messages']),
                executionSha256=digest(tree), workersSha256=digest(row['workers']),
                nodes=len(tree['nodes']), receipts=len(tree.get('retiredUsageNodes', [])),
                seedPreserved=True, oldToolPreserved=True, allSeededNodesAndReceiptsPreserved=True,
                explicitLateCorrectionPreserved=True, exactCurrentAdditionsPreserved=True)
            if restart:
                assert facts == json.loads((home / 'before-restart.json').read_text()), 'Restart changed durable bodies'
                assert runtime.sent == runtime.stopped == []
            else:
                (home / 'before-restart.json').write_text(json.dumps(facts))
            return facts

        if restart:
            restart_readback = verify()
        else:
            restart_readback = None

        def resident_counts():
            # Metadata only: never request the virtual execution value, expose
            # a snapshot, inspect rows or retain a borrowed container reference.
            session = service._session(ids[0], hydrate=False)
            owner = getattr(session, '_working_execution', None)
            current = owner.current if owner is not None else None
            return dict(rawExecutionPresent=dict.__contains__(session, 'execution'),
                workingExecutionPresent=owner is not None,
                currentCounts=({name: len(current.get(name, ())) for name in
                    ('nodes', 'turns', 'retiredUsageNodes')} if current is not None else None))

        async def facts(request):
            return web.json_response(dict(ids=ids, counts=actual_committed_counts, seed=oracle['bytes'],
                sends=len(runtime.sent), stops=len(runtime.stopped), eventCosts=runtime.event_costs,
                modelAdmissions=runtime.admissions,
                controlledRuntimeReady=runtime.ready,
                requestCosts=request_costs, oldTool=oracle['oldTool'],
                sourceCounters=counts.rows,
                residentExecution=resident_counts(),
                pulsesDone=sum(t.done() for t in runtime.pulses), pulses=len(runtime.pulses),
                revision=service._state['revision'], restartReadback=restart_readback))

        async def operate(request):
            operation = (await request.json())['operation']
            if operation == 'begin-measurement':
                assert not counts.enabled
                counts.enabled = True
                return web.json_response({'started': True})
            if operation == 'end-measurement':
                counts.enabled = False
                return web.json_response({'sourceCounters': counts.rows, 'eventCosts': runtime.event_costs})
            if operation == 'pulse':
                assert len(runtime.pulses) < 6, 'Bounded six switch pulses'
                runtime.pulses.append(asyncio.create_task(runtime.pulse()))
                return web.json_response({'pulse': len(runtime.pulses)})
            if operation == 'late-receipt':
                # Real reducer/save path for a corrected old receipt, never direct SQL.
                payload = dict(id='llm-0', kind='llm', revision=2, producerId='fixture-producer',
                    sessionId=ids[0], rootSessionId=ids[0], turnId='input-00000',
                    phase='completed', liveObservation=True, startedAt=1700000000,
                    endedAt=1700000001, provider='synthetic', model='no-model',
                    usage=dict(inputTokens=1000, outputTokens=101, totalTokens=1101, costUsd=.01))
                await service.on_runtime_event('execution.event', payload)
                # Inspect the committed copy, not the resident session. Warming
                # its full mutable execution here would hide a cold detail bug.
                from amplifier_web.state_records import load_owner
                descriptor, _ = load_owner(service.db, ids[0])
                saved = cold_display.load(service.db, descriptor['$viewPayload'])
                reference = saved.get('_coldFields', {}).get('execution')
                if reference and reference.get('executionProjection') == 2:
                    from amplifier_web import execution_records
                    actual = execution_records.read_record(service.db, reference, 'nodes',
                        execution_records.key('nodes', {'id': 'llm-0'}))
                else:
                    tree = cold_display.load(service.db, reference) if reference else saved['execution']
                    actual = next(n for n in tree['nodes'] if n['id'] == 'llm-0')
                assert actual['usage']['outputTokens'] == 101
                return web.json_response({'lateReceiptPreserved': True, 'verification': 'detached committed record'})
            if operation == 'verify':
                await asyncio.gather(*runtime.pulses)
                assert len(runtime.sent) == len(runtime.stopped) == 1
                assert len(runtime.admissions) == 1 and runtime.admissions[0]['result']['allowed'] is True
                counts.enabled = False
                return web.json_response(verify())
            raise web.HTTPBadRequest(text='Unknown fixture operation')

        app.router.add_get('/fixture/execution-switching', facts)
        app.router.add_post('/fixture/execution-switching', operate)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] = app['allowed_origins'] | {url}
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        print(json.dumps(dict(url=url, ids=ids, loaded=loaded, restart=restart,
            backendReadyMs=(time.perf_counter() - ready_started) * 1000,
            restartReadback=restart_readback)), flush=True)
        try:
            await stop.wait()
        finally:
            try:
                await runner.cleanup()
            finally:
                counts.restore()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--restart', action='store_true')
    parser.add_argument('--seed-only', action='store_true')
    args = parser.parse_args()
    assert not (args.restart and args.seed_only)
    asyncio.run(main(args.home.resolve(), args.restart, args.seed_only))
