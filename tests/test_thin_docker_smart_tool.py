"""Thin Docker contract and opt-in real maintained Smart Tool integration."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
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


@pytest.mark.skipif(not os.environ.get('UNIFIED_DTU_PROOF_ROOT'),
                    reason='Opt-in real Docker Smart Tool proof requires an owned registered proof root.')
def test_real_smart_tool_two_owners_dirty_worktree_wheel_and_preview():
    """No model/provider campaign. Uses installed Smart Tool, never its bundle."""
    home = Path(os.environ['UNIFIED_DTU_PROOF_ROOT']).resolve()
    python = os.environ['UNIFIED_DTU_PYTHON']
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    report_path = home / 'native-result.json'
    assert not report_path.exists(), 'Inspect an existing result; never replay this proof.'
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
    config = {'python': python, 'backend': 'docker', 'dockerHost': 'unix:///var/run/docker.sock',
              'image': environments.APPROVED_IMAGE, 'admissionRoot': str(home / 'admission')}
    rows, removal_attempted, report = [], set(), {'originalCommit': original, 'result': 'incomplete', 'environments': []}
    def call(row, action, **args):
        if action == 'remove':
            assert row['id'] not in removal_attempted, 'Unknown removal must not replay.'
            removal_attempted.add(row['id'])
        result = environments.invoke(config, home, row, action,
            {'requestId': row['id'] + '-' + action, 'expectedRevision': row['revision'], **args})
        report.setdefault('calls', []).append({'owner': row['sessionId'], 'action': action, 'result': result})
        setup.write_json(report_path, report)


        assert result.get('ok'), result
        return result
    try:
        for suffix in ('a', 'b'):
            row = {'sessionId': 'thin-dtu-proof-' + suffix, 'id': 'owner-' + suffix,
                   'launchOperationId': 'thin-dtu-proof-' + suffix, 'revision': 1,
                   'backendKey': environments.config_key(config),
                   'source': str(checkout), 'profileId': 'linux-python',
                   'composeFile': 'compose.json', 'offline': False, 'preserveSource': False}
            environments.storage.directory(home, row).mkdir(parents=True, mode=0o700)
            prepared = call(row, 'prepare')
            row.update(universeId=prepared['universeId'], snapshot=prepared['snapshot'], backendIdentity=prepared['backendIdentity'])
            rows.append(row)
            report['environments'].append({'id': row['universeId'], 'owner': row['sessionId'], 'state': 'prepared'})
            # Registration is done before this test's launch using these stable
            # roots/operation identities, not by scanning or adopting old guests.
            launched = call(row, 'start')
            report['environments'][-1].update(state='running', urls=launched['urls'])
        row = rows[0]
        command = (
            'unset PYTHONPATH; cd /workspace; python -m pip wheel --no-deps . -w /artifacts/dist'
            ' && python -m venv /tmp/installed'
            ' && /tmp/installed/bin/python -m pip install /artifacts/dist/*.whl'
            ' && cd /artifacts && /tmp/installed/bin/python -I -c '
            + shlex.quote("import dirty_proof,sys; assert dirty_proof.VALUE=='dirty-selected-worktree'; assert dirty_proof.__file__.startswith(sys.prefix+'/'); print(dirty_proof.__file__)")
            + ' > /artifacts/installed-proof.txt')
        installed = call(row, 'execute', command=command, timeoutSeconds=300)
        assert installed['result']['exit_code'] == 0, installed
        url = report['environments'][0]['urls'][0]['url'].rstrip('/') + '/installed-proof.txt'
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
                                        'modelCampaign': 'not_run', 'lifecycleOwner': row['sessionId']}
        call(row, 'stop')
        call(row, 'remove')
        report['environments'][0]['state'] = 'removed'
        assert call(rows[1], 'read')['status'] == 'running', 'Cleanup crossed owners.'
        report['sourceUnchanged'] = before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                              for p in checkout.iterdir() if p.is_file()}
        assert report['sourceUnchanged'] and git(source, 'rev-parse', 'HEAD') == original
        report['result'] = 'passed'
    finally:
        # Observe exact current owners; teardown never retries an unknown remove.
        for index, row in enumerate(rows):
            if report['environments'][index]['state'] == 'removed' or row['id'] in removal_attempted:
                continue
            call(row, 'stop')
            call(row, 'remove')
            report['environments'][index]['state'] = 'removed'
        setup.write_json(report_path, report)

