"""Disposable synthetic import and actual private-backup browser observations."""
import asyncio
import hashlib
import json
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.session_store import SessionStore


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def start(self, session, emit, **kwargs):
        self.calls.append({'operation': 'start', 'id': session['id']})
        return await super().start(session, emit, **kwargs)

    async def send(self, session, *args, **kwargs):
        self.calls.append({'operation': 'send', 'id': session['id']})
        raise AssertionError('Import or backup must not execute a conversation')

    async def control(self, sid, operation, args):
        self.calls.append({'operation': operation, 'id': sid})
        return await super().control(sid, operation, args)


async def main(home):
    fixture.Runtime = Runtime
    app = await fixture.main(home)
    service = app['service']
    original = service._session()
    original_id = original['id']
    original['historyManaged'] = False
    original['status'] = 'stopped'
    rows = [{'role': 'user', 'content': 'Keep original question'},
            {'role': 'assistant', 'content': 'Keep original answer'}]
    store = SessionStore.for_app(service.data_dir, original['workspace'])
    store.save(original_id, rows, {'name': original['title']})
    for row in rows:
        service._message(original, row['role'], row['content'])
    service.state['view']['panel'] = None
    service._publish()
    original_hash = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()

    async def observations(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        async with service.lock:
            sessions = list(service.state['sessions'])
            imported = []
            for row in sessions:
                if row['id'] == original_id:
                    continue
                saved = SessionStore.for_app(service.data_dir, row['workspace']).load(row['id'])
                if saved and saved[1].get('imported_file'):
                    imported.append({'id': row['id'], 'status': row['status'],
                                     'messages': saved[0], 'jobsReplayed': saved[1].get('jobs_replayed')})
            saved_original = store.load(original_id)[0]
            maintenance = dict(service.state.get('maintenance', {}))
            runtime_calls = list(service.runtime.calls)
        with sqlite3.connect(service.data_dir / 'schedules.sqlite3') as db:
            schedules = db.execute('SELECT count(*) FROM schedules').fetchone()[0]
        backup = None
        if maintenance.get('backup'):
            path = Path(maintenance['backup'])
            assert path.is_relative_to(home / 'backups')
            with tarfile.open(path, 'r:gz') as archive:
                names = archive.getnames()
            backup = {'path': str(path), 'fileMode': path.stat().st_mode & 0o777,
                      'directoryMode': path.parent.stat().st_mode & 0o777,
                      'bytes': path.stat().st_size, 'hasDatabase': 'app.sqlite3' in names,
                      'hasImportedTranscript': any(item['id'] in name and name.endswith('transcript.jsonl')
                                                   for item in imported for name in names)}
        return web.json_response({'originalId': original_id,
            'originalPreserved': original_hash == hashlib.sha256(json.dumps(saved_original, sort_keys=True).encode()).hexdigest(),
            'imported': imported, 'runtimeCalls': runtime_calls, 'schedules': schedules,
            'backup': backup, 'sessionIds': sorted(row['id'] for row in sessions)})

    app.router.add_get('/api/fixture/privacy-observations', observations)
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
    with tempfile.TemporaryDirectory(prefix='unified-privacy-effects-') as folder:
        asyncio.run(main(Path(folder)))
