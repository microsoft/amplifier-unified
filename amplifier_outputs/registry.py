"""Caller owns transactions, authorization, content access and serialization."""
import copy
import hashlib
import json
import time
import uuid


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class OutputRegistry:
    def __init__(self, db):
        self.db = db
        db.execute('CREATE TABLE IF NOT EXISTS output_records (id TEXT PRIMARY KEY, session_id TEXT, created REAL, value TEXT)')
        db.execute('CREATE INDEX IF NOT EXISTS output_session ON output_records(session_id,created)')
        db.execute('CREATE TABLE IF NOT EXISTS output_receipts (id TEXT PRIMARY KEY, fingerprint TEXT, value TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS output_comments (id TEXT PRIMARY KEY, output_id TEXT, value TEXT)')

    def read(self, identity):
        row = self.db.execute('SELECT value FROM output_records WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise ValueError('This output relationship is unavailable.')
        return json.loads(row[0])

    def list(self, sid, *, include_unlinked=False, offset=0, limit=20):
        rows = self.db.execute("SELECT value FROM output_records WHERE session_id=? AND (? OR json_extract(value,'$.linked')=1) ORDER BY created DESC,id LIMIT ? OFFSET ?",
            (sid, include_unlinked, limit+1, offset)).fetchall()
        return {'items':[json.loads(row[0]) for row in rows[:limit]], 'nextOffset':offset+limit if len(rows)>limit else None}

    def receipt(self, identity, request):
        row = self.db.execute('SELECT fingerprint,value FROM output_receipts WHERE id=?',(identity,)).fetchone()
        if row:
            if row[0]!=fingerprint(request):
                raise ValueError('This command identity already has different contents.')
            return {**json.loads(row[1]), 'duplicate':True}

    def remember(self, identity, request, value):
        self.db.execute('INSERT INTO output_receipts VALUES (?,?,?)',(identity,fingerprint(request),json.dumps(value)))
        return value

    def create(self, sid, value):
        if self.db.execute('SELECT COUNT(*) FROM output_records').fetchone()[0]>=10000:
            raise ValueError('The output relationship limit is reached.')
        record = {**copy.deepcopy(value),'id':uuid.uuid4().hex,'sessionId':sid,'createdAt':time.time(),'revision':1,'linked':True}
        parent_id = record.get('parentId')
        if parent_id:
            parent = self.read(parent_id)
            if parent['sessionId']!=sid:
                raise ValueError('Output lineage must stay within the originating conversation.')
            record['version'] = parent.get('version',1)+1
        else:
            record['version']=1
        self.db.execute('INSERT INTO output_records VALUES (?,?,?,?)',(record['id'],sid,record['createdAt'],json.dumps(record)))
        return record

    def link(self, identity, expected_revision, linked):
        record = self.read(identity)
        if record['revision']!=expected_revision:
            raise ValueError('The relationship changed. Read its current revision.')
        record.update(linked=linked,revision=record['revision']+1)
        self.db.execute('UPDATE output_records SET value=? WHERE id=?',(json.dumps(record),identity))
        return record

    def comments(self, identity):
        return [json.loads(row[0]) for row in self.db.execute('SELECT value FROM output_comments WHERE output_id=? ORDER BY rowid LIMIT 200',(identity,))]

    def comment(self, identity, value):
        if len(self.comments(identity))>=200:
            raise ValueError('This output already has200review comments.')
        comment = {**copy.deepcopy(value),'id':uuid.uuid4().hex,'outputId':identity,'createdAt':time.time()}
        self.db.execute('INSERT INTO output_comments VALUES (?,?,?)',(comment['id'],identity,json.dumps(comment)))
        return comment
