"""Mounted Full actors share owned environments; controlled backend effects only."""
import asyncio
from copy import deepcopy
import threading

import pytest

from amplifier_web import task_environments as owner
from amplifier_web.collaboration import BINDING, PRINCIPAL
from amplifier_web.service import AppError
from test_access_mode import save
from test_configuration_authority import app
from test_full_child_configuration import mounted, dispatch
from test_task_environments import backend, scope, start
from test_environment_recovery import observation, removal_read_fixture, removal_read_snapshot


PREFIX = 'workspace.environments.'


def source_facts(h):
    # These are the real fixture owner's explicit placement facts, not recovered
    # defaults for a cold native record.
    h.source.update(sessionKind='root', workingDirectory=h.source['workspace'],
                    executionRevision=0, interruptionRevision=0)


async def native(h, name, journey):
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        async def invoke(action, args, identity):
            result = await dispatch(tools['app_control'], PREFIX + action, args, identity)
            assert result.success, result.error
            return result.output
        await journey(invoke, coordinator.session_id)
        return 'done'
    h.install_execute(execute)
    await h.registry.spawn('worker', 'Run the delegated test task', h.parent, sub_session_id=name)


def system(app, sid):
    async def invoke(action, args, identity):
        return await app.dispatch(PREFIX + action, args, origin='system',
            caller_session_id=sid, command_id=identity, include_state=False)
    return invoke


def attributed(request, sid, principal, generation='generation-1'):
    assert request['attribution'] == {
        'sessionId': sid, 'origin': 'system' if principal is None else 'agent',
        'principal': principal, 'generationId': generation if principal is not None else None}


@pytest.mark.parametrize('continuation', ['child', 'system'])
async def test_mounted_full_environment_cycle_keeps_owner_and_actual_actors(mounted, backend, monkeypatch, continuation):
    h = mounted
    source_facts(h)
    await save(h.app)
    original = owner.invoke
    def invoke(config, home, row, action, args):
        if action in {'exportArtifacts', 'reconcile'}:
            backend.append((action, dict(args)))
            if action == 'reconcile':
                return observation(row, 'stopped')
            return {'ok': True, 'status': 'stopped', 'universeId': row['universeId'],
                    'artifactExport': {'verified': True, 'selection': ['/artifacts'], 'archiveBytes': 100}}
        return original(config, home, row, action, args)
    monkeypatch.setattr(owner, 'invoke', invoke)
    first = []
    start_args = {**scope(h.app), 'profileId': owner.PROFILE, 'requestId': 'start-once'}
    async def create(call, principal):
        assert not (await call('read', scope(h.app), 'empty'))['result']['items']
        result = await call('start', start_args, 'start-first')
        assert result['result']['request']['state'] == 'completed'
        attributed(result['result']['request'], h.source['id'], principal)
        first.append(result['result'])
    await native(h, 'first-child', create)
    assert len(first) == 1
    original_request = deepcopy(first[0]['request'])
    retained = owner.get(h.app.data_dir, h.source['id'], first[0]['resource']['id'])
    assert retained['owner'] == {'sessionId': h.source['id'], 'principal': 'native-root'}
    evidence = owner.storage.directory(h.app.data_dir, retained) / 'retained-evidence.txt'
    evidence.write_text('keep these bytes')

    async def continue_work(call, principal):
        duplicate = await call('start', start_args, 'different-transport-id')
        assert duplicate['duplicate'] and duplicate['result']['request'] == original_request
        row = duplicate['result']['resource']
        for action, extra in [('execute', {'command': 'python -V'}), ('stop', {}),
                              ('exportArtifacts', {}), ('reconcile', {}), ('remove', {})]:
            args = {**scope(h.app), 'id': row['id'], 'expectedRevision': row['revision'],
                    'requestId': action + '-once', **extra}
            result = await call(action, args, action + '-transport')
            assert result['result']['request']['state'] == 'completed', result
            attributed(result['result']['request'], h.source['id'], principal)
            count = len(backend)
            retry = await call(action, args, action + '-retry-transport')
            assert retry['duplicate'] and retry['result']['request'] == result['result']['request']
            assert len(backend) == count
            if action == 'execute':
                assert result['result']['request']['result']['result']['stdout'] == 'check passed\n'
                with pytest.raises((AppError, AssertionError), match='different operation arguments'):
                    await call(action, {**args, 'command': 'changed'}, 'changed-command')
            row = result['result']['resource']
        assert row['status'] == 'removed' and evidence.read_text() == 'keep these bytes'
        args = {**scope(h.app), 'id': row['id'], 'expectedRevision': row['revision']}
        before = removal_read_snapshot(h.app, row)
        count = len(backend)
        listed = (await call('removal.list', args, 'passive-list'))['result']
        assert any(item['originalRequestId'] == 'remove-once' for item in listed['items'])
        read = (await call('removal.read', {**args, 'originalRequestId': 'remove-once'}, 'passive-read'))['result']
        assert read['original']['requestId'] == 'remove-once'
        assert removal_read_snapshot(h.app, row) == before and len(backend) == count
    if continuation == 'child':
        await native(h, 'later-child', continue_work)
    else:
        await continue_work(system(h.app, h.source['id']), None)
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'stop', 'exportArtifacts', 'reconcile', 'remove']
    assert h.app.operations.requests.read(h.source['id'], 'start-once') == original_request
    assert not h.source['approvals'] and h.app.configuration_authority.rows(h.source['id']) == []
    h.approval_prompt.assert_not_awaited()


async def test_full_child_retries_legacy_owner_receipt_and_ui_retries_child_after_revocation(mounted, backend):
    h = mounted
    legacy = await start(h.app, 'legacy-start')
    assert 'attribution' not in legacy['request']
    signature = h.app.operations.requests.journal.db.execute(
        'SELECT signature FROM operation_requests WHERE session_id=? AND id=?',
        (h.source['id'], 'legacy-start')).fetchone()[0]
    await save(h.app)
    executed = []
    args = {**scope(h.app), 'id': legacy['resource']['id'], 'expectedRevision': legacy['resource']['revision'],
            'requestId': 'child-execute', 'command': 'python -V'}
    async def run(call, principal):
        retry = await call('start', {**scope(h.app), 'profileId': owner.PROFILE, 'requestId': 'legacy-start'}, 'legacy-retry')
        assert retry['duplicate'] and retry['result']['request'] == legacy['request']
        executed.append((await call('execute', args, 'child-execute'))['result']['request'])
    await native(h, 'child', run)
    assert len(executed) == 1
    attributed(executed[0], h.source['id'], 'child')
    await save(h.app, mode='ask')
    result = await h.app.dispatch(PREFIX + 'execute', args)
    assert result['duplicate'] and result['result']['request'] == executed[0]
    assert h.app.operations.requests.journal.db.execute(
        'SELECT signature FROM operation_requests WHERE session_id=? AND id=?',
        (h.source['id'], 'legacy-start')).fetchone()[0] == signature
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute']


@pytest.mark.parametrize('change', ['terminal', 'stop', 'revoke', 'off-on', 'move', 'native-owner', 'backend'])
async def test_full_native_admission_outlives_turn_but_rechecks_next_effect(mounted, backend, monkeypatch, tmp_path, change):
    h = mounted
    await save(h.app)
    entered, release = threading.Event(), threading.Event()
    original = owner.invoke
    def paused(config, home, row, action, args):
        result = original(config, home, row, action, args)
        if action == 'prepare':
            entered.set()
            assert release.wait(5), 'test did not release preparation'
        return result
    monkeypatch.setattr(owner, 'invoke', paused)
    results = []
    async def create(call, principal):
        result = await call('start', {**scope(h.app), 'profileId': owner.PROFILE,
            'requestId': 'held-start'}, 'held-start')
        results.append(result['result'])
    task = asyncio.create_task(native(h, 'child', create))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        if change == 'terminal':
            h.source['collaborationGeneration']['terminal'] = True
        elif change == 'stop':
            await h.app.dispatch('conversation.stop', {'sessionId': h.source['id']})
        elif change in {'revoke', 'off-on'}:
            await save(h.app, mode='ask')
            if change == 'off-on':
                await save(h.app)
        elif change == 'move':
            h.source['workingDirectory'] = str(tmp_path / 'moved')
            h.source['executionRevision'] = h.source.get('executionRevision', 0) + 1
        elif change == 'native-owner':
            h.source['runtimeSessionId'] = 'another-native-owner'
        else:
            config = owner.configuration(h.app)[0]
            monkeypatch.setattr(owner, 'configuration', lambda app: ({**config, 'image': 'different'}, 'ready', 'changed'))
    finally:
        release.set()
    await asyncio.wait_for(task, 10)
    assert len(results) == 1
    attributed(results[0]['request'], h.source['id'], 'child')
    assert results[0]['request']['state'] == ('completed' if change == 'terminal' else 'failed')
    assert [action for action, _ in backend] == (['prepare', 'start'] if change == 'terminal' else ['prepare'])


async def test_unknown_full_system_effect_stays_unknown_for_later_child(mounted, backend, monkeypatch):
    h = mounted
    await save(h.app)
    invoke = system(h.app, h.source['id'])
    row = (await invoke('start', {**scope(h.app), 'profileId': owner.PROFILE,
        'requestId': 'system-start'}, 'system-start'))['result']['resource']
    original = owner.invoke
    def lost(config, home, resource, action, args):
        if action == 'execute':
            backend.append((action, dict(args)))
            raise TimeoutError('reply lost after command admission')
        if action == 'reconcile':
            backend.append((action, dict(args)))
            return observation(resource, 'stopped')
        return original(config, home, resource, action, args)
    monkeypatch.setattr(owner, 'invoke', lost)
    args = {**scope(h.app), 'id': row['id'], 'expectedRevision': row['revision'],
            'requestId': 'uncertain-execute', 'command': 'run once'}
    unknown = (await invoke('execute', args, 'uncertain'))['result']
    assert unknown['request']['state'] == 'outcome_unknown'
    attributed(unknown['request'], h.source['id'], None)
    async def recover(call, principal):
        retry = await call('execute', args, 'later-child-retry')
        assert retry['duplicate'] and retry['result']['request'] == unknown['request']
        result = await call('reconcile', {**scope(h.app), 'id': row['id'],
            'expectedRevision': unknown['resource']['revision'], 'requestId': 'inspect-once'}, 'inspect-once')
        assert result['result']['request']['state'] == 'completed'
        assert result['result']['resource']['lastOperation'] == unknown['request']
        attributed(result['result']['request'], h.source['id'], principal)
    await native(h, 'later-child', recover)
    assert h.app.operations.requests.read(h.source['id'], args['requestId']) == unknown['request']
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'reconcile']


@pytest.mark.parametrize('invalid', ['ask-child', 'stopped-child', 'missing-system-owner', 'foreign-owner', 'stale-system-binding'])
async def test_routine_full_admission_never_invents_owner_or_current_native_work(mounted, backend, invalid):
    h = mounted
    if invalid != 'ask-child':
        await save(h.app)
    args = {**scope(h.app), 'profileId': owner.PROFILE, 'requestId': 'not-admitted'}
    if invalid in {'ask-child', 'stopped-child'}:
        visited = []
        async def execute(self, prompt, context, providers, tools, hooks, coordinator):
            if invalid == 'stopped-child':
                await h.app.dispatch('conversation.stop', {'sessionId': h.source['id']})
            result = await dispatch(tools['app_control'], PREFIX + 'start', args, 'not-admitted')
            assert not result.success
            visited.append(True)
            return 'refused'
        h.install_execute(execute)
        await h.registry.spawn('worker', 'setup', h.parent, sub_session_id='child')
        assert visited == [True]
    else:
        principal = PRINCIPAL.set('native-root' if invalid == 'stale-system-binding' else None)
        binding = BINDING.set({'_runtimeSessionId': 'native-root', '_generationId': 'stale'}
            if invalid == 'stale-system-binding' else None)
        try:
            caller = None if invalid == 'missing-system-owner' else h.source['id']
            if invalid == 'foreign-owner':
                args['sessionId'] = next(row['id'] for row in h.app.state['sessions'] if row['id'] != h.source['id'])
            with pytest.raises(AppError):
                await h.app.dispatch(PREFIX + 'start', args, origin='system', caller_session_id=caller)
        finally:
            BINDING.reset(binding)
            PRINCIPAL.reset(principal)
    assert not backend and not h.app.operations.requests.list(h.source['id'])


async def test_system_full_passive_removal_reads_never_hydrate_or_mutate(app, backend, monkeypatch):
    row, original = await removal_read_fixture(app, monkeypatch)
    source = app.state['sessions'][0]
    await save(app)
    before = removal_read_snapshot(app, row)
    changes = (app.db.total_changes, app.operations.requests.journal.db.total_changes)
    monkeypatch.setattr(app, '_session', lambda *a, **k: pytest.fail('passive owner hydration'))
    monkeypatch.setattr(owner, 'configuration', lambda *a: pytest.fail('passive backend lookup'))
    invoke = system(app, source['id'])
    args = {**scope(app), 'id': row['id'], 'expectedRevision': row['revision']}
    listed = (await invoke('removal.list', args, 'passive-list'))['result']
    assert any(item['originalRequestId'] == original['requestId'] for item in listed['items'])
    read = (await invoke('removal.read', {**args, 'originalRequestId': original['requestId']}, 'passive-read'))['result']
    assert read['original']['requestId'] == original['requestId']
    assert removal_read_snapshot(app, row) == before
    assert (app.db.total_changes, app.operations.requests.journal.db.total_changes) == changes


@pytest.mark.parametrize('mode', ['nested', 'resumed', 'persistent'])
async def test_full_routine_calls_keep_actual_nested_resumed_and_persistent_actor(mounted, backend, mode):
    from amplifier_web.host.children import _PERSISTENT
    h = mounted
    await save(h.app)
    visited = []
    original_start = []
    args = {**scope(h.app), 'profileId': owner.PROFILE, 'requestId': 'one-environment'}
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        principal = coordinator.session_id
        visited.append(principal)
        result = await dispatch(tools['app_control'], PREFIX + 'start', args, 'transport-' + str(len(visited)))
        assert result.success, result.error
        current = result.output['result']
        if not original_start:
            original_start.append(deepcopy(current['request']))
            attributed(current['request'], h.source['id'], principal)
        else:
            assert result.output['duplicate'] and current['request'] == original_start[0]
        run = await dispatch(tools['app_control'], PREFIX + 'execute', {
            **scope(h.app), 'id': current['resource']['id'], 'expectedRevision': current['resource']['revision'],
            'requestId': 'run-' + str(len(visited)), 'command': 'python -V'}, 'run-' + str(len(visited)))
        assert run.success and run.output['result']['request']['state'] == 'completed'
        attributed(run.output['result']['request'], h.source['id'], principal)
        if mode == 'nested' and principal == 'child':
            await h.registry.spawn('worker', 'nested test', h.registry.sessions['child'], sub_session_id='grandchild')
        return 'tested'
    h.install_execute(execute)
    token = _PERSISTENT.set(mode == 'persistent')
    try:
        await h.registry.spawn('worker', 'run tests', h.parent, sub_session_id='child')
        if mode == 'resumed':
            await h.registry.resume('child', 'continue tests', h.parent)
    finally:
        _PERSISTENT.reset(token)
    assert visited == (['child', 'grandchild'] if mode == 'nested' else ['child', 'child'] if mode == 'resumed' else ['child'])
    assert [action for action, _ in backend] == ['prepare', 'start'] + ['execute'] * len(visited)
    assert not h.source['approvals']


@pytest.mark.parametrize('outcome', ['proven-pre-removal-failure', 'unknown'])
async def test_full_child_cleanup_uses_exact_original_evidence_without_rewriting_it(mounted, backend, monkeypatch, outcome):
    h = mounted
    await save(h.app)
    call = system(h.app, h.source['id'])
    row = (await call('start', {**scope(h.app), 'profileId': owner.PROFILE,
        'requestId': 'cleanup-start'}, 'cleanup-start'))['result']['resource']
    row = (await call('stop', {**scope(h.app), 'id': row['id'], 'expectedRevision': row['revision'],
        'requestId': 'cleanup-stop'}, 'cleanup-stop'))['result']['resource']
    original = owner.invoke
    def controlled(config, home, current, action, args):
        if action == 'remove':
            backend.append((action, dict(args)))
            if outcome == 'unknown':
                raise TimeoutError('unknown removal')
            return {'ok': False, 'status': 'stopped', 'universeId': current['universeId'],
                    'phase': 'preserving', 'code': 'preservation_failed', 'effectAdmitted': False}
        if action == 'finishCleanup':
            backend.append((action, dict(args)))
            return {'ok': True, 'status': 'removed', 'universeId': current['universeId']}
        return original(config, home, current, action, args)
    monkeypatch.setattr(owner, 'invoke', controlled)
    failed = (await call('remove', {**scope(h.app), 'id': row['id'], 'expectedRevision': row['revision'],
        'requestId': 'original-removal'}, 'original-removal'))['result']
    retained = deepcopy(failed['request'])
    assert retained['state'] == ('failed' if outcome == 'proven-pre-removal-failure' else 'outcome_unknown')
    args = {**scope(h.app), 'id': row['id'], 'expectedRevision': failed['resource']['revision'],
            'requestId': 'child-cleanup', 'previousRequestId': 'original-removal'}
    visited = []
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        result = await dispatch(tools['app_control'], PREFIX + 'finishCleanup', args, 'child-cleanup')
        visited.append(True)
        if outcome == 'unknown':
            assert not result.success and 'unresolved' in str(result.error)
        else:
            assert result.success, result.error
            assert result.output['result']['request']['state'] == 'completed'
            assert result.output['result']['resource']['status'] == 'removed'
            attributed(result.output['result']['request'], h.source['id'], coordinator.session_id)
        return 'inspected cleanup'
    h.install_execute(execute)
    await h.registry.spawn('worker', 'finish owned cleanup', h.parent, sub_session_id='child')
    assert visited == [True]
    assert h.app.operations.requests.read(h.source['id'], 'original-removal') == retained
    assert [action for action, _ in backend] == ['prepare', 'start', 'stop', 'remove'] + (
        ['finishCleanup'] if outcome == 'proven-pre-removal-failure' else [])
