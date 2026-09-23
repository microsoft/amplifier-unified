"""Real chat navigation and persistence with disposable histories; no model calls."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiohttp import web
from amplifier_web.server import create_app
from amplifier_web.session_files import project_slug


class ObservedRuntime:
    def __init__(self):
        self.started = []
        self.sent = []
        self.jobs = {}

    async def start(self, session, emit):
        self.started.append(session['id'])
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'ready'})

    async def send(self, session, text, input_id, emit):
        self.sent.append(session['id'])
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'working'})
        if os.environ.get('AMPLIFIER_SHELL_PROOF') == '1' and text == 'Hold work during shell changes':
            self.jobs[session['id']] = asyncio.create_task(asyncio.Event().wait())
            return
        await emit('assistant.message', {'sessionId': session['id'], 'text': 'Fixture reply to a real submitted turn.', 'inputId': input_id})
        await emit('runtime.generation', {'sessionId': session['id'], 'event': 'generation.finished',
                   'generation_id': 'fixture-turn', 'input_ids': [input_id], 'active_job_ids': [], 'disposition': 'manager_turn_finished'})
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})

    async def control(self, *args):
        return {}

    async def stop(self, *args):
        pass

    async def close(self):
        for task in self.jobs.values():
            task.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)


def native_chat(home, workspace, identity, title, at, *, exists=True, worker=False):
    if exists:
        workspace.mkdir(parents=True, exist_ok=True)
    directory = home / 'projects' / project_slug(workspace) / 'sessions' / identity
    directory.mkdir(parents=True)
    metadata = {'session_id': identity, 'bundle': 'bundle:anchors', 'name': title,
                'working_dir': str(workspace), 'created': '2020-01-01T00:00:00Z', 'turn_count': 1}
    if worker:
        metadata['parent_id'] = 'alpha-201'
    (directory / 'metadata.json').write_text(json.dumps(metadata))
    transcript = directory / 'transcript.jsonl'
    transcript.write_text(''.join(json.dumps(row) + '\n' for row in [
        {'role': 'user', 'content': 'Saved question'}, {'role': 'assistant', 'content': 'Saved response'},
    ]))
    os.utime(transcript, (at, at))


async def main():
    with tempfile.TemporaryDirectory(prefix='amplifier-chat-library-') as directory:
        temp = Path(directory).resolve()
        home, data = temp / 'native', temp / 'app'
        os.environ.update(AMPLIFIER_HOME=str(home), AMPLIFIER_WEB_HOME=str(data),
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(temp / 'legacy'),
                          AMPLIFIER_SESSION_STATE_HOME=str(temp / 'session-state'))
        paths = {'one': temp / 'folders/development/playground',
                 'two': temp / 'folders/research/playground',
                 'missing': temp / 'folders/deleted/playground'}
        for index in range(202):
            title = 'Review very long workspace navigation names without changing the height of neighboring rows' if index == 201 and os.environ.get('AMPLIFIER_NAVIGATION_PROOF') == '1' else f'Alpha {index:03}'
            native_chat(home, paths['one'], f'alpha-{index:03}', title, 1_700_000_000 + index)
        for index in range(3):
            native_chat(home, paths['two'], f'beta-{index:03}', f'Beta {index:03}', 1_700_001_000 + index)
        native_chat(home, paths['one'], 'worker-only', 'Hidden worker', 1_700_009_000, worker=True)
        native_chat(home, paths['missing'], 'missing-root', 'Deleted workspace chat', 1_700_010_000, exists=False)
        runtime = ObservedRuntime()
        stopped, restart = asyncio.Event(), asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
        port, generation, initial, quiet = 0, 0, None, None
        while not stopped.is_set():
            restart.clear()
            app = await create_app(data, workspace=paths['one'], runtime=runtime,
                                   voice=False, background_updates=False, preload_providers=False)
            app['control_token'] = 'fixture-browser-control-token'
            service = app['service']
            await service.history.refresh()
            if generation == 0:
                await service.dispatch('session.create', {'title': 'Quiet older chat', 'workspace': str(paths['one'])})
                quiet = service.state['selectedSessionId']
                chat = service._session(quiet)
                service._message(chat, 'user', 'An earlier fixture question')
                service._message(chat, 'assistant', 'An earlier fixture response')
                chat.update(createdAt=1_600_000_000, updatedAt=1_600_000_000,
                            recentActivityAt=1_600_000_000, navigationActivityAt=1_600_000_000,
                            deferRuntimeUntilInteraction=True)
                chat.pop('navigationActivityPending', None)
                for row in chat['messages']:
                    row['createdAt'] = 1_600_000_000
                initial = next(row['id'] for row in service.state['sessions'] if row.get('nativeIdentity') == 'alpha-201')
                await service.dispatch('session.select', {'id': initial})
                await service.dispatch('view.update', {'patch': {'navPinned': True}})
                service._save()
            if generation == 0 and os.environ.get('AMPLIFIER_NAVIGATION_PROOF') == '1':
                by_native = {row.get('nativeIdentity'): row for row in service.state['sessions']}
                by_native['alpha-200'].update(status='working', recentActivityAt=time.time()-120)
                by_native['alpha-199'].update(approvals=[{'id': 'navigation-approval', 'title': 'Allow workspace access', 'status': 'pending'}], recentActivityAt=time.time()-240)
                by_native['alpha-198'].update(completion={'id': 'navigation-completion', 'at': time.time()-600}, recentActivityAt=time.time()-600)
                by_native['alpha-197'].update(error='Fixture operation failed', status='error', recentActivityAt=time.time()-900)
                service._save()
            generation += 1

            async def info(request):
                return web.json_response({'paths': {key: str(value) for key, value in paths.items()},
                    'initialSession': initial, 'quietSession': quiet, 'generation': generation,
                    'runtimeJobs': [key for key, task in runtime.jobs.items() if not task.done()], 'runtimeStarts': runtime.started, 'runtimeSends': runtime.sent, 'state': service.get_state()})

            async def agent(request):
                payload = await request.json()
                result = await service.app_bridge(payload.get('operation', 'dispatch'), payload['args'],
                                                 service.state.get('selectedSessionId'))
                return web.json_response(result)

            async def restart_server(request):
                asyncio.get_running_loop().call_later(.1, restart.set)
                return web.json_response({'restarting': True, 'generation': generation})

            app.router.add_get('/api/fixture/info', info)
            app.router.add_post('/api/fixture/agent', agent)
            app.router.add_post('/api/fixture/restart', restart_server)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', port)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
            if generation == 1:
                print(json.dumps({'port': port}), flush=True)
            waits = [asyncio.create_task(stopped.wait()), asyncio.create_task(restart.wait())]
            try:
                await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in waits:
                    task.cancel()
                await asyncio.gather(*waits, return_exceptions=True)
                await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
