from copy import deepcopy

import pytest

from amplifier_web.navigation_summary import activity


@pytest.mark.parametrize('status,label', [
    ('working', 'Working'), ('running', 'Working'), ('stopping', 'Stopping'),
])
def test_running_task_is_visible_even_when_saved_task_remains_blocked(status, label):
    # Saved task state is not the current execution state, nor evidence of a
    # particular dependency. Reading navigation must not rewrite either owner.
    task = {'id': 'objective', 'revision': 2, 'continuationEpoch': 2,
            'status': 'blocked', 'dependencyWaitEpoch': 1,
            'blockProvenance': {'version': 1, 'origin': 'agent', 'dependency': True, 'revision': 2}}
    session = {'id': 'manager', 'status': status, 'task': task}
    before = deepcopy(session)
    assert activity(session, blocked=True) == {'kind': 'working', 'label': label}
    assert activity({**session, 'status': 'idle'}, blocked=True) == {
        'kind': 'idle', 'label': 'Blocked — details unavailable'}
    assert activity({**session, 'approvals': [{'id': 'approval', 'status': 'pending'}]},
                    blocked=True) == {'kind': 'attention', 'label': 'Approval requested'}
    assert session == before


def test_block_flag_without_dependency_evidence_does_not_invent_wait_or_attention():
    session = {'id': 'manager', 'status': 'idle'}
    before = deepcopy(session)
    assert activity(session, blocked=True) == {'kind': 'idle', 'label': 'Blocked — details unavailable'}
    assert activity(session) == {'kind': 'idle', 'label': 'Idle'}
    assert session == before
