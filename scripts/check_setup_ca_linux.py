"""Opt-in acceptance of /setup commands in ONE disposable Debian/Ubuntu guest.

Never run on a host or a real user's machine. Requires a writable system trust
store, root, sudo, OpenSSL and ca-certificates inside an owned isolated guest.
No package installation, network, real certificate, or browser-profile changes.
The caller owns guest creation, evidence export and teardown.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile


DEST = Path('/usr/local/share/ca-certificates/amplifier-unified-ca.crt')
BUNDLE = '/etc/ssl/certs/ca-certificates.crt'


class Commands(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.blocks = {}
        self.current = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        identity = dict(attrs).get('id', '')
        if tag == 'code' and identity.startswith('linux-ca-'):
            self.current = identity
            self.blocks[identity] = ''

    def handle_data(self, data):
        if self.current:
            self.blocks[self.current] += data

    def handle_endtag(self, tag):
        if tag == 'code':
            self.current = None


def cleanup_trust_store(owned, cleanup_bytes, result, run):
    """Remove only this run's exact public bytes; record progress before rebuilding."""
    result['failureCleanup'] = 'not required'
    result['bundleRebuild'] = 'not required'
    if not owned:
        return
    result['failureCleanup'] = 'pending ownership check'
    if not DEST.exists() and not DEST.is_symlink():
        result['failureCleanup'] = 'destination already absent'
    elif not DEST.is_symlink() and DEST.read_bytes() in cleanup_bytes:
        DEST.unlink()
        result['failureCleanup'] = 'removed exact synthetic CA'
    else:
        result['failureCleanup'] = 'not removed: bytes changed or symlink; destroy owned guest'
        raise RuntimeError(result['failureCleanup'])
    result['bundleRebuild'] = 'pending'
    try:
        run(['update-ca-certificates', '--fresh'])
    except BaseException:
        result['bundleRebuild'] = 'failed'
        raise
    result['bundleRebuild'] = 'completed'


@contextmanager
def reported_checks(result, report, cleanup):
    """Keep the check failure authoritative while attempting every finalization step."""
    primary = None
    finalization_errors = []
    directory = None
    result.update(status='running', primaryFailure=None, cleanupFailures=[],
                  temporaryDirectoryCleanup='not created', destinationAbsent=None)

    def failure(exc):
        return {'type': type(exc).__name__, 'message': str(exc)}

    def finalize(phase, action):
        try:
            action()
        except BaseException as exc:
            finalization_errors.append(exc)
            result['cleanupFailures'].append({'phase': phase, **failure(exc)})

    try:
        directory = tempfile.TemporaryDirectory(prefix='unified-ca-guest-')
        result['temporaryDirectoryCleanup'] = 'pending'
        yield directory.name
    except BaseException as exc:
        primary = exc
        result['primaryFailure'] = failure(exc)
    finally:
        finalize('trust-store', cleanup)
        if directory is not None:
            def cleanup_directory():
                try:
                    directory.cleanup()
                except BaseException:
                    result['temporaryDirectoryCleanup'] = 'failed'
                    raise
                result['temporaryDirectoryCleanup'] = 'completed'
            finalize('temporary-directory', cleanup_directory)

        def inspect_destination():
            result['destinationAbsent'] = not DEST.exists() and not DEST.is_symlink()
        finalize('destination-observation', inspect_destination)
        result['status'] = 'failed' if primary is not None or finalization_errors else 'passed'
        # A reporting failure must not replace the primary or earlier cleanup error.
        finalize('report-write', lambda: report.write_text(json.dumps(result, indent=2) + '\n'))
        if finalization_errors:
            result['status'] = 'failed'
    if primary is not None:
        raise primary.with_traceback(primary.__traceback__)
    if finalization_errors:
        raise finalization_errors[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-disposable-system-trust-store', action='store_true')
    parser.add_argument('--inject-failure-after-install', action='store_true',
                        help='Exercise exact-byte failure cleanup; intentionally exits nonzero')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_disposable_system_trust_store:
        parser.error('Explicit disposable-guest opt-in is required')
    if os.geteuid() != 0 or not (
        Path('/.dockerenv').exists() or Path('/run/systemd/container').exists()
    ):
        parser.error('Requires root inside an isolated disposable container, never the host')
    release = Path('/etc/os-release').read_text()
    if not any(line in release.splitlines() for line in ('ID=ubuntu', 'ID=debian')):
        parser.error('Requires an actual Ubuntu or Debian guest')
    for tool in ('sh', 'grep', 'openssl', 'sudo', 'install', 'update-ca-certificates'):
        if not shutil.which(tool):
            parser.error(f'Missing guest prerequisite: {tool}')
    if DEST.exists() or DEST.is_symlink():
        parser.error('Destination already exists; refusing to overwrite or remove it')
    if not os.access(DEST.parent, os.W_OK):
        parser.error('System trust directory is not writable; do not bypass runner restrictions')

    result = {'status': 'running', 'osRelease': release, 'commands': [], 'checks': [],
              'limitations': ['Synthetic guest only, not a real browser trust-store check',
                              'Root invokes real sudo; interactive sudo authentication is not tested']}
    source = Path(__file__).resolve().parents[1] / 'amplifier_web/setup_page.py'
    module = runpy.run_path(str(source))
    commands = Commands(module['render_setup_page'](module['Platform'].LINUX, True, 'AA:BB')).blocks
    assert len(commands) == 6
    result['copiedCommands'] = commands
    args.report.parent.mkdir(parents=True, exist_ok=True)

    def run(argv, *, expected=0, env=None, demote=False):
        def unprivileged():
            os.setgroups([])
            os.setgid(65534)
            os.setuid(65534)
        completed = subprocess.run(argv, text=True, capture_output=True, env=env,
                                   preexec_fn=unprivileged if demote else None, timeout=60)
        result['commands'].append({'argv': argv, 'returncode': completed.returncode,
                                   'stdout': completed.stdout, 'stderr': completed.stderr})
        assert (completed.returncode == 0) if expected == 0 else (completed.returncode != 0), result['commands'][-1]
        return completed.stdout

    def check(name):
        result['checks'].append(name)

    owned = False
    owned_bytes = None
    cleanup_bytes = ()
    with reported_checks(
        result, args.report, lambda: cleanup_trust_store(owned, cleanup_bytes, result, run),
    ) as directory:
        root = Path(directory)
        root.chmod(0o755)
        downloads = root / 'Downloads'
        downloads.mkdir(mode=0o755)
        ca = downloads / 'amplifier-unified-ca (1).crt'
        wrong = root / 'wrong.crt'
        for cert, key in ((ca, root / 'ca.key'), (wrong, root / 'wrong.key')):
            run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                 '-subj', '/CN=Disposable Unified Test CA', '-addext', 'basicConstraints=critical,CA:TRUE',
                 '-keyout', str(key), '-out', str(cert)])
            key.chmod(0o600)
            cert.chmod(0o644)
        owned_bytes = ca.read_bytes()
        leaf = root / 'leaf.crt'
        run(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes',
             '-subj', '/CN=unified.test', '-keyout', str(root / 'leaf.key'),
             '-out', str(root / 'leaf.csr')])
        (root / 'extensions').write_text('subjectAltName=DNS:unified.test\nbasicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\n')
        run(['openssl', 'x509', '-req', '-in', str(root / 'leaf.csr'), '-CA', str(ca),
             '-CAkey', str(root / 'ca.key'), '-CAcreateserial', '-days', '1',
             '-extfile', str(root / 'extensions'), '-out', str(leaf)])
        env = {**os.environ, 'HOME': str(root)}  # disposable client's home inside this guest
        prefix = commands['linux-ca-duplicate'] + '\n'
        copied = lambda name: ['sh', '-eu', '-c', prefix + commands[name]]
        expected_fp = run(['openssl', 'x509', '-in', str(ca), '-noout', '-fingerprint', '-sha256']).strip()
        assert expected_fp in run(copied('linux-ca-fingerprint'), env=env)
        assert expected_fp not in run(['openssl', 'x509', '-in', str(wrong), '-noout', '-fingerprint', '-sha256'])
        check('duplicate filename renders and executes; wrong CA fingerprint differs')
        verify_leaf = ['openssl', 'verify', '-CAfile', BUNDLE, '-no-CApath', '-no-CAstore',
                       '-verify_hostname', 'unified.test', str(leaf)]
        run(verify_leaf, expected=1)
        # Negative controls are potential writers too if the behavior regresses.
        cleanup_bytes = (owned_bytes, owned_bytes + wrong.read_bytes())
        owned = True
        run(['install', '-m', '0644', '--', str(ca), str(DEST)], expected=1, demote=True)
        assert not DEST.exists()
        check('leaf untrusted before install; non-root system-store write denied')
        ca.write_bytes(owned_bytes + wrong.read_bytes())
        run(copied('linux-ca-install'), env=env, expected=1)
        assert not DEST.exists()
        ca.write_bytes(owned_bytes)
        check('concatenated CA bundle refused before any trust-store write')
        run(copied('linux-ca-install'), env=env)
        assert DEST.read_bytes() == ca.read_bytes()
        assert DEST.stat().st_uid == 0 and DEST.stat().st_mode & 0o777 == 0o644
        if args.inject_failure_after_install:
            raise RuntimeError('Injected post-install failure to check exact-byte cleanup')
        run(copied('linux-ca-verify'), env=env)
        run(verify_leaf)
        check('exact displayed install/verify commands: root-owned 0644 CA and trusted hostname chain')
        run(['openssl', 'verify', '-CAfile', BUNDLE, '-no-CApath', '-no-CAstore', str(wrong)], expected=1)
        run([*verify_leaf[:-3], '-verify_hostname', 'wrong.test', str(leaf)], expected=1)
        check('wrong CA and wrong hostname rejected after install')
        before = DEST.read_bytes()
        run(copied('linux-ca-install'), env=env, expected=1)
        assert DEST.read_bytes() == before
        check('existing destination refuses overwrite')
        run(['sh', '-eu', '-c', commands['linux-ca-remove']], env=env)
        assert not DEST.exists()
        owned = False
        run(verify_leaf, expected=1)
        run(copied('linux-ca-verify'), env=env, expected=1)
        check('exact displayed backout removes CA and trust; leaf and CA rejected')
    print(json.dumps({'status': result['status'], 'checks': result['checks'],
                      'report': str(args.report)}))


if __name__ == '__main__':
    main()
