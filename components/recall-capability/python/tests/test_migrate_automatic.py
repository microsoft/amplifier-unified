"""Automatic history stays passive, scoped, deduplicated and source verified."""
import copy
import json
import sqlite3
import time

import pytest
from amplifier_recall.store import digest

from amplifier_unified_recall.migrate import import_legacy_memories, sha256
from amplifier_unified_recall.owner import Owner

SID = 'ahp-session:/received'
WS = '/owned/workspace'
TEXT = 'Keep the cobalt headings concise.'
MAPPING = {'sessions': {'legacy-chat': SID}, 'workspaces': {'legacy-workspace': WS}}


def capture(tmp_path):
    source = tmp_path / 'captured.sqlite3'
    db = sqlite3.connect(source)
    db.executescript('''
        CREATE TABLE memories(id TEXT PRIMARY KEY,scope TEXT,target TEXT,revision INTEGER,value TEXT);
        CREATE TABLE memory_versions(id TEXT,revision INTEGER,value TEXT,PRIMARY KEY(id,revision));
        CREATE TABLE memory_receipts(id TEXT PRIMARY KEY,fingerprint TEXT,result TEXT);
        CREATE TABLE memory_settings(workspace TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE memory_attempts(id TEXT PRIMARY KEY,workspace TEXT,session_id TEXT,created REAL,value TEXT);
        CREATE TABLE memory_suppression(key TEXT PRIMARY KEY);
    ''')
    old_key = digest(['legacy-chat', 'original-message', digest(TEXT)])
    value = {'id': 'saved-note', 'scope': 'workspace', 'target': 'legacy-workspace', 'revision': 1,
             'text': TEXT, 'createdAt': 1, 'updatedAt': 1, 'supersedes': ['older-note'],
             'source': {'kind': 'attributed-user', 'sessionId': 'legacy-chat', 'messageId': 'original-message',
                        'sha256': digest(TEXT), 'quote': TEXT, 'messageCreatedAt': 1},
             'automationSourceKey': old_key, 'automationKey': digest([old_key, TEXT]),
             'provenance': {'origin': 'consolidation', 'sessionId': 'legacy-chat', 'wording': 'model-derived'}}
    db.execute('INSERT INTO memories VALUES(?,?,?,?,?)', ('saved-note', 'workspace', 'legacy-workspace', 1, json.dumps(value)))
    db.execute('INSERT INTO memory_versions VALUES(?,?,?)', ('saved-note', 1, json.dumps(value)))
    db.execute('INSERT INTO memory_settings VALUES(?,?)', ('legacy-workspace', json.dumps({'revision': 2, 'contribute': True, 'use': True, 'excludedSessions': [], 'maxCallsPerDay': 3})))
    db.execute('INSERT INTO memory_receipts VALUES(?,?,?)', ('deleted-command', 'original', '{"deleted":true,"id":"deleted-note"}'))
    # A removed note leaves only a suppression digest. Its source must resolve
    # even though the note itself no longer exists.
    deleted_key = digest(['legacy-chat', 'deleted-source', digest('A preference that was withdrawn.')])
    db.execute('INSERT INTO memory_suppression VALUES(?)', (deleted_key,))
    evidence = {'messages': [
        {'sessionId': 'legacy-chat', 'messageId': 'original-message', 'sha256': digest(TEXT), 'mappedMessageId': 'received-message'},
        {'sessionId': 'legacy-chat', 'messageId': 'deleted-source', 'sha256': digest('A preference that was withdrawn.'), 'mappedMessageId': 'received-deleted'},
    ], 'attempts': []}
    attempts = []
    for status in ['claimed', 'completed', 'failed']:
        revision = digest([['original-message', TEXT], status])
        mapped_revision = digest([['received-message', TEXT], status])
        identity = digest(['legacy-chat', revision])
        result = {'status': status, 'sourceRevision': revision, 'reason': 'Original outcome'}
        db.execute('INSERT INTO memory_attempts VALUES(?,?,?,?,?)', (identity, 'legacy-workspace', 'legacy-chat', time.time(), json.dumps(result)))
        evidence['attempts'].append({'id': identity, 'sourceRevision': revision, 'mappedSourceRevision': mapped_revision})
        attempts.append((identity, status, mapped_revision))
    db.commit()
    db.close()
    return source, value, evidence, attempts


async def forbidden(*args, **kwargs):
    raise AssertionError('Migration cannot start work or make remote calls')


@pytest.mark.asyncio
async def test_automatic_history_preserves_consent_and_does_not_replay_or_resurrect(tmp_path):
    source, original, evidence, attempts = capture(tmp_path)
    before = source.read_bytes()
    target = tmp_path / 'new'
    receipt = await import_legacy_memories(source, target, expected_sha256=sha256(source), mapping=MAPPING, evidence=evidence)
    assert receipt['automaticMemoryMigrated'] and not receipt['executionStarted'] and not receipt['activated']
    assert receipt['counts'] == {'memories': 1, 'versions': 1, 'settings': 1, 'receipts': 1, 'attempts': 3, 'suppression': 1, 'automation': 1}
    assert source.read_bytes() == before == (target / 'legacy-recall.sqlite3').read_bytes()
    owner = Owner({'dataDir': str(target)}, forbidden, forbidden)
    try:
        note = owner.store.memory('saved-note')
        assert note['text'] == original['text'] and note['provenance'] == original['provenance']
        assert note['supersedes'] == ['older-note']
        assert note['source']['sessionId'] == SID and note['source']['messageId'] == 'received-message'
        assert owner.policy.settings(WS)['use'] and owner.policy.settings(WS)['contribute']
        for identity, status, revision in attempts:
            assert owner.policy.claim(WS, SID, revision, 3) is None
            record = next(a for a in owner.policy.attempts(WS) if a['legacyAttemptId'] == identity)
            assert record['status'] == ('unknown' if status == 'claimed' else status)
        with pytest.raises(ValueError, match='call limit'):
            owner.policy.claim(WS, SID, 'new-source', 3)
        # Different model phrasing cannot bypass a user's prior removal.
        deleted = {'messageId': 'received-deleted', 'sha256': digest('A preference that was withdrawn.')}
        assert owner.policy.save(WS, SID, deleted, {'text': 'A regenerated preference', 'quote': 'withdrawn'}, 'new') is None
        assert owner.policy.save(WS, SID, note['source'], {'text': TEXT, 'quote': TEXT}, 'new') is None
        assert len(owner.store.list_memories(None)['items']) == 1
        with pytest.raises(ValueError, match='different contents'):
            owner.store.mutate('memory.create', {'scope': 'workspace', 'target': WS, 'text': 'Recreate'}, command_id='deleted-command', provenance={})
    finally:
        await owner.close()
    # Neither opening nor reopening the receiving owner restarts imported work.
    owner = Owner({'dataDir': str(target)}, forbidden, forbidden)
    assert not owner.tasks
    assert len(owner.policy.attempts(WS)) == 3
    await owner.close()


@pytest.mark.asyncio
async def test_mapped_note_still_requires_real_current_source_and_live_consent(tmp_path):
    source, _, evidence, _ = capture(tmp_path)
    target = tmp_path / 'new'
    await import_legacy_memories(source, target, expected_sha256=sha256(source), mapping=MAPPING, evidence=evidence)
    message = {'id': 'received-message', 'role': 'user', 'text': TEXT, 'inputOrigin': 'user',
               'provenance': {'source': 'host-admission', 'complete': True}}
    workspace = WS

    async def host(method, args):
        if method == 'inspectSession': return {'historyHome': workspace}
        if method == 'readSessionContext': return {'messages': [message]}
        if method == 'readUserMessage':
            assert args['messageId'] == 'received-message'
            return message
        raise AssertionError(method)

    owner = Owner({'dataDir': str(target)}, host, forbidden)
    try:
        assert len((await owner.request('context', {'session': SID}))['items']) == 1
        message['text'] = 'Cobalt headings with changed source wording'
        assert not (await owner.request('context', {'session': SID}))['items']
        message['text'] = TEXT
        message['provenance']['source'] = 'unverified-native-history'
        assert not (await owner.request('context', {'session': SID}))['items']
        message['provenance']['source'] = 'host-admission'
        owner.policy.configure(WS, {'expectedRevision': 2, 'excludedSessions': [SID]}, {'origin': 'ui'})
        assert not (await owner.request('context', {'session': SID}))['items']
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_explicit_reference_keeps_absent_human_attribution_and_superseded_text_does_not_block(tmp_path):
    source, value, evidence, _ = capture(tmp_path)
    db = sqlite3.connect(source)
    value['supersededBy'] = 'later-note'
    manual = {**value, 'id': 'manual-reference', 'source': {k: v for k, v in value['source'].items() if k != 'kind'},
              'provenance': {'origin': 'ui', 'sessionId': 'legacy-chat'}, 'text': 'Explicitly saved reference'}
    for key in ['automationKey', 'automationSourceKey', 'supersededBy', 'supersedes']:
        manual.pop(key, None)
    db.execute('UPDATE memories SET value=? WHERE id=?', (json.dumps(value), value['id']))
    db.execute('UPDATE memory_versions SET value=? WHERE id=?', (json.dumps(value), value['id']))
    db.execute('INSERT INTO memories VALUES(?,?,?,?,?)', (manual['id'], 'workspace', 'legacy-workspace', 1, json.dumps(manual)))
    db.execute('INSERT INTO memory_versions VALUES(?,?,?)', (manual['id'], 1, json.dumps(manual)))
    db.commit()
    db.close()
    target = tmp_path / 'new'
    await import_legacy_memories(source, target, expected_sha256=sha256(source), mapping=MAPPING, evidence=evidence)
    owner = Owner({'dataDir': str(target)}, forbidden, forbidden)
    try:
        imported = owner.store.memory('manual-reference')
        assert imported['source']['sessionId'] == SID and 'kind' not in imported['source']
        assert imported['provenance'] == manual['provenance']
        # A distinct later human source may legitimately restore an older
        # preference. Retain the old quote guard, not its obsolete text guard.
        later = {'kind': 'attributed-user', 'sessionId': SID, 'messageId': 'later-message', 'sha256': digest('Restore cobalt headings'), 'quote': 'cobalt headings'}
        assert owner.policy.save(WS, SID, later, {'text': TEXT, 'quote': 'cobalt headings'}, 'new') is not None
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change,pattern', [
    ('missing-source', 'Unmapped exact'), ('missing-deleted-source', 'Withdrawn source'),
    ('missing-attempt', 'Every retained attempt'), ('wrong-attempt', 'original identity'),
    ('merged-attempts', 'merge distinct outcomes'), ('extra-attempt', 'unknown source attempt'),
    ('merged-messages', 'merging source'), ('wrong-note-key', 'saved identity'),
])
async def test_incomplete_or_ambiguous_evidence_preserves_whole_source(tmp_path, change, pattern):
    source, _, evidence, _ = capture(tmp_path)
    if change == 'missing-source': evidence['messages'].pop(0)
    if change == 'missing-deleted-source': evidence['messages'].pop()
    if change == 'missing-attempt': evidence['attempts'].pop()
    if change == 'wrong-attempt': evidence['attempts'][0]['sourceRevision'] = '0' * 64
    if change == 'merged-attempts': evidence['attempts'][1]['mappedSourceRevision'] = evidence['attempts'][0]['mappedSourceRevision']
    if change == 'extra-attempt': evidence['attempts'].append({'id': '1' * 64, 'sourceRevision': '2' * 64, 'mappedSourceRevision': '3' * 64})
    if change == 'merged-messages': evidence['messages'].append(copy.deepcopy(evidence['messages'][0]))
    if change == 'wrong-note-key':
        db = sqlite3.connect(source)
        for table in ['memories', 'memory_versions']:
            db.execute("UPDATE " + table + " SET value=json_set(value,'$.automationSourceKey','wrong')")
        db.commit()
        db.close()
    before = source.read_bytes()
    with pytest.raises(ValueError, match=pattern):
        await import_legacy_memories(source, tmp_path / 'new', expected_sha256=sha256(source), mapping=MAPPING, evidence=evidence)
    assert source.read_bytes() == before and not (tmp_path / 'new').exists()
