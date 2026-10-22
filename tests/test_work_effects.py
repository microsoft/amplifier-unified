"""Real service/receipt checks with explicitly synthetic authenticated-worker facts.

These tests cannot qualify native delivery, witness consumption or actual tool
execution. The adapter integration must supply its independently observed facts.
"""
import hashlib
import json
import asyncio

import pytest

from test_collaborative_workspaces import app, generation
from test_work_invocation import prepared_work, assert_available_commission, current_work, transport, retained_bytes
from test_workspace_work import read, revise, source


def tool_args():
    return {'toolName': 'write_file', 'toolInput': {'file_path': 'output.txt', 'content': 'exact'},
            'toolCallId': 'actual-dispatch-call'}


def trusted_fixture(child, required, args):
    """Test transport fixture, NOT a fake retained native/provider receipt."""
    digest = hashlib.sha256(json.dumps([args['toolName'], args['toolInput']],
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    origin = {'schemaVersion': 1, 'rootSessionId': child['id'], 'runtimeSessionId': child['id'],
              'generationId': required['generationId'], 'inputIds': required['inputIds'],
              'inputCoverage': 'complete', 'stopRevision': child.get('interruptionRevision', 0),
              'admittedCallId': 'llm:fixture-admission', 'attemptId': 'fixture-attempt',
              'fittedSha256': 'a' * 64}
    return {'status': 'available', 'complete': True, 'effectAuthorized': False,
            'version': required['version'], 'rootSessionId': child['id'],
            'generationId': required['generationId'], 'inputIds': required['inputIds'],
            'requirementCount': required['requirementCount'], 'coveredCount': required['requirementCount'],
            'attemptId': origin['attemptId'], 'requestSha256': origin['fittedSha256'],
            'origin': origin, 'toolCallId': args['toolCallId'], 'toolName': args['toolName'],
            'argsSha256': digest}


async def check(app, child, args, witness=None):
    binding = transport(child)
    if witness is not None:
        binding['_workOrigin'] = witness
    return await app.app_bridge('work.effect.check', {**args, **binding}, child['id'])


async def test_private_work_check_is_not_an_effect_or_ordinary_permission(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'effect-precondition')
    _, child = await assert_available_commission(app, owner, item, 'effect-commission')
    required = (await current_work(app, child))['required']
    args = tool_args(); before = retained_bytes(app); inputs = list(app.runtime.inputs)
    denied = await check(app, child, args)
    assert denied['applicable'] and not denied['allowed'] and denied['effectAuthorized'] is False
    witness = trusted_fixture(child, required, args)
    result = await check(app, child, args, witness)
    assert result['applicable'] and result['allowed'] and result['effectAuthorized'] is False
    assert result['requiredVersion'] == required['version']
    assert retained_bytes(app) == before and app.runtime.inputs == inputs


@pytest.mark.parametrize('fault', [
    'toolCallId', 'toolName', 'arguments', 'version', 'coverage', 'attempt',
    'generation', 'inputs', 'stop', 'admission', 'request'])
async def test_work_precondition_rejects_mismatched_origin(app, fault):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'effect-negative')
    _, child = await assert_available_commission(app, owner, item, 'effect-negative-commission')
    required = (await current_work(app, child))['required']; args = tool_args()
    witness = trusted_fixture(child, required, args)
    if fault in {'toolCallId', 'toolName', 'version'}: witness[fault] = 'different'
    elif fault == 'arguments': args['toolInput']['content'] = 'altered after originating request'
    elif fault == 'coverage': witness['coveredCount'] -= 1
    elif fault == 'attempt': witness['origin']['attemptId'] = 'other-attempt'
    elif fault == 'generation': witness['origin']['generationId'] = 'old-generation'
    elif fault == 'inputs': witness['origin']['inputIds'] = []
    elif fault == 'stop':
        await app.dispatch('conversation.stop', {'sessionId': child['id']}, command_id='effect-host-stop')
    elif fault == 'admission': witness['origin']['admittedCallId'] = None
    elif fault == 'request': witness['requestSha256'] = 'b' * 64
    before = retained_bytes(app)
    result = await check(app, child, args, witness)
    assert result['applicable'] and not result['allowed'] and result['effectAuthorized'] is False
    assert retained_bytes(app) == before


async def test_missing_work_association_cannot_become_an_ordinary_tool_exemption(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'missing-effect-association')
    created, child = await assert_available_commission(app, owner, item, 'missing-effect-commission')
    # Deliberate corruption, not a manufactured positive receipt.
    app.db.execute('DELETE FROM commands WHERE id=?', (created['initialInputId'],)); app.db.commit()
    result = await check(app, child, tool_args())
    assert result['applicable'] and not result['allowed'] and result['effectAuthorized'] is False
    recovery = {'toolName': 'app_control', 'toolInput': {'operation': 'dispatch', 'args': {
        'action': 'workspace.work.invocation', 'args': {'sessionId': child['id']}}}, 'toolCallId': 'recovery'}
    result = await check(app, child, recovery)
    assert result['allowed'] and result['effectAuthorized'] is False


async def test_local_worker_stop_counter_is_not_the_persistent_host_counter(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'local-stop-counter')
    _, child = await assert_available_commission(app, owner, item, 'local-stop-commission')
    required = (await current_work(app, child))['required']; args = tool_args()
    witness = trusted_fixture(child, required, args)
    witness['origin']['stopRevision'] = 3
    assert child.get('interruptionRevision', 0) == 0
    result = await check(app, child, args, witness)
    assert result['allowed'] and result['effectAuthorized'] is False
    await app.dispatch('conversation.stop', {'sessionId': child['id']}, command_id='local-stop-host-stop')
    result = await check(app, child, args, witness)
    assert not result['allowed'] and result['effectAuthorized'] is False


async def test_unrelated_root_preserves_ordinary_policy_and_model_witness_is_rejected(app):
    owner = app.state['sessions'][0]
    await generation(app, owner, 'ordinary-generation', ['ordinary-human-input'])
    result = await check(app, owner, tool_args())
    assert result['applicable'] is False and result['allowed'] and result['effectAuthorized'] is False
    from amplifier_web.service import AppError
    with pytest.raises(AppError, match='exact tool name'):
        await check(app, owner, {**tool_args(), 'witness': {'complete': True}})


async def test_safe_reporting_does_not_exempt_nested_effects(app):
    from amplifier_web.workspace_work_effects import recovery_read
    report = {'operation': 'dispatch', 'args': {'action': 'workspace.work.report', 'args': {}}}
    assert recovery_read('app_control', report)
    assert not recovery_read('app_control', {'operation': 'dispatch', 'args': {'action': 'workspace.work.revise'}})
    assert not recovery_read('app_control', {'operation': 'dispatch', 'args': {'action': 'runtime.control', 'args': {'operation': 'tool.invoke'}}})
    assert not recovery_read('bash', {'command': 'cat work.txt'})
    assert not recovery_read('tool_exec', {'code': 'await tools.write_file({})'})


@pytest.mark.parametrize('guarded,tool,allowed', [
    (True, 'tool_exec', True), (False, 'tool_exec', False),
    ('true', 'tool_exec', False), (True, 'write_file', False)])
async def test_programmatic_container_requires_exact_private_host_fact(app, guarded, tool, allowed):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'container-boundary')
    _, child = await assert_available_commission(app, owner, item, 'container-commission')
    args = {'toolName': tool, 'toolInput': {'code': 'await tools.read_file({})'}, 'toolCallId': 'container-call'}
    result = await app.app_bridge('work.effect.check', {
        **args, **transport(child), '_workDispatchGuarded': guarded}, child['id'])
    assert result['allowed'] is allowed and result['effectAuthorized'] is False
    from amplifier_web.service import AppError
    with pytest.raises(AppError, match='exact tool name'):
        await check(app, child, {**args, 'workDispatchGuarded': True})


@pytest.mark.parametrize('arguments', ['workspace.work.list', ['workspace.work.list']])
async def test_malformed_nested_tool_arguments_return_unavailable(app, arguments):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'malformed-effect')
    _, child = await assert_available_commission(app, owner, item, 'malformed-effect-commission')
    result = await check(app, child, {'toolName': 'app_control', 'toolInput': {
        'operation': 'dispatch', 'args': arguments}, 'toolCallId': 'malformed'})
    assert not result['allowed'] and result['effectAuthorized'] is False


async def test_same_witness_is_invalid_after_real_promise_change(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'promise-effect')
    _, child = await assert_available_commission(app, owner, item, 'promise-effect-commission')
    required = (await current_work(app, child))['required']; args = tool_args()
    witness = trusted_fixture(child, required, args)
    assert (await check(app, child, args, witness))['allowed']
    current = (await read(app, owner, item['id']))['item']
    await revise(app, owner, current, 'promise-effect-revision',
                 source=source(app, owner, 'New condition before effect.'),
                 constraints=['New condition before effect.'])
    before = retained_bytes(app)
    result = await check(app, child, args, witness)
    assert not result['allowed'] and result['effectAuthorized'] is False
    assert retained_bytes(app) == before


async def app_effect(app, child, action, args, command, witness=None):
    payload = {'action': action, 'args': args, 'id': command}
    envelope = transport(child)
    if witness is not None:
        envelope['_workOrigin'] = witness
    return await app.app_bridge('dispatch', {**payload, **envelope}, child['id'])


def app_witness(child, required, action, args, command):
    return trusted_fixture(child, required, {
        'toolName': 'app_control', 'toolInput': {'operation': 'dispatch', 'args': {
            'action': action, 'args': args, 'id': command}}, 'toolCallId': command + ':call'})


async def test_app_effect_requires_coverage_but_safe_reads_stay_available(app):
    from amplifier_web.service import AppError
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'app-effect')
    _, child = await assert_available_commission(app, owner, item, 'app-effect-commission')
    args = {'id': child['id'], 'pinned': True}
    before = retained_bytes(app)
    with pytest.raises(AppError, match='originating-call coverage'):
        await app_effect(app, child, 'session.pin', args, 'pin-uncovered')
    assert retained_bytes(app) == before
    read_result = await app_effect(app, child, 'workspace.work.invocation',
                                   {'sessionId': child['id']}, 'read-uncovered')
    assert read_result['accepted']
    required = (await current_work(app, child))['required']
    witness = app_witness(child, required, 'session.pin', args, 'pin-covered')
    result = await app_effect(app, child, 'session.pin', args, 'pin-covered', witness)
    assert result['accepted'] and child['id'] in app._state['pinnedSessionIds']


@pytest.mark.parametrize('change', [False, True])
async def test_peer_notify_rechecks_after_real_history_await(app, monkeypatch, change):
    from amplifier_web.service import AppError
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'await-effect')
    _, child = await assert_available_commission(app, owner, item, 'await-effect-commission')
    required = (await current_work(app, child))['required']
    args = {'sessionId': owner['id'], 'text': 'Retained notification.', 'mode': 'notify'}
    command = 'await-notification'
    witness = app_witness(child, required, 'conversation.send', args, command)
    reached, release = asyncio.Event(), asyncio.Event()
    original = app.history.ensure_loaded

    async def held(sid, *values, **options):
        if sid == owner['id']:
            reached.set()
            await release.wait()
        return await original(sid, *values, **options)

    monkeypatch.setattr(app.history, 'ensure_loaded', held)
    pending = asyncio.create_task(app_effect(app, child, 'conversation.send', args, command, witness))
    try:
        await asyncio.wait_for(reached.wait(), 3)
        monkeypatch.setattr(app.history, 'ensure_loaded', original)
        if change:
            current = (await read(app, owner, item['id']))['item']
            await revise(app, owner, current, 'await-effect-revision',
                         source=source(app, owner, 'New promise while notification waits.'),
                         constraints=['New promise before notification.'])
        before = retained_bytes(app)
    finally:
        release.set()
    if change:
        with pytest.raises(AppError, match='Work|coverage'):
            await pending
        assert retained_bytes(app) == before
        assert command not in retained_bytes(app)
    else:
        result = await pending
        assert result['accepted'] and result['delivery'] == 'notified'
        assert command in retained_bytes(app)


async def test_unqualified_deferred_effect_owner_refuses_before_admission(app):
    from amplifier_web.service import AppError
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'deferred-effect')
    _, child = await assert_available_commission(app, owner, item, 'deferred-effect-commission')
    required = (await current_work(app, child))['required']
    args = {'sessionId': owner['id'], 'text': 'Do not enqueue.', 'mode': 'queue'}
    witness = app_witness(child, required, 'coordination.send', args, 'deferred')
    before = retained_bytes(app)
    with pytest.raises(AppError, match='no qualified final effect owner'):
        await app_effect(app, child, 'coordination.send', args, 'deferred', witness)
    assert retained_bytes(app) == before
