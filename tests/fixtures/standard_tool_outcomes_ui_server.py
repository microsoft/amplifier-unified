"""Synthetic tool outcomes through the real durable Smart Tools command owner."""
import asyncio
import copy
import json
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
                   messages=[{'id':'outcomes-saved', 'role':'user', 'text':'Preserve the saved question.'}])
    schema = {'name':'owned_calculate', 'title':'Owned calculation', 'description':'Controlled local outcome proof.',
              'inputSchema':{'type':'object', 'properties':{'amount':{'type':'integer','title':'Amount'},
                'mode':{'type':'string','title':'Outcome','enum':['error','interrupt','success']}},
                'required':['amount','mode']}}
    server = {'id':'owned-outcome', 'name':'Owned outcome connection', 'transport':'stdio',
              'command':'synthetic-controlled-execution', 'args':[], 'env':{}, 'status':'ready',
              'catalogRevision':'owned-revision', 'loadedSchemaRevision':'owned-revision',
              'catalogState':'ready', 'tools':[copy.deepcopy(schema)], 'loadedSchemas':{'owned_calculate':copy.deepcopy(schema)}}
    service.smart_tools.state['servers'] = [server]
    calls = []
    original = service.smart_tools.execute
    async def execute(action, args, origin='ui'):
        if args.get('id') == server['id'] and action == 'smartTools.schemas':
            return {'tools':[copy.deepcopy(schema)]}
        if args.get('id') == server['id'] and action == 'smartTools.call':
            calls.append(copy.deepcopy(args.get('arguments', {})))
            if args['arguments']['mode'] == 'interrupt':
                raise asyncio.CancelledError()
            if args['arguments']['mode'] == 'error':
                return {'isError':True, 'content':[{'type':'text','text':'Owned calculation rejected this input; revise the amount.'}]}
            return {'content':[{'type':'text','text':'Owned calculation succeeded.'}], 'structuredContent':{'amount':args['arguments']['amount']}}
        return await original(action, args, origin=origin)
    service.smart_tools.execute = execute
    await service.dispatch('view.update', {'patch':{'setupJourney':'done','navPinned':True,'panel':None}})
    service._publish()
    async def inspect(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        async with service.lock:
            return web.json_response({'calls':copy.deepcopy(calls),
                'operations':[copy.deepcopy(row) for row in service.smart_tools.state['operations'] if row['action']=='smartTools.call'],
                'messages':copy.deepcopy(service._session(session['id'])['messages']),
                'sessionId':session['id']})
    app.router.add_get('/fixture/tool-outcomes', inspect)
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
    print(json.dumps({'url':url}), flush=True)
    try:
        await stop.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='unified-tool-outcomes-') as folder:
        asyncio.run(main(Path(folder)))
