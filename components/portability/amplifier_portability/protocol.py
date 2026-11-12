"""Signed, fail-closed transfer journal. No network, credentials or execution.

The embedding host supplies checkpoint, native writer fencing and destination
validation. A ready receipt cannot authorize execution: only the source's
irreversible release certificate can. Lost acknowledgements never cause rollback.
"""
import base64
import copy
import json
import os
from pathlib import Path
import time
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from filelock import FileLock

from amplifier_worktrees.git import atomic, digest


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def durable(path, value):
    durable_directory(path.parent)
    atomic(path, value)
    # A release certificate must never outrun its durable source tombstone.
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_directory(path):
    """Confirm every ancestor, including a retry after an interrupted mkdir."""
    path = Path(path).absolute()
    chain = [path, *path.parents]
    if len(chain) > 128:
        raise ValueError('Transfer storage exceeds the directory depth limit')
    for directory in reversed(chain):
        if directory.is_symlink():
            raise ValueError('Transfer storage cannot contain symbolic links')
        directory.mkdir(exist_ok=True, mode=0o700)
    # Sync after all mkdir calls, so each parent confirms its new child entry.
    for directory in reversed(chain):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def confirm_durable(path):
    # A previous replace may have succeeded before its fsync failed. An exact
    # retry cannot treat the visible record as a durable acknowledgement yet.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    durable_directory(path.parent)


class TransferNode:
    """One durable local identity; peer keys are operator-configured trust roots."""
    def __init__(self, directory, label):
        self.directory = Path(directory).resolve()
        durable_directory(self.directory)
        self.lock = FileLock(str(self.directory / '.lock'), timeout=30)
        with self.lock:
            key = self.directory / 'identity.json'
            if key.exists():
                if key.is_symlink() or key.stat().st_mode & 0o077:
                    raise ValueError('The host identity must be a private local file')
                value = json.loads(key.read_text())
                confirm_durable(key)
                self.key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(value['privateKey'], validate=True))
            else:
                self.key = Ed25519PrivateKey.generate()
                durable(key, {'privateKey': base64.b64encode(self.key.private_bytes_raw()).decode()})
        public = self.key.public_key().public_bytes_raw()
        self.identity = {'id': digest(public), 'label': label, 'publicKey': base64.b64encode(public).decode()}

    def peers(self):
        path = self.directory / 'peers.json'
        if not path.exists():
            return {}
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError('Paired host keys must be an operator-owned private file')
        peers = json.loads(path.read_text())
        for identity, peer in peers.items():
            if digest(base64.b64decode(peer['publicKey'], validate=True)) != identity:
                raise ValueError('Paired host fingerprint does not match its public key')
        return peers

    def sign(self, body):
        return {'body': copy.deepcopy(body), 'signer': self.identity['id'],
                'signature': base64.b64encode(self.key.sign(encoded(body))).decode()}

    def verify(self, envelope, *, kind, signer=None):
        try:
            identity = envelope['signer']
            peer = self.peers().get(identity)
            if not peer or (signer and signer != identity):
                raise ValueError('Transfer requires an explicitly paired host')
            Ed25519PublicKey.from_public_bytes(base64.b64decode(peer['publicKey'], validate=True)).verify(
                base64.b64decode(envelope['signature'], validate=True), encoded(envelope['body']))
            body = envelope['body']
            if not isinstance(body, dict) or body.get('kind') != kind or type(body.get('version')) is not int or body['version'] != 1:
                raise ValueError('Unsupported transfer receipt')
            return copy.deepcopy(body)
        except (KeyError, TypeError, InvalidSignature) as exc:
            raise ValueError('Transfer receipt authentication failed') from exc

    def path(self, identity):
        try:
            if str(uuid.UUID(identity)) != identity:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError('Invalid transfer identity') from None
        return self.directory / 'receipts' / (identity + '.json')

    def get(self, identity):
        path = self.path(identity)
        row = json.loads(path.read_text())
        confirm_durable(path)
        return row

    def records(self, sid=None):
        rows = [json.loads(path.read_text()) for path in sorted((self.directory / 'receipts').glob('*.json'))]
        return [row for row in rows if sid is None or row['sessionId'] == sid]

    def save(self, row):
        row['updatedAt'] = time.time()
        durable(self.path(row['id']), row)
        return copy.deepcopy(row)

    def fenced(self, sid):
        rows = self.records(sid)
        if not rows:
            return False
        # Generations serialize round trips; a later local activation supersedes
        # an older outgoing tombstone but never a newer unfinished transfer.
        latest = max(rows, key=lambda row: (row['generation'], row['createdAt']))
        return latest['phase'] not in {'active', 'cancelled'}

    def begin(self, sid, destination, command_id, request):
        with self.lock:
            if destination == self.identity['id'] or destination not in self.peers():
                raise ValueError('Choose a different explicitly paired destination host')
            identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'task-transfer:' + self.identity['id'] + ':' + command_id))
            signature = digest([sid, destination, request])
            if self.path(identity).exists():
                row = self.get(identity)
                if row['requestHash'] != signature:
                    raise ValueError('This transfer command already has different contents')
                return {**row, 'duplicate': True}
            previous = self.records(sid)
            if previous and self.fenced(sid):
                raise ValueError('This task has an unresolved or released transfer; inspect its receipt')
            generation = max((row['generation'] for row in previous), default=0) + 1
            row = {'id': identity, 'sessionId': sid, 'generation': generation, 'source': self.identity['id'],
                   'destination': destination, 'direction': 'outgoing', 'phase': 'preparing', 'revision': 1,
                   'requestHash': signature, 'createdAt': time.time(), 'inputsReplayed': False}
            return self.save(row)

    def prepared(self, identity, payload):
        from .capsule import validate_public, write_capsule
        with self.lock:
            row = self.get(identity)
            if row['phase'] != 'preparing':
                raise ValueError('Export was already attempted; inspect its receipt')
            validate_public(payload)
            body = {key: row[key] for key in ('id', 'sessionId', 'generation', 'source', 'destination')}
            body.update(kind='capsule', version=1, payload=payload)
            envelope = self.sign(body)
            path = self.directory / 'packages' / (identity + '.json')
            write_capsule(path, envelope)
            row.update(phase='prepared', revision=row['revision'] + 1, capsuleHash=digest(encoded(body)), package=str(path))
            return self.save(row)

    def receive(self, envelope, request, *, readiness_policy=None):
        with self.lock:
            body = self.verify(envelope, kind='capsule')
            if type(body.get('generation')) is not int or body['generation'] < 1:
                raise ValueError('Invalid transfer generation')
            if body['source'] != envelope['signer'] or body['destination'] != self.identity['id']:
                raise ValueError('This package belongs to different execution hosts')
            identity = body['id']
            if self.path(identity).exists():
                row = self.get(identity)
                if row.get('capsuleHash') != digest(encoded(body)) or row.get('requestHash') != digest(request):
                    raise ValueError('This transfer was already staged with different contents or destination')
                return {**row, 'duplicate': True}
            previous = self.records(body['sessionId'])
            if previous:
                latest = max(previous, key=lambda row: (row['generation'], row['createdAt']))
                allowed = (latest['phase'] == 'released' and latest['destination'] == body['source']) or (latest['phase'] == 'discarded' and latest['source'] == body['source'])
                if not allowed or body['generation'] <= latest['generation']:
                    raise ValueError('Destination already owns this task or has an unresolved transfer')
            elif body['generation'] != 1:
                # A third host can receive generation >1. Its trusted sender is
                # the current owner; absence is still checked by the app adapter.
                if type(body['generation']) is not int or body['generation'] < 1:
                    raise ValueError('Invalid transfer generation')
            row = {key: body[key] for key in ('id', 'sessionId', 'generation', 'source', 'destination')}
            row.update(direction='incoming', phase='staging', revision=1, createdAt=time.time(),
                       capsuleHash=digest(encoded(body)), requestHash=digest(request), inputsReplayed=False)
            if readiness_policy is not None:
                row.update(readinessPolicy=copy.deepcopy(readiness_policy), readinessPolicyHash=digest(readiness_policy))
            return self.save(row)

    def readiness_checks(self, row):
        """Authenticate this host's original checks before a new activation effect."""
        try:
            receipt = row['readyReceipt']
            body = receipt['body']
            if (receipt['signer'] != self.identity['id'] or body.get('kind') != 'ready'
                    or type(body.get('version')) is not int or body['version'] != 1):
                raise ValueError('Unsupported destination readiness receipt')
            self.key.public_key().verify(base64.b64decode(receipt['signature'], validate=True), encoded(body))
            self.match(row, body)
            if body['checksHash'] != digest(row['checks']):
                raise ValueError('Stored destination checks differ from signed readiness')
            return copy.deepcopy(row['checks'])
        except (KeyError, TypeError, InvalidSignature) as exc:
            raise ValueError('Destination readiness authentication failed') from exc

    def ready(self, identity, destination, checks):
        with self.lock:
            row = self.get(identity)
            if row['phase'] != 'staging':
                raise ValueError('Import was already attempted; inspect its receipt')
            if not all(checks.get(key) is True for key in ('runtimeVerified', 'accountVerified', 'nativeFenceVerified')) or checks.get('credentialsOrigin') != 'destination':
                raise ValueError('Destination runtime, account and native ownership fence must be verified locally')
            row.update(phase='ready', revision=row['revision'] + 1, destinationState=copy.deepcopy(destination), checks=copy.deepcopy(checks))
            body = {key: row[key] for key in ('id', 'sessionId', 'generation', 'source', 'destination', 'capsuleHash')}
            body.update(kind='ready', version=1, checksHash=digest(checks))
            row['readyReceipt'] = self.sign(body)
            return self.save(row)

    @staticmethod
    def match(row, body):
        if any(row[key] != body.get(key) for key in ('id', 'sessionId', 'generation', 'source', 'destination', 'capsuleHash')):
            raise ValueError('Receipt does not match this exact staged transfer')

    def release(self, identity, ready, expected_revision):
        with self.lock:
            row = self.get(identity)
            body = self.verify(ready, kind='ready', signer=row['destination'])
            self.match(row, body)
            if row['phase'] == 'released':
                if row['readyHash'] != digest(ready):
                    raise ValueError('This task was released against another readiness receipt')
                return {**row, 'duplicate': True}
            if row['phase'] not in {'prepared', 'releasing'} or row['revision'] != expected_revision:
                raise ValueError('Inspect the current prepared transfer revision before releasing')
            certificate = {key: row[key] for key in ('id', 'sessionId', 'generation', 'source', 'destination', 'capsuleHash')}
            certificate.update(kind='release', version=1, readyHash=digest(ready))
            # This save is the irreversible ownership boundary. No API rolls it
            # back if certificate transport/activation acknowledgement is lost.
            row.update(phase='released', revision=row['revision'] + 1, readyHash=digest(ready), releaseCertificate=self.sign(certificate))
            return self.save(row)

    def releasing(self, identity, ready, expected_revision, archive):
        with self.lock:
            row = self.get(identity)
            body = self.verify(ready, kind='ready', signer=row['destination'])
            self.match(row, body)
            if row['revision'] != expected_revision or not (row['phase'] == 'prepared' or
                    row['phase'] == 'releasing' or (row['phase'] == 'unknown' and row.get('previousPhase') == 'releasing')):
                raise ValueError('Inspect the current source release receipt before continuing')
            if row.get('readyReceipt') and row['readyReceipt'] != ready:
                raise ValueError('Source release was attempted against another destination receipt')
            row.update(phase='releasing', revision=row['revision'] + 1, readyReceipt=copy.deepcopy(ready), archive=str(archive))
            return self.save(row)

    def activating(self, identity, certificate, expected_revision):
        with self.lock:
            row = self.get(identity)
            body = self.verify(certificate, kind='release', signer=row['source'])
            self.match(row, body)
            if body['readyHash'] != digest(row['readyReceipt']):
                raise ValueError('Source release does not acknowledge this destination readiness')
            if row['phase'] == 'active':
                return {**row, 'duplicate': True}
            if row['phase'] != 'ready' or row['revision'] != expected_revision:
                raise ValueError('Inspect the current ready transfer revision before activation')
            self.readiness_checks(row)
            row.update(phase='activating', revision=row['revision'] + 1, releaseCertificate=copy.deepcopy(certificate))
            return self.save(row)

    def active(self, identity):
        with self.lock:
            row = self.get(identity)
            if row['phase'] != 'activating':
                raise ValueError('Activation must hold the matching source release')
            row.update(phase='active', revision=row['revision'] + 1)
            return self.save(row)

    def cancel(self, identity, expected_revision, evidence):
        with self.lock:
            row = self.get(identity)
            if row['direction'] != 'outgoing':
                raise ValueError('Only the source can cancel an unreleased transfer')
            if row['phase'] == 'cancelled':
                return {**row, 'duplicate': True}
            if row['revision'] != expected_revision or not (row['phase'] == 'prepared' or
                    (row['phase'] == 'unknown' and row.get('previousPhase') == 'preparing')):
                raise ValueError('Only a confirmed unreleased transfer can be cancelled; inspect both hosts')
            if not isinstance(evidence, str) or not evidence.strip():
                raise ValueError('Record the reason for cancelling the transfer')
            body = {key: row.get(key) for key in ('id', 'sessionId', 'generation', 'source', 'destination', 'capsuleHash')}
            body.update(kind='cancel', version=1)
            # Cancellation and release are mutually exclusive durable choices.
            row.update(phase='cancelled', revision=row['revision'] + 1, evidence=evidence,
                       cancelCertificate=self.sign(body))
            return self.save(row)

    def discard(self, identity, certificate, expected_revision):
        with self.lock:
            row = self.get(identity)
            body = self.verify(certificate, kind='cancel', signer=row['source'])
            self.match(row, body)
            if row['phase'] == 'discarded':
                return {**row, 'duplicate': True}
            if row['direction'] != 'incoming' or row.get('releaseCertificate') or row['revision'] != expected_revision or row['phase'] not in {'ready', 'unknown'}:
                raise ValueError('Only an unactivated destination can retain a source cancellation')
            row.update(phase='discarded', revision=row['revision'] + 1, cancelCertificate=copy.deepcopy(certificate))
            return self.save(row)

    def unknown(self, identity):
        with self.lock:
            row = self.get(identity)
            # Never weaken a committed source tombstone, even on a UI error.
            if row['phase'] not in {'released', 'active', 'cancelled', 'discarded', 'unknown'}:
                row.update(previousPhase=row['phase'], phase='unknown', revision=row['revision'] + 1,
                           detail='The transfer did not reach a confirmed boundary. Inspect both hosts; no work was replayed.')
                self.save(row)
            return row

    def recover(self):
        for row in self.records():
            if row['phase'] in {'preparing', 'staging', 'activating', 'releasing'}:
                self.unknown(row['id'])
