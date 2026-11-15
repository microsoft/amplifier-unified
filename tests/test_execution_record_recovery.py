"""Execution pages use the existing durable roots and transaction recovery."""
import json
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


@pytest.mark.parametrize('carrier', [
    'state', 'client_views', 'smart_tool_operations', 'conversation_shares', 'output_records'])
def test_actual_collector_keeps_old_view_until_its_durable_owner_releases_it(db, carrier):
    original = value()
    first = records.import_tree(db, OWNER, original)
    save(db, first)
    db.commit()

    changed = {**original['nodes'][73], 'revision': 2, 'detail': {'text': 'late correction'}}
    second = records.replace_records(db, first, {'nodes': {records.key('nodes', changed): changed}})
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
    assert records.materialize(db, first, owner=OWNER) == original
    assert records.read_record(db, second, 'nodes', records.key('nodes', changed), owner=OWNER) == changed

    if carrier == 'state':
        save(db, second)
    else:
        db.execute('DELETE FROM ' + carrier)
    db.commit()
    removed = collect(db)
    assert first['$resource'] in removed
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
