"""Fixed isolated-CI preparation for real omitted-ref worker qualification.

Acquire the canonical runtime graph once, retaining its real Git/registry source
policy and exact lock. Independent generation environments reuse only uv's cache,
not this interpreter's installed packages. Private logs/source stay on the runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
LOOP = 'amplifier-module-loop-live'
FAILURE_CLASSIFIER_VERSION = 1
FAILURE_SCAN_BYTES = 64 * 1024
# Fixed diagnostic signatures, not root-cause proof. Never return matched text.
FAILURE_SIGNATURES = {
    'manifest': r'^(?:error: |  Caused by: )?(?:TOML parse error|Failed to parse `pyproject\.toml`)',
    'dependency-conflict': r'^[ \t]*(?:error: |\u00d7 )?No solution found when resolving dependencies',
    'transport-auth': r'^(?:fatal: (?:Authentication failed|could not read Username|unable to access)|error: (?:Failed to download|Failed to fetch)|Permission denied \(publickey\))',
    'unavailable-wheel': r'^error: (?:No binary distribution found|No compatible wheel found)',
    'build': r'^[ \t]*(?:error: |\u00d7 )?(?:Failed to build |The build backend returned an error)',
}


def command_log_facts(log, returncode):
    """Hash the full log with bounded memory; classify only complete small logs.

    Unrecognized, ambiguous, oversized or undecodable output stays unknown.
    Only fixed category strings leave this function, never paths or source text.
    An unknown command outcome is not classified from possibly partial output.
    """
    sha, size = hashlib.sha256(), 0
    try:
        with log.open('rb') as stream:
            sample = stream.read(FAILURE_SCAN_BYTES + 1)
            sha.update(sample)
            size += len(sample)
            for chunk in iter(lambda: stream.read(FAILURE_SCAN_BYTES), b''):
                sha.update(chunk)
                size += len(chunk)
    except OSError:
        # Diagnostic IO must not replace the original command outcome.
        return {'returncode': returncode, 'rawLogSha256': None, 'rawLogBytes': None,
                'rawLogExported': False, 'classifierVersion': FAILURE_CLASSIFIER_VERSION,
                'captureError': 'log-read-failed',
                **({'failureCategory': 'unknown'} if returncode != 0 else {})}
    facts = {'returncode': returncode, 'rawLogSha256': sha.hexdigest(),
             'rawLogBytes': size, 'rawLogExported': False,
             'classifierVersion': FAILURE_CLASSIFIER_VERSION,
             'classificationBytes': min(size, FAILURE_SCAN_BYTES),
             'classificationTruncated': size > FAILURE_SCAN_BYTES}
    if returncode == 0:
        return facts
    categories = set()
    if type(returncode) is int and returncode > 0 and size <= FAILURE_SCAN_BYTES:
        try:
            text = sample.decode('utf-8')
        except UnicodeDecodeError:
            pass
        else:
            categories = {category for category, pattern in FAILURE_SIGNATURES.items()
                          if re.search(pattern, text, re.MULTILINE | re.IGNORECASE)}
    facts['failureCategory'] = next(iter(categories)) if len(categories) == 1 else 'unknown'
    return facts


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def diagnostic_warning(status, message):
    try:
        print(message, file=sys.stderr)
    except OSError:
        status['diagnosticWarningError'] = 'stderr-write-failed'


def save_status(path, status):
    """Do not replace an in-flight command failure with diagnostic IO errors."""
    try:
        save(path, status)
        return True
    except OSError:
        status['receiptWriteError'] = 'status-write-failed'
        diagnostic_warning(status, 'Omitted-ref diagnostic receipt write failed; no success is certified.')
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--loop-source', required=True, type=Path)
    args = parser.parse_args()
    work, output, loop = args.work.resolve(), args.output.resolve(), args.loop_source.resolve()
    assert sys.version_info[:2] == (3, 13), sys.version
    assert os.environ.get('GITHUB_ACTIONS') == 'true', 'Use the fixed isolated CI job'
    assert work != ROOT and not work.is_relative_to(ROOT)
    work.mkdir(exist_ok=False, parents=True)
    output.mkdir(exist_ok=True, parents=True)
    raw_logs = work / 'private-logs'; raw_logs.mkdir()
    status = {'status': 'preparing', 'commands': [], 'modelCalls': 'not-started'}
    if not save_status(output / 'status.json', status):
        return 1  # No command starts without its initial receipt.

    def run(label, command, *, cwd=ROOT, env=None, timeout=900):
        log = raw_logs / (label + '.log')
        record = {'label': label, 'returncode': None}
        status['commands'].append(record)
        if not save_status(output / 'status.json', status):
            raise RuntimeError('Diagnostic receipt unavailable')
        try:
            with log.open('w') as stream:
                result = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                        stderr=subprocess.STDOUT, timeout=timeout)
            record['returncode'] = result.returncode
            if result.returncode:
                raise RuntimeError(label + ' failed')
        finally:
            record.update(command_log_facts(log, record['returncode']))
            receipt_saved = save_status(output / 'status.json', status)
        if not receipt_saved:
            # Reached only after a successful command. An existing failure or
            # timeout propagates unchanged through the guarded finally above.
            raise RuntimeError('Diagnostic receipt unavailable')

    try:
        git = lambda *a: subprocess.check_output(['git', *a], cwd=ROOT, text=True).strip()
        source = {'commit': git('rev-parse', 'HEAD'), 'tree': git('rev-parse', 'HEAD^{tree}'),
                  'runId': os.environ.get('GITHUB_RUN_ID'), 'runAttempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
                  'python': sys.version, 'uv': subprocess.check_output(['uv', '--version'], text=True).strip()}
        runtime_path = ROOT / 'amplifier_web/runtime_deps/pyproject.toml'
        raw = runtime_path.read_text()
        canonical = tomllib.loads(raw)
        assert canonical['project']['name'] == 'amplifier-web-runtime'
        assert 'amplifier-core' in canonical['tool']['uv']['no-build-package']
        locked = tomllib.loads((ROOT / 'uv.lock').read_text())
        loop_rows = [p for p in locked['package'] if p['name'] == LOOP]
        assert len(loop_rows) == 1
        pin = loop_rows[0]['source']['git'].rsplit('#', 1)[1]
        assert re.fullmatch('[a-f0-9]{40}', pin)
        assert canonical['tool']['uv']['sources'][LOOP]['git'] == 'https://github.com/microsoft/' + LOOP
        loop_git = lambda *a: subprocess.check_output(['git', '-C', str(loop), *a], text=True).strip()
        assert loop_git('rev-parse', 'HEAD') == pin
        assert not loop_git('status', '--porcelain', '--untracked-files=all')
        assert loop_git('remote', 'get-url', 'origin').removesuffix('.git') in {
            'git@github.com:microsoft/' + LOOP, 'https://github.com/microsoft/' + LOOP}
        source.update(runtimeManifestSha256=digest(runtime_path), hostLockSha256=digest(ROOT / 'uv.lock'), loopCommit=pin)
        save(output / 'source.json', source)
        seed = work / 'runtime-seed'; seed.mkdir()
        # A private transport mirror binds canonical main to the inspected lock
        # commit without turning a branch policy into a legacy SHA override.
        mirror = work / 'loop-transport.git'
        run('loop-transport', ['git', 'clone', '--bare', '--no-hardlinks', str(loop), str(mirror)])
        run('loop-transport-main', ['git', '-C', str(mirror), 'update-ref', 'refs/heads/main', pin])
        assert subprocess.check_output(['git', '-C', str(mirror), 'rev-parse', 'refs/heads/main'],
                                       text=True).strip() == pin
        text = raw.replace('requires-python = ">=3.13"', 'requires-python = ">=3.13,<3.14"')
        text = text.replace('[tool.uv]\n', '[tool.uv]\nenvironments = ["sys_platform == \'linux\'"]\n', 1)
        (seed / 'pyproject.toml').write_text(text + '\n')
        cache = work / 'uv-cache'
        setup_env = {**os.environ, 'UV_PYTHON_DOWNLOADS': 'never', 'GIT_TERMINAL_PROMPT': '0',
            'UV_CACHE_DIR': str(cache), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_COUNT': '1',
            'GIT_CONFIG_KEY_0': 'url.' + mirror.as_uri() + '.insteadOf',
            'GIT_CONFIG_VALUE_0': 'https://github.com/microsoft/' + LOOP}
        for key in ('PYTHONPATH', 'UV_PROJECT_ENVIRONMENT', 'UV_OFFLINE', 'UV_NO_INDEX'):
            setup_env.pop(key, None)
        run('runtime-resolve', ['uv', 'sync', '--project', str(seed), '--python', sys.executable], env=setup_env)
        lock = tomllib.loads((seed / 'uv.lock').read_text())
        resolved_loop, = [p for p in lock['package'] if p['name'] == LOOP]
        assert resolved_loop['source']['git'].rsplit('#', 1)[1] == pin
        save(output / 'runtime-resolution.json', {'lockSha256': digest(seed / 'uv.lock'),
            'generatedManifestSha256': digest(seed / 'pyproject.toml'),
            'packages': [{key: p[key] for key in ('name', 'version', 'source') if key in p} for p in lock['package']]})
        python = seed / '.venv/bin/python'
        probe = """import hashlib,importlib,json,sys
from pathlib import Path
from amplifier_foundation.paths.resolution import parse_uri
names=['amplifier_core','amplifier_foundation','amplifier_module_loop_live','amplifier_module_loop_streaming','amplifier_module_context_simple']
rows={}
for name in names:
 mod=importlib.import_module(name);p=Path(mod.__file__);assert p.is_relative_to(Path(sys.prefix));rows[name]={'origin':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
assert parse_uri('git+https://example.invalid/approval').ref=='main'
assert parse_uri('git+https://example.invalid/approval@HEAD').ref=='HEAD'
Path(sys.argv[1]).write_text(json.dumps({'python':sys.executable,'prefix':sys.prefix,'modules':rows,'omittedRef':'main','explicitHEAD':'HEAD'},indent=2))
"""
        run('worker-readiness', [str(python), '-c', probe, str(output / 'worker-readiness.json')], env=setup_env)
        offline = work / 'offline-readiness'; offline.mkdir()
        for name in ('pyproject.toml', 'uv.lock'):
            shutil.copyfile(seed / name, offline / name)
        run('independent-offline-install', ['uv', 'sync', '--locked', '--project', str(offline),
            '--python', sys.executable], env={**setup_env, 'UV_OFFLINE': '1'})
        run('independent-offline-readiness', [str(offline / '.venv/bin/python'), '-c', probe,
            str(output / 'independent-worker-readiness.json')], env={**setup_env, 'UV_OFFLINE': '1'})
        # Actual qualification has no IP network interface or elevated identity.
        # A fresh namespace is private to this process tree and never changes the
        # runner's global firewall; no credential or private checkout is mounted.
        host_python = ROOT / '.venv/bin/python'
        assert host_python.is_file()
        fixture_env = {'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
                       'HOME': str(work / 'fixture-home'), 'UV_PYTHON_DOWNLOADS': 'never'}
        status['status'] = 'checking'
        command = ['sudo', '-n', 'unshare', '--net', '--', 'setpriv', '--reuid', str(os.getuid()),
            '--regid', str(os.getgid()), '--init-groups', '/usr/bin/env', '-i',
            'PATH=' + fixture_env['PATH'], 'LANG=C.UTF-8', 'PYTHONDONTWRITEBYTECODE=1',
            'HOME=' + fixture_env['HOME'], str(host_python),
            str(ROOT / 'tests/fixtures/omitted_ref_worker_probe.py'), '--root', str(work / 'fixture'),
            '--runtime-manifest', str(seed / 'pyproject.toml'), '--runtime-lock', str(seed / 'uv.lock'),
            '--uv-cache', str(cache), '--parent-netns', os.readlink('/proc/self/ns/net'),
            '--output', str(output / 'fixture')]
        run('real-worker-adoption', command, env=fixture_env, timeout=1200)
        status.update(status='passed', modelCalls=0)
    except BaseException as error:
        status.update(status='failed', errorType=type(error).__name__,
                      classifierVersion=FAILURE_CLASSIFIER_VERSION,
                      failureCategory=(status['commands'][-1].get('failureCategory', 'unknown')
                                       if status['commands'] else 'unknown'))
        # Do not print traceback/source excerpts from the private dependency.
        diagnostic_warning(status, 'Omitted-ref qualification failed; see the bounded status receipt.')
    finally:
        receipt_saved = save_status(output / 'status.json', status)
    return 0 if status['status'] == 'passed' and receipt_saved else 1


if __name__ == '__main__':
    raise SystemExit(main())
