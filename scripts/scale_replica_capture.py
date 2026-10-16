#!/usr/bin/env python3
"""Read-only source capture. Write a tar stream, never a source-host archive.

Operational audit helper, not application code. Output is private, not a public
fixture. Explicit credential stores and client drafts are excluded; historical
prose/tool output remains private user data, not a claim of secret-free content.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tarfile
import time

SECRET_KEYS = frozenset({'apikey', 'accesstoken', 'refreshtoken', 'clientsecret',
                         'password', 'authorization', 'bearertoken', 'authtoken',
                         'controltoken', 'sessionsecret', 'cookie', 'cookies',
                         'token', 'secret', 'privatekey', 'credential', 'credentials'})
PRIVATE_GLOBALS = frozenset({'view', 'devices', 'deviceCommands', 'voice',
                             'voicePreviewBusy', 'selectedSessionId'})
PRIVATE_TABLES = frozenset({'client_views', 'conversation_shares', 'feedback_requests',
                            'feedback_followups', 'feedback_attachments', 'feedback_excerpts'})


def redact(value):
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()
                if ''.join(c for c in key.lower() if c.isalnum()) not in SECRET_KEYS
                and key not in {'privateDrafts', 'clientViews'}}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def database(path):
    """Consistent SQLite online backup in RAM; filter private stores before output."""
    source = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
    source.execute('PRAGMA query_only=ON')
    target = sqlite3.connect(':memory:')
    target.execute('PRAGMA temp_store=MEMORY')
    started = time.monotonic()
    def progress(status, remaining, total):
        if time.monotonic() - started > 120:
            raise TimeoutError('SQLite capture exceeded bounded duration')
        time.sleep(.002)
    try:
        source.backup(target, pages=256, progress=progress, sleep=.05)
        tables = {r[0] for r in target.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in PRIVATE_TABLES & tables:
            target.execute('DELETE FROM "' + name + '"')
        if 'shell_records' in tables:
            target.execute("DELETE FROM shell_records WHERE kind IN ('client','command','change')")
        if 'state_records' in tables:
            for key in PRIVATE_GLOBALS:
                target.execute("DELETE FROM state_records WHERE kind='global' AND id=?", (key,))
        # JSON state/receipts can contain explicit credential fields even though
        # credential files themselves are never included. Preserve other shapes.
        for name in sorted(tables):
            if name.startswith('sqlite_'):
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            columns = [r[1] for r in target.execute('PRAGMA table_info(' + quoted + ')')
                       if r[1] in {'value', 'receipt', 'payload', 'configuration', 'body'}]
            for column in columns:
                for rowid, text in list(target.execute('SELECT rowid,"' + column + '" FROM ' + quoted)):
                    if not isinstance(text, str):
                        continue
                    try:
                        original = json.loads(text)
                    except ValueError:
                        continue
                    clean = redact(original)
                    if name == 'state' and isinstance(clean, dict):
                        for key in PRIVATE_GLOBALS:
                            clean.pop(key, None)
                    if clean != original:
                        target.execute('UPDATE ' + quoted + ' SET "' + column + '"=? WHERE rowid=?',
                                       (json.dumps(clean, ensure_ascii=False), rowid))
        target.commit()
        target.execute('VACUUM')  # discarded private bytes cannot survive free pages
        return target.serialize()
    finally:
        target.close()
        source.close()


class LimitedReader:
    def __init__(self, file, rate):
        self.file, self.rate = file, rate
        self.bytes = 0
        self.started = time.monotonic()
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        data = self.file.read(size)
        self.digest.update(data)
        self.bytes += len(data)
        delay = self.bytes / self.rate - (time.monotonic() - self.started)
        if delay > 0:
            time.sleep(delay)
        return data


def signature(info):
    return {'device': info.st_dev, 'inode': info.st_ino, 'bytes': info.st_size,
            'mtimeNs': info.st_mtime_ns, 'ctimeNs': info.st_ctime_ns}


def selected_files(app, native, phase):
    if phase == 'databases':
        for name in ('app.sqlite3', 'native-catalog.sqlite3', 'operations.sqlite3',
                     'schedules.sqlite3', 'observations.sqlite3'):
            if (app / name).is_file():
                yield app / name, 'app/' + name, True
        return
    if phase == 'artifacts':
        for folder in ('artifacts', 'attachments'):
            for path in sorted((app / folder).rglob('*')):
                if path.is_file() and not path.is_symlink():
                    yield path, 'app/' + str(path.relative_to(app)), False
        return
    if phase == 'ancillary':
        # Legacy saved display resources and durable checkout receipts. Do not
        # copy effective runtime configuration, keys, processes or cache trees.
        names = {'view.json', 'checkpoint.json', 'context-checkpoint.json',
                 'metadata.json', 'transcript.jsonl', 'control-state.json'}
        for folder in ('sessions', 'worktree-handoffs'):
            for path in sorted((app / folder).rglob('*.json')):
                if (path.is_file() and not path.is_symlink()
                        and (folder == 'worktree-handoffs' or path.name in names
                             or re.fullmatch(r'[a-f0-9]{64}\.json', path.name))):
                    yield path, 'app/' + str(path.relative_to(app)), False
        return
    names = ({'events.jsonl'} if phase == 'events' else
             {'metadata.json', 'metadata.json.backup', 'transcript.jsonl',
              'transcript.jsonl.backup', 'transcript.jsonl.append-pending'})
    # The .backup files here are canonical recovery inputs, not arbitrary
    # historical duplicate backups. All *.bak/code/keys/config/checkpoints omitted.
    for directory, children, files in os.walk(native / 'projects', followlinks=False):
        children[:] = sorted(n for n in children if not Path(directory, n).is_symlink())
        for name in sorted(set(files) & names):
            path = Path(directory, name)
            yield path, 'native/' + str(path.relative_to(native)), False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--phase', choices=('databases', 'artifacts', 'primary', 'events', 'ancillary', 'workspace-inventory'), required=True)
    parser.add_argument('--shard', type=int, default=0)
    parser.add_argument('--shards', type=int, default=1)
    parser.add_argument('--rate-mib', type=float, default=80)
    args = parser.parse_args()
    if not 0 <= args.shard < args.shards or args.rate_mib <= 0:
        parser.error('Invalid shard/rate')
    receipts = []
    started = time.time()
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|', format=tarfile.PAX_FORMAT) as archive:
        if args.phase == 'workspace-inventory':
            paths = set()
            db = sqlite3.connect((args.app/'native-catalog.sqlite3').as_uri()+'?mode=ro', uri=True)
            paths.update(row[0] for row in db.execute('SELECT DISTINCT workspace_path FROM native_rows') if row[0])
            db.close()
            db = sqlite3.connect((args.app/'app.sqlite3').as_uri()+'?mode=ro', uri=True)
            paths.update(row[0] for row in db.execute('SELECT DISTINCT workspace FROM history_local_rows') if row[0])
            row = db.execute("SELECT value FROM state_records WHERE kind='global' AND id='workspaces'").fetchone()
            if row:
                paths.update(w['path'] for w in json.loads(row[0])['value'] if isinstance(w,dict) and w.get('path'))
            db.close()
            rows=[]
            for value in sorted(paths):
                path=Path(value)
                if not path.is_absolute() or '..' in path.parts:
                    rows.append({'path':value,'supported':False});continue
                rows.append({'path':value,'supported':True,'exists':path.exists(),'isDir':path.is_dir(),
                             'resolved':str(path.resolve())})
            payload=json.dumps({'capturedAt':time.time(),'workspaces':rows},separators=(',',':')).encode()
            info=tarfile.TarInfo('capture/workspaces-source.json');info.size=len(payload);info.mode=0o600
            archive.addfile(info,io.BytesIO(payload))
            print(json.dumps({'phase':args.phase,'paths':len(rows),'existing':sum(r.get('isDir',False) for r in rows)}),file=sys.stderr)
            return
        for path, name, is_database in selected_files(args.app, args.native, args.phase):
            if int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) % args.shards != args.shard:
                continue
            if is_database:
                payload = database(path)
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(payload), 0o600, int(time.time())
                archive.addfile(info, io.BytesIO(payload))
                receipts.append({'path': name, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
                                 'consistency': 'online-backup-filtered', 'capturedAt': time.time()})
                del payload
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as file:
                before = os.fstat(file.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError('Nonregular capture input')
                reader = LimitedReader(file, args.rate_mib * 1024 * 1024)
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = before.st_size, 0o600, before.st_mtime
                archive.addfile(info, reader)
                after = os.fstat(file.fileno())
                receipts.append({'path': name, 'before': signature(before), 'after': signature(after),
                                 'bytes': reader.bytes, 'sha256': reader.digest.hexdigest(),
                                 'stable': signature(before) == signature(after)})
        receipt = {'phase': args.phase, 'shard': args.shard, 'shards': args.shards,
                   'startedAt': started, 'finishedAt': time.time(), 'files': receipts,
                   'globalAtomicSnapshot': False, 'privateData': True}
        payload = json.dumps(receipt, separators=(',', ':')).encode()
        info = tarfile.TarInfo('capture/' + args.phase + '-' + str(args.shard) + '.json')
        info.size, info.mode = len(payload), 0o600
        archive.addfile(info, io.BytesIO(payload))
    print(json.dumps({'phase': args.phase, 'shard': args.shard, 'files': len(receipts),
                      'bytes': sum(r['bytes'] for r in receipts),
                      'unstable': sum(r.get('stable') is False for r in receipts),
                      'seconds': time.time()-started}), file=sys.stderr, flush=True)


if __name__ == '__main__':
    main()
