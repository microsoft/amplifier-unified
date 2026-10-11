"""Synthetic storage ownership, cancellation and reconciliation checks."""
import asyncio
import hashlib
import json
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