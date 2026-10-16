"""Disposable read-only discovery for automatic names, outside the host loop.

Only a bounded candidate page returns. The host still owns eligibility checks,
attempt receipts and naming; this worker never admits or executes a chat.
"""
import asyncio
import sqlite3
import sys

from .history_projection_process import ProcessProjector
from .performance_diagnostics import count, measure
from .shell_query_model import durable_stamp, file_id
from .shell_query_worker import INPUT_LIMIT, OUTPUT_LIMIT


def candidate_page(directory, state, cursor):
    where = ("history_managed AND native_project IS NOT NULL AND session_kind='root' "
        "AND COALESCE(auto_name,1) AND available "
        "AND COALESCE(name_source,'') NOT IN ('manual','generated') "
        "AND COALESCE(title_source,'') NOT IN ('manual','generated')")
    sql, params = directory.effective(state)
    total = directory.db.execute(sql + ' SELECT COUNT(*) FROM directory_rows WHERE ' + where, params).fetchone()[0]
    start, size = cursor % max(1, total), min(32, total)
    order = 'COALESCE(recent,created,0) DESC,ordinal,id'
    window = directory.select(where, limit=size, offset=start, order=order, state=state)
    if len(window) < size:
        window += directory.select(where, limit=size-len(window), order=order, state=state)
    return window


def _stamp(service, directory):
    return (service._state['revision'], service.db.total_changes,
            directory._native_path, directory._native_namespace,
            service.db.execute('PRAGMA main.data_version').fetchone()[0],
            durable_stamp(directory.db, directory._native_namespace,
                          'naming_candidates', directory._live_values),
            [file_id(service.data_dir / 'app.sqlite3'),
             file_id(directory._native_path) if directory._native_path else None])


async def read_candidates(service, cursor, *, projector_factory=ProcessProjector):
    """No main-loop SQL scan, shared connection, or child survives this read."""
    if service.db.in_transaction:
        return []
    directory = service.directory
    # Freeze only admitted scalar facts. No workspace registry or history body
    # belongs in this background read's input.
    directory._sync_live(service._state)
    from .shell_query_owner import InputBudget, InputLimit
    try:
        stamp = _stamp(service, directory)
        inputs = InputBudget().copy({
            'operation': 'naming_candidates', 'args': {'cursor': cursor},
            'live': directory._live_values, 'notices': [row[:5] for row in directory._live_notices],
            'native': {'path': directory._native_path, 'namespace': directory._native_namespace},
            'durable': stamp[-2], 'files': stamp[-1],
            'state': {key: service._state[key] for key in (
                'attentionRead', 'hiddenNativeSessions', 'hiddenNativeWorkspaces') if key in service._state}})
    except (InputLimit, OSError, sqlite3.Error):
        return []
    projector = projector_factory(service.data_dir,
        _command=(sys.executable, '-m', 'amplifier_web.shell_query_worker'),
        _timeout=12, _input_limit=INPUT_LIMIT, _output_limit=OUTPUT_LIMIT,
        _spool_limit=16*1024*1024, _max_jobs=1)
    try:
        with measure(service, 'naming.candidates.read'):
            result = await projector.project(inputs, key='automatic-naming-candidates')
        if (service.closed or service.db.in_transaction or _stamp(service, directory) != stamp
                or result.get('status') != 'complete' or result.get('durable') != stamp[-2]):
            count(service, 'naming.candidates.deferred')
            return []
        for field in ('vmSteps', 'decodedRows'):
            value = result.get('stats', {}).get(field, 0)
            if type(value) is int and value >= 0:
                count(service, 'naming.candidates.' + field, value)
        return result['value']
    except asyncio.CancelledError:
        raise
    except (OSError, sqlite3.Error, ValueError, KeyError):
        return []
    finally:
        # The history owner's canceled loop also comes through here. EOF and
        # exact child ownership are handled by the shared bounded transport.
        await projector.close()
