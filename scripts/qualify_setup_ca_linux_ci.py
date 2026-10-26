"""CI-only, one-container qualification of the exact rendered /setup CA commands.

No application installs, secrets, host mounts, privileged containers, or host trust
changes. The runner controls Docker; only guest-local synthetic stores are changed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid


IMAGE = 'docker.io/library/python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f'
INPUTS = ('amplifier_web/setup_page.py', 'scripts/check_setup_ca_linux.py')
LABEL = 'io.amplifier.setup-ca-owner'
INJECTED = 'Injected post-install failure to check exact-byte cleanup'
OBSERVE = """
import hashlib,json,pathlib
p=pathlib.Path('/usr/local/share/ca-certificates/amplifier-unified-ca.crt')
print(json.dumps({
 'destinationAbsent':not p.exists() and not p.is_symlink(),
 'bundleSha256':hashlib.sha256(pathlib.Path('/etc/ssl/certs/ca-certificates.crt').read_bytes()).hexdigest(),
 'temporaryFiles':[p.name for p in pathlib.Path('/test-tmp').iterdir()],
 'inputs':{p:hashlib.sha256(pathlib.Path('/opt/setup-ca',p).read_bytes()).hexdigest()
           for p in ('amplifier_web/setup_page.py','scripts/check_setup_ca_linux.py')}
}))
"""


def main():
    if (os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or sys.platform != 'linux'):
        raise SystemExit('Requires a GitHub-hosted Linux CI runner; never run on a live host')
    expected = os.environ['EXPECTED_SOURCE']
    if not re.fullmatch('[0-9a-f]{40}', expected):
        raise SystemExit('Expected exact source commit')
    owner = f"setup-ca-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    if not re.fullmatch('setup-ca-[0-9]+-[0-9]+', owner):
        raise SystemExit('Invalid CI owner identity')
    output = Path(os.environ['RUNNER_TEMP']) / 'setup-ca-evidence'
    stage = Path(os.environ['RUNNER_TEMP']) / 'setup-ca-input'
    output.mkdir(exist_ok=False)
    stage.mkdir(exist_ok=False)
    nonce = uuid.uuid4().hex
    result = {'status': 'running', 'owner': owner, 'ownershipNonce': nonce, 'imageReference': IMAGE,
              'primaryFailure': None, 'cleanupFailures': [], 'removed': False,
              'checks': {}, 'limits': ['Synthetic Debian OS trust, not browser trust',
                                     'APT versions recorded, not transitively pinned',
                                     'Real sudo as root; interactive authentication untested']}
    sequence = 0
    container = None
    create_attempted = False
    create_uncertain = False

    def save():
        (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')

    def run(argv, *, expected_rc=0, timeout=120):
        nonlocal sequence
        sequence += 1
        prefix = output / f'{sequence:02d}'
        record = {'argv': argv}
        try:
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            record['returncode'] = completed.returncode
            prefix.with_suffix('.stdout').write_text(completed.stdout)
            prefix.with_suffix('.stderr').write_text(completed.stderr)
        except subprocess.TimeoutExpired as exc:
            record['timeoutSeconds'] = timeout
            for suffix, value in (('stdout', exc.stdout), ('stderr', exc.stderr)):
                prefix.with_suffix('.' + suffix).write_bytes(
                    value if isinstance(value, bytes) else (value or '').encode())
            raise
        finally:
            prefix.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n')
        if expected_rc is not None and completed.returncode != expected_rc:
            raise RuntimeError(f'Command {sequence} returned {completed.returncode}, expected {expected_rc}')
        return completed

    def inspect_owned():
        info, = json.loads(run(['docker', 'inspect', owner]).stdout)
        if info['Name'] != '/' + owner or info['Config']['Labels'].get(LABEL) != nonce:
            raise RuntimeError('Container ownership mismatch; refusing mutation')
        if container is not None and info['Id'] != container:
            raise RuntimeError('Container identity changed; refusing mutation')
        return info

    def observe(name):
        state = json.loads(run(['docker', 'exec', container, 'python', '-I', '-c', OBSERVE]).stdout)
        (output / f'{name}-store.json').write_text(json.dumps(state, indent=2) + '\n')
        assert state['destinationAbsent'] and not state['temporaryFiles'], state
        assert state['inputs'] == result['inputSha256'], state
        return state

    try:
        save()  # retain the intended owner before any Docker effect
        revision = run(['git', 'rev-parse', 'HEAD']).stdout.strip()
        assert revision == expected, (revision, expected)
        assert not run(['git', 'status', '--porcelain']).stdout.strip(), 'Source must be clean'
        result['source'] = revision
        result['tree'] = run(['git', 'rev-parse', 'HEAD^{tree}']).stdout.strip()
        result['inputSha256'] = {}
        for name in INPUTS:
            data = run(['git', 'show', f'HEAD:{name}']).stdout.encode()
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            result['inputSha256'][name] = hashlib.sha256(data).hexdigest()
        for name in ('.github/workflows/setup-ca-linux.yml', 'scripts/qualify_setup_ca_linux_ci.py'):
            result.setdefault('adapterSha256', {})[name] = hashlib.sha256(Path(name).read_bytes()).hexdigest()
        assert not run(['docker', 'ps', '-aq', '--filter', f'name=^/{owner}$']).stdout.strip(), 'Owner already exists; no replay'
        run(['docker', 'pull', '--platform', 'linux/amd64', IMAGE], timeout=180)
        result['image'] = json.loads(run(['docker', 'image', 'inspect', IMAGE]).stdout)[0]
        save()
        create_attempted = True
        try:
            created = run(['docker', 'create', '--name', owner, '--label', f'{LABEL}={nonce}',
                           '--platform', 'linux/amd64', '--user', '0:0', '--cpus', '2',
                           '--memory', '1g', '--pids-limit', '128', IMAGE, 'sleep', 'infinity'])
        except BaseException:
            create_uncertain = True
            result['createUncertain'] = True
            raise
        container = created.stdout.strip()
        assert re.fullmatch('[0-9a-f]{64}', container), container
        result['containerId'] = container
        save()
        info = inspect_owned()
        assert not info['HostConfig']['Privileged'] and not info['Mounts'], info
        run(['docker', 'start', container])
        run(['docker', 'exec', container, 'sh', '-ec',
             'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends sudo openssl ca-certificates coreutils grep && mkdir -p /opt/setup-ca /evidence /test-tmp && update-ca-certificates --fresh'], timeout=240)
        run(['docker', 'exec', container, 'sh', '-ec',
             'cat /etc/os-release; python --version; openssl version; dpkg-query -W sudo openssl ca-certificates coreutils grep'])
        run(['docker', 'cp', str(stage) + '/.', container + ':/opt/setup-ca'])
        run(['docker', 'network', 'disconnect', 'bridge', container])
        info = inspect_owned()
        assert not info['NetworkSettings']['Networks'], 'Guest must be disconnected before testing'
        result['networkDisconnected'] = True
        baseline = observe('before')
        for name, flags, returncode in (('normal', [], 0), ('injected', ['--inject-failure-after-install'], 1)):
            invocation = run(['docker', 'exec', '-e', 'TMPDIR=/test-tmp', container,
                              'python', '-I', '/opt/setup-ca/scripts/check_setup_ca_linux.py',
                              '--allow-disposable-system-trust-store', '--report', f'/evidence/{name}.json',
                              *flags], expected_rc=None, timeout=180)
            run(['docker', 'cp', f'{container}:/evidence/{name}.json', str(output / f'{name}.json')])
            report = json.loads((output / f'{name}.json').read_text())
            result['checks'][name] = {'returncode': invocation.returncode, 'report': report}
            save()
            assert invocation.returncode == returncode, result['checks'][name]
            assert report['cleanupFailures'] == [] and report['destinationAbsent'], report
            assert report['temporaryDirectoryCleanup'] == 'completed', report
            if name == 'normal':
                assert report['status'] == 'passed' and report['primaryFailure'] is None, report
            else:
                assert report['status'] == 'failed', report  # expected negative stays failed
                assert report['primaryFailure'] == {'type': 'RuntimeError', 'message': INJECTED}, report
                assert report['failureCleanup'] == 'removed exact synthetic CA', report
                assert report['bundleRebuild'] == 'completed', report
            restored = observe(name)
            assert restored == baseline, (baseline, restored)
            result['checks'][name]['qualified'] = True
    except BaseException as exc:
        result['primaryFailure'] = {'type': type(exc).__name__, 'message': str(exc)}
    finally:
        # Reconcile a create timeout using the same owner, never a new name.
        try:
            ids = run(['docker', 'ps', '-aq', '--filter', f'name=^/{owner}$']).stdout.split()
            if ids and create_attempted:
                info = inspect_owned()
                container = info['Id']
                result['containerId'] = container
                if info['State']['Running']:
                    run(['docker', 'stop', '--time', '10', container], timeout=30)
                assert not inspect_owned()['State']['Running']
                # Report-export failures do not prevent exact owned removal.
                for name in ('normal', 'injected'):
                    if not (output / f'{name}.json').exists():
                        try:
                            exported = run(['docker', 'cp', f'{container}:/evidence/{name}.json',
                                            str(output / f'{name}.json')], expected_rc=None)
                            if exported.returncode:
                                result.setdefault('exportFailures', []).append(name + ': unavailable')
                        except BaseException as exc:
                            result.setdefault('exportFailures', []).append(
                                {'report': name, 'type': type(exc).__name__, 'message': str(exc)})
                inspect_owned()
                run(['docker', 'rm', container])
            if create_attempted:
                assert not run(['docker', 'ps', '-aq', '--filter', f'name=^/{owner}$']).stdout.strip()
                if create_uncertain and not ids:
                    result['cleanupDisposition'] = 'Unknown create outcome; observed absence is not settled cleanup'
                else:
                    result['removed'] = True
            else:
                result['cleanupDisposition'] = 'No create attempted; existing containers left untouched'
        except BaseException as exc:
            result['cleanupFailures'].append({'type': type(exc).__name__, 'message': str(exc)})
        result['status'] = ('passed' if result['primaryFailure'] is None
                            and not result['cleanupFailures'] and result['removed']
                            and all(result['checks'].get(n, {}).get('qualified') for n in ('normal', 'injected'))
                            else 'failed')
        save()
    print(json.dumps({'status': result['status'], 'removed': result['removed'], 'evidence': str(output)}))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
