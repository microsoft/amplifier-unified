"""Inert media source primitives.

These helpers do not grant attachment or playback authority. Callers must check
session, assistant publication and write fences before and after worker reads.
Structural validation is not decoder success or a native-memory ceiling.
"""
import asyncio
import base64
from collections import OrderedDict
import copy
import hashlib
import os
from pathlib import Path
import re
import stat
import struct
import time
import uuid


MAX_BYTES = 32 * 1024 * 1024
MAX_SIDE = 4096
MAX_PIXELS = 4_000_000
MAX_FRAMES = 500
MAX_DECODED_ESTIMATE = 128 * 1024 * 1024


def _dimensions(width, height):
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE and width * height <= MAX_PIXELS):
        raise ValueError("Media dimensions exceed 4096 per side or 4 million pixels.")


def _gif(data):
    if len(data) < 13:
        raise ValueError("Truncated GIF header.")
    width, height, packed, background, aspect = struct.unpack_from("<HHBBB", data, 6)
    _dimensions(width, height)
    position = 13

    def take(count):
        nonlocal position
        end = position + count
        if end > len(data):
            raise ValueError("Truncated GIF block.")
        value = data[position:end]
        position = end
        return value

    def subblocks(capture=False):
        # Do not retain one Python object per encoded image/comment subblock.
        # The only captured payload is a single three-byte loop declaration.
        nonlocal position
        chunks = []
        blocks = 0
        while True:
            count = take(1)[0]
            if not count:
                return blocks, chunks
            blocks += 1
            if position + count > len(data):
                raise ValueError("Truncated GIF subblock.")
            if capture:
                if blocks > 1 or count != 3:
                    raise ValueError("Invalid GIF loop metadata.")
                chunks.append(take(count))
            else:
                position += count

    has_palette = bool(packed & 0x80)
    if has_palette:
        take(3 * (2 << (packed & 7)))
    frames = duration = 0
    delay = 0
    loop = None
    pending_control = False
    while position < len(data):
        marker = take(1)[0]
        if marker == 0x3b:
            if not frames or position != len(data) or pending_control:
                raise ValueError("GIF requires image frames and an exact final trailer.")
            return {"mimeType": "image/gif", "width": width, "height": height,
                    "frameCount": frames, "durationMs": duration, "loopCount": loop,
                    "decodedPixelBytesEstimate": width * height * 4 * frames}
        if marker == 0x21:
            extension = take(1)[0]
            if extension == 0xf9:
                if pending_control or take(1) != b"\x04":
                    raise ValueError("Invalid GIF graphic control block.")
                control = take(4)
                if control[0] & 0xe0 or ((control[0] >> 2) & 7) > 3 or take(1) != b"\x00":
                    raise ValueError("Unsupported GIF graphic control.")
                delay = int.from_bytes(control[1:3], "little") * 10
                pending_control = True
            elif extension == 0xff:
                if take(1) != b"\x0b":
                    raise ValueError("Invalid GIF application block.")
                application = take(11)
                looping = application in {b"NETSCAPE2.0", b"ANIMEXTS1.0"}
                count, blocks = subblocks(capture=looping)
                if looping:
                    if loop is not None or count != 1 or blocks[0][0] != 1:
                        raise ValueError("Invalid GIF loop metadata.")
                    loop = int.from_bytes(blocks[0][1:], "little")
            elif extension == 0xfe:
                subblocks()
            else:
                # Plain-text extensions introduce separate rendering semantics.
                raise ValueError("Unsupported GIF extension.")
        elif marker == 0x2c:
            left, top, frame_width, frame_height, flags = struct.unpack("<HHHHB", take(9))
            if (not frame_width or not frame_height or left + frame_width > width
                    or top + frame_height > height or flags & 0x18):
                raise ValueError("Invalid GIF frame bounds.")
            local_palette = bool(flags & 0x80)
            if local_palette:
                take(3 * (2 << (flags & 7)))
            if not (has_palette or local_palette) or not 2 <= take(1)[0] <= 8:
                raise ValueError("Invalid GIF color table or LZW code size.")
            if not subblocks()[0]:
                raise ValueError("Empty GIF image data.")
            frames += 1
            duration += delay
            delay = 0
            pending_control = False
            if frames > MAX_FRAMES or width * height * 4 * frames > MAX_DECODED_ESTIMATE:
                raise ValueError("GIF exceeds 500 frames or the 128 MiB decoded-pixel estimate.")
        else:
            raise ValueError("Invalid GIF block marker.")
    raise ValueError("GIF trailer is missing.")


def _atoms(data, start, end):
    """A flat, bounded walk; nesting is only performed at named depths below."""
    result = []
    position = start
    while position < end:
        if end - position < 8 or len(result) >= 4096:
            raise ValueError("Truncated MP4 atom or excessive atom count.")
        size, kind = struct.unpack_from(">I4s", data, position)
        header = 8
        if size == 1:
            if end - position < 16:
                raise ValueError("Truncated MP4 extended atom.")
            size = int.from_bytes(data[position + 8:position + 16], "big")
            header = 16
        # Zero-size atoms are deliberately unsupported, including at EOF.
        if size < header or size > end - position:
            raise ValueError("Invalid or unsupported MP4 atom size.")
        result.append((kind, position + header, position + size))
        position += size
    return result


def _mp4(data):
    top = _atoms(data, 0, len(data))
    types = [kind for kind, _, _ in top]
    if types.count(b"ftyp") != 1 or types.count(b"moov") != 1 or b"mdat" not in types:
        raise ValueError("MP4 requires ftyp, moov and media data.")
    ftyp = next((start, end) for kind, start, end in top if kind == b"ftyp")
    brands = data[ftyp[0]:ftyp[1]]
    if len(brands) < 8 or (len(brands) - 8) % 4 or brands[:4] not in {b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V ", b"qt  "}:
        raise ValueError("Unsupported MP4 file brand.")
    moov = next((start, end) for kind, start, end in top if kind == b"moov")
    tracks = [row for row in _atoms(data, *moov) if row[0] == b"trak"]
    if not tracks or len(tracks) > 16:
        raise ValueError("MP4 requires bounded tracks.")
    videos = []
    for _, start, end in tracks:
        children = _atoms(data, start, end)
        headers = [row for row in children if row[0] == b"tkhd"]
        media = [row for row in children if row[0] == b"mdia"]
        if len(headers) != 1 or len(media) != 1:
            raise ValueError("Invalid MP4 track shape.")
        mdia = _atoms(data, media[0][1], media[0][2])
        handlers = [row for row in mdia if row[0] == b"hdlr"]
        if len(handlers) != 1 or handlers[0][2] - handlers[0][1] < 24:
            raise ValueError("Invalid MP4 handler.")
        if data[handlers[0][1] + 8:handlers[0][1] + 12] != b"vide":
            continue
        _, begin, finish = headers[0]
        version = data[begin] if finish > begin else -1
        offset = 76 if version == 0 else 88 if version == 1 else None
        if offset is None or finish - begin < offset + 8:
            raise ValueError("Invalid MP4 video dimensions.")
        raw_width, raw_height = struct.unpack_from(">II", data, begin + offset)
        if raw_width & 0xffff or raw_height & 0xffff:
            raise ValueError("Unsupported fractional MP4 dimensions.")
        width, height = raw_width >> 16, raw_height >> 16
        _dimensions(width, height)
        container = mdia
        for kind in (b"minf", b"stbl"):
            rows = [row for row in container if row[0] == kind]
            if len(rows) != 1:
                raise ValueError("Missing MP4 sample table.")
            container = _atoms(data, rows[0][1], rows[0][2])
        descriptions = [row for row in container if row[0] == b"stsd"]
        if len(descriptions) != 1:
            raise ValueError("Missing MP4 video description.")
        _, begin, finish = descriptions[0]
        if finish - begin < 8 or data[begin:begin + 4] != b"\x00" * 4:
            raise ValueError("Unsupported MP4 sample description version.")
        count = int.from_bytes(data[begin + 4:begin + 8], "big")
        entries = _atoms(data, begin + 8, finish)
        if count != len(entries) or not 1 <= count <= 16 or any(
                kind not in {b"avc1", b"avc3", b"hvc1", b"hev1", b"vp09", b"av01", b"mp4v"}
                or finish - begin < 78 for kind, begin, finish in entries):
            raise ValueError("Unsupported MP4 video sample description.")
        coded_sizes = []
        for _, begin, _ in entries:
            coded_width, coded_height = struct.unpack_from(">HH", data, begin + 24)
            _dimensions(coded_width, coded_height)
            coded_sizes.append((coded_width, coded_height))
        if len(set(coded_sizes)) != 1:
            raise ValueError("Unsupported changing MP4 coded dimensions.")
        coded_width, coded_height = coded_sizes[0]
        videos.append({"mimeType": "video/mp4", "width": width, "height": height,
                       "codedWidth": coded_width, "codedHeight": coded_height})
    if len(videos) != 1:
        raise ValueError("MP4 requires exactly one video track.")
    return videos[0]


def validate_media(data):
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Choose nonempty media up to 32 MiB.")
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return _gif(data)
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return _mp4(data)
    raise ValueError("Only structurally valid GIF or MP4 media is supported.")


def read_verified(root, value, expected_sha256):
    """Run in a bounded worker. Never resolve symlinks in the supplied path."""
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("Choose an exact SHA-256.")
    root = Path(root).absolute()
    path = Path(value)
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError:
            raise ValueError("Media must stay inside this conversation workspace.") from None
    if not path.parts or any(part in {"..", "."} for part in path.parts):
        raise ValueError("Choose a file inside this conversation workspace.")
    if ".." in root.parts:
        raise ValueError("Choose a canonical conversation workspace.")
    # Opening an absolute path in one call would still follow symlinks in its
    # ancestors. Pin each root component, just as for the relative source path.
    directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        for part in path.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_BYTES:
                raise ValueError("Choose a regular media file up to 32 MiB.")
            data = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if len(data) != before.st_size or any(getattr(before, key) != getattr(after, key) for key in fields):
                raise ValueError("Media changed while reading; inspect before retrying.")
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("Media bytes differ from the expected SHA-256.")
        return data, path.name, validate_media(data)
    finally:
        os.close(directory)


class MediaRangeError(ValueError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def byte_range(header, size):
    """Parse one range after authorization. Malformed/multiple=400, empty=416."""
    if header is None:
        return None
    if len(header) > 80 or not re.fullmatch(r"bytes=[0-9]*-[0-9]*", header) or header == "bytes=-":
        raise MediaRangeError("Only one well-formed byte range is supported.", 400)
    first, last = header[6:].split("-")
    if not first:
        count = int(last)
        start, end = max(0, size - count), size - 1
        if not count:
            raise MediaRangeError("Byte range is unsatisfiable.", 416)
    else:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    if size <= 0 or start >= size or start > end:
        raise MediaRangeError("Byte range is unsatisfiable.", 416)
    return start, end


def _prepare_media(data):
    from .resource_files import prepare
    return prepare({'encoding': 'base64', 'data': base64.b64encode(data).decode('ascii')})


def _signature(source):
    if source.directory is None:
        return (source.identity, source.indexed)
    info = (source.directory / (source.identity + '.json')).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Media backing file is not regular.')
    return tuple(getattr(info, key) for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def _saved_bytes(source, digest):
    from .resource_files import read_detached
    before = _signature(source)
    value = read_detached(source, max_bytes=MAX_BYTES * 4 // 3 + 1024)
    if value.get('encoding') != 'base64':
        raise ValueError('Invalid media body encoding.')
    data = base64.b64decode(value['data'], validate=True)
    if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Media bytes no longer match the saved hash.')
    after = _signature(source)
    if before != after:
        raise ValueError('Media backing file changed during read.')
    return data, after


class Media:
    """Immutable registry delivery, never a durable cache or playback witness."""
    def __init__(self, outputs):
        self.outputs = outputs
        self.app = outputs.app
        self.workers = asyncio.Semaphore(2)
        self.cache = OrderedDict()
        self.observations = OrderedDict()

    @staticmethod
    def assistant(session, message_id):
        row = next((row for row in session.get('messages', []) if row.get('id') == message_id), None)
        if row is None or row.get('role') != 'assistant':
            raise ValueError('Choose an existing saved assistant message in this conversation.')
        return row

    def authority(self, session, origin):
        if session.get('sessionKind', 'root') != 'root':
            raise ValueError('Media attachment belongs to an ordinary root conversation.')
        if origin == 'agent':
            # These context values come from the runtime envelope, never args.
            self.app.collaboration.active_binding(session)

    @staticmethod
    def workspace(session):
        return session.get('workingDirectory') or session.get('workspace')

    @staticmethod
    def descriptor(record):
        return {key: copy.deepcopy(record[key]) for key in
                ('id', 'sessionId', 'title', 'filename', 'version', 'sha256', 'bytes', 'media', 'linked', 'revision')
                if key in record}

    async def attach(self, args, origin, command_id):
        from .resource_files import put_prepared_async, settle_worker
        identity = command_id or uuid.uuid4().hex
        request = ['outputs.attachMedia', args, origin]
        async with self.app.lock:
            session = self.app._session(args['sessionId'])
            self.authority(session, origin)
            self.assistant(session, args['messageId'])
            previous = self.outputs.store.receipt(identity, request)
            if previous:
                return {'accepted': True, 'result': previous}
            workspace = self.workspace(session)
            execution = session.get('executionRevision', 0)
            fence = self.app.portability.write_context(session['id'])
        async with self.workers:
            data, filename, metadata = await settle_worker(read_verified, workspace, args['path'], args['expectedSha256'])
            prepared = await settle_worker(_prepare_media, data)
        async with self.app.lock:
            current = self.app._session(args['sessionId'])
            self.authority(current, origin)
            self.assistant(current, args['messageId'])
            if (self.workspace(current) != workspace or current.get('executionRevision', 0) != execution
                    or self.app.portability.write_context(current['id']) != fence):
                raise ValueError('The task workspace or write authority changed while reading media.')
            previous = self.outputs.store.receipt(identity, request)
            if previous:
                return {'accepted': True, 'result': previous}
            old_revision = current.get('mediaRevision', 0)
            owns = not self.app.db.in_transaction
            if owns:
                self.app.db.execute('BEGIN IMMEDIATE')
            self.app.db.execute('SAVEPOINT media_attach')
            try:
                body = await put_prepared_async(self.app.db, prepared)
                record = self.outputs.store.create(current['id'], {
                    'kind': 'media', 'title': args['title'], 'filename': filename,
                    'origin': {'source': origin, 'messageId': args['messageId']},
                    'body': body, 'sha256': args['expectedSha256'], 'bytes': len(data),
                    'media': metadata, 'versionEvidence': 'snapshot',
                })
                result = self.descriptor(record)
                self.outputs.store.remember(identity, request, result)
                current['mediaRevision'] = old_revision + 1
                self.app.db.execute('RELEASE media_attach')
            except BaseException:
                self.app.db.execute('ROLLBACK TO media_attach')
                self.app.db.execute('RELEASE media_attach')
                if owns:
                    self.app.db.rollback()
                current['mediaRevision'] = old_revision
                raise
            try:
                self.app._publish_changes(sessions={current['id']})
            except BaseException:
                current['mediaRevision'] = old_revision
                raise
            return {'accepted': True, 'result': result}

    def client_scope(self, sid):
        client = self.app.clients.current.get()
        record = self.app.clients.records.get(client)
        if not record or record.get('selectedSessionId') != sid:
            raise ValueError('This media requires its originating attached client conversation.')
        self.app._session(sid)
        return client

    def descriptors(self, sid, message_id):
        self.client_scope(sid)
        session = self.app._session(sid)
        self.assistant(session, message_id)
        rows = self.app.db.execute("""SELECT value FROM output_records WHERE session_id=?
            AND json_extract(value,'$.kind')='media' AND json_extract(value,'$.linked')=1
            AND json_extract(value,'$.origin.messageId')=? ORDER BY created,id LIMIT 21""",
            (sid, message_id)).fetchall()
        import json
        return {'items': [self.descriptor(json.loads(row[0])) for row in rows[:20]],
                'overflow': len(rows) > 20, 'mediaRevision': session.get('mediaRevision', 0)}

    def guard(self, values):
        sid = values['sessionId']
        self.client_scope(sid)
        session = self.app._session(sid)
        self.assistant(session, values['messageId'])
        record = self.outputs.record(sid, values['id'])
        if (record.get('kind') != 'media' or not record.get('linked')
                or record.get('origin', {}).get('messageId') != values['messageId']
                or record.get('version') != int(values['version']) or record.get('sha256') != values['sha256']):
            raise ValueError('Choose the exact linked assistant media version and hash.')
        return record

    def report(self, values, origin):
        if origin != 'ui':
            raise ValueError('Media observations must originate in the authenticated browser UI.')
        self.guard(values)
        client = self.app.clients.current.get()
        key = (client, values['id'], values['version'], values['sha256'])
        previous = self.observations.get(key)
        order = (values['generation'], values['sequence'])
        if previous and order <= (previous['generation'], previous['sequence']):
            raise ValueError('This media observation generation or sequence is stale.')
        result = {key: values[key] for key in ('generation', 'sequence', 'renderState', 'playback')}
        result.update(clientId=client, serverTimestamp=time.time(), detail=values.get('detail', ''),
                      evidence='authenticated-ui-report', modelAttestation=False)
        self.observations[key] = result
        self.observations.move_to_end(key)
        while len(self.observations) > 200:
            self.observations.popitem(last=False)
        return {'accepted': True, 'result': result}

    def observation(self, record):
        client = self.app.clients.current.get()
        observed = self.observations.get((client, record['id'], record['version'], record['sha256']))
        if (not record.get('linked') or not observed or
                self.app.clients.records.get(client, {}).get('selectedSessionId') != record['sessionId']
                or time.time() - observed['serverTimestamp'] > 30):
            return None
        return copy.deepcopy(observed)

    async def bytes(self, values):
        from .resource_files import detach, settle_worker
        async with self.app.lock:
            record = self.guard(values)
            source = detach(self.app.db, record['body']['$resource'])
            workspace = self.workspace(self.app._session(values['sessionId']))
        # Bounded workers also serialize cache decisions: no durable byte store.
        async with self.workers:
            signature = await settle_worker(_signature, source)
            key = (source.identity, record['sha256'])
            cached = self.cache.get(key)
            if cached and cached[1] == signature and time.monotonic() - cached[2] < 5:
                data = cached[0]
                self.cache.move_to_end(key)
            else:
                self.cache.pop(key, None)
                data, signature = await settle_worker(_saved_bytes, source, record['sha256'])
                self.cache[key] = (data, signature, time.monotonic())
                while len(self.cache) > 2 or sum(len(row[0]) for row in self.cache.values()) > 64 * 1024 * 1024:
                    self.cache.popitem(last=False)
        async with self.app.lock:
            current = self.guard(values)
            if current.get('body') != record.get('body') or self.workspace(self.app._session(values['sessionId'])) != workspace:
                raise ValueError('Media source authority changed during read.')
        return data