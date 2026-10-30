"""Only the latest queued outcome wakes a requester; all evidence remains saved."""
import copy

import pytest

from amplifier_web.service import AppService
from test_collaborative_workspaces import (
    app, declared_result, finish, generation, Runtime, settled,
)
from test_coordination_revisions import revised, subscribe


async def first_return(app, kind='question', outcome='question'):
    source, target = app.state['sessions'][:2]
    await declared_result(app, kind=kind, outcome=outcome)
    wait = await subscribe(app)
    await finish(app, target, 'result-request')
    identity = wait['result']['continuationId']
    assert app.collaboration.receipt(identity)['delivery'] == 'queued'
    return source, target, identity


async def later_return(app, target, kind='question', outcome='question'):
    await generation(app, target, 'later-result', ['followup'])
    await revised(app, 'result-request', 1, kind=kind, outcome=outcome)
    request = app.collaboration.receipt('result-request')
    return app.collaboration.continuation_identity(request['subscription'], 2)


@pytest.mark.parametrize(('kind', 'outcome'), [
    ('question', 'question'), ('defer', 'deferred'), ('conflict', 'conflict'),
    ('decline', 'declined'), ('result', 'failed'), ('result', 'cancelled'),
])
async def test_latest_outcome_replaces_old_queued_wake_without_erasing_reports(app, kind, outcome):
    source, target, old_id = await first_return(app, kind, outcome)
    old_report = copy.deepcopy(app.collaboration.receipt('declare-result-request'))
    old_messages = copy.deepcopy(source['messages'])
    new_id = await later_return(app, target)
    source['status'] = 'idle'
    await app.collaboration.drain(source['id'])
    assert app.collaboration.receipt(old_id)['delivery'] == 'suppressed'
    assert 'superseded' in app.collaboration.receipt(old_id)['detail']
    assert app.collaboration.receipt(new_id)['delivery'] == 'accepted'
    assert not app.collaboration.receipt(new_id)['dependencySatisfied']
    assert [row[1] for row in app.runtime.inputs] == ['result-request', new_id]
    assert app.collaboration.receipt('declare-result-request') == old_report
    assert source['messages'][:len(old_messages)] == old_messages
    result = (await app.dispatch('coordination.result', {'requestId': 'result-request'}))['result']
    assert [r['responseRevision'] for r in result['revisions']] == [2, 1]


async def test_staged_success_does_not_hide_an_outstanding_question(app):
    source, target, old_id = await first_return(app)
    await later_return(app, target, kind='result', outcome='success')
    request = app.collaboration.receipt('result-request')
    assert request['outcomeRevision'] == 1
    assert request['response']['status'] == 'staged'
    assert not request['response']['qualified']
    source['status'] = 'idle'
    await app.collaboration.drain(source['id'])
    assert app.collaboration.receipt(old_id)['delivery'] == 'accepted'


async def test_retiring_old_return_releases_backpressure_without_another_external_event(app, monkeypatch):
    source, target, old_id = await first_return(app)
    # One pending return plus its interim subscription reserves both slots.
    monkeypatch.setattr('amplifier_web.collaboration.MAX_PENDING_INPUTS', 2)
    new_id = await later_return(app, target)
    assert app.collaboration.receipt('result-request')['subscription']['status'] == 'backpressured'
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', (new_id,)).fetchone()
    source['status'] = 'idle'
    await app.collaboration.drain(source['id'])
    await settled(app, new_id, 'accepted')
    assert app.collaboration.receipt(old_id)['delivery'] == 'suppressed'
    assert [row[1] for row in app.runtime.inputs] == ['result-request', new_id]


@pytest.mark.parametrize('phase', ['accepted', 'unknown'])
async def test_new_outcome_does_not_rewrite_admitted_or_uncertain_return(app, phase):
    source, target, old_id = await first_return(app)
    old = app.collaboration.receipt(old_id)
    old['delivery'] = phase
    app.collaboration.save(old_id, old)
    app.db.commit()
    new_id = await later_return(app, target, kind='result', outcome='failed')
    source['status'] = 'idle'
    await app.collaboration.drain(source['id'])
    assert app.collaboration.receipt(old_id) == old
    assert app.collaboration.receipt(new_id)['delivery'] == 'accepted'
    assert [row[1] for row in app.runtime.inputs] == ['result-request', new_id]


@pytest.mark.parametrize('sealed', [False, True])
async def test_restart_admits_only_latest_queued_outcome(app, sealed):
    source, target, old_id = await first_return(app)
    new_id = await later_return(app, target, kind='result', outcome='failed')
    if sealed:
        from amplifier_web.automatic_history import display_identity
        from amplifier_operations.coordination import fingerprint
        anchor = {'messageId': display_identity(target, 9, 'assistant', 'Later failure'),
            'nativeIndex': 9, 'nativeText': 'Later failure', 'textDigest': fingerprint('Later failure'),
            'rootSessionId': target['id'], 'generationId': 'later-result'}
        await finish(app, target, 'result-request', generation_id='later-result',
            input_ids=['followup'], text='Later failure', nativeTerminal=anchor)
        assert app.collaboration.receipt('result-request')['response']['status'] == 'sealed'
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    reopened.runtime.app = reopened
    try:
        reopened._session(source['id'])['status'] = 'idle'
        request = reopened.collaboration.receipt('result-request')
        # An interrupted staged report becomes a newer unknown outcome on
        # recovery. That uncertainty must wake the requester, not either old wake.
        assert request['outcomeRevision'] == (2 if sealed else 3)
        assert request['response']['status'] == ('sealed' if sealed else 'unknown')
        latest_id = reopened.collaboration.continuation_identity(
            request['subscription'], request['outcomeRevision'])
        reopened.collaboration.start()
        await settled(reopened, latest_id, 'accepted')
        assert reopened.collaboration.receipt(old_id)['delivery'] == 'suppressed'
        assert reopened.collaboration.receipt(new_id)['delivery'] == ('accepted' if sealed else 'suppressed')
        assert [row[1] for row in reopened.runtime.inputs] == [latest_id]
        assert reopened.collaboration.receipt('declare-result-request')['result']['outcome'] == 'question'
    finally:
        await reopened.close()
