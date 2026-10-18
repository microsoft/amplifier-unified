#!/usr/bin/env python3
"""Manual IC-13 qualification; never installs into the host interpreter.

run requires a GitHub-hosted Linux runner with Docker already available. It
copies only committed candidate bytes and a credential-free Git object bundle
of the exact private loop checkout. Preparation has network access; a separate
unprivileged, network-none container executes the unchanged installed suites.
A fresh output/work directory is mandatory. An uncertain operation is recorded,
never retried or reported as cleaned up. Use the retained IDs to reconcile it.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.parse
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ET
from email.parser import Parser

ROOT = Path(__file__).resolve().parents[1]
GUEST = Path('/opt/defaults')
LABEL = 'org.amplifier.default-installed'
SUITES = ('test_default_behaviors_integration.py', 'test_default_runtime_safety.py',
          'test_default_mount_runtime.py', 'test_default_routing_runtime.py')
DESKTOP_CASES = {
    'preserved-22': {
        'test_expanded_desktop_restriction_survives_child_alias': 8,
        'test_resume_preserves_saved_desktop_ceiling_and_history': 6,
        'test_runtime_desktop_ceiling_controls': 4,
        'test_runtime_inherited_desktop_ceiling_survives_representation': 4,
    },
    'remote-defaults': {
        'test_runtime_remote_implicit_restriction_cannot_be_disabled_by_child': 2,
        'test_runtime_remote_explicit_write_opt_in_control': 1,
    },
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def in_container():
    return Path('/.dockerenv').is_file()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temp.replace(path)


def clean_env():
    return {'PATH': '/opt/defaults/bin:/opt/defaults/.venv/bin:/usr/bin:/bin',
            'HOME': str(GUEST / 'home'), 'LANG': 'C.UTF-8',
            'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
            'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'TMPDIR': str(GUEST / 'tmp'),
            'UV_CACHE_DIR': str(GUEST / 'cache'), 'UV_NO_CONFIG': '1',
            'UV_PYTHON_INSTALL_DIR': str(GUEST / 'python'),
            'UV_NO_BUILD_PACKAGE': 'amplifier-core',
            'UV_CONCURRENT_BUILDS': '1', 'UV_CONCURRENT_DOWNLOADS': '4',
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_TERMINAL_PROMPT': '0',
            'AMPLIFIER_HOME': str(GUEST / 'home/amplifier'),
            'AMPLIFIER_WEB_HOME': str(GUEST / 'home/unified'),
            'AMPLIFIER_SESSION_STATE_HOME': str(GUEST / 'home/session-state'),
            'AMPLIFIER_TERMINAL_HOME': str(GUEST / 'home/terminal'),
            'XDG_CONFIG_HOME': str(GUEST / 'home/config'),
            'XDG_CACHE_HOME': str(GUEST / 'cache'), 'XDG_DATA_HOME': str(GUEST / 'home/data')}


def command(argv, *, cwd=None, env=None, timeout=120, capture=False):
    print('+', ' '.join(map(str, argv)), flush=True)
    return subprocess.run(list(map(str, argv)), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          check=True, timeout=timeout, text=True,
                          stdout=subprocess.PIPE if capture else None)


def pins(path):
    data = json.loads(Path(path).read_text())
    require(data['schemaVersion'] == 1 and data['platform'] == 'linux/amd64', 'Unsupported pin format/platform')
    names = set()
    for row in data['repositories']:
        require(re.fullmatch(r'amplifier[-a-z0-9]+', row['name']) and row['name'] not in names, 'Invalid/duplicate repository')
        names.add(row['name'])
        require(re.fullmatch(r'[a-f0-9]{40}', row['commit']), 'Exact repository revision required')
        require(row.get('private', False) == (row['name'] == 'amplifier-module-loop-live'), 'Unexpected private source')
        for package in row['packages']:
            require(not Path(package).is_absolute() and '..' not in Path(package).parts, 'Package path escapes source')
    for key in ('uv', 'core'):
        parsed = urllib.parse.urlsplit(data[key]['url'])
        require(parsed.scheme == 'https' and parsed.netloc == 'files.pythonhosted.org', 'Unexpected binary source')
        require(re.fullmatch(r'[a-f0-9]{64}', data[key]['sha256']), 'Binary hash required')
    return data


def inventory(root, *, boundary=None):
    """Hash contents, including non-Python assets; ignore only Git and bytecode."""
    result = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if '.git' in relative.parts or '__pycache__' in relative.parts or path.suffix == '.pyc':
            continue
        if path.is_symlink():
            require(path.resolve().is_relative_to((boundary or root).resolve()), 'Source symlink escapes root: ' + str(relative))
            result[str(relative)] = {'link': os.readlink(path)}
        elif path.is_file():
            result[str(relative)] = digest(path)
    return result


def download(spec, destination):
    with urllib.request.urlopen(spec['url'], timeout=60) as response, destination.open('xb') as output:
        shutil.copyfileobj(response, output)
    require(digest(destination) == spec['sha256'], 'Downloaded binary differs from pinned SHA256')


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def wheel_metadata(path):
    with zipfile.ZipFile(path) as wheel:
        names = [n for n in wheel.namelist() if n.endswith('.dist-info/METADATA')]
        require(len(names) == 1, 'Ambiguous wheel metadata')
        return Parser().parsestr(wheel.read(names[0]).decode())


def check_direct_requirements(wheels, names, python_version):
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    environment = {**default_environment(), 'python_version': '.'.join(python_version.split('.')[:2]),
                   'python_full_version': python_version}
    for path in wheels:
        info = wheel_metadata(path)
        extras = ('', 'client') if canonical(info['Name']) == 'amplifier-bundle-context-intelligence' else ('',)
        for text in info.get_all('Requires-Dist', []):
            requirement = Requirement(text)
            active = not requirement.marker or any(
                requirement.marker.evaluate({**environment, 'extra': extra}) for extra in extras)
            if active and requirement.url:
                require(canonical(requirement.name) in names, 'Undeclared active direct dependency: ' + text)


def verify_candidate_wheel(wheel_path, candidate):
    """Match every packaged payload byte and the complete declared source set."""
    build = tomllib.loads((candidate / 'pyproject.toml').read_text())['tool']['hatch']['build']['targets']['wheel']
    mappings = {name: name for name in build['packages']}
    mappings.update({dest: source for source, dest in build.get('force-include', {}).items()})
    expected = {}
    for destination, source in mappings.items():
        path = candidate / source
        for item in ([path] if path.is_file() else path.rglob('*')):
            if item.is_file() and '__pycache__' not in item.parts and item.suffix != '.pyc':
                relative = '' if item == path else '/' + item.relative_to(path).as_posix()
                expected[destination + relative] = digest(item)
    with zipfile.ZipFile(wheel_path) as wheel:
        actual = {n: hashlib.sha256(wheel.read(n)).hexdigest() for n in wheel.namelist()
                  if not n.endswith('/') and '.dist-info/' not in n}
    require(actual == expected, 'Candidate wheel payload differs from declared source/resources')
    return actual


def verify_installed():
    """Validate every hashed RECORD entry, reject editables and foreign origins."""
    records = {}
    for dist in metadata.distributions():
        name = canonical(dist.metadata['Name'])
        require(name != 'amplifier-app-cli', 'CLI must not enter this runtime')
        direct = json.loads(dist.read_text('direct_url.json') or '{}')
        require(not direct.get('dir_info', {}).get('editable'), 'Editable distribution: ' + name)
        require(dist.read_text('WHEEL') and dist.read_text('RECORD'), 'Non-wheel distribution: ' + name)
        for row in dist.files or []:
            if row.hash is None:
                continue  # RECORD itself is covered by the before/after inventory.
            path = Path(dist.locate_file(row)).resolve()
            require(path.is_relative_to(GUEST / '.venv'), 'Installed RECORD escapes owned venv')
            require(row.hash.mode == 'sha256', 'Unsupported RECORD digest')
            raw = path.read_bytes()
            actual = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
            require(actual == row.hash.value and len(raw) == row.size, 'Changed installed payload: ' + str(row))
        records[name] = {'version': dist.version, 'direct': direct}
    return records


def extract_candidate(archive, destination):
    destination.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter='data')


def extract_evidence(archive, destination):
    """Only bounded regular evidence files; never restore links or devices."""
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        require(sum(member.size for member in members) <= 64 * 1024 * 1024, 'Evidence exceeds 64 MiB')
        for member in members:
            path = Path(member.name)
            require(not path.is_absolute() and '..' not in path.parts
                    and (member.isfile() or member.isdir()), 'Unsafe evidence member')
        destination.mkdir()
        tar.extractall(destination, members=members, filter='data')


def guest_prepare():
    require(in_container() and os.getuid() == 0, 'Preparation requires the owned container')
    env = clean_env()
    for folder in ('home', 'tmp', 'cache', 'bin', 'wheels', 'repos', 'run', 'evidence'):
        (GUEST / folder).mkdir(exist_ok=True)
    candidate = GUEST / 'candidate'
    extract_candidate(GUEST / 'candidate.tar', candidate)
    data = pins(candidate / 'scripts/default-installed-pins.json')
    identity = json.loads((GUEST / 'input.json').read_text())
    require(digest(GUEST / 'candidate.tar') == identity['archiveSha256'], 'Candidate archive mismatch')
    source_before = inventory(candidate)
    uv_wheel = GUEST / 'uv.whl'
    download(data['uv'], uv_wheel)
    with zipfile.ZipFile(uv_wheel) as wheel:
        members = [n for n in wheel.namelist() if n.endswith('.data/scripts/uv')]
        require(len(members) == 1, 'uv binary absent')
        (GUEST / 'bin/uv').write_bytes(wheel.read(members[0]))
    (GUEST / 'bin/uv').chmod(0o755)
    uv = GUEST / 'bin/uv'
    command([uv, 'python', 'install', data['python']], env=env, timeout=180)
    command([uv, 'venv', '--python', data['python'], GUEST / '.venv'], env=env)
    python = GUEST / '.venv/bin/python'
    packages = [candidate]
    sources = {}
    for row in data['repositories']:
        target = GUEST / 'repos' / row['name']
        remote = 'https://github.com/microsoft/' + row['name']
        if row.get('private'):
            require(digest(GUEST / 'loop.bundle') == identity['loopBundleSha256'], 'Loop bundle mismatch')
            command(['git', 'clone', '--quiet', GUEST / 'loop.bundle', target], env=env)
            command(['git', '-C', target, 'remote', 'set-url', 'origin', remote], env=env)
        else:
            command(['git', 'init', '--quiet', target], env=env)
            command(['git', '-C', target, 'remote', 'add', 'origin', remote], env=env)
            command(['git', '-C', target, 'fetch', '--quiet', '--depth=1', 'origin', row['commit']], env=env)
        command(['git', '-C', target, 'checkout', '--quiet', '--detach', row['commit']], env=env)
        head = command(['git', '-C', target, 'rev-parse', 'HEAD'], env=env, capture=True).stdout.strip()
        require(head == row['commit'], 'Repository pin mismatch')
        sources[row['name']] = {'commit': head, 'files': inventory(target)}
        for relative in row['packages']:
            package = target / relative
            require(package.resolve().is_relative_to(target) and (package / 'pyproject.toml').is_file(), 'Missing pinned package')
            packages.append(package)
    constraints = {}
    for package in tomllib.loads((candidate / 'uv.lock').read_text())['package']:
        if 'registry' in package['source']:
            constraints.setdefault(package['name'], set()).add(package['version'])
    constraint_file = GUEST / 'constraints.txt'
    constraint_file.write_text('\n'.join(f'{name}=={next(iter(values))}' for name, values in sorted(constraints.items()) if len(values) == 1) + '\n')
    build_constraint = GUEST / 'build-constraints.txt'
    build_constraint.write_text(data['buildRequirement'] + '\n')
    for package in packages:
        command([uv, 'build', '--wheel', '--no-sources', '--python', python,
                 '--build-constraint', build_constraint, '--out-dir', GUEST / 'wheels', package], env=env, timeout=120)
    core = GUEST / 'wheels' / Path(urllib.parse.urlsplit(data['core']['url']).path).name
    download(data['core'], core)
    wheels = sorted((GUEST / 'wheels').glob('*.whl'))
    names = [canonical(wheel_metadata(p)['Name']) for p in wheels]
    require(len(names) == len(set(names)) == len(packages) + 1, 'Duplicate/missing distribution build')
    check_direct_requirements(wheels, names, data['python'])
    overrides = GUEST / 'overrides.txt'
    overrides.write_text('\n'.join(f'{name} @ {path.as_uri()}' for name, path in zip(names, wheels)) + '\n')
    app_wheel = wheels[names.index('amplifier-unified')]
    payload = verify_candidate_wheel(app_wheel, candidate)
    command([uv, 'pip', 'install', '--python', python, '--link-mode=copy', '--only-binary', 'amplifier-core',
             '--constraint', constraint_file, '--override', overrides, *wheels, *data['testRequirements'],
             'amplifier-bundle-context-intelligence[client]'], env=env, timeout=240)
    command([uv, 'pip', 'check', '--python', python], env=env)
    freeze = command([uv, 'pip', 'freeze', '--python', python], env=env, capture=True).stdout
    (GUEST / 'evidence/packages.txt').write_text(freeze)
    # Find installed resource path without importing the routing hook in this process.
    query = 'import importlib.util; from pathlib import Path; print(Path(importlib.util.find_spec("amplifier_module_hooks_routing").origin).parents[3] / "routing")'
    resource_path = Path(command([python, '-I', '-c', query], env=env, capture=True).stdout.strip())
    require(resource_path.is_relative_to(GUEST / '.venv') and not resource_path.exists(), 'Unexpected routing resource destination')
    routing_source = GUEST / 'repos/amplifier-bundle-routing-matrix/routing'
    shutil.copytree(routing_source, resource_path)
    require(inventory(resource_path) == inventory(routing_source), 'Routing resource copy mismatch')
    shutil.copytree(candidate / 'tests', GUEST / 'qualification/tests')
    require(inventory(candidate) == source_before, 'Build changed candidate source')
    for row in data['repositories']:
        require(inventory(GUEST / 'repos' / row['name']) == sources[row['name']]['files'], 'Build changed upstream source')
    save(GUEST / 'evidence/preparation.json', {
        **identity, 'pinsSha256': digest(candidate / 'scripts/default-installed-pins.json'),
        'candidatePayload': payload, 'sources': sources,
        'wheels': {p.name: digest(p) for p in wheels},
        'routingResources': {'path': str(resource_path), 'files': inventory(resource_path)},
        'candidateFiles': source_before, 'constraintsSha256': digest(constraint_file),
        'buildConstraintsSha256': digest(build_constraint), 'uvSha256': digest(uv),
        'status': 'prepared_not_accepted'})
    command([python, '-I', __file__, 'guest-attest'], env=env)
    # Git identity reads require matching ownership. The test root filesystem is
    # read-only, so owning a source directory cannot make it writable.
    command(['chown', '-R', '65532:65532', GUEST / 'repos'], env=env)
    (GUEST / 'evidence').rename(GUEST / 'prepared-evidence')
    (GUEST / 'evidence').mkdir()


def guest_attest():
    require(in_container(), 'Attestation requires owned container')
    require(Path(sys.prefix) == GUEST / '.venv', 'Wrong interpreter')
    data = pins(GUEST / 'candidate/scripts/default-installed-pins.json')
    require(sys.version_info[:2] == (3, 13), 'Wrong Python')
    require(metadata.version('amplifier-core') == data['core']['version'], 'Wrong Core')
    from amplifier_core import AmplifierSession
    from amplifier_core._engine import RustSession
    require(AmplifierSession is RustSession, 'Native Core required')
    installed = verify_installed()
    preparation = json.loads((GUEST / 'evidence/preparation.json' if (GUEST / 'evidence/preparation.json').exists()
                              else GUEST / 'prepared-evidence/preparation.json').read_text())
    app = metadata.distribution('amplifier-unified')
    for relative, expected in preparation['candidatePayload'].items():
        require(digest(app.locate_file(relative)) == expected, 'Installed candidate differs from built payload: ' + relative)
    snapshot = {'installed': installed, 'venv': inventory(GUEST / '.venv', boundary=GUEST),
                'python': inventory(GUEST / 'python'),
                'candidate': inventory(GUEST / 'candidate'), 'repos': inventory(GUEST / 'repos'),
                'copiedTests': inventory(GUEST / 'qualification')}
    before = GUEST / 'prepared-evidence/before.json'
    if not before.exists():
        before = GUEST / 'evidence/before.json'
    if before.exists():
        require(snapshot == json.loads(before.read_text()), 'Installed/source/resource bytes changed during qualification')
        save(GUEST / 'evidence/after.json', snapshot)
    else:
        save(before, snapshot)


def desktop_result(path, group, exit_code):
    """Count the fixed safety discriminators separately, retaining failed cases."""
    expected = DESKTOP_CASES[group]
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    rows, counts, identities = [], {}, set()
    for case in cases:
        name = case.get('name', '')
        function = name.split('[', 1)[0]
        require(case.get('classname') == 'tests.test_default_desktop_safety'
                and function in expected and name not in identities, 'Unexpected or duplicate desktop selector')
        identities.add(name)
        counts[function] = counts.get(function, 0) + 1
        outcome = next((kind for kind in ('error', 'failure', 'skipped') if case.find(kind) is not None), 'passed')
        rows.append({'name': name, 'outcome': outcome})
    require(counts == expected, 'Missing desktop cases: ' + repr(counts))
    clean = not any(n.tag in {'error', 'failure', 'skipped'} for n in root.iter())
    clean = clean and all(not int(suite.get(key, '0')) for suite in root.iter('testsuite')
                          for key in ('errors', 'failures', 'skipped'))
    return {'group': group, 'exitCode': exit_code, 'passed': exit_code == 0 and clean, 'cases': rows}


def run_desktop_safety(python, env, evidence):
    """Fixed optional continuation; never reruns or inflates the IC-13 baseline."""
    report = {'baselineExecuted': False, 'groups': {}, 'passed': False}
    for group, functions in DESKTOP_CASES.items():
        junit = evidence / ('desktop-' + group + '.xml')
        require(not junit.exists(), 'Existing desktop evidence; reconcile, never replay')
        try:
            test = subprocess.run([str(python), '-I', '-B', '-m', 'pytest', '-c', '/dev/null',
                '-o', 'asyncio_mode=auto', '--import-mode=prepend', '--rootdir=' + str(GUEST / 'qualification'),
                '-p', 'pytest_asyncio.plugin', '-p', 'pytest_aiohttp.plugin', '-p', 'no:cacheprovider',
                '--basetemp=' + str(GUEST / 'tmp' / group), '--junitxml=' + str(junit), '-q', '-s', '-rA',
                *(str(GUEST / 'qualification/tests/test_default_desktop_safety.py') + '::' + name
                  for name in functions)], env=env, cwd=GUEST / 'run', stdin=subprocess.DEVNULL, timeout=120)
            report['groups'][group] = desktop_result(junit, group, test.returncode)
        except (subprocess.TimeoutExpired, OSError, ValueError, ET.ParseError) as error:
            report['groups'][group] = {'passed': False, 'error': type(error).__name__ + ': ' + str(error)}
        finally:
            save(evidence / 'desktop-safety-result.json', report)
    report['passed'] = all(row['passed'] for row in report['groups'].values())
    save(evidence / 'desktop-safety-result.json', report)
    require(report['passed'], 'Desktop safety check failed or incomplete; see separate group evidence')


def guest_test(desktop_safety=False):
    require(in_container() and os.getuid() == 65532, 'Unprivileged owned test container required')
    env = clean_env()
    env.update(UNIFIED_RUNTIME_PYTHON=str(GUEST / '.venv/bin/python'),
               UNIFIED_BEHAVIOR_REPOS=str(GUEST / 'repos'),
               WARM_FOUNDATION_PATH=str(GUEST / 'repos/amplifier-foundation'),
               WARM_ROUTING_PATH=str(GUEST / 'repos/amplifier-bundle-routing-matrix'))
    python = GUEST / '.venv/bin/python'
    evidence = GUEST / 'evidence'
    shutil.copytree(GUEST / 'prepared-evidence', evidence, dirs_exist_ok=True)
    junit = evidence / 'junit.xml'
    require(not junit.exists(), 'Existing result; reconcile, never replay')
    result = {'passed': False, 'adapterExit': None, 'testExit': None, 'checkExit': None, 'attestation': False,
              'stage': 'desktop-safety' if desktop_safety else 'ic13', 'baselineExecuted': not desktop_safety}
    try:
        command([python, '-I', __file__, 'guest-attest'], env=env)
        command([python, '-I', '-B', '-m', 'pytest', '--noconftest', '--import-mode=importlib',
                 '-c', '/dev/null', '-p', 'no:cacheprovider', '-q',
                 '--basetemp=' + str(GUEST / 'tmp/adapter-pytest'),
                 '--junitxml=' + str(evidence / 'adapter.xml'),
                 GUEST / 'candidate/tests/test_default_installed_adapter.py'],
                env=env, cwd=GUEST / 'run')
        result['adapterExit'] = 0
        if desktop_safety:
            run_desktop_safety(python, env, evidence)
            result['passed'] = True
            return  # finally still attests and saves; no IC-13 execution/count change.
        test = subprocess.run([str(python), '-I', '-B', '-m', 'pytest', '-c', '/dev/null',
            '-o', 'asyncio_mode=auto', '--import-mode=prepend', '--rootdir=' + str(GUEST / 'qualification'),
            '-p', 'pytest_asyncio.plugin', '-p', 'pytest_aiohttp.plugin', '-p', 'no:cacheprovider',
            '--basetemp=' + str(GUEST / 'tmp/pytest'), '--junitxml=' + str(junit), '-q', '-s', '-rA',
            *(str(GUEST / 'qualification/tests' / name) for name in SUITES)],
            env=env, cwd=GUEST / 'run', stdin=subprocess.DEVNULL, timeout=300)
        result['testExit'] = test.returncode
        command([python, '-I', GUEST / 'candidate/scripts/integration_checks.py', 'check',
                 '--lane', 'default-installed', '--junit', junit], env=env)
        result['checkExit'] = 0
        import xml.etree.ElementTree as ET
        require(len(list(ET.parse(junit).iter('testcase'))) == 12, 'Expected all 12 unchanged baseline cases')
        require(test.returncode == 0, 'pytest failed')
        result['passed'] = True
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        # An error here cannot turn a failed/incomplete run into acceptance.
        try:
            command([python, '-I', __file__, 'guest-attest'], env=env)
            result['attestation'] = True
        except BaseException as error:
            result.update(passed=False, attestationError=type(error).__name__ + ': ' + str(error))
            raise
        finally:
            save(evidence / 'result.json', result)


def git(root, *args):
    return command(['git', '-C', root, *args], capture=True).stdout.strip()


def checked_checkout(root, revision=None):
    require(Path(git(root, 'rev-parse', '--show-toplevel')).resolve() == root.resolve(), 'Checkout root required')
    require(not git(root, 'status', '--porcelain', '--untracked-files=all'), 'Clean committed source required')
    head = git(root, 'rev-parse', 'HEAD')
    require(re.fullmatch('[0-9a-f]{40}', head), 'Invalid source revision')
    require(revision is None or head == revision, 'Wrong checkout revision')
    return head


def hosted_run(work, output, loop_source, desktop_safety=False):
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted',
            'This manual adapter requires an ordinary GitHub-hosted runner; it cannot provision a host')
    require(sys.platform == 'linux', 'Linux hosted runner required')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    require(work.is_relative_to(temporary) and output.is_relative_to(temporary)
            and work != temporary and output != temporary
            and not work.is_relative_to(output) and not output.is_relative_to(work),
            'Distinct fresh paths below the hosted runner temporary directory required')
    require(not work.exists() and not output.exists(), 'Fresh work/output directories required; inspect existing receipts')
    data = pins(ROOT / 'scripts/default-installed-pins.json')
    revision = checked_checkout(ROOT)
    loop = next(r for r in data['repositories'] if r.get('private'))
    checked_checkout(loop_source, loop['commit'])
    origin = git(loop_source, 'remote', 'get-url', 'origin').removesuffix('.git')
    require(origin in ('https://github.com/microsoft/amplifier-module-loop-live',
                       'git@github.com:microsoft/amplifier-module-loop-live'), 'Wrong private source origin')
    work.mkdir(parents=True)
    output.mkdir(parents=True)
    operation = uuid.uuid4().hex
    state = {'operation': operation, 'candidate': revision, 'phase': 'source', 'passed': False,
             'stage': 'desktop-safety' if desktop_safety else 'ic13',
             'containers': [], 'image': None, 'cleanup': 'not_started', 'effects': []}
    uncertain = False

    def effect(argv, timeout=120, capture=True):
        nonlocal uncertain
        state['effects'].append({'argv': list(map(str, argv)), 'state': 'attempting'})
        save(output / 'operation.json', state)
        try:
            result = command(argv, timeout=timeout, capture=capture)
        except (subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit, OSError):
            uncertain = True
            state['effects'][-1]['state'] = 'unknown'
            raise
        except subprocess.CalledProcessError as error:
            # Only a completed exec with an ordinary guest exit establishes
            # process failure. Docker transport/create/commit errors may leave
            # daemon effects; retain their identity and do not remove/replay.
            settled = len(argv) > 1 and argv[1] == 'exec' and 0 < error.returncode < 125
            uncertain = not settled
            state['effects'][-1]['state'] = 'failed' if settled else 'unknown'
            raise
        except BaseException:
            state['effects'][-1]['state'] = 'failed'
            raise
        else:
            state['effects'][-1]['state'] = 'completed'
            return result.stdout.strip() if capture else None
        finally:
            save(output / 'operation.json', state)

    def owned(kind, identity):
        info = json.loads(command(['docker', kind, 'inspect', identity], capture=True).stdout)[0]
        labels = info.get('Config', {}).get('Labels') or {}
        require(labels.get(LABEL) == operation, 'Refusing unrelated Docker resource')
        return info

    try:
        archive = work / 'candidate.tar'
        command(['git', '-C', ROOT, 'archive', '--format=tar', '--output', archive, revision])
        bundle = work / 'loop.bundle'
        command(['git', '-C', loop_source, 'bundle', 'create', bundle, 'HEAD'])
        identity = {'candidateRevision': revision, 'archiveSha256': digest(archive),
                    'loopRevision': loop['commit'], 'loopBundleSha256': digest(bundle)}
        save(work / 'input.json', identity)
        effect(['docker', 'pull', '--platform', data['platform'], data['image']], timeout=180, capture=False)
        base = json.loads(command(['docker', 'image', 'inspect', data['image']], capture=True).stdout)[0]
        state['baseImage'] = {'id': base['Id'], 'repoDigests': base.get('RepoDigests'),
                              'retention': 'Shared Docker cache; not owned teardown'}
        prep_name = 'default-prep-' + operation
        prep = effect(['docker', 'create', '--name', prep_name, '--label', LABEL + '=' + operation,
                       '--platform', data['platform'], '--cpus=2', '--memory=4g', '--pids-limit=256',
                       '--security-opt=no-new-privileges', base['Id'], 'sleep', 'infinity'])
        state['containers'].append(prep)
        effect(['docker', 'start', prep])
        effect(['docker', 'exec', prep, 'mkdir', '-p', str(GUEST)])
        for file in (archive, bundle, work / 'input.json'):
            effect(['docker', 'cp', file, prep + ':' + str(GUEST / file.name)])
        # Script bytes come from the same committed archive; no working-tree copy.
        effect(['docker', 'exec', prep, 'sh', '-ec',
                'mkdir -p /opt/defaults/evidence && '
                'apt-get update && apt-get install -y --no-install-recommends python3 python3-packaging git ca-certificates libpam0g && '
                'mkdir /opt/defaults/bootstrap && tar -xf /opt/defaults/candidate.tar -C /opt/defaults/bootstrap '
                'scripts/qualify_default_installed.py && '
                'python3 /opt/defaults/bootstrap/scripts/qualify_default_installed.py guest-prepare'],
               timeout=900, capture=False)
        effect(['docker', 'cp', prep + ':' + str(GUEST / 'prepared-evidence'), output / 'preparation-evidence'])
        state['preparationEvidenceRetained'] = True
        effect(['docker', 'stop', '--time=10', prep])
        image = effect(['docker', 'commit', '--change', 'LABEL ' + LABEL + '=' + operation, prep])
        state['image'] = image
        owned('image', image)
        test = effect(['docker', 'create', '--name', 'default-test-' + operation,
                       '--label', LABEL + '=' + operation, '--network=none', '--user=65532:65532',
                       '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=2', '--memory=4g',
                       '--pids-limit=256', '--read-only',
                       *(arg for folder in ('home', 'tmp', 'run', 'cache', 'evidence')
                         for arg in ('--tmpfs', str(GUEST / folder) + ':rw,uid=65532,gid=65532,mode=0700,size=512m')),
                       image, 'sh', '-c',
                       '/opt/defaults/.venv/bin/python -I /opt/defaults/candidate/scripts/qualify_default_installed.py guest-test'
                       + (' --desktop-safety' if desktop_safety else '') + '; '
                       'code=$?; printf "%s\\n" "$code" > /opt/defaults/evidence/exit-code.tmp; '
                       'mv /opt/defaults/evidence/exit-code.tmp /opt/defaults/evidence/exit-code; exec sleep infinity'])
        state['containers'].append(test)
        info = owned('container', test)
        require(info['HostConfig']['NetworkMode'] == 'none'
                and all(m['Type'] == 'tmpfs' for m in info['Mounts'])
                and info['HostConfig']['ReadonlyRootfs'] and not info['HostConfig'].get('Binds')
                and not info['HostConfig']['Privileged'] and info['Config']['User'] == '65532:65532',
                'Unexpected test container isolation')
        state['testIsolation'] = {key: info['HostConfig'][key] for key in
                                 ('NetworkMode', 'ReadonlyRootfs', 'Memory', 'NanoCpus', 'PidsLimit', 'CapDrop', 'SecurityOpt', 'Tmpfs')}
        state['phase'] = 'testing'
        effect(['docker', 'start', test])
        # Keep the container alive until evidence has been copied: tmpfs data
        # disappears on stop. This waits only for this invocation's exit record.
        exit_code = effect(['docker', 'exec', test, 'sh', '-c',
                            'while [ ! -f /opt/defaults/evidence/exit-code ]; do sleep 1; done; '
                            'cat /opt/defaults/evidence/exit-code'], timeout=420)
        state['testExit'] = exit_code
        require(not owned('container', test)['State']['OOMKilled'], 'Test container exceeded its memory limit')
        with (output / 'tests.log').open('w') as log:
            subprocess.run(['docker', 'logs', test], stdout=log, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, check=True, timeout=30)
        # Docker archive-copy does not read runtime tmpfs mounts. Stream from
        # the still-running process namespace before stopping the container.
        state['evidenceTransfer'] = 'attempting'
        save(output / 'operation.json', state)
        try:
            with (output / 'evidence.tar').open('xb') as archive_output:
                subprocess.run(['docker', 'exec', test, 'tar', '-C', str(GUEST / 'evidence'), '-cf', '-', '.'],
                               stdout=archive_output, stdin=subprocess.DEVNULL, check=True, timeout=60)
            extract_evidence(output / 'evidence.tar', output / 'evidence')
            state['evidenceTransfer'] = 'retained'
        except BaseException:
            uncertain = True
            state['evidenceTransfer'] = 'unknown_or_incomplete'
            raise
        require(exit_code == '0', 'Installed-default container failed; inspect retained evidence')
        require(json.loads((output / 'evidence/result.json').read_text())['passed'], 'No successful qualification receipt')
        state['passed'] = True
        state['phase'] = 'qualified'
    except BaseException as error:
        state.update(phase='unknown' if uncertain else 'failed', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        if uncertain:
            state['cleanup'] = 'unresolved; inspect operation labels/names before any replay or removal'
        else:
            try:
                for container in reversed(state['containers']):
                    owned('container', container)
                    # Retain partial preparation evidence before any removal.
                    if container == state['containers'][0] and not state.get('preparationEvidenceRetained'):
                        require(owned('container', container)['State']['Running'],
                                'Stopped preparation without retained evidence; preserve for reconciliation')
                        for folder in ('evidence', 'prepared-evidence'):
                            found = subprocess.run(['docker', 'exec', container, 'test', '-d', str(GUEST / folder)],
                                stdin=subprocess.DEVNULL, timeout=30)
                            if found.returncode == 0:
                                effect(['docker', 'cp', container + ':' + str(GUEST / folder),
                                        output / ('preparation-' + folder)])
                            else:
                                require(found.returncode == 1, 'Cannot inspect preparation evidence')
                    # Capture preparation diagnostics even when no test container exists.
                    with (output / (container[:12] + '.log')).open('w') as log:
                        subprocess.run(['docker', 'logs', container], stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, timeout=30, check=True)
                    effect(['docker', 'rm', '--force', container])
                if state['image']:
                    owned('image', state['image'])
                    effect(['docker', 'image', 'rm', state['image']])
                remaining = command(['docker', 'ps', '-aq', '--filter', 'label=' + LABEL + '=' + operation], capture=True).stdout.strip()
                images = command(['docker', 'images', '-q', '--filter', 'label=' + LABEL + '=' + operation], capture=True).stdout.strip()
                require(not remaining and not images, 'Owned Docker resources remain')
                state['cleanup'] = 'verified_absent'
            except BaseException as error:
                state.update(cleanup='unresolved', cleanupError=type(error).__name__ + ': ' + str(error), passed=False)
        save(output / 'operation.json', state)
    require(state['cleanup'] == 'verified_absent' and state['passed'], 'Qualification or cleanup incomplete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'guest-prepare', 'guest-attest', 'guest-test'])
    parser.add_argument('--work', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--loop-source', type=Path)
    parser.add_argument('--desktop-safety', action='store_true',
                        help='Run only the separate fixed 22+3 desktop proof, not the IC-13 baseline')
    args = parser.parse_args()
    if args.command == 'run':
        require(args.work and args.output and args.loop_source, 'run requires --work, --output and --loop-source')
        hosted_run(args.work.resolve(), args.output.resolve(), args.loop_source.resolve(), args.desktop_safety)
    elif args.command == 'guest-test':
        guest_test(args.desktop_safety)
    else:
        require(not args.desktop_safety, '--desktop-safety applies only to run/guest-test')
        {'guest-prepare': guest_prepare, 'guest-attest': guest_attest}[args.command]()


if __name__ == '__main__':
    main()
