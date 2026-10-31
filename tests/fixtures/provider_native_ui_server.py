"""Ordinary-root browser fixture: real Foundation/Core and production workers.

Run with the prepared interpreter: python <this-file> <new-artifact-root>.
The first stdout line is the listening HTTP URL. Login uses the production
control-token flow with fixture-browser-control-token. No sessions or mounted
controls are seeded. The browser owns creation, selection and all four sends.

GET /fixture/native-proof is strictly observational: it never calls a runtime
control, starts a worker or reads a transcript/events.jsonl. configuration.providers
receipts are captured only if an ordinary client operation requested them; the
service's browsing catalog is reported separately. Missing observations stay absent.

Disable automatic naming through session.naming before each chat's first send
(or rename it through session.rename, which disables Auto). Naming calls, if any,
are retained too, not filtered out to manufacture four calls.
Only the provider, prepared-interpreter command selection and owned proof sinks
are fixture code. The audit guard refuses external networking and unlisted child
commands; it is not a replacement preparation, mount, selector or worker.
"""
from __future__ import annotations

import asyncio
import atexit
import copy
import hashlib
import importlib
import importlib.metadata
import ipaddress
import json
import os
from pathlib import Path
import signal
import sys
import time

SOURCE = Path(__file__).resolve().parents[2]
MODULES = {
    'amplifier_core': 'amplifier-core',
    'amplifier_core._engine': 'amplifier-core',
    'amplifier_foundation': 'amplifier-foundation',
    'amplifier_module_loop_live': 'amplifier-module-loop-live',
    'amplifier_module_context_simple': 'amplifier-module-context-simple',
    'amplifier_module_hook_context_intelligence': 'amplifier-module-hook-context-intelligence',
}
PROVIDER = r'''
import json, os, time
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock
__amplifier_module_type__ = 'provider'

class NativeFixtureProvider:
    name = 'native-fixture'
    def __init__(self, config=None, coordinator=None):
        self.config = config or {}
        self.coordinator = coordinator
        self.priority = self.config.get('priority', 100)
        self.default_model = self.config.get('default_model', 'native-model')
    def get_info(self):
        return ProviderInfo(id=self.name,
            display_name=self.config.get('display_name', 'Native fixture'),
            defaults={'model':self.default_model,
                      'reasoning_effort':self.config.get('reasoning_effort')})
    def get_config_schema(self):
        return {'fields':[{'id':'reasoning_effort','field_type':'choice',
                          'choices':['low','high','xhigh'],'required':False}]}
    async def list_models(self):
        return [{'id':'native-model','display_name':'Native model'}]
    def parse_tool_calls(self, response):
        return response.tool_calls or []
    async def complete(self, request, **kwargs):
        if self.coordinator is None:
            raise RuntimeError('Metadata-only provider must not execute inference')
        identities = [name for name, provider in
                      (self.coordinator.get('providers') or {}).items() if provider is self]
        if len(identities) != 1:
            raise RuntimeError('Cannot identify the actual mounted provider instance')
        row = {'sessionId':self.coordinator.session_id, 'instance':identities[0],
            'model':kwargs.get('model') or request.model or self.default_model,
            'effort':kwargs.get('reasoning_effort') or request.reasoning_effort
                     or self.config.get('reasoning_effort'),
            'inputIds':[(m.metadata or {}).get('amplifier_input',{}).get('id')
                        for m in request.messages if m.role == 'user'],
            'pid':os.getpid(), 'atNs':time.time_ns(), 'origin':__file__,
            'requestFields':{'model':request.model, 'reasoning_effort':request.reasoning_effort},
            'keywordOverrides':{key:kwargs.get(key) for key in ('model','reasoning_effort')},
            'providerDefaults':{'model':self.default_model,
                                'reasoning_effort':self.config.get('reasoning_effort')}}
        # One append syscall per request; retain every call, including auxiliaries.
        payload = (json.dumps(row)+'\n').encode()
        fd = os.open(self.config['proof_sink'], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            if os.write(fd, payload) != len(payload):
                raise RuntimeError('Incomplete provider proof append')
        finally:
            os.close(fd)
        return ChatResponse(content=[TextBlock(text='native-fixture-response')])

async def mount(coordinator, config=None):
    await coordinator.mount('providers', NativeFixtureProvider(config, coordinator),
                            name='native-fixture')
'''


def save(path, value):
    # Initial fixture settings and proof only; never a saved native control/plan.
    path.write_text(json.dumps(value, indent=2) + '\n')


def append(path, value):
    with path.open('a') as stream:
        stream.write(json.dumps(value) + '\n')


def file_identity(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def process_identity(pid):
    # Linux-only qualification: start ticks distinguish PID reuse, not just liveness.
    stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': pid, 'processGroup': int(stat[2]), 'startTicks': stat[19]}


def install_guard(root, commands=()):
    """Inherited via sitecustomize; fail before any installer or remote transport."""
    allowed = {tuple(command) for command in commands}
    script_identity = file_identity(sys.argv[0])
    def audit(event, args):
        refused = False
        if event == 'subprocess.Popen':
            refused = tuple(args[1]) not in allowed if isinstance(args[1], (tuple, list)) else True
        elif event in {'os.system', 'os.posix_spawn', 'os.exec'}:
            refused = True
        elif event in {'socket.connect', 'socket.getaddrinfo'}:
            address = args[1] if event == 'socket.connect' else args[0]
            # Unix sockets are local. TCP/DNS is restricted to numeric loopback.
            host = address[0] if isinstance(address, tuple) else address
            if event == 'socket.connect' and isinstance(address, (str, bytes)):
                return
            try:
                refused = host != 'localhost' and not ipaddress.ip_address(host).is_loopback
            except ValueError:
                refused = True
        if refused:
            append(root / f'guard-{os.getpid()}.jsonl', {'event': event, 'atNs': time.time_ns(),
                'detail': str(args)[:1000]})
            raise RuntimeError('Native fixture forbids external acquisition/transport or an unlisted subprocess')
    sys.addaudithook(audit)
    save(root / f'guard-ready-{os.getpid()}.json', {
        'pid': os.getpid(), 'script': sys.argv[0], 'allowedCommands': [list(c) for c in commands]})
    def loaded_sources():
        rows = {}
        for name in (*MODULES, 'amplifier_web.host.session', 'amplifier_web.runtime_worker',
                     'amplifier_web.runtime_controls', 'amplifier_module_provider_native_fixture'):
            location = getattr(sys.modules.get(name), '__file__', None)
            rows[name] = file_identity(location) if location else {'status': 'not_loaded'}
        save(root / f'loaded-{os.getpid()}.json', {'pid': os.getpid(), 'python': sys.executable,
            'script': script_identity, 'modules': rows})
    atexit.register(loaded_sources)


def read_json(path):
    if not path.exists():
        return None
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError(f'Fixture evidence exceeded its bound: {path.name}')
    return json.loads(path.read_text())


def calls_at(path):
    if not path.exists():
        return []
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Provider proof exceeded its bound')
    # Never silently drop an incomplete append or malformed call.
    data = path.read_text()
    if data and not data.endswith('\n'):
        raise ValueError('Provider proof append is incomplete')
    rows = [json.loads(line) for line in data.splitlines()]
    if len(rows) > 64:
        raise ValueError('Unexpectedly many provider requests')
    return rows


async def main(root):
    root.mkdir(parents=True, exist_ok=True)
    # Refuse reuse; a new attempt must not overwrite retained execution evidence.
    for name in ('app', 'workspace', 'shared', 'bootstrap', 'provider', 'user-home'):
        (root / name).mkdir()
    url_stream = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    interpreter = os.environ.get('AMPLIFIER_TEST_PYTHON', sys.executable)
    if os.path.abspath(interpreter) != os.path.abspath(sys.executable):
        raise RuntimeError('Launch the fixture with AMPLIFIER_TEST_PYTHON itself; no interpreter fallback')
    if os.name != 'posix' or not Path('/proc/self/stat').is_file():
        raise RuntimeError('This fixture requires Linux process identity evidence')
    command = [sys.executable, '-u', str(SOURCE / 'amplifier_web/runtime_worker.py')]
    commands = [command, *[command[:-1] + [str(SOURCE / 'amplifier_web' / name)]
                         for name in ('draft_defaults_probe.py', 'provider_probe.py')]]
    # Inherited credentials/source-store selectors cannot turn this into a user run.
    for name in list(os.environ):
        if (name.startswith(('AMPLIFIER_', 'COPILOT_', 'AZURE_', 'AWS_', 'OTEL_'))
                or name.endswith(('_API_KEY', '_ACCESS_TOKEN', '_AUTH_TOKEN'))
                or name in {'OPENAI_KEY', 'GH_TOKEN', 'GITHUB_TOKEN'}):
            os.environ.pop(name, None)
    os.environ.update(AMPLIFIER_HOME=str(root / 'shared'), AMPLIFIER_WEB_HOME=str(root / 'app'),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'), AMPLIFIER_SESSION_STATE_HOME=str(root / 'ownership'),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / 'capture'), AMPLIFIER_RUNTIME_IMMUTABLE='1',
        HOME=str(root / 'user-home'), XDG_CONFIG_HOME=str(root / 'user-home/.config'),
        XDG_CACHE_HOME=str(root / 'user-home/.cache'), PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(SOURCE), str(root / 'provider')]
    os.environ['PYTHONPATH'] = os.pathsep.join((str(root / 'bootstrap'), str(Path(__file__).parent),
                                              str(SOURCE), str(root / 'provider')))
    (root / 'bootstrap/sitecustomize.py').write_text(
        'import os, traceback\ntry:\n'
        '    from pathlib import Path\n'
        '    from provider_native_ui_server import install_guard\n'
        f'    install_guard(Path({str(root)!r}))\n'
        'except BaseException:\n'
        '    traceback.print_exc()\n'
        '    os._exit(78)\n')  # site normally swallows import errors; fail closed.
    install_guard(root, commands)
    os.chdir(root / 'workspace')
    processes, watchers, ready_events, control_reads, lifecycle, routing_reads = [], [], [], [], [], []
    runtime = runner = app = None
    primary_failure = None
    original_spawn = asyncio.create_subprocess_exec
    original_command = original_setup_perform = None
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    try:
        # Import readiness, not a mount/prepare dry-run. No missing package fallback.
        modules = {}
        paths = {}
        for name, distribution in MODULES.items():
            module = importlib.import_module(name)
            modules[name] = {**file_identity(module.__file__),
                             'version': importlib.metadata.version(distribution)}
            if name.startswith('amplifier_module_'):
                paths[name.removeprefix('amplifier_module_').replace('_', '-')] = str(Path(module.__file__).parent)
        from aiohttp import web
        from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
        from amplifier_web.runtime import RuntimeManager
        from amplifier_web.server import create_app
        from amplifier_web.setup import SetupManager
        from amplifier_web.auth import control_token_path
        from amplifier_web.session_files import validate_id
        package = root / 'provider/amplifier_module_provider_native_fixture'
        package.mkdir()
        (package / '__init__.py').write_text(PROVIDER)
        importlib.import_module('amplifier_module_provider_native_fixture')
        paths['provider-native-fixture'] = str(package.parent)
        bundle = root / 'root.yaml'
        save(bundle, {'bundle': {'name': 'provider-native-ui', 'version': '1.0.0'},
            'session': {'orchestrator': {'module': 'loop-live', 'source': paths['loop-live'],
                                        'config': {'use_streaming': False}},
                        'context': {'module': 'context-simple', 'source': paths['context-simple'],
                                    'config': {'max_tokens': 1000000}}}})
        settings = root / 'shared/settings.yaml'
        save(settings, {'bundle': {'active': str(bundle), 'app': []},
            'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)}, 'sources': {'modules': paths},
            'config': {'providers': [
                {'module': 'provider-native-fixture', 'id': identity, 'config': {
                    'display_name': label, 'priority': priority, 'default_model': 'native-model',
                    'reasoning_effort': effort, 'proof_sink': str(root / 'calls.jsonl')}}
                for identity, label, priority, effort in (
                    ('native-a', 'Native A', 1, 'low'), ('native-b', 'Native B', 20, 'high'))]}})
        settings_identity = file_identity(settings)
        control_token_path(root / 'app').write_text('fixture-browser-control-token\n')
        # Same local prepared-interpreter seam as bundle_switch_probe; real probes.
        original_command = RuntimeManager._command
        RuntimeManager._command = lambda self, *args, **kwargs: list(self.command or command)
        original_setup_perform = SetupManager.perform
        async def observed_setup(manager, action, arguments):
            # Observe the real returned optional diagnostic before another setup
            # owner replaces the shared view. Never synthesize success or retry.
            result = await original_setup_perform(manager, action, arguments)
            if action == 'routing.list':
                row = {'action': action, 'arguments': copy.deepcopy(arguments),
                       'atNs': time.time_ns(), 'result': copy.deepcopy(result)}
                routing_reads.append(row)
                append(root / 'routing-discovery.jsonl', row)
            return result
        SetupManager.perform = observed_setup
        async def observed_spawn(*argv, **kwargs):
            if list(argv) not in commands:
                raise RuntimeError(f'Unexpected fixture subprocess: {argv!r}')
            process = await original_spawn(*argv, **kwargs)
            entry = {'process': process, 'identity': {'pid': process.pid},
                     'command': list(argv), 'kind': Path(argv[-1]).stem}
            processes.append(entry)
            try:
                entry['identity'] = process_identity(process.pid)
            except OSError as exc:
                # Retain custody even if a failed probe exits before /proc is read.
                entry['identity']['identityError'] = f'{type(exc).__name__}: {exc}'
            append(root / 'processes.jsonl', {'event': 'spawned', **entry['identity'],
                                             'command': entry['command'], 'kind': entry['kind']})
            async def exited():
                code = await process.wait()
                append(root / 'processes.jsonl', {'event': 'reaped', **entry['identity'], 'returncode': code})
            watchers.append(asyncio.create_task(exited()))
            return process
        asyncio.create_subprocess_exec = observed_spawn
        runtime = RuntimeManager(command=command, startup_timeout=90)
        app = await create_app(root / 'app', workspace=str(root / 'workspace'), runtime=runtime,
                               voice=False, background_updates=False, preload_providers=False)
        service = app['service']
        runtime.app_bridge = service.app_bridge
        original_event, original_control = service.on_runtime_event, runtime.control
        async def observed_event(kind, payload):
            observed_at = time.time_ns()
            ready = None
            if kind == 'runtime.status' and payload.get('status') == 'ready' and payload.get('report'):
                row = runtime.workers.get(payload['sessionId'], {})
                ready = {'event': 'runtime.ready', 'source': 'RuntimeManager pipe reduction',
                    'pid': row.get('native_worker_pid'), 'atNs': observed_at, **copy.deepcopy(payload)}
            await original_event(kind, payload)
            if kind in {'runtime.nextMessageApplied', 'runtime.modelSelectionApplied', 'runtime.generation'}:
                event = {'kind': kind, 'atNs': observed_at, **copy.deepcopy(payload)}
                lifecycle.append(event)
                append(root / 'lifecycle.jsonl', event)
            if ready is not None:
                ready_events.append(ready)
                append(root / 'ready.jsonl', ready)
        async def observed_control(sid, operation, arguments=None, **kwargs):
            result = await original_control(sid, operation, arguments, **kwargs)
            if operation == 'configuration.providers':
                row = runtime.workers.get(sid, {})
                record = {'sessionId': sid, 'pid': row.get('native_worker_pid'),
                          'atNs': time.time_ns(), 'operation': operation, 'result': copy.deepcopy(result)}
                control_reads.append(record)
                append(root / 'provider-controls.jsonl', record)
            return result
        service.on_runtime_event, runtime.control = observed_event, observed_control
        static = SOURCE / 'amplifier_web/static'
        if not (static / 'index.html').is_file():
            raise RuntimeError('Prepared frontend assets are missing; fixture never builds them')
        readiness = {'python': sys.executable, 'prefix': sys.prefix, 'modules': modules,
            'modulePaths': paths, 'settings': file_identity(settings), 'rootBundle': file_identity(bundle),
            'fixture': file_identity(__file__), 'provider': file_identity(package / '__init__.py'),
            'sources': {name: file_identity(SOURCE / 'amplifier_web' / name) for name in (
                'server.py', 'service.py', 'runtime.py', 'runtime_worker.py', 'runtime_controls.py',
                'host/session.py', 'host/config.py', 'draft_defaults.py', 'draft_defaults_probe.py')},
            'static': {str(p.relative_to(static)): file_identity(p)['sha256']
                       for p in sorted(static.rglob('*')) if p.is_file()},
            'importsReady': True, 'startupGate': 'imports-and-assets-only',
            'externalAcquisitionAllowed': False}
        save(root / 'readiness.json', readiness)
        def proof():
            sessions = list(service.state.get('sessions', []))
            if len(sessions) > 16:
                raise ValueError('Fixture session bound exceeded')
            result = []
            for session in sessions:
                sid = session['id']
                native = session.get('runtimeSessionId') or session.get('nativeIdentity') or sid
                validate_id(native)
                controls = read_json(root / 'app/sessions' / native / 'control-state.json')
                result.append({'id': sid, 'nativeId': native, 'selection': session.get('selection'),
                    'status': session.get('status'), 'report': session.get('runtimeReport'),
                    'error': session.get('error'), 'failure': session.get('failure'),
                    'nextMessageRevision': session.get('nextMessageRevision'),
                    'appliedConfigurationRevision': session.get('appliedConfigurationRevision'),
                    'mountedReport': read_json(root / 'app/runtime-reports' / native / 'mounted.json'),
                    'savedSelection': controls.get('selection') if controls is not None else None,
                    'controlsPresent': controls is not None,
                    'catalog': service.state.get('runtimeControl', {}).get(sid, {})})
            setup = service.state.get('setup', {})
            discovery = {'revision': service.state.get('revision'),
                'configurationRevision': service.state.get('configurationRevision', 0),
                'hostInstanceId': service.instance_id,
                'setup': {key: copy.deepcopy(setup.get(key)) for key in (
                    'configurationTarget', 'operations', 'providers', 'providerCatalogs',
                    'providersWorkspace', 'providersRequestedWorkspace', 'providersLocation',
                    'providersLoadedAt', 'routingCatalogError')},
                'draftDefaults': copy.deepcopy(service.state.get('draftDefaults', {}))}
            return {'calls': calls_at(root / 'calls.jsonl'), 'sessions': result,
                'workers': [{**entry['identity'], 'kind': entry['kind'],
                    'returncode': entry['process'].returncode} for entry in processes],
                'workerReady': ready_events, 'providerControls': control_reads,
                'lifecycle': lifecycle, 'readiness': readiness, 'discovery': discovery,
                'routingDiscovery': routing_reads,
                'guardRefusals': [row for path in sorted(root.glob('guard-*.jsonl')) for row in calls_at(path)],
                'sharedSettings': {'initial': settings_identity, 'current': file_identity(settings)}}
        async def inspect(request):
            return web.json_response(proof(), headers={'Cache-Control': 'no-store'})
        app.router.add_get('/fixture/native-proof', inspect)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] = app['allowed_origins'] | {url}
        url_stream.write(url + '\n')
        await stopped.wait()
        save(root / 'final-proof.json', proof())
    except BaseException as exc:
        primary_failure = exc
        try:
            save(root / 'failure.json', {'type': type(exc).__name__, 'message': str(exc)})
        except OSError as capture:
            print(f'Failure receipt write failed: {capture}', file=sys.stderr)
        raise
    finally:
        cleanup_errors = []
        try:
            if runner is not None:
                await asyncio.wait_for(runner.cleanup(), 12)  # Production service.close -> runtime.close.
            elif app is not None:
                await asyncio.wait_for(app['service'].close(), 12)
        except BaseException as exc:
            cleanup_errors.append(f'runner cleanup: {type(exc).__name__}: {exc}')
        try:
            if runtime is not None:
                await asyncio.wait_for(runtime.close(), 8)
        except BaseException as exc:
            cleanup_errors.append(f'runtime close: {type(exc).__name__}: {exc}')
        # Only retained subprocess handles; fallback never kills a reused PID.
        fallback = []
        async def reap_owned(entry):
            process = entry['process']
            if process.returncode is None:
                from amplifier_web.probe_process import stop
                try:
                    current = process_identity(process.pid)
                except OSError as exc:
                    cleanup_errors.append(f'Identity unreadable: {process.pid}: {exc}')
                    return
                if current != entry['identity'] or current['processGroup'] != process.pid:
                    cleanup_errors.append(f'Identity changed: {process.pid}')
                    return
                fallback.append(process.pid)
                try:
                    await asyncio.wait_for(stop(process, grace=3), 10)
                except BaseException as exc:
                    cleanup_errors.append(f'reap {process.pid}: {type(exc).__name__}: {exc}')
        complete = False
        try:
            try:
                await asyncio.gather(*(reap_owned(entry) for entry in processes))
                if all(entry['process'].returncode is not None for entry in processes):
                    await asyncio.wait_for(asyncio.gather(*watchers), 3)
            except BaseException as exc:
                cleanup_errors.append(f'cleanup watcher: {type(exc).__name__}: {exc}')
            identities = [{**entry['identity'], 'kind': entry['kind'],
                           'returncode': entry['process'].returncode,
                           'guardReady': (root / f'guard-ready-{entry["identity"]["pid"]}.json').is_file(),
                           'procAbsent': not Path(f'/proc/{entry["identity"]["pid"]}').exists()}
                          for entry in processes]
            complete = not cleanup_errors and all(row['returncode'] is not None and row['procAbsent']
                                                  and row['guardReady'] and 'identityError' not in row
                                                  for row in identities)
            save(root / 'cleanup.json', {'complete': complete, 'errors': cleanup_errors,
                                        'fallbackPids': fallback, 'processes': identities})
        except BaseException as exc:
            complete = False
            cleanup_errors.append(f'cleanup receipt: {type(exc).__name__}: {exc}')
            print(json.dumps({'cleanupErrors': cleanup_errors}), file=sys.stderr)
        finally:
            asyncio.create_subprocess_exec = original_spawn
            if original_command is not None:
                RuntimeManager._command = original_command
            if original_setup_perform is not None:
                SetupManager.perform = original_setup_perform
            for signum in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(signum)
            try:
                url_stream.close()
            except OSError as exc:
                complete = False
                print(f'URL stream close failed: {exc}', file=sys.stderr)
        if (not complete or fallback) and primary_failure is None:
            raise RuntimeError('Native fixture cleanup did not pass the ordinary-close qualification gate')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: provider_native_ui_server.py <new-artifact-root>')
    asyncio.run(main(Path(sys.argv[1]).expanduser().resolve()))
