import copy
import json
import asyncio
import threading

import pytest

from amplifier_web import browser_detail, execution
from amplifier_web.canvas_library import fork_artifacts
from amplifier_web.event_log_view import EventIndex
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.image_generation import metadata, project
from amplifier_web.service import AppService, AppError
from test_generated_images import receipt


async def test_generated_image_uses_one_body_and_exact_origin_and_forks(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        session['messages'] = [{'id': 'origin', 'role': 'user', 'text': 'Draw it'},
                               {'id': 'later', 'role': 'user', 'text': 'Another request'}]
        app.state['view']['draft'] = 'Keep my draft'
        before = copy.deepcopy(app.state['canvas'])
        _, data = receipt(tmp_path, 'generated')
        args = {'sessionId': session['id'], 'messageId': 'origin', 'title': 'Generated', 'receiptPath': 'generated.json'}
        result = (await app.dispatch('outputs.attachImage', args, command_id='attach-generated'))['result']
        repeated = (await app.dispatch('outputs.attachImage', args, command_id='attach-generated'))['result']
        assert result['id'] == repeated['id']
        canvas = next(row for row in app.state['canvasArtifacts'] if row['id'] == result['canvasId'])
        assert canvas['body'] == result['body'] == canvas['contentResource']
        assert canvas['publications'] == [{'messageId': 'origin', 'version': 1}]
        assert canvas['versions'][0]['messageId'] == 'origin'
        assert canvas['imageRequestId'] == 'generated'
        from amplifier_web.state_records import load
        assert any(row['id'] == canvas['id'] for row in load(app.db)['canvasArtifacts'])
        assert app.outputs.content(result) == data
        assert app.state['canvas'] == before and app.state['view']['draft'] == 'Keep my draft'
        target = {'id': 'fork', 'messages': [{'id': 'origin'}]}
        mapping = fork_artifacts(app.state, session['id'], target, app.db)
        app.outputs.fork(session['id'], target, mapping)
        clone = app.outputs.store.list('fork')['items'][0]
        assert clone['canvasId'] == mapping[canvas['id']] != canvas['id']
        assert clone['body'] == result['body'] and app.outputs.content(clone) == data
        assert next(row for row in app.state['canvasArtifacts'] if row['id'] == clone['canvasId'])['sessionId'] == 'fork'
        app._save_changes()
        sid = session['id']
    finally:
        await app.close()
    restored = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        saved = restored.outputs.record(sid, result['id'])
        assert restored.outputs.content(saved) == data
        assert next(row for row in restored.state['canvasArtifacts'] if row['id'] == saved['canvasId'])['publications'] == canvas['publications']
    finally:
        await restored.close()


async def test_unanchored_image_stays_library_only_and_registry_failure_creates_no_canvas(tmp_path, monkeypatch):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        session['messages'] = [{'id': 'later', 'role': 'user', 'text': 'Do not attach here'}]
        receipt(tmp_path, 'image')
        args = {'sessionId': session['id'], 'title': 'Saved', 'receiptPath': 'image.json'}
        result = (await app.dispatch('outputs.attachImage', args))['result']
        row = next(row for row in app.state['canvasArtifacts'] if row['id'] == result['canvasId'])
        assert row['publications'] == [] and row['messageId'] is None
        before = copy.deepcopy(app.state['canvasArtifacts'])
        def reject(*args):
            raise ValueError('The output relationship limit is reached.')
        monkeypatch.setattr(app.outputs.store, 'create', reject)
        with pytest.raises(AppError, match='limit'):
            await app.dispatch('outputs.attachImage', {**args, 'messageId': 'later'})
        assert app.state['canvasArtifacts'] == before
    finally:
        await app.close()


async def test_origin_removed_during_image_read_is_not_published(tmp_path, monkeypatch):
    from amplifier_web import image_receipts
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = image_receipts.read_image_receipt
    def delayed(*args):
        value = original(*args)
        entered.set()
        if not release.wait(5):
            raise AssertionError('Test did not release image read')
        return value
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        session['messages'] = [{'id': 'origin', 'role': 'user', 'text': 'Draw it'}]
        receipt(tmp_path, 'race')
        monkeypatch.setattr(image_receipts, 'read_image_receipt', delayed)
        pending = asyncio.create_task(app.dispatch('outputs.attachImage', {'sessionId': session['id'],
            'title': 'Race', 'receiptPath': 'race.json', 'messageId': 'origin'}))
        assert await asyncio.to_thread(entered.wait, 3)
        async with app.lock:
            session['messages'] = [{'id': 'replacement', 'role': 'user', 'text': 'Revised request'}]
        release.set()
        with pytest.raises(AppError, match='original message changed'):
            await pending
        assert not app.outputs.store.list(session['id'])['items']
        assert not app.state.get('canvasArtifacts')
    finally:
        release.set()
        await app.close()


def test_live_and_saved_image_evidence_agree_without_prompts_or_paths(tmp_path):
    emitted = []
    observer = ExecutionEvents('root', emitted.append)
    observer.lifecycle({'type': 'input.delivered', 'input_id': 'turn'})
    args = {'action': 'generate', 'request_id': 'one', 'prompt': 'private prompt', 'output': '/private/path'}
    before = {'tool_name': 'image_generate', 'tool_call_id': 'call', 'tool_input': args}
    after = {'tool_call_id': 'call', 'result': {'success': True, 'output': {'status': 'completed', 'receiptPath': '/private/receipt'}}}
    observer.hook('root', 'tool:pre', before)
    from amplifier_web.runtime import normalize_event
    _, active = normalize_event(emitted[-1], 'root')
    assert active['imageGeneration'] == {'requestId': 'one', 'operation': 'generate'}
    assert 'private' not in json.dumps(active)
    observer.hook('root', 'tool:post', after)
    _, final = normalize_event(emitted[-1], 'root')
    path = tmp_path / 'events.jsonl'
    path.write_text(''.join(json.dumps({'event': name, 'timestamp': at, 'data': {'session_id': 'root', **data}}) + '\n'
                            for name, at, data in [('tool:pre', 1, before), ('tool:post', 2, after)]))
    index = EventIndex(path, 'root')
    index.refresh()
    saved = next(iter(index.nodes.values()))
    assert saved['imageGeneration'] == final['imageGeneration'] == {**active['imageGeneration'], 'outcome': 'completed'}


@pytest.mark.parametrize('action', ['capabilities', 'status', 'cancel', None, [], {}])
def test_inspection_is_not_generation(action):
    observer = ExecutionEvents('root', lambda row: None)
    observer.hook('root', 'tool:pre', {'tool_name': 'image_generate', 'tool_call_id': 'call',
                                      'tool_input': {'action': action, 'request_id': 'one'}})
    assert 'imageGeneration' not in observer.calls[('root', 'call')]


@pytest.mark.parametrize('value', [None, [], {'operation': [], 'requestId': 'one'},
    {'operation': 'generate', 'requestId': {}}, {'operation': 'edit', 'requestId': 'x' * 501}])
def test_malformed_image_metadata_is_ignored(value):
    assert metadata(value) is None


def test_malformed_outcome_does_not_break_valid_metadata():
    assert metadata({'operation': 'generate', 'requestId': 'one', 'outcome': []}) == {
        'operation': 'generate', 'requestId': 'one'}


def test_compact_projection_survives_collapsed_activity_and_stops_with_execution():
    session = {'id': 'root', 'status': 'working', 'messages': [{'id': 'origin', 'role': 'user', 'inputId': 'turn'}]}
    tree = execution.ensure_turn(session, 'turn')
    node = {'id': 'image', 'kind': 'tool', 'label': 'image_generate', 'turnId': 'turn', 'sessionId': 'root',
            'phase': 'running', 'imageGeneration': {'requestId': 'one', 'operation': 'generate'}}
    execution.ingest(session, node)
    session['messages'].append({'id': 'later', 'role': 'user'})
    state = browser_detail.project(session)
    assert state['execution']['nodes'] == []
    assert state['execution']['imageGeneration'] == [{'id': 'image', 'messageId': 'origin', 'phase': 'running', 'requestId': 'one', 'operation': 'generate'}]
    session['status'] = 'interrupted'
    assert browser_detail.project(session)['execution']['imageGeneration'][0]['phase'] == 'interrupted'
    tree['nodes'][0]['sessionId'] = 'unrelated'
    assert project(session, tree) == []
    tree['nodes'][0]['sessionId'] = 'root'
    tree['turns'][0]['anchorMessageId'] = None
    assert project(session, tree) == []


@pytest.mark.parametrize('result,outcome', [({'success': False}, 'error'), ({'success': True, 'output': {'status': 'unknown'}}, 'unknown')])
def test_failed_or_unknown_generation_never_claims_completed(result, outcome):
    observer = ExecutionEvents('root', lambda row: None)
    observer.hook('root', 'tool:pre', {'tool_name': 'image_generate', 'tool_call_id': 'call',
                                      'tool_input': {'action': 'edit', 'request_id': 'one'}})
    observer.hook('root', 'tool:post', {'tool_call_id': 'call', 'result': result})
    assert observer.calls[('root', 'call')]['imageGeneration']['outcome'] == outcome


@pytest.mark.parametrize('operation', ['generate', 'edit'])
def test_delegated_image_generation_uses_proven_parent_turn(operation):
    session = {'id': 'root', 'status': 'working', 'messages': [{'id': 'origin'}, {'id': 'later'}]}
    generation = {'requestId': 'tool:child:call', 'operation': operation}
    tree = {'turns': [{'id': 'root-turn', 'anchorMessageId': 'origin', 'phase': 'working'}],
            'nodes': [
                {'id': 'delegate', 'sessionId': 'root', 'turnId': 'root-turn'},
                {'id': 'worker', 'sessionId': 'child', 'parentId': 'delegate'},
                {'id': 'image', 'sessionId': 'child', 'parentId': 'worker', 'phase': 'running', 'imageGeneration': generation},
            ]}
    assert project(session, tree) == [{'id': 'image', 'messageId': 'origin', 'phase': 'running', **generation}]
    session['status'] = 'idle'
    assert project(session, tree)[0]['phase'] == 'interrupted'
    tree['nodes'][1]['parentId'] = 'image'
    assert project(session, tree) == []
    tree['nodes'][1]['parentId'] = 'missing'
    assert project(session, tree) == []


@pytest.mark.parametrize('operation,expected', [('generate', True), ('edit', True), ('analyze', False)])
def test_nano_generation_observed_without_exposing_prompt_or_paths(operation, expected):
    from amplifier_web.runtime import normalize_event
    emitted = []
    observer = ExecutionEvents('root', emitted.append)
    observer.hook('root', 'tool:pre', {'tool_name': 'nano-banana', 'tool_call_id': 'image-call',
        'tool_input': {'operation': operation, 'prompt': 'private prompt', 'output_path': '/private/output'}})
    _, active = normalize_event(emitted[-1], 'root')
    assert ('imageGeneration' in active) is expected
    assert 'private' not in json.dumps(active)
    observer.hook('root', 'tool:post', {'tool_call_id': 'image-call',
        'result': {'success': True, 'output': {'generated_images': ['/private/output']}}})
    _, final = normalize_event(emitted[-1], 'root')
    if expected:
        assert final['imageGeneration'] == {'requestId': 'tool:root:image-call', 'operation': operation, 'outcome': 'completed'}
    else:
        assert 'imageGeneration' not in final
