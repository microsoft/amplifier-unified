"""Offline real Worker + loop-live: deferred settings reach two genuine inputs."""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))

PROVIDER = '''
import asyncio, json, os
from pathlib import Path
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock
__amplifier_module_type__ = "provider"
class FixtureProvider:
    name = "fixture"
    def __init__(self, config=None, coordinator=None):
        self.config, self.coordinator = config or {}, coordinator
    def get_info(self):
        return ProviderInfo(id="fixture", display_name="Offline fixture", defaults={"model":"first"})
    def get_config_schema(self):
        return {"fields":[{"id":"reasoning_effort","field_type":"choice","required":False,"choices":["low","high"]}]}
    async def list_models(self):
        return [{"id":"first"},{"id":"second"}]
    def parse_tool_calls(self, response): return []
    async def complete(self, request, **kwargs):
        with Path(self.config["calls"]).open("a") as stream:
            stream.write(json.dumps({"pid":os.getpid(),"root":self.config["root"],
                "implementation":__file__,
                "model":request.model,"effort":request.reasoning_effort,
                "kwargsModel":kwargs.get("model"),"kwargsEffort":kwargs.get("reasoning_effort")})+"\\n")
        return ChatResponse(content=[TextBlock(text="native-next-message-"+self.config["root"])])
async def mount(coordinator, config=None):
    if (config or {}).get("fail"):
        raise ValueError("Synthetic unavailable module")
    if (config or {}).get("hold"):
        Path(config["hold"]).touch()
        await asyncio.Event().wait()
    await coordinator.mount("providers", FixtureProvider(config, coordinator), name="fixture")
'''


async def until(predicate):
    async with asyncio.timeout(35):
        while not predicate():
            await asyncio.sleep(.02)


async def run(root, regression=False, binding=False, lifecycle=False):
    root.mkdir(parents=True, exist_ok=False)
    home, shared, workspace = (root/name for name in ('app', 'shared', 'workspace'))
    shared.mkdir(); workspace.mkdir()
    os.environ.update(AMPLIFIER_WEB_HOME=str(home), AMPLIFIER_HOME=str(shared),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root/'legacy'), AMPLIFIER_SESSION_STATE_HOME=str(root/'ownership'),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root/'capture'), AMPLIFIER_RUNTIME_IMMUTABLE='1')
    import amplifier_module_context_simple
    import amplifier_module_loop_live
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.server import create_app
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.builtin_behaviors import APPENDED_DEFAULTS
    from amplifier_web import draft_defaults
    from amplifier_web.draft_defaults_probe import query
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_web.session_files import sessions_dir
    from aiohttp.test_utils import TestClient, TestServer
    from aiohttp import ServerDisconnectedError
    from amplifier_web.auth import SESSION_COOKIE, new_session
    from amplifier_web.setup import SetupManager
    import yaml

    for key in list(os.environ):
        if key.endswith(('_API_KEY', '_ACCESS_TOKEN')):
            os.environ.pop(key, None)
    provider = root/'provider'/'amplifier_module_provider_fixture'
    provider.mkdir(parents=True)
    (provider/'__init__.py').write_text(PROVIDER)
    sys.path.insert(0, str(provider.parent))
    os.environ['PYTHONPATH'] = str(SOURCE)+os.pathsep+str(provider.parent)
    context = str(Path(amplifier_module_context_simple.__file__).parent)
    loop = str(Path(amplifier_module_loop_live.__file__).parent)
    bundles = []
    calls = root/'calls.jsonl'
    connection = {'root': 'shared' if binding else 'a', 'calls': str(calls),
                  **({'api_key': 'synthetic-fixture-only'} if binding else {})}
    for label in ('a', 'b', 'bad', 'slow'):
        path = root/f'bundle-{label}.yaml'
        path.write_text(yaml.safe_dump({'bundle': {'name': 'fixture-'+label, 'version': SNAPSHOT_VERSION},
            'session': {'orchestrator': {'module': 'loop-live', 'config': {'use_streaming': False}},
                        'context': {'module': 'context-simple', 'source': context}},
            'providers': [{'module': 'provider-fixture', 'source': str(provider.parent),
                           'config': {**connection, **({} if binding else {'root': label}),
                               **({'fail': True} if label == 'bad' else {}),
                               **({'hold': str(root/'mount-held')} if label == 'slow' else {})}}]}))
        bundles.append(str(path))
    # Binding mode exercises the configured host catalog, not a fabricated key.
    # Its offline provider accepts raw but never emits a network request.
    if binding:
        from amplifier_web.provider_recording import RAW_MODULES
        assert 'provider-openai' in RAW_MODULES
        # The fixture is mounted under OpenAI's module ID so the actual host
        # recording default participates, while its implementation stays local.
        for path in map(Path, bundles):
            value = yaml.safe_load(path.read_text())
            value['providers'][0].update(module='provider-openai', instance_id='fixture')
            path.write_text(yaml.safe_dump(value))
        package = root/'provider'/'amplifier_module_provider_openai'
        package.mkdir()
        (package/'__init__.py').write_text(
            PROVIDER.replace('name="fixture")', 'name="openai")') + '\nOpenAIProvider = FixtureProvider\n')
        (home/'diagnostics').mkdir(parents=True)
        (home/'diagnostics/config.json').write_text('{"providerRequests":true}')
    (shared/'settings.yaml').write_text(yaml.safe_dump({'bundle': {'active': bundles[0], 'app': []},
        'web_bundles': {'excluded': list(APPENDED_DEFAULTS)},
        **({'config': {'providers': [{'id': 'fixture', 'module': 'provider-openai',
            'source': str(provider.parent), 'config': connection}]}} if binding else {}),
        'sources': {'modules': {'loop-live': loop, 'context-simple': context,
            ('provider-openai' if binding else 'provider-fixture'): str(provider.parent)}}}))
    # Real composition/metadata query in this isolated process. No host runtime
    # downloader is needed: the fixture's explicit modules are already installed.
    async def resolver(home, workspace, bundle=None, app_bundle=None, **kwargs):
        if binding and kwargs.get('catalog'):
            manager = SetupManager(home)
            # Replace only dependency/probe transport with a local metadata
            # fixture; host discovery, cache and binding producers stay real.
            async def offline_probe(action, args, workspace):
                import amplifier_module_provider_openai as offline
                from amplifier_web.provider_probe import public
                assert Path(offline.__file__).resolve() == package/'__init__.py'
                provider = offline.FixtureProvider()
                return {'info': public(provider.get_info()),
                    'configSchema': provider.get_config_schema(), 'models': await provider.list_models(),
                    'modelsSupported': True, 'modelsProvenance': 'provider-api'}
            manager.probe = offline_probe
            try:
                return await manager.discover_catalog(workspace, instance=kwargs.get('instance'),
                    selected_only=kwargs.get('selected_only', False))
            finally:
                await manager.catalog.close()
        return await query({'home': str(home), 'workspace': workspace, 'bundle': bundle,
            'sessionId': kwargs.get('session_id'), 'catalog': kwargs.get('catalog', False),
            'refresh': kwargs.get('refresh', False), 'replacement': kwargs.get('replacement'),
            'desiredBundle': kwargs.get('desired_bundle', False)})
    draft_defaults.resolve_defaults = resolver
    runtime = RuntimeManager(command=[sys.executable, '-u', str(Path(__file__).resolve()), '--worker'],
        startup_timeout=45, retention={'prewarm_on_select': False})
    verified = []
    original_control = runtime.control
    async def observed_control(sid, operation, args, **kwargs):
        result = await original_control(sid, operation, args, **kwargs)
        if operation == 'provider.verifyConnection':
            assert args.get('expectedConnectionKey') and result == {'verified': True}
            verified.append(sid)
        return result
    runtime.control = observed_control
    app = await create_app(home, workspace=workspace, runtime=runtime, voice=False,
        background_updates=False, preload_providers=False)
    service = app['service']; runtime.app_bridge, runtime.home = service.app_bridge, home
    endings = []
    original_event = service.on_runtime_event
    async def observed_event(kind, payload):
        await original_event(kind, payload)
        if kind == 'runtime.ended':
            current = service._session(payload['sessionId'])
            endings.append({'reason': payload.get('reason'), 'owner': payload.get('runtimeOwnerId'),
                'turns': {turn['id']: turn['phase'] for turn in current.get('execution', {}).get('turns', [])},
                'failure': (current.get('failure') or {}).get('category')})
    service.on_runtime_event = observed_event
    client = TestClient(TestServer(app))
    report = {'regression': regression, 'versions': {name: importlib.metadata.version(name) for name in
        ('amplifier-core', 'amplifier-foundation', 'amplifier-module-loop-live', 'amplifier-module-context-simple')},
        'processes': []}
    worker_processes = []
    spawn = asyncio.create_subprocess_exec
    async def observed_spawn(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        if '--worker' in args:
            worker_processes.append(process)
        return process
    asyncio.create_subprocess_exec = observed_spawn
    try:
        await client.start_server()
        headers = {'Cookie': SESSION_COOKIE+'='+new_session(app['session_secret']),
                   'X-Amplifier-State-Transport': 'delta-v1'}
        async def action(name, args, identity):
            response = await client.post('/api/actions', headers=headers, json={'action': name, 'args': args, 'id': identity})
            value = await response.json()
            assert response.status == 200 and value.get('accepted'), value
            return value
        await action('session.create', {'bundle': bundles[0], 'title': 'Native next message'}, 'create')
        current = service._session(); sid = current['id']
        await action('session.naming', {'id': sid, 'automatic': False}, 'no-name-model')
        if binding:
            # Real ordinary startup before the pending bundle+model replacement.
            await action('conversation.send', {'sessionId': sid, 'text': 'BASELINE_INPUT'}, 'baseline-input')
            await until(lambda: current.get('collaborationGeneration', {}).get('terminal'))
            await until(lambda: runtime.workers.get(sid, {}).get('parked'))
            assert len(calls.read_text().splitlines()) == 1
            # Keep the existing exactly-once oracle focused on the next inputs.
            calls.write_text('')
        if lifecycle:
            outcomes = []
            for revision, bundle, model, effort, identity in (
                    (0, bundles[0], 'second', 'high', 'model-only'),
                    (1, bundles[1], 'first', 'low', 'bundle-change')):
                starts, count = len(worker_processes), len(calls.read_text().splitlines())
                await action('conversation.configure', {'sessionId': sid, 'expectedRevision': revision,
                    'bundle': bundle, 'selection': {'instance': 'fixture', 'model': model, 'effort': effort}},
                    'save-' + identity)
                assert len(worker_processes) == starts and len(calls.read_text().splitlines()) == count
                assert current.get('modelConnectionKey')
                await action('conversation.send', {'sessionId': sid, 'text': identity,
                    'expectedConfigurationRevision': revision + 1}, identity)
                await until(lambda: current.get('collaborationGeneration', {}).get('terminal'))
                await until(lambda: runtime.workers.get(sid, {}).get('parked'))
                turn = next(turn for turn in current['execution']['turns'] if turn['id'] == identity)
                observed = [json.loads(line) for line in calls.read_text().splitlines()]
                outcomes.append({'input': identity, 'phase': turn['phase'], 'error': current.get('error'),
                    'revision': current.get('appliedConfigurationRevision'), 'model': observed[-1]['model'],
                    'effort': observed[-1]['effort']})
                assert len(observed) == count + 1 and observed[-1]['model'] == model and observed[-1]['effort'] == effort
                assert turn['phase'] == 'completed', outcomes
                assert not current.get('failure') and not current.get('error'), outcomes
                assert current.get('appliedConfigurationRevision') == revision + 1
            messages = SessionHistoryStore(sessions_dir(workspace)/runtime.workers[sid]['runtime_id']).load_messages()
            ids = [(message.get('metadata') or {}).get('amplifier_input', {}).get('id') for message in messages]
            assert all(ids.count(identity) == 1 for identity in ('baseline-input', 'model-only', 'bundle-change'))
            assert len(worker_processes) == 2 and len(verified) >= 2
            assert not any(event['failure'] == 'execution_interrupted' for event in endings), endings
            report.update(passed=True, completedTurns=outcomes, endings=endings,
                nonemptyBindingVerified=True, inputsExactlyOnce=True, modelOnlyReusedProcess=True,
                bundleChangedProcess=True)
            return
        starts_before_save = len(worker_processes)
        calls_before_save = calls.read_text() if calls.exists() else None
        for revision, bundle, model, effort in [(0,bundles[1],'second','high'),(1,bundles[1],'first','low')]:
            await action('conversation.configure', {'sessionId':sid, 'expectedRevision':revision,
                'bundle':bundle, 'selection':{'instance':'fixture','model':model,'effort':effort}}, 'save-'+str(revision))
        if binding:
            assert current.get('modelConnectionKey')
        if not binding:
            assert not runtime.workers and not worker_processes and not calls.exists()
            report['zeroStartsBeforeSend'] = True
        else:
            assert len(worker_processes) == starts_before_save
            assert calls.read_text() == calls_before_save
            report['configurationSaveDidNotStartWork'] = True
        if regression:
            # Deliberate stale-selection injection before the provider boundary;
            # the same acceptance oracle below must reject it.
            original = runtime._input_configuration
            async def stale(captured):
                startup, guard = await original(captured)
                startup['pendingModelSelection'] = {'instance':'fixture','model':'second','effort':'high'}
                return startup, guard
            runtime._input_configuration = stale
        await action('conversation.send', {'sessionId':sid,'text':'FIRST_GENUINE_INPUT','expectedConfigurationRevision':2}, 'input-one')
        await until(lambda: sid in runtime.workers or current.get('status') == 'error')
        assert sid in runtime.workers, current.get('error')
        report['processes'].append(runtime.workers[sid]['process'].pid)
        await until(lambda: current.get('collaborationGeneration', {}).get('terminal'))
        assert calls.exists(), current.get('error')
        await until(lambda: runtime.workers[sid].get('parked'))
        first = [json.loads(line) for line in calls.read_text().splitlines()]
        assert len(first) == 1 and first[0]['root'] == ('shared' if binding else 'b') and first[0]['model'] == 'first' and first[0]['effort'] == 'low', first
        if binding:
            assert len(verified) >= 1
            assert Path(first[0]['implementation']).resolve() == package/'__init__.py'
            report['nonemptyBindingVerified'] = True
        assert current['appliedConfigurationRevision'] == 2 and not current.get('pendingBundleSelection')
        await action('conversation.configure', {'sessionId':sid,'expectedRevision':2,'bundle':bundles[0],
            'selection':{'instance':'fixture','model':'second','effort':'high'}}, 'save-adjacent')
        assert len(calls.read_text().splitlines()) == 1
        await action('conversation.send', {'sessionId':sid,'text':'SECOND_GENUINE_INPUT','expectedConfigurationRevision':3}, 'input-two')
        await until(lambda: current.get('collaborationGeneration', {}).get('terminal'))
        await until(lambda: runtime.workers[sid].get('parked'))
        report['processes'].append(runtime.workers[sid]['process'].pid)
        observed = [json.loads(line) for line in calls.read_text().splitlines()]
        assert observed[1]['root'] == ('shared' if binding else 'a') and observed[1]['model'] == 'second' and observed[1]['effort'] == 'high', observed
        assert len(set(report['processes'])) == 2
        if binding:
            assert len(verified) >= 2
        messages = SessionHistoryStore(sessions_dir(workspace)/runtime.workers[sid]['runtime_id']).load_messages()
        ids = [(message.get('metadata') or {}).get('amplifier_input',{}).get('id') for message in messages]
        assert ids.count('input-one') == ids.count('input-two') == 1, ids
        if binding:
            assert ids.count('baseline-input') == 1
        assert len(worker_processes) == 2 + int(binding)
        native_id = runtime.workers[sid]['runtime_id']
        baseline = SessionHistoryStore(sessions_dir(workspace)/native_id).load_messages()
        await action('conversation.configure', {'sessionId':sid,'expectedRevision':3,'bundle':bundles[2]}, 'save-unavailable')
        response = await client.post('/api/actions', headers=headers, json={'action':'conversation.send',
            'args':{'sessionId':sid,'text':'KEEP_FAILED_INPUT','expectedConfigurationRevision':4},'id':'input-unavailable'})
        failure = await response.json()
        assert not failure.get('accepted') and failure.get('receipt', {}).get('delivery') == 'failed', failure
        assert current['pendingBundleSelection']['bundle'] == bundles[2]
        assert any(m.get('text') == 'KEEP_FAILED_INPUT' and m.get('delivery', {}).get('status') == 'failed'
                   for m in current['messages'])
        assert SessionHistoryStore(sessions_dir(workspace)/native_id).load_messages() == baseline
        assert len(calls.read_text().splitlines()) == 2
        await action('conversation.configure', {'sessionId':sid,'expectedRevision':4,'bundle':bundles[3]}, 'save-slow')
        sending = asyncio.create_task(client.post('/api/actions', headers=headers, json={'action':'conversation.send',
            'args':{'sessionId':sid,'text':'KEEP_STOPPED_INPUT','expectedConfigurationRevision':5},'id':'input-stop'}))
        await until(lambda: (root/'mount-held').exists())
        await action('conversation.stop', {'sessionId':sid}, 'stop-delayed-application')
        try:
            stopped_response = await asyncio.wait_for(sending, 45)
            stopped = await stopped_response.json()
            assert not stopped.get('accepted'), stopped
            report['stopTransport'] = 'rejected'
        except ServerDisconnectedError:
            # Stop cancels the original request handler. A lost response alone
            # proves nothing: reconcile below against process exit and exact
            # native history, without replaying the input.
            report['stopTransport'] = 'interrupted'
        await until(lambda: sid not in runtime.workers)
        assert current['pendingBundleSelection']['bundle'] == bundles[3]
        assert current['interruptionRevision'] == 1
        assert len(calls.read_text().splitlines()) == 2
        assert SessionHistoryStore(sessions_dir(workspace)/native_id).load_messages() == baseline
        report.update(passed=True, calls=observed, inputsExactlyOnce=True, adjacentCycle=True,
            failedMountRolledBack=True, failedTextRetained=True, stoppedMountRolledBack=True,
            noFailureReplay=True, observedWorkerStarts=len(worker_processes))
    except BaseException as exc:
        report.update(passed=False, error=type(exc).__name__+': '+str(exc))
        if 'current' in locals():
            report['applicationFailure'] = {key: current.get(key) for key in
                ('nextMessageError', 'nextMessageFailure', 'error', 'failure')}
        raise
    finally:
        await asyncio.wait_for(client.close(), 60)
        asyncio.create_subprocess_exec = spawn
        report['cleanupConfirmed'] = all(process.returncode is not None for process in worker_processes)
        (root/'result.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
        assert report['cleanupConfirmed']


async def main():
    if '--worker' in sys.argv:
        from amplifier_web.runtime_worker import Worker, configure_protocol
        configure_protocol()
        await Worker().run()
    else:
        lifecycle = '--lifecycle' in sys.argv
        await run(Path(sys.argv[1]), '--stale-regression' in sys.argv,
                  '--connection-binding' in sys.argv or lifecycle, lifecycle)


if __name__ == '__main__':
    asyncio.run(main())
