"""Cancellation must settle native acquisition before startup exits."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from amplifier_foundation.session import shared_state
from amplifier_web import runtime_worker


async def observed(event):
    async with asyncio.timeout(8):
        while not event.is_set():
            await asyncio.sleep(.001)


def independent_acquire(workspace, root):
    # A fresh process tests the real OS lock, not just Foundation's local table.
    script = '''import json,sys
sys.path.insert(0, sys.argv[1])
from amplifier_foundation.session.shared_state import SharedSessionStore
held = SharedSessionStore(sys.argv[2], 'cancelled-worker', root=sys.argv[3]).acquire(app='contender')
try: print(json.dumps({'active': held.active}))
finally: held.release()
'''
    result = subprocess.run([sys.executable, '-B', '-c', script,
        str(Path(shared_state.__file__).resolve().parents[2]), str(workspace), str(root)],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'active': True}


@pytest.mark.skipif(os.name != 'posix', reason='Independent OS-lock witness is qualified on POSIX')
@pytest.mark.parametrize('barrier', ['before-acquire', 'before-delivery'])
async def test_worker_cancel_waits_for_real_acquire_and_release(tmp_path, monkeypatch, barrier):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    root = tmp_path / 'ownership'
    store = shared_state.SharedSessionStore(workspace, 'cancelled-worker', root=root)
    # Existing checkpoint bytes are authority; acquisition/abandonment cannot edit them.
    held = store.acquire(app='seed')
    try:
        held.write([{'role': 'user', 'content': 'unchanged'}], bundle='fixture')
    finally:
        held.release()
    checkpoint = store.checkpoint_path.read_bytes()
    worker = runtime_worker.Worker()
    worker.workspace, worker.home = workspace, tmp_path / 'app'
    worker.runtime = SimpleNamespace(session_id='cancelled-worker')
    worker.shared_store = store
    entered, acquired, proceed, releasing, release_now = (threading.Event() for _ in range(5))
    handles, calls = [], []
    original_acquire, original_release = store.acquire, shared_state.HeldSession.release

    def acquire(**kwargs):
        calls.append(kwargs)
        entered.set()
        if barrier == 'before-acquire':
            assert proceed.wait(10), 'fixture acquire barrier expired'
        handle = original_acquire(**kwargs)
        handles.append(handle)
        acquired.set()
        if barrier == 'before-delivery':
            assert proceed.wait(10), 'fixture delivery barrier expired'
        return handle

    def release(handle):
        if handle in handles:
            releasing.set()
            assert release_now.wait(10), 'fixture release barrier expired'
        return original_release(handle)

    def forbidden(*args, **kwargs):
        pytest.fail('Cancelled acquisition reached history, composition or reactivation')

    monkeypatch.setattr(store, 'acquire', acquire)
    monkeypatch.setattr(shared_state.HeldSession, 'release', release)
    monkeypatch.setattr('amplifier_web.history_revision.recover_pending', forbidden)
    monkeypatch.setattr('amplifier_web.host.session.prepare_manager', forbidden)
    monkeypatch.setattr('amplifier_web.runtime_bootstrap.bootstrap_app_package', lambda: None)
    monkeypatch.setattr('amplifier_web.runtime_qualification.active_install_overrides', lambda *args: None)
    monkeypatch.setitem(sys.modules, 'amplifier_module_loop_live.runtime',
                        SimpleNamespace(Runtime=lambda **kwargs: worker.runtime))
    events = []
    monkeypatch.setattr(runtime_worker, 'publish', events.append)
    operation = worker.start({'id': 'cancelled-worker', 'workspace': str(workspace)},
                             recover_bundle=False, raise_errors=True)
    task = asyncio.create_task(operation)
    try:
        await observed(entered if barrier == 'before-acquire' else acquired)
        task.cancel('original cancellation')
        await asyncio.sleep(0)
        task.cancel('repeated during acquisition')
        await asyncio.sleep(0)
        assert not task.done()
        proceed.set()
        await observed(releasing)
        task.cancel('repeated during release')
        await asyncio.sleep(0)
        assert not task.done() and handles[0].active
        release_now.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, 5)
        assert caught.value.args == ('original cancellation',)
        assert calls == [{'app': 'amplifier-unified', 'pid': os.getpid()}]
        assert len(handles) == 1 and not handles[0].active
        assert worker.shared_handle is None
        assert not any(e['type'] in {'runtime.ready', 'runtime.reactivated'} for e in events)
        assert store.checkpoint_path.read_bytes() == checkpoint
        await asyncio.to_thread(independent_acquire, workspace, root)
    finally:
        proceed.set()
        release_now.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for handle in handles:
            original_release(handle)
