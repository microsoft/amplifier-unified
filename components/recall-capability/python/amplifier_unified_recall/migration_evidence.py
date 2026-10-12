"""Reviewed identity translations for offline legacy memory transfer.

These mappings grant no input or execution authority. The receiving owner still
verifies a referenced note against its exact, currently available human source.
"""
import copy
import math
import re

from amplifier_recall.store import digest


def token(value, name, limit=512):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid ' + name)
    return value


def hash_value(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('Exact source digest required')
    return value


class Evidence:
    def __init__(self, value, mapping, maximum=100_000):
        if not isinstance(value, dict) or set(value) != {'messages', 'attempts'}:
            raise ValueError('Reviewed message and attempt evidence required')
        self.mapping = mapping
        self.messages, self.attempts, self.source_keys = {}, {}, {}
        self.used_attempts = set()
        targets = set()
        for kind in ('messages', 'attempts'):
            if not isinstance(value[kind], list) or len(value[kind]) > maximum:
                raise ValueError('Invalid evidence capacity')
        for item in value['messages']:
            if not isinstance(item, dict) or set(item) != {'sessionId', 'messageId', 'sha256', 'mappedMessageId'}:
                raise ValueError('Exact message evidence required')
            sid = token(item['sessionId'], 'source session')
            mid = token(item['messageId'], 'source message')
            new_mid = token(item['mappedMessageId'], 'mapped message')
            sha = hash_value(item['sha256'])
            if sid not in mapping['sessions']:
                raise ValueError('Unmapped evidence session')
            source = (sid, mid, sha)
            target = (mapping['sessions'][sid], new_mid, sha)
            if source in self.messages or target in targets:
                raise ValueError('Duplicate or merging source evidence')
            self.messages[source] = target
            targets.add(target)
            self.source_keys[digest(list(source))] = digest(list(target))
        for item in value['attempts']:
            if not isinstance(item, dict) or set(item) != {'id', 'sourceRevision', 'mappedSourceRevision'}:
                raise ValueError('Exact attempt evidence required')
            identity = hash_value(item['id'])
            if identity in self.attempts:
                raise ValueError('Duplicate attempt evidence')
            self.attempts[identity] = (hash_value(item['sourceRevision']), hash_value(item['mappedSourceRevision']))

    def source(self, value):
        if not isinstance(value, dict) or value.get('kind') not in {None, 'attributed-user'}:
            raise ValueError('Unrecognized memory source attribution')
        old = (value.get('sessionId'), value.get('messageId'), value.get('sha256'))
        if old not in self.messages:
            raise ValueError('Unmapped exact memory evidence')
        sid, mid, _ = self.messages[old]
        # sourceRevision describes the historical extraction, not a newly
        # observed revision. Live access verifies the exact text digest again.
        return {**value, 'sessionId': sid, 'messageId': mid}

    def note(self, value):
        result = copy.deepcopy(value)
        if result.get('source'):
            result['source'] = self.source(result['source'])
        automated = bool(value.get('automationKey') or value.get('automationSourceKey') or value['provenance'].get('origin') == 'consolidation')
        if automated:
            source = value.get('source') or {}
            old_key = digest([source.get('sessionId'), source.get('messageId'), source.get('sha256')])
            quote = source.get('quote')
            if (source.get('kind') != 'attributed-user' or not isinstance(quote, str) or not quote.strip()
                    or value.get('automationSourceKey') != old_key
                    or value.get('automationKey') != digest([old_key, ' '.join(quote.split())])):
                raise ValueError('Automatic memory evidence disagrees with its saved identity')
            new_key = self.source_keys[old_key]
            result['automationSourceKey'] = new_key
            result['automationKey'] = digest([new_key, ' '.join(quote.split())])
        for key in ('supersedes', 'supersededBy'):
            if key in result:
                identities = result[key] if key == 'supersedes' else [result[key]]
                if not isinstance(identities, list) or len(identities) > 100:
                    raise ValueError('Invalid supersession evidence')
                for identity in identities:
                    token(identity, 'memory relationship', 200)
        return result

    def attempt(self, row):
        from .migrate import encode, mapped, record
        identity, workspace, sid, created, raw = row
        value = record(raw)
        if (identity not in self.attempts or isinstance(created, bool)
                or not isinstance(created, (int, float)) or not math.isfinite(created) or created < 0):
            raise ValueError('Every retained attempt requires exact source-revision evidence')
        status = value.get('status')
        if status not in {'claimed', 'completed', 'failed', 'interrupted', 'unknown'}:
            raise ValueError('Unrecognized retained memory attempt status')
        original_revision, revision = self.attempts[identity]
        if identity != digest([sid, original_revision]) or value.get('sourceRevision', original_revision) != original_revision:
            raise ValueError('Attempt evidence disagrees with original identity')
        sid = mapped(sid, 'sessions', self.mapping)
        workspace = mapped(workspace, 'workspaces', self.mapping)
        # Keep the original payload intact inside the retained capture. A claim
        # is an uncertain outcome, never permission to run again after import.
        value = {**value, 'legacyAttemptId': identity, 'sourceRevision': revision}
        if status == 'claimed':
            value.update(status='unknown', reason='Imported attempt outcome unconfirmed; not replayed')
        self.used_attempts.add(identity)
        return (digest([sid, revision]), workspace, sid, created, encode(value))

    def suppression(self, key):
        if key not in self.source_keys:
            raise ValueError('Withdrawn source requires an exact evidence mapping')
        return self.source_keys[key]

    def complete(self):
        if self.used_attempts != set(self.attempts):
            raise ValueError('Attempt evidence contains an unknown source attempt')
