"""Exercise the legacy schema with populated notes, consent and retry records."""
import json
import sqlite3
from pathlib import Path

import pytest

from amplifier_unified_recall.migrate import import_explicit_memories, sha256
from amplifier_unified_recall.owner import Owner

SID = 'ahp-session:/imported'
MAPPING = {'sessions': {'old-chat': SID, 'excluded-chat': 'ahp-session:/excluded'},
           'workspaces': {'old-workspace': '/owned/project'}}


def legacy(tmp_path):
    path = tmp_path / 'legacy.sqlite3'
    db = sqlite3.connect(path)
    db.executescript('''
      CREATE TABLE memories(id TEXT PRIMARY KEY,scope TEXT,target TEXT,revision INTEGER,value TEXT);
      CREATE TABLE memory_versions(id TEXT,revision INTEGER,value TEXT,PRIMARY KEY(id,revision));
      CREATE TABLE memory_receipts(id TEXT PRIMARY KEY,fingerprint TEXT,result TEXT);
      CREATE TABLE memory_settings(workspace TEXT PRIMARY KEY,value TEXT);
      CREATE TABLE memory_attempts(id TEXT PRIMARY KEY,workspace TEXT,session_id TEXT,created REAL,value TEXT);
    ''')
    notes = []
    for scope, target in [('task', 'old-chat'), ('workspace', 'old-workspace'), ('global', '')]:
        value = {'id': scope, 'revision': 2, 'scope': scope, 'target': target,
                 'createdAt': 1.0, 'updatedAt': 2.0, 'text': 'Keep the cobalt headings concise.\nExact wording.',
                 'source': None, 'provenance': {'origin': 'ui', 'sessionId': 'old-chat'}}
        notes.append(value)
        db.execute('INSERT INTO memories VALUES(?,?,?,?,?)', (scope, scope, target, 2, json.dumps(value)))
        for version in [{**value, 'revision': 1, 'text': 'Original cobalt wording.'}, value]:
            db.execute('INSERT INTO memory_versions VALUES(?,?,?)', (scope, version['revision'], json.dumps(version)))
    db.execute('INSERT INTO memory_receipts VALUES(?,?,?)', ('already-deleted', 'original-fingerprint', json.dumps({'id': 'deleted', 'deleted': True, 'revision': 3})))
    settings = {'revision': 4, 'contribute': False, 'use': True, 'excludedSessions': ['excluded-chat'],
                'maxCallsPerDay': 2, 'workspace': 'old-workspace', 'provenance': {'origin': 'ui', 'sessionId': 'old-chat'}}
    db.execute('INSERT INTO memory_settings VALUES(?,?)', ('old-workspace', json.dumps(settings)))
    db.commit()
    db.close()
    return path, notes, settings


async def migrate(source, destination, **options):
    return await import_explicit_memories(source, destination, expected_sha256=sha256(source), mapping=MAPPING, **options)


async def forbidden(*args, **kwargs):
    raise AssertionError('No model, native execution, remote service or notification during migration')


@pytest.mark.asyncio
async def test_populated_explicit_memories_survive_in_real_owner_and_model_context(tmp_path):
    source, notes, settings = legacy(tmp_path)
    before = source.read_bytes()
    target = tmp_path / 'new-owner'
    receipt = await migrate(source, target)
    assert receipt['counts'] == {'memories': 3, 'versions': 6, 'settings': 1, 'receipts': 1}
    assert not receipt['activated'] and not receipt['executionStarted']
    assert source.read_bytes() == before == (target / 'legacy-recall.sqlite3').read_bytes()
    calls = []
    user_message = {'id': 'typed', 'role': 'user', 'text': 'Use cobalt headings',
                    'inputOrigin': 'user', 'provenance': {'source': 'host-admission', 'complete': True}}

    async def host(method, args):
        calls.append(method)
        if method == 'inspectSession':
            return {'historyHome': '/owned/project'}
        if method == 'readSessionContext':
            return {'messages': [user_message]}
        if method == 'readUserMessage':
            return user_message
        raise AssertionError(method)

    owner = Owner({'dataDir': str(target)}, host, forbidden)
    try:
        for original in notes:
            actual = owner.store.memory(original['id'])
            assert actual == {**original, 'target': {'task': SID, 'workspace': '/owned/project', 'global': ''}[original['scope']]}
            assert len(owner.store.versions(actual['id'])) == 2
        imported = owner.policy.settings('/owned/project')
        assert imported == {**settings, 'workspace': '/owned/project', 'excludedSessions': ['ahp-session:/excluded']}
        result = await owner.request('context', {'session': SID})
        assert len(result['items']) == 3, result
        assert all(item['text'] == notes[0]['text'] for item in result['items'])
        assert set(calls) <= {'inspectSession', 'readSessionContext', 'readUserMessage'}
        with pytest.raises(ValueError, match='different contents'):
            owner.store.mutate('memory.create', {'scope': 'global', 'target': '', 'text': 'Must not recreate'}, command_id='already-deleted', provenance={})
        assert len(owner.store.list_memories(None)['items']) == 3
    finally:
        await owner.close()
    # No implicit merge, overwrite, or repeated migration on retry.
    with pytest.raises(ValueError, match='already exists'):
        await migrate(source, target)
    assert source.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('change,pattern', [
    ('missing-task', 'Unmapped'), ('missing-workspace', 'Unmapped'), ('missing-exclusion', 'Unmapped'),
    ('many-to-one', 'Many-to-one'), ('relative-workspace', 'workspace history path'),
])
async def test_scope_mapping_must_be_complete_explicit_and_nonmerging(tmp_path, change, pattern):
    source, _, _ = legacy(tmp_path)
    mapping = json.loads(json.dumps(MAPPING))
    if change == 'missing-task': del mapping['sessions']['old-chat']
    if change == 'missing-workspace': del mapping['workspaces']['old-workspace']
    if change == 'missing-exclusion': del mapping['sessions']['excluded-chat']
    if change == 'many-to-one': mapping['sessions']['excluded-chat'] = SID
    if change == 'relative-workspace': mapping['workspaces']['old-workspace'] = '../elsewhere'
    before = source.read_bytes()
    with pytest.raises(ValueError, match=pattern):
        await import_explicit_memories(source, tmp_path / 'target', expected_sha256=sha256(source), mapping=mapping)
    assert not (tmp_path / 'target').exists() and source.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('change,pattern', [
    ('uncertain-attempt', 'consolidation migration'), ('referenced-note', 'source-evidence migration'),
    ('changed-revision', 'differs from saved history'), ('missing-current-version', 'missing from history'),
    ('inconsistent-scope', 'scope columns'), ('withdrawn-source', 'source-evidence migration'),
])
async def test_unqualified_authority_fails_without_creating_partial_owner(tmp_path, change, pattern):
    source, notes, _ = legacy(tmp_path)
    db = sqlite3.connect(source)
    if change == 'uncertain-attempt':
        db.execute('INSERT INTO memory_attempts VALUES(?,?,?,?,?)', ('pending', 'old-workspace', 'old-chat', 1, '{"status":"claimed"}'))
    elif change == 'missing-current-version':
        db.execute('DELETE FROM memory_versions WHERE revision=2')
    elif change == 'inconsistent-scope':
        db.execute("UPDATE memories SET target='different' WHERE id='task'")
    else:
        value = notes[0]
        if change == 'referenced-note': value['source'] = {'sessionId': 'old-chat', 'messageId': 'original'}
        if change == 'withdrawn-source': value['automationSourceKey'] = 'withdrawn-reference'
        if change == 'changed-revision': value['text'] = 'A conflicting current revision'
        db.execute('UPDATE memories SET value=? WHERE id=?', (json.dumps(value), value['id']))
    db.commit()
    db.close()
    before = source.read_bytes()
    with pytest.raises(ValueError, match=pattern):
        await migrate(source, tmp_path / 'target')
    assert source.read_bytes() == before and not (tmp_path / 'target').exists()


@pytest.mark.asyncio
async def test_changed_snapshot_and_live_sidecars_refuse_before_import(tmp_path):
    source, _, _ = legacy(tmp_path)
    with pytest.raises(ValueError, match='changed since review'):
        await import_explicit_memories(source, tmp_path / 'target', expected_sha256='0' * 64, mapping=MAPPING)
    Path(str(source) + '-wal').write_bytes(b'uncheckpointed original')
    with pytest.raises(ValueError, match='closed captured snapshot'):
        await migrate(source, tmp_path / 'target')
    assert Path(str(source) + '-wal').read_bytes() == b'uncheckpointed original'
    assert not (tmp_path / 'target').exists()
