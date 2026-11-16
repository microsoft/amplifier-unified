"""Fixed FUTURE candidate execution driver from nominated accepted H.

Never called by phase-one PR checks. Runs only on a disposable hosted Docker
runner, with no repository/control token in its process environment. Candidate
build hooks/imports are code execution under reviewed first-party testing trust.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import selectors
import signal
import subprocess
import sys
import tomllib
import time
import urllib.request

from candidate_native_protocol import REPOSITORY, KIND, require, validate_selection, save

HERE = Path(__file__).resolve().parent
DTU = '5235833098d9a630791452a46558d92f58af0389'
IMAGE = 'docker.io/library/python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f'


def command(argv, *, cwd, env=None, timeout=600):
    # Command words/paths are fixed by H; C supplies no commands/profiles/mounts.
    return subprocess.run(argv, cwd=cwd, env=env, check=True, timeout=timeout,
                          text=True, capture_output=True)

def bounded_build(argv, *, cwd, env, timeout, stdout_path, stderr_path):
    """Drain both streams with bounded retained tails; terminate/reap on timeout."""
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    tails = {process.stdout: bytearray(), process.stderr: bytearray()}
    deadline, truncated, complete = time.monotonic() + timeout, False, False
    try:
        with selectors.DefaultSelector() as poller:
            for stream in tails: poller.register(stream, selectors.EVENT_READ)
            while poller.get_map():
                require(time.monotonic() < deadline, 'Candidate build deadline exceeded; inspect original attempt')
                for key, _ in poller.select(min(.2, max(0, deadline - time.monotonic()))):
                    raw = os.read(key.fileobj.fileno(), 65536)
                    if not raw:
                        poller.unregister(key.fileobj)
                    else:
                        tail = tails[key.fileobj]
                        tail.extend(raw)
                        if len(tail) > 8 * 1024 ** 2:
                            del tail[:-8 * 1024 ** 2]
                            truncated = True
        code = process.wait(timeout=max(1, deadline - time.monotonic()))
        require(not truncated, 'Candidate command output exceeded retained bound; not a passing prerequisite')
        if code:
            raise subprocess.CalledProcessError(code, argv)
        complete = True
        return tails[process.stdout].decode(errors='replace')
    finally:
        if not complete or process.poll() is None:
            # A child may still own inherited pipes after the leader exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        Path(stdout_path).write_bytes(tails[process.stdout])
        Path(stderr_path).write_bytes(tails[process.stderr])
        process.stdout.close(); process.stderr.close()


def current_run(selection):
    # Public, credential-free observation; no token crosses from issuer job.
    def read(path):
        url = f"https://api.github.com/repos/{REPOSITORY}/" + path
        request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'candidate-native-test'})
        with urllib.request.urlopen(request, timeout=20) as stream:
            raw = stream.read(1024 ** 2 + 1)
        require(len(raw) <= 1024 ** 2, 'Run observation exceeds bound')
        return json.loads(raw)
    value = read(f"actions/runs/{selection['run']}")
    require(value['head_repository']['full_name'] == REPOSITORY
            and value['head_sha'] == selection['harness'] and value['run_attempt'] == selection['attempt']
            and value['status'] == 'in_progress' and value['conclusion'] is None, 'Run cancelled, changed or superseded')


def context(root, candidate, output):
    root, candidate, output = Path(root).resolve(), Path(candidate).resolve(), Path(output).resolve()
    admission = json.loads((output / 'admission.json').read_text())
    selection = admission['selection']
    validate_selection(selection)
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'Hosted Linux only; flags alone grant nothing')
    require(not any(os.environ.get(k) for k in ('GH_TOKEN', 'GITHUB_TOKEN', 'ACTIONS_ID_TOKEN_REQUEST_TOKEN')),
            'Control/repository credentials must not enter candidate execution')
    require(command(['git', 'rev-parse', 'HEAD'], cwd=root).stdout.strip() == selection['harness'], 'Harness checkout changed')
    require(command(['git', 'rev-parse', 'HEAD'], cwd=candidate).stdout.strip() == selection['candidate']
            and command(['git', 'rev-parse', 'HEAD^{tree}'], cwd=candidate).stdout.strip() == selection['candidateTree'], 'Candidate checkout changed')
    for name, expected in admission['qualifiers'].items():
        require(hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, 'Harness bytes changed')
    current_run(selection)
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/pulls/{selection['pr']}",
        headers={'User-Agent': 'candidate-native-test'})
    with urllib.request.urlopen(request, timeout=20) as stream:
        body = stream.read(1024 ** 2 + 1)
    require(len(body) <= 1024 ** 2, 'Candidate observation exceeds bound')
    pr = json.loads(body)
    require(pr['head']['sha'] == selection['candidate'] and pr['state'] == 'open'
            and pr['head']['repo']['full_name'] == pr['base']['repo']['full_name'] == REPOSITORY,
            'Candidate PR moved or repository changed')
    validate_selection(selection)  # All public observations may block.
    return admission


def build(root, candidate, output, core):
    admission = context(root, candidate, output)
    root, candidate, output, core = map(lambda x: Path(x).resolve(), (root, candidate, output, core))
    require(not (output / 'build-started.json').exists(), 'Inspect original build attempt; never replay')
    save(output / 'build-started.json', {'kind': KIND, 'selection': admission['selection']})
    temp = Path(os.environ['RUNNER_TEMP']).resolve() / 'candidate-native-owned'
    temp.mkdir(mode=0o700)
    prefix = temp / 'installed'
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'GH_TOKEN', 'GITHUB_TOKEN')}
    records = []
    def run(argv, cwd=candidate, timeout=900, custom=None):
        try:
            text = bounded_build(argv, cwd=cwd, env=custom or env, timeout=timeout,
                stdout_path=output / ('build-' + str(len(records)) + '.stdout'),
                stderr_path=output / ('build-' + str(len(records)) + '.stderr'))
        except BaseException:
            records.append({'command': list(map(str, argv)), 'outcome': 'failed-or-interrupted'})
            save(output / 'build-commands.json', records)
            raise
        records.append({'command': list(map(str, argv)), 'returncode': 0})
        save(output / 'build-commands.json', records)
        return text
    # No private runtime extras/dev group, overrides, live app, or mutable graph.
    run(['uv', 'sync', '--locked', '--no-dev', '--no-editable'])
    run(['uv', 'build', '--wheel', '--out-dir', str(output / 'dist')])
    wheel, = (output / 'dist').glob('*.whl')
    shutil.copyfile(wheel, output / wheel.name)
    freeze = run(['uv', 'pip', 'freeze', '--python', str(candidate / '.venv/bin/python')])
    requirements = output / 'resolved-requirements.txt'
    requirements.write_text('\n'.join(x for x in freeze.splitlines() if not x.startswith(('#', '-e ', 'amplifier-unified'))) + '\n')
    run(['uv', 'venv', '--python', '3.13', str(prefix)])
    python = str(prefix / 'bin/python')
    run(['uv', 'pip', 'install', '--python', python, '--no-deps', '-r', str(requirements)])
    run(['uv', 'pip', 'install', '--python', python, '--no-deps', str(output / wheel.name)])
    packages = tomllib.loads((root / 'uv.lock').read_text())['package']
    test_tools = [name + '==' + next(p['version'] for p in packages if p['name'] == name)
                  for name in ('pytest', 'pytest-asyncio')]
    run(['uv', 'pip', 'install', '--python', python, '--only-binary', ':all:', *test_tools])
    run(['uv', 'pip', 'check', '--python', python])
    require(command(['git', 'rev-parse', 'HEAD'], cwd=core).stdout.strip() == DTU, 'DTU source changed')
    tool_id = hashlib.sha256(DTU.encode()).hexdigest()[:24]
    core_prefix = temp / 'app/smart-tools/installs' / tool_id / 'venv'
    run(['uv', 'sync', '--locked', '--no-dev', '--no-default-groups', '--project', str(core)], cwd=core,
        custom={**env, 'UV_PROJECT_ENVIRONMENT': str(core_prefix)})
    # This is the fixture's sole future backend setup, not an agent fallback.
    run(['docker', 'pull', IMAGE], timeout=300)
    save(output / 'installed.json', {'kind': KIND, 'prefix': str(prefix), 'python': python,
        'corePython': str(core_prefix / 'bin/python'), 'coreCommit': DTU, 'image': IMAGE,
        'wheelFile': wheel.name, 'wheelSha256': hashlib.sha256((output / wheel.name).read_bytes()).hexdigest(),
        'harness': str(root), 'candidate': str(candidate), 'ownedRoot': str(temp), 'sourceBlobs': admission['sourceBlobs']})


def run_profiles(root, candidate, output):
    admission = context(root, candidate, output)
    root, output = Path(root).resolve(), Path(output).resolve()
    installed = json.loads((output / 'installed.json').read_text())
    require(not (output / 'execution-started.json').exists(), 'Inspect original native attempt; never replay')
    save(output / 'execution-started.json', {'kind': KIND, 'selection': admission['selection']})
    # Only trusted H test source/scripts are copied; C test code cannot substitute.
    tests = Path(installed['ownedRoot']) / 'harness'
    tests.mkdir(mode=0o700)
    for name in ('test_candidate_native_runtime.py',):
        shutil.copyfile(root / 'tests' / name, tests / name)
    bootstrap = ('import sys,pytest;sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[2]);'
                 'raise SystemExit(pytest.main(sys.argv[3:]))')
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'GH_TOKEN', 'GITHUB_TOKEN')}
    env.update(CANDIDATE_NATIVE_OUTPUT=str(output))
    with (output / 'pytest.stdout').open('w') as out, (output / 'pytest.stderr').open('w') as err:
        result = subprocess.run([installed['python'], '-I', '-c', bootstrap, str(root / 'scripts'),
            str(root / 'tests/fixtures'), '-c', '/dev/null', '-o', 'asyncio_mode=auto', '--rootdir=.',
            '--import-mode=importlib', '-v', '-s', '--tb=short', 'test_candidate_native_runtime.py',
            '--junitxml=' + str(output / 'junit.xml')], cwd=tests, env=env, stdout=out, stderr=err, timeout=2100)
    from candidate_native_protocol import verify_cases
    require(result.returncode == 0, 'Native cases failed; retain originals and cleanup custody')
    verify_cases(json.loads((output / 'report.json').read_text()), (output / 'junit.xml').read_bytes(), admission['selection'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('preflight', 'build', 'run'))
    for k in ('harness', 'candidate', 'output'):
        parser.add_argument('--' + k, required=True)
    parser.add_argument('--core')
    a = parser.parse_args()
    if a.operation == 'preflight': context(a.harness, a.candidate, a.output)
    elif a.operation == 'build':
        require(a.core is not None, 'Pinned DTU checkout required')
        build(a.harness, a.candidate, a.output, a.core)
    else: run_profiles(a.harness, a.candidate, a.output)


if __name__ == '__main__':
    main()
