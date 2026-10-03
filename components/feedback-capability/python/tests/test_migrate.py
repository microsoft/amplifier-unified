from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from filelock import Timeout
from amplifier_unified_feedback.migrate import import_page
from amplifier_unified_feedback.owner import Owner


async def forbidden(*args):
    raise AssertionError('No migration or historical read may execute work')


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source, self.destination = self.root / 'legacy.sqlite', self.root / 'owner'
        with closing(sqlite3.connect(self.source)) as db, db:
            for table in ('feedback_requests', 'feedback_followups'):
                db.execute(f'CREATE TABLE {table}(id TEXT PRIMARY KEY,fingerprint TEXT,payload TEXT,receipt TEXT)')

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, identity, status, *, table='feedback_requests', payload=None, **receipt):
        payload = payload if payload is not None else {'requestId': identity, 'title': 'Original title', 'body': 'Original body', 'category': 'bug'}
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute(f'INSERT INTO {table} VALUES(?,?,?,?)', (identity, 'legacy-hash', json.dumps(payload), json.dumps({'requestId': identity, 'status': status, **receipt})))

    async def test_history_is_byte_preserved_unknown_is_not_replayed_and_pages_restart(self):
        self.add('confirmed', 'submitted', url='https://github.com/microsoft/amplifier-unified/issues/1')
        self.add('in-flight', 'sending')
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        first = await import_page(self.source, self.destination, limit=1)
        second = await import_page(self.source, self.destination, after=first['nextCursor'], limit=1)
        self.assertEqual([r['classification'] for r in first['items'] + second['items']], ['imported', 'imported'])
        self.assertIsNone(second['nextCursor'])
        repeated = await import_page(self.source, self.destination)
        self.assertTrue(all(r['classification'] == 'already-imported' for r in repeated['items']))
        owner = Owner({'dataDir': str(self.destination)}, forbidden, forbidden, github=forbidden)
        try:
            self.assertEqual(owner.receipt('confirmed')['status'], 'completed')
            self.assertEqual(owner.receipt('in-flight')['status'], 'unknown')
            with self.assertRaisesRegex(ValueError, 'different feedback'):
                await owner.request('action', {'operation': 'feedback.submit', 'args': {'requestId': 'in-flight', 'title': 'Original title', 'body': 'Original body', 'category': 'bug'}})
            self.assertEqual(owner.db.execute('SELECT count(*) FROM legacy_imports').fetchone()[0], 2)
        finally:
            await owner.close()
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)

    async def test_correction_and_diagnostics_remain_immutable_historical_receipts(self):
        payload = {'requestId': 'original', 'title': 'First title', 'body': 'First body', '_diagnostics': {'ownerVersion': 'old'}}
        self.add('original', 'submitted', payload=payload)
        self.add('correction', 'completed', table='feedback_followups', action='feedback.update', feedbackId='original',
                 payload={'requestId': 'correction', 'feedbackId': 'original', 'title': 'Correction', 'body': 'Second body'})
        await import_page(self.source, self.destination)
        await import_page(self.source, self.destination, table='feedback_followups')
        owner = Owner({'dataDir': str(self.destination)}, forbidden, forbidden, github=forbidden)
        try:
            self.assertEqual(owner.receipt('correction')['operation'], 'feedback.update')
            self.assertEqual(owner.receipt('original')['diagnostics'], payload['_diagnostics'])
            original = json.loads(owner.db.execute('SELECT payload FROM commands WHERE id=?', ('original',)).fetchone()[0])
            self.assertEqual(original, payload)
            self.assertTrue(owner.receipt('correction')['migration']['historical'])
        finally:
            await owner.close()

    async def test_conflicts_invalid_and_oversize_records_preserve_both_sources(self):
        self.add('collision', 'submitted')
        await import_page(self.source, self.destination)
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE feedback_requests SET payload=? WHERE id='collision'", (json.dumps({'title': 'Changed', 'body': 'Changed'}),))
        self.add('invalid', 'unexpected')
        self.add('huge', 'sending', payload={'title': 'Huge', 'body': 'x' * 2_000_001})
        result = await import_page(self.source, self.destination)
        self.assertEqual([r['classification'] for r in result['items']], ['identity-conflict', 'invalid-source-retained', 'oversize-source-retained'])
        with closing(sqlite3.connect(self.destination / 'feedback.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM commands').fetchone()[0], 1)
            self.assertEqual(json.loads(db.execute('SELECT payload FROM commands').fetchone()[0])['title'], 'Original title')
            self.assertIsNone(db.execute("SELECT original FROM legacy_imports WHERE identity='huge'").fetchone()[0])
        self.assertTrue(self.source.exists())

    async def test_running_owner_is_never_modified(self):
        self.add('new', 'sending')
        owner = Owner({'dataDir': str(self.destination)}, forbidden, forbidden, github=forbidden)
        try:
            with self.assertRaises(Timeout):
                await import_page(self.source, self.destination)
            self.assertEqual(owner.db.execute('SELECT count(*) FROM commands').fetchone()[0], 0)
        finally:
            await owner.close()
