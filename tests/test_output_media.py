"""Synthetic parser fixtures, not playback evidence or the reporter's input."""
import hashlib
import os
import struct

import pytest

from amplifier_web.output_media import (
    MAX_BYTES, MediaRangeError, byte_range, read_verified, validate_media,
)


def gif(width=1, height=1, frames=2, delays=(7, 19), loop=1):
    # Two-entry palette and a complete one-pixel LZW stream. Larger logical
    # screens intentionally exercise composited-pixel admission, not decoding.
    header = b"GIF89a" + struct.pack("<HHBBB", width, height, 0x80, 0, 0)
    palette = b"\xff\x00\x00\x00\x00\xff"
    application = b"\x21\xff\x0bNETSCAPE2.0\x03\x01" + struct.pack("<H", loop) + b"\x00"
    rows = []
    for index in range(frames):
        rows.append(b"\x21\xf9\x04\x04" + struct.pack("<H", delays[index % len(delays)]) + b"\x00\x00")
        rows.append(b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02\x02\x44\x01\x00")
    return header + palette + application + b"".join(rows) + b"\x3b"


def atom(kind, body):
    return struct.pack(">I4s", len(body) + 8, kind) + body


def mp4():
    # Minimal structural fixture; deliberately not claimed as playable video.
    tkhd = bytearray(84)
    tkhd[76:84] = struct.pack(">II", 320 << 16, 240 << 16)
    hdlr = b"\x00" * 8 + b"vide" + b"\x00" * 12
    sample = bytearray(78)
    sample[24:28] = struct.pack(">HH", 320, 240)
    stsd = b"\x00" * 4 + struct.pack(">I", 1) + atom(b"avc1", bytes(sample))
    mdia = atom(b"hdlr", hdlr) + atom(b"minf", atom(b"stbl", atom(b"stsd", stsd)))
    return atom(b"ftyp", b"isom\x00\x00\x00\x00isommp42") + atom(b"moov", atom(b"trak", atom(b"tkhd", bytes(tkhd)) + atom(b"mdia", mdia))) + atom(b"mdat", b"\x00")


def test_gif_metadata_preserves_nonuniform_delay_and_loop():
    data = gif()
    result = validate_media(data)
    assert result == {"mimeType": "image/gif", "width": 1, "height": 1,
                      "frameCount": 2, "durationMs": 260, "loopCount": 1,
                      "decodedPixelBytesEstimate": 8}
    assert data == gif()  # no rewriting or transcoding


@pytest.mark.parametrize("data", [
    b"", b"GIF89a", gif()[:-1], gif() + b"trailing",
    gif().replace(b"\x21\xf9\x04", b"\x21\xf9\x03", 1),
    gif().replace(b"\x02\x02\x44\x01\x00", b"\x01\x02\x44\x01\x00", 1),
    gif(width=0), gif(width=4097), gif(width=2048, height=2048),
    gif(frames=501), gif(width=1280, height=720, frames=37),
])
def test_gif_damaged_or_over_budget_refused(data):
    with pytest.raises(ValueError):
        validate_media(data)


def test_original_shape_estimate_admitted_without_native_memory_claim():
    result = validate_media(gif(width=1280, height=720, frames=26))
    assert result["decodedPixelBytesEstimate"] == 95_846_400
    assert result["frameCount"] == 26


def test_mp4_requires_sized_video_track_and_sample_description():
    result = validate_media(mp4())
    assert result["mimeType"] == "video/mp4"
    assert (result["width"], result["height"]) == (320, 240)
    assert (result["codedWidth"], result["codedHeight"]) == (320, 240)
    for data in (mp4()[:-1], mp4().replace(b"vide", b"soun"),
                 mp4().replace(b"avc1", b"xxxx"), atom(b"ftyp", b"isom"),
                 b"\x00\x00\x00\x00moov", b"\x00\x00\x00\x01moov"):
        with pytest.raises(ValueError):
            validate_media(data)


def test_mp4_coded_dimensions_cannot_hide_behind_small_display():
    original = mp4()
    offset = original.index(b"avc1") + 4 + 24
    for width, height in ((0, 240), (4097, 240), (2048, 2048)):
        data = original[:offset] + struct.pack(">HH", width, height) + original[offset + 4:]
        with pytest.raises(ValueError, match="dimensions"):
            validate_media(data)


def test_mp4_later_video_track_never_bypasses_validation():
    original = mp4()
    position = original.index(b"trak") - 4
    size = int.from_bytes(original[position:position + 4], "big")
    track = original[position:position + size]
    data = atom(b"ftyp", b"isom\x00\x00\x00\x00isommp42") + atom(b"moov", track + track) + atom(b"mdat", b"\x00")
    with pytest.raises(ValueError, match="one video track"):
        validate_media(data)
    oversized = bytearray(track)
    offset = oversized.index(b"avc1") + 4 + 24
    oversized[offset:offset + 4] = struct.pack(">HH", 4097, 240)
    data = atom(b"ftyp", b"isom\x00\x00\x00\x00isommp42") + atom(b"moov", track + oversized) + atom(b"mdat", b"\x00")
    with pytest.raises(ValueError, match="dimensions"):
        validate_media(data)


def test_gif_tiny_comment_blocks_have_bounded_parser_allocation():
    import tracemalloc
    original = gif()
    # Python parser overhead only, not native decoder memory qualification.
    data = original[:19] + b"\x21\xfe" + b"\x01x" * 200_000 + b"\x00" + original[19:]
    tracemalloc.start()
    try:
        result = validate_media(data)
        _, peak = tracemalloc.get_traced_memory()
        assert result["frameCount"] == 2
        assert peak < 1024 * 1024
    finally:
        tracemalloc.stop()


def test_encoded_limit_remains_media_only():
    with pytest.raises(ValueError, match="32"):
        validate_media(b"x" * (MAX_BYTES + 1))
    from amplifier_web.outputs import MAX_FILE
    assert MAX_FILE == 8 * 1024 * 1024


def test_exact_regular_file_no_symlink_even_inside_workspace(tmp_path):
    data = gif()
    path = tmp_path / "clip.gif"
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    saved, name, metadata = read_verified(tmp_path, "clip.gif", digest)
    assert saved == data and name == "clip.gif" and metadata["frameCount"] == 2
    (tmp_path / "alias.gif").symlink_to(path)
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "clip.gif").write_bytes(data)
    (tmp_path / "alias-dir").symlink_to(tmp_path / "dir", target_is_directory=True)
    for value in ("alias.gif", "alias-dir/clip.gif", "../clip.gif", "."):
        with pytest.raises((ValueError, OSError)):
            read_verified(tmp_path, value, digest)
    with pytest.raises(ValueError, match="SHA"):
        read_verified(tmp_path, "clip.gif", "0" * 64)


def test_file_modified_during_read_refused(tmp_path, monkeypatch):
    path = tmp_path / "clip.gif"
    path.write_bytes(gif())
    real = os.fstat
    calls = 0

    def changed(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(gif(frames=3))
        return real(fd)

    monkeypatch.setattr(os, "fstat", changed)
    with pytest.raises(ValueError, match="changed"):
        read_verified(tmp_path, "clip.gif", hashlib.sha256(gif()).hexdigest())


def test_workspace_ancestor_symlink_is_not_followed(tmp_path):
    parent = tmp_path / "real"
    workspace = parent / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "clip.gif").write_bytes(gif())
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)
    with pytest.raises(OSError):
        read_verified(alias / "workspace", "clip.gif", hashlib.sha256(gif()).hexdigest())


@pytest.mark.parametrize("header, expected", [
    (None, None), ("bytes=2-4", (2, 4)), ("bytes=2-", (2, 9)),
    ("bytes=-3", (7, 9)), ("bytes=-30", (0, 9)), ("bytes=0-99", (0, 9)),
])
def test_single_exact_byte_range(header, expected):
    assert byte_range(header, 10) == expected


@pytest.mark.parametrize("header, status", [
    ("bytes=10-", 416), ("bytes=4-2", 416), ("bytes=-0", 416),
    ("bytes=0-1,3-4", 400), ("items=0-1", 400), ("bytes=-", 400),
    ("bytes= 0-1", 400), ("bytes=" + "9" * 100 + "-", 400),
])
def test_range_refusal_policy_explicit(header, status):
    with pytest.raises(MediaRangeError) as error:
        byte_range(header, 10)
    assert error.value.status == status


@pytest.fixture
async def media_app(tmp_path):
    from amplifier_web.service import AppService
    from test_service import Runtime
    app = AppService(tmp_path / 'app', workspace=tmp_path, runtime=Runtime())
    await app.dispatch('session.create', {'title': 'Synthetic media'})
    sid = app._session()['id']
    # Production event handling allocates the ID and commits assistant role.
    await app.on_runtime_event('assistant.message', {
        'sessionId': sid, 'text': 'Retain this animation.', 'generationId': 'fixture-generation',
    })
    try:
        yield app
    finally:
        for queue in list(app.queue_clients):
            app.unsubscribe(queue)
        await app.close()


def media_args(app):
    from pathlib import Path
    session = app._session()
    data = gif()
    (Path(session['workspace']) / 'clip.gif').write_bytes(data)
    return {'sessionId': session['id'], 'messageId': session['messages'][-1]['id'],
            'title': 'Synthetic animation', 'path': 'clip.gif',
            'expectedSha256': hashlib.sha256(data).hexdigest()}


async def test_media_actual_assistant_commit_receipt_revision_and_source_removal(media_app):
    from pathlib import Path
    args = media_args(media_app)
    record = (await media_app.dispatch('outputs.attachMedia', args, command_id='media-one'))['result']
    session = media_app._session()
    assert session['mediaRevision'] == 1
    assert 'path' not in record and 'data' not in record and record['media']['frameCount'] == 2
    Path(session['workspace'], 'clip.gif').unlink()
    duplicate = (await media_app.dispatch('outputs.attachMedia', args, command_id='media-one'))['result']
    assert duplicate['id'] == record['id'] and duplicate['duplicate']
    assert session['mediaRevision'] == 1
    media_app.clients.attach('media-client')
    with media_app.clients.bind('media-client'):
        descriptors = await media_app.outputs.media.describe(args['sessionId'], args['messageId'])
        assert descriptors['items'][0]['id'] == record['id']
        tuple_args = {**descriptors['items'][0], 'sessionId': args['sessionId'], 'messageId': args['messageId']}
        assert await media_app.outputs.media.bytes(tuple_args) == gif()
    await media_app.dispatch('outputs.unlink', {'sessionId': args['sessionId'], 'id': record['id'], 'expectedRevision': 1})
    assert session['mediaRevision'] == 2
    with media_app.clients.bind('media-client'):
        with pytest.raises(ValueError, match='linked'):
            await media_app.outputs.media.bytes(tuple_args)


async def test_media_role_unknown_foreign_hash_and_command_forgery(media_app):
    from amplifier_web.service import AppError
    args = media_args(media_app)
    with pytest.raises(AppError, match='assistant'):
        await media_app.dispatch('outputs.attachMedia', {**args, 'messageId': 'unknown'})
    await media_app.on_runtime_event('assistant.message', {
        'sessionId': args['sessionId'], 'text': 'Later message.', 'generationId': 'later-generation',
    })
    # Older saved assistant messages are explicit valid targets.
    await media_app.dispatch('outputs.attachMedia', args, command_id='original')
    with pytest.raises(AppError, match='different'):
        await media_app.dispatch('outputs.attachMedia', {**args, 'expectedSha256': '0' * 64}, command_id='original')
    with pytest.raises(AppError, match='SHA'):
        await media_app.dispatch('outputs.attachMedia', {**args, 'expectedSha256': '0' * 64})
    with pytest.raises(AppError):
        await media_app.dispatch('outputs.attachMedia', {**args, 'sourceGenerationId': 'forged'})
    media_app._session()['messages'][0]['role'] = 'user'
    with pytest.raises(AppError, match='assistant'):
        await media_app.dispatch('outputs.attachMedia', args)


async def test_media_role_recheck_after_worker_read(media_app, monkeypatch):
    import asyncio
    import threading
    from amplifier_web import output_media
    from amplifier_web.service import AppError
    args = media_args(media_app)
    entered, release = threading.Event(), threading.Event()
    real = output_media.read_verified

    def blocked(*values):
        entered.set()
        assert release.wait(5)
        return real(*values)

    monkeypatch.setattr(output_media, 'read_verified', blocked)
    task = asyncio.create_task(media_app.dispatch('outputs.attachMedia', args))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        async with media_app.lock:
            media_app._session()['messages'][0]['role'] = 'user'
        release.set()
        with pytest.raises(AppError, match='assistant'):
            await task
        assert media_app.outputs.store.list(args['sessionId'])['items'] == []
    finally:
        release.set()
        if not task.done():
            await task


async def test_media_cached_bytes_detect_corruption_and_client_scope(media_app):
    from pathlib import Path
    from amplifier_web.resource_files import root
    args = media_args(media_app)
    record = (await media_app.dispatch('outputs.attachMedia', args))['result']
    media_app.clients.attach('scope-one')
    media_app.clients.attach('scope-two')
    values = {'sessionId': args['sessionId'], 'messageId': args['messageId'],
              'id': record['id'], 'version': record['version'], 'sha256': record['sha256']}
    with media_app.clients.bind('scope-one'):
        assert await media_app.outputs.media.bytes(values) == gif()
        assert await media_app.outputs.media.bytes(values) == gif()
        internal = media_app.outputs.record(args['sessionId'], record['id'])
        assert 'body' not in record  # Never widen a public receipt to fix a fixture.
        path = root(media_app.db) / (internal['body']['$resource'] + '.json')
        path.write_text('{"encoding":"base64","data":"corrupt"}')
        with pytest.raises(ValueError, match='identity'):
            await media_app.outputs.media.bytes(values)
    media_app.clients.records['scope-two']['selectedSessionId'] = None
    with media_app.clients.bind('scope-two'):
        with pytest.raises(ValueError, match='client'):
            await media_app.outputs.media.bytes(values)


@pytest.mark.parametrize('in_memory', [False, True])
async def test_warm_cache_never_bypasses_changed_index(tmp_path, in_memory):
    import sqlite3
    from types import SimpleNamespace
    import asyncio
    from amplifier_web.output_media import Media, _prepare_media
    from amplifier_web.resource_files import put_prepared_async, root
    db = sqlite3.connect(':memory:' if in_memory else tmp_path / 'index.sqlite3')
    db.execute('CREATE TABLE state_resources(id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    session = {'id': 'own', 'workspace': 'synthetic'}
    app = SimpleNamespace(db=db, lock=asyncio.Lock(), _session=lambda sid, **kwargs: session)
    media = Media(SimpleNamespace(app=app))
    body = await put_prepared_async(db, _prepare_media(gif()))
    db.commit()
    record = {'body': body, 'sha256': hashlib.sha256(gif()).hexdigest()}
    media.guard = lambda values, proof=None: record

    async def indexed_identity(*args, **kwargs):
        return {'fact': ('saved-assistant', 'assistant'), 'snapshot': session}

    media.identity = indexed_identity
    values = {'sessionId': 'own', 'messageId': 'saved-assistant'}
    try:
        assert await media.bytes(values) == gif()
        before = None if in_memory else (root(db) / (body['$resource'] + '.json')).stat()
        for wrong in ['{"$blob":"' + '0' * 64 + '"}', '{}', '[]', 'null',
                      '{"$blob":"' + body['$resource'] + '","foreign":true}']:
            db.execute('UPDATE state_resources SET value=? WHERE id=?', (wrong, body['$resource']))
            db.commit()
            with pytest.raises(ValueError, match='index|identity'):
                await media.bytes(values)
            if before is not None:
                assert (root(db) / (body['$resource'] + '.json')).stat() == before
    finally:
        db.close()


async def test_actual_large_media_publication_when_maintenance_is_due(media_app, monkeypatch):
    import base64
    import io
    import threading
    from pathlib import Path
    from amplifier_web import resource_files as files
    from amplifier_web.host.storage import SessionStore
    app = media_app
    task = getattr(app, '_storage_maintenance_task', None)
    if task:
        await task
    args = media_args(app)
    # Valid large synthetic comment payload. Not native decoding evidence.
    data = gif()[:-1] + b'\x21\xfe' + (b'\xff' + b'x' * 255) * 75_000 + b'\x00\x3b'
    Path(app._session()['workspace'], 'clip.gif').write_bytes(data)
    args['expectedSha256'] = hashlib.sha256(data).hexdigest()
    expected = files.prepare({'encoding': 'base64', 'data': base64.b64encode(data).decode('ascii')})
    stale = files.put(app.db, {'data': 'actually unreferenced'})['$resource']
    leaf = files.put(app.db, {'data': 'nested retained evidence'})
    parent = files.put(app.db, {'nested': [leaf]})
    app._session()['artifactRefs'] = [parent]
    app._save()
    app.db.commit()
    owner = threading.get_ident()
    reads, writes, json_threads, dump_threads, sql_threads, small_writes = [], [], [], [], [], []
    real_open, real_io_open, real_loads, real_dumps = files.os.open, io.open, files.json.loads, files.json.dumps
    real_atomic = SessionStore._atomic
    directory = files.root(app.db)
    large_path = directory / (expected.identity + '.json')

    def opened(path, *values, **kwargs):
        if isinstance(path, (str, os.PathLike)) and Path(path) == large_path:
            reads.append((str(path), threading.get_ident()))
        return real_open(path, *values, **kwargs)

    def io_opened(path, *values, **kwargs):
        if isinstance(path, (str, os.PathLike)) and Path(path) == large_path:
            reads.append((str(path), threading.get_ident()))
        return real_io_open(path, *values, **kwargs)

    def atomic(path, text):
        # Observe the atomic large write by final filename AND canonical content.
        # Temporary-file opens and ordinary small state publication are distinct.
        if Path(path) == large_path:
            assert text == expected.text
            writes.append((str(path), len(text.encode()), threading.get_ident()))
        elif Path(path).parent == directory:
            small_writes.append((Path(path).name, len(text.encode()), threading.get_ident()))
        return real_atomic(path, text)

    def loaded(text, *values, **kwargs):
        encoded = text.encode() if isinstance(text, str) else text
        if hashlib.sha256(encoded).hexdigest() == expected.identity:
            json_threads.append(threading.get_ident())
        return real_loads(text, *values, **kwargs)

    def dumped(value, *values, **kwargs):
        text = real_dumps(value, *values, **kwargs)
        if len(text) == len(expected.text) and hashlib.sha256(text.encode()).hexdigest() == expected.identity:
            dump_threads.append(threading.get_ident())
        return text

    monkeypatch.setattr(files.os, 'open', opened)
    monkeypatch.setattr(io, 'open', io_opened)
    monkeypatch.setattr(files.json, 'loads', loaded)
    monkeypatch.setattr(files.json, 'dumps', dumped)
    monkeypatch.setattr(SessionStore, '_atomic', staticmethod(atomic))
    app.db.set_trace_callback(lambda sql: sql_threads.append(threading.get_ident()))
    app._last_storage_sweep = 0
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    await app._storage_maintenance_task
    internal = app.outputs.record(args['sessionId'], record['id'])
    assert internal['body'] == {'$resource': expected.identity, 'bytes': expected.size}
    assert large_path.stat().st_size == expected.size > 19_000_000
    assert record['bytes'] == len(data) and record['sha256'] == hashlib.sha256(data).hexdigest()
    assert app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (internal['body']['$resource'],)).fetchone()
    assert not app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (stale,)).fetchone()
    assert not (directory / (stale + '.json')).exists()
    for reference in (parent, leaf):
        assert app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (reference['$resource'],)).fetchone()
        assert (directory / (reference['$resource'] + '.json')).is_file()
    assert json_threads and owner not in json_threads
    assert dump_threads and owner not in dump_threads
    assert reads and all(thread != owner for _, thread in reads)
    assert writes and all(thread != owner for _, _, thread in writes)
    # Explicit counter-witness: owner-thread metadata publication is allowed,
    # but never classified as off-owner large media IO.
    assert small_writes and any(thread == owner for _, _, thread in small_writes)
    assert all(size < 1024 * 1024 for _, size, _ in small_writes)
    assert sql_threads and set(sql_threads) == {owner}
    assert app._resource_scan_status['blocked'] is False
    app.db.set_trace_callback(None)


async def test_media_http_auth_head_exact_ranges_download_and_no_unscoped_bypass(authenticated_client, tmp_path, monkeypatch):
    from amplifier_web.server import create_app
    from test_service import Runtime
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                           voice=False, preload_providers=False, background_updates=False)
    service = app['service']
    await service.dispatch('session.create', {'title': 'HTTP media'})
    sid = service._session()['id']
    await service.on_runtime_event('assistant.message', {'sessionId': sid, 'text': 'Animation.'})
    args = media_args(service)
    record = (await service.dispatch('outputs.attachMedia', args))['result']
    service.clients.attach('http-media')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'http-media'
    query = {'sessionId': sid, 'messageId': args['messageId'], 'version': str(record['version']), 'sha256': record['sha256']}
    path = '/api/outputs/' + record['id'] + '/media'
    real_bytes = service.outputs.media.bytes

    async def forbidden_body(*args):
        pytest.fail('HEAD opened/decoded the media body')

    monkeypatch.setattr(service.outputs.media, 'bytes', forbidden_body)
    response = await client.head(path, params=query)
    assert response.status == 200 and await response.read() == b''
    assert int(response.headers['Content-Length']) == len(gif())
    ranged_head = await client.head(path, params=query, headers={'Range': 'bytes=2-4'})
    assert ranged_head.status == 206 and await ranged_head.read() == b''
    assert ranged_head.headers['Content-Length'] == '3'
    monkeypatch.setattr(service.outputs.media, 'bytes', real_bytes)
    for header, expected, bounds in [('bytes=2-4', gif()[2:5], (2, 4)),
                                     ('bytes=2-', gif()[2:], (2, len(gif()) - 1)),
                                     ('bytes=-3', gif()[-3:], (len(gif()) - 3, len(gif()) - 1))]:
        response = await client.get(path, params=query, headers={'Range': header})
        assert response.status == 206 and await response.read() == expected
        assert response.headers['Content-Length'] == str(len(expected))
        assert response.headers['Content-Range'] == f'bytes {bounds[0]}-{bounds[1]}/{len(gif())}'
    for header, code in [('bytes=99999-', 416), ('bytes=0-1,3-4', 400), ('bytes=-', 400)]:
        response = await client.get(path, params=query, headers={'Range': header})
        assert response.status == code
        if code == 416:
            assert response.headers['Content-Range'] == 'bytes */' + str(len(gif()))
    response = await client.get(path, params={**query, 'download': '1'})
    assert response.status == 200 and await response.read() == gif()
    assert response.headers['Content-Disposition'].startswith('attachment;')
    assert (await client.get('/api/outputs/' + record['id'] + '/content')).status == 403
    from amplifier_web import output_media
    real_guard = service.outputs.media.guarded
    rejected = []

    async def witnessed_guard(values):
        try:
            return await real_guard(values)
        except ValueError as error:
            rejected.append(str(error))
            raise

    async def forbidden_rejected_body(*args):
        pytest.fail('A rejected tuple opened/decoded the media body')

    def forbidden_rejected_range(*args):
        pytest.fail('A rejected tuple reached Range parsing')

    monkeypatch.setattr(service.outputs.media, 'guarded', witnessed_guard)
    monkeypatch.setattr(service.outputs.media, 'bytes', forbidden_rejected_body)
    monkeypatch.setattr(output_media, 'byte_range', forbidden_rejected_range)
    for key, value in [('sessionId', 'foreign'), ('messageId', 'foreign'), ('version', '99'), ('sha256', '0' * 64)]:
        rejected.clear()
        wrong = await client.get(path, params={**query, key: value}, headers={'Range': 'malformed'})
        assert wrong.status == 400  # Existing boundaries middleware maps ValueError to 400.
        assert len(rejected) == 1
        assert await wrong.json() == {'accepted': False, 'error': 'Invalid request: ' + rejected[0]}
    rejected.clear()
    client.session.headers['Authorization'] = ''
    client.session.cookie_jar.clear()
    response = await client.get(path, params=query, headers={'Range': 'malformed'}, allow_redirects=False)
    assert response.status in {401, 403}
    assert rejected == []  # Authentication fails before tuple authority, body or Range.


async def test_media_report_origin_exact_tuple_and_monotonic_order(media_app):
    from amplifier_web.service import AppError
    args = media_args(media_app)
    record = (await media_app.dispatch('outputs.attachMedia', args))['result']
    media_app.clients.attach('reported-client')
    values = {'sessionId': args['sessionId'], 'messageId': args['messageId'], 'id': record['id'],
              'version': record['version'], 'sha256': record['sha256'],
              'generation': 1, 'sequence': 1, 'renderState': 'loading', 'playback': 'paused'}
    with media_app.clients.bind('reported-client'):
        queue = media_app.subscribe()
        with pytest.raises(AppError, match='UI'):
            await media_app.dispatch('outputs.mediaReport', values, origin='agent',
                                     caller_session_id=args['sessionId'])
        await media_app.dispatch('outputs.mediaReport', values, origin='ui')
        with pytest.raises(AppError, match='stale'):
            await media_app.dispatch('outputs.mediaReport', values, origin='ui')
        await media_app.dispatch('outputs.mediaReport', {**values, 'sequence': 2, 'renderState': 'ready', 'playback': 'playing'}, origin='ui')
        result = (await media_app.dispatch('outputs.read', {'sessionId': args['sessionId'], 'id': record['id']}))['result']
        assert result['saved'] and result['attached'] and result['playback'] == 'playing'
        assert result['browserObservation']['evidence'] == 'authenticated-ui-report'
        assert result['browserObservation']['modelAttestation'] is False
        media_app.unsubscribe(queue)
        disconnected = (await media_app.dispatch('outputs.read', {'sessionId': args['sessionId'], 'id': record['id']}))['result']
        assert disconnected['playback'] == 'unknown'
        assert disconnected['browserObservation']['status'] == 'disconnected'
        assert 'presentedFrames' not in disconnected['browserObservation']


async def test_media_report_expiry_unbound_and_agent_read_are_not_never_loaded(media_app, monkeypatch):
    from amplifier_web import output_media
    app = media_app
    args = media_args(app)
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    app.clients.attach('observer-one')
    app.clients.attach('observer-two')
    values = {'sessionId': args['sessionId'], 'messageId': args['messageId'], 'id': record['id'],
              'version': record['version'], 'sha256': record['sha256'], 'generation': 10, 'sequence': 1,
              'renderState': 'ready', 'playback': 'paused', 'presentedFrames': 1, 'mediaTime': 0}
    internal = app.outputs.record(args['sessionId'], record['id'])
    with app.clients.bind('observer-one'):
        queue = app.subscribe()
        await app.dispatch('outputs.mediaReport', values, origin='ui')
        result = await app.app_bridge('dispatch', {'action': 'outputs.read',
            'args': {'id': record['id']}, '_inputClients': ['observer-one']}, args['sessionId'])
        assert result['result']['browserObservation']['status'] == 'observed'
        assert result['result']['browserObservation']['presentedFrames'] == 1
        stamp = result['result']['browserObservation']['serverTimestamp']
        monkeypatch.setattr(output_media.time, 'time', lambda: stamp + 31)
        expired = app.outputs.media.observation(internal)
        assert expired['status'] == 'expired' and 'playback' not in expired
        app.unsubscribe(queue)
    with app.clients.bind('observer-two'):
        assert app.outputs.media.observation(internal)['status'] == 'not_observed'
        app.clients.records['observer-two']['selectedSessionId'] = None
        assert app.outputs.media.observation(internal)['status'] == 'unbound'
    assert app.outputs.media.observation(internal)['status'] == 'unbound'


async def test_media_unlinked_workspace_restart_preserves_exact_retained_bytes(media_app):
    import copy
    from pathlib import Path
    from amplifier_web.service import AppService
    from test_service import Runtime
    app = media_app
    args = media_args(app)
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    sid = args['sessionId']
    messages = copy.deepcopy(app._session(sid)['messages'])
    Path(app._session(sid)['workspace'], args['path']).unlink()
    directory, workspace = app.data_dir, app._session(sid)['workspace']
    await app.close()
    reopened = AppService(directory, workspace=workspace, runtime=Runtime())
    try:
        reopened.clients.attach('restart-media')
        with reopened.clients.bind('restart-media'):
            await reopened.dispatch('session.select', {'id': sid})
            items = (await reopened.outputs.media.describe(sid, args['messageId']))['items']
            assert items[0]['sha256'] == record['sha256']
            assert await reopened.outputs.media.bytes({**items[0], 'messageId': args['messageId']}) == gif()
            assert reopened.outputs.media.observation(reopened.outputs.record(sid, record['id']))['status'] == 'not_observed'
        assert reopened._session(sid)['messages'] == messages
    finally:
        await reopened.close()


async def test_explicit_fork_keeps_assistant_media_origin_and_canonical_context(media_app):
    import copy
    from amplifier_web.host.storage import SessionStore
    app = media_app
    args = media_args(app)
    sid = args['sessionId']
    record = (await app.dispatch('outputs.attachMedia', args))['result']
    # Native history is a normal saved context, not an invented UI message ID.
    store = SessionStore.for_app(app.data_dir, app._session(sid)['workspace'])
    native = [{'role': 'system', 'content': 'Keep system context.'},
              {'role': 'user', 'content': 'Keep this animation.'},
              {'role': 'assistant', 'content': app._session(sid)['messages'][-1]['text']}]
    store.save(sid, native, {}, preserve_system=True)
    raw = (store.directory(sid) / 'transcript.jsonl').read_bytes()
    messages = copy.deepcopy(app._session(sid)['messages'])
    await app.dispatch('session.fork', {'id': sid})
    fork = app._session()
    rows = app.outputs.store.list(fork['id'])['items']
    media = next(row for row in rows if row['kind'] == 'media')
    assert media['forkSource'] == {'sessionId': sid, 'outputId': record['id'], 'sha256': record['sha256']}
    assert media['origin']['messageId'] == args['messageId']
    assert media['sha256'] == record['sha256'] and media['id'] != record['id']
    assert store.load(fork['id'])[0] == native
    assert (store.directory(sid) / 'transcript.jsonl').read_bytes() == raw
    assert app._session(sid)['messages'] == messages
    app.clients.attach('fork-media')
    with app.clients.bind('fork-media'):
        await app.dispatch('session.select', {'id': fork['id']})
        assert await app.outputs.media.bytes({**media, 'messageId': args['messageId']}) == gif()
        with pytest.raises(ValueError):
            await app.outputs.media.bytes({**media, 'sessionId': sid, 'messageId': args['messageId']})


def canonical_corpus(app):
    """Real Foundation transcript corpus, not normal-turn delivery evidence."""
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_files import project_slug
    from amplifier_web.automatic_history import directory, read_transcript, display_message
    session = app._session()
    corpus = [{'role': 'user' if i % 2 == 0 else 'assistant',
               'content': f'Canonical message {i}: ' + ('**雪** [sample](https://example.org)\n' * 1200 if i == 1 else 'saved')}
              for i in range(240)]
    store = SessionStore.for_app(app.data_dir, session['workspace'])
    store.save(session['id'], corpus, {}, preserve_system=True)
    session.update(nativeProject=project_slug(session['workspace']), nativeIdentity=session['id'],
                   runtimeSessionId=session['id'], historyManaged=True, status='idle')
    page = read_transcript(session)
    session.update(messages=page['messages'], nativeRevision=page['revision'],
                   sharedHistoryOffset=page['offset'], sharedHistoryTotal=page['total'],
                   sharedHistoryUserTurnOffset=page['userOffset'])
    assert page['total'] == 240 and len(session['messages']) == 100
    old = display_message(corpus[1], 1, session)['id']
    assert old not in {row['id'] for row in session['messages']}
    return session, corpus, store, directory(session), old


async def test_canonical_240_tail_100_old_window_empty_attach_http_and_body_bounds(
        authenticated_client, tmp_path, monkeypatch):
    import copy
    import threading
    from amplifier_foundation.session import history as foundation_history
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_foundation.session.jsonl import TranscriptIndex
    from amplifier_web import automatic_history, conversation_navigation, media_message_identity
    from amplifier_web.server import create_app
    from test_service import Runtime
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                           voice=False, preload_providers=False, background_updates=False)
    service = app['service']
    await service.dispatch('session.create', {'title': 'Canonical authority corpus'})
    session, corpus, store, directory, old = canonical_corpus(service)
    raw = (directory / 'transcript.jsonl').read_bytes()
    tail = copy.deepcopy(session['messages'])
    reads, threads = [], []
    owner = threading.get_ident()
    real_positions, real_index = TranscriptIndex.read_positions, SessionHistoryStore.indexed_messages

    def positions(index, requested):
        requested = list(requested)
        reads.append(len(requested))
        return real_positions(index, requested)

    def indexed(reader):
        threads.append(threading.get_ident())
        return real_index(reader)

    monkeypatch.setattr(TranscriptIndex, 'read_positions', positions)
    # Warm the actual navigator seam; old article lives only in its 60-row
    # window, not in the canonical 100-row tail.
    page = conversation_navigation.query(session, message_id=old, window=True)
    assert len(page['messages']) == 60 and old in {row['id'] for row in page['messages']}
    assert reads == [60]
    monkeypatch.setattr(SessionHistoryStore, 'indexed_messages', indexed)

    def no_hydration(*args, **kwargs):
        pytest.fail('Media hydrated a transcript/display body')

    monkeypatch.setattr(automatic_history, 'read_transcript', no_hydration)
    service.clients.attach('canonical-browser')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'canonical-browser'
    sid = session['id']
    query = {'sessionId': sid, 'messageId': old}
    real_decode, real_resolve = foundation_history._decode, media_message_identity.resolve
    media_worker = threading.local()

    def resolve(*values):
        media_worker.active = True
        try:
            return real_resolve(*values)
        finally:
            media_worker.active = False

    def decode(*values):
        if getattr(media_worker, 'active', False):
            pytest.fail('Warm media identity decoded a transcript body instead of cached facts')
        return real_decode(*values)

    monkeypatch.setattr(media_message_identity, 'resolve', resolve)
    with monkeypatch.context() as cold:
        cold.setattr(foundation_history, '_decode', decode)
        cold.setattr(service.cold_display, 'hydrate', no_hydration)
        empty = await client.get('/api/outputs/media', params=query)
        assert empty.status == 200 and (await empty.json())['items'] == []
        args = {**media_args_without_hydration(session), 'messageId': old}
        # The ordinary dispatch preflight is not a media read boundary. This
        # corpus attach uses the owned method; delivery proof remains the
        # browser fixture's normal generation/assistant event/host bridge.
        receipt = await service.outputs.media.attach(args, 'ui', 'canonical-attach')
        record = receipt['result']
        fresh = await client.get('/api/outputs/media', params=query)
        assert fresh.status == 200 and (await fresh.json())['items'][0]['id'] == record['id']
        exact = {**query, 'version': str(record['version']), 'sha256': record['sha256']}
        route = '/api/outputs/' + record['id'] + '/media'
        response = await client.get(route, params=exact, headers={'Range': 'bytes=0-5'})
        assert response.status == 206 and await response.read() == gif()[:6]
        head = await client.head(route, params=exact)
        assert head.status == 200 and head.headers['Content-Length'] == str(len(gif()))
        observed = await client.post('/api/actions', json={
            'action': 'outputs.mediaReport', 'args': {
                **query, 'id': record['id'], 'version': record['version'], 'sha256': record['sha256'],
                'generation': 1, 'sequence': 1, 'renderState': 'ready', 'playback': 'paused'}})
        assert observed.status == 200 and (await observed.json())['accepted']
        for invalid in ('missing', '\x00malformed', 'x' * 201,
                        automatic_history.display_message(corpus[0], 0, session)['id']):
            refused = await client.get('/api/outputs/media', params={**query, 'messageId': invalid})
            assert refused.status == 400
    assert reads == [60]  # Warm descriptor/bytes/report never read message bodies.
    assert threads and owner not in threads
    assert dict.get(session, 'messages') == tail
    assert (directory / 'transcript.jsonl').read_bytes() == raw
    # Disappearance invalidates canonical authority even while the tail is
    # untouched. No empty-success response disguises the missing origin.
    store.save(sid, corpus[2:], {}, preserve_system=True)
    assert (await client.get('/api/outputs/media', params=query)).status == 400
    assert (await client.get(route, params=exact)).status == 400


def media_args_without_hydration(session):
    from pathlib import Path
    data = gif()
    Path(session['workspace'], 'clip.gif').write_bytes(data)
    return {'sessionId': session['id'], 'title': 'Canonical corpus animation',
            'path': 'clip.gif', 'expectedSha256': hashlib.sha256(data).hexdigest()}


def test_media_cold_snapshot_never_resolves_unrelated_display_resources():
    from amplifier_web.cold_display import ColdRecord
    from amplifier_web.media_message_identity import snapshot

    class NoDisplayIO:
        def execute(self, *args):
            pytest.fail('Identity snapshot opened a cold display body')

    session = ColdRecord({'id': 'cold-canonical', 'nativeProject': 'synthetic-project',
        'historyManaged': True, '_coldFields': {
            'messages': {'$resource': 'a' * 64}, 'execution': {'$resource': 'b' * 64},
            'configuration': {'$resource': 'c' * 64}}}, NoDisplayIO())
    value = snapshot(session, 'd' * 32)
    assert value['rows'] == [] and value['coldMessages'] == {'$resource': 'a' * 64}
    assert not dict.__contains__(session, 'messages') and not dict.__contains__(session, 'execution')


async def test_retained_web_alias_and_native_forgery_ambiguity(media_app):
    import copy
    from amplifier_web.automatic_history import display_message
    app = media_app
    session, corpus, _, _, old = canonical_corpus(app)
    canonical = display_message(corpus[1], 1, session)
    alias = {**canonical, 'id': 'retained-web-assistant', 'source': 'amplifier',
             'nativeMessageId': old}
    session.update(historyManaged=False, messages=[alias, *session['messages']])
    app.clients.attach('retained-browser')
    with app.clients.bind('retained-browser'):
        assert (await app.outputs.media.describe(session['id'], alias['id']))['items'] == []
        session['messages'].append({**alias, 'id': 'competing-web-assistant'})
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], alias['id'])
        session['messages'].pop()
        session['messages'].insert(0, copy.deepcopy(alias))
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], alias['id'])
        session['messages'].pop(0)
        session.update(historyManaged=True, messages=[{**canonical, 'id': 'forged-native-id'}])
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], 'forged-native-id')


async def test_hidden_canonical_assistant_id_does_not_grant_media_authority(media_app):
    from amplifier_web.automatic_history import display_identity
    app = media_app
    session, corpus, store, _, _ = canonical_corpus(app)
    internal = display_identity(session, 3, 'assistant', corpus[3]['content'])
    corpus[3]['metadata'] = {'ephemeral': True}
    store.save(session['id'], corpus, {}, preserve_system=True)
    app.clients.attach('hidden-canonical-browser')
    with app.clients.bind('hidden-canonical-browser'):
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], internal)


async def test_old_canonical_media_restart_reopen_preserves_origin_without_display_hydration(media_app):
    import copy
    from pathlib import Path
    from amplifier_web.service import AppService
    from test_service import Runtime
    app = media_app
    session, _, _, directory, old = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': old}
    saved = (await app.outputs.media.attach(args, 'ui', 'canonical-reopen-attach'))['result']
    sid, workspace, data_dir = session['id'], session['workspace'], app.data_dir
    raw = (directory / 'transcript.jsonl').read_bytes()
    Path(workspace, args['path']).unlink()
    await app.close()
    reopened = AppService(data_dir, workspace=workspace, runtime=Runtime())
    try:
        reopened.clients.attach('canonical-reopen-browser')
        with reopened.clients.bind('canonical-reopen-browser'):
            await reopened.dispatch('session.select', {'id': sid})
            source = reopened._session(sid, hydrate=False)
            markers = copy.deepcopy(dict.get(source, '_coldFields', {}))
            items = (await reopened.outputs.media.describe(sid, old))['items']
            assert len(items) == 1 and items[0]['id'] == saved['id']
            assert await reopened.outputs.media.bytes({**items[0], 'messageId': old}) == gif()
            if 'messages' in markers:
                assert not dict.__contains__(source, 'messages')
                assert dict.get(source, '_coldFields', {}).get('messages') == markers['messages']
            else:
                assert len(dict.get(source, 'messages', [])) == 100
        assert (directory / 'transcript.jsonl').read_bytes() == raw
    finally:
        await reopened.close()


async def test_cold_retained_web_alias_requires_bounded_saved_alias_descriptor(media_app):
    """ROOT acceptance discriminator: cold alias descriptor seam is missing.

    The canonical ID can qualify without hydrating display storage, but an
    older retained web ID also needs its exact saved canonical binding. Do not
    promote rejection of that genuine alias to acceptance or hydrate all bodies.
    """
    from amplifier_web.automatic_history import display_message
    from amplifier_web.cold_display import ColdRecord
    from amplifier_web.resource_files import put
    app = media_app
    session, corpus, _, _, old = canonical_corpus(app)
    alias = {**display_message(corpus[1], 1, session), 'id': 'cold-retained-assistant',
             'nativeMessageId': old, 'source': 'amplifier'}
    session.update(historyManaged=False, messages=[alias, *session['messages']])
    app.clients.attach('cold-retained-browser')
    async with app.lock:
        reference = put(app.db, session['messages'])
        app.db.commit()
        metadata = {key: value for key, value in session.items() if key != 'messages'}
        chilled = ColdRecord({**metadata, '_coldFields': {'messages': reference}}, app.db)
        position = app.state['sessions'].index(session)
        app.state['sessions'][position] = chilled
    with app.clients.bind('cold-retained-browser'):
        assert (await app.outputs.media.describe(session['id'], old))['items'] == []
        assert not dict.__contains__(chilled, 'messages')
        assert (await app.outputs.media.describe(session['id'], alias['id']))['items'] == []
        assert not dict.__contains__(chilled, 'messages')


@pytest.mark.parametrize('change', ['role', 'generation', 'execution', 'namespace'])
async def test_canonical_bytes_revalidate_entire_operation_scope(media_app, monkeypatch, change):
    import asyncio
    import threading
    from amplifier_web import output_media
    app = media_app
    session, corpus, store, _, old = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': old}
    record = (await app.outputs.media.attach(args, 'ui', 'scope-attach'))['result']
    app.clients.attach('scope-browser')
    entered, release = threading.Event(), threading.Event()
    real = output_media._saved_bytes

    def blocked(*values):
        entered.set()
        assert release.wait(10)
        return real(*values)

    monkeypatch.setattr(output_media, '_saved_bytes', blocked)
    with app.clients.bind('scope-browser'):
        pending = asyncio.create_task(app.outputs.media.bytes({**record, 'messageId': old}))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            async with app.lock:
                if change == 'role':
                    corpus[1]['role'] = 'user'
                    store.save(session['id'], corpus, {}, preserve_system=True)
                elif change == 'generation':
                    session['collaborationGeneration'] = {'id': 'next-generation'}
                elif change == 'execution':
                    session['executionRevision'] = session.get('executionRevision', 0) + 1
                else:
                    session['nativeIdentity'] = 'different-canonical-session'
            release.set()
            with pytest.raises(ValueError):
                await pending
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)


async def test_canonical_old_media_fork_requires_exact_surviving_origin(media_app):
    """Acceptance discriminator for ROOT's missing transcript index-map seam.

    No xfail or same-text substitute: this source return does not implement
    canonical remapping across the outside-owned fork boundary.
    """
    from amplifier_web.automatic_history import display_message
    app = media_app
    session, corpus, _, directory, old = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': old}
    record = (await app.outputs.media.attach(args, 'ui', 'old-fork-attach'))['result']
    raw = (directory / 'transcript.jsonl').read_bytes()
    sid = session['id']
    await app.dispatch('session.fork', {'id': sid})
    target = app._session()
    rows = [row for row in app.outputs.store.list(target['id'])['items'] if row['kind'] == 'media']
    assert len(rows) == 1, 'Canonical media lost across the exact preserved fork boundary'
    kept = next(row for row in target['messages']
                if row.get('nativeIndex') == 1 and row['role'] == 'assistant')
    assert rows[0]['origin']['messageId'] == kept['id']
    assert kept['id'] == display_message(corpus[1], 1, target)['id']
    assert rows[0]['forkSource']['outputId'] == record['id']
    app.clients.attach('fork-old-browser')
    with app.clients.bind('fork-old-browser'):
        await app.dispatch('session.select', {'id': target['id']})
        assert await app.outputs.media.bytes({**rows[0], 'messageId': kept['id']}) == gif()
    assert (directory / 'transcript.jsonl').read_bytes() == raw


@pytest.mark.parametrize('change', ['role', 'transfer-fence'])
async def test_canonical_commit_revalidates_after_owned_resource_file_await(media_app, monkeypatch, change):
    import asyncio
    import threading
    from amplifier_web import resource_files as files
    app = media_app
    session, corpus, store, directory, old = canonical_corpus(app)
    args = {**media_args_without_hydration(session), 'messageId': old}
    entered, release = threading.Event(), threading.Event()
    real_write = files.write_prepared

    def blocked(root, prepared):
        if threading.current_thread() is not threading.main_thread() and '"encoding":"base64"' in prepared.text:
            entered.set()
            assert release.wait(10)
        return real_write(root, prepared)

    monkeypatch.setattr(files, 'write_prepared', blocked)
    pending = asyncio.create_task(app.outputs.media.attach(args, 'ui', 'late-canonical-change'))
    marker = directory / 'transfer-fence.json'
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert app.lock.locked() and app.db.in_transaction
        # External canonical writers/fence files do not acquire the web lock.
        if change == 'role':
            corpus[1]['role'] = 'user'
            store.save(session['id'], corpus, {}, preserve_system=True)
        else:
            marker.write_text('{"synthetic":true}')
        release.set()
        with pytest.raises(ValueError):
            await pending
        assert not app.db.in_transaction and session.get('mediaRevision', 0) == 0
        assert app.outputs.store.list(session['id'])['items'] == []
        assert app.db.execute('SELECT 1 FROM output_receipts WHERE id=?', ('late-canonical-change',)).fetchone() is None
        assert (await app.dispatch('shell.query', {'clientId': 'after-canonical-change',
                                                 'instanceId': 'chats'}))['accepted']
    finally:
        release.set()
        marker.unlink(missing_ok=True)
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('cancel_media', [False, True])
async def test_owned_media_writer_excludes_real_shell_http_and_receipts(
        authenticated_client, tmp_path, monkeypatch, cancel_media):
    import asyncio
    import threading
    from amplifier_web import resource_files as files
    from amplifier_web.server import create_app
    from test_service import Runtime
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                           voice=False, preload_providers=False, background_updates=False)
    service = app['service']
    await service.dispatch('session.create', {'title': 'Transaction owner corpus'})
    await service.on_runtime_event('assistant.message', {'sessionId': service._session()['id'],
                                                        'text': 'Owned animation.'})
    args = media_args(service)
    for identity in ('owner-browser', 'other-browser', 'fresh-browser'):
        service.clients.attach(identity)
    for identity in ('owner-browser', 'other-browser'):
        await service.dispatch('shell.query', {'clientId': identity, 'instanceId': 'chats'})
    assert service.shell.get('client', 'fresh-browser') is None
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    waiting = asyncio.Event()
    waiters, transactions, body_ids = [], [], []
    real_write = files.write_prepared

    original_acquire = service.lock.acquire

    async def acquire():
        if (service.lock.locked() and entered.is_set() and not release.is_set()
                and service.clients.current.get() in {'owner-browser', 'other-browser', 'fresh-browser'}):
            waiters.append(asyncio.current_task())
            if len(waiters) >= 5:
                waiting.set()
        return await original_acquire()

    monkeypatch.setattr(service.lock, 'acquire', acquire)

    def blocked(directory, prepared):
        if threading.current_thread() is not threading.main_thread() and '"encoding":"base64"' in prepared.text:
            body_ids.append(prepared.identity)
            entered.set()
            assert release.wait(10)
            try:
                return real_write(directory, prepared)
            finally:
                finished.set()
        return real_write(directory, prepared)

    def trace(sql):
        if entered.is_set() and not release.is_set() and sql.strip().upper().startswith(('COMMIT', 'ROLLBACK')):
            transactions.append(sql)

    monkeypatch.setattr(files, 'write_prepared', blocked)
    service.db.set_trace_callback(trace)
    client = await authenticated_client(app)
    pending = asyncio.create_task(service.outputs.media.attach(args, 'ui', 'owned-media-command'))
    requests = []
    cancelled = None
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert service.lock.locked() and service.db.in_transaction

        async def post(identity, action, values, command_id):
            return await client.post('/api/actions', headers={'X-Amplifier-Client': identity},
                json={'action': action, 'args': {'clientId': identity, **values}, 'id': command_id})

        requests = [
            asyncio.create_task(client.get('/api/shell', params={'clientId': 'fresh-browser'})),
            asyncio.create_task(post('owner-browser', 'shell.report',
                {'revision': 0, 'instances': {'chats': 'ready', 'workspaces': 'ready'}}, 'owned-report')),
            asyncio.create_task(post('other-browser', 'shell.query', {'instanceId': 'chats'}, 'owned-query')),
            asyncio.create_task(post('owner-browser', 'shell.view.update',
                {'instanceId': 'chats', 'patch': {'navFilter': 'owner-only'}}, 'owned-view')),
        ]
        with service.clients.bind('owner-browser'):
            cancelled = asyncio.create_task(service.shell.dispatch('shell.query',
                {'clientId': 'owner-browser', 'instanceId': 'chats'}, 'ui', 'cancelled-waiter'))
        await asyncio.wait_for(waiting.wait(), 10)
        assert all(not request.done() for request in requests)
        assert service.shell.get('client', 'fresh-browser') is None
        assert transactions == [] and service.db.in_transaction
        # This authenticated reader does not initialize or commit shell state.
        assert (await client.get('/api/actions')).status == 200
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert service.shell.get('command', 'cancelled-waiter') is None
        if cancel_media:
            pending.cancel()
            await asyncio.sleep(0)  # Give cancellation to the owned worker wait.
            assert not pending.done() and not finished.is_set() and service.db.in_transaction
        release.set()
        if cancel_media:
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            assert (await pending)['accepted']
        assert finished.is_set()
        responses = await asyncio.wait_for(asyncio.gather(*requests), 10)
        assert all(response.status == 200 for response in responses)
        first, report, query, view = [await response.json() for response in responses]
        assert first['revision'] == 0 and report['accepted'] and query['accepted'] and view['accepted']
        assert service.shell.client('owner-browser')['views']['chats']['view']['navFilter'] == 'owner-only'
        assert service.shell.client('other-browser')['views'].get('chats', {}).get('view', {}).get('navFilter', '') == ''
        assert service.shell.client('fresh-browser')['views'] == {}
        for identity in ('owned-report', 'owned-query', 'owned-view'):
            assert service.shell.get('command', identity)['receipt']['accepted']
        for identity in ('owner-browser', 'other-browser'):
            assert service.shell.client(identity)['views'].get('chats', {}).get('generation', 0) == 0
        assert not service.db.in_transaction
        if cancel_media:
            assert service.outputs.store.list(args['sessionId'])['items'] == []
            assert service.db.execute('SELECT 1 FROM output_receipts WHERE id=?',
                                      ('owned-media-command',)).fetchone() is None
            assert service.db.execute('SELECT 1 FROM state_resources WHERE id=?', (body_ids[0],)).fetchone() is None
            # The writer has settled before normal reconciliation can retire
            # its unindexed file; a surviving orphan is a required failure.
            async with service.lock:
                cursor = None
                while True:
                    cursor, result = files.sweep_unindexed(service.db, service._state, cursor)
                    assert not result['blocked']
                    if result['complete']:
                        break
            assert not (files.root(service.db) / (body_ids[0] + '.json')).exists()
        again = await service.dispatch('shell.query',
            {'clientId': 'owner-browser', 'instanceId': 'chats'}, command_id='after-owned-write')
        assert again['accepted']
    finally:
        release.set()
        service.db.set_trace_callback(None)
        if cancelled and not cancelled.done():
            cancelled.cancel()
        await asyncio.gather(pending, *requests, *([cancelled] if cancelled else []), return_exceptions=True)


def cold_alias_corpus(app, *, native_message_id=True):
    from amplifier_web.automatic_history import display_message
    session, corpus, store, directory, canonical = canonical_corpus(app)
    alias = {**display_message(corpus[1], 1, session),
             'id': 'saved-web-assistant', 'source': 'amplifier'}
    if native_message_id:
        alias['nativeMessageId'] = canonical
    session.update(historyManaged=False, messages=[alias, *session['messages']])
    return session, corpus, store, directory, canonical


async def freeze_alias_display(app, session, *, rows=None, hints=False):
    """Synthetic legacy reference, not a substitute for real producer tests."""
    from amplifier_web.cold_display import ColdRecord
    from amplifier_web.media_saved_aliases import binding_hint
    from amplifier_web.resource_files import put
    async with app.lock:
        body = dict.get(session, 'messages') if rows is None else rows
        reference = put(app.db, body)
        if hints:
            reference = binding_hint(session, reference, body)
        app.db.commit()
        metadata = dict(dict.items(session))
        metadata.pop('messages', None)
        metadata['_coldFields'] = {**metadata.get('_coldFields', {}), 'messages': reference}
        chilled = ColdRecord(metadata, app.db)
        app._state['sessions'][app._state['sessions'].index(session)] = chilled
    return chilled, reference


def test_media_root_default_is_only_missing_not_falsey():
    from amplifier_web.media_message_identity import snapshot, validate
    session = {'id': 'synthetic-root', 'messages': [
        {'id': 'saved-assistant', 'role': 'assistant', 'text': 'Retained'}]}
    before = snapshot(session, 'saved-assistant')
    assert before['sessionKind'] == 'root'
    session['sessionKind'] = 'root'
    assert snapshot(session, 'saved-assistant') == before
    proof = {'snapshot': before}
    assert validate(session, 'saved-assistant', proof)['role'] == 'assistant'
    for invalid in (None, '', False, 'worker', 'internal'):
        session['sessionKind'] = invalid
        assert snapshot(session, 'saved-assistant')['sessionKind'] == invalid
        with pytest.raises(ValueError, match='identity changed'):
            validate(session, 'saved-assistant', proof)
    session['sessionKind'] = 'root'
    for field, changed in (
        ('id', 'different-root'), ('workspace', '/synthetic/other'),
        ('parentId', 'different-parent'), ('nativeRevision', [1, 2]),
        ('executionRevision', 2), ('collaborationGeneration', {'id': 'different'})):
        session[field] = changed
        with pytest.raises(ValueError, match='identity changed'):
            validate(session, 'saved-assistant', proof)
        session.pop(field)
        if field == 'id':
            session['id'] = 'synthetic-root'


@pytest.mark.parametrize('producer', ['cold-display', 'session-projection'])
async def test_real_cold_alias_producers_and_reopen(media_app, monkeypatch, producer):
    import copy
    from amplifier_web import cold_display, state_records
    from amplifier_web.service import AppService
    from test_service import Runtime
    app = media_app
    session, _, _, directory, canonical = cold_alias_corpus(app, native_message_id=False)
    sid = session['id']
    raw = (directory / 'transcript.jsonl').read_bytes()
    home, workspace = app.data_dir, session['workspace']
    if producer == 'cold-display':
        # Real retirement must release the list, not just create a descriptor.
        app._state['selectedSessionId'] = None
        monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
        assert sid in app.cold_display.retire(force=True)
        assert not dict.__contains__(session, 'messages')
        reference = copy.deepcopy(dict.get(session, '_coldFields')['messages'])
    else:
        app._save(session_ids={sid}, record_only=True)
        pointer = next(row['$viewPayload'] for row in state_records.load(app.db)['sessions']
                       if row['id'] == sid)
        saved = cold_display.load(app.db, pointer)
        reference = saved['_coldFields']['messages']
        assert dict.__contains__(session, 'messages'), 'storage projection must not evict live aliases'
    hint = reference['mediaBindings']
    assert hint['resource'] == reference['$resource'] and hint['scope'] == session['nativeIdentity']
    assert hint['revision'] == session['nativeRevision']
    assert hint['project'] == session['nativeProject']
    assert all('text' not in row for row in hint['bindings'])
    assert any(row['id'] == 'saved-web-assistant' and row['nativeIndex'] == 1 for row in hint['bindings'])
    app._save(session_ids={sid}, record_only=True)
    await app.close()
    reopened = AppService(home, workspace=workspace, runtime=Runtime())
    try:
        reopened.clients.attach('producer-reopen-browser')
        reopened.clients.records['producer-reopen-browser']['selectedSessionId'] = sid
        source = reopened._session(sid, hydrate=False)
        markers = copy.deepcopy(dict.get(source, '_coldFields'))
        assert markers['messages'] == reference
        with reopened.clients.bind('producer-reopen-browser'):
            assert (await reopened.outputs.media.describe(sid, canonical))['items'] == []
            assert (await reopened.outputs.media.describe(sid, 'saved-web-assistant'))['items'] == []
        assert dict.get(source, '_coldFields') == markers
        assert not dict.__contains__(source, 'messages')
        assert (directory / 'transcript.jsonl').read_bytes() == raw
    finally:
        await reopened.close()


@pytest.mark.parametrize('native_message_id', [False, True])
async def test_legacy_cold_canonical_spelling_plus_unique_retained_alias(media_app, native_message_id):
    from amplifier_web.automatic_history import display_message
    app = media_app
    session, corpus, _, _, canonical = cold_alias_corpus(app, native_message_id=native_message_id)
    rows = [*session['messages'], display_message(corpus[1], 1, session)]
    chilled, reference = await freeze_alias_display(app, session, rows=rows)
    assert set(reference) == {'$resource', 'bytes'}, 'legacy bare references stay supported'
    app.clients.attach('legacy-pair-browser')
    with app.clients.bind('legacy-pair-browser'):
        for identity in (canonical, 'saved-web-assistant'):
            assert (await app.outputs.media.describe(session['id'], identity))['items'] == []
    assert dict.get(chilled, '_coldFields')['messages'] == reference
    assert not dict.__contains__(chilled, 'messages')


@pytest.mark.parametrize('defect', [
    'duplicate-id', 'duplicate-id-other-index', 'competing-alias', 'conflicting-text', 'wrong-native-id',
    'wrong-index', 'bool-index', 'user', 'malformed-role', 'malformed-text', 'observation', 'observation-id',
    'malformed-id', 'forged-native',
])
async def test_cold_saved_alias_conflicts_fail_closed(media_app, defect):
    app = media_app
    session, corpus, _, _, canonical = cold_alias_corpus(app)
    rows = [dict(row) for row in session['messages']]
    if defect == 'duplicate-id':
        rows.append(dict(rows[0]))
    elif defect == 'duplicate-id-other-index':
        from amplifier_web.automatic_history import display_message
        other = display_message(corpus[3], 3, session)
        rows.append({**other, 'id': rows[0]['id'], 'source': 'amplifier',
                     'nativeMessageId': other['id']})
    elif defect == 'competing-alias':
        rows.append({**rows[0], 'id': 'competing-saved-alias'})
    elif defect == 'conflicting-text':
        rows[0]['text'] += 'not canonical'
    elif defect == 'wrong-native-id':
        rows[0]['nativeMessageId'] = '0' * 32
    elif defect == 'wrong-index':
        rows[0]['nativeIndex'] = 3
    elif defect == 'bool-index':
        rows[0]['nativeIndex'] = True
    elif defect == 'user':
        rows[0]['role'] = 'user'
    elif defect == 'malformed-role':
        rows[0]['role'] = {'malformed': True}
    elif defect == 'malformed-text':
        rows[0]['text'] = {'malformed': True}
    elif defect == 'observation':
        rows[0]['observation'] = {'kind': 'tool'}
    elif defect == 'observation-id':
        rows[0]['observation_id'] = 'observation'
    elif defect == 'malformed-id':
        rows[0]['id'] = '\x00malformed'
    else:
        session['historyManaged'] = True
        rows[0]['source'] = 'native'
    chilled, _ = await freeze_alias_display(app, session, rows=rows, hints=True)
    app.clients.attach('invalid-alias-browser')
    with app.clients.bind('invalid-alias-browser'):
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], rows[0]['id'])
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], canonical)
    assert not dict.__contains__(chilled, 'messages')


async def test_untrusted_compact_alias_hint_cannot_replace_saved_body(media_app):
    app = media_app
    session, _, _, _, canonical = cold_alias_corpus(app)
    chilled, reference = await freeze_alias_display(app, session, rows=session['messages'][1:], hints=True)
    reference['mediaBindings']['bindings'].insert(0, {
        'id': 'hint-only-alias', 'role': 'assistant', 'nativeIndex': 1, 'nativeMessageId': canonical})
    reference['mediaBindings'].update(scope='forged-scope', revision=[0], resource='0' * 64)
    app.clients.attach('hint-browser')
    with app.clients.bind('hint-browser'):
        assert (await app.outputs.media.describe(session['id'], canonical))['items'] == []
        with pytest.raises(ValueError, match='assistant'):
            await app.outputs.media.describe(session['id'], 'hint-only-alias')
    assert not dict.__contains__(chilled, 'messages')


@pytest.mark.parametrize('change', ['reference', 'index', 'revision', 'canonical-role', 'scope'])
async def test_cold_alias_discovery_rechecks_after_worker_await(media_app, monkeypatch, change):
    import asyncio
    import threading
    from amplifier_web import media_saved_aliases, resource_files
    app = media_app
    session, corpus, store, _, _ = cold_alias_corpus(app)
    chilled, reference = await freeze_alias_display(app, session)
    entered, release = threading.Event(), threading.Event()
    real = media_saved_aliases.SavedAliases.resolve

    def blocked(instance, *args):
        entered.set()
        assert release.wait(10)
        return real(instance, *args)

    monkeypatch.setattr(media_saved_aliases.SavedAliases, 'resolve', blocked)
    pending = asyncio.create_task(app.outputs.media.identity(session['id'], 'saved-web-assistant'))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        async with app.lock:
            if change == 'reference':
                replacement = resource_files.put(app.db, [{'id': 'other', 'role': 'user', 'text': 'other'}])
                app.db.commit()
                dict.get(chilled, '_coldFields')['messages'] = replacement
            elif change == 'index':
                app.db.execute('UPDATE state_resources SET value=? WHERE id=?',
                               ('{"$blob":"' + 'f' * 64 + '"}', reference['$resource']))
                app.db.commit()
            elif change == 'revision':
                chilled['nativeRevision'] = [0, 0]
            elif change == 'canonical-role':
                corpus[1]['role'] = 'user'
                store.save(session['id'], corpus, {}, preserve_system=True)
            else:
                chilled['nativeIdentity'] = 'different-native-scope'
        release.set()
        with pytest.raises(ValueError):
            await pending
        assert not dict.__contains__(chilled, 'messages')
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('boundary', ['descriptor', 'guard', 'report'])
async def test_cold_alias_later_boundaries_recheck_detached_index(media_app, boundary):
    app = media_app
    session, _, _, _, _ = cold_alias_corpus(app)
    chilled, reference = await freeze_alias_display(app, session)
    record = (await app.outputs.media.attach({
        **media_args_without_hydration(session), 'messageId': 'saved-web-assistant'},
        'ui', 'saved-alias-boundary-attach'))['result']
    app.clients.attach('proof-boundary-browser')
    with app.clients.bind('proof-boundary-browser'):
        proof = await app.outputs.media.identity(session['id'], 'saved-web-assistant', scoped=True)
        values = {**record, 'messageId': 'saved-web-assistant', 'generation': 1, 'sequence': 1,
                  'renderState': 'ready', 'playback': 'paused'}
        async with app.lock:
            app.db.execute('UPDATE state_resources SET value=? WHERE id=?',
                           ('{"$blob":"' + 'f' * 64 + '"}', reference['$resource']))
            app.db.commit()
            with pytest.raises(ValueError, match='resource index changed'):
                if boundary == 'descriptor':
                    app.outputs.media.descriptors(session['id'], 'saved-web-assistant', proof)
                elif boundary == 'guard':
                    app.outputs.media.guard(values, proof=proof)
                else:
                    app.outputs.media.report(values, 'ui', proof=proof)
    assert not dict.__contains__(chilled, 'messages')


async def test_cold_alias_final_attach_rechecks_without_recursive_identity(media_app, monkeypatch):
    from amplifier_web import resource_files
    app = media_app
    session, _, _, _, _ = cold_alias_corpus(app)
    chilled, reference = await freeze_alias_display(app, session)
    real = resource_files.put_prepared_async

    async def change_index_after_write(db, prepared):
        body = await real(db, prepared)
        assert app.lock.locked() and db.in_transaction
        # A synchronous owner-thread hook changes the same transaction index.
        # Final publication must freshly detach, not recursively acquire lock.
        db.execute('UPDATE state_resources SET value=? WHERE id=?',
                   ('{"$blob":"' + 'f' * 64 + '"}', reference['$resource']))
        return body

    monkeypatch.setattr(resource_files, 'put_prepared_async', change_index_after_write)
    with pytest.raises(ValueError):
        await app.outputs.media.attach({
            **media_args_without_hydration(session), 'messageId': 'saved-web-assistant'},
            'ui', 'saved-alias-final-index')
    assert not app.db.in_transaction
    assert app.outputs.store.list(session['id'])['items'] == []
    assert app.db.execute('SELECT 1 FROM output_receipts WHERE id=?',
                          ('saved-alias-final-index',)).fetchone() is None
    assert not dict.__contains__(chilled, 'messages')


async def test_cold_alias_warm_http_descriptor_head_range_report_read_no_display_bodies(
        authenticated_client, tmp_path, monkeypatch):
    import asyncio
    import copy
    import threading
    from amplifier_foundation.session import history as foundation_history
    from amplifier_web import conversation_navigation, media_saved_aliases, resource_files
    from amplifier_web.server import create_app
    from test_service import Runtime
    server = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                              voice=False, preload_providers=False, background_updates=False)
    app = server['service']
    await app.dispatch('session.create', {'title': 'Saved alias HTTP corpus'})
    session, _, _, directory, canonical = cold_alias_corpus(app, native_message_id=False)
    chilled, reference = await freeze_alias_display(app, session)
    markers = copy.deepcopy(dict.get(chilled, '_coldFields'))
    raw = (directory / 'transcript.jsonl').read_bytes()
    # Warm canonical facts, not transcript rows. The media resolver uses the
    # same worker-only seam and must not invoke its returned body reader.
    await asyncio.to_thread(conversation_navigation.source, {
        **dict(dict.items(chilled)), 'historyManaged': True})
    path = resource_files.root(app.db) / (reference['$resource'] + '.json')
    info = path.stat()
    evidence = (info.st_dev, info.st_ino)
    counts = {'reads': 0, 'bytes': 0, 'displayDecode': 0, 'canonicalDecode': 0}
    threads, warm = [], False
    owner = threading.get_ident()
    real_fdopen = resource_files.os.fdopen
    real_string = media_saved_aliases.DisplayJSON.string
    real_decode = foundation_history._decode

    class CountingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            assert not warm, 'Warm media opened/read the saved display body'
            assert 0 <= size <= 64 * 1024, 'Display reads must be bounded'
            result = self.stream.read(size)
            counts['reads'] += 1
            counts['bytes'] += len(result)
            threads.append(threading.get_ident())
            return result

    def fdopen(fd, *args, **kwargs):
        current = os.fstat(fd)
        stream = real_fdopen(fd, *args, **kwargs)
        return CountingStream(stream) if (current.st_dev, current.st_ino) == evidence else stream

    def string(parser, *args, **kwargs):
        assert not warm, 'Warm media decoded saved display JSON'
        counts['displayDecode'] += 1
        return real_string(parser, *args, **kwargs)

    def decode(*args, **kwargs):
        counts['canonicalDecode'] += 1
        assert not warm, 'Warm media decoded canonical transcript bodies'
        return real_decode(*args, **kwargs)

    monkeypatch.setattr(resource_files.os, 'fdopen', fdopen)
    monkeypatch.setattr(media_saved_aliases.DisplayJSON, 'string', string)
    monkeypatch.setattr(foundation_history, '_decode', decode)
    app.clients.attach('saved-alias-http-browser')
    client = await authenticated_client(server)
    client.session.headers['X-Amplifier-Client'] = 'saved-alias-http-browser'
    sid = session['id']
    await app.outputs.media.identity(sid, canonical)
    record = (await app.outputs.media.attach({
        **media_args_without_hydration(session), 'messageId': 'saved-web-assistant'},
        'ui', 'saved-alias-http-attach'))['result']
    values = {**record, 'messageId': 'saved-web-assistant'}
    with app.clients.bind('saved-alias-http-browser'):
        assert await app.outputs.media.bytes(values) == gif()
    assert counts['reads'] > 0 and counts['bytes'] >= reference['bytes']
    assert counts['displayDecode'] > 0 and threads and owner not in threads
    before = dict(counts)
    warm = True
    query = {'sessionId': sid, 'messageId': 'saved-web-assistant'}
    exact = {**query, 'version': str(record['version']), 'sha256': record['sha256']}
    route = '/api/outputs/' + record['id'] + '/media'
    for sequence in (1, 2):
        descriptor = await client.get('/api/outputs/media', params=query)
        assert descriptor.status == 200
        assert (await descriptor.json())['items'][0]['id'] == record['id']
        canonical_descriptor = await client.get('/api/outputs/media', params={**query, 'messageId': canonical})
        assert canonical_descriptor.status == 200
        head = await client.head(route, params=exact)
        assert head.status == 200 and head.headers['Content-Length'] == str(len(gif()))
        response = await client.get(route, params=exact, headers={'Range': 'bytes=0-5'})
        assert response.status == 206 and await response.read() == gif()[:6]
        observed = await client.post('/api/actions', json={
            'action': 'outputs.mediaReport', 'args': {
                **query, 'id': record['id'], 'version': record['version'],
                'sha256': record['sha256'], 'generation': 1, 'sequence': sequence,
                'renderState': 'ready', 'playback': 'paused'}})
        assert observed.status == 200 and (await observed.json())['accepted']
    assert counts == before
    assert dict.get(chilled, '_coldFields') == markers and not dict.__contains__(chilled, 'messages')
    assert (directory / 'transcript.jsonl').read_bytes() == raw


@pytest.mark.parametrize('ensure_ascii', [False, True])
def test_saved_alias_incremental_full_text_hash_matches_default_json_semantics(tmp_path, ensure_ascii):
    import json
    from amplifier_web.automatic_history import display_identity
    from amplifier_web.media_saved_aliases import DisplayJSON, row_identity
    # Include multibyte boundary splits, JSON control escapes, surrogate pairs,
    # and default ", " separators. Only the existing text allocation is large.
    text = ('雪😀"\\\n\t\b\f\r\x00/' * 20000) + 'full trailing identity'
    row = {'text': text, 'id': 'saved-web-assistant', 'role': 'assistant', 'nativeIndex': 7}
    path = tmp_path / 'display.json'
    path.write_text(json.dumps([row], ensure_ascii=ensure_ascii), encoding='utf-8')
    value = {'id': 'synthetic', 'nativeIdentity': 'native-scope'}
    expected = display_identity(value, 7, 'assistant', text)
    with path.open('rb') as stream:
        rows = list(DisplayJSON(stream).rows())
        assert len(rows) == 1 and 'text' not in rows[0]
        assert row_identity(stream, value, rows[0]) == expected


def test_saved_alias_projection_bounded_memory_full_hash_and_streamed_history(tmp_path):
    import json
    import sqlite3
    import tracemalloc
    from amplifier_web.automatic_history import display_identity
    from amplifier_web.media_saved_aliases import SavedAliases
    from amplifier_web.resource_files import detach, put
    db = sqlite3.connect(tmp_path / 'state.sqlite')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        value = {'id': 'synthetic', 'nativeIdentity': 'native-scope'}
        text = '雪\\\n"' * 250000
        canonical = display_identity(value, 1, 'assistant', text)
        rows = [{'id': 'unrelated-' + str(i), 'role': 'user', 'text': 'small'} for i in range(30000)]
        rows.insert(15000, {'id': 'saved-web-assistant', 'role': 'assistant', 'nativeIndex': 1, 'text': text})
        reference = put(db, rows)
        db.commit()
        source = detach(db, reference['$resource'])
        value['coldMessages'] = reference
        del rows, text
        facts = [(canonical, 'assistant', True, 1, None)]
        aliases = SavedAliases()
        tracemalloc.start()
        try:
            fact, _ = aliases.resolve(source, value, 'saved-web-assistant', [1, 2], facts)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert fact == facts[0] and peak < 1024 * 1024
        assert len(aliases.cache) == 1
        assert 'text' not in repr(list(aliases.cache.values()))
        # A last-byte change matters even beyond the requested row/large text.
        path = source.directory / (source.identity + '.json')
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + b' ')
        with pytest.raises(ValueError):
            aliases.resolve(source, value, 'saved-web-assistant', [1, 2], facts)
    finally:
        db.close()


def test_detached_projection_validates_complete_hash_index_and_path_evidence(tmp_path):
    import json
    import sqlite3
    from amplifier_web.resource_files import Detached, detach, project_detached, put
    db = sqlite3.connect(tmp_path / 'state.sqlite')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        reference = put(db, ['prefix', 'suffix'])
        db.commit()
        source = detach(db, reference['$resource'])
        path = source.directory / (source.identity + '.json')
        assert project_detached(source, lambda stream: stream.read(1))[0] == b'['
        wrong_index = Detached(source.directory, source.identity, json.dumps({'$blob': 'f' * 64}))
        with pytest.raises(ValueError, match='index'):
            project_detached(wrong_index, lambda stream: stream.read(1))
        raw = path.read_bytes()
        path.write_bytes(raw[:-2] + b'x' + raw[-1:])
        with pytest.raises(ValueError, match='content'):
            project_detached(source, lambda stream: stream.read(1))
        path.write_bytes(raw)

        def replace_during_projection(stream):
            prefix = stream.read(1)
            replacement = path.with_suffix('.replacement')
            replacement.write_bytes(raw)
            replacement.replace(path)
            return prefix

        with pytest.raises(ValueError, match='changed'):
            project_detached(source, replace_during_projection)
        target = path.with_suffix('.target')
        path.rename(target)
        path.symlink_to(target)
        with pytest.raises((ValueError, OSError)):
            project_detached(source, lambda stream: stream.read(1))
    finally:
        db.close()


def test_saved_alias_cache_coalesces_workers_and_bounds_retained_descriptors(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    from amplifier_web.automatic_history import display_identity
    from amplifier_web import media_saved_aliases
    from amplifier_web.resource_files import detach, put
    db = sqlite3.connect(tmp_path / 'state.sqlite')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        value = {'id': 'synthetic', 'nativeIdentity': 'native-scope'}
        rows = [{'id': 'alias-' + str(i), 'role': 'assistant', 'nativeIndex': i, 'text': 'full text'}
                for i in range(40)]
        facts = [(display_identity(value, i, 'assistant', 'full text'), 'assistant', True, i, None)
                 for i in range(40)]
        reference = put(db, rows)
        db.commit()
        source = detach(db, reference['$resource'])
        value['coldMessages'] = reference
        aliases = media_saved_aliases.SavedAliases()
        real, projections = media_saved_aliases.project_detached, []

        def counted(*args):
            projections.append(args[0])
            return real(*args)

        monkeypatch.setattr(media_saved_aliases, 'project_detached', counted)
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(aliases.resolve, source, value, 'alias-0', [1, 2], facts)
                       for _ in range(8)]
            assert all(future.result()[0] == facts[0] for future in futures)
        assert len(projections) == 1, 'same concurrent proof must stream only once'
        for i in range(1, 40):
            assert aliases.resolve(source, value, 'alias-' + str(i), [1, 2], facts)[0] == facts[i]
        assert len(aliases.cache) == aliases.LIMIT == 32
    finally:
        db.close()


async def test_colliding_retained_alias_ids_do_not_share_service_authority(media_app, tmp_path):
    from amplifier_web.automatic_history import revision
    from amplifier_web.service import AppService
    from test_service import Runtime
    first = media_app
    one, _, _, _, _ = cold_alias_corpus(first)
    await freeze_alias_display(first, one)
    assert (await first.outputs.media.identity(one['id'], 'saved-web-assistant'))['fact'][1] == 'assistant'
    workspace = tmp_path / 'other-workspace'
    workspace.mkdir()
    second = AppService(tmp_path / 'other-app', workspace=workspace, runtime=Runtime())
    try:
        await second.dispatch('session.create', {'title': 'Colliding retained ID'})
        collision = second._session()
        collision.update(id=one['id'], runtimeSessionId=one['id'])
        second._state['selectedSessionId'] = one['id']
        two, corpus, store, _, _ = cold_alias_corpus(second)
        assert two['id'] == one['id']
        # Same public session/retained IDs/index, but a different exact owner.
        corpus[1]['content'] = 'Different canonical text in the other owner'
        store.save(two['id'], corpus, {}, preserve_system=True)
        two['nativeRevision'] = revision(two)
        await freeze_alias_display(second, two)
        assert first.outputs.media.saved_aliases is not second.outputs.media.saved_aliases
        with pytest.raises(ValueError, match='assistant'):
            await second.outputs.media.identity(two['id'], 'saved-web-assistant')
        assert (await first.outputs.media.identity(one['id'], 'saved-web-assistant'))['fact'][1] == 'assistant'
    finally:
        await second.close()


@pytest.mark.parametrize('raw', [
    b'[{"id":"saved","id":"duplicate","role":"assistant"}]',
    b'[{"id":"saved","role":"assistant","text":"unterminated}]',
    b'[{"id":"saved","text":"\\uD800"}]',
    b'[{"id":"saved","text":"\\x20"}]',
    b'[{"id":"saved","text":"\xff"}]',
    b'[{"id":"saved","unknown":[1,]}]',
    b'[{"id":"saved"}] trailing',
    b'[{"id":"saved","nativeIndex":01}]',
])
def test_saved_display_stream_rejects_malformed_json_without_partial_projection(raw):
    import io
    from amplifier_web.media_saved_aliases import DisplayJSON
    with pytest.raises((ValueError, UnicodeError)):
        list(DisplayJSON(io.BytesIO(raw)).rows())


@pytest.mark.parametrize('defect', ['duplicate-fact', 'hidden-fact', 'user-fact'])
def test_saved_alias_projection_requires_unique_visible_assistant_canonical_fact(tmp_path, defect):
    import sqlite3
    from amplifier_web.automatic_history import display_identity
    from amplifier_web.media_saved_aliases import SavedAliases
    from amplifier_web.resource_files import detach, put
    db = sqlite3.connect(tmp_path / 'state.sqlite')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        value = {'id': 'synthetic'}
        canonical = display_identity(value, 1, 'assistant', 'full text')
        reference = put(db, [{'id': 'saved-web-assistant', 'nativeIndex': 1,
                              'role': 'assistant', 'text': 'full text'}])
        db.commit()
        source = detach(db, reference['$resource'])
        facts = [(canonical, 'assistant', True, 1, None)]
        if defect == 'duplicate-fact':
            facts *= 2
        elif defect == 'hidden-fact':
            facts = [(canonical, 'assistant', False, 1, None)]
        else:
            facts = [(canonical, 'user', True, 1, None)]
        with pytest.raises(ValueError, match='assistant'):
            SavedAliases().resolve(source, value, 'saved-web-assistant', [1, 2], facts)
    finally:
        db.close()


def test_generic_inline_detached_projection_bounds_utf8_copy_and_preserves_byte_seek():
    import json
    import tracemalloc
    from amplifier_web.resource_files import Detached, project_detached
    body = json.dumps(['雪😀' * 300000, 'tail'], ensure_ascii=False)
    encoded = body.encode()
    source = Detached(None, hashlib.sha256(encoded).hexdigest(), body)
    expected = encoded[2:9]
    del encoded

    def projection(stream):
        stream.read(64 * 1024)
        stream.seek(2)
        selected = stream.read(7)
        stream.seek(0)
        assert stream.read(1) == b'['
        return selected

    tracemalloc.start()
    try:
        selected, _ = project_detached(source, projection)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert selected == expected and peak < 1024 * 1024


def _exact_media_fork_corpus(app, *, partial=False, retained_alias=False):
    from amplifier_web.automatic_history import display_message, revision
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_files import project_slug
    session = app._session()
    rows = [
        {'role': 'system', 'content': 'Synthetic system context'},
        {'role': 'developer', 'content': 'Synthetic developer context'},
        {'role': 'user', 'content': 'First media request'},
        {'role': 'user', 'content': 'First internal reference before origin',
         'metadata': {'amplifier_visible_reference': True}},
        {'role': 'user', 'content': 'Second internal reference before origin',
         'metadata': {'amplifier_visible_reference': True}},
        {'role': 'assistant', 'content': 'Starting a synthetic tool',
         'tool_calls': [{'id': 'pending-before-media-origin', 'name': 'bash'}]},
        {'role': 'assistant', 'content': 'Identical assistant media text'},
        {'role': 'user', 'content': 'Second media request'},
        {'role': 'assistant', 'content': 'Identical assistant media text'},
        {'role': 'user', 'content': 'Third media request'},
        {'role': 'assistant', 'content': 'Identical assistant media text'},
        {'role': 'user', 'content': 'Tail request'},
        {'role': 'assistant', 'content': 'Tail answer'},
    ]
    store = SessionStore.for_app(app.data_dir, session['workspace'])
    store.save(session['id'], rows, {}, preserve_system=True)
    session.update(nativeProject=project_slug(session['workspace']), nativeIdentity=session['id'],
                   runtimeSessionId=session['id'], historyManaged=not retained_alias,
                   historyLoaded=True, status='idle')
    session['nativeRevision'] = revision(session)
    indexes = (7, 8, 9, 10, 11, 12) if partial else (11, 12)
    session['messages'] = [display_message(rows[index], index, session) for index in indexes]
    session.update(sharedHistoryOffset=3 if partial else 7, sharedHistoryTotal=9,
                   sharedHistoryUserTurnOffset=1 if partial else 3)
    if retained_alias:
        canonical = display_message(rows[6], 6, session)
        session['messages'].insert(0, {**canonical, 'id': 'proven-fork-media-ui-alias',
                                       'source': 'amplifier', 'nativeMessageId': canonical['id']})
    return session, rows, store


@pytest.mark.parametrize('scenario', [
    'inserted-receipt', 'partial-reference-removal', 'bundle-reference-remap',
    'retained-alias-canonical-origin', 'retained-alias-ui-origin',
])
async def test_exact_media_fork_origin_descriptor_head_range_and_source_tuple(
        authenticated_client, tmp_path, scenario):
    import copy
    from pathlib import Path
    from amplifier_web.automatic_history import display_message
    from amplifier_web.server import create_app
    from amplifier_web.session_store import fork_session
    from test_service import Runtime
    server = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
                              voice=False, preload_providers=False, background_updates=False)
    app = server['service']
    await app.dispatch('session.create', {'title': 'Exact fork origin corpus'})
    partial = scenario in {'partial-reference-removal', 'bundle-reference-remap'}
    retained = scenario.startswith('retained-alias-')
    source, rows, store = _exact_media_fork_corpus(app, partial=partial, retained_alias=retained)
    sid = source['id']
    raw = (store.directory(sid) / 'transcript.jsonl').read_bytes()
    original_messages = copy.deepcopy(source['messages'])
    saved = {}
    for index, data in ((6, gif()), (10, gif(frames=3))):
        canonical = display_message(rows[index], index, source)['id']
        origin = ('proven-fork-media-ui-alias'
                  if index == 6 and scenario == 'retained-alias-ui-origin' else canonical)
        filename = f'synthetic-fork-{index}.gif'
        Path(source['workspace'], filename).write_bytes(data)
        receipt = (await app.outputs.media.attach({
            'sessionId': sid, 'messageId': origin, 'title': f'Synthetic media at {index}',
            'path': filename, 'expectedSha256': hashlib.sha256(data).hexdigest(),
        }, 'ui', f'exact-fork-attach-{index}'))['result']
        saved[index] = (copy.deepcopy(app.outputs.record(sid, receipt['id'])), data)
        Path(source['workspace'], filename).unlink()
    if scenario == 'bundle-reference-remap':
        # session.fork has no bundle argument. Compose the existing transcript
        # owner and output cloner directly, without pretending to test that API.
        origins = {}
        async with app.lock:
            target = app._new_session({'title': 'Synthetic bundle fork',
                                       'workspace': source['workspace'], 'bundle': source['bundle']})
            target.update(fork_session(app.data_dir, source, target['id'], turn=2,
                                       bundle='synthetic-other-bundle', media_origins=origins))
            assert not any('media' in key.lower() for key in target)
            app.state['sessions'].insert(0, target)
            app.state['selectedSessionId'] = target['id']
            app.outputs.fork(sid, target, media_origins=origins)
            app.db.commit()
    else:
        await app.dispatch('session.fork', {'id': sid, **({'turn': 2} if partial else {})})
        target = app._session()
    target_rows = store.load(target['id'])[0]
    if partial:
        # Two reference deletions and one new receipt precede the origin.
        assert len(target_rows) == (6 if scenario == 'bundle-reference-remap' else 8)
        assert not any(row.get('metadata', {}).get('amplifier_visible_reference') for row in target_rows)
        first_index, decoy_index = ((3, 5) if scenario == 'bundle-reference-remap' else (5, 7))
        assert 'Third media request' not in str(target_rows)
    else:
        assert len(target_rows) == len(rows) + 1
        assert target_rows[:6] == rows[:6] and target_rows[7:] == rows[6:]
        first_index, decoy_index = 7, 9
    receipt = target_rows[first_index - 1]
    assert receipt['role'] == 'tool' and receipt['tool_call_id'] == 'pending-before-media-origin'
    import json
    assert json.loads(receipt['content'])['status'] == 'interrupted'
    if scenario == 'bundle-reference-remap':
        assert not any(row['role'] in {'system', 'developer'} for row in target_rows)
    cloned = [row for row in app.outputs.store.list(target['id'])['items'] if row['kind'] == 'media']
    expected_indexes = (6,) if partial else (6, 10)
    assert len(cloned) == len(expected_indexes)
    assert {row['forkSource']['outputId'] for row in cloned} == {saved[index][0]['id'] for index in expected_indexes}
    app.clients.attach('exact-fork-http-client')
    with app.clients.bind('exact-fork-http-client'):
        await app.dispatch('session.select', {'id': target['id']})
    client = await authenticated_client(server)
    client.session.headers['X-Amplifier-Client'] = 'exact-fork-http-client'
    for index in expected_indexes:
        original, data = saved[index]
        new_index = first_index if index == 6 else 11
        canonical_target = display_message(target_rows[new_index], new_index, target)['id']
        expected_id = 'proven-fork-media-ui-alias' if retained and index == 6 else canonical_target
        kept = next(row for row in target['messages'] if row.get('nativeIndex') == new_index)
        assert kept['id'] == expected_id and kept['text'] == rows[index]['content']
        if retained and index == 6:
            assert kept['nativeMessageId'] == canonical_target
            assert kept['id'] == original_messages[0]['id']
        clone = next(row for row in cloned if row['forkSource']['outputId'] == original['id'])
        internal = app.outputs.record(target['id'], clone['id'])
        assert internal['origin']['messageId'] == expected_id
        assert internal['id'] != original['id'] and internal['sessionId'] == target['id']
        assert internal['sha256'] == original['sha256'] == hashlib.sha256(data).hexdigest()
        assert internal['bytes'] == original['bytes'] == len(data)
        assert internal['body'] == original['body'] and internal['media'] == original['media']
        assert internal['forkSource'] == {'sessionId': sid, 'outputId': original['id'],
                                         'sha256': original['sha256']}
        query = {'sessionId': target['id'], 'messageId': expected_id}
        descriptor = await client.get('/api/outputs/media', params=query)
        assert descriptor.status == 200
        items = (await descriptor.json())['items']
        assert len(items) == 1 and items[0]['id'] == clone['id']
        assert items[0]['sha256'] == original['sha256'] and items[0]['bytes'] == len(data)
        assert 'body' not in items[0]
        exact = {**query, 'version': str(internal['version']), 'sha256': internal['sha256']}
        route = '/api/outputs/' + clone['id'] + '/media'
        head = await client.head(route, params=exact)
        assert head.status == 200 and await head.read() == b''
        assert head.headers['Content-Length'] == str(len(data))
        ranged = await client.get(route, params=exact, headers={'Range': 'bytes=2-7'})
        assert ranged.status == 206 and await ranged.read() == data[2:8]
        assert ranged.headers['Content-Length'] == '6'
        assert ranged.headers['Content-Range'] == f'bytes 2-7/{len(data)}'
        full = await client.get(route, params=exact)
        assert full.status == 200 and await full.read() == data
        wrong_session = {**exact, 'sessionId': sid, 'messageId': original['origin']['messageId']}
        assert (await client.head(route, params=wrong_session)).status == 400
        assert (await client.get(route, params=wrong_session, headers={'Range': 'bytes=2-7'})).status == 400
        # Bind to the source too: a refused source tuple must not depend only
        # on the current client's target selection.
        with app.clients.bind('exact-fork-http-client'):
            await app.dispatch('session.select', {'id': sid})
        assert (await client.get(route, params=wrong_session)).status == 400
        with app.clients.bind('exact-fork-http-client'):
            await app.dispatch('session.select', {'id': target['id']})
    decoy = next(row for row in target['messages'] if row.get('nativeIndex') == decoy_index)
    assert decoy['text'] == rows[6]['content']
    empty = await client.get('/api/outputs/media', params={'sessionId': target['id'], 'messageId': decoy['id']})
    assert empty.status == 200 and (await empty.json())['items'] == []
    assert source['messages'] == original_messages
    assert (store.directory(sid) / 'transcript.jsonl').read_bytes() == raw
    for original, _ in saved.values():
        assert app.outputs.record(sid, original['id']) == original


async def test_media_fork_refuses_live_alias_without_original_binding_despite_inferred_index(media_app):
    import copy
    from amplifier_web.automatic_history import display_message, revision
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_files import project_slug
    app = media_app
    source = app._session()
    args = media_args(app)
    attached = (await app.dispatch('outputs.attachMedia', args))['result']
    original_output = copy.deepcopy(app.outputs.record(source['id'], attached['id']))
    unbound = copy.deepcopy(source['messages'][0])
    unbound.pop('nativeIndex', None)
    unbound.pop('nativeMessageId', None)
    rows = [
        {'role': 'user', 'content': 'First saved request'},
        {'role': 'assistant', 'content': unbound['text']},
        {'role': 'user', 'content': 'Second saved request'},
        {'role': 'assistant', 'content': unbound['text']},
    ]
    store = SessionStore.for_app(app.data_dir, source['workspace'])
    store.save(source['id'], rows, {}, preserve_system=True)
    source.update(nativeProject=project_slug(source['workspace']), nativeIdentity=source['id'],
                  runtimeSessionId=source['id'], historyManaged=False, historyLoaded=True, status='idle',
                  sharedHistoryOffset=0, sharedHistoryUserTurnOffset=0)
    source['nativeRevision'] = revision(source)
    source['messages'] = [display_message(rows[0], 0, source), unbound,
                          display_message(rows[2], 2, source),
                          {'id': 'another-unbound-media-ui-id', 'role': 'assistant', 'text': unbound['text']}]
    original_messages = copy.deepcopy(source['messages'])
    raw = (store.directory(source['id']) / 'transcript.jsonl').read_bytes()
    await app.dispatch('session.fork', {'id': source['id']})
    target = app._session()
    kept = next(row for row in target['messages'] if row['id'] == args['messageId'])
    assert kept['nativeIndex'] == 1 and 'nativeIndex' not in original_messages[1]
    assert [row for row in app.outputs.store.list(target['id'])['items'] if row['kind'] == 'media'] == []
    app.clients.attach('unbound-fork-media-client')
    with app.clients.bind('unbound-fork-media-client'):
        await app.dispatch('session.select', {'id': target['id']})
        assert (await app.outputs.media.describe(target['id'], kept['id']))['items'] == []
    assert store.load(target['id'])[0] == rows
    assert source['messages'] == original_messages
    assert (store.directory(source['id']) / 'transcript.jsonl').read_bytes() == raw
    assert app.outputs.record(source['id'], attached['id']) == original_output


@pytest.mark.parametrize('message_spelling', ['canonical', 'retained'])
def test_saved_alias_warm_cache_rechecks_current_canonical_id_collisions(
        tmp_path, message_spelling):
    import sqlite3
    from amplifier_web.automatic_history import display_identity
    from amplifier_web.media_saved_aliases import SavedAliases
    from amplifier_web.resource_files import detach, put
    db = sqlite3.connect(tmp_path / 'synthetic.sqlite')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        value = {'id': 'synthetic-scope'}
        canonical = display_identity(value, 1, 'assistant', 'Exact saved text')
        reference = put(db, [{'id': 'retained-alias', 'role': 'assistant',
                              'nativeIndex': 1, 'text': 'Exact saved text'}])
        db.commit()
        value['coldMessages'] = reference
        source = detach(db, reference['$resource'])
        fact = (canonical, 'assistant', True, 1, None)
        requested = canonical if message_spelling == 'canonical' else 'retained-alias'
        aliases = SavedAliases()
        assert aliases.resolve(source, value, requested, [1, 2], [fact])[0] == fact
        # Same revision tuple is not permission to ignore changed index facts.
        changed = [fact, ('retained-alias', 'assistant', True, 7, None)]
        with pytest.raises(ValueError, match='assistant'):
            aliases.resolve(source, value, requested, [1, 2], changed)
        with pytest.raises(ValueError, match='assistant'):
            SavedAliases().resolve(source, value, requested, [1, 2], changed)
    finally:
        db.close()


def test_large_legacy_inline_saved_alias_warm_requests_require_no_body_reads(monkeypatch):
    """Unresolved inline freshness/cache discriminator; never xfail it.

    The file-backed cache does not retain a large inline index as its key. A
    compact exact-index freshness seam is still required for this warm path.
    """
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    from amplifier_web.automatic_history import display_identity
    from amplifier_web import media_saved_aliases, resource_files
    db = sqlite3.connect(':memory:')
    try:
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        value = {'id': 'synthetic-inline-scope'}
        text = 'Exact inline saved text 雪\n' * 5000
        canonical = display_identity(value, 1, 'assistant', text)
        reference = resource_files.put(db, [{'id': 'legacy-inline-alias',
            'role': 'assistant', 'nativeIndex': 1, 'text': text}])
        db.commit()
        source = resource_files.detach(db, reference['$resource'])
        assert source.directory is None and len(source.indexed) > 1024
        value['coldMessages'] = reference  # Bare legacy reference, no hints.
        facts = [(canonical, 'assistant', True, 1, None)]
        aliases = media_saved_aliases.SavedAliases()
        for requested in (canonical, 'legacy-inline-alias'):
            assert aliases.resolve(source, value, requested, [1, 2], facts)[0] == facts[0]
        reads, decodes = [], []
        read = resource_files._InlineStream.read
        rows = media_saved_aliases.DisplayJSON.rows

        def counted_read(stream, size):
            reads.append(size)
            return read(stream, size)

        def counted_rows(parser):
            decodes.append(True)
            yield from rows(parser)

        monkeypatch.setattr(resource_files._InlineStream, 'read', counted_read)
        monkeypatch.setattr(media_saved_aliases.DisplayJSON, 'rows', counted_rows)
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(aliases.resolve, source, value, requested, [1, 2], facts)
                       for requested in (canonical, 'legacy-inline-alias') * 2]
            assert all(future.result()[0] == facts[0] for future in futures)
        assert reads == [], 'Warm inline identity still reads the saved display body'
        assert decodes == [], 'Warm inline identity still decodes the saved display body'
        assert resource_files.detach(db, reference['$resource']) == source
        assert value['coldMessages'] == reference and 'messages' not in value
    finally:
        db.close()