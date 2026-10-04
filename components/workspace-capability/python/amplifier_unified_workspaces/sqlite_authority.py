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

def inspect_authority(path, schema, *, workspace=False):
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
        if workspace:
            meta=dict(db.execute("SELECT key,substr(value,1,201) FROM meta WHERE key IN ('source','revision','configSequence','defaultRoot') LIMIT 5"))
            source=meta.get("source")
            if not isinstance(source,str) or not source.startswith("unified-workspaces:") or not 1<=len(source)<=200:
                raise refused()
            later_profile="defaultRoot" in meta or db.execute("SELECT 1 FROM sqlite_schema WHERE type='index' AND name='retention_workspace' LIMIT 1").fetchone() is not None
            for key in ("revision","configSequence"):
                value=meta.get(key)
                if key=="configSequence" and value is None and version==0 and not later_profile:
                    continue
                if not isinstance(value,str) or not 1<=len(value)<=16 or not value.isascii() or not value.isdecimal() or not 0<=int(value)<=9007199254740991:
                    raise refused()
        return True
    finally:
        db.close()

SCHEMA={
 "meta":{"key":("TEXT",0,1),"value":("TEXT",1,0)},
 "registrations":{"id":("TEXT",0,1),"path":("TEXT",1,0),"name":("TEXT",1,0),"hidden":("INTEGER",1,0),"revision":("INTEGER",1,0)},
 "plans":{"id":("TEXT",0,1),"payload":("TEXT",1,0),"command_id":("TEXT",0,0)},
 "commands":{"id":("TEXT",0,1),"operation":("TEXT",1,0),"payload":("TEXT",1,0),"status":("TEXT",1,0),"result":("TEXT",0,0),"updated":("REAL",1,0)},
}
