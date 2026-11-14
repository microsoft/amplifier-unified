"""Concrete diagnostic image binding; unknown original policy always refuses."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import hashlib
from itertools import zip_longest
import json
import os
from pathlib import Path
import stat
import sqlite3
import tempfile

from .capture_index import CaptureDrift, _content_stamp


def _digest(stream, poll=None, size=None):
    result = hashlib.sha256()
    while size is None or size:
        if poll:
            poll()
        block = stream.read(65536 if size is None else min(size, 65536))
        if not block:
            if size:
                raise CaptureDrift('Capture proof extends beyond its source')
            break
        result.update(block)
        if size is not None:
            size -= len(block)
    return result.hexdigest()


def _metadata(stream):
    raw = stream.read(65537)
    if len(raw) > 65536:
        raise CaptureDrift('Capture proof metadata exceeds its bound')
    return json.loads(raw)


def _record_rows(stream, journal):
    header = json.loads(stream.readline(65537))
    if (header.get('format') != 'ci-optimize-record-mapping'
            or type(header.get('version')) is not int or header['version'] != 1
            or header.get('span') != 'physical-line-including-newline'):
        raise CaptureDrift('Unsupported capture record mapping')
    for side, prefix in (('old', 'source'), ('new', 'candidate')):
        if header[side] != {'offset': 0, 'sha256': journal[prefix + '_sha256'],
                            'bytes': journal[prefix + '_bytes']}:
            raise CaptureDrift('Capture map belongs to another generation')
    while raw := stream.readline(65537):
        if len(raw) > 65536 or not raw.endswith(b'\n'):
            raise CaptureDrift('Capture map row exceeds its metadata bound')
        yield json.loads(raw)


def _expiry_rows(stream, poll):
    """SQLite sees only a hash-verified private snapshot, never unchecked WAL."""
    with tempfile.TemporaryDirectory(prefix='capture-ack-map-') as directory:
        path = Path(directory) / 'map.sqlite3'
        with path.open('xb') as copy:
            os.fchmod(copy.fileno(), 0o600)
            while block := stream.read(65536):
                if poll:
                    poll()
                copy.write(block)
        db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            if db.execute('SELECT version FROM format WHERE name=?',
                          ('ci-diagnostic-retention',)).fetchone() != (1,):
                raise CaptureDrift('Unsupported expiry mapping')
            for values in db.execute('SELECT old_offset,old_bytes,old_sha256,'
                    'new_offset,new_bytes,new_sha256 FROM record_map ORDER BY ordinal'):
                yield {'old': dict(zip(('offset', 'bytes', 'sha256'), values[:3])),
                       'new': dict(zip(('offset', 'bytes', 'sha256'), values[3:]))}
        finally:
            db.close()


@contextmanager
def _pinned(path):
    """Borrow one owned generation; reads cannot follow a substituted link."""
    path = Path(path)
    for parent in path.parents:
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise CaptureDrift('Redirected capture proof ancestry')
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_uid != os.getuid()):
        raise CaptureDrift('Capture proof is not an owned regular file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if _content_stamp(os.fstat(stream.fileno())) != _content_stamp(before):
            raise CaptureDrift('Capture proof changed while opening')
        yield stream
        if (_content_stamp(path.lstat()) != _content_stamp(before)
                or _content_stamp(os.fstat(stream.fileno())) != _content_stamp(before)):
            raise CaptureDrift('Capture proof changed during acknowledgement')


class _InstalledReceipt:
    def __init__(self, owner, sha256):
        self.owner, self.sha256 = owner, sha256

    def verify(self):
        return {'session_id': self.owner.store_session_id,
                'source': str(self.owner.source), 'sha256': self.sha256}


class InstalledCaptureCheckpoint:
    """Exact diagnostic reachability, never original-policy or canonical authority.

    Construct inside the host's stopped/flush boundary. The Foundation owner must
    separately verify original receipt/group publication and binary closure.
    This verifier yields ONLY each manifest row's selected reference at its exact
    installed CI slot, not every member in the assets-only publication group.
    Foundation exhausts this generator before binding any checkpoint acceptance.
    """
    def __init__(self, directory, source, *, session_id, store_session_id, poll=None):
        self.directory, self.source = Path(directory), Path(source)
        self.session_id, self.store_session_id, self.poll = session_id, store_session_id, poll
        if self.directory.parent != self.source.parent:
            raise CaptureDrift('Capture checkpoint operation is not its source sibling')

    def receipt_for(self, manifest, *, source, sha256):
        if Path(manifest) != self.directory / 'assets.jsonl' or Path(source) != self.source:
            raise CaptureDrift('Capture acknowledgement belongs to another operation')
        return _InstalledReceipt(self, sha256)

    def refs(self, receipt):
        # Every proof is hashed, parsed/copied and finally rehashed through the
        # SAME pinned descriptor; no hash-then-reopen race or WAL side authority.
        with ExitStack() as stack:
            checks = []
            def proof(path, expected=None):
                stream = stack.enter_context(_pinned(path))
                digest = _digest(stream, self.poll)
                if expected is not None and digest != expected:
                    raise CaptureDrift('Capture checkpoint proof changed')
                checks.append((stream, digest))
                stream.seek(0)
                return stream
            yield from self._refs(receipt, proof, stack)
            for stream, expected in checks:
                stream.seek(0)
                if _digest(stream, self.poll) != expected:
                    raise CaptureDrift('Capture checkpoint proof changed during acknowledgement')

    def _refs(self, receipt, proof, stack):
        from amplifier_foundation.session.media_conversion_owner import (
            BINDING, GROUP_BINDING, diagnostic_image_ref)
        from context_intelligence.optimize.positions import capture_image_verifier
        if not isinstance(receipt, _InstalledReceipt) or receipt.owner is not self:
            raise CaptureDrift('Capture checkpoint needs its live owner receipt')
        journal = _metadata(proof(self.directory / 'journal.json'))
        if (journal['source'] != str(self.source) or journal['session_id'] != self.session_id
                or journal.get('format') != 'context-intelligence-optimize'
                or type(journal.get('version')) is not int or journal['version'] != 1
                or journal['state'] not in {'replace-pending', 'replaced', 'verified'}):
            raise CaptureDrift('Capture checkpoint lacks exact replacement intent')
        expiry = None
        expiry_map = None
        if receipt.sha256 != journal['candidate_sha256']:
            from context_intelligence.prune.coordination import receipt_path
            from context_intelligence.prune.compound import optimize_retention_receipt
            fence = proof(receipt_path(self.source))
            fence_digest = _digest(fence, self.poll)
            fence.seek(0)
            if _metadata(fence) != {
                    'format': 'context-intelligence-optimize', 'version': 1,
                    'operation_id': journal['operation_id'],
                    'source_sha256': journal['source_sha256'],
                    'candidate_sha256': journal['candidate_sha256'],
                    'notice': 'Local historical conversion; network replay remains refused.'}:
                raise CaptureDrift('Capture expiry replay fence changed')
            try:
                expiry = _metadata(proof(optimize_retention_receipt(self.source, fence_digest)))
            except FileNotFoundError:
                raise CaptureDrift('Final capture has no exact owner expiry receipt') from None
            if (expiry.get('format') != 'context-intelligence-prune'
                    or type(expiry.get('version')) is not int or expiry['version'] != 1
                    or expiry['base_receipt_sha256'] != fence_digest
                    or expiry['source']['sha256'] != journal['candidate_sha256']
                    or expiry['source']['bytes'] != journal['candidate_bytes']
                    or expiry['expected']['sha256'] != receipt.sha256):
                raise CaptureDrift('Final capture has no exact owner expiry receipt')
            mapping = expiry['diagnostic_manifest']
            if (not isinstance(mapping['name'], str)
                    or Path(mapping['name']).name != mapping['name']
                    or mapping['name'] in {'', '.', '..'}):
                raise CaptureDrift('Invalid capture expiry mapping name')
            expiry_map = proof(self.source.parent / mapping['name'], mapping['sha256'])
        mapping_stream = proof(self.directory / 'records.jsonl', journal['mapping_sha256'])
        original = stack.enter_context(_pinned(self.directory / 'original'))
        installed = stack.enter_context(_pinned(self.source))
        manifest = stack.enter_context(_pinned(self.directory / 'assets.jsonl'))
        # Source generations remain pinned through the OUTER final proof hash
        # loop and all its cancellation/authority polls, not just slot parsing.
        with tempfile.TemporaryDirectory(prefix='capture-ack-selected-') as directory:
            if (_digest(original, self.poll) != journal['source_sha256']
                    or _digest(installed, self.poll) != receipt.sha256
                    or _digest(manifest, self.poll) != journal['manifest_sha256']):
                raise CaptureDrift('Capture checkpoint generation changed')
            manifest.seek(0)

            def mappings():
                rows = _record_rows(mapping_stream, journal)
                composed = zip_longest(rows, _expiry_rows(expiry_map, self.poll)) if expiry else (
                    (item, None) for item in rows)
                old_end = new_end = 0
                for ordinal, (item, expired) in enumerate(composed, 1):
                    if self.poll:
                        self.poll()
                    if item is None or (expiry and (expired is None or expired['old'] != item['new'])):
                        raise CaptureDrift('Capture expiry mapping does not join the image generation')
                    before, after = item['old'], expired['new'] if expiry else item['new']
                    if (type(item['ordinal']) is not int or item['ordinal'] != ordinal
                            or type(before['offset']) is not int or type(after['offset']) is not int
                            or before['offset'] != old_end
                            or after['offset'] != new_end):
                        raise CaptureDrift('Capture acknowledgement mapping is incomplete')
                    for stream, fact in ((original, before), (installed, after)):
                        stream.seek(fact['offset'])
                        if type(fact['bytes']) is not int or fact['bytes'] <= 0:
                            raise CaptureDrift('Invalid capture acknowledgement span')
                        if _digest(stream, self.poll, fact['bytes']) != fact['sha256']:
                            raise CaptureDrift('Capture checkpoint slice differs from its mapping')
                    old_end += before['bytes']
                    new_end += after['bytes']
                    yield ordinal, before
                if (old_end != journal['source_bytes']
                        or new_end != os.fstat(installed.fileno()).st_size):
                    raise CaptureDrift('Capture acknowledgement mapping does not cover both generations')

            # Disk spool separates complete manifest/map admission from one
            # sealed CI batch. No per-selected-image full-generation hash loop.
            db = sqlite3.connect(Path(directory) / 'selected.sqlite3')
            os.chmod(Path(directory) / 'selected.sqlite3', 0o600)
            try:
                db.execute('PRAGMA cache_size=-1024')
                db.execute('CREATE TABLE selected(sequence INTEGER PRIMARY KEY,'
                           'snapshot TEXT,ref TEXT,reachable INTEGER DEFAULT 0)')
                mapped = iter(mappings())
                current, previous, count = None, -1, 0
                while raw := manifest.readline(65537):
                    if len(raw) > 65536 or not raw.endswith(b'\n'):
                        raise CaptureDrift('Capture manifest row exceeds its metadata bound')
                    row = json.loads(raw)
                    snapshot, owner = row['origin'], row['owner']
                    if (type(snapshot['image_offset']) is not int
                            or snapshot['image_offset'] <= previous):
                        raise CaptureDrift('Duplicate or unordered selected capture position')
                    previous = snapshot['image_offset']
                    while current is None or current[0] < snapshot['ordinal']:
                        current = next(mapped, None)
                        if current is None:
                            raise CaptureDrift('Selected capture image has no mapped record')
                    start, length = current[1]['offset'], current[1]['bytes']
                    if current[0] != snapshot['ordinal'] or not start <= previous < start + length:
                        raise CaptureDrift('Selected capture position is outside its mapped record')
                    if owner['binding'] == GROUP_BINDING:
                        selected = owner['selected']
                        if (type(selected) is not int or selected < 0
                                or owner['source_origins'][selected] != snapshot):
                            raise CaptureDrift('Selected capture group member changed')
                        ref = owner['refs'][selected]
                    elif owner['binding'] == BINDING:
                        ref = owner['ref']
                    else:
                        raise CaptureDrift('Unknown capture media owner binding')
                    count += 1
                    db.execute('INSERT INTO selected(sequence,snapshot,ref) VALUES(?,?,?)',
                               (count, json.dumps(snapshot), json.dumps(ref)))
                for _ in mapped:
                    pass
                original.seek(0)
                installed.seek(0)
                observed = 0
                with capture_image_verifier(original, installed, session_id=self.session_id,
                        source_sha256=journal['source_sha256'], installed_sha256=receipt.sha256,
                        poll=self.poll) as read_selected:
                    snapshots = (json.loads(row[0]) for row in db.execute(
                        'SELECT snapshot FROM selected ORDER BY sequence'))
                    with read_selected.read_many(snapshots) as values:
                        for observed, value in enumerate(values, 1):
                            row = db.execute('SELECT ref FROM selected WHERE sequence=?',
                                             (observed,)).fetchone()
                            if row is None:
                                raise CaptureDrift('Capture verifier returned an extra position')
                            if value['status'] == 'absent':
                                if not expiry:
                                    raise CaptureDrift('Selected capture reference disappeared without expiry')
                                continue
                            if (value['status'] != 'present'
                                    or diagnostic_image_ref(value['value']) != json.loads(row[0])):
                                raise CaptureDrift('Installed capture slot differs from its selected reference')
                            db.execute('UPDATE selected SET reachable=1 WHERE sequence=?', (observed,))
                    if observed != count:
                        raise CaptureDrift('Capture verifier did not exhaust selected positions')
                # CI batch + outer final hash checks succeeded. F1 will
                # additionally exhaust this iterator and pinned-proof checks
                # before it commits even the first checkpoint reference.
                for encoded, in db.execute('SELECT ref FROM selected WHERE reachable=1 ORDER BY sequence'):
                    yield json.loads(encoded)
            finally:
                db.close()


class UnknownHistoricalMedia(ValueError):
    pass


def capture_owner(app, row, operation, *, resume=False, poll=None):
    # Both actual historical owners were inspected: old UUID-upload metadata has
    # no creation/expiry/admission receipt; legacy computer issuance was memory
    # only. There is no durable capture-record-to-original-observation binding.
    # Modern accepted refs already use their owning readers and need no inline
    # conversion. An event timestamp, path or equal digest cannot recreate the
    # missing authority. Keep this explicit rather than discovering a fictitious
    # callback or silently assigning durable/null expiry to historical pixels.
    # Apply invokes this only inside its stopped-writer/flush lifetime, off the
    # event loop. The keyword arguments are the explicit resume/poll boundary for
    # a future receipt-backed context; they do not supply original authority.
    raise UnknownHistoricalMedia('Original image observation and expiry receipt is unavailable; historical pixels were kept unchanged')