"""Exact review receipts clear stale navigation, never saved work or obligations."""
from copy import deepcopy

import pytest

from amplifier_web.service import AppService


def activity_for(app, sid):
    state = app.browser_state()
    row = next(row for row in state['chatNavigation']['items'] if row['id'] == sid)
    workspace = next(row for row in state['workspaceExplorer']['rows']
                     if row.get('path') == app._session(sid)['workspace'])
    return row['activity'], workspace['activityCounts']


@pytest.mark.parametrize('origin', ['ui', 'agent'])
async def test_review_survives_restart_and_invalidates_navigation_without_replaying(tmp_path, origin):
    app = AppService(tmp_path/'data', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app.state['selectedSessionId']
        await app.on_runtime_event('runtime.error', {'sessionId': sid, 'error': 'Fixture failure'})
        original = deepcopy(app._session(sid))
        assert activity_for(app, sid)[0]['kind'] == 'attention'
        key_before = app.projections.shell_key(app.state)
        item = next(item for item in app.state_context()['attention']['items']
                    if item['id'] == 'session:' + sid)
        args = {'ids': [item['id']], 'fingerprints': {item['id']: item['fingerprint']}}
        if origin == 'agent':
            await app.app_bridge('dispatch', {'action': 'attention.read', 'args': args}, sid)
        else:
            await app.dispatch('attention.read', args, origin='ui')
        assert app._session(sid) == original
        assert activity_for(app, sid) == ({'kind': 'idle', 'label': 'Idle'},
                                         {'attention': 0, 'working': 0, 'unread': 0, 'idle': 1})
        assert app.projections.shell_key(app.state) != key_before
        assert app.state_context()['chatNavigation']['items'][0]['activity']['kind'] == 'idle'
    finally:
        await app.close()
    app = AppService(tmp_path/'data', workspace=tmp_path)
    try:
        assert app._session(sid)['error'] == original['error']
        assert app._session(sid)['failure'] == original['failure']
        assert app._session(sid)['messages'] == original['messages']
        assert activity_for(app, sid)[0]['kind'] == 'idle'
        await app.on_runtime_event('runtime.error', {'sessionId': sid, 'error': 'Fixture failure'})
        assert activity_for(app, sid)[0]['kind'] == 'attention'
        await app.dispatch('attention.read', args)  # Old receipt cannot clear the new occurrence.
        assert activity_for(app, sid)[0]['kind'] == 'attention'
    finally:
        await app.close()


async def test_reviewed_failure_preserves_pending_approval_question_and_blocked_task(tmp_path):
    app = AppService(tmp_path/'data', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app.state['selectedSessionId']
        await app.on_runtime_event('runtime.error', {'sessionId': sid, 'error': 'Fixture failure'})
        item = next(item for item in app.state_context()['attention']['items']
                    if item['id'] == 'session:' + sid)
        session = app._session(sid)
        session['approvals'] = [{'id': 'approval', 'status': 'pending', 'tool': 'fixture'}]
        question = (await app.dispatch('question.create', {'sessionId': sid,
                     'prompt': 'Choose a path', 'required': True, 'dependency': 'Choose the report path'}))['result']
        app.state.setdefault('runtimeControl', {})[sid] = {'task.get': {'task': {'id': 'task', 'status': 'blocked'}}}
        app._publish()
        obligations = deepcopy((session['approvals'], session['questions'], app.state['runtimeControl'][sid]))
        await app.dispatch('attention.read', {'ids': [item['id']], 'fingerprints': {item['id']: item['fingerprint']}})
        assert activity_for(app, sid)[0]['label'] == 'Approval requested'
        assert app.coordination.snapshot({'sessionId': sid})['attention']
        assert (session['approvals'], session['questions'], app.state['runtimeControl'][sid]) == obligations
        # Reading the question notice does not answer it or clear its obligation.
        pending = next(row for row in app.state_context()['attention']['items'] if row['id'] == 'question:' + question['id'])
        await app.dispatch('attention.read', {'ids': [pending['id']], 'fingerprints': {pending['id']: pending['fingerprint']}})
        session['approvals'][0]['status'] = 'allow'
        app._publish()
        assert activity_for(app, sid)[0]['label'] == 'Answer requested'
        assert session['questions'][0]['status'] == 'pending'
        assert app.coordination.snapshot({'sessionId': sid})['attention']
        await app.dispatch('question.cancel', {'sessionId': sid, 'id': question['id'],
                           'expectedRevision': question['revision'], 'reason': 'Fixture resolution'})
        assert activity_for(app, sid)[0]['label'] == 'Work blocked'
        blocked_key = app.projections.shell_key(app.state)
        app.state['runtimeControl'][sid]['task.get']['task']['status'] = 'active'
        app._publish()
        assert activity_for(app, sid)[0]['kind'] == 'idle'
        assert app.projections.shell_key(app.state) != blocked_key
    finally:
        await app.close()
