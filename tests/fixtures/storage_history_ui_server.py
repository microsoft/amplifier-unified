"""Synthetic session-history cleanup and navigation fixture."""
import asyncio
import json
import os
import signal
import tempfile
from pathlib import Path
from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.session_files import sessions_dir, project_slug

async def main(home):
    app=await fixture.main(home);service=app['service']
    workspace=home/'project';workspace.mkdir()
    (workspace/'source.txt').write_text('Keep project working files')
    ids=[]
    titles = ['Same saved title', 'Same saved title'] if os.environ.get('AMPLIFIER_TEST_DUPLICATE_TITLES') == '1' else ['Remove one session','Keep until project cleanup']
    for title in titles:
        await service.dispatch('session.create',{'title':title,'workspace':str(workspace)})
        row=service._session();sid=row['id'];ids.append(sid)
        directory=sessions_dir(workspace)/sid;directory.mkdir(parents=True,exist_ok=True)
        (directory/'metadata.json').write_text(json.dumps({'session_id':sid,'working_dir':str(workspace),'title':title}))
        (directory/'transcript.jsonl').write_text('{"role":"user","content":"Synthetic saved turn"}\n')
    # Settle native discovery and display reconciliation before exposing the
    # fixture. A preview/confirm test must not race initial fixture population.
    for _ in range(8):
        await service.history.refresh()
        if all(service._session(sid, hydrate=False).get('nativeProject') == project_slug(workspace) for sid in ids):
            break
    else:
        raise RuntimeError('Synthetic native identities did not finish bounded discovery')
    for sid in ids:
        await service.history.refresh_session(sid)
    await service._flush_pending_progress()
    service.state['view'].update(panel=None)
    service._publish()
    async def info(request):
        return web.json_response({'project':project_slug(workspace),'workspace':str(workspace),'ids':ids,'exists':[(sessions_dir(workspace)/sid).exists() for sid in ids], 'projectExists':(workspace/'source.txt').exists()})
    app.router.add_get('/api/fixture/history',info)
    async def expire_review(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        # Deterministic elapsed-preview boundary, confined to this synthetic DB.
        async with service.lock:
            rows = service.db.execute("SELECT id,value FROM managed_deletions WHERE phase='preview'").fetchall()
            for identity, raw in rows:
                value = json.loads(raw)
                value['expiresAt'] = 0
                service.db.execute('UPDATE managed_deletions SET value=? WHERE id=?', (json.dumps(value), identity))
            service.db.commit()
        return web.json_response({'expired': len(rows)})
    app.router.add_post('/api/fixture/history/expire-review',expire_review)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins']=app['allowed_origins']|{url}
    stopped=asyncio.Event();loop=asyncio.get_running_loop()
    shutdown_signals=(signal.SIGTERM,signal.SIGINT) if os.name=='posix' else ()
    for signum in shutdown_signals:loop.add_signal_handler(signum,stopped.set)
    print(json.dumps({'url':url}),flush=True)
    try:await stopped.wait()
    finally:
        try:await runner.cleanup()
        finally:
            for signum in shutdown_signals:loop.remove_signal_handler(signum)

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='unified-storage-history-') as temp:asyncio.run(main(Path(temp)))
