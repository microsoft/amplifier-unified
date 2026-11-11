"""Public native-bridge parity for saved choices; no target runtime/model work."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppError, AppService, command_fingerprint
from amplifier_web.management import Management
from amplifier_web.next_message_configuration import guard_for
from test_coordination_generation import agent


async def mode(app, source, value):
    current = (await app.dispatch('access.get', {'sessionId': source['id']}))['result']
    await app.dispatch('access.set', {'sessionId': source['id'], 'scope': 'session',
        'mode': value, 'expectedRevision': current['revision']})


@pytest.fixture
async def app(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    app.management = Management(app)
    caller = app._new_session({'title': 'Configuration operator', 'bundle': 'work-amp-dev'})
    caller['collaborationGeneration'] = {
        'id': 'operator-generation', 'inputIds': ['human-settings'], 'terminal': False}
    app.state['sessions'].append(caller)
    app.state['selectedSessionId'] = caller['id']
    app._publish()
    await mode(app, caller, 'full')
    app.operator = caller
    yield app
    await app.close()


def peer(app, **patch):
    row = app._new_session({'title': 'Target', 'bundle': 'anchors-amp-dev'})
    row.update(patch)
    app.state['sessions'].append(row)
    app._publish()
    return row


async def configure(app, target, identity='peer-choice', source=None, **extra):
    return await agent(app, source or app.operator, 'conversation.configure', {
        'sessionId': target['id'], 'expectedRevision': target.get('nextMessageRevision', 0),
        'bundle': 'work-amp-dev', **extra}, identity)


@pytest.mark.parametrize('location', ['same-workspace', 'other-workspace', 'managed'])
@pytest.mark.parametrize('status', ['idle', 'working', 'stopped', 'interrupted'])
async def test_full_explicit_target_saves_without_selecting_starting_or_resuming(app, tmp_path, monkeypatch, location, status):
    target = peer(app, status=status, archived=True, interruptionRevision=3,
                  selection={'instance': 'openai', 'model': 'chosen', 'effort': 'high'})
    if location != 'same-workspace':
        folder = tmp_path / location
        folder.mkdir()
        target['workspace'] = str(folder)
        if location == 'managed':
            target['location'] = {'kind': 'managed'}
        app._publish()
    original = deepcopy(target)
    selected = app.state['selectedSessionId']
    monkeypatch.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('History hydration')))
    monkeypatch.setattr(app.management, 'ensure_runtime', AsyncMock(side_effect=AssertionError('Worker startup')))
    receipt = await configure(app, target)
    assert receipt['result'] == {'revision': 1, 'status': 'saved', 'applies': 'next-message', 'mounted': False}
    assert target['bundle'] == 'anchors-amp-dev'
    assert target['pendingBundleSelection']['bundle'] == 'work-amp-dev'
    assert all(target[key] == original[key] for key in ('status', 'archived', 'selection', 'messages', 'interruptionRevision'))
    assert app.state['selectedSessionId'] == selected and app.runtime is None
    authority = target['pendingBundleSelection']['authority']
    assert authority['chain'][0]['sessionId'] == app.operator['id']
    assert authority['operatorConfiguration']['target']['sessionId'] == target['id']
    duplicate = await agent(app, app.operator, 'conversation.configure', {
        'sessionId': target['id'], 'expectedRevision': 0, 'bundle': 'work-amp-dev'}, 'peer-choice')
    assert duplicate['duplicate'] and target['nextMessageRevision'] == 1


async def test_public_read_is_bounded_and_redacts_pending_authority(app, monkeypatch):
    from test_next_message_configuration import retired_configuration
    target = retired_configuration(app, monkeypatch)
    await configure(app, target)
    before = deepcopy(dict.get(target, '_coldFields'))
    monkeypatch.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('History hydration')))
    result = (await agent(app, app.operator, 'conversation.configuration',
        {'sessionId': target['id']}, 'read-choice'))['result']
    assert result['sessionId'] == target['id'] and result['nextMessageRevision'] == 1
    assert result['pendingBundleSelection'] == {'bundle': 'work-amp-dev', 'resetModel': False}
    assert result['blockedReason'] is None
    assert dict.get(target, '_coldFields') == before and app.runtime is None
    assert all(name not in json.dumps(result) for name in ('operatorConfiguration', 'authority', 'accessRevision', 'configuration', 'draft', 'messages'))


async def test_read_dormant_owner_never_admits_or_follows_cold_resources(app, monkeypatch):
    from amplifier_web import cold_display
    from test_next_message_configuration import retired_configuration
    target = retired_configuration(app, monkeypatch)
    await configure(app, target)
    identity = target['id']
    app._publish()
    app.state['sessions'].remove(target)  # Evict overlay; committed owner is retained.
    admitted = {row['id'] for row in app.state['sessions']}
    monkeypatch.setattr(app, '_admit_session', lambda *a, **k: pytest.fail('Passive read admitted owner'))
    monkeypatch.setattr(cold_display, 'load', lambda *a, **k: pytest.fail('Cold body hydration'))
    monkeypatch.setattr(app.history, 'refresh', AsyncMock(side_effect=AssertionError('Native discovery')))
    monkeypatch.setattr(app, '_flush_pending_progress', AsyncMock(side_effect=AssertionError('Progress flush')))
    monkeypatch.setattr(app, 'browser_state', lambda *a, **k: pytest.fail('Full snapshot'))
    result = (await agent(app, app.operator, 'conversation.configuration',
        {'sessionId': identity}, 'dormant-settings'))['result']
    assert result['nextMessageRevision'] == 1
    assert result['pendingBundleSelection']['bundle'] == 'work-amp-dev'
    assert {row['id'] for row in app.state['sessions']} == admitted


async def test_read_cross_workspace_needs_explicit_own_history_scope(app, tmp_path):
    folder = tmp_path / 'other'
    folder.mkdir()
    target = peer(app, workspace=str(folder))
    with pytest.raises(AppError, match='scope=all'):
        await agent(app, app.operator, 'conversation.configuration', {'sessionId': target['id']}, 'narrow-read')
    result = (await agent(app, app.operator, 'conversation.configuration',
        {'sessionId': target['id'], 'scope': 'all'}, 'all-read'))['result']
    assert result['bundle'] == 'anchors-amp-dev'


@pytest.mark.parametrize('failure', ['ask', 'missing-principal', 'forged', 'child', 'system', 'stop', 'worker', 'internal'])
async def test_no_new_authority_from_claimed_target_or_origin(app, failure):
    target = peer(app)
    transport = {}
    if failure == 'ask':
        await mode(app, app.operator, 'ask')
    elif failure == 'missing-principal':
        transport['_runtimeSessionId'] = None
    elif failure == 'forged':
        transport['_runtimeSessionId'] = target['id']
    elif failure == 'child':
        transport['_childOrigin'] = {}
    elif failure == 'stop':
        app.operator['interruptionRevision'] = 1
    elif failure in {'worker', 'internal'}:
        target['sessionKind'] = failure
    with pytest.raises((AppError, ValueError)):
        if failure == 'system':
            await app.dispatch('conversation.configure', {
                'sessionId': target['id'], 'expectedRevision': 0, 'bundle': 'work-amp-dev'},
                origin='system', caller_session_id=app.operator['id'])
        else:
            await agent(app, app.operator, 'conversation.configure', {
                'sessionId': target['id'], 'expectedRevision': 0, 'bundle': 'work-amp-dev'}, 'denied', **transport)
    assert 'pendingBundleSelection' not in target and target.get('nextMessageRevision', 0) == 0


@pytest.mark.parametrize('hold', ['busy', 'fence', 'unknown', 'saved-not-mounted', 'ownership'])
async def test_permission_does_not_clear_or_replace_original_holds(app, hold):
    target = peer(app)
    if hold == 'busy':
        target['configurationBusy'] = True
    elif hold == 'fence':
        target['configurationOperationFence'] = 'retained-operation'
    elif hold == 'ownership':
        target['ownership'] = {'status': 'blocked'}
    else:
        target['pendingConfiguration'] = {'phase': 'error', 'operationId': 'original',
            'effect': 'unknown' if hold == 'unknown' else 'saved_not_mounted'}
    before = deepcopy(target)
    with pytest.raises(AppError):
        await configure(app, target)
    assert target == before
    result = (await agent(app, app.operator, 'conversation.configuration',
        {'sessionId': target['id']}, 'held-read'))['result']
    assert result['blockedReason'] in {'configuration-held', 'ownership'}


async def test_stale_revision_and_other_caller_cannot_reuse_command(app):
    target = peer(app)
    await configure(app, target)
    with pytest.raises(AppError, match='changed'):
        await configure(app, target, identity='stale', expectedRevision=0)
    other = peer(app)
    other['collaborationGeneration'] = {
        'id': 'second-generation', 'inputIds': ['second-human'], 'terminal': False}
    await mode(app, other, 'full')
    with pytest.raises(AppError, match='different contents'):
        await configure(app, target, source=other, expectedRevision=0)
    assert command_fingerprint('conversation.configure', {}, 'agent', None, caller_session_id=app.operator['id']) != command_fingerprint(
        'conversation.configure', {}, 'agent', None, caller_session_id=other['id'])


@pytest.mark.parametrize('change', ['full-off-on', 'caller-stop', 'caller-placement', 'target-stop', 'target-placement'])
async def test_saved_choice_rechecks_original_authority_at_next_input(app, change):
    target = peer(app)
    await configure(app, target)
    captured = deepcopy(target)
    if change == 'full-off-on':
        await mode(app, app.operator, 'ask')
        await mode(app, app.operator, 'full')
    elif change == 'caller-stop':
        app.operator['interruptionRevision'] = 1
    elif change == 'caller-placement':
        app.operator['executionRevision'] = 1
    elif change == 'target-stop':
        target['interruptionRevision'] = 1
    else:
        target['executionRevision'] = 1
    with pytest.raises(Exception, match='authority changed|stopped|placement changed'):
        guard_for(app, captured)()
    assert target['pendingBundleSelection']['bundle'] == 'work-amp-dev' and app.runtime is None


@pytest.mark.parametrize('action', ['conversation.stop', 'worker.spawn', 'bundle.switch', 'capability.read', 'access.set'])
async def test_narrow_parity_does_not_grant_other_peer_controls(app, action):
    target = peer(app)
    args = {'sessionId': target['id']}
    if action == 'bundle.switch':
        args['bundle'] = 'work-amp-dev'
    elif action == 'worker.spawn':
        args['task'] = 'Must not run'
    elif action == 'access.set':
        args.update(scope='session', mode='full', expectedRevision='fake')
    with pytest.raises(AppError):
        await agent(app, app.operator, action, args, 'unrelated-'+action)
    assert app.runtime is None


async def test_cross_workspace_default_uses_actor_authority_and_registered_target(app, tmp_path):
    from amplifier_web.shared_settings import read_yaml
    folder = tmp_path / 'other'
    folder.mkdir()
    await app.dispatch('workspace.add', {'path': str(folder)})
    selected = app.state['selectedSessionId']
    args = {'scope': 'workspace', 'workspace': str(folder), 'bundle': 'work-amp-dev'}
    await agent(app, app.operator, 'bundle.default', args, 'other-default')
    assert read_yaml(folder / '.amplifier/settings.local.yaml')['bundle']['active'] == 'work-amp-dev'
    assert app.state['selectedSessionId'] == selected and app.runtime is None
    await mode(app, app.operator, 'ask')
    with pytest.raises(AppError):
        await agent(app, app.operator, 'bundle.default', {**args, 'bundle': 'anchors'}, 'ask-default')


async def test_unregistered_workspace_default_is_not_an_arbitrary_settings_writer(app, tmp_path):
    folder = tmp_path / 'unregistered'
    folder.mkdir()
    with pytest.raises(AppError, match='registered'):
        await agent(app, app.operator, 'bundle.default', {
            'scope': 'workspace', 'workspace': str(folder), 'bundle': 'work-amp-dev'}, 'unknown-workspace')
    assert not (folder / '.amplifier').exists()


@pytest.mark.parametrize('change', ['caller-stop', 'target-stop', 'full-off-on', 'target-placement'])
async def test_operator_proof_is_not_recaptured_after_commit_lock_wait(app, change):
    target = peer(app)
    await app.lock.acquire()
    pending = asyncio.create_task(configure(app, target))
    try:
        # Wait until dispatch is actually queued on this existing lock.
        async with asyncio.timeout(2):
            while not app.lock._waiters:
                await asyncio.sleep(0)
        if change == 'full-off-on':
            # Access control mutations also take the lock; advance the saved
            # canonical preference while the request is held at this test gate.
            from amplifier_web.access_mode import keys
            identity = keys(app.operator['workspace'], app.operator['id'])['session']
            raw = json.loads(app.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()[0])
            raw['result']['revision'] += 2
            app.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(raw), identity))
            app.db.commit()
        elif change == 'caller-stop':
            app.operator['interruptionRevision'] = 1
        elif change == 'target-stop':
            target['interruptionRevision'] = 1
        else:
            target['executionRevision'] = 1
    finally:
        app.lock.release()
    with pytest.raises(AppError):
        await pending
    assert 'pendingBundleSelection' not in target and app.runtime is None


@pytest.mark.parametrize('peer_mode', ['ask', 'narrow-grant', 'full'])
async def test_lower_authority_peer_cannot_borrow_cross_chat_full(app, peer_mode):
    target = peer(app)
    sender = peer(app)
    sender['collaborationGeneration'] = {
        'id': 'sender-generation', 'inputIds': ['sender-human'], 'terminal': False}
    await mode(app, sender, 'full' if peer_mode == 'full' else 'ask')
    if peer_mode == 'narrow-grant':
        await app.dispatch('capability.grant', {'sessionId': sender['id'],
            'effects': ['bundles'], 'scopes': ['session'], 'reason': 'Only my bundle'})
    request = await agent(app, sender, 'coordination.send', {
        'sessionId': app.operator['id'], 'text': 'Configure the other chat', 'mode': 'notify'}, 'peer-input')
    app.operator['collaborationGeneration'] = {
        'id': 'peer-awakened-operator', 'inputIds': [request['requestId']], 'terminal': False}
    if peer_mode != 'full':
        with pytest.raises(AppError, match='peer'):
            await configure(app, target)
        assert 'pendingBundleSelection' not in target
    else:
        await configure(app, target)
        captured = deepcopy(target)
        assert target['pendingBundleSelection']['authority']['chain'][1]['sessionId'] == sender['id']
        await mode(app, sender, 'ask')
        with pytest.raises(Exception, match='authority changed'):
            guard_for(app, captured)()


async def test_model_only_peer_choice_keeps_delayed_operator_authority(app):
    target = peer(app)
    result = await agent(app, app.operator, 'conversation.configure', {
        'sessionId': target['id'], 'expectedRevision': 0,
        'selection': {'instance': 'openai', 'model': 'chosen', 'effort': 'high'}}, 'model-only')
    assert result['result']['mounted'] is False
    captured = deepcopy(target)
    assert target['pendingChoiceAuthority']['chain'][0]['effect'] == 'model_defaults'
    await mode(app, app.operator, 'ask')
    with pytest.raises(Exception, match='model authority changed'):
        guard_for(app, captured)()


async def test_owner_model_replacement_does_not_borrow_obsolete_peer_authority(app):
    from amplifier_web.conversation_models import select
    target = peer(app)
    await agent(app, app.operator, 'conversation.configure', {
        'sessionId': target['id'], 'expectedRevision': 0,
        'selection': {'instance': 'openai', 'model': 'peer-model'}}, 'peer-model')
    assert target['pendingChoiceAuthority']
    await select(app.management, target, {'instance': 'openai', 'model': 'owner-model'})
    assert 'pendingChoiceAuthority' not in target
    await mode(app, app.operator, 'ask')
    guard_for(app, deepcopy(target))()
    assert target['pendingModelSelection']['model'] == 'owner-model'


@pytest.mark.parametrize('manifest', ['legacy-view', 'oversized-inline', 'malformed-inline', 'valid-session-bad-runtime'])
async def test_passive_read_bounds_manifest_and_never_decodes_runtime(app, manifest):
    from amplifier_web.conversation_configuration import read
    target = peer(app)
    identity = target['id']
    raw = {'id': identity, 'workspace': target['workspace'], 'bundle': 'work-amp-dev', 'nextMessageRevision': 7}
    if manifest == 'legacy-view':
        raw['$view'] = 1
    elif manifest == 'oversized-inline':
        raw['unused'] = 'x' * (1024 * 1024)
    encoded = 'bad-json' if manifest == 'malformed-inline' else json.dumps(raw)
    app.db.execute("UPDATE state_records SET value=? WHERE kind='session' AND id=?", (encoded, identity))
    app.db.execute("INSERT OR REPLACE INTO state_records VALUES('runtime',?,?)", (identity, 'invalid-' + 'x' * 2000000))
    app.db.commit()
    app.state['sessions'].remove(target)
    if manifest == 'valid-session-bad-runtime':
        assert read(app, {'sessionId': identity})['nextMessageRevision'] == 7
    else:
        with pytest.raises(AppError, match='bounded|legacy'):
            read(app, {'sessionId': identity})


async def test_default_rechecks_authority_inside_the_canonical_settings_mutator(app, tmp_path, monkeypatch):
    from amplifier_web.preferences import SettingsStore
    folder = tmp_path / 'registered'
    folder.mkdir()
    await app.dispatch('workspace.add', {'path': str(folder)})
    original = SettingsStore.update
    def mutate(store, workspace, scope, mutator, **kwargs):
        # Canonical settings lock acquired after initial action admission.
        def guarded(value):
            app.operator['interruptionRevision'] = 1
            mutator(value)
        return original(store, workspace, scope, guarded, **kwargs)
    monkeypatch.setattr(SettingsStore, 'update', mutate)
    with pytest.raises(AppError):
        await agent(app, app.operator, 'bundle.default', {
            'scope': 'workspace', 'workspace': str(folder), 'bundle': 'work-amp-dev'}, 'race-default')
    assert not (folder / '.amplifier/settings.local.yaml').exists()


@pytest.mark.parametrize('change', ['defaults-only', 'directory', 'revision', 'native', 'stop'])
async def test_peer_legacy_native_placement_defaults_keep_original_authority(app, change):
    target = peer(app)
    target.pop('workingDirectory', None)
    target.pop('executionRevision', None)
    await configure(app, target)
    authority = target['pendingBundleSelection']['authority']
    witness = authority['operatorConfiguration']['target']
    witness.update(workingDirectory=None, executionRevision=None)
    original = deepcopy(authority)
    target.update(workingDirectory=target['workspace'], executionRevision=0)
    if change == 'directory':
        target['workingDirectory'] += '/different'
    elif change == 'revision':
        target['executionRevision'] = 1
    elif change == 'native':
        target['nativeIdentity'] = 'replaced-native-root'
        target['runtimeSessionId'] = 'replaced-native-root'
    elif change == 'stop':
        target['interruptionRevision'] = 1
    if change == 'defaults-only':
        app.configuration_authority.check(authority)
    else:
        with pytest.raises(AppError):
            app.configuration_authority.check(authority)
    assert authority == original


async def test_native_discovered_root_keeps_saved_intent_after_restart(app, tmp_path, monkeypatch):
    from amplifier_web.host.storage import SessionStore
    store = SessionStore.for_app(app.data_dir, tmp_path)
    native_id = 'native-configurable-root'
    store.save(native_id, [{'role': 'user', 'content': 'Native canonical history'}],
               {'working_dir': str(tmp_path), 'bundle': 'anchors-amp-dev'})
    await app.history.refresh(force=True)
    row = app.directory.resolve(native_id)
    assert row is not None
    target = app._admit_session(row)
    assert target['historyManaged']
    native_before = (store.directory(native_id) / 'transcript.jsonl').read_bytes()
    await configure(app, target)
    assert not target['historyManaged']
    sid, home = target['id'], app.data_dir
    await app.close()
    restored = AppService(home, workspace=tmp_path)
    restored.management = Management(restored)
    try:
        before = {row['id'] for row in restored.state['sessions']}
        monkeypatch.setattr(restored, '_admit_session', lambda *a, **k: pytest.fail('Passive read admitted dormant owner'))
        monkeypatch.setattr(restored.history, 'refresh', AsyncMock(side_effect=AssertionError('History refresh')))
        monkeypatch.setattr(restored, '_flush_pending_progress', AsyncMock(side_effect=AssertionError('Progress publication')))
        result = await restored.dispatch('conversation.configuration', {'sessionId': sid}, include_state=False)
        view = result['result']
        assert view['nextMessageRevision'] == 1 and view['pendingBundleSelection']['bundle'] == 'work-amp-dev'
        assert {row['id'] for row in restored.state['sessions']} == before
        assert (store.directory(native_id) / 'transcript.jsonl').read_bytes() == native_before
    finally:
        await restored.close()