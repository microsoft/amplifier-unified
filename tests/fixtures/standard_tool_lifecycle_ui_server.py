"""Owned local MCP transport and package files; no external install or account."""
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

from aiohttp import web
import settings_ui_server as base


async def main(home):
    app = await base.main(home)
    service = app['service']
    session = service._session()
    session.update(historyManaged=False, historyLoaded=True,
                   messages=[{'id':'tools-saved', 'role':'user', 'text':'Keep this saved tools question'}])
    identity = 'ab' * 16
    package = service.smart_tools.root / 'installs' / identity
    package.mkdir(parents=True)
    (package / 'owned.txt').write_text('Only this app-managed package may be removed.')
    work = home / 'workspace' / 'saved-tool-work.txt'
    work.write_text('Keep independently saved work.')
    starts = home / 'mcp-starts.jsonl'
    helper = package / 'local_server.py'
    helper.write_text(
        'import json,os,sys\nfrom pathlib import Path\nfrom mcp.server import MCPServer\n'
        f'with Path({str(starts)!r}).open("a") as f:f.write(json.dumps({{"args":sys.argv[1:],"cwd":os.getcwd(),"referenceResolved":os.environ.get("CHILD_TOKEN")=="synthetic-local-proof"}})+"\\n")\n'
        'server=MCPServer("Owned local proof")\n'
        '@server.tool()\ndef local_read() -> str:\n    """Read a fixed local fixture value."""\n    return "local fixture value"\n'
        'server.run()\n')
    os.environ['OWNED_MCP_REFERENCE'] = 'synthetic-local-proof'
    service.smart_tools.state['installations'] = [{
        'id':identity, 'name':'Owned local proof', 'repository':'https://example.com/owned-local-proof',
        'path':'.', 'status':'installed', 'binDir':str(package),
        'guidance':'Use the locally supplied fixture executable; no account is required.'}]
    runtime_calls = []
    for name in ('start', 'stop', 'control'):
        old = getattr(service.runtime, name)
        async def observed(*args, _name=name, _old=old, **kwargs):
            runtime_calls.append(_name)
            return await _old(*args, **kwargs)
        setattr(service.runtime, name, observed)
    await service.dispatch('view.update', {'patch':{'setupJourney':'done','navPinned':True,'panel':None}})
    service._publish()

    async def inspect(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        async with service.lock:
            row = service._session(session['id'])
            return web.json_response({
                'sessionId':row['id'], 'messages':copy.deepcopy(row['messages']),
                'servers':copy.deepcopy(service.smart_tools.state['servers']),
                'installations':copy.deepcopy(service.smart_tools.state['installations']),
                'operations':copy.deepcopy(service.smart_tools.state['operations']),
                'liveConnections':sorted(service.smart_tools.connections),
                'packageExists':package.exists(), 'work':work.read_text(),
                'starts':[json.loads(line) for line in starts.read_text().splitlines()] if starts.exists() else [],
                'runtimeCalls':list(runtime_calls)})
    app.router.add_get('/fixture/tool-lifecycle', inspect)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
    app['allowed_origins'] |= {url}
    stop = asyncio.Event()
    if sys.platform != 'win32':
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    print(json.dumps({'url':url, 'python':sys.executable, 'helper':str(helper),
                      'workspace':str(work.parent), 'installationId':identity}), flush=True)
    try:
        await stop.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='unified-tool-lifecycle-') as folder:
        asyncio.run(main(Path(folder)))
