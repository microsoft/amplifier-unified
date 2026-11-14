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
from types import SimpleNamespace

import pytest

from amplifier_web.storage_optimize import SavedStorageOptimize
from amplifier_web.operation_requests import OperationRequests
from amplifier_operations import OperationJournal
from amplifier_web.storage_optimize_capture import apply, operation_directory
from amplifier_web.storage_optimize_retention import file_hash
from amplifier_web.session_files import capture_dir
from amplifier_web.capture_index import index_shared


async def test_actual_ci_foundation_private_diagnostic_apply_rebind_and_resume(tmp_path):
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
    original = path.read_bytes()
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
        indexed = index_shared(connection, [(str(tmp_path), 'root')], {'streams': ['tools']})
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
        'summary': {'coverage': {}, 'bytes': {}}}
    with journal.db:
        controller._save_operation(operation)
    row = {'owner': 'context-intelligence', 'sessionId': 'root', 'kind': 'root',
           'bytes': len(original), '_sourceId': 'root', '_path': str(path),
           '_sourceSha256': file_hash(path), 'status': 'eligible'}
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
        assert receipt['session_id'] == 'root'
        assert receipt['sha256'] == file_hash(path)
        assert receipt['mapping_sha256'] == file_hash(opdir / 'records.jsonl')
        # Small single-record fixture; production owner verification is streamed.
        block = json.loads(path.read_bytes())['data']['result']['content'][0]
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
    try:
        result = await apply(app, row, operation, media=binding)
        assert result['status'] == 'completed'
        assert (opdir / 'original').read_bytes() == original
        assert b'base64' not in path.read_bytes()
        with db() as connection:
            assert connection.execute('SELECT id FROM records').fetchall() == [(indexed[0][0],)]
            assert connection.execute('SELECT * FROM deliveries').fetchall() == [(indexed[0][0], 'accepted', '{"same":"outcome"}')]
            assert index_shared(connection, [(str(tmp_path), 'root')], {'streams': ['tools']}) == []
        installed = path.read_bytes()
        assert (await apply(app, row, operation, media=binding, resume=True))['status'] == 'completed'
        assert path.read_bytes() == installed
    finally:
        diagnostic.close()
        admitted.close()
        journal.db.close()
        appdb.close()