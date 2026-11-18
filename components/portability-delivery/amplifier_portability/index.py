"""Rebuildable receipt metadata index; canonical signed JSON stays authoritative.

A committed pending entry precedes every canonical write. Those pending identities
are write intent, not disposable lookup data. Selected reads repair only those
exact entries; missing or unreadable evidence refuses admission, never assumes idle.
"""
from contextlib import contextmanager,closing
import base64
import json
import os
from pathlib import Path
import sqlite3
import stat

FIELDS=('id','sessionId','generation','source','destination','direction','phase','revision','createdAt','updatedAt','inputsReplayed','previousPhase','detail')

class IndexMigrationRequired(ValueError):pass

PROFILE={'rows':'id,session,generation,created,phase,body','pending':'id,session','metadata':'key,value'}

def complete(db):
    """Fixed schema and two singleton checks; never inspect receipt history."""
    try:
        for table,columns in PROFILE.items():
            if db.execute('SELECT type FROM sqlite_master WHERE name=?',(table,)).fetchone()!=('table',):return False
            db.execute(f'SELECT {columns} FROM {table} LIMIT 0')
        ready=db.execute("SELECT value FROM metadata WHERE key='ready'").fetchone()
        revision=db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
        return ready is not None and ready[0] in (0,1) and revision is not None and type(revision[0]) is int and revision[0]>=0
    except sqlite3.DatabaseError:return False

def require_pending(db):
    """A missing intent table cannot be reconstructed from successful JSON writes."""
    if db.execute("SELECT type FROM sqlite_master WHERE name='pending'").fetchone()!=('table',):
        raise IndexMigrationRequired('Transfer write intent is unavailable; preserve the database and inspect its receipts')
    db.execute('SELECT id,session FROM pending LIMIT 0')

class ReceiptIndex:
    def __init__(self,node):
        self.node=node;self.path=node.directory/'receipts.sqlite3'
        for suffix in ('','-wal','-shm','-journal'):
            path=Path(str(self.path)+suffix)
            if os.path.lexists(path) and not stat.S_ISREG(os.lstat(path).st_mode):raise IndexMigrationRequired('Receipt index storage must be regular non-symbolic files; explicit maintenance is required')
        existing=os.path.lexists(self.path)
        partial=False
        if existing:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as prior:
                partial=not complete(prior)
                if partial:require_pending(prior)
        with self.connect() as db:
            if partial:self.maintenance(db)
            self.schema(db)
            ready=db.execute("SELECT value FROM metadata WHERE key='ready'").fetchone()
            if not partial and ready is None:
                directory=node.directory/'receipts'
                exists=False
                if directory.exists():
                    with os.scandir(directory) as entries:exists=any(x.name.endswith('.json') for x in entries)
                db.execute("INSERT INTO metadata VALUES('ready',?)",(int(not exists),))
                db.execute("INSERT INTO metadata VALUES('revision',0)")
    @staticmethod
    def maintenance(db):
        # executescript commits before DDL; persist the fence before any missing
        # derived table can be recreated, including interruption during repair.
        db.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value INTEGER NOT NULL)')
        db.execute("INSERT OR REPLACE INTO metadata VALUES('ready',0)")
        db.execute("INSERT OR IGNORE INTO metadata VALUES('revision',0)");db.commit()
    @staticmethod
    def schema(db):
        db.executescript('''CREATE TABLE IF NOT EXISTS rows(id TEXT PRIMARY KEY,session TEXT NOT NULL,generation INTEGER NOT NULL,created REAL NOT NULL,phase TEXT NOT NULL,body TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS rows_latest ON rows(session,generation DESC,created DESC,id DESC);
            CREATE INDEX IF NOT EXISTS rows_page ON rows(created DESC,id DESC);
            CREATE INDEX IF NOT EXISTS rows_session_page ON rows(session,created DESC,id DESC);
            CREATE INDEX IF NOT EXISTS rows_phase ON rows(phase,id);
            CREATE TABLE IF NOT EXISTS pending(id TEXT PRIMARY KEY,session TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS pending_session ON pending(session,id);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value INTEGER NOT NULL);''')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path);db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
        os.chmod(self.path,0o600)
        try:
            with db:yield db
        finally:db.close()
    def require_ready(self,db):
        if not complete(db) or not db.execute("SELECT value FROM metadata WHERE key='ready'").fetchone()[0]:raise IndexMigrationRequired('Existing transfer receipts require explicit rebuild_index maintenance before indexed admission')
    @staticmethod
    def put(db,row):
        summary={k:row[k] for k in FIELDS if k in row}
        data=json.dumps(summary,separators=(',',':'),allow_nan=False)
        if len(data.encode())>16384:raise ValueError('Transfer metadata exceeds the 16 KiB index bound')
        db.execute('INSERT OR REPLACE INTO rows VALUES(?,?,?,?,?,?)',(row['id'],row['sessionId'],row['generation'],row['createdAt'],row['phase'],data))
    def prepare(self,row):
        with self.connect() as db:
            self.require_ready(db)
            db.execute('INSERT OR REPLACE INTO pending VALUES(?,?)',(row['id'],row['sessionId']))
    def committed(self,row):
        with self.connect() as db:
            self.put(db,row);db.execute('DELETE FROM pending WHERE id=?',(row['id'],))
            db.execute("UPDATE metadata SET value=value+1 WHERE key='revision'")
    def repair(self,db,sid=None):
        self.require_ready(db)
        query='SELECT id FROM pending'+(' WHERE session=?' if sid is not None else '')+' LIMIT 101'
        rows=db.execute(query,(sid,) if sid is not None else ()).fetchall()
        if len(rows)>100:raise ValueError('Interrupted transfer index maintenance is required; admission remains fenced')
        for (identity,) in rows:
            # No durability acknowledgement is returned here. Reads can safely
            # observe a visible canonical release even if its prior fsync failed.
            try:row=json.loads(self.node.path(identity).read_text())
            except (OSError,ValueError):raise ValueError('Transfer write outcome is unresolved; inspect its exact receipt') from None
            previous=db.execute('SELECT body FROM rows WHERE id=?',(identity,)).fetchone()
            self.put(db,row)
            current=db.execute('SELECT body FROM rows WHERE id=?',(identity,)).fetchone()
            if previous!=current:db.execute("UPDATE metadata SET value=value+1 WHERE key='revision'")
            # Pending remains until an explicit successful durable write/rebuild.
    def latest(self,sid):
        with self.node.lock,self.connect() as db:
            self.repair(db,sid)
            row=db.execute('SELECT body FROM rows WHERE session=? ORDER BY generation DESC,created DESC,id DESC LIMIT 1',(sid,)).fetchone()
            return json.loads(row[0]) if row else None
    def page(self,*,sid=None,cursor=None,limit=50,phases=None):
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('Transfer page limit must be between 1 and 100')
        with self.node.lock,self.connect() as db:
            self.repair(db,sid);revision=db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0]
            clauses=[];args=[]
            if sid is not None:clauses.append('session=?');args.append(sid)
            if phases is not None:
                if not isinstance(phases,(list,tuple)) or not 1<=len(phases)<=20 or any(not isinstance(p,str) for p in phases):raise ValueError('Bounded transfer phases required')
                phases=list(phases)
                clauses.append('phase IN ('+','.join('?' for _ in phases)+')');args.extend(phases)
            if cursor:
                try:
                    if not isinstance(cursor,str) or len(cursor)>1024:raise ValueError()
                    value=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
                    if value['revision']!=revision or value['sid']!=sid or value['phases']!=phases:raise ValueError()
                    created,identity=value['after'];clauses.append('(created,id)<(?,?)');args.extend((created,identity))
                except (TypeError,ValueError,KeyError):raise ValueError('Transfer page cursor is stale or invalid') from None
            query='SELECT body,created,id FROM rows'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY created DESC,id DESC LIMIT ?'
            values=db.execute(query,(*args,limit+1)).fetchall();items=[json.loads(x[0]) for x in values[:limit]];next_cursor=None
            if len(values)>limit:
                after=values[limit-1]
                next_cursor=base64.urlsafe_b64encode(json.dumps({'revision':revision,'sid':sid,'phases':phases,'after':[after[1],after[2]]},separators=(',',':')).encode()).decode().rstrip('=')
            return {'items':items,'nextCursor':next_cursor,'revision':revision}
    def recover(self):
        """Prepare lookup state at the caller's explicit startup recovery boundary."""
        with self.node.lock:
            with self.connect() as db:
                ready=complete(db) and db.execute("SELECT value FROM metadata WHERE key='ready'").fetchone()[0]
            if not ready:self.rebuild()
    def rebuild(self):
        """Linear disk scan, constant row memory; never replace missing write intent."""
        with self.node.lock,self.connect() as db:
            require_pending(db)
            self.maintenance(db);self.schema(db)
            db.execute('DELETE FROM rows');count=0;directory=self.node.directory/'receipts'
            if directory.exists():
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if not entry.name.endswith('.json'):continue
                        if not entry.is_file(follow_symlinks=False):raise ValueError('Canonical transfer receipt is not a regular file')
                        row=self.node.get(entry.name[:-5])
                        if row['id']!=entry.name[:-5]:raise ValueError('Canonical transfer identity differs from its filename')
                        self.put(db,row);count+=1
            # A missing canonical write has no receipt to reconstruct. Keep its
            # original pending identity and maintenance fence for inspection.
            db.execute('DELETE FROM pending WHERE id IN (SELECT id FROM rows)')
            if db.execute('SELECT 1 FROM pending LIMIT 1').fetchone():
                raise IndexMigrationRequired('Transfer write outcome is unresolved; inspect its exact receipt before rebuild')
            db.execute("UPDATE metadata SET value=1 WHERE key='ready'")
            db.execute("UPDATE metadata SET value=value+1 WHERE key='revision'")
            return {'indexed':count,'replayed':False}
