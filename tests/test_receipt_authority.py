"""Owner authority source controls. Authored before implementation; run in isolation.

These exercise the supported host lifecycle, not privileged online restore/schema
surgery. A stopped-service restore must retire disposable proofs separately.
"""
from contextlib import closing
import copy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from amplifier_web import receipt_authority as authority


RECORDS = ('CREATE TABLE records(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,'
           'at REAL,stream TEXT,session TEXT,workspace TEXT,event TEXT,data TEXT)')
WORKSPACE = '/synthetic/workspace'


def owner(identity='a', workspace=WORKSPACE, **patch):
    return {'id': identity, 'workspace': workspace, **patch}


def create_database(home, *, install=True):
    path = home / 'diagnostics' / 'events.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('BEGIN IMMEDIATE')
    db.execute(RECORDS)
    db.execute('CREATE INDEX record_session ON records(session,seq)')
    db.execute('CREATE TABLE deliveries(record_id TEXT,destination TEXT,revision TEXT,payload TEXT,status TEXT,attempts INTEGER DEFAULT 0,next_at REAL DEFAULT 0,error TEXT,updated REAL,PRIMARY KEY(record_id,destination))')
    db.execute('CREATE INDEX delivery_ready ON deliveries(destination,status,next_at)')
    db.execute('CREATE TABLE counters(name TEXT PRIMARY KEY,value INTEGER)')
    db.execute('CREATE TABLE captures(path TEXT PRIMARY KEY,offset INTEGER,inode INTEGER)')
    if install:
        authority.initialize(db)
    db.commit()
    return db


@pytest.fixture
def store(tmp_path):
    db = create_database(tmp_path)
    try:
        yield tmp_path, db
    finally:
        db.close()


def insert(db, identity='row', session='a', workspace=WORKSPACE, data='{}', at=1):
    db.execute('INSERT INTO records(id,at,stream,session,workspace,event,data) VALUES(?,?,?,?,?,?,?)',
               (identity, at, 'tools', session, workspace, 'tool:post', data))


def token(home, identity='a', workspace=WORKSPACE):
    return authority.read_token(home, owner(identity, workspace))


@pytest.mark.parametrize('identity_fields,expected', [
    ({}, 'alias'), ({'runtimeSessionId': 'runtime'}, 'runtime'),
    ({'runtimeSessionId': 'runtime', 'nativeIdentity': 'native'}, 'native'),
])
def test_typed_token_and_canonical_identity(store, identity_fields, expected):
    home, db = store
    value = authority.read_token(home, owner('alias', **identity_fields))
    assert set(value) == {'kind', 'database', 'schema', 'epoch', 'workspace', 'session', 'revision'}
    assert value['kind'] == 'diagnostics-owner-v1'
    assert value['workspace'] == WORKSPACE and value['session'] == expected
    assert value['revision'] == 0
    assert len(value['epoch']) == 32 and bytes.fromhex(value['epoch'])
    assert len(value['schema']) == 64
    assert value['database'] == {'path': str(home / 'diagnostics' / 'events.sqlite3'),
        'device': (home / 'diagnostics' / 'events.sqlite3').stat().st_dev,
        'inode': (home / 'diagnostics' / 'events.sqlite3').stat().st_ino}
    assert json.loads(json.dumps(value)) == value
    before = value.copy()
    db.execute('BEGIN IMMEDIATE')
    authority.initialize(db)
    db.commit()
    assert authority.read_token(home, owner('alias', **identity_fields)) == before


@pytest.mark.parametrize('column,value', [
    ('seq', 91), ('id', 'changed-id'), ('at', 2), ('stream', 'usage'),
    ('session', 'b'), ('workspace', '/synthetic/other'),
    ('event', 'tool:error'), ('data', '{"changed":true}'),
])
def test_each_direct_record_update_advances_affected_owners_only(store, column, value):
    home, db = store
    insert(db)
    db.commit()
    original, unrelated = token(home), token(home, 'unrelated')
    next_owner = owner('b') if column == 'session' else owner(workspace='/synthetic/other')
    next_before = authority.read_token(home, next_owner)
    db.execute(f'UPDATE records SET {column}=? WHERE id=?', (value, 'row'))
    db.commit()
    assert token(home)['revision'] > original['revision']
    assert token(home, 'unrelated') == unrelated
    if column in {'session', 'workspace'}:
        assert authority.read_token(home, next_owner)['revision'] > next_before['revision']


def test_negative_dependency_insert_delete_and_tombstone(store):
    home, db = store
    absent = token(home)
    insert(db)
    db.commit()
    present = token(home)
    assert present['revision'] > absent['revision']
    db.execute('DELETE FROM records')
    db.commit()
    deleted = token(home)
    assert deleted['revision'] > present['revision']
    assert db.execute('SELECT revision FROM binding_owner WHERE workspace=? AND session=?',
                      (WORKSPACE, 'a')).fetchone()[0] == deleted['revision']


@pytest.mark.parametrize('change', ['delete-insert', 'owner-aba', 'data-aba'])
def test_aba_never_restores_old_authority(store, change):
    home, db = store
    insert(db)
    db.commit()
    before = token(home)
    if change == 'delete-insert':
        db.execute('DELETE FROM records WHERE id=?', ('row',))
        insert(db)
    elif change == 'owner-aba':
        db.execute("UPDATE records SET session='b'")
        db.execute("UPDATE records SET session='a'")
    else:
        db.execute("UPDATE records SET data='changed'")
        db.execute("UPDATE records SET data='{}'")
    db.commit()
    assert token(home)['revision'] > before['revision']
    assert token(home)['epoch'] == before['epoch']


def test_exact_noop_ignored_id_and_seq_duplicates_and_rollback_preserve(store):
    home, db = store
    insert(db)
    db.commit()
    before, other = token(home), token(home, 'b')
    stored = db.execute('SELECT * FROM records').fetchall()
    db.execute('UPDATE records SET seq=seq,id=id,at=at,stream=stream,session=session,workspace=workspace,event=event,data=data')
    db.execute("INSERT OR IGNORE INTO records(id,session,workspace) VALUES('row','b',?)", (WORKSPACE,))
    seq = stored[0][0]
    db.execute("INSERT OR IGNORE INTO records(seq,id,session,workspace) VALUES(?,'other','b',?)", (seq, WORKSPACE))
    db.commit()
    assert db.execute('SELECT * FROM records').fetchall() == stored
    assert token(home) == before and token(home, 'b') == other
    db.execute("UPDATE records SET data='rollback'")
    db.rollback()
    assert token(home) == before


@pytest.mark.parametrize('statement', [
    "INSERT OR IGNORE INTO records(id,session,workspace) VALUES('new','a',?)",
    "UPDATE OR IGNORE records SET data='changed' WHERE workspace=?",
    'DELETE FROM records WHERE workspace=?',
])
def test_overflow_explicitly_aborts_and_preserves_records(store, statement):
    home, db = store
    insert(db)
    db.execute('UPDATE binding_owner SET revision=?', (2**63 - 1,))
    db.commit()
    before = token(home)
    stored = db.execute('SELECT * FROM records').fetchall()
    with pytest.raises(sqlite3.IntegrityError, match='authority'):
        db.execute(statement, (WORKSPACE,))
    db.rollback()
    assert db.execute('SELECT * FROM records').fetchall() == stored
    assert token(home) == before
    # A duplicate is not a successful records mutation, even at the ceiling.
    db.execute("INSERT OR IGNORE INTO records(id,session,workspace) VALUES('row','a',?)", (WORKSPACE,))
    db.commit()
    assert token(home) == before


def test_multirow_overflow_aborts_whole_ignored_statement(store):
    home, db = store
    insert(db)
    insert(db, identity='last', session='last')
    db.execute("UPDATE binding_owner SET revision=? WHERE session='last'", (2**63 - 1,))
    db.commit()
    before, last = token(home), token(home, 'last')
    with pytest.raises(sqlite3.IntegrityError, match='authority'):
        db.execute("UPDATE OR IGNORE records SET data='changed'")
    # ABORT, not IGNORE: the earlier row and its authority increment roll back.
    db.commit()
    assert db.execute('SELECT data FROM records').fetchall() == [('{}',), ('{}',)]
    assert token(home) == before and token(home, 'last') == last


def test_ignored_key_changing_update_does_not_advance_either_owner(store):
    home, db = store
    insert(db)
    insert(db, identity='other', session='b')
    db.commit()
    before, other = token(home), token(home, 'b')
    db.execute("UPDATE OR IGNORE records SET id='other',session='b' WHERE id='row'")
    db.commit()
    assert token(home) == before and token(home, 'b') == other
    assert db.execute("SELECT session FROM records WHERE id='row'").fetchone()[0] == 'a'


@pytest.mark.parametrize('damage', [
    'DELETE FROM binding_meta',
    "UPDATE binding_meta SET epoch=X'00'",
    'UPDATE binding_meta SET schema=99',
    "UPDATE binding_owner SET revision='broken'",
    'UPDATE binding_owner SET revision=0',
    'DELETE FROM binding_owner',
])
def test_malformed_authority_aborts_even_ignored_duplicate_and_is_not_regenerated(store, damage):
    home, db = store
    insert(db)
    db.commit()
    db.execute('PRAGMA ignore_check_constraints=ON')
    db.execute(damage)
    db.commit()
    with pytest.raises((ValueError, sqlite3.Error)):
        token(home)
    db.execute('BEGIN IMMEDIATE')
    with pytest.raises((ValueError, sqlite3.Error)):
        authority.initialize(db)
    db.rollback()
    with pytest.raises(sqlite3.IntegrityError, match='authority'):
        db.execute("INSERT OR IGNORE INTO records(id,session,workspace) VALUES('row','a',?)", (WORKSPACE,))
    db.rollback()
    assert db.execute('SELECT count(*) FROM records').fetchone()[0] == 1


@pytest.mark.parametrize('workspace,session', [(None, 'a'), (WORKSPACE, None), ('', 'a'), (WORKSPACE, '')])
def test_unkeyable_legacy_owner_fails_install_atomically(tmp_path, workspace, session):
    with closing(create_database(tmp_path, install=False)) as db:
        insert(db, workspace=workspace, session=session)
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match='owner'):
            authority.initialize(db)
        # The helper's savepoint removes partial DDL without committing the caller.
        assert db.in_transaction
        assert not db.execute("SELECT name FROM sqlite_master WHERE name GLOB 'binding_*'").fetchall()
        assert db.execute('SELECT count(*) FROM records').fetchone()[0] == 1
        db.rollback()


def test_initialize_and_maintain_require_caller_transaction(store):
    _, db = store
    with pytest.raises(ValueError, match='transaction'):
        authority.initialize(db)
    with pytest.raises(ValueError, match='transaction'):
        authority.maintain(db, 100)


def test_install_and_reference_rewrite_share_caller_rollback(tmp_path):
    with closing(create_database(tmp_path, install=False)) as db:
        insert(db)
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        authority.initialize(db)
        db.execute("UPDATE records SET data='replacement'")
        assert db.execute('SELECT revision FROM binding_owner').fetchone()[0] > 1
        db.rollback()
        assert db.execute('SELECT data FROM records').fetchone()[0] == '{}'
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='binding_meta'").fetchone()


def test_tombstone_bound_rotates_before_pruning_and_preserves_live_owners(store):
    home, db = store
    insert(db)
    for index in range(6):
        insert(db, identity=f'dead-{index}', session=f'dead-{index}')
    db.execute("DELETE FROM records WHERE session != 'a'")
    db.commit()
    before = token(home)
    db.execute('BEGIN IMMEDIATE')
    authority.maintain(db, 2)
    db.commit()
    after = token(home)
    assert after['epoch'] != before['epoch'] and after['revision'] == before['revision']
    assert db.execute('SELECT workspace,session FROM binding_owner').fetchall() == [(WORKSPACE, 'a')]
    db.execute('BEGIN IMMEDIATE')
    authority.maintain(db, 2)
    db.commit()
    assert token(home) == after


@pytest.mark.parametrize('forget', ['sessions', 'workspace'])
def test_explicit_privacy_pruning_rotates_and_rolls_back_atomically(store, forget):
    home, db = store
    insert(db)
    insert(db, identity='kept', session='kept', workspace='/synthetic/kept')
    db.commit()
    before, kept = token(home), token(home, 'kept', '/synthetic/kept')
    options = {'forget_sessions': ('a',)} if forget == 'sessions' else {'forget_workspace': WORKSPACE}
    for rollback in (True, False):
        db.execute("DELETE FROM records WHERE session='a'")
        authority.maintain(db, 100, **options)
        assert not db.execute("SELECT 1 FROM binding_owner WHERE session='a'").fetchone()
        if rollback:
            db.rollback()
            assert token(home) == before
        else:
            db.commit()
    after = token(home)
    assert after['epoch'] != before['epoch'] and after['revision'] == 0
    assert token(home, 'kept', '/synthetic/kept')['revision'] == kept['revision']


def test_privacy_prune_refuses_still_live_owner(store):
    home, db = store
    insert(db)
    db.commit()
    before = token(home)
    db.execute('BEGIN IMMEDIATE')
    with pytest.raises(ValueError, match='live'):
        authority.maintain(db, 100, forget_sessions=('a',))
    db.rollback()
    assert token(home) == before


def test_real_legacy_initializer_installs_before_reference_updates(tmp_path, monkeypatch):
    from amplifier_web.diagnostics import Diagnostics
    from amplifier_web import session_files
    with closing(create_database(tmp_path, install=False)) as db:
        insert(db, data='{"event_id":"legacy"}')
        db.commit()
    monkeypatch.setattr(session_files, 'capture_dir', lambda *_: tmp_path / 'synthetic-capture')
    monkeypatch.setattr(session_files, 'event_index', lambda *_: {})
    monkeypatch.setattr(session_files, 'append_event', lambda *_: {'$event': 'synthetic', 'offset': 0})
    service = SimpleNamespace(data_dir=tmp_path, state={})
    diagnostics = Diagnostics(service)
    assert diagnostics.storage_ready and not diagnostics.storage_error
    assert token(tmp_path)['revision'] > 1  # seeded owner then legacy UPDATE
    with diagnostics._db() as db:
        assert json.loads(db.execute('SELECT data FROM records').fetchone()[0]) == {'$event': 'synthetic', 'offset': 0}


def test_legacy_failure_rolls_back_authority_install_and_rewrites(tmp_path, monkeypatch):
    from amplifier_web.diagnostics import Diagnostics
    from amplifier_web import session_files
    with closing(create_database(tmp_path, install=False)) as db:
        insert(db, identity='one')
        insert(db, identity='two')
        db.commit()
    count = 0
    def append(*_):
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError('synthetic migration failure')
        return {'$event': 'synthetic', 'offset': 0}
    monkeypatch.setattr(session_files, 'capture_dir', lambda *_: tmp_path / 'synthetic-capture')
    monkeypatch.setattr(session_files, 'event_index', lambda *_: {})
    monkeypatch.setattr(session_files, 'append_event', append)
    diagnostics = Diagnostics(SimpleNamespace(data_dir=tmp_path, state={}))
    assert diagnostics.storage_error and not diagnostics.storage_ready
    with closing(sqlite3.connect(diagnostics.path)) as db:
        assert db.execute('SELECT data FROM records').fetchall() == [('{}',), ('{}',)]
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='binding_meta'").fetchone()


@pytest.mark.parametrize('kind,name', [('INDEX', 'record_session'), ('TABLE', 'records')])
def test_real_initializer_does_not_repair_partial_authority_schema(store, kind, name):
    from amplifier_web.diagnostics import Diagnostics
    home, db = store
    db.execute(f'DROP {kind} {name}')
    db.commit()
    diagnostics = Diagnostics(SimpleNamespace(data_dir=home, state={}))
    assert diagnostics.storage_error and not diagnostics.storage_ready
    assert not db.execute('SELECT name FROM sqlite_master WHERE name=?', (name,)).fetchone()


def test_real_capture_duplicate_and_replacement_participate(store, monkeypatch):
    from amplifier_web.capture_index import index_shared
    from amplifier_web import capture_index
    home, db = store
    root = home / 'captures'
    directory = root / 'a' / 'context-intelligence'
    directory.mkdir(parents=True)
    path = directory / 'events.jsonl'
    line = json.dumps({'event': 'tool:post', 'timestamp': '2026-01-01T00:00:00Z', 'data': {'event_id': 'capture'}}) + '\n'
    path.write_text(line)
    monkeypatch.setattr(capture_index, 'capture_sessions_dir', lambda _: root)
    monkeypatch.setattr(capture_index, 'validate_capture', lambda _: None)
    config = {'streams': ['tools']}
    assert len(index_shared(db, [(WORKSPACE, 'a')], config)) == 1
    db.commit()
    before = token(home)
    # Revisit the same line without stale-index removal: actual INSERT OR IGNORE.
    db.execute('UPDATE captures SET offset=0')
    assert index_shared(db, [(WORKSPACE, 'a')], config) == []
    db.commit()
    assert token(home) == before
    replacement = directory / 'replacement'
    replacement.write_text(line)
    replacement.replace(path)
    assert len(index_shared(db, [(WORKSPACE, 'a')], config)) == 1
    db.commit()
    assert token(home)['revision'] > before['revision']


def test_real_retention_participates_and_maintains_bound(tmp_path):
    from amplifier_web.diagnostics import Diagnostics, DEFAULT
    diagnostics = Diagnostics(SimpleNamespace(data_dir=tmp_path, state={'sessions': []}))
    with diagnostics._db() as db:
        insert(db, at=0)
    before = token(tmp_path)
    diagnostics._persist([], copy.deepcopy(DEFAULT))
    assert token(tmp_path)['revision'] > before['revision']
    with diagnostics._db() as db:
        assert not db.execute('SELECT 1 FROM records').fetchone()


@pytest.mark.parametrize('preserve', [True, False])
def test_real_managed_deletion_prunes_metadata_in_records_transaction(store, preserve):
    from amplifier_web.managed_deletion import _cleanup_aux
    home, db = store
    insert(db)
    insert(db, identity='same-workspace', session='b')
    insert(db, identity='other', session='other', workspace='/synthetic/other')
    db.commit()
    before = token(home)
    _cleanup_aux(home, {'ids': ['a'], 'workspace': WORKSPACE, 'preserveWorkspace': preserve, 'paths': []})
    after = token(home)
    assert after['epoch'] != before['epoch'] and after['revision'] == 0
    assert not db.execute("SELECT 1 FROM binding_owner WHERE session='a'").fetchone()
    assert bool(db.execute("SELECT 1 FROM records WHERE session='b'").fetchone()) is preserve
    assert bool(db.execute("SELECT 1 FROM binding_owner WHERE session='b'").fetchone()) is preserve
    assert db.execute("SELECT 1 FROM records WHERE session='other'").fetchone()


def test_real_managed_deletion_rolls_back_record_and_authority_changes_on_failure(store, monkeypatch):
    from amplifier_web.managed_deletion import _cleanup_aux
    home, db = store
    insert(db)
    db.commit()
    before = token(home)
    real_maintain = authority.maintain
    def fail_after_prune(*args, **kwargs):
        real_maintain(*args, **kwargs)
        raise ValueError('synthetic cleanup failure')
    monkeypatch.setattr(authority, 'maintain', fail_after_prune)
    with pytest.raises(ValueError, match='synthetic'):
        _cleanup_aux(home, {'ids': ['a'], 'workspace': WORKSPACE, 'preserveWorkspace': True, 'paths': []})
    assert token(home) == before
    assert db.execute("SELECT 1 FROM records WHERE id='row'").fetchone()


def test_real_managed_deletion_uses_configured_tombstone_bound(store, monkeypatch):
    from amplifier_web.managed_deletion import _cleanup_aux
    home, db = store
    insert(db)
    db.commit()
    (home / 'diagnostics' / 'config.json').write_text(json.dumps({'maxRecords': 321}))
    seen = []
    real_maintain = authority.maintain
    def observe(connection, bound, **kwargs):
        seen.append(bound)
        return real_maintain(connection, bound, **kwargs)
    monkeypatch.setattr(authority, 'maintain', observe)
    _cleanup_aux(home, {'ids': ['a'], 'workspace': WORKSPACE, 'preserveWorkspace': True, 'paths': []})
    assert seen == [321]


def test_unrelated_writes_deliveries_offsets_and_wal_housekeeping_preserve(store):
    home, db = store
    insert(db)
    db.commit()
    before = token(home)
    insert(db, identity='other', session='other')
    db.execute("UPDATE records SET data='other' WHERE session='other'")
    db.execute("DELETE FROM records WHERE session='other'")
    db.execute("INSERT INTO deliveries(record_id,destination,status) VALUES('row','synthetic','pending')")
    db.execute("UPDATE deliveries SET status='accepted'")
    db.execute("INSERT INTO captures VALUES('synthetic',12,34)")
    db.execute('UPDATE captures SET offset=24')
    db.commit()
    assert token(home) == before
    assert db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    assert token(home) == before
    db.close()  # Last writer closure may remove WAL; the fixed DB is unchanged.
    assert token(home) == before


@pytest.mark.parametrize('damage', [
    'DROP TRIGGER binding_records_insert',
    'DROP INDEX binding_records_owner',
    'DROP TABLE binding_owner',
    "CREATE TRIGGER unexpected AFTER INSERT ON records BEGIN SELECT 1; END",
])
def test_schema_loss_or_extra_trigger_refuses_without_reinstall(store, damage):
    home, db = store
    db.execute(damage)
    db.commit()
    with pytest.raises((ValueError, sqlite3.Error)):
        token(home)
    db.execute('BEGIN IMMEDIATE')
    with pytest.raises((ValueError, sqlite3.Error)):
        authority.initialize(db)
    db.rollback()


def test_epoch_rotation_rejects_existing_snapshot(store):
    home, db = store
    with pytest.raises(ValueError, match='changed'):
        with authority.authority_snapshot(home, owner()) as (reader, before):
            assert reader.execute('SELECT count(*) FROM records').fetchone()[0] == 0
            db.execute('UPDATE binding_meta SET epoch=randomblob(16)')
            db.commit()
            assert before['epoch'] != db.execute('SELECT hex(epoch) FROM binding_meta').fetchone()[0].lower()


@pytest.mark.parametrize('same_owner', [True, False])
def test_snapshot_never_adopts_rows_under_a_later_token(store, same_owner):
    home, db = store
    insert(db)
    db.commit()
    observed = []
    def read():
        with authority.authority_snapshot(home, owner()) as (reader, stamp):
            observed.append((stamp, reader.execute('SELECT data FROM records WHERE id=?', ('row',)).fetchone()[0]))
            if same_owner:
                db.execute("UPDATE records SET data='revoked' WHERE id='row'")
            else:
                insert(db, identity='other', session='other')
            db.commit()
            assert reader.execute('SELECT data FROM records WHERE id=?', ('row',)).fetchone()[0] == '{}'
    if same_owner:
        with pytest.raises(ValueError, match='changed'):
            read()
        assert observed[0][0] != token(home)
    else:
        read()
        assert observed[0][0] == token(home)
    assert observed[0][1] == '{}'


def test_commit_before_authority_query_uses_new_rows_and_new_token(store, monkeypatch):
    home, db = store
    insert(db)
    db.commit()
    before = token(home)
    real_token = authority._token
    changed = []
    def change_before_read(*args):
        if not changed:
            changed.append(True)
            db.execute("UPDATE records SET data='new'")
            db.commit()
        return real_token(*args)
    monkeypatch.setattr(authority, '_token', change_before_read)
    with authority.authority_snapshot(home, owner()) as (reader, after):
        assert after != before
        assert reader.execute("SELECT data FROM records WHERE id='row'").fetchone()[0] == 'new'
    assert after == token(home)


def test_commit_during_authority_query_cannot_mix_epoch_revision_and_rows(store, monkeypatch):
    home, db = store
    insert(db)
    db.commit()
    before = token(home)
    real_connect = sqlite3.connect
    changed = []
    class QueryMutation(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith('SELECT revision FROM binding_owner') and not changed:
                changed.append(True)
                db.execute("UPDATE records SET data='new'")
                db.commit()
            return super().execute(sql, *args)
    monkeypatch.setattr(authority.sqlite3, 'connect', lambda *a, **kw: real_connect(*a, **kw, factory=QueryMutation))
    with pytest.raises(ValueError, match='changed'):
        with authority.authority_snapshot(home, owner()) as (reader, original):
            assert original == before
            assert reader.execute("SELECT data FROM records WHERE id='row'").fetchone()[0] == '{}'
    assert changed == [True]


def test_commit_after_snapshot_connection_close_is_seen_by_fresh_check(store, monkeypatch):
    home, db = store
    insert(db)
    db.commit()
    real_connect = sqlite3.connect
    closed = []
    class CloseMutation(sqlite3.Connection):
        def close(self):
            super().close()
            if not closed:
                closed.append(True)
                db.execute("UPDATE records SET data='after-close'")
                db.commit()
    monkeypatch.setattr(authority.sqlite3, 'connect', lambda *a, **kw: real_connect(*a, **kw, factory=CloseMutation))
    with pytest.raises(ValueError, match='changed'):
        with authority.authority_snapshot(home, owner()) as (reader, stamp):
            assert reader.execute('SELECT data FROM records').fetchone()[0] == '{}'
    assert closed == [True]


def test_commit_after_final_check_is_detected_on_next_request(store):
    home, db = store
    with authority.authority_snapshot(home, owner()) as (_, before):
        pass
    insert(db)
    db.commit()
    assert token(home) != before


def test_replacement_with_identical_metadata_refuses_live_snapshot(store, tmp_path):
    home, db = store
    insert(db)
    db.commit()
    copied = tmp_path / 'copy.sqlite3'
    with closing(sqlite3.connect(copied)) as destination:
        db.backup(destination)
    path = home / 'diagnostics' / 'events.sqlite3'
    with pytest.raises((ValueError, OSError, sqlite3.Error)):
        with authority.authority_snapshot(home, owner()) as (reader, before):
            assert reader.execute('SELECT data FROM records').fetchone()[0] == '{}'
            copied.replace(path)
            assert path.stat().st_ino != before['database']['inode']


@pytest.mark.parametrize('target', ['database', 'wal'])
@pytest.mark.parametrize('kind', ['symlink', 'directory'])
def test_symlink_and_unknown_file_kind_refuse(store, target, kind):
    home, db = store
    path = home / 'diagnostics' / ('events.sqlite3' if target == 'database' else 'events.sqlite3-wal')
    db.close()
    if path.exists():
        path.rename(path.with_name(path.name + '.saved'))
    if kind == 'symlink':
        path.symlink_to(home / 'missing')
    else:
        path.mkdir()
    with pytest.raises((ValueError, OSError, sqlite3.Error)):
        token(home)


def test_guard_and_sql_work_budget_are_enforced(store):
    home, _ = store
    calls = []
    def guard():
        calls.append(True)
        raise ValueError('synthetic budget exhausted')
    with pytest.raises(ValueError, match='budget'):
        authority.read_token(home, owner(), guard)
    assert calls
    with pytest.raises(sqlite3.OperationalError, match='interrupted'):
        with authority.authority_snapshot(home, owner()) as (reader, _):
            reader.execute('WITH RECURSIVE r(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM r WHERE n<1000000) SELECT sum(n) FROM r').fetchone()
