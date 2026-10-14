"""One-owner legacy migration never commits another operation's transaction."""
import copy

import pytest

from amplifier_web.resource_files import put, root
from amplifier_web.state_records import load_owner
from test_cold_execution_compaction import app, populate, tree


async def dormant_legacy(app):
    row = await populate(app)
    identity = row['id']
    old = put(app.db, tree(identity))
    dict.pop(row, 'execution')
    row['_coldFields'] = {'execution': old}
    app._save()
    app._state['sessions'].remove(row)
    app._session_projection_refs.pop(identity, None)
    app.projections.invalidate(state=app._state)
    return identity, old


async def test_admission_defers_legacy_compaction_inside_existing_writer(app):
    identity, old = await dormant_legacy(app)
    before = copy.deepcopy(load_owner(app.db, identity))
    app.db.execute('CREATE TABLE admission_test_pending(value TEXT)')
    app.db.commit()
    app.db.execute("INSERT INTO admission_test_pending VALUES('not committed')")
    admitted = app._session(identity, hydrate=False)
    # Existing lifecycle normalization may read the old tree. It must not
    # publish a compacted pointer or commit this caller's unrelated write.
    assert admitted['id'] == identity
    assert load_owner(app.db, identity) == before
    assert app.db.in_transaction
    app.db.rollback()
    assert app.db.execute('SELECT count(*) FROM admission_test_pending').fetchone()[0] == 0
    assert (root(app.db) / (old['$resource'] + '.json')).is_file()


async def test_failed_admission_compaction_retains_committed_owner_and_retries(app, monkeypatch):
    identity, old = await dormant_legacy(app)
    before = copy.deepcopy(load_owner(app.db, identity))
    save = app._save
    def fail(**kwargs):
        raise RuntimeError('Injected migration commit failure')
    monkeypatch.setattr(app, '_save', fail)
    with pytest.raises(RuntimeError, match='migration commit failure'):
        app._session(identity, hydrate=False)
    assert not app.db.in_transaction
    assert identity not in {row['id'] for row in app._state['sessions']}
    assert load_owner(app.db, identity) == before
    assert (root(app.db) / (old['$resource'] + '.json')).is_file()
    monkeypatch.setattr(app, '_save', save)
    admitted = app._session(identity, hydrate=False)
    assert dict.get(admitted, '_coldFields')['execution']['executionProjection'] == 1
    assert not app.db.in_transaction
