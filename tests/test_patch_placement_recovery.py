"""Proposed narrow placement API: test-first, NOT implemented at this base.

All data is synthetic. AppService/Management/authority/command ledger are real;
subprocess boot/lease is a fixture, but Worker controls, Core mounts, both file
engines and FileSourceGuard are real. No provider or personal settings are used.
An absent action is an explicit failing prerequisite, never a negative-test pass.
"""
import asyncio
import copy
import json
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

import pytest

from amplifier_web.coordination_guidance import ROOT_SCOPE_MESSAGE
from amplifier_web.management import Management
from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_controls import override_path
from amplifier_web.service import ACTION_DEFINITIONS, AppError, AppService
from test_configuration_authority import finished, grant, revoke
from test_coordination_generation import agent

READ = 'configuration.patchPlacement.read'
BIND = 'configuration.patchPlacement.bind'
MODULE = 'patch-fixture'
SECRET = 'synthetic-secret-must-never-enter-placement-projection'
PEER_REFUSAL = ('A user must explicitly perform this peer mutation; collaboration '
                'does not grant stop, worker or settings authority.')
CHILD_REFUSAL = ROOT_SCOPE_MESSAGE


def require_contract():
    missing = {READ, BIND} - ACTION_DEFINITIONS.keys()
    assert not missing, f'Placement recovery contract is not implemented: {sorted(missing)}'


def fixture_plan(home, checkout, engine):
    return {
        'session': {'orchestrator': {'module': 'loop-live'},
                    'context': {'module': 'context-simple'}},
        'providers': [{'module': 'provider-fixture', 'config': {'api_key': SECRET}}],
        'hooks': [{'module': 'hooks-fixture', 'config': {'private': SECRET}}],
        'tools': [
            {'module': 'tool-filesystem', 'config': {
                'allowed_read_paths': [str(home), str(checkout)],
                'allowed_write_paths': [str(checkout)],
                'denied_write_paths': [str(checkout / 'denied')]}},
            {'module': 'tool-apply-patch', 'id': MODULE,
             'source': 'fixture-pinned-patch-source', 'config': {
                 'engine': engine, 'working_dir': str(home),
                 'allowed_write_paths': [str(checkout)],
                 'denied_write_paths': [str(checkout / 'denied')]}},
            {'module': 'tool-fixture', 'config': {'key': SECRET, 'unchanged': [1, 2]}},
        ],
        'agents': {'child': {'tools': [{'module': 'tool-apply-patch', 'config': {
            'working_dir': str(home), 'allowed_write_paths': [],
            'denied_write_paths': [str(home), str(checkout)]}}]}},
        'spawn': {'exclude_tools': ['tool-fixture']},
        'instruction': 'Synthetic retained instruction',
    }


@pytest.fixture
async def placement(tmp_path, monkeypatch):
    home, checkout, app_home = (tmp_path / name for name in ('history', 'checkout', 'app'))
    for directory in (home, checkout):
        directory.mkdir()
        (directory / 'file.txt').write_text('old\n')
    (checkout / 'denied').mkdir()
    (checkout / 'denied/file.txt').write_text('old\n')
    trace, fault = tmp_path / 'trace.jsonl', tmp_path / 'fault.txt'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app_home))
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.setenv('PATCH_PLACEMENT_TRACE', str(trace))
    monkeypatch.setenv('PATCH_PLACEMENT_FAULT', str(fault))
    worker = Path(__file__).parent / 'fixtures/patch_placement_worker.py'
    runtime = RuntimeManager(command=[sys.executable, '-u', str(worker)],
        startup_timeout=10, retention={'prewarm_on_select': False})
    app = AppService(app_home, runtime, workspace=home)
    owner, peer = [app._new_session({'title': title}) for title in ('Owner', 'Peer')]
    owner.update(workingDirectory=str(checkout), executionRevision=1,
                 runtimeSessionId='synthetic-placement-native')
    owner['collaborationGeneration'] = {
        'id': 'placement-generation', 'inputIds': ['operator-choice'], 'terminal': False}
    owner['messages'].append({'id': 'operator-choice', 'role': 'user', 'inputOrigin': 'ui', 'text':
        'Bind this root patch instance to its recorded execution folder, retaining all other settings.',
        'createdAt': time.time()})
    peer.update(workingDirectory=str(checkout), executionRevision=1,
                runtimeSessionId='synthetic-placement-peer')
    peer['collaborationGeneration'] = {
        'id': 'peer-generation', 'inputIds': ['peer-choice'], 'terminal': False}
    peer['messages'].append({'id': 'peer-choice', 'role': 'user', 'inputOrigin': 'ui', 'text':
        'Bind this peer root patch instance to its recorded execution folder.',
        'createdAt': time.time()})
    app.state['sessions'] = [owner, peer]
    app.state['selectedSessionId'] = owner['id']
    app._publish()
    app.management = Management(app)
    runtime.app_bridge = app.app_bridge
    native = owner['runtimeSessionId']
    path = override_path(native)
    path.parent.mkdir(parents=True, exist_ok=True)
    initial = fixture_plan(home, checkout, 'native')
    path.write_text(json.dumps(initial, indent=2) + '\n')
    peer_path = override_path(peer['runtimeSessionId'])
    peer_path.parent.mkdir(parents=True, exist_ok=True)
    peer_path.write_text(json.dumps(initial, indent=2) + '\n')
    controls = path.with_name('control-state.json')
    controls.write_text(json.dumps({'mode': 'fixture', 'configurator': {
        'disabled': {'tools': ['tool-fixture']}}, 'selection': {'instance': 'fixture'}}))
    canonical = home / 'retained-history.txt'
    canonical.write_bytes(b'synthetic history/settings boundary\n')
    b = SimpleNamespace(app=app, owner=owner, peer=peer, runtime=runtime, home=home,
        checkout=checkout, path=path, controls=controls, trace=trace, fault=fault,
        initial=initial, canonical=canonical, original_controls=controls.read_bytes(),
        original_history=canonical.read_bytes(), native=native)
    try:
        yield b
    finally:
        await asyncio.wait_for(app.close(), 12)


def stages(b):
    return [json.loads(line) for line in b.trace.read_text().splitlines()] if b.trace.exists() else []


def assert_safe(value):
    raw = json.dumps(value, default=str)
    assert SECRET not in raw
    assert 'provider-fixture' not in raw
    assert 'hooks-fixture' not in raw
    assert 'Synthetic retained instruction' not in raw
    assert '"plan"' not in raw and '"config"' not in raw


async def read_placement(b, identity='read-placement'):
    require_contract()
    receipt = await agent(b.app, b.owner, READ,
        {'sessionId': b.owner['id'], 'moduleId': MODULE}, identity,
        _runtimeSessionId=b.native)
    result = receipt['result']
    assert_safe(result)
    return result


def bind_args(b, snapshot):
    return {'sessionId': b.owner['id'], 'moduleId': MODULE,
        'expectedRevision': snapshot['revision'],
        'expectedExecutionRevision': snapshot['executionRevision'],
        'choice': 'bind-to-execution', 'sourceMessageId': 'operator-choice'}


async def authorize(b):
    # Synthetic human decision in this root, NOT an agent-created grant.
    return await grant(b.app, ['runtime_configuration'], ['session'])


async def submit(b, args, identity='bind-placement', **transport):
    require_contract()
    return await agent(b.app, b.owner, BIND, args, identity,
        **{'_runtimeSessionId': b.native, **transport})


async def operation(b):
    b.observation_number = getattr(b, 'observation_number', 0) + 1
    result = (await agent(b.app, b.owner, 'configuration.operation.read',
        {'sessionId': b.owner['id']}, 'observe-operation-' + str(b.observation_number),
        _runtimeSessionId=b.native))['result']
    assert_safe(result)
    return result


async def settle_submitted_commands(b):
    """Drain already-created service tasks before claiming a duplicate was inert."""
    await asyncio.sleep(0)
    tasks = [task for task in tuple(b.app.tasks) if not task.done()
             and task is not asyncio.current_task()]
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks), 5)
    assert not any(not task.done() for task in b.app.management.pending_config_tasks.values())


async def start(b):
    await asyncio.wait_for(b.runtime.start(b.owner, b.app.on_runtime_event), 12)


async def tool(b, name, arguments):
    return await b.runtime._request(b.owner['id'], 'fixture.tool', name=name, arguments=arguments)


def patch(engine, path='file.txt'):
    if engine == 'native':
        return {'type': 'update_file', 'path': path, 'diff': '@@\n-old\n+new'}
    marker = '***'
    return {'patch': f'{marker} Begin Patch\n{marker} Update File: {path}\n@@\n-old\n+new\n{marker} End Patch'}


def assert_patch_access_denied(result, engine):
    expected_code = {'native': 'file_source_access', 'function': 'file_source_check_failed'}[engine]
    assert result['success'] is False
    assert result['error']['code'] == expected_code
    assert result['error']['message'].startswith('Access denied:')
    assert result['error']['effect'] == 'none'


async def test_public_contract_exposes_no_arbitrary_path_or_full_plan():
    require_contract()
    spec = ACTION_DEFINITIONS[BIND][1]
    assert spec['additionalProperties'] is False
    assert {'path', 'directory', 'workingDirectory', 'config'}.isdisjoint(spec['properties'])
    assert {'sessionId', 'moduleId', 'expectedRevision', 'expectedExecutionRevision', 'choice'} <= set(spec['required'])


@pytest.mark.parametrize('engine', ['native', 'function'])
async def test_process_fixture_readiness_uses_real_engines_and_guard(placement, engine):
    """Can run before the absent recovery API; not a repair acceptance."""
    b = placement
    b.initial['tools'][1]['config']['engine'] = engine
    b.path.write_text(json.dumps(b.initial, indent=2))
    await start(b)
    observed = await b.runtime._request(b.owner['id'], 'fixture.observe')
    assert isinstance(observed.pop('pid'), int)
    assert observed == {'patchDirectory': str(b.home), 'readDirectory': str(b.checkout),
        'engine': 'NativeEngine' if engine == 'native' else 'FunctionEngine', 'guard': True}
    before = b.path.read_bytes()
    assert (await tool(b, 'read_file', {'file_path': 'file.txt'}))['success']
    denied = await tool(b, 'apply_patch', patch(engine))
    assert_patch_access_denied(denied, engine)
    assert b.path.read_bytes() == before
    assert (b.home / 'file.txt').read_text() == (b.checkout / 'file.txt').read_text() == 'old\n'


async def test_cold_read_separates_recorded_saved_and_unobserved_without_warming(placement):
    b = placement
    before = b.path.read_bytes()
    result = await read_placement(b)
    assert result['sessionId'] == b.owner['id']
    assert result['executionDirectory'] == str(b.checkout)
    assert result['executionRevision'] == 1
    assert result['module']['id'] == MODULE
    assert result['module']['module'] == 'tool-apply-patch'
    assert result['saved']['workingDirectory'] == str(b.home)
    assert result['saved']['origin'] == 'unknown'
    assert result['mounted']['status'] == 'unobserved'
    assert result['mounted']['workingDirectory'] is None
    assert not b.runtime.workers and stages(b) == []
    assert b.path.read_bytes() == before


@pytest.mark.parametrize('case', ['peer', 'child'])
async def test_placement_read_cannot_inspect_other_principals(placement, case):
    require_contract()
    b = placement
    args = {'sessionId': b.peer['id'] if case == 'peer' else b.owner['id'], 'moduleId': MODULE}
    transport = {'_runtimeSessionId': 'synthetic-child' if case == 'child' else b.native}
    before, controls = b.path.read_bytes(), b.controls.read_bytes()
    expected = PEER_REFUSAL if case == 'peer' else CHILD_REFUSAL
    with pytest.raises(AppError, match='^' + re.escape(expected) + '$'):
        await agent(b.app, b.owner, READ, args, 'wrong-read-' + case, **transport)
    assert stages(b) == [] and not b.runtime.workers
    assert b.path.read_bytes() == before and b.controls.read_bytes() == controls


@pytest.mark.parametrize('engine', ['native', 'function'])
async def test_bound_remount_changes_only_pin_and_requires_fresh_guarded_read(placement, engine):
    require_contract()
    b = placement
    b.initial['tools'][1]['config']['engine'] = engine
    b.path.write_text(json.dumps(b.initial, indent=2))
    await start(b)
    before = await b.runtime._request(b.owner['id'], 'fixture.observe')
    assert before['patchDirectory'] == str(b.home) and before['readDirectory'] == str(b.checkout)
    assert (await tool(b, 'read_file', {'file_path': 'file.txt'}))['success']
    snapshot = await read_placement(b)
    await authorize(b)
    receipt = await submit(b, bind_args(b, snapshot))
    assert receipt['accepted']
    terminal = await finished(b.app, 'bind-placement')
    assert terminal['phase'] == 'ready', terminal
    observed = await read_placement(b, 'read-after-bind')
    assert observed['saved']['workingDirectory'] == str(b.checkout)
    assert observed['saved']['origin'] == 'explicit'
    assert observed['mounted']['status'] == 'observed'
    assert observed['mounted']['workingDirectory'] == str(b.checkout)
    assert (await operation(b))['state']['effect'] == 'mounted'
    expected = copy.deepcopy(b.initial)
    expected['tools'][1]['config']['working_dir'] = str(b.checkout)
    assert json.loads(b.path.read_text()) == expected
    assert b.controls.read_bytes() == b.original_controls
    assert b.canonical.read_bytes() == b.original_history
    assert b.owner['workspace'] == str(b.home) and b.owner['executionRevision'] == 1
    assert b.owner.get('configuration') is None  # No broad plan published after restart.
    after = await b.runtime._request(b.owner['id'], 'fixture.observe')
    assert after['guard'] and after['patchDirectory'] == after['readDirectory'] == str(b.checkout)
    unread = await tool(b, 'apply_patch', patch(engine))
    assert not unread['success'] and unread['error']['code'] == 'file_source_unread'
    for denied in (str(b.home / 'file.txt'), 'denied/file.txt'):
        result = await tool(b, 'apply_patch', patch(engine, denied))
        assert_patch_access_denied(result, engine)
    assert (await tool(b, 'read_file', {'file_path': 'file.txt'}))['success']
    (b.checkout / 'file.txt').write_text('peer\n')
    stale = await tool(b, 'apply_patch', patch(engine))
    assert not stale['success'] and stale['error']['code'] == 'file_source_stale'
    (b.checkout / 'file.txt').write_text('old\n')
    assert (await tool(b, 'read_file', {'file_path': 'file.txt'}))['success']
    assert (await tool(b, 'apply_patch', patch(engine)))['success']
    assert (b.checkout / 'file.txt').read_text() == 'new\n'
    assert (b.home / 'file.txt').read_text() == 'old\n'
    assert (b.checkout / 'denied/file.txt').read_text() == 'old\n'
    trace = stages(b)
    assert sum(row['stage'] == 'start' for row in trace) == 2
    assert sum(row['stage'] == 'stop' for row in trace) == 1
    assert all(row.get('operation') not in {'configuration.inspect', 'configuration.providers'} for row in trace)
    assert_safe(terminal)


@pytest.mark.parametrize('case', ['ungranted', 'test-only', 'revoked', 'expired', 'stopped',
                                  'peer', 'child', 'stale-generation'])
async def test_bind_rejects_wrong_authority_before_any_runtime_effect(placement, case):
    b = placement
    snapshot = await read_placement(b)
    before = b.path.read_bytes()
    if case == 'test-only':
        await grant(b.app, ['test_environments'], ['session'])
    elif case != 'ungranted':
        row = await authorize(b)
        if case == 'revoked':
            await revoke(b.app, row)
        elif case == 'expired':
            saved = b.app.configuration_authority.record(row['id'])
            saved['expiresAt'] = time.time() - 1
            b.app.configuration_authority.save(saved)
        elif case == 'stopped':
            b.owner['interruptionRevision'] = b.owner.get('interruptionRevision', 0) + 1
    args = bind_args(b, snapshot)
    transport = {}
    if case == 'peer':
        peer_snapshot = (await agent(b.app, b.peer, READ,
            {'sessionId': b.peer['id'], 'moduleId': MODULE}, 'peer-own-read',
            _runtimeSessionId=b.peer['runtimeSessionId']))['result']
        assert peer_snapshot['saved']['workingDirectory'] == str(b.home)
        args['sessionId'] = b.peer['id']
        args['expectedRevision'] = peer_snapshot['revision']
        args['expectedExecutionRevision'] = peer_snapshot['executionRevision']
        args['sourceMessageId'] = 'peer-choice'
    if case == 'child':
        transport['_runtimeSessionId'] = 'synthetic-native-child'
    if case == 'stale-generation':
        transport['_generationId'] = 'old-generation'
    with pytest.raises(AppError, match='(?i)authority|grant|root|own|caller|child|generation|stopp'):
        await submit(b, args, 'forbidden-' + case, **transport)
    assert stages(b) == [] and not b.runtime.workers
    assert b.path.read_bytes() == before
    assert b.controls.read_bytes() == b.original_controls


@pytest.mark.parametrize('case', ['execution', 'config', 'module', 'source', 'engine', 'ambiguity'])
async def test_compare_and_set_rejects_changed_target_without_writes(placement, case):
    b = placement
    snapshot = await read_placement(b)
    await authorize(b)
    if case == 'execution':
        b.owner['executionRevision'] += 1
    else:
        plan = json.loads(b.path.read_text())
        target = plan['tools'][1]
        if case == 'config': target['config']['working_dir'] = str(b.checkout)
        if case == 'module': target['module'] = 'tool-not-patch'
        if case == 'source': target['source'] = 'fixture-different-source'
        if case == 'engine': target['config']['engine'] = 'function'
        if case == 'ambiguity': plan['tools'].append(copy.deepcopy(target))
        b.path.write_text(json.dumps(plan))
    before = b.path.read_bytes()
    try:
        await submit(b, bind_args(b, snapshot), 'stale-' + case)
    except AppError:
        pass
    else:
        assert (await finished(b.app, 'stale-' + case))['phase'] == 'error'
    assert b.path.read_bytes() == before
    assert not b.runtime.workers and stages(b) == []


async def test_live_config_drift_after_read_is_not_overwritten(placement):
    require_contract()
    b = placement
    await start(b)
    snapshot = await read_placement(b)
    await authorize(b)
    await b.runtime._request(b.owner['id'], 'fixture.pin', value=str(b.checkout / 'deliberate'))
    before = b.path.read_bytes()
    await submit(b, bind_args(b, snapshot))
    assert (await finished(b.app, 'bind-placement'))['phase'] == 'error'
    assert b.path.read_bytes() == before
    assert sum(row['stage'] == 'start' for row in stages(b)) == 1
    assert sum(row['stage'] == 'stop' for row in stages(b)) == 0


@pytest.mark.parametrize('case', ['omitted', 'preserve', 'assistant-evidence', 'arbitrary-path', 'full-plan'])
async def test_unknown_pin_requires_explicit_choice_and_rejects_wider_inputs(placement, case):
    b = placement
    snapshot = await read_placement(b)
    await authorize(b)
    args = bind_args(b, snapshot)
    if case == 'omitted': args.pop('choice')
    if case == 'preserve': args['choice'] = 'preserve'
    if case == 'assistant-evidence':
        b.owner['messages'].append({'id': 'not-human', 'role': 'assistant', 'text': 'Bind it'})
        args['sourceMessageId'] = 'not-human'
    if case == 'arbitrary-path': args['workingDirectory'] = str(b.home)
    if case == 'full-plan': args['config'] = b.initial
    before = b.path.read_bytes()
    with pytest.raises(AppError):
        await submit(b, args, 'invalid-choice-' + case)
    assert b.path.read_bytes() == before and stages(b) == []


@pytest.mark.parametrize('phase', ['queued', 'applying', 'error'])
async def test_unresolved_prior_operation_is_not_replaced_or_cleared(placement, phase):
    b = placement
    snapshot = await read_placement(b)
    await authorize(b)
    # Produce a valid retained record through the existing operation owner;
    # this is fixture setup, not a forged UUID or a public repair implementation.
    async with b.app.lock:
        record = b.app.management.create_configuration_operation(
            b.owner, copy.deepcopy(b.initial), 'prior-full-plan', None,
            {'origin': 'ui', 'target': b.app.management.configuration_target_binding(b.owner)})
        retained_id = record['id']
        b.app.management.configuration_transition(b.owner, retained_id,
            phase=phase, stage='dispatching' if phase != 'queued' else 'queued',
            effect='unknown' if phase != 'queued' else 'none', recoveryRequired=phase == 'error')
        b.owner['configurationBusy'] = phase != 'queued'
        b.owner['configurationOperationFence'] = retained_id
    pending = copy.deepcopy(b.owner['pendingConfiguration'])
    record_path = b.app.management.configuration_record_path(b.owner['id'], retained_id)
    prior_record = record_path.read_bytes()
    before = b.path.read_bytes()
    try:
        await submit(b, bind_args(b, snapshot), 'blocked-' + phase)
    except AppError as exc:
        assert any(term in str(exc).lower() for term in ('pending', 'unresolved', 'configuration', 'busy'))
    else:
        refusal = await finished(b.app, 'blocked-' + phase)
        assert refusal['phase'] == 'error'
        assert any(term in refusal['error'].lower() for term in ('pending', 'unresolved', 'configuration', 'busy'))
    assert b.owner['pendingConfiguration'] == pending
    assert b.owner['configurationOperationFence'] == retained_id
    assert record_path.read_bytes() == prior_record
    assert b.path.read_bytes() == before and stages(b) == []


async def test_host_busy_is_refused_without_queuing_a_new_recovery(placement):
    b = placement
    snapshot = await read_placement(b)
    await authorize(b)
    b.owner['status'] = 'working'
    before = b.path.read_bytes()
    try:
        await submit(b, bind_args(b, snapshot))
    except AppError:
        pass
    else:
        assert (await finished(b.app, 'bind-placement'))['phase'] == 'error'
    assert b.path.read_bytes() == before and stages(b) == []
    assert not b.owner.get('pendingConfiguration')


@pytest.mark.parametrize('phase', ['pending', 'unknown'])
async def test_unresolved_handoff_keeps_execution_fence(placement, phase):
    b = placement
    snapshot = await read_placement(b)
    await authorize(b)
    # This is the placement projection consumed by Worktrees.execution_state,
    # not a second Git handoff implementation or a change of execution folder.
    b.owner['worktreeHandoffs'] = [{'id': 'synthetic-handoff', 'phase': phase}]
    b.app._publish_changes(sessions={b.owner['id']})
    before = b.path.read_bytes()
    try:
        await submit(b, bind_args(b, snapshot))
    except AppError as exc:
        assert any(term in str(exc).lower() for term in ('handoff', 'execution', 'fence'))
    else:
        result = await finished(b.app, 'bind-placement')
        assert result['phase'] == 'error'
        assert any(term in result['error'].lower() for term in ('handoff', 'execution', 'fence'))
    assert b.path.read_bytes() == before and stages(b) == []
    assert b.owner['worktreeHandoffs'] == [{'id': 'synthetic-handoff', 'phase': phase}]


async def test_duplicate_success_does_not_write_or_remount_again(placement):
    b = placement
    snapshot = await read_placement(b)
    await authorize(b)
    args = bind_args(b, snapshot)
    await submit(b, args)
    assert (await finished(b.app, 'bind-placement'))['phase'] == 'ready'
    await settle_submitted_commands(b)
    before, trace, stamp = b.path.read_bytes(), stages(b), b.path.stat().st_mtime_ns
    retained = await operation(b)
    duplicate = await submit(b, args)
    assert duplicate['duplicate']
    await settle_submitted_commands(b)
    assert await operation(b) == retained
    assert b.path.stat().st_mtime_ns == stamp
    assert b.path.read_bytes() == before and stages(b) == trace
    with pytest.raises(AppError):
        await submit(b, {**args, 'moduleId': 'different-instance'})
    assert b.path.read_bytes() == before and stages(b) == trace


async def test_duplicate_native_attempt_cannot_repeat_the_effect(placement, monkeypatch):
    b = placement
    snapshot = await read_placement(b)
    await authorize(b)
    original = b.runtime.control
    dispatched = []
    async def capture(sid, op, args, **kwargs):
        if op == BIND:
            dispatched.append((sid, op, copy.deepcopy(args)))
        return await original(sid, op, args, **kwargs)
    monkeypatch.setattr(b.runtime, 'control', capture)
    await submit(b, bind_args(b, snapshot))
    assert (await finished(b.app, 'bind-placement'))['phase'] == 'ready'
    assert len(dispatched) == 1
    sid, op, args = dispatched[0]
    assert args.get('_configurationAttempt'), 'Missing original host/native effect binding'
    before, stamp = b.path.read_bytes(), b.path.stat().st_mtime_ns
    retained = await operation(b)
    with pytest.raises((RuntimeError, ValueError)):
        await original(sid, op, args)
    assert b.path.read_bytes() == before and b.path.stat().st_mtime_ns == stamp
    assert await operation(b) == retained


@pytest.mark.parametrize('change', ['revoke', 'stop'])
async def test_authority_rechecked_after_waiting_for_management_lock(placement, change):
    b = placement
    snapshot = await read_placement(b)
    authorized = await authorize(b)
    before = b.path.read_bytes()
    await b.app.management.lock.acquire()
    try:
        await submit(b, bind_args(b, snapshot))
        if change == 'revoke':
            await revoke(b.app, authorized)
        else:
            b.owner['interruptionRevision'] = b.owner.get('interruptionRevision', 0) + 1
    finally:
        b.app.management.lock.release()
    assert (await finished(b.app, 'bind-placement'))['phase'] == 'error'
    assert b.path.read_bytes() == before and stages(b) == []


async def test_native_busy_badge_cannot_authorize_the_write(placement):
    b = placement
    require_contract()
    await start(b)
    snapshot = await read_placement(b)
    await authorize(b)
    await b.runtime._request(b.owner['id'], 'fixture.busy', count=1)
    before = b.path.read_bytes()
    await submit(b, bind_args(b, snapshot))
    assert (await finished(b.app, 'bind-placement'))['phase'] == 'error'
    retained = await operation(b)
    assert retained['nativeEffect'] == 'none'
    assert retained['state']['effect'] != 'mounted'
    assert b.path.read_bytes() == before
    assert sum(row['stage'] == 'start' for row in stages(b)) == 1


async def test_saved_setting_is_not_mounted_success_when_actual_engine_disagrees(placement):
    b = placement
    require_contract()
    await start(b)
    first = await b.runtime._request(b.owner['id'], 'fixture.observe')
    snapshot = await read_placement(b)
    await authorize(b)
    b.fault.write_text('wrong-mounted-base')
    await submit(b, bind_args(b, snapshot))
    assert (await finished(b.app, 'bind-placement'))['phase'] == 'error'
    retained = await operation(b)
    assert retained['state']['effect'] in {'saved_not_mounted', 'unknown'}
    assert b.owner['configurationBusy']
    assert json.loads(b.path.read_text())['tools'][1]['config']['working_dir'] == str(b.checkout)
    actual = await b.runtime._request(b.owner['id'], 'fixture.observe')
    assert actual['pid'] != first['pid']
    assert sum(row['stage'] == 'start' for row in stages(b)) == 2
    assert sum(row['stage'] == 'injected-wrong-mounted-base' for row in stages(b)) == 1
    assert actual['patchDirectory'] == str(b.home)
    assert b.controls.read_bytes() == b.original_controls


async def test_lost_reply_preserves_unknown_and_read_or_duplicate_never_replays(placement, monkeypatch):
    b = placement
    snapshot = await read_placement(b)
    await authorize(b)
    original = b.runtime.control
    injections = []
    async def lose(sid, operation, args, **kwargs):
        result = await original(sid, operation, args, **kwargs)
        if operation == BIND:
            injections.append(operation)
            raise RuntimeError('Synthetic transport lost after narrow native effect')
        return result
    monkeypatch.setattr(b.runtime, 'control', lose)
    args = bind_args(b, snapshot)
    await submit(b, args)
    assert (await finished(b.app, 'bind-placement'))['phase'] == 'error'
    assert injections == [BIND]
    assert json.loads(b.path.read_text())['tools'][1]['config']['working_dir'] == str(b.checkout)
    retained = await operation(b)
    assert retained['state']['effect'] in {'unknown', 'saved_not_mounted'}
    await settle_submitted_commands(b)
    before, trace, stamp = b.path.read_bytes(), stages(b), b.path.stat().st_mtime_ns
    assert (await submit(b, args))['duplicate']
    await settle_submitted_commands(b)
    assert await operation(b) == retained
    assert injections == [BIND]
    assert b.path.stat().st_mtime_ns == stamp
    assert b.path.read_bytes() == before and stages(b) == trace
    assert b.owner['configurationBusy']


@pytest.mark.parametrize('origin', [None, 'agent', 'system'])
async def test_role_user_is_not_proof_of_human_binding_choice(placement, origin):
    b = placement
    snapshot = await read_placement(b)
    await authorize(b)
    next(row for row in b.owner['messages'] if row['id'] == 'operator-choice')['inputOrigin'] = origin
    before = b.path.read_bytes()
    with pytest.raises(AppError, match='(?i)human|choice|evidence'):
        await submit(b, bind_args(b, snapshot), 'not-a-human-choice')
    assert b.path.read_bytes() == before and stages(b) == []


async def test_explicit_when_idle_waits_for_root_turn_without_weakening_write_fence(placement):
    b = placement
    snapshot = await read_placement(b)
    await authorize(b)
    b.owner['status'] = 'working'
    before = b.path.read_bytes()
    receipt = await submit(b, {**bind_args(b, snapshot), 'whenIdle': True})
    assert receipt['accepted']
    await asyncio.sleep(0)
    assert b.owner['pendingConfiguration']['phase'] == 'queued'
    assert b.path.read_bytes() == before and stages(b) == []
    b.owner['status'] = 'idle'
    assert (await finished(b.app, 'bind-placement'))['phase'] == 'ready'
    assert json.loads(b.path.read_text())['tools'][1]['config']['working_dir'] == str(b.checkout)


async def test_placement_controls_preserve_bytes_until_ordinary_persistence(placement):
    from amplifier_core import ModuleCoordinator
    from amplifier_web.runtime_controls import RuntimeControls
    b = placement
    session = SimpleNamespace(session_id=b.native, parent_id=None, config=copy.deepcopy(b.initial))
    session.coordinator = ModuleCoordinator(session=session)
    controls = RuntimeControls(session, SimpleNamespace(generation=None, queued_inputs=0),
        preserve_configuration=True)
    try:
        controls.persist()
        assert b.controls.read_bytes() == b.original_controls
        assert not b.path.with_name('effective-configuration.json').exists()
        controls.preserve_configuration = False
        controls.persist()
        assert b.path.with_name('effective-configuration.json').exists()
        assert b.controls.read_bytes() != b.original_controls
    finally:
        await controls.close()
        await session.coordinator.cleanup()


@pytest.mark.parametrize('change', ['terminal', 'revoked', 'stopped'])
async def test_completed_root_can_read_and_retrieve_exact_receipt_without_admission(placement, monkeypatch, change):
    b = placement
    snapshot = await read_placement(b)
    authorized = await authorize(b)
    args = bind_args(b, snapshot)
    accepted = await submit(b, args)
    assert (await finished(b.app, 'bind-placement'))['phase'] == 'ready'
    await settle_submitted_commands(b)
    # This must be the terminal generation produced by the real worker exit,
    # not a fixture reset to manufacture new mutation authority.
    assert b.owner['collaborationGeneration']['terminal'] is True
    if change == 'revoked':
        await revoke(b.app, authorized)
    elif change == 'stopped':
        b.owner['interruptionRevision'] = b.owner.get('interruptionRevision', 0) + 1
    generation = copy.deepcopy(b.owner['collaborationGeneration'])
    retained = await operation(b)
    record_path = b.app.management.configuration_record_path(b.owner['id'], retained['operationId'])
    record_bytes = record_path.read_bytes()
    before, stamp, trace = b.path.read_bytes(), b.path.stat().st_mtime_ns, stages(b)
    commands = b.app.db.execute('SELECT count(*) FROM commands').fetchone()[0]

    def no_new_admission(*args, **kwargs):
        pytest.fail('A passive placement observation or exact receipt retry invoked new authority capture')
    monkeypatch.setattr(b.app.configuration_authority, 'capture', no_new_admission)
    observed = await read_placement(b, 'terminal-observation')
    assert observed['saved'] == {'workingDirectory': str(b.checkout), 'origin': 'explicit'}
    assert observed['mounted'] == {'status': 'observed', 'workingDirectory': str(b.checkout)}
    duplicate = await submit(b, args)
    assert duplicate['duplicate'] and duplicate['accepted'] == accepted['accepted']
    assert duplicate['result'] == accepted['result']
    assert_safe(duplicate['result'])
    await settle_submitted_commands(b)
    assert await operation(b) == retained
    assert record_path.read_bytes() == record_bytes
    assert b.path.read_bytes() == before and b.path.stat().st_mtime_ns == stamp
    assert stages(b) == trace and b.controls.read_bytes() == b.original_controls
    assert b.app.db.execute('SELECT count(*) FROM commands').fetchone()[0] == commands
    assert b.owner['collaborationGeneration'] == generation


@pytest.mark.parametrize('change', ['terminal', 'stale-generation'])
async def test_passive_read_never_admits_new_binding_from_stale_generation(placement, change):
    b = placement
    await authorize(b)
    if change == 'terminal':
        b.app.collaboration.observe(b.owner, 'runtime.ended', {'sessionId': b.owner['id']})
    snapshot = await read_placement(b)
    transport = {'_generationId': 'stale-generation'} if change == 'stale-generation' else {}
    before = b.path.read_bytes()
    with pytest.raises(AppError, match='current transport-bound root generation'):
        await submit(b, bind_args(b, snapshot), 'new-stale-operation', **transport)
    assert not b.owner.get('pendingConfiguration') and not b.runtime.workers
    assert stages(b) == [] and b.path.read_bytes() == before
    assert b.controls.read_bytes() == b.original_controls


@pytest.mark.parametrize('change', ['revoke', 'stop'])
async def test_retained_no_effect_receipt_does_not_borrow_authority_for_new_request(placement, change):
    b = placement
    snapshot = await read_placement(b)
    authorized = await authorize(b)
    args = bind_args(b, snapshot)
    before = b.path.read_bytes()
    await b.app.management.lock.acquire()
    try:
        accepted = await submit(b, args)
        if change == 'revoke':
            await revoke(b.app, authorized)
        else:
            b.owner['interruptionRevision'] = b.owner.get('interruptionRevision', 0) + 1
    finally:
        b.app.management.lock.release()
    assert (await finished(b.app, 'bind-placement'))['phase'] == 'error'
    await settle_submitted_commands(b)
    assert b.owner['collaborationGeneration']['terminal'] is False
    retained = await operation(b)
    assert retained['state']['effect'] == 'none' and retained['attempts'] == 0
    duplicate = await submit(b, args)
    assert duplicate['duplicate'] and duplicate['result'] == accepted['result']
    fresh = await read_placement(b, 'after-authority-ended')
    with pytest.raises(AppError, match='Configuration authority'):
        await submit(b, bind_args(b, fresh), 'different-new-request')
    await settle_submitted_commands(b)
    assert await operation(b) == retained
    assert stages(b) == [] and not b.runtime.workers
    assert b.path.read_bytes() == before and b.controls.read_bytes() == b.original_controls


@pytest.mark.parametrize('action', [READ, BIND])
@pytest.mark.parametrize('actor', ['foreign-root', 'child', 'missing-principal'])
async def test_terminal_observation_and_receipt_remain_transport_root_bound(placement, action, actor):
    b = placement
    snapshot = await read_placement(b)
    await authorize(b)
    args = bind_args(b, snapshot)
    await submit(b, args)
    assert (await finished(b.app, 'bind-placement'))['phase'] == 'ready'
    await settle_submitted_commands(b)
    assert b.owner['collaborationGeneration']['terminal'] is True
    retained = await operation(b)
    before, stamp, trace = b.path.read_bytes(), b.path.stat().st_mtime_ns, stages(b)
    source = b.peer if actor == 'foreign-root' else b.owner
    principal = b.peer['runtimeSessionId'] if actor == 'foreign-root' else 'synthetic-child' if actor == 'child' else None
    request = args if action == BIND else {'sessionId': b.owner['id'], 'moduleId': MODULE}
    expected = PEER_REFUSAL if actor == 'foreign-root' else CHILD_REFUSAL if actor == 'child' else 'Patch placement requires its authenticated transport root.'
    with pytest.raises(AppError, match='^' + re.escape(expected) + '$'):
        await agent(b.app, source, action, request, 'bind-placement', _runtimeSessionId=principal)
    await settle_submitted_commands(b)
    assert await operation(b) == retained
    assert b.path.read_bytes() == before and b.path.stat().st_mtime_ns == stamp
    assert stages(b) == trace and b.controls.read_bytes() == b.original_controls


@pytest.mark.parametrize('mismatch', ['payload', 'root'])
async def test_terminal_binding_receipt_rejects_same_id_with_changed_identity(placement, mismatch):
    b = placement
    snapshot = await read_placement(b)
    await authorize(b)
    args = bind_args(b, snapshot)
    await submit(b, args)
    assert (await finished(b.app, 'bind-placement'))['phase'] == 'ready'
    await settle_submitted_commands(b)
    assert b.owner['collaborationGeneration']['terminal'] is True
    retained = await operation(b)
    before, stamp, trace = b.path.read_bytes(), b.path.stat().st_mtime_ns, stages(b)
    if mismatch == 'root':
        source, principal = b.peer, b.peer['runtimeSessionId']
        args = {**args, 'sessionId': b.peer['id'], 'sourceMessageId': 'peer-choice'}
    else:
        source, principal = b.owner, b.native
        args = {**args, 'moduleId': 'different-instance'}
    with pytest.raises(AppError, match='This command ID was already used with different contents'):
        await agent(b.app, source, BIND, args, 'bind-placement', _runtimeSessionId=principal)
    await settle_submitted_commands(b)
    assert await operation(b) == retained
    assert b.path.read_bytes() == before and b.path.stat().st_mtime_ns == stamp
    assert stages(b) == trace


async def test_binding_receipt_lookup_rejects_a_command_from_another_action(placement):
    b = placement
    snapshot = await read_placement(b)
    await b.app.dispatch('session.rename', {'id': b.owner['id'], 'title': 'Synthetic renamed owner'},
        command_id='not-a-binding-command')
    b.app.collaboration.observe(b.owner, 'runtime.ended', {'sessionId': b.owner['id']})
    before = b.path.read_bytes()
    with pytest.raises(AppError, match='This command ID was already used with different contents'):
        await submit(b, bind_args(b, snapshot), 'not-a-binding-command')
    assert b.path.read_bytes() == before and stages(b) == []
    assert not b.runtime.workers and not b.owner.get('pendingConfiguration')
