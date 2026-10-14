"""Isolated production UI with real managed shell; synthetic approvals, no model."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web
import settings_ui_server as fixture
from amplifier_worktrees.git import git
from amplifier_web.integration_validation import admit
from amplifier_web.runtime_controls import RuntimeControls
from amplifier_module_tool_bash import BashTool


async def main(home):
    app = await fixture.main(home)
    service = app['service']; sid = service._session()['id']; workspace = Path(service._session()['workspace'])
    roots = [workspace / name for name in ('first', 'second')]
    for root in roots:
        root.mkdir()
        git(root, 'init', '-b', 'main'); git(root, 'config', 'user.name', 'Fixture'); git(root, 'config', 'user.email', 'fixture@example.test')
        (root / 'source.txt').write_text('original\n'); (root / '.gitignore').write_text('result.txt\n')
        git(root, 'add', '.'); git(root, 'commit', '-m', 'Fixture contributor')
    tool = BashTool({'managed_processes': True, 'safety_profile': 'unrestricted', 'working_dir': str(workspace)})
    tool._processes.observer = lambda: lambda event: service.operations.observe(sid, sid, event)
    hooks = SimpleNamespace(emit=AsyncMock(return_value=SimpleNamespace(action='continue')))
    coordinator = SimpleNamespace(get=lambda name: {'bash': tool} if name == 'tools' else None,
        get_capability=lambda name: (lambda identity, command: admit(service, sid, identity, command)) if name == 'integration.admit' else None,
        hooks=hooks, process_hook_result=AsyncMock(side_effect=lambda value,*_:value))
    controls = object.__new__(RuntimeControls); controls.coordinator = coordinator; controls.session = SimpleNamespace(session_id=sid)
    original = service.runtime.control
    async def control(owner, action, args):
        return await controls.perform(action, args) if action.startswith('operations.') else await original(owner, action, args)
    service.runtime.control = control
    service.runtime.integration_validation_supported = AsyncMock(return_value=True)
    service.management.ensure_runtime = AsyncMock()
    async def facts(request):
        return web.json_response({'sessionId': sid, 'roots': [str(root) for root in roots],
            'operations': service.operations.journal.list(sid),
            'results': [{'repositoryPath': str(root), 'references': ['result.txt@sha256:' + hashlib.sha256((root / 'result.txt').read_bytes()).hexdigest()]} for root in roots if (root / 'result.txt').exists()]})
    async def change(request):
        (roots[1] / 'source.txt').write_text('other contributor changed\n')
        return web.json_response({'ok': True})
    async def remove(request):
        for root in roots: (root / 'result.txt').unlink(missing_ok=True)
        return web.json_response({'ok': True})
    app.router.add_get('/fixture/integration', facts)
    app.router.add_post('/fixture/change-dependency', change)
    app.router.add_post('/fixture/remove-result', remove)
    await service.dispatch('view.update', {'patch': {'panel': 'chat-work', 'runtimeDraft': {'tab': 'direction'}, 'draft': 'Private integration draft'}})
    runner = web.AppRunner(app); await runner.setup(); site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url, 'sessionId': sid}), flush=True)
    try: await asyncio.Event().wait()
    finally: await tool.close(); await runner.cleanup()


if __name__ == '__main__':
    base = os.environ.get('AMPLIFIER_TEST_OUTPUT_DIR')
    if base:
        directory = tempfile.mkdtemp(prefix='integration-validation-fixture-', dir=base)
        asyncio.run(main(Path(directory)))
    else:
        with tempfile.TemporaryDirectory(prefix='integration-validation-fixture-') as directory: asyncio.run(main(Path(directory)))
