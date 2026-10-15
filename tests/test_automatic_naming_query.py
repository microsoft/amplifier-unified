"""Background naming scans cannot block navigation or publish stale candidates."""
import asyncio

import pytest

from amplifier_web.automatic_naming_query import candidate_page, read_candidates
from test_automatic_history import app_factory
from test_automatic_naming import imported
from test_shell_query_owner import held_projector, started


async def test_process_candidate_page_matches_canonical_query_without_host_scan(app_factory, tmp_path, monkeypatch):
    app, naming, _, _ = await imported(app_factory, tmp_path, 37)
    expected = candidate_page(app.directory, app._state, 29)
    assert len(expected) == 32
    prior = {row['id'] for row in app._state['sessions']}

    def forbidden(*args, **kwargs):
        raise AssertionError('Naming discovery scanned the host directory')

    monkeypatch.setattr(app.directory, 'select', forbidden)
    actual = await read_candidates(app, 29)
    assert actual == expected
    assert app.performance_diagnostics.counters['naming.candidates.decodedRows'] == 32
    assert {row['id'] for row in app._state['sessions']} == prior
    assert not app.runtime.started and not app.runtime.sent


async def test_uncommitted_naming_scan_never_exports_or_spawns(app_factory, tmp_path):
    app, _, _, _ = await imported(app_factory, tmp_path)
    app.db.execute("UPDATE history_directory_layout SET revision=revision+1 WHERE version=1")
    def forbidden(*args, **kwargs):
        raise AssertionError('Uncommitted state spawned a reader')
    try:
        assert await read_candidates(app, 0, projector_factory=forbidden) == []
    finally:
        app.db.rollback()


async def test_held_naming_scan_releases_event_loop_and_foreground_lock(app_factory, tmp_path):
    app, _, _, _ = await imported(app_factory, tmp_path)
    task = asyncio.create_task(read_candidates(app, 0, projector_factory=held_projector))
    await started(app)
    try:
        async with asyncio.timeout(2):
            async with app.lock:
                for _ in range(10):
                    await asyncio.sleep(0)
        assert not task.done()
    finally:
        (app.data_dir / 'query-release').touch()
    assert len(await task) == 1


@pytest.mark.parametrize('change', ['durable', 'native', 'closed'])
async def test_changed_sources_discard_naming_page(app_factory, tmp_path, change):
    app, _, _, _ = await imported(app_factory, tmp_path)
    task = asyncio.create_task(read_candidates(app, 0, projector_factory=held_projector))
    await started(app)
    if change == 'durable':
        app.db.execute("UPDATE history_directory_layout SET revision=revision+1 WHERE version=1")
        app.db.commit()
    elif change == 'native':
        import sqlite3
        with sqlite3.connect(app.directory._native_path) as db:
            db.execute('UPDATE native_revisions SET root_navigation=root_navigation+1 WHERE namespace=?',
                       (app.directory._native_namespace,))
    else:
        app.closed = True
    (app.data_dir / 'query-release').touch()
    try:
        assert await task == []
    finally:
        # app_factory still owns the fixture's ordinary final close.
        if change == 'closed':
            app.closed = False


async def test_cancelled_naming_scan_closes_its_owned_child(app_factory, tmp_path):
    app, _, _, _ = await imported(app_factory, tmp_path)
    projectors = []
    def factory(*args, **kwargs):
        value = held_projector(*args, **kwargs)
        projectors.append(value)
        return value
    task = asyncio.create_task(read_candidates(app, 0, projector_factory=factory))
    await started(app)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert projectors[0]._closed
    assert not projectors[0]._tasks
    assert not app.runtime.started and not app.runtime.sent
