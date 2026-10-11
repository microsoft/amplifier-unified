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
    yield app
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
        descriptors = media_app.outputs.media.descriptors(args['sessionId'], args['messageId'])
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
    app = SimpleNamespace(db=db, lock=asyncio.Lock(), _session=lambda sid: session)
    media = Media(SimpleNamespace(app=app))
    body = await put_prepared_async(db, _prepare_media(gif()))
    db.commit()
    record = {'body': body, 'sha256': hashlib.sha256(gif()).hexdigest()}
    media.guard = lambda values: record
    values = {'sessionId': 'own'}
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
    reads, writes, json_threads, sql_threads, small_writes = [], [], [], [], []
    real_open, real_loads = files.os.open, files.json.loads
    real_atomic = SessionStore._atomic
    directory = files.root(app.db)
    large_path = directory / (expected.identity + '.json')

    def opened(path, *values, **kwargs):
        if Path(path) == large_path:
            reads.append((str(path), threading.get_ident()))
        return real_open(path, *values, **kwargs)

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

    monkeypatch.setattr(files.os, 'open', opened)
    monkeypatch.setattr(files.json, 'loads', loaded)
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
    for key, value in [('sessionId', 'foreign'), ('messageId', 'foreign'), ('version', '99'), ('sha256', '0' * 64)]:
        wrong = await client.get(path, params={**query, key: value}, headers={'Range': 'malformed'})
        assert wrong.status == 409  # tuple authority precedes Range parsing (400)
    client.session.headers['Authorization'] = ''
    client.session.cookie_jar.clear()
    response = await client.get(path, params=query, headers={'Range': 'malformed'}, allow_redirects=False)
    assert response.status in {401, 403}


async def test_media_report_origin_exact_tuple_and_monotonic_order(media_app):
    from amplifier_web.service import AppError
    args = media_args(media_app)
    record = (await media_app.dispatch('outputs.attachMedia', args))['result']
    media_app.clients.attach('reported-client')
    values = {'sessionId': args['sessionId'], 'messageId': args['messageId'], 'id': record['id'],
              'version': record['version'], 'sha256': record['sha256'],
              'generation': 1, 'sequence': 1, 'renderState': 'loading', 'playback': 'paused'}
    with media_app.clients.bind('reported-client'):
        with pytest.raises(AppError, match='UI'):
            await media_app.dispatch('outputs.mediaReport', values, origin='agent')
        await media_app.dispatch('outputs.mediaReport', values, origin='ui')
        with pytest.raises(AppError, match='stale'):
            await media_app.dispatch('outputs.mediaReport', values, origin='ui')
        await media_app.dispatch('outputs.mediaReport', {**values, 'sequence': 2, 'renderState': 'ready', 'playback': 'playing'}, origin='ui')
        result = (await media_app.dispatch('outputs.read', {'sessionId': args['sessionId'], 'id': record['id']}))['result']
        assert result['saved'] and result['attached'] and result['playback'] == 'playing'
        assert result['browserObservation']['evidence'] == 'authenticated-ui-report'
        assert result['browserObservation']['modelAttestation'] is False


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
        await app.dispatch('outputs.mediaReport', values, origin='ui')
        result = await app.app_bridge('dispatch', {'action': 'outputs.read',
            'args': {'id': record['id']}, '_inputClients': ['observer-one']}, args['sessionId'])
        assert result['result']['browserObservation']['status'] == 'observed'
        assert result['result']['browserObservation']['presentedFrames'] == 1
        stamp = result['result']['browserObservation']['serverTimestamp']
        monkeypatch.setattr(output_media.time, 'time', lambda: stamp + 31)
        expired = app.outputs.media.observation(internal)
        assert expired['status'] == 'expired' and 'playback' not in expired
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
            items = reopened.outputs.media.descriptors(sid, args['messageId'])['items']
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