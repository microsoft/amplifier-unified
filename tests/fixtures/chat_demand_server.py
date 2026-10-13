"""Real HTTP/SSE, disposable large library and synthetic provider metadata only."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

ROOT = Path(os.environ.get('AMPLIFIER_PERF_APP_ROOT', Path(__file__).resolve().parents[2]))
sys.path[:0] = [str(ROOT), str(ROOT / 'tests/fixtures')]
from aiohttp import web
from configured_browser import seed_connection
from library_performance_server import make_catalog, NoModelRuntime
from amplifier_web import server, draft_defaults
from amplifier_web.setup import SetupManager


async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home/'native'), AMPLIFIER_WEB_HOME=str(home/'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(home/'sessions'), AMPLIFIER_UNIFIED_IMPORT_HOME=str(home/'legacy'))
    seed_connection(home/'native')
    metadata = {'module': 'provider-fixture', 'info': {'display_name': 'Fixture provider', 'defaults': {'model': 'first', 'reasoning_effort': 'high'}},
                'configSchema': {'fields': [{'id': 'reasoning_effort', 'choices': ['low', 'high'], 'default': 'high'}]}}
    models = [{'id': 'first'}, {'id': 'second'}]
    providers = [{'id': 'one', 'module': 'provider-fixture', 'config': {'model': 'first'}, 'enabled': True},
                 {'id': 'two', 'module': 'provider-fixture', 'config': {'model': 'other-account'}, 'enabled': True}]
    SetupManager.provider_rows = lambda self, workspace: copy.deepcopy(providers)
    SetupManager.catalog_key = lambda self, args, workspace: 'current-' + args['id']
    async def probe(self, action, args, workspace):
        return {'models': copy.deepcopy(models if args['id'] == 'one' else [{'id': 'other-account'}]),
                'providerMetadata': copy.deepcopy(metadata), 'modelsSupported': True}
    SetupManager.probe = probe
    async def resolve(*args, **kwargs):
        return {'providers': [{'id': p['id'], 'sharedCatalogKey': 'current-'+p['id'],
                               'info': {**metadata['info'], 'defaults': {'model': p['config']['model']}}} for p in providers],
                'effective': {'instance': 'one', 'model': 'first', 'effort': 'high'},
                'catalogs': {'current-'+p['id']: {'phase': 'ready', 'models': copy.deepcopy(models if p['id'] == 'one' else [{'id': 'other-account'}]), 'metadata': copy.deepcopy(metadata)} for p in providers}}
    draft_defaults.resolve_defaults = resolve
    catalog = make_catalog(home/'workspaces', 24000, 4800, 5000)
    runtime = NoModelRuntime()
    app = await server.create_app(home/'app', workspace=catalog['workspaces'][0]['path'], runtime=runtime,
                                  voice=False, background_updates=False, preload_providers=False)
    app['control_token'] = 'fixture-demand-token'
    service = app['service']
    await service.history.close()
    await service.event_log_view.close()
    service.history.index.scan_if_changed = lambda **kwargs: (object(), catalog)
    await service.history.refresh()
    ids = []
    for i in range(2):
        row = service._session(catalog['sessions'][i]['id'])
        ids.append(row['id'])
        row.update(title=['Demand source', 'Demand target'][i], historyManaged=False, historyLoaded=True,
                   historyLoading=False, status='idle', messages=[{'id': f'{i}-m-{j}', 'role': 'user' if j % 2 == 0 else 'assistant',
                       'text': f'Saved message {j}. ' + 'Ordinary **formatted** content. ' * 30, 'createdAt': 1700000000+j} for j in range(120)],
                   execution={'nodes': [{'id': 'node', 'kind': 'tool', 'toolCallId': 'old-call', 'turnId': 'turn', 'phase': 'completed',
                                         'anchorMessageId': f'{i}-m-118', 'output': 'Saved worker result'}],
                              'turns': [{'id': 'turn', 'phase': 'completed'}]},
                   workers=[{'id': f'old-{j}', 'sessionId': f'child-{j}', 'callId': 'old-call' if j == 0 else f'call-{j}',
                             'kind': 'session', 'name': f'Historical worker {j}', 'status': 'completed', 'report': 'Saved report. '*1000} for j in range(1000)],
                   runtimeReport={'provider_choices': [{'id':p['id'], 'sharedCatalogKey':'current-'+p['id'],
                       'display_name':'Fixture provider', 'model':p['config']['model'], 'effort':'high'} for p in providers],
                                  'effective_selection': {'instance': 'one', 'model': 'first', 'effort': 'high'}})
        row.pop('nativeProject', None)
        row.pop('nativeIdentity', None)
        for j in (0, 119):
            row['messages'][j]['observation'] = {'source': 'local-job-recovery', 'recovery': {'call_id': 'old-call', 'status': 'returned'}}
    # A resolvable historical child, far outside the selected worker window.
    child = service._session(catalog['sessions'][5000]['id'])
    child['runtimeSessionId'] = 'child-0'
    service._state.update(selectedSessionId=ids[0], selectedWorkspaceId=service._session(ids[0])['workspaceId'], pinnedSessionIds=ids, configurationRevision=1)
    result = await resolve()
    for sid in ids:
        service._state.setdefault('runtimeControl', {})[sid] = {'configuration.catalog': {k:v for k,v in result.items() if k != 'catalogs'} | {'phase':'ready', 'configurationRevision':1}}
    service._state['modelCatalogs'] = {f'historical-{i}': {'phase':'ready', 'models': [{'id':f'model-{j}', 'description':'retired model metadata '*30} for j in range(20)], 'metadata':metadata} for i in range(120)} | result['catalogs']
    for key, value in result['catalogs'].items():
        service.management.provider_catalog.put(('providers.models',key), {'models':value['models'],'providerMetadata':value['metadata'],'modelsSupported':True})
    service.state['view'].update(navPinned=True,navChatScope='all',setupJourney='done')
    service._publish_full(reason='Synthetic demand fixture')
    receipts_before = hashlib.sha256(json.dumps(service._session(ids[1])['workers'],sort_keys=True).encode()).hexdigest()
    counts = {'publications':0, 'actions':[]}
    original = service._publish
    def publish(*args, **kwargs):
        counts['publications'] += 1
        return original(*args, **kwargs)
    service._publish = publish
    @web.middleware
    async def observe(request, handler):
        if request.path == '/api/actions' and request.method == 'POST':
            data = await request.json()
            counts['actions'].append({'action':data.get('action'),'operation':data.get('args',{}).get('operation')})
        return await handler(request)
    app.middlewares.append(observe)
    cpu_start = time.process_time_ns()
    async def metrics(request):
        return web.json_response({**counts,'cpuNs':time.process_time_ns()-cpu_start,'ids':ids,
            'catalogEntries':len(service._state['modelCatalogs']),
            'pending':len([t for t in service.tasks if not t.done()]),
            'catalogPending':len(getattr(service.management,'conversation_catalog_requests',{})),
            'runtimeCalls':runtime.calls,
            'workersUnchanged':receipts_before == hashlib.sha256(json.dumps(service._session(ids[1])['workers'],sort_keys=True).encode()).hexdigest()})
    async def reset(request):
        nonlocal cpu_start
        counts.update(publications=0,actions=[])
        cpu_start=time.process_time_ns()
        return web.json_response({'ok':True})
    async def invalidate(request):
        await service.management.invalidate_configuration(warm=False)
        return web.json_response({'ok':True})
    async def activity(request):
        data=await request.json()
        row=service._session(ids[1])
        row['workers']=[w for w in row['workers'] if not w['id'].startswith('control-')]
        if data.get('terminal'):
            rows=[{'id':'control-child','kind':'session','callId':'controlled','sessionId':'controlled-child','status':'working'},
                  {'id':'control-job','kind':'job','callId':'controlled','status':data['terminal'],
                   'event':{'completed':'job.returned','error':'job.failed','cancelled':'job.cancelled'}[data['terminal']]}]
            if data.get('reverse'):rows.reverse()
            row['workers'].extend(rows+[{'id':'control-active','kind':'session','callId':'unrelated','status':'working','name':'Active worker'}])
        service._publish_changes(sessions={ids[1]})
        return web.json_response({'ok':True,'revision':service.state['revision']})
    app.router.add_get('/fixture/demand',metrics)
    app.router.add_post('/fixture/reset',reset)
    app.router.add_post('/fixture/invalidate',invalidate)
    app.router.add_post('/fixture/activity',activity)
    runner=web.AppRunner(app)
    await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0)
    await site.start()
    url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] |= {url}
    print(json.dumps({'url':url,'ids':ids}),flush=True)
    stopped=asyncio.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):asyncio.get_running_loop().add_signal_handler(sig,stopped.set)
    try:await stopped.wait()
    finally:await runner.cleanup()


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='chat-demand-') as folder:
        asyncio.run(main(Path(folder)))
