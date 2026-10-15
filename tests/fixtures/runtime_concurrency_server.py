"""Owned synthetic app with actual RuntimeManager/Worker/loop-live processes.

Explicit worker interpreter bypasses runtime qualification/install. It does not
replace worker control, admission, model completion, streaming or stop paths.
Run only in an owned DTU; requires --worker-python and isolated --home.
"""
import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def quantiles(values):
    rows = sorted(values)
    return {'count': len(rows), 'p50': rows[len(rows)//2] if rows else None,
            'p95': rows[min(len(rows)-1, int(len(rows)*.95))] if rows else None,
            'max': max(rows) if rows else None}


async def main(args):
    home = Path(args.home).resolve()
    home.mkdir(parents=True, exist_ok=False)
    shared, app_home, workspace = home/'shared', home/'app', home/'workspace'
    shared.mkdir(); workspace.mkdir()
    os.environ.update(AMPLIFIER_HOME=str(shared), AMPLIFIER_WEB_HOME=str(app_home),
                      AMPLIFIER_SESSION_STATE_HOME=str(home/'session-state'),
                      AMPLIFIER_UNIFIED_IMPORT_HOME=str(home/'legacy'),
                      AMPLIFIER_RUNTIME_IMMUTABLE='1',
                      PYTHONPATH=str(Path(__file__).parent)+os.pathsep+str(ROOT))
    # No inherited external provider credentials are needed or usable.
    for key in list(os.environ):
        if key.endswith(('_API_KEY', '_ACCESS_TOKEN')) or key in {'OPENAI_KEY', 'ANTHROPIC_AUTH_TOKEN'}:
            os.environ.pop(key, None)
    import yaml
    from aiohttp import web
    from amplifier_web.builtin_behaviors import IMAGEGEN_BEHAVIOR_URI
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.server import create_app
    from amplifier_web.auth import new_session
    worker_python = Path(args.worker_python).absolute()  # Keep the venv launcher identity.
    # Query the worker interpreter's installed module paths without borrowing
    # mutable state or installing anything into its qualified environment.
    query = "import json,amplifier_module_context_simple as c,amplifier_module_hook_context_intelligence as h; from pathlib import Path; from importlib.metadata import version; print(json.dumps({'context':str(Path(c.__file__).parent),'capture':str(Path(h.__file__).parent),'versions':{n:version(n) for n in ['amplifier-core','amplifier-foundation','amplifier-module-loop-live','amplifier-module-context-simple']}}))"
    proc = await asyncio.create_subprocess_exec(str(worker_python), '-c', query,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    if proc.returncode: raise RuntimeError(stderr.decode())
    installed = json.loads(stdout)
    context_path, capture_path = installed['context'], installed['capture']
    provider_path = str(Path(__file__).with_name('amplifier_module_provider_concurrency'))
    provider = {'module':'provider-concurrency', 'id':'concurrency', 'source':provider_path,
                'config': {'default_model':'synthetic-stream', 'interval':.25, 'chunks':480}}
    bundle = home/'bundle.yaml'
    bundle.write_text(yaml.safe_dump({'bundle':{'name':'concurrency-fixture','version':'1.0.0'},
        'session': {'orchestrator':{'module':'loop-live'},
                    'context':{'module':'context-simple','source':context_path}},
        'providers':[provider], 'hooks':[{'module':'hook-context-intelligence','source':capture_path,
            'config':{'destinations':{},'base_path':str(home/'capture')}}]}))
    (shared/'settings.yaml').write_text(yaml.safe_dump({
        'bundle':{'active':str(bundle),'app':[], 'added':{'concurrency-fixture':str(bundle)}},
        'web_bundles':{'excluded':[IMAGEGEN_BEHAVIOR_URI]},
        'config':{'providers':[provider]}, 'sources':{'modules':{'context-simple':context_path,
                                                               'provider-concurrency':provider_path,
                                                               'hook-context-intelligence':capture_path}}}))
    runtime = RuntimeManager(command=[str(worker_python), '-u', str(ROOT/'amplifier_web/runtime_worker.py')],
        startup_timeout=90, preparation_timeout=90)
    app = await create_app(app_home, workspace=str(workspace), runtime=runtime,
                          voice=False, background_updates=False, preload_providers=False)
    app['control_token'] = 'fixture-real-worker-benchmark'
    service = app['service']
    runtime.app_bridge = service.app_bridge
    ids = []
    for index in range(14):
        created = await service.dispatch('session.create', {'title':f'Concurrency {index:02}',
            'workspace':str(workspace),'bundle':str(bundle),
            'selection':{'instance':'concurrency','model':'synthetic-stream'}})
        sid = created.get('sessionId') or service.state['selectedSessionId']
        ids.append(sid)
        service._message(service._session(sid), 'assistant', f'Saved fixture conversation {index:02}.')
        await service.dispatch('session.pin', {'id':sid,'pinned':True})
    await service.dispatch('session.select', {'id':ids[0]})
    await service.dispatch('view.update', {'patch':{'setupJourney':'done','navPinned':True}})
    loop_lag = []
    stop = asyncio.Event()
    async def sample_loop():
        previous = time.monotonic()
        while True:
            await asyncio.sleep(.05)
            now = time.monotonic(); loop_lag.append(max(0,(now-previous-.05)*1000)); previous = now
            if len(loop_lag)>12000: del loop_lag[:1000]
    ticker = asyncio.create_task(sample_loop())
    async def inspect(request):
        if request.query.get('reset') == '1': loop_lag.clear()
        sessions = service._state['sessions']
        worker_rows = [{'id':sid,'pid':row['process'].pid,'returncode':row['process'].returncode,
                        'parked':bool(row.get('parked'))} for sid,row in runtime.workers.items()]
        rss = {}
        for pid in [os.getpid(), *(row['pid'] for row in worker_rows)]:
            try:
                data = Path(f'/proc/{pid}/status').read_text()
                rss[str(pid)] = next(int(line.split()[1]) for line in data.splitlines() if line.startswith('VmRSS:'))
            except (OSError, StopIteration): pass
        return web.json_response({'at':time.time(),'hostPid':os.getpid(),'workers':worker_rows,
            'sessions':[{'id':row['id'],'title':row.get('title'),'status':row.get('status'),
                         'error':row.get('error'),'streamingBytes':len(row.get('streaming','').encode()),
                         'messageCount':len(row.get('messages',[]))} for row in sessions],
            'residentSessions':len(sessions),'residentMessages':sum(len(row.get('messages',[])) for row in sessions),
            'residentJsonBytes':len(json.dumps(sessions,default=str).encode()),'rssKiB':rss,
            'eventLoopLagMs':quantiles(loop_lag)})
    app.router.add_get('/fixture/runtime',inspect)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner,'127.0.0.1',0); await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url':url,'ids':ids,'home':str(home),'cookie':new_session(app['session_secret']),
        'runtimePython':str(worker_python),'runtimeVersions':installed['versions'],'qualificationBypassed':True,'deterministicProvider':True}),flush=True)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(sig,stop.set)
    try: await stop.wait()
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError): await ticker
        await runner.cleanup()
        print(json.dumps({'closed':True,'remainingWorkers':len(runtime.workers)}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home',required=True)
    parser.add_argument('--worker-python',required=True)
    asyncio.run(main(parser.parse_args()))
