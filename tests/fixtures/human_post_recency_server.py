"""Bounded 24-root HTTP/SSE fixture; acknowledgement and answer are independent.

Use only in the manager's qualified test environment. No model, SDK execution,
real history, provider inspection or live service is used.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import tempfile
import time

from library_performance_server import make_catalog, NoModelRuntime
from aiohttp import web
from amplifier_web.chat_navigation import navigation_activity
from amplifier_web.server import create_app


class HeldRuntime(NoModelRuntime):
    def __init__(self):
        super().__init__()
        self.inputs = []
        self.release = asyncio.Event()

    async def send(self, session, text, input_id, emit):
        self.inputs.append((session['id'], input_id))
        await self.release.wait()
        return {'accepted': True}


async def main():
    with tempfile.TemporaryDirectory(prefix='amplifier-human-post-recency-') as directory:
        root = Path(directory)
        os.environ.update(AMPLIFIER_HOME=str(root / 'native'), AMPLIFIER_WEB_HOME=str(root / 'app'),
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'),
                          AMPLIFIER_SESSION_STATE_HOME=str(root / 'session-state'))
        catalog = make_catalog(root / 'workspaces', 24, 1, 24)
        runtime = HeldRuntime()
        app = await create_app(root / 'app', workspace=catalog['workspaces'][0]['path'], runtime=runtime,
                               voice=False, background_updates=False, preload_providers=False)
        app['control_token'] = 'fixture-human-post-token'
        service = app['service']
        await service.history.close()
        service.history.index.scan_if_changed = lambda **kwargs: (object(), copy.deepcopy(catalog))
        await service.history.refresh()
        assert not service.state['sharedHistory'].get('error')
        sessions = [service._session(row['id']) for row in catalog['sessions']]
        for session in sessions:
            session.update(historyManaged=False, historyLoaded=True, historyLoading=False,
                           navigationActivityAt=session['recentActivityAt'], messages=[])
        target = sessions[0]
        for session in sessions[:2]:
            session.update(bundle='fixture-bundle',
                           selection={'instance': 'fixture-provider', 'model': 'fixture-model', 'effort': 'high'})
        service.state.update(selectedSessionId=target['id'], selectedWorkspaceId=target['workspaceId'])
        service.state['view'].update(navPinned=True, navChatScope='all')
        service.state.setdefault('setup', {}).update(providersLoadedAt=time.time(),
                                                     providersWorkspace=target['workspace'])
        service._publish()
        # Normalize ordinary browser/persistence setup before draft baselines.
        service._save()
        original_dispatch = service.dispatch
        catalog_reads = []

        async def observed_dispatch(action, args=None, *positional, **kwargs):
            if action == 'runtime.control':
                expected = {'sessionId': (args or {}).get('sessionId'),
                            'operation': 'configuration.catalog', 'args': {}}
                allowed_ids = {identity for session in sessions[:2]
                               for identity in (session['id'], session['nativeIdentity'])}
                if args != expected or expected['sessionId'] not in allowed_ids:
                    raise AssertionError('Only two-root synthetic catalog reads are admitted')
                catalog_reads.append(copy.deepcopy(args))
                result = {'effective': {'instance': 'fixture-provider', 'model': 'fixture-model', 'effort': 'high'},
                    'providers': [{'id': 'fixture-provider', 'info': {'display_name': 'Synthetic fixture provider',
                        'defaults': {'model': 'fixture-model'}}, 'configSchema': {'fields': []}}],
                    'modelsProviderId': 'fixture-provider', 'models': [{'id': 'fixture-model'}],
                    'fixtureScope': 'Scripted no-inference catalog; no account/provider acceptance'}
                return {'accepted': True, 'result': result, 'state': service.browser_state()}
            return await original_dispatch(action, args, *positional, **kwargs)

        service.dispatch = observed_dispatch
        progress = None
        ticks = 0

        async def emit_progress():
            nonlocal ticks
            while True:
                ticks += 1
                payload = {'sessionId': target['id']}
                await service.on_runtime_event('assistant.delta', {**payload, 'text': f' progress-{ticks}'})
                await service.on_runtime_event('runtime.tool', {**payload, 'tool': 'read_file',
                                                                'callId': 'fixture-tool', 'phase': 'pre'})
                await service.on_runtime_event('runtime.tool', {**payload, 'tool': 'read_file',
                                                                'callId': 'fixture-tool', 'phase': 'post'})
                await service.on_runtime_event('session.naming.progress', {**payload, 'completedInputs': []})
                await asyncio.sleep(.2)

        async def control(request):
            nonlocal progress
            args = await request.json()
            if args.get('running') and progress is None:
                progress = asyncio.create_task(emit_progress())
            elif not args.get('running') and progress:
                progress.cancel()
                await asyncio.gather(progress, return_exceptions=True)
                progress = None
            if args.get('history'):
                await service.history.refresh()
            if args.get('ack'):
                runtime.release.set()
            if args.get('ready'):
                await service.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'idle'})
            if args.get('attention'):
                await service.on_runtime_event('approval.requested', {
                    'sessionId': target['id'], 'id': 'fixture-approval', 'tool': 'write_file'})
            return web.json_response({'ticks': ticks})

        async def metrics(request):
            return web.json_response({'sessions': [row['id'] for row in sessions], 'target': target['id'],
                'ticks': ticks, 'inputs': runtime.inputs, 'runtimeCalls': runtime.calls,
                'catalogReads': catalog_reads,
                'durableClients': {identity: json.loads(value) for identity, value
                                   in service.db.execute("SELECT id,value FROM client_views")},
                'navigationActivityAt': navigation_activity(target), 'recentActivityAt': target['recentActivityAt'],
                'pending': target.get('navigationActivityPending'), 'posts': [
                    {'inputId': message['inputId'], 'navigationPost': message.get('navigationPost'),
                     'attachments': message.get('attachments', []), 'delivery': message.get('delivery')}
                    for message in target['messages'] if message.get('role') == 'user'],
                'assistantCount': sum(message.get('role') == 'assistant' for message in target['messages'])})

        app.router.add_post('/fixture/control', control)
        app.router.add_get('/fixture/metrics', metrics)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
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
            runtime.release.set()
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())