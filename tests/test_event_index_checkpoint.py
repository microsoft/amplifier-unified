"""Eviction/restart reuses descriptors, never replays or rewrites native logs."""
import json
import subprocess
import sys
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


@pytest.mark.parametrize('prefix', [b'{"schema": 1,', b'{"schema": 200,', b' ' * 128])
def test_obsolete_or_unrecognized_header_is_rejected_before_graph_decode(home, monkeypatch, prefix):
    import gzip
    row = session(home, 'obsolete')
    path = events.event_path(row, row['id'])
    append(path, row['id'])
    cache = checkpoints.Checkpoints(home / 'host')
    cache.directory.mkdir(parents=True)
    target = cache.path(path, row['id'])
    target.write_bytes(gzip.compress(prefix + b'"unused":"' + b'x' * (2 * 1024 * 1024) + b'"}'))
    original_read = gzip.GzipFile.read
    decoded = []
    def read(stream, count=-1):
        value = original_read(stream, count)
        decoded.append(len(value))
        return value
    def unexpected(*args, **kwargs):
        pytest.fail('Rejected checkpoint must not allocate/decode the saved graph')
    index = events.EventIndex(path, row['id'])
    with monkeypatch.context() as patch:
        patch.setattr(gzip.GzipFile, 'read', read)
        patch.setattr(checkpoints.json, 'loads', unexpected)
        assert not cache.load(index)
    assert sum(decoded) <= checkpoints.HEADER_BYTES
    assert index.refresh()
    assert [node['toolCallId'] for node in index.nodes.values()] == ['call']


@pytest.mark.parametrize('stage', ['decompress', 'decode', 'restore'])
def test_optional_checkpoint_allocation_failure_resets_then_reads_canonical(home, monkeypatch, stage):
    row = session(home, 'allocation')
    path = events.event_path(row, row['id'])
    append(path, row['id'])
    original = events.EventIndex(path, row['id']); original.refresh()
    cache = checkpoints.Checkpoints(home / 'host'); cache.save(original)
    index = events.EventIndex(path, row['id'])
    def failure(*args, **kwargs):
        if stage == 'restore':
            index.nodes['partial'] = {'id': 'partial'}
            index.offset = 123
        raise MemoryError
    with monkeypatch.context() as patch:
        if stage == 'decompress':
            patch.setattr(checkpoints.gzip.GzipFile, 'read', failure)
        elif stage == 'decode':
            patch.setattr(checkpoints.json, 'loads', failure)
        else:
            patch.setattr(checkpoints, 'restore', failure)
        assert not cache.load(index)
    assert not index.nodes and index.offset == 0
    assert index.refresh()
    assert index.nodes == original.nodes and index.association_events == original.association_events


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


@pytest.mark.parametrize('error', [OSError, MemoryError])
def test_unwritable_optional_cache_does_not_fail_reads(home, monkeypatch, error):
    monkeypatch.setattr(events.EventLogView, 'MAX_INDEX_BYTES', 1)
    row = session(home, 'readonly')
    path = events.event_path(row, row['id'])
    append(path, row['id'])
    reader = view(home)
    def failure(index):
        raise error('cache unavailable')
    monkeypatch.setattr(reader.checkpoints, 'save', failure)
    assert reader.read(row)['nodes'][0]['toolCallId'] == 'call'
    assert not reader.indexes


def test_checkpoint_serialization_does_not_allocate_a_complete_document(home):
    import gzip
    import tracemalloc
    row = session(home, 'streaming')
    path = events.event_path(row, row['id'])
    append(path, row['id'])
    index = events.EventIndex(path, row['id']); index.refresh()
    # More than 8 MiB of JSON from small existing descriptors, with little
    # source allocation. The old dumps/encode/compress writer adds >16 MiB.
    value = 'descriptor ' * 1024
    index.diagnostics = [{'summary': value, 'ordinal': n} for n in range(800)]
    cache = checkpoints.Checkpoints(home / 'host')
    tracemalloc.start()
    try:
        cache.save(index)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024
    with gzip.open(cache.path(path, row['id']), 'rt') as stream:
        assert json.load(stream) == json.loads(json.dumps(checkpoints.encode(index)))


@pytest.mark.parametrize('limit', ['MAX_DECODED', 'MAX_FILE', 'MAX_SCALAR_CHARS'])
def test_checkpoint_limit_preserves_prior_complete_file_and_cleans_temporary(home, monkeypatch, limit):
    row = session(home, 'bounded')
    path = events.event_path(row, row['id'])
    append(path, row['id'])
    index = events.EventIndex(path, row['id']); index.refresh()
    cache = checkpoints.Checkpoints(home / 'host'); cache.save(index)
    target = cache.path(path, row['id'])
    previous = target.read_bytes()
    prior_signature = index._checkpoint_signature
    append(path, row['id'], call='new'); index.refresh()
    monkeypatch.setattr(checkpoints, limit, 1)
    cache.save(index)
    assert target.read_bytes() == previous
    assert index._checkpoint_signature == prior_signature
    assert not list(cache.directory.glob('.index-*'))


def test_failed_checkpoint_write_cleans_private_temporary(home, monkeypatch):
    row = session(home, 'allocation')
    path = events.event_path(row, row['id']); append(path, row['id'])
    index = events.EventIndex(path, row['id']); index.refresh()
    cache = checkpoints.Checkpoints(home / 'host')
    def fail(*args, **kwargs):
        raise MemoryError
    monkeypatch.setattr(checkpoints.gzip.GzipFile, 'write', fail)
    with pytest.raises(MemoryError):
        cache.save(index)
    assert not list(cache.directory.iterdir())


def test_large_scalar_is_rejected_before_json_encoding(home, monkeypatch):
    row = session(home, 'large-scalar')
    path = events.event_path(row, row['id']); append(path, row['id'])
    index = events.EventIndex(path, row['id']); index.refresh()
    index.diagnostics = [{'summary': '\\' * (checkpoints.MAX_SCALAR_CHARS + 1)}]
    cache = checkpoints.Checkpoints(home / 'host')
    def unexpected(*args, **kwargs):
        raise AssertionError('large scalar must not reach JSON escaping')
    monkeypatch.setattr(checkpoints.json.JSONEncoder, 'iterencode', unexpected)
    cache.save(index)
    assert not cache.path(path, row['id']).exists()


def test_compressed_limit_applies_after_header_without_publishing_partial_file(home, monkeypatch):
    import hashlib
    row = session(home, 'compressed-limit')
    path = events.event_path(row, row['id']); append(path, row['id'])
    index = events.EventIndex(path, row['id']); index.refresh()
    cache = checkpoints.Checkpoints(home / 'host'); cache.save(index)
    target = cache.path(path, row['id']); previous = target.read_bytes()
    append(path, row['id'], call='new'); index.refresh()
    index.diagnostics = [{'summary': hashlib.sha256(str(n).encode()).hexdigest()} for n in range(500)]
    monkeypatch.setattr(checkpoints, 'MAX_FILE', 1024)
    cache.save(index)
    assert target.read_bytes() == previous
    assert not list(cache.directory.glob('.index-*'))


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux address-space budget proof')
def test_retention_preserves_selected_result_with_bounded_allocation_headroom(home):
    result = subprocess.run([sys.executable, '-c', '''
import json, os, resource, sys
from pathlib import Path
from types import SimpleNamespace
from amplifier_web import event_log_view as events
home = Path(sys.argv[1])
row = {'id': 'bounded', 'runtimeSessionId': 'bounded', 'workspace': str(home / 'workspace'),
       'messages': [], 'workers': [], 'status': 'idle'}
path = events.event_path(row, row['id'])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({'event': 'tool:post', 'timestamp': 10,
    'data': {'session_id': 'bounded', 'tool_call_id': 'call', 'result': 'exact result'}}) + '\\n')
reader = events.EventLogView(SimpleNamespace(data_dir=home / 'host'))
expected = reader.read(row)
index = reader.indexes[str(path)]
index.diagnostics = [{'summary': 'descriptor ' * 1024, 'ordinal': n} for n in range(800)]
reader.MAX_INDEX_BYTES = 1
virtual = int(Path('/proc/self/statm').read_text().split()[0]) * os.sysconf('SC_PAGE_SIZE')
resource.setrlimit(resource.RLIMIT_AS, (virtual + 12 * 1024 * 1024,) * 2)
actual = reader.read(row)
assert actual == expected
assert events.read_field(actual['nodes'][0]['_eventFields']['output']) == 'exact result'
assert reader.checkpoints.path(path, row['id']).is_file()
assert not reader.indexes
print(json.dumps({'selectedNodes': len(actual['nodes']), 'checkpointSaved': True, 'headroomBytes': 12*1024*1024}))
''', str(home)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['checkpointSaved']
