"""Two real local app processes; synthetic account inference, no SSH or external accounts."""
import base64
import json
import os
from pathlib import Path
import secrets
import select
import shutil
import subprocess
import sys

from amplifier_worktrees.git import git
from test_worktrees import repository


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures'


class LocalHost:
    def __init__(self, root, repo, label):
        self.root, self.repo, self.label = root, repo, label
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        native = root / 'native'
        native.mkdir(mode=0o700)
        self.secret = secrets.token_hex(24)
        self.env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(root),
            'PYTHONPATH': os.pathsep.join((str(FIXTURES), str(ROOT))),
            'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1',
            'AMPLIFIER_HOME': str(native), 'AMPLIFIER_SESSION_STATE_HOME': str(root / 'locks'),
            'AMPLIFIER_WEB_HOME': str(root / 'app'), 'AMPLIFIER_UNIFIED_IMPORT_HOME': str(root / 'legacy'),
            'AMPLIFIER_TERMINAL_HOME': str(root / 'terminal'),
            'PORTABILITY_FIXTURE_KEY': self.secret, 'PORTABILITY_FIXTURE_HOST': label,
            'PORTABILITY_FIXTURE_AUDIT': str(root / 'probe-audit.jsonl')}
        settings = native / 'settings.yaml'
        settings.write_text('bundle:\n  active: work\nconfig:\n  providers:\n'
            '    - module: provider-portability-fixture\n      id: portability-fixture\n'
            '      config:\n        api_key: ${PORTABILITY_FIXTURE_KEY}\n')
        settings.chmod(0o600)
        self.settings = settings.read_bytes()
        self.process = None
        self.start()

    def start(self):
        self.errors = (self.root / 'host-errors.txt').open('a')
        os.chmod(self.errors.name, 0o600)
        self.process = subprocess.Popen([sys.executable, '-u', str(FIXTURES / 'portability_host.py'), str(self.repo)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors,
            cwd=self.root, env=self.env, text=True, bufsize=1)
        self.identity = self.call('identity')

    def call(self, action, args=None):
        self.process.stdin.write(json.dumps({'action': action, 'args': args or {}}) + '\n')
        self.process.stdin.flush()
        readable, _, _ = select.select([self.process.stdout], [], [], 60)
        assert readable, f'{self.label} did not complete {action}'
        line = self.process.stdout.readline()
        assert line, f'{self.label} exited during {action}; see private fixture log'
        response = json.loads(line)
        assert response['ok'], f'{self.label} {action}: {response.get("error")}: {response.get("detail")}'
        return response['result']

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                self.call('close')
                self.process.wait(timeout=20)
            except Exception:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.process:
            self.process.stdin.close()
            self.process.stdout.close()
        self.errors.close()

    def restart(self):
        identity = self.identity['host']
        old_pid = self.identity['pid']
        self.close()
        self.start()
        assert self.identity['host'] == identity and self.identity['pid'] != old_pid

    def transport(self, path):
        source = Path(path)
        incoming = self.root / 'transport'
        incoming.mkdir(mode=0o700, exist_ok=True)
        target = incoming / source.name
        shutil.copyfile(source, target)
        target.chmod(0o600)
        assert target.read_bytes() == source.read_bytes()
        assert target.stat().st_mode & 0o077 == 0
        return str(target)

    def audit(self):
        path = self.root / 'probe-audit.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def assert_preserved(host, seeded):
    identity = seeded['sessionId']
    value = host.call('inspect', {'sessionId': identity})
    session = value['session']
    assert value['transcript'] == seeded['transcript']
    assert session['id'] == session['nativeIdentity'] == identity
    assert session['task']['id'] == 'task-original'
    assert session['draft'] == 'Unsent fixture draft'
    assert any(row.get('voiceId') == 'voice-original' for row in session['messages'])
    assert session['deferRuntimeUntilInteraction'] is True
    assert not value['workers'] and not session['workers']
    assert not session.get('generations') and not value['fenced']
    def saved_job_files(item):
        if isinstance(item, dict):
            if item.get('name') == 'job-unknown-original.json':
                yield json.loads(base64.b64decode(item['file']['data']))
            for child in item.values():
                yield from saved_job_files(child)
        elif isinstance(item, list):
            for child in item:
                yield from saved_job_files(child)
    assert seeded['unknownJob'] in list(saved_job_files(value['observations']))
    unknown_args = seeded['unknownRequestArgs']
    assert host.call('operations.request', {'sessionId': identity,
        'requestId': unknown_args['requestId']}) == seeded['unknownRequest']
    assert host.call('operations.submit', unknown_args) == seeded['unknownRequest']
    assert host.call('operations.submit', unknown_args) == seeded['unknownRequest']
    after_retry = host.call('inspect', {'sessionId': identity})
    assert not after_retry['runtimeControls'] and not after_retry['workers']
    assert value['controls']['task']['id'] == 'task-original'
    assert value['controls']['taskReceipts']['original-task-command']['id'] == 'original-task-receipt'
    assert value['controls']['budget']['maxIterations'] == 12
    first, second = seeded['firstOutput'], seeded['secondOutput']
    assert {row['id'] for row in value['outputs']} == {first['id'], second['id']}
    actual = next(row for row in value['outputs'] if row['id'] == second['id'])
    assert actual['parentId'] == first['id'] and actual['evidenceIds'] == [first['id']]
    assert value['contents'][second['id']] == base64.b64encode(b'Second output').decode()
    assert value['comments'] == [seeded['comment']]
    workspace = Path(session['workingDirectory'])
    assert (workspace / 'a.txt').read_bytes() == b'staged\nunstaged\n'
    assert git(workspace, 'show', ':a.txt') == b'staged\n'
    assert (workspace / 'new.txt').read_text() == 'Untracked original work'
    assert host.call('native-admission', {'workspace': str(workspace), 'sessionId': identity}) == {'fenced': False}
    return value


def transfer(source, destination, seeded, command):
    sid = seeded['sessionId']
    source_state = source.call('inspect', {'sessionId': sid})
    original_workspace = source_state['session']['workspace']
    before_audit = len(destination.audit())
    outgoing = source.call('export', {'sessionId': sid, 'destination': destination.identity['host']['id'], 'commandId': command})
    assert outgoing['phase'] == 'prepared', outgoing
    assert outgoing['releaseEvidence']['quiesced'] and outgoing['releaseEvidence']['transferFenced']
    assert outgoing['releaseEvidence']['inputsReplayed'] is False
    assert source.call('native-admission', {'workspace': original_workspace, 'sessionId': sid}) == {'fenced': True}
    package = destination.transport(outgoing['package'])
    for secret in (source.secret, destination.secret):
        assert secret.encode() not in Path(package).read_bytes()
    arguments = {'path': package, 'repository': str(destination.repo)}
    staged = destination.call('portability.stage', arguments)
    assert staged['phase'] == 'ready'
    assert staged['checks']['method'] == 'provider.complete'
    assert staged['checks']['credentialsOrigin'] == 'destination'
    assert len(destination.audit()) == before_audit + 1
    assert destination.call('portability.stage', arguments)['duplicate'] is True
    assert len(destination.audit()) == before_audit + 1
    working = staged['destinationState']['workspace']
    assert destination.call('native-admission', {'workspace': working, 'sessionId': sid}) == {'fenced': True}
    ready = source.transport(staged['receiptPath'])
    released = source.call('portability.release', {'sessionId': sid, 'id': outgoing['id'],
        'expectedRevision': outgoing['revision'], 'path': ready})
    assert released['phase'] == 'released'
    assert (Path(released['archive']) / 'transcript.jsonl').read_bytes() == base64.b64decode(seeded['transcript'])
    source.restart()
    assert source.call('native-admission', {'workspace': original_workspace, 'sessionId': sid}) == {'fenced': True}
    certificate = destination.transport(released['receiptPath'])
    args = {'sessionId': sid, 'id': staged['id'], 'expectedRevision': staged['revision'], 'path': certificate}
    active = destination.call('portability.activate', args)
    assert active['phase'] == 'active'
    assert len(destination.audit()) == before_audit + 2
    assert destination.call('portability.activate', args)['duplicate'] is True
    assert len(destination.audit()) == before_audit + 2
    assert_preserved(destination, seeded)
    return outgoing, released


def test_two_process_hosts_round_trip_without_replaying_task_or_transferring_credentials(tmp_path):
    source_root, destination_root = tmp_path / 'source', tmp_path / 'destination'
    source_root.mkdir(mode=0o700)
    destination_root.mkdir(mode=0o700)
    source_repo = repository(source_root)
    destination_repo = destination_root / 'repo'
    git(destination_root, 'clone', '--no-local', str(source_repo), str(destination_repo))
    source = LocalHost(source_root, source_repo, 'source')
    destination = None
    try:
        destination = LocalHost(destination_root, destination_repo, 'destination')
        assert source.identity['pid'] != destination.identity['pid']
        for key in ('nativeHome', 'stateHome', 'appHome'):
            assert source.identity[key] != destination.identity[key]
        source.call('pair', destination.identity['host'])
        destination.call('pair', source.identity['host'])
        seeded = source.call('seed')
        first, _ = transfer(source, destination, seeded, 'outbound-original')
        assert first['generation'] == 1
        destination.restart()
        assert_preserved(destination, seeded)
        second, _ = transfer(destination, source, seeded, 'return-original')
        assert second['generation'] == 2
        source.restart()
        returned = assert_preserved(source, seeded)
        assert destination.call('inspect', {'sessionId': seeded['sessionId']})['fenced']
        assert [r['phase'] for r in sorted(returned['receipts'], key=lambda r: r['generation'])] == ['released', 'active']
        for host in (source, destination):
            audit = host.audit()
            assert len(audit) == 2
            assert all(row['host'] == host.label and row['destinationCredentialVerified'] is True for row in audit)
            assert len({row['pid'] for row in audit}) == 2
            assert all(row['pid'] != host.identity['pid'] and row['purpose'] == 'destination-execution-probe' for row in audit)
            assert (host.root / 'native' / 'settings.yaml').read_bytes() == host.settings
            assert not list((host.root / 'app').rglob('keys.env'))
            for folder in ('transport', 'app/portability/packages', 'app/portability/exchange'):
                for path in (host.root / folder).glob('*.json'):
                    raw = path.read_bytes()
                    assert source.secret.encode() not in raw and destination.secret.encode() not in raw
    finally:
        source.close()
        if destination:
            destination.close()
