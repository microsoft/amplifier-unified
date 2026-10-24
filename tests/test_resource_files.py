"""Synthetic storage ownership, cancellation and reconciliation checks."""
import asyncio
import hashlib
import json
import os
import sqlite3
import threading

import pytest

from amplifier_web import resource_files as files


def database(path):
    db = sqlite3.connect(path, timeout=0)
    db.execute('CREATE TABLE IF NOT EXISTS state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.commit()
    return db


async def test_preparation_file_work_detached_read_and_db_owner(tmp_path, monkeypatch):
    db = database(tmp_path / 'state.sqlite3')
    owner = threading.get_ident()
    sql_threads = []
    db.set_trace_callback(lambda sql: sql_threads.append(threading.get_ident()))
    real_prepare, real_write = files.prepare, files.write_prepared
    worker_threads = []

    def prepare(value):
        worker_threads.append(threading.get_ident())
        return real_prepare(value)

    def write(directory, prepared):
        worker_threads.append(threading.get_ident())
        return real_write(directory, prepared)

    monkeypatch.setattr(files, 'prepare', prepare)
    monkeypatch.setattr(files, 'write_prepared', write)
    try:
        value = {'encoding': 'base64', 'data': 'synthetic' * 100_000}
        reference = await files.put_async(db, value)
        expected = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        assert reference == {'$resource': hashlib.sha256(expected).hexdigest(), 'bytes': len(expected)}
        source = files.detach(db, reference['$resource'])
        db.commit()
        result = await asyncio.to_thread(files.read_detached, source, max_bytes=len(expected))
        assert result == value
        assert sql_threads and set(sql_threads) == {owner}
        assert worker_threads and owner not in worker_threads
        assert json.loads(db.execute('SELECT value FROM state_resources').fetchone()[0]) == {'$blob': reference['$resource']}
    finally:
        db.close()


@pytest.mark.parametrize('outer_transaction', [False, True])
async def test_write_failure_rolls_back_only_owned_scope(tmp_path, monkeypatch, outer_transaction):
    db = database(tmp_path / 'state.sqlite3')
    if outer_transaction:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO state_resources VALUES (?, ?)', ('kept', '{}'))

    def fail(*args):
        raise OSError('synthetic write failure')

    monkeypatch.setattr(files, 'write_prepared', fail)
    try:
        with pytest.raises(OSError, match='write failure'):
            await files.put_async(db, {'data': 'failed'})
        rows = db.execute('SELECT id FROM state_resources').fetchall()
        assert rows == ([('kept',)] if outer_transaction else [])
        assert db.in_transaction == outer_transaction
    finally:
        db.close()


async def test_repeated_cancellation_settles_write_before_gc_or_rollback(tmp_path, monkeypatch):
    db = database(tmp_path / 'state.sqlite3')
    collector = database(tmp_path / 'state.sqlite3')
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    real = files.write_prepared

    def blocked(directory, prepared):
        started.set()
        assert release.wait(5)
        try:
            return real(directory, prepared)
        finally:
            finished.set()

    monkeypatch.setattr(files, 'write_prepared', blocked)
    task = asyncio.create_task(files.put_async(db, {'data': 'owned write'}))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        # A reconciliation writer cannot enter while the file write is owned.
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            files.sweep_unindexed(collector, {})
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and db.in_transaction and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set() and not db.in_transaction
        assert db.execute('SELECT COUNT(*) FROM state_resources').fetchone()[0] == 0
        iterator, result = files.sweep_unindexed(collector, {})
        assert iterator is None and result['removed'] == 1
        assert not list((tmp_path / 'artifacts').glob('*.json'))
    finally:
        release.set()
        if not task.done():
            try:
                await task
            except BaseException:
                pass
        collector.close()
        db.close()


async def test_conflicting_file_and_detached_corruption_fail_closed(tmp_path):
    db = database(tmp_path / 'state.sqlite3')
    try:
        prepared = files.prepare({'data': 'original'})
        directory = files.root(db)
        directory.mkdir()
        path = directory / (prepared.identity + '.json')
        path.write_text('wrong size')
        with pytest.raises(ValueError, match='conflicts'):
            await files.put_async(db, {'data': 'original'})
        assert path.read_text() == 'wrong size'
        path.unlink()
        reference = await files.put_async(db, {'data': 'original'})
        db.commit()
        source = files.detach(db, reference['$resource'])
        path.write_text('{"data":"modified"}')
        with pytest.raises(ValueError, match='identity'):
            await asyncio.to_thread(files.read_detached, source)
        path.unlink()
        path.symlink_to(tmp_path / 'missing.json')
        with pytest.raises(OSError):
            await asyncio.to_thread(files.read_detached, source)
    finally:
        db.close()


@pytest.mark.parametrize('in_memory', [False, True])
async def test_worker_mark_retains_large_media_nested_edges_and_unrelated_roots(tmp_path, monkeypatch, in_memory):
    db = database(':memory:' if in_memory else tmp_path / 'mark.sqlite3')
    owner = threading.get_ident()
    threads = []
    real = files.detached_references
    monkeypatch.setattr(files, 'detached_references',
                        lambda source: (threads.append(threading.get_ident()), real(source))[1])
    try:
        nested = files.put(db, {'data': 'nested source'})
        # Imported/untrusted media-shaped content is NOT a leaf.
        media = files.put(db, {'encoding': 'base64', 'data': 'x' * 2_000_000, 'nested': [nested]})
        unrelated = files.put(db, {'data': 'retained elsewhere'})
        stale = files.put(db, {'data': 'genuine garbage'})
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        marked = await files.marked_references_async(db, {'outputs': [media], 'other': unrelated})
        assert marked == {ref['$resource'] for ref in (nested, media, unrelated)}
        assert files.prune_marked(db, marked) == [stale['$resource']]
        db.commit()
        files.remove_files(db, [stale['$resource']])
        assert threads and owner not in threads
        # A missing or damaged retained body can hide edges. Keep all resources.
        db.execute('UPDATE state_resources SET value=? WHERE id=?', ('{"$blob":"' + '0' * 64 + '"}', media['$resource']))
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        assert await files.marked_references_async(db, {'output': media}) is None
        db.rollback()
        assert db.execute('SELECT COUNT(*) FROM state_resources').fetchone()[0] == 3
    finally:
        db.close()


async def test_cancelled_mark_settles_before_writer_exclusion_releases(tmp_path, monkeypatch):
    db = database(tmp_path / 'cancel-mark.sqlite3')
    competitor = database(tmp_path / 'cancel-mark.sqlite3')
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    real = files.detached_references

    def blocked(source):
        started.set()
        try:
            assert release.wait(5)
            return real(source)
        finally:
            finished.set()

    reference = files.put(db, {'data': 'retained'})
    db.commit()
    monkeypatch.setattr(files, 'detached_references', blocked)

    async def sweep():
        db.execute('BEGIN IMMEDIATE')
        try:
            return await files.marked_references_async(db, reference)
        finally:
            db.rollback()

    task = asyncio.create_task(sweep())
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and db.in_transaction and not finished.is_set()
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            competitor.execute('BEGIN IMMEDIATE')
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set() and not db.in_transaction
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        competitor.close()
        db.close()


async def test_mark_refuses_changed_snapshot_and_service_close_settles(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from amplifier_web.storage_migration import maintenance, close_maintenance
    db = database(tmp_path / 'lifecycle.sqlite3')
    reference = files.put(db, {'data': 'retained'})
    db.commit()
    state = {'retained': reference}
    started, release = threading.Event(), threading.Event()
    real = files.detached_references

    def blocked(source):
        started.set()
        assert release.wait(5)
        return real(source)

    monkeypatch.setattr(files, 'detached_references', blocked)
    service = SimpleNamespace(db=db, _state=state, closed=False, lock=asyncio.Lock())
    maintenance(service)
    task = service._storage_maintenance_task
    try:
        assert await asyncio.to_thread(started.wait, 5)
        maintenance(service)
        assert service._storage_maintenance_task is task  # no unowned loop/fanout
        state['new'] = {'$resource': '0' * 64}  # deliberate violation of caller lock
        release.set()
        await task
        assert service._resource_scan_status['blocked']
        assert db.execute('SELECT COUNT(*) FROM state_resources').fetchone()[0] == 1
        service.closed = True
        await close_maintenance(service)
        assert service._storage_maintenance_task is None and not db.in_transaction
    finally:
        release.set()
        await close_maintenance(service)
        db.close()

@pytest.mark.parametrize('change', ['none', 'new-root', 'changed-index', 'cancel'])
async def test_background_sweep_releases_navigation_and_writer_during_body_reads(tmp_path, monkeypatch, change):
    from types import SimpleNamespace
    from amplifier_web.storage_migration import maintenance, close_maintenance
    db = database(tmp_path / 'responsive.sqlite3')
    competitor = database(tmp_path / 'responsive.sqlite3')
    keep = files.put(db, {'retained': True})
    garbage = files.put(db, {'old': True})
    db.commit()
    state = {'keep': keep}
    service = SimpleNamespace(db=db, _state=state, closed=False, lock=asyncio.Lock())
    entered, release = threading.Event(), threading.Event()
    original = files.detached_references
    def blocked(source):
        entered.set()
        assert release.wait(5)
        return original(source)
    monkeypatch.setattr(files, 'detached_references', blocked)
    maintenance(service)
    task = service._storage_maintenance_task
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        # An unrelated command can acquire the app lock while the file read is
        # still blocked, and another connection is not denied writer access.
        await asyncio.wait_for(service.lock.acquire(), 1)
        try:
            assert not db.in_transaction
            competitor.execute('BEGIN IMMEDIATE')
            competitor.rollback()
            if change == 'new-root':
                state['new'] = files.put(db, {'nested': garbage})
                db.commit()
            elif change == 'changed-index':
                db.execute('DELETE FROM state_resources WHERE id=?', (keep['$resource'],))
                db.commit()
            elif change == 'cancel':
                task.cancel()
        finally:
            service.lock.release()
        release.set()
        await asyncio.gather(task, return_exceptions=change == 'cancel')
        exists = db.execute('SELECT 1 FROM state_resources WHERE id=?', (garbage['$resource'],)).fetchone()
        assert bool(exists) is (change != 'none')
        assert not db.in_transaction
    finally:
        release.set()
        await close_maintenance(service)
        competitor.close()
        db.close()


def test_reference_walk_deduplicates_shared_graph_without_loading_cold_fields(monkeypatch):
    from types import MappingProxyType
    from amplifier_web.cold_display import ColdRecord
    def unexpected(*args):
        pytest.fail('Walking retained roots must not hydrate cold fields')
    monkeypatch.setattr('amplifier_web.cold_display.load', unexpected)
    ref = {'$resource': 'a' * 64}
    shared = {'nested': [MappingProxyType(ref)]}
    shared['self'] = shared
    row = ColdRecord({'id': 'cold', '_coldFields': {'messages': ref}}, None)
    assert set(files.references([shared, shared, row])) == {'a' * 64}
    assert 'messages' not in dict.keys(row)


def test_edge_metadata_reuses_only_exact_unchanged_files(tmp_path, monkeypatch):
    db = database(tmp_path / 'edges.sqlite3')
    nested = files.put(db, {'child': True})
    ref = files.put(db, {'nested': [nested], 'data': 'x' * 200_000})
    db.commit()
    source = files.detach(db, ref['$resource'])
    cache = files.ReferenceEdges()
    original = files.detached_references
    reads = []
    monkeypatch.setattr(files, 'detached_references', lambda row: (reads.append(row.identity), original(row))[1])
    try:
        assert cache.read(source) == (nested['$resource'],)
        assert cache.read(source) == (nested['$resource'],)
        assert reads == [source.identity] and cache.hits == 1
        path = source.directory / (source.identity + '.json')
        # An identical replacement is valid, but its new inode must be read.
        replacement = path.with_suffix('.replacement')
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        assert cache.read(source) == (nested['$resource'],)
        assert len(reads) == 2 and len(cache.entries) == 1
        assert cache.edge_count == 1
        # The cache retains only the small file pointer, signature and edges.
        assert all(len(key.indexed) < 128 for key in cache.entries)
        assert cache.entries[source][1] == (nested['$resource'],)
    finally:
        db.close()


@pytest.mark.parametrize('change', ['missing', 'symlink', 'same-size-corruption', 'changed-index'])
async def test_cached_graph_uncertainty_preserves_all_garbage(tmp_path, monkeypatch, change):
    from types import SimpleNamespace
    from amplifier_web.storage_migration import _maintenance_async, close_maintenance
    from amplifier_web.performance_diagnostics import PerformanceDiagnostics
    db = database(tmp_path / 'cached.sqlite3')
    keep = files.put(db, {'retained': 'original'})
    db.commit()
    service = SimpleNamespace(db=db, _state={'keep': keep}, closed=False,
                              lock=asyncio.Lock(), performance_diagnostics=PerformanceDiagnostics())
    try:
        await _maintenance_async(service)
        misses = service._resource_edges.misses
        await _maintenance_async(service)
        assert service._resource_edges.misses == misses and service._resource_edges.hits == 1
        # Add truly unreferenced bytes only after the warm graph was established.
        garbage = files.put(db, {'garbage': True})
        db.commit()
        source = files.detach(db, keep['$resource'])
        path = source.directory / (source.identity + '.json')
        if change == 'missing':
            path.unlink()
        elif change == 'symlink':
            original = path.read_bytes()
            path.unlink()
            outside = tmp_path / 'outside.json'
            outside.write_bytes(original)
            path.symlink_to(outside)
        elif change == 'same-size-corruption':
            before = path.stat()
            path.write_bytes(path.read_bytes().replace(b'original', b'modified'))
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        else:
            db.execute('UPDATE state_resources SET value=? WHERE id=?',
                       (json.dumps({'$blob': 'f' * 64}), keep['$resource']))
            db.commit()
        await _maintenance_async(service)
        assert service._resource_scan_status['blocked']
        assert db.execute('SELECT 1 FROM state_resources WHERE id=?', (garbage['$resource'],)).fetchone()
        assert (files.root(db) / (garbage['$resource'] + '.json')).exists()
        snapshot = service.performance_diagnostics.snapshot()
        assert snapshot['operations']['storage.gc.roots']['count'] >= 4
        assert snapshot['counters']['storage.gc.edgeHits'] == 1
    finally:
        await close_maintenance(service)
        if getattr(service, '_resource_scan', None):
            service._resource_scan.close()
        db.close()


def test_edge_metadata_bounds_and_inline_payloads_are_not_cached(tmp_path):
    db = database(tmp_path / 'bounded.sqlite3')
    cache = files.ReferenceEdges(max_entries=2, max_edges=2)
    try:
        children = [files.put(db, {'child': index}) for index in range(3)]
        parents = [files.put(db, {'nested': child}) for child in children]
        wide = files.put(db, {'nested': children})
        db.commit()
        for parent in parents:
            cache.read(files.detach(db, parent['$resource']))
            assert len(cache.entries) <= 2 and cache.edge_count <= 2
        assert set(cache.read(files.detach(db, wide['$resource']))) == {row['$resource'] for row in children}
        assert len(cache.entries) == cache.edge_count == 2  # Oversized edge list is not retained.
        prepared = files.prepare({'large inline': 'x' * 200_000})
        inline = files.Detached(None, prepared.identity, prepared.text)
        assert cache.read(inline) == cache.read(inline) == ()
        assert inline not in cache.entries and len(cache.entries) == 2
    finally:
        db.close()


def test_invalid_nested_edge_is_never_cached(tmp_path):
    db = database(tmp_path / 'invalid-edge.sqlite3')
    try:
        reference = files.put(db, {'nested': {'$resource': 'unbounded-invalid-reference'}})
        db.commit()
        cache = files.ReferenceEdges()
        with pytest.raises(ValueError, match='Invalid retained resource'):
            cache.read(files.detach(db, reference['$resource']))
        assert not cache.entries and cache.edge_count == 0
    finally:
        db.close()


def test_root_budget_counts_wide_scalar_fields_without_weakening_full_walk():
    value = {'wide': list(range(1000)), 'retained': {'$resource': 'a' * 64}}
    budget = files.ReferenceBudget(seconds=60, fields=100)
    with pytest.raises(files.ReferenceBudgetExceeded) as error:
        list(files.references(value, budget=budget))
    assert error.value.reason == 'fields' and budget.fields == 101
    assert set(files.references(value)) == {'a' * 64}
