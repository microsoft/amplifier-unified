import json

import pytest

from test_automatic_history import app_factory

from amplifier_web.browser_detail import project
from amplifier_web.chat_plan import latest_plan
from amplifier_web.event_log_view import EventLogView, event_path
from amplifier_web.session_projection import stored_execution


def tasks(status='in_progress'):
    return [{'content': 'Draft the team update', 'activeForm': 'Drafting the team update', 'status': status}]


def test_explicit_plan_read_remains_separate_from_last_reported_display(tmp_path, monkeypatch):
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
    assert 'plan' not in session['execution']
    assert EventLogView(None).read_plan(session, include_items=True)['plan']['items'] == expected
    wire = project(session)
    assert wire['execution']['nodes'] == []
    assert wire['execution']['plan'] is None
    stored = stored_execution(session['execution'])
    assert 'plan' not in stored
    # Explicit query still reads historical receipts; ordinary reopen does not.
    session['execution'] = stored
    assert 'plan' not in EventLogView(None).read(session)
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
    initial = app.state['selectedSessionId']
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
    # A saved but dormant row is the important case: hydrate=False alone still
    # admits it and expands the shared live overlay on every workspace page.
    app._state['sessions'][:] = [row for row in app._state['sessions'] if row['id'] != session['id']]
    app._state['selectedSessionId'] = initial
    app.projections.invalidate()
    admitted = [row['id'] for row in app._state['sessions']]
    monkeypatch.setattr(app, '_admit_session', forbidden)
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
    selected = app.state['selectedSessionId']
    original_bytes = path.read_bytes()
    result = await app.dispatch('session.plans', {'ids': [session['id']]})
    plan = result['result']['items'][0]['plan']
    assert (plan['done'], plan['total']) == (0, 1)
    assert 'items' not in plan
    assert scanned == [identity]
    detail = await app.dispatch('session.plans', {'ids': [session['id']], 'includeItems': True})
    assert detail['result']['items'][0]['plan']['items'] == tasks()

    # A browsing query must not acquire an execution worker or alter saved work.
    assert app.state['selectedSessionId'] == selected
    assert app.runtime.started == app.runtime.sent == app.runtime.stopped == []
    assert [row['id'] for row in app._state['sessions']] == admitted
    assert path.read_bytes() == original_bytes
    assert result['effects'] == detail['effects'] == []
    before_rejections = list(scanned)
    from amplifier_web.service import AppError
    with pytest.raises(AppError):
        await app.dispatch('session.plans', {'ids': [f'chat-{i}' for i in range(51)]})
    with pytest.raises(AppError, match='one chat'):
        await app.dispatch('session.plans', {'ids': [session['id'], 'another-chat'], 'includeItems': True})
    assert scanned == before_rejections
    assert app.runtime.started == app.runtime.sent == app.runtime.stopped == []


async def test_plan_sources_preserve_native_alias_ambiguity_and_delete_guards(app_factory, tmp_path, monkeypatch):
    from amplifier_web.native_catalog import NativeCatalog
    from amplifier_web.service import AppError
    from test_native_directory import seed
    app = app_factory()
    await app.dispatch('session.create', {'title': 'Resident deletion guard'})
    catalog = NativeCatalog(tmp_path / 'catalog.sqlite3', tmp_path / 'native')
    seed(catalog, tmp_path / 'one', 1)
    app.directory.bind(catalog)
    before = [row['id'] for row in app._state['sessions']]
    sources = []
    monkeypatch.setattr(app.event_log_view, 'read_plan',
        lambda source, **kwargs: (sources.append(source), {'sessionId': source['id'], 'plan': None})[1])
    monkeypatch.setattr(app, '_admit_session', lambda *_: pytest.fail('Plan lookup admitted a dormant chat'))
    await app.dispatch('session.plans', {'ids': ['saved-00000']})
    assert sources[-1]['nativeIdentity'] == 'saved-00000' and sources[-1]['nativeProject']
    seed(catalog, tmp_path / 'two', 1)
    with pytest.raises(AppError, match='more than one project') as ambiguous:
        await app.dispatch('session.plans', {'ids': ['saved-00000']})
    assert ambiguous.value.status == 409
    one = catalog.lookup('saved-00000')[0]
    await app.dispatch('session.plans', {'ids': [one['id']]})
    assert sources[-1]['nativeProject'] == one['nativeProject']
    assert sources[-1]['id'] == one['id']
    with pytest.raises(AppError) as missing:
        await app.dispatch('session.plans', {'ids': ['missing']})
    assert missing.value.status == 404
    resident = app._state['sessions'][0]
    resident['runtimeSessionId'] = 'resident-alias'
    resident['_deleting'] = True
    for identity in (resident['id'], 'resident-alias'):
        with pytest.raises(AppError, match='being deleted') as deleting:
            await app.dispatch('session.plans', {'ids': [identity]})
        assert deleting.value.status == 409
    assert [row['id'] for row in app._state['sessions']] == before


async def report(app, observer, status='in_progress', *, call=None, sid=None, outcome='success'):
    from amplifier_web.runtime import normalize_event
    sid = sid or observer.root_id
    call = call or f'plan-{len(observer.nodes)}'
    observer.hook(sid, 'tool:pre', {'session_id': sid, 'tool_name': 'todo',
        'tool_call_id': call, 'tool_input': {'action': 'update', 'todos': tasks(status)}})
    value = {'success': True, 'output': {'status': 'updated'}}
    if outcome == 'failure':
        value = {'success': False, 'output': {'todos': tasks('completed')}}
    elif outcome == 'malformed':
        value = {'success': True, 'output': {'todos': 'not a list'}}
    elif outcome == 'clear':
        value = {'success': True, 'output': {'todos': []}}
    observer.hook(sid, 'tool:post', {'session_id': sid, 'tool_name': 'todo',
        'tool_call_id': call, 'result': value})
    for event in observer.test_events:
        await app.on_runtime_event(*normalize_event(event, observer.root_id, 'input'))
    observer.test_events.clear()


def observer_for(sid):
    from amplifier_web.execution_events import ExecutionEvents
    events = []
    observer = ExecutionEvents(sid, events.append)
    observer.test_events = events
    return observer


def no_plan_history_reads(monkeypatch):
    from amplifier_web.event_log_view import EventIndex
    def forbidden(*args, **kwargs):
        pytest.fail('Ordinary Plan must not read historical events')
    monkeypatch.setattr(EventIndex, 'refresh', forbidden)
    monkeypatch.setattr(EventLogView, 'read_plan', forbidden)
    monkeypatch.setattr(EventLogView, 'read', forbidden)


@pytest.mark.parametrize('batched', [False, True])
async def test_last_report_persists_reopens_clears_and_ignores_failed_child_reports(
        app_factory, tmp_path, monkeypatch, batched):
    from amplifier_web.runtime import normalize_event
    from amplifier_web.state_records import load
    no_plan_history_reads(monkeypatch)
    app = app_factory()
    await app.dispatch('session.create', {'title': 'Reported Plan'})
    session = app._session()
    sid = session['id']
    assert project(session).get('execution', {}).get('plan') is None
    observer = observer_for(sid)
    await report(app, observer)
    assert project(session)['execution']['plan']['items'] == tasks()
    assert '_reportedPlan' not in project(session)
    first = json.dumps(session['_reportedPlan'])
    for outcome in ('failure', 'malformed'):
        await report(app, observer, outcome=outcome)
        assert json.dumps(session['_reportedPlan']) == first
    await report(app, observer, sid='child', status='completed')
    assert json.dumps(session['_reportedPlan']) == first
    # Defense at normalization and ingestion, not just the producer's child filter.
    forged = {'id': 'child-report', 'kind': 'tool', 'label': 'todo', 'phase': 'completed',
              'sessionId': 'child', 'rootSessionId': sid, 'reportedPlan': tasks('completed')}
    normalized = normalize_event({'type': 'execution.event', 'event': forged}, sid, 'input')
    assert 'reportedPlan' not in normalized[1]
    await app.on_runtime_event('execution.event', forged)
    assert json.dumps(session['_reportedPlan']) == first
    if batched:
        event = {'id': 'batch-plan', 'revision': 1, 'kind': 'tool', 'label': 'todo',
                 'phase': 'completed', 'sessionId': sid, 'rootSessionId': sid,
                 'reportedPlan': tasks('completed')}
        await app.on_runtime_events([('execution.event', event)])
    else:
        await report(app, observer, status='completed')
    assert session['_reportedPlan']['items'] == tasks('completed')
    # Terminal publication persisted the existing view before another model turn.
    assert load(app.db)['sessions']
    home, workspace = app.data_dir, tmp_path / 'web-workspace'
    await app.close()
    reopened = app_factory(home=home, workspace=workspace)
    restored = reopened._session(sid, hydrate=False)
    assert project(restored)['execution']['plan']['items'] == tasks('completed')
    # A fresh tool coordinator is empty; the UI report is deliberately separate.
    observer = observer_for(sid)
    assert observer.plan_inputs == {}
    await report(reopened, observer, outcome='clear', call='clear')
    assert project(restored)['execution']['plan']['items'] == []
    await reopened.close()
    again = app_factory(home=home, workspace=workspace)
    assert project(again._session(sid, hydrate=False))['execution']['plan']['items'] == []
    assert again.runtime.started == again.runtime.sent == again.runtime.stopped == []


async def test_last_report_does_not_reconstruct_large_edited_legacy_logs(app_factory, monkeypatch):
    app = app_factory()
    await app.dispatch('session.create', {})
    session = app._session()
    path = event_path(session, session['id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as stream:
        stream.write(b'old arbitrary bytes' * 100000)
    no_plan_history_reads(monkeypatch)
    assert project(session).get('execution', {}).get('plan') is None
    await report(app, observer_for(session['id']))
    expected = tasks()
    path.write_bytes(b'rewritten non-winner becomes winner; not parsed\n')
    assert project(session)['execution']['plan']['items'] == expected
    assert session['_reportedPlan']['items'] == expected
    assert app.runtime.started == app.runtime.sent == app.runtime.stopped == []


def test_last_report_follows_existing_node_revision_rejection():
    from amplifier_web.execution import ingest
    session = {'id': 'root'}
    event = {'id': 'tool:root:plan', 'kind': 'tool', 'label': 'todo', 'phase': 'completed',
             'sessionId': 'root', 'rootSessionId': 'root', 'revision': 2,
             'reportedPlan': tasks('completed')}
    ingest(session, event)
    ingest(session, {**event, 'revision': 1, 'reportedPlan': tasks('pending')})
    assert session['_reportedPlan']['items'] == tasks('completed')
    assert 'reportedPlan' not in session['execution']['nodes'][0]
    ingest(session, {**event, 'revision': 3, 'reportedPlan': []})
    assert session['_reportedPlan']['items'] == []


def test_report_proposals_and_fields_are_bounded_and_not_tool_cache_history(monkeypatch):
    monkeypatch.delenv('AMPLIFIER_STATUS_DIR', raising=False)
    observer = observer_for('root')
    for number in range(40):
        observer.hook('root', 'tool:pre', {'tool_name': 'todo', 'tool_call_id': str(number),
            'tool_input': {'action': 'update', 'todos': tasks()}})
    assert len(observer.plan_inputs) == 32
    observer.hook('root', 'tool:post', {'tool_name': 'todo', 'tool_call_id': '0',
        'result': {'success': True, 'output': {'status': 'updated'}}})
    assert observer.test_events[-1]['event']['reportedPlan'] == tasks()
    assert all('reportedPlan' not in row for row in observer.calls.values())
    assert all('reportedPlan' not in row for row in observer.nodes.values())
    from amplifier_web.chat_plan import accepted_report
    assert accepted_report({'todos': [{**tasks()[0], 'private': 'secret'}]}) == tasks()
    for bad in (tasks() * 201, [{**tasks()[0], 'content': 'x' * 4001}], [{'status': 'made-up'}]):
        assert accepted_report({'todos': bad}) is None
    assert accepted_report({'success': False, 'todos': tasks()}) is None


def test_native_reference_retains_only_explicit_last_report(tmp_path):
    from amplifier_web.session_projection import persist, hydrate
    session = {'id': 'native', 'nativeIdentity': 'native', 'nativeProject': 'p',
               'workspace': str(tmp_path), 'historyManaged': True,
               '_reportedPlan': {'items': tasks()}, 'titleSource': 'native'}
    saved = persist(tmp_path, {'sessions': [session]}, {})
    assert saved['sessions'][0]['$native']
    assert saved['sessions'][0]['_reportedPlan'] == session['_reportedPlan']
    hydrate(tmp_path, saved, None)
    assert saved['sessions'][0]['_reportedPlan']['items'] == tasks()


async def test_superseded_owner_cannot_replace_report_but_keeps_execution_evidence(app_factory):
    app = app_factory()
    await app.dispatch('session.create', {})
    session = app._session()
    sid = session['id']
    app.runtime.workers = {sid: {'runtimeOwnerId': 'current'}}
    event = {'id': 'accepted', 'revision': 1, 'kind': 'tool', 'label': 'todo', 'phase': 'completed',
             'sessionId': sid, 'rootSessionId': sid, 'runtimeOwnerId': 'current',
             'reportedPlan': tasks('completed')}
    await app.on_runtime_event('execution.event', event)
    await app.on_runtime_events([('execution.event', {**event, 'id': 'late-old',
        'runtimeOwnerId': 'old', 'reportedPlan': tasks('pending')})])
    assert session['_reportedPlan']['items'] == tasks('completed')
    assert any(row['id'] == 'late-old' for row in session['execution']['nodes'])
    app.runtime.workers.clear()


def test_reused_call_cannot_publish_another_attempts_proposal():
    observer = observer_for('root')
    for status in ('pending', 'completed'):
        observer.hook('root', 'tool:pre', {'tool_name': 'todo', 'tool_call_id': 'same',
            'tool_input': {'action': 'update', 'todos': tasks(status)}})
    terminal = {'tool_name': 'todo', 'tool_call_id': 'same',
                'result': {'success': True, 'output': {'status': 'updated'}}}
    observer.hook('root', 'tool:post', terminal)
    assert 'reportedPlan' not in observer.test_events[-1]['event']
    # Exact input on the successful terminal or a full-list result is sufficient.
    observer.hook('root', 'tool:post', {**terminal,
        'tool_input': {'action': 'update', 'todos': tasks('pending')}})
    assert observer.test_events[-1]['event']['reportedPlan'] == tasks('pending')
    observer.hook('root', 'tool:post', {**terminal, 'result': {'success': True, 'output': {'todos': []}}})
    assert observer.test_events[-1]['event']['reportedPlan'] == []
