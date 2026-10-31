"""Issue #1279: real omitted-ref qualification, promotion and worker adoption.

Authoring this fixture is not qualification. Run once in an isolated Linux/Python
3.13 CI job. Supply --runtime-manifest, its exact --runtime-lock, and the
dedicated populated --uv-cache from preparation, all under --root's parent.
Keep canonical Git/registry source identities, including wheel-only registry
Core; only loop-live's repository-lock pin and Linux/Python bounds may differ.

Only runtime_environment.manifest_path is redirected. UpdateManager.install,
validate, update_probe.py, Foundation preparation, freeze, activate and
RuntimeManager.start/_command are production implementations. Subprocess
observers return the original bytes and process objects. The Git wrapper changes
transport configuration only for `remote get-url origin`, never its output.

The provider and lifecycle hook are synthetic. The context delegates to the real
installed context-simple. No model input is sent. Every generation installs its
own .venv; there is no --system-site-packages, interpreter substitution, success
probe stub, manual validation marker or active-pointer write.

--root and --output must be fresh, separate directories. Export only --output,
never --root or the dependency supply/cache. Raw pipes are hashed and counted,
not exported; probe records use the production sanitizer, and runtime.ready is
reduced to fixed identity/capability fields. Failures contain no exception text,
source lines or arbitrary diagnostic payloads.

The caller must launch this process in its OS network sandbox (unshare --net).
The fixture checks a separate Linux network namespace with only loopback before
clearing UV_OFFLINE: uv otherwise refuses the uncached synthetic Git source even
though Git redirects it to a local fixture. GIT_ALLOW_PROTOCOL=file stays set;
all other dependencies must resolve from the declared cache. No network retry,
fallback supply, host-site-packages inheritance or source-policy bypass is used.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tomllib
from urllib.parse import parse_qs, urlsplit
import uuid

CHECKOUT = Path(__file__).resolve().parents[2]
URL = "https://example.invalid/approval"
URI = "git+" + URL  # Intentionally no @ref; the production resolver chooses main.
PROFILE = "omitted-ref-proof"
PACKAGES = {
    "amplifier-module-context-omittedfixture": ("context-omittedfixture", "context"),
    "amplifier-module-provider-omittedfixture": ("provider-omittedfixture", "provider"),
    "amplifier-module-hooks-omittedfixture": ("hooks-omittedfixture", "hooks"),
}
RECEIPTS = (
    "validated.json", "profiles.json", "profiles-qualified.json", "plan.json",
    "runtime.toml", "runtime-base.toml", "runtime.lock", "runtime-project.json",
    "runtime-installed.json", "runtime-sources.json", "runtime-install-policy.json",
    "runtime-install-overrides.txt", "selected-source-declarations.json",
)

# PEP 517 backend: stdlib only, complete RECORD, deterministic wheel payloads.
# build_editable deliberately produces a regular wheel, as in checked-cache tests;
# PEP 610 still identifies the actual source supplied to the installer.
BACKEND = r'''from pathlib import Path
import base64, csv, hashlib, io, subprocess, tomllib, zipfile

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    info = tomllib.loads(Path('pyproject.toml').read_text())['project']
    name, version = info['name'].replace('-', '_'), info['version']
    dist = name + '-' + version
    filename = dist + '-py3-none-any.whl'
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    with zipfile.ZipFile(Path(wheel_directory) / filename, 'w') as wheel:
        for path in sorted(Path(name).rglob('*.py')):
            wheel.writestr(path.as_posix(), path.read_bytes())
        wheel.writestr(name + '/_built.py', 'GIT_REVISION = ' + repr(revision) + '\n')
        wheel.writestr(dist + '.dist-info/METADATA',
            'Metadata-Version: 2.3\nName: ' + info['name'] + '\nVersion: ' + version + '\n')
        wheel.writestr(dist + '.dist-info/WHEEL',
            'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        module = info['name'].removeprefix('amplifier-module-')
        wheel.writestr(dist + '.dist-info/entry_points.txt',
            '[amplifier.modules]\n' + module + ' = ' + name + ':mount\n')
        record = io.StringIO(newline='')
        writer = csv.writer(record, lineterminator='\n')
        for member in wheel.namelist():
            payload = wheel.read(member)
            digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b'=').decode()
            writer.writerow((member, 'sha256=' + digest, len(payload)))
        writer.writerow((dist + '.dist-info/RECORD', '', ''))
        wheel.writestr(dist + '.dist-info/RECORD', record.getvalue())
    return filename
build_editable = build_wheel
'''

PROOF_MODULE = r'''import hashlib, json, os, subprocess, sys
from pathlib import Path
from ._payload import PAYLOAD

def mark(event, origin):
    path = Path(origin).resolve()
    source_root = next((p for p in path.parents if (p / '.git').exists()), None)
    if source_root is not None:
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source_root, text=True).strip()
    else:
        from ._built import GIT_REVISION
        revision = GIT_REVISION
    home = Path(os.environ['AMPLIFIER_WEB_HOME'])
    generation = os.environ.get('AMPLIFIER_UNIFIED_RELEASE') or (
        home.name if home.parent.name == 'releases' else None)
    row = {'event': event, 'module': __package__, 'payload': PAYLOAD,
        'revision': revision, 'payloadSha256': hashlib.sha256(path.with_name('_payload.py').read_bytes()).hexdigest(),
        'pid': os.getpid(), 'prefix': sys.prefix, 'executable': sys.executable,
        'processStartTicks': Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19],
        'origin': str(path), 'generation': generation,
        'kind': 'probe' if Path(sys.argv[0]).name == 'update_probe.py' else 'worker'}
    fd = os.open(os.environ['OMITTED_REF_MOUNTS'], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        data = (json.dumps(row, sort_keys=True) + '\n').encode()
        if os.write(fd, data) != len(data):
            raise RuntimeError('Short fixture evidence write')
    finally:
        os.close(fd)
'''

CONTEXT = '''import inspect
from amplifier_module_context_simple import mount as simple_mount
from ._proof import mark
__amplifier_module_type__ = 'context'
async def mount(coordinator, config=None):
    cleanup = await simple_mount(coordinator, config)
    mark('mount', __file__)
    async def finish():
        if cleanup is not None:
            result = cleanup()
            if inspect.isawaitable(result):
                await result
        mark('cleanup', __file__)
    return finish
'''
PROVIDER = '''import os
from pathlib import Path
from amplifier_core import ProviderInfo
from ._proof import mark
__amplifier_module_type__ = 'provider'
class Provider:
    name = 'omittedfixture'
    def get_info(self):
        return ProviderInfo(id=self.name, display_name='No-call fixture', defaults={'model': 'fixture'})
    async def list_models(self): return []
    async def complete(self, request, **kwargs):
        Path(os.environ['OMITTED_REF_MODEL_CALLS']).touch()
        raise AssertionError('Qualification/adoption must not call a model')
    def parse_tool_calls(self, response): return []
async def mount(coordinator, config=None):
    await coordinator.mount('providers', Provider(), name='omittedfixture')
    mark('mount', __file__)
    async def finish(): mark('cleanup', __file__)
    return finish
'''
HOOK = '''from ._proof import mark
__amplifier_module_type__ = 'hook'
async def mount(coordinator, config=None):
    mark('mount', __file__)
    async def finish(): mark('cleanup', __file__)
    return finish
'''


def require(value, message):
    if not value:
        raise AssertionError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def toml_value(value):
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(k) + '=' + toml_value(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(v) for v in value) + ']'
    if isinstance(value, (str, bool, int, float)):
        return json.dumps(value)
    raise ValueError('Unsupported manifest value')


def manifest_text(data):
    return '\n'.join(json.dumps(k) + '=' + toml_value(v) for k, v in data.items()) + '\n'


def requirement_name(value):
    match = re.match(r'([A-Za-z0-9][A-Za-z0-9_.-]*)', value)
    require(match is not None, 'Invalid dependency name')
    return re.sub(r'[-_.]+', '-', match[1]).lower()


class Evidence:
    """Fixed-schema evidence; raw subprocess text never reaches output files."""
    def __init__(self, output, replacements):
        self.output, self.replacements = output, sorted(replacements, key=lambda p: len(p[0]), reverse=True)
        self.stream_hashes = {}
        self.report = {'schema': 1, 'status': 'running', 'phase': 'prerequisites',
                       'cycles': [], 'negativeControls': [], 'processes': [], 'cleanup': {'status': 'not-started'}}
        self.save()

    def text(self, text):
        for original, replacement in self.replacements:
            text = text.replace(original, replacement)
        return text

    def save(self):
        text = self.text(json.dumps(self.report, indent=2, sort_keys=True, default=str))
        require(len(text.encode()) <= 512 * 1024, 'Evidence report exceeded its fixed budget')
        (self.output / 'result.json').write_text(text + '\n')

    def phase(self, name):
        self.report['phase'] = name
        self.save()

    def log(self, row, channel, payload):
        value = self.stream_hashes.setdefault((row['id'], channel), hashlib.sha256())
        value.update(payload)
        row[channel + 'ObservedBytes'] = row.get(channel + 'ObservedBytes', 0) + len(payload)
        row[channel + 'ObservedChunks'] = row.get(channel + 'ObservedChunks', 0) + 1
        row[channel + 'ObservedSha256'] = value.hexdigest()


def safe_failure(error, phase):
    """Read frame identities only; do not load traceback source lines or locals."""
    name = type(error).__name__
    safe = {'errorType': name if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', name) else 'Exception',
            'phase': phase, 'frames': []}
    trace = error.__traceback__
    while trace:
        frame = trace.tb_frame
        module, function = frame.f_globals.get('__name__', ''), frame.f_code.co_name
        if Path(frame.f_code.co_filename) == Path(__file__):
            module = 'omitted_ref_worker_probe'
        if (isinstance(module, str) and re.fullmatch(
                r'(?:omitted_ref_worker_probe|amplifier_web|amplifier_foundation|amplifier_core)'
                r'(?:\.[A-Za-z_][A-Za-z0-9_]*)*', module)
                and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', function)):
            safe['frames'].append({'module': module, 'function': function, 'line': trace.tb_lineno})
        trace = trace.tb_next
    safe['frames'] = safe['frames'][-12:]
    return safe


def ready_record(value):
    """Retain only the ready pipe's identity and fixed boolean capabilities."""
    if not isinstance(value, dict) or value.get('type') != 'runtime.ready':
        return None
    identity = value.get('workerIdentity')
    if not isinstance(identity, dict):
        return None
    sid, pid = identity.get('sessionId'), identity.get('pid')
    if not (isinstance(sid, str) and re.fullmatch(r'[a-f0-9-]{32,36}', sid)
            and type(pid) is int and pid > 0):
        return None
    report = value.get('report')
    report = report if isinstance(report, dict) else {}
    return {'type': 'runtime.ready', 'workerIdentity': {'sessionId': sid, 'pid': pid},
            'report': {key: report[key] for key in ('standalone', 'cliAbsent')
                       if type(report.get(key)) is bool}}


def phase_record(value):
    """Narrow already-sanitized production diagnostics to bounded phase facts."""
    from amplifier_web.update_diagnostics import ERROR_TYPES, PROBE_PREFIX, probe_record
    value = value if isinstance(value, dict) else {}
    result = {}
    for key in ('phase', 'status'):
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(r'[a-z][a-z-]{0,79}', item):
            result[key] = item
    if value.get('errorType') in ERROR_TYPES:
        result['errorType'] = value['errorType']
    for key in ('durationMs', 'exitCode', 'stdoutBytes', 'stderrBytes'):
        if type(value.get(key)) is int:
            result[key] = value[key]
    if isinstance(value.get('probe'), dict):
        probe = probe_record(PROBE_PREFIX + json.dumps(value['probe']))
        if probe is not None:
            result['probe'] = probe
    return result


class ProcessObservations:
    """Tap real pipes without changing commands, environment, bytes or outcomes."""
    def __init__(self, evidence):
        self.evidence = evidence
        self.spawn = asyncio.create_subprocess_exec
        self.owned = []

    async def __call__(self, *args, **kwargs):
        require(len(self.owned) < 256, 'Subprocess evidence budget exhausted; no process launched')
        process = await self.spawn(*args, **kwargs)
        argv = list(map(str, args))
        kind = ('probe' if any(a.endswith('/update_probe.py') for a in argv) else
                'worker' if any(a.endswith('/runtime_worker.py') for a in argv) else 'command')
        row = {'id': f'process-{len(self.owned):03d}', 'pid': process.pid, 'kind': kind,
               'argvSha256': hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
               'returncode': None, 'probeReports': [], 'ready': [],
               'stdoutComplete': False, 'stderrComplete': False}
        for channel in ('stdout', 'stderr'):
            row.update({channel + 'ObservedBytes': 0, channel + 'ObservedChunks': 0,
                        channel + 'ObservedSha256': hashlib.sha256(b'').hexdigest()})
        if kind == 'probe':
            row['probeRole'] = ('collect' if '--source-policy-only' in argv else
                                'prepare' if '--checked-sources' in argv else 'compatibility')
        self.owned.append((process, row))
        self.evidence.report['processes'].append(row)
        communicate = process.communicate
        async def observed_communicate(*a, **kw):
            # On cancellation/timeout, communicate may not return consumed
            # bytes. Keep *Complete false; never label a partial hash complete.
            result = await communicate(*a, **kw)
            for channel, value in zip(('stdout', 'stderr'), result):
                if value:
                    self.evidence.log(row, channel, value)
                    if channel == 'stdout' and kind == 'probe':
                        from amplifier_web.update_diagnostics import probe_record
                        facts = probe_record(value)
                        if facts is not None:
                            row['probeReports'].append(facts)
                row[channel + 'Complete'] = True
            row['returncode'] = process.returncode
            return result
        process.communicate = observed_communicate
        def tap_reader(reader, channel):
            readline = reader.readline
            async def observed_readline():
                value = await readline()
                if value:
                    self.evidence.log(row, channel, value)
                    if channel == 'stdout':
                        try:
                            item = json.loads(value)
                        except (ValueError, UnicodeError):
                            item = None
                        safe = ready_record(item)
                        if safe is not None and len(row['ready']) < 4:
                            row['ready'].append(safe)
                else:
                    row[channel + 'Complete'] = True
                return value
            reader.readline = observed_readline
        if kind == 'worker':
            for channel in ('stdout', 'stderr'):
                reader = getattr(process, channel)
                if reader is not None:
                    tap_reader(reader, channel)
        return process

    def settle(self):
        for process, row in self.owned:
            row['returncode'] = process.returncode
        return all(process.returncode is not None for process, _ in self.owned)


def fresh_directory(path):
    require(not path.is_symlink(), 'Refusing a symlink output/root')
    if path.exists():
        require(path.is_dir() and not any(path.iterdir()), 'Root/output must be fresh; never retry in a used directory')
    else:
        path.mkdir(parents=True, mode=0o700)
    return path.resolve()


def require_network_sandbox(parent_netns):
    """Bounded read-only prerequisite; the caller owns network isolation."""
    require(re.fullmatch(r'net:\[\d+\]', parent_netns) is not None, 'Missing caller network identity')
    require(os.readlink('/proc/self/ns/net') != parent_netns,
            'Launch under a separate Linux network namespace')
    devices = {line.split(':', 1)[0].strip() for line in Path('/proc/net/dev').read_text().splitlines()
               if ':' in line}
    require(devices <= {'lo'}, 'Network namespace contains non-loopback interfaces')
    return {'separateNamespace': True, 'onlyLoopbackInterfaces': True,
            'owner': 'caller', 'uvOffline': False, 'gitProtocols': ['file']}


def isolated_environment(root, git_binary, uv_cache):
    """No inherited app settings, credentials, Python path or uv/Git overrides."""
    path = os.environ.get('PATH', os.defpath)
    os.environ.clear()
    os.environ.update({
        'PATH': path, 'HOME': str(root / 'home'), 'LANG': 'C.UTF-8',
        'TMPDIR': str(root / 'tmp'), 'XDG_CONFIG_HOME': str(root / 'config'),
        'XDG_CACHE_HOME': str(root / 'cache'), 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONNOUSERSITE': '1', 'UV_CACHE_DIR': str(uv_cache),
        'UV_PYTHON_DOWNLOADS': 'never', 'UV_NO_PROGRESS': '1',
        'UV_HTTP_TIMEOUT': '5', 'UV_HTTP_RETRIES': '0',
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_COUNT': '0',
        'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': 'file',
        'AMPLIFIER_WEB_HOME': str(root / 'app'), 'AMPLIFIER_HOME': str(root / 'shared'),
        'AMPLIFIER_UNIFIED_IMPORT_HOME': str(root / 'legacy'),
        'AMPLIFIER_SESSION_STATE_HOME': str(root / 'ownership'),
        'AMPLIFIER_SOURCE_STORE': str(root / 'app/source-store'),
        'AMPLIFIER_UNIFIED_RELEASE': '',
        'OMITTED_REF_MOUNTS': str(root / 'mounts.jsonl'),
        'OMITTED_REF_MODEL_CALLS': str(root / 'model-calls'),
    })
    for name in ('home', 'tmp', 'workspace', 'shared', 'origin', 'bin'):
        (root / name).mkdir(mode=0o700)
    config = root / 'gitconfig'
    config.write_text('[url "' + (root / 'origin').as_uri() + '"]\n insteadOf = ' + URL + '\n'
                      '[core]\n hooksPath = /dev/null\n[credential]\n helper =\n')
    os.environ['GIT_CONFIG_GLOBAL'] = str(config)
    # Git expands insteadOf even on get-url. Identity checks need the stored
    # authored origin, not its test-only transport. All other argv/env are exact.
    wrapper = root / 'bin/git'
    wrapper.write_text('#!' + sys.executable + '\nimport os, sys\n'
        'args = sys.argv[1:]\nenv = dict(os.environ)\n'
        "if args == ['remote', 'get-url', 'origin'] or (len(args) == 5 and args[0] == '-C' and args[2:] == ['remote', 'get-url', 'origin']):\n"
        "    env['GIT_CONFIG_GLOBAL'] = os.devnull\n"
        'os.execve(' + repr(git_binary) + ', [' + repr(git_binary) + '] + args, env)\n')
    wrapper.chmod(0o700)
    os.environ['PATH'] = str(root / 'bin') + os.pathsep + path


def lock_identity(data, project_name):
    """Compare every original package entry, excluding only root/fixture rows."""
    return sorted((row for row in data.get('package', [])
                   if row['name'] not in {project_name, *PACKAGES}),
                  key=lambda row: json.dumps(row, sort_keys=True))


def prepare_manifest(args, destination, evidence):
    """Accept only the canonical setup contract, then append the fixture deps."""
    data = tomllib.loads(args.runtime_manifest.read_text())
    setup_lock = tomllib.loads(args.runtime_lock.read_text())
    canonical = tomllib.loads((CHECKOUT / 'amplifier_web/runtime_deps/pyproject.toml').read_text())
    required = {requirement_name(value) for value in canonical['project']['dependencies']}
    repository_lock = tomllib.loads((CHECKOUT / 'uv.lock').read_text())
    loop_name = 'amplifier-module-loop-live'
    loop_rows = [row for row in repository_lock['package'] if row['name'] == loop_name]
    require(len(loop_rows) == 1, 'Repository lock has ambiguous loop identity')
    loop = urlsplit(loop_rows[0]['source']['git'])
    require(re.fullmatch('[a-f0-9]{40}', loop.fragment) is not None, 'Repository loop pin is missing')
    expected = copy.deepcopy(canonical)
    expected['project']['requires-python'] = '>=3.13,<3.14'
    expected['tool']['uv']['environments'] = ["sys_platform == 'linux'"]
    # This exact structural comparison also preserves all direct dependencies,
    # constraints, registry Core policy, source URLs and original branch refs.
    normalized = copy.deepcopy(data)
    normalized['project']['requires-python'] = normalized['project']['requires-python'].replace(' ', '')
    normalized['tool']['uv']['environments'] = [
        value.replace('"', "'") for value in normalized['tool']['uv'].get('environments', [])]
    require(normalized == expected, 'Supplied manifest differs from canonical setup contract')
    require(required <= {row['name'] for row in setup_lock['package']}, 'Setup lock omits canonical dependencies')
    require(not any(row['name'] in PACKAGES for row in setup_lock['package']), 'Setup lock already contains fixture packages')
    setup_loop = [row for row in setup_lock['package'] if row['name'] == loop_name]
    require(len(setup_loop) == 1, 'Setup lock has ambiguous loop identity')
    locked_loop = urlsplit(setup_loop[0]['source']['git'])
    require(locked_loop._replace(query='', fragment='').geturl() == expected['tool']['uv']['sources'][loop_name]['git']
            and locked_loop.fragment == loop.fragment
            and parse_qs(locked_loop.query).get('rev') == ['main'], 'Setup lock changed the original loop identity')
    core = [row for row in setup_lock['package'] if row['name'] == 'amplifier-core']
    require(len(core) == 1 and core[0]['source'] == {'registry': 'https://pypi.org/simple'}
            and core[0].get('wheels'), 'Core must retain its registry-wheel identity')
    project_name = data['project']['name']
    root_rows = [row for row in setup_lock['package'] if row['name'] == project_name]
    require(len(root_rows) == 1 and root_rows[0]['source'] == {'virtual': '.'}, 'Setup lock is not the runtime project lock')
    original_graph = lock_identity(setup_lock, project_name)
    data['project']['dependencies'].extend(PACKAGES)
    selected = data.setdefault('tool', {}).setdefault('uv', {}).setdefault('sources', {})
    for name, (_, kind) in PACKAGES.items():
        selected[name] = {'git': URL, 'branch': 'main', 'subdirectory': 'modules/' + kind}
    destination.write_text(manifest_text(data))
    evidence.report['supply'] = {'manifestSha256': digest(args.runtime_manifest),
        'setupLockSha256': digest(args.runtime_lock),
        'canonicalManifestSha256': digest(CHECKOUT / 'amplifier_web/runtime_deps/pyproject.toml'),
        'repositoryLockSha256': digest(CHECKOUT / 'uv.lock'),
        'generatedManifestSha256': digest(destination), 'loopRevision': loop.fragment,
        'directDependencyCount': len(required), 'setupPackageCount': len(original_graph),
        'uvCache': str(args.uv_cache), 'cacheContentIdentity': 'mutable; exact setup lock retained'}
    evidence.save()
    return required, original_graph, project_name


async def make_repository(root, process):
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.host.config import PRECONFIGURED_BUNDLES
    repo = root / 'origin'
    await process('git', 'init', '-b', 'main', cwd=repo)
    await process('git', 'config', 'user.email', 'fixture@example.invalid', cwd=repo)
    await process('git', 'config', 'user.name', 'Fixture', cwd=repo)
    await process('git', 'config', 'commit.gpgsign', 'false', cwd=repo)
    plan = {'bundle': {'name': PROFILE, 'version': SNAPSHOT_VERSION},
            'session': {'orchestrator': {'module': 'loop-live'},
                        'context': {'module': 'context-omittedfixture', 'source': './modules/context'}},
            'providers': [{'module': 'provider-omittedfixture', 'source': './modules/provider'}],
            'hooks': [{'module': 'hooks-omittedfixture', 'source': './modules/hooks'}]}
    (repo / 'bundle.yaml').write_text(json.dumps(plan))
    for name, (_, kind) in PACKAGES.items():
        directory = repo / 'modules' / kind
        package = directory / name.replace('-', '_')
        package.mkdir(parents=True)
        (directory / 'pyproject.toml').write_text(manifest_text({
            'project': {'name': name, 'version': '0.1.0'},
            'build-system': {'requires': [], 'build-backend': 'backend', 'backend-path': ['.']}}))
        (directory / 'backend.py').write_text(BACKEND)
        (package / '__init__.py').write_text({'context': CONTEXT, 'provider': PROVIDER, 'hooks': HOOK}[kind])
        (package / '_proof.py').write_text(PROOF_MODULE)
        (package / '_payload.py').write_text('PAYLOAD = 0\n')
    (root / 'shared/settings.yaml').write_text(json.dumps({
        'bundle': {'active': PROFILE, 'app': [], 'added': {PROFILE: URI}},
        'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS), 'entries': [
            {'name': name, 'uri': uri, 'role': 'standalone', 'enabled': False}
            for name, uri in PRECONFIGURED_BUNDLES.items()]}}))
    await process('git', 'add', '.', cwd=repo)
    await process('git', 'commit', '-m', 'baseline', cwd=repo)
    return await process('git', 'rev-parse', 'HEAD', cwd=repo)


def mounts(root):
    path = root / 'mounts.jsonl'
    if not path.exists():
        return []
    require(path.stat().st_size <= 256 * 1024, 'Mount evidence exceeded its budget')
    return [json.loads(line) for line in path.read_text().splitlines()]


def native_ended(row):
    """Check exact Linux process identity without signalling a possibly reused PID."""
    try:
        current = Path('/proc', str(row['pid']), 'stat').read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError:
        return True
    return current != row['processStartTicks']


def adopted(rows, *, generation, revision, payload_hash, prefix, pid):
    selected = [row for row in rows if row.get('kind') == 'worker' and row.get('event') == 'mount' and row.get('pid') == pid]
    require({r['module'] for r in selected} == {name.replace('-', '_') for name in PACKAGES}, 'Worker did not mount every fixture module')
    for row in selected:
        require(row['generation'] == generation and row['revision'] == revision, 'Worker did not adopt the activated source revision')
        require(row['payloadSha256'] == payload_hash, 'Worker mounted stale same-version payload')
        require(Path(row['prefix']) == prefix and Path(row['executable']).parent == prefix / 'bin', 'Worker used a different interpreter')
        require(Path(row['origin']).is_absolute(), 'Missing concrete module origin')
    return selected


def rejected(call, message):
    try:
        call()
    except AssertionError as error:
        return {'status': 'rejected', 'errorType': type(error).__name__}
    raise AssertionError(message)


async def run(args, evidence):
    root = args.root
    original_environment = dict(os.environ)
    original_path, original_bytecode = list(sys.path), sys.dont_write_bytecode
    observations = ProcessObservations(evidence)
    app = runtime = environments = None
    manifest_path = None
    leases = []
    retained = []
    failure = None
    try:
        require(sys.platform == 'linux' and sys.version_info[:2] == (3, 13), 'Requires Linux and Python 3.13')
        uv, git_binary = shutil.which('uv'), shutil.which('git')
        require(uv and git_binary, 'Requires installed uv and Git')
        evidence.phase('network-sandbox-prerequisite')
        evidence.report['network'] = require_network_sandbox(args.parent_netns)
        isolated_environment(root, git_binary, args.uv_cache)
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(CHECKOUT))
        asyncio.create_subprocess_exec = observations
        from amplifier_web import updates, update_checks
        from amplifier_web import runtime_environment as environments
        from amplifier_web import runtime_qualification as qualification
        from amplifier_web.runtime import RuntimeManager
        from amplifier_web.service import AppService
        from amplifier_web.generation_leases import acquire
        from amplifier_web.host.config import read_config
        from amplifier_web.bundles import profile_candidates
        from amplifier_web.runtime_profiles import configuration_key
        from amplifier_web.update_sequence import classify
        baseline_revision = await make_repository(root, updates.process)
        generated = root / 'runtime-supply.toml'
        evidence.phase('canonical-supply-prerequisite')
        required, setup_graph, project_name = prepare_manifest(args, generated, evidence)
        manifest_path = environments.manifest_path
        environments.manifest_path = lambda: generated
        home, workspace = root / 'app', root / 'workspace'
        runtime = RuntimeManager(startup_timeout=90)
        app = AppService(home, runtime=runtime, workspace=workspace)
        runtime.app_bridge = app.app_bridge
        manager = app.update_manager = updates.UpdateManager(app)
        require(profile_candidates(read_config(workspace, home=home)) == [PROFILE], 'Unexpected profiles would fetch unrelated dependencies')
        evidence.phase('baseline-install')
        baseline = environments.prepare_project(home)
        shutil.copy2(args.runtime_lock, baseline / 'uv.lock')
        require(digest(baseline / 'uv.lock') == evidence.report['supply']['setupLockSha256'], 'Baseline lock copy changed')
        # The exact supplied lock is the starting point. Adding only three
        # dependency-free fixtures requires a lock update, not --locked here.
        await updates.process(uv, 'sync', '--project', str(baseline), '--python', '3.13', timeout=900)
        require(lock_identity(tomllib.loads((baseline / 'uv.lock').read_text()), project_name) == setup_graph,
                'Adding fixture dependencies changed the exact setup package graph')
        baseline_graph = qualification.installed_graph(baseline)
        require(required <= {row['name'] for row in baseline_graph}, 'Baseline lacks the canonical worker graph')
        evidence.report['baseline'] = {'revision': str(baseline_revision), 'packageCount': len(baseline_graph),
                                       'lockSha256': digest(baseline / 'uv.lock'), 'setupGraphPreserved': True}

        # Real guard, not a mocked validation failure or an altered good receipt.
        evidence.phase('negative-missing-qualification')
        pointer = updates.active_release(home)
        missing = uuid.uuid4().hex
        await manager.publish(pendingRelease=missing)
        try:
            await manager.activate()
        except ValueError as error:
            require(str(error) == 'This ecosystem release has not passed validation', 'Wrong activation rejection')
            evidence.report['negativeControls'].append({'name': 'missing-qualification', 'status': 'rejected', 'productionGuard': True})
        else:
            raise AssertionError('Production activation accepted an unqualified release')
        finally:
            await manager.publish(pendingRelease=None)
        require(updates.active_release(home) == pointer, 'Negative activation changed the pointer')

        # Detect supply/policy incompatibility before either positive cycle. This
        # is a REAL read/derivation, not filtered inventory or a patched guard.
        evidence.phase('dependency-supply-update-policy-preflight')
        await environments.update_manifest(home)
        require(not any(row.get('kind') == 'runtime environment' for row in await environments.update_inventory(home)),
                'Baseline unexpectedly requires runtime policy migration')
        source = app._new_session({'bundle': PROFILE})
        app.state['sessions'].append(source)
        events = []
        async def emit(kind, data):
            if kind == 'runtime.status' and data.get('status') == 'ready':
                events.append({'type': kind, 'sessionId': data.get('sessionId'), 'status': 'ready'})

        for cycle in (1, 2):
            evidence.phase(f'cycle-{cycle}-check')
            for name, (_, kind) in PACKAGES.items():
                (root / 'origin/modules' / kind / name.replace('-', '_') / '_payload.py').write_text(f'PAYLOAD = {cycle}\n')
            await updates.process('git', 'commit', '-am', f'same-version payload {cycle}', cwd=root / 'origin')
            checked = str(await updates.process('git', 'rev-parse', 'HEAD', cwd=root / 'origin'))
            payload_hash = hashlib.sha256(f'PAYLOAD = {cycle}\n'.encode()).hexdigest()
            proof = await update_checks.ref_policy(URL, 'main', updates.process)
            require(proof['kind'] == 'branch' and proof['revision'] == checked, 'Checked branch identity differs')
            # Use production tier classification, retaining ALL rows. Without
            # tiers the older stage path refreshes every moving Git dependency,
            # not merely the fixture packages checked by this bounded scenario.
            inventory = classify(home, await environments.update_inventory(home))
            chosen = [row for row in inventory if row.get('package') in PACKAGES]
            require(len(chosen) == len(PACKAGES), 'Fixture package inventory is incomplete')
            for row in chosen:
                require(row['eligible'] and row['ref'] == 'main' and row['url'] == URL, 'Omitted-ref source lost moving policy')
                require(row['current'] == (retained[-1]['revision'] if retained else baseline_revision), 'Previous package identity changed')
                row.update(status='update', latest=checked)
            # Retain every observed inventory row, including protected sources.
            manager.inventory = inventory
            await manager.publish(items=inventory, available=len(chosen))
            before_processes = len(observations.owned)
            evidence.phase(f'cycle-{cycle}-install-qualify-activate')
            await manager.install()  # Owns validate(), receipt and activation.
            state = app.state['updates']
            require(state['phase'] == 'installed' and state.get('pendingRelease') is None,
                    'Production install did not finish')
            active = updates.active_release(home)
            generation = active['current']
            require(active.get('previous') == (retained[-1]['generation'] if retained else None), 'Wrong rollback pointer')
            require(generation and generation != active.get('previous'), 'No new generation was promoted')
            leases.append(acquire(home, generation, os.getpid()))
            stage = environments.receipt_directory(home, generation)
            project = environments.project_path(home, generation)
            require(project.name.startswith('q-'), 'Adoption target is not the frozen project')
            require(json.loads((stage / 'plan.json').read_text())['mode'] == 'worker', 'Data-only reuse did not qualify a worker')
            marker = json.loads((stage / 'validated.json').read_text())
            require(marker['generationSchema'] == 1 and marker['workerProtocol'] == 1, 'No production qualification receipt')
            descriptor = json.loads((stage / 'profiles-qualified.json').read_text())
            config = read_config(workspace, home=home, session_id=source['id'], registry_home=stage / 'foundation')
            require(descriptor['profiles'] == [PROFILE] and descriptor['configuration'] == configuration_key(config.settings, home, stage),
                    'Worker would qualify a different custom generation')
            qualification.verify_recorded(project, stage)
            graph = qualification.installed_graph(project)
            require(required <= {row['name'] for row in graph}, 'Qualified graph omits canonical packages')
            require('include-system-site-packages = false' in (project / '.venv/pyvenv.cfg').read_text().lower(), 'Generation inherits host site-packages')
            policies = json.loads((stage / 'runtime-sources.json').read_text())
            for name in PACKAGES:
                require(policies[name]['ref'] == 'main' and policies[name]['revision'] == checked and policies[name]['url'] == URL,
                        'Recorded policy differs from checked source')
                installed = next(row for row in graph if row['name'] == name)
                require(installed['version'] == '0.1.0' and installed.get('cacheSource', {}).get('revision') == checked
                        and installed['cacheSource']['dirty'] is False,
                        'Fixture did not install from the selected cache revision')
            selection = json.loads((stage / 'selected-source-declarations.json').read_text())
            require(all(selection['packages'][name]['status'] == 'selected' and selection['packages'][name]['ref'] == 'main'
                        for name in PACKAGES), 'Omitted root was not observed by the real collector')
            probes = [row for _, row in observations.owned[before_processes:] if row['kind'] == 'probe']
            roles = [row['probeRole'] for row in probes]
            require(roles.count('collect') >= 1 and roles[-2:] == ['prepare', 'compatibility']
                    and set(roles[:-2]) == {'collect'}, 'Expected collection, preparation and compatibility subprocesses')
            facts = [fact for row in probes for fact in row['probeReports']]
            # probe_record currently omits source-policy-collected from its
            # stage allowlist. The invocation role and dependenciesPrepared
            # distinguish it without retaining unsanitized probe JSON.
            require(len(facts) == len(probes)
                    and all(fact.get('dependenciesPrepared') is False for fact in facts[:-2])
                    and [fact.get('stage') for fact in facts[-2:]] == ['prepared', 'complete']
                    and all(fact.get('ok') is True for fact in facts)
                    and all(row['returncode'] == 0 for row in probes), 'Actual subprocess qualification did not complete')
            require(facts[-1].get('standalone') and facts[-1].get('providersPresent') and facts[-1].get('cliAbsent'),
                    'Real compatibility capability checks were not satisfied')
            probe_mounts = [r for r in mounts(root) if r['kind'] == 'probe' and r['generation'] == generation]
            require({r['module'] for r in probe_mounts if r['event'] == 'mount'} == {name.replace('-', '_') for name in PACKAGES},
                    'Compatibility probe did not really mount every fixture module')
            require({(r['pid'], r['module']) for r in probe_mounts if r['event'] == 'mount'} ==
                    {(r['pid'], r['module']) for r in probe_mounts if r['event'] == 'cleanup'}, 'Probe cleanup was not observed')
            require(all(r['revision'] == checked and Path(r['prefix']) == project / '.venv' and r['payloadSha256'] == payload_hash
                        for r in probe_mounts), 'Compatibility mounted the wrong revision/interpreter')
            require(all(native_ended(r) for r in probe_mounts), 'Compatibility Python process did not exit')

            evidence.phase(f'cycle-{cycle}-worker-adoption')
            command = runtime._command(home=home)
            require(Path(runtime.project_path(command)) == project and '--no-sync' in command and '--locked' in command,
                    'Production worker command does not select the frozen environment')
            negative = rejected(lambda: adopted(mounts(root), generation=generation, revision=checked,
                payload_hash=payload_hash, prefix=project / '.venv', pid=retained[-1]['nativePid'] if retained else -1),
                'Adoption verifier accepted an absent/previous worker')
            evidence.report['negativeControls'].append({'name': f'cycle-{cycle}-non-adoption', 'verifierOnly': True, **negative})
            worker_start = len(observations.owned)
            await runtime.start(source, emit)
            row = runtime.workers[source['id']]
            native_pid = row.get('native_worker_pid')
            require(type(native_pid) is int, 'runtime.ready lacked native worker identity')
            require(row['generation'] == row['source_generation'] == generation
                    and row['ready'].done() and not row['ready'].cancelled(), 'Worker did not become ready in activated generation')
            actual = adopted(mounts(root), generation=generation, revision=checked, payload_hash=payload_hash,
                             prefix=project / '.venv', pid=native_pid)
            workers = [p for _, p in observations.owned[worker_start:] if p['kind'] == 'worker']
            require(len(workers) == 1 and len(workers[0]['ready']) == 1, 'Missing exact runtime.ready pipe evidence')
            require(workers[0]['ready'][0]['workerIdentity']['pid'] == native_pid, 'Mount and ready came from different workers')
            await runtime.control(source['id'], 'configuration.inspect', {})
            require(len(events) == cycle, 'Missing public ready observation')
            await runtime.stop(source['id'])
            require(all(p.returncode is not None for p, r in observations.owned[worker_start:] if r['kind'] == 'worker'), 'Worker stop did not reap its launcher')
            require(all(native_ended(r) for r in actual), 'Worker Python process did not exit')
            worker_events = [r for r in mounts(root) if r['pid'] == native_pid and r['kind'] == 'worker']
            require({r['module'] for r in worker_events if r['event'] == 'cleanup'} == {r['module'] for r in actual}, 'Worker module cleanup missing')
            require(not (root / 'model-calls').exists(), 'A model invocation was attempted')
            qualification.verify_recorded(project, stage)
            payload = qualification.package_content(project)
            receipt_hashes = {name: digest(stage / name) for name in RECEIPTS if (stage / name).exists()}
            from amplifier_foundation.sources.shared import SharedSourceStore
            store = SharedSourceStore(home / 'source-store')
            source_root = store.verify(URL, checked)
            source_hashes = {str(p.relative_to(source_root)): digest(p) for p in source_root.rglob('*')
                             if p.is_file() and not p.is_symlink() and '.git' not in p.relative_to(source_root).parts}
            for old in retained:
                require(old['receiptHashes'] == {name: digest(old['stage'] / name) for name in old['receiptHashes']}, 'First receipts changed')
                qualification.verify_recorded(old['project'], old['stage'])
                require(qualification.installed_graph(old['project']) == old['graph'], 'First installed graph changed')
                require(qualification.package_content(old['project']) == old['payload'] != payload, 'Same-version payload did not change or old payload was altered')
                old_root = store.verify(URL, old['revision'])
                require(old['sourceHashes'] == {str(p.relative_to(old_root)): digest(p) for p in old_root.rglob('*')
                        if p.is_file() and not p.is_symlink() and '.git' not in p.relative_to(old_root).parts}, 'First source object changed')
                require(old['nativePid'] != native_pid and old['project'] != project, 'Worker/environment was reused across changed payloads')
            retained.append({'generation': generation, 'revision': checked, 'stage': stage, 'project': project,
                             'graph': graph, 'payload': payload, 'receiptHashes': receipt_hashes,
                             'sourceHashes': source_hashes, 'nativePid': native_pid})
            evidence.report['cycles'].append({'cycle': cycle, 'generation': generation, 'revision': checked,
                'previous': active.get('previous'), 'packageVersion': '0.1.0', 'packageCount': len(graph),
                'installedPayloadSha256': payload, 'fixturePayloadSha256': payload_hash,
                'receiptHashes': receipt_hashes, 'probeFacts': facts, 'workerCommand': command,
                'nativePid': native_pid, 'mounts': actual, 'firstProofPreserved': cycle == 2})
            evidence.save()
        require(len(evidence.report['cycles']) == 2, 'Both adjacent cycles must finish')
        require(digest(args.runtime_manifest) == evidence.report['supply']['manifestSha256']
                and digest(args.runtime_lock) == evidence.report['supply']['setupLockSha256'],
                'Caller-owned setup identity changed during qualification')
        evidence.report['modelCalls'] = 0
        evidence.report['positiveChecksComplete'] = True
    except BaseException as error:
        failure = error
        evidence.report['failure'] = safe_failure(error, evidence.report['phase'])
    finally:
        if app is not None:
            evidence.report['updateState'] = phase_record(app.state['updates'])
            diagnostics = app.update_manager.diagnostics.state if app.update_manager else {}
            evidence.report['updateDiagnostics'] = {
                'events': [phase_record(row) for row in diagnostics.get('events', [])[-50:]],
                'lastFailure': phase_record(diagnostics.get('lastFailure'))}
            try:
                await app.diagnostics.flush()
                page = app.diagnostics.read(stream='updates', limit=100)
                evidence.report['phaseReports'] = [phase_record(row.get('data')) for row in page['items']]
                evidence.report['phaseReportsCapped'] = page.get('nextBefore') is not None
            except BaseException as error:
                evidence.report['diagnosticReadFailure'] = safe_failure(error, 'cleanup-diagnostics')
                failure = failure or error
            try:
                await app.close()
                evidence.report['cleanup']['appClose'] = 'completed'
            except BaseException as error:
                evidence.report['cleanup']['appClose'] = {'status': 'failed', **safe_failure(error, 'cleanup-app')}
                failure = failure or error
                if runtime is not None:
                    try:
                        await runtime.close()
                    except BaseException as closing:
                        evidence.report['cleanup']['runtimeCloseError'] = safe_failure(closing, 'cleanup-runtime')
        else:
            evidence.report['cleanup']['appClose'] = 'not-created'
            if runtime is not None:
                try:
                    await runtime.close()
                except BaseException as error:
                    evidence.report['cleanup']['runtimeCloseError'] = safe_failure(error, 'cleanup-runtime')
                    failure = failure or error
        ended = observations.settle()
        try:
            evidence.report['mountEvents'] = mounts(root)
            native_exited = all(native_ended(row) for row in evidence.report['mountEvents'])
            evidence.report['cleanup']['allObservedNativeWorkersEnded'] = native_exited
            ended = ended and native_exited
        except BaseException as error:
            evidence.report['mountEvidenceFailure'] = safe_failure(error, 'cleanup-mount-evidence')
            failure = failure or error
            ended = False
        evidence.report['cleanup'].update(status='completed' if ended and not failure else 'failed' if not ended else 'settled-after-failure',
                                         allOwnedProcessesEnded=ended, retainedRoot=True)
        if ended:
            for lease in leases:
                if lease:
                    lease.unlink(missing_ok=True)
        else:
            failure = failure or AssertionError('Owned subprocess exit was not confirmed; evidence/root/leases retained')
        if (root / 'model-calls').exists():
            failure = failure or AssertionError('A model invocation was attempted')
            evidence.report['modelCallAttempted'] = True
        if environments is not None and manifest_path is not None:
            environments.manifest_path = manifest_path
        asyncio.create_subprocess_exec = observations.spawn
        os.environ.clear()
        os.environ.update(original_environment)
        sys.path[:] = original_path
        sys.dont_write_bytecode = original_bytecode
        evidence.report['status'] = 'failed' if failure else 'passed'
        evidence.save()
    return 1 if failure else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--runtime-manifest', required=True, type=Path)
    parser.add_argument('--runtime-lock', required=True, type=Path)
    parser.add_argument('--uv-cache', required=True, type=Path)
    parser.add_argument('--parent-netns', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    require(args.root.resolve() != args.output.resolve() and not args.output.resolve().is_relative_to(args.root.resolve())
            and not args.root.resolve().is_relative_to(args.output.resolve()), 'Evidence and disposable root must be separate')
    original_home = str(Path.home())
    args.output = fresh_directory(args.output)
    evidence = Evidence(args.output, [(str(args.root.resolve()), '<fixture-root>'),
        (str(CHECKOUT), '<checkout>'), (str(args.root.resolve().parent), '<task-root>'),
        (original_home, '<original-home>')])
    try:
        args.root = fresh_directory(args.root)
        evidence.phase('declared-supply-paths')
        for name in ('runtime_manifest', 'runtime_lock', 'uv_cache'):
            path = getattr(args, name).resolve(strict=True)
            require(path.is_relative_to(args.root.parent) and path != args.root.parent
                    and not path.is_relative_to(args.root) and not path.is_relative_to(args.output),
                    'Supply must remain under the task parent, outside fixture root/output')
            require(path.is_dir() if name == 'uv_cache' else path.is_file(), 'Wrong supply path type')
            setattr(args, name, path)
        require(args.runtime_manifest != args.runtime_lock
                and not args.runtime_manifest.is_relative_to(args.uv_cache)
                and not args.runtime_lock.is_relative_to(args.uv_cache)
                and any(args.uv_cache.iterdir()), 'Supply needs distinct inputs and a populated dedicated cache')
        code = asyncio.run(run(args, evidence))
    except BaseException as error:
        evidence.report.update(status='failed', launcherFailure=safe_failure(error, evidence.report['phase']))
        evidence.save()
        code = 1
    print(json.dumps({'status': evidence.report['status'], 'completedCycles': len(evidence.report['cycles']),
                      'phase': evidence.report['phase'], 'evidence': 'result.json'}), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
