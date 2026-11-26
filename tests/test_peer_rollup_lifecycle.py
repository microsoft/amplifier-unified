"""Event-controlled cold window/proof ownership, not elapsed-time cleanup proof."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import threading

import pytest

from amplifier_web import saved_message_window
from test_peer_preview import app, make_cold
from test_peer_rollup_proof import long_receiver


@pytest.fixture(autouse=True)
async def reader_executor(app):
    """Exactly eight worker slots; readiness observers must use none of them."""
    loop = asyncio.get_running_loop()
    # Materialize the prior default through its public API before replacing it.
    await loop.run_in_executor(None, lambda: None)
    previous = loop._default_executor
    owned = ThreadPoolExecutor(max_workers=8, thread_name_prefix='peer-lifecycle-test')
    loop.set_default_executor(owned)
    try:
        yield
    finally:
        await app[0].history.windows.close()
        loop.set_default_executor(previous)
        owned.shutdown(wait=True)


@pytest.mark.parametrize('stage', ['window', 'qualification'])
@pytest.mark.parametrize('entrypoint', ['read', 'load'])
async def test_close_drains_whole_cold_pipeline_without_late_cache_publication(app, monkeypatch, stage, entrypoint):
    service, source, receiving, mid = app
    long_receiver(service, receiving)
    receiving = make_cold(service, receiving, copy.deepcopy(receiving['messages']))
    windows = service.history.windows
    loop = asyncio.get_running_loop()
    window_entered, window_release = asyncio.Event(), threading.Event()
    proof_entered, proof_release = asyncio.Event(), threading.Event()
    original_window, original_proof = saved_message_window.read_saved_window, windows.peer_originals.cold

    def window(*args, **kwargs):
        result = original_window(*args, **kwargs)
        loop.call_soon_threadsafe(window_entered.set)
        assert window_release.wait(10)
        return result

    def qualify(*args):
        loop.call_soon_threadsafe(proof_entered.set)
        assert proof_release.wait(10)
        return original_proof(*args)

    monkeypatch.setattr(saved_message_window, 'read_saved_window', window)
    monkeypatch.setattr(windows.peer_originals, 'cold', qualify)
    with service.clients.bind('preview-client'):
        read = asyncio.create_task(getattr(windows, entrypoint)(receiving['id']))
        close = None
        try:
            await asyncio.wait_for(window_entered.wait(), 5)
            if stage == 'qualification':
                window_release.set()
                await asyncio.wait_for(proof_entered.wait(), 5)
            closing = asyncio.Event()
            async def drain():
                closing.set()
                await windows.close()
            close = asyncio.create_task(drain())
            await closing.wait()  # close has set closed and yielded in its drain.
            assert windows.closed and not close.done()
            window_release.set()
            await asyncio.wait_for(proof_entered.wait(), 5)
            assert not close.done(), 'Shutdown lost the physical qualification phase'
            with pytest.raises(ValueError, match='closed'):
                await windows.read(receiving['id'])
            proof_release.set()
            await asyncio.wait_for(close, 5)
            if entrypoint == 'read':
                with pytest.raises(ValueError, match='closed'):
                    await read
            else:
                await read
            assert not windows.jobs and not windows.pages and not windows.initial
            assert not windows.peer_originals.cache and windows.peer_originals.retained_bytes == 0
        finally:
            window_release.set()
            proof_release.set()
            await asyncio.gather(read, *([close] if close else []), return_exceptions=True)


async def test_eight_admissions_keep_same_jobs_across_phases_and_http_cancellation(app, monkeypatch):
    service, source, receiving, mid = app
    long_receiver(service, receiving)
    receiving = make_cold(service, receiving, copy.deepcopy(receiving['messages']))
    windows = service.history.windows
    loop = asyncio.get_running_loop()
    window_entered, window_release = asyncio.Event(), threading.Event()
    proof_entered, proof_release = asyncio.Event(), threading.Event()
    counts, mutex = {'window': 0, 'proof': 0}, threading.Lock()
    original_window, original_proof = saved_message_window.read_saved_window, windows.peer_originals.cold

    def window(*args, **kwargs):
        result = original_window(*args, **kwargs)
        with mutex:
            counts['window'] += 1
            if counts['window'] == 8:
                loop.call_soon_threadsafe(window_entered.set)
        assert window_release.wait(10)
        return result

    def qualify(*args):
        with mutex:
            counts['proof'] += 1
            if counts['proof'] == 8:
                loop.call_soon_threadsafe(proof_entered.set)
        assert proof_release.wait(10)
        return original_proof(*args)

    monkeypatch.setattr(saved_message_window, 'read_saved_window', window)
    monkeypatch.setattr(windows.peer_originals, 'cold', qualify)
    with service.clients.bind('preview-client'):
        readers = [asyncio.create_task(windows.read(receiving['id'])) for _ in range(8)]
        try:
            await asyncio.wait_for(window_entered.wait(), 5)
            admitted = set(windows.jobs)
            assert len(admitted) == 8
            with pytest.raises(ValueError, match='busy'):
                await windows.read(receiving['id'])
            window_release.set()
            await asyncio.wait_for(proof_entered.wait(), 5)
            assert windows.jobs == admitted, 'Stage two must not drop or reacquire admission'
            readers[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await readers[0]
            assert windows.jobs == admitted
            with pytest.raises(ValueError, match='busy'):
                await windows.read(receiving['id'])
            assert counts == {'window': 8, 'proof': 8}
            proof_release.set()
            results = await asyncio.gather(*readers[1:])
            assert all(page['items'][-1]['attribution']['peer']['available'] for page in results)
            await windows.close()
            assert not windows.jobs and not windows.pages and not windows.peer_originals.cache
        finally:
            window_release.set()
            proof_release.set()
            await asyncio.gather(*readers, return_exceptions=True)
            await windows.close()
