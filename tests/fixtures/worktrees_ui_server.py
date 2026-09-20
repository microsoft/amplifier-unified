"""Real temporary Git repository and app controller; synthetic runtime release."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import AsyncMock
from aiohttp import web
import settings_ui_server as fixture
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from amplifier_worktrees.git import git

async def main(home):
    app = await fixture.main(home)
    service = app['service']; session = service._session(); root = Path(session['workspace'])
    git(root, 'init', '-b', 'main'); git(root, 'config', 'user.name', 'Fixture'); git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root/'file.txt').write_text('original\n'); git(root, 'add', '.'); git(root, 'commit', '-m', 'base')
    runtime = app['runtime']
    runtime.quiesce_for_handoff = AsyncMock(return_value={'quiesced': True, 'inputsReplayed': False})
    await service.dispatch('view.update', {'patch': {'panel':'settings','runtimeDraft':{'tab':'overview'},'draft':'Keep my worktree draft'}})
    async def fail_next(request):
        runtime.quiesce_for_handoff.side_effect = RuntimeError('Fixture lost release confirmation')
        return web.json_response({'ok':True})
    async def restore(request):
        runtime.quiesce_for_handoff.side_effect = None
        return web.json_response({'ok':True})
    app.router.add_post('/fixture/fail-release', fail_next); app.router.add_post('/fixture/restore-release',restore)
    runner=web.AppRunner(app); await runner.setup(); site=web.TCPSite(runner,'127.0.0.1',0); await site.start()
    url=f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app['allowed_origins']=app['allowed_origins']|{url}
    print(json.dumps({'url':url,'sessionId':session['id'],'root':str(root)}),flush=True)
    try: await asyncio.Event().wait()
    finally: await runner.cleanup()
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='unified-worktrees-') as directory: asyncio.run(main(Path(directory)))
