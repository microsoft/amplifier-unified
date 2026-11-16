import json
from types import SimpleNamespace

import pytest

from amplifier_recall import RecallStore
from amplifier_recall.personalization import Personalization


def test_fts_ranks_entire_5000_conversation_library_and_isolates_scopes(tmp_path):
    store=RecallStore(tmp_path/'recall.db')
    try:
        for n in range(5000):
            store.replace({'id':str(n),'title':f'Chat {n}','workspace':'a' if n%2 else 'b'},str(n),n,
                [{'id':f'm{n}','role':'assistant','text':'The old decision was cobalt.' if n==4999 else 'An unrelated note.'}])
        found=store.search('old cobalt',{str(n) for n in range(5000)})
        assert len(found['items'])==1 and found['items'][0]['sessionId']=='4999'
        assert found['items'][0]['reference']['sourceRevision']==4999
        assert not store.search('cobalt',{str(n) for n in range(0,5000,2)})['items']
        assert store.search('" OR *',{str(n) for n in range(5000)})['items']==[]
        assert len(store.search('unrelated',{str(n) for n in range(5000)},limit=20)['items'])==20
        store.prune({'0'})
        assert not store.search('cobalt',{'4999'})['items']
    finally:store.close()


def test_note_receipts_and_rollback_survive_reopen_without_deleted_text(tmp_path):
    path = tmp_path / 'recall.db'
    provenance = {'origin': 'ui', 'sessionId': 'source'}
    create = {'scope': 'workspace', 'target': 'workspace', 'text': 'Use violet.'}
    store = RecallStore(path)
    try:
        saved = store.mutate('memory.create', create, command_id='create', provenance=provenance)
        with pytest.raises(RuntimeError, match='rollback'):
            with store.atomic():
                store.mutate('memory.create', {**create, 'text': 'Discard this note.'},
                             command_id='rolled-back', provenance=provenance)
                raise RuntimeError('rollback')
    finally:
        store.close()

    store = RecallStore(path)
    try:
        assert store.mutate('memory.create', create, command_id='create', provenance=provenance) == {
            **saved, 'duplicate': True}
        assert store.db.execute("SELECT COUNT(*) FROM memory_receipts WHERE id='rolled-back'").fetchone()[0] == 0
        assert [row['id'] for row in store.list_memories(None)['items']] == [saved['id']]
        changed = store.mutate('memory.update', {'id': saved['id'], 'expectedRevision': 1, 'text': 'Use orange.'},
                               command_id='update', provenance=provenance)
        with pytest.raises(ValueError, match='changed'):
            store.mutate('memory.update', {'id': saved['id'], 'expectedRevision': 1, 'text': 'Stale note.'},
                         command_id='stale', provenance=provenance)
        assert [row['text'] for row in store.versions(saved['id'])] == ['Use orange.', 'Use violet.']
        delete = {'id': saved['id'], 'expectedRevision': changed['revision']}
        removed = store.mutate('memory.delete', delete, command_id='delete', provenance=provenance)
    finally:
        store.close()

    store = RecallStore(path)
    try:
        assert store.mutate('memory.delete', delete, command_id='delete', provenance=provenance) == {
            **removed, 'duplicate': True}
        with pytest.raises(ValueError, match='different contents'):
            store.mutate('memory.create', {**create, 'text': 'Different request.'},
                         command_id='create', provenance=provenance)
        assert store.list_memories(None)['items'] == []
        assert store.db.execute('SELECT COUNT(*) FROM memory_versions').fetchone()[0] == 0
        receipts = json.dumps(store.db.execute('SELECT * FROM memory_receipts').fetchall())
        assert all(text not in receipts for text in ('Use violet.', 'Use orange.', 'Discard this note.'))
    finally:
        store.close()


def test_personalization_reopen_retains_settings_and_never_replays_claim(tmp_path, monkeypatch):
    monkeypatch.setattr('amplifier_recall.personalization.time', SimpleNamespace(time=lambda: 1700000000))
    path = tmp_path / 'recall.db'
    args = {'expectedRevision': 0, 'contribute': True, 'use': False, 'maxCallsPerDay': 1}
    provenance = {'origin': 'ui'}
    store = RecallStore(path)
    try:
        personalization = Personalization(store)
        configured = personalization.configure('workspace', args, provenance, 'configure')
        claim = personalization.claim('workspace', 'source', 'revision-one', 1)
        assert claim is not None
    finally:
        store.close()

    store = RecallStore(path)
    try:
        personalization = Personalization(store)
        assert personalization.settings('workspace') == configured
        assert personalization.configure('workspace', args, provenance, 'configure') == {
            **configured, 'duplicate': True}
        attempt, = personalization.attempts('workspace')
        assert attempt['id'] == claim and attempt['sessionId'] == 'source'
        assert attempt['status'] == 'interrupted' and 'not replayed' in attempt['reason']
        assert personalization.claim('workspace', 'source', 'revision-one', 1) is None
        with pytest.raises(ValueError, match='call limit'):
            personalization.claim('workspace', 'source', 'revision-two', 1)
    finally:
        store.close()
