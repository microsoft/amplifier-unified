"""Eviction/restart reuses descriptors, never replays or rewrites native logs."""
import json
from types import SimpleNamespace

import pytest

from amplifier_web import event_log_view as events
from amplifier_web import event_index_checkpoint as checkpoints
from test_event_index_retention import append, session, home


def view(home):
    return events.EventLogView(SimpleNamespace(data_dir=home / 'host'))


def test_oversized_index_appends_without_reparsing_old_payloads(home, monkeypatch):
    monkeypatch.setattr(events.EventLogView, 'MAX_INDEX_BYTES', 1024)
    row = session(home, 'large')
    path = events.event_path(row, row['id'])
    append(path, row['id'], result='old payload ' * 100000)
    consumed = []
    original = events.EventIndex.ingest
    def ingest(index, event, reference):
        consumed.append(reference['bytes'])
        return original(index, event, reference)
    monkeypatch.setattr(events.EventIndex, 'ingest', ingest)
    first = view(home)
    tree = first.read(row)
    assert consumed and not first.indexes
    native = path.read_bytes()
    second = view(home)  # Process-local cache starts empty again.
    consumed.clear()
    assert second.read(row) == tree
    assert consumed == []
    delta = append(path, row['id'], call='new', result='new output')
    consumed.clear()
    refreshed = second.read(row)
    assert consumed == [delta]
    assert events.read_field(refreshed['nodes'][0]['_eventFields']['output']) == 'old payload ' * 100000
    assert path.read_bytes().startswith(native)
    assert not second.indexes and second.retained_index_bytes == 0


def test_replacement_and_corrupt_checkpoint_fall_back_to_canonical_log(home, monkeypatch):
    monkeypatch.setattr(events.EventLogView, 'MAX_INDEX_BYTES', 1)
    row = session(home, 'replace')
    path = events.event_path(row, row['id'])
    append(path, row['id'], call='old')
    reader = view(home)
    reader.read(row)
    cache = reader.checkpoints.path(path, row['id'])
    cache.write_bytes(b'broken cache')
    assert reader.read(row)['nodes'][0]['toolCallId'] == 'old'
    replacement = path.with_suffix('.replacement')
    append(replacement, row['id'], call='replacement')
    replacement.replace(path)
    assert [n['toolCallId'] for n in reader.read(row)['nodes']] == ['replacement']
    path.unlink()
    assert reader.read(row) is None


def test_checkpoint_preserves_pending_identity_and_prompt_association(home):
    row = session(home, 'pending')
    path = events.event_path(row, row['id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {'event':'prompt:submit','data':{'session_id':row['id'],'prompt':'Private prompt'}},
        {'event':'llm:request','timestamp':1,'data':{'session_id':row['id'],'request_id':'call','model':'model',
                                                 'raw':{'messages':['Private raw content']}}}]
    path.write_text(''.join(json.dumps(item)+'\n' for item in records))
    original = events.EventIndex(path, row['id']);original.refresh()
    cache = checkpoints.Checkpoints(home / 'cache')
    cache.save(original)
    restored = events.EventIndex(path, row['id'])
    assert cache.load(restored)
    assert restored.association_events == original.association_events
    pending = next(iter(restored.pending.values()))[0]
    assert pending is restored.nodes[pending['id']]
    with path.open('a') as stream:
        stream.write(json.dumps({'event':'llm:response','timestamp':2,'data':{
            'session_id':row['id'],'request_id':'call','model':'model','usage':{'input_tokens':1}}})+'\n')
    original.refresh();restored.refresh()
    assert restored.nodes == original.nodes and restored.pending == original.pending
    # Raw model payloads stay in their canonical event, not the checkpoint.
    import gzip
    assert b'Private raw content' not in gzip.decompress(cache.path(path,row['id']).read_bytes())
    assert b'Private prompt' not in gzip.decompress(cache.path(path,row['id']).read_bytes())


def test_cache_bounds_and_rejects_redirected_payload_reference(home, monkeypatch):
    monkeypatch.setattr(checkpoints, 'MAX_FILES', 2)
    cache = checkpoints.Checkpoints(home / 'host')
    for n in range(4):
        row = session(home, str(n));path = events.event_path(row,row['id'])
        append(path,row['id'])
        index = events.EventIndex(path,row['id']);index.refresh();cache.save(index)
    assert len(list(cache.directory.glob('*.json.gz'))) == 2
    payload = checkpoints.encode(index)
    payload['nodes'][0][1]['_eventFields']['output']['path'] = '/another/source'
    with pytest.raises(ValueError, match='source'):
        checkpoints.restore(events.EventIndex(path,row['id']), payload)
    monkeypatch.setattr(checkpoints, 'MAX_DECODED', 1)
    assert not cache.load(events.EventIndex(path,row['id']))


def test_partial_append_and_truncation_after_checkpoint(home, monkeypatch):
    monkeypatch.setattr(events.EventLogView, 'MAX_INDEX_BYTES', 1)
    row = session(home, 'partial')
    path = events.event_path(row, row['id'])
    append(path, row['id'], call='first')
    reader = view(home)
    reader.read(row)
    record = json.dumps({'event': 'tool:post', 'timestamp': 11, 'data': {
        'session_id': row['id'], 'tool_call_id': 'second', 'result': 'complete'}})
    with path.open('a') as stream:
        stream.write(record[:20])
    assert len(view(home).read(row)['nodes']) == 1
    with path.open('a') as stream:
        stream.write(record[20:] + '\n')
    assert [node['toolCallId'] for node in view(home).read(row)['nodes']] == ['first', 'second']
    path.write_text('')
    append(path, row['id'], call='truncated')
    assert [node['toolCallId'] for node in view(home).read(row)['nodes']] == ['truncated']


def test_unwritable_optional_cache_does_not_fail_reads(home, monkeypatch):
    monkeypatch.setattr(events.EventLogView, 'MAX_INDEX_BYTES', 1)
    row = session(home, 'readonly')
    path = events.event_path(row, row['id'])
    append(path, row['id'])
    reader = view(home)
    def failure(index):
        raise OSError('cache unavailable')
    monkeypatch.setattr(reader.checkpoints, 'save', failure)
    assert reader.read(row)['nodes'][0]['toolCallId'] == 'call'
    assert not reader.indexes
