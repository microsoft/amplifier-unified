"""Bounded active-work facts, private drafts, and advisory declaration parity."""
import copy
import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.workspace_awareness import MAX_PACKET_BYTES, packet, intent_for
from test_collaborative_workspaces import app, agent_action, Runtime


def args(session, paths=None, revision=0, **patch):
    return {'sessionId': session['id'], 'expectedRevision': revision,
        'expectedExecutionRevision': session.get('executionRevision', 0),
        'paths': paths if paths is not None else [{'path': 'src', 'kind': 'directory'}], **patch}


async def declare(app, session, identity, **options):
    return await agent_action(app, session, 'coordination.intent', args(session, **options), identity)


async def test_user_and_commissioned_active_peers_share_bounded_facts_not_private_content(app):
    source, peer = app.state['sessions']
    peer.update(status='working', messages=[{'id': 'private', 'role': 'user', 'text': 'PRIVATE PROMPT'}],
                draft='PRIVATE DRAFT', configuration={'secret': 'PRIVATE CREDENTIAL'})
    commissioned = app._new_session({'title': 'Commissioned peer'})
    commissioned.update(status='working', collaboration={'creatorSessionId': source['id'], 'brief': 'PRIVATE BRIEF'})
    dormant = app._new_session({'title': 'Idle history'})
    loading = app._new_session({'title': 'Only loading', 'historyLoading': True})
    loading['historyLoading'] = True
    child = app._new_session({'title': 'Observed native child'})
    child.update(sessionKind='child', parentId=peer['id'], status='running')
    app.state['sessions'].extend([commissioned, dormant, loading, child]); app._publish()
    value = await app.app_bridge('coordination.current', {'_runtimeSessionId': source['id']}, source['id'])
    observed = value['awareness']
    assert {row['sessionId'] for row in observed['peers']} == {peer['id'], commissioned['id'], child['id']}
    assert all(row['sameExecutionDirectory'] for row in observed['peers'])
    assert 'PRIVATE' not in json.dumps(value) and 'Idle history' not in json.dumps(value)
    assert not app.runtime.inputs
    another = await app.app_bridge('coordination.current', {'_runtimeSessionId': 'native-child'}, source['id'])
    assert another['awareness']['actor'] == {'rootSessionId': source['id'], 'principal': 'native-child', 'role': 'child'}


async def test_overlap_is_advisory_and_exact_paths_can_be_read_without_peer_control(app):
    source, peer = app.state['sessions']; peer['status'] = 'working'; app._publish()
    first = await declare(app, source, 'source-declaration')
    other = await app.dispatch('coordination.intent', args(peer, [{'path': 'src/file.py', 'kind': 'file'}]), command_id='user-declaration')
    assert first['result']['outcome'] == other['result']['outcome'] == 'declared'
    value = packet(app, source['id']); observed = value['peers'][0]
    assert observed['declaredOverlap'] and observed['sameExecutionDirectory']
    assert observed['intent']['paths'][0]['path'].endswith('src/file.py')
    full = await agent_action(app, source, 'coordination.intent.read', {'sessionId': peer['id']}, 'read-peer')
    assert full['result']['paths'][0]['resolvedPath'] == str(Path(peer['workspace'])/'src/file.py')
    assert not app.runtime.inputs
    assert set(first['state']) == {'revision', 'sessionId', '_stateAccess'}
    with pytest.raises(AppError, match='peer mutation'):
        await agent_action(app, source, 'coordination.intent', args(peer, revision=1), 'spoof')


async def test_separate_copies_and_directory_boundaries_do_not_report_false_overlap(app, tmp_path):
    source, peer = app.state['sessions']; peer['status'] = 'working'; app._publish()
    await declare(app, source, 'source')
    await app.dispatch('coordination.intent', args(peer, [{'path': 'src-other/file.py', 'kind': 'file'}]), command_id='adjacent')
    assert not packet(app, source['id'])['peers'][0]['declaredOverlap']
    copy_dir = tmp_path/'isolated'; copy_dir.mkdir()
    peer.update(workingDirectory=str(copy_dir), executionRevision=1); app._publish_changes(sessions={peer['id']})
    assert packet(app, source['id'])['peers'][0]['intent']['needsReview']
    await app.dispatch('coordination.intent', args(peer, revision=1), command_id='copy-declaration')
    observed = packet(app, source['id'])['peers'][0]
    assert not observed['sameExecutionDirectory'] and not observed['declaredOverlap']
    assert not observed['intent']['needsReview']


async def test_stale_and_retried_declarations_never_resurrect_or_overwrite_newer_intent(app):
    source = app.state['sessions'][0]
    original = await declare(app, source, 'declare')
    stale = await declare(app, source, 'stale')
    assert stale['result']['outcome'] == 'stale'
    cleared = await declare(app, source, 'clear', paths=[], revision=1)
    assert cleared['result']['intent']['revision'] == 2 and not intent_for(app, source)['paths']
    duplicate = await declare(app, source, 'declare')
    assert duplicate['duplicate'] and duplicate['result'] == original['result']
    assert (await declare(app, source, 'stale'))['result'] == stale['result']
    assert source['workspaceWriteIntent']['revision'] == 2
    with pytest.raises(AppError, match='different contents'):
        await declare(app, source, 'declare', paths=[])


async def test_child_cannot_replace_root_declaration_and_other_workspaces_are_private(app, tmp_path):
    source, peer = app.state['sessions']
    with pytest.raises(AppError, match='child cannot borrow'):
        await app.app_bridge('dispatch', {'action': 'coordination.intent', 'args': args(source),
            'id': 'child', '_runtimeSessionId': 'child'}, source['id'])
    peer.update(workspace=str(tmp_path/'other'), status='working'); app._publish()
    assert not packet(app, source['id'])['peers']
    for action in ('coordination.awareness', 'coordination.intent.read'):
        with pytest.raises(AppError, match='calling workspace'):
            await agent_action(app, source, action, {'sessionId': peer['id']}, action)
    assert 'workspaceWriteIntent' not in source


async def test_declarations_reject_escaping_paths_and_allow_associated_copies(app, tmp_path):
    source = app.state['sessions'][0]
    outside = tmp_path.parent/'awareness-outside'; outside.mkdir(exist_ok=True)
    link = Path(source['workspace'])/'escape'; link.symlink_to(outside, target_is_directory=True)
    for path in ('../outside', str(outside), 'escape/file'):
        with pytest.raises(AppError, match='inside this chat'):
            await declare(app, source, 'outside-'+path, paths=[{'path': path, 'kind': 'file'}])
    source['worktrees'] = [{'path': str(outside), 'status': 'ready', 'id': 'associated'}]
    response = await declare(app, source, 'associated', paths=[{'path': str(outside/'new.py'), 'kind': 'file'}])
    assert response['result']['intent']['paths'][0]['resolvedPath'] == str(outside/'new.py')


async def test_stale_execution_revision_preserves_intent_and_source(app):
    source = app.state['sessions'][0]
    source['executionRevision'] = 2
    result = await declare(app, source, 'stale-placement', expectedExecutionRevision=1)
    assert result['result']['outcome'] == 'stale'
    assert 'workspaceWriteIntent' not in source


async def test_failed_commit_rolls_back_declaration_and_command_then_retry_succeeds(app, monkeypatch):
    source = app.state['sessions'][0]
    original = app._save
    def fail():
        assert app.db.execute("SELECT count(*) FROM commands WHERE id='failed-commit'").fetchone()[0] == 1
        raise sqlite3.OperationalError('Injected storage failure')
    monkeypatch.setattr(app, '_save', fail)
    with pytest.raises(sqlite3.OperationalError, match='Injected'):
        await declare(app, source, 'failed-commit')
    assert 'workspaceWriteIntent' not in source
    assert app.db.execute("SELECT count(*) FROM commands WHERE id='failed-commit'").fetchone()[0] == 0
    monkeypatch.setattr(app, '_save', original)
    assert (await declare(app, source, 'failed-commit'))['result']['outcome'] == 'declared'


async def test_restart_preserves_declaration_and_exact_retry_without_replay(app):
    source = app.state['sessions'][0]
    saved = await app.dispatch('coordination.intent', args(source), command_id='restart-intent')
    directory = app.data_dir; await app.close()
    restored = AppService(directory, Runtime(), workspace=source['workspace'])
    try:
        value = await restored.dispatch('coordination.intent.read', {'sessionId': source['id']})
        assert value['result'] == saved['result']['intent']
        repeated = await restored.dispatch('coordination.intent', args(source), command_id='restart-intent')
        assert repeated['duplicate'] and repeated['result'] == saved['result']
        assert not restored.runtime.inputs
    finally:
        await restored.close()


async def test_hot_awareness_reads_only_active_index_and_changes_with_lifecycle(app, monkeypatch):
    source, peer = app.state['sessions']; peer['status'] = 'working'
    # No transcript bodies are needed to build the ordinary metadata index.
    app.state['sessions'].extend({'id': 'dormant-'+str(i), 'title': 'History', 'workspace': source['workspace'], 'status': 'idle'} for i in range(4000))
    app.projections.invalidate(state=app.state)
    index = app.projections.sessions(app.state)
    class NoScan(list):
        def __iter__(self): raise AssertionError('Scanned the dormant catalog')
    app.state['sessions'] = NoScan(app.state['sessions'])
    monkeypatch.setattr(app.history, 'ensure_loaded', lambda *_: pytest.fail('Loaded history'))
    before = packet(app, source['id'])
    assert len(before['peers']) == 1
    assert packet(app, source['id'])['revision'] == before['revision']
    peer['status'] = 'idle'; index.active.discard(peer['id'])
    after = packet(app, source['id'])
    assert not after['peers'] and after['revision'] != before['revision']
    app.state['sessions'] = app.state['sessions'][:]  # ordinary teardown


async def test_packet_bytes_are_bounded_and_explicit_pages_cover_active_peers(app):
    source = app.state['sessions'][0]
    for i in range(30):
        row = app._new_session({'title': '界'*100})
        row.update(status='working')
        app.state['sessions'].append(row)
        app.projections.invalidate(state=app.state)
        await app.dispatch('coordination.intent', args(row, [{'path': ('界'*100)+'-'+str(j), 'kind': 'file'} for j in range(16)]), command_id='long-'+str(i))
    app._publish()
    cursor, found = 0, set()
    while cursor is not None:
        result = await agent_action(app, source, 'coordination.awareness', {'sessionId': source['id'], 'offset': cursor, 'limit': 25}, 'read')
        value = result['result']
        assert len(json.dumps(value).encode()) <= MAX_PACKET_BYTES
        found.update(row['sessionId'] for row in value['peers'])
        assert value['nextOffset'] is None or value['nextOffset'] > cursor
        cursor = value['nextOffset']
    assert len(found) == 30


async def test_simultaneous_same_revision_has_one_winner_and_one_retained_conflict(app):
    source = app.state['sessions'][0]
    outcomes = await asyncio.gather(*[declare(app, source, str(i), paths=[{'path': str(i)+'.py', 'kind': 'file'}]) for i in range(2)])
    assert sorted(row['result']['outcome'] for row in outcomes) == ['declared', 'stale']
    accepted = next(row for row in outcomes if row['result']['outcome'] == 'declared')
    assert intent_for(app, source) == accepted['result']['intent']
    assert 'paths' not in source['workspaceWriteIntent']


async def test_failure_after_commit_keeps_durable_intent_and_retry_returns_receipt(app, monkeypatch):
    source = app.state['sessions'][0]
    original = app._publish_changes
    def fail(**kwargs):
        original(**kwargs)
        raise RuntimeError('Injected acknowledgment loss')
    monkeypatch.setattr(app, '_publish_changes', fail)
    with pytest.raises(RuntimeError, match='acknowledgment loss'):
        await declare(app, source, 'lost-ack')
    assert intent_for(app, source)['revision'] == 1
    repeated = await declare(app, source, 'lost-ack')
    assert repeated['duplicate'] and repeated['result']['intent']['revision'] == 1


async def test_missing_intent_is_unknown_and_explicit_replacement_can_recover(app):
    source, peer = app.state['sessions']; peer['status'] = 'working'; app._publish()
    await declare(app, source, 'missing')
    app.db.execute("DELETE FROM commands WHERE id='missing'"); app.db.commit()
    observed = packet(app, source['id'])
    assert observed['own']['intent']['status'] == 'unavailable'
    assert observed['peers'][0]['declaredOverlap'] is None
    context = await agent_action(app, source, 'coordination.context', {'sessionId': source['id']}, 'context')
    assert context['result']['writeIntent']['status'] == 'unavailable'
    assert context['result']['writeIntent']['sessionId'] == source['id']
    recovered = await declare(app, source, 'replacement', revision=1)
    assert recovered['result']['intent']['revision'] == 2


async def test_unicode_folders_do_not_exhaust_packet_or_pagination(app):
    source, peer = app.state['sessions']
    originals = source['workspace'], peer['workspace']
    try:
        source['workspace'] = peer['workspace'] = '/'+'界'*600
        peer.update(status='working', title='界'*100)
        app.projections.invalidate(state=app.state)
        value = packet(app, source['id'])
        assert len(json.dumps(value).encode()) <= MAX_PACKET_BYTES
        assert len(value['peers']) == 1 and value['nextOffset'] is None
    finally:
        source['workspace'], peer['workspace'] = originals
