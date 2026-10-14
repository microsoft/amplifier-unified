"""Real HTTP/SSE with a Spark-sized disposable catalog and synthetic progress."""
import asyncio
import json
import os
from pathlib import Path
import signal
import tempfile
import time

from library_performance_server import make_catalog, NoModelRuntime
from aiohttp import web
from amplifier_web.server import create_app
from amplifier_web.capacity import admission
from amplifier_web.native_catalog import NativeCatalog
from amplifier_web.native_history import NativeHistory
from amplifier_web.session_projection import view_path


async def main():
    with tempfile.TemporaryDirectory(prefix='amplifier-active-client-performance-') as directory:
        root = Path(directory)
        os.environ.update(AMPLIFIER_HOME=str(root/'native'), AMPLIFIER_WEB_HOME=str(root/'app'),
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(root/'legacy'),
                          AMPLIFIER_SESSION_STATE_HOME=str(root/'session-state'))
        catalog = make_catalog(root/'workspaces', 22915, 3932, 4000)
        # Seed the real indexed history boundary. Overriding the obsolete full
        # scan hook forces every synthetic chat into the admitted working set,
        # bypassing the very residency contract this browser scenario measures.
        (root/'app').mkdir()
        native = NativeCatalog(root/'app'/'native-catalog.sqlite3', root/'native')
        projects = {row['nativeProject']:{'workspace':row,'sessions':[]} for row in catalog['workspaces']}
        for row in catalog['sessions']:
            projects[row['nativeProject']]['sessions'].append(row)
        native.save(projects, {}, {}, set())
        for row in catalog['sessions'][:2]:
            folder = root/'native'/'projects'/row['nativeProject']/'sessions'/row['nativeIdentity']
            folder.mkdir(parents=True)
            (folder/'metadata.json').write_text(json.dumps({'session_id':row['nativeIdentity'],
                'working_dir':row['workspace'],'name':row['title'],'bundle':'anchors','parent_id':None}))
            (folder/'transcript.jsonl').write_text(json.dumps({'role':'user','content':'Synthetic saved question'})+'\n'
                +json.dumps({'role':'assistant','content':'Synthetic saved reply'})+'\n')
        runtime = NoModelRuntime()
        app = await create_app(root/'app', workspace=catalog['workspaces'][0]['path'], runtime=runtime,
                               voice=False, background_updates=False, preload_providers=False)
        app['control_token'] = 'fixture-active-client-token'
        service = app['service']
        await service.history.close()
        service.history.index = NativeHistory(root/'native', watch=False,
            cache_path=native.path, directory=True)
        # The disposable catalog is a frozen performance input, not a filesystem
        # discovery test. Exercise ordinary persisted scan_changes/refresh with
        # a quiet watcher and no scheduled reconciliation during this journey.
        service.history.index._invalidations = lambda: (True, set())
        service.history.index._reconcile_at = time.monotonic() + 900
        await service.history.refresh(force=False)
        assert native.count() == 22915 and service.directory.root_count() == 4000
        assert len(service.state['workspaces']) == 3932
        assert len(service.state['sessions']) <= 6
        assert not service.state['sharedHistory'].get('error')
        sessions = [service._session(row['id']) for row in catalog['sessions'][:2]]
        for index, session in enumerate(sessions):
            session.update(historyManaged=False, historyLoaded=True, historyLoading=False, messages=[
                {'id': 'fixture-user-'+str(index), 'role': 'user', 'text': 'Synthetic saved question'},
                {'id': 'fixture-answer-'+str(index), 'role': 'assistant', 'text': 'Synthetic saved reply'}])
        service.state.update(selectedSessionId=sessions[0]['id'], selectedWorkspaceId=sessions[0]['workspaceId'])
        service.state['view'].update(navPinned=True, navChatScope='all')
        service.state.setdefault('setup', {}).update(providersLoadedAt=time.time(), providersWorkspace=sessions[0]['workspace'])
        if os.environ.get('AMPLIFIER_TRANSPORT_FIXTURE'):
            # Simulate large settings/catalog sections without credentials or
            # model calls. These values must not ride along with view changes.
            service.state.setdefault('updates', {})['application'] = {'current': '0.20.1', 'releaseNotes': [{'version': '0.20.1', 'summary': 'Saved release documentation. ' * 80_000}]}
            sessions[0]['execution'] = {'nodes': [], 'turns': [], 'retiredUsageNodes': [
                {'id': 'historical-'+str(i), 'kind': 'llm', 'phase': 'completed',
                 'provider': 'fixture', 'model': 'fixture', 'detail': 'Historical accounting ' * 40}
                for i in range(1000)]}
        service._publish()
        assert len(service.state['sessions']) <= 6
        progress = None
        ticks = 0

        async def emit_progress():
            nonlocal ticks
            await service.on_runtime_event('runtime.status', {'sessionId': sessions[0]['id'], 'status': 'working'})
            try:
                while True:
                    ticks += 1
                    await service.on_runtime_event('assistant.delta', {'sessionId': sessions[0]['id'], 'text': ' progress-'+str(ticks)})
                    await asyncio.sleep(.2)
            finally:
                await service.on_runtime_event('runtime.status', {'sessionId': sessions[0]['id'], 'status': 'idle'})

        async def control(request):
            nonlocal progress
            args = await request.json()
            if args.get('running') and progress is None:
                progress = asyncio.create_task(emit_progress())
            elif not args.get('running') and progress:
                progress.cancel()
                await asyncio.gather(progress, return_exceptions=True)
                progress = None
            return web.json_response({'ticks': ticks})

        async def admit(request):
            identity = (await request.json())['id']
            native = sessions[0].get('runtimeSessionId') or sessions[0]['id']
            result = await admission(service, sessions[0]['id'], {'call': {
                'id': identity, 'rootSessionId': native, 'sessionId': native,
                'producerId': 'fixture-active-client', 'kind': 'llm', 'phase': 'running',
                'provider': 'synthetic', 'model': 'synthetic', 'startedAt': time.time()}})
            saved = json.loads(view_path(service.data_dir, sessions[0]).read_text())
            execution = saved['execution']
            receipts = execution.get('nodes', []) + execution.get('retiredUsageNodes', [])
            assert any(row.get('id') == identity and row.get('admittedAt') for row in receipts)
            return web.json_response(result)

        async def metrics(request):
            assert len(service.state['sessions']) <= 6
            assert native.count() == 22915
            return web.json_response({'sessions': [s['id'] for s in sessions], 'ticks': ticks,
                                      'subscriptions': len(service.queue_clients), 'runtimeCalls': runtime.calls})

        app.router.add_post('/fixture/progress', control)
        app.router.add_post('/fixture/admit', admit)
        app.router.add_get('/fixture/metrics', metrics)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = 'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
        app['allowed_origins'] |= {url}
        print(json.dumps({'url': url}), flush=True)
        stopped = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
        try:
            await stopped.wait()
        finally:
            if progress:
                progress.cancel()
                await asyncio.gather(progress, return_exceptions=True)
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
