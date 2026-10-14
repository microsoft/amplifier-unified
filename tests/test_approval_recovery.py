"""Native waiters die with their host; durable grants keep their own lifecycle."""
import copy

import pytest

from amplifier_web.collaboration import restore_owner
from amplifier_web.service import AppError
from test_automatic_history import app_factory


@pytest.mark.parametrize('dormant', [False, True])
async def test_restart_expires_native_approval_and_rejects_old_decision_without_execution(app_factory, dormant):
    app = app_factory()
    selected = app._new_session({'title': 'Unrelated selected chat'})
    target = app._new_session({'title': 'Awaiting tool permission'})
    target.update(status='idle' if dormant else 'working', approvals=[{
        'id': 'native-permission', 'status': 'pending', 'prompt': 'Write the reviewed file?',
    }])
    target['messages'] = [{'id': 'saved-result', 'role': 'assistant', 'text': 'Retained original work'}]
    app._state['sessions'].extend([selected, target])
    app._state['selectedSessionId'] = selected['id']
    app._save()
    original_messages = copy.deepcopy(target['messages'])
    selected_messages = copy.deepcopy(selected['messages'])
    await app.close()

    restored = app_factory(home=app.data_dir)
    assert (target['id'] not in {row['id'] for row in restored._state['sessions']}) is dormant
    # A stale browser or another client can answer without selecting the chat.
    # Its exact target admission must recover the pending record first.
    for decision in ('allow', 'deny'):
        with pytest.raises(AppError, match='expired when the app restarted') as error:
            await restored.dispatch('approval.respond', {'sessionId': target['id'],
                'id': 'native-permission', 'decision': decision}, origin='ui',
                command_id='stale-' + decision)
        assert error.value.status == 409
    current = restored._session(target['id'])
    assert current['approvals'][0]['status'] == 'expired'
    assert current['approvals'][0]['resolutionReason'] == 'host_restarted'
    assert 'nothing was replayed' in current['approvals'][0]['detail']
    assert current['messages'] == original_messages
    assert current['status'] == ('idle' if dormant else 'interrupted')
    assert not restored.runtime.started and not restored.runtime.sent
    assert not restored.db.execute("SELECT 1 FROM commands WHERE id LIKE 'stale-%'").fetchone()
    # Opening the target does not change another chat's retained conversation.
    assert restored._session(selected['id'])['messages'] == selected_messages
    restored._publish_changes(sessions={target['id']})
    await restored.close()
    again = app_factory(home=app.data_dir)
    assert again._session(target['id'])['approvals'][0] == current['approvals'][0]
    assert not again.runtime.started and not again.runtime.sent


def test_recovery_respects_approval_owners_terminal_records_and_same_host():
    session = {'_collaborationHostInstanceId': 'old-host', 'approvals': [
        {'id': 'native', 'status': 'pending'},
        {'id': 'legacy'},
        {'id': 'capability:durable', 'status': 'pending'},
        {'id': 'collaboration:prior-generation', 'status': 'pending'},
        *[{'id': 'terminal-' + status, 'status': status} for status in ('allow', 'deny', 'expired', 'retired')],
    ]}
    terminal = copy.deepcopy(session['approvals'][4:])
    restore_owner(session, 'new-host')
    assert [row['status'] for row in session['approvals'][:4]] == ['expired', 'expired', 'pending', 'retired']
    assert session['approvals'][4:] == terminal
    # Same-host eviction/admission must not expire a fresh live native wait.
    fresh = {'id': 'new-native', 'status': 'pending'}
    session['approvals'].append(fresh)
    restored = copy.deepcopy(session)
    restore_owner(session, 'new-host')
    assert session == restored
    assert fresh['status'] == 'pending'
