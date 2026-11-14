"""Incremental read-only indexes of shared CI files, never an upload backfill."""
from collections import OrderedDict
from datetime import datetime
import hashlib
import json
import stat

from .session_files import capture_sessions_dir, validate_capture, validate_id


class CaptureDrift(ValueError):
    """A changed capture must not erase delivery evidence or enroll old events."""


def maintenance_schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS capture_maintenance('
               'path TEXT PRIMARY KEY, operation_id TEXT NOT NULL, value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS capture_rebindings('
               'path TEXT NOT NULL, operation_id TEXT NOT NULL, value TEXT NOT NULL,'
               'PRIMARY KEY(path,operation_id))')
    db.execute('CREATE TABLE IF NOT EXISTS capture_quarantine('
               'path TEXT PRIMARY KEY, reason TEXT NOT NULL)')


def _file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def hold_replacement(db, path, operation_id, witness):
    """Commit before file replacement, while the diagnostics flush lock is held.

    The caller owns source/writer exclusion; this ledger is not that exclusion.
    A crash leaves a hold, never permission for ordinary reindex/backfill.
    """
    maintenance_schema(db)
    path = str(path)
    old = db.execute('SELECT operation_id,value FROM capture_maintenance WHERE path=?', (path,)).fetchone()
    encoded = json.dumps(witness, sort_keys=True)
    completed = db.execute('SELECT value FROM capture_rebindings WHERE path=? AND operation_id=?',
                           (path, operation_id)).fetchone()
    if completed:
        if json.loads(completed[0]) != witness:
            raise CaptureDrift('Completed capture replacement identity changed')
        return  # Rebind's exact completed receipt owns the later readback.
    if old and tuple(old) != (operation_id, encoded):
        raise CaptureDrift('Another capture replacement is unresolved')
    if old:
        return  # A retained uncertain replacement is reconciled, not restaged.
    from pathlib import Path
    source = Path(path)
    stamp = source.lstat()
    if not stat.S_ISREG(stamp.st_mode) or source.is_symlink():
        raise CaptureDrift('Capture source is not an owned regular file')
    if stamp.st_size != witness['old']['bytes'] or _file_digest(source) != witness['old']['sha256'] or source.lstat() != stamp:
        raise CaptureDrift('Capture source changed before replacement admission')
    if stamp.st_size:
        with source.open('rb') as stream:
            stream.seek(-1, 2)
            if stream.read(1) != b'\n':
                raise CaptureDrift('Capture has an unterminated tail; replacement is held')
    cursor = db.execute('SELECT offset,inode FROM captures WHERE path=?', (path,)).fetchone()
    if cursor and (cursor[0] != witness['old']['bytes'] or cursor[1] != stamp.st_ino):
        raise CaptureDrift('Capture indexing has not reached the qualified source boundary')
    # Refuse stale/legacy references BEFORE diagnostic metadata is expired or
    # sidecars unlinked, not merely during postcommit index repair.
    with source.open('rb') as stream:
        for raw, in db.execute("SELECT data FROM records WHERE json_extract(data,'$.\"$event\"')=?", (path,)):
            reference = json.loads(raw)
            if (type(reference.get('offset')) is not int or type(reference.get('bytes')) is not int
                    or reference['offset'] < 0 or reference['bytes'] <= 0
                    or reference['offset'] + reference['bytes'] > stamp.st_size
                    or not isinstance(reference.get('sha256'), str)):
                raise CaptureDrift('Legacy or invalid capture references need qualification before replacement')
            stream.seek(reference['offset'])
            remaining = reference['bytes']
            digest = hashlib.sha256()
            while remaining:
                block = stream.read(min(remaining, 65536))
                if not block:
                    raise CaptureDrift('Indexed capture reference extends beyond source')
                digest.update(block)
                remaining -= len(block)
            if digest.hexdigest() != reference['sha256']:
                raise CaptureDrift('Indexed capture reference is stale before replacement')
    if source.lstat() != stamp:
        raise CaptureDrift('Capture source changed during reference admission')
    db.execute('INSERT OR IGNORE INTO capture_maintenance VALUES(?,?,?)', (path, operation_id, encoded))


def rebind_replaced_capture(db, path, operation_id, witness, mappings):
    """Rebind exact byte references, preserving IDs, delivery rows and outcomes.

    ``mappings`` streams all physical JSONL lines, with old/new offset, bytes and
    sha256. The writer has already durably stored this mapping before replacing
    the source. The caller retains its real writer fence and flush lock.
    """
    from pathlib import Path
    path = Path(path)
    maintenance_schema(db)
    held = db.execute('SELECT operation_id,value FROM capture_maintenance WHERE path=?', (str(path),)).fetchone()
    completed = db.execute('SELECT value FROM capture_rebindings WHERE path=? AND operation_id=?',
                           (str(path), operation_id)).fetchone()
    if completed:
        if json.loads(completed[0]) != witness:
            raise CaptureDrift('Capture replacement identity was reused with different proof')
        if path.is_symlink() or path.stat().st_size != witness['new']['bytes'] or _file_digest(path) != witness['new']['sha256']:
            raise CaptureDrift('Previously rebound capture changed')
        return {'alreadyRebound': True, 'backfilled': 0, 'deliveriesChanged': 0}
    if not held or held[0] != operation_id or json.loads(held[1]) != witness:
        raise CaptureDrift('Capture replacement proof is unavailable')
    stamp = path.lstat()
    if not stat.S_ISREG(stamp.st_mode) or path.is_symlink():
        raise CaptureDrift('Replacement is not an owned regular file')
    old, new = witness['old'], witness['new']
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    if stamp.st_size != new['bytes'] or digest.hexdigest() != new['sha256']:
        raise CaptureDrift('Replacement bytes do not match their saved proof')
    cursor = db.execute('SELECT offset,inode FROM captures WHERE path=?', (str(path),)).fetchone()
    if cursor and cursor[0] != old['bytes']:
        raise CaptureDrift('Capture indexing has not reached the qualified source boundary')
    db.execute('SAVEPOINT capture_rebind')
    try:
        # SQLite TEMP table bounds memory and makes reference joining linear in
        # retained records + mapping size, not a corpus scan per mapping line.
        db.execute('CREATE TEMP TABLE IF NOT EXISTS capture_rebind_map('
                   'old_offset INTEGER PRIMARY KEY,old_bytes INTEGER,old_sha TEXT,'
                   'new_offset INTEGER,new_bytes INTEGER,new_sha TEXT)')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS capture_rebind_new ON capture_rebind_map(new_offset)')
        db.execute('DELETE FROM capture_rebind_map')
        old_end = new_end = count = 0
        with path.open('rb') as stream:
            for item in mappings:
                before, after = item['old'], item['new']
                for entry in (before, after):
                    if any(type(entry.get(key)) is not int or entry[key] < 0 for key in ('offset', 'bytes')):
                        raise CaptureDrift('Invalid capture coordinates')
                    if not entry['bytes'] or not isinstance(entry.get('sha256'), str) or len(entry['sha256']) != 64:
                        raise CaptureDrift('Invalid capture slice identity')
                if before['offset'] != old_end or after['offset'] != new_end:
                    raise CaptureDrift('Capture mapping does not cover ordered complete records')
                size, line_hash, last = after['bytes'], hashlib.sha256(), None
                while size:
                    chunk = stream.read(min(size, 65536))
                    if not chunk:
                        raise CaptureDrift('Capture mapping extends beyond replacement')
                    size -= len(chunk)
                    line_hash.update(chunk)
                    last = chunk[-1]
                if last != 10 or line_hash.hexdigest() != after['sha256']:
                    raise CaptureDrift('Capture line does not match its mapping')
                db.execute('INSERT INTO capture_rebind_map VALUES(?,?,?,?,?,?)',
                           (old_end, before['bytes'], before['sha256'], new_end, after['bytes'], after['sha256']))
                old_end += before['bytes']
                new_end += after['bytes']
                count += 1
        if old_end != old['bytes'] or new_end != new['bytes']:
            raise CaptureDrift('Capture mapping is incomplete')
        # Legacy references without a hash cannot prove an old slice; held for
        # explicit owner qualification rather than matched by event_id alone.
        records = db.execute("SELECT id,data FROM records WHERE json_extract(data,'$.\"$event\"')=?", (str(path),))
        for identity, raw in records:
            reference = json.loads(raw)
            match = db.execute('SELECT old_bytes,old_sha,new_offset,new_bytes,new_sha '
                               'FROM capture_rebind_map WHERE old_offset=?', (reference['offset'],)).fetchone()
            if not match or (reference['bytes'], reference.get('sha256')) != tuple(match[:2]):
                raise CaptureDrift('Retained event reference does not match the qualified old source')
            reference.update(offset=match[2], bytes=match[3], sha256=match[4])
            db.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(reference), identity))
        if path.stat() != stamp:
            raise CaptureDrift('Replacement changed during index reconciliation')
        db.execute('INSERT INTO captures VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET '
                   'offset=excluded.offset,inode=excluded.inode', (str(path), new_end, stamp.st_ino))
        db.execute('DELETE FROM capture_maintenance WHERE path=? AND operation_id=?', (str(path), operation_id))
        db.execute('DELETE FROM capture_quarantine WHERE path=?', (str(path),))
        db.execute('INSERT INTO capture_rebindings VALUES(?,?,?)', (str(path), operation_id, json.dumps(witness)))
        db.execute('RELEASE capture_rebind')
        return {'records': count, 'backfilled': 0, 'deliveriesChanged': 0}
    except BaseException:
        db.execute('ROLLBACK TO capture_rebind')
        db.execute('RELEASE capture_rebind')
        raise


class CaptureMetadataCache:
    """Bounded successful format checks; never retain capture metadata or events."""

    MAX_ENTRIES = 2048

    def __init__(self):
        self._validated = OrderedDict()

    @staticmethod
    def _stamp(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def validate(self, directory):
        path = directory / 'metadata.json'
        key = str(path)
        try:
            before = path.stat()
        except OSError:
            self._validated.pop(key, None)
            raise
        stamp = self._stamp(before)
        if stat.S_ISREG(before.st_mode) and self._validated.get(key) == stamp:
            self._validated.move_to_end(key)
            return
        # Failed validation must not leave a stale positive entry behind.
        self._validated.pop(key, None)
        validate_capture(directory)
        try:
            after = path.stat()
        except OSError:
            # Validation succeeded, but the source changed before admission.
            # Preserve that result; the next scan checks the file again.
            return
        if stat.S_ISREG(after.st_mode) and stamp == self._stamp(after):
            self._validated[key] = stamp
            while len(self._validated) > self.MAX_ENTRIES:
                self._validated.popitem(last=False)


def event_stream(event):
    prefix = event.split(':', 1)[0]
    return {'prompt': 'conversation', 'llm': 'usage', 'provider': 'usage', 'tool': 'tools',
            'delegate': 'workers', 'worker': 'workers', 'session': 'sessions',
            'execution': 'sessions', 'canvas': 'canvas'}.get(prefix, 'app')


def index_shared(db, scopes, config, *, metadata_cache=None):
    added = []
    maintenance_schema(db)
    # Workers share workspace roots. Re-resolve these on each invocation so
    # relocation settings and workspace symlink changes are observed next time.
    roots = {}
    for workspace, session in set(scopes):
        if workspace not in roots:
            roots[workspace] = capture_sessions_dir(workspace)
        directory = roots[workspace] / validate_id(session) / 'context-intelligence'
        path = directory / 'events.jsonl'
        if not path.is_file():
            continue
        if metadata_cache is None:
            validate_capture(directory)
        else:
            metadata_cache.validate(directory)
        stat = path.stat()
        if db.execute('SELECT 1 FROM capture_maintenance WHERE path=? UNION ALL '
                      'SELECT 1 FROM capture_quarantine WHERE path=? LIMIT 1', (str(path), str(path))).fetchone():
            # Quarantine this source only. Its retained cursor/records/outcomes
            # remain held; unrelated sources and app-authored rows still persist.
            continue
        previous = db.execute('SELECT offset,inode FROM captures WHERE path=?', (str(path),)).fetchone()
        offset = previous[0] if previous and previous[1] == stat.st_ino and previous[0] <= stat.st_size else 0
        if previous and (previous[1] != stat.st_ino or previous[0] > stat.st_size):
            # Inode drift is not authority to discard accepted delivery facts
            # or forward a rewritten historical record as a new event.
            db.execute('INSERT OR IGNORE INTO capture_quarantine VALUES(?,?)',
                       (str(path), 'source-generation-drift'))
            continue
        with path.open('rb') as stream:
            stream.seek(offset)
            for _ in range(1000):
                start = stream.tell()
                line = stream.readline(2_000_001)
                if len(line) == 2_000_001 and not line.endswith(b'\n'):
                    while line and not line.endswith(b'\n'):
                        line = stream.readline(2_000_001)
                    if not line.endswith(b'\n'):
                        break
                    offset = stream.tell()
                    continue
                if not line or not line.endswith(b'\n'):
                    # A hook may still be appending. Retry an incomplete tail.
                    break
                offset = stream.tell()
                try:
                    row = json.loads(line)
                    event, data = row['event'], row['data']
                    if not isinstance(data, dict):
                        continue
                    # Unified's own rows were already indexed at capture time.
                    if data.get('app') == 'amplifier-unified':
                        continue
                    category = event_stream(event)
                    if category not in config['streams']:
                        continue
                    at = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).timestamp()
                    event_id = data.get('event_id')
                    identity = 'capture-' + hashlib.sha256(str(path).encode() + str(start).encode() + line).hexdigest()
                    reference = {'$event': str(path), 'offset': start, 'bytes': len(line), 'eventId': event_id,
                                 'sha256': hashlib.sha256(line).hexdigest()}
                    inserted = db.execute('INSERT OR IGNORE INTO records(id,at,stream,session,workspace,event,data) VALUES(?,?,?,?,?,?,?)',
                               (identity, at, category, session, workspace, event, json.dumps(reference)))
                    if inserted.rowcount:
                        added.append((identity, at, category, session, workspace, event, data))
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue  # The original capture remains available for repair.
        if previous is None or previous[0] != offset or previous[1] != stat.st_ino:
            db.execute('INSERT OR REPLACE INTO captures VALUES(?,?,?)', (str(path), offset, stat.st_ino))
    return added
