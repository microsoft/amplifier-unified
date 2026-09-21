"""Disposable systemd integration fixture; invoked by scripts/test_systemd_update.py.

The package swap is a local fixture. Everything after replacement probing runs
through the production updater, real user-service lifecycle and HTTP listener.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path('/home/updater/update-test')
DATA = ROOT / 'app'
PORT = 19841  # Container loopback only: deliberately never published to the host.
REVISION = 'a' * 40
OLD_REVISION = 'b' * 40
UNIT = 'amplifier-unified.service'


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def prepare():
    from importlib import metadata
    distribution = metadata.distribution('amplifier-unified')
    version = distribution.version
    source = Path('/src/amplifier_web')
    snapshots = Path('/opt/test/snapshots')
    for name, revision in [('old', OLD_REVISION), ('new', REVISION)]:
        target = snapshots / name
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target / 'amplifier_web', dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
        info = target / distribution._path.name
        shutil.copytree(distribution._path, info, dirs_exist_ok=True)
        write(info / 'direct_url.json', {'url': 'https://github.com/microsoft/amplifier-unified',
              'vcs_info': {'vcs': 'git', 'commit_id': revision}})
        if name == 'old':
            init = target / 'amplifier_web/__init__.py'
            init.write_text(re.sub(r'__version__ = "[^"]+"', '__version__ = "0.0.0"', init.read_text()))
            info.joinpath('METADATA').write_text(info.joinpath('METADATA').read_text().replace(
                'Version: ' + version + '\n', 'Version: 0.0.0\n', 1))
    shutil.copytree(snapshots / 'old', snapshots / 'old-buggy', dirs_exist_ok=True)
    # Exact historical updater, not a reimplementation of its faulty branch.
    shutil.copy('/opt/legacy-app-updates.py', snapshots / 'old-buggy/amplifier_web/app_updates.py')
    # The actual isolated replacement probe executes this environment.
    write(distribution._path / 'direct_url.json', {'url': 'https://github.com/microsoft/amplifier-unified',
          'vcs_info': {'vcs': 'git', 'commit_id': REVISION}})
    ROOT.mkdir(parents=True, exist_ok=True)
    write(ROOT / 'version.json', version)
    subprocess.run(['chown', '-R', '1234:1234', str(ROOT)], check=True)


class Runtime:
    async def close(self):
        pass


async def serve():
    # Snapshots contain actual installed package code and metadata. Selecting the
    # new snapshot models a local installation without fetching private releases.
    sys.path.insert(0, str((ROOT / 'current-site').resolve()))
    from aiohttp import web
    from amplifier_web import app_updates
    from amplifier_web.server import create_app
    from amplifier_web.deployment_service import current_process_is_unit_managed
    from amplifier_web.deployment import validate_server

    config = validate_server({'port': PORT, 'bind': ['127.0.0.1'], 'tls': {'method': 'none'}})
    app = await create_app(DATA, workspace=ROOT, runtime=Runtime(), voice=False,
                           background_updates=False, preload_providers=False, server_config=config)
    service = app['service']
    manager = service.update_manager
    assert current_process_is_unit_managed(DATA), 'Fixture must really belong to the generated unit'
    write(ROOT / 'process.json', {'pid': os.getpid(), 'cgroup': Path('/proc/self/cgroup').read_text(),
                                  'invocationId': os.environ.get('INVOCATION_ID'),
                                  'runningIdentity': manager.running_identity})
    real_process = app_updates.process

    async def local_target():
        return '/fixture/local-install', '/opt/test/bin/amplifier-unified', Path('/opt/test/bin/python'), {
            'version': '0.0.0', 'source': 'file:///fixture/previous-package'}

    async def install_local_snapshot(*args, **kwargs):
        if args[0] == '/fixture/local-install':
            link = ROOT / 'next-site'
            link.symlink_to('/opt/test/snapshots/new')
            link.replace(ROOT / 'current-site')
            return ''
        return await real_process(*args, **kwargs)

    app_updates.installed_target = local_target
    app_updates.process = install_local_snapshot
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)

    async def commands():
        path = ROOT / 'command.json'
        while not stop.is_set():
            if path.exists():
                command = json.loads(path.read_text())
                path.unlink()
                if command['action'] == 'activate':
                    version = json.loads((ROOT / 'version.json').read_text())
                    release = {'status': 'update', 'revision': REVISION, 'latest': 'v' + version}
                    manager.service.state['updates']['pendingApp'] = release
                    marker = manager.directory / 'applications' / REVISION / 'validated.json'
                    write(marker, {'version': version, 'revision': REVISION, 'attemptId': command['id']})
                    candidate = marker.parent / 'tools/amplifier-unified/bin/python'
                    candidate.parent.mkdir(parents=True, exist_ok=True)
                    candidate.symlink_to('/opt/test/bin/python')
                    service._save()
                    write(ROOT / 'accepted.json', {'id': command['id']})
                    await app_updates.activate(manager)
            await asyncio.sleep(.03)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', PORT).start()
    task = asyncio.create_task(commands())
    try:
        await stop.wait()
    finally:
        # Match a graceful HTTP host shutdown: let the child systemctl's actual
        # SIGTERM result reach the still-running updater before cancelling it.
        try:
            await asyncio.wait_for(asyncio.shield(task), 2)
        except asyncio.TimeoutError:
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        write(ROOT / ('exit-state-' + str(os.getpid()) + '.json'), service.state['updates'])
        await runner.cleanup()


def systemctl(*args):
    return subprocess.run(['/usr/bin/systemctl', '--user', *args], check=True, capture_output=True, text=True).stdout.strip()


def request(path='/api/health'):
    token = (DATA / 'config/auth/control-token').read_text().strip()
    req = urllib.request.Request('http://127.0.0.1:' + str(PORT) + path,
                                 headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.load(response)


def wait_for(predicate, description, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(.1)
    raise AssertionError('Timed out: ' + description)


def exercise():
    unit = Path.home() / '.config/systemd/user' / UNIT
    unit.parent.mkdir(parents=True, exist_ok=True)
    bindir = ROOT / 'bin'
    bindir.mkdir(exist_ok=True)
    wrapper = bindir / 'systemctl'
    wrapper.write_text('''#!/opt/test/bin/python
import json,os,sys
from pathlib import Path
with Path('/home/updater/update-test/systemctl.jsonl').open('a') as log:
 log.write(json.dumps({'args':sys.argv[1:],'pid':os.getpid(),'cgroup':Path('/proc/self/cgroup').read_text()})+'\\n')
os.execv('/usr/bin/systemctl',['systemctl',*sys.argv[1:]])
''')
    wrapper.chmod(0o755)
    unit.write_text(f'''# Managed by amplifier-unified; do not edit.
[Service]
Type=simple
ExecStart=/opt/test/bin/python /src/tests/fixtures/systemd_update_service.py serve
Environment="PATH={bindir}:/opt/test/bin:/usr/local/bin:/usr/bin:/bin"
Environment="AMPLIFIER_WEB_HOME={DATA}"
Environment="AMPLIFIER_HOME={ROOT}/native"
Environment="AMPLIFIER_SESSION_STATE_HOME={ROOT}/session-state"
KillMode=control-group
TimeoutStopSec=5
Restart=no
''')
    systemctl('daemon-reload')
    version = json.loads((ROOT / 'version.json').read_text())

    def reset(snapshot='old'):
        systemctl('stop', UNIT)
        shutil.rmtree(DATA, ignore_errors=True)
        for name in ('current-site', 'command.json', 'accepted.json', 'process.json', 'systemctl.jsonl'):
            (ROOT / name).unlink(missing_ok=True)
        (ROOT / 'current-site').symlink_to('/opt/test/snapshots/' + snapshot)

    def start():
        systemctl('start', UNIT)
        health = wait_for(lambda: request(), 'HTTP listener')
        process = json.loads((ROOT / 'process.json').read_text())
        assert process['pid'] == int(systemctl('show', UNIT, '--property=MainPID', '--value'))
        assert UNIT in process['cgroup'] and process['invocationId']
        return health, process

    def activate():
        attempt = uuid.uuid4().hex
        write(ROOT / 'command.json', {'action': 'activate', 'id': attempt})
        wait_for(lambda: (ROOT / 'accepted.json').exists(), 'activation accepted')
        return attempt

    def invoked_inside_unit(*, no_block=True):
        rows = [json.loads(line) for line in (ROOT / 'systemctl.jsonl').read_text().splitlines()]
        assert len(rows) == 1, rows
        expected = ['--user', *(['--no-block'] if no_block else []), 'restart', UNIT]
        assert rows[0]['args'] == expected, rows
        assert UNIT in rows[0]['cgroup'], rows

    reports = []
    try:
        reset()
        initial, previous = start()
        assert initial['version'] == '0.0.0' and initial['revision'] == OLD_REVISION, initial
        attempt = activate()
        health = wait_for(lambda: (h if (h := request())['version'] == version else None), 'successor version')
        state = wait_for(lambda: (s if (s := request('/api/state'))['updates']['phase'] == 'installed' else None), 'HTTP-confirmed restart receipt')
        successor = json.loads((ROOT / 'process.json').read_text())
        assert successor['pid'] != previous['pid']
        assert health['revision'] == REVISION and health['instanceId'] != initial['instanceId']
        assert health['dataIdentity'] == initial['dataIdentity']
        updates = state['updates']
        assert updates.get('pendingRestart') is None and not updates.get('error')
        ack = [e for e in updates['diagnostics']['events'] if e['phase'] == 'restart-ack' and e['status'] == 'succeeded']
        assert ack and ack[-1]['attemptId'] == attempt, updates
        assert not updates['diagnostics'].get('lastFailure'), updates
        invoked_inside_unit()
        reports.append({'scenario': 'managed-self-restart', 'oldPid': previous['pid'], 'newPid': successor['pid'],
                        'version': health['version'], 'revision': health['revision'], 'httpConfirmed': True})

        reset()
        dropin = unit.with_suffix('.service.d') / 'refuse.conf'
        dropin.parent.mkdir(exist_ok=True)
        dropin.write_text('[Unit]\nRefuseManualStop=yes\n')
        systemctl('daemon-reload')
        initial, previous = start()
        activate()
        state = wait_for(lambda: (s if (s := request('/api/state'))['updates'].get('pendingRestart', {}).get('requestStatus') == 'rejected' else None), 'real rejected systemctl request')
        assert int(systemctl('show', UNIT, '--property=MainPID', '--value')) == previous['pid']
        assert state['updates'].get('pendingRestart'), state['updates']
        assert state['updates']['diagnostics']['lastFailure']['exitCode'] > 0
        invoked_inside_unit()
        reports.append({'scenario': 'rejected-systemd-request', 'originalPidRetained': True, 'handoffRetained': True})
        dropin.unlink()
        systemctl('daemon-reload')

        # The parent implementation supplies strict legacy-receipt recovery;
        # seed exactly the durable evidence left by the old SIGTERM bug.
        reset('new')
        subprocess.run(['/opt/test/bin/python', __file__, 'seed-legacy'], check=True)
        health, successor = start()
        state = wait_for(lambda: (s if (s := request('/api/state'))['updates'].get('phase') == 'installed' else None), 'legacy erased-marker reconciliation')
        updates = state['updates']
        assert health['version'] == version and health['revision'] == REVISION
        assert updates['diagnostics'].get('lastFailure'), updates
        reconciled = [e for e in updates['diagnostics']['events'] if e['phase'] == 'restart-reconcile' and e['status'] == 'succeeded']
        assert reconciled and not any(e['phase'] == 'restart-ack' for e in updates['diagnostics']['events']), updates
        reports.append({'scenario': 'legacy-erased-marker', 'httpConfirmed': True, 'historicalFailurePreserved': True})
        reset('old-buggy')
        initial, previous = start()
        attempt = activate()
        health = wait_for(lambda: (h if (h := request())['version'] == version else None), 'old-buggy updater successor')
        state = wait_for(lambda: (s if (s := request('/api/state'))['updates'].get('phase') == 'installed' else None), 'real old-to-new reconciliation')
        old = json.loads((ROOT / ('exit-state-' + str(previous['pid']) + '.json')).read_text())
        assert old.get('pendingRestart') is None and old['phase'] == 'error', old
        failure = old['diagnostics']['lastFailure']
        assert failure['phase'] == 'service-restart' and failure['exitCode'] == -15, failure
        assert failure['attemptId'] == attempt
        updates = state['updates']
        assert health['revision'] == REVISION and health['instanceId'] != initial['instanceId']
        assert updates['diagnostics']['lastFailure'] == failure
        assert any(e['phase'] == 'restart-reconcile' and e['status'] == 'succeeded' and e['attemptId'] == attempt
                   for e in updates['diagnostics']['events']), updates
        assert not any(e['phase'] == 'restart-ack' for e in updates['diagnostics']['events']), updates
        invoked_inside_unit(no_block=False)
        reports.append({'scenario': 'historical-updater-to-fixed-successor', 'historicalCommit': 'fd12fb041dbf71050257e8f97b2605b33cec0492',
                        'realChildExitCode': -15, 'erasedMarkerReconciled': True, 'historicalFailurePreserved': True})
        print(json.dumps({'ok': True, 'integration': 'real-systemd-user-service', 'scenarios': reports}), flush=True)
    except BaseException:
        try:
            print(json.dumps({'failedScenarioUpdates': request('/api/state')['updates']}), flush=True)
        except Exception:
            pass
        raise
    finally:
        dropin = unit.with_suffix('.service.d') / 'refuse.conf'
        dropin.unlink(missing_ok=True)
        systemctl('daemon-reload')
        systemctl('stop', UNIT)
        unit.unlink(missing_ok=True)
        systemctl('daemon-reload')


async def seed_legacy():
    sys.path.insert(0, str((ROOT / 'current-site').resolve()))
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    service = AppService(DATA, runtime=Runtime(), workspace=ROOT)
    manager = UpdateManager(service)
    service.update_manager = manager
    attempt = uuid.uuid4().hex
    version = json.loads((ROOT / 'version.json').read_text())
    manager.diagnostics.begin('application', REVISION, attempt)
    manager.diagnostics.record('replacement-probe', 'succeeded', probe={'ok': True, 'version': version, 'stage': 'complete'})
    manager.diagnostics.record('service-restart', 'failed', exitCode=-15, errorType='CommandFailure')
    write(manager.directory / 'applications' / REVISION / 'validated.json', {
        'version': version, 'revision': REVISION, 'attemptId': attempt})
    service.state['updates'].update(phase='error', pendingRestart=None, pendingApp=None,
                                  error='The app installed, but the managed service could not restart. Run amplifier-unified service restart.')
    service._save()
    await service.close()


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'prepare':
        prepare()
    elif action == 'exercise':
        exercise()
    elif action == 'serve':
        asyncio.run(serve())
    elif action == 'seed-legacy':
        asyncio.run(seed_legacy())
    else:
        raise SystemExit('Unknown fixture mode')
