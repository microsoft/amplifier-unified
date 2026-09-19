"""Synthetic native files only: lazy browsing never becomes execution or storage."""
import json
import pytest

from amplifier_web.automatic_history import read_transcript
from amplifier_web.native_activity import apply_activity
from amplifier_web.session_files import sessions_dir, project_slug
from amplifier_web.host.storage import SessionStore


def fixture(tmp_path, monkeypatch, *, relocate=False):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    root = sessions_dir(workspace) / 'native-root'
    store = SessionStore(root.parent, shared=True)
    rows = [{'role': 'user', 'content': 'inspect fixture'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'tool-1', 'type': 'function',
                'function': {'name': 'inspect', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'tool-1', 'content': 'private tool result'},
            {'role': 'assistant', 'content': 'Done'}]
    store.save('native-root', rows, {'working_dir': str(workspace), 'bundle': 'anchors'})
    events_root = tmp_path / 'relocated' if relocate else root
    if relocate:
        monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(events_root))
        events_root = events_root / project_slug(workspace) / 'sessions' / 'native-root'
    capture = events_root / 'context-intelligence'
    capture.mkdir(parents=True)
    (capture / 'events.jsonl').write_text(''.join(json.dumps({'event': event, 'session_id': 'native-root',
        'timestamp': '2026-01-01T12:00:00Z', 'data': data}) + '\n' for event, data in [
        ('prompt:submit', {'prompt': 'inspect fixture'}),
        ('tool:pre', {'tool_call_id': 'tool-1', 'tool_name': 'inspect', 'arguments': {'secret': 'no'}}),
        ('tool:post', {'tool_call_id': 'tool-1', 'tool_name': 'inspect', 'result': 'private tool result'}),
        ('llm:response', {'purpose': 'session naming', 'response': 'private model result'}),
    ]))
    session = {'id': 'app-alias', 'nativeIdentity': 'native-root', 'nativeProject': project_slug(workspace),
               'workspace': str(workspace), 'createdAt': 0}
    return root, capture, session


def snapshot(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob('*') if path.is_file()}


def test_page_associates_exact_tool_lifecycle_without_payloads_or_file_changes(tmp_path, monkeypatch):
    root, capture, session = fixture(tmp_path, monkeypatch, relocate=True)
    # A legacy root event must never be used instead of relocated CI capture.
    (root / 'events.jsonl').write_text('{invalid legacy event')
    original = snapshot(tmp_path)
    result = read_transcript(session)
    assert [row['text'] for row in result['messages']] == ['inspect fixture', 'Done']
    node, = result['activity']['nodes']
    assert node['toolCallId'] == 'tool-1' and node['phase'] == 'completed'
    assert result['activity']['auxiliaryEvents'] == 1
    assert result['activity']['turns'][0]['anchorMessageId'] == result['messages'][0]['id']
    assert 'private tool result' not in json.dumps(result) and 'arguments' not in json.dumps(result)
    assert snapshot(tmp_path) == original
    assert not list(root.glob('checkpoint*'))


def test_corrupt_event_tail_never_invents_messages_and_scan_limit_is_explicit(tmp_path, monkeypatch):
    root, capture, session = fixture(tmp_path, monkeypatch)
    with (capture / 'events.jsonl').open('a') as stream:
        stream.write('{partial')
    result = read_transcript(session)
    assert len(result['messages']) == 2
    assert any(row['code'] == 'incomplete_event' for row in result['activity']['diagnostics'])
    monkeypatch.setattr('amplifier_web.native_activity.MAX_SCAN_EVENTS', 1)
    result = read_transcript(session)
    assert not result['activity']['nodes']
    assert any(row['code'] in {'activity_scan_limit', 'scan_limit'} for row in result['activity']['diagnostics'])


def test_backup_read_is_visible_and_event_details_are_not_persisted(tmp_path, monkeypatch):
    from amplifier_web.session_projection import persist
    root, _, session = fixture(tmp_path, monkeypatch)
    transcript = root / 'transcript.jsonl'
    transcript.with_suffix('.jsonl.backup').write_bytes(transcript.read_bytes())
    transcript.write_text('{partial')
    before = snapshot(root)
    result = read_transcript(session)
    assert len(result['messages']) == 2
    assert any('backup' in row['code'] for row in result['activity']['diagnostics'])
    assert snapshot(root) == before
    session.update(messages=result['messages'], runtimeSessionId='native-root')
    apply_activity(session, result['activity'])
    assert session['execution']['nodes']
    persist(tmp_path / 'app', {'sessions': [session]}, {})
    saved = json.loads((root / 'unified/view.json').read_text())
    assert not saved['execution']['nodes'] and 'historyActivity' not in saved


def test_native_activity_never_duplicates_existing_live_turn(tmp_path, monkeypatch):
    _, _, session = fixture(tmp_path, monkeypatch)
    result = read_transcript(session)
    session.update(messages=result['messages'], execution={'nodes': [], 'turns': [
        {'id': 'live-turn', 'anchorMessageId': result['messages'][0]['id']}], 'currentTurnId': None})
    apply_activity(session, result['activity'])
    assert len(session['execution']['turns']) == 1 and not session['execution']['nodes']


@pytest.mark.parametrize('override', ['', 'relative/path', '${UNSET}/capture', ' /${UNSET}/capture '])
def test_invalid_ci_relocation_keeps_native_capture(tmp_path, monkeypatch, override):
    _, _, session = fixture(tmp_path, monkeypatch)
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', override)
    result = read_transcript(session)
    assert result['activity']['nodes'][0]['toolCallId'] == 'tool-1'


def test_native_tool_activity_attaches_to_existing_web_message_identity(tmp_path, monkeypatch):
    _, _, session = fixture(tmp_path, monkeypatch)
    result = read_transcript(session)
    session['messages'] = [{**row, 'id': 'web-' + row['id']} for row in result['messages']]
    apply_activity(session, result['activity'])
    assert session['execution']['turns'][0]['anchorMessageId'] == session['messages'][0]['id']


def test_later_pages_anchor_tools_after_user_role_tool_result_without_shift(tmp_path, monkeypatch):
    root, capture, session = fixture(tmp_path, monkeypatch)
    rows = []
    for number in range(3):
        call = f'call-{number}'
        rows += [{'role': 'user', 'content': f'prompt-{number}'},
                 {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': call, 'name': 'inspect', 'input': {}}]},
                 {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': call, 'content': 'fixture result'}]},
                 {'role': 'assistant', 'content': f'answer-{number}'}]
    SessionStore(root.parent).save('native-root', rows, {'bundle': 'anchors'})
    (capture / 'events.jsonl').write_text(''.join(json.dumps({'event': 'tool:post', 'session_id': 'native-root',
        'data': {'tool_call_id': f'call-{number}', 'tool_name': 'inspect'}}) + '\n' for number in range(3)))
    latest = read_transcript(session, limit=2)
    earlier = read_transcript(session, before=latest['offset'], limit=2)
    assert latest['offset'] == 4 and latest['userOffset'] == 2
    assert latest['activity']['nodes'][0]['toolCallId'] == 'call-2'
    assert latest['activity']['turns'][0]['anchorMessageId'] == latest['messages'][0]['id']
    assert earlier['activity']['nodes'][0]['toolCallId'] == 'call-1'
    session['messages'] = latest['messages']
    apply_activity(session, latest['activity'])
    session['messages'] = earlier['messages'] + session['messages']
    apply_activity(session, earlier['activity'], append=True)
    assert len(session['execution']['nodes']) == 2
    assert {turn['anchorMessageId'] for turn in session['execution']['turns']} == {
        earlier['messages'][0]['id'], latest['messages'][0]['id']}


def test_physical_scan_limit_stops_invalid_and_unrelated_rows(tmp_path, monkeypatch):
    _, capture, session = fixture(tmp_path, monkeypatch)
    path = capture / 'events.jsonl'
    path.write_text('{bad\n' * 20 + path.read_text())
    monkeypatch.setattr('amplifier_web.native_activity.MAX_SCAN_EVENTS', 3)
    result = read_transcript(session)
    assert not result['activity']['nodes']
    diagnostics = result['activity']['diagnostics']
    assert sum(row['code'] == 'invalid_event' for row in diagnostics) == 3
    assert any(row['code'] == 'scan_limit' for row in diagnostics)


def test_byte_scan_limit_does_not_parse_oversized_raw_payload(tmp_path, monkeypatch):
    _, capture, session = fixture(tmp_path, monkeypatch)
    path = capture / 'events.jsonl'
    path.write_text(json.dumps({'event':'llm:response', 'session_id':'native-root',
                               'data': {'raw_response': 'x' * 2000}}) + '\n')
    monkeypatch.setattr('amplifier_web.native_activity.MAX_SCAN_BYTES', 100)
    result = read_transcript(session)
    assert result['activity']['scannedEvents'] == 0
    assert any(row['code'] == 'scan_limit' for row in result['activity']['diagnostics'])
