"""CI-only real user-manager installation-repair qualification (stdlib harness).

CLI: python tests/fixtures/installation_repair_native.py prepare|run|cleanup
Requires disposable GitHub-hosted Ubuntu/macOS, setup-python 3.13, uv on PATH,
UV_PYTHON_DOWNLOADS=never. No sudo, container, network, dependency install or bus
provisioning. Missing real user systemd / GUI launchd is a capability FAILURE.
Workflow must run cleanup with always(), upload ONLY installation-repair-evidence,
and serialize OS jobs (max-parallel: 1). Do not run native commands locally.

Controlled substitutes: a tiny local wheel called amplifier-unified, its health,
doctor and two package probes, and a locally asserted delivery descriptor. It is
NOT official published release trust or full-app qualification. The unmodified
artifact verifier checks bytes/stamp; the exact production controller launch and
copied -I -S worker/reset/artifact siblings do every native/uv/lease/swap effect.
Private plans/tokens/environments never enter the upload directory.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import csv
import hashlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import tomllib
import uuid
import zipfile

SOURCE = Path(__file__).resolve().parents[2]
PRODUCTION = ('installation_repair.py', 'installation_repair_worker.py', 'reset.py',
              'reset_retention.py', 'app_release_artifacts.py')
SIBLINGS = PRODUCTION[1:]
UNIT = 'amplifier-unified.service'
LABEL = 'com.microsoft.amplifier-unified'
CASES = ('happy', 'preflight', 'rollback', 'verify', 'data-lease')
PROBE = 'from amplifier_web._native_fixture import probe; probe()'
DEPENDENCY = 'import importlib.metadata as m,sys; assert sys.flags.isolated; assert len(list(m.distributions())) == 1'
LIMITS = {'officialPublishedTrust': False, 'fullAppHealthDoctor': False,
          'nativeWindows': False, 'liveAdoption': False,
          'substitutions': ['local fixture wheel/delivery assertion', 'stdlib health/doctor', 'package probes']}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    temporary = path.with_name('.' + path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def module(path):
    spec = importlib.util.spec_from_file_location('_native_' + Path(path).stem, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def execute(*args, env=None, check_result=True, timeout=120):
    result = subprocess.run([str(arg) for arg in args], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    # Never publish stderr, environment, or arbitrary argv in failures.
    check(not check_result or result.returncode == 0,
          'command failed: ' + Path(str(args[0])).name + ' (exit ' + str(result.returncode) + ')')
    return result


def gate():
    check(os.environ.get('GITHUB_ACTIONS') == 'true'
          and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted',
          'native qualification requires a disposable GitHub-hosted runner')
    check(os.name == 'posix' and sys.platform in {'linux', 'darwin'} and os.getuid() != 0,
          'native qualification requires an unprivileged Linux/macOS runner account')
    import pwd

    check(Path.home().resolve() == Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(),
          'HOME must be the real disposable runner account, not a redirected home')
    check(sys.version_info[:2] == (3, 13), 'setup-python 3.13 is required')
    check(os.environ.get('UV_PYTHON_DOWNLOADS') == 'never', 'UV_PYTHON_DOWNLOADS must be never')
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    root = temp / 'installation-repair-native'
    evidence = temp / 'installation-repair-evidence'
    check(not root.is_symlink() and not evidence.is_symlink(), 'redirected fixture root')
    return root, evidence


def paths():
    if sys.platform == 'linux':
        return Path.home() / '.config/systemd/user' / UNIT
    return Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')


def manager_capability(env):
    if sys.platform == 'linux':
        result = execute('systemctl', '--user', 'show-environment', env=env, check_result=False)
    else:
        result = execute('launchctl', 'print', f'gui/{os.getuid()}', env=env, check_result=False)
    check(result.returncode == 0, 'real systemd user bus / launchd GUI domain unavailable; no skip or substitute')


def manager_environment():
    # The runner account's own user bus, never a mounted host/container bus.
    reset = module(SOURCE / 'amplifier_web/reset.py')
    recovered = reset.user_service_environment()
    return {key: value for key, value in recovered.items() if key in {
        'PATH', 'HOME', 'USER', 'LOGNAME', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS'}}


def properties(label, env):
    result = execute('systemctl', '--user', 'show', label,
                     '--property=MainPID,LoadState,ActiveState,FragmentPath,DropInPaths,NeedDaemonReload,InvocationID,NRestarts,Restart,ControlGroup',
                     env=env, check_result=False)
    value = dict(row.split('=', 1) for row in result.stdout.splitlines() if '=' in row)
    check(result.returncode == 0 or value.get('LoadState') == 'not-found',
          'systemd user manager observation failed')
    return value


def service_observation(env):
    if sys.platform == 'linux':
        return properties(UNIT, env)
    domain = f'gui/{os.getuid()}'
    result = execute('launchctl', 'print', domain + '/' + LABEL, env=env)
    return {'domain': domain, 'label': LABEL,
            'pids': re.findall(r'^\s*pid = (\d+)\s*$', result.stdout, re.M),
            'programs': re.findall(r'^\s*program = (.+?)\s*$', result.stdout, re.M)}


def launch_job(label, env):
    found = []
    for domain in (f'gui/{os.getuid()}', f'user/{os.getuid()}'):
        # Check a domain's health before interpreting a failed job print as absent.
        parent = execute('launchctl', 'print', domain, env=env, check_result=False)
        check(parent.returncode == 0, 'launchd domain observation failed')
        result = execute('launchctl', 'print', domain + '/' + label, env=env, check_result=False)
        if result.returncode == 0:
            found.append((domain, result.stdout))
    # A GUI job may be visible through a user-domain lookup as well. Preserve
    # exact identity; prefer the GUI owner used by production bootstrap/bootout.
    return found


def pid_start(pid, env):
    return execute('ps', '-p', pid, '-o', 'lstart=', env=env, check_result=False).stdout.strip()


def wait_for(read, predicate=bool, *, seconds=90, description='checkpoint'):
    deadline = time.monotonic() + seconds
    while True:
        try:
            value = read()
            if predicate(value):
                return value
        except (FileNotFoundError, json.JSONDecodeError, ConnectionError, OSError):
            pass
        check(time.monotonic() < deadline, 'timed out: ' + description)
        time.sleep(.15)


def wheel(path, revision, version):
    """Build a no-dependency test wheel using only stdlib; never a production build."""
    info = 'amplifier_unified-' + version + '.dist-info/'
    files = {'amplifier_web/' + name: (SOURCE / 'amplifier_web' / name).read_bytes() for name in PRODUCTION}
    files.update({
        'amplifier_web/__init__.py': b'"""Native service-manager TEST SUBSTITUTE, not Unified."""\n',
        'amplifier_web/_native_fixture.py': Path(__file__).with_name('installation_repair_native_app.py').read_bytes(),
        'amplifier_web/__main__.py': b'from ._native_fixture import main\nraise SystemExit(main())\n',
        'amplifier_web/release-source.json': json.dumps({
            'schema': 1, 'repository': 'microsoft/amplifier-unified', 'version': version, 'revision': revision}).encode(),
        info + 'METADATA': ('Metadata-Version: 2.1\nName: amplifier-unified\nVersion: ' + version + '\n\nNative test substitute.\n').encode(),
        info + 'WHEEL': b'Wheel-Version: 1.0\nGenerator: native-repair-stdlib-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        info + 'entry_points.txt': b'[console_scripts]\namplifier-unified = amplifier_web._native_fixture:main\n',
    })
    output = io.StringIO()
    writer = csv.writer(output, lineterminator='\n')
    for name, data in sorted(files.items()):
        writer.writerow((name, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('='), len(data)))
    writer.writerow((info + 'RECORD', '', ''))
    files[info + 'RECORD'] = output.getvalue().encode()
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    path.chmod(0o600)


def definition(launcher, home, workspace, env):
    argv = [str(launcher), '--no-open', '--data-dir', str(home), '--workspace', str(workspace), 'serve']
    if sys.platform == 'darwin':
        return plistlib.dumps({'Label': LABEL, 'AmplifierUnifiedMarker': 'amplifier-unified launch agent v1',
                               'ProgramArguments': argv, 'WorkingDirectory': str(workspace),
                               'EnvironmentVariables': env, 'RunAtLoad': True, 'KeepAlive': True,
                               'StandardOutPath': '/dev/null', 'StandardErrorPath': '/dev/null'})
    # Literal generated launch and ordinary mixed kill-group semantics. Test
    # environments are assignments, not extra launch arguments or a wrapper.
    assignments = '\n'.join('Environment=' + json.dumps(key + '=' + value) for key, value in sorted(env.items()))
    return ('# Managed by amplifier-unified; do not edit.\n[Unit]\nDescription=Amplifier Unified\nAfter=network.target\n'
            '\n[Service]\nType=simple\nExecStart=' + shlex.join(argv)
            + '\nRestart=on-failure\nRestartSec=5\nKillMode=mixed\n' + assignments
            + '\n\n[Install]\nWantedBy=default.target\n').encode()


def request(config, path, body=None):
    connection = http.client.HTTPConnection('127.0.0.1', config['port'], timeout=15)
    token = (Path(config['home']) / 'config/auth/control-token').read_text().strip()
    try:
        connection.request('POST' if body is not None else 'GET', path,
                           body=json.dumps(body) if body is not None else None,
                           headers={'Authorization': 'Bearer ' + token})
        response = connection.getresponse()
        check(response.status == 200, 'fixture authenticated HTTP request failed')
        return json.loads(response.read(65536))
    finally:
        connection.close()


def prepare(root, evidence):
    check(not root.exists() and not evidence.exists(), 'fixture/evidence destination already exists; inspect, do not replay')
    root.mkdir(mode=0o700)
    evidence.mkdir(mode=0o700)
    save(root / 'owner.json', {'definition': None, 'helpers': [], 'processes': [], 'cases': []})
    env = manager_environment()
    manager_capability(env)
    check(not paths().exists() and not paths().is_symlink(), 'preexisting service definition; refusing to overwrite')
    if sys.platform == 'linux':
        check(properties(UNIT, env).get('LoadState') == 'not-found', 'preexisting systemd service registration')
    else:
        check(not launch_job(LABEL, env), 'preexisting launchd label')
    uv = shutil.which('uv')
    check(uv is not None, 'uv must be provided by workflow PATH')
    revision = execute('git', '-C', SOURCE, 'rev-parse', 'HEAD').stdout.strip()
    check(re.fullmatch('[a-f0-9]{40}', revision), 'exact source revision required')
    selected = ['amplifier_web/' + name for name in PRODUCTION] + [
        'tests/fixtures/installation_repair_native.py', 'tests/fixtures/installation_repair_native_app.py',
        'tests/test_installation_repair_native.py', 'pyproject.toml']
    check(not execute('git', '-C', SOURCE, 'status', '--porcelain', '--', *selected).stdout.strip(),
          'source/fixture inputs differ from stamped candidate commit')
    version = tomllib.loads((SOURCE / 'pyproject.toml').read_text())['project']['version']
    artifacts = module(SOURCE / 'amplifier_web/app_release_artifacts.py')
    artifact = root / artifacts.filename(version)
    wheel(artifact, revision, version)
    descriptor = {'path': str(artifact), 'sha256': digest(artifact),
                  'url': artifacts.SOURCE + '/releases/download/v' + version + '/' + artifact.name}
    artifacts.verify(descriptor, version, revision)
    save(root / 'fixture.json', {'revision': revision, 'version': version, 'artifact': descriptor,
                               'uv': uv, 'basePython': str(Path(sys.executable).resolve())})
    save(evidence / 'inputs.json', {'revision': revision, 'version': version, 'artifactSHA256': descriptor['sha256'],
                                  'productionSHA256': {name: digest(SOURCE / 'amplifier_web' / name) for name in PRODUCTION},
                                  'fixtureSHA256': {name: digest(Path(__file__).with_name(name)) for name in
                                                   ('installation_repair_native.py', 'installation_repair_native_app.py')},
                                  'platform': sys.platform, 'os': os.uname().release, 'uid': os.getuid(),
                                  'manager': 'systemd-user' if sys.platform == 'linux' else 'launchd-gui',
                                  'uvVersion': execute(uv, '--version').stdout.strip(), 'limits': LIMITS})


def events(case):
    path = case / 'effects.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def effect_counts(case):
    # Observed fixture checkpoints, NOT an invented trace of every uv/native call.
    return dict(Counter(row['kind'] + (':' + row['stage'] if row['kind'] == 'probe' else '')
                        for row in events(case)))


def helper_label(identity):
    return ('amplifier-installation-repair-' if sys.platform == 'linux'
            else 'com.microsoft.amplifier-unified.repair.') + identity


def helper_observation(label, env):
    if sys.platform == 'linux':
        value = properties(label + '.service', env)
        if value.get('LoadState') != 'not-found':
            check(value.get('Restart') == 'no' and value.get('NRestarts') == '0',
                  'helper is not a one-shot no-restart job')
        return {'label': label, **value, 'settled': value.get('MainPID') == '0'
                and value.get('ActiveState') in {'inactive', 'failed'}}
    rows = launch_job(label, env)
    settled = True
    summaries = []
    for domain, text in rows:
        pids = re.findall(r'^\s*pid = (\d+)\s*$', text, re.M)
        runs = re.findall(r'^\s*runs = (\d+)\s*$', text, re.M)
        check(not runs or max(map(int, runs)) <= 1, 'one-shot helper automatically reran')
        settled = settled and not pids
        summaries.append({'domain': domain, 'pids': pids, 'runs': runs})
    return {'label': label, 'domains': summaries, 'settled': settled}


def terminal(folder):
    value = load(folder / 'operation.json')
    # rolled-back can be published before restart; caller also waits for helper.
    return value if value['status'] in {'ready', 'failed', 'rolled-back', 'reinstalled', 'unknown'} else None


def new_operation(config, fixture, env):
    identity = uuid.uuid4().hex
    folder = Path(config['home']) / 'installation-repair' / identity
    folder.mkdir(mode=0o700)
    for name in SIBLINGS:
        shutil.copyfile(SOURCE / 'amplifier_web' / name, folder / name)
        (folder / name).chmod(0o600)
    artifacts = module(folder / 'app_release_artifacts.py')
    artifact = folder / Path(fixture['artifact']['path']).name
    shutil.copyfile(fixture['artifact']['path'], artifact)
    artifact.chmod(0o600)
    descriptor = {**fixture['artifact'], 'path': str(artifact)}
    artifacts.verify(descriptor, fixture['version'], fixture['revision'])
    plan = {'operationId': identity, 'previewId': uuid.uuid4().hex, 'hostInstanceId': 'pending-service-binding',
            'home': config['home'], 'toolRoot': config['toolRoot'], 'binRoot': config['binRoot'],
            'basePython': fixture['basePython'], 'uv': fixture['uv'], 'extras': [],
            'pointer': {'exists': False, 'sha256': None}, 'artifact': descriptor,
            'target': {'version': fixture['version'], 'revision': fixture['revision'], 'source': descriptor['url']},
            'probe': PROBE, 'dependencyProbe': DEPENDENCY,
            'health': {'urls': [f'http://127.0.0.1:{config["port"]}/api/health'], 'pin': None},
            'env': env}
    operation = {'operationId': identity, 'previewId': plan['previewId'], 'hostInstanceId': plan['hostInstanceId'],
                 'status': 'accepted', 'stage': 'accepted', 'reinstalled': False,
                 'healthVerified': False, 'serviceState': 'not-checked'}
    save(folder / 'plan.json', plan)
    save(folder / 'operation.json', operation)
    return folder


def worker_command(fixture, folder, *, verify=False):
    return [fixture['basePython'], '-I', '-S', str(folder / 'installation_repair_worker.py'),
            *(['--verify'] if verify else []), str(folder)]


def snapshot(config):
    worker = module(SOURCE / 'amplifier_web/installation_repair_worker.py')
    prefix = Path(config['toolRoot']) / 'amplifier-unified'
    return {'prefix': worker._path_snapshot(prefix),
            'launcher': worker.file_identity(Path(config['binRoot']) / 'amplifier-unified'),
            'events': events(Path(config['caseRoot']))}


def lease_process(root, case, fixture, target, env):
    ready, release = case / 'lease-ready', case / 'lease-release'
    # Keep the holder's argv outside amplifier_web and the target prefix, so
    # assert_no_processes does NOT preempt the actual kernel data-lease test.
    for name in ('reset.py', 'reset_retention.py'):
        shutil.copyfile(SOURCE / 'amplifier_web' / name, case / name)
        (case / name).chmod(0o600)
    process = subprocess.Popen([fixture['basePython'], '-I', '-S', str(Path(__file__).resolve()),
                                '_lease', str(case / 'reset.py'), str(target),
                                str(ready), str(release)], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    owner = load(root / 'owner.json')
    owner['processes'].append({'pid': process.pid, 'start': pid_start(process.pid, env), 'release': str(release)})
    save(root / 'owner.json', owner)
    wait_for(lambda: ready.exists(), description='shared data lease')
    return process, release


def run_case(root, evidence, name, fixture, transport):
    case = root / name
    case.mkdir(mode=0o700)
    home, tools, bins = case / 'data', case / 'tools', case / 'bin'
    for path in (home, tools, bins, home / 'installation-repair', home / 'config', home / 'config/auth'):
        path.mkdir(mode=0o700)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    config = {'case': name, 'caseRoot': str(case), 'home': str(home), 'toolRoot': str(tools), 'binRoot': str(bins),
              'port': port, 'version': fixture['version'], 'revision': fixture['revision'], 'definition': str(paths())}
    save(home / 'fixture.json', config)
    (home / 'config/auth/control-token').write_text(uuid.uuid4().hex + uuid.uuid4().hex)
    (home / 'config/auth/control-token').chmod(0o600)
    draft = home / 'retained-draft.bin'
    draft.write_bytes(b'unsent draft\x00\xff\nnot an operation\n')
    draft.chmod(0o600)
    draft_hash = digest(draft)
    env = {**transport, 'UV_PYTHON_DOWNLOADS': 'never', 'UV_OFFLINE': '1', 'UV_NO_CACHE': '1',
           'UV_TOOL_DIR': str(tools), 'UV_TOOL_BIN_DIR': str(bins),
           'NATIVE_FIXTURE_CONFIG': str(home / 'fixture.json')}
    execute(fixture['uv'], 'tool', 'install', '--python', '3.13', '--from', fixture['artifact']['path'],
            'amplifier-unified', env=env)
    prefix = tools / 'amplifier-unified'
    sites = list(prefix.glob('lib/python*/site-packages/amplifier_web'))
    check(len(sites) == 1, 'fixture wheel has no unique installed package')
    installed_sources = {item: digest(sites[0] / item) for item in PRODUCTION}
    check(installed_sources == {item: digest(SOURCE / 'amplifier_web' / item) for item in PRODUCTION},
          'installed controller/worker bytes differ from exact candidate')
    (prefix / 'old-prefix-sentinel').write_bytes(b'original prefix must survive repair\n')
    old_stat = [prefix.stat().st_dev, prefix.stat().st_ino]
    raw = definition(bins / 'amplifier-unified', home, case, transport)
    path = paths()
    check(not path.exists() and not path.is_symlink(), 'definition unexpectedly exists')
    path.parent.mkdir(parents=True, exist_ok=True)
    owner = load(root / 'owner.json')
    owner['definition'] = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'home': str(home)}
    owner['cases'].append(str(case))
    save(root / 'owner.json', owner)  # durable ownership BEFORE registration/start
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
    reset = module(SOURCE / 'amplifier_web/reset.py')
    commands = reset.service_commands(home)
    if sys.platform == 'linux':
        execute('systemctl', '--user', 'daemon-reload', env=transport)
    execute(*commands[1], env=transport)
    host = wait_for(lambda: load(case / 'host.json'), description='managed old host')
    old_pid = host['pid']
    old_start = pid_start(old_pid, transport)
    old_manager = service_observation(transport)
    health = request(config, '/api/health')
    check(health['instanceId'] == host['instanceId'], 'old authenticated instance mismatch')
    if name == 'verify':
        (case / 'doctor-fail').touch(mode=0o600)
    holder = None
    if name == 'data-lease':
        holder, release = lease_process(root, case, fixture, home, env)
    folder = new_operation(config, fixture, env)
    label = helper_label(folder.name)
    check(helper_observation(label, transport)['settled'], 'helper label already active')
    # An inactive preexisting job is still not ours: refuse any registered label.
    if sys.platform == 'linux':
        check(properties(label + '.service', transport).get('LoadState') == 'not-found', 'preexisting helper registration')
    else:
        check(not launch_job(label, transport), 'preexisting helper registration')
    owner = load(root / 'owner.json')
    owner['helpers'].append({'label': label, 'folder': str(folder)})
    save(root / 'owner.json', owner)
    try:
        request(config, '/fixture-dispatch', {'operationId': folder.name})
    except (OSError, http.client.HTTPException):
        # Dispatch may kill its own HTTP host. Never retry the request.
        pass
    exclusion = None
    if name == 'happy':
        wait_for(lambda: (case / 'probe-waiting').exists(), description='candidate barrier')
        before = snapshot(config)
        same = execute(*worker_command(fixture, folder), env=env, check_result=False)
        check(same.returncode != 0, 'concurrent worker unexpectedly acquired operation lock')
        competing = new_operation(config, fixture, env)
        execute(*worker_command(fixture, competing), env=env)
        refused = load(competing / 'operation.json')
        check(refused['status'] == 'failed' and refused['stage'] == 'preflight-failed', 'namespace competitor was not refused')
        check(not (competing / 'candidate-tools').exists() and not (competing / 'backup').exists(),
              'namespace competitor created installer/swap outputs')
        check(snapshot(config) == before, 'competing workers produced installation/native effects')
        observation = helper_observation(label, transport)
        check(not observation['settled'], 'manager did not own active detached helper')
        probe_parent = next(row['parentPID'] for row in events(case) if row['kind'] == 'probe')
        if sys.platform == 'linux':
            check(observation['MainPID'] == str(probe_parent)
                  and observation['ControlGroup'] != old_manager['ControlGroup'],
                  'helper is not manager-owned outside the service kill group')
        else:
            check(any(str(probe_parent) in row['pids'] for row in observation['domains']),
                  'launchd does not own the isolated worker PID')
        exclusion = {'sameOperationExit': same.returncode, 'namespaceReceipt': refused,
                     'effectsUnchanged': True, 'helperWhileOldHostAlive': observation}
        (case / 'probe-release').touch(mode=0o600)
    wait_for(lambda: terminal(folder), description=name + ' terminal receipt')
    settled = wait_for(lambda: helper_observation(label, transport), lambda value: value['settled'],
                       description='one-shot helper settled')
    receipt = load(folder / 'operation.json')
    expected = {'happy': 'ready', 'preflight': 'failed', 'rollback': 'rolled-back',
                'verify': 'reinstalled', 'data-lease': 'failed'}[name]
    # Save sanitized failure evidence BEFORE assertions, including unknown results.
    plan = load(folder / 'plan.json')
    report = {'case': name, 'operationId': folder.name, 'receipt': receipt, 'helper': settled,
              'oldPID': old_pid, 'oldStart': old_start, 'oldPrefixStat': old_stat,
              'oldManager': old_manager,
              'installedProductionSHA256': installed_sources,
              'sourceSHA256': {item: digest(folder / item) for item in SIBLINGS},
              'planSHA256': digest(folder / 'plan.json'), 'artifactSHA256': digest(plan['artifact']['path']),
              'planPublic': {key: plan[key] for key in ('operationId', 'previewId', 'target', 'extras', 'service', 'pointer')},
              'exclusion': exclusion, 'effects': events(case), 'observedEffectCounts': effect_counts(case),
              'limits': LIMITS}
    save(evidence / (name + '.json'), report)
    check(receipt['status'] == expected, name + ': unexpected terminal status (see sanitized evidence)')
    check(digest(draft) == draft_hash, 'retained draft bytes changed')
    after_stat = [prefix.stat().st_dev, prefix.stat().st_ino]
    recovery = reset.tool_recovery_root(tools) / folder.name
    if name in {'preflight', 'data-lease'}:
        check(after_stat == old_stat and (prefix / 'old-prefix-sentinel').is_file(), 'refused repair changed original prefix')
        check(not recovery.exists(), 'refused repair created swap recovery')
    if name == 'preflight':
        check(pid_start(old_pid, transport) == old_start, 'preflight failure interrupted original service')
    else:
        check(not pid_start(old_pid, transport), 'old service PID survived native stop')
    if name in {'happy', 'verify'}:
        check(after_stat != old_stat and (recovery / 'previous/old-prefix-sentinel').is_file(), 'old prefix was not retained')
        recovered = recovery / 'previous'
        check([recovered.stat().st_dev, recovered.stat().st_ino] == old_stat, 'recovery prefix identity mismatch')
        worker = module(folder / 'installation_repair_worker.py')
        check(worker.installed_graph(plan, prefix / 'bin/python', env) == load(folder / 'candidate-graph.json'), 'fresh graph differs')
        check((folder / 'constraints.txt').stat().st_size > 0, 'candidate constraints missing')
    if name == 'rollback':
        check(after_stat == old_stat and (recovery / 'failed').is_dir(), 'failed fresh prefix or original rollback not preserved')
    if name == 'data-lease':
        check(receipt['stage'] == 'left-stopped' and not receipt['reinstalled']
              and receipt.get('errorType') == 'RuntimeError' and holder.poll() is None,
              'live shared data lease did not prevent swap')
        release.touch(mode=0o600)
        holder.wait(timeout=15)
    if name != 'data-lease':
        successor = (load(case / 'host.json') if name == 'preflight' else
                     wait_for(lambda: load(case / 'host.json'),
                              lambda value: value['pid'] != old_pid, description='managed successor startup'))
        report['newManager'] = service_observation(transport)
        if name != 'preflight':
            check(successor['pid'] != old_pid and pid_start(successor['pid'], transport), 'new manager host PID missing')
            worker = module(folder / 'installation_repair_worker.py')
            check(worker._service_pid(plan) == successor['pid'], 'successor is not exact manager PID')
        if name in {'happy', 'verify'}:
            observed = request(config, '/api/health')
            check(observed['instanceId'] == successor['instanceId'] and observed['installationRepair']['operationId'] == folder.name,
                  'authenticated successor operation/instance mismatch')
        if name == 'verify':
            stable = snapshot(config)
            (case / 'doctor-fail').unlink()
            execute(*worker_command(fixture, folder, verify=True), env=env, timeout=240)
            verified = load(folder / 'operation.json')
            check(verified['status'] == 'ready', 'explicit read-only verification did not recover readiness')
            current = snapshot(config)
            check(current['prefix'] == stable['prefix'] and current['launcher'] == stable['launcher'], 'verify replaced installation')
            check([row for row in current['events'] if row['kind'].startswith('service-')]
                  == [row for row in stable['events'] if row['kind'].startswith('service-')], 'verify restarted service')
            report['verification'] = verified
    stable = snapshot(config)
    saved_receipt = (folder / 'operation.json').read_bytes()
    execute(*worker_command(fixture, folder), env=env)
    check(snapshot(config) == stable and (folder / 'operation.json').read_bytes() == saved_receipt,
          'exact settled operation replay produced effects')
    time.sleep(.6)
    check(helper_observation(label, transport)['settled'], 'one-shot helper reran')
    counters = effect_counts(case)
    check(counters.get('dispatch') == 1, 'operation was dispatched more than once')
    check(counters.get('service-start') == (1 if name in {'preflight', 'data-lease'} else 2)
          and counters.get('service-stop', 0) == (0 if name == 'preflight' else 1),
          'unexpected native service stop/start checkpoint count')
    report.update(effects=events(case), draftSHA256=digest(draft), effectsUnchangedOnReplay=True,
                  observedEffectCounts=counters,
                  finalPrefixStat=after_stat, newHost=load(case / 'host.json'), passed=True)
    save(evidence / (name + '.json'), report)


def cleanup(root, evidence):
    if not (root / 'owner.json').exists():
        return  # preparation never acquired registration ownership
    owner = load(root / 'owner.json')
    env = manager_environment()
    manager_capability(env)
    results = {'helpers': [], 'definitionAbsent': False, 'processesExited': False, 'passed': False}
    save(evidence / 'cleanup.json', results)
    for process in owner['processes']:
        Path(process['release']).touch(mode=0o600)
    # Never kill/remove a possibly running installer. Require its exact saved job
    # to settle. Timeout fails cleanup, retaining definitions/ownership for review.
    for helper in owner['helpers']:
        label = helper['label']
        observed = wait_for(lambda: helper_observation(label, env), lambda row: row['settled'],
                            seconds=120, description='cleanup helper settlement')
        if sys.platform == 'darwin':
            for row in observed['domains']:
                execute('launchctl', 'bootout', row['domain'] + '/' + label, env=env, check_result=False)
            wait_for(lambda: not launch_job(label, env), description='helper registration absence')
        else:
            execute('systemctl', '--user', 'reset-failed', label + '.service', env=env, check_result=False)
            wait_for(lambda: properties(label + '.service', env).get('LoadState') == 'not-found',
                     description='transient helper collection')
        results['helpers'].append({'label': label, 'settled': observed, 'absent': True})
    saved = owner['definition']
    pids, helper_pids, child_pids = [], [], []
    for case in owner['cases']:
        pids.extend(row['pid'] for row in events(Path(case)) if row['kind'] == 'service-start')
        helper_pids.extend(row['parentPID'] for row in events(Path(case))
                           if row['kind'] == 'probe')
        child_pids.extend(row['pid'] for row in events(Path(case))
                          if row['kind'] in {'probe', 'doctor'})
    if saved:
        path = Path(saved['path'])
        check(path == paths() and path.is_file() and not path.is_symlink() and digest(path) == saved['sha256'],
              'owned definition changed; refusing cleanup')
        commands = module(SOURCE / 'amplifier_web/reset.py').service_commands(Path(saved['home']))
        execute(*commands[0], env=env, check_result=False)
        if sys.platform == 'linux':
            wait_for(lambda: properties(UNIT, env).get('MainPID') == '0', description='service stopped')
        else:
            wait_for(lambda: not launch_job(LABEL, env), description='service bootout')
        check(digest(path) == saved['sha256'], 'definition changed during cleanup')
        path.unlink()
        if sys.platform == 'linux':
            execute('systemctl', '--user', 'daemon-reload', env=env)
            execute('systemctl', '--user', 'reset-failed', UNIT, env=env, check_result=False)
            wait_for(lambda: properties(UNIT, env).get('LoadState') == 'not-found', description='unit definition absence')
        else:
            check(not launch_job(LABEL, env), 'launchd registration remained')
    wait_for(lambda: all(not pid_start(pid, env) for pid in pids)
             and all(not pid_start(pid, env) for pid in helper_pids)
             and all(not pid_start(pid, env) for pid in child_pids)
             and all(not pid_start(row['pid'], env) for row in owner['processes']), description='owned PIDs exited')
    results.update(definitionAbsent=not paths().exists(), processesExited=True,
                   servicePIDs=sorted(set(pids)), helperPIDs=sorted(set(helper_pids)),
                   probeDoctorPIDs=sorted(set(child_pids)), passed=True)
    save(evidence / 'cleanup.json', results)
    history = evidence / 'cleanup-history.json'
    save(history, [*(load(history) if history.exists() else []), results])
    # Retain fixtures, uv prefixes and private operation receipts in RUNNER_TEMP;
    # the disposable runner owns storage reclamation. No broad filesystem delete.
    owner['definition'], owner['helpers'], owner['processes'] = None, [], []
    save(root / 'owner.json', owner)


def run(root, evidence):
    fixture = load(root / 'fixture.json')
    transport = manager_environment()
    for case in CASES:
        try:
            run_case(root, evidence, case, fixture, transport)
        finally:
            cleanup(root, evidence)
    save(evidence / 'result.json', {'passed': True, 'cases': list(CASES), 'limits': LIMITS})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run', 'cleanup'))
    args = parser.parse_args()
    os.umask(0o077)
    root, evidence = gate()
    try:
        {'prepare': prepare, 'run': run, 'cleanup': cleanup}[args.action](root, evidence)
    except Exception as error:
        if evidence.is_dir():
            save(evidence / (args.action + '-failure.json'), {'passed': False, 'errorType': type(error).__name__,
                                                           'detail': str(error) if isinstance(error, RuntimeError) else 'fixture failed; private state retained'})
        raise


if __name__ == '__main__':
    if len(sys.argv) == 6 and sys.argv[1] == '_lease':
        os.umask(0o077)
        reset = module(Path(sys.argv[2]))
        with reset.repair_lock(Path(sys.argv[3]), shared=True):
            Path(sys.argv[4]).touch(mode=0o600)
            deadline = time.monotonic() + 180
            while not Path(sys.argv[5]).exists() and time.monotonic() < deadline:
                time.sleep(.1)
    else:
        main()
