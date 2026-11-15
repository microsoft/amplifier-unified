"""Fixed local cleanup loader. No operation payload or backend before permit.

This launcher is part of the trusted app invocation, not an arbitrary module
runner. Its two source inputs are captured once; compile/exec consumes those
exact buffers, never a source loader or bytecode cache. No sys.modules census
or reread of a pathname is represented as imported-byte proof.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import sys
import time
import types

LIMIT = 262144
CONTROL_LIMIT = 65536
READY_LIMIT = 16384
NAMES = ('environment_retention.py', 'test_environment_worker.py')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def receive(limit, seconds=20):
    """Read one private pipe frame without buffering beyond its newline."""
    data, deadline = bytearray(), time.monotonic() + seconds
    with selectors.DefaultSelector() as selector:
        selector.register(sys.stdin, selectors.EVENT_READ)
        while len(data) <= limit:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise ValueError('Cleanup permit was not received')
            raw = os.read(sys.stdin.fileno(), 1)
            if not raw:
                raise ValueError('Cleanup controller disconnected')
            if raw == b'\n':
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise ValueError('Invalid cleanup control')
                return value
            data.extend(raw)
    raise ValueError('Cleanup control exceeds its bound')


def capture():
    root = Path(__file__).parent
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError('Cleanup origin is not the selected adjacent installation')
    buffers = {}
    for name in NAMES:
        fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
                raise ValueError('Invalid cleanup source')
            raw = stream.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise ValueError('Cleanup source exceeds its bound')
            buffers[name] = raw
    return buffers


def load(buffers):
    modules = {}
    for name in NAMES:
        path = Path(__file__).with_name(name)
        module = types.ModuleType('_captured_' + path.stem)
        module.__file__ = str(path)
        if name == 'test_environment_worker.py':
            module.__dict__['_captured_retention'] = modules['environment_retention.py']
        exec(compile(buffers[name], str(path), 'exec', dont_inherit=True), module.__dict__)
        modules[name] = module
    return modules['test_environment_worker.py']


def main():
    context = receive(4096)
    if (set(context) != {'nonce', 'members'} or not isinstance(context['nonce'], str)
            or not re.fullmatch('[a-f0-9]{64}', context['nonce'])):
        raise ValueError('Invalid cleanup invocation')
    buffers = capture()
    members = [{'name': 'amplifier_web/' + name, 'bytes': len(buffers[name]),
                'sha256': hashlib.sha256(buffers[name]).hexdigest(),
                'capture': 'executed-source-buffer'} for name in NAMES]
    if members != context['members']:
        raise ValueError('Cleanup source does not match the qualified wheel')
    worker = load(buffers)
    ready = {'schema': 1, 'phase': 'loaded', 'nonce': context['nonce'],
             'pid': os.getpid(), 'start': Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19],
             'interpreter': sys.executable, 'prefix': sys.prefix,
             'members': members}
    encoded = json.dumps(ready, separators=(',', ':'))
    if len(encoded) > READY_LIMIT:
        raise ValueError('Cleanup readiness exceeds its bound')
    sys.stdout.write(encoded + '\n')
    sys.stdout.flush()
    # Two bounded host rechecks can each include a 30s backend inspection and
    # authority waits/source validation. Expiry is refusal, never probe reuse.
    permit = receive(CONTROL_LIMIT, seconds=180)
    if (set(permit) != {'nonce', 'readySha256', 'payload'}
            or permit['nonce'] != ready['nonce'] or permit['readySha256'] != digest(ready)
            or not isinstance(permit['payload'], dict)
            or permit['payload'].get('action') not in {'remove', 'finishCleanup'}):
        raise ValueError('Cleanup permit does not match this process')
    # There is no continuation/probe mode and no restart on EOF or timeout.
    try:
        value = worker.execute_call(permit['payload'])
    except worker.RemovalFailure as error:
        value = error.receipt
    sys.stdout.write(json.dumps(value, allow_nan=False))
    sys.stdout.flush()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Before a permit this is a loader refusal; after a permit it is UNKNOWN.
        # Only the host knows which side of the durable gate was reached.
        sys.exit(1)