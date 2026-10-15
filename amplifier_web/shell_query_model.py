"""Read-only catalog queries in a disposable process, using the canonical SQL.

Inputs are host-authorized scalar projections, never a client-supplied database
path or SQL statement. The worker owns and closes both pinned database views.
"""
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import time


def file_id(path):
    stat = Path(path).stat()
    return [stat.st_dev, stat.st_ino]


def query_collections(operation):
    return ('workspace',) if operation == 'workspace_counts' else (
        ('root_navigation', 'workspace') if operation == 'pinned_navigation' else ('root_navigation',))


def durable_stamp(db, namespace=None, operation='chats', live=None):
    collections = query_collections(operation)
    local = [list(db.execute('SELECT revision FROM history_observation_revisions WHERE collection=?',
                            (name,)).fetchone() or (0,)) for name in collections]
    from .native_query_receipts import receipt
    native = [receipt(db, namespace, name, live or {}) for name in collections] if namespace is not None else []
    return [local, native]


class ShellQueryModel:
    def __init__(self, cache_home):
        self.home = Path(cache_home).resolve()

    def project(self, inputs, *, check_budget=lambda: None):
        from .history_directory import HistoryDirectory
        from . import history_directory_query as query
        # The sole application database belongs to this host's cache home.
        # Native catalog paths are supplied only by its existing directory owner.
        app_path, native = self.home / 'app.sqlite3', inputs['native']
        def source_files():
            return [file_id(app_path), file_id(native['path']) if native['path'] else None]
        if source_files() != inputs['files']:
            return {'status': 'retry', 'reason': 'source_changed'}
        db = sqlite3.connect(app_path.as_uri() + '?mode=ro', uri=True, timeout=.1)
        started, cpu = time.monotonic(), time.thread_time()
        deadline = started + 10
        steps = 0
        def progress():
            nonlocal steps
            steps += 10000
            check_budget()
            return int(time.monotonic() > deadline)
        try:
            db.execute('PRAGMA query_only=ON')
            if native['path'] is not None:
                db.execute('ATTACH DATABASE ? AS history_native',
                    (Path(native['path']).as_uri() + '?mode=ro',))
            db.set_progress_handler(progress, 10000)
            db.execute('BEGIN')
            receipt = durable_stamp(db, native['namespace'], inputs['operation'], inputs['live'])
            if receipt != inputs['durable'] or source_files() != inputs['files']:
                return {'status': 'retry', 'reason': 'source_changed'}
            state = inputs['state']
            from .performance_diagnostics import PerformanceDiagnostics
            diagnostics = PerformanceDiagnostics()
            owner = SimpleNamespace(db=db, _state=state, performance_diagnostics=diagnostics)
            directory = HistoryDirectory(owner, db=db, snapshot=True)
            directory._native_path, directory._native_namespace = native['path'], native['namespace']
            directory._live_values = inputs['live']
            directory._live_notices = inputs['notices']
            directory._frozen_state = state
            operation, args = inputs['operation'], inputs['args']
            if operation == 'chats':
                value = query.chats(directory, state, section=args['section'])
            elif operation == 'workspace_counts':
                value = query.workspace_counts(directory, state, archive=args['archive'])
            elif operation == 'pinned_navigation':
                value = query.pinned_navigation(directory, state, args['workspaces'])
            else:
                raise ValueError('Unknown directory read')
            check_budget()
            return {'status': 'complete', 'durable': receipt, 'value': value,
                'stats': {'vmSteps': steps, 'decodedRows': diagnostics.counters.get('directory.rows.decoded', 0),
                    'elapsedMs': (time.monotonic()-started)*1000, 'threadCpuMs': (time.thread_time()-cpu)*1000}}
        finally:
            db.close()
