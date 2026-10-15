"""Reject optional cache misses before materializing the old Work graph."""
import copy
import json

import pytest

from amplifier_web import history_read_model as models
from amplifier_web.event_log_view import EventLogView
from test_event_log_view import source
from test_history_read_model import family, inspect, model


def test_changed_input_after_restart_reads_receipt_before_any_tree_rows(source, tmp_path, monkeypatch):
    session, path = source
    family(session, path, 4)
    home = tmp_path / 'host'
    first = models.SelectedHistoryReadModel(home)
    inspect(first, session)
    first.close()
    session['messages'].append({'id': 'later', 'role': 'user', 'createdAt': 40})
    expected = EventLogView(None).read(copy.deepcopy(EventLogView.projection_input(session)))
    second = models.SelectedHistoryReadModel(home)
    queries = []
    second.store.db.set_trace_callback(queries.append)
    read = second.reader.read

    def canonical(inputs):
        assert queries, 'cache receipt was not checked'
        assert not any('body' in query.lower() or 'from nodes' in query.lower() for query in queries)
        assert session['id'] not in second.models
        second.store.db.set_trace_callback(None)
        return read(inputs)

    monkeypatch.setattr(second.reader, 'read', canonical)
    try:
        result = inspect(second, session)
        assert result['diagnostics']['counters']['events.model.rebuilt'] == 1
        assert session['execution'] == expected
    finally:
        second.close()


def test_changed_input_releases_only_its_retained_model_before_source_read(source, model, monkeypatch):
    session, path = source
    family(session, path, 2)
    inspect(model, session)
    unrelated = {**session, 'id': 'other-requested-chat'}
    inspect(model, unrelated)
    unrelated_charge = model.models[unrelated['id']]['bytes']
    session['messages'].append({'id': 'later', 'role': 'user', 'createdAt': 40})
    expected = EventLogView(None).read(copy.deepcopy(EventLogView.projection_input(session)))
    read = model.reader.read

    def canonical(inputs):
        assert set(model.models) == {unrelated['id']}
        assert model.model_bytes == unrelated_charge
        for name in ('read_paths', 'read_revisions', 'read_proofs'):
            assert session['id'] not in getattr(model.reader, name)
        return read(inputs)

    monkeypatch.setattr(model.reader, 'read', canonical)
    result = inspect(model, session)
    assert result['diagnostics']['counters']['events.model.rebuilt'] == 1
    assert session['execution'] == expected
    assert unrelated['id'] in model.models


def test_oversized_receipt_is_rejected_before_json_or_tree_reads(source, tmp_path, monkeypatch):
    session, path = source
    family(session, path, 2)
    home = tmp_path / 'host'
    first = models.SelectedHistoryReadModel(home)
    inspect(first, session)
    receipt = json.loads(first.store.db.execute('SELECT receipt FROM models').fetchone()[0])
    receipt['unused'] = 'x' * (1024 * 1024)
    with first.store.db:
        first.store.db.execute('UPDATE models SET receipt=?', (json.dumps(receipt),))
    first.close()
    expected = copy.deepcopy(session['execution'])
    second = models.SelectedHistoryReadModel(home)
    queries = []
    second.store.db.set_trace_callback(queries.append)
    loads = models.json.loads
    read = second.reader.read

    def bounded_load(value, *args, **kwargs):
        assert not isinstance(value, str) or len(value) < 1024 * 1024, 'oversized cache receipt decoded'
        return loads(value, *args, **kwargs)

    def canonical(inputs):
        assert not any('body' in query.lower() or 'select receipt ' in query.lower() for query in queries)
        second.store.db.set_trace_callback(None)
        return read(inputs)

    monkeypatch.setattr(models.json, 'loads', bounded_load)
    monkeypatch.setattr(second.reader, 'read', canonical)
    try:
        result = inspect(second, session)
        assert result['diagnostics']['counters']['events.model.rebuilt'] == 1
        assert session['execution'] == expected
    finally:
        second.close()


@pytest.mark.parametrize('update', [{}, {'source_only': True}, {'positions': [0]}])
def test_receipt_write_cap_keeps_oversized_metadata_out_of_disk_cache(source, model, monkeypatch, update):
    session, path = source
    family(session, path, 2)
    inspect(model, session)
    value = model.models[session['id']]
    monkeypatch.setattr(models, 'MAX_RECEIPT_BYTES', 1, raising=False)
    model.store.save(session['id'], value, **update)
    assert model.store.db.execute('SELECT count(*) FROM models').fetchone()[0] == 0
    assert model.store.db.execute('SELECT count(*) FROM nodes').fetchone()[0] == 0
    assert path.exists()


def test_matching_receipt_keeps_tree_byte_limit_before_decoding_nodes(source, tmp_path, monkeypatch):
    session, path = source
    family(session, path, 2)
    home = tmp_path / 'host'
    first = models.SelectedHistoryReadModel(home)
    inspect(first, session)
    first.close()
    second = models.SelectedHistoryReadModel(home)
    expected = copy.deepcopy(session['execution'])
    queries = []
    second.store.db.set_trace_callback(queries.append)
    monkeypatch.setattr(models, 'MAX_MODEL_BYTES', 1)
    try:
        result = inspect(second, session)
        assert result['diagnostics']['counters']['events.model.rebuilt'] == 1
        assert session['execution'] == expected
        assert not any(query.lower().startswith('select body from nodes') for query in queries)
    finally:
        second.close()
