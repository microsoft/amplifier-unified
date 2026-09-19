"""Real workspace actions and discovery against disposable folders; no providers."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiohttp import web

from amplifier_web.server import create_app
from amplifier_web.session_files import project_slug


class ObservedRuntime:
    def __init__(self):
        self.started = []
        self.sent = []

    async def start(self, session, emit):
        self.started.append(session['id'])

    async def send(self, session, text, input_id, emit):
        self.sent.append(session['id'])

    async def stop(self, *args):
        pass

    async def close(self):
        pass


def native_chat(home, workspace, identity, *, exists=True, worker=False, unresolved=False):
    if exists:
        workspace.mkdir(parents=True, exist_ok=True)
    directory = home / 'projects' / project_slug(workspace) / 'sessions' / identity
    directory.mkdir(parents=True)
    metadata = {'session_id': identity, 'bundle': 'bundle:anchors', 'name': identity,
                'created': '2026-01-01T12:00:00Z', 'turn_count': 1}
    if not unresolved:
        metadata['working_dir'] = str(workspace)
    if worker:
        metadata['parent_id'] = 'absent-root'
    (directory / 'metadata.json').write_text(json.dumps(metadata))
    (directory / 'transcript.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in [
        {'role': 'user', 'content': 'Saved fixture question'},
        {'role': 'assistant', 'content': 'Saved fixture answer'},
    ]))


async def main():
    with tempfile.TemporaryDirectory(prefix='amplifier-workspace-explorer-') as directory:
        temp = Path(directory).resolve()
        home = temp / 'native'
        data = temp / 'app'
        os.environ['AMPLIFIER_HOME'] = str(home)
        os.environ['AMPLIFIER_WEB_HOME'] = str(data)
        os.environ['AMPLIFIER_UNIFIED_IMPORT_HOME'] = str(temp / 'legacy')
        root = temp / 'folders'
        paths = {
            'root': root,
            'dev': root / 'development',
            'research': root / 'research',
            'mixed': root / 'development' / 'amplifier-unified',
            'nested': root / 'development' / 'amplifier-unified' / 'extensions' / 'renderer',
            'playgroundOne': root / 'development' / 'playground',
            'playgroundTwo': root / 'research' / 'playground',
            'workerOnly': root / 'development' / 'worker-only',
            'missing': root / 'development' / 'missing',
            'unresolved': root / 'development' / 'unresolved',
            'empty': root / 'development' / 'empty-registered',
            'unrelated': root / 'development' / 'unrelated-folder',
            'new': root / 'development' / 'new-parent' / 'new-workspace-with-a-long-folder-name',
            'existing': root / 'existing-folder',
        }
        for key in ['mixed', 'nested', 'playgroundOne', 'playgroundTwo']:
            native_chat(home, paths[key], key + '-chat')
        native_chat(home, paths['workerOnly'], 'worker-only-chat', worker=True)
        native_chat(home, paths['missing'], 'missing-chat', exists=False)
        native_chat(home, paths['unresolved'], 'unresolved-chat', unresolved=True)
        for key in ['empty', 'unrelated', 'existing']:
            paths[key].mkdir(parents=True)
        (paths['existing'] / 'keep.txt').write_text('Existing workspace contents must stay intact.\n')
        runtime = ObservedRuntime()
        app = await create_app(data, workspace=paths['mixed'], runtime=runtime,
                               voice=False, background_updates=False, preload_providers=False)
        app['control_token'] = 'fixture-browser-control-token'
        service = app['service']
        await service.history.refresh()
        await service.dispatch('workspace.add', {'path': str(paths['empty'])})
        initial = next(row['id'] for row in service.state['sessions'] if row.get('nativeIdentity') == 'mixed-chat')
        await service.dispatch('session.select', {'id': initial})
        await service.dispatch('view.update', {'patch': {'navPinned': True}})

        async def info(request):
            saved = json.loads(service.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
            return web.json_response({
                'paths': {key: str(value) for key, value in paths.items()},
                'initialSession': initial,
                'runtimeStarts': runtime.started, 'runtimeSends': runtime.sent,
                'directories': {key: value.is_dir() for key, value in paths.items()},
                'existingContents': (paths['existing'] / 'keep.txt').read_text(),
                'savedView': saved.get('view', {}),
                'state': service.get_state(),
            })

        async def agent(request):
            payload = await request.json()
            result = await service.app_bridge(payload.get('operation', 'dispatch'), payload['args'],
                                               service.state.get('selectedSessionId'))
            return web.json_response(result)

        app.router.add_get('/api/fixture/info', info)
        app.router.add_post('/api/fixture/agent', agent)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
        print(json.dumps({'port': port}), flush=True)
        stopped = asyncio.Event()
        for name in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(name, stopped.set)
        try:
            await stopped.wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
