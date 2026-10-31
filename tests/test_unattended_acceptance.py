"""One unattended journey with native tools and real local test subprocesses.

Peer delivery and the Docker protocol boundary are controlled fixtures. Processes,
source snapshots, stdout, archived files, native actors and app receipts are real.
This is not installed-provider or physical nested-container qualification.
"""
import asyncio
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import task_environments as environments
from amplifier_web.service import AppService
from test_collaborative_workspaces import Runtime, generation, settled, until
from test_file_source_native_children import execute_tool
from test_full_child_configuration import dispatch


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class LocalChecks:
    """Controlled lifecycle protocol; every check is an actual owned process."""

    def __init__(self, root):
        self.root, self.jobs, self.calls, self.archives = root, {}, [], []

    def invoke(self, config, home, row, action, args):
        self.calls.append((action, row['id'], args.get('requestId')))
        directory = self.root / row['id']
        # This identity explicitly names a fixture protocol peer, not a daemon.
        result = {'ok': True, 'universeId': row['id'], 'status': 'running',
                  'backendIdentity': {'backend': 'docker', 'daemon': 'fixture-local-process-no-container'}}
        if action == 'prepare':
            directory.mkdir(parents=True)
            shutil.copytree(row['source'], directory / 'source')
            archive = directory / 'source.tar'
            with tarfile.open(archive, 'w') as output:
                output.add(directory / 'source', arcname='source')
            result['snapshot'] = {'snapshot_commit': subprocess.check_output(
                ['git', '-C', row['source'], 'rev-parse', 'HEAD'], text=True).strip(),
                'archive_sha256': digest(archive)}
        elif action == 'execute':
            identity = args['requestId']
            assert identity not in self.jobs, 'An exact admitted command must never replay'
            stdout, stderr = directory / 'stdout.txt', directory / 'stderr.txt'
            with stdout.open('wb') as out, stderr.open('wb') as err:
                process = subprocess.Popen(args['command'], shell=True, cwd=directory / 'source',
                                           stdout=out, stderr=err, start_new_session=True)
            self.jobs[identity] = {'process': process, 'directory': directory, 'cancelled': False}
            return self.observe(identity)
        elif action in {'job_read', 'job_cancel'}:
            identity = args['requestId']
            if action == 'job_cancel':
                import os
                import signal
                job = self.jobs[identity]
                if job['process'].poll() is None:
                    os.killpg(job['process'].pid, signal.SIGTERM)
                    job['process'].wait(timeout=5)
                    job['cancelled'] = True
            return self.observe(identity)
        elif action == 'stop':
            assert all(job['process'].poll() is not None for job in self.jobs.values()
                       if job['directory'] == directory)
            result['status'] = 'stopped'
        elif action == 'remove':
            archive = directory / 'retained.tar'
            paths = [directory / name for name in ('source', 'stdout.txt', 'stderr.txt')]
            with tarfile.open(archive, 'w') as output:
                for path in paths:
                    output.add(path, arcname=path.name)
            with tarfile.open(archive) as output:
                assert output.extractfile('stdout.txt').read() == (directory / 'stdout.txt').read_bytes()
            result.update(status='removed', retention={'verified': True, 'path': str(archive),
                'sha256': digest(archive), 'bytes': archive.stat().st_size})
            self.archives.append(result['retention'])
            shutil.rmtree(directory / 'source')
        elif action != 'start':
            raise AssertionError('Unexpected lifecycle action: ' + action)
        return result

    def observe(self, identity):
        job = self.jobs[identity]
        code = job['process'].poll()
        state = 'running' if code is None else 'cancelled' if job['cancelled'] else 'passed' if code == 0 else 'failed'
        return {'ok': True, 'status': 'running', 'job': {'version': 1, 'id': identity,
            'state': state, 'terminal': code is not None}, 'result': {'exit_code': code,
            'stdout': (job['directory'] / 'stdout.txt').read_text(),
            'stderr': (job['directory'] / 'stderr.txt').read_text()}}

    def close(self):
        for identity, job in self.jobs.items():
            if job['process'].poll() is None:
                self.invoke({}, None, {'id': job['directory'].name}, 'job_cancel', {'requestId': identity})


@pytest.fixture
async def journey(tmp_path, monkeypatch):
    from amplifier_web.management import Management
    project = tmp_path / 'project'
    project.mkdir()
    subprocess.run(['git', 'init', '-q', str(project)], check=True)
    subprocess.run(['git', '-C', str(project), '-c', 'user.name=Fixture', '-c',
                    'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'Synthetic baseline'], check=True)
    runtime = Runtime()
    app = runtime.app = AppService(tmp_path / 'app', runtime, workspace=project)
    app.management = Management(app)
    current = (await app.dispatch('access.get', {'scope': 'app'}))['result']
    await app.dispatch('access.set', {'scope': 'app', 'mode': 'full',
        'expectedRevision': current['revision']}, command_id='operator-full-once')
    builder, initiator, reviewer = [app._new_session({'title': title}) for title in ('Builder', 'System requester', 'Reviewer')]
    app.state['sessions'] = [builder, initiator, reviewer]
    for row in app.state['sessions']:
        row.update(workingDirectory=str(project), executionRevision=0, interruptionRevision=0)
    app._publish()
    checks = LocalChecks(tmp_path / 'physical-checks')
    config = {'python': sys.executable, 'dockerHost': 'unix:///fixture-local-process-no-container',
              'image': environments.APPROVED_IMAGE}
    monkeypatch.setattr(environments, 'configuration', lambda app: (config, 'ready', 'Fixture local subprocess backend'))
    monkeypatch.setattr(environments.setup, 'read', AsyncMock(return_value={'status': 'ready', 'backends': [], 'operation': None}))
    monkeypatch.setattr(environments, 'capacity', lambda *a: {'available': True, 'counts': {'active': 0, 'stopped_retained': 0}})
    monkeypatch.setattr(environments, 'invoke', checks.invoke)
    monkeypatch.setattr(app.test_queue, 'start', lambda: None)
    app.test_queue.poll_seconds = .02
    context = tmp_path / 'shared-context.txt'
    context.write_text('Strip surrounding spaces and normalize uppercase text to lowercase.\n')
    value = SimpleNamespace(app=app, builder=builder, initiator=initiator, reviewer=reviewer,
                            project=project, context=context, checks=checks, mounts=[], trace=[], review_deliveries=[])
    try:
        yield value
    finally:
        checks.close()
        if destination := os.environ.get('UNATTENDED_ACCEPTANCE_EVIDENCE'):
            evidence = Path(destination)
            evidence.mkdir(parents=True, exist_ok=True)
            for archive in checks.archives:
                retained = evidence / (tmp_path.name + '-' + archive['sha256'][:12] + '.tar')
                shutil.copyfile(archive['path'], retained)
                assert digest(retained) == archive['sha256']
                archive['evidenceCopy'] = str(retained)
            rows = [json.loads(row[0]) for row in app.db.execute("SELECT receipt FROM commands WHERE "
                "json_extract(receipt,'$.commandAction') IN ('workspace.tests.submit','coordination.send','coordination.reply')")]
            (evidence / (tmp_path.name + '.json')).write_text(json.dumps({'trace': value.trace,
                'reviewDeliveries': value.review_deliveries,
                'receipts': rows, 'physicalCalls': checks.calls, 'retainedArchives': checks.archives,
                'processesExited': all(job['process'].poll() is not None for job in checks.jobs.values()),
                'limits': 'Controlled lifecycle/peer transports; actual owned local processes and native mounts; no live provider or nested-container proof.'}, indent=2))
        for mount in reversed(value.mounts):
            await mount.parent.cleanup()
        await app.close()


async def mount(j, source, tmp_path, monkeypatch):
    """Use Core/Foundation and the actual trusted host bridge for every actor."""
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime as NativeRuntime
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.host.children import install_children
    from amplifier_web.host.config import read_config
    from amplifier_web.host.session import _apply_host_policy
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.runtime_worker import Worker

    spec = importlib.util.spec_from_file_location('unattended_native_' + source['id'],
        Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            'tools': [{'module': 'tool-filesystem'}], 'agents': {'worker': {'instruction': 'Perform the delegated step.'}}}
    bundle = Bundle(name='unattended-fixture', **plan)
    _apply_host_policy(bundle, read_config(source['workspace'], home=j.app.data_dir, session_id=source['id']))
    plan['tools'] = copy.deepcopy(bundle.tools)
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple', 'tool-filesystem')}
    prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths), bundle)
    ask = AsyncMock(side_effect=AssertionError('Standing Full requested human approval'))
    approvals = Approvals(None, ask)
    parent = await prepared.create_session(session_id=source['id'], approval_system=approvals)
    parent.coordinator.register_capability('session.working_dir', source['workingDirectory'])
    active = source['collaborationGeneration']
    worker = Worker()
    worker.session = parent
    worker.start_config = {key: source.get(key, 0) for key in
                           ('interruptionRevision', 'executionRevision', 'configurationPendingRevision')}
    worker.runtime = SimpleNamespace(session_id=source['id'], generation={'id': active['id'], 'input_ids': list(active['inputIds'])},
                                     closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.context_inputs = list(active['inputIds'])
    worker.context_bindings = {identity: {'clientId': None, 'targets': []} for identity in active['inputIds']}
    manager = RuntimeManager()
    manager.app_bridge = j.app.app_bridge
    owned = {'process': SimpleNamespace(returncode=None), 'runtimeOwnerId': 'fixture-' + source['id']}
    manager.workers[source['id']] = owned
    if not hasattr(j.app.runtime, 'workers'):
        j.app.runtime.workers = {}
    j.app.runtime.workers[source['id']] = owned
    responses = {}
    async def write(row, response):
        assert row is owned
        responses[response['id']] = response
    manager._write = write
    async def bridge(operation, args):
        identity = str(len(j.trace))
        trace = {'owner': source['id'], 'actor': args.get('_runtimeSessionId'),
                 'operation': operation, 'action': args.get('action'), 'generation': args.get('_generationId')}
        j.trace.append(trace)
        await manager._bridge(source['id'], owned, {'id': identity, 'operation': operation, 'args': args})
        result = responses.pop(identity)
        trace['replyError'] = result.get('error')
        if result.get('error'):
            raise RuntimeError(result['error'])
        return result['result']
    worker.bridge = bridge
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))
    store = SessionStore(tmp_path / ('native-' + source['id']))
    async def checkpoint(*args, **kwargs):
        return store.save(source['id'], await parent.coordinator.get('context').get_messages(),
                          {'session_id': source['id'], 'working_dir': source['workingDirectory']})
    parent.coordinator.register_capability('live.checkpoint', checkpoint)
    worker.install_collaboration_checkpoint(parent.coordinator)
    registry = await install_children(parent, prepared, NativeRuntime(source['id']), store, approvals)
    actions, failures = {}, []
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        try:
            await context.add_message({'role': 'user', 'content': prompt})
            result = await actions[coordinator.session_id](coordinator)
            await context.add_message({'role': 'assistant', 'content': result})
            return result
        except Exception as exc:
            failures.append(exc)
            raise
    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', execute)
    value = SimpleNamespace(parent=parent, worker=worker, registry=registry, actions=actions,
                            source=source, ask=ask, failures=failures, store=store)
    j.mounts.append(value)
    return value


async def invoke(m, action, args, identity, coordinator=None):
    result = await dispatch((coordinator or m.parent.coordinator).get('tools')['app_control'], action, args, identity)
    assert result.success, result.error
    return result.output


async def child(m, name, operation, *, resume=False, prompt='Complete the assigned step'):
    m.actions[name] = operation
    if resume:
        result = await m.registry.resume(name, prompt, m.parent)
    else:
        result = await m.registry.spawn('worker', prompt, m.parent, sub_session_id=name)
    if m.failures:
        raise m.failures.pop(0)
    return result


def scope(j, source):
    workspace = next(row for row in j.app.state['workspaces'] if row['path'] == source['workspace'])
    return {'workspaceId': workspace['id'], 'sessionId': source['id']}


async def check(j, m, identity, command):
    args = {**scope(j, m.source), 'requestId': identity, 'profileId': environments.PROFILE,
            'command': command, 'notify': False}
    await invoke(m, 'workspace.tests.submit', args, identity)
    await j.app.test_queue.tick()
    await asyncio.gather(*j.app.test_queue.tasks.values())
    return (await invoke(m, 'workspace.tests.result', {**scope(j, m.source), 'requestId': identity}, 'read-' + identity))['result']


async def finish(j, m, text):
    # Terminal attribution comes from the actual persisted native assistant row.
    async def complete(coordinator):
        return text
    m.actions[m.source['id']] = complete
    assert await m.parent.execute('Finish the current peer request') == text
    await m.parent.coordinator.get_capability('live.checkpoint')('completed')
    anchor = m.worker.collaboration_terminal
    assert anchor['nativeText'] == text
    current = m.source['collaborationGeneration']
    await j.app.on_runtime_event('runtime.generation', {'sessionId': m.source['id'], 'rootSessionId': m.source['id'],
        'event': 'generation.finished', 'generation_id': current['id'], 'input_ids': current['inputIds'],
        'text': text, 'nativeTerminal': anchor, 'disposition': 'manager_turn_finished', 'active_job_ids': []})


async def initiate(j, tmp_path, monkeypatch):
    await j.app.dispatch('conversation.send', {'sessionId': j.initiator['id'],
        'text': 'Coordinate implementation of normalization and obtain peer review.'},
        origin='system', caller_session_id=j.initiator['id'], command_id='system-initiation')
    message = next(row for row in j.initiator['messages'] if row.get('inputId'))
    assert message['inputOrigin'] == 'system'
    await generation(j.app, j.initiator, 'system-requester-turn', [message['inputId']])
    initiator = await mount(j, j.initiator, tmp_path, monkeypatch)
    await invoke(initiator, 'coordination.send', {'sessionId': j.builder['id'],
        'text': 'Implement normalization and obtain peer review.', 'mode': 'queue'}, 'system-task')
    await settled(j.app, 'system-task', 'accepted')
    await generation(j.app, j.builder, 'builder-system-turn', ['system-task'])
    assert j.app.collaboration.receipt('system-task')['origin'] == 'agent'


async def test_unattended_native_build_test_review_correct_and_exact_delivery(journey, tmp_path, monkeypatch):
    j = journey
    await initiate(j, tmp_path, monkeypatch)
    builder = await mount(j, j.builder, tmp_path, monkeypatch)
    async def first(coordinator):
        result = await execute_tool(coordinator, 'read_file', {'file_path': str(j.context)})
        assert result.success and 'lowercase' in json.dumps(result.output)
        assert (await execute_tool(coordinator, 'write_file', {
            'file_path': str(tmp_path / 'shared-notes.txt'),
            'content': 'Standing Full child can write the explicitly chosen shared artifact folder.'})).success
        for name, content in {'normalize.py': 'def normalize(text):\n    return text.strip()\n',
            'check.py': "from normalize import normalize\nassert normalize(' hello ') == 'hello'\nprint('trim check passed')\n"}.items():
            assert (await execute_tool(coordinator, 'write_file', {'file_path': str(j.project / name), 'content': content})).success
        return 'Initial candidate written; lowercase handling still needs review.'
    await child(builder, 'implementation-child', first)
    assert (tmp_path / 'shared-notes.txt').is_file()
    first_result = await check(j, builder, 'first-check', sys.executable + ' check.py')
    assert first_result['exitCode'] == 0 and 'trim check passed' in first_result['text']
    assert first_result['check']['status'] == 'passed'
    await invoke(builder, 'coordination.send', {'sessionId': j.reviewer['id'],
        'text': 'Review normalize.py against the exact shared context and test output.', 'mode': 'queue'}, 'review-request')
    await settled(j.app, 'review-request', 'accepted')
    await generation(j.app, j.reviewer, 'review-turn', ['review-request'])
    reviewer = await mount(j, j.reviewer, tmp_path, monkeypatch)
    async def inspect(coordinator):
        actual = await execute_tool(coordinator, 'read_file', {'file_path': str(j.project / 'normalize.py')})
        requirement = await execute_tool(coordinator, 'read_file', {'file_path': str(j.context)})
        assert actual.success and requirement.success
        assert 'casefold' not in json.dumps(actual.output) and 'lowercase' in json.dumps(requirement.output)
        text = 'Correction required: use casefold after strip; test uppercase input.'
        assert (await execute_tool(coordinator, 'write_file', {'file_path': str(j.project / 'review.txt'), 'content': text})).success
        return text
    reviewer.actions[j.reviewer['id']] = inspect
    review_text = await reviewer.parent.execute('Review the actual source')
    reference = 'review.txt@sha256:' + digest(j.project / 'review.txt')
    reply = await invoke(reviewer, 'coordination.reply', {'requestId': 'review-request', 'kind': 'result',
        'outcome': 'success', 'text': review_text, 'references': [reference]}, 'review-declaration')
    assert reply['result']['artifactVerification'] == 'verified'
    await finish(j, reviewer, review_text)
    reviewed = await invoke(builder, 'coordination.artifact', {'sessionId': j.builder['id'],
        'requestId': 'review-request', 'reference': reference}, 'read-review')
    assert reviewed['result']['qualified'] and reviewed['result']['text'] == review_text
    review_prompt = f"Apply this verified peer review ({reference}):\n{reviewed['result']['text']}"
    async def correction(coordinator):
        messages = await coordinator.get('context').get_messages()
        delivered = next(row['content'] for row in reversed(messages) if row['role'] == 'user')
        assert delivered == review_prompt
        assert reference in delivered and reviewed['result']['text'] in delivered
        j.review_deliveries.append({'actor': coordinator.session_id, 'reference': reference,
                                    'nativeInput': delivered})
        for name, content in {'normalize.py': 'def normalize(text):\n    return text.strip().casefold()\n',
            'check.py': "from normalize import normalize\nassert normalize(' HELLO ') == 'hello'\nprint('uppercase and trim checks passed')\n"}.items():
            assert (await execute_tool(coordinator, 'read_file', {'file_path': str(j.project / name)})).success
            assert (await execute_tool(coordinator, 'write_file', {'file_path': str(j.project / name), 'content': content})).success
        return 'Applied the actual peer correction.'
    await child(builder, 'implementation-child', correction, resume=True, prompt=review_prompt)
    corrected = await check(j, builder, 'corrected-check', sys.executable + ' check.py')
    assert corrected['exitCode'] == 0 and 'uppercase and trim checks passed' in corrected['text']
    result_file = j.project / 'result.txt'
    assert (await execute_tool(builder.parent.coordinator, 'write_file', {
        'file_path': str(result_file), 'content': corrected['text']})).success
    final_refs = [name + '@sha256:' + digest(j.project / name) for name in ('normalize.py', 'result.txt')]
    delivery_args = {'requestId': 'system-task', 'kind': 'result', 'outcome': 'success',
                     'text': 'Corrected source and actual passing test output retained.', 'references': final_refs}
    delivered = await invoke(builder, 'coordination.reply', delivery_args, 'final-delivery')
    frozen = copy.deepcopy(delivered['result'])
    assert frozen['artifactVerification'] == 'verified' and len(frozen['artifacts']) == 2
    await finish(j, builder, delivery_args['text'])
    repeated = await invoke(builder, 'coordination.reply', delivery_args, 'final-delivery')
    assert repeated['duplicate'] and repeated['result']['artifacts'] == frozen['artifacts']
    result_file.unlink()
    observed = await j.app.dispatch('coordination.artifact', {'sessionId': j.initiator['id'],
        'requestId': 'system-task', 'reference': final_refs[1]},
        origin='system', caller_session_id=j.initiator['id'])
    assert observed['result']['qualified'] and observed['result']['text'] == corrected['text']
    assert len(j.checks.jobs) == 2 and len(j.checks.archives) == 2
    for archive in j.checks.archives:
        assert digest(archive['path']) == archive['sha256']
    assert all(not row['approvals'] for row in j.app.state['sessions']) and not j.app.clients.records
    from amplifier_web.schedule_handoff import latest_human
    assert all(latest_human(row) is None for row in j.app.state['sessions'])
    assert all(message.get('inputOrigin') not in {'ui', 'user'}
               for row in j.app.state['sessions'] for message in row['messages'] if message.get('role') == 'user')
    assert any(row['actor'] == 'implementation-child' for row in j.trace)
    assert all(job['process'].poll() is not None for job in j.checks.jobs.values())


async def test_native_child_reads_actual_completed_test_receipt(journey, tmp_path, monkeypatch):
    j = journey
    await initiate(j, tmp_path, monkeypatch)
    builder = await mount(j, j.builder, tmp_path, monkeypatch)
    completed = await check(j, builder, 'observed-check', sys.executable + " -c 'print(42)'")
    assert completed['exitCode'] == 0 and completed['text'] == '42\n'
    observations = []
    async def observe(coordinator):
        for action, extra in [('read', {}), ('result', {'requestId': 'observed-check'})]:
            observations.append(await dispatch(coordinator.get('tools')['app_control'], 'workspace.tests.' + action,
                {**scope(j, j.builder), **extra}, 'child-observe-' + action))
        return 'Recorded the actual owner responses; no command replayed.'
    await child(builder, 'result-observer-child', observe)
    assert len(j.checks.jobs) == 1
    # Validate every successful response before classifying any known refusal:
    # a partial owner fix must not hide a stale or malformed successful result.
    if observations[0].success:
        assert observations[0].output['result'] == {'items': [completed['check']], 'nextOffset': None}
    if observations[1].success:
        assert observations[1].output['result'] == completed
    refusals = [response for response in observations if not response.success]
    if refusals:
        assert all('A child cannot borrow' in str(response.error) for response in refusals), refusals
        pytest.xfail('Verified baseline gap: actual Full child cannot read saved workspace.tests.read/result; multi-worker owns the pending fix. This is not full unattended acceptance.')


async def test_native_root_cancels_real_active_check_and_observes_without_replay(journey, tmp_path, monkeypatch):
    j = journey
    await initiate(j, tmp_path, monkeypatch)
    builder = await mount(j, j.builder, tmp_path, monkeypatch)
    command = sys.executable + " -u -c 'import time; print(42); time.sleep(30)'"
    args = {**scope(j, j.builder), 'requestId': 'active-check', 'profileId': environments.PROFILE,
            'command': command, 'notify': False}
    await invoke(builder, 'workspace.tests.submit', args, 'active-check')
    await j.app.test_queue.tick()
    await until(lambda: bool(j.checks.jobs))
    job = next(iter(j.checks.jobs.values()))
    await until(lambda: '42' in (job['directory'] / 'stdout.txt').read_text())
    assert job['process'].poll() is None
    cancel = await invoke(builder, 'workspace.tests.cancel', {**scope(j, j.builder), 'requestId': 'active-check'}, 'cancel-active')
    assert cancel['result']['cancelRequested']
    await asyncio.wait_for(asyncio.gather(*j.app.test_queue.tasks.values()), 5)
    result = (await invoke(builder, 'workspace.tests.result', {**scope(j, j.builder),
        'requestId': 'active-check'}, 'cancel-result'))['result']
    assert result['check']['status'] == 'cancelled'
    assert result['exitCode'] != 0 and result['text'] == '42\n'
    assert job['process'].poll() is not None and len(j.checks.archives) == 1
    assert (await invoke(builder, 'workspace.tests.submit', args, 'repeat-active'))['duplicate']
    assert len(j.checks.jobs) == 1


async def test_queued_native_check_fences_stop_before_effect(journey, tmp_path, monkeypatch):
    j = journey
    await initiate(j, tmp_path, monkeypatch)
    builder = await mount(j, j.builder, tmp_path, monkeypatch)
    args = {**scope(j, j.builder), 'requestId': 'waiting-check', 'profileId': environments.PROFILE,
            'command': sys.executable + " -c 'print(42)'", 'notify': False}
    await invoke(builder, 'workspace.tests.submit', args, 'waiting-check')
    await j.app.dispatch('conversation.stop', {'sessionId': j.builder['id']})
    await j.app.test_queue.tick()
    await asyncio.gather(*j.app.test_queue.tasks.values())
    receipt = j.app.test_queue.get(j.app.test_queue.identity(j.builder['id'], 'waiting-check'))
    assert receipt['job']['status'] == 'cancelled' and not j.checks.calls and not j.checks.jobs
    # A new transport ID cannot turn this cancelled accepted check into work.
    observed = await j.app.dispatch('workspace.tests.result', {**scope(j, j.builder), 'requestId': 'waiting-check'})
    assert observed['result']['check']['status'] == 'cancelled' and observed['result']['exitCode'] is None


async def test_captured_native_full_configuration_is_revoked_before_commit(journey, tmp_path, monkeypatch):
    from amplifier_web.preferences import SettingsStore
    j = journey
    await initiate(j, tmp_path, monkeypatch)
    builder = await mount(j, j.builder, tmp_path, monkeypatch)
    before = copy.deepcopy(SettingsStore(j.app.data_dir).read(j.builder['workspace'], 'local'))
    await j.app.management.lock.acquire()
    try:
        result = await invoke(builder, 'permissions.save', {'sessionId': j.builder['id'],
            'scope': 'local', 'allowed': [str(tmp_path)], 'denied': []}, 'captured-file-setting')
        assert result['accepted']
        current = (await j.app.dispatch('access.get', {'scope': 'app'}))['result']
        await j.app.dispatch('access.set', {'scope': 'app', 'mode': 'ask', 'expectedRevision': current['revision']})
    finally:
        j.app.management.lock.release()
    await until(lambda: 'captured-file-setting' in j.app.state.get('managementResults', {}))
    receipt = j.app.state['managementResults']['captured-file-setting']
    assert receipt['phase'] == 'error' and 'Full access changed' in receipt['error']
    assert SettingsStore(j.app.data_dir).read(j.builder['workspace'], 'local') == before
