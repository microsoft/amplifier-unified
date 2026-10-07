"""Real HTTP/SSE Recent fixture, one server, no SDK/model work.

Synthetic indexed roots exercise navigation, shell records, client isolation
and actions in an isolated test environment.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import tempfile
import time
import sys
import uuid

from aiohttp import web
import amplifier_web.server as application
from amplifier_web.session_files import project_slug


class NoModelRuntime:
    def __init__(self):
        self.calls = []

    async def start(self, *args):
        self.calls.append('start')
        raise AssertionError('Recent must not start a runtime')

    async def send(self, *args):
        self.calls.append('send')
        raise AssertionError('Recent must not send a model input')

    async def control(self, *args):
        self.calls.append('control')
        raise AssertionError('Recent must not inspect providers')

    async def stop(self, *args):
        self.calls.append('stop')
        raise AssertionError('Recent must not stop a task')

    async def close(self):
        pass


def make_catalog(path):
    # Only synthetic discovery input; no import-path rewrites to a checkout.
    path.mkdir(parents=True)
    workspace = {'id': uuid.uuid5(uuid.NAMESPACE_URL, str(path)).hex,
                 'nativeProject': project_slug(path), 'path': str(path), 'name': 'Recent fixture',
                 'available': True, 'sessionCount': 134, 'workerSessionCount': 2}
    rows = []
    for index in range(136):
        native_id = f'recent-fixture-{index:03}'
        rows.append({'id': uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-session:{workspace["nativeProject"]}/{native_id}').hex,
            'nativeIdentity': native_id, 'nativeProject': workspace['nativeProject'],
            'title': f'Recent fixture {index:03}', 'description': '', 'bundle': 'anchors',
            'sessionKind': 'root' if index < 134 else 'worker', 'parentId': None if index < 134 else 'recent-fixture-000',
            'createdAt': 900-index, 'recentActivityAt': 1000-index, 'turnCount': 1,
            'transcriptAvailable': True, 'transcriptRevision': [1, 1],
            'workspace': str(path), 'workspaceId': workspace['id'], 'canResume': index < 134})
    return {'workspaces': [workspace], 'sessions': rows, 'sessionCount': 134, 'workerSessionCount': 2, 'issues': []}


async def main():
    with tempfile.TemporaryDirectory(prefix='amplifier-recent-visibility-') as directory:
        root = Path(directory)
        os.environ.update(AMPLIFIER_HOME=str(root / 'native'), AMPLIFIER_WEB_HOME=str(root / 'app'),
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'),
                          AMPLIFIER_SESSION_STATE_HOME=str(root / 'session-state'))
        expected = os.environ.get('AMPLIFIER_RECENT_EXPECTED_PACKAGE')
        if expected and Path(application.__file__).resolve().parent != Path(expected).resolve():
            raise AssertionError('Imported application package does not match the declared installed package')
        catalog = make_catalog(root / 'workspaces')
        runtime = NoModelRuntime()
        app = await application.create_app(root / 'app', workspace=catalog['workspaces'][0]['path'],
                               runtime=runtime, voice=False, background_updates=False,
                               preload_providers=False)
        app['control_token'] = 'fixture-recent-visibility-token'
        service = app['service']
        await service.history.close()
        service.history.index.scan_if_changed = lambda **kwargs: (object(), copy.deepcopy(catalog))
        await service.history.refresh()
        assert not service.state['sharedHistory'].get('error')
        sessions = [service._session(row['id']) for row in catalog['sessions']]
        for index, session in enumerate(sessions):
            session.update(historyManaged=False, historyLoaded=True, historyLoading=False,
                           title=f'Recent fixture {index:03}', recentActivityAt=1000-index,
                           navigationActivityAt=1000-index, messages=[])
        creator, selected, commissioned, pinned, legacy, fork = sessions[:6]
        origin = {'creatorSessionId': creator['id'], 'requestId': 'fixture-commission',
                  'brief': 'Private commissioning brief', 'grantId': 'fixture-grant'}
        commissioned.update(collaboration=copy.deepcopy(origin), recentActivityAt=974.5, navigationActivityAt=974.5)
        pinned['collaboration'] = copy.deepcopy(origin)
        legacy['collaboration'] = {'creatorSessionId': creator['id']}  # Unknown is visible.
        fork['parentId'] = creator['id']  # Human independent fork is still a root.
        sessions[-1]['sessionKind'] = 'internal'
        selected.update(bundle='fixture-bundle',
                        selection={'instance': 'fixture-provider', 'model': 'fixture-model', 'effort': 'high'},
                        messages=[{'id': 'fixture-saved', 'role': 'assistant',
                                   'text': 'Saved reply remains unchanged.', 'createdAt': 900}])
        service.state.update(selectedSessionId=selected['id'], selectedWorkspaceId=selected['workspaceId'],
                             pinnedSessionIds=[pinned['id']])
        service.state['view'].update(navPinned=True, navChatScope='all')
        service.state.setdefault('setup', {}).update(providersLoadedAt=time.time(),
                                                     providersWorkspace=selected['workspace'])
        service._publish()
        initial_sessions = copy.deepcopy(service.state['sessions'])
        mutations = []
        original_dispatch = service.dispatch

        async def observed_dispatch(action, args=None, *positional, **kwargs):
            if action in {'shell.view.update', 'shell.query', 'session.select', 'conversation.send'}:
                mutations.append({'action': action, 'args': copy.deepcopy(args)})
            return await original_dispatch(action, args, *positional, **kwargs)
        service.dispatch = observed_dispatch

        async def control(request):
            args = await request.json()
            if args.get('progress'):
                await service.on_runtime_event('runtime.status', {'sessionId': selected['id'], 'status': 'working'})
                await service.on_runtime_event('assistant.delta', {'sessionId': selected['id'], 'text': ' synthetic progress'})
            if args.get('ready'):
                await service.on_runtime_event('runtime.status', {'sessionId': selected['id'], 'status': 'idle'})
            if args.get('attention'):
                await service.on_runtime_event('approval.requested', {
                    'sessionId': selected['id'], 'id': 'fixture-approval', 'tool': 'write_file'})
            if args.get('shrink'):
                # Deliberately shrink only this synthetic catalog, not user history.
                keep = {row['id'] for row in sessions[:7]}
                service.state['sessions'] = [row for row in service.state['sessions'] if row['id'] in keep]
                service._publish()
            return web.json_response({'ok': True})

        async def metrics(request):
            return web.json_response({'selected': selected['id'], 'commissioned': commissioned['id'],
                'pinned': pinned['id'], 'fork': fork['id'], 'legacy': legacy['id'],
                'excluded': [row['id'] for row in sessions[-2:]], 'runtimeCalls': runtime.calls,
                'mutations': mutations, 'sessionCount': len(service.state['sessions']),
                'sessionsUnchanged': service.state['sessions'] == initial_sessions,
                'bundle': selected['bundle'], 'selection': selected.get('selection')})
        app.router.add_post('/fixture/control', control)
        app.router.add_get('/fixture/metrics', metrics)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
        app['allowed_origins'] |= {url}
        print(json.dumps({'url': url, 'applicationModule': str(Path(application.__file__).resolve()),
                          'python': sys.executable, 'expectedPackageChecked': bool(expected)}), flush=True)
        stopped = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
        try:
            await stopped.wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())