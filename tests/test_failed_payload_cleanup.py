"""Discriminating cleanup probe, run unchanged on baseline and prototype."""
import copy
import json
import sqlite3

import pytest

from amplifier_web import state_records, resource_files, cold_display
from test_automatic_history import app_factory
from test_live_clients import command


async def test_failed_save_then_restart_and_delete_cleans_unindexed_payloads(app_factory, monkeypatch):
    app = app_factory()
    app.clients.attach('web')
    created = await command(app, 'web', 'session.create', {'location':{'kind':'managed'}})
    sid = created['sessionId']
    row = app._session(sid)
    row['messages'] = [{'id':'m', 'role':'user', 'text':'ORIGINAL'+('x'*40000)}]
    app._publish()
    before = state_records.load(app.db)
    expected = copy.deepcopy(row['messages'])
    root = resource_files.root(app.db)
    existing = {p.name for p in root.glob('*.json')}
    row['messages'][0]['text'] = 'FAILED-UNIQUE'+('y'*40000)
    original = state_records.save
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise sqlite3.OperationalError('after staged pointer')
    with monkeypatch.context() as m:
        m.setattr(state_records, 'save', fail)
        with pytest.raises(sqlite3.OperationalError):
            app._publish(session_ids={sid}, detail_only=True, record_only=True)
    assert state_records.load(app.db) == before
    indexed = {identity+'.json' for (identity,) in app.db.execute('SELECT id FROM state_resources')}
    new_unindexed = [p for p in root.glob('*.json') if p.name not in existing and p.name not in indexed]
    await app.publishing.close()
    restored = app_factory(home=app.data_dir)
    assert restored._session(sid)['messages'] == expected
    stale = resource_files.collect(restored.db, restored._state)
    restored.db.commit()
    resource_files.remove_files(restored.db, stale)
    preview = (await command(restored, 'web', 'session.deletePreview', {'id':sid}))['result']
    deleted = await command(restored, 'web', 'session.delete', {'id':sid,'confirmationToken':preview['confirmationToken']})
    assert deleted['result']['deleted'] and not deleted['result']['cleanupPending']
    remaining = [p for p in new_unindexed if p.exists()]
    print('CLEANUP_OBSERVATION '+json.dumps({'newUnindexedAfterFailure':len(new_unindexed),
          'remainingAfterRestartGCAndDelete':len(remaining),'remainingBytes':sum(p.stat().st_size for p in remaining),
          'oldCommittedMessagesPreserved':True,'deleteReportedComplete':True}))
    assert not remaining, 'Failed transaction left unindexed payload files after GC and managed deletion'
