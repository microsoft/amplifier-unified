"""Private distribution-admission journal; never feedback history or replay authority."""
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

TABLE = 'feedback_aggregate_admissions'
PROFILE = 'feedback-aggregate-admission-profile-v1'
MARKER = {'kind': PROFILE, 'version': 1}
CONTEXT = {'commandId', 'fenceId', 'instanceId', 'dataScope', 'purpose'}
IDENTITY = CONTEXT - {'purpose'}
RECEIPT = IDENTITY | {'ownerId', 'status', 'receiptId'}
PROOF = CONTEXT | {'kind', 'verified', 'receiptId'}


def token(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or any(ord(c) < 32 for c in value):
        raise ValueError('Bounded trusted admission identity required')
    return value


def detached(value):
    encoded = json.dumps(value, allow_nan=False)
    if len(encoded.encode()) > 16384:
        raise ValueError('Aggregate admission exceeds private journal bound')
    return json.loads(encoded)


def validate_authority(path):
    """Check the additive profile before any writable intake connection opens."""
    path = Path(path).absolute()
    if not os.path.lexists(path):
        return  # The intake gate independently rejects surviving sidecars.
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        row = db.execute('SELECT value FROM releases WHERE fence=?', (PROFILE,)).fetchone()
        kind = db.execute('SELECT type FROM sqlite_master WHERE name=?', (TABLE,)).fetchone()
        if row is None and kind is None:
            return  # Explicit additive initialization of this new profile only.
        if row is None or json.loads(row[0]) != MARKER or kind != ('table',):
            raise ValueError('Aggregate admission authority profile unavailable; no implicit repair')
        columns = {r[1]: (r[2].upper(), r[3], r[5]) for r in db.execute('PRAGMA table_info(' + TABLE + ')')}
        if columns != {'fence': ('TEXT', 0, 1), 'value': ('TEXT', 1, 0)}:
            raise ValueError('Aggregate admission journal schema differs')
        db.execute('SELECT fence,value FROM ' + TABLE + ' LIMIT 1').fetchone()
    finally:
        db.close()


class AggregateAdmissions:
    VERSION = 1

    def __init__(self, intake):
        self.intake = intake
        self.db = intake.db
        row = self.db.execute('SELECT value FROM releases WHERE fence=?', (PROFILE,)).fetchone()
        if row is None:
            with self.db:
                self.db.execute('CREATE TABLE ' + TABLE + '(fence TEXT PRIMARY KEY,value TEXT NOT NULL)')
                self.db.execute('INSERT INTO releases VALUES(?,?)', (PROFILE, json.dumps(MARKER)))

    def request(self, args):
        if not isinstance(args, dict):
            raise ValueError('Private aggregate admission mapping required')
        operation = args.get('operation')
        fields = {'begin': {'owners'}, 'attempt': {'childOwnerId'}, 'result': {'childOwnerId', 'acquisition'},
                  'abortIntent': {'proof'}, 'abortReceipt': {'childOwnerId', 'receipt'}, 'complete': set(), 'read': set()}
        if operation not in fields or set(args) != {'operation', 'context', 'ownerId'} | fields[operation]:
            raise ValueError('Exact private aggregate admission operation required')
        context = args['context']
        if not isinstance(context, dict) or set(context) != CONTEXT or context.get('purpose') != 'distribution-update':
            raise ValueError('Exact distribution admission context required')
        context = {key: token(context[key]) for key in CONTEXT}
        if context['fenceId'] == PROFILE:
            raise ValueError('Reserved admission profile identity')
        owner = token(args['ownerId'])
        row = self.db.execute('SELECT value FROM ' + TABLE + ' WHERE fence=?', (context['fenceId'],)).fetchone()
        journal = json.loads(row[0]) if row else None
        if journal and (journal['context'] != context or journal['ownerId'] != owner):
            raise ValueError('Aggregate admission differs from retained original identity')
        if operation == 'read':
            return detached(journal) if journal else None
        if operation == 'begin':
            owners = args['owners']
            if not isinstance(owners, list) or owners != [owner + ':uploads', owner + ':python']:
                raise ValueError('Exact ordered upload and Python child identities required')
            for child in owners:
                token(child)
            if journal:
                if journal['owners'] != owners:
                    raise ValueError('Aggregate child identities differ from original')
                return detached(journal)
            journal = {'version': 1, 'context': context, 'ownerId': owner, 'owners': list(owners), 'attempts': []}
        elif journal is None:
            raise ValueError('No original aggregate admission journal')
        elif operation == 'attempt':
            child = token(args['childOwnerId'])
            if any(a['childOwnerId'] == child for a in journal['attempts']):
                return detached(journal)
            if journal.get('abortProof') or journal.get('receipt'):
                raise ValueError('No child dispatch after abort intent')
            index = len(journal['attempts'])
            if index >= len(journal['owners']) or child != journal['owners'][index]:
                raise ValueError('Child dispatch differs from original ordered declaration')
            if index and journal['attempts'][-1]['status'] != 'acquired':
                raise ValueError('Earlier acquisition remains refused or unknown')
            journal['attempts'].append({'childOwnerId': child, 'status': 'pending'})
        elif operation == 'result':
            attempt = self._attempt(journal, args['childOwnerId'])
            acquisition = detached(args['acquisition'])
            if not isinstance(acquisition, dict) or len(acquisition) > 16 or len(json.dumps(acquisition).encode()) > 4096:
                raise ValueError('Bounded original acquisition reply required')
            if acquisition.get('acquired') is True:
                if acquisition.get('intakeClosed') is not True or acquisition != {'acquired': True, 'fenceId': context['fenceId'], 'intakeClosed': True}:
                    raise ValueError('Exact acquired child reply required')
                status = 'acquired'
            elif acquisition.get('acquired') is False and acquisition.get('executed') is False:
                status = 'refused'
            else:
                raise ValueError('Child acquisition outcome remains unknown')
            if 'acquisition' in attempt:
                if attempt['acquisition'] != acquisition:
                    raise ValueError('Acquisition reply differs from retained original')
                return detached(journal)
            if journal.get('receipt') or attempt.get('abortReceipt'):
                raise ValueError('Cannot replace settled child evidence')
            attempt.update(status=status, acquisition=acquisition)
        elif operation == 'abortIntent':
            proof = detached(args['proof'])
            if (not isinstance(proof, dict) or set(proof) != PROOF or proof.get('kind') != 'distribution-admission-abort'
                    or proof.get('verified') is not True or any(proof.get(k) != context[k] for k in CONTEXT)):
                raise ValueError('Exact distinct authenticated aggregate abort proof required')
            token(proof['receiptId'])
            if journal.get('abortProof') and journal['abortProof'] != proof:
                raise ValueError('Aggregate abort proof differs from retained original')
            journal['abortProof'] = proof
        elif operation == 'abortReceipt':
            if not journal.get('abortProof'):
                raise ValueError('Retained aggregate abort intent required')
            attempt = self._attempt(journal, args['childOwnerId'])
            receipt = detached(args['receipt'])
            if (not isinstance(receipt, dict) or set(receipt) != RECEIPT
                    or receipt.get('ownerId') != attempt['childOwnerId']
                    or receipt.get('status') not in ('released', 'not-acquired')
                    or any(receipt.get(k) != context[k] for k in IDENTITY)):
                raise ValueError('Exact child abort receipt required')
            for key in RECEIPT - {'status'}:
                token(receipt[key])
            expected = {'acquired': 'released', 'refused': 'not-acquired'}.get(attempt['status'])
            if expected and receipt['status'] != expected:
                raise ValueError('Child abort receipt contradicts original acquisition')
            if attempt.get('abortReceipt'):
                if attempt['abortReceipt'] != receipt:
                    raise ValueError('Child abort receipt differs from retained original')
                return detached(journal)
            unsettled = [a for a in reversed(journal['attempts']) if not a.get('abortReceipt')]
            if not unsettled or unsettled[0]['childOwnerId'] != attempt['childOwnerId']:
                raise ValueError('Child abort must settle reverse attempted order')
            attempt['abortReceipt'] = receipt
        elif operation == 'complete':
            if not journal.get('abortProof') or any(not a.get('abortReceipt') for a in journal['attempts']):
                raise ValueError('Aggregate abort has unsettled attempted child owners')
            if self.intake.calls or self.intake.background:
                raise ValueError('Python owner work remains in flight')
            if not journal.get('receipt'):
                journal['receipt'] = {key: context[key] for key in IDENTITY}
                journal['receipt'].update(ownerId=owner, receiptId=str(uuid4()), status=(
                    'released' if any(a['abortReceipt']['status'] == 'released' for a in journal['attempts']) else 'not-acquired'))
        self._save(journal)
        return detached(journal['receipt'] if operation == 'complete' else journal)

    @staticmethod
    def _attempt(journal, child):
        token(child)
        for attempt in journal['attempts']:
            if attempt['childOwnerId'] == child:
                return attempt
        raise ValueError('No retained child dispatch intent')

    def _save(self, journal):
        value = json.dumps(detached(journal), allow_nan=False)
        with self.db:
            self.db.execute('INSERT INTO ' + TABLE + ' VALUES(?,?) ON CONFLICT(fence) DO UPDATE SET value=excluded.value',
                            (journal['context']['fenceId'], value))
