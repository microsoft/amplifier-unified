"""Synthetic session-history cleanup and navigation fixture."""
import asyncio
import json
import tempfile
import sys
import copy
from pathlib import Path
from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.session_files import sessions_dir, project_slug

async def main(home):
    app=await fixture.main(home);service=app['service']
    from amplifier_web import managed_deletion
    original_plan=managed_deletion.plan
    observed={}
    def diagnostic_plan(app,sid,history=None):
        value=original_plan(app,sid,history)
        if sid in observed:
            before,state=observed[sid]
            changes=[key for key in value if value[key]!=before[key]]
            if changes:
                print('DELETE PLAN CHANGES '+json.dumps({'keys':changes,'before':{k:before[k] for k in changes},'after':{k:value[k] for k in changes},'sessionsBefore':state,'sessionsAfter':app.state['sessions']}),file=sys.stderr,flush=True)
        observed[sid]=(value,copy.deepcopy(app.state['sessions']))
        return value
    managed_deletion.plan=diagnostic_plan
    workspace=home/'project';workspace.mkdir()
    (workspace/'source.txt').write_text('Keep project working files')
    ids=[]
    for title in ['Remove one session','Keep until project cleanup']:
        await service.dispatch('session.create',{'title':title,'workspace':str(workspace)})
        row=service._session();sid=row['id'];ids.append(sid)
        directory=sessions_dir(workspace)/sid;directory.mkdir(parents=True,exist_ok=True)
        (directory/'metadata.json').write_text(json.dumps({'session_id':sid,'working_dir':str(workspace),'title':title}))
        (directory/'transcript.jsonl').write_text('{"role":"user","content":"Synthetic saved turn"}\n')
    service.state['view'].update(panel=None)
    service._publish()
    async def info(request):
        return web.json_response({'project':project_slug(workspace),'ids':ids,'exists':[(sessions_dir(workspace)/sid).exists() for sid in ids], 'projectExists':(workspace/'source.txt').exists()})
    app.router.add_get('/api/fixture/history',info)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins']=app['allowed_origins']|{url};print(json.dumps({'url':url}),flush=True)
    try:await asyncio.Event().wait()
    finally:await runner.cleanup()

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='unified-storage-history-') as temp:asyncio.run(main(Path(temp)))
