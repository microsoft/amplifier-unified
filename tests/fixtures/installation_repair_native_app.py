"""Controlled stdlib app wheel for installation_repair_native.py, NOT Unified.

Only health/doctor/package qualification and release-download trust are substituted.
Controller dispatch, copied worker/reset/artifact code, managers, processes, uv,
archive/source verification, filesystem swaps and kernel leases are real.
"""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import signal
import ssl
import subprocess
import sys
import time
import uuid


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)


def event(root, kind, **facts):
    # Each JSON line is one O_APPEND write, below PIPE_BUF; bounded public facts.
    raw = (json.dumps({'kind': kind, 'pid': os.getpid(), 'parentPID': os.getppid(), **facts}, sort_keys=True) + '\n').encode()
    fd = os.open(Path(root) / 'effects.jsonl', os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)


def probe():
    """Explicit fixture substitute executed in each independent installed Python."""
    import importlib.metadata
    config = json.loads(Path(os.environ['NATIVE_FIXTURE_CONFIG']).read_text())
    root = Path(config['caseRoot'])
    stage = 'fresh' if Path(sys.prefix) == Path(config['toolRoot']) / 'amplifier-unified' else 'candidate'
    assert sys.flags.isolated and sys.version_info[:2] == (3, 13)
    assert importlib.metadata.version('amplifier-unified') == config['version']
    stamp = json.loads(Path(__file__).with_name('release-source.json').read_text())
    assert stamp['revision'] == config['revision'] and stamp['version'] == config['version']
    event(root, 'probe', stage=stage, prefix=str(Path(sys.prefix)))
    if config['case'] == 'happy' and stage == 'candidate':
        (root / 'probe-waiting').touch(mode=0o600)
        deadline = time.monotonic() + 60
        while not (root / 'probe-release').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('fixture probe barrier timed out')
            time.sleep(.1)
    if config['case'] == 'preflight' or config['case'] == 'rollback' and stage == 'fresh':
        raise RuntimeError('controlled fixture qualification failure')
    print('AMPLIFIER_UPDATE_PROBE=' + json.dumps({
        'ok': True, 'isolated': bool(sys.flags.isolated), 'version': config['version']}))


def current_admission(home):
    path = home / 'installation-repair/admissions.json'
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    assert value['schema'] == 1 and value['current'] in value['previews'].values()
    return value['current']


def dispatch(home, instance, host_instance, folder, *, launch=True):
    from . import installation_repair as controller, installation_repair_worker as worker, reset
    plan = worker.read(folder / 'plan.json')
    config = json.loads((home / 'fixture.json').read_text())
    definition = Path(config['definition'])
    info = home.stat()
    assert all(worker.executable_identity(plan[key]) == plan['executables'][key]
               for key in ('uv', 'basePython'))
    plan.update(
        recoverySchema=1,
        home=str(home), dataStat=[info.st_dev, info.st_ino], uid=os.getuid(),
        hostInstanceId=host_instance,
        runningIdentity={'version': config['version'], 'revision': config['revision'], 'instanceId': instance},
        bootstrap=worker.bootstrap_identity(Path(plan['toolRoot']) / 'amplifier-unified'),
        launcher=worker.file_identity(Path(plan['binRoot']) / 'amplifier-unified'),
        uvReceiptDigest=worker._digest(Path(plan['toolRoot']) / 'amplifier-unified/uv-receipt.toml'),
        service={'commands': [list(row) if row is not None else None for row in reset.service_commands(home)],
                 'path': str(definition), 'sha256': worker._digest(definition), 'pid': os.getpid(),
                 'processStart': subprocess.check_output(
                     ['ps', '-p', str(os.getpid()), '-o', 'lstart='], text=True).strip()})
    operation = worker.read(folder / 'operation.json')
    operation['hostInstanceId'] = host_instance
    if sys.platform == 'darwin':
        prepare = getattr(controller, '_prepare_launchd_job', None)
        if not callable(prepare):
            raise RuntimeError('joined production controller lacks private one-shot launchd preparation')
        # The real controller owns the bytes and closed metadata. Preparation
        # must precede plan/receipt publication and the atomic admission.
        operation['helperJob'] = plan['helperJob'] = prepare(folder, plan)
    else:
        operation['helperJob'] = controller.helper_job(plan['operationId'])
    worker.write(folder / 'plan.json', plan)
    worker.write(folder / 'operation.json', operation)
    # This is a controlled fixture admission, NOT production confirm/binding.
    # Mirror the exact atomic admission format, not the retired split records.
    worker.write(home / 'installation-repair/admissions.json', {
        'schema': 1, 'current': plan['operationId'], 'previews': {plan['previewId']: plan['operationId']}})
    event(config['caseRoot'], 'admit', operationId=plan['operationId'], helperJob=operation['helperJob'])
    # Ownership of this exact one-shot label was saved by the harness BEFORE
    # this request. Never retry this dispatch if the response is lost.
    if launch:
        event(config['caseRoot'], 'dispatch', operationId=plan['operationId'])
        controller.launch(folder, plan)


def tls_server(config, handler):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config['tls']['cert'], config['tls']['key'])
    server = HTTPServer(('127.0.0.1', config['port']), handler)
    try:
        server.socket = context.wrap_socket(server.socket, server_side=True)
    except BaseException:
        server.server_close()
        raise
    return server


def main():
    os.umask(0o077)
    args = sys.argv[1:]
    home = Path(args[args.index('--data-dir') + 1])
    config = json.loads((home / 'fixture.json').read_text())
    if 'doctor' in args:
        ok = not (Path(config['caseRoot']) / 'doctor-fail').exists()
        event(config['caseRoot'], 'doctor', ok=ok)
        print(json.dumps({'ok': ok, 'substitute': 'native-manager-fixture-not-Unified'}))
        return 0 if ok else 1
    from . import reset
    token = (home / 'config/auth/control-token').read_text().strip()
    instance = uuid.uuid4().hex
    host_instance = str(uuid.uuid4())
    stamp = json.loads(Path(__file__).with_name('release-source.json').read_text())
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def authorized(self):
            return self.headers.get('Authorization') == 'Bearer ' + token

        def reply(self, status, value):
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if not self.authorized() or self.path != '/api/health':
                return self.reply(403, {})
            operation = current_admission(home)
            self.reply(200, {'ok': True, 'app': 'amplifier-unified', 'instanceId': instance,
                            'hostInstanceId': host_instance,
                            'dataIdentity': hashlib.sha256(str(home).encode()).hexdigest(),
                            'version': stamp['version'], 'revision': stamp['revision'],
                            'installationRepair': {'protocol': 1, 'operationId': operation}})

        def do_POST(self):
            if not self.authorized() or self.path not in {'/fixture-dispatch', '/fixture-admit'}:
                return self.reply(403, {})
            # Single purpose local fixture interface: never exposed by production.
            raw = self.rfile.read(min(int(self.headers.get('Content-Length', 0)), 1024))
            identity = json.loads(raw)['operationId']
            assert len(identity) == 32 and all(c in '0123456789abcdef' for c in identity)
            dispatch(home, instance, host_instance, home / 'installation-repair' / identity,
                     launch=self.path == '/fixture-dispatch')
            self.reply(200, {'admitted': identity})

    with reset.repair_lock(home, shared=True):
        server = tls_server(config, Handler)
        server.timeout = .2
        event(config['caseRoot'], 'service-start', instanceId=instance)
        save(Path(config['caseRoot']) / 'host.json', {'pid': os.getpid(), 'instanceId': instance})
        try:
            while not stopping:
                server.handle_request()
        finally:
            server.server_close()
            event(config['caseRoot'], 'service-stop', instanceId=instance)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
