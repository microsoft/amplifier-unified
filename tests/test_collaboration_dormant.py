"""Dormant admission observes host lifetime without replaying prior work."""
import copy
import time

import pytest

from test_automatic_history import app_factory
from test_history_residency import browse, saved_directory


@pytest.mark.parametrize('restart', [False, True])
async def test_collaboration_authority_normalizes_only_prior_host_on_keyed_admission(app_factory, restart):
    app = app_factory()
    owner = app._new_session({'title': 'Dormant authority'})
    owner['collaborationGeneration'] = {'id': 'prior-generation', 'inputIds': ['prior-input'], 'terminal': False}
    owner['approvals'] = [
        {'id': 'collaboration:prior', 'status': 'pending'},
        {'id': 'ordinary-approval', 'status': 'pending'},
        {'id': 'capability:durable', 'status': 'pending'},
    ]
    # A durable card needs its canonical grant; an orphan correctly expires.
    app.configuration_authority.save({
        'id': 'capability:durable', 'sessionId': owner['id'], 'workspace': owner['workspace'],
        'interruptionRevision': 0, 'revision': 1, 'status': 'pending', 'createdAt': time.time(),
        'effects': ['file_access'], 'scopes': ['local'], 'reason': 'Saved configuration request',
        'nativeWait': False,
    })
    active = app._new_session({'title': 'Selected chat'})
    app._state['sessions'].extend([owner, active])
    app._state['selectedSessionId'] = active['id']
    identity, generation = owner['id'], copy.deepcopy(owner['collaborationGeneration'])
    app._save()
    if restart:
        await app.close()
        app = app_factory(home=app.data_dir)
        # Pending prior-host authority is recovered before passive reads.
        assert identity in {row['id'] for row in app._state['sessions']}
    else:
        # Explicit readmission exercises the incarnation distinction even though
        # normal retirement protects a live generation from eviction.
        app._state['sessions'].remove(owner)
        app._session_projection_refs.pop(identity, None)
        app.projections.invalidate(state=app._state)
    admitted = app._session(identity, hydrate=False)
    assert admitted['_collaborationHostInstanceId'] == app.instance_id
    if restart:
        assert admitted['collaborationGeneration'] == {
            **generation, 'terminal': True, 'detail': 'Host restarted; no live generation authority.'}
        assert admitted['approvals'][0]['status'] == 'retired'
    else:
        assert admitted['collaborationGeneration'] == generation
        assert admitted['approvals'][0]['status'] == 'pending'
    ordinary = admitted['approvals'][1]
    if restart:
        # Native waiters belong to the prior host; durable configuration
        # decisions retain their own authority across host replacement.
        assert ordinary['status'] == 'expired'
        assert ordinary['resolutionReason'] == 'host_restarted'
    else:
        assert ordinary == {'id': 'ordinary-approval', 'status': 'pending'}
    assert admitted['approvals'][2] == {'id': 'capability:durable', 'status': 'pending'}
    assert not app.runtime.started and not app.runtime.sent


@pytest.mark.parametrize('pending', ['generation', 'approval'])
async def test_restart_retires_idle_pending_authority_before_passive_reads(app_factory, pending):
    app = app_factory()
    owner = app._new_session({'title': 'Idle pending collaboration'})
    if pending == 'generation':
        owner['collaborationGeneration'] = {'id': 'old-generation', 'terminal': False}
    else:
        owner['approvals'] = [{'id': 'collaboration:old-approval', 'status': 'pending'}]
    dormant = app._new_session({'title': 'Unrelated dormant chat'})
    selected = app._new_session({'title': 'Selected chat'})
    app._state['sessions'].extend([owner, dormant, selected])
    app._state['selectedSessionId'] = selected['id']
    identity, dormant_id = owner['id'], dormant['id']
    app._save()
    assert app.db.execute('SELECT startup FROM history_local_rows WHERE id=?', (identity,)).fetchone()[0] == 1
    await app.close()

    app = app_factory(home=app.data_dir)
    assert identity in {row['id'] for row in app._state['sessions']}
    assert dormant_id not in {row['id'] for row in app._state['sessions']}
    metadata = app.directory.resolve(identity)
    if pending == 'generation':
        restored = next(row for row in app._state['sessions'] if row['id'] == identity)
        assert restored['collaborationGeneration']['terminal'] is True
    else:
        assert metadata['approvals'][0]['status'] == 'retired'
        assert all(item['id'] != identity for item in app.directory.attention_rows(app._state))
    assert app.db.execute('SELECT startup FROM history_local_rows WHERE id=?', (identity,)).fetchone()[0] == 0
    assert not app.runtime.started and not app.runtime.sent
    await app.close()

    # Recovered authority no longer keeps the owner resident on later boots.
    app = app_factory(home=app.data_dir)
    assert identity not in {row['id'] for row in app._state['sessions']}
    assert not app.runtime.started and not app.runtime.sent


async def test_idle_owner_with_nonterminal_generation_is_not_retired(app_factory):
    app, identities = await saved_directory(app_factory)
    owner = app._session(identities[1], hydrate=False)
    app.collaboration.observe(owner, 'runtime.generation', {
        'event': 'generation.started', 'generation_id': 'current-generation', 'sessionId': owner['id']})
    del owner  # No borrowed reference may be the reason this test passes.
    for identity in identities[2:]:
        browse(app, identity)
    owner = next(row for row in app._state['sessions'] if row['id'] == identities[1])
    assert owner['status'] == 'idle' and not owner['collaborationGeneration']['terminal']
    app.collaboration.observe(owner, 'runtime.generation', {
        'event': 'generation.finished', 'generation_id': 'current-generation', 'sessionId': owner['id'],
        'disposition': 'manager_turn_finished', 'active_job_ids': []})
    del owner
    for identity in identities[2:]:
        browse(app, identity)
    assert identities[1] not in {row['id'] for row in app._state['sessions']}
    assert not app.runtime.started and not app.runtime.sent
