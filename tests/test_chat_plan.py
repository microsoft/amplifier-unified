import json

from test_automatic_history import app_factory

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


def test_workspace_plan_summary_uses_recorded_steps_not_tool_payloads():
    from amplifier_web.chat_plan import overview
    session = {'id': 'root', 'execution': {'nodes': [node('first', output={'todos': tasks('pending')}, at=1),
                                                     node('second', output={'todos': tasks('completed')}, at=2)]}}
    summary = overview(session)['plan']
    assert summary['done'] == summary['total'] == 1
    assert 'items' not in summary
    assert [row['done'] for row in summary['history']] == [0, 1]
    assert overview(session, include_items=True)['plan']['items'] == tasks('completed')
    session['execution']['nodes'].append(node('bad', output={'todos': False}, at=3))
    assert overview(session)['plan']['unavailable']


async def test_workspace_plan_query_avoids_hydration_children_and_transcript_association(app_factory, monkeypatch):
    app = app_factory()
    made = await app.dispatch('session.create', {'title': 'Plans'})
    session = app._session(made['sessionId'])
    identity = session.get('runtimeSessionId') or session['id']
    path = event_path(session, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps({'event': event, 'timestamp': at, 'data': data}) + '\n'
        for event, at, data in [
            ('delegate:agent_spawned', 1, {'session_id': identity, 'sub_session_id': 'huge-child', 'tool_call_id': 'child'}),
            ('tool:pre', 2, {'session_id': identity, 'tool_name': 'todo', 'tool_call_id': 'todo', 'tool_input': {'action': 'update', 'todos': tasks()}}),
            ('tool:post', 3, {'session_id': identity, 'tool_name': 'todo', 'tool_call_id': 'todo', 'result': {'success': True, 'output': {'status': 'updated'}}})]))
    def forbidden(*args, **kwargs):
        raise AssertionError('Plan listing must not hydrate chat history or project full execution')
    monkeypatch.setattr(app.cold_display, 'hydrate', forbidden)
    monkeypatch.setattr(app.event_log_view, 'refresh', forbidden)
    from amplifier_web.event_log_view import EventIndex
    monkeypatch.setattr(EventIndex, 'associations', forbidden)
    original = EventIndex.refresh
    scanned = []
    def observed(index):
        scanned.append(index.identity)
        return original(index)
    monkeypatch.setattr(EventIndex, 'refresh', observed)
    result = await app.dispatch('session.plans', {'ids': [session['id']]})
    plan = result['result']['items'][0]['plan']
    assert (plan['done'], plan['total']) == (0, 1)
    assert 'items' not in plan
    assert scanned == [identity]
    detail = await app.dispatch('session.plans', {'ids': [session['id']], 'includeItems': True})
    assert detail['result']['items'][0]['plan']['items'] == tasks()
