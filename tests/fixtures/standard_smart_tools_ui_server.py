"""Public Smart Tools controls with real receipts and a credential-free installer."""
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import signal
import tempfile

from aiohttp import web
import settings_ui_server as base

calls = Counter()
inspections = []
catalog_reads = 0


async def catalog(self):
    global catalog_reads
    catalog_reads += 1
    rows = [{'id': f'tool-{i}', 'name': f'Tool {i:02d}',
             'repository': f'https://example.com/tool-{i}', 'ref': 'main', 'path': '.',
             'description': ('Research' if i < 32 else 'Creation') + ' specialist tool',
             'version': '1.0'} for i in range(51)]
    await self._change(lambda state: state.update(catalog=rows))
    return {'items': rows}


async def inspect_source(self, args):
    inspections.append(dict(args))
    if not args['repository'].startswith('https://example.com/'):
        raise ValueError('Synthetic source is unavailable; check the repository URL.')
    return {**args, 'name': 'Reviewed source tool', 'commit': 'reviewed-commit-' + args.get('ref', 'main'),
            'manifest': {'name': 'Reviewed source tool', 'version': '1.0'}, 'pythonSupported': True}


async def install(self, args):
    identity = args['repository']
    calls[identity] += 1
    if identity.endswith('tool-2') and calls[identity] == 1:
        raise ValueError('Temporary fixture failure. Retry this item.')
    if args.get('ref') == 'broken-revision':
        raise ValueError('Synthetic revision unavailable. The installed version is retained.')
    row = {**args, 'id': identity.rsplit('/', 1)[-1], 'name': identity.rsplit('/', 1)[-1],
           'status': 'installed', 'commit': 'reviewed-commit-' + args.get('ref', 'main'),
           'binDir': '/fixture/bin', 'guidance': 'Configure the documented MCP executable.'}
    await self._change(lambda state: state.update(installations=[
        item for item in state['installations'] if item['id'] != row['id']] + [row]))
    return row


base.SmartToolsManager.catalog = catalog
base.SmartToolsManager.inspect = inspect_source
base.SmartToolsManager.install = install


async def main(home):
    app = await base.main(home)
    service = app['service']
    await service.dispatch('view.update', {'patch': {'setupJourney': 'done', 'panel': None}})
    runtime_calls = []
    for name in ('start', 'stop', 'control'):
        original = getattr(service.runtime, name)
        async def observe(*args, _name=name, _original=original, **kwargs):
            runtime_calls.append(_name)
            return await _original(*args, **kwargs)
        setattr(service.runtime, name, observe)

    async def observations(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        async with service.lock:
            return web.json_response({'catalogReads': catalog_reads, 'installCalls': dict(calls),
                'inspections': inspections, 'installations': service.smart_tools.state['installations'],
                'servers': service.smart_tools.state['servers'], 'runtimeCalls': runtime_calls,
                'messages': hashlib.sha256(json.dumps(service._session()['messages'], sort_keys=True).encode()).hexdigest()})
    app.router.add_get('/fixture/standard-tools', observations)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
    app['allowed_origins'] |= {url}
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    print(json.dumps({'url': url}), flush=True)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='unified-standard-tools-') as folder:
        asyncio.run(main(Path(folder)))
