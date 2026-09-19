"""Real HTTP/state/action performance with a synthetic, disposable native index.

Only discovery input is synthetic. AutomaticHistory ingestion, AppService,
SQLite/presentation persistence, projections, agent access and SSE are real.
No model, provider, user history or credentials are consulted.
"""
from __future__ import annotations
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import uuid

APP_ROOT = Path(os.environ.get('AMPLIFIER_PERF_APP_ROOT', Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(APP_ROOT))
from aiohttp import web
from amplifier_web.server import create_app
from amplifier_web.session_files import project_slug


class NoModelRuntime:
    def __init__(self):
        self.calls = []
    async def start(self, *args):
        self.calls.append('start')
        raise AssertionError('Navigation must not start a model runtime')
    async def send(self, *args):
        self.calls.append('send')
        raise AssertionError('Performance fixture cannot send a model request')
    async def control(self, *args):
        self.calls.append('control')
        raise AssertionError('Navigation must not inspect providers')
    async def stop(self, *args): pass
    async def close(self): pass


def make_catalog(root, count, workspace_count, root_count):
    workspaces = []
    for index in range(workspace_count):
        path = root / f'team-{index % 20:02}' / f'project-{index:04}'
        path.mkdir(parents=True)
        workspaces.append({'id': uuid.uuid5(uuid.NAMESPACE_URL, str(path)).hex,
            'nativeProject': project_slug(path), 'path': str(path), 'name': path.name,
            'available': True, 'sessionCount': 0, 'workerSessionCount': 0})
    sessions = []
    for index in range(count):
        workspace = workspaces[index % workspace_count]
        native_id = f'perf-chat-{index:06}'
        worker = index >= root_count
        workspace['workerSessionCount' if worker else 'sessionCount'] += 1
        title = f'Performance conversation {index:06}'
        at = 1_700_000_000 + index
        sessions.append({'id': uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-session:{workspace["nativeProject"]}/{native_id}').hex,
            'nativeIdentity': native_id, 'nativeProject': workspace['nativeProject'],
            'name': title, 'title': title, 'description': 'A synthetic saved conversation about a project, its current implementation, and the next useful development step.',
            'bundle': 'anchors', 'parentId': f'perf-chat-{index % workspace_count:06}' if worker else None,
            'sessionKind': 'worker' if worker else 'root', 'createdAt': at - 60, 'updatedAt': at,
            'recentActivityAt': at, 'turnCount': 4, 'transcriptAvailable': True,
            'transcriptRevision': [at * 1_000_000_000, 8192], 'nameSource': 'generated',
            'workspace': workspace['path'], 'workspaceId': workspace['id'],
            'canResume': not worker, 'readOnlyReason': 'Worker history is read-only.' if worker else None})
    return {'workspaces': workspaces, 'sessions': sessions, 'sessionCount': root_count,
            'workerSessionCount': count-root_count, 'issues': []}


async def main(args):
    with tempfile.TemporaryDirectory(prefix='amplifier-library-performance-') as directory:
        temp = Path(directory).resolve()
        os.environ.update(AMPLIFIER_HOME=str(temp/'native'), AMPLIFIER_WEB_HOME=str(temp/'app'),
            AMPLIFIER_UNIFIED_IMPORT_HOME=str(temp/'legacy'), AMPLIFIER_SESSION_STATE_HOME=str(temp/'session-state'))
        catalog = make_catalog(temp/'workspaces', args.summaries, args.workspaces, args.roots)
        runtime = NoModelRuntime()
        app = await create_app(temp/'app', workspace=catalog['workspaces'][0]['path'], runtime=runtime,
                               voice=False, background_updates=False, preload_providers=False)
        app['control_token'] = 'fixture-library-performance-token'
        service = app['service']
        await service.history.close()
        service.history.index.scan = lambda **kwargs: copy.deepcopy(catalog)
        await service.history.refresh()
        if service.state['sharedHistory'].get('error'):
            raise AssertionError(service.state['sharedHistory']['error'])
        selected = service._session(catalog['sessions'][0]['id'])
        selected.update(historyLoaded=True, historyLoading=False, messages=[
            {'id':'fixture-question','role':'user','text':'Show this saved conversation.', 'createdAt':1_700_000_000},
            {'id':'fixture-answer','role':'assistant','text':'The saved conversation is ready.', 'createdAt':1_700_000_001}])
        service.state.update(selectedSessionId=selected['id'], selectedWorkspaceId=catalog['workspaces'][0]['id'])
        service.state['view'].update(navPinned=True, navChatScope='all')
        service.state.setdefault('setup', {}).update(providersLoadedAt=time.time(), providersWorkspace=selected['workspace'])
        service._publish()
        counters = {}
        def reset():
            counters.clear()
            counters.update(publications=0, stateCalls=0, stateMs=0, saveCalls=0, saveMs=0, endpoints=[])
        reset()
        original_publish, original_state, original_save = service._publish, service.get_state, service._save
        def publish(*positional, **kwargs):
            counters['publications'] += 1
            return original_publish(*positional, **kwargs)
        def get_state(*positional, **kwargs):
            started = time.perf_counter()
            try: return original_state(*positional, **kwargs)
            finally:
                counters['stateCalls'] += 1
                counters['stateMs'] += (time.perf_counter()-started)*1000
        def save(*positional, **kwargs):
            started = time.perf_counter()
            try: return original_save(*positional, **kwargs)
            finally:
                counters['saveCalls'] += 1
                counters['saveMs'] += (time.perf_counter()-started)*1000
        service._publish, service.get_state, service._save = publish, get_state, save
        @web.middleware
        async def measured(request, handler):
            started = time.perf_counter()
            response = await handler(request)
            if request.path in {'/api/state','/api/actions','/api/view'}:
                counters['endpoints'].append({'path':request.path,'method':request.method,
                    'ms':round((time.perf_counter()-started)*1000,2),
                    'bytes':len(response.body or b'') if isinstance(response,web.Response) else None})
                counters['endpoints'] = counters['endpoints'][-100:]
            return response
        app.middlewares.append(measured)
        async def metrics(request):
            return web.json_response({**counters, 'runtimeCalls':runtime.calls,
                'summaries':args.summaries,'workspaces':args.workspaces,'roots':args.roots,
                'selectedId':selected['id'],
                'offPageRootId':catalog['sessions'][min(args.roots-1,args.roots//2+1)]['id'],
                'offPageRootTitle':catalog['sessions'][min(args.roots-1,args.roots//2+1)]['title']})
        async def clear(request):
            reset(); return web.json_response({'reset':True})
        async def agent(request):
            payload = await request.json()
            return web.json_response(await service.app_bridge(payload.get('operation','get_state'),payload['args'],service.state['selectedSessionId']))
        app.router.add_get('/api/fixture/metrics',metrics)
        app.router.add_post('/api/fixture/reset',clear)
        app.router.add_post('/api/fixture/agent',agent)
        runner=web.AppRunner(app); await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0); await site.start()
        port=site._server.sockets[0].getsockname()[1]
        app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
        print(json.dumps({'port':port}),flush=True)
        stopped=asyncio.Event()
        for sig in (signal.SIGTERM,signal.SIGINT):asyncio.get_running_loop().add_signal_handler(sig,stopped.set)
        try: await stopped.wait()
        finally: await runner.cleanup()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--summaries',type=int,required=True)
    parser.add_argument('--workspaces',type=int,required=True)
    parser.add_argument('--roots',type=int,required=True)
    args=parser.parse_args()
    if not 1 <= args.workspaces <= args.roots <= args.summaries:parser.error('Require 1 <= workspaces <= roots <= summaries')
    asyncio.run(main(args))
