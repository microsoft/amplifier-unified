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
    from amplifier_web.storage_optimize_media import InstalledCaptureCheckpoint
    installed_checkpoint = InstalledCaptureCheckpoint(opdir, path, session_id='root',
                                                       store_session_id='root')
    def checkpoint(receipt):
        assert app.diagnostics.flush_lock.locked()
        from amplifier_foundation.session import SharedSessionStore, SessionBusyError
        with pytest.raises(SessionBusyError):
            SharedSessionStore(str(tmp_path), 'root').acquire(app='checkpoint-competitor')
        yield from installed_checkpoint.refs(receipt)
    diagnostic = SessionMediaStore(tmp_path / 'private-diagnostic-only', 'root',
        policy=MediaPolicy(100000, 1000, 2), checkpoint_verifier=checkpoint)
    def allocated(ref):
        with diagnostic.resolve_ref(ref, committed=False) as stream:
            return os.fstat(stream.fileno()).st_size
    binding = CaptureMediaOwner(diagnostic, staged_factory=StagedImage, origin_for=origin_for,
        checkpoint_receipt_for=installed_checkpoint.receipt_for,
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


def test_capture_ack_expiry_map_does_not_consume_unhashed_wal(tmp_path):
    from amplifier_web.storage_optimize_media import _expiry_rows
    path = tmp_path / 'map.sqlite3'
    writer = sqlite3.connect(path)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.executescript("CREATE TABLE format(name TEXT,version INTEGER);"
            "INSERT INTO format VALUES('ci-diagnostic-retention',1);"
            "CREATE TABLE record_map(ordinal INTEGER PRIMARY KEY,old_offset INTEGER,"
            "old_bytes INTEGER,old_sha256 TEXT,new_offset INTEGER,new_bytes INTEGER,new_sha256 TEXT);"
            "INSERT INTO record_map VALUES(1,0,10,'old',0,5,'new');")
        writer.commit()
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        before = file_hash(path)
        writer.execute('UPDATE record_map SET new_offset=999')
        writer.commit()
        assert file_hash(path) == before and Path(str(path) + '-wal').stat().st_size > 0
        with path.open('rb') as pinned:
            rows = list(_expiry_rows(pinned, None))
        assert rows == [{'old': {'offset': 0, 'bytes': 10, 'sha256': 'old'},
                         'new': {'offset': 0, 'bytes': 5, 'sha256': 'new'}}]
    finally:
        writer.close()


@pytest.mark.parametrize('selected_only,wrong_slot,expire_raw,padding,crash,drift,members', [
    (False, False, False, 0, False, None, 2), (True, False, False, 0, False, None, 2),
    (False, True, False, 0, False, None, 2), (False, False, True, 0, False, None, 2),
    (False, False, False, 16 * 1024 * 1024, False, None, 2),
    (False, False, False, 0, True, None, 2),
    (False, False, False, 0, False, 'records', 2),
    (False, False, True, 0, False, 'expiry-receipt', 2),
    (False, False, True, 0, False, 'expiry-map', 2),
    (False, False, False, 0, False, 'batch-final', 2),
    (False, False, False, 0, False, 'final-proof-tail', 2),
    (False, False, False, 0, False, None, 12)])
def test_exact_host_ack_commits_only_actual_selected_group_positions(tmp_path, monkeypatch,
        selected_only, wrong_slot, expire_raw, padding, crash, drift, members):
    """Real group/CI/F1 mechanism; explicit synthetic origin, no legacy claim."""
    from PIL import Image
    from amplifier_foundation.session.media import MediaPolicy, SessionMediaStore, MediaRefusal
    from amplifier_foundation.session.media_conversion_owner import CaptureGroupMediaOwner, GROUP_BINDING
    from context_intelligence.optimize import stage, apply as commit, resume, StagedImage
    from context_intelligence.optimize.capture import capture_group_sources
    from amplifier_web.storage_optimize_media import InstalledCaptureCheckpoint
    path = tmp_path / 'events.jsonl'
    pixels = []
    for color in [(position * 19 % 256, position * 31 % 256, position * 43 % 256)
                  for position in range(members)]:
        stream = io.BytesIO()
        Image.new('RGB', (3, 2), color).save(stream, format='PNG')
        pixels.append(stream.getvalue())
    blocks = [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
               'data': base64.b64encode(value).decode()}} for value in pixels]
    record = {'event': 'tool:post', 'session_id': 'root', 'event_id': 'one',
        'timestamp': '2020-01-01T00:00:00Z',
        'data': {'tool_call_id': 'original', 'status': 'unknown',
                 'result': {'success': False, 'content': blocks}}}
    if expire_raw:
        record.update(event='llm:request', data={'raw': {'messages': [{'role': 'user', 'content': blocks}]}})
    if padding:
        record['data']['padding'] = 'x' * padding
    path.write_text(json.dumps(record) + '\n')
    digest = file_hash(path)
    opdir = tmp_path / 'operation'
    checkpoint = InstalledCaptureCheckpoint(opdir, path, session_id='root', store_session_id='diagnostic')
    store = SessionMediaStore(tmp_path / 'diagnostic-media', 'diagnostic',
        policy=MediaPolicy(100000, 1000, 16), checkpoint_verifier=checkpoint.refs)
    origin = {'kind': 'migration', 'session_id': 'root', 'id': 'explicit-synthetic-group',
              'created_at': '2020-01-01T00:00:00Z', 'expires_at': None}
    # This is deliberately a declared fixture authority, NOT production receipt
    # admission. Production capture_owner still refuses unknown originals.
    def original(snapshot):
        assert snapshot['source_sha256'] == digest
        return origin
    try:
        with path.open('rb') as source, capture_group_sources(source, session_id='root',
                source_sha256=digest, origin_for=original,
                complete_group_for=lambda snapshot, policy: policy == origin) as groups:
            owner = CaptureGroupMediaOwner(store, group_sources_for=groups, staged_factory=StagedImage,
                origin_for=original, checkpoint_receipt_for=checkpoint.receipt_for,
                new_asset_bytes_for=lambda ref: 0,
                reader_ready=lambda name: name == GROUP_BINDING)
            class Selected:
                count = 0
                first_replacement = None
                def stage_image(self, stream, *, mime, origin):
                    self.count += 1
                    result = owner.stage_image(stream, mime=mime, origin=origin)
                    if self.count == 1:
                        self.first_replacement = result.replacement
                    if wrong_slot and self.count == 2:
                        return StagedImage(replacement=self.first_replacement,
                                           receipt=result.receipt, new_asset_bytes=0)
                    if selected_only and self.count == 2:
                        # Entire original group was assets-published, but the
                        # second selected slot is not a diagnostic reference.
                        return StagedImage(replacement=blocks[1], receipt=result.receipt,
                                           new_asset_bytes=0)
                    return result
                publish = owner.publish
                verify = owner.verify
                acknowledge = owner.acknowledge
            stage(path, opdir, session_id='root', media=Selected(), writers_stopped=True)
            if selected_only:
                # Synthetic staged-subset layout for mechanism qualification,
                # NOT a production receipt admission. The ordinary CI stage
                # selects all supported images; publication remains whole-group.
                manifest = opdir / 'assets.jsonl'
                rows = manifest.read_bytes().splitlines(keepends=True)
                manifest.write_bytes(rows[0])
                journal = json.loads((opdir / 'journal.json').read_bytes())
                journal['manifest_sha256'] = file_hash(manifest)
                (opdir / 'journal.json').write_text(json.dumps(journal))
            if wrong_slot:
                with pytest.raises(CaptureDrift, match='slot differs'):
                    commit(opdir, media=owner, writers_stopped=True)
                first_row = json.loads((opdir / 'assets.jsonl').read_bytes().splitlines()[0])
                for ref in first_row['owner']['refs']:
                    with pytest.raises(MediaRefusal):
                        store.resolve_ref(ref)
                assert json.loads((opdir / 'journal.json').read_bytes())['state'] == 'replaced'
                return  # No partial ACK despite a valid first installed member.
            if crash:
                import context_intelligence.optimize.operations as operations
                actual_save, actual_replace = operations._save, operations._replace_candidate
                replacements = []
                def replaced(*args):
                    replacements.append(True)
                    return actual_replace(*args)
                def save(directory, journal):
                    if journal['state'] == 'replaced':
                        raise OSError('lost replacement journal save')
                    return actual_save(directory, journal)
                monkeypatch.setattr(operations, '_replace_candidate', replaced)
                monkeypatch.setattr(operations, '_save', save)
                with pytest.raises(OSError, match='lost replacement journal'):
                    commit(opdir, media=owner, writers_stopped=True)
                assert json.loads((opdir / 'journal.json').read_bytes())['state'] == 'replace-pending'
                monkeypatch.setattr(operations, '_save', actual_save)
                assert resume(opdir, media=owner, writers_stopped=True)['state'] == 'verified'
                assert len(replacements) == 1
            if drift == 'batch-final':
                import context_intelligence.optimize.positions as positions
                actual = positions.capture_image_verifier
                @contextmanager
                def final_failure(*args, **kwargs):
                    with actual(*args, **kwargs) as read:
                        @contextmanager
                        def many(snapshots):
                            with read.read_many(snapshots) as values:
                                yield values
                            raise CaptureDrift('synthetic batch final seal failed')
                        yield SimpleNamespace(read_many=many)
                with monkeypatch.context() as patch:
                    patch.setattr(positions, 'capture_image_verifier', final_failure)
                    with pytest.raises(CaptureDrift, match='batch final seal'):
                        commit(opdir, media=owner, writers_stopped=True)
                first_row = json.loads((opdir / 'assets.jsonl').read_bytes().splitlines()[0])
                for ref in first_row['owner']['refs']:
                    with pytest.raises(MediaRefusal):
                        store.resolve_ref(ref)
                return  # Even fully read valid slots cannot precede final seal.
            if drift == 'final-proof-tail':
                actual = checkpoint._refs
                ready, substituted = [], []
                def slot_phase(*args):
                    yield from actual(*args)
                    ready.append(True)
                def poll_tail():
                    if ready and not substituted:
                        same = path.with_name('changed-tail')
                        same.write_bytes(path.read_bytes())
                        same.replace(path)
                        substituted.append(True)
                with monkeypatch.context() as patch:
                    patch.setattr(checkpoint, '_refs', slot_phase)
                    patch.setattr(checkpoint, 'poll', poll_tail)
                    with pytest.raises(CaptureDrift, match='proof changed'):
                        commit(opdir, media=owner, writers_stopped=True)
                assert substituted
                first_row = json.loads((opdir / 'assets.jsonl').read_bytes().splitlines()[0])
                for ref in first_row['owner']['refs']:
                    with pytest.raises(MediaRefusal):
                        store.resolve_ref(ref)
                return  # Tail polls cannot outlive the installed generation pin.
            result = commit(opdir, media=owner, writers_stopped=True)
            assert result['state'] == 'verified'
            with pytest.raises(CaptureDrift, match='live owner receipt'):
                list(checkpoint.refs({'session_id': 'diagnostic', 'sha256': file_hash(path)}))
            other = InstalledCaptureCheckpoint(opdir, path, session_id='root', store_session_id='diagnostic')
            with pytest.raises(CaptureDrift, match='live owner receipt'):
                list(checkpoint.refs(other.receipt_for(opdir / 'assets.jsonl',
                                                     source=path, sha256=file_hash(path))))
            manifest_row = json.loads((opdir / 'assets.jsonl').read_bytes().splitlines()[0])
            refs = manifest_row['owner']['refs']
            assert len(refs) == members
            with store.resolve_ref(refs[0]) as binary:
                assert binary.read() == pixels[0]
            if selected_only:
                store.validate_published_ref(refs[1])
                with pytest.raises(MediaRefusal):
                    store.resolve_ref(refs[1])  # Assets-only is not installed.
            else:
                with store.resolve_ref(refs[1]) as binary:
                    assert binary.read() == pixels[1]
            installed = path.read_bytes()
            assert resume(opdir, media=owner, writers_stopped=True)['state'] == 'verified'
            assert path.read_bytes() == installed
            import context_intelligence.optimize.positions as positions
            hash_calls = []
            actual_hash = positions._check_hash
            def observed_hash(*args):
                hash_calls.append(True)
                return actual_hash(*args)
            with monkeypatch.context() as patch:
                patch.setattr(positions, '_check_hash', observed_hash)
                receipt = checkpoint.receipt_for(opdir / 'assets.jsonl', source=path, sha256=file_hash(path))
                assert list(checkpoint.refs(receipt)) == (refs[:1] if selected_only else refs)
            # Same fixed batch+outer hash passes for 2 and 12 selected images.
            assert 0 < len(hash_calls) <= 12, len(hash_calls)
            def assert_proof_drift(target):
                import context_intelligence.optimize.positions as positions
                actual = positions.capture_image_verifier
                swapped = []
                @contextmanager
                def changed(*args, **kwargs):
                    with actual(*args, **kwargs) as read:
                        @contextmanager
                        def many(snapshots):
                            with read.read_many(snapshots) as values:
                                def results():
                                    for value in values:
                                        if not swapped:
                                            same = target.with_name(target.name + '.replacement')
                                            same.write_bytes(target.read_bytes())
                                            same.replace(target)
                                            swapped.append(True)
                                        yield value
                                yield results()
                        yield SimpleNamespace(read_many=many)
                with monkeypatch.context() as patch:
                    patch.setattr(positions, 'capture_image_verifier', changed)
                    receipt = checkpoint.receipt_for(opdir / 'assets.jsonl',
                                                     source=path, sha256=file_hash(path))
                    with pytest.raises(CaptureDrift, match='proof changed'):
                        list(checkpoint.refs(receipt))
                assert swapped
            if drift == 'records':
                assert_proof_drift(opdir / 'records.jsonl')
            if padding:
                import tracemalloc
                tracemalloc.start()
                try:
                    receipt = checkpoint.receipt_for(opdir / 'assets.jsonl', source=path, sha256=file_hash(path))
                    assert list(checkpoint.refs(receipt)) == refs
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                # Two disk indexes, streamed physical mappings/manifest/hash:
                # adding a giant non-image string cannot hydrate the record.
                assert peak < 4 * 1024 * 1024, peak
            if expire_raw:
                from datetime import datetime, timedelta, timezone
                from context_intelligence.prune.operations import PruneRequest, prune_file
                from context_intelligence.prune.policy import Policy
                result = prune_file(path, PruneRequest(tmp_path,
                    Policy(datetime(2026, 1, 1, tzinfo=timezone.utc), diagnostic_sidecars=True),
                    apply=True, writers_stopped=True, min_idle=timedelta(0), verified_optimize=opdir))
                assert result.status == 'pruned'
                receipt = checkpoint.receipt_for(opdir / 'assets.jsonl', source=path, sha256=file_hash(path))
                assert list(checkpoint.refs(receipt)) == []  # All raw-field slots truly expired.
                from context_intelligence.prune.coordination import receipt_path
                from context_intelligence.prune.compound import optimize_retention_receipt
                expiry_proof = optimize_retention_receipt(path, file_hash(receipt_path(path)))
                retained_proof = tmp_path / 'retained-expiry-proof.json'
                expiry_proof.rename(retained_proof)
                try:
                    with pytest.raises(CaptureDrift, match='exact owner expiry receipt'):
                        list(checkpoint.refs(receipt))  # Absence alone grants nothing.
                finally:
                    retained_proof.rename(expiry_proof)
                if drift:
                    expiry = json.loads(expiry_proof.read_bytes())
                    target = expiry_proof if drift == 'expiry-receipt' else (
                        path.parent / expiry['diagnostic_manifest']['name'])
                    assert_proof_drift(target)
                owner.acknowledge(opdir / 'assets.jsonl', source=path, sha256=file_hash(path))
                # Retained original recovery still pins bytes; final empty ACK
                # is not a reclamation or retroactive-uncommit claim.
                assert (opdir / 'original').read_bytes()
    finally:
        store.close()


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