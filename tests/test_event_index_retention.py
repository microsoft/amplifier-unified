"""Retained event indexes must not turn tiny appends into historical rereads."""
import asyncio
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from amplifier_web import event_log_view as events


def append(path, sid, call='call', result='done'):
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({'event': 'tool:post', 'timestamp': 10,
                      'data': {'session_id': sid, 'tool_call_id': call, 'result': result}}) + '\n'
    with path.open('a') as stream:
        stream.write(line)
    return len(line.encode())


def session(tmp_path, identity):
    return {'id': identity, 'runtimeSessionId': identity, 'workspace': str(tmp_path / 'workspace'),
            'messages': [], 'workers': [], 'status': 'idle'}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.delenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', raising=False)
    return tmp_path


async def test_small_appends_reuse_three_root_working_set(home, monkeypatch):
    sessions = [session(home, f'root-{i}') for i in range(3)]
    by_id = {row['id']: row for row in sessions}
    service = SimpleNamespace(_session=by_id.__getitem__, lock=asyncio.Lock(), closed=False,
                              _publish=lambda **scope: None)
    view = events.EventLogView(service)
    for row in sessions:
        path = events.event_path(row, row['id'])
        for child in range(36):
            sid = row['id'] + f'-child-{child}'
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a') as stream:
                stream.write(json.dumps({'event': 'delegate:agent_spawned', 'timestamp': 1,
                    'data': {'session_id': row['id'], 'sub_session_id': sid, 'tool_call_id': str(child)}}) + '\n')
            append(events.event_path(row, sid), sid, result='x' * 512)
    original = events.EventIndex.refresh
    consumed = []
    def observed(index):
        offset = index.offset
        value = original(index)
        consumed.append(max(0, index.offset - offset))
        return value
    monkeypatch.setattr(events.EventIndex, 'refresh', observed)
    for row in sessions:
        await view.refresh(row['id'])
    consumed.clear()
    for row in sessions:
        await view.refresh(row['id'])
    assert not consumed, 'unchanged projections must not reread even when other roots ran'
    for row in sessions:
        expected = append(events.event_path(row, row['id']), row['id'], call='new')
        consumed.clear()
        await view.refresh(row['id'])
        assert sum(consumed) == expected, 'unchanged root/child history must stay parsed'
    assert view.retained_index_bytes <= view.MAX_INDEX_BYTES
    assert len(view.indexes) <= view.MAX_INDEXES


@pytest.mark.parametrize('count', [63, 64, 65])
def test_entry_limit_and_recent_history_retirement(home, monkeypatch, count):
    monkeypatch.setattr(events.EventLogView, 'MAX_INDEXES', 64)
    view = events.EventLogView(None)
    sessions = [session(home, f's-{i}') for i in range(count)]
    for row in sessions:
        append(events.event_path(row, row['id']), row['id'])
        view.read(row)
    assert len(view.indexes) == min(64, count)
    assert str(events.event_path(sessions[-1], sessions[-1]['id'])) in view.indexes
    if count == 65:
        assert str(events.event_path(sessions[0], sessions[0]['id'])) not in view.indexes
    assert view.retained_index_bytes <= view.MAX_INDEX_BYTES


def test_byte_limit_discards_oversized_index_but_keeps_exact_output(home, monkeypatch):
    monkeypatch.setattr(events.EventLogView, 'MAX_INDEX_BYTES', 1024)
    row = session(home, 'large')
    path = events.event_path(row, row['id'])
    append(path, row['id'], result='large exact body ' * 1000)
    digest = hashlib.sha256(path.read_bytes()).digest()
    view = events.EventLogView(None)
    for _ in range(2):
        tree = view.read(row)
        assert events.read_field(tree['nodes'][0]['_eventFields']['output']) == 'large exact body ' * 1000
        assert not view.indexes and view.retained_index_bytes == 0
    assert hashlib.sha256(path.read_bytes()).digest() == digest


def test_byte_budget_counts_association_payload_and_deduplicates_aliases(home):
    row = session(home, 'bytes')
    index = events.EventIndex(events.event_path(row, row['id']), row['id'])
    initial = events.retained_index_size(index)
    payload = 'q' * 200000
    index.association_events.append({'prompt': payload})
    grown = events.retained_index_size(index)
    index.association_cache['alias'] = payload
    aliased = events.retained_index_size(index)
    assert grown >= initial + 200000
    assert aliased - grown < 1000, 'aliasing the same object must not charge its body twice'


def test_sizing_stops_before_expanding_a_proven_oversized_container(home):
    row = session(home, 'bounded-size')
    index = events.EventIndex(events.event_path(row, row['id']), row['id'])
    exact = events.retained_index_size(index)
    assert events.retained_index_size(index, limit=exact) == exact
    assert events.retained_index_size(index, limit=exact - 1) > exact - 1
    class LargeUnvisitedBody(dict):
        def __sizeof__(self):
            return 2_000_000
        def keys(self):
            raise AssertionError('Admission is already rejected; do not expand this body')
        def values(self):
            raise AssertionError('Admission is already rejected; do not expand this body')
    index.nodes = LargeUnvisitedBody()
    assert events.retained_index_size(index, limit=1_000_000) > 1_000_000


def test_oversize_receipt_reuses_only_negative_admission_after_source_validation(home, monkeypatch):
    row = session(home, 'negative-receipt')
    path = events.event_path(row, row['id'])
    append(path, row['id'], result='exact original output ' * 1000)
    view = events.EventLogView(SimpleNamespace(data_dir=home / 'host'))
    view.MAX_INDEX_BYTES = 1024
    calls, ingested = [], []
    original, ingest = events.retained_index_size, events.EventIndex.ingest
    def observed(index, **kwargs):
        calls.append((index.revision, index.association_revision))
        return original(index, **kwargs)
    def observed_ingest(index, event, reference):
        ingested.append(reference['bytes'])
        return ingest(index, event, reference)
    monkeypatch.setattr(events, 'retained_index_size', observed)
    monkeypatch.setattr(events.EventIndex, 'ingest', observed_ingest)
    expected = view.read(row)
    assert len(calls) == 1 and not view.indexes
    ingested.clear()
    assert view.read(row) == expected
    assert len(calls) == 1 and not ingested
    assert events.read_field(expected['nodes'][0]['_eventFields']['output']) == 'exact original output ' * 1000
    # A rejection receipt never substitutes for canonical validation or cache
    # recovery: corrupting the checkpoint still forces native event parsing.
    view.checkpoints.path(path, row['id']).write_bytes(b'broken checkpoint')
    assert view.read(row) == expected
    assert ingested and len(calls) == 1
    append(path, row['id'], call='appended')
    assert len(view.read(row)['nodes']) == 2 and len(calls) == 2
    # Association growth changes size even when the event file is unchanged.
    transcript = path.parent.parent / 'transcript.jsonl'
    transcript.write_text(json.dumps({'role': 'user', 'content': 'New association input'}) + '\n')
    view.read(row)
    assert len(calls) == 3
    replacement = path.with_suffix('.replacement')
    append(replacement, row['id'], call='replacement')
    replacement.replace(path)
    assert [node['toolCallId'] for node in view.read(row)['nodes']] == ['replacement']
    assert len(calls) == 4 and not view.indexes
    # More memory can admit the source only after a fresh complete measurement.
    view.MAX_INDEX_BYTES = 1_000_000
    view.read(row)
    assert len(calls) == 5 and view.indexes and not view._oversized
    assert view.retained_index_bytes <= view.MAX_INDEX_BYTES
    view.MAX_INDEX_BYTES = 512
    view.read(row)
    assert len(calls) == 5 and not view.indexes
    view.read(row)
    assert len(calls) == 5 and view.retained_index_bytes == 0


def test_negative_size_receipts_are_bounded_metadata_only(home):
    view = events.EventLogView(SimpleNamespace(data_dir=home / 'host'))
    view.MAX_INDEX_BYTES, view.MAX_SIZE_RECEIPTS = 1024, 3
    paths = []
    for number in range(8):
        row = session(home, f'large-{number}')
        path = events.event_path(row, row['id'])
        paths.append(str(path))
        append(path, row['id'], result='tool preview')
        view.read(row)
    assert list(view._oversized) == paths[-3:]
    def metadata(value):
        return type(value) in (str, int, type(None)) or type(value) is tuple and all(metadata(v) for v in value)
    assert all(metadata(value) for value in view._oversized.values())
    assert not view.indexes and view.retained_index_bytes == 0


def test_remeasurement_detects_growth_and_replacement(home, monkeypatch):
    row = session(home, 'growing')
    path = events.event_path(row, row['id'])
    append(path, row['id'])
    view = events.EventLogView(None)
    view.read(row)
    before = view.retained_index_bytes
    for n in range(20):
        append(path, row['id'], call=f'more-{n}', result='x' * 512)
    view.read(row)
    assert view.retained_index_bytes > before
    monkeypatch.setattr(view, 'MAX_INDEX_BYTES', view.retained_index_bytes // 2)
    view.read(row)
    assert not view.indexes and view.retained_index_bytes == 0
    replacement = path.with_suffix('.replacement')
    append(replacement, row['id'], call='replacement')
    replacement.replace(path)
    tree = view.read(row)
    assert [node['toolCallId'] for node in tree['nodes']] == ['replacement']
    assert view.retained_index_bytes <= view.MAX_INDEX_BYTES


def test_missing_logs_are_not_retained(home):
    view = events.EventLogView(None)
    for n in range(100):
        assert view.read(session(home, f'absent-{n}')) is None
    assert not view.indexes and view.retained_index_bytes == 0


def test_failed_remeasurement_evicts_grown_resident(home, monkeypatch):
    row = session(home, 'sizing-failed')
    path = events.event_path(row, row['id'])
    append(path, row['id'])
    view = events.EventLogView(None)
    view.read(row)
    assert view.indexes
    append(path, row['id'], call='new')
    def failure(index, **kwargs):
        raise MemoryError('injected sizing allocation failure')
    monkeypatch.setattr(events, 'retained_index_size', failure)
    with pytest.raises(MemoryError):
        view.read(row)
    assert not view.indexes and view.retained_index_bytes == 0


def test_root_measured_once_after_associations(home, monkeypatch):
    row = session(home, 'measure-once')
    path = events.event_path(row, row['id'])
    append(path, row['id'])
    calls = []
    original = events.retained_index_size
    def observed(index, **kwargs):
        calls.append(index.path)
        return original(index, **kwargs)
    monkeypatch.setattr(events, 'retained_index_size', observed)
    view = events.EventLogView(None)
    view.read(row)
    assert calls == [path]
    calls.clear()
    append(path, row['id'], call='new')
    view.read(row)
    assert calls == [path]


@pytest.mark.parametrize('bound', ['entries', 'bytes'])
def test_same_read_eviction_reuses_only_unchanged_live_object_charges(home, monkeypatch, bound):
    row = session(home, 'receipt-root')
    root = events.event_path(row, row['id'])
    for number in range(2):
        child = f'child-{number}'
        append(root, row['id'], call=f'delegate-{number}')
        with root.open('a') as stream:
            stream.write(json.dumps({'event': 'delegate:agent_spawned', 'timestamp': 1,
                'data': {'session_id': row['id'], 'sub_session_id': child,
                         'tool_call_id': f'delegate-{number}'}}) + '\n')
        for call in range(4):
            append(events.event_path(row, child), child, call=f'call-{call}')
    view = events.EventLogView(SimpleNamespace(data_dir=home / 'host'))
    expected = view.read(row)
    # Stabilize checkpoint bookkeeping and per-read counters before this exact
    # unchanged-object case. A checkpoint write itself must invalidate a charge.
    for index in view.indexes.values():
        view._checkpoint(index)
    view.read(row)
    children = {key: index for key, index in view.indexes.items() if index.path != root}
    if bound == 'entries':
        view.MAX_INDEXES = 2
    else:
        view.MAX_INDEX_BYTES = sum(view._index_sizes[key][1] for key in children)
        assert max(size for _, size in view._index_sizes.values()) <= view.MAX_INDEX_BYTES
    view._forget_index(str(root))
    calls = []
    original = events.retained_index_size
    def observed(index, **kwargs):
        calls.append(index)
        return original(index, **kwargs)
    monkeypatch.setattr(events, 'retained_index_size', observed)
    for _ in range(2):
        with root.open('a') as stream:
            stream.write(json.dumps({'event': 'synthetic:metadata',
                                     'data': {'session_id': row['id']}}) + '\n')
        calls.clear()
        assert view.read(row) == expected
        assert len(calls) == 1 and calls[0].path == root
        assert all(view.indexes[key] is index for key, index in children.items())
        assert view.retained_index_bytes == sum(size for _, size in view._index_sizes.values())
        assert view.retained_index_bytes <= view.MAX_INDEX_BYTES
        assert len(view.indexes) <= view.MAX_INDEXES
    # Persisted descriptors carry no RAM charge: a different reader must size
    # every restored object even though all canonical revisions still match.
    restored = events.EventLogView(view.service)
    calls.clear()
    assert restored.read(row) == expected
    assert len(calls) == 3
    assert not any(index is prior for index in calls for prior in children.values())


@pytest.mark.parametrize('mutation', ['append', 'association', 'checkpoint', 'read-counter'])
def test_retention_receipt_remeasures_every_size_affecting_revision(home, monkeypatch, mutation):
    row = session(home, 'mutated-receipt')
    path = events.event_path(row, row['id'])
    append(path, row['id'])
    view = events.EventLogView(SimpleNamespace(data_dir=home / 'host'))
    view.read(row)
    index = view.indexes[str(path)]
    receipt = (index, view._index_sizes[str(path)])
    view._forget_index(str(path))
    if mutation == 'append':
        append(path, row['id'], call='new')
        assert index.refresh()
    elif mutation == 'association':
        transcript = path.parent.parent / 'transcript.jsonl'
        transcript.write_text(json.dumps({'role': 'user', 'content': 'new prompt'}) + '\n')
        index.associations(path.parent.parent)
    elif mutation == 'checkpoint':
        view._checkpoint(index)
        assert index._checkpoint_signature is not None
    else:
        assert index.read_bytes > 0
        assert index.refresh() and index.read_bytes == 0
    calls = []
    original = events.retained_index_size
    def observed(value, **kwargs):
        calls.append(value)
        return original(value, **kwargs)
    monkeypatch.setattr(events, 'retained_index_size', observed)
    view._retain_index(index, receipt=receipt)
    assert calls == [index]
    assert view.retained_index_bytes == original(index)
    # A failed fresh measurement never leaves a stale charge or resident index.
    append(path, row['id'], call='failed-measurement')
    assert index.refresh()
    def failure(value, **kwargs):
        raise MemoryError('injected receipt remeasurement failure')
    monkeypatch.setattr(events, 'retained_index_size', failure)
    with pytest.raises(MemoryError):
        view._retain_index(index, receipt=receipt)
    assert not view.indexes and not view._index_sizes and view.retained_index_bytes == 0


def test_new_checkpoint_object_never_borrows_another_objects_charge(home, monkeypatch):
    row = session(home, 'restored-receipt')
    path = events.event_path(row, row['id'])
    append(path, row['id'])
    view = events.EventLogView(SimpleNamespace(data_dir=home / 'host'))
    view.read(row)
    old = view.indexes[str(path)]
    view._checkpoint(old)
    view.read(row)
    receipt = (old, view._index_sizes[str(path)])
    restored = view._new_index(path, row['id'])
    assert restored is not old
    assert view._charge_signature(restored) == view._charge_signature(old)
    calls = []
    original = events.retained_index_size
    def observed(index, **kwargs):
        calls.append(index)
        return original(index, **kwargs)
    monkeypatch.setattr(events, 'retained_index_size', observed)
    view._retain_index(restored, receipt=receipt)
    assert calls == [restored]
    assert view.indexes[str(path)] is restored
    assert view.retained_index_bytes == original(restored)


async def test_cancelled_refresh_keeps_reader_accounting_serialized(home, monkeypatch):
    row = session(home, 'cancelled')
    append(events.event_path(row, row['id']), row['id'])
    view = events.EventLogView(SimpleNamespace(_session=lambda _: row, lock=asyncio.Lock(),
                                               closed=False, _publish=lambda **scope: None))
    entered, release = threading.Event(), threading.Event()
    original = events.EventIndex.refresh
    calls, active, maximum = 0, 0, 0
    def blocked(index):
        nonlocal calls, active, maximum
        calls += 1
        active += 1
        maximum = max(maximum, active)
        try:
            if calls == 1:
                entered.set()
                assert release.wait(5)
            return original(index)
        finally:
            active -= 1
    monkeypatch.setattr(events.EventIndex, 'refresh', blocked)
    first = asyncio.create_task(view.refresh(row['id']))
    assert await asyncio.to_thread(entered.wait, 5)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    second = asyncio.create_task(view.refresh(row['id']))
    try:
        await asyncio.sleep(0.05)
        assert calls == 1, 'cancellation must not admit a second mutable reader'
    finally:
        release.set()
        await asyncio.wait_for(second, 5)
    assert maximum == 1
    assert view.retained_index_bytes == sum(size for _, size in view._index_sizes.values())
    assert view.retained_index_bytes <= view.MAX_INDEX_BYTES


def test_failed_chat_read_preserves_other_chats_parsed_history(home, monkeypatch):
    view = events.EventLogView(None)
    healthy, changing = (session(home, identity) for identity in ('healthy', 'changing'))
    for row in (healthy, changing):
        append(events.event_path(row, row['id']), row['id'])
        view.read(row)
    key = str(events.event_path(healthy, healthy['id']))
    retained = view.indexes[key]
    original = events.EventIndex.associations

    def fail(index, directory):
        if index.identity == 'changing':
            raise ValueError('Session history changed during indexed read')
        return original(index, directory)

    monkeypatch.setattr(events.EventIndex, 'associations', fail)
    with pytest.raises(ValueError, match='indexed read'):
        view.read(changing)
    assert view.indexes[key] is retained
    assert view.retained_index_bytes == sum(size for _, size in view._index_sizes.values())
    view.read(healthy)
    assert view.indexes[key] is retained
