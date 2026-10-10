"""Actual native discovery and naming; synthetic provider, no conversation work."""
import asyncio
import json
from pathlib import Path
import tempfile
from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.session_files import amplifier_home, project_slug


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    workspace = home / 'imported-project'
    workspace.mkdir()
    directory = amplifier_home() / 'projects' / project_slug(workspace) / 'sessions' / 'imported-report'
    directory.mkdir(parents=True)
    transcript = json.dumps({'role': 'user', 'content': '<context_file paths="@notes.md">Internal setup</context_file>\nSummarize the quarterly report'}) + '\n'
    (directory / 'transcript.jsonl').write_text(transcript)
    (directory / 'metadata.json').write_text(json.dumps({'session_id': 'imported-report', 'working_dir': str(workspace),
        'name': '', 'name_source': 'fallback', 'created': '2026-01-01T12:00:00Z', 'turn_count': 1}))
    await service.history.refresh()
    source = next(row for row in service.state['sessions'] if row.get('nativeIdentity') == 'imported-report')
    release = asyncio.Event()
    calls = []
    original_probe = fixture.SetupManager.probe

    async def probe(manager, action, args, workspace):
        if action != 'naming.complete':
            return await original_probe(manager, action, args, workspace)
        calls.append(args['prompt'])
        await release.wait()
        return {'text': json.dumps({'action': 'set', 'name': 'Quarterly report summary'})}

    fixture.SetupManager.probe = probe

    async def check(request):
        current = service._session(source['id'], hydrate=False)
        return web.json_response({'calls': len(calls), 'title': current['title'], 'historyLoaded': current['historyLoaded'],
            'unchanged': (directory / 'transcript.jsonl').read_text() == transcript,
            'cleanPrompt': all('Internal setup' not in prompt and '<context_file' not in prompt for prompt in calls)})

    async def finish(request):
        release.set()
        return web.json_response({'released': True})

    app.router.add_get('/fixture/check', check)
    app.router.add_post('/fixture/finish', finish)
    service._publish()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] |= {url}
    print(json.dumps({'url': url, 'sessionId': source['id']}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='imported-naming-ui-') as tmp:
        asyncio.run(main(Path(tmp)))
