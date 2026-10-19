"""Standing user authorization through the real shared action boundary."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.access_mode import read_mode
from amplifier_web.host.session import _apply_host_policy
from amplifier_web.service import AppError
from test_configuration_authority import app, finished
from test_coordination_generation import agent, until


async def save(app, session=None, mode='full', scope='session', identity=None):
    source = session or app.state['sessions'][0]
    report = (await app.dispatch('access.get', {'sessionId': source['id']}))['result']
    return await app.dispatch('access.set', {'sessionId': source['id'], 'scope': scope,
        'mode': mode, 'expectedRevision': report['revision']}, command_id=identity)


async def test_user_only_precedence_persistence_and_inherit(app):
    source, peer = app.state['sessions']
    assert not app.access_mode.full(source)
    await save(app, scope='app')
    assert app.access_mode.full(source) and app.access_mode.full(peer)
    await save(app, mode='ask', scope='workspace')
    assert not app.access_mode.full(source) and not app.access_mode.full(peer)
    await save(app)
    assert app.access_mode.full(source) and not app.access_mode.full(peer)
    assert read_mode(app.data_dir, source['workspace'], source['id']) == 'full'
    report = app.access_mode.snapshot(source)
    with pytest.raises(AppError, match='Only the user'):
        await agent(app, source, 'access.set', {'sessionId': source['id'], 'scope': 'app',
            'mode': 'full', 'expectedRevision': report['revision']}, 'forged')
    await save(app, mode='inherit')
    assert not app.access_mode.full(source)
    await save(app, mode='inherit', scope='workspace')
    assert app.access_mode.full(source) and app.access_mode.full(peer)


async def test_mode_survives_stop_but_delayed_authority_does_not(app):
    source = app.state['sessions'][0]
    await save(app)
    # Exercise the authority capture inside an authenticated bridge dispatch.
    args = {'sessionId': source['id'], 'scope': 'local', 'allowed': [], 'denied': []}
    await agent(app, source, 'permissions.save', args, 'full-save')
    assert (await finished(app, 'full-save'))['phase'] == 'ready'
    assert not source['approvals']
    authority = {'accessRevision': app.access_mode.snapshot(source)['revision'],
        'sessionId': source['id'], 'workspace': source['workspace'], 'interruptionRevision': 0,
        'effect': 'file_access', 'scope': 'local'}
    app.configuration_authority.check(authority)
    source['interruptionRevision'] = 1
    assert app.access_mode.full(source)
    with pytest.raises(AppError, match='stopped or moved'):
        app.configuration_authority.check(authority)
    source['interruptionRevision'] = 0
    await save(app, mode='ask')
    with pytest.raises(AppError, match='Full access changed'):
        app.configuration_authority.check(authority)


async def test_request_in_full_mode_has_no_card_and_is_revoked_with_mode(app):
    source = app.state['sessions'][0]
    await save(app)
    result = await agent(app, source, 'capability.request', {'sessionId': source['id'],
        'effects': ['file_access'], 'scopes': ['global'], 'reason': 'Requested work'}, 'request')
    grant = result['result']
    assert grant['status'] == 'active' and grant['approvedBy'] == 'full-access'
    assert not source['approvals']
    assert app.configuration_authority.valid(grant, source)
    await save(app, mode='ask')
    assert not app.configuration_authority.valid(grant, source)


async def test_enabling_settles_existing_capability_and_permission_but_not_questions(app):
    source = app.state['sessions'][0]
    replies = []
    async def respond(sid, identity, decision):
        replies.append((sid, identity, decision))
    app.runtime.approval = respond
    receipt = await agent(app, source, 'capability.request', {'sessionId': source['id'],
        'effects': ['test_environments'], 'scopes': ['session'], 'reason': 'Check'}, 'pending',
        _configurationWait=True)
    grant = receipt['result']
    assert grant['status'] == 'pending'
    source['approvals'] += [
        {'id': 'ordinary', 'status': 'pending', 'options': ['allow', 'deny']},
        {'id': 'question', 'status': 'pending', 'options': ['first', 'second']},
        {'id': 'expired', 'status': 'pending', 'options': ['allow', 'deny'], 'resolutionReason': 'host_restarted'}]
    source['status'] = 'working'
    await save(app)
    await until(lambda: len(replies) == 2)
    await app.access_mode.settle(source['id'])
    assert len(replies) == 2
    assert {r[1] for r in replies} == {grant['id'], 'ordinary'}
    assert app.configuration_authority.record(grant['id'])['status'] == 'active'
    assert next(r for r in source['approvals'] if r['id'] == 'question')['status'] == 'pending'
    assert source['fileAccessPending']
    assert not any(call[0] == 'start' for call in app.runtime.calls)


async def test_stale_preview_and_exact_retry_do_not_change_authority_twice(app):
    source = app.state['sessions'][0]
    before = app.access_mode.snapshot(source)
    receipt = await save(app, identity='save-access')
    args = {'sessionId': source['id'], 'scope': 'session', 'mode': 'full', 'expectedRevision': before['revision']}
    duplicate = await app.dispatch('access.set', args, command_id='save-access')
    assert duplicate['duplicate'] and duplicate['result'] == receipt['result']
    with pytest.raises(AppError, match='Access settings changed'):
        await app.dispatch('access.set', {**args, 'mode': 'ask'}, command_id='stale')
    assert app.access_mode.full(source)


async def test_peer_and_unrelated_workspace_are_not_implicitly_granted(app, tmp_path):
    source, peer = app.state['sessions']
    await save(app)
    assert not app.access_mode.full(peer)
    peer['collaborationGeneration'] = {'id': 'peer-gen', 'inputIds': ['human-peer'], 'terminal': False}
    with pytest.raises(AppError):
        await agent(app, peer, 'access.get', {'sessionId': source['id']}, 'cross-read')
    with pytest.raises(AppError, match='Configuration authority is required'):
        await agent(app, peer, 'permissions.save', {'sessionId': peer['id'], 'scope': 'local',
            'allowed': [], 'denied': []}, 'cross-grant')


@pytest.mark.parametrize('module', ['tool-filesystem', 'tool-apply-patch', 'tool-image', 'tool-search'])
def test_full_file_policy_reversible_for_roots_children_and_saved_plans(tmp_path, module):
    config = SimpleNamespace(workspace=tmp_path, settings={}, access_mode='ask')
    original = {'allowed_paths': [], 'denied_paths': [str(tmp_path/'private')]} if module=='tool-search' else {
        'allowed_write_paths': [], 'denied_write_paths': [str(tmp_path/'private')]}
    bundle = SimpleNamespace(tools=[{'module': module, 'config': original}],
        agents={'child': {'tools': [{'module': module, 'config': copy.deepcopy(original)}]}})
    asked = copy.deepcopy(_apply_host_policy(bundle, config))
    config.access_mode = 'full'
    full = _apply_host_policy(bundle, config)
    mounted = full.tools[0]['config']
    assert mounted.get('denied_write_paths', mounted.get('denied_paths')) == []
    if module == 'tool-search':
        assert mounted['allowed_paths'] is None
    else:
        assert str(tmp_path.anchor) in mounted['allowed_write_paths']
    # Simulate a persisted mounted plan reloaded after revocation.
    saved = copy.deepcopy(full)
    config.access_mode = 'ask'
    restored = _apply_host_policy(saved, config)
    assert restored.tools == asked.tools and restored.agents == asked.agents


async def test_admission_revoked_while_waiting_for_lock_does_not_save(app):
    source = app.state['sessions'][0]
    report = app.access_mode.snapshot(source)
    admitted = True
    def check():
        if not admitted:
            raise AppError('Client access revoked', 403)
    async with app.lock:
        pending = asyncio.create_task(app.dispatch('access.set', {'sessionId': source['id'],
            'scope': 'session', 'mode': 'full', 'expectedRevision': report['revision']}, admission_check=check))
        await asyncio.sleep(.02)
        admitted = False
    with pytest.raises(AppError, match='Client access revoked'):
        await pending
    assert not app.access_mode.full(source)


async def test_permission_delivery_retry_resolves_only_existing_waiter(app):
    source = app.state['sessions'][0]
    attempts = []
    async def respond(sid, identity, decision):
        attempts.append(identity)
        if len(attempts) == 1:
            await asyncio.sleep(1)
    app.runtime.approval = respond
    app.access_mode.approval_timeout = .01
    source['status'] = 'working'
    source['approvals'] = [{'id': 'only-waiter', 'status': 'pending', 'options': ['allow', 'deny']}]
    await save(app)
    await until(lambda: source['approvals'][0]['status'] == 'allow')
    assert attempts == ['only-waiter', 'only-waiter']
    assert not source['approvals'][0].get('deliveryError')
    assert not any(call[0] == 'start' for call in app.runtime.calls)


@pytest.mark.parametrize('module', ['tool-filesystem', 'tool-search'])
def test_full_plan_restoration_keeps_later_non_policy_edits(tmp_path, module):
    config = SimpleNamespace(workspace=tmp_path, settings={}, access_mode='full')
    bundle = SimpleNamespace(tools=[{'module': module, 'config': {'timeout': 1}}], agents={})
    _apply_host_policy(bundle, config)
    bundle.tools[0]['config']['timeout'] = 99
    if module == 'tool-search':
        bundle.tools[0]['config']['grep']['max_results'] = 13
    config.access_mode = 'ask'
    _apply_host_policy(bundle, config)
    assert bundle.tools[0]['config']['timeout'] == 99
    if module == 'tool-search':
        assert bundle.tools[0]['config']['grep']['max_results'] == 13


async def test_new_approval_during_resolution_is_not_lost(app):
    source = app.state['sessions'][0]
    source['status'] = 'working'
    replies = []
    async def respond(sid, identity, decision):
        replies.append(identity)
        if identity == 'first':
            source['approvals'].append({'id': 'second', 'status': 'pending', 'options': ['allow', 'deny']})
            await app.access_mode.settle(sid)
    app.runtime.approval = respond
    source['approvals'] = [{'id': 'first', 'status': 'pending', 'options': ['allow', 'deny']}]
    await save(app)
    await until(lambda: replies == ['first', 'second'])
    assert all(row['status'] == 'allow' for row in source['approvals'])


async def test_failed_policy_commit_restores_mode_and_admission_fence(app, monkeypatch):
    source = app.state['sessions'][0]
    source['status'] = 'working'
    def fail(**kwargs):
        raise OSError('Storage unavailable')
    monkeypatch.setattr(app, '_publish_changes', fail)
    with pytest.raises(OSError, match='Storage unavailable'):
        await save(app)
    assert not app.access_mode.full(source)
    assert not source.get('fileAccessPending')


async def test_native_file_mount_uses_saved_mode_and_restores_restriction(app, tmp_path):
    filesystem = pytest.importorskip('amplifier_module_tool_filesystem')
    pytest.importorskip('amplifier_module_loop_live')
    pytest.importorskip('amplifier_module_context_simple')
    from amplifier_core import AmplifierSession
    from amplifier_web.host.config import read_config
    source = app.state['sessions'][0]
    source['nativeIdentity'] = 'older-native-alias'
    source['runtimeSessionId'] = 'native-root'
    external = tmp_path.parent / (tmp_path.name+'-external')
    external.mkdir()
    bundle = SimpleNamespace(tools=[{'module': 'tool-filesystem'}], agents={})
    async def write(name):
        config = read_config(source['workspace'], home=app.data_dir, session_id='native-root')
        _apply_host_policy(bundle, config)
        native = AmplifierSession({'session': {'orchestrator': {'module': 'loop-live',
            'config': {'configured_bundle': True, 'native_provider': True}}, 'context': {'module': 'context-simple'}}, 'providers': []})
        try:
            await native.initialize()
            native.coordinator.register_capability('session.working_dir', source['workspace'])
            await filesystem.mount(native.coordinator, bundle.tools[0]['config'])
            return await native.coordinator.get('tools')['write_file'].execute({'file_path': str(external/name), 'content': name})
        finally:
            await native.cleanup()
    assert not (await write('before.txt')).success
    await save(app)
    assert (await write('full.txt')).success
    await save(app, mode='ask')
    assert not (await write('after.txt')).success
    assert not (external/'before.txt').exists() and not (external/'after.txt').exists()
    assert (external/'full.txt').read_text() == 'full.txt'
