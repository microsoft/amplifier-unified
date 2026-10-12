"""Verify a previously saved memory without minting current human admission.

Only the offline import's retained database and exact identity translations can
establish this historical relationship. Native role labels alone cannot do so.
This verifier is deliberately unavailable to authorization and consolidation.
"""
import json
import sqlite3
from pathlib import Path

from amplifier_recall.store import digest

from .evidence import is_typed_text, verify


class RetainedEvidence:
    def __init__(self, directory):
        self.db = None
        self.evidence = None
        self.mapping = None
        self.error = None
        directory = Path(directory)
        if not (directory / 'migration.json').exists():
            return
        try:
            from .migrate import MAX_SNAPSHOT, encode, mappings, sha256
            from .migration_evidence import Evidence
            paths = [directory / name for name in ('migration.json', 'identity-mapping.json', 'legacy-recall.sqlite3')]
            for path, maximum in zip(paths, (65536, 32 * 1024 * 1024, MAX_SNAPSHOT)):
                if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
                    raise ValueError('Retained memory evidence is missing or oversized')
            receipt = json.loads(paths[0].read_text())
            if receipt.get('schema') != 'amplifier-legacy-memory-migration-v1':
                return
            identity = json.loads(paths[1].read_text())
            import hashlib
            checksum = lambda value: hashlib.sha256(encode(value).encode()).hexdigest()
            if (sha256(paths[2]) != receipt.get('sourceSha256')
                    or checksum(identity['mapping']) != receipt.get('mappingSha256')
                    or checksum(identity['evidence']) != receipt.get('evidenceSha256')
                    or any(Path(str(paths[2]) + suffix).exists() for suffix in ('-wal', '-shm', '-journal'))):
                raise ValueError('Retained memory evidence changed after import')
            self.mapping = mappings(identity['mapping'])
            self.evidence = Evidence(identity['evidence'], self.mapping)
            self.db = sqlite3.connect(paths[2].resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
            if self.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Retained memory evidence failed integrity check')
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
            self.close()
            self.error = str(error)

    def eligible(self, note):
        if self.db is None:
            return False
        original = self.db.execute('SELECT value FROM memories WHERE id=?', (note['id'],)).fetchone()
        if original is None:
            return False
        from .migrate import note as translate
        original = json.loads(original[0])
        retained = translate(
            json.dumps(original), original['id'], original['revision'], self.mapping, self.evidence)
        source = retained.get('source') or {}
        return (source.get('kind') == 'attributed-user' and source == note.get('source')
                and retained['scope'] == note['scope'] and retained['target'] == note['target'])

    def verifies(self, note, row):
        if not self.eligible(note):
            return False
        source = note['source']
        if (row.get('id') != source.get('messageId') or row.get('role') != 'user'
                or row.get('provenance') != {'source': 'host-projection', 'complete': True}
                or row.get('inputOrigin') not in {'user', 'ui', 'voice', 'unknown'}
                or any(row.get(key) for key in ('scheduledRunId', 'questionId'))):
            return False
        history = row.get('_meta', {}).get('amplifier.dev/history', {})
        marker = history.get('nativeInput', {})
        if history.get('historicalOnly'):
            preserved = history.get('preservedSource')
            if (history.get('mutationAuthority') != 'none' or not isinstance(preserved, dict)
                    or set(preserved) != {'kind', 'archiveId', 'sha256', 'complete'}
                    or preserved['kind'] != 'context-clear' or preserved['complete'] is not True
                    or any(not isinstance(preserved[key], str) or len(preserved[key]) != 64
                           or any(char not in '0123456789abcdef' for char in preserved[key])
                           for key in ('archiveId', 'sha256'))):
                return False
        # This exception verifies only a memory already bound to the immutable
        # migration evidence above. It never makes archived text a human input.
        if (history.get('authorization') != 'unverified-native-history'
                or marker.get('version') != 1 or marker.get('kind') != 'user'
                or marker.get('id') != source['messageId'] or marker.get('source', 'user') != 'user'
                or any(history.get(key) for key in ('peerEnvelope', 'feedbackEvent', 'questionId', 'scheduledRunId', 'recordedOnly'))):
            return False
        text = row.get('text')
        return (isinstance(text, str) and is_typed_text(text)
                and digest(text) == source.get('sha256') and verify(source.get('quote', ''), [text]))

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
