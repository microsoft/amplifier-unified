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
                              _publish=lambda: None)
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
    def failure(index):
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
    def observed(index):
        calls.append(index.path)
        return original(index)
    monkeypatch.setattr(events, 'retained_index_size', observed)
    view = events.EventLogView(None)
    view.read(row)
    assert calls == [path]
    calls.clear()
    append(path, row['id'], call='new')
    view.read(row)
    assert calls == [path]


async def test_cancelled_refresh_keeps_reader_accounting_serialized(home, monkeypatch):
    row = session(home, 'cancelled')
    append(events.event_path(row, row['id']), row['id'])
    view = events.EventLogView(SimpleNamespace(_session=lambda _: row, lock=asyncio.Lock(),
                                               closed=False, _publish=lambda: None))
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
