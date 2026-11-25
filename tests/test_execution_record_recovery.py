"""Execution pages use the existing durable roots and transaction recovery."""
import json
import hashlib
import sqlite3

import pytest

from amplifier_web import execution_records as records, resource_files, state_records


OWNER = ('chat', 'native', '/synthetic')


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / 'state.sqlite3')
    connection.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    connection.execute('CREATE TABLE state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
    state_records.initialize(connection)
    connection.commit()
    yield connection
    connection.close()


def value():
    return {'turns': [], 'retiredUsageNodes': [], 'nodes': [
        {'id': str(i), 'sessionId': 'native', 'phase': 'completed', 'revision': 1,
         'detail': {'text': 'saved tool result ' + str(i)}} for i in range(400)]}


def save(db, reference, *, retained=None):
    state = {'revision': 1, 'runtimeControl': {}, 'sessions': [
        {'id': 'chat', '_coldFields': {'execution': reference}}]}
    if retained is not None:
        state['retainedView'] = retained
    state_records.checkpoint(db, state)


def collect(db):
    # Empty live state intentionally exercises the collector's durable owners,
    # including disconnected clients and operations not loaded into memory.
    removed = resource_files.collect(db, {})
    db.commit()
    resource_files.remove_files(db, removed)
    return removed


def first_body_page(db, reference):
    selected = records._root(db, reference)['collections']['nodes']['order']
    page = records._read(db, selected)
    while 'children' in page:
        selected = page['children'][min(page['children'], key=int)]
        page = records._read(db, selected)
    assert page['order'][0][1]['value']['detail'] == {'text': 'saved tool result 0'}
    return selected['$resource']


@pytest.mark.parametrize('carrier', [
    'state', 'client_views', 'smart_tool_operations', 'conversation_shares', 'output_records'])
def test_actual_collector_keeps_old_view_until_its_durable_owner_releases_it(db, carrier):
    original = value()
    first = records.import_tree(db, OWNER, original)
    old_body = first_body_page(db, first)
    save(db, first)
    db.commit()

    changed = {**original['nodes'][73], 'revision': 2, 'detail': {'text': 'late correction'}}
    second = records.replace_records(db, first, {'nodes': {records.key('nodes', changed): changed}})
    assert first_body_page(db, second) != old_body
    garbage = resource_files.put(db, {'unowned': True})
    if carrier == 'state':
        save(db, second, retained=first)
    else:
        db.execute('CREATE TABLE ' + carrier + ' (value TEXT NOT NULL)')
        db.execute('INSERT INTO ' + carrier + ' VALUES (?)', (json.dumps({'view': first}),))
        save(db, second)
    db.commit()

    removed = collect(db)
    assert garbage['$resource'] in removed
    assert first['$resource'] not in removed and second['$resource'] not in removed
    assert old_body not in removed
    assert records.materialize(db, first, owner=OWNER) == original
    assert records.read_record(db, second, 'nodes', records.key('nodes', changed), owner=OWNER) == changed

    if carrier == 'state':
        save(db, second)
    else:
        db.execute('DELETE FROM ' + carrier)
    db.commit()
    removed = collect(db)
    assert first['$resource'] in removed
    assert old_body in removed
    with pytest.raises(ValueError, match='unavailable'):
        records.materialize(db, first)
    assert records.read_record(db, second, 'nodes', records.key('nodes', changed)) == changed


def test_failed_publication_restart_and_collection_keep_the_last_committed_graph(db):
    original = value()
    first = records.import_tree(db, OWNER, original)
    save(db, first)
    db.commit()
    changed = {**original['nodes'][73], 'revision': 2}
    staged = records.replace_records(db, first, {'nodes': {records.key('nodes', changed): changed}})
    save(db, staged)
    db.rollback()  # The owner failed before publishing its new durable root.

    path = next(row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main')
    with sqlite3.connect(path) as restarted:
        selected = state_records.load(restarted)['sessions'][0]['_coldFields']['execution']
        assert selected == first
        collect(restarted)
        assert records.materialize(restarted, selected, owner=OWNER) == original
        with pytest.raises(ValueError, match='unavailable'):
            records.materialize(restarted, staged)

        retry = records.replace_records(restarted, selected, {'nodes': {records.key('nodes', changed): changed}})
        save(restarted, retry)
        restarted.commit()
        assert records.read_record(restarted, retry, 'nodes', records.key('nodes', changed)) == changed


def test_root_index_preserves_canonical_bytes_and_recovery_encoding(db):
    original = value()
    reference = records.import_tree(db, OWNER, original)
    save(db, reference)
    db.commit()
    identity = reference['$resource']
    source = resource_files.detach(db, identity)
    assert json.loads(source.indexed) == {'$blob': 'execution-root/1:' + identity}
    path = resource_files.root(db) / (identity + '.json')
    encoded = path.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == identity
    root = resource_files.read_detached(source)
    assert resource_files.prepare(root).encoded == encoded
    assert resource_files.prepare(root).execution_root
    assert resource_files.detached_signature(source)[2] == len(encoded)

    # Missing-index recovery must not silently restore an old-reader-compatible
    # pointer. The resource and every descendant keep their exact identities.
    db.execute('DELETE FROM state_resources WHERE id=?', (identity,))
    db.commit()
    assert resource_files.restore(db, {}, identity, root) == {'id': identity, 'restored': True}
    db.commit()
    assert resource_files.detach(db, identity).indexed == source.indexed
    assert path.read_bytes() == encoded
    collect(db)
    assert records.materialize(db, reference) == original


async def test_prepared_and_async_root_readoption_keep_the_format_guard(db):
    reference = records.import_tree(db, OWNER, value())
    source = resource_files.detach(db, reference['$resource'])
    root = resource_files.read_detached(source)
    prepared = resource_files.prepare(root)
    assert resource_files.put_prepared(db, prepared) == {'$resource': prepared.identity, 'bytes': prepared.size}
    assert await resource_files.put_prepared_async(db, prepared) == resource_files.put(db, root)
    assert await resource_files.put_async(db, root) == resource_files.put(db, root)
    assert resource_files.detach(db, prepared.identity).indexed == source.indexed

    equivalent = json.dumps(json.loads(source.indexed), indent=2)
    db.execute('UPDATE state_resources SET value=? WHERE id=?', (equivalent, prepared.identity))
    resource_files.put_prepared(db, prepared)
    assert resource_files.detach(db, prepared.identity).indexed == equivalent

    # An existing unknown or untagged index is not overwritten by re-adoption.
    conflicting = json.dumps({'$blob': prepared.identity})
    db.execute('UPDATE state_resources SET value=? WHERE id=?', (conflicting, prepared.identity))
    with pytest.raises(ValueError, match='required format'):
        resource_files.put_prepared(db, prepared)
    assert resource_files.detach(db, prepared.identity).indexed == conflicting


def test_generic_custom_dictionary_preparation_does_not_add_a_get_hook():
    class Custom(dict):
        def get(self, *args):
            raise AssertionError('Preparing a generic artifact must not call an extra custom hook')
    prepared = resource_files.prepare(Custom(format=records.FORMAT, custom=True))
    assert not prepared.execution_root
    assert json.loads(prepared.encoded) == {'format': records.FORMAT, 'custom': True}

    class Format(str):
        def __eq__(self, other):
            raise AssertionError('Generic format values must not invoke custom equality')
    prepared = resource_files.prepare({'format': Format(records.FORMAT)})
    assert not prepared.execution_root
    assert json.loads(prepared.encoded) == {'format': records.FORMAT}


def test_unsupported_root_index_refuses_reads_and_collection_without_rewriting(db):
    reference = records.import_tree(db, OWNER, value())
    save(db, reference)
    db.commit()
    identity = reference['$resource']
    changed = json.dumps({'$blob': 'execution-root/2:' + identity})
    db.execute('UPDATE state_resources SET value=? WHERE id=?', (changed, identity))
    db.commit()
    before = db.execute('SELECT id,value FROM state_resources ORDER BY id').fetchall()
    with pytest.raises(ValueError, match='Invalid artifact index'):
        records.materialize(db, reference)
    assert resource_files.marked_references(db, {}) is None
    assert collect(db) == []
    assert db.execute('SELECT id,value FROM state_resources ORDER BY id').fetchall() == before
