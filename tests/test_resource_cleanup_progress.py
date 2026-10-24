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


@pytest.mark.parametrize('late_root', [False, True])
async def test_changed_durable_roots_are_parsed_then_revalidated_under_writer_exclusion(tmp_path, monkeypatch, late_root):
    app, keep, garbage = setup(tmp_path)
    db = app.db
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
        if late_root:
            competitor.execute('UPDATE client_views SET value=?', (json.dumps({'keep': keep, 'new': garbage}),))
            competitor.commit()
        final_release.set()
        await task
        assert app._resource_scan_status['blocked'] is late_root
        assert bool(db.execute('SELECT 1 FROM state_resources WHERE id=?', (garbage['$resource'],)).fetchone()) is late_root
        assert not db.in_transaction
    finally:
        body_release.set()
        final_release.set()
        await asyncio.gather(task, return_exceptions=True)
        competitor.close()
        await close(app)


async def test_background_deletion_is_batched_and_repeated_saves_finish_backlog(tmp_path, monkeypatch):
    app, keep, _ = setup(tmp_path)
    app._state['keep'] = keep
    for i in range(6):
        files.put(app.db, {'obsolete': i})
    app.db.commit()
    monkeypatch.setattr(storage_migration, 'BACKGROUND_PRUNE_LIMIT', 2)
    try:
        counts = []
        for _ in range(4):
            app._last_storage_sweep = -1000
            storage_migration.maintenance(app)
            await app._storage_maintenance_task
            counts.append(app._resource_scan_status['indexedRemoved'])
        assert counts == [2, 2, 2, 1]
        assert app.db.execute('SELECT id FROM state_resources').fetchall() == [(keep['$resource'],)]
        assert len(list(files.root(app.db).glob('*.json'))) == 1
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
    app, keep, garbage = setup(tmp_path)
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
