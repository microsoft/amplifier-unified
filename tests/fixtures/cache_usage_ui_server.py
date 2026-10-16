"""Production UI with synthetic update inventory; never fetches or installs."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aiohttp import web
from amplifier_web.server import create_app
from amplifier_web.setup import SetupManager


async def fixture_routing_catalog(self, workspace):
    # This fixture exercises synthetic update inventory, not source discovery.
    pass


SetupManager.ensure_routing_catalog = fixture_routing_catalog


class Runtime:
    async def close(self):pass


async def main(home):
    os.environ['AMPLIFIER_HOME'] = str(home/'shared')
    os.environ['AMPLIFIER_WEB_HOME'] = str(home)
    workspace = home/'workspace'
    workspace.mkdir()
    app = await create_app(home, workspace=workspace, runtime=Runtime(), voice=False, background_updates=False)
    app['control_token'] = 'fixture-browser-control-token'
    service = app['service']
    service.state['updates'].update(lastCheck=100, available=1, items=[
        {'id':'selected','label':'Configured bundle','usage':'configured','usageEvidence':['Selected bundle'],'status':'check_failed'},
        {'id':'old','label':'Historical branch','ref':'master','usage':'unknown','status':'check_failed'},
        {'id':'edited','label':'Edited dependency','usage':'unknown','status':'local_changes','detail':'Tracked source changes are preserved.'},
        {'id':'update','label':'Indirect dependency','usage':'unknown','status':'update','current':'a'*40,'latest':'b'*40},
        {'id':'pin','label':'Pinned dependency','usage':'unknown','status':'pinned'},
    ])
    calls = []
    async def check():
        calls.append('check')
        await service.update_manager.publish(phase='checked', detail='Synthetic update check completed.')
    async def install():
        calls.append('install')
        await service.update_manager.publish(phase='installed', detail='Synthetic update installation completed.')
    service.update_manager.check = check
    service.update_manager.install = install
    async def receipt(request):return web.json_response(calls)
    app.router.add_get('/fixture', receipt)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    stopped = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    print(json.dumps({'url':url}), flush=True)
    try:await stopped.wait()
    finally:await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-cache-usage-') as temp:
        asyncio.run(main(Path(temp)))
