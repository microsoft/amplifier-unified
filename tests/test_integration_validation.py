"""Exact invocation/owner evidence, partial combination and no-effect reads."""
import asyncio
import hashlib
import json
import shlex
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.integration_validation import admit


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


@pytest.fixture
async def project(tmp_path):
    roots = [tmp_path / 'workspace' / name for name in ('first', 'second')]
    for root in roots:
        root.mkdir(parents=True)
        git(root, 'init', '-q'); git(root, 'config', 'user.name', 'Fixture'); git(root, 'config', 'user.email', 'fixture@example.test')
        (root / 'source.txt').write_text('original\n')
        (root / (root.name + '_dependency.py')).write_text('VALUE = ' + repr(root.name) + '\n')
        (root / '.gitignore').write_text('result.txt\n')
        git(root, 'add', '.'); git(root, 'commit', '-qm', 'Original contributor work')
    app = AppService(tmp_path / 'app', workspace=roots[0].parent)
    await app.dispatch('session.create', {'title': 'Integration owner'})
    sid = app._session()['id']
    app.runtime = SimpleNamespace(close=AsyncMock(), provenance=AsyncMock(return_value={'status': 'unknown'}), integration_validation_supported=AsyncMock(return_value=True))
    capture = await app.dispatch('integration.capture', {'sessionId': sid, 'repositories': [
        {'repositoryPath': str(root), 'sourceRevision': app.worktrees.git.inspect(root)['sourceRevision']} for root in roots],
        'dependencies': []}, command_id='candidate')
    assert capture['result']['status'] == 'matched'
    calls = []
    async def control(session_id, action, args):
        calls.append(args)
        await admit(app, sid, args['integrationId'], args['command'])
        identity = 'process-' + str(len(calls))
        await observed(app, sid, identity)
        return {'result': {'success': True, 'output': {'process_id': identity, 'owner_id': 'mount', 'state': 'running'}}}
    app.runtime.control = control
    yield app, sid, roots, calls
    await app.close()


async def observed(app, sid, identity, *, code=0, complete=True):
    def event(seq, phase, **extra):
        return {'schemaVersion': 1, 'eventId': identity + ':' + str(seq), 'sequence': seq,
                'operationId': identity, 'ownerId': 'mount', 'source': 'tool-bash', 'kind': 'process', 'phase': phase, 'at': 1, **extra}
    await app.operations.observe(sid, sid, event(1, 'started'))
    await app.operations.observe(sid, sid, event(2, 'finished', status={'state': 'completed' if code == 0 else 'failed',
        'returncode': code, 'output_complete': complete, 'total_output_bytes': 0, 'ended_at': 2}))


async def settle(app):
    # Await only the currently scheduled owned submission, never replay it.
    pending = list(app.tasks)
    if pending: await asyncio.wait_for(asyncio.gather(*pending), 5)


async def validate(app, sid, root, identity='run', command='printf passed'):
    result = await app.dispatch('integration.validate', {'sessionId': sid, 'captureId': 'candidate',
        'repositoryPath': str(root), 'command': command}, command_id=identity)
    await settle(app)
    return result


async def collect(app, sid, ids, identity='collection', results=None):
    return (await app.dispatch('integration.collect', {'sessionId': sid, 'captureId': 'candidate',
        'validationIds': ids, **({'results': results} if results is not None else {})}, command_id=identity))['result']


async def test_exact_commands_partial_combination_artifacts_and_reported_publication(project):
    app, sid, roots, calls = project
    first = await validate(app, sid, roots[0])
    assert first['result']['phase'] == 'admitting'
    assert (await validate(app, sid, roots[0]))['duplicate'] and len(calls) == 1
    partial = await collect(app, sid, ['run'])
    assert partial['status'] == 'partial'
    assert [row['status'] for row in partial['repositories']] == ['passed', 'not_validated']
    await validate(app, sid, roots[1], 'run-second')
    artifact = roots[0] / 'result.txt'; artifact.write_text('exact result\n')
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    results = [{'repositoryPath': str(roots[0]), 'references': ['result.txt@sha256:' + digest],
                'publication': {'status': 'published', 'reference': 'https://example.test/release'}}]
    combined = await collect(app, sid, ['run', 'run-second'], 'combined', results)
    assert combined['status'] == 'passed'
    assert all(row['resultingSource']['head'] == git(root, 'rev-parse', 'HEAD') for row, root in zip(combined['repositories'], roots))
    row = combined['repositories'][0]
    assert row['publication']['evidence'] == 'reported' and not row['publication']['ownerConfirmed']
    assert combined['repositories'][1]['publication']['status'] == 'unknown'
    artifact.unlink()
    record = app.outputs.record(sid, row['artifacts'][0]['outputId'])
    assert await app.outputs.content_async(record) == b'exact result\n'
    again = await collect(app, sid, ['run', 'run-second'], 'combined', results)
    assert again == combined
    assert len(calls) == 2


async def test_drift_rejected_before_execution_and_after_terminal_preserves_contributors(project):
    app, sid, roots, calls = project
    await validate(app, sid, roots[0])
    (roots[1] / 'source.txt').write_text('other contributor updated\n')
    blocked = await validate(app, sid, roots[1], 'changed')
    assert blocked['result']['phase'] == 'not_started' and len(calls) == 1
    collection = await collect(app, sid, ['run'])
    assert collection['status'] == 'mismatch'
    assert (roots[0] / 'source.txt').read_text() == 'original\n'
    assert (roots[1] / 'source.txt').read_text() == 'other contributor updated\n'


@pytest.mark.parametrize('evidence_case', ['failed', 'incomplete', 'wrong_owner', 'unobserved', 'no_admission'])
async def test_failure_or_missing_native_evidence_cannot_pass(project, evidence_case):
    app, sid, roots, calls = project
    async def control(session_id, action, args):
        if evidence_case != 'no_admission': await admit(app, sid, args['integrationId'], args['command'])
        if evidence_case != 'unobserved': await observed(app, sid, 'p', code=3 if evidence_case == 'failed' else 0, complete=evidence_case != 'incomplete')
        return {'result': {'success': True, 'output': {'process_id': 'p', 'owner_id': 'other' if evidence_case == 'wrong_owner' else 'mount'}}}
    app.runtime.control = control
    await validate(app, sid, roots[0])
    result = await collect(app, sid, ['run'])
    assert result['repositories'][0]['status'] == ('failed' if evidence_case == 'failed' else 'unknown')


async def test_assignment_explicit_member_responsibility_stop_and_delayed_admission(project):
    app, sid, roots, calls = project
    assignment = (await app.dispatch('assignment.start', {'sessionId': sid}))['result']['assignment']
    with pytest.raises(AppError, match='explicitly name'):
        await validate(app, sid, roots[0])
    await app.dispatch('session.create', {'title': 'Independent peer'})
    peer = app._session()['id']
    with pytest.raises(AppError, match='independent'):
        await app.dispatch('assignment.integration', {'sessionId': sid, 'expectedRevision': assignment['revision'], 'responsibleSessionId': peer})
    assignment = (await app.dispatch('assignment.integration', {'sessionId': sid,
        'expectedRevision': assignment['revision'], 'responsibleSessionId': sid}))['result']['assignment']
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.runtime.control
    async def delayed(*values):
        entered.set(); await release.wait(); return await original(*values)
    app.runtime.control = delayed
    await app.dispatch('integration.validate', {'sessionId': sid, 'captureId': 'candidate', 'repositoryPath': str(roots[0]), 'command': 'printf pass'}, command_id='delayed')
    await asyncio.wait_for(entered.wait(), 5)
    # Stop's scope is exercised separately by its canonical tests; here its
    # revision/state change occurs while this command awaits approval.
    assignment['status'] = 'stopped'; assignment['revision'] += 1
    app.assignments.save(assignment); app.db.commit()
    release.set(); await settle(app)
    assert not app.operations.journal.list(sid)
    assert app.assignments.current(app._session(peer)) is None


async def test_restart_lost_ack_and_changed_retry_do_not_replay(project, monkeypatch):
    app, sid, roots, calls = project
    await validate(app, sid, roots[0])
    original = app._save_changes
    def lost(**kwargs):
        original(**kwargs); raise OSError('ack lost after commit')
    monkeypatch.setattr(app, '_save_changes', lost)
    with pytest.raises(AppError, match='ack lost'):
        await collect(app, sid, ['run'], 'lost')
    # First subordinate check may have committed before the collection itself;
    # either retained stage is safe to retry without executing a command.
    monkeypatch.setattr(app, '_save_changes', original)
    result = await collect(app, sid, ['run'], 'lost')
    with pytest.raises(AppError):
        await validate(app, sid, roots[0], command='printf changed')
    directory = app.data_dir
    await app.close()
    restarted = AppService(directory, workspace=roots[0].parent)
    try:
        retry = await restarted.dispatch('integration.validate', {'sessionId': sid, 'captureId': 'candidate', 'repositoryPath': str(roots[0]), 'command': 'printf passed'}, command_id='run')
        assert retry['duplicate']
        read = await restarted.dispatch('integration.read', {'sessionId': sid, 'id': 'lost'})
        assert read['result'] == result
        assert len(calls) == 1
    finally: await restarted.close()


async def test_foreign_agent_read_and_result_mutation_are_refused(project):
    app, sid, roots, calls = project
    await validate(app, sid, roots[0])
    await app.dispatch('session.create', {'title': 'Unrelated'})
    other = app._session()['id']
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch', {'action': 'integration.validate', 'args': {'sessionId': sid, 'captureId': 'candidate', 'repositoryPath': str(roots[0]), 'command': 'printf pass'}}, other)
    with pytest.raises(AppError):
        await app.dispatch('integration.read', {'sessionId': other, 'id': 'run'})


async def test_older_runtime_adapter_is_rejected_before_command_submission(project):
    app, sid, roots, calls = project
    del app.runtime.integration_validation_supported
    await validate(app, sid, roots[0])
    assert calls == []
    assert app.operations.requests.read(sid, 'integration:run')['state'] == 'rejected'


async def test_worker_capability_probe_is_passive_and_requires_current_mounted_worker(monkeypatch):
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web import runtime_worker
    replies = []
    monkeypatch.setattr(runtime_worker, 'publish', replies.append)
    worker = Worker(); worker.acquire_for_mutation = AsyncMock(side_effect=AssertionError('Must not unpark'))
    await worker.command({'op': 'dependencies', 'id': 'cold', 'integrationValidation': True})
    assert replies[-1]['result'] == {'integrationValidation': None}
    worker.controls = object()
    await worker.command({'op': 'dependencies', 'id': 'mounted', 'integrationValidation': True})
    assert replies[-1]['result'] == {'integrationValidation': 'admission-v1'}
    manager = RuntimeManager(); manager._start_locked = AsyncMock(side_effect=AssertionError('Must not start'))
    assert not await manager.integration_validation_supported('cold')
    ready = asyncio.get_running_loop().create_future(); ready.set_result(None)
    row = {'process': SimpleNamespace(returncode=None), 'ready': ready}
    manager.workers['current'] = row
    manager._request = AsyncMock(return_value={'scope': 'older-worker'})
    assert not await manager.integration_validation_supported('current')
    manager._request.return_value = {'integrationValidation': 'admission-v1'}
    assert await manager.integration_validation_supported('current')
    async def replaced(*args, **kwargs):
        manager.workers['current'] = {'process': SimpleNamespace(returncode=None), 'ready': ready}
        return {'integrationValidation': 'admission-v1'}
    manager._request.side_effect = replaced
    assert not await manager.integration_validation_supported('current')
    with pytest.raises(RuntimeError, match='current worker'):
        await manager._admit('current', 'control', {'operation': 'operations.submit', 'arguments': {'integrationId': 'one'}})


async def test_malformed_collection_file_records_incomplete_repair_and_opaque_is_rejected(project):
    app, sid, roots, _ = project
    await validate(app, sid, roots[0])
    result = await collect(app, sid, ['run'], 'malformed-file', [{
        'repositoryPath': str(roots[0]), 'references': ['file:result.txt#sha256=' + '0'*64]}])
    repository = result['repositories'][0]
    assert repository['status'] == 'incomplete'
    assert repository['artifacts'][0]['status'] == 'unsupported'
    assert 'path@sha256:' in repository['artifacts'][0]['detail']
    assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 0
    with pytest.raises(AppError, match='local path@sha256'):
        await collect(app, sid, ['run'], 'opaque-file', [{
            'repositoryPath': str(roots[0]), 'references': ['https://example.test/result']}])


async def test_changed_artifact_stays_incomplete_and_precommit_failure_rolls_back_outputs(project, monkeypatch):
    app, sid, roots, calls = project
    await validate(app, sid, roots[0])
    artifact = roots[0] / 'result.txt'; artifact.write_text('exact')
    results = [{'repositoryPath': str(roots[0]), 'references': ['result.txt@sha256:' + '0'*64]}]
    changed = await collect(app, sid, ['run'], 'changed-artifact', results)
    assert changed['repositories'][0]['status'] == 'incomplete'
    assert changed['repositories'][0]['artifacts'][0]['status'] == 'changed'
    results[0]['references'] = ['result.txt@sha256:' + hashlib.sha256(artifact.read_bytes()).hexdigest()]
    original = app._save_changes
    def fail_collection(**kwargs):
        if app.db.execute('SELECT 1 FROM commands WHERE id=?', ('failed-collection',)).fetchone():
            raise OSError('failure before combined commit')
        return original(**kwargs)
    monkeypatch.setattr(app, '_save_changes', fail_collection)
    with pytest.raises(AppError, match='before combined commit'):
        await collect(app, sid, ['run'], 'failed-collection', results)
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', ('failed-collection',)).fetchone()
    assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 0


async def test_postcommit_collection_ack_preserves_exact_bytes_without_rereading(project, monkeypatch):
    app, sid, roots, calls = project
    await validate(app, sid, roots[0])
    artifact = roots[0] / 'result.txt'; artifact.write_text('retained despite lost acknowledgment')
    results = [{'repositoryPath': str(roots[0]), 'references': ['result.txt@sha256:' + hashlib.sha256(artifact.read_bytes()).hexdigest()]}]
    original = app._save_changes
    def lose_ack(**kwargs):
        is_collection = bool(app.db.execute('SELECT 1 FROM commands WHERE id=?', ('lost-collection',)).fetchone())
        original(**kwargs)
        if is_collection: raise OSError('lost combined response')
    monkeypatch.setattr(app, '_save_changes', lose_ack)
    with pytest.raises(AppError, match='lost combined response'):
        await collect(app, sid, ['run'], 'lost-collection', results)
    artifact.unlink()
    monkeypatch.setattr(app, '_save_changes', original)
    saved = await collect(app, sid, ['run'], 'lost-collection', results)
    record = app.outputs.record(sid, saved['repositories'][0]['artifacts'][0]['outputId'])
    assert await app.outputs.content_async(record) == b'retained despite lost acknowledgment'
    assert len(calls) == 1


@pytest.mark.parametrize('action', ['validate', 'collect'])
@pytest.mark.parametrize('initial_match', [True, False])
async def test_failed_parent_commit_retry_uses_fresh_source_observations(project, monkeypatch, action, initial_match):
    app, sid, roots, calls = project
    if action == 'collect':
        await validate(app, sid, roots[0])
        await validate(app, sid, roots[1], 'second')
    source = roots[0] / 'source.txt'
    if not initial_match:
        source.write_text('different candidate\n')
    identity = 'failed-parent'
    original = app._save_changes
    def fail_parent(**kwargs):
        if app.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone():
            raise OSError('injected before parent commit')
        return original(**kwargs)
    async def retry():
        if action == 'validate':
            return (await validate(app, sid, roots[0], identity))['result']
        return await collect(app, sid, ['run', 'second'], identity)
    monkeypatch.setattr(app, '_save_changes', fail_parent)
    with pytest.raises(AppError, match='before parent commit'):
        await retry()
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone()
    retained_checks = {row[0] for row in app.db.execute("SELECT id FROM commands WHERE json_extract(receipt,'$.commandAction')='integration.check'")}
    assert retained_checks  # Subordinate observations survived the failed parent.
    source.write_text('changed after failed commit\n' if initial_match else 'original\n')
    monkeypatch.setattr(app, '_save_changes', original)
    result = await retry()
    if action == 'validate':
        assert result['beforeStatus'] == ('mismatch' if initial_match else 'matched')
        assert result['phase'] == ('not_started' if initial_match else 'admitting')
        assert result['beforeCheckId'] not in retained_checks
        assert len(calls) == (0 if initial_match else 1)
    else:
        assert result['status'] == ('mismatch' if initial_match else 'passed')
        assert not set(result['checkIds']) & retained_checks
        assert len(calls) == 2
    # Once the parent commits, later changes cannot rewrite its retained result
    # or cause another observation/command on an exact retry.
    saved = app.collaboration.receipt(identity)['result']
    saved_checks = {row[0] for row in app.db.execute("SELECT id FROM commands WHERE json_extract(receipt,'$.commandAction')='integration.check'")}
    saved_calls = len(calls)
    source.write_text('changed after successful parent commit\n')
    assert await retry() == saved
    assert {row[0] for row in app.db.execute("SELECT id FROM commands WHERE json_extract(receipt,'$.commandAction')='integration.check'")} == saved_checks
    assert len(calls) == saved_calls


@pytest.mark.parametrize('change', ['execution', 'stop', 'responsibility'])
async def test_authority_changes_during_source_io_refuse_new_validation(project, monkeypatch, change):
    app, sid, roots, calls = project
    from amplifier_web import integration_evidence
    original = integration_evidence.repository_snapshot
    changed = False
    async def inspect(*values):
        nonlocal changed
        result = await original(*values)
        if not changed:
            changed = True
            if change == 'execution': app._session(sid)['executionRevision'] = 9
            elif change == 'stop': app._session(sid)['interruptionRevision'] = 9
            else:
                assignment = app.assignments.ensure(app._session(sid), 'new-assignment')
                app.assignments.save(assignment)
        return result
    monkeypatch.setattr(integration_evidence, 'repository_snapshot', inspect)
    with pytest.raises(AppError): await validate(app, sid, roots[0])
    assert not calls
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', ('run',)).fetchone()


async def test_concurrent_retries_and_unacknowledged_runtime_never_replay(project):
    app, sid, roots, calls = project
    arguments = {'sessionId': sid, 'captureId': 'candidate', 'repositoryPath': str(roots[0]), 'command': 'printf once'}
    replies = await asyncio.gather(*(app.dispatch('integration.validate', arguments, command_id='once') for _ in range(2)))
    await settle(app)
    assert len(calls) == 1 and sum(row.get('duplicate', False) for row in replies) == 1
    app.runtime.control = AsyncMock(side_effect=RuntimeError('transport lost'))
    await validate(app, sid, roots[1], 'uncertain')
    app.runtime.control.assert_awaited_once()
    assert (await validate(app, sid, roots[1], 'uncertain'))['duplicate']
    app.runtime.control.assert_awaited_once()
    result = await collect(app, sid, ['once', 'uncertain'])
    assert result['status'] == 'partial'
    assert [row['status'] for row in result['repositories']] == ['passed', 'unknown']


async def test_http_agent_parity_without_private_selection_or_unrelated_reads(project, authenticated_client):
    from amplifier_web.server import create_app
    app, sid, roots, calls = project
    directory = app.data_dir
    await app.close()
    runtime = SimpleNamespace(close=AsyncMock(), provenance=AsyncMock(return_value={'status': 'unknown'}), integration_validation_supported=AsyncMock(return_value=True))
    server = await create_app(directory, workspace=roots[0].parent, runtime=runtime,
                              voice=False, background_updates=False, preload_providers=False)
    service = server['service']
    async def control(session, action, args):
        await admit(service, sid, args['integrationId'], args['command'])
        await observed(service, sid, 'http-operation')
        return {'result': {'success': True, 'output': {'process_id': 'http-operation', 'owner_id': 'mount'}}}
    runtime.control = control
    service.management.ensure_runtime = AsyncMock()
    client = await authenticated_client(server)
    await client.post('/api/clients/attach', json={'clientId': 'integration-terminal', 'kind': 'tui'})
    headers = {'X-Amplifier-Client': 'integration-terminal'}
    endpoint = f'/api/sessions/{sid}/commands'
    body = {'id': 'http-validation', 'action': 'integration.validate', 'args': {'captureId': 'candidate', 'repositoryPath': str(roots[0]), 'command': 'printf fixture'}}
    response = await client.post(endpoint, json=body, headers=headers)
    assert response.status == 200, await response.text()
    await settle(service)
    response = await client.post(endpoint, json={'id': 'http-collection', 'action': 'integration.collect', 'args': {'captureId': 'candidate', 'validationIds': ['http-validation']}}, headers=headers)
    assert response.status == 200, await response.text()
    saved = (await response.json())['result']
    agent = await service.app_bridge('dispatch', {'action': 'integration.read', 'args': {'sessionId': sid, 'id': 'http-collection'}}, sid)
    assert agent['result'] == saved
    assert saved['repositories'][0]['status'] == 'passed'
    await client.close()


async def test_real_native_shell_command_and_postapproval_stop(project, monkeypatch):
    bash = pytest.importorskip('amplifier_module_tool_bash')
    pytest.importorskip('amplifier_module_loop_live')
    from amplifier_web.runtime_controls import RuntimeControls
    app, sid, roots, calls = project
    tool = bash.BashTool({'managed_processes': True, 'safety_profile': 'unrestricted'})
    tool._processes.observer = lambda: lambda event: app.operations.observe(sid, sid, event)
    stop_after_approval = False
    async def hook(name, data):
        if name == 'tool:pre' and stop_after_approval:
            app._session(sid)['interruptionRevision'] = 1
        return SimpleNamespace(action='continue')
    coordinator = SimpleNamespace(get=lambda name: {'bash': tool} if name == 'tools' else None,
        get_capability=lambda name: (lambda identity, command: admit(app, sid, identity, command)) if name == 'integration.admit' else None,
        hooks=SimpleNamespace(emit=hook), process_hook_result=AsyncMock(side_effect=lambda value,*_:value))
    controls = object.__new__(RuntimeControls); controls.coordinator = coordinator; controls.session = SimpleNamespace(session_id=sid)
    async def control(session_id, action, args):
        return await controls.perform(action, args)
    app.runtime.control = control
    async def terminal(identity):
        async with asyncio.timeout(5):
            while True:
                changed = app.operations.changed
                row = app.operations.status(sid, identity)
                if row['state'] in {'completed', 'failed'} and row.get('outputComplete') and row.get('captureComplete'):
                    return row
                await changed.wait()
    try:
        # Explicit source copies are also the subprocess import origins. -I and
        # -B prevent inherited PYTHONPATH and bytecode writes changing evidence.
        script = "import sys,json; from pathlib import Path; sys.path[:0]=" + repr([str(root) for root in roots]) + "; import first_dependency as a, second_dependency as b; assert (a.VALUE,b.VALUE)==('first','second'); assert [str(Path(m.__file__).resolve()) for m in (a,b)]==" + repr([str(root / (root.name + '_dependency.py')) for root in roots]) + "; print(json.dumps({'imports':[a.__file__,b.__file__]})); Path('result.txt').write_text('native-result')"
        await validate(app, sid, roots[0], command=shlex.quote(sys.executable) + ' -I -B -c ' + shlex.quote(script))
        request = app.operations.requests.read(sid, 'integration:run')
        assert request['state'] == 'accepted', request
        await tool.execute({'action': 'wait', 'process_id': request['operationId'], 'wait_ms': 3000})
        await terminal(request['operationId'])
        result = await collect(app, sid, ['run'])
        assert result['repositories'][0]['status'] == 'passed', result
        assert (roots[0] / 'result.txt').read_text() == 'native-result'
        stop_after_approval = True
        await validate(app, sid, roots[1], 'stopped', command='printf unsafe > result.txt')
        assert not (roots[1] / 'result.txt').exists()
        stop_after_approval = False
        # This explicit fixture command intentionally demonstrates a failed
        # combination. The product never constructs or runs a merge by itself.
        root = roots[0]; original_branch = git(root, 'branch', '--show-current')
        git(root, 'checkout', '-qb', 'contributor')
        (root / 'source.txt').write_text('contributor change\n'); git(root, 'commit', '-qam', 'Contributor')
        git(root, 'checkout', '-q', original_branch)
        (root / 'source.txt').write_text('integrator change\n'); git(root, 'commit', '-qam', 'Integrator')
        await app.dispatch('integration.capture', {'sessionId': sid, 'repositories': [
            {'repositoryPath': str(path), 'sourceRevision': app.worktrees.git.inspect(path)['sourceRevision']} for path in roots],
            'dependencies': []}, command_id='conflicting-candidate')
        await app.dispatch('integration.validate', {'sessionId': sid, 'captureId': 'conflicting-candidate',
            'repositoryPath': str(root), 'command': 'git merge --no-commit contributor'}, command_id='conflicting-command')
        await settle(app)
        failed = app.operations.requests.read(sid, 'integration:conflicting-command')
        assert failed['state'] == 'accepted'
        await tool.execute({'action': 'wait', 'process_id': failed['operationId'], 'wait_ms': 3000})
        await terminal(failed['operationId'])
        conflict = (await app.dispatch('integration.collect', {'sessionId': sid, 'captureId': 'conflicting-candidate',
            'validationIds': ['conflicting-command']}, command_id='conflicting-result'))['result']
        assert conflict['candidateStatus'] == 'mismatch'
        assert conflict['repositories'][0]['operation']['returncode'] == 1
        assert 'UU source.txt' in git(root, 'status', '--porcelain')
        assert git(root, 'show', 'HEAD:source.txt') == 'integrator change'
        assert git(root, 'show', 'contributor:source.txt') == 'contributor change'
        assert (roots[1] / 'source.txt').read_text() == 'original\n'
    finally: await tool.close()


@pytest.mark.parametrize('bridge', [False, True])
@pytest.mark.parametrize('action', ['integration.validate', 'integration.collect'])
async def test_validation_authority_and_collection_do_not_hydrate_history(project, monkeypatch, action, bridge):
    app, sid, roots, calls = project
    await validate(app, sid, roots[0])
    arguments = {'sessionId': sid, 'captureId': 'candidate'}
    if action == 'integration.validate':
        # A drifted candidate must be rejected before any execution is started,
        # including when the saved chat's display/transcript has gone cold.
        (roots[1] / 'source.txt').write_text('changed since capture\n')
        arguments.update(repositoryPath=str(roots[1]), command='printf should-not-run')
    else:
        arguments['validationIds'] = ['run']
    original = app._session
    metadata_reads = []
    def metadata_only(identity=None, *, hydrate=True):
        assert not hydrate, 'Integration authority must not load cold conversation bodies'
        metadata_reads.append(identity)
        return original(identity, hydrate=False)
    with monkeypatch.context() as patch:
        patch.setattr(app, '_session', metadata_only)
        patch.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('History loaded')))
        patch.setattr(app.cold_display, 'hydrate', lambda *_: pytest.fail('Cold display hydrated'))
        if bridge:
            value = await app.app_bridge('dispatch', {'action': action, 'args': arguments,
                'id': 'metadata-result', '_runtimeSessionId': sid}, sid)
        else:
            value = await app.dispatch(action, arguments, command_id='metadata-result')
        assert value['accepted'] and metadata_reads and set(metadata_reads) == {sid}
        if action == 'integration.validate':
            assert value['result']['phase'] == 'not_started'
            assert value['result']['status'] == 'mismatch'
        else:
            assert [row['status'] for row in value['result']['repositories']] == ['passed', 'not_validated']
        assert len(calls) == 1  # Neither path launched another command.
