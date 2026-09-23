"""Browser fixture: real app, Git, signatures, native history and idle writer fence.

Settings/provider UI remains synthetic. The transfer release delegates to the
actual RuntimeManager and Foundation native lock; no provider request runs.
"""
import asyncio
import json
import os
from pathlib import Path
import signal
import tempfile
import time

from aiohttp import web
import settings_ui_server as fixture

from amplifier_foundation.session import SessionTransferFencedError, SharedSessionStore
from amplifier_portability import TransferNode
from amplifier_portability.capsule import read_capsule, write_capsule
from amplifier_web.host.storage import SessionStore
from amplifier_web.runtime import RuntimeManager
from amplifier_worktrees.git import atomic, git, snapshot


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.proof = RuntimeManager()
        self.proof.retention.wake = lambda: None
        self.allow_release = asyncio.Event()
        self.release_started = asyncio.Event()
        self.release_count = 0

    async def quiesce_for_handoff(self, session, request_id, *, transfer_destination=None):
        # A deterministic pause exposes the genuine pending receipt in the UI.
        # The gate never supplies synthetic writer-release evidence.
        self.release_count += 1
        self.release_started.set()
        await self.allow_release.wait()
        return await self.proof.quiesce_for_handoff(
            session, request_id, transfer_destination=transfer_destination)

    async def close(self):
        self.allow_release.set()
        await self.proof.close()


async def main(home):
    os.environ['AMPLIFIER_SESSION_STATE_HOME'] = str(home / 'locks')
    app = await fixture.main(home)
    service = app['service']
    session = service._session()
    root = Path(session['workspace'])
    runtime = Runtime()
    service.runtime = runtime
    app['runtime'] = runtime

    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / '.gitignore').write_text('ignored.txt\n')
    (root / 'file.txt').write_text('original\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'portable browser fixture')
    (root / 'file.txt').write_text('staged\n')
    git(root, 'add', 'file.txt')
    (root / 'file.txt').write_text('staged\nunstaged\n')
    (root / 'new.txt').write_text('Keep untracked work\n')
    (root / 'ignored.txt').write_text('Excluded local data\n')
    source_revision = snapshot(root)[0]['sourceRevision']

    session.update(title='Portable browser task', selection={'instance': 'one', 'model': 'fixture-model'},
                   status='idle', deferRuntimeUntilInteraction=True,
                   messages=[{'id': 'saved-user', 'role': 'user', 'text': 'Preserve this task',
                              'createdAt': time.time()}])
    native = SessionStore.for_app(service.data_dir, root)
    native.save(session['id'], [{'role': 'user', 'content': 'Preserve this task'},
                               {'role': 'assistant', 'content': 'Saved result'}],
                {'name': 'Portable browser task', 'bundle': session['bundle']})
    transcript = native.directory(session['id']) / 'transcript.jsonl'
    original_transcript = transcript.read_bytes()
    atomic(service.data_dir / 'sessions' / session['id'] / 'control-state.json',
           {'selection': session['selection'], 'budget': {'maxIterations': 10}})

    peer = TransferNode(home / 'peer', 'Local paired destination')
    atomic(service.portability.node.directory / 'peers.json', {peer.identity['id']: peer.identity})
    bad_package = home / 'exchange' / 'unsupported-peer-package.json'
    write_capsule(bad_package, peer.sign({'kind': 'capsule', 'version': 99}))
    await service.dispatch('view.update', {'patch': {'panel': 'settings', 'runtimeDraft': {'tab': 'overview'}}})

    async def allow_release(request):
        runtime.allow_release.set()
        return web.json_response({'ok': True})

    async def state(request):
        store = SharedSessionStore(root, session['id'])
        allowed = False
        try:
            held = store.acquire(app='portability-browser-proof')
        except SessionTransferFencedError:
            pass
        else:
            allowed = True
            held.release()
        receipts = service.portability.node.records(session['id'])
        packages = []
        for row in receipts:
            if row.get('package'):
                workspace = read_capsule(row['package'])['body']['payload']['workspace']
                packages.append({'mode': workspace['mode'], 'untracked': [item['path'] for item in workspace['untracked']],
                                 'ignoredFilesIncluded': workspace['ignoredFilesIncluded']})
        return web.json_response({'receipts': receipts, 'nativeFence': store.transfer_fence(),
            'ordinaryAcquisitionAllowed': allowed, 'releaseStarted': runtime.release_started.is_set(),
            'releaseCount': runtime.release_count, 'providerWorkers': len(runtime.proof.workers),
            'historyUnchanged': transcript.read_bytes() == original_transcript,
            'workspaceUnchanged': snapshot(root)[0]['sourceRevision'] == source_revision,
            'packages': packages})

    app.router.add_post('/fixture/allow-release', allow_release)
    app.router.add_get('/fixture/portability-state', state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url, 'sessionId': session['id'], 'root': str(root),
                      'peer': peer.identity, 'invalidPackage': str(bad_package)}), flush=True)
    stopped = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='unified-portability-browser-') as directory:
        asyncio.run(main(Path(directory).resolve()))
