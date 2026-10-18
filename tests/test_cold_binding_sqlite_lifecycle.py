"""Measure the existing cold-binding fence; never change or bypass it.

Owned synthetic databases only. Diagnostics supplies its real schema/WAL setup
and _db writer/commit/close lifecycle; start()/background writers never run.
Physical churn is an observation, not a required verdict. These controls do not
stand in for the unchanged cold/HTTP/regular-work regressions selected with them.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
from types import SimpleNamespace

import pytest

from amplifier_web.diagnostics import Diagnostics
from amplifier_web import event_window
from amplifier_web.work_windows import WorkWindows


RECORD_ID = 'owned-synthetic-record'
QUERY = ('SELECT event,stream,session,workspace,data FROM records '
         'WHERE id=? AND length(data)<=4096')
ALL_ROWS = 'SELECT id,event,stream,session,workspace,data FROM records ORDER BY seq LIMIT 3'
OLD_DATA = '{"fixture":"owned-synthetic-0"}'
NEW_DATA = '{"fixture":"owned-synthetic-1","changed":true}'
OLD_ROW = ['tool:post', 'tools', 'synthetic-session', 'synthetic-workspace', OLD_DATA]
NEW_ROW = [*OLD_ROW[:-1], NEW_DATA]


def _observe(home, record, stage):
    """Use exactly the product's dev/inode/size/mtime/ctime stamp; no SQL here."""
    revision = event_window.binding_revision(home)
    observation = {'stage': stage, 'revision': revision,
                   'dbExists': (home / 'diagnostics/events.sqlite3').exists(),
                   'walExists': (home / 'diagnostics/events.sqlite3-wal').exists()}
    record['stages'].append(observation)
    if revision is None:
        record['gaps'].append({'stage': stage, 'reason': 'binding_revision_unavailable'})
    return revision


def _row(cursor):
    value = cursor.fetchone()
    return list(value) if value is not None else None


def _read_once(home, database, record, ordinal):
    cycle = {'ordinal': ordinal, 'stages': [], 'gaps': [], 'connectionClosed': False}
    record['reads'].append(cycle)
    db = None
    try:
        before = _observe(home, cycle, 'before-connect')
        db = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.05)
        _observe(home, cycle, 'after-connect')
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        _observe(home, cycle, 'after-BEGIN')
        cycle['row'] = _row(db.execute(QUERY, (RECORD_ID,)))
        _observe(home, cycle, 'after-first-records-query')
        cycle['allRows'] = [list(row) for row in db.execute(ALL_ROWS)]
        cycle['journalMode'] = db.execute('PRAGMA journal_mode').fetchone()[0]
    finally:
        if db is not None:
            db.close()
            cycle['connectionClosed'] = True
        after = _observe(home, cycle, 'after-close')
    cycle['physicalFenceChanged'] = before != after
    cycle['changes'] = [
        {'from': a['stage'], 'to': b['stage'], 'changed': a['revision'] != b['revision']}
        for a, b in zip(cycle['stages'], cycle['stages'][1:])
    ]
    return cycle


@pytest.fixture
async def lifecycle(tmp_path, request):
    home = tmp_path / 'app'
    service = SimpleNamespace(data_dir=home, state={'sessions': []},
                              default_workspace=str(tmp_path / 'workspace'))
    record = {
        'schema': 1, 'case': request.node.name,
        'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
        'stampFields': ['st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'],
        'reader': {'uriMode': 'ro', 'queryOnly': True, 'timeout': .05,
                   'query': QUERY, 'commitOrWriteInReader': False},
        'sourceSha256': {
            name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for name, module in [('event_window', event_window)]
        },
        'stages': [], 'reads': [], 'gaps': [], 'complete': False,
        'backgroundWriterStarted': False,
    }
    diagnostics = None
    try:
        diagnostics = Diagnostics(service)
        assert diagnostics.storage_ready and not diagnostics.storage_error
        assert diagnostics.task is None and diagnostics.pending == []
        assert diagnostics.delivery_tasks == {}
        _observe(home, record, 'after-Diagnostics-initialize-and-close')
        yield SimpleNamespace(home=home, service=service, diagnostics=diagnostics, record=record)
    finally:
        try:
            if diagnostics is not None:
                record['noPendingWritesBeforeCleanup'] = diagnostics.pending == []
                record['backgroundTaskAbsentBeforeCleanup'] = diagnostics.task is None
                try:
                    await diagnostics.close()
                    record['diagnosticsClosed'] = diagnostics.stopping and diagnostics.task is None
                except Exception as error:
                    record['gaps'].append({'stage': 'cleanup', 'reason': type(error).__name__})
                    raise
        finally:
            # Save even if cleanup raises; pytest retains that genuine failure.
            # Ordinary full-suite runs keep this bounded evidence in pytest tmp.
            destination = Path(os.environ.get('UNIFIED_COLD_BINDING_DIAGNOSTIC_DIR', tmp_path / 'evidence'))
            destination.mkdir(parents=True, exist_ok=True)
            name = request.node.name.replace('[', '-').replace(']', '') + '.json'
            raw = json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
            assert len(raw.encode()) <= 32768, 'Synthetic diagnostic exceeded its bound'
            (destination / name).write_text(raw)


def _seed(lifecycle):
    # The actual writer context commits and closes, as Diagnostics._persist does.
    with lifecycle.diagnostics._db() as db:
        db.execute('INSERT INTO records(id,at,stream,session,workspace,event,data) VALUES(?,?,?,?,?,?,?)',
                   (RECORD_ID, 1.0, 'tools', 'synthetic-session', 'synthetic-workspace', 'tool:post', OLD_DATA))
    _observe(lifecycle.home, lifecycle.record, 'after-populated-writer-close')


@pytest.mark.parametrize('table', ['populated', 'empty'])
@pytest.mark.asyncio
async def test_no_writer_sqlite_read_lifecycle(lifecycle, table):
    _seed(lifecycle)
    if table == 'empty':
        # Matches the failed missing-index control's DELETE, not missing DB.
        with lifecycle.diagnostics._db() as db:
            db.execute('DELETE FROM records')
        _observe(lifecycle.home, lifecycle.record, 'after-empty-writer-close')
    expected = OLD_ROW if table == 'populated' else None
    expected_rows = [[RECORD_ID, *OLD_ROW]] if table == 'populated' else []
    for ordinal in (1, 2):
        cycle = _read_once(lifecycle.home, lifecycle.diagnostics.path, lifecycle.record, ordinal)
        cycle['logicalRowsEqualExpected'] = cycle['row'] == expected and cycle['allRows'] == expected_rows
        assert cycle['connectionClosed'] and cycle['journalMode'] == 'wal'
        assert cycle['logicalRowsEqualExpected']
        assert not cycle['gaps']
    a, b = lifecycle.record['reads']
    lifecycle.record['identicalReadLogicalEquality'] = (a['row'], a['allRows']) == (b['row'], b['allRows'])
    assert lifecycle.record['identicalReadLogicalEquality']
    assert lifecycle.diagnostics.pending == [] and lifecycle.diagnostics.task is None
    lifecycle.record['complete'] = True


@pytest.mark.asyncio
async def test_controlled_writer_cannot_redeem_old_binding_revision(lifecycle):
    _seed(lifecycle)
    record, home = lifecycle.record, lifecycle.home
    reader = None
    try:
        _observe(home, record, 'before-old-reader-connect')
        reader = sqlite3.connect(lifecycle.diagnostics.path.as_uri() + '?mode=ro', uri=True, timeout=.05)
        reader.execute('PRAGMA query_only=ON')
        reader.execute('BEGIN')
        _observe(home, record, 'after-old-reader-BEGIN')
        record['oldRow'] = _row(reader.execute(QUERY, (RECORD_ID,)))
        before = _observe(home, record, 'after-old-reader-first-records-query')
        assert record['oldRow'] == OLD_ROW and before is not None
        page = {'_bindingRevision': before}
        predicate_owner = SimpleNamespace(service=lifecycle.service)
        record['currentBeforeWriter'] = WorkWindows._binding_current(predicate_owner, page)
        assert record['currentBeforeWriter'] is True
        # One deliberate synchronous commit. No timer or background writer.
        with lifecycle.diagnostics._db() as writer:
            writer.execute('UPDATE records SET data=? WHERE id=?', (NEW_DATA, RECORD_ID))
        after = _observe(home, record, 'after-controlled-writer-commit-and-close')
        record['oldTransactionRowAfterCommit'] = _row(reader.execute(QUERY, (RECORD_ID,)))
        record['currentAfterWriter'] = WorkWindows._binding_current(predicate_owner, page)
        record['writerChangedPhysicalFence'] = after != before
        assert record['oldTransactionRowAfterCommit'] == OLD_ROW
        assert record['writerChangedPhysicalFence']
        assert record['currentAfterWriter'] is False, 'Existing predicate accepted stale attestation dependency'
        # The new row gets a new read, never the old row with a newer stamp.
        fresh = _read_once(home, lifecycle.diagnostics.path, record, 1)
        assert fresh['row'] == NEW_ROW and fresh['allRows'] == [[RECORD_ID, *NEW_ROW]]
        assert fresh['connectionClosed'] and not fresh['gaps']
        record['freshRowMatchesControlledCommit'] = True
        record['complete'] = True
    finally:
        if reader is not None:
            reader.close()
            record['oldReaderClosed'] = True
        _observe(home, record, 'after-old-reader-close')
