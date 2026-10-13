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
import json
import os
from pathlib import Path
import re
import stat
import struct
import time
import uuid


MAX_BYTES = 64 * 1024 * 1024
MAX_MP4_BYTES = 32 * 1024 * 1024
MAX_SIDE = 4096
MAX_PIXELS = 4_000_000
MAX_FRAMES = 500
MAX_DECODED_ESTIMATE = 512 * 1024 * 1024


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
                raise ValueError("GIF exceeds 500 frames or the 512 MiB decoded-pixel estimate.")
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
        raise ValueError("Choose nonempty GIF media up to 64 MiB or MP4 up to 32 MiB.")
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return _gif(data)
    if len(data) >= 8 and data[4:8] == b"ftyp":
        if len(data) > MAX_MP4_BYTES:
            raise ValueError("Choose MP4 media up to 32 MiB.")
        return _mp4(data)
    raise ValueError("Only structurally valid GIF or MP4 media is supported.")


def retained_facts(record, sid):
    """Pure shape validation, not admission authority or transcript evidence.

    Only the owning host registry or signed/reviewed transfer route supplies
    records. A payload flag or a content hash cannot admit a new record.
    """
    def text(value, maximum, minimum=1):
        return isinstance(value, str) and minimum <= len(value) <= maximum

    def integer(value, low, high):
        return type(value) is int and low <= value <= high

    origin = record.get('origin')
    body = record.get('body')
    metadata = record.get('media')
    if (record.get('kind') != 'media' or record.get('sessionId') != sid
            or not text(record.get('id'), 100) or not text(sid, 200)
            or not isinstance(origin, dict) or not isinstance(origin.get('source'), str)
            or origin['source'] not in {'ui', 'agent'}
            or not text(origin.get('messageId'), 200)
            or not origin['messageId'].strip()
            or any(ord(char) < 32 for char in origin['messageId'])
            or not integer(record.get('version'), 1, 2**63 - 1)
            or not isinstance(record.get('sha256'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', record['sha256'])
            or not text(record.get('title'), 200, 0) or not text(record.get('filename'), 4000)
            or ('versionEvidence' in record and not text(record['versionEvidence'], 200))
            or not isinstance(body, dict) or set(body) not in ({'$resource'}, {'$resource', 'bytes'})
            or not isinstance(body['$resource'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', body['$resource'])
            or ('bytes' in body and not integer(body['bytes'], 1, MAX_BYTES * 4 // 3 + 1024))
            or not isinstance(metadata, dict)):
        raise ValueError('Invalid retained media record or historical provenance.')
    mime = metadata.get('mimeType')
    if mime == 'image/gif':
        fields = {'mimeType', 'width', 'height', 'frameCount', 'durationMs',
                  'loopCount', 'decodedPixelBytesEstimate'}
        if (not integer(metadata.get('frameCount'), 1, MAX_FRAMES)
                or not integer(metadata.get('durationMs'), 0, MAX_FRAMES * 655350)
                or (metadata.get('loopCount') is not None
                    and not integer(metadata['loopCount'], 0, 65535))):
            raise ValueError('Invalid retained GIF metadata.')
    elif mime == 'video/mp4':
        fields = {'mimeType', 'width', 'height', 'codedWidth', 'codedHeight'}
    else:
        raise ValueError('Invalid retained media MIME type.')
    if set(metadata) != fields:
        raise ValueError('Invalid retained media metadata fields.')
    for first, second in [('width', 'height')] + (
            [('codedWidth', 'codedHeight')] if mime == 'video/mp4' else []):
        if not integer(metadata[first], 1, MAX_SIDE) or not integer(metadata[second], 1, MAX_SIDE):
            raise ValueError('Invalid retained media dimensions.')
        _dimensions(metadata[first], metadata[second])
    if mime == 'image/gif':
        estimate = metadata['width'] * metadata['height'] * 4 * metadata['frameCount']
        if (not integer(metadata['decodedPixelBytesEstimate'], 1, MAX_DECODED_ESTIMATE)
                or metadata['decodedPixelBytesEstimate'] != estimate):
            raise ValueError('Invalid retained GIF decoded-pixel estimate.')
    if not integer(record.get('bytes'), 1, MAX_MP4_BYTES if mime == 'video/mp4' else MAX_BYTES):
        raise ValueError('Invalid media byte metadata.')
    # Linking and its CAS revision are publication state, not object lifetime.
    return {key: copy.deepcopy(record[key]) for key in (
        'id', 'sessionId', 'kind', 'origin', 'title', 'filename',
        'version', 'sha256', 'bytes', 'media', 'body', 'versionEvidence') if key in record}


def validate_retained_body(data, record):
    """Worker-only structural/integrity comparison with the admitted metadata."""
    if (len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']
            or validate_media(data) != record['media']):
        raise ValueError('Media body differs from its saved metadata or hash.')


def validate_media_reference(reference, record, sid, *, kind):
    """Bind an inert adapter to one exact host-admitted output, without I/O."""
    retained_facts(record, sid)
    if not isinstance(reference, dict) or set(reference) != {'item', 'messageId'}:
        raise ValueError('Invalid retained media reference.')
    item = reference['item']
    fields = ('id', 'sessionId', 'title', 'filename', 'version', 'sha256', 'bytes', 'media')
    if isinstance(item, dict):
        retained_facts({**record, **item}, sid)
    if (not isinstance(item, dict) or set(item) != set(fields)
            or any(item[key] != record[key] or type(item[key]) is not type(record[key]) for key in fields)
            or reference['messageId'] != record['origin']['messageId']
            or kind not in {'gif', 'mp4'}
            or record['media']['mimeType'] != {'gif': 'image/gif', 'mp4': 'video/mp4'}.get(kind)):
        raise ValueError('Media reference must match its exact owning output, kind, version and hash.')


def _validate_retained_source(source, signature, data, record):
    validate_retained_body(data, record)
    if _signature(source) != signature:
        raise ValueError('Media backing file changed during validation.')


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
                raise ValueError("Choose a regular GIF file up to 64 MiB or MP4 up to 32 MiB.")
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
    # A cache is not index authority. Validate the freshly detached index even
    # if the backing file's lstat signature has not changed since a warm read.
    if source.directory is None:
        if hashlib.sha256(source.indexed.encode()).hexdigest() != source.identity:
            raise ValueError('Media index conflicts with its content identity.')
        return (source.identity,)
    if len(source.indexed) > 1024 or json.loads(source.indexed) != {'$blob': source.identity}:
        raise ValueError('Invalid media artifact index.')
    info = (source.directory / (source.identity + '.json')).lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES * 4 // 3 + 1024:
        raise ValueError('Media backing file is not regular.')
    return tuple(getattr(info, key) for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def _saved_bytes(source, digest):
    from .resource_files import read_detached
    before = _signature(source)
    value = read_detached(source, max_bytes=MAX_BYTES * 4 // 3 + 1024)
    if not isinstance(value, dict) or set(value) != {'encoding', 'data'} or value.get('encoding') != 'base64':
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
        from .media_saved_aliases import SavedAliases
        self.saved_aliases = SavedAliases()

    def assistant(self, session, message_id, proof=None):
        from .media_message_identity import snapshot, canonical_required, detached, resolve, validate
        if proof is not None:
            return validate(session, message_id, proof,
                            detached(self.app.db, snapshot(session, message_id)))
        value = snapshot(session, message_id)
        if canonical_required(value):
            raise ValueError('Canonical media identity requires an off-thread lookup.')
        return validate(session, message_id, resolve(value, message_id))

    async def identity(self, sid, message_id, *, scoped=False, origin=None):
        from .media_message_identity import (
            snapshot, canonical_required, detached, resolve, resident_claimants, complete,
        )
        from .resource_files import settle_worker
        async with self.app.lock:
            if scoped:
                self.client_scope(sid)
            session = self.app._session(sid, hydrate=False)
            if origin is not None:
                self.authority(session, origin)
            elif scoped:
                self.authority(session, 'ui')
            value = snapshot(session, message_id)
            saved = detached(self.app.db, value)
        if not canonical_required(value):
            return resolve(value, message_id)
        async with self.workers:
            proof = await settle_worker(resolve, value, message_id, saved, self.saved_aliases)
        async with self.app.lock:
            rows = resident_claimants(self.app._session(sid, hydrate=False), message_id, proof['fact'])
        async with self.workers:
            proof = await settle_worker(complete, proof, message_id, rows)
        async with self.app.lock:
            self.assistant(self.app._session(sid, hydrate=False), message_id, proof)
        return proof

    async def describe(self, sid, message_id):
        async with self.app.lock:
            self.client_scope(sid)
            session = self.app._session(sid, hydrate=False)
            if session.get('sessionKind', 'root') in ('worker', 'internal'):
                # Discovery is empty for explicit host-classified non-roots.
                # Identity, attachment, bytes and reports remain root-only.
                return {'items': [], 'overflow': False,
                        'mediaRevision': session.get('mediaRevision', 0)}
            return self.descriptors(sid, message_id)

    async def guarded(self, values):
        async with self.app.lock:
            return self.guard(values)

    async def observe(self, values, origin):
        if origin != 'ui':
            raise ValueError('Media observations must originate in the authenticated browser UI.')
        async with self.app.lock:
            return self.report(values, origin)

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
        proof = await self.identity(args['sessionId'], args['messageId'], origin=origin)
        async with self.app.lock:
            session = self.app._session(args['sessionId'], hydrate=False)
            self.authority(session, origin)
            self.assistant(session, args['messageId'], proof)
            previous = self.outputs.store.receipt(identity, request)
            if previous:
                return {'accepted': True, 'result': previous}
            workspace = self.workspace(session)
            execution = session.get('executionRevision', 0)
            fence = self.app.portability.write_context(session['id'])
        async with self.workers:
            data, filename, metadata = await settle_worker(read_verified, workspace, args['path'], args['expectedSha256'])
            prepared = await settle_worker(_prepare_media, data)
        refreshed = await self.identity(args['sessionId'], args['messageId'], origin=origin)
        if refreshed != proof:
            raise ValueError('The saved assistant identity changed while reading media.')
        async with self.app.lock:
            current = self.app._session(args['sessionId'], hydrate=False)
            self.authority(current, origin)
            self.assistant(current, args['messageId'], refreshed)
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
                # The file writer awaited while this transaction remained
                # owned. External transcript writers can still remove/change
                # the origin: re-check off-thread before publishing a receipt.
                from .media_message_identity import (
                    snapshot, canonical_required, detached, resolve, resident_claimants, complete,
                )
                value = snapshot(self.app._session(args['sessionId'], hydrate=False), args['messageId'])
                if canonical_required(value):
                    saved = detached(self.app.db, value)
                    async with self.workers:
                        final = await settle_worker(resolve, value, args['messageId'],
                                                    saved, self.saved_aliases)
                        rows = resident_claimants(self.app._session(args['sessionId'], hydrate=False),
                                                  args['messageId'], final['fact'])
                        final = await settle_worker(complete, final, args['messageId'], rows)
                    self.assistant(self.app._session(args['sessionId'], hydrate=False), args['messageId'], final)
                    if final != refreshed:
                        raise ValueError('The saved assistant identity changed before media commit.')
                current = self.app._session(args['sessionId'], hydrate=False)
                self.authority(current, origin)
                self.assistant(current, args['messageId'], refreshed)
                if (self.workspace(current) != workspace or current.get('executionRevision', 0) != execution
                        or self.app.portability.write_context(current['id']) != fence):
                    raise ValueError('The task workspace or write authority changed before media commit.')
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
        self.app._session(sid, hydrate=False)
        return client

    def descriptors(self, sid, message_id, proof=None):
        self.client_scope(sid)
        session = self.app._session(sid, hydrate=False)
        self.authority(session, 'ui')
        if (not isinstance(message_id, str) or not 0 < len(message_id) <= 200
                or any(ord(char) < 32 for char in message_id)):
            raise ValueError('Choose an exact stored media message ID.')
        rows = self.app.db.execute("""SELECT value FROM output_records WHERE session_id=?
            AND json_extract(value,'$.kind')='media' AND json_extract(value,'$.linked')=1
            AND json_extract(value,'$.origin.messageId')=? ORDER BY created,id LIMIT 21""",
            (sid, message_id)).fetchall()
        import json
        records = [json.loads(row[0]) for row in rows[:20]]
        for record in records:
            retained_facts(record, sid)
        return {'items': [self.descriptor(record) for record in records],
                'overflow': len(rows) > 20, 'mediaRevision': session.get('mediaRevision', 0)}

    def guard(self, values, proof=None):
        sid = values['sessionId']
        self.client_scope(sid)
        session = self.app._session(sid, hydrate=False)
        self.authority(session, 'ui')
        record = self.outputs.record(sid, values['id'])
        retained_facts(record, sid)
        version = values['version']
        if isinstance(version, str) and re.fullmatch(r'[1-9][0-9]{0,18}', version):
            version = int(version)
        if (record['id'] != values['id']
                or record.get('origin', {}).get('messageId') != values['messageId']
                or type(version) is not int or record.get('version') != version
                or record.get('sha256') != values['sha256']):
            raise ValueError('Choose the exact retained assistant media version and hash.')
        return record

    def snapshot(self, values):
        """Under the app lock: immutable registry/index facts and caller scope."""
        from .resource_files import detach
        record = self.guard(values)
        return (retained_facts(record, values['sessionId']),
                detach(self.app.db, record['body']['$resource']),
                self.client_scope(values['sessionId']))

    def metadata_snapshot(self, sid, identity):
        """Caller ownership belongs to app_bridge, not browser selection.

        Byte delivery and UI observations retain their stricter client scope.
        """
        from .resource_files import detach
        session = self.app._session(sid, hydrate=False)
        self.authority(session, 'ui')
        record = self.outputs.record(sid, identity)
        facts = copy.deepcopy(retained_facts(record, sid))
        return (facts, detach(self.app.db, facts['body']['$resource']))

    async def metadata(self, sid, identity, args):
        from .resource_files import settle_worker
        async with self.app.lock:
            before = self.metadata_snapshot(sid, identity)
        async with self.workers:
            await settle_worker(_signature, before[1])
        async with self.app.lock:
            if self.metadata_snapshot(sid, identity) != before:
                raise ValueError('Retained media changed during metadata read.')
            record = self.outputs.record(sid, identity)
            observation = self.observation(record)
            comments = self.outputs.store.comments(identity)
            start, count = args.get('commentOffset', 0), args.get('commentLimit', 5)
            return {**self.descriptor(record), 'kind': 'media',
                    'origin': {key: record['origin'][key] for key in ('source', 'messageId')},
                    'versionEvidence': record.get('versionEvidence', 'snapshot'),
                    'saved': True, 'attached': bool(record.get('linked')),
                    'browserObservation': observation,
                    'playback': observation.get('playback', 'unknown'),
                    'comments': comments[start:start + count],
                    'nextCommentOffset': start + count if start + count < len(comments) else None}

    def report(self, values, origin, proof=None):
        if origin != 'ui':
            raise ValueError('Media observations must originate in the authenticated browser UI.')
        record = self.guard(values)
        client = self.app.clients.current.get()
        key = (client, values['id'], values['version'], values['sha256'])
        previous = self.observations.get(key)
        order = (values['generation'], values['sequence'])
        if previous and order <= (previous['generation'], previous['sequence']):
            raise ValueError('This media observation generation or sequence is stale.')
        result = {key: values[key] for key in ('generation', 'sequence', 'renderState', 'playback',
                                              'presentedFrames', 'mediaTime') if key in values}
        result.update(clientId=client, serverTimestamp=time.time(), detail=values.get('detail', ''),
                      evidence='authenticated-ui-report', modelAttestation=False,
                      linked=bool(record.get('linked')))
        self.observations[key] = result
        self.observations.move_to_end(key)
        while len(self.observations) > 200:
            self.observations.popitem(last=False)
        return {'accepted': True, 'result': result}

    def observation(self, record):
        client = self.app.clients.current.get()
        observed = self.observations.get((client, record['id'], record['version'], record['sha256']))
        # Absence of current evidence is not evidence that a browser never loaded
        # it. Do not borrow an arbitrary other client's report for an agent read.
        if not client or self.app.clients.records.get(client, {}).get('selectedSessionId') != record['sessionId']:
            return {'status': 'unbound', 'detail': 'No matching authenticated client is bound to this read.'}
        if not observed:
            return {'status': 'not_observed', 'detail': 'No report retained for this client and exact media tuple.'}
        if time.time() - observed['serverTimestamp'] > 30:
            return {'status': 'expired', 'serverTimestamp': observed['serverTimestamp'],
                    'detail': 'The previous UI report expired; current playback is unknown.'}
        if client not in self.app.queue_clients.values():
            return {'status': 'disconnected', 'serverTimestamp': observed['serverTimestamp'],
                    'detail': 'The reporting browser is disconnected; current playback is unknown.'}
        return {'status': 'observed', **copy.deepcopy(observed), 'linked': bool(record.get('linked'))}

    async def bytes(self, values):
        from .resource_files import settle_worker
        async with self.app.lock:
            before = self.snapshot(values)
            record, source, _client = before
            record = copy.deepcopy(record)
        # Bounded workers also serialize cache decisions: no durable byte store.
        async with self.workers:
            signature = await settle_worker(_signature, source)
            # Reuse successful validation only for exact immutable metadata and
            # resource/index identity, not merely an unchanged media hash.
            key = (source, json.dumps(record, sort_keys=True, separators=(',', ':')))
            cached = self.cache.get(key)
            if cached and cached[1] == signature and time.monotonic() - cached[2] < 5:
                data = cached[0]
                self.cache.move_to_end(key)
            else:
                self.cache.pop(key, None)
                data, signature = await settle_worker(_saved_bytes, source, record['sha256'])
                await settle_worker(_validate_retained_source, source, signature, data, record)
                self.cache[key] = (data, signature, time.monotonic())
                while len(self.cache) > 2 or sum(len(row[0]) for row in self.cache.values()) > 64 * 1024 * 1024:
                    self.cache.popitem(last=False)
            # Hits avoid body hashing/decoding but still recheck fresh index and
            # backing signature after awaits, before scope revalidation.
            if await settle_worker(_signature, source) != signature:
                self.cache.pop(key, None)
                raise ValueError('Media backing file changed during read.')
        async with self.app.lock:
            if self.snapshot(values) != before:
                raise ValueError('Media source authority changed during read.')
        return data

    async def head(self, values):
        """Indexed metadata only: HEAD never opens/decodes a saved media body."""
        from .resource_files import settle_worker
        async with self.app.lock:
            before = self.snapshot(values)
            record, source, _client = before
        async with self.workers:
            await settle_worker(_signature, source)
        async with self.app.lock:
            if self.snapshot(values) != before:
                raise ValueError('Media source authority changed during HEAD.')
        return record['bytes']