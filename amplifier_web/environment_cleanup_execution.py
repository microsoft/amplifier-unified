"""Serving-wheel binding and same-invocation gate for local Docker cleanup.

Private adapter only: no action accepts hashes, paths, code or permits. The
updater's original authenticated download/qualification is the trust root, not
RECORD, direct_url, a source stamp, or a caller assertion. This does not attest
the interpreter or the DTU/stdlib dependency closure.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import selectors
import time
import uuid

from . import app_release_artifacts as artifacts, update_readiness

NAMES = ('environment_retention.py', 'test_environment_worker.py')
LAUNCHER = 'environment_cleanup_launcher.py'
READY_LIMIT = 16384
# Execute only a host-captured, qualified, fixed app launcher. It must not be
# reopened by Python's script/source loader (including an unchecked .pyc).
# The private first frame is consumed bytewise so the launcher's next frame
# cannot be stranded in a different BufferedReader.
BOOTSTRAP = """import os,json
b=bytearray()
while len(b)<=524288:
 c=os.read(0,1)
 if c==b'\\n': break
 if not c: raise SystemExit(1)
 b.extend(c)
else: raise SystemExit(1)
v=json.loads(b)
exec(compile(bytes.fromhex(v['source']),v['origin'],'exec',dont_inherit=True),{'__name__':'__main__','__file__':v['origin']})
"""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def send(process, value, *, seconds=10, maximum=524288, before_commit=None):
    """Bound pipe backpressure; a failed permit write remains uncertain."""
    raw = json.dumps(value, allow_nan=False).encode()
    if len(raw) > maximum:
        raise ValueError('Cleanup control exceeds its bound')
    fd, offset, deadline = process.stdin.fileno(), 0, time.monotonic() + seconds
    os.set_blocking(fd, False)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_WRITE)
            while offset < len(raw):
                remaining = deadline - time.monotonic()
                if process.poll() is not None or remaining <= 0 or not selector.select(remaining):
                    raise ValueError('Cleanup control delivery was not confirmed')
                try:
                    offset += os.write(fd, raw[offset:])
                except BlockingIOError:
                    continue
            # Without the newline, the helper cannot parse/act on the body.
            # Run the final guard AFTER transport waits and immediately before
            # an atomic nonblocking commit. Any possibly committed write is
            # still uncertain; callers must never infer not_sent from an error.
            deadline = time.monotonic() + seconds
            while True:
                if before_commit is not None:
                    before_commit()
                try:
                    if os.write(fd, b'\n') != 1:
                        raise ValueError('Cleanup commit delivery was not confirmed')
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ValueError('Cleanup commit delivery was not confirmed')
    finally:
        os.set_blocking(fd, True)


def context(app):
    manager = app.update_manager
    captured = getattr(manager, '_running_artifact', None)
    current = (update_readiness._observed_artifact(
        manager, captured['qualification'], captured['installation']) if captured else None)
    if not current or not manager.running_identity.get('instanceId'):
        raise ValueError('Cleanup requires the qualified serving application wheel')
    return {'serving': copy.deepcopy(manager.running_identity),
            'hostInstanceId': app.instance_id, **current}


class Gate:
    def __init__(self, app):
        self.app = app
        self.context = context(app)
        manager = app.update_manager
        # Cache only authenticated immutable member bytes, never eligibility.
        # Every gate and permit revalidates the actual serving installation.
        key = digest(self.context)
        cached = getattr(manager, '_cleanup_members', None)
        if cached is None or cached[0] != key:
            names = ['amplifier_web/' + name for name in (*NAMES, LAUNCHER)]
            raw = artifacts.captured_members(self.context['artifact'],
                self.context['serving']['version'], self.context['serving']['revision'], names)
            cached = (key, tuple((name, raw[name]) for name in names))
            manager._cleanup_members = cached
        self.raw = dict(cached[1])
        self.members = [{'name': 'amplifier_web/' + name,
            'bytes': len(self.raw['amplifier_web/' + name]),
            'sha256': hashlib.sha256(self.raw['amplifier_web/' + name]).hexdigest(),
            'capture': 'executed-source-buffer'} for name in NAMES]
        self.nonce = uuid.uuid4().hex + uuid.uuid4().hex

    def bootstrap(self):
        # The exact qualified launcher buffer is the executed input. __file__
        # supplies a fixed adjacent origin, never a request-selected path.
        origin = Path(__file__).with_name(LAUNCHER)
        return {'source': self.raw['amplifier_web/' + LAUNCHER].hex(),
                'origin': str(origin)}

    def receive(self, process, seconds=20):
        data, deadline = bytearray(), time.monotonic() + seconds
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, data)
            selector.register(process.stderr, selectors.EVENT_READ, None)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('Cleanup readiness timed out')
                for item, _ in selector.select(min(1, remaining)):
                    chunk = os.read(item.fileobj.fileno(), 1 if item.data is data else 4096)
                    if not chunk:
                        selector.unregister(item.fileobj)
                        continue
                    if item.data is None:
                        # No diagnostics are part of the readiness protocol.
                        raise ValueError('Cleanup loader reported an error')
                    if chunk == b'\n':
                        return json.loads(data)
                    data.extend(chunk)
                    if len(data) > READY_LIMIT:
                        raise ValueError('Cleanup readiness exceeded its bound')
        raise ValueError('Cleanup helper exited before readiness')

    def validate(self, ready, process, python):
        if (not isinstance(ready, dict)
                or set(ready) != {'schema', 'phase', 'nonce', 'pid', 'start', 'interpreter', 'prefix', 'members'}
                or type(ready['schema']) is not int or ready['schema'] != 1
                or ready['phase'] != 'loaded' or ready['nonce'] != self.nonce
                or type(ready['pid']) is not int or ready['pid'] != process.pid
                or ready['interpreter'] != python
                or ready['prefix'] != str(Path(python).parent.parent)
                or ready['members'] != self.members
                or process.poll() is not None):
            raise ValueError('Cleanup loaded-input proof does not match this invocation')
        start = Path('/proc', str(process.pid), 'stat').read_text().rsplit(')', 1)[1].split()[19]
        if ready['start'] != start:
            raise ValueError('Cleanup helper lifecycle changed')
        return (str(process.pid), start)

    def recheck(self):
        if context(self.app) != self.context:
            raise ValueError('The qualified serving application changed during cleanup readiness')

    def evidence(self, ready):
        return {'schema': 1, 'status': 'loaded-awaiting-permit',
                'serving': self.context['serving'], 'hostInstanceId': self.context['hostInstanceId'],
                'installationSha256': digest(self.context['installation']),
                'qualificationSha256': digest(self.context['qualification']),
                'wheelSha256': self.context['artifact']['sha256'],
                'ready': ready, 'readySha256': digest(ready)}