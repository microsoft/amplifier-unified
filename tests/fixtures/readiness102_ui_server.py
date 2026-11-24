"""Synthetic, disposable workspace/storage/attention acceptance fixture."""
import asyncio
import json
import os
from pathlib import Path
import tempfile

from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import normalize_event


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    workspace = home / 'workspaces' / 'cleanup-demo'
    workspace.mkdir(parents=True)
    (workspace / 'notes.txt').write_text('Synthetic cleanup fixture\n')
    (workspace / 'nested').mkdir()
    (workspace / 'nested' / 'data.txt').write_text('Drill in and back\n')
    await service.dispatch('workspace.add', {'path': str(workspace), 'name': 'Cleanup demo'})
    wid = next(row['id'] for row in service.state['workspaces'] if row.get('path') == str(workspace))
    for index in range(7):
        extra=home/'workspaces'/f'storage-example-{index}'
        extra.mkdir(parents=True)
        (extra/'data').write_bytes(b'x'*(1024*(index+1)))
        await service.dispatch('workspace.add', {'path':str(extra),'name':f'Storage example {index}'})
    for index,identity in enumerate(['a'*32,'b'*32]):
        backup=home/'backups'/identity
        backup.mkdir(parents=True)
        archive=backup/'private-state.tar.gz';archive.write_bytes(b'synthetic backup')
        os.utime(archive,(1700000000+index,1700000000+index))
    await service.dispatch('session.create', {'title':'Storage managed chat','location':{'kind':'managed'}})
    managed=service._session()['id']
    await service.dispatch('session.create', {'title': 'Plan demo', 'workspace': str(workspace)})
    source = service._session()
    sid = source['id']
    source['messages'] = [{'id': 'fixture-message', 'role': 'assistant', 'text': 'This conversation will be retained.'}]
    emitted, plan_reports = [], []
    observer = ExecutionEvents(sid, emitted.append)
    for index, status in enumerate(['pending', 'completed']):
        call = f'plan-{index}'
        # Last-reported Plan comes from successful live observations. Do not seed
        # execution.plan or reconstruct it from retired historical event scans.
        observer.hook(sid, 'tool:pre', {'session_id': sid, 'tool_name': 'todo',
            'tool_call_id': call,
            'tool_input': {'action': 'update', 'todos': [{'content': 'Review the demo', 'status': status}]}})
        observer.hook(sid, 'tool:post', {'session_id': sid, 'tool_name': 'todo',
            'tool_call_id': call,
            'result': {'success': True, 'output': {'status': 'updated', 'count': 1}}})
        observations = []
        for event in emitted:
            kind, payload = normalize_event(event, sid, 'readiness-plan-fixture')
            await service.on_runtime_event(kind, payload)
            observations.append({key: payload[key] for key in (
                'id', 'revision', 'toolCallId', 'sessionId', 'rootSessionId', 'kind', 'label', 'phase')})
        emitted.clear()
        plan_reports.append({'events': observations,
                             'plan': service._session(sid, hydrate=False).get('_reportedPlan')})
    service.state['view'].update(panel=None, workSurface='chat')
    service._publish()

    async def info(request):
        return web.json_response({'sessionId': sid, 'workspaceId': wid, 'workspace': str(workspace),
                                  'planReports': plan_reports,
                                  'filesExist': workspace.exists(),'managedSessionId':managed,'oldBackupExists':(home/'backups'/('a'*32)).exists(),'newBackupExists':(home/'backups'/('b'*32)).exists()})
    app.router.add_get('/api/fixture/readiness', info)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='unified-readiness102-') as temp:
        asyncio.run(main(Path(temp)))
