"""Real shared dispatch and persistent settings; runtime effects are controlled."""
import asyncio
import copy
import json
import time

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.management import Management
from amplifier_web.preferences import SettingsStore
from test_coordination_generation import agent, until, source_plan


class Runtime:
    def __init__(self):
        self.calls = []

    async def start(self, session, emit):
        self.calls.append(('start', session['id']))

    async def control(self, sid, operation, args):
        self.calls.append((operation, sid, args))
        if operation == 'provider.select':
            return {'selection': args}
        if operation == 'configuration.providers':
            return {'providers': []}
        return {}

    async def stop(self, sid, *, reason="stopped"):
        self.calls.append(('stop', sid))

    async def close(self):
        pass


@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    source, peer = [service._new_session({'title': title}) for title in ('Owner', 'Peer')]
    service.state['sessions'] = [source, peer]
    service.state['selectedSessionId'] = source['id']
    source['collaborationGeneration'] = {'id': 'generation-1', 'inputIds': ['user-input'], 'terminal': False}
    source_plan(service, source)
    service._publish()
    service.management = Management(service)
    yield service
    await service.close()


async def grant(app, effects, scopes, identity='grant'):
    source = app.state['sessions'][0]
    return (await app.dispatch('capability.grant', {'sessionId': source['id'], 'effects': effects,
        'scopes': scopes, 'reason': 'Perform the requested setup'}, command_id=identity))['result']


async def revoke(app, row):
    return await app.dispatch('capability.revoke', {'sessionId': row['sessionId'], 'id': row['id'],
        'expectedRevision': row['revision']}, command_id='revoke-' + row['id'])


async def finished(app, identity):
    await until(lambda: identity in app.state.get('managementResults', {}))
    return app.state['managementResults'][identity]


async def test_explicit_authority_is_reusable_scoped_and_does_not_grant_other_effects(app):
    source = app.state['sessions'][0]
    args = {'sessionId': source['id'], 'scope': 'local', 'allowed': [], 'denied': []}
    with pytest.raises(AppError, match='Configuration authority is required'):
        await agent(app, source, 'permissions.save', args, 'before-grant')
    row = await grant(app, ['file_access'], ['local'])
    for identity in ('first', 'second'):
        await agent(app, source, 'permissions.save', args, identity)
        assert (await finished(app, identity))['phase'] == 'ready'
    assert len(app.configuration_authority.rows(source['id'])) == 1
    assert not source['approvals']
    with pytest.raises(AppError, match='Configuration authority is required'):
        await agent(app, source, 'permissions.save', {**args, 'scope': 'global'}, 'other-scope')
    with pytest.raises(AppError, match='Configuration authority is required'):
        await agent(app, source, 'modules.save', {'scope': 'local', 'section': 'tools', 'module': 'tool-fixture'}, 'other-effect')
    await revoke(app, row)
    with pytest.raises(AppError, match='Configuration authority is required'):
        await agent(app, source, 'permissions.save', args, 'after-revoke')


async def test_user_grant_keeps_every_current_input_without_authorizing_later_peers(app):
    source, peer = app.state['sessions']
    peer['collaborationGeneration'] = {'id': 'peer-origin', 'inputIds': ['peer-human'], 'terminal': False}
    ids = []
    for index in range(65):
        result = await agent(app, peer, 'coordination.send', {
            'sessionId': source['id'], 'text': 'Inspect a setup change', 'mode': 'notify'}, f'grant-input-{index}')
        ids.append(result['requestId'])
    source['collaborationGeneration'] = {'id': 'current-approved-inputs', 'inputIds': list(ids), 'terminal': False}
    approved = await grant(app, ['file_access'], ['local'], 'approve-complete-inputs')
    assert approved['authorizedInputIds'] == ids
    args = {'scope': 'local', 'allowed': [], 'denied': []}
    await agent(app, source, 'permissions.save', args, 'complete-approved-change')
    assert (await finished(app, 'complete-approved-change'))['phase'] == 'ready'
    later = await agent(app, peer, 'coordination.send', {
        'sessionId': source['id'], 'text': 'A new setup request', 'mode': 'notify'}, 'later-unapproved')
    source['collaborationGeneration']['inputIds'].append(later['requestId'])
    with pytest.raises(AppError, match='cannot borrow'):
        await agent(app, source, 'permissions.save', args, 'later-unapproved-change')
    assert app.configuration_authority.record(approved['id'])['authorizedInputIds'] == ids
    source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    assert not app.configuration_authority.valid(approved, source)


async def test_standard_approval_is_durable_user_only_and_exact_retry(app):
    source = app.state['sessions'][0]
    args = {'sessionId': source['id'], 'effects': ['modules'], 'scopes': ['project'], 'reason': 'Add the requested tool'}
    receipt = await agent(app, source, 'capability.request', args, 'request')
    row = receipt['result']
    assert row['status'] == 'pending'
    assert source['approvals'][0]['id'] == row['id']
    assert (await agent(app, source, 'capability.request', args, 'request'))['duplicate']
    approval = {'sessionId': source['id'], 'id': row['id'], 'decision': 'allow'}
    with pytest.raises(AppError, match='Only the user'):
        await agent(app, source, 'approval.respond', approval, 'self-approve')
    with pytest.raises(AppError, match='Only the user'):
        await agent(app, source, 'capability.grant', args, 'self-grant')
    await app.close()
    restored = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    try:
        assert restored._session(source['id'])['approvals'][0]['status'] == 'pending'
        decided = await restored.dispatch('approval.respond', approval, command_id='approve')
        assert decided['result']['status'] == 'active'
        assert (await restored.dispatch('approval.respond', approval, command_id='approve'))['duplicate']
        with pytest.raises(AppError, match='different contents'):
            await restored.dispatch('approval.respond', {**approval, 'decision': 'deny'}, command_id='approve')
        assert restored.configuration_authority.snapshot(restored._session(source['id']))['grants'][0]['usable']
    finally:
        await restored.close()


@pytest.mark.parametrize('change', ['child', 'stale', 'missing', 'peer', 'scheduler'])
async def test_grants_do_not_replace_actual_principal_and_generation(app, change):
    source, peer = app.state['sessions']
    await grant(app, ['file_access'], ['global'])
    args = {'scope': 'global', 'allowed': [], 'denied': []}
    with pytest.raises(AppError):
        if change == 'scheduler':
            await app.dispatch('permissions.save', {**args, 'sessionId': source['id']}, origin='scheduler')
        else:
            transport = {'child': {'_runtimeSessionId': 'native-child'}, 'stale': {'_generationId': 'old'},
                         'missing': {'_generationId': None}, 'peer': {}}[change]
            await agent(app, source, 'permissions.save', {**args, **({'sessionId': peer['id']} if change == 'peer' else {})}, 'bad-' + change, **transport)


async def test_management_wait_rechecks_revoke_before_writes(app):
    source = app.state['sessions'][0]
    row = await grant(app, ['file_access'], ['local'])
    await app.management.lock.acquire()
    try:
        await agent(app, source, 'permissions.save', {'scope': 'local', 'allowed': ['/should-not-write'], 'denied': []}, 'waiting')
        await until(lambda: app.state.get('actionStatus', {}).get('permissions.save', {}).get('phase') == 'queued')
        await revoke(app, row)
    finally:
        app.management.lock.release()
    result = await finished(app, 'waiting')
    assert result['phase'] == 'error' and 'revoked' in result['error']
    assert SettingsStore(app.data_dir).read(source['workspace'], 'local') == {}


async def test_validation_rechecks_before_saving_after_revocation(app, monkeypatch):
    from amplifier_web.registry import RegistryManager
    source = app.state['sessions'][0]
    row = await grant(app, ['modules'], ['local'])
    entered, release = asyncio.Event(), asyncio.Event()
    async def validate(*args):
        entered.set()
        await release.wait()
        return {'validation': {'passed': True}}
    monkeypatch.setattr(RegistryManager, '_validate_row', validate)
    await agent(app, source, 'sources.save', {'scope': 'local', 'kind': 'module', 'name': 'tool-fixture',
        'section': 'tools', 'source': source['workspace'], 'validate': True}, 'validate')
    await entered.wait()
    await revoke(app, row)
    release.set()
    result = await finished(app, 'validate')
    assert result['phase'] == 'error' and 'revoked' in result['error']
    assert SettingsStore(app.data_dir).read(source['workspace'], 'local') == {}


async def test_native_control_rechecks_after_runtime_preparation(app, monkeypatch):
    source = app.state['sessions'][0]
    row = await grant(app, ['runtime_configuration'], ['session'])
    entered, release = asyncio.Event(), asyncio.Event()
    async def prepare(*args, **kwargs):
        entered.set()
        await release.wait()
    monkeypatch.setattr(app.management, 'ensure_runtime', prepare)
    await agent(app, source, 'runtime.control', {'sessionId': source['id'], 'operation': 'configuration.toggle',
        'args': {'section': 'tools', 'name': 'tool-fixture', 'enabled': True}}, 'toggle')
    await entered.wait()
    await revoke(app, row)
    release.set()
    result = await finished(app, 'toggle')
    assert result['phase'] == 'error' and 'revoked' in result['error']
    assert not app.runtime.calls


async def test_existing_provider_selection_does_not_require_administration(app):
    source = app.state['sessions'][0]
    await agent(app, source, 'runtime.control', {'sessionId': source['id'], 'operation': 'provider.select',
        'args': {'instance': 'existing', 'model': 'configured-model'}}, 'select')
    assert (await finished(app, 'select'))['phase'] == 'ready'
    assert any(row[0] == 'provider.select' for row in app.runtime.calls)
    assert not app.configuration_authority.rows(source['id'])


async def test_global_configuration_target_and_direct_user_administration(app):
    source = app.state['sessions'][0]
    await grant(app, ['modules'], ['global'])
    await agent(app, source, 'modules.save', {'configurationTarget': {'kind': 'global'},
        'section': 'tools', 'module': 'tool-fixture', 'config': {}}, 'target')
    assert (await finished(app, 'target'))['phase'] == 'ready'
    assert SettingsStore(app.data_dir).read(source['workspace'], 'global')['config']['tools'][0]['module'] == 'tool-fixture'
    await app.dispatch('permissions.save', {'sessionId': source['id'], 'scope': 'project', 'allowed': [], 'denied': []}, command_id='user')
    assert (await finished(app, 'user'))['phase'] == 'ready'


@pytest.mark.parametrize('change', ['stop', 'revoke', 'expired'])
async def test_delayed_mount_plan_retains_authority_across_restart(app, change):
    source = app.state['sessions'][0]
    row = await grant(app, ['runtime_configuration'], ['session'])
    source['status'] = 'working'
    plan = {'providers': [{'module': 'provider-fixture'}], 'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}}}
    await agent(app, source, 'configuration.apply', {'id': source['id'], 'config': plan, 'whenIdle': True}, 'queued')
    assert (await finished(app, 'queued'))['phase'] == 'ready'
    assert source['pendingConfiguration']['authority']['grantId'] == row['id']
    await app.close()
    restored = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    try:
        current = restored._session(source['id'])
        if change == 'revoke':
            await revoke(restored, row)
        elif change == 'expired':
            saved = restored.configuration_authority.record(row['id'])
            saved['expiresAt'] = time.time() - 1
            restored.configuration_authority.save(saved)
        else:
            current['interruptionRevision'] = current.get('interruptionRevision', 0) + 1
        current['status'] = 'idle'
        restored._publish()
        restored.management = Management(restored)
        await until(lambda: current['pendingConfiguration']['phase'] not in {'queued', 'applying'})
        assert current['pendingConfiguration']['phase'] == 'error'
        assert not restored.runtime.calls
        assert json.loads(restored.management.queued_path(current['id']).read_text()) == plan
    finally:
        await restored.close()


async def test_declined_and_stopped_requests_never_authorize_and_user_grants_do_not_copy(app):
    source, peer = app.state['sessions']
    args = {'sessionId':source['id'], 'effects':['file_access'], 'scopes':['global'], 'reason':'Configure the requested access'}
    pending = (await agent(app,source,'capability.request',args,'declined-request'))['result']
    await app.dispatch('approval.respond',{'sessionId':source['id'],'id':pending['id'],'decision':'deny'})
    assert not app.configuration_authority.snapshot(source)['grants'][0]['usable']
    row = await grant(app,['file_access'],['global'])
    peer['collaborationGeneration']={'id':'peer-run','inputIds':['peer-input'],'terminal':False}
    peer['collaboration']={'creatorSessionId':source['id']}
    with pytest.raises(AppError,match='Configuration authority is required'):
        await agent(app,peer,'permissions.save',{'scope':'global','allowed':[],'denied':[]},'peer-no-grant')
    assert app.configuration_authority.rows(peer['id']) == []
    pending = (await agent(app,source,'capability.request',args,'stopped-request'))['result']
    await app.dispatch('conversation.stop',{'sessionId':source['id']})
    cleared = await app.dispatch('approval.respond',{'sessionId':source['id'],'id':pending['id'],'decision':'allow'})
    assert cleared['decisionApplied'] is False and cleared['result']['status'] == 'expired'
    assert not app.configuration_authority.valid(row, source)


async def test_grant_receipts_handle_failed_commit_and_lost_acknowledgment(app,monkeypatch):
    source=app.state['sessions'][0]
    args={'sessionId':source['id'],'effects':['file_access'],'scopes':['local'],'reason':'Requested setup'}
    publish=app._publish_changes
    def failure(**kwargs):
        raise RuntimeError('before commit')
    monkeypatch.setattr(app,'_publish_changes',failure)
    with pytest.raises(RuntimeError):
        await agent(app,source,'capability.request',args,'failed')
    assert not app.configuration_authority.rows(source['id']) and not source['approvals']
    def lost_ack(**kwargs):
        publish(**kwargs)
        raise RuntimeError('after commit')
    monkeypatch.setattr(app,'_publish_changes',lost_ack)
    with pytest.raises(RuntimeError):
        await agent(app,source,'capability.request',args,'lost-ack')
    assert len(app.configuration_authority.rows(source['id']))==1 and len(source['approvals'])==1
    monkeypatch.setattr(app,'_publish_changes',publish)
    assert (await agent(app,source,'capability.request',args,'lost-ack'))['duplicate']


@pytest.mark.parametrize('change', ['stop', 'generation', 'workspace', 'placement'])
async def test_request_revalidates_original_authority_after_waiting_for_commit_lock(app, monkeypatch, change):
    source = app.state['sessions'][0]
    args = {'sessionId': source['id'], 'effects': ['file_access'], 'scopes': ['local'], 'reason': 'Requested setup'}
    entered = asyncio.Event()
    original = app.configuration_authority.source
    def inspect(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        return result
    monkeypatch.setattr(app.configuration_authority, 'source', inspect)
    async with app.lock:
        pending = asyncio.create_task(agent(app, source, 'capability.request', args, 'racing-request'))
        await entered.wait()
        if change == 'stop':
            source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
        elif change == 'generation':
            source['collaborationGeneration'] = {'id': 'new-generation', 'inputIds': ['new-user'], 'terminal': False}
        elif change == 'workspace':
            source['workspace'] += '-moved'
        else:
            source['executionRevision'] = source.get('executionRevision', 0) + 1
    with pytest.raises(AppError, match='changed|current transport'):
        await pending
    assert not app.configuration_authority.rows(source['id'])
    assert not source['approvals']


async def test_user_grant_binds_current_state_after_lock_and_duplicate_stays_passive(app):
    source = app.state['sessions'][0]
    args = {'sessionId': source['id'], 'effects': ['file_access'], 'scopes': ['local'], 'reason': 'Requested setup'}
    async with app.lock:
        pending = asyncio.create_task(app.dispatch('capability.grant', args, command_id='user-waiting'))
        await asyncio.sleep(0)
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    first = await pending
    assert first['result']['interruptionRevision'] == source['interruptionRevision']
    duplicate = await app.dispatch('capability.grant', args, command_id='user-waiting')
    assert duplicate['duplicate'] and duplicate['result'] == first['result']


async def test_committed_agent_retry_survives_generation_change_while_waiting(app, monkeypatch):
    source = app.state['sessions'][0]
    args = {'sessionId': source['id'], 'effects': ['file_access'], 'scopes': ['local'], 'reason': 'Requested setup'}
    first = await agent(app, source, 'capability.request', args, 'retained-request')
    entered = asyncio.Event()
    original = app.configuration_authority.source
    def inspect(*args, **kwargs):
        value = original(*args, **kwargs)
        entered.set()
        return value
    monkeypatch.setattr(app.configuration_authority, 'source', inspect)
    async with app.lock:
        pending = asyncio.create_task(agent(app, source, 'capability.request', args, 'retained-request'))
        await entered.wait()
        source['collaborationGeneration'] = {'id': 'new-generation', 'inputIds': ['new-user'], 'terminal': False}
    duplicate = await pending
    assert duplicate['duplicate'] and duplicate['result'] == first['result']
    assert len(app.configuration_authority.rows(source['id'])) == 1


async def test_peer_cannot_launder_privilege_but_reviewed_current_request_can(app):
    source, peer=app.state['sessions']
    await grant(app,['file_access'],['local'])
    peer['collaborationGeneration']={'id':'peer-gen','inputIds':['peer-user'],'terminal':False}
    receipt=await agent(app,peer,'coordination.send',{'sessionId':source['id'],'text':'Change the owner settings','mode':'notify'},'peer-admin')
    source['collaborationGeneration']={'id':'owner-peer-woken','inputIds':[receipt['requestId']],'terminal':False}
    with pytest.raises(AppError,match='cannot borrow'):
        await agent(app,source,'permissions.save',{'scope':'local','allowed':[],'denied':[]},'laundered')
    request=(await agent(app,source,'capability.request',{'sessionId':source['id'],'effects':['file_access'],
        'scopes':['local'],'reason':'Authorize this peer-requested configuration change'},'current-approval'))['result']
    await app.dispatch('approval.respond',{'sessionId':source['id'],'id':request['id'],'decision':'allow'})
    await agent(app,source,'permissions.save',{'scope':'local','allowed':[],'denied':[]},'reviewed')
    assert (await finished(app,'reviewed'))['phase']=='ready'


async def test_peer_authority_is_rechecked_during_delayed_management(app):
    source,peer=app.state['sessions']
    await grant(app,['file_access'],['local'])
    peer['collaborationGeneration']={'id':'peer-gen','inputIds':['peer-human'],'terminal':False}
    inherited=(await app.dispatch('capability.grant',{'sessionId':peer['id'],'effects':['file_access'],
        'scopes':['local'],'reason':'Explicit peer administration'}))['result']
    receipt=await agent(app,peer,'coordination.send',{'sessionId':source['id'],'text':'Apply requested settings','mode':'notify'},'authorized-peer')
    source['collaborationGeneration']={'id':'owner-peer-woken','inputIds':[receipt['requestId']],'terminal':False}
    await app.management.lock.acquire()
    try:
        await agent(app,source,'permissions.save',{'scope':'local','allowed':[],'denied':[]},'peer-delayed')
        await revoke(app,inherited)
    finally:
        app.management.lock.release()
    result=await finished(app,'peer-delayed')
    assert result['phase']=='error' and 'originating peer' in result['error']


@pytest.mark.parametrize('lower_authority_origin', [False, True])
async def test_saved_own_dependency_return_keeps_authorized_administration(app, lower_authority_origin):
    source,peer=app.state['sessions']
    await grant(app,['file_access'],['local'])
    # These host-owned receipts are the continuation owner's durable proof;
    # peer-authored replyTo text cannot create a system continuation receipt.
    original={'commandAction':'coordination.send','senderSessionId':source['id'],'target':{'sessionId':peer['id']},
              'origin':'agent','sourceInputIds':['user-input'],'sourceGenerationId':'generation-1','sourceInterruptionRevision':0}
    if lower_authority_origin:
        peer['collaborationGeneration']={'id':'peer-gen','inputIds':['peer-human'],'terminal':False}
        incoming=await agent(app,peer,'coordination.send',{'sessionId':source['id'],'text':'Alter configuration','mode':'notify'},'lower-origin')
        original['sourceInputIds']=[incoming['requestId']]
    returned={'commandAction':'coordination.send','origin':'system','dependencyRequestId':'own-request',
              'senderSessionId':peer['id'],'target':{'sessionId':source['id']}}
    for identity,value in [('own-request',original),('own-return',returned)]:
        app.db.execute('INSERT INTO commands VALUES(?,?,?)',(identity,identity,json.dumps(value)))
    source['collaborationGeneration']={'id':'returned','inputIds':['own-return'],'terminal':False}
    if lower_authority_origin:
        with pytest.raises(AppError,match='cannot borrow'):
            await agent(app,source,'permissions.save',{'scope':'local','allowed':[],'denied':[]},'borrow-through-helper')
        return
    await agent(app,source,'permissions.save',{'scope':'local','allowed':[],'denied':[]},'continue-admin')
    assert (await finished(app,'continue-admin'))['phase']=='ready'


async def test_alternate_global_default_routes_have_equivalent_grants(app):
    source=app.state['sessions'][0]
    with pytest.raises(AppError,match='Configuration authority is required'):
        await agent(app,source,'settings.update',{'patch':{'bundle':'new-root'}},'ungranted-default')
    await grant(app,['bundles'],['global'])
    await agent(app,source,'settings.update',{'patch':{'bundle':'new-root'}},'granted-default')
    assert SettingsStore(app.data_dir).read(source['workspace'],'global')['bundle']['active']=='new-root'
    with pytest.raises(AppError,match='Configuration authority is required'):
        await agent(app,source,'settings.update',{'patch':{'bundle':'different','updates':{'autoInstall':True}}},'mixed-default')
    assert app.state['settings']['bundle']=='new-root'
    with pytest.raises(AppError,match='Configuration authority is required'):
        await agent(app,source,'runtime.retention.update',{'patch':{'max_warm_workers':2}},'background-policy')


async def test_assignment_stop_fences_queued_configuration(app):
    source=app.state['sessions'][0]
    await grant(app,['file_access'],['local'])
    assignment=(await app.dispatch('assignment.start',{'sessionId':source['id']}))['result']
    await app.management.lock.acquire()
    try:
        await agent(app,source,'permissions.save',{'scope':'local','allowed':[],'denied':[]},'assignment-queued')
        await app.dispatch('assignment.stop',{'sessionId':source['id'],'expectedRevision':assignment['assignment']['revision']})
    finally:
        app.management.lock.release()
    result=await finished(app,'assignment-queued')
    assert result['phase']=='error' and 'stopped' in result['error']
    assert SettingsStore(app.data_dir).read(source['workspace'],'local')=={}


@pytest.mark.parametrize('kind', ['watch', 'creation_failure'])
@pytest.mark.parametrize('lower_authority_origin', [False, True])
async def test_actual_system_returns_preserve_original_configuration_authority(app, monkeypatch, kind, lower_authority_origin):
    source, peer = app.state['sessions']
    await grant(app, ['file_access'], ['local'])
    # Keep notification queued; native generation linkage is controlled here,
    # while the real host creates the original operation and exact return.
    source['status'] = 'working'
    async def input_adapter(*args):
        pytest.fail('A busy fixture root must not admit a notification')
    app.runtime.collaboration_input = input_adapter
    if lower_authority_origin:
        peer['collaborationGeneration'] = {'id':'peer-origin','inputIds':['peer-human'],'terminal':False}
        incoming = await agent(app, peer, 'coordination.send', {'sessionId':source['id'], 'text':'Change settings', 'mode':'notify'}, 'unprivileged-peer')
        source['collaborationGeneration'] = {'id':'peer-woken','inputIds':[incoming['requestId']],'terminal':False}
        with pytest.raises(AppError, match='cannot borrow'):
            await agent(app, source, 'permissions.save', {'scope':'local','allowed':[],'denied':[]}, 'direct-borrow')
    original_inputs = source['collaborationGeneration']['inputIds'][:]
    if kind == 'watch':
        original = await agent(app, source, 'coordination.watch', {'sessionId':source['id'],
            'targetSessionId':peer['id'], 'condition':'idle', 'expiresInSeconds':60}, 'observed')
    else:
        async def unavailable(*args):
            raise RuntimeError('Fixture preparation failed before root creation')
        monkeypatch.setattr(app.collaboration, '_finish_create', unavailable)
        original = await agent(app, source, 'coordination.create', {'title':'Unavailable helper','text':'Inspect a dependency'}, 'observed')
        await until(lambda: app.collaboration.receipt('observed')['subscription']['status'] == 'claimed')
        original = app.collaboration.receipt('observed')
    assert original['sourceInputIds'] == original_inputs
    assert original['sourceGenerationId'] == source['collaborationGeneration']['id']
    returned = app.collaboration.receipt(original['subscription']['continuationId'])
    assert returned['origin'] == 'system' and returned['dependencyRequestId'] == 'observed'
    source['collaborationGeneration'] = {'id':'returned','inputIds':[returned['requestId']],'terminal':False}
    if lower_authority_origin:
        with pytest.raises(AppError, match='cannot borrow'):
            await agent(app, source, 'permissions.save', {'scope':'local','allowed':[],'denied':[]}, 'after-return')
    else:
        await agent(app, source, 'permissions.save', {'scope':'local','allowed':[],'denied':[]}, 'after-return')
        assert (await finished(app, 'after-return'))['phase'] == 'ready'


@pytest.mark.parametrize('old', [False, True])
async def test_explicit_user_watch_reuses_grant_but_unknown_legacy_watch_requires_approval(app, old):
    source, peer = app.state['sessions']
    await grant(app, ['file_access'], ['local'])
    source['status'] = 'working'
    async def input_adapter(*args):
        pytest.fail('The busy root must not admit work')
    app.runtime.collaboration_input = input_adapter
    observed = await app.dispatch('coordination.watch', {'sessionId':source['id'], 'targetSessionId':peer['id'],
        'condition':'idle', 'expiresInSeconds':60}, command_id='user-watch')
    assert observed['origin'] == 'ui' and observed['sourceInputIds'] == []
    if old:
        for key in ('origin', 'sourceGenerationId', 'sourceInputIds', 'sourceInterruptionRevision'):
            observed.pop(key)
        app.collaboration.save('user-watch', observed)
        app.db.commit()
    source['collaborationGeneration'] = {'id':'user-watch-return','inputIds':[observed['subscription']['continuationId']],'terminal':False}
    if old:
        with pytest.raises(AppError, match='no verified original input lineage'):
            await agent(app, source, 'permissions.save', {'scope':'local','allowed':[],'denied':[]}, 'unknown-lineage')
        reviewed = (await agent(app, source, 'capability.request', {'sessionId':source['id'], 'effects':['file_access'],
            'scopes':['local'], 'reason':'Approve the current observed request'}, 'approve-legacy'))['result']
        await app.dispatch('approval.respond', {'sessionId':source['id'], 'id':reviewed['id'], 'decision':'allow'})
    await agent(app, source, 'permissions.save', {'scope':'local','allowed':[],'denied':[]}, 'approved-return')
    assert (await finished(app, 'approved-return'))['phase'] == 'ready'


async def test_older_creation_metadata_still_preserves_lower_authority_ancestry(app):
    source, peer = app.state['sessions']
    peer['collaborationGeneration'] = {'id':'peer-gen','inputIds':['peer-human'],'terminal':False}
    await grant(app, ['file_access'], ['local'])
    incoming = await agent(app, peer, 'coordination.send', {'sessionId':source['id'], 'text':'Change settings','mode':'notify'}, 'lower')
    original = {'commandAction':'coordination.create','requestId':'older-create','senderSessionId':source['id'], 'origin':'agent',
        'result':{'creatorSessionId':source['id'],'requestId':'older-create', 'sourceInputIds':[incoming['requestId']],
                  'sourceGenerationId':'old-generation','sourceInterruptionRevision':0}}
    returned = {'commandAction':'coordination.send','origin':'system','dependencyRequestId':'older-create',
        'senderSessionId':source['id'],'target':{'sessionId':source['id']}}
    for identity, value in [('older-create',original),('older-return',returned)]:
        app.db.execute('INSERT INTO commands VALUES(?,?,?)',(identity,identity,json.dumps(value)))
    source['collaborationGeneration'] = {'id':'returned','inputIds':['older-return'],'terminal':False}
    with pytest.raises(AppError, match='cannot borrow'):
        await agent(app, source, 'permissions.save', {'scope':'local','allowed':[],'denied':[]}, 'creation-borrow')


@pytest.mark.parametrize('ancestry', ['human', 'unprivileged-peer', 'missing', 'empty'])
@pytest.mark.parametrize('origin', ['agent', 'ui'])
async def test_actual_handoff_return_retains_original_configuration_authority(app, ancestry, origin):
    from unittest.mock import AsyncMock
    source, peer = app.state['sessions']
    await grant(app, ['file_access'], ['local'])
    app.runtime.quiesce_for_handoff = AsyncMock(return_value={'quiesced': True, 'inputsReplayed': False})
    app.runtime.collaboration_input = AsyncMock(return_value={'accepted': True})
    source['status'] = 'working'
    if ancestry == 'unprivileged-peer':
        peer['collaborationGeneration'] = {'id':'peer-gen','inputIds':['peer-human'],'terminal':False}
        incoming = await agent(app, peer, 'coordination.send', {'sessionId':source['id'],
            'text':'Change settings', 'mode':'notify'}, 'lower-authority-request')
        source['collaborationGeneration'] = {'id':'peer-woken','inputIds':[incoming['requestId']],'terminal':False}
    original_inputs = source['collaborationGeneration']['inputIds'][:]
    original_generation = source['collaborationGeneration']['id']
    move = {'sessionId':source['id'], 'id':None, 'expectedExecutionRevision':0}
    receipt = ((await agent(app, source, 'worktree.handoff', move, 'move'))['result'] if origin == 'agent'
        else (await app.dispatch('worktree.handoff', move, command_id='move'))['result'])
    await asyncio.gather(*list(app.worktrees.jobs))
    saved = app.worktrees.read(source['id'], receipt['id'])
    original = app.collaboration.receipt(saved['continuation']['requestId'])
    assert original['origin'] == origin
    assert original['sourceInputIds'] == original_inputs
    assert original['sourceGenerationId'] == original_generation
    assert original['sourceInterruptionRevision'] == 0
    assert original['causalDepth'] == saved['causalDepth']
    if ancestry == 'unprivileged-peer':
        assert original['causalDepth'] > incoming['causalDepth']
    returned = app.collaboration.receipt(original['subscription']['continuationId'])
    assert returned['origin'] == 'system' and returned['dependencyRequestId'] == original['requestId']
    if ancestry == 'missing':
        # A retained receipt without trustworthy lineage must not become an
        # empty, human-originated authority merely because its move succeeded.
        for key in ('sourceInputIds', 'sourceGenerationId', 'sourceInterruptionRevision'):
            original.pop(key)
        app.collaboration.save(original['requestId'], original)
        app.db.commit()
    if ancestry == 'empty':
        original['sourceInputIds'] = []
        app.collaboration.save(original['requestId'], original)
        app.db.commit()
    source['collaborationGeneration'] = {'id':'continued','inputIds':[returned['requestId']],'terminal':False}
    values = {'scope':'local','allowed':[],'denied':[]}
    if ancestry != 'human':
        reason = 'cannot borrow' if ancestry == 'unprivileged-peer' else 'no verified original input lineage'
        with pytest.raises(AppError, match=reason):
            await agent(app, source, 'permissions.save', values, 'after-move')
    else:
        await agent(app, source, 'permissions.save', values, 'after-move')
        assert (await finished(app, 'after-move'))['phase'] == 'ready'
        with pytest.raises(AppError, match='Configuration authority is required'):
            await agent(app, source, 'permissions.save', {**values, 'scope':'global'}, 'broader-scope')
        with pytest.raises(AppError, match='Configuration authority is required'):
            await agent(app, source, 'modules.save', {'scope':'local','section':'tools','module':'tool-fixture'}, 'different-effect')
    assert len(app.configuration_authority.rows(source['id'])) == 1
