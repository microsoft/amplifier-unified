"""FUTURE native two-profile harness. Never enabled by ordinary PR test checks.

Requires the nominated H controller's exact admission and installed C context;
CANDIDATE_NATIVE_OUTPUT by itself is not qualification. No production Gate or
artifact validator is changed, and no model/provider is started.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get('CANDIDATE_NATIVE_OUTPUT'),
                               reason='Only separately nominated H hosted qualification runs these native cases.')


class NoModelRuntime:
    async def start(self, *args, **kwargs):
        raise AssertionError('Candidate native tests must not start a model')

    async def close(self):
        pass


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True, timeout=60).strip()


def source_digest(root):
    from candidate_native_protocol import digest
    return digest({p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(root.rglob('*')) if p.is_file() and '.git' not in p.relative_to(root).parts})


RUNTIME_PROBE = r'''import ctypes,hashlib,json,os,shutil,stat,sys,_struct
from pathlib import Path
contract=json.loads(Path(os.environ['AMPLIFIER_TEST_CONTRACT']).read_text())
r=contract['runtimeWorkdir']; runtime=Path(os.environ['AMPLIFIER_TEST_RUNTIME'])
assert str(runtime)==r['path']=='/unified-runtime'
assert (os.getuid(),os.getgid(),os.getgroups())==(65534,65534,[])
assert (runtime.stat().st_uid,runtime.stat().st_gid,stat.S_IMODE(runtime.stat().st_mode))==(65534,65534,0o700)
f=os.statvfs(runtime)
assert f.f_flag&(os.ST_NOSUID|os.ST_NODEV)==(os.ST_NOSUID|os.ST_NODEV)
assert not f.f_flag&(os.ST_RDONLY|os.ST_NOEXEC)
assert f.f_blocks*f.f_frsize==r['sizeMiB']*1024**2
assert r['tmpMiB']+r['sizeMiB']==int(sys.argv[1])
assert r['tmpMiB']==int(sys.argv[1])//4
assert os.statvfs('/tmp').f_flag&os.ST_NOEXEC
assert os.environ['HOME']=='/tmp/unified-test/home'
assert os.environ['XDG_CACHE_HOME']=='/tmp/unified-test/cache'
assert not os.access('/var/lib/unified-jobs',os.R_OK|os.W_OK)
assert os.statvfs('/').f_flag&os.ST_RDONLY
lib=runtime/'candidate-native.so';shutil.copyfile(_struct.__file__,lib);ctypes.CDLL(str(lib))
negative=Path('/tmp/candidate-native.so');shutil.copyfile(_struct.__file__,negative)
try: ctypes.CDLL(str(negative))
except OSError: pass
else: raise AssertionError('noexec /tmp allowed native mapping')
proof={'nativeMap':True,'tmpNoexec':True,'runtime':r,'uid':os.getuid(),'gid':os.getgid(),'contract':contract}
Path('/artifacts/runtime-proof.json').write_text(json.dumps(proof,sort_keys=True))
print(json.dumps(proof,sort_keys=True))
'''


@pytest.mark.asyncio
@pytest.mark.parametrize('profile', ['python-project', 'browser-test'])
async def test_prepared_runtime(profile, monkeypatch):
    from candidate_native_protocol import Registry, registration_plan, empty_report, save, require, dependencies_absent
    from candidate_native_execution import current_run
    from candidate_native_adapter import WheelInputs, candidate_test_cleanup
    from amplifier_web import task_environments as owner, test_environment_configs as configs
    from amplifier_web import test_dependency_layers
    from amplifier_web.service import AppService
    from amplifier_web.smart_tools import SmartToolsManager

    output = Path(os.environ['CANDIDATE_NATIVE_OUTPUT']).resolve()
    admission = json.loads((output / 'admission.json').read_text()); selection = admission['selection']
    installed = json.loads((output / 'installed.json').read_text())
    current = lambda: current_run(selection)
    current()
    inputs = WheelInputs(output / installed['wheelFile'], installed['wheelSha256'],
                         admission['sourceBlobs'], Path(owner.__file__).resolve().parent.parent)
    require(inputs.installation['prefix'] == installed['prefix'], 'Installed candidate prefix mismatch')
    save(output / 'module-origins.json', {'kind': admission['kind'], 'members': inputs.origins,
                                         'installation': inputs.installation})
    root = Path(installed['ownedRoot'])
    registry = Registry(selection, output / 'registry.json', current=current)
    repository = root / ('source-' + profile); repository.mkdir(mode=0o700)
    (repository / 'pyproject.toml').write_text('[project]\nname="prepared-native-fixture"\nversion="1.0.0"\nrequires-python=">=3.13"\ndependencies=["packaging==25.0"]\n[dependency-groups]\ndev=["pytest==8.4.2"]\n[tool.uv]\npackage=false\n')
    subprocess.run(['uv', 'lock', '--python', '3.13'], cwd=repository, check=True, capture_output=True, timeout=120)
    if profile == 'browser-test':
        (repository / 'package.json').write_text('{"private":true,"dependencies":{"playwright":"1.58.0"}}')
        subprocess.run(['npm', 'install', '--package-lock-only', '--ignore-scripts', '--no-audit', '--no-fund'],
                       cwd=repository, check=True, capture_output=True, timeout=120)
    git(repository, 'init', '-q', '-b', 'fixture')
    git(repository, 'add', '.')
    git(repository, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Locked native fixture')
    before, head = source_digest(repository), git(repository, 'rev-parse', 'HEAD')
    monkeypatch.setenv('AMPLIFIER_HOME', str(root / ('native-home-' + profile)))
    app = AppService(root / 'app', NoModelRuntime(), workspace=repository)
    app.smart_tools = SmartToolsManager(app)
    session = app._new_session({'title': 'Candidate test ' + profile})
    app.state['sessions'] = [session]; app.state['selectedSessionId'] = session['id']; app._publish()
    tool_id = hashlib.sha256(installed['coreCommit'].encode()).hexdigest()[:24]
    if not any(x['id'] == tool_id for x in app.smart_tools.state['installations']):
        app.smart_tools.state['installations'].append({'id': tool_id, 'status': 'installed', 'commit': installed['coreCommit'],
            'repository': owner.REPOSITORY, 'extras': [], 'python': installed['corePython']})
    config_value = {'enabled': True, 'installationId': tool_id, 'commit': installed['coreCommit'], 'backend': 'docker',
                    'dockerHost': 'unix:///var/run/docker.sock', 'image': installed['image'],
                    'maxRunning': 1, 'maxInstances': 1, 'minFreeBytes': 1024 ** 3}
    save(app.data_dir / 'test-environments.json', config_value)
    config, availability, reason = owner.configuration(app)
    require(availability == 'ready', reason)
    workspace = next(x for x in app.state['workspaces'] if x['path'] == str(repository))
    sid = session['id']; principal = session.get('runtimeSessionId') or session.get('nativeIdentity') or sid
    actor = {'sessionId': sid, 'principal': principal}
    request_id = profile + '-start'
    # Metadata-only allocation lets H register exact immutable identities before
    # snapshot preparation/build/launch. Normal dispatch reuses this exact intent.
    row = owner.prepare(app.data_dir, sid, workspace['id'], actor, str(repository), configs.key(config),
        request_id, config.get('installationId'), config, profile, compose_options={'preserveSource': True})
    capacity = owner.invoke(config, app.data_dir, row, 'capacity', {'requestId': profile + '-capacity'})
    backend = {'backend': 'docker', 'daemon': capacity['capacity']['scope']['daemon']}
    folder = owner.storage.directory(app.data_dir, row)
    probe = ('from pathlib import Path;from digital_twin_universe import lib;from digital_twin_universe.schemas import BackendConfig;'
             'import sys;print(lib.launch_id(sys.argv[2],config=BackendConfig(state_root=Path(sys.argv[1])/"backend",docker_host="unix:///var/run/docker.sock")))')
    universe = subprocess.check_output([config['python'], '-I', '-c', probe, str(folder), row['launchOperationId']], text=True, timeout=30).strip()
    plan = registration_plan(selection, profile=profile, owner=sid, root=folder, source=repository, backend=backend,
        resource=row['id'], launch=row['launchOperationId'], universe=universe, source_digest=before,
        nonce=uuid.uuid4().hex + uuid.uuid4().hex)
    ack = registry.register(plan)
    registry.consume(plan, ack, action='start')
    evidence = {'profile': profile, 'passed': False, 'calls': [], 'registration': {'plan': plan, 'ack': ack}}
    report_path = output / 'report.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else empty_report(selection, wheel=inputs.sha256)
    report['wheelFile'] = installed['wheelFile']
    report['cases'].append(evidence)
    save(report_path, report)

    async def act(action, **values):
        nonlocal row
        registry.recheck(row['id'], action=action)
        require(source_digest(repository) == before and git(repository, 'rev-parse', 'HEAD') == head, 'Original source changed')
        arguments = {'workspaceId': workspace['id'], 'sessionId': sid, 'requestId': profile + '-' + action, **values}
        if action == 'start': arguments.update(profileId=profile, preserveSource=True)
        else: arguments.update(id=row['id'], expectedRevision=row['revision'])
        evidence['calls'].append({'action': action, 'phase': 'before-effect'})
        save(report_path, report)
        value = (await app.dispatch('workspace.environments.' + action, arguments,
                                  command_id='candidate-native-' + arguments['requestId']))['result']
        evidence['calls'][-1] = {'action': action, 'result': value}
        save(report_path, report)
        row = owner.get(app.data_dir, sid, row['id'])
        require(value['request']['state'] == 'completed', 'Original operation not completed; do not replay')
        return value

    try:
        await act('start')
        require(row['id'] == plan['resource'] and row['universeId'] == universe and row['backendIdentity'] == backend,
                'Started owner differs from registration')
        registry.bind_snapshot(row['id'], row['snapshot'])
        contract = owner.public(app.data_dir, row)['executionContract']
        require(contract['runtimeWorkdir']['path'] == '/unified-runtime', 'New candidate prepared contract missing')
        total = row['resourceRequest']['tmpMiB']
        command = shlex.join(['python', '-c', RUNTIME_PROBE, str(total)])
        if profile == 'browser-test':
            command += ' && node --version && command -v Xvfb && xvfb-run -a python -c ' + shlex.quote(
                "from playwright.sync_api import sync_playwright;p=sync_playwright().start();b=p.chromium.launch(args=['--no-sandbox']);"
                "page=b.new_page();page.set_content('<title>prepared</title>');assert page.title()=='prepared';b.close();p.stop()")
        checked = await act('execute', command=command, timeoutSeconds=120)
        execution = checked['request']['result']['result']
        require(execution['exit_code'] == 0, 'Native mapping or noexec negative failed')
        evidence.update(nativeMap=True, tmpNoexec=True, execution=execution)
    finally:
        # Only known states admit ordinary cleanup. UNKNOWN is retained and
        # blocks profile two; never raw Docker fallback or a new remove identity.
        try:
            row = owner.get(app.data_dir, sid, row['id'])
            if row.get('environmentState') == 'running': await act('stop')
            if row.get('environmentState') in ('stopped', 'not-started'):
                with candidate_test_cleanup(app, inputs, registry, row['id']):
                    removed = await act('remove')
                result = removed['request']['result']
                require(result.get('retention', {}).get('verified') is True, 'Retention not verified')
                archives = result.get('archives') or result.get('guestRemoval', {}).get('archives')
                require(isinstance(archives, list) and bool(archives), 'Missing original retained artifacts')
                kept = []
                for archive in archives:
                    path = Path(archive['destination'])
                    body_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                    require(body_hash == archive['sha256'], 'Retained archive changed')
                    destination = output / (profile + '-' + path.name)
                    shutil.copyfile(path, destination); kept.append(body_hash)
                    if archive['source'] == '/artifacts':
                        with tarfile.open(path) as stream:
                            members = [m for m in stream.getmembers() if m.isfile() and m.name.endswith('/runtime-proof.json')]
                            require(len(members) == 1, 'Native artifact missing or duplicated')
                            proof = json.loads(stream.extractfile(members[0]).read())
                            require(proof['nativeMap'] is True and proof['tmpNoexec'] is True, 'Native artifact failed')
                        evidence['artifactSha256'] = body_hash
                observed = owner.invoke(config, app.data_dir, row, 'reconcile', {'requestId': profile + '-independent-absence'})
                save(output / (profile + '-absence.json'), observed)
                require(observed['observation']['absenceConfirmed'] is True, 'Independent absence unverified')
                retired = []
                inspected = test_dependency_layers.inspect(app.data_dir)
                evidence['dependencyBeforeRetirement'] = inspected
                require(inspected['complete'] is True and inspected['protectedIds'] == [],
                        'Dependency custody unresolved before retirement')
                test_dependency_layers.retire(app.data_dir, {x['id']: x for x in inspected['candidates']},
                                             lambda state, item: retired.append({'state': state, **item}))
                evidence['dependencyRetirement'] = retired
                inspected = test_dependency_layers.inspect(app.data_dir)
                evidence['dependencyAfterRetirement'] = inspected
                dependencies_absent(inspected)
                evidence.update(cleanup='completed', absent=True)
                registry.settle(row['id'], cleanup='completed', absent=True)
            else:
                evidence.update(cleanup='unknown', absent=False)
                registry.settle(row['id'], cleanup='unknown', absent=False)
            evidence['sourceUnchanged'] = source_digest(repository) == before and git(repository, 'rev-parse', 'HEAD') == head
            evidence['passed'] = bool(evidence.get('nativeMap') and evidence.get('tmpNoexec')
                and evidence.get('cleanup') == 'completed' and evidence.get('absent') and evidence['sourceUnchanged'])
            require(evidence['passed'], 'Native/retention/cleanup case is incomplete')
        finally:
            save(report_path, report)
            await app.close()
