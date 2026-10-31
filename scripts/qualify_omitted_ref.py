"""Fixed isolated-CI preparation for real omitted-ref worker qualification.

Acquire the canonical runtime graph once, retaining its real Git/registry source
policy and exact lock. Independent generation environments reuse only uv's cache,
not this interpreter's installed packages. Private logs/source stay on the runner.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
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
# Load the fixed sibling leaf without depending on the invocation's sys.path.
_diagnostics_spec = importlib.util.spec_from_file_location(
    'qualification_diagnostics', Path(__file__).with_name('qualification_diagnostics.py'))
diagnostics = importlib.util.module_from_spec(_diagnostics_spec)
_diagnostics_spec.loader.exec_module(diagnostics)
FAILURE_CLASSIFIER_VERSION = diagnostics.FAILURE_CLASSIFIER_VERSION
FAILURE_SCAN_BYTES = diagnostics.FAILURE_SCAN_BYTES


def command_log_facts(log, returncode):
    """IO wrapper only; bounded hashing/classification has one shared owner."""
    try:
        with log.open('rb') as stream:
            return diagnostics.stream_facts(
                iter(lambda: stream.read(FAILURE_SCAN_BYTES), b''), returncode)
    except OSError:
        return diagnostics.capture_error_facts(returncode, 'log-read-failed')
    except Exception:
        return diagnostics.capture_error_facts(returncode, 'log-capture-failed')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def diagnostic_warning(status, message):
    try:
        print(message, file=sys.stderr)
    except Exception:
        status['diagnosticWarningError'] = 'stderr-write-failed'


def save_status(path, status):
    """Do not replace an in-flight command failure with diagnostic IO errors."""
    try:
        save(path, status)
        return True
    except Exception:
        status['receiptWriteError'] = 'status-write-failed'
        diagnostic_warning(status, 'Omitted-ref diagnostic receipt write failed; no success is certified.')
        return False


def verified_git_source(path, pin, ref='HEAD'):
    """A named-ref transport mirror must supply complete history at the pin."""
    git = lambda *args: subprocess.check_output(
        ['git', '-C', str(path), *args], text=True).strip()
    if git('rev-parse', '--is-shallow-repository') != 'false':
        raise RuntimeError('Git source must have complete history')
    if git('rev-parse', ref) != pin:
        raise RuntimeError('Git source pin mismatch')
    return {'shallow': False, 'commit': pin}


def mirror_seed_environment(cache, mirror):
    # uv 0.13.6's GitHub API fast path can bypass Git's insteadOf transport.
    # Disable it only for this mirror-bound seed, not globally or in the fixture.
    env = {**os.environ, 'UV_PYTHON_DOWNLOADS': 'never', 'GIT_TERMINAL_PROMPT': '0',
           'UV_NO_GITHUB_FAST_PATH': '1',
           'UV_CACHE_DIR': str(cache), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_COUNT': '1',
           'GIT_CONFIG_KEY_0': 'url.' + mirror.as_uri() + '.insteadOf',
           'GIT_CONFIG_VALUE_0': 'https://github.com/microsoft/' + LOOP}
    for key in ('PYTHONPATH', 'UV_PROJECT_ENVIRONMENT', 'UV_OFFLINE', 'UV_NO_INDEX'):
        env.pop(key, None)
    return env


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
    if work == output or work.is_relative_to(output) or output.is_relative_to(work):
        diagnostic_warning({}, 'Qualification work and evidence directories must be separate.')
        return 1
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
        stream = None
        try:
            stream = log.open('w')
            result = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=timeout)
            record['returncode'] = result.returncode
            if result.returncode:
                raise RuntimeError(label + ' failed')
        finally:
            close_failed = False
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    close_failed = True
            record.update(diagnostics.capture_error_facts(record['returncode'], 'log-capture-failed')
                          if close_failed else command_log_facts(log, record['returncode']))
            receipt_saved = save_status(output / 'status.json', status)
        if not receipt_saved or record.get('captureError') or not record.get('complete'):
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
        checkout_identity = verified_git_source(loop, pin)
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
        mirror_identity = verified_git_source(mirror, pin, 'refs/heads/main')
        save(output / 'mirror.json', {'checkout': checkout_identity, 'mirror': mirror_identity})
        text = raw.replace('requires-python = ">=3.13"', 'requires-python = ">=3.13,<3.14"')
        text = text.replace('[tool.uv]\n', '[tool.uv]\nenvironments = ["sys_platform == \'linux\'"]\n', 1)
        (seed / 'pyproject.toml').write_text(text + '\n')
        cache = work / 'uv-cache'
        setup_env = mirror_seed_environment(cache, mirror)
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
