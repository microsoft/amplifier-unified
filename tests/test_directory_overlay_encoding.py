"""Repeated page/key reads serialize only changed admitted scalar overlays."""
from copy import deepcopy
import json
import pytest

from amplifier_web import history_directory
from test_history_directory_navigation import directory_fixture
from test_shell_modules import service
from test_shell_query_owner import prepare, settle


def count_overlay_encodings(directory, monkeypatch):
    calls = []
    original = json.dumps

    def encode(value, *args, **kwargs):
        if value is directory._live_notices:
            calls.append('notices')
        elif isinstance(value, list) and value and directory._live_values:
            first = next(iter(directory._live_values.values()))
            if value[0] is first:
                calls.append('rows')
            elif (isinstance(value[0], tuple) and len(value[0]) == len(first)
                  and value[0][0] == first[0]
                  and value[0][history_directory.COLUMNS.index('summary')] is None):
                calls.append('scalar-rows')
        return original(value, *args, **kwargs)

    monkeypatch.setattr(history_directory.json, 'dumps', encode)
    return calls


def test_repeated_scoped_reads_reuse_overlay_but_observe_changes(tmp_path, monkeypatch):
    state, directory = directory_fixture(tmp_path, 12)
    calls = count_overlay_encodings(directory, monkeypatch)
    old = deepcopy(directory.select('id=?', ('s4',), limit=1, state=state))
    for scope in ({}, {'public_ids': ['s4']}, {'identity': 's4'}, {}):
        directory.effective(state, **scope)
    assert sorted(calls) == ['notices', 'rows']

    # Same state revision and same mutable app-row objects: preparation must
    # discover changed content before a new SQL query can reuse encodings.
    state['sessions'][4]['title'] = 'Changed without revision'
    changed = directory.select('id=?', ('s4',), limit=1, state=state)
    assert changed[0]['title'] == 'Changed without revision'
    assert old[0]['title'] != changed[0]['title']
    assert sorted(calls) == ['notices', 'notices', 'rows', 'rows']

    state['sessions'].pop(4)
    directory.effective(state)
    assert 's4' not in directory._live_values
    assert sorted(calls) == ['notices'] * 3 + ['rows'] * 3


def test_encoding_reuse_does_not_cache_read_receipts(tmp_path):
    state, directory = directory_fixture(tmp_path, 12)
    directory.effective(state)
    # An external/unrelated receipt need not change the live overlay. It must
    # still reach SQL, without waiting for a host revision or overlay rebuild.
    state['attentionRead']['other-owner:notice'] = 'new-fingerprint'
    _, args = directory.effective(state)
    assert json.loads(args[2]) == state['attentionRead']


def test_frozen_reader_keeps_its_own_encoded_overlay(tmp_path):
    state, directory = directory_fixture(tmp_path, 12)
    old = directory.select('id=?', ('s4',), limit=1, state=state)
    frozen = directory.read_snapshot(state)
    try:
        assert frozen.select('id=?', ('s4',), limit=1) == old
        state['sessions'][4]['title'] = 'Live owner changed'
        assert directory.select('id=?', ('s4',), limit=1, state=state)[0]['title'] == 'Live owner changed'
        assert frozen.select('id=?', ('s4',), limit=1) == old
    finally:
        frozen.close()


async def test_browser_projection_encodes_each_changed_overlay_once(service, monkeypatch):
    prepare(service)
    await settle(service)
    service._session('alpha', hydrate=False)['status'] = 'working'
    service._save_changes(sessions={'alpha'})
    service._client_snapshots.clear()
    calls = count_overlay_encodings(service.directory, monkeypatch)
    with service.clients.bind('browser'):
        actual = service.browser_state()
    assert actual['selectedSessionId'] == 'alpha'
    assert actual['shellResources']['instances']
    # Browser scalar queries need no full-body encoding; another consumer may
    # request it once. Notices are encoded once regardless of query order.
    assert calls.count('notices') == 1
    assert calls.count('scalar-rows') == 1
    assert calls.count('rows') <= 1


@pytest.mark.parametrize('scalar_first', [False, True])
def test_mixed_reads_encode_each_variant_once(tmp_path, monkeypatch, scalar_first):
    state, directory = directory_fixture(tmp_path, 2)
    calls = count_overlay_encodings(directory, monkeypatch)
    scopes = [{'fields': {'id'}}, {}]
    if not scalar_first:
        scopes.reverse()
    for _ in range(3):
        for scope in scopes:
            sql, args = directory.effective(state, **scope)
            assert directory.db.execute(sql + ' SELECT id FROM directory_rows', args).fetchall()
    assert sorted(calls) == ['notices', 'rows', 'scalar-rows']

    state['sessions'][0]['title'] = 'Changed without revision'
    for scope in scopes:
        directory.effective(state, **scope)
    assert sorted(calls) == ['notices'] * 2 + ['rows'] * 2 + ['scalar-rows'] * 2
