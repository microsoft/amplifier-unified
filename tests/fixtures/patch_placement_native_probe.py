"""Finite production-worker placement probe; only provider and mutant are synthetic.

Adapted from TOOL002's queued_configuration_native_probe at dea6fbadc: real
Worker.run/prepare_manager/ownership/park/replace, retained process identities,
ordinary close before owned fallback, and a failing cleanup qualification gate.
No personal settings, remote provider, installed-file edit or worker subclass.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import signal
import sys

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
READ = 'configuration.patchPlacement.read'
BIND = 'configuration.patchPlacement.bind'
MODULE = 'tool-apply-patch'

PROVIDER = r'''
import asyncio, json, os
from pathlib import Path
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
__amplifier_module_type__ = "provider"

def receipt(message):
    text = message.content if isinstance(message.content, str) else ''.join(
        b.text for b in message.content or [] if getattr(b, 'type', '') == 'text')
    value = json.loads(text)
    if 'success' in value:
        assert value['success'], value
        value = value['output']
    if isinstance(value, str): value = json.loads(value)
    assert value.get('accepted'), value
    return value

def body(message):
    return receipt(message)['result']

class PlacementProvider:
    name = 'placement'
    def __init__(self, coordinator=None, config=None):
        self.coordinator, self.config = coordinator, config if config is not None else {}
    def get_info(self):
        return ProviderInfo(id=self.name, display_name='Offline placement fixture',
            capabilities=['tools'], defaults={'model':'offline-placement'})
    def get_config_schema(self): return {'fields':[]}
    async def list_models(self): return []
    def parse_tool_calls(self, response): return response.tool_calls or []
    async def complete(self, request, **kwargs):
        path = Path(self.config['scenario'])
        async with asyncio.timeout(30):
            while not path.exists(): await asyncio.sleep(.02)
        s = json.loads(path.read_text())
        with Path(s['calls']).open('a') as out:
            out.write(json.dumps({'pid':os.getpid(), 'inputIds': [
                (m.metadata or {}).get('amplifier_input',{}).get('id')
                for m in request.messages if m.role == 'user']})+'\n')
        users = [str(m.content) for m in request.messages if m.role=='user'
                 and not (m.metadata or {}).get('ephemeral')]
        results = {m.tool_call_id:m for m in request.messages if m.role=='tool'}
        if users and 'PLACEMENT_NEXT' in users[-1] and s.get('variant')=='when-idle':
            if 'placement-duplicate-tool' in results:
                value = receipt(results['placement-duplicate-tool'])
                assert value.get('duplicate'), value
                return ChatResponse(content=[TextBlock(text='placement-next-duplicate-verified')])
            return ChatResponse(content=[], tool_calls=[ToolCall(id='placement-duplicate-tool', name='app_control',
                arguments={'operation':'dispatch','args':{'action':'configuration.patchPlacement.bind',
                    'args':json.loads(Path(s['request']).read_text()),'id':'placement-bind-command'}})])
        if not users or 'BIND_WHEN_IDLE' not in users[-1]:
            return ChatResponse(content=[TextBlock(text='placement-finite-turn-complete')])
        if 'placement-bind-tool' in results:
            body(results['placement-bind-tool'])
            async with asyncio.timeout(30):
                while not Path(s['release']).exists(): await asyncio.sleep(.02)
            return ChatResponse(content=[TextBlock(text='placement-binding-turn-complete')])
        if 'placement-read-tool' in results:
            observed = body(results['placement-read-tool'])
            async with asyncio.timeout(30):
                while not s.get('sourceMessageId'):
                    await asyncio.sleep(.02)
                    s = json.loads(path.read_text())
            args = {'sessionId':s['sid'], 'moduleId':'tool-apply-patch',
                'expectedRevision':observed['revision'],
                'expectedExecutionRevision':observed['executionRevision'],
                'choice':'bind-to-execution', 'sourceMessageId':s['sourceMessageId'], 'whenIdle':True}
            Path(s['request']).write_text(json.dumps(args))
            return ChatResponse(content=[], tool_calls=[ToolCall(id='placement-bind-tool', name='app_control',
                arguments={'operation':'dispatch','args':{'action':'configuration.patchPlacement.bind',
                    'args':args,'id':'placement-bind-command'}})])
        return ChatResponse(content=[], tool_calls=[ToolCall(id='placement-read-tool', name='app_control',
            arguments={'operation':'dispatch','args':{'action':'configuration.patchPlacement.read',
                'args':{'sessionId':s['sid'],'moduleId':'tool-apply-patch'},'id':'placement-read-command'}})])

async def mount(coordinator, config=None):
    await coordinator.mount('providers', PlacementProvider(coordinator, config or {}), name='placement')
'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def process_stamp(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def record(root, value):
    with (root / f'worker-events-{os.getpid()}.jsonl').open('a') as out:
        out.write(json.dumps(value) + '\n')


async def worker(root, mutant):
    from amplifier_web.runtime_worker import Worker, configure_protocol
    from amplifier_web.runtime_controls import RuntimeControls
    original = RuntimeControls.__init__
    source = inspect.getsource(original)
    if mutant:
        def skip_preservation(self, *args, **kwargs):
            requested = kwargs.get('preserve_configuration') is True
            kwargs['preserve_configuration'] = False
            if requested:
                record(root, {'stage':'injected-skip-preservation'})
            original(self, *args, **kwargs)
        RuntimeControls.__init__ = skip_preservation
    (root / f'worker-source-{os.getpid()}.json').write_text(json.dumps({
        'pid':os.getpid(), 'probePid':os.getppid(), 'processGroup':os.getpgrp(),
        'startTicks':process_stamp(os.getpid()), 'mutant':mutant,
        'runtimeControlsSha256':digest(Path(inspect.getfile(original))),
        'originalConstructorSha256':hashlib.sha256(source.encode()).hexdigest(),
        'executedConstructorSha256':hashlib.sha256(inspect.getsource(RuntimeControls.__init__).encode()).hexdigest(),
        'workerSha256':digest(Path(inspect.getfile(Worker))), 'python':sys.executable}))
    # Interpreter-level observations only: the production methods are never
    # replaced. Coroutine resumption may emit several call events; these are
    # entry witnesses, deliberately not advertised as completed-call counts.
    seen = set()
    def profile(frame, event, arg):
        if event != 'call':
            return
        name = frame.f_code.co_name
        if name not in {'start','park','acquire_for_mutation','remount','prepare_manager'}:
            return
        file = Path(frame.f_code.co_filename)
        if ((file.name == 'runtime_worker.py' and name in {'start','park','acquire_for_mutation','remount'})
                or file.name == 'session.py' and file.parent.name == 'host' and name == 'prepare_manager'):
            obj = frame.f_locals.get('self')
            witness = (name, bool(getattr(obj, 'parked', False)))
            if witness not in seen:
                seen.add(witness)
                record(root, {'stage':'production-method-entry','method':name,'parked':witness[1]})
    configure_protocol()
    sys.setprofile(profile)
    try:
        await Worker().run()
    finally:
        sys.setprofile(None)
        modules = {}
        for name in ('amplifier_core', 'amplifier_core._engine', 'amplifier_foundation',
                     'amplifier_module_loop_live', 'amplifier_module_context_simple',
                     'amplifier_module_tool_filesystem', 'amplifier_module_tool_apply_patch',
                     'amplifier_web.host.session', 'amplifier_web.runtime_worker',
                     'amplifier_web.patch_placement'):
            loaded = sys.modules.get(name)
            location = getattr(loaded, '__file__', None)
            modules[name] = ({'path':str(Path(location).resolve()),'sha256':digest(Path(location))}
                             if location else {'status':'not_loaded'})
        record(root, {'stage':'loaded-module-sources','modules':modules})


async def until(predicate, seconds=45):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(.02)


async def writer_available(workspace, sid):
    def acquire():
        from amplifier_foundation.session import SharedSessionStore, SessionBusyError
        try:
            handle = SharedSessionStore(workspace, sid).acquire(app='placement-native-probe')
        except SessionBusyError:
            return False
        handle.release()
        return True
    return await asyncio.wait_for(asyncio.to_thread(acquire), 3)


def history(workspace, sid):
    from amplifier_foundation.session import SharedSessionStore
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_web.session_files import sessions_dir
    handle = SharedSessionStore(workspace, sid).acquire(app='placement-native-history-probe')
    try:
        return SessionHistoryStore(sessions_dir(workspace) / sid).load_messages()
    finally:
        handle.release()


async def reap_owned(process, fallback):
    for signum in (signal.SIGTERM, signal.SIGKILL):
        if process.returncode is not None:
            return
        assert os.getpgid(process.pid) == process.pid
        fallback.append({'pid':process.pid, 'signal':signum.name, 'source':'probe-fallback'})
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            continue
    assert process.returncode is not None, 'Owned worker exit not confirmed'


def require_next_success(owner):
    """A parked terminal generation alone is not a successful next response."""
    identity = 'placement-next-input'
    generation = owner['collaborationGeneration']
    assert generation['terminal']
    outcomes = [event for event in owner.get('generations', [])
                if event.get('generation_id') == generation['id']
                and event.get('sessionId') == event.get('rootSessionId') == owner['id']
                and event.get('event') in {'generation.finished', 'generation.failed', 'generation.detached'}]
    assert len(outcomes) == 1 and outcomes[0]['event'] == 'generation.finished', 'Next generation did not succeed'
    assert identity in outcomes[0].get('input_ids', []), 'Completion belongs to another input'
    final = [message for message in owner['messages']
             if message.get('role') == 'assistant' and message.get('inputId') == identity
             and message.get('generationId') == generation['id']
             and message.get('text') == 'placement-finite-turn-complete'
             and message.get('response', {}).get('status') == 'completed'
             and message.get('response', {}).get('phase') == 'final']
    assert len(final) == 1, 'Missing exact successful final response for placement-next-input'
    return {'generationId':generation['id'], 'inputId':identity, 'outcome':outcomes[0]['event'],
            'messageId':final[0]['id'], 'text':final[0]['text']}


async def probe(root, variant):
    root.mkdir(parents=True, exist_ok=False)
    home, workspace, checkout, shared = (root / x for x in ('app','history','checkout','shared'))
    for p in (workspace, checkout, shared): p.mkdir()
    for p in (workspace, checkout): (p / 'file.txt').write_text('old\n')
    (checkout / 'denied').mkdir()
    (checkout / 'denied/file.txt').write_text('old\n')
    os.environ.update(AMPLIFIER_HOME=str(shared), AMPLIFIER_WEB_HOME=str(home),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'),
        AMPLIFIER_SESSION_STATE_HOME=str(root / 'ownership'),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / 'capture'),
        AMPLIFIER_RUNTIME_IMMUTABLE='1', PYTHONPATH=str(SOURCE))
    for key in list(os.environ):
        if key.endswith(('_API_KEY','_ACCESS_TOKEN')) or key in {'OPENAI_KEY','ANTHROPIC_AUTH_TOKEN'}:
            os.environ.pop(key, None)
    import yaml
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.builtin_behaviors import APPENDED_DEFAULTS
    from amplifier_web.management import Management
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.runtime_controls import override_path
    from amplifier_web.service import AppService
    from importlib import import_module
    # Missing native prerequisites fail, never importorskip or install here.
    paths = {n:str(Path(import_module('amplifier_module_'+n.replace('-','_')).__file__).parent)
        for n in ('loop-live','context-simple','tool-filesystem','tool-apply-patch')}
    versions = {n:importlib.metadata.version(n) for n in ('amplifier-core','amplifier-foundation',
        'amplifier-module-loop-live','amplifier-module-context-simple',
        'amplifier-module-tool-filesystem','amplifier-module-tool-apply-patch')}
    provider = root / 'provider/amplifier_module_provider_placement'
    provider.mkdir(parents=True)
    (provider / '__init__.py').write_text(PROVIDER)
    scenario, calls, release, request = (root / n for n in ('scenario.json','calls.jsonl','release-final','request.json'))
    plan = {'session':{'orchestrator':{'module':'loop-live','config':{'use_streaming':False}},
            'context':{'module':'context-simple','source':paths['context-simple']}},
        'providers':[{'module':'provider-placement','source':str(provider.parent),
            'config':{'scenario':str(scenario),'default_model':'offline-placement'}}],
        'tools':[{'module':'tool-filesystem','source':paths['tool-filesystem'],
            'config':{'allowed_write_paths':[str(checkout)], 'denied_write_paths':[str(checkout/'denied')]}},
            {'module':MODULE,'source':paths[MODULE], 'config':{'engine':'native',
                'working_dir':str(workspace),'allowed_write_paths':[str(checkout)],
                'denied_write_paths':[str(checkout/'denied')]}}]}
    bundle = root / 'bundle.yaml'
    bundle.write_text(yaml.safe_dump({'bundle':{'name':'placement-native','version':SNAPSHOT_VERSION},**plan}))
    (shared / 'settings.yaml').write_text(yaml.safe_dump({'bundle':{'active':str(bundle),'app':[]},
        'web_bundles':{'excluded':list(APPENDED_DEFAULTS)},
        'sources':{'modules':{**paths,'provider-placement':str(provider.parent)}}}))
    mutant = variant == 'skip-preservation'
    command = [sys.executable, '-u', str(Path(__file__).resolve()), '--worker', str(root)]
    if mutant: command.append('--skip-preservation')
    runtime = RuntimeManager(command=command, startup_timeout=45, retention={'prewarm_on_select':False})
    app = AppService(home, runtime, workspace=workspace)
    app.management = Management(app)
    runtime.home, runtime.app_bridge = home, app.app_bridge
    report = {'variant':variant,'versions':versions,'modulePaths':paths,'stages':[],
        'signals':[],'cleanupErrors':[],'processes':[]}
    processes, sid, primary = [], None, None
    original_spawn, original_killpg = asyncio.create_subprocess_exec, os.killpg
    async def spawn(*argv, **kwargs):
        p = await original_spawn(*argv, **kwargs)
        if '--worker' in argv and str(root) in argv:
            processes.append(p)
            # Parent-owned inventory precedes all child imports/provenance work.
            (root / f'worker-owned-{p.pid}.json').write_text(json.dumps({
                'pid':p.pid, 'probePid':os.getpid(), 'processGroup':os.getpgid(p.pid),
                'startTicks':process_stamp(p.pid)}))
        return p
    def killpg(pid, sig):
        if any(p.pid == pid for p in processes):
            report['signals'].append({'pid':pid,'signal':signal.Signals(sig).name,'source':'production'})
        return original_killpg(pid, sig)
    asyncio.create_subprocess_exec, os.killpg = spawn, killpg
    try:
        async def action(name, args, identity):
            result = await app.dispatch(name, args, command_id=identity, include_state=False)
            assert result.get('accepted'), result
            return result
        await action('session.create', {'bundle':str(bundle),'title':'Native placement'}, 'placement-create')
        owner = app._session(); sid = owner['id']
        await action('session.naming', {'id':sid,'automatic':False}, 'placement-disable-naming')
        # Initial synthetic host record, not a simulated handoff or permission edit.
        owner.update(workingDirectory=str(checkout), executionRevision=1)
        app._publish_changes(sessions={sid})
        native = owner.get('runtimeSessionId') or owner.get('nativeIdentity') or sid
        stored = override_path(native); stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_text(json.dumps(plan, indent=2)+'\n')
        controls = stored.with_name('control-state.json')
        controls.write_text(json.dumps({'selection':None,'configurator':{'disabled':{}},
            'fixtureSentinel':{'preserve':[1,2,3]}}, indent=2)+'\n')
        scenario_value = {'sid':sid,'calls':str(calls),'release':str(release),'request':str(request),'variant':variant}
        scenario.write_text(json.dumps(scenario_value))
        report.update(sessionId=sid, nativeId=native, initialPlanHash=digest(stored))
        if variant in {'parked','parked-remount','when-idle'}:
            await action('conversation.send', {'sessionId':sid,'text':'PLACEMENT_WARMUP'}, 'placement-warm-input')
            await until(lambda: sid in runtime.workers and runtime.workers[sid].get('parked'))
            assert await writer_available(workspace, native)
            report['stages'].append('real-initial-startup-and-park')
            report['parkedPid'] = runtime.workers[sid]['process'].pid
        if variant == 'parked-remount':
            # Invalidate the production stat-stamp without altering any setting.
            # A resulting frozen-product CAS refusal is a diagnostic failure,
            # never permission for this probe to substitute a new snapshot.
            previous = controls.stat()
            same_bytes = controls.read_bytes()
            os.utime(controls, ns=(previous.st_atime_ns, previous.st_mtime_ns + 2_000_000_000))
            assert controls.read_bytes() == same_bytes and controls.stat().st_mtime_ns != previous.st_mtime_ns
            report['staleRemountRequested'] = True
        # Freeze bytes at the actual preparation/admission boundary, not before
        # an unrelated ordinary warmup is allowed to persist its controls.
        control_bytes, plan_bytes = controls.read_bytes(), stored.read_bytes()
        report['controlHashBefore'] = digest(controls)
        if variant == 'when-idle':
            grant = (await action('capability.grant', {'sessionId':sid,'effects':['runtime_configuration'],
                'scopes':['session'],'reason':'Synthetic explicit placement choice'}, 'placement-grant'))['result']
            # A host bridge observer only records bytes and identities; it does
            # not substitute answers, admission, start, acquire, park or persist.
            original_bridge = runtime.app_bridge
            async def bridge(op, args, identity):
                result = await original_bridge(op, args, identity)
                if op == 'dispatch' and args.get('action') == READ:
                    report.setdefault('controlHashAtRead', digest(controls))
                if op == 'dispatch' and args.get('action') == BIND:
                    if result.get('duplicate'):
                        report.setdefault('duplicateObservations', []).append({
                            'generation':copy.deepcopy(owner.get('collaborationGeneration')),
                            'controlHash':digest(controls), 'receipt':result.get('result')})
                    else:
                        assert 'generationAtBind' not in report, 'A second binding was admitted'
                        report['controlHashAtBind'] = digest(controls)
                        report['generationAtBind'] = copy.deepcopy(owner.get('collaborationGeneration'))
                        report['admittedReceipt'] = result.get('result')
                return result
            runtime.app_bridge = bridge
            await action('conversation.send', {'sessionId':sid,
                'text':'BIND_WHEN_IDLE: bind this root patch instance to its recorded execution folder, retaining all other settings.'},
                'placement-original-input')
            source = next(m for m in owner['messages'] if m.get('inputId')=='placement-original-input')
            scenario_value['sourceMessageId'] = source['id'];scenario.write_text(json.dumps(scenario_value))
            await until(lambda: owner.get('pendingConfiguration',{}).get('phase') == 'queued')
            assert not owner['collaborationGeneration']['terminal']
            assert owner['pendingConfiguration']['authority']['grantId'] == grant['id']
            assert not await writer_available(workspace, native)
            bind_args = json.loads(request.read_text())
            before_calls = calls.read_bytes()
            report['stages'].append('actual-generation-queued-binding')
            release.touch()
        else:
            snapshot = (await action(READ, {'sessionId':sid,'moduleId':MODULE}, 'placement-read-command'))['result']
            if variant == 'cold' or mutant:
                assert not runtime.workers and not calls.exists()
                assert snapshot['mounted']['status']=='unobserved'
            else:
                assert snapshot['mounted']['workingDirectory']==str(workspace)
                assert runtime.workers[sid]['process'].pid==report['parkedPid']
            bind_args = {'sessionId':sid,'moduleId':MODULE,'expectedRevision':snapshot['revision'],
                'expectedExecutionRevision':snapshot['executionRevision'],'choice':'bind-to-execution'}
            before_calls = calls.read_bytes() if calls.exists() else b''
            await action(BIND, bind_args, 'placement-bind-command')
        await until(lambda: 'placement-bind-command' in app.state.get('managementResults',{})
            and sid not in app.management.pending_config_tasks, seconds=60)
        current = app.management.configuration_record(sid, owner['pendingConfiguration']['operationId'])
        report.update(operationId=current['id'], state=current['state'], attempts=len(current.get('attempts',[])),
            controlHashAfter=digest(controls), actualReadback=(await action(READ,
                {'sessionId':sid,'moduleId':MODULE}, 'placement-post-read'))['result'])
        row = runtime.workers.get(sid)
        report['workerReadyAtSettlement'] = bool(row and row['ready'].done()
            and not row['ready'].cancelled() and row['ready'].exception() is None)
        report['stages'].append('binding-settled')
        if variant != 'when-idle':
            assert (calls.read_bytes() if calls.exists() else b'') == before_calls
            report['noBindingProviderReplay'] = True
        if variant == 'parked-remount':
            events = [json.loads(line) for p in root.glob('worker-events-*.jsonl')
                      for line in p.read_text().splitlines()]
            assert any(x.get('method') == 'remount' for x in events), 'Production stale remount was not entered'
            report['staleRemountObserved'] = True
            if current['state']['phase'] != 'ready':
                report['productionBoundaryFailure'] = 'parked-remount-binding-refused'
        if mutant:
            injected = [json.loads(line) for p in root.glob('worker-events-*.jsonl') for line in p.read_text().splitlines()]
            assert sum(x['stage']=='injected-skip-preservation' for x in injected)>=1
            assert report['workerReadyAtSettlement']
            assert current['state']['phase']=='error' and controls.read_bytes()!=control_bytes
            assert stored.read_bytes()==plan_bytes and current['state']['effect']!='mounted'
            report['negativeWitness']='real-startup-preservation-skipped'
            raise AssertionError('Placement startup changed original control-state bytes')
        assert current['state']['phase']=='ready' and current['state']['effect']=='mounted', report
        assert controls.read_bytes()==control_bytes, 'Placement rewrote original control-state bytes'
        expected = json.loads(plan_bytes)
        next(r for r in expected['tools'] if r['module']==MODULE)['config']['working_dir']=str(checkout)
        assert json.loads(stored.read_text())==expected
        assert report['actualReadback']['mounted']=={'status':'observed','workingDirectory':str(checkout)}
        assert owner['workspace']==str(workspace) and owner['executionRevision']==1
        assert owner.get('configuration') is None
        assert current['request']['commandId']=='placement-bind-command' and len(current['attempts'])==1
        assert report['workerReadyAtSettlement']
        assert len(processes)==2 and processes[0].returncode==0
        if variant in {'parked','parked-remount'}:
            assert processes[0].pid==report['parkedPid']
        if variant=='when-idle':
            assert owner['collaborationGeneration']['terminal']
            assert current['request']['source']['generation']['id']==report['generationAtBind']['id']
            assert report['controlHashAtRead']==report['controlHashAtBind']==digest(controls)
            # Provider continued once to consume BIND and finish, never replayed
            # during the native configuration replacement.
            assert len(calls.read_text().splitlines()) == 4  # Warmup + READ + BIND + final.
        report['stages'].append('field-only-and-actual-replacement-verified')
        observed = copy.deepcopy(current); stamp=stored.stat().st_mtime_ns
        count=len(processes); calls_before=calls.read_bytes() if calls.exists() else b''
        async def object_identity():
            value = await runtime._request(sid, 'dependencies', patchPlacement={'moduleId':MODULE})
            assert value['status'] == 'observed' and value['objectId']
            return value['objectId']
        duplicate_object = await object_identity()
        if variant!='when-idle':
            duplicate=await action(BIND,bind_args,'placement-bind-command')
            assert duplicate['duplicate']
        await asyncio.sleep(0)
        assert app.management.configuration_record(sid,current['id'])==observed
        assert stored.stat().st_mtime_ns==stamp and len(processes)==count
        assert (calls.read_bytes() if calls.exists() else b'')==calls_before
        assert await object_identity() == duplicate_object
        if variant!='when-idle':
            report['stages'].append('exact-duplicate-no-effect')
        # Production runtime control invokes actual mounted guard/hook pipeline.
        async def tool(name, arguments):
            result=await runtime.control(sid,'tool.invoke',{'name':name,'arguments':arguments,'actor':'ui'})
            assert 'result' in result, result
            return result['result']
        patch={'type':'update_file','path':'file.txt','diff':'@@\n-old\n+new'}
        unread=await tool('apply_patch',patch)
        assert not unread['success'] and unread['error']['code']=='file_source_unread'
        denied=await tool('apply_patch',{**patch,'path':str(workspace/'file.txt')})
        assert not denied['success'] and denied['error']['effect']=='none'
        assert denied['error']['message'].startswith('Access denied:')
        assert (workspace/'file.txt').read_text()=='old\n'
        assert (await tool('read_file',{'file_path':'file.txt'}))['success']
        assert (await tool('apply_patch',patch))['success']
        assert (checkout/'file.txt').read_text()=='new\n' and (workspace/'file.txt').read_text()=='old\n'
        await until(lambda: runtime.workers[sid].get('parked'))
        assert await writer_available(workspace,native)
        report['stages'].append('fresh-read-write-and-park')
        messages=history(workspace,native)
        if variant=='when-idle':
            ids=[(m.get('metadata') or {}).get('amplifier_input',{}).get('id') for m in messages]
            assert ids.count('placement-original-input')==1
            assert sum(m.get('tool_call_id')=='placement-bind-tool' for m in messages)==1
            report['originalInputCount']=report['originalBindToolCount']=1
        previous_generation = (owner.get('collaborationGeneration') or {}).get('id')
        next_object = await object_identity()
        await action('conversation.send',{'sessionId':sid,'text':'PLACEMENT_NEXT'},'placement-next-input')
        await until(lambda: owner.get('collaborationGeneration',{}).get('terminal')
            and owner['collaborationGeneration'].get('id') != previous_generation
            and any(m.get('inputId')=='placement-next-input' for m in owner['messages'])
            and runtime.workers[sid].get('parked'))
        assert len(processes)==2
        assert app.management.configuration_record(sid,current['id'])==observed
        assert stored.stat().st_mtime_ns==stamp
        if variant=='when-idle':
            assert any(m.get('text')=='placement-next-duplicate-verified' for m in owner['messages'])
            assert len(calls.read_text().splitlines())==6
            assert await object_identity() == next_object
            assert current['request']['source']['generation']['id'] == report['generationAtBind']['id']
            assert len(report['duplicateObservations']) == 1
            duplicate = report['duplicateObservations'][0]
            assert duplicate['receipt'] == report['admittedReceipt']
            assert duplicate['generation']['id'] != report['generationAtBind']['id']
            report['stages'].append('exact-duplicate-no-effect')
        else:
            report['nextSuccessfulResponse'] = require_next_success(owner)
        report['stages'].append('next-generation-and-park-usable')
    except BaseException as exc:
        primary=exc
        report['failure']={'type':type(exc).__name__,'message':str(exc)}
        raise
    finally:
        release.touch()
        close=asyncio.create_task(app.close())
        try:
            await asyncio.wait_for(asyncio.shield(close),45)
        except BaseException as exc:
            report['cleanupErrors'].append(type(exc).__name__+': '+str(exc))
        try:
            for p in processes:
                try: await reap_owned(p,report['signals'])
                except BaseException as exc: report['cleanupErrors'].append(type(exc).__name__+': '+str(exc))
            if not close.done():
                try: await asyncio.wait_for(asyncio.shield(close),5)
                except BaseException as exc:
                    report['cleanupErrors'].append('close: '+type(exc).__name__)
                    close.cancel();await asyncio.gather(close,return_exceptions=True)
            report['processes']=[{'pid':p.pid,'returncode':p.returncode} for p in processes]
            report['runtimeRowsAfterClose']=len(runtime.workers)
            report['preparationsAfterClose']=len(runtime._preparations)
            if sid:
                try:
                    report['writerAvailableAfterClose']=await writer_available(workspace,native)
                except BaseException as exc:
                    report['cleanupErrors'].append('writer: '+type(exc).__name__+': '+str(exc))
        finally:
            asyncio.create_subprocess_exec,os.killpg=original_spawn,original_killpg
            report['workerSources'], report['workerEvents'] = [], []
            for pattern, field in [('worker-source-*.json','workerSources'), ('worker-events-*.jsonl','workerEvents')]:
                for p in sorted(root.glob(pattern)):
                    try:
                        rows = [json.loads(x) for x in p.read_text().splitlines()] if p.suffix=='.jsonl' else [json.loads(p.read_text())]
                        report[field].extend(rows)
                    except (OSError, ValueError) as exc:
                        report['cleanupErrors'].append(p.name+': '+type(exc).__name__)
            (root/'evidence.json').write_text(json.dumps(report,indent=2)+'\n')
        if (report['signals'] or report['cleanupErrors'] or runtime.workers or runtime._preparations
                or any(p.returncode!=0 for p in processes) or sid and report.get('writerAvailableAfterClose') is not True):
            detail='Normal native cleanup not confirmed: '+json.dumps(report['signals']+report['cleanupErrors'])
            if primary: primary.add_note(detail)
            else: raise AssertionError(detail)


async def bounded_probe(root, variant):
    loop, task=asyncio.get_running_loop(),asyncio.current_task()
    def cancel():
        if not task.cancelling(): task.cancel()
    for sig in (signal.SIGTERM,signal.SIGINT): loop.add_signal_handler(sig,cancel)
    try:
        async with asyncio.timeout(180): await probe(root,variant)
    finally:
        for sig in (signal.SIGTERM,signal.SIGINT): loop.remove_signal_handler(sig)


if __name__=='__main__':
    if sys.argv[1]=='--worker':
        asyncio.run(worker(Path(sys.argv[2]),'--skip-preservation' in sys.argv))
    else:
        asyncio.run(bounded_probe(Path(sys.argv[1]),sys.argv[2]))
