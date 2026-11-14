"""Real Unified host and independent ACP peers; synthetic model-free history."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from amplifier_web.deployment import save_server_config
from amplifier_web.server import create_app
from test_acp_participant import operations, peer
from test_service import Runtime


async def serve(home):
    for key, folder in [('AMPLIFIER_HOME', 'native'), ('AMPLIFIER_WEB_HOME', 'app'),
                        ('AMPLIFIER_SESSION_STATE_HOME', 'sessions'),
                        ('AMPLIFIER_TERMINAL_HOME', 'terminals')]:
        os.environ[key] = str(home / folder)
    workspace = home / 'workspace'
    workspace.mkdir()
    identities = ('alpha', 'beta', 'legacy-alpha', 'legacy-beta')
    agents = [{'id': identity, 'label': identity.title(), 'command': sys.executable,
               'args': [str(Path(__file__).with_name('shared_acp_agent.py')),
                        str(home / identity / 'peer'), identity]}
              for identity in ('alpha', 'beta')]
    agents.extend({**peer(home / identity), 'id': identity, 'label': identity.title()}
                  for identity in ('legacy-alpha', 'legacy-beta'))
    save_server_config(home / 'app', {'externalAgents': agents})
    runtime = Runtime()
    app = await create_app(home / 'app', workspace=workspace, runtime=runtime,
                          voice=False, background_updates=False,
                          preload_providers=False, background_work=False)
    app['control_token'] = 'fixture-browser-control-token'
    service = app['service']
    created = await service.dispatch('session.create', {'workspace': str(workspace),
        'title': 'Saved directed group', 'externalAgentIds': ['legacy-alpha', 'legacy-beta']},
        command_id='seed-legacy-directed')
    legacy_id = created['result']['sessionId']
    # Represent an existing saved group from before shared participation. This
    # fixture-only seed does not add a product conversion or creation option.
    async with service.lock:
        service._session(legacy_id)['externalAgent'].pop('participation')
        service._publish_changes(sessions={legacy_id})

    async def observe(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        async with service.lock:
            return web.json_response({'nativeStarts': runtime.started, 'nativeSends': runtime.sent,
                'sessions': [{'id': row['id'], 'messages': row['messages'],
                              'status': row.get('status'),
                              'externalAgent': row.get('externalAgent'),
                              'externalStreams': row.get('externalStreams', {}),
                              'sourceDeliveries': row.get('_externalRoom', {}).get('deliveries', [])}
                             for row in service._state['sessions']],
                'peers': {identity: operations(home / identity)
                          if (home / identity / 'peer/operations.jsonl').exists() else []
                          for identity in identities}})

    async def release(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        # Deterministically finish the maintained peer's held work. This is a
        # test-fixture control, never a public input, prompt or product API.
        (home / 'beta/peer/release').touch()
        return web.json_response({'released': True})

    app.router.add_get('/fixture/participants', observe)
    app.router.add_post('/fixture/release-beta', release)
    runner = web.AppRunner(app)
    await runner.setup()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    try:
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] = app['allowed_origins'] | {url}
        print(json.dumps({'url': url, 'workspace': str(workspace), 'legacyId': legacy_id}), flush=True)
        await stopped.wait()
    finally:
        await runner.cleanup()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-multiparty-ui-') as directory:
        asyncio.run(serve(Path(directory)))
