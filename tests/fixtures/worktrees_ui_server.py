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
from amplifier_web.host_identity import local_host_identity

async def main(home):
    app = await fixture.main(home)
    service = app['service']; session = service._session(); root = Path(session['workspace'])
    repositories = [root / 'api', root / 'client'] if '--multi-repo' in sys.argv else [root]
    for repository in repositories:
        repository.mkdir(exist_ok=True)
        git(repository, 'init', '-b', 'main'); git(repository, 'config', 'user.name', 'Fixture'); git(repository, 'config', 'user.email', 'fixture@example.invalid')
        (repository/'file.txt').write_text('original\n'); git(repository, 'add', '.'); git(repository, 'commit', '-m', 'base')
    if '--multi-repo' in sys.argv:
        (repositories[0]/'file.txt').write_text('Retain uncommitted API work\n')
    runtime = app['runtime']
    runtime.quiesce_for_handoff = AsyncMock(return_value={'quiesced': True, 'inputsReplayed': False})
    await service.dispatch('view.update', {'patch': {'panel':'chat-work','runtimeDraft':{'tab':'direction'},'draft':'Keep my worktree draft'}})
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
    print(json.dumps({'url':url,'sessionId':session['id'],'root':str(root),'repositories':[str(path) for path in repositories],'host':local_host_identity()}),flush=True)
    try: await asyncio.Event().wait()
    finally: await runner.cleanup()
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='unified-worktrees-') as directory: asyncio.run(main(Path(directory)))
