"""Synthetic controls for the private snapshot helper; run only in owned DTU."""
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest

import scale_replica_capture as capture
from scale_replica_extract import extract


class CaptureTests(unittest.TestCase):
    def test_backup_filters_private_bytes_and_does_not_change_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'app.sqlite3'
            db = sqlite3.connect(path)
            db.executescript('CREATE TABLE client_views(id,value); CREATE TABLE state(id,value);'
                             'CREATE TABLE state_records(kind,id,value); CREATE TABLE shell_records(kind,id,value);')
            db.execute('INSERT INTO client_views VALUES(?,?)', ('private', 'PRIVATE-DRAFT-SENTINEL'))
            db.execute('INSERT INTO state VALUES(1,?)', (json.dumps({'sessions': [], 'view': {'draft': 'PRIVATE-DRAFT-SENTINEL'},
                'settings': {'model': 'synthetic', 'api_key': 'SECRET-SENTINEL',
                             'token': 'TOKEN-SENTINEL', 'secret': 'GENERIC-SECRET-SENTINEL'}}),))
            db.execute('INSERT INTO state_records VALUES(?,?,?)', ('global', 'view', '{}'))
            db.commit(); db.close()
            before = path.read_bytes()
            payload = capture.database(path)
            self.assertEqual(path.read_bytes(), before)
            self.assertNotIn(b'PRIVATE-DRAFT-SENTINEL', payload)
            self.assertNotIn(b'SECRET-SENTINEL', payload)
            self.assertNotIn(b'TOKEN-SENTINEL', payload)
            copy = sqlite3.connect(':memory:'); copy.deserialize(payload)
            self.assertEqual(copy.execute('SELECT count(*) FROM client_views').fetchone()[0], 0)
            self.assertEqual(json.loads(copy.execute('SELECT value FROM state').fetchone()[0])['settings']['model'], 'synthetic')
            copy.close()

    def test_stream_receipts_exact_bytes_and_canonical_backup_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, native = Path(tmp)/'app', Path(tmp)/'native'
            app.mkdir(); folder = native/'projects'/'synthetic'/'sessions'/'one'; folder.mkdir(parents=True)
            files = {'transcript.jsonl': b'{"role":"user","content":"synthetic"}\n',
                     'transcript.jsonl.backup': b'{"role":"user","content":"previous"}\n',
                     'transcript.jsonl.bak': b'NOT-CANONICAL', 'keys.env': b'NOT-ALLOWED'}
            for name, data in files.items(): (folder/name).write_bytes(data)
            result = subprocess.run([sys.executable, capture.__file__, '--app', str(app), '--native', str(native), '--phase', 'primary'], capture_output=True, check=True)
            verified = extract(Path(tmp)/'seed', io.BytesIO(result.stdout))
            self.assertTrue(verified['allHashesMatch'])
            self.assertEqual(verified['files'], 2)
            with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
                names = archive.getnames()
                self.assertEqual(len(names), 3)
                self.assertFalse(any(n.endswith(('keys.env','.bak')) for n in names))
                receipt = json.load(archive.extractfile('capture/primary-0.json'))
                for row in receipt['files']:
                    data = archive.extractfile(row['path']).read()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), row['sha256'])
                    self.assertTrue(row['stable'])

    def test_reading_profile_admits_shell_navigation_but_not_wrapped_mutations(self):
        from scale_replica_app import reading_action
        self.assertTrue(reading_action('session.select', {'id': 'synthetic'}))
        self.assertTrue(reading_action('shell.command', {'action': 'session.select', 'args': {'id': 'synthetic'}}))
        for action in ('conversation.send', 'session.create', 'schedule.create', 'updates.install', 'shell.command'):
            self.assertFalse(reading_action('shell.command', {'action': action, 'args': {}}))
        self.assertFalse(reading_action('conversation.send', {}))
        self.assertFalse(reading_action('shell.command', None))

    def test_extractor_rejects_path_escape(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode='w') as archive:
            row = tarfile.TarInfo('../outside'); row.size = 1
            archive.addfile(row, io.BytesIO(b'x'))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): extract(Path(tmp)/'seed', io.BytesIO(payload.getvalue()))

    @unittest.skipUnless(os.geteuid() == 0, 'Owned DTU root qualification')
    def test_extractor_drops_to_seed_owner_before_writing(self):
        owner = pwd.getpwnam('unified-dev')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, native = root/'app', root/'native'
            app.mkdir(); folder = native/'projects/synthetic/sessions/one'
            folder.mkdir(parents=True)
            (folder/'transcript.jsonl').write_text('{"role":"user","content":"synthetic"}\n')
            captured = subprocess.run([sys.executable, capture.__file__, '--app', str(app),
                '--native', str(native), '--phase', 'primary'], capture_output=True, check=True)
            seed = root/'seed'; seed.mkdir(mode=0o700)
            os.chown(root, owner.pw_uid, owner.pw_gid)
            os.chown(seed, owner.pw_uid, owner.pw_gid)
            extractor = Path(capture.__file__).with_name('scale_replica_extract.py')
            result = subprocess.run([sys.executable, str(extractor), '--root', str(seed)],
                input=captured.stdout, capture_output=True, check=True)
            self.assertTrue(json.loads(result.stdout)['allHashesMatch'])
            self.assertEqual((seed/'native/projects/synthetic/sessions/one/transcript.jsonl').stat().st_uid,
                             owner.pw_uid)


if __name__ == '__main__': unittest.main()
