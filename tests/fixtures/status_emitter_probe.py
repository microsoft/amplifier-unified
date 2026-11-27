"""Actual installed CLI + Unified canonical runtime adapter, synthetic work only.

Run inside an owned DTU with CLI_PYTHON and CONSUMER_PYTHON pointing at separate
owned environments. CLI uses installed Core/Foundation/loop-live/context-simple;
only the provider and two local tools are scripted. No network/model calls.

--cli-sources names a reviewed JSON input (schemaVersion: 1, store: absolute
shared-store path, sources: [{url, ref, commit}]). Its explicit SHA-256 binds the
selection. Prepare complete public objects and installed module dependencies
normally, outside this probe. The probe only verifies objects and creates normal
Foundation ref bindings in its NEW private home; it never acquires source inputs.
--host-sources and --host-sources-sha256 separately bind schemaVersion: 1,
store: an absolute host source store, captureCommit: an exact public CI commit.
The capture hook and installed CI bundle must use that same selection.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]


DEFAULT_REPOSITORIES = {
    'amplifier-bundle-imagegen', 'amplifier-bundle-modes',
    'amplifier-bundle-skills', 'amplifier-bundle-routing-matrix',
    'amplifier-bundle-wayfinder', 'amplifier-module-hooks-approval',
}
DEFAULT_CONTEXT = {
    'imagegen': ['context/imagegen.md'],
    'modes': ['context/modes-instructions.md'],
    'skills': ['context/skills-instructions.md'],
    'routing-matrix': ['context/routing-instructions.md'],
    'wayfinder': ['context/wayfinder-voice.md', 'context/propose-and-ack.md'],
    'app-cli': ['context/cli-awareness.md'],
}
DEFAULT_MODULES = {
    'tool-image', 'tool-mode', 'hooks-mode', 'tool-skills',
    'hooks-routing', 'hooks-wayfinder', 'hooks-approval',
}
CAPTURE_REPOSITORY = 'https://github.com/microsoft/amplifier-bundle-context-intelligence'
CAPTURE_MODULE = 'amplifier_module_hook_context_intelligence'
CAPTURE_FILES = {
    CAPTURE_MODULE: '__init__.py',
    CAPTURE_MODULE+'.config_resolver': 'config_resolver.py',
    CAPTURE_MODULE+'.handlers': 'handlers/__init__.py',
    CAPTURE_MODULE+'.handlers.logging_handler': 'handlers/logging_handler.py',
    CAPTURE_MODULE+'.upload': 'upload.py',
    CAPTURE_MODULE+'.fanout': 'fanout.py',
}
WORKER_SOURCE_FILES = {
    '__main__': 'amplifier_web/runtime_worker.py',
    'runtime_bootstrap': 'amplifier_web/runtime_bootstrap.py',
    'runtime_protocol': 'amplifier_web/runtime_protocol.py',
    'amplifier_web': 'amplifier_web/__init__.py',
    'amplifier_operations': 'amplifier_operations/__init__.py',
    'amplifier_kernels': 'amplifier_kernels/__init__.py',
    'amplifier_scheduling.store': 'components/scheduling-delivery/amplifier_scheduling/store.py',
}
WORKER_MODULES = (*WORKER_SOURCE_FILES, 'amplifier_module_loop_live',
                  'amplifier_module_context_simple', 'amplifier_module_hooks_session_naming',
                  *CAPTURE_FILES)


def read_cli_sources(path, expected_sha256):
    data = Path(path).read_bytes()
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha256 or '') or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError('CLI source input digest differs')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Ambiguous CLI source input key: '+key)
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=unique)
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'store', 'sources'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1):
        raise ValueError('Expected explicit CLI source input schemaVersion 1')
    if not isinstance(value['store'], str):
        raise ValueError('CLI source store must be an existing canonical directory')
    store = Path(value['store'])
    if not store.is_absolute() or store != store.resolve(strict=True) or not store.is_dir():
        raise ValueError('CLI source store must be an existing canonical directory')
    sources = value['sources']
    if not isinstance(sources, list) or not sources:
        raise ValueError('CLI source bindings are missing')
    identities = set()
    for row in sources:
        if (not isinstance(row, dict) or set(row) != {'url', 'ref', 'commit'}
                or not isinstance(row['url'], str)
                or not re.fullmatch(r'https://github.com/microsoft/[A-Za-z0-9_-]+', row['url'])
                or not isinstance(row['ref'], str) or not re.fullmatch(r'[A-Za-z0-9_./-]+', row['ref'])
                or not isinstance(row['commit'], str) or not re.fullmatch(r'[a-f0-9]{40}', row['commit'])):
            raise ValueError('Invalid exact public CLI source binding')
        identity = (row['url'], row['ref'])
        if identity in identities:
            raise ValueError('Ambiguous CLI source binding')
        identities.add(identity)
    # Foundation's parser supplies main for an omitted Git ref (Approval).
    required = {('https://github.com/microsoft/'+name, 'main') for name in DEFAULT_REPOSITORIES}
    if not required <= identities:
        raise ValueError('CLI default source bindings are incomplete')
    return value


def read_host_sources(path, expected_sha256):
    with Path(path).open('rb') as stream:
        data = stream.read(65537)
    if (len(data) > 65536 or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256 or '')
            or hashlib.sha256(data).hexdigest() != expected_sha256):
        raise ValueError('Host source input size or digest differs')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Ambiguous host source input')
            value[key] = item
        return value
    value = json.loads(data, object_pairs_hook=unique)
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'store', 'captureCommit'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or not isinstance(value['captureCommit'], str)
            or not re.fullmatch(r'[a-f0-9]{40}', value['captureCommit'])
            or not isinstance(value['store'], str)):
        raise ValueError('Expected exact host source input schemaVersion 1')
    store = Path(value['store'])
    if not store.is_absolute() or store != store.resolve(strict=True) or not store.is_dir():
        raise ValueError('Host source store must be an existing canonical directory')
    return value


def separate_source_work(root, cli_inputs, host_inputs):
    paths = [root, Path(cli_inputs['store']), Path(host_inputs['store'])]
    if any(a.is_relative_to(b) or b.is_relative_to(a)
           for index, a in enumerate(paths) for b in paths[index+1:]):
        raise ValueError('Native work and prepared source stores must be separate')


def host_settings(excluded, host_inputs):
    return {'bundle': {'app': []}, 'web_bundles': {'excluded': list(excluded)},
            'sources': {'modules': {'hook-context-intelligence':
                'git+'+CAPTURE_REPOSITORY+'@'+host_inputs['captureCommit']
                +'#subdirectory=modules/hook-context-intelligence'}}}


async def prepare_host_sources(root, inputs):
    """Use the installed host Foundation, not the independent CLI's Foundation."""
    from importlib.metadata import distribution
    from amplifier_foundation.sources.shared import SharedSourceStore
    from amplifier_web.host.config import read_config
    config = read_config(root/'workspace', home=root/'app', shared_home=root/'shared')
    expected = host_settings([], inputs)['sources']['modules']['hook-context-intelligence']
    assert config.module_sources.get('hook-context-intelligence') == expected
    # A hook source override does not rewrite installed transitive dependencies.
    # Require both normally installed distributions to name the same public pin.
    for name, subdirectory in (
            ('amplifier-bundle-context-intelligence', None),
            ('amplifier-module-hook-context-intelligence', 'modules/hook-context-intelligence')):
        direct = json.loads(distribution(name).read_text('direct_url.json') or 'null')
        assert isinstance(direct, dict) and direct.get('url') == CAPTURE_REPOSITORY
        assert direct.get('vcs_info', {}).get('vcs') == 'git'
        assert direct.get('vcs_info', {}).get('commit_id') == inputs['captureCommit']
        assert direct.get('subdirectory') == subdirectory
        assert direct.get('dir_info', {}).get('editable') is not True
    store = SharedSourceStore(inputs['store'])
    # verify() checks the full already-prepared repository. Never ensure/acquire
    # an absent object here. bind() receives the exact commit as both ref and pin.
    source = store.verify(CAPTURE_REPOSITORY, inputs['captureCommit'])
    await store.bind(config.registry_home/'cache', CAPTURE_REPOSITORY,
                     inputs['captureCommit'], inputs['captureCommit'])
    return source


async def observe_worker(runtime, sid, root, capture_source, proof):
    """Untimed, bounded, listed-module evidence; never an exhaustive import trace."""
    from amplifier_web.shared_state import configuration_path_stamp, configuration_path_digest
    from importlib.metadata import distribution, distributions
    row = runtime.workers.get(sid)
    assert row and row['process'].returncode is None, 'No existing worker'
    pid, started = row['process'].pid, row['started_at']
    proof.update(passed=False, pid=pid, startedMonotonic=started,
                 importedBytesVerified=False, exhaustive=False, modules=[])
    path = Path(row.get('sessionSettingsPath', '')).resolve()
    matches = [entry for entry in row.get('configurationStamp', [])
               if isinstance(entry, list) and len(entry) == 2 and entry[0] == str(path)]
    proof['readySettingsContained'] = path.is_relative_to(root)
    proof['readyStampMatches'] = len(matches) == 1 and matches[0][1] == configuration_path_stamp(path)
    assert proof['readySettingsContained'] and proof['readyStampMatches'], 'READY settings identity differs'
    proof['readySettingsSha256'] = configuration_path_digest(path)
    proof['readySettingsPresent'] = matches[0][1] is not None
    observed = await runtime.provenance(sid, modules=WORKER_MODULES)
    proof['identityStable'] = observed.get('identityStable') is True
    proof['sameWorker'] = (runtime.workers.get(sid) is row and row['process'].returncode is None
                           and observed.get('workerInstance') == f'{pid}:{started}')
    proof['sameSession'] = observed.get('runtimeSessionId') == row.get('runtime_id')
    assert (observed.get('status') == 'observed' and observed.get('mounted') is True
            and proof['identityStable'] and proof['sameWorker'] and proof['sameSession']), 'Worker provenance incomplete'
    modules = observed.get('modules')
    assert isinstance(modules, list) and len(modules) <= 64
    names = [item['module'] for item in modules]
    required = {'__main__', 'amplifier_web', 'amplifier_core', 'amplifier_foundation', CAPTURE_MODULE}
    assert len(set(names)) == len(names) and (set(WORKER_MODULES) | required) <= set(names)
    capture_package = capture_source/'modules/hook-context-intelligence'/CAPTURE_MODULE
    installed_capture = Path(distribution('amplifier-module-hook-context-intelligence').locate_file(CAPTURE_MODULE)).resolve()
    authored = {root/'provider/amplifier_module_provider_statusfixture/__init__.py',
                root/'tools/amplifier_module_tool_statusfixture/__init__.py'}
    # Join listed installed origins to normal RECORD hashes, not merely a path
    # beneath a trusted prefix. This remains current-file, not imported-byte proof.
    installed = {}
    for dist in distributions():
        for file in dist.files or ():
            if file.hash and file.hash.mode == 'sha256':
                installed.setdefault(Path(dist.locate_file(file)).resolve(), []).append(file.hash.value)
    total = 0
    for item in modules:
        name = item['module']
        assert isinstance(name, str) and len(name) <= 200 and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', name)
        safe = {'module': name, 'loaded': item.get('loaded') is True}
        proof['modules'].append(safe)
        if not safe['loaded']:
            assert name not in required, 'Required worker module absent'
            continue
        assert item.get('fileStatus') == 'observed' and item.get('importedBytesVerified') is False
        origin = Path(item['origin']).resolve(strict=True)
        owner_record = None
        if name in CAPTURE_FILES:
            expected = capture_package/CAPTURE_FILES[name]
            assert origin in {expected.resolve(strict=True), installed_capture/CAPTURE_FILES[name]}, 'Capture origin differs'
            kind = 'capture'
        elif name in WORKER_SOURCE_FILES:
            expected = ROOT/WORKER_SOURCE_FILES[name]
            kind = 'current-source'
            if name == 'amplifier_scheduling.store' and origin != expected.resolve(strict=True):
                # Worker bootstrap exposes only Web/Operations/Kernels from
                # source. Scheduling can come from the normal Unified wheel.
                dist = distribution('amplifier-unified')
                direct = json.loads(dist.read_text('direct_url.json') or '{}')
                assert direct.get('dir_info', {}).get('editable') is not True
                relative = 'amplifier_scheduling/store.py'
                assert origin == Path(dist.locate_file(relative)).resolve(strict=True), 'Selected package origin differs'
                entries = [file for file in dist.files or () if str(file) == relative]
                assert len(entries) == 1 and entries[0].hash and entries[0].hash.mode == 'sha256'
                owner_record = entries[0].hash.value
                kind = 'installed-selected-source'
            else:
                assert origin == expected.resolve(strict=True), 'Current source origin differs'
        elif origin in authored:
            expected, kind = origin, 'scripted-provider-or-tool'
        elif origin.is_relative_to(ROOT/'amplifier_web'):
            expected, kind = origin, 'current-source'
        else:
            assert origin.is_relative_to(Path(sys.prefix).resolve()), 'Undeclared worker origin'
            expected, kind = origin, 'installed-host'
        with expected.open('rb') as stream:
            data = stream.read(2*1024*1024+1)
        total += len(data)
        assert len(data) <= 2*1024*1024 and total <= 16*1024*1024
        digest = hashlib.sha256(data).hexdigest()
        if owner_record is not None or kind == 'installed-host' or origin.is_relative_to(installed_capture):
            record = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
            if owner_record is not None:
                assert owner_record == record, 'Selected package RECORD differs'
            assert installed.get(origin) and all(value == record for value in installed[origin]), 'Installed RECORD differs'
        safe.update(originClass=kind, currentFileSha256=digest, bytes=len(data),
                    originPathSha256=hashlib.sha256(str(origin).encode()).hexdigest())
        assert item.get('currentFileSha256') == digest and item.get('bytes') == len(data), 'Worker source bytes differ'
    proof['passed'] = True


def cli_environment(root, store):
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'SSL_CERT_FILE') if key in os.environ}
    env.update(HOME=str(root/'cli-base-home'), AMPLIFIER_HOME=str(root/'cli-home'),
               AMPLIFIER_STATUS_DIR=str(root/'status'),
               AMPLIFIER_SESSION_STATE_HOME=str(root/'cli-ownership'),
               AMPLIFIER_SOURCE_STORE=store, PYTHONDONTWRITEBYTECODE='1',
               UV_OFFLINE='true', UV_CACHE_DIR=str(root/'cli-uv-cache'),
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='protocol.allow',
               GIT_CONFIG_VALUE_0='never', GIT_TERMINAL_PROMPT='0')
    return env


async def bind_cli_sources(store, sources, cache):
    # Verify ALL objects before bind(), whose missing-object path may acquire.
    objects = {(row['url'], row['ref']): store.verify(row['url'], row['commit']) for row in sources}
    for row in sources:
        await store.bind(cache, row['url'], row['ref'], row['commit'])
    return objects


async def run_cli_preparation(command, *, cwd, env, log, timeout=60):
    process = await asyncio.create_subprocess_exec(
        *command, cwd=cwd, env=env, stdout=log, stderr=log)
    try:
        return await asyncio.wait_for(process.wait(), timeout)
    finally:
        # Keep descendants in the native runner's group. A detached helper
        # would escape its outer cancellation. The qualifier/owned native-only
        # runner settles the whole group, also on a failed native exit.
        if process.returncode is None:
            process.kill()
        await process.wait()


def local_cli_source(parsed, base_path, contained):
    """Preserve the file handler's container root, not the admission boundary."""
    path = Path(unquote(parsed.path)).expanduser()
    path = path if path.is_absolute() else base_path/path
    origin, _ = contained(path)
    active, _ = contained(origin/parsed.subpath if parsed.subpath else origin)
    return active, origin if origin.is_dir() else origin.parent


async def prepare_cli(root, inputs, digest):
    """Installed CLI process: bind/inspect sources, never create a session."""
    expected_env = cli_environment(root, inputs['store'])
    if any(os.environ.get(key) != value for key, value in expected_env.items()):
        raise ValueError('CLI preparation requires the isolated fixture environment')
    if Path.cwd() != root/'workspace' or (root/'cli-home/cache').exists():
        raise ValueError('CLI preparation requires a fresh fixture home and workspace')
    if json.loads((root/'cli-home/settings.yaml').read_text()) != {'bundle': {'app': []}}:
        raise ValueError('CLI fixture settings changed before preparation')
    import amplifier_app_cli
    import amplifier_status
    from amplifier_app_cli.lib.settings import AppSettings
    from amplifier_app_cli.runtime.config import resolve_bundle_config
    from amplifier_foundation.modules.install_policy import dependency_installation_policy
    from amplifier_foundation.paths.resolution import ResolvedSource, parse_uri
    from amplifier_foundation.sources.policy import SourceResolutionDenied, source_resolution_policy
    from amplifier_foundation.sources.shared import SharedSourceStore

    store = SharedSourceStore(inputs['store'])
    cache = root/'cli-home/cache'
    objects = await bind_cli_sources(store, inputs['sources'], cache)
    cli_package = Path(amplifier_app_cli.__file__).resolve().parent
    status_package = Path(amplifier_status.__file__).resolve().parent
    local_roots = [root, status_package, cli_package]
    allowed = [*objects.values(), *local_roots]
    denied, warnings, seen = [], [], set()

    def contained(path):
        path = Path(path).resolve(strict=True)
        matches = [base for base in allowed if path == base or path.is_relative_to(base)]
        if not matches:
            raise ValueError('CLI resource is outside prepared inputs: '+str(path))
        return path, max(matches, key=lambda base: len(base.parts))

    def admit(uri, *, base_path):
        try:
            parsed = parse_uri(uri)
            if parsed.is_git:
                if not uri.startswith('git+https://'):
                    raise ValueError('Unsupported public CLI transport: '+uri)
                key = ('https://'+parsed.host+parsed.path, parsed.ref or 'HEAD')
                if key not in objects:
                    raise ValueError('Unbound CLI source: '+uri)
                origin = store.cached(uri, cache)
                if origin != objects[key]:
                    raise ValueError('CLI source binding changed: '+uri)
                active, _ = contained(origin/parsed.subpath if parsed.subpath else origin)
                if not active.is_relative_to(origin):
                    raise ValueError('CLI source subpath escapes its object')
            elif parsed.is_file:
                active, origin = local_cli_source(parsed, base_path, contained)
            else:
                raise ValueError('Unsupported prepared CLI source: '+uri)
            seen.add(uri)
            return ResolvedSource(active_path=active, source_root=origin)
        except Exception as error:
            denied.append(str(error))
            raise SourceResolutionDenied(str(error)) from error

    class Capture(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                warnings.append(record.getMessage())

    capture = Capture()
    loggers = [logging.getLogger(name) for name in ('amplifier_app_cli', 'amplifier_foundation')]
    for logger in loggers:
        logger.addHandler(capture)
    try:
        with source_resolution_policy(admit), dependency_installation_policy(False):
            config, prepared = await resolve_bundle_config((root/'bundle.yaml').as_uri(), AppSettings())
    finally:
        for logger in loggers:
            logger.removeHandler(capture)
    if denied or warnings:
        raise ValueError('Incomplete CLI preparation: '+repr(denied+warnings))
    modules = {item['module'] for section in ('tools', 'hooks') for item in config.get(section, [])}
    if not DEFAULT_MODULES <= modules:
        raise ValueError('CLI defaults were not completely composed')
    namespaces = prepared.bundle.source_base_paths
    if not DEFAULT_CONTEXT.keys() <= namespaces.keys():
        raise ValueError('CLI default resource namespaces are incomplete')
    prepared.bundle.resolve_pending_context()
    context_paths = {Path(path).resolve(strict=True) for path in prepared.bundle.context.values()}
    expected_context = {contained(namespaces[name]/relative)[0]
                        for name, relatives in DEFAULT_CONTEXT.items() for relative in relatives}
    if not expected_context <= context_paths:
        raise ValueError('CLI default context was omitted')
    for path in prepared.bundle.context.values():
        active, _ = contained(path)
        if not active.is_file():
            raise ValueError('CLI context file is missing')
    for name in prepared.bundle.agents:
        path = prepared.bundle.resolve_agent_path(name)
        if path is None or not contained(path)[0].is_file():
            raise ValueError('CLI agent resource is missing: '+name)
    skills = next(item for item in config['tools'] if item['module'] == 'tool-skills')['config']['skills']
    curated = 'git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills'
    if not {curated, '@imagegen:skills', '@routing-matrix:skills', '@wayfinder:skills',
            str(cli_package/'data/skills')} <= set(skills):
        raise ValueError('CLI default skills were omitted')
    if not (cli_package/'data/skills').is_dir():
        raise ValueError('CLI packaged skills are missing')
    for source in skills:
        if source.startswith('git+'):
            parsed = parse_uri(source)
            origin = store.cached(source, cache/'skills')
            if origin != objects.get(('https://'+parsed.host+parsed.path, parsed.ref or 'HEAD')):
                raise ValueError('Curated skills binding is unavailable')
            if not contained(origin/parsed.subpath)[0].is_dir():
                raise ValueError('Curated skills directory is missing')
        elif source.startswith('@'):
            namespace, relative = source[1:].split(':', 1)
            if namespace not in namespaces or not contained(namespaces[namespace]/relative)[0].is_dir():
                raise ValueError('Namespaced skills directory is missing')
    # Wayfinder reads content beside its source module, not from wheel metadata.
    if not contained(namespaces['wayfinder']/'content')[0].is_dir():
        raise ValueError('Wayfinder content is missing')
    rows = [{'module': module, 'source': hint, 'path': str(path)}
            for module, hint, path in prepared.resolver.prepared_sources()]
    for row in rows:
        contained(row['path'])
    proof = {'inputSha256': digest, 'sources': inputs['sources'], 'sourceRequests': sorted(seen),
             'modules': rows, 'namespaces': {key: str(value) for key, value in namespaces.items()},
             'contextFiles': sorted(map(str, context_paths)), 'skills': skills,
             'warnings': warnings, 'denied': denied, 'sessionsStarted': 0}
    (root/'cli-prepared.json').write_text(json.dumps(proof, indent=2)+'\n')


def cli_tool_sample(rows, pid, launched_at, received_at, received_monotonic, first=None):
    """Freeze the first fresh observation, never the most favorable publication."""
    if first is not None:
        return first
    matches = []
    for row in rows:
        producer, now = row['producer'], row['now']
        if (producer['host'] != 'cli' or type(producer['pid']) is not int or producer['pid'] != pid
                or row['lifecycle'] != 'live' or row['freshness'] != 'live'
                or now['status'] != 'tool'):
            continue
        tools = [tool for tool in now['tools']
                 if tool['name'] == 'status_hold' and tool['id'] == 'hold-call']
        if len(tools) != 1:
            continue
        stamp, started = row['observedAt'], tools[0]['startedAt']
        if (not all(type(value) in (int, float) and math.isfinite(value)
                    for value in (stamp, started))
                or not launched_at <= started <= stamp <= received_at):
            continue
        assert type(row['sequence']) is int and row['sequence'] > 0
        identity = [producer['instanceId'], row['where']['sessionId'], tools[0]['id']]
        assert all(isinstance(value, str) and 0 < len(value) <= 200 for value in identity)
        matches.append({
            'pid': pid, 'sequence': row['sequence'], 'publishedAt': stamp,
            'toolStartedAt': started, 'receivedAt': received_at,
            'receivedMonotonic': received_monotonic, 'launchedAt': launched_at,
            'identitySha256': hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
        })
    assert len(matches) <= 1, 'Ambiguous CLI tool observation'
    return matches[0] if matches else None


def check_cli_tool_latency(timing):
    first, entered = timing['firstCliTool'], timing['toolEntryAt']
    assert first is not None, 'No first CLI tool observation'
    assert type(entered) in (int, float) and math.isfinite(entered), 'Missing tool entry time'
    assert entered >= first['launchedAt'], 'Tool entry predates this CLI'
    # Receiver time includes publication and polling delay. A late first read
    # must fail even if its publisher timestamp happens to be early.
    lag = first['receivedAt'] - entered
    timing['cliToolObservationSeconds'] = lag
    assert 0 <= lag < 1, lag
    return lag


def record_cli_tool_entry(root, cli, timing):
    """Read the non-atomic marker only after its owning process has exited."""
    assert cli.returncode is not None, 'CLI tool entry writer is still running'
    if timing['firstCliTool'] is not None:
        entered = json.loads((root/('tool-started-'+str(cli.pid)+'.json')).read_text())['at']
        assert type(entered) in (int, float) and math.isfinite(entered)
        timing['toolEntryAt'] = entered


async def main(args):
    inputs = read_cli_sources(args.cli_sources, args.cli_sources_sha256)
    host_inputs = read_host_sources(args.host_sources, args.host_sources_sha256)
    root = args.root.resolve()
    separate_source_work(root, inputs, host_inputs)
    root.mkdir(parents=True)
    # This receipt survives handled failures without copying exception text,
    # consumer rows, prompts or private logs. Forced outer termination can still
    # prevent finalization; an absent receipt is never a pass.
    result = {'passed': False, 'phase': 'setup', 'cleanupCompleted': False,
              'timing': {'firstCliTool': None, 'finalCliTool': None,
                         'toolEntryAt': None, 'phases': {}}}
    try:
        await run_probe(args, root, inputs, host_inputs, result)
    except BaseException as error:
        result['passed'] = False
        # A failure in cleanup must not turn the success-only payload into
        # diagnostic output. The fixed timing/phase facts suffice on failure.
        result.pop('consumer', None)
        result.pop('cliPreparation', None)
        result['failureKind'] = ('assertion' if isinstance(error, AssertionError) else
                                 'error' if isinstance(error, Exception) else 'interrupted')
        raise
    finally:
        (root/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')


async def run_probe(args, root, inputs, host_inputs, result):
    timing = result['timing']
    def phase(name):
        result['phase'] = name
        timing['phases'][name] = time.monotonic()

    workspace = root/'workspace'; workspace.mkdir()
    env = cli_environment(root, inputs['store'])
    Path(env['AMPLIFIER_HOME']).mkdir()
    Path(env['AMPLIFIER_HOME'], 'settings.yaml').write_text(json.dumps({'bundle': {'app': []}}))
    sys.path.insert(0, str(ROOT))
    registry = root/'status'
    os.environ.update(AMPLIFIER_STATUS_DIR=str(registry), AMPLIFIER_HOME=str(root/'shared'),
                      AMPLIFIER_WEB_HOME=str(root/'app'), AMPLIFIER_SESSION_STATE_HOME=str(root/'ownership'),
                      AMPLIFIER_SOURCE_STORE=host_inputs['store'], PYTHONDONTWRITEBYTECODE='1',
                      UV_OFFLINE='true', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                      GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='protocol.allow',
                      GIT_CONFIG_VALUE_0='never', GIT_TERMINAL_PROMPT='0')
    sys.dont_write_bytecode = True
    settings = root/'shared/settings.yaml'; settings.parent.mkdir()
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    settings.write_text(json.dumps(host_settings(DEFAULT_BEHAVIORS, host_inputs)))
    phase('host_sources')
    capture_source = await prepare_host_sources(root, host_inputs)
    result['hostSources'] = {'inputSha256': args.host_sources_sha256,
                             'captureCommit': host_inputs['captureCommit']}
    provider = root/'provider/amplifier_module_provider_statusfixture'; provider.mkdir(parents=True)
    provider.joinpath('__init__.py').write_text('''from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall, Usage
__amplifier_module_type__ = 'provider'
class Provider:
    name = 'statusfixture'
    def __init__(self): self.calls = 0
    def get_info(self): return ProviderInfo(id=self.name,display_name='Status fixture',capabilities=['tools'],defaults={'model':'offline'})
    async def list_models(self): return []
    def parse_tool_calls(self,response): return response.tool_calls or []
    async def complete(self,request,**kwargs):
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(content=[],tool_calls=[ToolCall(id='plan-call',name='todo',arguments={'action':'update','todos':[{'content':'PRIVATE plan label','status':'completed'},{'content':'PRIVATE current step','status':'in_progress'}]})],usage=Usage(input_tokens=1,output_tokens=1,total_tokens=2))
        if self.calls == 2:
            return ChatResponse(content=[],tool_calls=[ToolCall(id='hold-call',name='status_hold',arguments={})],usage=Usage(input_tokens=1,output_tokens=1,total_tokens=2))
        return ChatResponse(content=[TextBlock(text='Fixture complete')],usage=Usage(input_tokens=1,output_tokens=1,total_tokens=2))
async def mount(coordinator,config=None):
    await coordinator.mount('providers',Provider(),name='statusfixture')
''')
    tools = root/'tools/amplifier_module_tool_statusfixture'; tools.mkdir(parents=True)
    tools.joinpath('__init__.py').write_text('''import asyncio, json, time, os
from pathlib import Path
from amplifier_core import ToolResult
__amplifier_module_type__ = 'tool'
class Tool:
    description = 'Local status acceptance fixture'
    input_schema = {'type':'object','additionalProperties':True}
    def __init__(self,name): self.name=name
    async def execute(self,arguments):
        if self.name == 'status_hold':
            (Path('''+repr(str(root))+''')/('tool-started-'+str(os.getpid())+'.json')).write_text(json.dumps({'at':time.time()}))
            await asyncio.sleep(6)
            return ToolResult(success=True,output={'done':True})
        return ToolResult(success=True,output={'status':'updated','count':2})
async def mount(coordinator,config=None):
    for name in ['todo','status_hold']:
        await coordinator.mount('tools',Tool(name),name=name)
''')
    installed = await asyncio.create_subprocess_exec(os.environ['CLI_PYTHON'], '-I', '-c',
        'import amplifier_status, pathlib; print(pathlib.Path(amplifier_status.__file__).parent)',
        stdout=asyncio.subprocess.PIPE)
    installed_path, _ = await installed.communicate()
    assert installed.returncode == 0
    bundle = root/'bundle.yaml'
    bundle.write_text(json.dumps({'bundle': {'name': 'status-proof'},
        'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
        'providers': [{'module': 'provider-statusfixture', 'source': str(provider.parent)}],
        'tools': [{'module': 'tool-statusfixture', 'source': str(tools.parent)}],
        'hooks': [{'module': 'hooks-status-emitter', 'source': installed_path.decode().strip(), 'config': {'directory': str(registry), 'bundle': 'status-proof'}}]}))
    # No host/CLI session exists until exact full-default source preparation
    # succeeds in the independent CLI interpreter. No Unified sys.path there.
    phase('cli_preparation')
    with (root/'cli-prepare.log').open('wb') as log:
        code = await run_cli_preparation([
            os.environ['CLI_PYTHON'], '-I', '-B', str(Path(__file__).resolve()),
            str(root), '--prepare-cli', '--cli-sources', str(args.cli_sources.resolve()),
            '--cli-sources-sha256', args.cli_sources_sha256],
            cwd=workspace, env=env, log=log)
        if code:
            raise ValueError('CLI prepared inputs refused; inspect '+str(root/'cli-prepare.log'))
    from amplifier_web.service import AppService
    from amplifier_web.status_protocol import discover
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.management import Management
    runtime = RuntimeManager(command=[sys.executable, str(ROOT/'amplifier_web/runtime_worker.py')], startup_timeout=60)
    app = AppService(root/'app', runtime=runtime, workspace=workspace)
    app.management = Management(app)
    runtime.app_bridge = app.app_bridge
    runtime.home = root/'app'
    host_bundle = root/'host-bundle.yaml'
    host_plan = json.loads(bundle.read_text()); host_plan['hooks'] = []
    host_bundle.write_text(json.dumps(host_plan))
    session = app._new_session({'title': 'Private status acceptance', 'bundle': str(host_bundle), 'selection': {'instance':'statusfixture','model':'offline'}})
    app.state['sessions'].append(session)
    app.projections.invalidate(state=app._state)
    sid = session['id']
    cli = None
    observed = None
    try:
        phase('runtime_start')
        await app.management.ensure_runtime(session)
        phase('worker_provenance')
        result['workerProvenance'] = {}
        await observe_worker(runtime, sid, root, capture_source, result['workerProvenance'])
        # Public CLI entry point, installed in an environment without Unified.
        phase('cli_registration')
        registration = await asyncio.create_subprocess_exec(os.environ['CLI_PYTHON'], '-m', 'amplifier_app_cli.main', 'bundle', 'add', bundle.as_uri(), '--name', 'status-proof', cwd=workspace, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        output, errors = await asyncio.wait_for(registration.communicate(), 30)
        assert registration.returncode == 0, (output+errors).decode()
        command = [os.environ['CLI_PYTHON'], '-m', 'amplifier_app_cli.main', 'run', '--bundle', 'status-proof', '--mode', 'single', '--output-format', 'json', 'PRIVATE prompt never emitted']
        with (root/'cli.log').open('wb') as log:
            phase('cli_launch')
            launched_at = time.time()
            cli = await asyncio.create_subprocess_exec(*command, cwd=workspace, env=env, stdout=log, stderr=log)
            timing['cliPid'] = cli.pid
            timing['cliLaunchedAt'] = launched_at
            deadline = time.monotonic()+60
            host_started = False
            phase('first_cli_tool')
            while time.monotonic() < deadline:
                if cli.returncode is not None:
                    break
                rows = discover(registry)['sessions']
                received_at, received_monotonic = time.time(), time.monotonic()
                timing['firstCliTool'] = cli_tool_sample(
                    rows, cli.pid, launched_at, received_at, received_monotonic,
                    timing['firstCliTool'])
                if timing['firstCliTool'] is not None and not host_started:
                    phase('host_dispatch')
                    await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'PRIVATE host prompt'})
                    host_started = True
                    phase('dual_tool')
                running = cli_tool_sample(rows, cli.pid, launched_at, received_at, received_monotonic)
                host_running = any(row['producer']['host']=='unified' and any(t['name']=='status_hold' for t in row['now']['tools']) for row in rows)
                if running and host_running:
                    # External installed consumer imports no source or host code.
                    phase('consumer_read')
                    process = await asyncio.create_subprocess_exec(os.environ['CONSUMER_PYTHON'], '-I', '-m', 'amplifier_status.cli', '--directory', str(registry), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
                    stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
                    assert process.returncode == 0, stderr.decode()
                    observed = json.loads(stdout)
                    timing['finalCliTool'] = cli_tool_sample(
                        observed['sessions'], cli.pid, launched_at, time.time(), time.monotonic())
                    break
                await asyncio.sleep(.03)
            phase('cli_exit')
            await asyncio.wait_for(cli.wait(), 60)
        record_cli_tool_entry(root, cli, timing)
        phase('consumer_assertions')
        assert cli.returncode == 0, (root/'cli.log').read_text()[-6000:]
        assert observed is not None, (root/'cli.log').read_text()[-6000:]
        assert observed['complete'] and not observed['invalid'], observed
        by_host = {row['producer']['host']: row for row in observed['sessions']}
        assert set(by_host) == {'cli', 'unified'}, observed
        assert len(observed['sessions']) == 2
        assert timing['finalCliTool'] is not None
        assert timing['finalCliTool']['identitySha256'] == timing['firstCliTool']['identitySha256']
        assert timing['finalCliTool']['toolStartedAt'] == timing['firstCliTool']['toolStartedAt']
        assert timing['finalCliTool']['sequence'] >= timing['firstCliTool']['sequence']
        assert by_host['unified']['where']['sessionId'] == sid
        assert any(tool['name'] == 'status_hold' and tool['id'] == 'hold-call'
                   for tool in by_host['unified']['now']['tools'])
        assert by_host['cli']['plan']['counts'] == {'pending': 0, 'in_progress': 1, 'completed': 1}
        assert by_host['unified']['plan']['counts']['in_progress'] == 1
        assert by_host['cli']['now']['status'] == by_host['unified']['now']['status'] == 'tool'
        phase('first_observation_assertion')
        lag = check_cli_tool_latency(timing)
        phase('privacy_assertion')
        assert 'PRIVATE' not in json.dumps(observed)
        phase('ended_assertion')
        ended = [r for r in discover(registry)['sessions'] if r['producer']['host'] == 'cli']
        assert len(ended) == 1 and ended[0]['freshness'] == 'ended', ended
        result.update({'passed': True, 'cliToolObservationSeconds': lag, 'consumer': observed,
                  'cliEnded': True, 'networkModelCalls': 0,
                  'cliPreparation': json.loads((root/'cli-prepared.json').read_text()),
                  'limits': ['CLI is real installed entry point with scripted provider/local tools.',
                             'Unified uses a real worker/Core/Foundation with the same scripted provider and local tools.',
                             'First observation includes polling delay; publisher observedAt can advance on later updates.',
                             'No runtime/TUI/recipe-specific adapter qualification.']})
        phase('assertions_complete')
    finally:
        if cli and cli.returncode is None:
            cli.terminate()
            await asyncio.wait_for(cli.wait(), 5)
        await app.close()
        result['cleanupCompleted'] = True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--cli-sources', type=Path, required=True)
    parser.add_argument('--cli-sources-sha256', required=True)
    parser.add_argument('--host-sources', type=Path)
    parser.add_argument('--host-sources-sha256')
    parser.add_argument('--prepare-cli', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.prepare_cli:
        asyncio.run(prepare_cli(args.root, read_cli_sources(args.cli_sources, args.cli_sources_sha256),
                                args.cli_sources_sha256))
    else:
        if args.host_sources is None or args.host_sources_sha256 is None:
            parser.error('native execution requires --host-sources and --host-sources-sha256')
        asyncio.run(main(args))
