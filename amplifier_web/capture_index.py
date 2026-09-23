"""Incremental read-only indexes of shared CI files, never an upload backfill."""
from collections import OrderedDict
from datetime import datetime
import hashlib
import json
import stat

from .session_files import capture_sessions_dir, validate_capture, validate_id


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
        previous = db.execute('SELECT offset,inode FROM captures WHERE path=?', (str(path),)).fetchone()
        offset = previous[0] if previous and previous[1] == stat.st_ino and previous[0] <= stat.st_size else 0
        if previous and not offset and previous[0]:
            # A repaired/replaced capture invalidates byte offsets. Leave the
            # original file alone; remove only its derived indexes/outboxes.
            stale = [r[0] for r in db.execute("SELECT id FROM records WHERE json_extract(data,'$.\"$event\"')=?", (str(path),))]
            db.executemany('DELETE FROM deliveries WHERE record_id=?', ((i,) for i in stale))
            db.executemany('DELETE FROM records WHERE id=?', ((i,) for i in stale))
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
