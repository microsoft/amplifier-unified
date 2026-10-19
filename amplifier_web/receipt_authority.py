"""Private owner-scoped authority in the existing host diagnostics database.

The host owns this schema and its lifecycle. Offline restore/schema surgery must
retire disposable proofs. REPLACE and conflict-replacing UPDATE of records are
unsupported internal writer forms; ordinary INSERT OR IGNORE remains supported.
Revisions prove currentness, not receipt authenticity. Readers still authenticate
exact receipts and source/owner/support-page dependencies in their callers.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat


MAX_REVISION = 2**63 - 1
_BASE = {
    'records': ('table', 'records', 'CREATE TABLE records(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,at REAL,stream TEXT,session TEXT,workspace TEXT,event TEXT,data TEXT)'),
    'sqlite_autoindex_records_1': ('index', 'records', None),
    'record_session': ('index', 'records', 'CREATE INDEX record_session ON records(session,seq)'),
}
_AUTHORITY = {
    'binding_meta': ('table', 'binding_meta', "CREATE TABLE binding_meta(singleton INTEGER PRIMARY KEY CHECK(singleton=1),schema INTEGER NOT NULL CHECK(schema=1),epoch BLOB NOT NULL CHECK(typeof(epoch)='blob' AND length(epoch)=16))"),
    'binding_owner': ('table', 'binding_owner', "CREATE TABLE binding_owner(workspace TEXT COLLATE BINARY NOT NULL,session TEXT COLLATE BINARY NOT NULL,revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>=1),PRIMARY KEY(workspace,session))"),
    'sqlite_autoindex_binding_owner_1': ('index', 'binding_owner', None),
    'binding_records_owner': ('index', 'records', 'CREATE INDEX binding_records_owner ON records(workspace,session)'),
}
_META_GUARD = """
    SELECT CASE WHEN (SELECT count(*) FROM binding_meta)!=1 OR NOT EXISTS
        (SELECT 1 FROM binding_meta WHERE singleton=1 AND typeof(schema)='integer'
         AND schema=1 AND typeof(epoch)='blob' AND length(epoch)=16)
        THEN RAISE(ABORT,'Invalid receipt authority metadata') END;
"""


def _key(alias):
    return f'workspace={alias}.workspace AND session={alias}.session'


def _owner_guard(alias):
    key = _key(alias)
    return f"""
        SELECT CASE WHEN typeof({alias}.workspace)!='text' OR {alias}.workspace=''
            OR typeof({alias}.session)!='text' OR {alias}.session=''
            OR EXISTS(SELECT 1 FROM binding_owner WHERE {key} AND
                (typeof(revision)!='integer' OR revision<1 OR revision>{MAX_REVISION}))
            OR (NOT EXISTS(SELECT 1 FROM binding_owner WHERE {key})
                AND EXISTS(SELECT 1 FROM records WHERE {key}))
            THEN RAISE(ABORT,'Invalid receipt authority owner') END;
    """


def _bump(alias, condition='1'):
    key = _key(alias)
    # Seed one, then advance: an installed legacy owner starts at one; the first
    # successful insertion for a new owner produces two. No meaning is attached
    # to the distance between revisions. Explicit ABORT survives outer OR IGNORE.
    return f"""
        SELECT CASE WHEN ({condition}) AND EXISTS
            (SELECT 1 FROM binding_owner WHERE {key} AND revision>={MAX_REVISION})
            THEN RAISE(ABORT,'Receipt authority revision overflow') END;
        INSERT INTO binding_owner(workspace,session,revision)
            SELECT {alias}.workspace,{alias}.session,1 WHERE ({condition})
            AND NOT EXISTS(SELECT 1 FROM binding_owner WHERE {key});
        UPDATE binding_owner SET revision=revision+1 WHERE {key} AND ({condition});
    """


for _operation, _aliases in [('insert', ('NEW',)), ('update', ('OLD', 'NEW')), ('delete', ('OLD',))]:
    _name = 'binding_records_before_' + _operation
    _sql = (f'CREATE TRIGGER {_name} BEFORE {_operation.upper()} ON records BEGIN '
            + _META_GUARD + ''.join(_owner_guard(alias) for alias in _aliases) + ' END')
    _AUTHORITY[_name] = ('trigger', 'records', _sql)
_CHANGED = ' OR '.join(f'OLD.{name} IS NOT NEW.{name}' for name in
                       ('seq', 'id', 'at', 'stream', 'session', 'workspace', 'event', 'data'))
for _operation, _body in [
    ('insert', _bump('NEW')),
    ('delete', _bump('OLD')),
    ('update', _bump('OLD') + _bump('NEW', 'OLD.workspace IS NOT NEW.workspace OR OLD.session IS NOT NEW.session')),
]:
    _name = 'binding_records_' + _operation
    _when = ' WHEN ' + _CHANGED if _operation == 'update' else ''
    _AUTHORITY[_name] = ('trigger', 'records',
        f'CREATE TRIGGER {_name} AFTER {_operation.upper()} ON records{_when} BEGIN {_body} END')


def _sql_key(sql):
    if sql is None:
        return None
    sql = re.sub(r'^CREATE (TABLE|INDEX) IF NOT EXISTS ', r'CREATE \1 ', sql.strip())
    # Whitespace outside quoted literals is immaterial; quoted contents are exact.
    return re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\s+",
                  lambda match: '' if match[0].isspace() else match[0], sql).rstrip(';')


def _normalized(objects):
    return {name: (kind, table, _sql_key(sql)) for name, (kind, table, sql) in objects.items()}


_SCHEMA = _normalized({**_BASE, **_AUTHORITY})
_FINGERPRINT = hashlib.sha256(json.dumps(_SCHEMA, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _objects(db):
    # Bound the catalog before fetching SQL bodies. Never scan payload records to
    # validate a read dependency, and never trust a declared schema version alone.
    rows = db.execute("SELECT type,name,tbl_name,length(sql),CASE WHEN length(sql)<=24000 THEN sql END "
        "FROM sqlite_master WHERE tbl_name IN ('records','binding_meta','binding_owner') "
        "OR name GLOB 'binding_*' LIMIT 33").fetchall()
    if len(rows) > 32 or any(len(row[1]) > 128 or len(row[2]) > 128 or (row[3] or 0) > 24000 for row in rows):
        raise ValueError('Receipt authority schema exceeds its bounds')
    return {row[1]: (row[0], row[2], _sql_key(row[4])) for row in rows}


def _validate_schema(db):
    if _objects(db) != _SCHEMA:
        raise ValueError('Unsupported receipt authority schema')
    # Verify actual unique/primary index shape, including BINARY owner equality.
    expected = {
        'records': {'sqlite_autoindex_records_1': (1, 'u', ('id',)),
                    'record_session': (0, 'c', ('session', 'seq')),
                    'binding_records_owner': (0, 'c', ('workspace', 'session'))},
        'binding_owner': {'sqlite_autoindex_binding_owner_1': (1, 'pk', ('workspace', 'session'))},
        'binding_meta': {},
    }
    for table, indexes in expected.items():
        rows = db.execute(f'PRAGMA index_list({table})').fetchmany(9)
        if len(rows) != len(indexes):
            raise ValueError('Invalid receipt authority indexes')
        for row in rows:
            entry = indexes.get(row[1])
            if entry is None or (row[2], row[3], row[4]) != (entry[0], entry[1], 0):
                raise ValueError('Invalid receipt authority uniqueness')
            columns = db.execute(f'PRAGMA index_xinfo({row[1]})').fetchmany(10)
            keys = [(column[2], column[3], column[4]) for column in columns if column[5]]
            if keys != [(name, 0, 'BINARY') for name in entry[2]]:
                raise ValueError('Invalid receipt authority index columns')


def _epoch(db):
    rows = db.execute('SELECT singleton,schema,epoch FROM binding_meta LIMIT 2').fetchall()
    if (len(rows) != 1 or type(rows[0][0]) is not int or rows[0][0] != 1
            or type(rows[0][1]) is not int or rows[0][1] != 1
            or type(rows[0][2]) is not bytes or len(rows[0][2]) != 16):
        raise ValueError('Invalid receipt authority metadata')
    return rows[0][2].hex()


def _validate_owners(db):
    # Writer-side installation/maintenance audit. Legacy null/empty owners are
    # unbindable: fail closed, preserve rows, require explicit offline repair.
    if db.execute("SELECT 1 FROM records WHERE typeof(workspace)!='text' OR workspace='' "
                  "OR typeof(session)!='text' OR session='' LIMIT 1").fetchone():
        raise ValueError('Unbindable legacy receipt owner; explicit repair required')
    if db.execute("SELECT 1 FROM binding_owner WHERE typeof(workspace)!='text' OR workspace='' "
                  "OR typeof(session)!='text' OR session='' OR typeof(revision)!='integer' "
                  'OR revision<1 LIMIT 1').fetchone():
        raise ValueError('Invalid receipt authority owner')
    if db.execute('SELECT 1 FROM records r WHERE NOT EXISTS (SELECT 1 FROM binding_owner b '
                  'WHERE b.workspace=r.workspace AND b.session=r.session) LIMIT 1').fetchone():
        raise ValueError('Missing receipt authority owner')


@contextmanager
def _savepoint(db):
    if not db.in_transaction:
        raise ValueError('Receipt authority requires a caller transaction')
    db.execute('SAVEPOINT receipt_authority')
    try:
        yield
    except BaseException:
        db.execute('ROLLBACK TO receipt_authority')
        db.execute('RELEASE receipt_authority')
        raise
    else:
        db.execute('RELEASE receipt_authority')


def initialize(db):
    """Install once or validate, inside the caller transaction; never commit.

    Only wholly absent authority can be installed. Partial/corrupt/unknown-version
    metadata is not regenerated. There is no earlier authority version to migrate;
    a future supported migration must explicitly rotate epoch in its transaction.
    """
    with _savepoint(db):
        objects = _objects(db)
        if objects == _normalized(_BASE):
            for kind, _, sql in _AUTHORITY.values():
                if sql is not None:
                    db.execute(sql)
            db.execute('INSERT INTO binding_meta VALUES(1,1,?)', (os.urandom(16),))
            # SQL DISTINCT preserves exact owner keys. NULL/empty legacy owners
            # fail the audit below (and the entire savepoint), never get coerced.
            if db.execute("SELECT 1 FROM records WHERE typeof(workspace)!='text' OR workspace='' "
                          "OR typeof(session)!='text' OR session='' LIMIT 1").fetchone():
                raise ValueError('Unbindable legacy receipt owner; explicit repair required')
            db.execute('INSERT INTO binding_owner SELECT DISTINCT workspace,session,1 FROM records')
        _validate_schema(db)
        _epoch(db)
        _validate_owners(db)


def maintain(db, max_records, *, forget_sessions=(), forget_workspace=None):
    """Prune owner metadata only with an atomic epoch rotation; never commit.

    Call after records retention/deletion in the same transaction. Up to twice
    max_records owner rows (including tombstones) are kept between maintenance
    passes. If exceeded, remove only owners with no records. Live owners are never
    pruned. The records writer remains responsible for its own maxRecords bound.
    """
    if type(max_records) is not int or not 1 <= max_records <= 100000:
        raise ValueError('Invalid receipt authority retention bound')
    sessions = tuple(dict.fromkeys(forget_sessions))
    if any(type(value) is not str or not value for value in sessions):
        raise ValueError('Invalid receipt authority forget session')
    if forget_workspace is not None and (type(forget_workspace) is not str or not forget_workspace):
        raise ValueError('Invalid receipt authority forget workspace')
    with _savepoint(db):
        _validate_schema(db)
        _epoch(db)
        _validate_owners(db)
        selected = [('session', value) for value in sessions]
        if forget_workspace is not None:
            selected.append(('workspace', forget_workspace))
        forget = False
        for column, value in selected:
            if db.execute(f'SELECT 1 FROM records WHERE {column}=? LIMIT 1', (value,)).fetchone():
                raise ValueError('Cannot forget a live receipt authority owner')
            forget |= db.execute(f'SELECT 1 FROM binding_owner WHERE {column}=? LIMIT 1', (value,)).fetchone() is not None
        excess = db.execute('SELECT count(*) FROM (SELECT 1 FROM binding_owner LIMIT ?)',
                            (2 * max_records + 1,)).fetchone()[0] > 2 * max_records
        if forget or excess:
            db.execute('UPDATE binding_meta SET epoch=? WHERE singleton=1', (os.urandom(16),))
            for column, value in selected:
                db.execute(f'DELETE FROM binding_owner WHERE {column}=?', (value,))
            if excess:
                db.execute('DELETE FROM binding_owner WHERE NOT EXISTS (SELECT 1 FROM records '
                           'WHERE records.workspace=binding_owner.workspace AND records.session=binding_owner.session)')


def _owner(session):
    if not isinstance(session, dict):
        raise ValueError('Invalid receipt authority session')
    workspace = session.get('workspace')
    identity = session.get('nativeIdentity') or session.get('runtimeSessionId') or session.get('id')
    if type(workspace) is not str or not workspace or type(identity) is not str or not identity:
        raise ValueError('Invalid receipt authority owner')
    return workspace, identity


def _path(cache_home):
    if not isinstance(cache_home, (str, os.PathLike)):
        raise ValueError('Missing host receipt authority directory')
    home = Path(cache_home).resolve(strict=True)
    directory = home / 'diagnostics'
    if directory.resolve(strict=True) != directory:
        raise ValueError('Receipt authority directory is a symlink')
    return directory / 'events.sqlite3'


def _regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_ino == 0 or info.st_nlink == 0:
        raise ValueError('Receipt authority file is not a stable regular file')
    return info.st_dev, info.st_ino


def _physical(path, identity, descriptor):
    if path.parent.resolve(strict=True) != path.parent or _regular(path) != identity:
        raise ValueError('Receipt authority database changed')
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink == 0
            or (info.st_dev, info.st_ino) != identity):
        raise ValueError('Receipt authority database changed')
    # WAL/SHM are transient pager files, not authority stamps. A checkpoint may
    # truncate/remove them; each observed file must still be regular, not a link.
    # Fresh logical validation handles commits; no WAL byte/mtime equality claim.
    for suffix in ('-wal', '-shm'):
        try:
            _regular(path.with_name(path.name + suffix))
        except FileNotFoundError:
            pass


def _token(db, path, physical, session):
    workspace, identity = _owner(session)
    _validate_schema(db)
    epoch = _epoch(db)
    row = db.execute('SELECT revision FROM binding_owner WHERE workspace=? AND session=?',
                     (workspace, identity)).fetchone()
    if row is None:
        if db.execute('SELECT 1 FROM records WHERE workspace=? AND session=? LIMIT 1',
                      (workspace, identity)).fetchone():
            raise ValueError('Missing receipt authority owner')
        revision = 0
    else:
        revision = row[0]
        if type(revision) is not int or not 1 <= revision <= MAX_REVISION:
            raise ValueError('Invalid receipt authority owner revision')
    return {'kind': 'diagnostics-owner-v1',
            'database': {'path': str(path), 'device': physical[0], 'inode': physical[1]},
            'schema': _FINGERPRINT, 'epoch': epoch, 'workspace': workspace,
            'session': identity, 'revision': revision}


@contextmanager
def _snapshot(cache_home, session, guard, steps=None):
    guard()
    steps = [0] if steps is None else steps
    _owner(session)
    path = _path(cache_home)
    identity = _regular(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    db = None
    try:
        _physical(path, identity, descriptor)
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.05)
        def progress():
            steps[0] += 1000
            guard()
            return int(steps[0] >= 100000)
        db.set_progress_handler(progress, 1000)
        db.execute('PRAGMA query_only=ON')
        # Pager initialization can create an empty WAL. Discard its snapshot;
        # no rows from this read may be attested under the later authority token.
        db.execute('BEGIN')
        db.execute('SELECT 1 FROM records LIMIT 1').fetchone()
        db.rollback()
        guard()
        _physical(path, identity, descriptor)
        db.execute('BEGIN')
        token = _token(db, path, identity, session)
        guard()
        _physical(path, identity, descriptor)
        yield db, token
        guard()
        _physical(path, identity, descriptor)
    finally:
        try:
            if db is not None:
                try:
                    db.rollback()
                finally:
                    db.close()
            _physical(path, identity, descriptor)
        finally:
            os.close(descriptor)


def read_token(cache_home, session, guard=None):
    """One fresh read-only token transaction, with physical checks through close.

    The selected snapshot is this read's linearization point; a subsequent commit
    is detected on the next request. Errors propagate; there is no physical-stamp
    or missing-schema fallback, no write/migration and no automatic retry.
    """
    with _snapshot(cache_home, session, guard or (lambda: None)) as (_, token):
        return token


@contextmanager
def authority_snapshot(cache_home, session, guard=None):
    """Yield (read-only db, token) for same-snapshot bounded receipt queries.

    After teardown a separate, fresh connection validates logical authority. The
    caller must not publish yielded material until this context exits normally.
    It must also revalidate at its own acceptance/await boundaries. read_token uses
    the primitive snapshot, not this wrapper: post-close validation never recurses.
    """
    check = guard or (lambda: None)
    steps = [0]  # One 100k SQL-step allowance, including post-close validation.
    with _snapshot(cache_home, session, check, steps) as pair:
        yield pair
    with _snapshot(cache_home, session, check, steps) as (_, current):
        pass
    if current != pair[1]:
        raise ValueError('Receipt authority changed during snapshot read')
