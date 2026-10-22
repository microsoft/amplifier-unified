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
from amplifier_web.session_files import project_slug, amplifier_home
from amplifier_web.native_catalog import NativeCatalog
from amplifier_web.native_history import NativeHistory


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

    async def stop(self, *args, reason="stopped"):
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
        # Reconnect observes actual native bytes, never an invented revision.
        directory = amplifier_home() / 'projects' / workspace['nativeProject'] / 'sessions' / native_id
        directory.mkdir(parents=True)
        transcript = directory / 'transcript.jsonl'
        transcript.write_text('')
        (directory / 'metadata.json').write_text(json.dumps({
            'session_id': native_id, 'working_dir': str(path), 'bundle': 'bundle:anchors',
            'name': f'Recent fixture {index:03}', 'turn_count': 0}))
        info = transcript.stat()
        rows.append({'id': uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-session:{workspace["nativeProject"]}/{native_id}').hex,
            'nativeIdentity': native_id, 'nativeProject': workspace['nativeProject'],
            'title': f'Recent fixture {index:03}', 'description': '', 'bundle': 'anchors',
            'sessionKind': 'root' if index < 134 else 'worker', 'parentId': None if index < 134 else 'recent-fixture-000',
            'createdAt': 900-index, 'recentActivityAt': 1000-index, 'turnCount': 1,
            'transcriptAvailable': True, 'transcriptRevision': [info.st_mtime_ns, info.st_size],
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
        from configured_browser import seed_connection
        seed_connection(root / 'native')
        catalog = make_catalog(root / 'workspaces')
        (root / 'app').mkdir()
        native = NativeCatalog(root / 'app' / 'native-catalog.sqlite3', root / 'native')
        projects = {row['nativeProject']: {'workspace': row, 'sessions': []} for row in catalog['workspaces']}
        for row in catalog['sessions']:
            projects[row['nativeProject']]['sessions'].append(row)
        native.save(projects, {}, {}, set())
        runtime = NoModelRuntime()
        app = await application.create_app(root / 'app', workspace=catalog['workspaces'][0]['path'],
                               runtime=runtime, voice=False, background_updates=False,
                               preload_providers=False)
        app['control_token'] = 'fixture-recent-visibility-token'
        service = app['service']
        await service.dispatch('view.update', {'patch': {'setupJourney': 'done'}})
        await service.history.close()
        service.history.index = NativeHistory(root / 'native', watch=False, cache_path=native.path, directory=True)
        # Frozen synthetic catalog; this journey exercises presentation rather
        # than discovery. Exact canonical bytes still back selected chat reads.
        service.history.index._invalidations = lambda: (True, set())
        service.history.index._reconcile_at = time.monotonic() + 900
        await service.history.refresh(force=False)
        assert native.count() == 136
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
            providersWorkspace=selected['workspace'], providers=[{'id': 'fixture-provider',
                'enabled': True, 'config': {'default_model': 'fixture-model'}}])
        service._publish()
        initial_sessions = copy.deepcopy(service.state['sessions'])
        mutations = []
        original_dispatch = service.dispatch

        async def observed_dispatch(action, args=None, *positional, **kwargs):
            if action == 'runtime.control':
                # This fixture owns no provider runtime. Its two synthetic
                # roots nevertheless expose a coherent, labelled browse catalog.
                expected = {'sessionId': (args or {}).get('sessionId'),
                            'operation': 'configuration.catalog', 'args': {}}
                allowed_ids = {selected['id'], selected['nativeIdentity'],
                               commissioned['id'], commissioned['nativeIdentity']}
                if args != expected or expected['sessionId'] not in allowed_ids:
                    raise AssertionError('Only scoped synthetic catalog reads are admitted')
                catalog = {'effective': {'instance': 'fixture-provider', 'model': 'fixture-model', 'effort': 'high'},
                    'providers': [{'id': 'fixture-provider', 'info': {'display_name': 'Synthetic fixture provider',
                        'defaults': {'model': 'fixture-model'}}, 'configSchema': {'fields': []}}],
                    'modelsProviderId': 'fixture-provider', 'models': [{'id': 'fixture-model'}],
                    'fixtureScope': 'Scripted no-inference catalog; no account/provider acceptance'}
                return {'accepted': True, 'result': catalog, 'state': service.browser_state()}
            if action in {'shell.view.update', 'shell.query', 'session.select', 'conversation.send'}:
                mutations.append({'action': action, 'args': copy.deepcopy(args)})
            return await original_dispatch(action, args, *positional, **kwargs)
        service.dispatch = observed_dispatch

        # Faults affect only this requested presentation resource, leaving
        # commands, client state, canonical history and other clients intact.
        faults = {'holdClient': None, 'omissions': 0, 'unnamedId': None, 'unnamedClient': None,
                  'holdPinsClient': None, 'pinOmissions': 0}
        original_navigation = service.shell.navigation

        def navigation(*args, **kwargs):
            if (faults['holdPinsClient'] is not None
                    and kwargs.get('identity') == faults['holdPinsClient']
                    and args[1]['id'] == 'chats' and kwargs.get('_page_only')
                    and 'pinnedNavigation' in kwargs.get('navigation_resources', ())):
                from amplifier_web.shell_query_owner import PendingQuery
                faults['pinOmissions'] += 1
                raise PendingQuery()
            if (faults['holdClient'] is not None
                    and kwargs.get('identity') == faults['holdClient']
                    and args[1]['id'] == 'chats' and kwargs.get('_page_only')
                    and 'recentNavigation' in kwargs.get('navigation_resources', ())):
                # Let the real resource owner publish pending/completed revisions.
                # Removing a page after snapshot() would reuse its completed
                # revision on retry, correctly rejected as stale by the client.
                from amplifier_web.shell_query_owner import PendingQuery
                faults['omissions'] += 1
                raise PendingQuery()
            result = original_navigation(*args, **kwargs)
            if (faults['unnamedId'] is None or kwargs.get('identity') != faults['unnamedClient']
                    or args[1]['id'] != 'chats'):
                return result
            result = copy.deepcopy(result)
            pending = [result]
            while pending:
                value = pending.pop()
                if isinstance(value, dict):
                    if value.get('id') == faults['unnamedId']:
                        value.update(title='Conversation lazy0001', titleSource='unnamed', nativeNameSource='unnamed')
                    pending.extend(value.values())
                elif isinstance(value, list):
                    pending.extend(value)
            return result

        service.shell.navigation = navigation

        async def control(request):
            args = await request.json()
            if 'holdPinsFor' in args:
                identity = args['holdPinsFor']
                assert identity is None or identity in service.clients.records
                previous = faults['holdPinsClient']
                faults['holdPinsClient'] = identity
                if identity is not None and args.get('invalidatePins'):
                    service.shell.resources.invalidate({'pinnedItemIds'})
                    service.shell.notify(identity)
                if identity is None and previous is not None and not args.get('quietPinRelease'):
                    # Wake the existing resource owner, without changing the
                    # app revision or inventing a completed projection.
                    service.shell.notify(previous)
            if 'holdRecentFor' in args:
                identity = args['holdRecentFor']
                assert identity is None or identity in service.clients.records
                faults['holdClient'] = identity
            if 'unnamedSummary' in args:
                identity = args['clientId']
                assert identity in service.clients.records
                assert args['unnamedSummary'] in {row['id'] for row in sessions}
                faults['unnamedId'] = args['unnamedSummary']
                faults['unnamedClient'] = identity
                service.shell.resources.invalidate()
                service.shell.notify(identity)
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
                removed = {row['id'] for row in sessions} - keep
                service.state['sessions'] = [row for row in service.state['sessions'] if row['id'] in keep]
                service.forget_session_records(removed)
                smaller = {name: {**project, 'sessions': [row for row in project['sessions'] if row['id'] in keep]}
                           for name, project in projects.items()}
                native.save(smaller, projects, {}, set())
                service._publish()
                assert all(service.directory.resolve(identity) is None for identity in removed)
            return web.json_response({'ok': True})

        async def metrics(request):
            current = {row['id']: row for row in service.state['sessions']}
            differences = {}
            for original in initial_sessions:
                after = current.get(original['id'])
                if after != original:
                    differences[original['id']] = {'removed': after is None} if after is None else {
                        key: {'before': original.get(key), 'after': after.get(key),
                              'beforePresent': key in original, 'afterPresent': key in after}
                        for key in original.keys() | after.keys()
                        if original.get(key) != after.get(key) or (key in original) != (key in after)}
            durable_clients = {
                identity: json.loads(value)
                for identity, value in service.db.execute("SELECT id,value FROM client_views")
            }
            return web.json_response({'selected': selected['id'], 'commissioned': commissioned['id'],
                'pinned': pinned['id'], 'fork': fork['id'], 'legacy': legacy['id'],
                'excluded': [row['id'] for row in sessions[-2:]], 'runtimeCalls': runtime.calls,
                'mutations': mutations, 'withheldRecentProjections': faults['omissions'],
                'withheldPinProjections': faults['pinOmissions'], 'sessionCount': len(service.state['sessions']),
                'sessionsUnchanged': service.state['sessions'] == initial_sessions,
                'sessionDifferences': differences, 'durableClients': durable_clients,
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
