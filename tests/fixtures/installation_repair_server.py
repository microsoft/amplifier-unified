"""Actual AppService/HTTP/static/SSE pairing; installation effects are synthetic.

Never runs an installer, detached helper, service command, provider or runtime
worker. Only this child's private operation.json may be advanced by the fixture.
This is not native installation or restart acceptance.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web
import settings_ui_server as settings
from amplifier_web import app_release_artifacts, installation_repair as repair, reset
from amplifier_web.auth import SESSION_COOKIE, new_session
from amplifier_web.installation_repair_worker import executable_identity


TOKEN = 'fixture-browser-control-token'
SECRET = 'INSTALLATION_REPAIR_FIXTURE_CREDENTIAL'
ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity():
    """Bind the actual checkout, not the mutable peer implementation."""
    paths = [
        'components/web-client.source.json', '.build/web-client/.client-source.json',
        'amplifier_web/static/build.json', 'amplifier_web/static/client-component.json',
        'amplifier_web/service.py', 'amplifier_web/server.py',
        'amplifier_web/installation_repair.py', 'amplifier_web/installation_repair_worker.py',
        'tests/fixtures/installation_repair_server.py',
        'tests/web/installation-repair-host-browser.mjs', 'tests/web/browser-checks.json',
    ]
    return {name: digest(ROOT / name) for name in paths}


async def main(home):
    calls = []

    class IdleRuntime(settings.Runtime):
        def __init__(self):
            super().__init__()
            self.workers = {}
            self._preparations = {}

        def has_pending_operations(self):
            return False

        @contextmanager
        def stopped_boundary(self, _session):
            assert not self.workers and not self._preparations
            yield

        async def start(self, *args, **kwargs):
            calls.append('start')
            raise AssertionError('Installation repair must not warm a runtime')

        async def control(self, *args, **kwargs):
            calls.append('control')
            raise AssertionError('Installation repair must not control a runtime')

        async def stop(self, *args, **kwargs):
            calls.append('stop')
            raise AssertionError('Installation repair must not stop a runtime')

        async def send(self, *args, **kwargs):
            calls.append('send')
            raise AssertionError('Installation repair must not send conversation input')

    # Reuse maintained Settings initialization without starting background work
    # or provider preparation. Do not close/reopen UpdateManager: closed managers
    # correctly refuse admission and are not an idle-host fixture.
    create_app = settings.create_app

    async def isolated_app(*args, **kwargs):
        kwargs.update(background_updates=False, background_work=False, preload_providers=False)
        return await create_app(*args, **kwargs)

    settings.create_app = isolated_app
    settings.Runtime = IdleRuntime
    app = await settings.main(home)
    try:
        return configure(app, home, calls)
    except BaseException:
        await app['service'].close()
        raise


def configure(app, home, calls):
    journey = os.environ.get('INSTALLATION_REPAIR_FIXTURE_JOURNEY', 'verify')
    assert journey in {'verify', 'reconcile'}
    service = app['service']
    manager = service.update_manager
    assert not manager.closed
    assert all(getattr(manager, key, None) is None for key in
               ('task', 'check_task', 'readiness_task', 'cleanup_task'))
    session = service._session()
    session.update(status='idle', messages=[{
        'id': 'installation-repair-retained-message', 'role': 'assistant',
        'text': 'Keep this saved installation repair conversation.', 'createdAt': 1,
    }])
    sid = session['id']
    service._publish_full(reason='Private installation repair browser fixture')
    # A real owner-private file satisfies the controller's executable identity
    # check. Its contents are deliberately NOT an interpreter. The module-local
    # subprocess seam below must intercept every invocation; never execute it.
    base_python = home / 'synthetic-base-python-not-executed'
    base_python.write_bytes(b'Synthetic fixture identity only; never execute.\n')
    base_python.chmod(0o700)
    expected = {
        'home': str(home), 'uid': os.getuid(), 'hostInstanceId': service.instance_id,
        'extras': ['native-desktop'], 'basePython': str(base_python),
        'executables': {'basePython': executable_identity(base_python)},
        'env': {'UV_INDEX_PASSWORD': SECRET}, 'pointer': {'exists': False, 'sha256': None},
    }
    repair.binding = AsyncMock(side_effect=lambda _: copy.deepcopy(expected))
    metadata_calls = []
    download_calls = []

    def release_metadata(env):
        assert env == expected['env']
        metadata_calls.append('stable-release')
        return {'tag_name': 'v99.0.0'}

    def download_command(*args, **kwargs):
        assert args == ('git', 'ls-remote', 'https://github.com/' + reset.REPOSITORY,
                        'refs/tags/v99.0.0', 'refs/tags/v99.0.0^{}')
        assert kwargs['env'] == expected['env']
        download_calls.append('exact-tag')
        return SimpleNamespace(stdout='a' * 40 + '\trefs/tags/v99.0.0\n')

    reset.release_metadata = release_metadata
    reset.download_command = download_command
    app_release_artifacts.prepare = AsyncMock(return_value={
        'url': 'https://github.com/microsoft/amplifier-unified/releases/download/'
               'v99.0.0/amplifier_unified-99.0.0-py3-none-any.whl',
        'path': str(home / 'synthetic-wheel-not-executed.whl'), 'sha256': 'b' * 64,
    })
    launched = []

    def launch(folder, plan):
        # Replace only the native effect boundary, using the same authoritative
        # receipt the real worker would own. No subprocess or helper is started.
        assert folder == repair.root(service) / plan['operationId']
        assert plan['home'] == str(home) and plan['hostInstanceId'] == service.instance_id
        launched.append({'operationId': plan['operationId'], 'previewId': plan['previewId']})
        assert len(launched) == 1, 'Only one synthetic helper may ever be admitted'
        path = folder / 'operation.json'
        operation = repair._read(path, home)
        assert operation['status'] == 'accepted'
        assert repair._admissions(service) == {
            'schema': 1, 'current': plan['operationId'],
            'previews': {plan['previewId']: plan['operationId']}}
        for name in ('installation_repair_worker.py', 'reset.py', 'reset_retention.py',
                     'app_release_artifacts.py'):
            assert digest(folder / name) == digest(ROOT / 'amplifier_web' / name)
            repair._private(folder / name, home)
        operation.update(status='unknown', stage='fixture-outcome-unknown',
                         detail='Synthetic helper outcome unknown. Inspect this receipt; do not repeat.',
                         updatedAt=time.time())
        repair._write(path, operation, home)
        # Real confirm() must retain the admitted identity and return this saved
        # unknown receipt, not turn a helper-dispatch error into a new attempt.
        raise RuntimeError('Synthetic helper dispatch uncertainty')

    repair.launch = launch
    verification = {'calls': [], 'healthy': False}
    reconciliation = {'calls': [], 'positive': False, 'writes': 0}

    def verify_child(command, **kwargs):
        # The controller's ordinary --verify child boundary remains real; only
        # its health/artifact/graph probe result is synthetic in this UI fixture.
        assert len(launched) == 1
        folder = repair.root(service) / launched[0]['operationId']
        assert command == [str(base_python), '-I', '-S',
                           str(folder / 'installation_repair_worker.py'), '--verify', str(folder)]
        assert Path(kwargs['cwd']) == folder
        operation = repair._read(folder / 'operation.json', home)
        assert operation['operationId'] == launched[0]['operationId']
        assert operation['previewId'] == launched[0]['previewId']
        assert operation['status'] == 'reinstalled' and operation['reinstalled'] is True
        verification['calls'].append({'operationId': operation['operationId'],
                                      'healthy': verification['healthy']})
        ready = verification['healthy']
        operation.update(status='ready' if ready else 'reinstalled',
                         stage='fixture-verified' if ready else 'fixture-checks-incomplete',
                         healthVerified=ready, serviceState='running',
                         detail='Synthetic read-only verification; no installation or service command ran.',
                         updatedAt=max(time.time(), operation['updatedAt'] + .001))
        repair._write(folder / 'operation.json', operation, home)
        return SimpleNamespace(returncode=0)

    def inspect_child(command, **kwargs):
        assert len(launched) == 1
        folder = repair.root(service) / launched[0]['operationId']
        assert command[:4] == [str(base_python), '-I', '-S',
                               str(folder / 'installation_repair_worker.py')]
        assert command[5] == str(folder)
        assert kwargs == {'cwd': folder, 'stdin': subprocess.DEVNULL,
                          'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL,
                          'timeout': 240 if command[4] == '--verify' else 45, 'check': True}
        if command[4] == '--verify':
            assert len(command) == 6
            assert journey == 'verify'
            return verify_child(command, **kwargs)
        assert command[4] == '--reconcile'
        assert len(command) == 7 and command[6] == service.instance_id
        path = folder / 'operation.json'
        operation = repair._read(path, home)
        assert operation['operationId'] == launched[0]['operationId']
        assert operation['previewId'] == launched[0]['previewId']
        assert operation['hostInstanceId'] == service.instance_id
        assert operation['status'] == 'unknown' and operation['reinstalled'] is False
        reconciliation['calls'].append({'operationId': operation['operationId'],
                                        'positive': reconciliation['positive']})
        if not reconciliation['positive']:
            # Simulate an uncertain probe acknowledgement, with no canonical
            # result write. The real controller must reread the unchanged receipt.
            raise subprocess.TimeoutExpired(command, 45)
        assert journey == 'reconcile' and reconciliation['writes'] == 0
        operation.update(status='failed', stage='reconciled-no-effect',
                         reinstalled=False, healthVerified=False, serviceState='running',
                         reconciliation={'noReplacement': True, 'verifiedAt': time.time(),
                                         'hostInstanceId': service.instance_id},
                         detail='Synthetic no-replacement proof; no install, stop or restart ran.',
                         updatedAt=max(time.time(), operation['updatedAt'] + .001))
        # Sole synthetic worker write; neither a fixture control nor the
        # controller manufactures the settlement or publishes substitute state.
        repair._write(path, operation, home)
        reconciliation['writes'] += 1
        return SimpleNamespace(returncode=0)

    class InspectionProcess:
        run = staticmethod(inspect_child)

        def __getattr__(self, name):
            return getattr(subprocess, name)

    # Module-local proxy: never patch subprocess.run for unrelated host owners.
    repair.subprocess = InspectionProcess()
    bound_identity = identity()
    transport = {'sseConnections': 0}

    @web.middleware
    async def select_sse(request, handler):
        # Fixture-only advertisement narrows the real registration response to
        # the existing HTTP/SSE protocol. No action, state, event or static bytes
        # are substituted; no browser request interception/fulfillment is used.
        if request.path == '/api/events':
            transport['sseConnections'] += 1
        response = await handler(request)
        if request.path == '/api/clients/attach' and response.status == 200:
            payload = json.loads(response.body)
            payload['transports'] = ['http', 'sse']
            response.body = json.dumps(payload).encode()
        return response

    app.middlewares.append(select_sse)

    def authorize(request):
        if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
            raise web.HTTPForbidden()

    async def cookie(request):
        authorize(request)
        if await request.read():
            raise web.HTTPBadRequest()
        response = web.json_response({'ready': True})
        response.set_cookie(SESSION_COOKIE, new_session(app['session_secret']),
                            httponly=True, samesite='Strict')
        return response

    async def observe(request):
        authorize(request)
        from amplifier_web.state_records import load_owner
        from amplifier_web.session_projection import hydrate
        async with service.lock:
            saved, _ = load_owner(service.db, sid)
            assert saved is not None and saved['id'] == sid
            detached = {'sessions': [saved]}
            hydrate(home, detached, service.db)
            operation = repair._latest(service)
            projection = copy.deepcopy(service.state['maintenance']['installationRepair'])
            assert SECRET not in json.dumps(projection)
            return web.json_response({
                'identity': bound_identity, 'hostInstanceId': service.instance_id,
                'dataDir': str(home), 'sessionId': sid,
                'selectedSessionId': service.state['selectedSessionId'],
                'messages': detached['sessions'][0]['messages'],
                'historySource': 'state_records.load_owner/session_projection.hydrate',
                'operation': operation, 'projection': projection,
                'journey': journey,
                'receiptSha256': digest(repair.root(service) / operation['operationId'] / 'operation.json')
                                 if operation else None,
                'admissions': repair._admissions(service),
                'repairHeld': repair.held(service),
                'counters': {
                    'helpers': len(launched), 'artifactPreparations': app_release_artifacts.prepare.await_count,
                    'verifications': len(verification['calls']),
                    'reconciliations': len(reconciliation['calls']),
                    'noReplacementWrites': reconciliation['writes'],
                    'bindingReads': repair.binding.await_count,
                    'releaseReads': len(metadata_calls), 'tagReads': len(download_calls),
                    'runtimeCalls': list(calls), **transport,
                },
                'backgroundUpdatesStopped': manager.task is None and manager.check_task is None,
                'privateCredentialProjected': False,
            })

    async def advance(request):
        authorize(request)
        payload = await request.json()
        if (not isinstance(payload, dict) or set(payload) != {'mode', 'operationId'}
                or payload['mode'] not in {'reinstalled', 'verification-healthy', 'reconciliation-positive'}):
            raise web.HTTPBadRequest(text='Only the fixed synthetic receipt/probe controls are allowed.')
        async with service._installation_repair_lock:
            if len(launched) != 1 or payload['operationId'] != launched[0]['operationId']:
                raise web.HTTPConflict(text='Only this child-owned operation may be advanced.')
            path = repair.root(service) / launched[0]['operationId'] / 'operation.json'
            operation = repair._read(path, home)
            assert operation['previewId'] == launched[0]['previewId']
            assert operation['hostInstanceId'] == service.instance_id
            if payload['mode'] == 'reconciliation-positive':
                if (journey != 'reconcile' or operation['status'] != 'unknown'
                        or reconciliation['positive'] or reconciliation['calls']):
                    raise web.HTTPConflict(text='Only the fresh reconciliation probe may be changed.')
                reconciliation['positive'] = True
                return web.json_response({'operationId': operation['operationId'],
                                          'status': 'reconciliation-positive'})
            if journey != 'verify':
                raise web.HTTPConflict(text='Reinstallation evidence belongs to the Verify journey.')
            if payload['mode'] == 'verification-healthy':
                if (operation['status'] != 'reinstalled' or verification['healthy']
                        or len(verification['calls']) != 1):
                    raise web.HTTPConflict(text='Verify one failed check before changing the synthetic probe.')
                verification['healthy'] = True
                return web.json_response({'operationId': operation['operationId'],
                                          'status': 'verification-healthy'})
            if operation['status'] != 'unknown':
                raise web.HTTPConflict(text='Synthetic transitions cannot be skipped or replayed.')
            operation.update(status='reinstalled', stage='fixture-reinstalled',
                             reinstalled=True, healthVerified=False, serviceState='running',
                             detail='Synthetic receipt only; no install or service command ran.',
                             updatedAt=max(time.time(), operation['updatedAt'] + .001))
            repair._write(path, operation, home)
        # Disk only. The browser must use the real public Status action to
        # publish this receipt over the real state stream.
        return web.json_response({'operationId': operation['operationId'], 'status': operation['status']})

    app.router.add_post('/fixture/installation-repair-cookie', cookie)
    app.router.add_get('/fixture/installation-repair', observe)
    app.router.add_post('/fixture/installation-repair', advance)
    return app, bound_identity


async def serve():
    with tempfile.TemporaryDirectory(prefix='unified-installation-repair-ui-') as directory:
        home = Path(directory)
        home.chmod(0o700)
        app, bound_identity = await main(home)
        runner = web.AppRunner(app)
        loop = asyncio.get_running_loop()
        stopped = asyncio.Event()
        signals = (signal.SIGTERM, signal.SIGINT) if os.name == 'posix' else ()
        try:
            await runner.setup()
            for signum in signals:
                loop.add_signal_handler(signum, stopped.set)
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            base = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
            app['allowed_origins'] |= {base}
            print(json.dumps({'fixture': 'installation-repair-host', 'pid': os.getpid(),
                              'base': base}), flush=True)
            await stopped.wait()
        finally:
            try:
                await runner.cleanup()
            finally:
                for signum in signals:
                    loop.remove_signal_handler(signum)
                assert identity() == bound_identity, 'Pairing inputs changed during qualification'
    assert not home.exists(), 'Private fixture home was not removed'


if __name__ == '__main__':
    asyncio.run(serve())