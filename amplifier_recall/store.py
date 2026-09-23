"""SQLite owns derived search data; callers own source access and authorization."""
import copy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class RecallStore:
    def __init__(self, path):
        path = Path(path)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        path.chmod(0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, signature TEXT, revision TEXT, value TEXT);
          CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(text, session_id UNINDEXED, identity UNINDEXED, metadata UNINDEXED, tokenize='unicode61');
          CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, scope TEXT, target TEXT, revision INTEGER, value TEXT);
          CREATE TABLE IF NOT EXISTS memory_versions(id TEXT, revision INTEGER, value TEXT, PRIMARY KEY(id,revision));
          CREATE TABLE IF NOT EXISTS memory_receipts(id TEXT PRIMARY KEY, fingerprint TEXT, result TEXT);
        ''')
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    @contextmanager
    def atomic(self):
        """Nestable memory transaction, including correction/supersession batches."""
        with self.lock:
            name = 'memory_' + uuid.uuid4().hex
            self.db.execute('SAVEPOINT '+name)
            try:
                yield
                self.db.execute('RELEASE SAVEPOINT '+name)
            except BaseException:
                self.db.execute('ROLLBACK TO SAVEPOINT '+name)
                self.db.execute('RELEASE SAVEPOINT '+name)
                raise

    def signatures(self):
        with self.lock:
            return dict(self.db.execute('SELECT id,signature FROM sources'))

    def replace(self, session, signature, revision, rows):
        sid = session['id']
        with self.lock, self.db:
            self.db.execute('DELETE FROM messages WHERE session_id=?', (sid,))
            self.db.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?)',
                (sid, signature, json.dumps(revision), json.dumps(session)))
            values = []
            for index, row in enumerate(rows):
                if row.get('role') not in {'user', 'assistant', 'task', 'artifact'} or not isinstance(row.get('text'), str):
                    continue
                identity = row.get('id') or digest([sid, index, row.get('role'), row['text']])
                metadata = {'index': index, 'messageId': identity, 'role': row['role'], 'via': row.get('via'),
                    'sha256': hashlib.sha256(row['text'].encode()).hexdigest(),
                    'sourceKind':row.get('sourceKind','message'), 'recordId':row.get('recordId'), 'recordRevision':row.get('recordRevision')}
                values.append((row['text'], sid, identity, json.dumps(metadata)))
            self.db.executemany('INSERT INTO messages VALUES (?,?,?,?)', values)
        return len(values)

    def prune(self, retained):
        with self.lock, self.db:
            for sid, in self.db.execute('SELECT id FROM sources').fetchall():
                if sid not in retained:
                    self.db.execute('DELETE FROM messages WHERE session_id=?', (sid,))
                    self.db.execute('DELETE FROM sources WHERE id=?', (sid,))

    def search(self, query, allowed, offset=0, limit=20):
        # User text is data, not an FTS expression. Limit term count and quote it.
        terms = re.findall(r'\w+', query, flags=re.UNICODE)[:20]
        if not terms:
            return {'items': [], 'nextOffset': None}
        match = ' AND '.join('"'+term.replace('"', '""')+'"' for term in terms)
        with self.lock, self.db:
            self.db.execute('CREATE TEMP TABLE IF NOT EXISTS allowed_sources(id TEXT PRIMARY KEY)')
            self.db.execute('DELETE FROM allowed_sources')
            self.db.executemany('INSERT INTO allowed_sources VALUES (?)', ((sid,) for sid in allowed))
            values = self.db.execute('''SELECT messages.session_id,messages.identity,messages.metadata,
              snippet(messages,0,'','',' … ',48),sources.value,sources.revision,bm25(messages)
              FROM messages JOIN allowed_sources ON allowed_sources.id=messages.session_id
              JOIN sources ON sources.id=messages.session_id WHERE messages MATCH ?
              ORDER BY bm25(messages),messages.session_id,messages.identity LIMIT ? OFFSET ?''',
                (match, limit+1, offset)).fetchall()
        items = [{'sessionId': sid, 'messageId': identity, **json.loads(metadata), 'snippet': snippet[:1000],
            'session': json.loads(session), 'sourceRevision': json.loads(revision), 'rank': score,
            'reference': {'sessionId': sid, 'messageId': identity, 'sourceRevision': json.loads(revision)}}
            for sid, identity, metadata, snippet, session, revision, score in values[:limit]]
        return {'items': items, 'nextOffset': offset+limit if len(values)>limit else None}

    def message(self, sid, identity, offset=0, limit=4000):
        with self.lock:
            row = self.db.execute('''SELECT text,metadata,sources.revision FROM messages JOIN sources
                ON sources.id=messages.session_id WHERE session_id=? AND identity=?''', (sid, identity)).fetchone()
        if row is None:
            raise ValueError('This indexed source is unavailable; refresh the index and search again.')
        text, metadata, revision = row
        return {**json.loads(metadata), 'sessionId': sid, 'sourceRevision': json.loads(revision),
            'text': text[offset:offset+limit], 'offset': offset,
            'nextOffset': offset+limit if offset+limit<len(text) else None}

    def list_memories(self, scopes, offset=0, limit=20):
        with self.lock:
            # Scope count is bounded by host policy (task, workspace, global).
            where = '1' if scopes is None else ' OR '.join('(scope=? AND target=?)' for _ in scopes) or '0'
            rows = self.db.execute('SELECT value FROM memories WHERE '+where+' ORDER BY id LIMIT ? OFFSET ?',
                (*[v for pair in (scopes or []) for v in pair], limit+1, offset)).fetchall()
        return {'items': [json.loads(row[0]) for row in rows[:limit]],
            'nextOffset': offset+limit if len(rows)>limit else None}

    def memory(self, identity):
        with self.lock:
            row = self.db.execute('SELECT value FROM memories WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise ValueError('This memory was deleted or is unavailable.')
        return json.loads(row[0])

    def versions(self, identity):
        self.memory(identity)
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute(
                'SELECT value FROM memory_versions WHERE id=? ORDER BY revision DESC LIMIT 50', (identity,))]

    def receipt(self, command_id, fingerprint):
        with self.lock:
            row = self.db.execute('SELECT fingerprint,result FROM memory_receipts WHERE id=?', (command_id,)).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise ValueError('This command ID was already used with different contents.')
        return {**json.loads(row[1]), 'duplicate': True}

    def mutate(self, action, args, *, command_id, provenance, request_fingerprint=None):
        fingerprint = request_fingerprint or digest([action,args,provenance])
        with self.atomic():
            old = self.db.execute('SELECT fingerprint,result FROM memory_receipts WHERE id=?', (command_id,)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise ValueError('This command ID was already used with different contents.')
                return {**json.loads(old[1]), 'duplicate': True}
            if action == 'memory.create':
                if self.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0]>=10000:
                    raise ValueError('Inspect and delete unused memories before adding more.')
                record = {'id': uuid.uuid4().hex, 'revision': 0, 'scope': args['scope'], 'target': args['target'],
                    'createdAt': time.time(), 'provenance': copy.deepcopy(provenance)}
            else:
                record = self.memory(args['id'])
                if record['revision'] != args['expectedRevision']:
                    raise ValueError('This memory changed. Read its current revision before editing.')
                if record.get('automationSourceKey'):
                    self.db.execute('INSERT OR IGNORE INTO memory_suppression VALUES (?)', (record['automationSourceKey'],))
            if action == 'memory.delete':
                self.db.execute('DELETE FROM memories WHERE id=?', (record['id'],))
                self.db.execute('DELETE FROM memory_versions WHERE id=?', (record['id'],))
                result = {'id': record['id'], 'deleted': True, 'revision': record['revision']+1}
            else:
                text = args['text'].strip()
                if not text or len(text)>8000:
                    raise ValueError('Memory text must contain 1–8000 characters.')
                record.update(text=text, updatedAt=time.time(), revision=record['revision']+1,
                    provenance=copy.deepcopy(provenance), source=copy.deepcopy(args.get('source', record.get('source'))))
                for key in ('automationKey', 'automationSourceKey', 'supersedes', 'supersededBy'):
                    if key in args:
                        record[key] = copy.deepcopy(args[key])
                self.db.execute('INSERT OR REPLACE INTO memories VALUES (?,?,?,?,?)',
                    (record['id'],record['scope'],record['target'],record['revision'],json.dumps(record)))
                self.db.execute('INSERT INTO memory_versions VALUES (?,?,?)',
                    (record['id'],record['revision'],json.dumps(record)))
                # Bounded history, explicitly reported by the host.
                self.db.execute('DELETE FROM memory_versions WHERE id=? AND revision<=?', (record['id'],record['revision']-50))
                result = {'id': record['id'], 'revision': record['revision'], 'scope': record['scope'], 'target': record['target']}
            # Receipts never retain the deleted note text or prior versions.
            self.db.execute('INSERT INTO memory_receipts VALUES (?,?,?)', (command_id,fingerprint,json.dumps(result)))
            return result
