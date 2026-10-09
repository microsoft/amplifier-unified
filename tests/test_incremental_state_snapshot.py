"""Full and scoped saves retain crash recovery without rewriting unrelated data."""
import copy
import json
import sqlite3

import pytest

from amplifier_web import state_records as records


def open_db(path):
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA wal_autocheckpoint=0')
    db.execute('CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
    records.initialize(db)
    db.commit()
    return db


def snapshot():
    return {'revision': 1, 'schemaVersion': 1,
            'sessions': [{'id': 'a', '$view': True}, {'id': 'b', '$native': True}],
            'runtimeControl': {'a': {'providers': 'x' * 500_000}},
            'workspaces': [{'id': 'w', 'metadata': 'y' * 500_000}],
            'view': {'draft': 'unsent'}, 'toRemove': {'value': 1}}


def test_full_saves_preserve_exact_order_deletions_and_nested_changes_on_reopen(tmp_path):
    path = tmp_path / 'app.sqlite3'
    with open_db(path) as db:
        state = snapshot()
        records.checkpoint(db, state)
        db.commit()
        state['revision'] += 1
        state['sessions'].reverse()
        state['sessions'][0]['$native'] = False
        state['sessions'].append({'id': 'c', 'value': 'added'})
        del state['runtimeControl']['a']
        del state['toRemove']
        state['workspaces'][0]['metadata'] = 'changed in place'
        records.checkpoint(db, state)
    db.close()
    with open_db(path) as db:
        assert records.load(db) == state
        assert [row['id'] for row in records.load(db)['sessions']] == ['b', 'a', 'c']
    db.close()


def test_legacy_snapshot_and_overlays_convert_atomically(tmp_path):
    with open_db(tmp_path / 'app.sqlite3') as db:
        state = snapshot()
        db.execute('INSERT INTO state VALUES(1,?)', (json.dumps(state),))
        db.commit()
        state['revision'] = 2
        state['view']['draft'] = 'scoped new draft'
        records.save(db, state, {'a': {'id': 'a', 'changed': True}}, {'a'}, {'view'})
        db.commit()
        expected = records.load(db)
        records.checkpoint(db, expected)
        # A failed conversion must leave the complete old base + overlays.
        db.rollback()
        assert records.load(db) == expected
        assert json.loads(db.execute('SELECT value FROM state').fetchone()[0]) == snapshot()
        records.checkpoint(db, expected)
        db.commit()
        assert records.load(db) == expected
    db.close()


def test_failed_full_save_then_scoped_save_cannot_publish_partial_state(tmp_path):
    with open_db(tmp_path / 'app.sqlite3') as db:
        state = snapshot()
        records.checkpoint(db, state)
        db.commit()
        before = copy.deepcopy(state)
        state['revision'] += 1
        state['sessions'].reverse()
        state['workspaces'] = []
        state['view']['draft'] = 'new draft'
        db.execute('''CREATE TEMP TRIGGER reject_view BEFORE UPDATE ON state_records
            WHEN NEW.kind='global' AND NEW.id='view'
            BEGIN SELECT RAISE(ABORT, 'test full save failure'); END''')
        with pytest.raises(sqlite3.IntegrityError, match='test full save failure'):
            records.checkpoint(db, state)
        db.rollback()
        assert records.load(db) == before
        db.execute('DROP TRIGGER reject_view')
        records.save(db, state, {'a': {'id': 'a', 'changed': True}}, {'a'})
        db.commit()
        durable = records.load(db)
        assert durable['view'] == before['view']
        assert durable['workspaces'] == before['workspaces']
        records.checkpoint(db, state)
        db.commit()
        assert records.load(db) == state
    db.close()


def test_scoped_tombstones_and_full_reconciliation_do_not_resurrect_records(tmp_path):
    with open_db(tmp_path / 'app.sqlite3') as db:
        state = snapshot()
        records.checkpoint(db, state)
        db.commit()
        del state['toRemove']
        del state['runtimeControl']['a']
        state['sessions'] = [state['sessions'][1]]
        state['revision'] += 1
        records.save(db, state, {}, {'a'}, {'toRemove'})
        db.commit()
        assert records.load(db) == state
        records.checkpoint(db, state)
        db.commit()
        assert records.load(db) == state
    db.close()


@pytest.mark.parametrize('scoped', [False, True])
def test_small_changes_have_bounded_journal_writes_with_large_unrelated_state(tmp_path, scoped):
    path = tmp_path / 'app.sqlite3'
    with open_db(path) as db:
        state = snapshot()
        records.checkpoint(db, state)
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        for number in range(10):
            state['revision'] += 1
            state['sessions'][0]['step'] = number
            if scoped:
                records.save(db, state, {'a': state['sessions'][0]}, {'a'})
            else:
                records.checkpoint(db, state)
            db.commit()
        assert records.load(db) == state
        # 1 MB of unrelated settings must not be rewritten for each small edit.
        assert path.with_name(path.name + '-wal').stat().st_size < 200_000
        size = path.with_name(path.name + '-wal').stat().st_size
        records.checkpoint(db, state)
        db.commit()
        assert path.with_name(path.name + '-wal').stat().st_size == size
    db.close()
