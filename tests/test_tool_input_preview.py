"""Bounded, presentation-only previews from real tool inputs and wire paths."""
import json
from pathlib import Path

import pytest

from amplifier_web import tool_observations as hints

CASES = json.loads((Path(__file__).parent / 'fixtures/tool-preview-cases.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
def test_shared_input_shapes(case):
    result = hints.input_hints(case['tool'], case['input'])
    assert result['inputPreview'] == case['inputPreview']
    assert result['actionPath'] == case['actionPath']
    assert len(result['inputPreview']) <= 180
    assert result['inputSummary'] == hints.input_summary(case['input'])


def test_missing_input_differs_from_explicit_null_and_clears_dispatch():
    assert hints.input_hints('app_control', None, present=False) == {
        'inputSummary': {}, 'inputPreview': '', 'actionPath': ''}
    assert hints.input_hints('app_control', None)['inputPreview'] == 'null'
    assert hints.input_hints('other', {'operation': 'dispatch', 'args': {'action': 'not-app'}})['actionPath'] == ''
    assert hints.input_hints('app_control', {'action': 'dispatch', 'args': {'action': 'not-operation'}})['actionPath'] == ''


def test_huge_values_have_visible_bounded_truncation_without_full_traversal():
    class BoundedDict(dict):
        def items(self):
            for count, item in enumerate(super().items()):
                assert count < 4, 'preview walked beyond its item limit'
                yield item
    class BoundedList(list):
        def __iter__(self):
            for count, value in enumerate(super().__iter__()):
                assert count < 4, 'preview walked beyond its item limit'
                yield value
    wide = BoundedDict({str(index): index for index in range(100)})
    assert hints.input_preview(wide) == '0=0, 1=1, 2=2, 3=3, …'
    assert hints.input_preview(BoundedList(range(100))) == '[0, 1, 2, 3, …]'
    long = hints.input_preview({'search': 'a' * 1_000_000, 'scope': 'b' * 1_000_000, 'names': ['c' * 1_000_000]})
    assert len(long) <= 180 and '…' in long
    recursive = {}; recursive['self'] = recursive
    assert hints.input_preview(recursive) == 'self={self: {…}}'
    assert hints.input_preview({'n': 1 << 100_000}) == 'n=…'


@pytest.mark.parametrize('key', ['password', 'clientSecret', 'api-key', 'access_token', 'Authorization',
    'refreshToken', 'cookie', 'private_key', 'credentials', 'service.apiKey', 'x-api-key', 'PASSWORD', 'x' * 200])
def test_secret_named_values_are_never_traversed(key):
    class NoTouch(dict):
        def items(self):
            pytest.fail('redacted value must not be traversed')
    result = hints.input_preview({'visible': 'query', key: NoTouch(value='DO-NOT-SHOW')})
    assert '[redacted]' in result and 'DO-NOT-SHOW' not in result


def test_normalize_limits_preview_fields_to_tool_metadata_and_never_raw_body():
    from amplifier_web.runtime import normalize_event
    from amplifier_web.execution import ingest
    event = {'id': 'tool', 'kind': 'tool', 'inputPreview': 'x' * 2000, 'actionPath': 'y' * 2000}
    public = normalize_event({'type': 'execution.event', 'event': event}, 'root')[1]
    assert public['inputPreview'] == 'x' * 179 + '…'
    assert public['actionPath'] == 'y' * 179 + '…'
    session = {}; ingest(session, public)
    assert session['execution']['nodes'][0]['inputPreview'] == public['inputPreview']
    other = normalize_event({'type': 'execution.event', 'event': {**event, 'kind': 'llm'}}, 'root')[1]
    assert 'inputPreview' not in other and 'actionPath' not in other


@pytest.mark.asyncio
@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
async def test_actual_hook_runtime_saved_window_compact_and_descriptor(case, monkeypatch):
    from test_tool_observation_binding import host, rows
    from amplifier_web.runtime import normalize_event
    from amplifier_web.execution import ingest
    from amplifier_web.browser_detail import compact
    from amplifier_web.delegate_exchange import descriptor
    from amplifier_web.tool_window_records import records, linked, attach
    co, worker, messages, saved = host(monkeypatch)
    await co.hooks.emit('tool:post', {'tool_call_id': 'same', 'tool_name': case['tool'],
        'tool_input': case['input'], 'result': {'success': True, 'output': 'done'}})
    update, recorded = rows(messages)[0], saved[0]
    session = {'id': 'root'}
    ingest(session, normalize_event({'type': 'execution.event', 'event': update}, 'root')[1])
    node = session['execution']['nodes'][0]
    page = {'items': saved, 'source': {}, 'sessionId': 'root', 'group': 'window'}
    page['toolRecords'] = records(page)
    bridged = attach(node, linked(page, session, node))
    public = compact(bridged, 'root', 'nodes', 512)
    exchange = descriptor(bridged, 'root')
    # Core's hook transport may canonicalize JSON key ordering. Compare the
    # preview with actual captured input, not the caller's pre-transport order.
    actual_input = recorded['input'] if isinstance(case['input'], str) else json.loads(recorded['input'])
    expected = hints.input_hints(case['tool'], actual_input)
    for stage, item in zip(("update", "recorded", "node", "bridged", "public", "exchange"), (update, recorded, node, bridged, public, exchange)):
        assert item['inputPreview'] == expected['inputPreview'], stage
        assert item.get('actionPath', '') == case['actionPath']
    assert public['contentAvailability']['status'] == 'linked'
    assert actual_input == case['input']


def test_native_index_preview_and_schema_invalidation(tmp_path):
    import gzip
    from amplifier_web.event_log_view import EventIndex
    from amplifier_web.event_index_checkpoint import Checkpoints
    path = tmp_path / 'events.jsonl'
    event = {'event': 'tool:post', 'data': {'tool_name': 'load_skill', 'tool_call_id': 'call',
        'tool_input': {'search': 'coordinate-work'}, 'result': 'saved'}}
    path.write_text(json.dumps(event) + '\n')
    index = EventIndex(path, 'root'); index.refresh()
    node = next(iter(index.nodes.values()))
    assert node['inputPreview'] == 'search="coordinate-work"'
    assert node['inputSummary'] == {}
    cache = Checkpoints(tmp_path / 'host'); cache.save(index)
    target = cache.path(path, 'root')
    data = json.loads(gzip.decompress(target.read_bytes()))
    data['schema'] = 3
    target.write_bytes(gzip.compress(json.dumps(data).encode()))
    other = EventIndex(path, 'root')
    assert not cache.load(other), 'old parsed checkpoint must be rebuilt for new preview fields'
    other.refresh()
    assert next(iter(other.nodes.values()))['inputPreview'] == node['inputPreview']
    cache.save(other)
    restored = EventIndex(path, 'root')
    assert cache.load(restored)
    assert next(iter(restored.nodes.values()))['inputPreview'] == node['inputPreview']


@pytest.mark.parametrize('event_name, outer', [('tool:error', {}),
    ('tool:post', {'error': 'outer failure'}), ('tool:post', {'error_message': 'outer failure'})])
def test_native_delegate_outer_failure_preserves_raw_result_but_no_typed_answer(tmp_path, event_name, outer):
    from amplifier_web.event_log_view import EventIndex
    path = tmp_path / 'events.jsonl'
    common = {'tool_name': 'delegate', 'tool_call_id': 'call',
        'tool_input': {'instruction': 'review'}, 'result': {'success': True, 'output': 'partial return'}}
    # The same native row may already carry a typed answer from an earlier event.
    path.write_text(json.dumps({'event': 'tool:post', 'data': common}) + '\n')
    index = EventIndex(path, 'root'); index.refresh()
    assert next(iter(index.nodes.values()))['delegateResponse'] == 'partial return'
    with path.open('a') as stream:
        stream.write(json.dumps({'event': event_name, 'data': {**common, **outer}}) + '\n')
    index.refresh()
    node = next(iter(index.nodes.values()))
    assert 'partial return' in node['output'] and 'output' in node['_eventFields']
    assert 'delegateResponse' not in node and 'delegateResponseDetail' not in node
    assert 'delegateResponse' not in node['_eventFields']


@pytest.mark.asyncio
async def test_new_terminal_clears_old_preview_without_inheriting_dispatch(monkeypatch):
    from test_tool_observation_binding import host, rows
    from amplifier_web.execution import ingest
    from amplifier_web.runtime import normalize_event
    co, worker, messages, saved = host(monkeypatch, sid='child')
    common = {'tool_call_id': 'same', 'tool_name': 'app_control', 'session_id': 'child'}
    await co.hooks.emit('tool:pre', {**common, 'tool_input': {'operation': 'dispatch', 'args': {'action': 'session.read'}}})
    await co.hooks.emit('tool:error', {**common, 'error': 'cancelled'})
    updates = rows(messages)
    assert updates[0]['actionPath'] == 'dispatch → session.read'
    assert updates[1]['inputPreview'] == updates[1]['actionPath'] == ''
    assert updates[0]['toolObservationId'] != updates[1]['toolObservationId']
    session = {}
    for update in updates:
        ingest(session, normalize_event({'type': 'execution.event', 'event': update}, 'root')[1])
    row = session['execution']['nodes'][0]
    assert row['inputPreview'] == row['actionPath'] == ''
    assert row['sessionId'] == 'child' and row['phase'] == 'error'


def test_action_path_limits_whitespace_and_string_size():
    result = hints.input_hints('app_control', {'operation': 'dispatch', 'args': {'action': 'x' * 2000}})
    assert len(result['actionPath']) == 180 and result['actionPath'].endswith('…')
    result = hints.input_hints('app_control', {'operation': 'dispatch', 'args': {'action': ' ' * 2000 + 'late'}})
    assert result['actionPath'].endswith('…')
