"""Committed execution reuse still witnesses content and validates storage."""
import copy
import json
import sqlite3

import pytest

from amplifier_web import execution_preparation, resource_files
from amplifier_web.execution_preparation import ExecutionPreparation


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / 'state.sqlite3')
    connection.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    connection.commit()
    yield connection
    connection.close()


def owner(identity='one'):
    return {'id': identity, 'runtimeSessionId': identity, 'workspace': '/synthetic',
            'execution': {'nodes': [{'id': str(i), 'kind': 'tool', 'phase': 'completed',
                'nested': [None, True, 1, 1.0, -0.0], 'text': 'é' * 500} for i in range(260)],
                'retiredUsageNodes': [], 'turns': []}}


def save(cache, db, row, owners=None):
    pending = {}
    result = cache.reference(db, row, row['execution'], pending)
    db.commit()
    cache.committed(pending, owners or {row['id']: row})
    return result


def test_two_owners_reuse_only_after_every_fresh_witness(db, monkeypatch):
    cache = ExecutionPreparation()
    rows = {name: owner(name) for name in ('one', 'two')}
    refs = {name: save(cache, db, row, rows) for name, row in rows.items()}
    count = []
    witness = execution_preparation._witness
    monkeypatch.setattr(execution_preparation, '_witness', lambda value: (count.append(1), witness(value))[1])
    with monkeypatch.context() as patch:
        patch.setattr(resource_files.hashlib, 'sha256', lambda *a, **k: pytest.fail('Unchanged whole resource rehashed'))
        for name, row in rows.items():
            result = save(cache, db, row, rows)
            assert result == refs[name]
            result['bytes'] = 0  # Returned dict is not retained evidence.
    assert len(count) == 10  # Three node chunks plus two other complete fields per owner.
    assert cache.bytes + cache._reserved({}) <= cache.MAX_BYTES


@pytest.mark.parametrize('change', ['alias', 'typed', 'field', 'remove-field', 'remove-chunk', 'order', 'custom'])
def test_mutations_and_unsupported_fragments_do_not_reuse(db, change):
    cache, row = ExecutionPreparation(), owner()
    first = save(cache, db, row)
    previous = cache.references[row['id']]
    tree = row['execution']
    if change == 'alias': tree['nodes'][-1]['nested'][0] = 'last chunk'
    elif change == 'typed': tree['nodes'][128]['nested'][1] = 1
    elif change == 'field': tree['extra'] = {'x': 1}
    elif change == 'remove-field': del tree['turns']
    elif change == 'remove-chunk': del tree['nodes'][128:]
    elif change == 'order': tree['nodes'].reverse()
    else:
        class Custom(list):
            pass
        tree['turns'] = Custom()
    result = save(cache, db, row)
    assert result['$resource'] == resource_files.prepare(tree).identity
    if change == 'custom':
        assert result == first and row['id'] not in cache.references
    else:
        assert result != first and cache.references[row['id']] is not previous


@pytest.mark.parametrize('change', ['missing-file', 'missing-index', 'replaced-valid-file'])
def test_lost_or_replaced_storage_requires_revalidation(db, change, monkeypatch):
    cache, row = ExecutionPreparation(), owner()
    first = save(cache, db, row)
    previous = cache.references[row['id']]
    path = resource_files.root(db) / (first['$resource'] + '.json')
    if change == 'missing-file': path.unlink()
    elif change == 'missing-index':
        db.execute('DELETE FROM state_resources WHERE id=?', (first['$resource'],))
        db.commit()
    else:
        text = path.read_bytes()
        path.unlink()
        path.write_bytes(text)
    validations = []
    validate = resource_files.project_detached
    monkeypatch.setattr(resource_files, 'project_detached', lambda *a, **k: (validations.append(1), validate(*a, **k))[1])
    assert save(cache, db, row) == first
    assert len(validations) == 1 and cache.references[row['id']] is not previous
    assert resource_files.read_detached(resource_files.detach(db, first['$resource'])) == row['execution']


@pytest.mark.parametrize('change', ['same-size-corruption', 'wrong-size', 'symlink', 'changed-index'])
def test_storage_conflicts_never_become_successful_cached_saves(db, change, tmp_path):
    cache, row = ExecutionPreparation(), owner()
    first = save(cache, db, row)
    previous = cache.references[row['id']]
    path = resource_files.root(db) / (first['$resource'] + '.json')
    if change == 'same-size-corruption':
        text = path.read_bytes(); path.write_bytes(text.replace(b'completed', b'corrupted', 1))
    elif change == 'wrong-size': path.write_bytes(b'{}')
    elif change == 'symlink':
        target = tmp_path / 'outside'; target.write_bytes(path.read_bytes())
        path.unlink(); path.symlink_to(target)
    else:
        db.execute('UPDATE state_resources SET value=? WHERE id=?', ('{"$blob":"' + 'f' * 64 + '"}', first['$resource']))
        db.commit()
    with pytest.raises((ValueError, OSError)):
        cache.reference(db, row, row['execution'], {})
    db.rollback()
    assert cache.references[row['id']] is previous
    assert path.exists()  # Preserve conflicting evidence.


def test_preexisting_same_size_corruption_cannot_seed_reference(db):
    cache, row = ExecutionPreparation(), owner()
    prepared = resource_files.prepare(row['execution'])
    path = resource_files.root(db) / (prepared.identity + '.json')
    path.parent.mkdir()
    path.write_bytes(b'x' * prepared.size)
    pending = {}
    with pytest.raises(ValueError, match='content conflicts'):
        cache.reference(db, row, row['execution'], pending)
    db.rollback()
    assert not cache.references and pending[row['id']] is None
    assert path.read_bytes() == b'x' * prepared.size


def test_failed_save_retry_owner_remap_and_cold_invalidation(db):
    cache, row = ExecutionPreparation(), owner()
    first = save(cache, db, row)
    original = cache.references[row['id']]
    row['execution']['nodes'][-1]['nested'][0] = 'staged'
    pending = {}
    staged = cache.reference(db, row, row['execution'], pending)
    assert pending[row['id']] is not original
    db.rollback()  # Resource staging succeeded; the owner pointer did not commit.
    assert cache.references[row['id']] is original
    row['execution']['nodes'][-1]['nested'][0] = 'retry'
    result = save(cache, db, row)
    assert result != first and result != staged
    assert resource_files.read_detached(resource_files.detach(db, result['$resource'])) == row['execution']
    prior = cache.references[row['id']]
    row = copy.deepcopy(row)
    assert save(cache, db, row) == result
    assert cache.references[row['id']] is not prior
    prior = cache.references[row['id']]
    row['runtimeSessionId'] = 'remapped'
    assert save(cache, db, row) == result and cache.references[row['id']] is not prior
    prior = cache.references[row['id']]
    row['workspace'] = '/another-workspace'
    assert save(cache, db, row) == result and cache.references[row['id']] is not prior
    cache.committed({row['id']: None}, {row['id']: row})
    assert not cache.references
    save(cache, db, row)
    cache.retain(())
    assert not cache.references and not cache.entries and not cache.bytes


@pytest.mark.parametrize('deferred', [False, True])
def test_reused_reference_keeps_writer_exclusion_until_pointer_settlement(db, deferred):
    cache, row = ExecutionPreparation(), owner()
    save(cache, db, row)
    path = next(r[2] for r in db.execute('PRAGMA database_list') if r[1] == 'main')
    other = sqlite3.connect(path, timeout=0)
    try:
        if deferred:
            db.execute('BEGIN')
            db.execute('SELECT id FROM state_resources').fetchall()
        pending = {}
        cache.reference(db, row, row['execution'], pending)
        assert db.in_transaction
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            other.execute('BEGIN IMMEDIATE')
        db.rollback()  # The canonical owner decides whether its pointer commits.
        other.execute('BEGIN IMMEDIATE')
        other.rollback()
    finally:
        other.close()


def test_inline_and_saturated_cache_preserve_original_storage(db):
    cache, row = ExecutionPreparation(), owner()
    cache.MAX_BYTES = 16000
    rows, pending = {}, {}
    for index in range(8):
        current = owner(str(index)); rows[current['id']] = current
        result = cache.reference(db, current, current['execution'], pending)
        assert result['$resource'] == resource_files.prepare(current['execution']).identity
        assert cache.bytes + cache._reserved(pending) <= cache.MAX_BYTES
    db.commit(); cache.committed(pending, rows)
    assert cache.bytes + cache._reserved({}) <= cache.MAX_BYTES
    memory = sqlite3.connect(':memory:')
    try:
        memory.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
        memory.commit()
        cache = ExecutionPreparation()
        result = save(cache, memory, row)
        assert not cache.references
        assert resource_files.read_detached(resource_files.detach(memory, result['$resource'])) == row['execution']
    finally:
        memory.close()


def test_committed_and_provisional_reference_metadata_share_fragment_budget(db):
    cache = ExecutionPreparation()
    cache.MAX_BYTES = 700_000
    rows = {name: owner(name) for name in ('one', 'two')}
    for row in rows.values(): save(cache, db, row, rows)
    assert len(cache.references) == 2 and cache.entries
    committed = dict(cache.references)
    original_keys = set(cache.entries)
    pending = {}
    for name in ('one', 'three', 'four', 'five'):
        row = rows.setdefault(name, owner(name))
        row['execution']['nodes'][-1]['nested'][0] = 'provisional replacement'
        cache.reference(db, row, row['execution'], pending)
        assert any(entry is not None for entry in pending.values())
        assert cache.references == committed
        assert cache.bytes + cache._reserved(pending) <= cache.MAX_BYTES
    assert not original_keys <= set(cache.entries)
    db.commit(); cache.committed(pending, rows)
    assert cache.bytes + cache._reserved({}) <= cache.MAX_BYTES


@pytest.mark.parametrize('change', ['replacement', 'runtime', 'workspace', 'cold', 'native', 'deleted'])
def test_staged_entry_cannot_be_promoted_for_an_invalidated_owner(db, change):
    cache, row = ExecutionPreparation(), owner()
    pending = {}
    resource = cache.reference(db, row, row['execution'], pending)
    assert pending[row['id']] is not None
    deleted = ()
    if change == 'replacement': row = copy.deepcopy(row)
    elif change == 'runtime': row['runtimeSessionId'] = 'replacement'
    elif change == 'workspace': row['workspace'] = '/replacement'
    elif change == 'cold': row['_coldFields'] = {'execution': resource}
    elif change == 'native': row['historyManaged'] = True
    else: deleted = {row['id']}
    db.commit(); cache.committed(pending, {row['id']: row}, deleted)
    assert not cache.references


@pytest.mark.parametrize('failure', ['pointer', 'preexisting-corrupt-file'])
async def test_failed_owner_pointer_commit_preserves_previous_reference_and_retry(tmp_path, monkeypatch, failure):
    from amplifier_web.service import AppService
    from amplifier_web.storage_migration import close_maintenance
    from amplifier_web import state_records
    from amplifier_web.cold_display import load, MARKER
    from test_automatic_history import ObservedRuntime
    app = AppService(tmp_path / 'app', ObservedRuntime(), workspace=tmp_path)
    try:
        for task in (app.history, app.event_log_view, app.diagnostics, app.schedules, app.observations):
            await task.close()
        row = app._new_session({'workspace': str(tmp_path)})
        row['execution'] = owner()['execution']
        row['execution']['retiredUsageNodes'] = [
            {'id': str(i), 'sessionId': row['id'], 'producerId': 'prior', 'kind': 'llm',
             'phase': 'completed', 'revision': 1, 'usage': {'inputTokens': i}} for i in range(130)]
        app._state['sessions'].append(row)
        if failure == 'preexisting-corrupt-file':
            from amplifier_web.session_projection import stored_execution
            prepared = resource_files.prepare(stored_execution(row['execution']))
            path = resource_files.root(app.db) / (prepared.identity + '.json')
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'x' * prepared.size)
            with pytest.raises(ValueError, match='content conflicts'):
                app._publish_changes(sessions={row['id']})
            assert row['id'] not in app._execution_preparation.references
            assert row['id'] not in {s['id'] for s in state_records.load(app.db)['sessions']}
            assert path.read_bytes() == b'x' * prepared.size
            assert not app.db.in_transaction
            # The rejected fixture was never saved. Remove only this synthetic
            # resident row so normal shutdown does not retry the intentional
            # corruption; leave its conflicting file intact for the assertions.
            app._state['sessions'].remove(row)
            return
        app._publish_changes(sessions={row['id']})
        await close_maintenance(app)
        previous = app._execution_preparation.references[row['id']]
        alias = row['execution']['nodes'][-1]['nested']
        alias[0] = 'staged before pointer failure'
        original = state_records.save_working
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('Synthetic owner pointer rejection')
        with monkeypatch.context() as patch:
            patch.setattr(state_records, 'save_working', fail)
            with pytest.raises(RuntimeError, match='pointer rejection'):
                app._publish_changes(sessions={row['id']})
        assert app._execution_preparation.references[row['id']] is previous
        alias[0] = 'new retry value'
        app._publish_changes(sessions={row['id']})
        saved = next(s for s in state_records.load(app.db)['sessions'] if s['id'] == row['id'])
        view = load(app.db, saved['$viewPayload'])
        assert load(app.db, view[MARKER]['execution'])['nodes'][-1]['nested'][0] == 'new retry value'
        assert app._execution_preparation.references[row['id']] is not previous
    finally:
        await close_maintenance(app)
        await app.close()
