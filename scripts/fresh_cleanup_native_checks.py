"""Ordinary native pytest suites on one disposable hosted runner, not release eligibility."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SUITES = {'fresh-cleanup-native': ('test_environment_fresh_cleanup_native.py',
                                  ['test_native_unknown_cleanup_keeps_history_and_preserves_current_files']),
          'prepared-runtime-native': ('test_prepared_preset_native.py',
              ['test_native_default_prepared_preset_runtime[' + p + ']' for p in ('python-project', 'browser-test')])}


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


def main():
    suite = os.environ['CHECK_SUITE']
    filename, _ = SUITES[suite]
    output = ROOT / 'output' / suite
    output.mkdir(parents=True, exist_ok=True)
    source = Path(os.environ.get('NATIVE_CANDIDATE_ROOT', str(ROOT))).resolve()
    root = Path(os.environ['CLEANUP_NATIVE_ROOT']).resolve()
    assert root.parent == Path(os.environ['RUNNER_TEMP']).resolve() and not root.exists()
    root.mkdir(mode=0o700)
    assert sys.platform == 'linux' and os.environ['GITHUB_ACTIONS'] == 'true'
    assert git(ROOT, 'rev-parse', 'HEAD') == os.environ['GITHUB_SHA']
    selected = {'suite': suite, 'fullSuite': False, 'targets': ['tests/' + filename],
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
 for n in ('task_environments','test_environment_worker','environment_cleanup_execution','environment_cleanup_launcher','environment_retention'):
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
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'VIRTUAL_ENV', 'GH_TOKEN', 'GITHUB_TOKEN')}
    env.update(CLEANUP_NATIVE_ROOT=str(root), CLEANUP_NATIVE_OUTPUT=str(output),
               UNIFIED_DTU_PYTHON=str(core_environment / 'bin/python'),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    bootstrap = 'import sys;sys.path.insert(0,sys.argv.pop(1));import pytest;raise SystemExit(pytest.main(sys.argv[1:]))'
    with (output / 'pytest.log').open('w') as stream:
        result = subprocess.run([str(python), '-I', '-c', bootstrap, str(harness), '-p', 'pytest_asyncio.plugin',
            '-c', '/dev/null', '--rootdir=' + str(harness), '-p', 'no:cacheprovider',
            '-o', 'asyncio_mode=auto', '-v', '-s', '--tb=short', filename,
            '--junitxml=' + str(output / 'junit.xml')], cwd=harness, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=2100)
    count = verify_cases(result.returncode, (output / 'junit.xml').read_bytes(), suite)
    record(output / 'result.json', {**selected, 'native': 'passed', 'passed': True, 'cases': count, 'returncode': result.returncode})


if __name__ == '__main__':
    main()
