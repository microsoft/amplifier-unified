"""Run the Context Intelligence seam against a private real server and database.

The database must be a disposable loopback test instance. No production URL,
credential, account, model, or user data is used. Source acquisition and package
installation are explicit workflow steps, not hidden test-time downloads.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def run(python, work, output, database):
    parsed = urlparse(database)
    if parsed.scheme != 'bolt' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Only an isolated loopback Bolt database is accepted')
    work.mkdir(parents=True, exist_ok=True)
    work.chmod(0o700)
    for name in ('home', 'tmp', 'blobs', 'queues', 'logs'):
        (work / name).mkdir(exist_ok=True)
    key = secrets.token_urlsafe(32)
    keyfile = work / 'test-key'
    keyfile.write_text(key)
    keyfile.chmod(0o600)
    config = work / 'server.json'
    config.write_text(json.dumps({
        'neo4j_url': database, 'neo4j_user': 'neo4j', 'neo4j_password': '',
        'api_key': key, 'auth_mode': 'static',
        'api_keys_store_path': str(work / 'api-keys.json'),
        'entra_identities_store_path': str(work / 'entra-identities.json'),
        'blob_path': str(work / 'blobs'), 'queues_path': str(work / 'queues'),
        'log_path': str(work / 'logs/server.jsonl'),
    }))
    config.chmod(0o600)
    env = {k: v for k, v in os.environ.items() if not k.startswith('AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_')}
    env.update(HOME=str(work / 'home'), TMPDIR=str(work / 'tmp'),
               AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_CONFIG_FILE=str(config))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'server-process.log').open('w') as log:
        process = subprocess.Popen([python, '-m', 'uvicorn',
                                    'context_intelligence_server.main:create_asgi_app', '--factory',
                                    '--host', '127.0.0.1', '--port', str(port)],
                                   cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Private Context Intelligence server exited before readiness')
                try:
                    with urlopen(Request(url + '/status', headers={'Authorization': 'Bearer ' + key}), timeout=2) as response:
                        if response.status == 200:
                            break
                except (URLError, TimeoutError):
                    time.sleep(.2)
            else:
                raise RuntimeError('Private Context Intelligence server did not become ready')
            env.update(UNIFIED_CI_TEST_URL=url, UNIFIED_CI_TEST_KEY_FILE=str(keyfile))
            return subprocess.run([python, str(ROOT / 'scripts/integration_checks.py'), 'run',
                                   '--lane', 'context-intelligence', '--python', python,
                                   '--output', str(output)], cwd=ROOT, env=env).returncode
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise RuntimeError('Private server did not stop cleanly')
            keyfile.unlink(missing_ok=True)
            config.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--database', default='bolt://127.0.0.1:17687')
    args = parser.parse_args()
    raise SystemExit(run(str(Path(args.python).absolute()), args.work.resolve(), args.output.resolve(), args.database))
