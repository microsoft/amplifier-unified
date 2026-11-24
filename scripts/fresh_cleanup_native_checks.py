"""Ordinary native pytest suites on one disposable hosted runner, not release eligibility."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SUITES = {'fresh-cleanup-native': ('test_environment_fresh_cleanup_native.py',
                                  ['test_native_unknown_cleanup_keeps_history_and_preserves_current_files']),
          'prepared-runtime-native': ('test_prepared_preset_native.py',
              ['test_native_default_prepared_preset_runtime[' + p + ']' for p in ('python-project', 'browser-test')]),
          'prepared-default-native': ('test_prepared_preset_native.py',
              ['test_native_current_prepared_preset_defaults[' + p + ']' for p in ('python-project', 'browser-test')]),
          'thin-docker-native': ('test_thin_docker_smart_tool.py',
              ['test_real_smart_tool_two_owners_dirty_worktree_wheel_and_preview'])}

# The thin fixture has two tiny synthetic sources and one wheel, not arbitrary
# project exports. Bounds apply even to failure/timeout evidence; no extraction.
THIN_FILE_BYTES = 64 * 1024**2
THIN_TOTAL_BYTES = 256 * 1024**2
THIN_FILES = 128
THIN_OWNER_FILES = {'source.tar', 'snapshot.json', 'source-ready.json',
                    'backend-binding.json', 'backend-launched.json', 'backend-removed.json'}
THIN_REMOVAL_FILES = {'artifacts.tar', 'manifest.json', 'retention.json',
                      'removal-started.json', 'destroy-dispatched.json',
                      'destroy-confirmed.json', 'removal-failed.json'}


def run(args, *, cwd=ROOT, **kwargs):
    print('+', ' '.join(map(str, args)), flush=True)
    return subprocess.run(args, cwd=cwd, check=True, timeout=kwargs.pop('timeout', 600), **kwargs)


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True, timeout=30).strip()


def record(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def constants(source):
    result = {}
    for node in ast.parse((source / 'amplifier_web/task_environments.py').read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in {'DTU_COMMIT', 'APPROVED_IMAGE', 'REPOSITORY'}:
                result[node.targets[0].id] = ast.literal_eval(node.value)
    return result


def verify_cases(code, xml, suite):
    cases = ET.fromstring(xml).findall('.//testcase')
    assert code == 0 and [c.get('name') for c in cases] == SUITES[suite][1], 'Native cases missing, duplicated or failed'
    assert all(not any(c.find(tag) is not None for tag in ('failure', 'error', 'skipped')) for c in cases), 'Native cases did not pass'
    return len(cases)


def retain_thin(root, output):
    """Copy only this fresh fixture's bounded evidence, never act on its guests."""
    destination = output / 'thin-evidence'
    destination.mkdir(mode=0o700)
    receipt = {'complete': False, 'files': [], 'errors': [],
               'meaning': 'Captured files only; not cleanup or executor settlement.',
               'limits': {'fileBytes': THIN_FILE_BYTES, 'totalBytes': THIN_TOTAL_BYTES, 'files': THIN_FILES}}
    total, attempted, visited = 0, 0, 0
    try:
        # fwalk supplies directory descriptors and does not follow directory
        # links. Only these exact host-owned levels are visited, never a venv,
        # guest export, copied Compose input, Git folder or application settings.
        if root.is_symlink():
            raise ValueError('Linked proof root')
        for folder, dirs, files, fd in os.fwalk(root, follow_symlinks=False):
            parts = Path(folder).relative_to(root).parts
            visited += 1
            if visited > 16:
                raise ValueError('Too many evidence directories')
            if not parts:
                allowed_dirs, allowed_files = {'app'}, {'native-result.json'}
            elif parts == ('app',):
                allowed_dirs, allowed_files = {'workspace-resources'}, set()
            elif parts == ('app', 'workspace-resources'):
                allowed_dirs, allowed_files = {'owned'}, set()
            elif len(parts) == 3:
                allowed_dirs = {name for name in dirs if re.fullmatch('[a-f0-9]{64}', name)}
                allowed_files = set()
                if len(allowed_dirs) > 2:
                    raise ValueError('Too many thin owners')
            elif len(parts) == 4:
                allowed_dirs = {name for name in dirs if re.fullmatch('[a-f0-9-]{36}', name)}
                allowed_files = set()
                if len(allowed_dirs) > 1:
                    raise ValueError('Too many environments for one thin owner')
            elif len(parts) == 5:
                allowed_dirs = {name for name in dirs if re.fullmatch('retained-[a-f0-9]{64}', name)}
                allowed_files = THIN_OWNER_FILES
                if len(allowed_dirs) > 1:
                    raise ValueError('Repeated removal evidence')
            else:
                allowed_dirs, allowed_files = set(), THIN_REMOVAL_FILES
            dirs[:] = sorted(set(dirs) & allowed_dirs)
            for name in dirs:
                if not stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                    raise ValueError('Linked evidence directory')
            for name in sorted(set(files) & allowed_files):
                relative = Path(*parts, name)
                attempted += 1
                if attempted > THIN_FILES:
                    raise ValueError('Too many evidence files')
                try:
                    source_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    with os.fdopen(source_fd, 'rb') as source:
                        before = os.fstat(source.fileno())
                        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                                or before.st_size > THIN_FILE_BYTES or total + before.st_size > THIN_TOTAL_BYTES):
                            raise ValueError('Evidence type or size bound')
                        # Failed captures still consume the copying budget.
                        total += before.st_size
                        target = destination / relative
                        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        out_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        digest, size = hashlib.sha256(), 0
                        with os.fdopen(out_fd, 'wb') as stream:
                            while block := source.read(min(1024 * 1024, before.st_size + 1 - size)):
                                size += len(block)
                                if size > before.st_size:
                                    raise ValueError('Evidence grew during capture')
                                digest.update(block)
                                stream.write(block)
                            stream.flush()
                            os.fsync(stream.fileno())
                        after = os.fstat(source.fileno())
                        if size != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                            raise ValueError('Evidence changed during capture')
                    receipt['files'].append({'path': relative.as_posix(), 'bytes': size, 'sha256': digest.hexdigest()})
                except (OSError, ValueError) as exc:
                    receipt['errors'].append({'path': relative.as_posix(), 'reason': type(exc).__name__})
        receipt['complete'] = not receipt['errors']
    except (OSError, ValueError) as exc:
        receipt['errors'].append({'reason': type(exc).__name__})
    finally:
        fd = os.open(destination / 'capture.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(receipt, stream, sort_keys=True, indent=2)
    return receipt


def verify_thin(root, output, captured):
    """Require the whole journey AND two original removals with actual absence."""
    assert captured['complete'], captured
    files = {item['path']: item for item in captured['files']}
    report_file = files['native-result.json']
    assert report_file['bytes'] <= 4 * 1024**2, 'Oversized thin report'
    report = json.loads((output / 'thin-evidence/native-result.json').read_text())
    assert report['result'] == 'passed'
    assert all(report[key] is True for key in ('journeyPassed', 'sourceUnchanged', 'cleanupComplete'))
    entries = report['environments']
    assert len(entries) == 2
    assert len({entry['owner'] for entry in entries}) == 2
    for key in ('id', 'universeId'):
        assert len({entry['resource'][key] for entry in entries}) == 2
    for entry in entries:
        row, absence = entry['resource'], entry['absence']
        assert entry['state'] == row['status'] == 'removed'
        request = row['lastOperation']
        assert (request['state'], request['action'], request['sessionId'], request['resourceId']) == (
            'completed', 'workspace.environments.remove', entry['owner'], row['id'])
        proof = request['cleanupExecution']
        assert proof['kind'] == 'qualification-test-checkout' and proof['status'] == 'permit-recorded'
        removed, binding = request['result'], request['guestRemovalBinding']
        assert removed['ok'] is True and removed['status'] == 'removed'
        assert removed['retention']['verified'] is True
        assert absence['ok'] is True and absence['status'] == 'absent'
        observed = absence['observation']
        assert observed['absenceConfirmed'] is True and observed['ownership'] == 'absent'
        assert {key: observed[key] for key in ('rootOwner', 'operationId', 'universeId', 'backendIdentity')} == {
            'rootOwner': entry['owner'], 'operationId': binding['launchOperationId'],
            'universeId': row['universeId'], 'backendIdentity': binding['backendIdentity']}
        folder = Path(row['retention']['path']).relative_to(root)
        assert files[(folder / 'source.tar').as_posix()]['sha256'] == row['snapshot']['archive_sha256']
        archives = removed['archives']
        assert len(archives) == 1 and archives[0]['source'] == '/artifacts'
        for archive in archives:
            relative = Path(archive['destination']).relative_to(root)
            assert files[relative.as_posix()]['sha256'] == archive['sha256']
        retained = folder / ('retained-' + hashlib.sha256(request['requestId'].encode()).hexdigest())
        for name in ('manifest.json', 'retention.json', 'removal-started.json',
                     'destroy-dispatched.json', 'destroy-confirmed.json'):
            assert (retained / name).as_posix() in files
    return {'owners': 2, 'removed': 2, 'absenceConfirmed': 2}


def execute_cases(python, harness, targets, env, output, suite, root):
    bootstrap = 'import sys;sys.path.insert(0,sys.argv.pop(1));import pytest;raise SystemExit(pytest.main(sys.argv[1:]))'
    try:
        with (output / 'pytest.log').open('w') as stream:
            result = subprocess.run([str(python), '-I', '-c', bootstrap, str(harness), '-p', 'pytest_asyncio.plugin',
                '-c', '/dev/null', '--rootdir=' + str(harness), '-p', 'no:cacheprovider',
                '-o', 'asyncio_mode=auto', '-v', '-s', '--tb=short', *targets,
                '--junitxml=' + str(output / 'junit.xml')], cwd=harness, env=env, stdout=stream,
                stderr=subprocess.STDOUT, timeout=2100)
    finally:
        if suite == 'thin-docker-native':
            # Also runs after pytest failure/timeout. Capture is not permission
            # to retry or clean up an uncertain surviving helper.
            captured = retain_thin(root, output)
    count = verify_cases(result.returncode, (output / 'junit.xml').read_bytes(), suite)
    custody = verify_thin(root, output, captured) if suite == 'thin-docker-native' else None
    return {'cases': count, 'returncode': result.returncode, **({'custody': custody} if custody else {})}


def case_environment(root, output, core_environment, suite):
    env = {k: v for k, v in os.environ.items() if k not in (
        'PYTHONPATH', 'VIRTUAL_ENV', 'GH_TOKEN', 'GITHUB_TOKEN', 'UNIFIED_DTU_PROOF_ROOT')}
    env.update(CLEANUP_NATIVE_ROOT=str(root), CLEANUP_NATIVE_OUTPUT=str(output),
               UNIFIED_DTU_PYTHON=str(core_environment / 'bin/python'),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    if suite == 'thin-docker-native':
        env['UNIFIED_DTU_PROOF_ROOT'] = str(root)
        env.pop('UNIFIED_REALITY_CHECK_PYTHON', None)
    return env


def main():
    suite = os.environ['CHECK_SUITE']
    filename, names = SUITES[suite]  # Reject unknown scope before any native effect.
    targets = [filename + '::' + name for name in names]
    output = ROOT / 'output' / suite
    output.mkdir(parents=True, exist_ok=True)
    source = Path(os.environ.get('NATIVE_CANDIDATE_ROOT', str(ROOT))).resolve()
    root = Path(os.environ['CLEANUP_NATIVE_ROOT']).resolve()
    assert root.parent == Path(os.environ['RUNNER_TEMP']).resolve() and not root.exists()
    root.mkdir(mode=0o700)
    assert sys.platform == 'linux' and os.environ['GITHUB_ACTIONS'] == 'true'
    assert git(ROOT, 'rev-parse', 'HEAD') == os.environ['GITHUB_SHA']
    selected = {'suite': suite, 'fullSuite': False, 'targets': ['tests/' + target for target in targets],
                'revision': git(ROOT, 'rev-parse', 'HEAD'), 'tree': git(ROOT, 'rev-parse', 'HEAD^{tree}'),
                'candidate': git(source, 'rev-parse', 'HEAD'), 'candidateTree': git(source, 'rev-parse', 'HEAD^{tree}'),
                'runId': os.environ['GITHUB_RUN_ID'], 'runAttempt': os.environ['GITHUB_RUN_ATTEMPT'],
                'workflowSha': os.environ.get('GITHUB_WORKFLOW_SHA'), 'native': 'unrun'}
    record(output / 'selection.json', selected)
    assert not os.environ.get('CANDIDATE_REF') or selected['candidate'] == os.environ['CANDIDATE_REF']
    fixed = constants(source)
    record(output / 'source.json', {**selected, **fixed, 'trust': 'reviewed first-party checkout test; NOT official release qualification'})
    core = ROOT / '.ci/dtu-core'
    assert git(core, 'rev-parse', 'HEAD') == fixed['DTU_COMMIT']
    run(['docker', 'version']); run(['docker', 'compose', 'version'])
    # The selected app's lock owns its dependencies. No development/provider extras.
    run(['uv', 'sync', '--locked', '--no-dev', '--no-editable'], cwd=source, timeout=900)
    dist = output / 'dist'
    run(['uv', 'build', '--wheel', '--out-dir', str(dist)], cwd=source)
    wheel, = dist.glob('*.whl')
    packages = subprocess.check_output(['uv', 'pip', 'freeze', '--python', str(source / '.venv/bin/python')], text=True)
    requirements = output / 'requirements.txt'
    requirements.write_text('\n'.join(p for p in packages.splitlines() if not p.startswith(('#', '-e ', 'amplifier-unified'))) + '\n')
    prefix = root / 'installed'
    run(['uv', 'venv', '--python', '3.13', str(prefix)])
    python = prefix / 'bin/python'
    run(['uv', 'pip', 'install', '--python', str(python), '--no-deps', '-r', str(requirements)], timeout=900)
    run(['uv', 'pip', 'install', '--python', str(python), '--no-deps', str(wheel)])
    packages = tomllib.loads((ROOT / 'uv.lock').read_text())['package']
    tools = [name + '==' + next(p['version'] for p in packages if p['name'] == name) for name in ('pytest', 'pytest-asyncio')]
    run(['uv', 'pip', 'install', '--python', str(python), '--only-binary', ':all:', *tools])
    run(['uv', 'pip', 'check', '--python', str(python)])
    identity = hashlib.sha256(fixed['DTU_COMMIT'].encode()).hexdigest()[:24]
    core_environment = root / 'app/smart-tools/installs' / identity / 'venv'
    run(['uv', 'sync', '--locked', '--no-dev', '--no-default-groups', '--project', str(core)],
        env={**os.environ, 'UV_PROJECT_ENVIRONMENT': str(core_environment)}, timeout=900)
    run(['docker', 'pull', fixed['APPROVED_IMAGE']], timeout=300)
    # One ordinary installed boundary: selected members, wheel and actual imports.
    code = '''import base64,csv,io,importlib,importlib.metadata as m,hashlib,json,sys,zipfile
from pathlib import Path
wheel,source=map(Path,sys.argv[1:]);d=m.distribution('amplifier-unified');out={}
assert json.loads(d.read_text('direct_url.json'))['url']==wheel.as_uri()
with zipfile.ZipFile(wheel) as z:
 records=dict((r[0],r[1:]) for r in csv.reader(io.StringIO(z.read(next(n for n in z.namelist() if n.endswith('.dist-info/RECORD'))).decode())))
 for n in ('task_environments','test_environment_worker','test_environment_profiles','test_environment_preparation','test_environment_initialize_guest','environment_cleanup_execution','environment_cleanup_launcher','environment_retention'):
  p=Path(importlib.import_module('amplifier_web.'+n).__file__).resolve();member='amplifier_web/'+n+'.py'
  assert p.is_relative_to(Path(sys.prefix)) and p.read_bytes()==z.read(member)==(source/member).read_bytes()
  assert records[member]==['sha256='+base64.urlsafe_b64encode(hashlib.sha256(p.read_bytes()).digest()).decode().rstrip('='),str(len(p.read_bytes()))]
  out[n]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
print(json.dumps({'prefix':sys.prefix,'wheelSha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'members':out},indent=2))'''
    with (output / 'installed-origins.json').open('w') as stream:
        run([str(python), '-I', '-c', code, str(wheel), str(source)], cwd=root, stdout=stream)
    harness = root / 'harness'; harness.mkdir()
    for name in (filename, 'native_cleanup_fixture.py'):
        shutil.copyfile(ROOT / 'tests' / name, harness / name)
    record(output / 'harness.json', {'revision': selected['revision'],
        'files': {name: hashlib.sha256((harness / name).read_bytes()).hexdigest()
                  for name in (filename, 'native_cleanup_fixture.py')}})
    env = case_environment(root, output, core_environment, suite)
    result = execute_cases(python, harness, targets, env, output, suite, root)
    record(output / 'result.json', {**selected, 'native': 'passed', 'passed': True, **result})


if __name__ == '__main__':
    main()
