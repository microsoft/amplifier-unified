"""Owned real host fixture for last-reported Plan; no providers or user data."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys

ROOT, HOME = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
os.environ.update(AMPLIFIER_HOME=str(HOME / 'native'), AMPLIFIER_WEB_HOME=str(HOME / 'app'),
                  AMPLIFIER_UNIFIED_IMPORT_HOME=str(HOME / 'legacy'),
                  AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(HOME / 'native' / 'projects'),
                  AMPLIFIER_SESSION_STATE_HOME=str(HOME / 'ownership'))
from aiohttp import web
from amplifier_web.server import create_app
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import normalize_event
from amplifier_web.event_log_view import EventLogView, EventIndex
from amplifier_web.setup import SetupManager
from amplifier_web.host.config import write_private
from test_automatic_history import ObservedRuntime, native_session
import yaml


async def probe(self, action, args, workspace):
    return {'models': [{'id': 'fixture-model'}], 'modelsProviderId': args.get('id'),
            'modelsSupported': True}


async def routing(self, workspace):
    pass


async def main():
    workspace = HOME / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    write_private(HOME / 'native' / 'settings.yaml', yaml.safe_dump({
        'config': {'providers': [{'module': 'provider-fixture', 'id': 'fixture',
                                  'config': {'default_model': 'fixture-model'}}]}}))
    SetupManager.probe, SetupManager.ensure_routing_catalog = probe, routing
    app = await create_app(HOME / 'app', workspace=workspace, runtime=ObservedRuntime(),
                           voice=False, background_updates=False, preload_providers=False)
    app['control_token'] = 'last-plan-fixture-token'
    service = app['service']
    identity_file = HOME / 'fixture-id'
    if identity_file.exists():
        sid = identity_file.read_text()
    else:
        made = await service.dispatch('session.create', {'title': 'Last reported checklist'})
        sid = made['sessionId']
        native = native_session(workspace, sid, messages=[
            {'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'Ordinary message {i}'}
            for i in range(240)])
        from amplifier_web.session_files import project_slug
        row = service._session(sid)
        row.update(nativeProject=project_slug(workspace), nativeIdentity=sid,
                   historyManaged=True, historyLoaded=False, messages=[])
        log = native / 'context-intelligence' / 'events.jsonl'
        with log.open('ab') as stream:
            # Real large/edited fixture history, never an application input.
            for _ in range(1024):
                stream.write(b'{"event":"session:end","data":{"padding":"' + b'x' * 16384 + b'"}}\n')
        service._publish_changes(sessions={sid})
        identity_file.write_text(sid)
    from amplifier_web.event_log_view import event_path
    row = service._session(sid, hydrate=False)
    log = event_path(row, sid)
    emitted = []
    observer = ExecutionEvents(sid, emitted.append)
    counts = {'historicalPlanReads': 0, 'reports': 0}
    def forbidden(*args, **kwargs):
        counts['historicalPlanReads'] += 1
        raise AssertionError('Last-reported Plan must not reconstruct historical events')
    # Leave the real recent Work/transcript windows running. Only whole-history
    # reconstruction is forbidden; no UI or persisted checklist is seeded.
    EventLogView.read_plan = forbidden
    EventLogView.read = forbidden
    EventIndex.refresh = forbidden

    async def control(request):
        body = await request.json()
        op = body['op']
        if op == 'edit-history':
            with log.open('r+b') as stream:
                stream.write(b'{"event":"edited-old-event","data":{}}\n')
        elif op in {'success', 'failure', 'malformed', 'clear', 'child'}:
            counts['reports'] += 1
            call = str(counts['reports'])
            owner = 'child' if op == 'child' else sid
            tasks = [{'content': body.get('text', 'Prepare the team update'),
                      'status': body.get('status', 'in_progress')}]
            observer.hook(owner, 'tool:pre', {'session_id': owner, 'tool_name': 'todo',
                'tool_call_id': call, 'tool_input': {'action': 'update', 'todos': tasks}})
            output = {'status': 'updated'}
            if op == 'malformed':
                output = {'todos': 'invalid'}
            elif op == 'clear':
                output = {'todos': []}
            observer.hook(owner, 'tool:post', {'session_id': owner, 'tool_name': 'todo',
                'tool_call_id': call, 'result': {'success': op != 'failure', 'output': output}})
            for event in emitted:
                await service.on_runtime_event(*normalize_event(event, sid, 'fixture-input'))
            emitted.clear()
        elif op != 'inspect':
            raise web.HTTPBadRequest()
        return web.json_response({**counts, 'revision': service.state['revision'],
            'runtimeCalls': len(service.runtime.started)
            + len(service.runtime.sent) + len(service.runtime.stopped),
            'plan': service._session(sid, hydrate=False).get('_reportedPlan'),
            'logBytes': log.stat().st_size})
    app.router.add_post('/api/fixture/plan', control)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    app['allowed_origins'] |= {f'http://127.0.0.1:{port}'}
    print(json.dumps({'port': port, 'id': sid}), flush=True)
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    await stopped.wait()
    await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())