"""Trusted fixture operator, after native restore and before app activation."""
import json
import sys

from amplifier_session_catalog import Catalog
from amplifier_session_catalog.restore import rebind_restored_sources

catalog = Catalog(sys.argv[1])
source, destination, digest = sys.argv[2:]
prefix = source + '/projects/'
after = ''
count = batch = 0
while True:
    with catalog.connect() as db:
        rows = db.execute('SELECT uri,storage_path FROM sessions WHERE uri>? '
                          'AND substr(storage_path,1,?)=? ORDER BY uri LIMIT 100',
                          (after, len(prefix), prefix)).fetchall()
    if not rows:
        break
    records = [{'uri': row['uri'], 'token': catalog._source_snapshot(row['storage_path'])['token']}
               for row in rows]
    result = rebind_restored_sources(catalog, command_id=f'fixture-restore-{batch}', restore_digest=digest,
                                    source_home=source, destination_home=destination, records=records)
    count += result['rebound']
    batch += 1
    after = rows[-1]['uri']
print(json.dumps({'rebound': count, 'batches': batch, 'nativeFilesModified': False}))
