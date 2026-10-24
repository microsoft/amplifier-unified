"""Detached durable roots for background collection; never deletion authority.

SQLite stays on its owner thread. Expensive JSON decoding and overlay assembly
run in a worker; an exact fresh snapshot must still agree under writer exclusion
before any index or file can be removed. No body values survive the sweep.
"""
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class Snapshot:
    base: str | None
    records: tuple | None
    stores: tuple


def capture(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    row = db.execute('SELECT value FROM state WHERE id=1').fetchone() if 'state' in tables else None
    records = (tuple(db.execute('SELECT kind,id,value FROM state_records ORDER BY kind,id'))
               if row is not None and 'state_records' in tables else None)
    stores = tuple((table, tuple(db.execute('SELECT id,value FROM ' + table + ' ORDER BY id')))
                   for table in ('smart_tool_operations', 'client_views', 'conversation_shares', 'output_records')
                   if table in tables)
    return Snapshot(row[0] if row else None, records, stores)


def references(snapshot):
    from .resource_files import references as walk
    from .state_records import restore
    found = set()
    if snapshot.base is not None:
        state = restore(snapshot.base, snapshot.records)
        found.update(walk(state))
    for _, rows in snapshot.stores:
        for _, text in rows:
            found.update(walk(json.loads(text)))
    return found
