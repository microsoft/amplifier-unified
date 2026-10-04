"""Bounded authority inspection under the caller's existing owner lease."""
import sqlite3
from pathlib import Path

def refused():
    return ValueError("Existing owner authority schema is unavailable; original database and sidecars retained")

def regular(path):
    try:
        mode=path.lstat().st_mode
    except FileNotFoundError:
        return False
    import stat
    if not stat.S_ISREG(mode):
        raise refused()
    return True

def inspect_authority(path, schema):
    path=Path(path)
    exists=regular(path)
    sidecars=[regular(Path(str(path)+suffix)) for suffix in ("-wal","-shm","-journal")]
    if not exists:
        if any(sidecars):
            raise refused()
        return False
    db=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)
    try:
        version=db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0,1):
            raise refused()
        for table, fields in schema.items():
            if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?",(table,)).fetchone():
                raise refused()
            columns={row[1]:(row[2].upper(),row[3],row[5]) for row in db.execute("PRAGMA table_info("+table+")")}
            if any(columns.get(name)!=definition for name,definition in fields.items()):
                raise refused()
        return True
    finally:
        db.close()

SCHEMA={"commands":{"id":("TEXT",0,1),"signature":("TEXT",0,0),"body":("TEXT",0,0)}}
