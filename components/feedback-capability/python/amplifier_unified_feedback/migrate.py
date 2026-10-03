"""Explicit offline receipt import. Never sends, resumes, or repairs remote work."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3

from .owner import Owner, encode

TABLES = ('feedback_requests', 'feedback_followups')
MAX_RECORD = 2_000_000


def converted(table, identity, payload, receipt, source_id, rowid, digest):
    if not isinstance(payload, dict) or not isinstance(receipt, dict):
        raise ValueError('invalid-record')
    if not isinstance(identity, str) or not 1 <= len(identity) <= 200 or receipt.get('requestId') != identity:
        raise ValueError('identity-mismatch')
    operation = 'feedback.submit' if table == TABLES[0] else receipt.get('action')
    if operation not in {'feedback.submit', 'feedback.get', 'feedback.comment', 'feedback.update',
                         'feedback.close', 'feedback.reopen', 'feedback.reconcile'}:
        raise ValueError('unsupported-operation')
    if operation in {'feedback.submit', 'feedback.update'} and any(not isinstance(payload.get(key), str) for key in ('title', 'body')):
        raise ValueError('invalid-original-text')
    status = receipt.get('status')
    if status in {'queued', 'sending', 'dispatching'}:
        status = 'unknown'
    elif status == 'submitted':
        status = 'completed'
    if status not in {'completed', 'unknown', 'failed'}:
        raise ValueError('unsupported-status')
    result = {**receipt, 'operation': operation, 'status': status,
              'migration': {'source': source_id, 'table': table, 'rowid': rowid,
                            'sha256': digest, 'historical': True, 'replayed': False}}
    if operation == 'feedback.submit' and '_diagnostics' in payload:
        result['diagnostics'] = payload['_diagnostics']
    if status == 'unknown':
        result['message'] = 'Legacy delivery is unresolved. Original evidence retained; nothing was resent.'
    if len(encode(result).encode()) > MAX_RECORD:
        raise ValueError('oversize-converted-receipt')
    return operation, status, result


async def import_page(source, destination, *, table='feedback_requests', after=0, limit=50):
    """Read one keyset page, retain exact source rows and refuse all overwrites."""
    if table not in TABLES or type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Choose a supported table, nonnegative row cursor and limit1..100')
    source, destination = Path(source).resolve(strict=True), Path(destination).resolve()
    if source == destination / 'feedback.sqlite3':
        raise ValueError('Legacy source must differ from the independent destination')
    source_id = hashlib.sha256(str(source).encode()).hexdigest()
    async def forbidden(*args):
        raise AssertionError('Migration cannot invoke hosts or remote services')
    owner = Owner({'dataDir': str(destination)}, forbidden, forbidden, github=forbidden)
    legacy = None
    try:
        if owner.intake.fence: raise ValueError('Destination feedback owner has a retained quiescence fence')
        legacy = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
        legacy.row_factory = sqlite3.Row
        legacy.execute('PRAGMA query_only=ON')
        legacy.execute('BEGIN')
        found = legacy.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone()
        if not found:
            return {'source': source_id, 'table': table, 'items': [], 'nextCursor': None,
                    'sourceRetained': True, 'remoteEffects': 0, 'tableAbsent': True}
        owner.db.execute('''CREATE TABLE IF NOT EXISTS legacy_imports(
            source TEXT, source_table TEXT, source_row INTEGER, digest TEXT,
            identity TEXT, classification TEXT, original TEXT,
            PRIMARY KEY(source,source_table,source_row,digest))''')
        result = []
        # Metadata first: oversized bodies are never transferred into Python.
        rows = legacy.execute(f'''SELECT rowid,id,
            length(CAST(payload AS BLOB))+length(CAST(receipt AS BLOB))+
            length(CAST(id AS BLOB))+length(CAST(fingerprint AS BLOB)) AS bytes
            FROM {table} WHERE rowid>? ORDER BY rowid LIMIT ?''', (after, limit + 1)).fetchall()
        for meta in rows[:limit]:
            identity, rowid = meta['id'], meta['rowid']
            raw = None
            digest = ''
            classification = 'oversize-source-retained' if type(meta['bytes']) is int else 'invalid-source-retained'
            if type(meta['bytes']) is int and meta['bytes'] <= MAX_RECORD:
                record = dict(legacy.execute(f'SELECT id,fingerprint,payload,receipt FROM {table} WHERE rowid=?', (rowid,)).fetchone())
                try:
                    raw = encode(record)
                    digest = hashlib.sha256(raw.encode()).hexdigest()
                    operation, status, receipt = converted(table, identity, json.loads(record['payload']),
                        json.loads(record['receipt']), source_id, rowid, digest)
                    fingerprint = 'legacy-import:' + digest
                    prior = owner.db.execute('SELECT fingerprint FROM commands WHERE id=?', (identity,)).fetchone()
                    classification = 'already-imported' if prior and prior[0] == fingerprint else 'identity-conflict' if prior else 'imported'
                    owner.db.execute('BEGIN IMMEDIATE')
                    if not prior:
                        owner.db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)',
                            (identity, fingerprint, operation, record['payload'], status, encode(receipt)))
                except (ValueError, TypeError, KeyError, UnicodeError):
                    classification = 'invalid-source-retained'
            if not owner.db.in_transaction:
                owner.db.execute('BEGIN IMMEDIATE')
            try:
                owner.db.execute('INSERT OR IGNORE INTO legacy_imports VALUES(?,?,?,?,?,?,?)',
                    (source_id, table, rowid, digest, identity, classification, raw))
                owner.db.execute('COMMIT')
            except BaseException:
                owner.db.execute('ROLLBACK')
                raise
            result.append({'row': rowid, 'requestId': identity, 'classification': classification,
                           'sha256': digest or None})
        return {'source': source_id, 'table': table, 'items': result,
                'nextCursor': rows[limit - 1]['rowid'] if len(rows) > limit else None,
                'sourceRetained': True, 'remoteEffects': 0}
    finally:
        if legacy:
            legacy.close()
        await owner.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--table', choices=TABLES, default=TABLES[0])
    parser.add_argument('--after', type=int, default=0)
    parser.add_argument('--limit', type=int, default=50)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(import_page(args.source, args.destination,
        table=args.table, after=args.after, limit=args.limit)), indent=2))


if __name__ == '__main__':
    main()
