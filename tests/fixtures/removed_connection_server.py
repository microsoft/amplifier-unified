"""Real configuration/removal/catalog/UI; synthetic provider metadata and execution.

No credentials, model calls, or remote bundle preparation. Catalog composition
uses the real local bundle and scoped settings; only provider metadata is local.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def main(home):
    shared = home / 'shared'; shared.mkdir()
    workspace = home / 'workspace'; workspace.mkdir()
    root = home / 'root.yaml'
    root.write_text('bundle:\n  name: recovery-fixture\n  version: 1.0.0\n')
    from amplifier_web.builtin_behaviors import IMAGEGEN_BEHAVIOR_URI
    settings = {'bundle': {'active': str(root), 'app': []},
        'web_bundles': {'excluded': [IMAGEGEN_BEHAVIOR_URI]}, 'config': {'providers': [
            {'id': identity, 'module': 'provider-openai', 'config': {'default_model': model}}
            for identity, model in [('removed-account', 'old-model'), ('replacement-account', 'new-model')]]}}
    (shared / 'settings.yaml').write_text(json.dumps(settings))
    os.environ.update(AMPLIFIER_HOME=str(shared), AMPLIFIER_WEB_HOME=str(home / 'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(home / 'history'))
    from amplifier_web import provider_environment, draft_defaults
    from amplifier_web.draft_defaults_probe import query
    from amplifier_web.setup import SetupManager

    class Provider:
        def __init__(self, config): self.config = config
        def get_info(self): return {'id': 'openai', 'display_name': 'OpenAI', 'defaults': {'model': self.config.get('default_model')}}
        def get_config_schema(self): return {'fields': []}
        def list_models(self): return [{'id': self.config.get('default_model')}]
        async def close(self): pass
    provider_environment.provider_class = lambda module: Provider

    async def resolve(home, workspace, bundle=None, app_bundle=None, **kwargs):
        return await query({'home': str(home), 'workspace': str(workspace), 'bundle': bundle or str(root),
            'catalog': kwargs.get('catalog', False), 'refresh': kwargs.get('refresh', False),
            'sessionId': kwargs.get('session_id'), 'globalOnly': kwargs.get('global_only', False)})
    draft_defaults.resolve_defaults = resolve

    async def metadata(self, action, args, workspace):
        row = next(row for row in self.provider_rows(workspace) if row['id'] == args['id'])
        return {'modelsProviderId': row['id'], 'models': [{'id': row['config']['default_model']}],
            'providerMetadata': {'module': row['module'], 'info': {'display_name': 'OpenAI'}, 'configSchema': {'fields': []}}}
    SetupManager.cached_probe = metadata

    class Runtime:
        def __init__(self): self.started = []; self.sent = []; self.controls = []; self.workers = {}
        async def start(self, session, emit):
            self.started.append(copy.deepcopy(session.get('selection')))
            assert session['selection']['instance'] == 'replacement-account'
            assert session['replaceSavedSelection'] is True
            await emit('runtime.status', {'sessionId': session['id'], 'status': 'ready'})
        async def control(self, sid, operation, args):
            self.controls.append(operation)
            return {'selection': args} if operation == 'provider.select' else {}
        async def send(self, session, text, input_id, emit):
            from amplifier_web.runtime import RuntimeManager
            await RuntimeManager._start_for_input(self, session, emit)
            self.sent.append({'text': text, 'inputId': input_id, 'selection': copy.deepcopy(session['selection'])})
            await emit('assistant.message', {'sessionId': session['id'], 'inputId': input_id, 'text': 'Replacement received the new message.'})
            await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
        async def close(self): pass
    from aiohttp import web
    from amplifier_web.server import create_app
    runtime = Runtime()
    app = await create_app(home / 'app', workspace=str(workspace), runtime=runtime, voice=False, background_updates=False)
    app['control_token'] = 'fixture-browser-control-token'
    service = app['service']
    await service.dispatch('view.update', {'patch': {'setupJourney': 'done'}})
    await service.dispatch('session.create', {'title': 'Removed connection recovery', 'bundle': str(root)})
    session = service._session()
    session.update(status='error', selection={'instance': 'removed-account', 'model': 'old-model'},
        failure={'category': 'worker_startup', 'message': 'The saved connection is unavailable.'})
    service._message(session, 'user', 'Earlier uncertain request — never replay this',
        inputId='uncertain-prior-input', delivery={'status': 'unknown'})
    control = home / 'app' / 'sessions' / session['id'] / 'control-state.json'
    control.parent.mkdir(parents=True, exist_ok=True)
    control.write_text(json.dumps({'selection': session['selection']}))
    service._publish_changes(sessions={session['id']})

    async def inspect(request):
        return web.json_response({'sessionId': session['id'], 'selection': session.get('selection'),
            'bundle': session.get('bundle'), 'catalogRequests': list(getattr(service.management,'conversation_catalog_requests',{})), 'runtimeControl': service.state.get('runtimeControl',{}),
            'pending': session.get('pendingModelSelection'), 'started': runtime.started, 'sent': runtime.sent,
            'controls': runtime.controls, 'savedSelection': json.loads(control.read_text())['selection'],
            'messages': [{'id': row['id'], 'role': row['role'], 'inputId': row.get('inputId'), 'delivery': row.get('delivery')} for row in session['messages']]})
    app.router.add_get('/fixture', inspect)
    import socket
    sock = socket.socket(); sock.bind(('127.0.0.1', 0))
    url = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    app['allowed_origins'] = app['allowed_origins'] | {url}
    runner = web.AppRunner(app); await runner.setup()
    site = web.SockSite(runner, sock); await site.start()
    stopped = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM): asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    print(json.dumps({'url': url}), flush=True)
    try: await stopped.wait()
    finally:
        await runner.cleanup()
        sock.close()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='removed-connection-') as directory:
        asyncio.run(main(Path(directory)))
