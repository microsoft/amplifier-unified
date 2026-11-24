"""Thin Docker contract and opt-in real maintained Smart Tool integration."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile
import io
import asyncio
import time
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

from amplifier_web import managed_runner_updates as updates
from amplifier_web import task_environments as environments
from amplifier_web import test_environment_configs as configs
from amplifier_web import test_environment_profiles as profiles
from amplifier_web import test_environment_setup as setup
from amplifier_web import test_environment_worker as worker


def installation(home, identity, commit):
    python = home / 'smart-tools/installs' / identity / 'venv/bin/python'
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    return {'id': identity, 'commit': commit, 'python': str(python),
            'repository': environments.REPOSITORY, 'status': 'installed',
            'extras': [], 'path': '.', 'ref': 'main'}


def test_docker_ordinary_update_lineage_and_retained_old_owner(tmp_path, monkeypatch):
    old, new = installation(tmp_path, 'a' * 24, 'a' * 40), installation(tmp_path, 'b' * 24, 'b' * 40)
    app = SimpleNamespace(data_dir=tmp_path,
        smart_tools=SimpleNamespace(state={'installations': [old, new], 'servers': []}))
    raw = {'enabled': True, 'installationId': old['id'], 'commit': old['commit'],
           'dockerHost': 'unix:///owned.sock', 'runnerQualificationRequired': True}
    old['runnerCandidate'] = True  # Old certification metadata is inert.
    setup.write_json(tmp_path / 'test-environments.json', raw)
    setup.write_json(setup.root(app) / ('a' * 32 + '.json'), {
        'id': 'a' * 32, 'state': 'completed', 'backend': 'docker', 'priorConfiguration': None,
        'installationId': old['id'], 'candidateConfiguration': raw,
        'runnerSourcePolicy': {'version': 1, 'repository': environments.REPOSITORY, 'ref': 'main'},
        'proof': {'configurationReady': True}})
    monkeypatch.setattr(updates, 'runtime_identity', lambda *a: pytest.fail('runtime inventory'))
    monkeypatch.setattr(updates, 'trusted_installation', lambda *a: pytest.fail('certification gate'))
    original = environments.validate_configuration_value(app, raw)[0]
    key = configs.retain(tmp_path, original)
    old['supersededBy'], new['previousInstallationId'] = new['id'], old['id']
    current, status, _ = environments.local_configuration(app)
    assert status == 'ready' and current['installationId'] == new['id']
    from amplifier_web import test_capacity_settings
    assert test_capacity_settings.execution_key(current) == test_capacity_settings.execution_key(original)
    assert configs.resolve(app, {'backendKey': key, 'backendConfigRetained': True}, current) == original
    assert setup.read_json(tmp_path / 'test-environments.json') == raw
    inventory = updates.update_sources(app)
    assert inventory[0]['installationId'] == new['id'] and 'runnerKind' not in inventory[0]
    assert updates.referenced(tmp_path, old['id'])


async def test_retained_docker_certification_cannot_restart_or_promote(tmp_path, monkeypatch):
    app = SimpleNamespace(data_dir=tmp_path)
    record = {'id': 'a' * 32, 'candidate': {'backend': 'docker'}, 'state': 'qualifying',
              'resourceId': 'retained-unknown'}
    path = updates.record_path(app, record['id'])
    setup.write_json(path, record)
    from amplifier_web import managed_runner_qualification
    monkeypatch.setattr(managed_runner_qualification, 'advance', lambda *a: pytest.fail('probe revived'))
    monkeypatch.setattr(updates, 'runtime_identity', lambda *a: pytest.fail('inventory'))
    with pytest.raises(updates.RunnerUpdateError):
        await updates.advance(app, record['id'])
    assert setup.read_json(path) == record


def test_default_network_and_disposable_copy_retention_are_explicit():
    options = profiles.compose_options({})
    assert options == {'composeFile': None, 'offline': False, 'preserveSource': False}
    selected = worker.profile(environments.APPROVED_IMAGE, {
        **options, 'rootOwner': 'owner', 'operationId': 'operation', 'profileId': 'app-preview'})
    assert selected['networks']['default']['internal'] is False
    assert selected['services']['twin']['ports'][0]['host_ip'] == '127.0.0.1'
    assert selected['services']['twin']['volumes'] == ['workspace:/workspace', 'artifacts:/artifacts', 'control:/var/lib/unified-jobs']
    assert not {'privileged', 'environment', 'env_file'} & selected['services']['twin'].keys()
    assert profiles.compose_options({'offline': True})['offline'] is True
    for path in ('/host/compose.yaml', '../outside.yaml'):
        with pytest.raises(ValueError):
            profiles.compose_options({'composeFile': path})


@pytest.mark.parametrize('profile', ['python-project', 'browser-test'])
@pytest.mark.parametrize('compose_file', [None, 'compose.yaml'])
def test_default_presets_keep_preparation_unless_custom_compose(tmp_path, profile, compose_file):
    config = {'backend': 'docker'}
    row = environments.prepare(tmp_path, 'owner', 'workspace', {}, '/source',
        configs.key(config), 'request', backend_config=config, profile_id=profile,
        compose_options={'composeFile': compose_file, 'offline': False})
    assert row['preparationRequired'] is (compose_file is None)
    public = environments.public(tmp_path, row)
    assert public['dependencyPreparation'] == ('automatic' if compose_file is None else 'custom-compose')
    assert public.get('executionContract') is None  # No fabricated prepared receipt.
    payload = {'profileId': profile, 'composeFile': compose_file, 'offline': False,
               'preparedRuntime': compose_file is None}
    assert worker.prepared_runtime(payload) is (compose_file is None)
    environment = worker.job_environment(payload)
    if compose_file:
        assert environment == {}
    else:
        assert environment['AMPLIFIER_TEST_PYTHON'] == '/opt/unified-prepared/venv/bin/python'
        assert environment['PATH'].startswith('/opt/unified-prepared/venv/bin:')
        assert not {'UV_OFFLINE', 'PIP_NO_INDEX', 'npm_config_offline'} & environment.keys()


@pytest.mark.parametrize('content', [
    'services:\n  twin:\n    image: python\n    env_file: /host/private.env\n',
    'include: [../../host/compose.yaml]\n',
    'services:\n  twin:\n    extends:\n      file: /host/compose.yaml\n      service: other\n',
    'services:\n  twin:\n    image: python\n    env_file: "${X:-/outside/private.env}"\n',
])
def test_compose_normalization_cannot_read_outside_copied_source(tmp_path, monkeypatch, content):
    with tarfile.open(tmp_path / 'source.tar', 'w') as archive:
        member = tarfile.TarInfo('compose.yaml')
        member.size = len(content.encode())
        archive.addfile(member, io.BytesIO(content.encode()))
    monkeypatch.setattr(worker.subprocess, 'run', lambda *a, **k: pytest.fail('Compose read host inputs'))
    with pytest.raises(ValueError, match='within copied source'):
        worker.compose_file(tmp_path, {'composeFile': 'compose.yaml'})


@pytest.mark.parametrize('case', ['single-service', 'invalid-twin', 'nonroot', 'capabilities', 'offline-bridge', 'offline-container', 'copied-env', 'foreign-volumes', 'inherited-volumes'])
def test_ordinary_compose_twin_and_supervisor_preflight(tmp_path, monkeypatch, case):
    content = 'services:\n  app:\n    image: python\n'
    with tarfile.open(tmp_path / 'source.tar', 'w') as archive:
        for name, data in [('compose.yaml', content), ('app.env', 'VISIBLE=local-copy\n')]:
            member = tarfile.TarInfo(name)
            member.size = len(data.encode())
            archive.addfile(member, io.BytesIO(data.encode()))
    normalized = {'services': {'app': {'image': 'python'}}, 'networks': {'default': {}}, 'x-dtu': {}}
    if case == 'invalid-twin':
        normalized['x-dtu']['twin_machine'] = 'missing'
    elif case == 'nonroot':
        normalized['services']['app']['user'] = '1000:1000'
    elif case == 'capabilities':
        normalized['services']['app']['cap_drop'] = ['ALL']
    elif case.startswith('offline-'):
        normalized['services']['app']['network_mode'] = 'bridge' if case.endswith('bridge') else 'container:foreign'
    elif case.endswith('volumes'):
        normalized['services']['app']['volumes_from'] = ['container:foreign:rw' if case.startswith('foreign') else 'other:rw']
    monkeypatch.setattr(worker.subprocess, 'run',
        lambda *a, **k: SimpleNamespace(stdout=json.dumps(normalized)))
    payload = {'composeFile': 'compose.yaml', 'dockerHost': 'unix:///owned.sock', 'rootOwner': 'owner',
               'operationId': 'operation', 'offline': case.startswith('offline-')}
    if case in {'single-service', 'copied-env'}:
        selected = json.loads(worker.compose_file(tmp_path, payload).read_text())
        assert selected['x-dtu']['twin_machine'] == 'app'
        assert selected['services']['app']['user'] == '0:0'
        assert {v['target'] for v in selected['services']['app']['volumes']} == {'/workspace', '/artifacts', '/var/lib/unified-jobs'}
    else:
        with pytest.raises(ValueError):
            worker.compose_file(tmp_path, payload)


def test_malformed_yaml_refuses_before_compose_or_launch(tmp_path, monkeypatch):
    with tarfile.open(tmp_path / 'source.tar', 'w') as archive:
        data = b'services: [\n'
        member = tarfile.TarInfo('compose.yaml')
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    monkeypatch.setattr(worker.subprocess, 'run', lambda *a, **k: pytest.fail('prelaunch parser invoked Docker'))
    with pytest.raises(ValueError, match='YAML is invalid'):
        worker.compose_file(tmp_path, {'composeFile': 'compose.yaml'})


@pytest.mark.parametrize('backend', ['incus', 'docker'])
def test_legacy_unadmitted_allocation_gap_remains_recoverable(tmp_path, backend):
    config = {'backend': backend}
    first = environments.prepare(tmp_path, 'owner', 'workspace', {}, '/source', configs.key(config), 'request',
                                 backend_config=config, _qualification=True)
    assert 'offline' not in first
    assert environments.prepare(tmp_path, 'owner', 'workspace', {}, '/source', configs.key(config), 'request',
                                backend_config=config, _qualification=True) == first


async def test_two_package_updates_rollback_compresses_selection_without_intermediate(tmp_path):
    from amplifier_web.smart_tool_updates import ManagedUpdates
    old, middle, last = [installation(tmp_path, c * 24, c * 40) for c in ('a', 'b', 'c')]
    state = {'installations': [old, middle, last], 'servers': []}
    raw = {'enabled': True, 'installationId': old['id'], 'commit': old['commit'], 'dockerHost': 'unix:///owned.sock'}
    setup.write_json(tmp_path / 'test-environments.json', raw)
    app = SimpleNamespace(data_dir=tmp_path)
    class Manager(ManagedUpdates):
        def __init__(self):
            self.state, self.service, self.install_lock = state, app, asyncio.Lock()
            self.connections, self.schemas = {}, {}
        async def _change(self, function):
            function(self.state)
    manager = app.smart_tools = Manager()
    setup.write_json(setup.root(app) / ('a' * 32 + '.json'), {
        'id': 'a' * 32, 'state': 'completed', 'backend': 'docker', 'priorConfiguration': None,
        'installationId': old['id'], 'candidateConfiguration': raw,
        'runnerSourcePolicy': {'version': 1, 'repository': environments.REPOSITORY, 'ref': 'main'},
        'proof': {'configurationReady': True}})
    await manager.activate_update(old['id'], middle['id'])
    await manager.activate_update(middle['id'], last['id'])
    assert old['supersededBy'] == last['id']
    assert environments.local_configuration(app)[0]['installationId'] == last['id']
    await manager.activate_update(last['id'], middle['id'])
    assert environments.local_configuration(app)[0]['installationId'] == middle['id']
    state['installations'].remove(last)
    assert environments.local_configuration(app)[0]['installationId'] == middle['id']


def git(source, *args):
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args],
                                   cwd=source, text=True).strip()


def thin_report(path, report):
    """Fixture evidence can exceed the private setup-record reader's 64 KiB."""
    handle, temporary = tempfile.mkstemp(prefix='.thin-report-', dir=path.parent)
    try:
        with os.fdopen(handle, 'w') as stream:
            json.dump(report, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def thin_require_running(result, row):
    request = result['request']
    assert request['state'] == 'completed' and request['resourceId'] == row['id']
    observed = request['result']
    assert observed['ok'] and observed['status'] == 'running', 'Cleanup crossed owners.'
    assert observed['observation']['ownership'] == 'matched'
    assert {key: observed['observation'][key] for key in
            ('rootOwner', 'operationId', 'universeId', 'backendIdentity')} == {
                'rootOwner': row['sessionId'], 'operationId': row['launchOperationId'],
                'universeId': row['universeId'], 'backendIdentity': row['backendIdentity']}


def thin_observe_absence(app, entry):
    """Retain a fresh exact-owner observation, never repeat a removal."""
    row = environments.get(app.data_dir, entry['owner'], entry['resource']['id'])
    config = environments.resolve_configuration(app, row, None)
    result = environments.invoke(config, app.data_dir, row, 'reconcile', {})
    entry['absence'] = result
    assert result['ok'] is True and result['status'] == 'absent', result
    observed = result['observation']
    assert observed['absenceConfirmed'] is True and observed['ownership'] == 'absent'
    assert {key: observed[key] for key in ('rootOwner', 'operationId', 'universeId', 'backendIdentity')} == {
        'rootOwner': row['sessionId'], 'operationId': row['launchOperationId'],
        'universeId': row['universeId'], 'backendIdentity': row['backendIdentity']}


async def thin_call(app, report_path, report, entry, action, **args):
    """Keep this fixture's original request and custody, including lost replies."""
    if action == 'remove':
        assert not entry.get('removalAttempted'), 'Unknown removal must not replay.'
        entry['removalAttempted'] = True
    arguments = {**entry['scope'], 'requestId': entry['owner'] + '-' + action, **args}
    if entry.get('resource'):
        arguments.update(id=entry['resource']['id'], expectedRevision=entry['resource']['revision'])
    call = {'owner': entry['owner'], 'action': action, 'phase': 'before-effect'}
    report.setdefault('calls', []).append(call)
    thin_report(report_path, report)
    try:
        result = (await app.dispatch('workspace.environments.' + action, arguments,
                                    command_id='thin-' + arguments['requestId']))['result']
        call['result'] = result
        assert result['request']['state'] == 'completed', result
        if action == 'remove':
            proof = result['request']['cleanupExecution']
            assert proof['kind'] == 'qualification-test-checkout'
            assert proof['status'] == 'permit-recorded'
            assert set(proof) == {'schema', 'kind', 'ready', 'readySha256', 'status', 'delivery'}
            assert result['resource']['status'] == 'removed'
        return result
    finally:
        # A dispatch exception may follow admission. Read the original journal,
        # never issue another request just to discover its resource.
        request = app.operations.requests.read(entry['owner'], arguments['requestId'])
        call['request'] = request
        if request and request.get('resourceId'):
            raw = environments.get(app.data_dir, entry['owner'], request['resourceId'])
            entry['resource'] = environments.public(app.data_dir, raw)
            entry['state'] = entry['resource']['status']
        thin_report(report_path, report)


async def thin_finalize(app, report_path, report):
    errors = []
    for entry in report['environments']:
        if not entry.get('resource') or entry['state'] == 'removed' or entry.get('removalAttempted'):
            continue
        try:
            assert entry['state'] in {'running', 'stopped', 'not-started'}, 'Unsettled owner; inspect original receipt.'
            if entry['state'] == 'running':
                await thin_call(app, report_path, report, entry, 'stop')
            await thin_call(app, report_path, report, entry, 'remove')
        except Exception as exc:
            # One owner's failed finalizer must not prevent the other's attempt.
            errors.append(exc)
    report['cleanupComplete'] = all(entry.get('state') == 'removed' for entry in report['environments'])
    thin_report(report_path, report)
    if errors:
        raise ExceptionGroup('Thin Docker cleanup is incomplete; inspect original receipts', errors)


async def thin_finish(app, report_path, report):
    try:
        await thin_finalize(app, report_path, report)
        # The hosted lane needs physical absence evidence in addition to the
        # original removal receipts. Observation failure never retries cleanup.
        if os.environ.get('CHECK_SUITE') == 'thin-docker-native':
            errors = []
            for entry in report['environments']:
                if entry.get('state') == 'removed':
                    try:
                        await asyncio.to_thread(thin_observe_absence, app, entry)
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        thin_report(report_path, report)
            if errors:
                raise ExceptionGroup('Thin Docker absence remains unverified', errors)
        if report.get('journeyPassed') and report['cleanupComplete']:
            report['result'] = 'passed'
    finally:
        try:
            thin_report(report_path, report)
        finally:
            await app.close()


def test_thin_report_keeps_large_evidence_and_private_mode(tmp_path):
    path = tmp_path / 'native-result.json'
    report = {'calls': [{'output': 'x' * 32768} for _ in range(4)], 'result': 'incomplete'}
    thin_report(path, report)
    assert path.stat().st_size > 65536
    report['cleanupComplete'] = True
    thin_report(path, report)
    assert json.loads(path.read_text()) == report
    assert path.stat().st_mode & 0o077 == 0
    assert not list(tmp_path.glob('.thin-report-*'))


async def test_thin_close_survives_report_write_failure(tmp_path, monkeypatch):
    closed = []

    async def close():
        closed.append(True)

    def fail(*args):
        raise OSError('Evidence storage is unavailable')

    monkeypatch.setattr(sys.modules[__name__], 'thin_report', fail)
    with pytest.raises(OSError):
        await thin_finish(SimpleNamespace(close=close), tmp_path / 'result.json', {'environments': []})
    assert closed == [True]


@pytest.mark.parametrize('changed', [None, 'stopped', 'rootOwner', 'operationId', 'universeId', 'backendIdentity'])
def test_thin_running_requires_fresh_owner_bound_observation(changed):
    row = {'id': 'second', 'sessionId': 'owner-b', 'launchOperationId': 'launch-b',
           'universeId': 'universe-b', 'backendIdentity': {'backend': 'docker', 'daemon': 'owned'}}
    observation = {'ownership': 'matched', 'rootOwner': row['sessionId'],
                   'operationId': row['launchOperationId'], 'universeId': row['universeId'],
                   'backendIdentity': row['backendIdentity']}
    fresh = {'ok': True, 'status': 'running', 'observation': observation}
    result = {'resource': {'status': 'running'},  # Deliberately stale projection.
              'request': {'state': 'completed', 'resourceId': row['id'], 'result': fresh}}
    if changed == 'stopped':
        fresh['status'] = 'stopped'
    elif changed:
        observation[changed] = 'foreign'
    if changed:
        with pytest.raises(AssertionError):
            thin_require_running(result, row)
    else:
        thin_require_running(result, row)


@pytest.mark.parametrize('outcome', ['failed', 'outcome_unknown', 'lost-reply'])
async def test_thin_call_retains_original_failure_and_fences_removal(tmp_path, monkeypatch, outcome):
    """Portable wrapper check only; no helper, permit or backend is fabricated."""
    state = 'failed' if outcome == 'failed' else 'outcome_unknown'
    request = {'requestId': 'owner-remove', 'resourceId': 'resource', 'state': state}
    row = {'id': 'resource', 'revision': 2, 'status': 'stopped' if state == 'failed' else 'unknown'}
    entry = {'owner': 'owner', 'scope': {'sessionId': 'owner', 'workspaceId': 'workspace'},
             'resource': {'id': 'resource', 'revision': 1}, 'state': 'stopped'}
    calls = []

    async def dispatch(action, args, **kwargs):
        calls.append((action, args))
        if outcome == 'lost-reply':
            raise TimeoutError('Lost original dispatch reply')
        return {'result': {'request': request, 'resource': row}}

    app = SimpleNamespace(data_dir=tmp_path, dispatch=dispatch,
        operations=SimpleNamespace(requests=SimpleNamespace(read=lambda *a: request)))
    monkeypatch.setattr(environments, 'get', lambda *a: row)
    monkeypatch.setattr(environments, 'public', lambda *a: row)
    report, path = {'environments': [entry]}, tmp_path / 'result.json'
    with pytest.raises(TimeoutError if outcome == 'lost-reply' else AssertionError):
        await thin_call(app, path, report, entry, 'remove')
    assert entry['removalAttempted'] and entry['state'] == row['status']
    assert setup.read_json(path)['calls'][0]['request'] == request
    with pytest.raises(AssertionError, match='must not replay'):
        await thin_call(app, path, report, entry, 'remove')
    await thin_finalize(app, path, report)
    assert len(calls) == 1 and report['cleanupComplete'] is False


@pytest.mark.parametrize('failure', ['stop', 'remove'])
async def test_thin_finalizers_are_independent_and_never_repeat_removal(tmp_path, monkeypatch, failure):
    entries = [{'owner': name, 'resource': {'id': name}, 'state': 'running'} for name in ('a', 'b')]
    report, path, calls = {'environments': entries}, tmp_path / 'result.json', []

    async def call(app, report_path, report, entry, action):
        calls.append((entry['owner'], action))
        if action == 'remove':
            entry['removalAttempted'] = True
        if entry['owner'] == 'a' and action == failure:
            entry['state'] = 'unknown'
            raise TimeoutError('Original finalizer outcome is unknown')
        entry['state'] = 'stopped' if action == 'stop' else 'removed'

    monkeypatch.setattr(sys.modules[__name__], 'thin_call', call)
    with pytest.raises(ExceptionGroup):
        await thin_finalize(None, path, report)
    assert calls == ([('a', 'stop')] + ([('a', 'remove')] if failure == 'remove' else [])
                     + [('b', 'stop'), ('b', 'remove')])
    assert entries[1]['state'] == 'removed' and report['cleanupComplete'] is False
    before = list(calls)
    if failure == 'stop':
        with pytest.raises(ExceptionGroup):
            await thin_finalize(None, path, report)
    else:
        await thin_finalize(None, path, report)
    assert calls == before


@pytest.mark.skipif(not os.environ.get('UNIFIED_DTU_PROOF_ROOT'),
                    reason='Opt-in real Docker Smart Tool proof requires an owned registered proof root.')
async def test_real_smart_tool_two_owners_dirty_worktree_wheel_and_preview(monkeypatch):
    """No model campaign. Provision the Smart Tool under proof-root/app beforehand."""
    from amplifier_web.service import AppService
    from amplifier_web.smart_tools import SmartToolsManager
    from native_cleanup_fixture import checkout_cleanup

    class NoModelRuntime:
        async def start(self, *args, **kwargs):
            raise AssertionError('Thin Docker proof must not start a model')

        async def close(self):
            pass

    home = Path(os.environ['UNIFIED_DTU_PROOF_ROOT']).resolve()
    python = os.environ['UNIFIED_DTU_PYTHON']
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    report_path = home / 'native-result.json'
    assert not report_path.exists(), 'Inspect an existing result; never replay this proof.'
    installation_id = hashlib.sha256(environments.DTU_COMMIT.encode()).hexdigest()[:24]
    # Use the same actual managed installation layout as the maintained native
    # cleanup fixture. Do not alias/copy a venv or bypass configuration validation.
    assert python == str(home / 'app/smart-tools/installs' / installation_id / 'venv/bin/python')
    assert Path(python).is_file(), 'Provision the owned Smart Tool before this proof.'
    assert {p.name for p in (home / 'app').iterdir()} == {'smart-tools'}, 'Use a fresh fixture app, not retained owners.'
    report = {'result': 'incomplete', 'environments': [], 'cleanupComplete': False}
    thin_report(report_path, report)
    source, checkout = home / 'repository', home / 'linked-worktree'
    compose = {
        'services': {'twin': {'image': environments.APPROVED_IMAGE,
            'command': ['python', '-m', 'http.server', '8000', '--directory', '/artifacts'],
            'ports': [{'target': 8000, 'published': '0', 'host_ip': '127.0.0.1'}],
            'healthcheck': {'test': ['CMD', 'python', '-c', "import urllib.request; urllib.request.urlopen('http://localhost:8000')"], 'interval': '2s'}}},
        'x-dtu': {'twin_machine': 'twin', 'urls': [{'port': 8000, 'label': 'Proof preview'}]},
    }
    source.mkdir()
    git(source, 'init', '-q', '-b', 'selected-feature')
    (source / 'pyproject.toml').write_text('[build-system]\nrequires=["setuptools"]\nbuild-backend="setuptools.build_meta"\n[project]\nname="unified-dirty-proof"\nversion="1.0.0"\n')
    (source / 'dirty_proof.py').write_text("VALUE = 'committed-original'\n")
    (source / 'compose.json').write_text(json.dumps(compose))
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Proof', '-c', 'user.email=proof@example.invalid', 'commit', '-qm', 'Original package')
    original = git(source, 'rev-parse', 'HEAD')
    git(source, 'worktree', 'add', '--detach', str(checkout), 'HEAD')
    (checkout / 'dirty_proof.py').write_text("VALUE = 'dirty-selected-worktree'\n")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in checkout.iterdir() if p.is_file()}
    report['originalCommit'] = original
    monkeypatch.setenv('AMPLIFIER_HOME', str(home / 'native-home'))
    app = AppService(home / 'app', NoModelRuntime(), workspace=checkout)
    try:
        app.smart_tools = SmartToolsManager(app)
        app.smart_tools.state['installations'].append({
            'id': installation_id, 'status': 'installed', 'commit': environments.DTU_COMMIT,
            'repository': environments.REPOSITORY, 'extras': [], 'python': python})
        setup.write_json(app.data_dir / 'test-environments.json', {
            'enabled': True, 'installationId': installation_id, 'commit': environments.DTU_COMMIT,
            'backend': 'docker', 'dockerHost': 'unix:///var/run/docker.sock',
            'image': environments.APPROVED_IMAGE, 'maxRunning': 2, 'maxInstances': 2})
        _, availability, reason = environments.configuration(app)
        assert availability == 'ready', reason
        sessions = [app._new_session({'title': 'Thin Docker owner ' + suffix}) for suffix in ('a', 'b')]
        app.state['sessions'] = sessions
        app.state['selectedSessionId'] = sessions[0]['id']
        app._publish()
        workspace = next(w for w in app.state['workspaces'] if w['path'] == str(checkout))
        # Only the source-evidence adapter is test-scoped. AppService issues the
        # original durable permit and retains every production recheck.
        checkout_cleanup(monkeypatch)
        for session in sessions:
            entry = {'owner': session['id'], 'state': 'not-started',
                     'scope': {'sessionId': session['id'], 'workspaceId': workspace['id']}}
            report['environments'].append(entry)
            await thin_call(app, report_path, report, entry, 'start', profileId='linux-python',
                            composeFile='compose.json', offline=False, preserveSource=False)
        entry = report['environments'][0]
        command = (
            'unset PYTHONPATH; cd /workspace; python -m pip wheel --no-deps . -w /artifacts/dist'
            ' && python -m venv /tmp/installed'
            ' && /tmp/installed/bin/python -m pip install /artifacts/dist/*.whl'
            ' && cd /artifacts && /tmp/installed/bin/python -I -c '
            + shlex.quote("import dirty_proof,sys; assert dirty_proof.VALUE=='dirty-selected-worktree'; assert dirty_proof.__file__.startswith(sys.prefix+'/'); print(dirty_proof.__file__)")
            + ' > /artifacts/installed-proof.txt')
        installed = await thin_call(app, report_path, report, entry, 'execute', command=command, timeoutSeconds=300)
        assert installed['request']['result']['result']['exit_code'] == 0, installed
        url = entry['resource']['urls'][0]['url'].rstrip('/') + '/installed-proof.txt'
        with urlopen(url, timeout=10) as response:
            assert '/tmp/installed/' in response.read().decode()
        handoff = {'target': url, 'request': 'Inspect the installed dirty-worktree package proof only.', 'keep_running': True}
        rc_python = os.environ.get('UNIFIED_REALITY_CHECK_PYTHON')
        if rc_python:
            signature = subprocess.check_output([rc_python, '-I', '-B', '-c',
                "import inspect,json,importlib.metadata; from reality_check import lib; v=json.loads(__import__('sys').argv[1]); inspect.signature(lib.check_software).bind(**v); print(json.dumps({'version':importlib.metadata.version('reality-check'),'callable':True,'modelCampaign':'not_run'}))",
                json.dumps(handoff)], text=True)
            report['realityCheckSignature'] = json.loads(signature)
        report['realityCheckHandoff'] = {**handoff,
                                        'modelCampaign': 'not_run', 'lifecycleOwner': entry['owner']}
        await thin_call(app, report_path, report, entry, 'stop')
        await thin_call(app, report_path, report, entry, 'remove')
        second = report['environments'][1]
        second_row = environments.get(app.data_dir, second['owner'], second['resource']['id'])
        observed = await thin_call(app, report_path, report, second, 'reconcile')
        thin_require_running(observed, second_row)
        report['sourceUnchanged'] = before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                              for p in checkout.iterdir() if p.is_file()}
        assert report['sourceUnchanged'] and git(source, 'rev-parse', 'HEAD') == original
        report['journeyPassed'] = True
    finally:
        # Observe exact current owners; teardown never retries an unknown remove.
        await thin_finish(app, report_path, report)

