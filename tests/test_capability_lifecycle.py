"""Expired permission cards clear without reviving authority or tool work."""
import copy
import time
import pytest

from test_configuration_authority import app
from test_coordination_generation import agent
from test_automatic_history import app_factory


@pytest.mark.parametrize('boundary', ['stop', 'expiry', 'workspace'])
async def test_stale_card_is_retired_and_late_allow_is_an_idempotent_noop(app, boundary):
    source = app.state['sessions'][0]
    result = await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['file_access'], 'scopes':['local'], 'reason':'Change shared file rules'}, 'old-request',
        _configurationWait=True)
    grant = result['result']
    if boundary == 'stop':
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    elif boundary == 'workspace':
        source['workspace'] = str(app.data_dir / 'moved')
    else:
        grant['expiresAt'] = time.time()-1
        app.configuration_authority.save(grant)
    messages = copy.deepcopy(source['messages'])
    calls = list(app.runtime.calls)
    app._publish_changes(sessions={source['id']})
    assert source['approvals'][0]['status'] == 'expired'
    args = {'sessionId':source['id'], 'id':grant['id'], 'decision':'allow'}
    receipt = await app.dispatch('approval.respond', args, command_id='late-click')
    assert receipt['accepted'] and not receipt['decisionApplied']
    assert receipt['result']['status'] == 'expired'
    assert (await app.dispatch('approval.respond', args, command_id='late-click'))['duplicate']
    assert source['messages'] == messages and app.runtime.calls == calls
    assert not app.configuration_authority.valid(receipt['result'], source)


@pytest.mark.parametrize('native_wait', [False, True])
@pytest.mark.parametrize('dormant', [False, True])
async def test_restart_reconciles_exact_owner_without_starting_worker(app_factory, native_wait, dormant):
    app = app_factory()
    selected = app._new_session({'title':'Selected'})
    target = app._new_session({'title':'Waiting'})
    target['status'] = 'idle' if dormant else 'working'
    app.state['sessions'].extend([selected, target])
    app.state['selectedSessionId'] = selected['id']
    grant = {'id':'capability:saved', 'sessionId':target['id'], 'workspace':target['workspace'],
        'interruptionRevision':0, 'revision':1, 'status':'pending', 'createdAt':time.time(),
        'effects':['file_access'], 'scopes':['local'], 'reason':'Saved scope', 'nativeWait':native_wait}
    app.configuration_authority.save(grant)
    target['approvals'] = [{'id':grant['id'], 'status':'pending'}]
    app._save()
    await app.close()
    restored = app_factory(home=app.data_dir)
    current = restored._session(target['id'])
    assert current['approvals'][0]['status'] == ('expired' if native_wait else 'pending')
    assert not restored.runtime.started and not restored.runtime.sent
    await restored.close()


async def test_old_test_only_request_is_automatic_but_host_setup_and_mixed_requests_still_ask(app):
    source = app.state['sessions'][0]
    for effects, scopes, pending in [(['test_environments'], ['session'], False),
            (['test_host_setup'], ['global'], True),
            (['test_environments', 'file_access'], ['session'], True)]:
        result = await agent(app, source, 'capability.request', {'sessionId':source['id'],
            'effects':effects, 'scopes':scopes, 'reason':'Requested task'}, 'request-'+effects[-1])
        assert (result['result']['status'] == 'pending') is pending
    assert len(source['approvals']) == 2


async def test_upgrade_clears_old_test_card_without_granting_or_executing(app):
    source = app.state['sessions'][0]
    grant = {'id':'capability:old-test', 'sessionId':source['id'], 'workspace':source['workspace'],
        'interruptionRevision':0, 'revision':1, 'status':'pending', 'createdAt':time.time(),
        'effects':['test_environments'], 'scopes':['session'], 'reason':'Test', 'nativeWait':True}
    app.configuration_authority.save(grant)
    source['approvals'] = [{'id':grant['id'], 'status':'pending'}]
    calls = list(app.runtime.calls)
    app._publish_changes(sessions={source['id']})
    assert source['approvals'][0]['resolutionReason'] == 'approval_not_required'
    assert source['approvals'][0]['status'] == 'expired'
    assert app.runtime.calls == calls


@pytest.mark.parametrize('decision', ['allow', 'deny'])
async def test_note_is_retained_and_sent_with_live_capability_decision_once(app, decision):
    from test_coordination_generation import until
    source = app.state['sessions'][0]
    calls = []
    async def approval(sid, identity, selected, comment=None):
        calls.append((sid, identity, selected, comment))
    app.runtime.approval = approval
    grant = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['file_access'], 'scopes':['local'], 'reason':'Requested change'}, 'noted',
        _configurationWait=True))['result']
    args = {'sessionId':source['id'], 'id':grant['id'], 'decision':decision,
            'comment':'Use the existing workspace folder instead.'}
    receipt = await app.dispatch('approval.respond', args, command_id='with-note')
    await until(lambda: len(calls) == 1)
    assert receipt['result']['comment'] == args['comment']
    assert calls[0][2:] == (decision, args['comment'])
    messages = [m for m in source['messages'] if m.get('approvalId') == grant['id']]
    assert len(messages) == 1 and args['comment'] in messages[0]['text']
    await app.dispatch('approval.respond', args, command_id='with-note')
    assert len([m for m in source['messages'] if m.get('approvalId') == grant['id']]) == 1


async def test_expired_note_is_retained_without_runtime_delivery(app):
    source = app.state['sessions'][0]
    grant = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['file_access'], 'scopes':['local'], 'reason':'Requested change'}, 'expired-note',
        _configurationWait=True))['result']
    source['interruptionRevision'] = 2
    async def forbidden(*args, **kwargs):
        pytest.fail('Expired waiter must not be contacted')
    app.runtime.approval = forbidden
    args = {'sessionId':source['id'], 'id':grant['id'], 'decision':'deny', 'comment':'Use a local alternative.'}
    receipt = await app.dispatch('approval.respond', args, command_id='old-note')
    assert receipt['decisionApplied'] is False
    assert receipt['result']['comment'] == args['comment']
    await app.dispatch('approval.respond', args, command_id='old-note')
    assert len([m for m in source['messages'] if m.get('approvalId') == grant['id']]) == 1


async def test_failed_stale_note_save_can_retry_without_duplicating_history(app, monkeypatch):
    source = app.state['sessions'][0]
    grant = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['file_access'], 'scopes':['local'], 'reason':'Requested change'}, 'failed-note'))['result']
    source['interruptionRevision'] = 1
    before = copy.deepcopy(source['messages'])
    publish = app._publish_changes
    def fail(**kwargs):
        raise OSError('Storage unavailable')
    monkeypatch.setattr(app, '_publish_changes', fail)
    args = {'sessionId':source['id'], 'id':grant['id'], 'decision':'deny', 'comment':'Use the local copy.'}
    with pytest.raises(OSError):
        await app.dispatch('approval.respond', args, command_id='note-retry')
    assert source['messages'] == before
    monkeypatch.setattr(app, '_publish_changes', publish)
    await app.dispatch('approval.respond', args, command_id='note-retry')
    assert len([m for m in source['messages'] if m.get('approvalId') == grant['id']]) == 1
