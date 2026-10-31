"""Standing Full waiting policy through the real dispatcher and native mounts."""
import asyncio
import copy
import json

import pytest

from amplifier_web import model_capacity_settings as capacity
from amplifier_web.collaboration import BINDING, PRINCIPAL
from amplifier_web.errors import AppError
from test_full_child_configuration import app, mounted, dispatch as native_dispatch
from test_full_feedback import call, mode


def values(revision=0):
    return {'expectedRevision': revision, 'maxWaitingCalls': 0, 'waitTimeoutSeconds': 0}


def saved(app, identity):
    row = app.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    return json.loads(row[0]) if row else None


@pytest.mark.parametrize('kind', ['root', 'finite', 'nested', 'resumed', 'persistent'])
async def test_actual_native_actor_sets_without_browser_or_approval(mounted, kind):
    from amplifier_web.host.children import _PERSISTENT

    h = mounted
    await mode(h.app, h.source)
    assigned = ['assigned-' + str(i) for i in range(17)]
    h.worker.runtime.generation['input_ids'] = list(assigned)
    h.source['collaborationGeneration']['inputIds'] = list(assigned)
    h.worker.context_inputs = list(assigned)
    h.worker.context_bindings = {identity: {'clientId': None, 'targets': []} for identity in assigned}
    messages, runtime_calls = copy.deepcopy(h.source['messages']), list(h.app.runtime.calls)
    actors = []

    async def exercise(control, actor):
        before = await native_dispatch(control, 'modelCapacity.read', {}, 'read')
        assert before.success, before.error
        args = values(before.output['result']['revision'])
        identity = 'save-' + str(len(actors))
        result = await native_dispatch(control, 'modelCapacity.set', args, identity)
        assert result.success, result.error
        assert result.output['result'] == {'revision': args['expectedRevision'] + 1,
                                          'maxWaitingCalls': 0, 'waitTimeoutSeconds': 0}
        authority = saved(h.app, identity)['_authority']
        assert authority['origin'] == 'agent' and authority['sessionId'] == h.source['id']
        assert authority['runtimeSessionId'] == actor
        assert authority['generationId'] == 'generation-1'
        assert authority['inputIds'] == assigned and authority['inputCoverage'] == 'complete'
        assert '_authority' not in result.output
        repeated = await native_dispatch(control, 'modelCapacity.set', args, identity)
        assert repeated.success and repeated.output['duplicate']
        actors.append(actor)

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        await exercise(tools['app_control'], coordinator.session_id)
        if kind == 'nested' and coordinator.session_id == 'child':
            await h.registry.spawn('worker', 'configure waiting', h.registry.sessions['child'], sub_session_id='grandchild')
        return 'configured'

    h.install_execute(execute)
    token = _PERSISTENT.set(kind == 'persistent')
    try:
        if kind == 'root':
            await exercise(h.parent.coordinator.get('tools')['app_control'], 'native-root')
        else:
            await h.registry.spawn('worker', 'configure waiting', h.parent, sub_session_id='child')
            if kind == 'resumed':
                await h.registry.resume('child', 'continue configuring', h.parent)
    finally:
        _PERSISTENT.reset(token)
    assert actors == (['native-root'] if kind == 'root' else ['child', 'grandchild'] if kind == 'nested'
                      else ['child', 'child'] if kind == 'resumed' else ['child'])
    assert not h.app.clients.records and not h.source['approvals']
    assert h.source['messages'] == messages and h.app.runtime.calls == runtime_calls
    h.approval_prompt.assert_not_awaited()


async def test_later_child_observes_original_actor_receipt(mounted, monkeypatch):
    h = mounted
    await mode(h.app, h.source)
    actors = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        result = await native_dispatch(tools['app_control'], 'modelCapacity.set', values(), 'shared-save')
        assert result.success, result.error
        actors.append(coordinator.session_id)
        if len(actors) == 2:
            assert result.output['duplicate']
        return 'configured'

    h.install_execute(execute)
    await h.registry.spawn('worker', 'configure waiting', h.parent, sub_session_id='first-child')
    original = saved(h.app, 'shared-save')
    monkeypatch.setattr(h.app.access_mode, 'capture_full', lambda *a: pytest.fail('Receipt renewed authority'))
    await h.registry.spawn('worker', 'observe settings', h.parent, sub_session_id='later-child')
    assert actors == ['first-child', 'later-child']
    assert saved(h.app, 'shared-save') == original
    assert original['_authority']['runtimeSessionId'] == 'first-child'
    assert capacity.policy(h.app)['revision'] == 1


async def test_system_save_and_exact_receipt_survive_completion_revocation_and_restart(app, monkeypatch):
    from amplifier_web.service import AppService

    source, peer = app.state['sessions']
    await mode(app, source)
    calls = list(app.runtime.calls)
    first = await call(app, source, 'modelCapacity.set', values(), origin='system', command='system-save')
    original = saved(app, 'system-save')
    assert original['_authority']['origin'] == 'system'
    assert original['_authority']['sessionId'] == source['id']
    assert not {'runtimeSessionId', 'generationId', 'inputIds'} & original['_authority'].keys()
    assert app.runtime.calls == calls and '_authority' not in first
    source['collaborationGeneration']['terminal'] = True
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    await mode(app, source, 'ask')
    observed = await call(app, source, 'modelCapacity.set', values(), origin='agent', command='system-save')
    assert observed['duplicate'] and saved(app, 'system-save') == original
    workspace = source['workspace']
    await app.close()
    other = AppService(app.data_dir, workspace=workspace)
    try:
        monkeypatch.setattr(other.access_mode, 'capture_full', lambda *a: pytest.fail('Receipt recaptured authority'))
        monkeypatch.setattr(other.history, 'ensure_loaded', lambda *a: pytest.fail('Receipt hydrated history'))
        changes = other.db.total_changes
        repeated = await call(other, source, 'modelCapacity.set', values(), origin='system', command='system-save')
        assert repeated['duplicate'] and repeated['result'] == first['result']
        assert saved(other, 'system-save') == original
        with pytest.raises(AppError, match='different calling conversation'):
            await call(other, peer, 'modelCapacity.set', values(), origin='system', command='system-save')
        with pytest.raises(AppError, match='different contents'):
            await call(other, source, 'modelCapacity.set', {**values(), 'maxWaitingCalls': 10},
                       origin='system', command='system-save')
        assert (await other.dispatch('modelCapacity.read', {}, origin='agent'))['result'] == first['result']
        assert other.db.total_changes == changes
    finally:
        await other.close()


@pytest.mark.parametrize('boundary', ['stop', 'assignment-stop', 'revoke', 'off-on', 'placement', 'native', 'execution', 'terminal'])
@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_authority_is_captured_before_lock_and_checked_at_commit(app, monkeypatch, boundary, origin):
    source = app.state['sessions'][0]
    await mode(app, source)
    if boundary == 'assignment-stop':
        assignment = (await app.dispatch('assignment.start', {'sessionId': source['id']}))['result']['assignment']
    entered, release = asyncio.Event(), asyncio.Event()
    original_lock = app.lock

    class HeldLock:
        async def __aenter__(self):
            if asyncio.current_task() is task:
                entered.set()
                await release.wait()
            return await original_lock.__aenter__()

        async def __aexit__(self, *args):
            return await original_lock.__aexit__(*args)

        def locked(self):
            return original_lock.locked()

    monkeypatch.setattr(app, 'lock', HeldLock())
    task = asyncio.create_task(call(app, source, 'modelCapacity.set', values(), origin=origin, command='held-save'))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if boundary == 'stop':
            await app.dispatch('conversation.stop', {'sessionId': source['id']})
        elif boundary == 'assignment-stop':
            await app.dispatch('assignment.stop', {'sessionId': source['id'], 'expectedRevision': assignment['revision']})
        elif boundary in {'revoke', 'off-on'}:
            await mode(app, source, 'ask')
            if boundary == 'off-on':
                await mode(app, source)
                assert app.access_mode.full(source)
        elif boundary == 'placement':
            source['workspace'] += '/moved'
        elif boundary == 'native':
            source['runtimeSessionId'] = 'replacement-native'
        elif boundary == 'execution':
            source['executionRevision'] = source.get('executionRevision', 0) + 1
        else:
            source['collaborationGeneration']['terminal'] = True
    finally:
        release.set()
    if boundary == 'terminal':
        assert (await task)['accepted'] and capacity.policy(app)['revision'] == 1
    else:
        with pytest.raises(AppError, match='authority changed|stopped'):
            await task
        assert capacity.policy(app)['revision'] == 0 and saved(app, 'held-save') is None


@pytest.mark.parametrize('envelope', ['fresh-system', 'current', 'stale', 'stopped', 'foreign-input'])
async def test_system_origin_keeps_or_validates_actual_native_envelope(app, envelope):
    source = app.state['sessions'][0]
    await mode(app, source)
    binding = {'_runtimeSessionId': source['id'], '_generationId': 'generation-1',
               '_inputBindings': [{'inputId': 'user-input'}]}
    if envelope == 'stale':
        binding['_generationId'] = 'old-generation'
    elif envelope == 'stopped':
        await app.dispatch('conversation.stop', {'sessionId': source['id']})
    elif envelope == 'foreign-input':
        binding['_inputBindings'] = [{'inputId': 'not-delivered'}]
    native = envelope != 'fresh-system'
    principal_token = PRINCIPAL.set(source['id'] if native else None)
    binding_token = BINDING.set(binding if native else None)
    try:
        if envelope in {'stale', 'stopped', 'foreign-input'}:
            with pytest.raises(AppError, match='current.*generation|Stop|current admitted input'):
                await call(app, source, 'modelCapacity.set', values(), origin='system', command='system-envelope')
            assert capacity.policy(app)['revision'] == 0
            return
        await call(app, source, 'modelCapacity.set', values(), origin='system', command='system-envelope')
    finally:
        PRINCIPAL.reset(principal_token)
        BINDING.reset(binding_token)
    authority = saved(app, 'system-envelope')['_authority']
    assert authority['origin'] == 'system' and authority['sessionId'] == source['id']
    if native:
        assert authority['runtimeSessionId'] == source['id']
        assert authority['generationId'] == 'generation-1' and authority['inputIds'] == ['user-input']
    else:
        assert not {'runtimeSessionId', 'generationId', 'inputIds'} & authority.keys()


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_ask_refuses_mutation_and_read_remains_passive(app, monkeypatch, origin):
    source = app.state['sessions'][0]
    with pytest.raises(AppError, match='Only the user'):
        await call(app, source, 'modelCapacity.set', values(), origin=origin)
    monkeypatch.setattr(app.access_mode, 'capture_full', lambda *a: pytest.fail('Read captured authority'))
    monkeypatch.setattr(app.history, 'ensure_loaded', lambda *a: pytest.fail('Read hydrated history'))
    assert (await call(app, source, 'modelCapacity.read', {}, origin=origin))['result'] == capacity.DEFAULTS


async def test_full_selected_chat_is_not_an_implicit_caller(app):
    await mode(app, app.state['sessions'][0])
    with pytest.raises(AppError, match='calling conversation'):
        await app.dispatch('modelCapacity.set', values(), origin='system')
    assert capacity.policy(app)['revision'] == 0
