"""Rendered dormant controls fixture: real HTTP host; every worker start fails."""
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aiohttp import web
from amplifier_web import draft_defaults
from amplifier_web.bundles import BundleManager
from amplifier_web.server import create_app


class AbsentRuntime:
    def __init__(self):
        self.calls = []
        self.workers = {}

    async def start(self, *args, **kwargs):
        self.calls.append('start')
        raise AssertionError('Dormant browsing must not start a conversation worker')

    async def prewarm(self, *args, **kwargs):
        self.calls.append('prewarm')
        raise AssertionError('Automatic prewarm is not authorized')

    async def send(self, *args, **kwargs):
        self.calls.append('send')
        raise AssertionError('This fixture does not authorize an input')

    async def control(self, *args, **kwargs):
        self.calls.append('control')
        raise AssertionError('No mounted worker exists')

    async def stop(self, *args, **kwargs):
        self.calls.append('stop')

    async def close(self):
        pass


async def main(root):
    shared, home, workspace = (root / name for name in ('shared', 'app', 'workspace'))
    shared.mkdir()
    workspace.mkdir()
    os.environ.update(AMPLIFIER_HOME=str(shared), AMPLIFIER_WEB_HOME=str(home),
        AMPLIFIER_SESSION_STATE_HOME=str(root/'ownership'),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root/'legacy'))
    (shared/'settings.yaml').write_text(json.dumps({'config': {'providers': [
        {'id': 'one', 'module': 'provider-fixture', 'config': {'default_model': 'first'}}]}}))
    metadata = {'info': {'display_name': 'Fixture provider', 'defaults': {'model': 'first'}},
        'configSchema': {'fields': [{'id': 'reasoning_effort', 'choices': ['low', 'high']}]}}
    catalog = {'phase': 'ready', 'configurationRevision': 0, 'nextMessageRevision': 0,
        'providers': [{'id': 'one', 'sharedCatalogKey': 'offline-one', 'info': metadata['info']}],
        'effective': {'instance': 'one', 'model': 'first', 'effort': 'low'}}
    models = {'phase': 'ready', 'models': [{'id': 'first'}, {'id': 'second'}], 'metadata': metadata}
    resolution = {'mode': 'held', 'calls': 0}
    release = asyncio.Event()

    async def resolve(*args, **kwargs):
        resolution['calls'] += 1
        if resolution['mode'] == 'held':
            await release.wait()
        if resolution['mode'] == 'failed':
            raise ValueError('Synthetic catalog offline')
        return {**copy.deepcopy(catalog), 'catalogs': {'offline-one': copy.deepcopy(models)}}
    draft_defaults.resolve_defaults = resolve
    original = BundleManager.perform
    async def perform(self, action, args, **kwargs):
        if action == 'bundles.list':
            return {'bundles': [], 'registeredBundles': [
                {'value': name, 'name': name, 'label': name.title()} for name in ('anchors', 'work')]}
        return await original(self, action, args, **kwargs)
    BundleManager.perform = perform
    runtime = AbsentRuntime()
    app = await create_app(home, workspace=workspace, runtime=runtime, voice=False,
        background_updates=False, preload_providers=False)
    app['control_token'] = 'dormant-fixture-token'
    service = app['service']
    await service.dispatch('session.create', {'title': 'Dormant synthetic chat', 'bundle': 'anchors'})
    session = service._session()
    sid = session['id']
    session.update(status='idle', historyLoaded=True, messages=[
        {'id': 'saved-user', 'role': 'user', 'text': 'Old saved question', 'createdAt': time.time()-86400*4},
        {'id': 'saved-answer', 'role': 'assistant', 'text': 'Old saved answer', 'createdAt': time.time()-86400*4}])
    service.state.setdefault('runtimeControl', {})[sid] = {'configuration.catalog': catalog}
    service.state['modelCatalogs'] = {'offline-one': models}
    service.state['registeredBundles'] = (await perform(None, 'bundles.list', {}))['registeredBundles']
    service.state['view'].update(setupJourney='done', navPinned=True)
    await service.dispatch('canvas.show', {'kind': 'markdown', 'title': 'Saved fixture artifact',
        'content': '# Saved artifact\nRetain this content.'})
    artifact_id = service.state['canvas']['id']
    await service.dispatch('canvas.close', {})
    service._publish_full(reason='Synthetic dormant controls')

    async def inspect(request):
        current = service._session(sid)
        return web.json_response({'id': sid, 'artifactId': artifact_id, 'runtimeCalls': runtime.calls,
            'resolverCalls': resolution['calls'], 'session': {key: copy.deepcopy(current.get(key)) for key in
            ('title', 'bundle', 'status', 'messages', 'pendingBundleSelection', 'pendingModelSelection', 'nextMessageRevision')}})

    async def state(request):
        values = await request.json()
        if values.get('status'):
            session['status'] = values['status']
            session['preparation'] = {'status': 'preparing' if values['status'] == 'starting' else 'cold'}
        if values.get('resolver'):
            resolution['mode'] = values['resolver']
            if values['resolver'] != 'held':
                release.set()
        service._publish_changes(sessions={sid})
        return web.json_response({'revision': service.state['revision']})

    app.router.add_get('/fixture/inspect', inspect)
    app.router.add_post('/fixture/state', state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
    print(json.dumps({'url': f'http://127.0.0.1:{port}', 'id': sid, 'artifactId': artifact_id}), flush=True)
    done = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, done.set)
    try:
        await done.wait()
    finally:
        release.set()
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='dormant-controls-') as directory:
        asyncio.run(main(Path(directory)))
