import json

from amplifier_web.browser_detail import project
from amplifier_web.chat_plan import latest_plan
from amplifier_web.event_log_view import EventLogView, event_path
from amplifier_web.session_projection import stored_execution


def tasks(status='in_progress'):
    return [{'content': 'Draft the team update', 'activeForm': 'Drafting the team update', 'status': status}]


def test_plan_reads_canonical_update_and_survives_empty_display_window(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'native'))
    session = {'id': 'app', 'runtimeSessionId': 'native', 'workspace': str(tmp_path),
               'messages': [], 'workers': [], 'status': 'idle'}
    path = event_path(session, 'native')
    path.parent.mkdir(parents=True)
    expected = [{'content': 'Complete task ' + str(i) + 'x'*100, 'activeForm': 'Working', 'status': 'pending'} for i in range(12)]
    rows = [
        ('tool:pre', {'tool_input': {'action': 'update', 'todos': expected}}),
        ('tool:post', {'result': {'success': True, 'output': {'status': 'updated', 'count': len(expected)}}}),
    ]
    path.write_text(''.join(json.dumps({'event': name, 'timestamp': 10+i, 'data': {
        'session_id': 'native', 'tool_name': 'todo', 'tool_call_id': 'plan', **data}})+'\n'
        for i, (name, data) in enumerate(rows)))
    original = path.read_bytes()
    session['execution'] = EventLogView(None).read(session)
    assert session['execution']['plan']['items'] == expected
    wire = project(session)
    assert wire['execution']['nodes'] == []
    assert wire['execution']['plan']['items'] == expected
    stored = stored_execution(session['execution'])
    assert 'plan' not in stored
    # A new process rebuilds the same plan, even without loaded messages.
    session['execution'] = stored
    assert EventLogView(None).read(session)['plan']['items'] == expected
    assert path.read_bytes() == original


def node(identity, *, sid='root', phase='completed', output=None, at=1):
    return {'id': identity, 'kind': 'tool', 'label': 'todo', 'sessionId': sid,
            'phase': phase, 'endedAt': at, 'output': {'success': True, 'output': output if output is not None else {'todos': tasks()}}}


def test_child_failed_and_pending_plans_cannot_replace_root_report():
    rows = [node('root'), node('child', sid='child', at=4),
            node('failed', phase='error', at=5), node('pending', phase='running', at=6)]
    assert latest_plan(rows, {'root'}, None)['id'] == 'root'
    assert latest_plan(rows, {'child'}, None)['id'] == 'child'


def test_clear_and_malformed_latest_are_not_replaced_with_stale_plan():
    assert latest_plan([node('old'), node('clear', output={'todos': []}, at=2)], {'root'}, None)['items'] == []
    assert latest_plan([node('old'), node('bad', output={'todos': 'not a list'}, at=2)], {'root'}, None)['unavailable']
    assert latest_plan([node('large', output={'todos': tasks()*201})], {'root'}, None)['unavailable']


def test_no_plan_without_successful_todo_and_no_invented_input_success():
    assert latest_plan([node('running', phase='running')], {'root'}, None) is None
    row = node('bad'); row['output'] = {'success': False}; row['input'] = {'action': 'update', 'todos': tasks()}
    assert latest_plan([row], {'root'}, None)['unavailable']
    row = node('unknown', output={'count': 1}); row['input'] = {'action': 'update', 'todos': tasks()}
    assert latest_plan([row], {'root'}, None)['unavailable']
