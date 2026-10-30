"""Standing Full authority is separate from Work assurance and effect truth.

Real commissioning receipts and app owners; explicit service transport fixtures
plus the actual worker's assigned-child producer. Native fitted/compacted and
nested dispatch coverage lives in test_work_native_effects.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from amplifier_web.collaboration import BINDING, PRINCIPAL
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppError
from test_access_mode import save
from test_collaborative_workspaces import app, agent_action, generation, settled
from test_runtime_worker import coordinator
from test_work_effects import app_effect, check, tool_args
from test_work_invocation import prepared_work, assert_available_commission, current_work, retained_bytes, transport
from test_workspace_work import create, read, revise, source, workspace_id


async def commissioned_full(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'full-work')
    created, target = await assert_available_commission(app, owner, item, 'full-work-commission')
    await save(app, target, scope='app', identity='operator-full')
    return owner, item, created, target


@pytest.mark.parametrize('witness', [None, {'complete': False, 'status': 'unavailable',
                                         'reason': 'Opaque compacted context.'}])
async def test_full_commissioned_work_reaches_ordinary_owners_without_coverage(app, witness, monkeypatch):
    _, _, created, target = await commissioned_full(app)
    # The commissioned generation is driven by a retained peer request, with
    # neither a fresh human input nor a browser permission decision.
    assert target['collaborationGeneration']['inputIds'] == [created['initialInputId']]
    assert not any(m.get('inputOrigin') == 'ui' for m in target['messages'])
    before = retained_bytes(app)
    def no_requirement_scan(*args, **kwargs):
        raise AssertionError('Full effect admission must not traverse Work source coverage')
    monkeypatch.setattr(app.work_invocation, 'required', no_requirement_scan)
    result = await check(app, target, tool_args(), witness)
    assert result['allowed'] and result['applicable']
    assert result['requiredVersion'] is None and result['effectAuthorized'] is False
    assert retained_bytes(app) == before
    # Rename is deliberately outside the old Work final-owner allowlist.
    response = await app_effect(app, target, 'session.rename',
        {'id': target['id'], 'title': 'Authorized Work'}, 'full-rename', witness)
    assert response['accepted'] and target['title'] == 'Authorized Work'


async def test_full_work_readback_reaches_publisher_without_effect_or_provider(app):
    owner, _, _, target = await commissioned_full(app)
    inputs, receipts = deepcopy(app.runtime.inputs), retained_bytes(app)
    result = await app_effect(app, target, 'publishing.list',
        {'sessionId': target['id']}, 'full-publishing-read')
    assert result['accepted'] and result['result']['releases'] == []
    assert app.runtime.inputs == inputs and retained_bytes(app) == receipts
    assert not app.publishing.store.receipts(target['id'])
    with pytest.raises(AppError, match='calling conversation'):
        await app_effect(app, target, 'publishing.list',
            {'sessionId': owner['id']}, 'full-wrong-publisher-owner')
    assert app.runtime.inputs == inputs and retained_bytes(app) == receipts


@pytest.mark.parametrize('change', ['metadata', 'promise', 'followup'])
async def test_full_work_iteration_keeps_incomplete_assurance_honest(app, change):
    owner, item, created, target = await commissioned_full(app)
    original = (await current_work(app, target))['required']
    if change in {'metadata', 'promise'}:
        current = (await read(app, owner, item['id']))['item']
        patch = {'nextAction': 'Continue the next step'} if change == 'metadata' else {
            'source': source(app, owner, 'Also preserve the corrected requirement.'),
            'constraints': ['Preserve the corrected requirement.']}
        await revise(app, owner, current, 'full-iteration-revision', **patch)
    else:
        identity = 'full-ordinary-followup'
        await agent_action(app, owner, 'coordination.send', {
            'sessionId': target['id'], 'text': 'Continue with this feedback.', 'mode': 'queue'}, identity)
        await app.on_runtime_event('runtime.generation', {
            'sessionId': target['id'], 'rootSessionId': target['id'],
            'generation_id': target['collaborationGeneration']['id'], 'event': 'generation.finished',
            'input_ids': [created['initialInputId']], 'disposition': 'manager_turn_finished', 'active_job_ids': []})
        await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'idle'})
        await settled(app, identity, 'accepted')
        await generation(app, target, 'full-followup-generation', [created['initialInputId'], identity])
    current = (await current_work(app, target))['required']
    assert current['version'] != original['version']
    if change != 'metadata':
        assert current['status'] == 'unavailable' and current['coverage']['complete'] is False
    decision = await check(app, target, tool_args())
    assert decision['allowed'] and decision['requiredVersion'] is None
    assert decision['effectAuthorized'] is False
    result = await app_effect(app, target, 'session.pin', {'id': target['id'], 'pinned': True}, 'full-iteration-pin')
    assert result['accepted']
    assert (await current_work(app, target))['required'] == current


@pytest.mark.parametrize('change', ['ask', 'revoked', 'stop', 'stale_generation', 'missing_actor'])
async def test_full_does_not_erase_current_mode_or_native_execution_boundary(app, change):
    _, _, _, target = await commissioned_full(app)
    binding = transport(target)
    if change == 'ask':
        await save(app, target, mode='ask', identity='narrow-ask')
    elif change == 'revoked':
        await save(app, target, scope='app', mode='ask', identity='revoke-app-full')
    elif change == 'stop':
        await app.dispatch('conversation.stop', {'sessionId': target['id']}, command_id='full-stop')
    elif change == 'stale_generation':
        binding['_generationId'] = 'expired-generation'
    else:
        binding.pop('_runtimeSessionId')
    before = retained_bytes(app)
    result = await app.app_bridge('work.effect.check', {**tool_args(), **binding}, target['id'])
    assert not result['allowed'] and result['effectAuthorized'] is False
    with pytest.raises(AppError, match='Work precondition'):
        await app.app_bridge('dispatch', {'action': 'session.pin',
            'args': {'id': target['id'], 'pinned': True}, 'id': 'rejected-full-pin', **binding}, target['id'])
    assert retained_bytes(app) == before and target['id'] not in app._state['pinnedSessionIds']


@pytest.mark.parametrize('advance', ['none', 'generation', 'stop'])
async def test_actual_child_bridge_preserves_actor_and_assigned_work_authority(app, advance, monkeypatch):
    _, _, created, target = await commissioned_full(app)
    root, child = coordinator(target['id']), coordinator('actual-native-child')
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=root)
    worker.runtime = SimpleNamespace(generation={'id': target['collaborationGeneration']['id'],
        'input_ids': list(target['collaborationGeneration']['inputIds'])}, closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.context_bindings = {}
    seen = []
    async def host(operation, arguments):
        seen.append(deepcopy(arguments))
        return await app.app_bridge(operation, arguments, target['id'])
    worker.bridge = host
    bridge = worker.app_access_bridge(child)
    actual_binding = app.collaboration.active_binding
    actors = []
    def observed_binding(owner, **options):
        actors.append((PRINCIPAL.get(), (BINDING.get() or {}).get('_runtimeSessionId')))
        return actual_binding(owner, **options)
    monkeypatch.setattr(app.collaboration, 'active_binding', observed_binding)
    if advance == 'generation':
        # A later generation still processing the commissioned input cannot be
        # borrowed by a child whose installer captured the previous generation.
        worker.runtime.generation = {'id': 'new-root-work', 'input_ids': [created['initialInputId']]}
        await generation(app, target, 'new-root-work', [created['initialInputId']])
    elif advance == 'stop':
        worker.stop_revision += 1
        await app.dispatch('conversation.stop', {'sessionId': target['id']}, command_id='child-full-stop')
    result = await bridge('work.effect.check', {**tool_args(), '_generationId': 'forged',
        '_runtimeSessionId': target['id'], '_workOrigin': {'complete': True}})
    assert seen[-1]['_runtimeSessionId'] == 'actual-native-child'
    assert '_workOrigin' not in seen[-1]
    if advance == 'none':
        assert result['allowed'] and result['effectAuthorized'] is False
        assert actors == [('actual-native-child', 'actual-native-child')]
        pinned = await bridge('dispatch', {'action': 'session.pin',
            'args': {'id': target['id'], 'pinned': True}, 'id': 'child-full-pin'})
        assert pinned['accepted'] and target['id'] in app._state['pinnedSessionIds']
    else:
        assert '_generationId' not in seen[-1]
        assert not result['allowed'] and target['id'] not in app._state['pinnedSessionIds']


async def test_full_work_preserves_domain_revision_provenance_and_exact_retry(app):
    _, _, _, target = await commissioned_full(app)
    item = (await create(app, target, command='own-work'))['result']['item']
    await revise(app, target, item, 'own-work-next', nextAction='Next step')
    with pytest.raises(AppError, match='revision'):
        await revise(app, target, item, 'own-work-stale', nextAction='Stale overwrite')
    with pytest.raises(AppError):
        await app_effect(app, target, 'workspace.work.decision', {
            'workspaceId': workspace_id(app, target), 'sessionId': target['id'],
            'id': item['id'], 'expectedRevision': item['revision'] + 1,
            'promiseRevision': item['promiseRevision'], 'candidate': 'a' * 40,
            'decision': 'accepted'}, 'forged-human-decision')
    args = {'id': target['id'], 'title': 'Retained exact result'}
    first = await app_effect(app, target, 'session.rename', args, 'full-exact-command')
    before = retained_bytes(app)
    repeated = await app_effect(app, target, 'session.rename', args, 'full-exact-command')
    assert first['accepted'] and repeated['duplicate'] and retained_bytes(app) == before
    with pytest.raises(AppError, match='different contents'):
        await app_effect(app, target, 'session.rename', {**args, 'title': 'Changed intent'}, 'full-exact-command')
    assert retained_bytes(app) == before and target['title'] == args['title']
