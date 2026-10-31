"""Atomic cooperative claims, independent of external effects and inventory."""
import copy
import json
from types import SimpleNamespace
import sqlite3
import uuid
from concurrent.futures import ProcessPoolExecutor

import pytest

from amplifier_web import workspace_resources as resources
from amplifier_web import test_execution_settings as settings
from amplifier_web.service import AppError
from test_collaborative_workspaces import app, agent_action


WORKSPACE = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


def owner(sid='root', principal=None):
    return {'kind': 'agent', 'sessionId': sid, 'principal': principal or sid}


def reserve(home, identity, workspace=WORKSPACE, actor=None, **args):
    return resources.reservation_command(home, workspace, 'workspace.resources.reserve',
        {'resourceKey': 'preview:example', **args}, actor or owner(identity), identity)


def change(home, row, action='release', actor=None, identity='update', **args):
    return resources.reservation_command(home, row['workspaceId'], 'workspace.resources.' + action,
        {'id': row['id'], 'expectedRevision': row['revision'], 'evidence': 'Observed and relinquished', **args},
        actor or row['owner'], identity)


def listing(home, workspace=WORKSPACE, **args):
    return resources.reservation_command(home, workspace, 'workspace.resources.reservations', args, {'kind': 'user'})


def test_acquisition_is_atomic_across_processes_and_workspaces(tmp_path):
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(reserve, tmp_path, str(i), WORKSPACE if i % 2 else OTHER) for i in range(12)]
        outcomes = [future.result()['outcome'] for future in futures]
    assert outcomes.count('reserved') == 1
    assert outcomes.count('conflict') == 11


def test_shared_requires_every_holder_to_opt_in_and_uncertain_blocks_new_holders(tmp_path):
    first = reserve(tmp_path, 'one', mode='shared')['reservation']
    second = reserve(tmp_path, 'two', mode='shared')['reservation']
    assert reserve(tmp_path, 'exclusive')['outcome'] == 'conflict'
    assert reserve(tmp_path, 'duplicate-owner', actor=owner('one'), mode='shared')['outcome'] == 'conflict'
    unknown = change(tmp_path, first, 'uncertain')['reservation']
    assert reserve(tmp_path, 'three', mode='shared')['outcome'] == 'conflict'
    change(tmp_path, unknown, identity='reconcile')
    assert reserve(tmp_path, 'four', mode='shared')['outcome'] == 'reserved'
    assert listing(tmp_path)['items'][0]['id'] == second['id']


def test_retries_after_release_and_reacquisition_never_resurrect_old_claim(tmp_path):
    original = reserve(tmp_path, 'one')
    row = original['reservation']
    released = change(tmp_path, row)
    new = reserve(tmp_path, 'two')
    assert reserve(tmp_path, 'one') == original
    assert change(tmp_path, row) == released
    assert listing(tmp_path)['items'] == [new['reservation']]
    assert listing(tmp_path, includeReleased=True)['items'][0]['status'] == 'released'
    # Even conflict is a stable result; releasing does not turn a retry into work.
    refused = reserve(tmp_path, 'blocked')
    change(tmp_path, new['reservation'], identity='release-two')
    assert reserve(tmp_path, 'blocked') == refused
    assert not listing(tmp_path)['items']


def test_revision_and_actor_checks_do_not_mutate_claim(tmp_path):
    row = reserve(tmp_path, 'one')['reservation']
    for actor in (owner('two'), owner('one', 'child')):
        with pytest.raises(ValueError, match='actual owner'):
            change(tmp_path, row, actor=actor)
    assert change(tmp_path, row, expectedRevision=9, identity='stale')['outcome'] == 'stale'
    with pytest.raises(ValueError, match='observation'):
        change(tmp_path, row, evidence=' ')
    assert listing(tmp_path)['items'] == [row]
    assert change(tmp_path, row, actor={'kind': 'user'})['outcome'] == 'released'


@pytest.mark.parametrize('patch', [{'resourceKey': 'different'}, {'mode': 'shared'}, {'note': 'changed'}])
def test_command_identity_is_bound_to_contents_and_actor(tmp_path, patch):
    reserve(tmp_path, 'one')
    with pytest.raises(ValueError, match='different update or actor'):
        reserve(tmp_path, 'one', **patch)
    with pytest.raises(ValueError, match='different update or actor'):
        reserve(tmp_path, 'one', actor=owner('two'))


def test_inventory_neither_acquires_nor_releases_claims(tmp_path):
    item = resources.command(tmp_path, WORKSPACE, 'workspace.resources.add',
        {'kind': 'preview', 'resourceId': 'preview:example', 'owner': 'arbitrary label'})
    claim = reserve(tmp_path, 'one')['reservation']
    resources.command(tmp_path, WORKSPACE, 'workspace.resources.update',
        {'id': item['id'], 'expectedRevision': 1, 'status': 'observed_absent', 'evidence': 'An observation'})
    assert reserve(tmp_path, 'two')['outcome'] == 'conflict'
    change(tmp_path, claim)
    assert resources.command(tmp_path, WORKSPACE, 'workspace.resources.list', {})['resources'][0]['status'] == 'observed_absent'


def test_limits_count_nested_owners_and_allow_reconciliation(tmp_path, monkeypatch):
    settings.write(SimpleNamespace(data_dir=tmp_path), {'version':1,'queue':{},'profiles':{},
        'concurrency':{'maxResourceReservationsPerChat':2,'maxResourceReservations':3}})
    first = reserve(tmp_path, 'one', actor=owner('root', 'child1'), resourceKey='one')['reservation']
    reserve(tmp_path, 'two', actor=owner('root', 'child2'), resourceKey='two')
    assert reserve(tmp_path, 'three', actor=owner('root'), resourceKey='three')['outcome'] == 'limit'
    reserve(tmp_path, 'four', actor=owner('different'), resourceKey='four')
    assert reserve(tmp_path, 'five', resourceKey='five')['outcome'] == 'limit'
    change(tmp_path, first)
    assert reserve(tmp_path, 'six', resourceKey='six')['outcome'] == 'reserved'


def test_reads_are_paginated_scoped_and_passive(tmp_path):
    for i in range(4):
        reserve(tmp_path, str(i), resourceKey=str(i))
    reserve(tmp_path, 'other', workspace=OTHER, resourceKey='other')
    before = (tmp_path / 'workspace-resources/reservations.sqlite3').read_bytes()
    first = listing(tmp_path, limit=2)
    second = listing(tmp_path, offset=first['nextOffset'], limit=2)
    assert len(first['items']) == len(second['items']) == 2
    assert second['nextOffset'] is None
    assert {row['resourceKey'] for row in first['items'] + second['items']} == {'0', '1', '2', '3'}
    assert (tmp_path / 'workspace-resources/reservations.sqlite3').read_bytes() == before


def test_receipt_failure_rolls_back_acquisition_and_release(tmp_path):
    first = reserve(tmp_path, 'first')['reservation']
    with sqlite3.connect(tmp_path / 'workspace-resources/reservations.sqlite3') as db:
        db.execute("CREATE TRIGGER receipt_failure BEFORE INSERT ON commands BEGIN SELECT RAISE(ABORT,'injected receipt failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        change(tmp_path, first)
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        reserve(tmp_path, 'second', resourceKey='second')
    assert listing(tmp_path)['items'] == [first]


def test_old_command_receipts_survive_large_released_history(tmp_path):
    original = reserve(tmp_path, 'first')
    change(tmp_path, original['reservation'])
    for i in range(210):
        row = reserve(tmp_path, str(i))['reservation']
        change(tmp_path, row, identity='release-' + str(i))
    assert reserve(tmp_path, 'first') == original
    assert not listing(tmp_path)['items']


async def test_shared_dispatch_binds_child_authority_and_preserves_private_state(app):
    source, peer = app.state['sessions']
    workspace = app.state['workspaces'][0]['id']
    args = {'workspaceId': workspace, 'resourceKey': 'preview:example'}
    before = copy.deepcopy(app.state['view'])
    result = await agent_action(app, source, 'workspace.resources.reserve', args, 'reserve')
    assert set(result['state']) == {'revision', 'sessionId', '_stateAccess'}
    row = result['result']['reservation']
    assert row['owner'] == owner(source['id'], source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id'])
    update = {'workspaceId': workspace, 'id': row['id'], 'expectedRevision': 1, 'evidence': 'Done'}
    with pytest.raises(AppError, match='actual owner'):
        await app.app_bridge('dispatch', {'action': 'workspace.resources.release', 'args': update, 'id': 'child-release',
            '_runtimeSessionId': 'actual-child'}, source['id'])
    child = await app.app_bridge('dispatch', {'action': 'workspace.resources.reserve', 'args': {**args, 'resourceKey': 'child-preview'},
        'id': 'child-reserve', '_runtimeSessionId': 'actual-child'}, source['id'])
    assert child['result']['reservation']['owner']['principal'] == 'actual-child'
    with pytest.raises(AppError, match='calling conversation'):
        await agent_action(app, source, 'workspace.resources.reserve', {**args, 'sessionId': peer['id']}, 'spoof')
    await agent_action(app, source, 'workspace.resources.release', update, 'release')
    assert app.state['view'] == before
    assert not app.runtime.inputs


async def test_user_can_reconcile_after_workspace_registration_disappears(app):
    source = app.state['sessions'][0]
    workspace = app.state['workspaces'][0]['id']
    result = await app.dispatch('workspace.resources.reserve', {'workspaceId': workspace,
        'sessionId': source['id'], 'resourceKey': 'preview:example'}, command_id='reserve')
    row = result['result']['reservation']
    app.state['workspaces'] = []
    inspected = await app.dispatch('workspace.resources.reservations', {'workspaceId': workspace})
    assert inspected['result']['items'] == [row]
    released = await app.dispatch('workspace.resources.release', {'workspaceId': workspace,
        'id': row['id'], 'expectedRevision': 1, 'evidence': 'Inspected remaining service'}, command_id='release')
    assert released['result']['outcome'] == 'released'


async def test_agent_cross_workspace_read_and_acquire_refused(app, tmp_path):
    source = app.state['sessions'][0]
    (tmp_path / 'other').mkdir()
    await app.dispatch('workspace.add', {'path': str(tmp_path / 'other')})
    workspace = app.state['selectedWorkspaceId']
    for action in ('reservations', 'reserve'):
        with pytest.raises(AppError, match='calling conversation'):
            await agent_action(app, source, 'workspace.resources.' + action,
                {'workspaceId': workspace, **({'resourceKey': 'anything'} if action == 'reserve' else {})}, action)


async def test_context_is_bounded_without_loading_or_starting_other_chats(app, monkeypatch):
    source = app.state['sessions'][0]
    workspace = app.state['workspaces'][0]['id']
    for i in range(18):
        await agent_action(app, source, 'workspace.resources.reserve',
            {'workspaceId': workspace, 'resourceKey': str(i)}, 'reserve-' + str(i))
    async def forbidden(*args, **kwargs):
        pytest.fail('A passive resource read loaded a conversation')
    monkeypatch.setattr(app.history, 'ensure_loaded', forbidden)
    context = await agent_action(app, source, 'coordination.context', {'sessionId': source['id']}, 'context')
    page = context['result']['resources']
    assert len(page['items']) == 16 and page['nextOffset'] == 16
    assert 'Private unsent draft' not in str(context)
    assert not app.runtime.inputs


def test_configured_reservation_ceiling_above_default_and_lowering_retains_owners(tmp_path):
    def configure(maximum):
        settings.write(SimpleNamespace(data_dir=tmp_path), {'version':1,'queue':{},'profiles':{},
            'concurrency':{'maxResourceReservationsPerChat':maximum}})
    configure(70)
    rows=[reserve(tmp_path,str(i),actor=owner('manager'),resourceKey=str(i))['reservation'] for i in range(65)]
    unknown=change(tmp_path,rows[0],'uncertain',identity='unknown')['reservation']
    configure(2)
    assert reserve(tmp_path,'blocked',actor=owner('manager'),resourceKey='fresh')['outcome']=='limit'
    current=listing(tmp_path)['items'];assert len(current)==65
    assert next(row for row in current if row['id']==unknown['id'])['status']=='uncertain'
    assert change(tmp_path,unknown,identity='release-unknown')['outcome']=='released'
    assert reserve(tmp_path,'0',actor=owner('manager'),resourceKey='0')['reservation']==rows[0]


def test_configured_reservation_limit_is_atomic_across_processes(tmp_path):
    settings.write(SimpleNamespace(data_dir=tmp_path), {'version':1,'queue':{},'profiles':{},
        'concurrency':{'maxResourceReservations':2,'maxResourceReservationsPerChat':2}})
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(reserve,tmp_path,str(i),WORKSPACE,owner('root'),resourceKey=str(i)) for i in range(8)]
        outcomes=[future.result()['outcome'] for future in futures]
    assert outcomes.count('reserved')==2 and outcomes.count('limit')==6


@pytest.mark.parametrize('explicit_zero', [False, True])
def test_uncapped_claim_counts_keep_conflicts_and_unknown_receipts(tmp_path, explicit_zero):
    actor = owner('manager')
    original = reserve(tmp_path, 'original', actor=actor, resourceKey='unknown')
    unknown = change(tmp_path, original['reservation'], 'uncertain', identity='unknown')['reservation']
    # Populate a valid indexed retained inventory beyond the former global1024
    # and per-chat64 ceilings; no services are created by a cooperative claim.
    with sqlite3.connect(tmp_path / 'workspace-resources/reservations.sqlite3') as db:
        for index in range(1024):
            row = {**original['reservation'], 'id': f'claim-{index}', 'resourceKey': f'key-{index}'}
            db.execute('INSERT INTO claims VALUES(?,?,?,?,?,?)',
                (row['id'], WORKSPACE, row['resourceKey'], actor['sessionId'], row['status'], json.dumps(row)))
        before = db.execute('SELECT id,body FROM claims ORDER BY id').fetchall()
    if explicit_zero:
        settings.write(SimpleNamespace(data_dir=tmp_path), {'version':1,'queue':{},'profiles':{},
            'concurrency':{'maxResourceReservations':0,'maxResourceReservationsPerChat':0}})
    assert reserve(tmp_path, 'fresh', actor=actor, resourceKey='new')['outcome'] == 'reserved'
    assert reserve(tmp_path, 'blocked-unknown', actor=owner('peer'), resourceKey='unknown')['outcome'] == 'conflict'
    assert reserve(tmp_path, 'original', actor=actor, resourceKey='unknown') == original
    with sqlite3.connect(tmp_path / 'workspace-resources/reservations.sqlite3') as db:
        assert db.execute('SELECT id,body FROM claims WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id',
            (json.dumps([identity for identity, _ in before]),)).fetchall() == before
    assert change(tmp_path, unknown, identity='reconcile')['outcome'] == 'released'
