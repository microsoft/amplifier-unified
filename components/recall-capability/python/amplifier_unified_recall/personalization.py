"""Derived personalization state in the existing Recall database.

No source history or execution state is owned here. Claims bound background
model attempts; they never schedule work or resume an interrupted call.
"""
import json
import time

from amplifier_recall.store import digest


DEFAULTS = {'revision': 0, 'contribute': False, 'use': False, 'excludedSessions': [],
            'maxCallsPerDay': 3}


class Personalization:
    def __init__(self, store):
        self.store = store
        with store.lock, store.db:
            store.db.executescript('''
              CREATE TABLE IF NOT EXISTS memory_settings(workspace TEXT PRIMARY KEY,value TEXT);
              CREATE TABLE IF NOT EXISTS memory_attempts(id TEXT PRIMARY KEY,workspace TEXT,session_id TEXT,created REAL,value TEXT);
              CREATE TABLE IF NOT EXISTS memory_suppression(key TEXT PRIMARY KEY);
              CREATE TABLE IF NOT EXISTS memory_activity(workspace TEXT PRIMARY KEY,value TEXT);
              CREATE INDEX IF NOT EXISTS memory_attempts_status ON memory_attempts(json_extract(value,'$.status'));
              CREATE INDEX IF NOT EXISTS memory_attempts_scope ON memory_attempts(workspace,created DESC);
              CREATE TABLE IF NOT EXISTS memory_automation(key TEXT PRIMARY KEY,source_key TEXT,text_key TEXT,note_id TEXT);
              CREATE INDEX IF NOT EXISTS memory_automation_text ON memory_automation(text_key);
            ''')
            store.db.execute("UPDATE memory_attempts SET value=? WHERE json_extract(value,'$.status')='claimed'", (json.dumps({'status':'interrupted','reason':'Previous attempt outcome unconfirmed; not replayed'}),))

    def activity(self, workspace, value=None):
        with self.store.lock, self.store.db:
            if value is not None:
                self.store.db.execute('INSERT OR REPLACE INTO memory_activity VALUES (?,?)', (workspace, json.dumps({'at':time.time(), **value})))
            row = self.store.db.execute('SELECT value FROM memory_activity WHERE workspace=?', (workspace,)).fetchone()
        return json.loads(row[0]) if row else None

    def settings(self, workspace):
        with self.store.lock:
            row = self.store.db.execute('SELECT value FROM memory_settings WHERE workspace=?', (workspace,)).fetchone()
        return {**DEFAULTS, **(json.loads(row[0]) if row else {}), 'workspace': workspace}

    def configure(self, workspace, args, provenance, command_id=None):
        fingerprint = digest(['memory.configure', workspace, args, provenance])
        with self.store.atomic():
            if command_id:
                previous = self.store.receipt(command_id, fingerprint)
                if previous is not None:
                    return previous
            current = self.settings(workspace)
            if args['expectedRevision'] != current['revision']:
                raise ValueError('Memory settings changed. Read the current revision first.')
            current.update({k: args[k] for k in DEFAULTS if k != 'revision' and k in args})
            current.update(revision=current['revision']+1, provenance=provenance)
            self.store.db.execute('INSERT OR REPLACE INTO memory_settings VALUES (?,?)', (workspace, json.dumps(current)))
            if command_id:
                self.store.db.execute('INSERT INTO memory_receipts VALUES (?,?,?)', (command_id, fingerprint, json.dumps(current)))
        return current

    def claim(self, workspace, sid, signature, limit):
        identity = digest([sid, signature])
        with self.store.lock, self.store.db:
            if self.store.db.execute('SELECT 1 FROM memory_attempts WHERE id=?', (identity,)).fetchone():
                return None
            start = time.time() // 86400 * 86400
            count = self.store.db.execute('SELECT COUNT(*) FROM memory_attempts WHERE workspace=? AND created>=?', (workspace, start)).fetchone()[0]
            if count >= limit:
                raise ValueError('The workspace memory call limit for today is reached.')
            self.store.db.execute('INSERT INTO memory_attempts VALUES (?,?,?,?,?)',
                (identity, workspace, sid, time.time(), json.dumps({'status': 'claimed', 'sourceRevision': signature})))
        return identity

    def finish(self, identity, result):
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE memory_attempts SET value=? WHERE id=?', (json.dumps(result), identity))

    def attempts(self, workspace):
        with self.store.lock:
            rows = self.store.db.execute('SELECT id,session_id,created,value FROM memory_attempts WHERE workspace=? ORDER BY created DESC LIMIT 20', (workspace,)).fetchall()
        return [{'id': identity, 'sessionId': sid, 'createdAt': at, **json.loads(value)} for identity,sid,at,value in rows]

    def save(self, workspace, sid, source, candidate, attempt):
        # Stable across re-indexing, app restart and model paraphrase. A corrected
        # or deleted quote never regenerates from the same human evidence.
        source_key = digest([sid, source['messageId'], source['sha256']])
        key = digest([source_key, ' '.join(candidate['quote'].split())])
        with self.store.lock:
            if self.store.db.execute('SELECT 1 FROM memory_suppression WHERE key=?', (source_key,)).fetchone():
                return None
            if self.store.db.execute('SELECT 1 FROM memory_automation WHERE key=? OR text_key=?',(key,digest([workspace,candidate['text'].casefold()]))).fetchone():
                return None
            note=self.store.mutate('memory.create', {'scope':'workspace','target':workspace,'text':candidate['text'],'source':source,'automationKey':key,'automationSourceKey':source_key},command_id='consolidate:'+key,provenance={'origin':'consolidation','sessionId':sid,'attemptId':attempt,'wording':'model-derived','evidence':'human quotation, not independently verified'})
            with self.store.atomic():self.store.db.execute('INSERT INTO memory_automation VALUES(?,?,?,?)',(key,source_key,digest([workspace,candidate['text'].casefold()]),note['id']))
            return note

    def suppress(self,note):
        if note.get('automationSourceKey'):
            with self.store.lock,self.store.db:self.store.db.execute('INSERT OR IGNORE INTO memory_suppression VALUES(?)',(note['automationSourceKey'],))
