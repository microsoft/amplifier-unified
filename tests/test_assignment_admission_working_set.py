"""Uncapped admission checks authority, not every member's historical usage."""
import copy

import pytest

from amplifier_web import assignments, cold_display
from amplifier_web.capacity import admission
from amplifier_web.resource_files import put
from test_assignments import app, call, limits, start


def enroll_fixture(app, value, session):
    """Install synthetic membership; no runtime work or provider is started."""
    session['assignmentId'] = value['id']
    value['members'][session['id']] = {'status': 'bound', 'role': 'commissioned'}
    app.assignments.save(value)
    app.db.commit()


async def test_uncapped_admission_checks_members_without_loading_or_aggregating_history(app, monkeypatch):
    source, peer = app.state['sessions']
    value = await start(app)
    enroll_fixture(app, value, peer)
    retained = {'nodes': [], 'turns': [], 'retiredUsageNodes': [
        {**call(peer, 'retained'), 'phase': 'outcome_unknown', 'assignmentId': value['id']}]}
    reference = put(app.db, retained)
    app.db.commit()
    peer.pop('execution', None)
    peer[cold_display.MARKER] = {'execution': reference}
    before = copy.deepcopy(reference)
    original_session = app._session
    reads = []

    def session(identity=None, *, hydrate=True):
        reads.append((identity, hydrate))
        return original_session(identity, hydrate=hydrate)

    def history_read(*args, **kwargs):
        pytest.fail('Uncapped assignment admission must not load or total historical usage')

    with monkeypatch.context() as scoped:
        scoped.setattr(app, '_session', session)
        scoped.setattr(assignments, 'usage_snapshot', history_read)
        scoped.setattr(assignments, 'aggregate', history_read)
        scoped.setattr(cold_display, 'load', history_read)
        async with app.lock:
            candidate = call(source, 'next')
            result = app.assignments.admission(source, candidate)
    assert result['allowed'] and not result['waiting']
    assert reads == [(sid, False) for sid in value['members']]
    assert candidate['assignmentId'] == value['id']
    assert candidate['assignmentRevision'] == value['revision']
    assert candidate['assignmentReservation'] == {'reserveTokens': 0, 'reserveCostUsd': 0}
    assert peer[cold_display.MARKER]['execution'] == before
    assert 'execution' not in dict.keys(peer)


@pytest.mark.parametrize('status', ['bound', 'detached', 'reserved', 'not_created'])
@pytest.mark.parametrize('policy', ['pause', 'allow'])
async def test_uncapped_admission_keeps_missing_retained_member_policy(app, status, policy):
    source = app.state['sessions'][0]
    value = await start(app)
    value['members']['missing-fixture-member'] = {'status': status, 'role': 'commissioned'}
    value['limits']['unknownPolicy'] = policy
    app.assignments.save(value)
    app.db.commit()
    candidate = call(source, 'next')
    async with app.lock:
        result = app.assignments.admission(source, candidate)
    blocked = status in {'bound', 'detached'} and policy == 'pause'
    assert result['allowed'] is not blocked
    assert ('assignmentId' in candidate) is not blocked
    if blocked:
        assert result['reasons'] == ['Assignment usage is unavailable for a retained member.']


@pytest.mark.parametrize('limit,value', [('maxConcurrentCalls', 1), ('maxTotalTokens', 100), ('maxCostUsd', 1)])
async def test_uncapped_admission_retains_unknown_receipt_for_later_limit_and_replay(app, limit, value):
    source = app.state['sessions'][0]
    await start(app)
    original = call(source, 'retained-call')
    assert (await admission(app, source['id'], {'call': original}))['allowed']
    from amplifier_web.capacity import restore_observation
    restore_observation(source)
    before = copy.deepcopy(source['execution']['nodes'])
    assert before[-1]['phase'] == 'outcome_unknown'
    assert (await admission(app, source['id'], {'call': call(source, 'uncapped-next')}))['allowed']
    assert source['execution']['nodes'][:len(before)] == before
    duplicate = await admission(app, source['id'], {'call': original})
    assert not duplicate['allowed'] and 'already admitted' in duplicate['reasons'][0]
    await limits(app, source, **{limit: value})
    limited = await admission(app, source['id'], {'call': call(source, 'limited-next')})
    assert not limited['allowed']
    assert source['execution']['nodes'][:len(before)] == before


async def test_uncapped_assignment_stop_keeps_admission_closed_without_usage_read(app, monkeypatch):
    source = app.state['sessions'][0]
    value = await start(app)
    value.update(status='stopped', revision=value['revision'] + 1)
    app.assignments.save(value)
    app.db.commit()
    monkeypatch.setattr(assignments, 'usage_snapshot', lambda *a, **k: pytest.fail('Stopped uncapped assignment needs no usage read'))
    candidate = call(source, 'after-stop')
    async with app.lock:
        result = app.assignments.admission(source, candidate)
    assert not result['allowed'] and not result['waiting']
    assert result['reasons'] == ['This assignment was stopped; automatic work is suppressed.']
    assert 'assignmentId' not in candidate
