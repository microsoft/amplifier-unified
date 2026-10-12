"""Produce saved outputs with Main's actual registry and resource serializer."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

legacy, destination = sys.argv[1:]
sys.path.insert(0, legacy)
from amplifier_outputs import OutputRegistry
from amplifier_web.resource_files import put

root = Path(destination)
seed = json.loads((root / 'legacy-seed.json').read_text())
sid = seed['nativeId']
rows = seed['originalRows']
message = hashlib.sha256(json.dumps(
    [sid, 1, 'assistant', rows[1]['content']], ensure_ascii=False).encode()).hexdigest()[:32]
database = root / 'legacy-outputs.sqlite3'
assert not database.exists()
db = sqlite3.connect(database)
db.execute('CREATE TABLE state_resources(id TEXT PRIMARY KEY,value TEXT)')
db.execute('CREATE TABLE state_records(kind TEXT,id TEXT,value TEXT,PRIMARY KEY(kind,id))')
db.execute('INSERT INTO state_records VALUES(?,?,?)', ('session', sid,
    json.dumps({'id': sid, 'messages': [{'id': 'original-display-message', 'nativeMessageId': message}]})))
registry = OutputRegistry(db)
records = []
for content in ['# Original café report\n\nViolet compass.', '# Revised café report\n\nCopper totals.']:
    data = content.encode()
    record = registry.create(sid, {'kind': 'writing', 'title': 'Retained report',
        'variant': 'document', 'filename': 'report.md', 'mimeType': 'text/markdown',
        'origin': {'source': 'ui', 'messageId': 'original-display-message'},
        'parentId': records[-1]['id'] if records else None,
        'evidenceIds': [records[0]['id']] if records else [],
        'body': put(db, {'encoding': 'utf-8', 'data': content}),
        'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
    registry.comment(record['id'], {'body': 'Preserve this review note', 'source': 'ui', 'externalPosted': False})
    records.append(record)
db.commit()
db.close()
receipt = {'database': str(database), 'sourceSession': sid,
    'sourceSha256': hashlib.sha256(database.read_bytes()).hexdigest(),
    'records': records, 'legacyMessageId': message,
    'serializer': 'Main OutputRegistry and resource_files.put'}
(root / 'legacy-outputs.json').write_text(json.dumps(receipt))
print(json.dumps({'outputs': len(records), 'sourceSha256': receipt['sourceSha256']}))
