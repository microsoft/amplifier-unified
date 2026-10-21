"""Image lifecycle hints stay compact; explicit saved inspection stays exact."""
import json

import pytest

from amplifier_web import browser_detail, execution
from amplifier_web.event_log_view import EventIndex
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import normalize_event
from amplifier_web.tool_observations import input_hints

TOOLS = [('image_generate', 'generate'), ('nano-banana', 'generate'), ('nano-banana', 'analyze')]


def no_hints(row):
    assert row.get('inputSummary', {}) == {}
    assert row.get('inputPreview', '') == ''
    assert row.get('actionPath', '') == ''
    assert row.get('outcomeSummary', {}) == {}


@pytest.mark.parametrize('tool,operation', TOOLS)
@pytest.mark.parametrize('terminal', ['tool:post', 'tool:error'])
def test_terminal_without_name_keeps_image_identity_and_clears_private_hints(tmp_path, tool, operation, terminal):
    emitted = []
    observer = ExecutionEvents('root', emitted.append)
    args = {'action': operation, 'operation': operation, 'request_id': 'image',
            'prompt': 'private prompt', 'output_path': '/private/output'}
    before = {'tool_name': tool, 'tool_call_id': 'call', 'tool_input': args}
    after = {'tool_call_id': 'call', 'tool_input': args,
             'result': {'success': terminal == 'tool:post', 'output': {'status': 'completed'}}}
    if terminal == 'tool:error':
        after['error'] = 'failed with private prompt at /private/output'
    events = [('tool:pre', before), (terminal, after)]
    for event, data in events:
        observer.hook('root', event, data)
        observed = emitted[-1]['event']
        assert observed['tool'] == tool
        no_hints(observed)
        _, public = normalize_event(emitted[-1], 'root')
        no_hints(public)
        assert 'private' not in json.dumps(public)
        session = {}; execution.ingest(session, public)
        no_hints(session['execution']['nodes'][0])

    # Native index knows the original label even if the terminal omits its name.
    path = tmp_path / 'events.jsonl'
    path.write_text(''.join(json.dumps({'event': event, 'data': data}) + '\n' for event, data in events))
    index = EventIndex(path, 'root'); index.refresh()
    saved = next(iter(index.nodes.values()))
    assert saved['inputSummary'] == {} and saved['inputPreview'] == ''
    assert 'private prompt' in saved['input'] and 'output' in saved['_eventFields']
    no_hints(browser_detail.compact(saved, 'root', 'nodes', 512))


@pytest.mark.parametrize('identity', [{'tool': 'image_generate'}, {'label': 'nano-banana'},
                                     {'tool': 'nano-banana', 'label': 'Image action'}])
def test_supplied_image_hints_are_fenced_at_all_public_boundaries(identity):
    event = {'id': 'image', 'kind': 'tool', **identity, 'inputSummary': {'prompt': 'private prompt'},
             'inputPreview': 'private output', 'actionPath': 'private action',
             'outcomeSummary': {'error': 'private result', 'exitCode': 1}}
    session = {'execution': {'nodes': [{**event}], 'turns': []}}
    execution.ingest(session, event)
    no_hints(session['execution']['nodes'][0])
    no_hints(normalize_event({'type': 'execution.event', 'event': event}, 'root')[1])
    compact = browser_detail.compact(event, 'root', 'nodes', 512)
    no_hints(compact)
    assert 'private' not in json.dumps(compact)


@pytest.mark.parametrize('tool,operation', TOOLS)
async def test_image_saved_windows_keep_exact_inputs_results_and_read_scope(tmp_path, monkeypatch, tool, operation):
    from test_work_windows import fixture
    from test_tool_window_records import observed
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    args = {'operation': operation, 'action': operation, 'request_id': 'one',
            'prompt': 'private prompt ' * 100, 'output_path': '/private/output'}
    result = {'success': True, 'output': {'status': 'completed', 'receiptPath': '/private/receipt'}}
    try:
        observed(session, path, name=tool, arguments=args, result=result)
        before = path.read_bytes()
        live_before = json.dumps(session, sort_keys=True)
        await owner.read('s')
        row = owner.live_pages.read(session, 't@user')['items'][0]
        no_hints(row)
        assert row['contentAvailability']['status'] == 'linked'
        for field, expected in [('input', args), ('output', result)]:
            reference = row[field + 'Detail']
            text = (await owner.read_text('s', reference))['value']
            assert json.loads(text) == expected
            with pytest.raises(ValueError):
                await owner.read_text('foreign', reference)
        assert path.read_bytes() == before and json.dumps(session, sort_keys=True) == live_before
    finally:
        await owner.close()


def test_non_image_prompt_preview_remains_useful():
    assert input_hints('other_tool', {'prompt': 'public query'})['inputSummary'] == {'prompt': 'public query'}
    assert input_hints('other_tool', {'prompt': 'public query'})['inputPreview'] == 'prompt="public query"'


def test_prior_image_checkpoint_is_rebuilt_without_discarding_saved_arguments(tmp_path):
    import gzip
    from amplifier_web.event_index_checkpoint import Checkpoints
    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps({'event': 'tool:pre', 'data': {'tool_name': 'image_generate',
        'tool_call_id': 'image', 'tool_input': {'prompt': 'private prompt'}}}) + '\n')
    index = EventIndex(path, 'root'); index.refresh()
    cache = Checkpoints(tmp_path / 'host'); cache.save(index)
    target = cache.path(path, 'root')
    old = json.loads(gzip.decompress(target.read_bytes()))
    old['schema'] = 4
    target.write_bytes(gzip.compress(json.dumps(old).encode()))
    restored = EventIndex(path, 'root')
    assert not cache.load(restored)
    restored.refresh()
    row = next(iter(restored.nodes.values()))
    assert row['inputSummary'] == {} and row['inputPreview'] == ''
    assert json.loads(row['input']) == {'prompt': 'private prompt'}
