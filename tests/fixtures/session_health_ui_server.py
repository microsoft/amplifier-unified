"""Disposable failed computer-tool conversation, with no provider calls."""
import asyncio
import json
from pathlib import Path
import tempfile
from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.host.storage import SessionStore

async def main(home):
    app = await fixture.main(home)
    service = app['service'];source = service._session()
    service._message(source,'user','Investigate the browser')
    service._message(source,'assistant','Checking the browser')
    source.update(status='error',error='RuntimeError: Manager turn failed; no automatic replay',errorAt=1789926000)
    rows=[{'role':'user','content':'Investigate the browser'},
          {'role':'assistant','content':'Checking the browser','tool_calls':[{'id':'blocked','name':'computer'}]},
          {'role':'tool','tool_call_id':'blocked','content':'halted: human confirmation required'}]
    store=SessionStore.for_app(home,source['workspace']);store.save(source['id'],rows,{})
    folder=store.directory(source['id'])/'context-intelligence';folder.mkdir(exist_ok=True)
    (folder/'events.jsonl').write_text(json.dumps({'event':'provider:error','data':{'session_id':source['id'],'error':{'type':'InvalidRequestError','msg':'Invalid image_url: invalid base64-encoded value'}}})+'\n')
    original=(store.directory(source['id'])/'transcript.jsonl').read_bytes()
    async def check(request):
        return web.json_response({'originalUnchanged':original==(store.directory(source['id'])/'transcript.jsonl').read_bytes(),'sessions':len(service.state['sessions']),'selected':service.state['selectedSessionId']})
    app.router.add_get('/fixture/check',check)
    service._publish()
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url=f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}";app['allowed_origins']=app['allowed_origins']|{url}
    print(json.dumps({'url':url,'sessionId':source['id']}),flush=True)
    try:await asyncio.Event().wait()
    finally:await runner.cleanup()

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-health-ui-') as temp:asyncio.run(main(Path(temp)))
