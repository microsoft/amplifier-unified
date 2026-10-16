"""Isolated browser fixture: real task actions/receipts, controlled DTU transport."""
import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiohttp import web
from amplifier_web.server import create_app
from amplifier_web import task_environments as environments

TOKEN = 'fixture-browser-control-token'


class Runtime:
    def __init__(self):
        self.calls = []

    async def start(self, session, emit, **kwargs):
        self.calls.append('start')
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'ready'})

    async def stop(self, *args, **kwargs):
        self.calls.append('stop')

    async def close(self):
        pass

    async def control(self, *args, **kwargs):
        self.calls.append('control')
        return {}


async def main(home, port):
    home = home.resolve()
    os.environ['AMPLIFIER_HOME'] = str(home / 'native')
    os.environ['AMPLIFIER_WEB_HOME'] = str(home)
    os.environ['AMPLIFIER_UNIFIED_IMPORT_HOME'] = str(home / 'legacy')
    workspace = home / 'workspace'
    workspace.mkdir()
    (workspace / 'check.py').write_text("print('workspace check passed')\n")
    calls, states, mode = [], {}, {'executeTimeout': False, 'capacityAvailable': True,
                                 'backendAvailable': True, 'longOutput': False, 'executeFailure': False}
    lock = threading.Lock()
    config = {'python': '/fixture/managed/python', 'dockerHost': 'unix:///fixture/docker.sock', 'image': environments.APPROVED_IMAGE}
    environments.configuration = lambda app: (config, 'ready', 'Browser fixture: controlled Linux test backend; no Docker or provider calls.') if mode['backendAvailable'] else (None, 'unavailable', 'New execution is unavailable in this controlled fixture.')
    environments.validate_configuration_value = lambda app, value: (config, 'ready', 'Retained controlled fixture backend.')
    backend_identity = {'backend': 'docker', 'daemon': 'fixture-daemon'}
    def invoke(config, home, row, action, args):
        with lock:
            calls.append({'action': action, 'requestId': args.get('requestId'), 'resourceId': row['id']})
            identity = 'fixture-' + row['id']
            if action == 'prepare':
                (environments.storage.directory(home, row) / 'source.tar').write_bytes(b'fixture-retained-source')
                return {'ok': True, 'universeId': identity, 'backendIdentity': backend_identity, 'snapshot': {'snapshot_commit': 'fixture-snapshot', 'snapshot_tree': 'fixture-tree',
                    'source_head': 'fixture-head', 'source_branch': 'fixture-branch', 'files': 1, 'archive_bytes': 23, 'archive_sha256': 'fixture-archive'}}
            if action == 'start':
                states[row['id']] = 'running'
            elif action == 'stop':
                states[row['id']] = 'stopped'
            elif action == 'remove':
                states[row['id']] = 'removed'
            elif action == 'execute' and mode['executeTimeout']:
                return {'ok': False, 'status': 'unknown', 'universeId': identity,
                    'message': 'The fixture command timed out. It may still be running; inspect or stop it without replaying this request.'}
            value = {'ok': True, 'universeId': identity, 'status': states.get(row['id'], 'unknown')}
            if action == 'execute':
                output = ('queue-page-start\n' + 'retained check line\n' * 400 + 'queue-page-end\n'
                          if mode['longOutput'] else 'workspace check passed\n')
                value['result'] = {'exit_code': 1 if mode['executeFailure'] else 0,
                    'stdout': output, 'stderr': 'fixture check failed\n' if mode['executeFailure'] else '',
                    'stdout_truncated': False, 'stderr_truncated': False}
            if action == 'reconcile':
                value['observation'] = {'backendIdentity': backend_identity, 'rootOwner': row['sessionId'],
                    'operationId': row['launchOperationId'], 'universeId': identity, 'ownership': 'matched', 'absenceConfirmed': False}
            if action == 'remove':
                value['retention'] = {'verified': True, 'sourceBytes': 23, 'archiveBytes': 2025,
                    'totalBytes': 2048, 'format': 'tar', 'policy': 'files'}
            return value
    environments.invoke = invoke
    environments.capacity = lambda app, config: {'available': mode['capacityAvailable'],
        'counts': {'active': 0 if mode['capacityAvailable'] else 1}, 'limits': {'max_running': 1},
        'blockers': [] if mode['capacityAvailable'] else [{'code': 'execution_capacity'}]}
    runtime = Runtime()
    app = await create_app(home, workspace=workspace, runtime=runtime, voice=False, background_updates=False)
    app['control_token'] = TOKEN
    app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
    service = app['service']
    await service.dispatch('session.create', {'title': 'Test environment acceptance', 'workspace': str(workspace), 'bundle': 'anchors'})
    await service.dispatch('view.update', {'patch': {'setupJourney': 'done', 'panel': None}})
    def authorize(request):
        if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
            raise web.HTTPForbidden()
    async def observations(request):
        authorize(request)
        sid = service.state['selectedSessionId']
        with lock:
            observed = {'calls': list(calls), 'callCounts': dict(Counter(call['action'] for call in calls)), 'states': dict(states), 'mode': dict(mode)}
        observed['requests'] = service.operations.requests.list(sid)
        observed['sessionId'] = sid
        observed['workspaceId'] = next(row['id'] for row in service.state['workspaces'] if row['path'] == str(workspace))
        observed['messages'] = service.state['sessions'][0].get('messages', [])
        observed['runtimeCalls'] = list(runtime.calls)
        observed['workspaceFile'] = (workspace / 'check.py').read_text()
        return web.json_response(observed)
    async def change_mode(request):
        authorize(request)
        value = await request.json()
        with lock:
            mode['executeTimeout'] = bool(value.get('executeTimeout', value.get('mode') == 'execute-timeout'))
            if 'capacityAvailable' in value:
                mode['capacityAvailable'] = bool(value['capacityAvailable'])
            if 'backendAvailable' in value:
                mode['backendAvailable'] = bool(value['backendAvailable'])
            for key in ('longOutput', 'executeFailure'):
                if key in value:
                    if type(value[key]) is not bool:
                        raise web.HTTPBadRequest(text=key + ' must be boolean')
                    mode[key] = value[key]
        service.test_queue.backoff.clear()
        service.test_queue.wakeup.set()
        return web.json_response(mode)
    app.router.add_get('/fixture/environments', observations)
    app.router.add_post('/fixture/environment-mode', change_mode)
    return app


async def serve(home, port):
    app = await main(home, port)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', port)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url, 'sessionId': app['service'].state['selectedSessionId']}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--interactive', action='store_true', help='Enable fixture-only login for manual browser acceptance on loopback.')
    options = parser.parse_args()
    if options.interactive:
        # This disposable fixture has no real account, provider or backend data.
        # Preserve the real login/cookie/CSRF flow for computer-use acceptance.
        from amplifier_web import auth
        auth.authenticate_pam = lambda username, password: username == 'fixture' and password == TOKEN
    with tempfile.TemporaryDirectory(prefix='amplifier-environments-ui-') as temporary:
        asyncio.run(serve(Path(temporary), options.port))
