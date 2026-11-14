"""Actual owner-graph fixture, not a claim of legacy provenance availability."""
import asyncio
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from amplifier_web.storage_optimize import SavedStorageOptimize
from amplifier_web.operation_requests import OperationRequests
from amplifier_operations import OperationJournal
from amplifier_web.storage_optimize_capture import apply, apply_combined, operation_directory
from amplifier_web.storage_optimize_retention import file_hash
from amplifier_web.session_files import capture_dir
from amplifier_web.capture_index import CaptureDrift, index_shared


@pytest.mark.parametrize('combined,lost_expiry_ack', [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize('layout', ['records', 'leading-blank', 'between-blank', 'trailing-blank', 'unterminated'])
async def test_actual_ci_foundation_private_diagnostic_apply_rebind_and_resume(tmp_path, combined, lost_expiry_ack, monkeypatch, layout):
    from PIL import Image
    from amplifier_foundation.session.media import MediaPolicy, SessionMediaStore
    from amplifier_foundation.session.media_conversion_owner import CaptureMediaOwner, BINDING, diagnostic_image_ref
    from context_intelligence.optimize import StagedImage
    from amplifier_web.runtime import RuntimeManager
    png = io.BytesIO()
    Image.new('RGB', (3, 2), 'red').save(png, format='PNG')
    pixels = png.getvalue()
    directory = capture_dir(str(tmp_path), 'root')
    directory.mkdir(parents=True)
    (directory / 'metadata.json').write_text(json.dumps(
        {'format': 'context-intelligence', 'version': '1.0.0'}))
    path = directory / 'events.jsonl'
    record = {'event': 'tool:post', 'timestamp': '2020-01-01T00:00:00Z',
        'event_id': 'original', 'session_id': 'root',
        'data': {'event_id': 'original', 'tool_call_id': 'original-call', 'status': 'failed',
            'result': {'success': False, 'content': [{'type': 'image', 'source': {
                'type': 'base64', 'media_type': 'image/png', 'data': base64.b64encode(pixels).decode()}}]}}}
    path.write_text(json.dumps(record) + '\n')
    if combined:
        body = b'{"old":"provider diagnostic"}'
        digest = hashlib.sha256(body).hexdigest()
        (directory / 'provider-payloads').mkdir()
        sidecar = directory / 'provider-payloads' / (digest + '.json')
        sidecar.write_bytes(body)
        with path.open('a') as stream:
            stream.write(json.dumps({'event': 'llm:request', 'timestamp': '2020-01-01T00:00:00Z',
                'session_id': 'root', 'data': {'raw': 'old wire diagnostic' * 100,
                'provider_payloads': {'raw': {'sha256': digest, 'bytes': len(body),
                                            'redacted': True, 'truncated': False}}}}) + '\n')
    payload = path.read_bytes()
    if layout == 'leading-blank':
        payload = b' \t\r\n' + payload
    elif layout == 'between-blank':
        first, rest = payload.split(b'\n', 1)
        payload = first + b'\n\n' + rest
    elif layout == 'trailing-blank':
        payload += b'\n'
    elif layout == 'unterminated':
        payload = payload.rstrip(b'\n')
    path.write_bytes(payload)
    original = payload
    diagnostic_path = tmp_path / 'diagnostics.sqlite3'
    @contextmanager
    def db():
        connection = sqlite3.connect(diagnostic_path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()
    with db() as connection:
        connection.executescript('CREATE TABLE captures(path TEXT PRIMARY KEY,offset INTEGER,inode INTEGER);'
            'CREATE TABLE records(id TEXT PRIMARY KEY,at REAL,stream TEXT,session TEXT,workspace TEXT,event TEXT,data TEXT);'
            'CREATE TABLE deliveries(record_id TEXT,status TEXT,payload TEXT);')
        indexed = index_shared(connection, [(str(tmp_path), 'root')], {'streams': ['tools', 'usage']})
        if indexed:
            connection.execute('INSERT INTO deliveries VALUES(?,?,?)', (indexed[0][0], 'accepted', '{"same":"outcome"}'))
    appdb = sqlite3.connect(':memory:')
    appdb.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT)')
    journal = OperationJournal(tmp_path / 'operations.sqlite3')
    source = {'id': 'root', 'workspace': str(tmp_path)}
    runtime = SimpleNamespace(_closed=False, _locks={}, _preparations={}, workers={},
                              _check_execution=lambda *args: None)
    runtime.stopped_boundary = lambda session: RuntimeManager.stopped_boundary(runtime, session)
    app = SimpleNamespace(db=appdb, lock=asyncio.Lock(), runtime=runtime,
        diagnostics=SimpleNamespace(_db=db, flush_lock=asyncio.Lock()),
        operations=SimpleNamespace(journal=journal, requests=OperationRequests(journal)),
        _session=lambda *args, **kwargs: source)
    controller = SavedStorageOptimize(app)
    app._saved_storage_optimize = controller
    operation = {'operationId': 'fixture-op', '_owner': 'user', '_authority': None,
        'revision': 0, 'status': 'applying', '_phase': 'apply',
        'summary': {'coverage': {}, 'bytes': {}},
        'cutoffs': {'requestBefore': '2026-01-01T00:00:00Z', 'responseBefore': None}}
    with journal.db:
        controller._save_operation(operation)
    row = {'owner': 'context-intelligence', 'sessionId': 'root', 'kind': 'root',
           'bytes': len(original), '_sourceId': 'root', '_path': str(path),
           '_sourceSha256': file_hash(path), 'status': 'eligible', '_ordinal': 1}
    opdir = operation_directory(row, operation)
    # Positive authority is an explicitly admitted synthetic original, NOT an
    # origin reconstructed from this fixture's event timestamp or old pixels.
    original_receipt = {'kind': 'upload', 'session_id': 'root', 'id': 'fixture-admitted-input-attachment',
                        'created_at': '2020-01-01T00:00:00Z', 'expires_at': None}
    admitted = SessionMediaStore(tmp_path / 'admitted-original', 'root', policy=MediaPolicy(100000, 1000, 2))
    ingested = await admitted.ingest({'stream': io.BytesIO(pixels), 'mime': 'image/png'},
        source_format='image-bytes', origin=original_receipt, policy=None)
    accepted = (await admitted.publish(ingested['staged_refs'], origin=original_receipt, disposition='retain'))['refs'][0]
    def origin_for(snapshot):
        assert snapshot['source_sha256'] == row['_sourceSha256']
        assert snapshot['sha256'] == accepted['sha256'] and snapshot['bytes'] == accepted['bytes']
        admitted.validate_ref(accepted, committed=False)
        return accepted['provenance']
    def checkpoint(receipt):
        assert app.diagnostics.flush_lock.locked()
        from amplifier_foundation.session import SharedSessionStore, SessionBusyError
        with pytest.raises(SessionBusyError):
            SharedSessionStore(str(tmp_path), 'root').acquire(app='checkpoint-competitor')
        assert receipt['session_id'] == 'root'
        assert receipt['sha256'] == file_hash(path)
        assert receipt['mapping_sha256'] == file_hash(opdir / 'records.jsonl')
        # Small single-record fixture; production owner verification is streamed.
        with path.open('rb') as stream:
            block = json.loads(stream.readline())['data']['result']['content'][0]
        ref = diagnostic_image_ref(block)
        assert json.loads((opdir / 'assets.jsonl').read_bytes())['owner']['ref'] == ref
        yield ref
    diagnostic = SessionMediaStore(tmp_path / 'private-diagnostic-only', 'root',
        policy=MediaPolicy(100000, 1000, 2), checkpoint_verifier=checkpoint)
    def allocated(ref):
        with diagnostic.resolve_ref(ref, committed=False) as stream:
            return os.fstat(stream.fileno()).st_size
    binding = CaptureMediaOwner(diagnostic, staged_factory=StagedImage, origin_for=origin_for,
        checkpoint_receipt_for=lambda manifest, source, sha256: {
            'session_id': 'root', 'sha256': sha256,
            'mapping_sha256': file_hash(opdir / 'records.jsonl')},
        new_asset_bytes_for=allocated, reader_ready=lambda name: name == BINDING,
        single_image_origin=lambda snapshot, provenance: provenance == accepted['provenance'])
    from context_intelligence.prune import operations as retention_operations
    actual_prune = retention_operations.prune_file
    import context_intelligence.optimize as optimize
    actual_commit = optimize.apply
    commits = []
    def observed_commit(*args, **kwargs):
        assert app.diagnostics.flush_lock.locked()
        commits.append(True)
        return actual_commit(*args, **kwargs)
    monkeypatch.setattr(optimize, 'apply', observed_commit)
    retention_results = []
    def observed_prune(*args, **kwargs):
        assert app.diagnostics.flush_lock.locked()
        from amplifier_foundation.session import SharedSessionStore, SessionBusyError
        with pytest.raises(SessionBusyError):
            SharedSessionStore(str(tmp_path), 'root').acquire(app='expiry-competitor')
        value = actual_prune(*args, **kwargs)
        retention_results.append((value.status, value.reason))
        if lost_expiry_ack and value.status == 'pruned':
            raise OSError('synthetic lost expiry result after actual replacement')
        return value
    monkeypatch.setattr(retention_operations, 'prune_file', observed_prune)
    try:
        if layout != 'records':
            with db() as connection:
                before = connection.execute('SELECT * FROM records').fetchall()
                deliveries = connection.execute('SELECT * FROM deliveries').fetchall()
                cursors = connection.execute('SELECT * FROM captures').fetchall()
            with pytest.raises(CaptureDrift, match='blank physical|unterminated'):
                await (apply_combined if combined else apply)(app, row, operation, media=binding)
            assert commits == retention_results == []
            assert path.read_bytes() == original
            assert (opdir / 'original').read_bytes() == original
            assert json.loads((opdir / 'journal.json').read_bytes())['state'] == 'staged'
            with db() as connection:
                assert connection.execute('SELECT * FROM records').fetchall() == before
                assert connection.execute('SELECT * FROM deliveries').fetchall() == deliveries
                assert connection.execute('SELECT * FROM captures').fetchall() == cursors
                assert connection.execute('SELECT count(*) FROM capture_maintenance').fetchone() == (0,)
            return  # No replacement/expiry or index repair was needed.
        if lost_expiry_ack:
            with pytest.raises(OSError, match='lost expiry result'):
                await apply_combined(app, row, operation, media=binding)
            saved = json.loads(journal.db.execute('SELECT value FROM storage_optimize_files').fetchone()[0])
            assert saved['stages'] == {'images': 'completed', 'diagnosticExpiry': 'pending'}
            assert len(retention_results) == 1
            result = await apply_combined(app, saved, operation, media=binding, resume=True)
            assert result['status'] == 'held' and len(retention_results) == 1
        else:
            result = await (apply_combined if combined else apply)(app, row, operation, media=binding)
            assert result['status'] == 'completed', retention_results
        assert (opdir / 'original').read_bytes() == original
        assert b'base64' not in path.read_bytes()
        with db() as connection:
            assert connection.execute('SELECT id FROM records ORDER BY rowid').fetchall() == [(item[0],) for item in indexed]
            assert connection.execute('SELECT * FROM deliveries').fetchall() == [(indexed[0][0], 'accepted', '{"same":"outcome"}')]
            assert index_shared(connection, [(str(tmp_path), 'root')], {'streams': ['tools']}) == []
        installed = path.read_bytes()
        if combined:
            assert result['stages'] == {'images': 'completed', 'diagnosticExpiry': 'held' if lost_expiry_ack else 'completed'}
            assert b'old wire diagnostic' not in installed and sidecar.read_bytes() == body
            assert result['_accounting']['unlinked'] == (None if lost_expiry_ack else 0)
            assert result['_accounting']['recoveryRetained'] is None
            assert result['stageAccounting']['images']['recoveryRetained'] == len(original)
            assert result['_accounting']['inlineRemoved'] is None if lost_expiry_ack else result['_accounting']['inlineRemoved'] > 0
            assert result['_accounting']['filesystemDelta'] is None
            controller._file(operation['operationId'], 1, result)
            saved = json.loads(journal.db.execute('SELECT value FROM storage_optimize_files').fetchone()[0])
            assert (await apply_combined(app, saved, operation, media=binding, resume=True))['status'] == 'held'
        else:
            assert (await apply(app, row, operation, media=binding, resume=True))['status'] == 'completed'
        assert path.read_bytes() == installed
    finally:
        diagnostic.close()
        admitted.close()
        journal.db.close()
        appdb.close()


@pytest.mark.parametrize('cancel,cleanup_failure', [(False, False), (True, False), (False, True)])
async def test_capture_lifetime_keeps_real_native_fence_and_thread_through_cleanup(tmp_path, monkeypatch, cancel, cleanup_failure):
    """Lifetime only: no invented media admission or installed-host claim."""
    from amplifier_foundation.session import SharedSessionStore, SessionBusyError
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.storage_optimize_capture import _capture_lifetime
    import amplifier_web.storage_optimize_media as media_module
    source = {'id': 'root', 'workspace': str(tmp_path)}
    runtime = SimpleNamespace(_closed=False, _locks={}, _preparations={}, workers={},
                              _check_execution=lambda *args: None)
    runtime.stopped_boundary = lambda session: RuntimeManager.stopped_boundary(runtime, session)
    journal = OperationJournal(tmp_path / 'operations.sqlite3')
    app = SimpleNamespace(lock=asyncio.Lock(), runtime=runtime,
        diagnostics=SimpleNamespace(flush_lock=asyncio.Lock()),
        operations=SimpleNamespace(journal=journal, requests=OperationRequests(journal)),
        _session=lambda *args, **kwargs: source)
    controller = SavedStorageOptimize(app)
    app._saved_storage_optimize = controller
    operation = {'operationId': 'lifetime', '_owner': 'user', '_authority': None,
        'revision': 0, 'status': 'applying', '_phase': 'apply',
        'summary': {'coverage': {}, 'bytes': {}}}
    with journal.db:
        controller._save_operation(operation)
    row = {'_sourceId': 'root', 'sessionId': 'root',
           '_path': str(capture_dir(str(tmp_path), 'root') / 'events.jsonl')}
    threads, phases = [], []
    entered, release = threading.Event(), threading.Event()

    def protected(phase):
        assert app.diagnostics.flush_lock.locked()
        with pytest.raises(SessionBusyError):
            SharedSessionStore(str(tmp_path), 'root').acquire(app='competing-test')
        threads.append(threading.get_ident())
        phases.append(phase)

    @contextmanager
    def binding(*args, **kwargs):
        protected('open')
        try:
            yield object()
        finally:
            protected('close')
            if cleanup_failure:
                raise OSError('synthetic context final verification failed')
    monkeypatch.setattr(media_module, 'capture_owner', binding)

    async def work():
        async with _capture_lifetime(app, row, operation, resume=False, media=None) as lifetime:
            def image_stage():
                protected('images')
                entered.set()
                assert release.wait(5)
                protected('image-settled')
            await lifetime['run'](image_stage)
            await lifetime['run'](lambda: protected('expiry-final-ack'))
    task = asyncio.create_task(work())
    try:
        while not entered.is_set():
            await asyncio.sleep(0.001)
            if task.done():
                await task
        if cancel:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # Repeated cancellation still cannot release fences.
            assert not task.done() and app.diagnostics.flush_lock.locked()
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        elif cleanup_failure:
            with pytest.raises(OSError, match='context final verification'):
                await task
        else:
            await task
        assert phases == (['open', 'images', 'image-settled', 'close'] if cancel else
                          ['open', 'images', 'image-settled', 'expiry-final-ack', 'close'])
        assert len(set(threads)) == 1 and threads[0] != threading.get_ident()
        assert not app.diagnostics.flush_lock.locked()
        held = SharedSessionStore(str(tmp_path), 'root').acquire(app='after-test')
        held.release()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        journal.db.close()


def test_unknown_original_never_becomes_admitted_by_resume_or_poll():
    from amplifier_web.storage_optimize_media import capture_owner, UnknownHistoricalMedia
    with pytest.raises(UnknownHistoricalMedia, match='receipt is unavailable'):
        capture_owner(None, {}, {}, resume=True, poll=lambda: None)


@pytest.mark.parametrize('mutate', [False, True])
def test_index_rebind_does_not_treat_its_own_read_atime_as_source_drift(tmp_path, mutate):
    from amplifier_web.capture_index import hold_replacement, rebind_replaced_capture
    path = tmp_path / 'events.jsonl'
    before, after = b'{"old":1}\n', b'{"new":1}\n'
    fact = lambda raw: {'offset': 0, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    witness = {'old': {key: value for key, value in fact(before).items() if key != 'offset'},
               'new': {key: value for key, value in fact(after).items() if key != 'offset'}}
    db = sqlite3.connect(':memory:')
    try:
        db.executescript('CREATE TABLE captures(path TEXT PRIMARY KEY,offset INTEGER,inode INTEGER);'
                        'CREATE TABLE records(id TEXT,data TEXT);')
        path.write_bytes(before)
        hold_replacement(db, path, 'op', witness)
        replacement = tmp_path / 'candidate'
        replacement.write_bytes(after)
        replacement.replace(path)
        # Reconciliation must read the installed bytes. With an older atime,
        # that ordinary read updates atime without changing content or ctime.
        os.utime(path, ns=(1, path.stat().st_mtime_ns))
        old = path.stat()
        def mappings():
            yield {'old': fact(before), 'new': fact(after)}
            if mutate:
                path.write_bytes(b'{"bad":1}\n')
        if mutate:
            with pytest.raises(CaptureDrift, match='changed during index'):
                rebind_replaced_capture(db, path, 'op', witness, mappings())
            assert db.execute('SELECT operation_id FROM capture_maintenance').fetchall() == [('op',)]
            assert db.execute('SELECT count(*) FROM capture_rebindings').fetchone() == (0,)
            return
        rebind_replaced_capture(db, path, 'op', witness, mappings())
        assert path.stat().st_mtime_ns == old.st_mtime_ns
        assert path.read_bytes() == after
    finally:
        db.close()


def test_content_stamp_ignores_only_atime(monkeypatch):
    from amplifier_web.capture_index import _content_stamp
    import amplifier_web.capture_index as module
    # Independent of mount atime/noatime policy; all mutation witnesses retained.
    monkeypatch.setattr(module, 'source_change_time', lambda info: info.st_ctime_ns)
    fields = dict(st_dev=1, st_ino=2, st_mode=3, st_nlink=4, st_uid=5,
                  st_gid=6, st_size=7, st_mtime_ns=8, st_ctime_ns=9, st_atime_ns=10)
    original = _content_stamp(SimpleNamespace(**fields))
    assert _content_stamp(SimpleNamespace(**{**fields, 'st_atime_ns': 11})) == original
    for key in fields.keys() - {'st_atime_ns'}:
        assert _content_stamp(SimpleNamespace(**{**fields, key: fields[key] + 1})) != original