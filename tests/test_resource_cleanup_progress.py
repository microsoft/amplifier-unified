"""Large retained stores must make progress without weakening deletion proof."""
import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import resource_files as files, resource_roots, storage_migration


def setup(tmp_path):
    db = sqlite3.connect(tmp_path / 'app.sqlite3', timeout=0)
    db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.execute('CREATE TABLE client_views (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    keep = files.put(db, {'keep': True})
    garbage = files.put(db, {'garbage': True})
    db.commit()
    app = SimpleNamespace(db=db, _state={}, closed=False, lock=asyncio.Lock())
    return app, keep, garbage


async def close(app):
    await storage_migration.close_maintenance(app)
    if getattr(app, '_resource_scan', None):
        app._resource_scan.close()
    app.db.close()


@pytest.mark.parametrize('store', ['live', 'state', 'state_records', 'client_views',
                                 'smart_tool_operations', 'conversation_shares', 'output_records'])
async def test_large_roots_collect_automatically_without_shrinking_records(tmp_path, monkeypatch, store):
    app, keep, garbage = setup(tmp_path)
    row = {'id': 'large', 'keep': keep, 'text': 'x' * 300_000, 'wide': list(range(60_000))}
    db = app.db
    if store == 'live':
        app._state = row
    elif store in {'state', 'state_records'}:
        from amplifier_web import state_records
        db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('INSERT INTO state VALUES(1,?)',
                   (json.dumps({'revision': 0, 'sessions': [row] if store == 'state' else []}),))
        if store == 'state_records':
            state_records.initialize(db)
            db.execute('INSERT INTO state_records VALUES(?,?,?)', ('session', 'large', json.dumps(row)))
    else:
        db.execute('CREATE TABLE IF NOT EXISTS ' + store + ' (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('INSERT INTO ' + store + ' VALUES(?,?)', ('large', json.dumps(row)))
    db.commit()
    before = resource_roots.capture(db)
    owner = threading.get_ident()
    sql_threads, parse_threads = [], []
    db.set_trace_callback(lambda _: sql_threads.append(threading.get_ident()))
    original = resource_roots.references

    def parse(snapshot):
        parse_threads.append(threading.get_ident())
        assert not app.lock.locked()
        return original(snapshot)

    monkeypatch.setattr(resource_roots, 'references', parse)
    try:
        storage_migration.maintenance(app)
        await app._storage_maintenance_task
        assert not app._resource_scan_status['blocked']
        assert db.execute('SELECT id FROM state_resources').fetchall() == [(keep['$resource'],)]
        assert resource_roots.capture(db) == before
        assert (files.root(db) / (keep['$resource'] + '.json')).is_file()
        assert not (files.root(db) / (garbage['$resource'] + '.json')).exists()
        assert sql_threads and set(sql_threads) == {owner}
        assert parse_threads and owner not in parse_threads
    finally:
        await close(app)


@pytest.mark.parametrize('cancel', [False, True])
async def test_durable_decode_releases_app_and_sql_writer_and_settles_on_close(tmp_path, monkeypatch, cancel):
    app, keep, garbage = setup(tmp_path)
    app.db.execute('INSERT INTO client_views VALUES(?,?)', ('one', json.dumps({'keep': keep})))
    app.db.commit()
    entered, release = threading.Event(), threading.Event()
    original = resource_roots.references

    def parse(snapshot):
        entered.set()
        assert release.wait(5)
        return original(snapshot)

    monkeypatch.setattr(resource_roots, 'references', parse)
    task = asyncio.create_task(storage_migration._maintenance_async(app))
    competitor = sqlite3.connect(tmp_path / 'app.sqlite3', timeout=0)
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        async with asyncio.timeout(1):
            async with app.lock:
                competitor.execute('BEGIN IMMEDIATE')
                competitor.rollback()
        if cancel:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        release.set()
        await asyncio.gather(task, return_exceptions=cancel)
        exists = app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (garbage['$resource'],)).fetchone()
        assert bool(exists) is cancel
        assert not app.db.in_transaction and not app.lock.locked()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        competitor.close()
        await close(app)


@pytest.mark.parametrize('late_root', [None, 'durable', 'live', 'index'])
async def test_changed_durable_roots_are_parsed_then_revalidated_under_writer_exclusion(tmp_path, monkeypatch, late_root):
    app, keep, garbage = setup(tmp_path)
    db = app.db
    unrelated = files.put(db, {'unrelated': 'obsolete'})
    db.execute('INSERT INTO client_views VALUES(?,?)', ('one', json.dumps({'keep': keep, 'progress': 0})))
    db.commit()
    competitor = sqlite3.connect(tmp_path / 'app.sqlite3', timeout=0)
    body_entered, body_release = threading.Event(), threading.Event()
    final_entered, final_release = threading.Event(), threading.Event()
    read, parse = files.detached_references, resource_roots.references

    def inspect(source):
        body_entered.set()
        assert body_release.wait(5)
        return read(source)

    calls = 0

    def decode(snapshot):
        nonlocal calls
        calls += 1
        if calls == 2:
            final_entered.set()
            assert final_release.wait(5)
        return parse(snapshot)

    monkeypatch.setattr(files, 'detached_references', inspect)
    monkeypatch.setattr(resource_roots, 'references', decode)
    task = asyncio.create_task(storage_migration._maintenance_async(app))
    try:
        assert await asyncio.to_thread(body_entered.wait, 5)
        async with app.lock:
            db.execute('UPDATE client_views SET value=?', (json.dumps({'keep': keep, 'progress': 1}),))
            db.commit()
        body_release.set()
        assert await asyncio.to_thread(final_entered.wait, 5)
        if late_root == 'durable':
            competitor.execute('UPDATE client_views SET value=?', (json.dumps({'keep': keep, 'new': garbage}),))
            competitor.commit()
        elif late_root == 'live':
            app._state['new'] = garbage
        elif late_root == 'index':
            competitor.execute('UPDATE state_resources SET value=? WHERE id=?',
                               (json.dumps({'new': garbage}), keep['$resource']))
            competitor.commit()
        final_release.set()
        await task
        # New roots now catch up through the ordinary drain loop. A conflicting
        # immutable source index still invalidates the proof and refuses ALL GC.
        assert app._resource_scan_status['blocked'] is (late_root == 'index')
        assert bool(db.execute('SELECT 1 FROM state_resources WHERE id=?', (garbage['$resource'],)).fetchone()) is bool(late_root)
        assert bool(db.execute('SELECT 1 FROM state_resources WHERE id=?',
                               (unrelated['$resource'],)).fetchone()) is (late_root == 'index')
        if late_root == 'index':
            assert app._resource_scan_status['reason'] == 'resource-index-changed'
        elif late_root:
            assert storage_migration.resource(db, garbage['$resource']) == {'garbage': True}
        assert not db.in_transaction
    finally:
        body_release.set()
        final_release.set()
        await asyncio.gather(task, return_exceptions=True)
        competitor.close()
        await close(app)


@pytest.mark.parametrize('change', ['durable', 'index'])
async def test_stable_live_capture_still_rechecks_external_writes_before_pruning(tmp_path, monkeypatch, change):
    app, keep, garbage = setup(tmp_path)
    app._state['keep'] = keep
    competitor = sqlite3.connect(tmp_path / 'app.sqlite3', timeout=0)
    capture = resource_roots.capture
    calls = 0

    def capture_then_external_write(db):
        nonlocal calls
        snapshot = capture(db)
        calls += 1
        if calls == 2:
            # An external writer can commit after the unchanged live capture
            # but before this connection acquires its writer transaction.
            if change == 'durable':
                competitor.execute('INSERT INTO client_views VALUES(?,?)',
                                   ('late', json.dumps({'keep': garbage})))
            else:
                competitor.execute('UPDATE state_resources SET value=? WHERE id=?',
                                   (json.dumps({'keep': garbage}), keep['$resource']))
            competitor.commit()
        return snapshot

    monkeypatch.setattr(resource_roots, 'capture', capture_then_external_write)
    try:
        await storage_migration._maintenance_async(app)
        assert app._resource_scan_status['blocked']
        assert app._resource_scan_status['reason'] == {
            'durable': 'durable-roots-changed', 'index': 'resource-index-changed'}[change]
        assert app.db.execute('SELECT COUNT(*) FROM state_resources').fetchone()[0] == 2
        assert (files.root(app.db) / (garbage['$resource'] + '.json')).is_file()
        assert not app.lock.locked() and not app.db.in_transaction
    finally:
        competitor.close()
        await close(app)


async def test_background_deletion_drains_batches_without_rewalking_retained_bodies(tmp_path, monkeypatch):
    app, keep, _ = setup(tmp_path)
    app._state['keep'] = keep
    for i in range(6):
        files.put(app.db, {'obsolete': i})
    app.db.commit()
    monkeypatch.setattr(storage_migration, 'BACKGROUND_PRUNE_LIMIT', 2)
    original = files.remove_files_async
    batches = []

    async def remove(db, identities):
        batches.append(len(identities))
        return await original(db, identities)

    monkeypatch.setattr(files, 'remove_files_async', remove)
    try:
        storage_migration.maintenance(app)
        await app._storage_maintenance_task
        assert len(batches) >= 4 and max(batches) <= 2
        assert sum(batches) == 7
        assert app.db.execute('SELECT id FROM state_resources').fetchall() == [(keep['$resource'],)]
        assert len(list(files.root(app.db).glob('*.json'))) == 1
        assert app._resource_edges.misses == 1 and app._resource_edges.hits == 0
    finally:
        await close(app)


async def test_async_delete_rechecks_adoption_after_index_commit(tmp_path, monkeypatch):
    app, keep, garbage = setup(tmp_path)
    app._state['keep'] = keep
    original = files.remove_files_async
    competitor = sqlite3.connect(tmp_path / 'app.sqlite3', timeout=0)

    async def readopt(db, identities):
        assert not db.in_transaction
        assert files.put(competitor, {'garbage': True}) == garbage
        competitor.commit()
        await original(db, identities)

    monkeypatch.setattr(files, 'remove_files_async', readopt)
    try:
        await storage_migration._maintenance_async(app)
        assert app.db.execute('SELECT COUNT(*) FROM state_resources').fetchone()[0] == 2
        assert (files.root(app.db) / (garbage['$resource'] + '.json')).is_file()
    finally:
        competitor.close()
        await close(app)


async def test_cancellation_settles_bounded_unlink_before_releasing_ownership(tmp_path, monkeypatch):
    from amplifier_web.performance_diagnostics import PerformanceDiagnostics
    app, keep, garbage = setup(tmp_path)
    app.performance_diagnostics = PerformanceDiagnostics()
    app._state['keep'] = keep
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = Path.unlink
    owner = threading.get_ident()

    def unlink(path, *args, **kwargs):
        if path.name == garbage['$resource'] + '.json':
            assert threading.get_ident() != owner
            entered.set()
            assert release.wait(5)
            try:
                return original(path, *args, **kwargs)
            finally:
                finished.set()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', unlink)
    competitor = sqlite3.connect(tmp_path / 'app.sqlite3', timeout=0)
    task = asyncio.create_task(storage_migration._maintenance_async(app))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert app.lock.locked() and app.db.in_transaction
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            competitor.execute('BEGIN IMMEDIATE')
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        assert app.lock.locked() and app.db.in_transaction
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set() and not app.lock.locked() and not app.db.in_transaction
        assert (files.root(app.db) / (keep['$resource'] + '.json')).is_file()
        assert not (files.root(app.db) / (garbage['$resource'] + '.json')).exists()
        facts = app.performance_diagnostics.snapshot()['storageCollection']
        assert facts['outcome'] == 'cancelled' and not facts['removalCountsComplete']
        competitor.execute('BEGIN IMMEDIATE')
        competitor.rollback()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        competitor.close()
        await close(app)


async def test_detached_roots_apply_overlay_replacements_and_tombstones(tmp_path):
    from amplifier_web import state_records
    app, keep, garbage = setup(tmp_path)
    db = app.db
    db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
    db.execute('INSERT INTO state VALUES(1,?)', (json.dumps({
        'revision': 0, 'sessions': [{'id': 'gone', 'ref': garbage}, {'id': 'keep', 'ref': garbage}],
        'runtimeControl': {'gone': {'ref': garbage}}, 'gone': garbage}),))
    state_records.initialize(db)
    db.executemany('INSERT INTO state_records VALUES(?,?,?)', [
        ('revision', '', '1'), ('session', 'gone', 'null'),
        ('session', 'keep', json.dumps({'id': 'keep', 'ref': keep})),
        ('runtime', 'gone', 'null'),
        ('global', 'gone', json.dumps({'present': False, 'value': None}))])
    db.commit()
    before = resource_roots.capture(db)
    try:
        assert resource_roots.references(before) == set(files.retained_references(db, {})) == {keep['$resource']}
        await storage_migration._maintenance_async(app)
        assert resource_roots.capture(db) == before
        assert db.execute('SELECT id FROM state_resources').fetchall() == [(keep['$resource'],)]
    finally:
        await close(app)


@pytest.mark.parametrize('value', ['{broken', '{"keep":{"$resource":"missing"}}'])
async def test_invalid_durable_roots_never_authorize_collection(tmp_path, value):
    app, keep, garbage = setup(tmp_path)
    app.db.execute('INSERT INTO client_views VALUES(?,?)', ('invalid', value))
    app.db.commit()
    try:
        await storage_migration._maintenance_async(app)
        assert app.db.execute('SELECT COUNT(*) FROM state_resources').fetchone()[0] == 2
        assert all((files.root(app.db) / (ref['$resource'] + '.json')).exists() for ref in (keep, garbage))
        assert not app.db.in_transaction and not app.lock.locked()
    finally:
        await close(app)


@pytest.mark.parametrize('store', ['live', 'client_views'])
@pytest.mark.parametrize('broken', [False, True])
async def test_progress_snapshot_added_during_walk_can_collect_unrelated_garbage(tmp_path, monkeypatch, store, broken):
    app, keep, adopted = setup(tmp_path)
    garbage = files.put(app.db, {'unrelated': 'obsolete'})

    def save(ref):
        if store == 'live':
            app._state['current'] = ref
        else:
            app.db.execute('INSERT OR REPLACE INTO client_views VALUES(?,?)', ('view', json.dumps(ref)))
        app.db.commit()

    save(keep)
    entered, release = threading.Event(), threading.Event()
    original = files.detached_references

    def inspect(source):
        if source.identity == keep['$resource']:
            entered.set()
            assert release.wait(5)
        return original(source)

    monkeypatch.setattr(files, 'detached_references', inspect)
    task = asyncio.create_task(storage_migration._maintenance_async(app))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        async with app.lock:
            # A new snapshot can adopt an OLD candidate for deletion, not only
            # fresh bodies. Its entire nested closure must join the proof.
            current = files.put(app.db, {'revision': 1, 'retained': adopted})
            save(current)
            if broken:
                (files.root(app.db) / (current['$resource'] + '.json')).unlink()
        release.set()
        await task
        retained = {row[0] for row in app.db.execute('SELECT id FROM state_resources')}
        assert {keep['$resource'], current['$resource'], adopted['$resource']} <= retained
        if broken:
            assert garbage['$resource'] in retained
            assert app._resource_scan_status['reason'] == 'resource-body-unavailable'
            return
        assert garbage['$resource'] not in retained
        assert not (files.root(app.db) / (garbage['$resource'] + '.json')).exists()
        assert not app._resource_scan_status['blocked']
        # The conservative union delays obsolete roots for only this pass.
        await storage_migration._maintenance_async(app)
        assert not app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (keep['$resource'],)).fetchone()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await close(app)


async def test_continuous_new_roots_defer_at_bounded_catchup_work(tmp_path, monkeypatch):
    from amplifier_web.performance_diagnostics import PerformanceDiagnostics
    app, keep, garbage = setup(tmp_path)
    app._state['current'] = keep
    app.performance_diagnostics = PerformanceDiagnostics()
    monkeypatch.setattr(storage_migration, 'BACKGROUND_CATCHUP_RESOURCES', 3)
    original = files.ReferenceEdges.read
    loop = asyncio.get_running_loop()
    writes = []

    async def progress():
        async with app.lock:
            current = files.put(app.db, {'progress': len(writes), 'nested': keep})
            app._state['current'] = current
            writes.append(current)
            app.db.commit()

    def inspect(edges, source):
        # Every inspection sees another ordinary committed session snapshot.
        asyncio.run_coroutine_threadsafe(progress(), loop).result(5)
        return original(edges, source)

    monkeypatch.setattr(files.ReferenceEdges, 'read', inspect)
    try:
        await asyncio.wait_for(storage_migration._maintenance_async(app), 5)
        assert len(writes) == 4
        assert app._resource_scan_status['reason'] == 'root-catchup-budget'
        assert app.db.execute('SELECT COUNT(*) FROM state_resources').fetchone()[0] == 2 + len(writes)
        facts = app.performance_diagnostics.snapshot()['storageCollection']
        assert facts['outcome'] == 'deferred' and facts['reason'] == 'root-catchup-budget'
        assert facts['indexedFilesRemoved'] == 0 and not facts['removalCountsComplete']
        assert not app.db.in_transaction and not app.lock.locked()
    finally:
        await close(app)


@pytest.mark.parametrize('store', ['live', 'client_views'])
async def test_chat_progress_beyond_three_catchups_does_not_starve_collection(tmp_path, monkeypatch, store):
    app, keep, garbage = setup(tmp_path)
    original = files.ReferenceEdges.read
    loop = asyncio.get_running_loop()
    writes = []

    async def progress():
        async with app.lock:
            current = files.put(app.db, {'progress': len(writes), 'nested': keep})
            if store == 'live':
                app._state['current'] = current
            else:
                app.db.execute('INSERT OR REPLACE INTO client_views VALUES(?,?)',
                               ('view', json.dumps({'current': current})))
            app.db.commit()
            writes.append(current)

    def inspect(edges, source):
        # Eight ordinary snapshots arrive during body inspection. Each can
        # reference an old resource, so their complete closure must be retained.
        if len(writes) < 8:
            asyncio.run_coroutine_threadsafe(progress(), loop).result(5)
        return original(edges, source)

    app._state['current'] = keep
    monkeypatch.setattr(files.ReferenceEdges, 'read', inspect)
    try:
        await storage_migration._maintenance_async(app)
        assert len(writes) == 8
        assert not app._resource_scan_status['blocked']
        assert not app.db.execute('SELECT 1 FROM state_resources WHERE id=?',
                                  (garbage['$resource'],)).fetchone()
        assert all(storage_migration.resource(app.db, ref['$resource']) for ref in [keep, *writes])
        assert not app.lock.locked() and not app.db.in_transaction
    finally:
        await close(app)


@pytest.mark.parametrize('store', ['live', 'client_views', 'smart_tool_operations',
                                 'conversation_shares', 'output_records'])
async def test_between_batches_new_root_retains_old_candidate_and_complete_closure(tmp_path, monkeypatch, store):
    app, keep, _ = setup(tmp_path)
    app._state['keep'] = keep
    child = files.put(app.db, {'old': 'candidate child'})
    parent = files.put(app.db, {'child': child})
    for i in range(24):
        files.put(app.db, {'obsolete': i})
    first = next(prepared for i in range(1024)
                 if (prepared := files.prepare({'first': i})).identity
                 < min(parent['$resource'], child['$resource']))
    files.put_prepared(app.db, first)
    app.db.commit()
    # Start with a retained page, then let progress adopt an old candidate at
    # the first inter-batch yield. Both parent and child are ahead of the cursor.
    ordered = [row[0] for row in app.db.execute('SELECT id FROM state_resources ORDER BY id')]
    app._state['first'] = {'$resource': ordered[0]}
    assert min(parent['$resource'], child['$resource']) > ordered[0]
    monkeypatch.setattr(storage_migration, 'BACKGROUND_PRUNE_LIMIT', 1)
    original = files.remove_files_async
    progress = []
    batches = []

    async def adopt():
        async with app.lock:
            assert not app.db.in_transaction
            if store == 'live':
                app._state['adopted'] = parent
            else:
                app.db.execute('CREATE TABLE IF NOT EXISTS ' + store + ' (id TEXT PRIMARY KEY,value TEXT)')
                app.db.execute('INSERT INTO ' + store + ' VALUES(?,?)', ('adopt', json.dumps(parent)))
                app.db.commit()
            progress.append(True)

    async def remove(db, identities):
        batches.append(len(identities))
        if len(batches) == 1:
            progress.append(asyncio.create_task(adopt()))
        return await original(db, identities)

    monkeypatch.setattr(files, 'remove_files_async', remove)
    try:
        await storage_migration._maintenance_async(app)
        await progress[0]
        assert progress[-1] is True and len(batches) > 2 and max(batches) == 1
        assert storage_migration.resource(app.db, parent['$resource']) == {'child': child}
        assert storage_migration.resource(app.db, child['$resource']) == {'old': 'candidate child'}
        assert app.db.execute('SELECT COUNT(*) FROM state_resources').fetchone()[0] == 4
        assert app._resource_edges.hits == 0
        assert not app.lock.locked() and not app.db.in_transaction
    finally:
        await close(app)


async def test_elapsed_drain_budget_keeps_keyset_progress_through_retained_prefix(tmp_path, monkeypatch):
    app, _, _ = setup(tmp_path)
    for i in range(14):
        files.put(app.db, {'row': i})
    app.db.commit()
    ordered = [row[0] for row in app.db.execute('SELECT id FROM state_resources ORDER BY id')]
    retained = ordered[:6]
    app._state['retained'] = [{'$resource': identity} for identity in retained]
    monkeypatch.setattr(storage_migration, 'BACKGROUND_PRUNE_LIMIT', 2)
    clock = [0]
    monkeypatch.setattr(storage_migration, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    original = files.remove_files_async
    batches = []

    async def remove(db, identities):
        batches.append(list(identities))
        result = await original(db, identities)
        clock[0] += storage_migration.BACKGROUND_DRAIN_SECONDS
        return result

    monkeypatch.setattr(files, 'remove_files_async', remove)
    try:
        for page in range(8):
            await storage_migration._maintenance_async(app)
            assert app._resource_scan_status['indexedInspected'] == 2
            assert app._resource_prune_after == ordered[2 * page + 1]
        assert len(batches) == 8 and batches[:3] == [[], [], []]
        assert {identity for batch in batches for identity in batch} == set(ordered) - set(retained)
        assert [row[0] for row in app.db.execute('SELECT id FROM state_resources ORDER BY id')] == retained
        await storage_migration._maintenance_async(app)
        assert app._resource_prune_after == ''  # next ordinary pass wraps
    finally:
        await close(app)


async def test_scalar_durable_progress_between_batches_keeps_draining(tmp_path, monkeypatch):
    app, keep, _ = setup(tmp_path)
    for i in range(8):
        files.put(app.db, {'obsolete': i})
    app.db.execute('INSERT INTO client_views VALUES(?,?)', ('view', json.dumps(keep)))
    app.db.commit()
    monkeypatch.setattr(storage_migration, 'BACKGROUND_PRUNE_LIMIT', 2)
    original = files.remove_files_async
    writes = []

    async def progress():
        async with app.lock:
            app.db.execute('UPDATE client_views SET value=?',
                           (json.dumps({'keep': keep, 'progress': len(writes)}),))
            app.db.commit()
            writes.append(True)

    async def remove(db, identities):
        result = await original(db, identities)
        asyncio.create_task(progress())
        return result

    monkeypatch.setattr(files, 'remove_files_async', remove)
    try:
        await storage_migration._maintenance_async(app)
        await asyncio.sleep(0)
        assert len(writes) > 3
        assert app.db.execute('SELECT id FROM state_resources').fetchall() == [(keep['$resource'],)]
        assert app._resource_edges.misses == 1 and app._resource_edges.hits == 0
    finally:
        await close(app)


async def test_unindexed_backlog_drains_after_indexed_eof_in_one_admission(tmp_path):
    app, keep, _ = setup(tmp_path)
    app._state['keep'] = keep
    directory = files.root(app.db)
    for i in range(300):
        (directory / (format(i, '064x') + '.json')).write_text('{}')
    try:
        await storage_migration._maintenance_async(app)
        assert list(directory.iterdir()) == [directory / (keep['$resource'] + '.json')]
        assert app._resource_scan_status['complete']
        assert app._resource_scan is None
        assert app._resource_edges.misses == 1 and app._resource_edges.hits == 0
    finally:
        await close(app)


async def test_budget_expired_during_yield_admits_no_additional_batch(tmp_path, monkeypatch):
    app, keep, _ = setup(tmp_path)
    app._state['keep'] = keep
    for i in range(8):
        files.put(app.db, {'obsolete': i})
    app.db.commit()
    monkeypatch.setattr(storage_migration, 'BACKGROUND_PRUNE_LIMIT', 2)
    clock = [0]
    monkeypatch.setattr(storage_migration, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    original = files.remove_files_async
    batches = []

    def delay():
        clock[0] += storage_migration.BACKGROUND_DRAIN_SECONDS

    async def remove(db, identities):
        batches.append(identities)
        result = await original(db, identities)
        asyncio.get_running_loop().call_soon(delay)
        return result

    monkeypatch.setattr(files, 'remove_files_async', remove)
    try:
        await storage_migration._maintenance_async(app)
        assert len(batches) == 1
        assert app.db.execute('SELECT COUNT(*) FROM state_resources').fetchone()[0] >= 8
        assert not app.lock.locked() and not app.db.in_transaction
    finally:
        await close(app)


async def test_catchup_decode_consuming_budget_starts_no_inspection_or_removal(tmp_path, monkeypatch):
    app, keep, garbage = setup(tmp_path)
    app._state['keep'] = keep
    app.db.execute('INSERT INTO client_views VALUES(?,?)', ('view', json.dumps(keep)))
    app.db.commit()
    clock = [0]
    monkeypatch.setattr(storage_migration, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    read, parse = files.ReferenceEdges.read, resource_roots.references
    loop = asyncio.get_running_loop()
    calls = []

    async def progress():
        async with app.lock:
            app.db.execute('UPDATE client_views SET value=?',
                           (json.dumps({'keep': keep, 'new': garbage}),))
            app.db.commit()

    def inspect(cache, source):
        asyncio.run_coroutine_threadsafe(progress(), loop).result(5)
        return read(cache, source)

    def decode(snapshot):
        calls.append(True)
        if len(calls) == 2:
            clock[0] += storage_migration.BACKGROUND_CATCHUP_SECONDS
        return parse(snapshot)

    monkeypatch.setattr(files.ReferenceEdges, 'read', inspect)
    monkeypatch.setattr(resource_roots, 'references', decode)
    try:
        await storage_migration._maintenance_async(app)
        assert len(calls) == 2 and app._resource_edges.misses == 1
        assert app._resource_scan_status['reason'] == 'root-catchup-budget'
        assert app.db.execute('SELECT COUNT(*) FROM state_resources').fetchone()[0] == 2
        assert all((files.root(app.db) / (ref['$resource'] + '.json')).exists() for ref in (keep, garbage))
        assert not app.lock.locked() and not app.db.in_transaction
    finally:
        await close(app)


@pytest.mark.parametrize('garbage_count', [24, 2048])
async def test_one_drain_reads_retained_bodies_once_and_bounds_every_index_page(tmp_path, monkeypatch, garbage_count):
    app, keep, _ = setup(tmp_path)
    app._state['keep'] = keep
    for i in range(garbage_count):
        files.put(app.db, {'obsolete': i})
    app.db.commit()
    monkeypatch.setattr(storage_migration, 'BACKGROUND_PRUNE_LIMIT', 32)
    original = files.prune_marked
    scandir = files.os.scandir
    pages = []
    scans = []

    def prune(db, marked, **kwargs):
        pages.append(len(kwargs['candidates']))
        return original(db, marked, **kwargs)

    def scan(path):
        if Path(path) == files.root(app.db):
            scans.append(1)
        return scandir(path)

    monkeypatch.setattr(files, 'prune_marked', prune)
    monkeypatch.setattr(files.os, 'scandir', scan)
    try:
        await storage_migration._maintenance_async(app)
        assert max(pages) <= 32 and sum(pages) == garbage_count + 2
        assert len(pages) == (garbage_count + 2) // 32 + 1
        assert app._resource_edges.misses == 1 and app._resource_edges.hits == 0
        assert len(scans) == 1
        assert app.db.execute('SELECT id FROM state_resources').fetchall() == [(keep['$resource'],)]
        assert not app.lock.locked() and not app.db.in_transaction
    finally:
        await close(app)


async def test_later_batch_cancellation_preserves_prior_effects_and_settles_before_unlock(tmp_path, monkeypatch):
    app, keep, _ = setup(tmp_path)
    app._state['keep'] = keep
    for i in range(12):
        files.put(app.db, {'obsolete': i})
    app.db.commit()
    monkeypatch.setattr(storage_migration, 'BACKGROUND_PRUNE_LIMIT', 1)
    original_remove, original_unlink = files.remove_files_async, Path.unlink
    batches = []
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    blocked = []
    owner = threading.get_ident()

    async def remove(db, identities):
        if identities:
            batches.append(list(identities))
            if len(batches) == 2:
                blocked.append(identities[0] + '.json')
        return await original_remove(db, identities)

    def unlink(path, *args, **kwargs):
        if path.name in blocked:
            assert threading.get_ident() != owner
            entered.set()
            assert release.wait(5)
            try:
                return original_unlink(path, *args, **kwargs)
            finally:
                finished.set()
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(files, 'remove_files_async', remove)
    monkeypatch.setattr(Path, 'unlink', unlink)
    task = asyncio.create_task(storage_migration._maintenance_async(app))
    competitor = sqlite3.connect(tmp_path / 'app.sqlite3', timeout=0)
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert len(batches) == 2
        assert not (files.root(app.db) / (batches[0][0] + '.json')).exists()
        assert app.lock.locked() and app.db.in_transaction
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            competitor.execute('BEGIN IMMEDIATE')
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set() and len(batches) == 2
        assert app.db.execute('SELECT COUNT(*) FROM state_resources').fetchone()[0] == 12
        assert storage_migration.resource(app.db, keep['$resource']) == {'keep': True}
        assert not app.lock.locked() and not app.db.in_transaction
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        competitor.close()
        await close(app)


@pytest.mark.parametrize('readopt', [False, True])
async def test_collector_reports_confirmed_unlinks_separately_from_pruned_index(tmp_path, monkeypatch, readopt):
    from amplifier_web.performance_diagnostics import PerformanceDiagnostics
    app, keep, garbage = setup(tmp_path)
    app._state['keep'] = keep
    app.performance_diagnostics = PerformanceDiagnostics()
    expected_bytes = (files.root(app.db) / (garbage['$resource'] + '.json')).stat().st_size
    original = files.remove_files_async

    async def unlink(db, identities):
        if readopt:
            assert files.put(db, {'garbage': True}) == garbage
            db.commit()
        return await original(db, identities)

    monkeypatch.setattr(files, 'remove_files_async', unlink)
    try:
        await storage_migration._maintenance_async(app)
        snapshot = app.performance_diagnostics.snapshot()
        facts = snapshot['storageCollection']
        assert facts['outcome'] == 'completed' and facts['removalCountsComplete']
        assert facts['indexedPruned'] == 1
        assert facts['indexedFilesRemoved'] == (0 if readopt else 1)
        assert facts['indexedBytesRemoved'] == (0 if readopt else expected_bytes)
        assert snapshot['counters']['storage.gc.passesCompleted'] == 1
        assert snapshot['counters']['storage.gc.indexedBytesRemoved'] == facts['indexedBytesRemoved']
        assert str(tmp_path) not in json.dumps(snapshot)
        facts['indexedPruned'] = -1
        assert app.performance_diagnostics.snapshot()['storageCollection']['indexedPruned'] == 1
    finally:
        await close(app)
