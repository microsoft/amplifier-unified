"""Public update actions with synthetic staging/rollback effects in private state.

The real command dispatcher and UI run; no installer, network or host restart
is called. This proves UI intent/retention, not installation acceptance.
"""
import asyncio
import copy
import json
import os
import signal
import tempfile
from pathlib import Path

from aiohttp import web
from settings_ui_server import main as settings_app
from amplifier_web import app_updates


async def serve():
    with tempfile.TemporaryDirectory(prefix='unified-update-effects-') as directory:
        app = await settings_app(Path(directory))
        service = app['service']
        manager = service.update_manager
        session = service._session()
        # A declared running-chat prerequisite exercises the real blocker projection.
        # No provider or worker actually runs in this UI-only effect fixture.
        session['status'] = 'working'
        session['messages'] = [{'id': 'retained-update-message', 'role': 'assistant',
                                'text': 'Keep this saved conversation.', 'createdAt': 1}]
        original_messages = copy.deepcopy(session['messages'])
        calls = []
        generation = {'active': 'original-generation', 'previous': 'previous-generation'}
        application = {'id': 'application', 'label': 'Amplifier Unified', 'kind': 'app', 'current': '0.20.165', 'latest': '99.0.0', 'revision': 'a'*40, 'status': 'update', 'canInstall': True}
        target = {'revision': 'a'*40, 'latest': '99.0.0', 'generation': 'b'*32, 'attemptId': 'fixture-staging-attempt'}
        available = copy.deepcopy(application)
        checks = []

        async def check():
            checks.append(copy.deepcopy(available))
            await asyncio.sleep(.2)
            return copy.deepcopy(available)
        app_updates.check = check  # Private fixture release channel; real check owner.

        async def install():
            target.update({key: service.state['updates']['application'][key] for key in ('revision', 'latest')})
            calls.append({'operation': 'install', 'application': copy.deepcopy(service.state['updates']['application'])})
            await manager.publish(phase='staging', error=None)
            await asyncio.sleep(.2)
            await manager.publish(phase='app-staged', pendingApp=target,
                                  blockers=[{'kind': 'conversation', 'sessionId': session['id'], 'label': 'Settings test is working'}])

        async def rollback():
            calls.append({'operation': 'rollback', 'target': generation['previous']})
            generation['active'] = generation['previous']
            await manager.publish(phase='installed', canRollback=False, error=None,
                                  sequence={'stage': 'complete'}, application={**application, 'status': 'current', 'latest': '0.20.165'})

        manager.install = install
        manager.rollback = rollback
        await manager.publish(phase='available', initialPreparation=False, items=[],
                              pendingApp=None, pendingRelease=None, pendingRestart=None,
                              sequence={}, error=None, application=application)

        async def control(request):
            nonlocal available
            if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
                raise web.HTTPForbidden()
            if request.method == 'POST':
                mode = (await request.json())['mode']
                if mode == 'newer-release':
                    available = {**application, 'latest': '99.0.1', 'revision': 'c'*40}
                elif mode == 'offline':
                    available = {'status': 'check_failed'}
                elif mode in {'management-hold', 'unobservable-hold', 'operation-hold', 'hold-clear'}:
                    # Synthetic predicate inputs, real gate/public projection.
                    if manager.service.management.lock.locked():
                        manager.service.management.lock.release()
                    session.update(status='error', configurationBusy=True,
                                   pendingConfiguration={'phase': 'error', 'recoveryRequired': True,
                                                         'error': 'PRIVATE_HOLD_SENTINEL'})
                    service.runtime.has_pending_operations = lambda: False
                    if mode == 'management-hold':
                        await service.management.lock.acquire()
                    elif mode == 'unobservable-hold':
                        service.runtime.has_pending_operations = None
                    elif mode == 'operation-hold':
                        session['pendingConfiguration']['operationId'] = 'PRIVATE_OPERATION_SENTINEL'
                    else:
                        session.update(status='working', configurationBusy=False, pendingConfiguration={})
                    await manager.publish()
                elif mode == 'unknown':
                    await manager.publish(phase='error', pendingApp=None,
                                          pendingReplacement={'attemptId': 'unconfirmed-attempt'},
                                          error='Synthetic installation outcome unknown; verify the installed generation.')
                elif mode == 'known-failure':
                    await manager.publish(phase='error', pendingApp=None, pendingReplacement=None,
                                          error='Synthetic staging failed; original generation retained.')
                elif mode == 'rollback-ready':
                    await manager.publish(phase='installed', pendingApp=None, pendingReplacement=None,
                                          pendingRelease=None, pendingRestart=None, error=None,
                                          sequence={'stage': 'complete'}, canRollback=True,
                                          application={**application, 'status': 'current', 'latest': '0.20.165'})
                else:
                    raise web.HTTPBadRequest()
            return web.json_response({'calls': calls, 'checks': checks, 'generation': generation,
                                      'target': target, 'updates': service.state['updates'],
                                      'sessionId': session['id'], 'messages': session['messages'],
                                      'originalMessages': original_messages})

        app.router.add_route('*', '/fixture/update-effects', control)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] |= {url}
        stopped = asyncio.Event()
        if os.name != 'nt':
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
        print(json.dumps({'url': url}), flush=True)
        try:
            await stopped.wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(serve())
