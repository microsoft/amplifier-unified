"""Disposable failed computer-tool conversation, with no provider calls."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.host.storage import SessionStore
from amplifier_web.naming import directory_for, read

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
    if os.environ.get('MODULE_FAILURE_FIXTURE'):
        from amplifier_web.module_failures import persist_failures
        failure = persist_failures(home / 'runtime-reports' / source['id'], [
            {'module': 'hook-fixture', 'type': 'hook', 'reason_code': 'invalid_module_metadata'}])
        await service.on_runtime_event('runtime.error', {
            'sessionId': source['id'], 'error': str(failure), 'moduleFailures': failure.failures})
    original=(store.directory(source['id'])/'transcript.jsonl').read_bytes()
    naming_calls = []
    original_control = service.runtime.control
    async def control(sid, operation, args):
        if operation != 'session.naming':
            return await original_control(sid, operation, args)
        naming_calls.append(sid)
        before = read(directory_for(home, service._session(sid)))
        await asyncio.sleep(.5)
        return {**before, 'name': 'Browser investigation', 'description': 'A fixture naming suggestion'}
    service.runtime.control = control
    async def check(request):
        return web.json_response({'originalUnchanged':original==(store.directory(source['id'])/'transcript.jsonl').read_bytes(),'namingCalls':len(naming_calls),'sessions':len(service.state['sessions']),'selected':service.state['selectedSessionId']})
    app.router.add_get('/fixture/check',check)
    service._publish()
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url=f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}";app['allowed_origins']=app['allowed_origins']|{url}
    print(json.dumps({'url':url,'sessionId':source['id']}),flush=True)
    try:await asyncio.Event().wait()
    finally:await runner.cleanup()

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-health-ui-') as temp:asyncio.run(main(Path(temp)))
