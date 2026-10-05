"""Synthetic role: real file lock, accepted child writer and closed-intake startup.
No credentials, model calls or real user history are read by this fixture.
"""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)

def save(name, value):
    tmp = root / (name + '.next')
    tmp.write_text(json.dumps(value))
    tmp.replace(root / name)

def append(name, value):
    with (root / name).open('a') as stream:
        stream.write(json.dumps(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())

if len(sys.argv) > 2 and sys.argv[2] == 'child':
    # Inherited open lock description survives parent exit/reparenting.
    save('child.json', {'pid': os.getpid(), 'parent': os.getppid()})
    while not (root / 'finish-child').exists():
        save('child-live.json', {'pid': os.getpid(), 'parent': os.getppid()})
        time.sleep(.02)
    append('history.jsonl', {'command': 'accepted-work', 'result': 'completed'})
    sys.exit(0)

identity = {
    'instanceId': os.environ['AMPLIFIER_UNIT_INSTANCE'],
    'dataScope': os.environ['AMPLIFIER_UNIT_SCOPE'],
    'releaseDigest': os.environ['AMPLIFIER_UNIT_RELEASE'],
    'invocationId': os.environ['INVOCATION_ID'],
}
append('launches.jsonl', identity)
lock = os.open(root / 'domain.lock', os.O_CREAT | os.O_RDWR, 0o600)
save('boot.json', identity)
# Actual independent storage lock acquisition precedes any ready observation.
fcntl.flock(lock, fcntl.LOCK_EX)
while (root / 'hold-startup').exists():
    time.sleep(.02)

state = {**identity, 'ready': True, 'intakeClosed': True, 'activeWork': 0,
         'fence': None, 'domainLockHeld': True}
child = None
sock_path = root / 'runtime.sock'
sock_path.unlink(missing_ok=True)
server = socket.socket(socket.AF_UNIX)
server.bind(str(sock_path))
os.chmod(sock_path, 0o600)
server.listen()
save('ready.json', state)
while True:
    conn, _ = server.accept()
    with conn:
        raw = b''
        while b'\n' not in raw:
            chunk = conn.recv(65536)
            if not chunk:
                break
            raw += chunk
        request = json.loads(raw)
        op = request['op']
        if child is not None and child.poll() is not None:
            child = None
        state['activeWork'] = int(child is not None)
        result = {'ok': True}
        if op == 'work':
            if state['intakeClosed']:
                result = {'ok': False, 'reason': 'intake_closed'}
            elif child is not None:
                result = {'ok': False, 'reason': 'already_accepted'}
            else:
                (root / 'finish-child').unlink(missing_ok=True)
                append('dispatches.jsonl', {'command': 'accepted-work'})
                child = subprocess.Popen([sys.executable, __file__, str(root), 'child'],
                                         pass_fds=(lock,), start_new_session=True)
                state['activeWork'] = 1
        elif op == 'callback':
            # The accepted command's completion callback remains usable while
            # fresh work is closed; it does not create another accepted command.
            (root / 'finish-child').touch()
        elif op == 'close':
            state['intakeClosed'] = True
            state['fence'] = request['fence']
        elif op == 'open':
            if request['instanceId'] != identity['instanceId']:
                result = {'ok': False, 'reason': 'wrong_generation'}
            else:
                state['intakeClosed'] = False
        elif op == 'exit-parent':
            conn.sendall(b'{"ok":true}\n')
            os._exit(0)
        elif op != 'inspect':
            result = {'ok': False, 'reason': 'unsupported'}
        if op == 'inspect':
            result = state.copy()
        save('ready.json', state)
        conn.sendall((json.dumps(result) + '\n').encode())
