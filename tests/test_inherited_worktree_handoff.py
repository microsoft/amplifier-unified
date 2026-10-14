"""Commissioned roots may leave and recover their exact borrowed placement."""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import app, Runtime, generation
from test_multi_repository_worktrees import create, move
from test_peer_creation_operation import commission, initial_accepted, plan
from test_worktrees import repository, settle


@pytest.fixture
async def inherited(app, tmp_path):
    app.runtime.quiesce_for_handoff = AsyncMock(return_value={'quiesced': True, 'inputsReplayed': False})
    creator = app.state['sessions'][0]
    repo = repository(tmp_path)
    checkout = await create(app, creator['id'], repo, 'creator-copy')
    assert (await move(app, creator['id'], checkout, 'creator-outbound'))['phase'] == 'applied'
    plan(app, creator)
    created = await commission(app, creator, returnNotification=False)
    await initial_accepted(app, created['initialInputId'])
    peer = app._session(created['sessionId'])
    assert peer['workingDirectory'] == checkout['path']
    assert peer['workspace'] == creator['workspace']
    assert not peer.get('worktrees')
    yield app, creator, peer, checkout


@pytest.mark.parametrize('own_checkout', [False, True])
@pytest.mark.parametrize('nested', [False, True])
async def test_commissioned_root_can_leave_inherited_checkout_without_adopting_it(inherited, own_checkout, nested):
    app, creator, peer, borrowed = inherited
    if nested:
        await generation(app, peer, 'nested-creator', ['commission:brief'])
        created = await commission(app, peer, 'nested-commission', returnNotification=False)
        await initial_accepted(app, created['initialInputId'])
        peer = app._session(created['sessionId'])
    # The captured placement remains valid after the original owner moves away.
    assert (await move(app, creator['id'], None, 'creator-home'))['phase'] == 'applied'
    destination = await create(app, peer['id'], borrowed['path'], 'peer-copy') if own_checkout else None
    messages = list(peer['messages'])
    receipt = await move(app, peer['id'], destination, 'peer-outbound', agent=True)
    assert receipt['phase'] == 'applied', receipt
    assert receipt['sourceBinding']['kind'] == 'inherited'
    assert receipt['sourceBinding']['checkoutOwnerId'] == creator['id']
    assert peer['workingDirectory'] == (destination['path'] if destination else peer['workspace'])
    assert peer['messages'] == messages
    assert app.worktrees.git.get(borrowed['id'])['sessionId'] == creator['id']
    assert borrowed['id'] not in [row['id'] for row in peer.get('worktrees', [])]
    # Inheritance is recovery evidence, not a new choice of another owner's id.
    calls = app.runtime.quiesce_for_handoff.await_count
    with pytest.raises(AppError, match='another task'):
        await move(app, peer['id'], borrowed, 'adopt-creator-copy')
    assert app.runtime.quiesce_for_handoff.await_count == calls


async def test_unknown_handoff_recovers_inherited_source_after_restart(inherited):
    app, creator, peer, borrowed = inherited
    destination = await create(app, peer['id'], borrowed['path'], 'peer-copy')
    app.runtime.quiesce_for_handoff.side_effect = RuntimeError('lost release acknowledgment')
    unknown = await move(app, peer['id'], destination, 'uncertain-peer-move')
    assert unknown['phase'] == 'unknown'
    # Another legitimate association with the same folder must not select a
    # different owner when the retained source binding is checked after restart.
    duplicate = app.worktrees.git.attach(borrowed['path'], source=borrowed['source'],
        command_id='another-association', session_id=app.state['sessions'][1]['id'])
    assert duplicate['id'] != unknown['sourceBinding']['checkoutId']
    await app.close()
    runtime = Runtime()
    runtime.quiesce_for_handoff = AsyncMock(return_value={'quiesced': True, 'inputsReplayed': False})
    restored = runtime.app = AppService(app.data_dir, runtime, workspace=app.default_workspace)
    try:
        saved = restored.worktrees.read(peer['id'], unknown['id'])
        assert saved['sourceBinding'] == unknown['sourceBinding']
        pending = (await restored.dispatch('worktree.reconcile', {'sessionId': peer['id'], 'id': saved['id'],
            'expectedRevision': saved['revision'], 'destination': 'source',
            'evidence': 'Inspected the inherited folder and confirmed release.'}, command_id='recover-inherited'))['result']
        await settle(restored)
        receipt = restored.worktrees.read(peer['id'], pending['id'])
        assert receipt['phase'] == 'applied', receipt
        assert receipt['targetBinding']['checkoutId'] == borrowed['id']
        assert restored._session(peer['id'])['workingDirectory'] == borrowed['path']
        assert restored.worktrees.git.get(borrowed['id'])['sessionId'] == creator['id']
        assert runtime.quiesce_for_handoff.await_count == 1
        assert not runtime.inputs
    finally:
        await restored.close()


@pytest.mark.parametrize('tamper', ['creator', 'path', 'missing_receipt'])
async def test_inherited_source_requires_matching_durable_creation_proof(inherited, tamper, tmp_path):
    app, _, peer, _ = inherited
    calls = app.runtime.quiesce_for_handoff.await_count
    if tamper == 'creator':
        peer['collaboration']['creatorSessionId'] = 'unrelated'
    elif tamper == 'path':
        other = tmp_path / 'unrelated'; other.mkdir()
        peer['workingDirectory'] = str(other)
    else:
        app.db.execute('DELETE FROM commands WHERE id=?', (peer['collaboration']['requestId'],))
        app.db.commit()
    with pytest.raises(AppError, match='creation receipt|receipt not found'):
        await move(app, peer['id'], None, 'unproven-source')
    assert app.runtime.quiesce_for_handoff.await_count == calls
    assert not app.worktrees.receipts(peer['id'])


async def test_reconcile_inherited_source_rejects_swapped_repository_before_release(inherited, tmp_path):
    app, _, peer, borrowed = inherited
    app.runtime.quiesce_for_handoff.side_effect = RuntimeError('lost release acknowledgment')
    unknown = await move(app, peer['id'], None, 'uncertain-peer-move')
    assert unknown['phase'] == 'unknown'
    app.runtime.quiesce_for_handoff.side_effect = None
    calls = app.runtime.quiesce_for_handoff.await_count
    parent = tmp_path / 'unrelated'; parent.mkdir()
    unrelated = repository(parent)
    marker = Path(borrowed['path']) / '.git'
    original = marker.read_bytes()
    try:
        marker.write_text(f'gitdir: {unrelated / ".git"}\n')
        pending = (await app.dispatch('worktree.reconcile', {'sessionId': peer['id'], 'id': unknown['id'],
            'expectedRevision': unknown['revision'], 'destination': 'source',
            'evidence': 'Attempt source recovery'}, command_id='wrong-repository'))['result']
        await settle(app)
        refused = app.worktrees.read(peer['id'], pending['id'])
        assert refused['phase'] == 'rejected' and refused['step'] == 'preflight'
        assert 'recorded repository' in refused['detail']
        assert app.runtime.quiesce_for_handoff.await_count == calls
        assert app.worktrees.read(peer['id'], unknown['id'])['phase'] == 'unknown'
    finally:
        marker.write_bytes(original)
