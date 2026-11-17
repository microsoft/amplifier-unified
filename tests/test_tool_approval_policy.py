"""Private worker facts are the only ordinary descriptor producer."""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web.approval_policy import PRODUCER, descriptor
from amplifier_web.runtime import RuntimeManager
from amplifier_web.tool_approval_scope import canonical


def fixture(tmp_path):
    scope = {'version': 1, 'producer': PRODUCER, 'key': 'a' * 64, 'description': 'Exact file call.',
             'allowOptionId': 'allow', 'authorityLifetime': 'request'}
    session = {'id': 'chat', 'workspace': str(tmp_path/'workspace')}
    approval = {'id': 'request', 'status': 'pending', 'options': ['allow', 'deny']}
    manager = RuntimeManager.__new__(RuntimeManager)
    row = {'process': SimpleNamespace(returncode=None), 'approvalScopes': {'request': {
        'owner': str(tmp_path/'app'), 'workspace': session['workspace'], 'descriptor': scope}}}
    manager.workers = {'chat': row}
    service = SimpleNamespace(data_dir=tmp_path/'app', runtime=manager)
    return service, session, approval, row, scope


def test_private_live_worker_fact_and_detached_descriptor(tmp_path):
    service, session, approval, row, scope = fixture(tmp_path)
    assert descriptor(service, session, approval) == scope
    value = service.runtime.approval_scope('chat', 'request')
    value['descriptor']['key'] = 'b' * 64
    assert descriptor(service, session, approval) == scope
    row['approvalScopes'].clear()
    approval.update(toolScope=value, policyScope=scope)
    assert descriptor(service, session, approval) is None


@pytest.mark.parametrize('fault', ['dead', 'closing', 'yielding', 'yielded', 'owner', 'workspace',
    'lifetime', 'producer', 'boolean-version', 'key', 'options', 'resolved', 'long-description'])
def test_expired_or_unverified_worker_scope_never_becomes_reusable(tmp_path, fault):
    service, session, approval, row, scope = fixture(tmp_path)
    if fault == 'dead': row['process'].returncode = 0
    elif fault in {'closing', 'yielding', 'yielded'}: row[fault] = True
    elif fault in {'owner', 'workspace'}: row['approvalScopes']['request'][fault] += '-other'
    elif fault == 'lifetime': scope['authorityLifetime'] = 'conversation'
    elif fault == 'producer': scope['producer'] = 'invented'
    elif fault == 'boolean-version': scope['version'] = True
    elif fault == 'key': scope['key'] = 'x' * 64
    elif fault == 'options': approval['options'] = [['allow'], 'deny']
    elif fault == 'resolved': approval['status'] = 'allow'
    else: scope['description'] = 'é' * 257
    assert descriptor(service, session, approval) is None


@pytest.mark.parametrize('value', [float('nan'), float('inf'), {1: 'key'}, {'a': (1, 2)}, Path('/path')])
def test_non_json_arguments_have_no_exact_canonical_key(value):
    with pytest.raises((TypeError, ValueError)):
        canonical(value)


@pytest.mark.parametrize('state', ['current', 'changed', 'missing', 'ordinary-once'])
async def test_service_binds_decision_to_current_private_scope(tmp_path, monkeypatch, state):
    import asyncio
    from unittest.mock import AsyncMock
    from amplifier_web.service import AppService, AppError

    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    runtime = SimpleNamespace(close=AsyncMock())
    delivered, calls = asyncio.Event(), []

    async def approval(*args):
        calls.append(args)
        delivered.set()

    runtime.approval = approval
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    session = app._new_session({'title': 'Exact approval'})
    app.state['sessions'] = [session]
    app.state['selectedSessionId'] = session['id']
    scope = {'version': 1, 'producer': PRODUCER, 'key': 'a' * 64,
             'description': 'Exact file call.', 'allowOptionId': 'allow',
             'authorityLifetime': 'request'}
    fact = {'owner': str(app.data_dir.resolve()),
            'workspace': str(Path(session['workspace']).resolve()), 'descriptor': scope}
    runtime.approval_scope = lambda sid, aid: (
        copy.deepcopy(fact) if state != 'missing' and (sid, aid) == (session['id'], 'request') else None)
    try:
        await app.on_runtime_event('approval.requested', {
            'sessionId': session['id'], 'id': 'request', 'prompt': 'Exact call',
            'options': ['allow', 'deny']})
        row = next(a for a in session['approvals'] if a['id'] == 'request')
        args = {'sessionId': session['id'], 'id': 'request', 'decision': 'allow',
                'expectedRequestHash': app.approval_request_hash(session, row)}
        if state != 'ordinary-once':
            args['expectedApprovalScope'] = scope['key']
        if state == 'changed':
            scope['key'] = 'b' * 64
        if state in {'changed', 'missing'}:
            with pytest.raises(AppError, match='exact approval target changed'):
                await app.dispatch('approval.respond', args, origin='ui', command_id='decision')
            assert row['status'] == 'pending' and not calls
        else:
            result = await app.dispatch('approval.respond', args, origin='ui', command_id='decision')
            assert result['accepted']
            await asyncio.wait_for(delivered.wait(), 2)
            expected = (session['id'], 'request', 'allow')
            if state == 'current':
                expected += (None, scope['key'])
            assert calls == [expected]
            duplicate = await app.dispatch('approval.respond', args, origin='ui', command_id='decision')
            assert duplicate['duplicate'] and calls == [expected]
    finally:
        await app.close()
