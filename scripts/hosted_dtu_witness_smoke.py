"""One exact installed witness smoke, only in the dedicated hosted CI job."""
import asyncio
import hashlib
import importlib.metadata as metadata
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

APP = '7c9959020c0046ee19d40a4203a892f4c596d36b'
TREE = '7e7fb8262f065c999b28ba86efe219858bca35bc'
BASE = 'ce6111c54c163f26b68de121100ae04ec24d1875'
DTU = '2100c34600036680e9b5c8bf484da9c6f106a7e4'
DTU_WHEEL = 'digital_twin_universe-0.6.12-py3-none-any.whl'
DTU_DIGEST = 'd8ac2e90106ac99ef60ba3073d6fa85d877d24e7a749bd7d045c0ea1bdb82ab0'
BRANCH = 'refs/heads/ci/2510-installed-dtu-witness'
SENTINEL = 'hosted-dtu-witness-2510\n'


def command(*args, cwd=None, timeout=60):
    return subprocess.check_output(args, cwd=cwd, timeout=timeout, text=True).strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def saved(path, evidence):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n')
    tmp.replace(path)


def public_sha(ref):
    return command('git', 'ls-remote', 'https://github.com/microsoft/amplifier-unified.git', ref).split()[0]


def guard():
    assert sys.platform == 'linux' and os.getenv('GITHUB_ACTIONS') == 'true'
    assert os.getenv('GITHUB_REPOSITORY') == 'microsoft/amplifier-unified'
    assert os.getenv('GITHUB_EVENT_NAME') == 'workflow_dispatch' and os.getenv('GITHUB_REF') == BRANCH
    assert os.getenv('WITNESS_RUNNER') == 'github-hosted' and os.getenv('CANDIDATE_REF') == APP


class NoModel:
    async def start(self, *_args, **_kwargs):
        raise AssertionError('This fixture must not start an agent')

    async def close(self):
        pass


async def probe(root, wheel, backend_wheel, path):
    from amplifier_web import task_environments as owner
    from amplifier_web.service import AppService
    from amplifier_web.smart_tools import SmartToolsManager
    from amplifier_web import environment_cleanup_execution

    evidence = json.loads(path.read_text())
    def record(**fields):
        evidence.update(fields)
        saved(path, evidence)

    assert Path.cwd() == root and Path(sys.prefix) == root / 'installed'
    assert not os.getenv('PYTHONPATH')
    dist = metadata.distribution('amplifier-unified')
    origin = json.loads(dist.read_text('direct_url.json'))
    assert origin['url'] == wheel.as_uri() and not origin.get('dir_info', {}).get('editable')
    members = {}
    with zipfile.ZipFile(wheel) as archive:
        for module in ('task_environments', 'test_environment_worker', 'test_queue', 'environment_cleanup_execution'):
            name = 'amplifier_web/' + module + '.py'
            deployed = Path(dist.locate_file(name)).resolve()
            assert deployed.is_relative_to(Path(sys.prefix))
            assert deployed.read_bytes() == archive.read(name)
            members[name] = digest(archive.read(name))
        assert Path(owner.__file__).resolve() == Path(dist.locate_file('amplifier_web/task_environments.py')).resolve()
    record(amplifierWheel={'sha256': digest(wheel.read_bytes()), 'version': dist.version, 'installedMembers': members},
           docker=command('docker', '--host', 'unix:///var/run/docker.sock', 'version', '--format', '{{.Server.Version}}'),
           compose=command('docker', '--host', 'unix:///var/run/docker.sock', 'compose', 'version', '--short'))

    source = root / 'fixture'
    source.mkdir()
    (source / 'input.txt').write_text(SENTINEL)
    command('git', 'init', '-q', cwd=source)
    command('git', 'add', 'input.txt', cwd=source)
    command('git', '-c', 'user.name=Hosted witness fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '-qm', 'One offline test input', cwd=source)
    app = AppService(root / 'app', NoModel(), workspace=source)
    try:
        app.smart_tools = SmartToolsManager(app)
        record(stage='smart-tool-install')
        installed = await app.smart_tools.install({'repository': owner.REPOSITORY, 'ref': DTU, 'path': '.', 'extras': []})
        assert installed['commit'] == DTU and not installed.get('extras')
        runner = Path(installed['python'])
        assert runner.is_relative_to(root / 'app' / 'smart-tools')
        library = json.loads(command(str(runner), '-I', '-B', '-c',
            'import json,sys,importlib.metadata as m; from digital_twin_universe import lib; '
            'assert callable(lib.launch_terminal_witness); print(json.dumps(dict(prefix=sys.prefix, lib=lib.__file__, version=m.version("digital-twin-universe"))))', cwd=root))
        package = Path(library['lib']).parent.resolve()
        assert package.is_relative_to(Path(library['prefix']).resolve()) and package.is_relative_to(root / 'app')
        assert library['version'] == '0.6.12'
        with zipfile.ZipFile(backend_wheel) as released:
            files = [n for n in released.namelist() if n.startswith('digital_twin_universe/') and n.endswith('.py')]
            assert len(files) == 49
            assert all((package / n.split('/', 1)[1]).read_bytes() == released.read(n) for n in files)
        record(dtu={'commit': DTU, 'version': '0.6.12', 'officialWheelSha256': DTU_DIGEST, 'installedPythonMembersMatched': len(files)})

        created = await app.dispatch('session.create', {'title': 'Hosted witness fixture'}, command_id='fixture-create', include_state=False)
        sid = created['sessionId']
        ws, = [w for w in app.state['workspaces'] if w['path'] == str(source)]
        scope = {'sessionId': sid, 'workspaceId': ws['id']}
        async def action(name, request_id=None, **values):
            args = {**scope, **values, **({'requestId': request_id} if request_id else {})}
            return (await app.dispatch(name, args, include_state=False))['result']
        record(stage='docker-setup')
        setup = await action('workspace.environments.setup', 'hosted-witness-setup', backend='docker')
        task = app._test_setup_tasks.get(setup['operation']['id'])
        if task:
            await asyncio.wait_for(asyncio.shield(task), 420)
        config, state, _ = owner.configuration(app)
        assert state == 'ready' and config['commit'] == DTU and config['installationId'] == installed['id']
        assert config['dockerHost'] == 'unix:///var/run/docker.sock' and not config.get('remoteRunner')
        available = await asyncio.to_thread(owner.capacity, app, config)
        assert available.get('available') is True, 'ordinary capacity did not admit one fixture'
        # No substitute gate: whether serving-wheel qualification is available is an observation.
        try:
            environment_cleanup_execution.context(app)
        except (AttributeError, ValueError):
            record(qualifiedServingWheel=False)
        else:
            record(qualifiedServingWheel=True)
        record(stage='queued-check', vmCustody='github-hosted-job-only', candidateAdmission='one fixture request')
        rid = 'hosted-witness-one'
        check_code = (f'from pathlib import Path; assert Path("/workspace/input.txt").read_text()=={SENTINEL!r}; '
                      f'Path("/artifacts/witness.txt").write_text({SENTINEL!r}); print({SENTINEL.strip()!r})')
        submitted = await action('workspace.tests.submit', rid, profileId='linux-python',
                                 command=shlex.join(['python', '-c', check_code]), offline=True,
                                 preserveSource=False, notify=False, timeoutSeconds=45, waitSeconds=90)
        assert submitted['status'] in {'queued', 'starting'}
        qid = app.test_queue.identity(sid, rid)
        async with asyncio.timeout(430):
            while True:
                job = app.test_queue.get(qid)['job']
                if job['status'] not in {'queued', 'starting', 'checking', 'stopping', 'removing'}:
                    break
                await asyncio.sleep(2)
        detail = await action('workspace.tests.result', rid)
        resource = job.get('resourceId')
        record(queueStatus=job['status'], resourceAdmitted=bool(resource), checkExitCode=detail.get('exitCode'))
        assert resource, 'no owned guest resource'
        row = owner.get(app.data_dir, sid, resource, read_only=True)
        journal = app.operations.requests
        started = journal.read(sid, row['commandId'])
        check = journal.read(sid, qid + ':execute')
        removal = journal.read(sid, qid + ':remove') if job.get('removal') else {}
        removed = removal.get('result', {})
        record(launchState=started['state'], checkState=check['state'],
               stopState=job.get('cleanup', {}).get('state'), removal={
                   'state': removal.get('state'), 'code': removed.get('code'),
                   'effectAdmitted': removed.get('effectAdmitted'), 'phase': removed.get('phase'),
                   'verified': removal.get('state') == 'completed' and removed.get('status') == 'removed'})
        assert started['state'] == 'completed' and started['resourceId'] == resource
        assert check['state'] == 'completed' and detail['exitCode'] == 0 and detail['text'].strip() == SENTINEL.strip()
        assert 'terminalWitness' not in detail.get('launch', {})
        record(completedResultProjection='correctly omitted; positive branch covered separately in controlled exact-pair proof')
        assert await asyncio.to_thread(owner.launch_witness, app, row) is False, 'installed adapter did not attribute a normal final-up witness'
        record(witness={'status': 'foreground-settled', 'postDispatchFailed': False, 'originalBindingVerified': True})
        assert job.get('cleanup', {}).get('state') == 'completed'
        record(stage='artifact-verification')
        if evidence['removal']['verified']:
            assert removed.get('retention', {}).get('verified') is True
            archives = removed.get('archives') or removal.get('guestRemoval', {}).get('archives', [])
        else:
            assert (removal.get('state') == 'failed' and removed.get('effectAdmitted') is False
                    and removed.get('code') == 'cleanup_helper_not_started'), 'removal not positively refused; preserve without replay'
            export = (await action('workspace.environments.exportArtifacts', 'hosted-witness-export',
                                   id=resource, expectedRevision=row['revision']))['request']
            assert export['state'] == 'completed' and export['result']['artifactExport']['verified'] is True
            archives = export['result']['archives']
        retained, = [a for a in archives if a['source'] == '/artifacts']
        archive = Path(retained['destination']).resolve()
        assert archive.is_relative_to(owner.storage.directory(app.data_dir, row)) and archive.stat().st_size < 1024 * 1024
        raw = archive.read_bytes()
        assert digest(raw) == retained['sha256']
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            marker, = [m for m in tar if Path(m.name).name == 'witness.txt' and m.isfile()]
            assert marker.size == len(SENTINEL) and tar.extractfile(marker).read() == SENTINEL.encode()
        assert journal.read(sid, row['commandId']) == started and journal.read(sid, qid + ':execute') == check
        assert journal.read(sid, qid + ':remove') == removal
        record(guestArtifact={'sha256': digest(raw), 'bytes': len(raw), 'sentinelVerified': True},
               vmCustody='normal product removal' if evidence['removal']['verified'] else 'stopped owner; hosted VM disposal only',
               installedWitnessSmoke='pass', stage='complete')
    finally:
        await app.close()


def main():
    guard()
    top = Path.cwd().resolve()
    source = top / '.ci/candidate'
    assert command('git', 'rev-parse', 'HEAD') == os.environ['GITHUB_WORKFLOW_SHA'] == os.environ['GITHUB_SHA']
    output = top / 'output/hosted-dtu-witness-2510'
    output.mkdir(parents=True, exist_ok=False)
    path = output / 'receipt.json'
    evidence = {'workflowSha': os.environ['GITHUB_SHA'], 'runId': os.environ['GITHUB_RUN_ID'],
                'runAttempt': os.environ['GITHUB_RUN_ATTEMPT'], 'runner': 'github-hosted',
                'candidate': APP, 'candidateTree': TREE, 'base': BASE, 'stage': 'source',
                'installedWitnessSmoke': 'unrun', 'productionCleanupQualified': False}
    saved(path, evidence)
    root = Path(os.environ['RUNNER_TEMP']).resolve() / 'dtu-witness-2510'
    root.mkdir(mode=0o700)
    (root / 'home').mkdir(mode=0o700)
    (root / 'tmp').mkdir(mode=0o700)
    for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'PYTHONPATH', 'VIRTUAL_ENV'):
        os.environ.pop(key, None)
    os.environ.update(HOME=str(root / 'home'), AMPLIFIER_HOME=str(root / 'home'), TMPDIR=str(root / 'tmp'),
                      UV_CACHE_DIR=str(root / 'cache'), XDG_CACHE_HOME=str(root / 'xdg-cache'),
                      GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, AMPLIFIER_GITHUB_ACCESS='anonymous')
    try:
        assert command('git', 'rev-parse', 'HEAD', cwd=source) == APP
        assert command('git', 'rev-parse', 'HEAD^{tree}', cwd=source) == TREE
        evidence.update(prHeadAtAdmission=public_sha('refs/pull/2510/head'), mainAtAdmission=public_sha('refs/heads/main'))
        saved(path, evidence)
        assert evidence['prHeadAtAdmission'] == APP and evidence['mainAtAdmission'] == BASE, 'public head/base moved before admission'
        backend_wheel = root / DTU_WHEEL
        with urllib.request.urlopen('https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe/releases/download/v0.6.12/' + DTU_WHEEL, timeout=45) as response:
            raw = response.read(800000)
        assert digest(raw) == DTU_DIGEST
        backend_wheel.write_bytes(raw)
        evidence.update(stage='build-app-wheel', verifiedPublicDtuWheel=DTU_DIGEST)
        saved(path, evidence)
        subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(root / 'wheel')], cwd=source, check=True, timeout=120)
        wheel, = (root / 'wheel').glob('*.whl')
        os.environ['UV_PROJECT_ENVIRONMENT'] = str(root / 'installed')
        subprocess.run(['uv', 'sync', '--locked', '--no-dev', '--no-editable', '--no-install-project'], cwd=source, check=True, timeout=720)
        python = root / 'installed/bin/python'
        subprocess.run(['uv', 'pip', 'install', '--python', str(python), '--no-deps', str(wheel)], cwd=root, check=True, timeout=120)
        subprocess.run(['uv', 'pip', 'check', '--python', str(python)], cwd=root, check=True, timeout=45)
        harness = root / 'hosted_case.py'
        shutil.copyfile(__file__, harness)
        subprocess.run([str(python), '-I', '-B', str(harness), '--probe', str(root), str(wheel), str(backend_wheel), str(path)],
                       cwd=root, check=True, timeout=930)
    finally:
        evidence = json.loads(path.read_text())
        try:
            evidence.update(prHeadAtExit=public_sha('refs/pull/2510/head'), mainAtExit=public_sha('refs/heads/main'))
        except (IndexError, OSError, subprocess.SubprocessError):
            evidence['exitDriftRead'] = 'unavailable'
        evidence['productionCleanupQualified'] = evidence.get('removal', {}).get('verified') is True
        evidence['vmDisposal'] = 'hosted job lifecycle; no independent VM deletion receipt'
        saved(path, evidence)


if __name__ == '__main__':
    if len(sys.argv) > 1:
        guard()
        assert sys.argv[1] == '--probe' and len(sys.argv) == 6
        asyncio.run(probe(*map(Path, sys.argv[2:])))
    else:
        main()
