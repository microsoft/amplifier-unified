"""Owned disposable Linux source fixture; takes an unused private output root.
Uses no installed app, production services, keys, histories or provider accounts.
"""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(mode=0o700, parents=True, exist_ok=False)
controllers = []
units = []
results = []

def ctl(*args, check=True):
    return subprocess.run(['systemctl', '--user', *args], check=check, text=True,
                          capture_output=True)

def until(fn, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.04)
    raise AssertionError('fixture observation deadline (does not authorize interruption)')

def read(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def unit_state(unit):
    return dict(line.split('=', 1) for line in ctl('show', unit,
        '--property=ActiveState,SubState,MainPID,ControlGroup,InvocationID').stdout.splitlines())

def record(name, **evidence):
    results.append({'case': name, 'passed': True, **evidence})

class Controller:
    def __init__(self, root, unit, expect_ready=True):
        self.root, self.unit = root, unit
        self.n = 0
        self.messages = queue.Queue()
        self.events = []
        self.log = (root / ('controller-' + uuid.uuid4().hex[:8] + '.stderr')).open('w')
        self.proc = subprocess.Popen(['node', '--experimental-transform-types',
            '--import', str(HERE / 'source-loader.mjs'), str(HERE / 'controller.mjs'),
            str(root), unit], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.log, text=True, bufsize=1)
        controllers.append(self)
        def read_messages():
            for line in self.proc.stdout:
                self.messages.put(json.loads(line))
        threading.Thread(target=read_messages, daemon=True).start()
        if expect_ready:
            event = self.messages.get(timeout=10)
            assert event.get('event') == 'controller-ready', event

    def call(self, action, **args):
        self.n += 1
        self.proc.stdin.write(json.dumps({'id': self.n, 'action': action, 'args': args}) + '\n')
        self.proc.stdin.flush()
        while True:
            item = self.messages.get(timeout=15)
            if item.get('id') == self.n:
                return item
            self.events.append(item)

    def result(self, action, **args):
        row = self.call(action, **args)
        assert 'error' not in row, row
        return row['result']

    def receipt(self, command, status):
        def observed():
            row = self.result('receipt', commandId=command)
            return row if row and row['status'] == status else None
        return until(observed)

    def crash(self):
        self.proc.kill()  # Exactly this disposable owned controller, intentional fault injection.
        self.proc.wait(timeout=5)

def instance(name):
    root = OUT / name
    root.mkdir(mode=0o700)
    unit = 'amplifier-lifecycle-spike-' + uuid.uuid4().hex[:12] + '.service'
    assert 'LoadState=not-found' in ctl('show', unit, '--property=LoadState').stdout
    units.append(unit)
    c = Controller(root, unit)
    first = c.result('initial')
    assert first['ready'] and first['identity']['id'] == 'fixture-a'
    return c, root, unit

def count(root, name):
    file = root / name
    return len(file.read_text().splitlines()) if file.exists() else 0

try:
    # Ordinary drain includes real accepted child work and its completion callback.
    c, root, unit = instance('ordinary')
    unknown = b'{"command":"remote-accepted","status":"unknown"}\n'
    (root / 'business.jsonl').write_bytes(unknown)
    assert c.result('runtime', op='work')['ok']
    child = until(lambda: read(root / 'child.json'))
    c.result('stop', commandId='stop-a')
    until(lambda: c.result('runtime', op='inspect')['intakeClosed'])
    time.sleep(.2)
    assert c.result('receipt', commandId='stop-a')['status'] == 'running'
    assert (Path('/proc') / str(child['pid'])).exists()
    assert c.result('runtime', op='work') == {'ok': False, 'reason': 'intake_closed'}
    assert c.result('runtime', op='callback')['ok']
    stopped = c.receipt('stop-a', 'stopped')
    assert stopped['phase'] == 'stopped' and 'interruption' not in stopped
    record('accepted-child-drain-and-callback', stopped=stopped, dispatches=count(root, 'dispatches.jsonl'))

    # Competing actual domain lock blocks B's readiness, not just a Boolean flag.
    domain = os.open(root / 'domain.lock', os.O_RDWR)
    fcntl.flock(domain, fcntl.LOCK_EX | fcntl.LOCK_NB)
    (root / 'hold-startup').touch()
    c.result('resume', commandId='resume-b', stoppedCommandId='stop-a', target='b')
    boot = until(lambda: (v if (v := read(root / 'boot.json')) and v['instanceId'] != stopped['expected']['instanceId'] else None))
    assert count(root, 'launches.jsonl') == 2
    assert read(root / 'ready.json')['instanceId'] == stopped['expected']['instanceId']
    record('domain-lock-before-ready', newBoot=boot, current=unit_state(unit))

    # Every configured controller uses the same existing ServiceStore authority.
    competitor = Controller(root, unit, expect_ready=False)
    assert competitor.proc.wait(timeout=10) != 0
    competitor.log.flush()
    assert 'service_owner_already_running' in Path(competitor.log.name).read_text()
    assert count(root, 'launches.jsonl') == 2
    record('configured-launcher-exclusion')

    # Controller disappears after A exit/B launch and before B's readiness.
    c.crash()
    before = unit_state(unit)
    assert before['InvocationID'] == boot['invocationId']
    c = Controller(root, unit)
    second = c.result('resume', commandId='duplicate-resume', stoppedCommandId='stop-a', target='b')
    assert second['status'] == 'refused'
    negative = c.call('try-occupied-launch')
    assert negative.get('error') == 'existing_unit_tree_not_stopped', negative
    assert count(root, 'launches.jsonl') == 2
    fcntl.flock(domain, fcntl.LOCK_UN)
    os.close(domain)
    (root / 'hold-startup').unlink()
    ready = until(lambda: (v if (v := read(root / 'ready.json')) and v['instanceId'] == boot['instanceId'] else None))
    assert ready['intakeClosed'] is True
    assert c.result('receipt', commandId='resume-b')['status'] == 'unknown'
    # A rogue early-open ready reply is not sufficient for first activation.
    c.result('runtime', op='open', instanceId=boot['instanceId'])
    assert c.result('reconcile', commandId='resume-b')['status'] == 'unknown'
    assert count(root, 'launches.jsonl') == 2
    c.result('runtime', op='close', fence=None)
    record('first-activation-requires-closed-intake')
    # Legitimate release opens intake, but its lost ACK must reconcile without
    # requiring it to be closed again or launching another generation.
    (root / 'drop-release-ack').touch()
    pending = c.result('reconcile', commandId='resume-b')
    assert pending['status'] == 'ready' and pending['admissionSettlement']['state'] == 'unknown'
    assert c.result('runtime', op='inspect')['intakeClosed'] is False
    c.crash()
    c = Controller(root, unit)
    settled = c.result('reconcile', commandId='resume-b')
    assert settled['status'] == 'ready' and settled['admissionSettlement']['state'] == 'settled'
    assert settled['observed']['instanceId'] == boot['instanceId']
    assert c.result('reconcile', commandId='resume-b')['status'] == 'ready'
    assert c.result('runtime', op='inspect')['intakeClosed'] is False
    assert unit_state(unit)['InvocationID'] == before['InvocationID']
    assert count(root, 'launches.jsonl') == 2
    assert count(root, 'dispatches.jsonl') == 1
    assert (root / 'business.jsonl').read_bytes() == unknown
    probe = os.open(root / 'domain.lock', os.O_RDWR)
    try:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raise AssertionError('replacement did not own real domain lock')
        except BlockingIOError:
            pass
    finally:
        os.close(probe)
    record('controller-loss-same-generation-no-replay', resumed=settled,
           businessSha256=hashlib.sha256(unknown).hexdigest(), launches=2, dispatches=1)
    record('lost-release-ack-after-open-reconciles-same-generation', before=pending, after=settled)
    record('independent-role-interpreter', controller='node', runtime='/usr/bin/python3')
    c.result('stop', commandId='finish-b')
    c.receipt('finish-b', 'stopped')
    c.result('close')
    c.crash()

    # Parent exits but reparented setsid child still writes and owns inherited lock.
    c, root, unit = instance('reparented')
    c.result('stop', commandId='prepare-orphan')
    c.receipt('prepare-orphan','stopped')
    c.result('resume', commandId='orphan-ready', stoppedCommandId='prepare-orphan')
    c.receipt('orphan-ready','ready')
    with sqlite3.connect(root/'controller/service.sqlite3') as db:
        saved=json.loads(db.execute('SELECT value FROM commands WHERE id=?',('orphan-ready',)).fetchone()[0])
    assert saved['localCustody']['invocationId']==unit_state(unit)['InvocationID']
    (root/'business.jsonl').write_bytes(unknown)
    c.result('runtime', op='work')
    child = until(lambda: read(root / 'child.json'))
    c.result('runtime', op='exit-parent')
    live = until(lambda: (v if (v := read(root / 'child-live.json')) and v['parent'] != child['parent'] else None))
    state = unit_state(unit)
    assert state['ActiveState'] == 'active'
    assert 'populated 1' in (Path('/sys/fs/cgroup') / state['ControlGroup'].lstrip('/') / 'cgroup.events').read_text()
    assert c.call('try-occupied-launch').get('error') == 'existing_unit_tree_not_stopped'
    assert count(root, 'launches.jsonl') == 2
    record('reparented-child-blocks-replacement', child=live, unit=state)
    assert c.call('try-wrong-invocation',fromResumeCommandId='orphan-ready').get('error')=='unit_generation_conflict'
    # Lose the controller too: custody must come from the existing durable
    # operation record, not a live endpoint or an in-memory process reference.
    c.crash()
    c=Controller(root,unit)
    c.result('stop',commandId='wrong-orphan',fromResumeCommandId='orphan-ready',
             wrongGeneration=True,interrupt=True,authorizationId='fixture-explicit-interruption')
    wrong=c.receipt('wrong-orphan','refused')
    assert wrong['phase']=='ownership_unproven'
    assert (Path('/proc')/str(child['pid'])).exists()
    c.result('stop',commandId='interrupt-orphan',fromResumeCommandId='orphan-ready',
             interrupt=True,authorizationId='fixture-explicit-interruption')
    stopped=c.receipt('interrupt-orphan','stopped')
    assert stopped['phase']=='interrupted' and stopped['interruption']['outcome']=='interrupted'
    assert count(root,'history.jsonl')==0 and count(root,'dispatches.jsonl')==1
    assert (root/'business.jsonl').read_bytes()==unknown
    assert not unit_state(unit)['ControlGroup']
    c.result('resume',commandId='after-orphan',stoppedCommandId='interrupt-orphan')
    c.receipt('after-orphan','ready')
    assert count(root,'launches.jsonl')==3 and count(root,'dispatches.jsonl')==1
    record('durable-orphan-custody-explicit-interruption',custody=saved['localCustody'],
           rejected=wrong,interrupted=stopped,launches=3,dispatches=1,
           businessSha256=hashlib.sha256(unknown).hexdigest())
    c.result('stop',commandId='finish-orphan')
    c.receipt('finish-orphan','stopped')
    c.result('close')
    c.crash()

    # Explicit authority differs from both a deadline and a successful drain.
    c, root, unit = instance('interruption')
    c.result('runtime', op='work')
    until(lambda: read(root / 'child.json'))
    denied = c.call('stop', commandId='denied-interruption', interrupt=True, authorizationId='not-authorized')
    assert denied.get('error') == 'interruption_not_authorized'
    assert c.result('runtime', op='inspect')['activeWork'] == 1
    c.result('stop', commandId='interrupt', interrupt=True, authorizationId='fixture-explicit-interruption')
    interrupted = c.receipt('interrupt', 'stopped')
    assert interrupted['phase'] == 'interrupted'
    assert interrupted['interruption']['outcome'] == 'interrupted'
    assert count(root, 'history.jsonl') == 0
    c.result('resume', commandId='resume-interrupted', stoppedCommandId='interrupt')
    resumed = c.receipt('resume-interrupted', 'ready')
    assert c.result('receipt', commandId='interrupt')['phase'] == 'interrupted'
    assert count(root, 'dispatches.jsonl') == 1
    record('authorized-interruption-distinct-outcome', interrupted=interrupted, resumed=resumed)
    c.result('stop', commandId='finish-interruption')
    c.receipt('finish-interruption', 'stopped')
    c.result('close')
    c.crash()
    status = 'passed'
except BaseException as error:
    status = 'failed'
    results.append({'error': repr(error)})
    raise
finally:
    # Only these random, fixture-created units/controllers are eligible cleanup.
    for c in controllers:
        if c.proc.poll() is None:
            c.crash()
        c.log.close()
    cleanup = []
    for unit in units:
        ctl('kill', '--kill-whom=all', '--signal=SIGKILL', unit, check=False)
        ctl('stop', unit, check=False)
        ctl('reset-failed', unit, check=False)
        cleanup.append({'unit': unit, 'state': unit_state(unit)})
        link = Path('/run/user') / str(os.getuid()) / 'systemd/user' / unit
        if link.is_symlink() and link.resolve().is_relative_to(OUT):
            link.unlink()
    ctl('daemon-reload')
    packet = {'status': status, 'cases': results, 'cleanup': cleanup,
        'scope': 'source-only disposable Linux fixture; no signed/released/full-app acceptance',
        'knownLimits': ['shared Host admission and trusted role verifier are fixture ports',
            'legacy direct-child adapter remains separate',
            'interrupting an already-pending ordinary drain is not wired in this spike',
            'configured-launcher boundary excludes arbitrary same-user systemctl']}
    (OUT / 'RESULT.json').write_text(json.dumps(packet, indent=2) + '\n')
    print(json.dumps(packet, indent=2))
