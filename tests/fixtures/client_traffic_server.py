"""Real browser transport with synthetic task history; never starts a model."""
import asyncio
import copy
import hashlib
import importlib.metadata
import sys
import json
import os
from pathlib import Path
import signal
import tempfile

from empty_host_ui_server import Runtime


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def change(index):
    proof = ''.join(hashlib.sha256(f'traffic-proof-{index}-{part}'.encode()).hexdigest()
                    for part in range(128))
    return {'revision': index + 2, 'origin': 'agent', 'at': 1700000000 + index,
            'fields': {'goalState': {'native_state': {'verification': proof}}}}


async def main(home):
    for name in ('user', 'cache', 'workspace', 'shared', 'app'):
        (home / name).mkdir()
    os.environ.update(HOME=str(home / 'user'), XDG_CACHE_HOME=str(home / 'cache'),
                      AMPLIFIER_HOME=str(home / 'shared'), AMPLIFIER_WEB_HOME=str(home / 'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(home / 'ownership'),
                      AMPLIFIER_UNIFIED_IMPORT_HOME=str(home / 'imports'),
                      AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(home / 'capture'))
    for key in list(os.environ):
        if key.endswith(('_API_KEY', '_ACCESS_TOKEN')) or key in {'OPENAI_KEY', 'ANTHROPIC_AUTH_TOKEN'}:
            os.environ.pop(key, None)
    from aiohttp import web
    from configured_browser import seed_connection
    from amplifier_web.server import create_app
    from amplifier_web.setup import SetupManager
    import amplifier_web.draft_defaults as defaults

    seed_connection(home / 'shared')
    SetupManager.provider_rows = lambda self, workspace: [
        {'id': 'fixture', 'module': 'provider-fixture', 'config': {'model': 'first'}, 'enabled': True}]

    async def catalog(self, action, args, workspace):
        return {'modelsProviderId': 'fixture', 'models': [{'id': 'first'}],
                'providerMetadata': {'module': 'provider-fixture', 'info': {'display_name': 'Fixture'},
                                     'configSchema': {'fields': []}}}

    async def resolve(home, workspace, bundle=None, app_bundle=None, **kwargs):
        return {'bundle': bundle or 'work', 'effective': {'instance': 'fixture', 'model': 'first'},
                'providers': [{'id': 'fixture', 'info': {'display_name': 'Fixture', 'defaults': {'model': 'first'}},
                               'configSchema': {'fields': []}}]}

    SetupManager.cached_probe = catalog
    defaults.resolve_defaults = resolve
    import amplifier_web
    package = Path(amplifier_web.__file__).resolve().parent
    expected = os.environ.get('AMPLIFIER_TEST_PACKAGE_ROOT')
    if expected:
        assert package == Path(expected).resolve(), 'Unexpected loaded application package'
    if os.environ.get('AMPLIFIER_TEST_INSTALLED') == '1':
        assert package.is_relative_to(Path(sys.prefix).resolve()), 'Installed mode imported source'
    else:
        assert package == Path(__file__).resolve().parents[2] / 'amplifier_web'
    loaded = {'package': str(package), 'python': sys.version,
              'version': importlib.metadata.version('amplifier-unified'),
              'build': json.loads((package / 'static/build.json').read_text()),
              'hashes': {name: hashlib.sha256((package / name).read_bytes()).hexdigest()
                         for name in ('browser_demand.py', 'browser_state.py', 'state_transport.py',
                                      'server.py', 'static/index.html', 'static/build.json')}}
    runtime = Runtime()
    app = await create_app(home / 'app', workspace=str(home / 'workspace'), runtime=runtime,
                           voice=False, background_updates=False)
    app['control_token'] = 'fixture-client-traffic-token'
    service = app['service']
    runtime.service = service
    await service.dispatch('view.update', {'patch': {'setupJourney': 'done'}})
    result = await service.dispatch('session.create', {'title': 'Traffic fixture'})
    sid = result['result']['sessionId']
    await service.on_runtime_event('assistant.message', {'sessionId': sid, 'text': 'Retained synthetic response.'})
    await service.dispatch('session.select', {'id': sid})
    owner = service._session(sid)
    task = {'id': 'traffic-task', 'revision': 129, 'appliedRevision': 129, 'status': 'active',
            'createdAt': 1700000000, 'updatedAt': 1700000128, 'continuationEpoch': 1,
            'objective': 'Observe traffic without sending work.',
            'originalObjective': 'Observe traffic without sending work.',
            'constraints': ['Preserve historical evidence.'], 'corrections': [],
            'changesRecordedFromRevision': 1, 'changes': [change(0)], 'questionIds': [],
            'operationIds': [], 'artifactRefs': [], 'completionEvidence': [], 'maxTurns': 1}
    owner['task'] = task
    control = {'task': copy.deepcopy(task), 'history': [], 'goal': None,
               'continuity': {'supported': False}, 'continuationSupported': False}
    service.state.setdefault('runtimeControl', {}).setdefault(sid, {})['task.get'] = control

    def persist():
        control['task'] = copy.deepcopy(task)
        identity = owner.get('runtimeSessionId') or owner.get('nativeIdentity') or sid
        path = service.data_dir / 'sessions' / identity / 'control-state.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded({'task': task, 'taskHistory': [], 'taskReceipts': {}, 'goal': None}))

    def facts():
        current = service._session(sid)['task']
        current_control = service.state['runtimeControl'][sid]['task.get']['task']
        return {'sessionId': sid, 'revision': service._state['revision'], 'taskRevision': task['revision'],
                'historyCount': len(current['changes']), 'historyBytes': len(encoded(current['changes'])),
                'taskSha256': hashlib.sha256(encoded(current)).hexdigest(),
                'controlTaskSha256': hashlib.sha256(encoded(current_control)).hexdigest(),
                'sent': len(runtime.sent), 'started': len(runtime.started),
                'firstDetailSha256': hashlib.sha256(json.dumps(change(0)['fields'], ensure_ascii=False,
                                                              separators=(',', ':')).encode()).hexdigest()}

    persist()
    service._publish_changes(sessions={sid})

    async def inspect(request):
        return web.json_response(facts())

    async def mutate(request):
        operation = (await request.json())['operation']
        if operation in {'pulse', 'settle'}:
            # Separate deliveries so the test catches per-publication redraws.
            for _ in range(8 if operation == 'pulse' else 1):
                async with service.lock:
                    service._publish_changes(sessions=())
                await asyncio.sleep(.08)
        else:
            async with service.lock:
                if operation == 'grow-history':
                    # Same current fields/revision: isolate historical contribution.
                    task['changes'] = [change(index) for index in range(128)]
                elif operation == 'append':
                    task['changes'].append(change(len(task['changes'])))
                    task['revision'] += 1
                    task['appliedRevision'] = task['revision']
                elif operation == 'current':
                    task['revision'] += 1
                    task['appliedRevision'] = task['revision']
                    task.update(objective='Current objective ' + 'o' * 3000,
                                corrections=[{'revision': task['revision'], 'origin': 'human',
                                              'text': 'Current correction ' + 'c' * 5000}],
                                completionEvidence=['Saved evidence'], questionIds=['question-current'],
                                operationIds=['operation-current'], artifactRefs=['artifact-current'])
                    control.update(continuationSupported=True, continuity={'supported': True, 'status': 'ready'})
                else:
                    raise web.HTTPBadRequest(text='Unknown fixture operation')
                persist()
                service._publish_changes(sessions={sid})
        return web.json_response(facts())

    app.router.add_get('/fixture/traffic', inspect)
    app.router.add_post('/fixture/traffic', mutate)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    print(json.dumps({'url': url, 'loaded': loaded, **facts()}), flush=True)
    try:
        await stop.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='unified-client-traffic-') as directory:
        asyncio.run(main(Path(directory)))
