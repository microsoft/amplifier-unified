#!/usr/bin/env python3
"""Private copied-scale fixture with only frozen synthetic execution owners."""
import asyncio
import contextlib
import errno
import json
import os
import re
from pathlib import Path
import signal
import socket
import time

from scale_replica_app import disable_automation
from scale_runtime_fence import Binding, Fence, ReplicaRefusal, guarded_runtime


def isolation():
    proof = json.loads(Path('/replica-owner.json').read_text())
    if proof['purpose'] != 'private-scale-runtime-20261015':
        raise RuntimeError('Wrong owned root')
    interfaces = {line.split(':', 1)[0].strip() for line in Path('/proc/net/dev').read_text().splitlines()[2:]}
    if interfaces != {'lo'} or os.environ.get('AMPLIFIER_WEB_HOME') != proof['appHome']:
        raise RuntimeError('Private namespace or state binding failed')
    mounted = {re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), parts[4]): set(parts[5].split(',')) for line in Path('/proc/self/mountinfo').read_text().splitlines()
               if len(parts := line.split()) > 6}
    native = [row for row in proof['mounts'] if row['readOnly'] and row['target'].startswith(proof['nativeHome'] + '/projects/')]
    if not native or any('ro' not in mounted.get(row['target'], set()) for row in native):
        raise RuntimeError('Copied native directories are not OS read-only')
    with socket.socket() as connection:
        connection.settimeout(.5)
        try:
            connection.connect(('1.1.1.1', 443))
        except OSError:
            pass
        else:
            raise RuntimeError('External connectivity was not denied')
    # Open without truncation or writes: refusal proves the actual file mount,
    # while a broken mount aborts without changing any copied bytes.
    checked = set()
    for row in native:
        sessions = Path(row['target']) / 'sessions'
        if not sessions.is_dir(): continue
        for session in sessions.iterdir():
            for name in ('metadata.json', 'transcript.jsonl', 'events.jsonl'):
                source = session / name
                if name in checked or not source.is_file(): continue
                try:
                    fd = os.open(source, os.O_WRONLY)
                except OSError as error:
                    if error.errno not in {errno.EROFS, errno.EACCES}: raise
                else:
                    os.close(fd)
                    raise RuntimeError('Copied canonical file accepted writable open')
                checked.add(name)
            if len(checked) == 3: break
        if len(checked) == 3: break
    if len(checked) != 3: raise RuntimeError('Canonical files unavailable for all read-only controls')
    return proof, {'privateNetwork': True, 'externalDenied': True,
                   'readOnlyNativeMounts': len(native), 'copiedWriteOpenDenied': sorted(checked),
                   'derivedPresentationOverlays': len(proof.get('derivedOverlays', []))}


async def main():
    proof, controls = isolation()
    disable_automation()
    import yaml
    from aiohttp import web
    from amplifier_web.builtin_behaviors import IMAGEGEN_BEHAVIOR_URI
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.server import create_app
    from amplifier_web.session_store import SessionStore
    workspace = proof['syntheticWorkspace']
    worker_python = '/opt/replica-worker/bin/python'
    query = ('import json,amplifier_module_context_simple as c,amplifier_module_hook_context_intelligence as h;'
             'from pathlib import Path;print(json.dumps({"context":str(Path(c.__file__).parent),"capture":str(Path(h.__file__).parent)}))')
    child = await asyncio.create_subprocess_exec(worker_python, '-c', query, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE)
    output, error = await child.communicate()
    if child.returncode: raise RuntimeError('Qualified worker module inspection failed: ' + error.decode()[:1000])
    installed = json.loads(output)
    provider_path = '/opt/unified-dev/source/tests/fixtures/amplifier_module_provider_concurrency'
    provider = {'module': 'provider-concurrency', 'id': 'concurrency', 'source': provider_path,
                'config': {'default_model': 'synthetic-stream', 'interval': .25, 'chunks': 480}}
    bundle = Path('/opt/synthetic-config/bundle.yaml')
    bundle.write_text(yaml.safe_dump({'bundle': {'name': 'scale-concurrency-fixture', 'version': '1.0.0'},
        'session': {'orchestrator': {'module': 'loop-live'},
                    'context': {'module': 'context-simple', 'source': installed['context']}},
        'providers': [provider], 'hooks': [{'module': 'hook-context-intelligence', 'source': installed['capture'],
                         'config': {'destinations': {}, 'base_path': '/tmp/synthetic-capture'}}]}))
    (Path(proof['nativeHome']) / 'settings.yaml').write_text(yaml.safe_dump({
        'bundle': {'active': str(bundle), 'app': [], 'added': {'scale-concurrency-fixture': str(bundle)}},
        'web_bundles': {'excluded': [IMAGEGEN_BEHAVIOR_URI]}, 'config': {'providers': [provider]},
        'sources': {'modules': {'context-simple': installed['context'], 'provider-concurrency': provider_path,
                               'hook-context-intelligence': installed['capture']}}}))
    fence = Fence()
    runtime = guarded_runtime(RuntimeManager, fence)(command=[worker_python, '-u',
        '/opt/unified-dev/source/amplifier_web/runtime_worker.py'], startup_timeout=90, preparation_timeout=90)
    app = await create_app(proof['appHome'], workspace=workspace, runtime=runtime, voice=False,
                           background_updates=False, preload_providers=False,
                           server_config={'bind': ['127.0.0.1'], 'port': 8943})
    service = app['service']
    original_dispatch, original_bridge = service.dispatch, service.app_bridge
    # One setup-only catalog identity inventory, no transcript/body hydration.
    # Never include this outside-measurement inventory in navigation timings.
    imported_ids = {row['id'] for row in service.directory.select(limit=-1)}
    owners_path = Path('/run/replica/synthetic-owners.json')
    if owners_path.exists():
        owned = json.loads(owners_path.read_text())
        if owned.get('source') != proof['sourceRevision'] or len(set(owned.get('ids', []))) != 14:
            raise RuntimeError('Synthetic fixture receipt mismatch')
        ids = owned['ids']
        bindings = [Binding(sid, workspace, str(bundle), proof['syntheticNativeProject']) for sid in ids]
        if any(not binding.matches(service._session(binding.identity, hydrate=False)) for binding in bindings):
            raise RuntimeError('Persisted synthetic owner was rebound')
    else:
        ids, bindings = [], []
        for index in range(14):
            result = await original_dispatch('session.create', {'title': f'Scale synthetic {index:02}',
                'workspace': workspace, 'bundle': str(bundle),
                'selection': {'instance': 'concurrency', 'model': 'synthetic-stream'}})
            sid = result.get('sessionId') or service.state['selectedSessionId']
            if sid in imported_ids or sid in ids:
                raise RuntimeError('Synthetic public identity overlaps captured owner')
            row = service._session(sid)
            row.update(nativeProject=proof['syntheticNativeProject'], nativeIdentity=sid,
                       runtimeSessionId=sid, historyManaged=False, historyLoaded=True)
            text = f'Saved synthetic concurrency conversation {index:02}.'
            SessionStore.for_app(service.data_dir, workspace).save(sid, [{'role': 'assistant', 'content': text}], {})
            service._message(row, 'assistant', text)
            ids.append(sid)
            bindings.append(Binding(sid, workspace, str(bundle), proof['syntheticNativeProject']))
        service._publish()
        owners_path.write_text(json.dumps({'source': proof['sourceRevision'], 'ids': ids}) + '\n')
        owners_path.chmod(0o600)
    fence.freeze(bindings, lambda sid: service._session(sid, hydrate=False))

    async def dispatch(action, args=None, **kwargs):
        fence.action(action, args)
        return await original_dispatch(action, args, **kwargs)
    async def bridge(operation, args, session_id):
        fence.bridge(operation, args, session_id)
        return await original_bridge(operation, args, session_id)
    service.dispatch, service.app_bridge, runtime.app_bridge = dispatch, bridge, bridge
    # Import no captured update activation, automatic operation, or credential.
    updates = service.state.get('updates', {})
    for key in ('pendingApp', 'pendingRestart', 'pendingReplacement'):
        updates.pop(key, None)
    updates.update(auto=False, phase='idle', error=None)
    service.state['view'].update(setupJourney='done', mode='chat', draft='', scheme='system', layout='balanced', navPinned=True, panel=None)
    service._publish()
    # Real dispatch/runtime negatives must fail before any worker/process.
    copied = next(row['id'] for row in service.directory.select(limit=100) if row['id'] not in ids)
    async def no_emit(*args): raise RuntimeError('Refused admission emitted runtime work')
    attempts = [lambda: dispatch('conversation.send', {'sessionId': copied, 'text': 'REFUSED'}),
        lambda: dispatch('shell.command', {'action': 'conversation.send', 'args': {'sessionId': copied, 'text': 'REFUSED'}}),
        lambda: bridge('dispatch', {'action': 'conversation.send', 'args': {'sessionId': copied, 'text': 'REFUSED'}}, ids[0]),
        lambda: runtime.start({'id': copied}, no_emit),
        lambda: runtime.send({'id': copied}, 'REFUSED', 'refused', no_emit),
        lambda: runtime.control(copied, 'ping'),
        lambda: runtime.spawn_worker(service._session(ids[0]), 'REFUSED', 'refused', no_emit)]
    for attempt in attempts:
        try: await attempt()
        except ReplicaRefusal: pass
        else: raise RuntimeError('Negative execution control was accepted')
    if runtime.workers or runtime._preparations:
        raise RuntimeError('Negative controls started owned runtime work')
    controls.update(realNegativeControls=len(attempts), syntheticOwners=len(ids), workersAfterNegatives=0)
    allowed_posts = {'/api/actions', '/api/view', '/api/shell/resources', '/api/conversation/work-sync',
                     '/api/clients/attach', '/login'}
    @web.middleware
    async def routes(request, handler):
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and request.path not in allowed_posts:
            raise web.HTTPForbidden(text='Effect is not admitted by the private scale fixture')
        return await handler(request)
    app.middlewares.insert(0, routes)
    lag, stop = [], asyncio.Event()
    async def ticker():
        previous = time.monotonic()
        while True:
            await asyncio.sleep(.05)
            now = time.monotonic(); lag.append(max(0, now - previous - .05) * 1000); previous = now
            if len(lag) > 12000: del lag[:1000]
    clock = asyncio.create_task(ticker())
    async def inspect(request):
        if request.headers.get('Authorization') != 'Bearer ' + app['control_token']:
            raise web.HTTPForbidden()
        if request.query.get('reset') == '1': lag.clear()
        rows = [service._session(sid, hydrate=False) for sid in ids]
        values = sorted(lag)
        return web.json_response({'ids': ids, 'controls': controls, 'hostPid': os.getpid(),
            'source': proof['sourceRevision'], 'qualificationBypassed': True, 'deterministicProvider': True,
            'workers': [{'id': sid, 'pid': row['process'].pid, 'returncode': row['process'].returncode}
                        for sid, row in runtime.workers.items()],
            'sessions': [{'id': row['id'], 'status': row['status'], 'error': row.get('error'), 'generation': row.get('collaborationGeneration'), 'streamingBytes': len(row.get('streaming', '').encode())}
                         for row in rows], 'hostCpuSeconds': time.process_time(),
            'eventLoopLagMs': {'count': len(values), 'p95': values[min(len(values)-1, int(len(values)*.95))] if values else None},
            'refused': fence.refused})
    app.router.add_get('/fixture/runtime', inspect)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 8943); await site.start()
    app['allowed_origins'] = app['allowed_origins'] | {'http://127.0.0.1:8943'}
    sock = Path('/run/replica/web.sock'); sock.unlink(missing_ok=True)
    unix = web.UnixSite(runner, str(sock)); await unix.start(); sock.chmod(0o600)
    Path('/run/replica/ready.json').write_text(json.dumps({'ids': ids, 'pid': os.getpid(),
        'source': proof['sourceRevision'], 'controls': controls, 'workloadStarted': False}) + '\n')
    for sig in (signal.SIGINT, signal.SIGTERM): asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    try: await stop.wait()
    finally:
        clock.cancel()
        with contextlib.suppress(asyncio.CancelledError): await clock
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
