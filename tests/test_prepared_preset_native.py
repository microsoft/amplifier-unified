"""Opt-in installed-candidate Python/browser checks, not production eligibility."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile

import pytest

from amplifier_web import task_environments as owner, test_dependency_layers
from amplifier_web.service import AppService
from amplifier_web.smart_tools import SmartToolsManager
from native_cleanup_fixture import checkout_cleanup, require_absent, require_prior_cleanup

pytestmark = pytest.mark.skipif(os.environ.get('CHECK_SUITE') != 'prepared-runtime-native',
                               reason='Ordinary manual prepared-runtime-native suite owns the disposable runner')


class NoModelRuntime:
    async def start(self, *args, **kwargs):
        raise AssertionError('Native tests must not start a model')

    async def close(self):
        pass


def command(*args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=120, **kwargs).strip()


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


PROBE = r'''import ctypes,json,os,packaging,pytest,shutil,stat,sys,_struct
from pathlib import Path
assert sys.prefix=='/opt/unified-prepared/venv'
assert packaging.__version__=='25.0' and pytest.__version__=='8.4.2'
contract=json.loads(Path(os.environ['AMPLIFIER_TEST_CONTRACT']).read_text())
r=contract['runtimeWorkdir']; runtime=Path(os.environ['AMPLIFIER_TEST_RUNTIME'])
assert str(runtime)==r['path']=='/unified-runtime'
assert (os.getuid(),os.getgid(),os.getgroups())==(65534,65534,[])
assert (runtime.stat().st_uid,runtime.stat().st_gid,stat.S_IMODE(runtime.stat().st_mode))==(65534,65534,0o700)
f=os.statvfs(runtime)
assert f.f_flag&(os.ST_NOSUID|os.ST_NODEV)==(os.ST_NOSUID|os.ST_NODEV)
assert not f.f_flag&(os.ST_RDONLY|os.ST_NOEXEC)
assert f.f_blocks*f.f_frsize==r['sizeMiB']*1024**2
assert r['tmpMiB']+r['sizeMiB']==int(sys.argv[1]) and r['tmpMiB']==int(sys.argv[1])//4
assert os.statvfs('/tmp').f_flag&os.ST_NOEXEC and os.statvfs('/').f_flag&os.ST_RDONLY
assert os.environ['HOME']=='/tmp/unified-test/home' and os.environ['XDG_CACHE_HOME']=='/tmp/unified-test/cache'
assert not os.access('/var/lib/unified-jobs',os.R_OK|os.W_OK)
lib=runtime/'native.so';shutil.copyfile(_struct.__file__,lib);ctypes.CDLL(str(lib))
negative=Path('/tmp/native.so');shutil.copyfile(_struct.__file__,negative)
try: ctypes.CDLL(str(negative))
except OSError: pass
else: raise AssertionError('noexec /tmp permitted native mapping')
proof={'nativeMap':True,'tmpNoexec':True,'runtime':r,'uid':os.getuid(),'gid':os.getgid(),'contract':contract}
Path('/artifacts/runtime-proof.json').write_text(json.dumps(proof,sort_keys=True))
print(json.dumps(proof,sort_keys=True))
'''


@pytest.mark.parametrize('profile', ['python-project', 'browser-test'])
async def test_native_default_prepared_preset_runtime(profile, monkeypatch):
    root = Path(os.environ['CLEANUP_NATIVE_ROOT']).resolve()
    output = Path(os.environ['CLEANUP_NATIVE_OUTPUT']).resolve()
    require_prior_cleanup(output, profile)
    report = {'profile': profile, 'passed': False, 'cleanupComplete': False, 'calls': []}
    result_path = output / (profile + '.json')
    assert not result_path.exists(), 'Inspect original attempt, not a replay'
    save(result_path, report)
    source = root / ('source-' + profile); source.mkdir()
    (source / 'pyproject.toml').write_text(
        '[project]\nname="prepared-proof"\nversion="1.0.0"\nrequires-python=">=3.13"\n'
        'dependencies=["packaging==25.0"]\n[dependency-groups]\ndev=["pytest==8.4.2"]\n[tool.uv]\npackage=false\n')
    command('uv', 'lock', '--python', '3.13', cwd=source)
    if profile == 'browser-test':
        (source / 'package.json').write_text('{"private":true,"dependencies":{"playwright":"1.58.0"}}')
        command('npm', 'install', '--package-lock-only', '--ignore-scripts', '--no-audit', '--no-fund', cwd=source)
    command('git', 'init', '-q', '-b', 'fixture', cwd=source)
    command('git', 'add', '.', cwd=source)
    command('git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Locked fixture', cwd=source)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir() if p.is_file()}
    monkeypatch.setenv('AMPLIFIER_HOME', str(root / ('home-' + profile)))
    app = AppService(root / 'app', NoModelRuntime(), workspace=source)
    app.smart_tools = SmartToolsManager(app)
    # A reused app data home retains its original workspace. Select this
    # profile's fixture explicitly, just as an ordinary caller would.
    await app.dispatch('workspace.add', {'path': str(source)})
    session = app._new_session({'title': 'Prepared ' + profile})
    app.state['sessions'] = [session]; app.state['selectedSessionId'] = session['id']; app._publish()
    tool_id = hashlib.sha256(owner.DTU_COMMIT.encode()).hexdigest()[:24]
    if not any(r['id'] == tool_id for r in app.smart_tools.state['installations']):
        app.smart_tools.state['installations'].append({'id': tool_id, 'status': 'installed', 'commit': owner.DTU_COMMIT,
            'repository': owner.REPOSITORY, 'extras': [], 'python': os.environ['UNIFIED_DTU_PYTHON']})
    config_path = app.data_dir / 'test-environments.json'
    save(config_path, {'enabled': True, 'installationId': tool_id, 'commit': owner.DTU_COMMIT,
        'backend': 'docker', 'dockerHost': 'unix:///var/run/docker.sock', 'image': owner.APPROVED_IMAGE,
        'maxRunning': 1, 'maxInstances': 1, 'minFreeBytes': 1024**3})
    config_path.chmod(0o600)
    config, availability, reason = owner.configuration(app)
    assert availability == 'ready', reason
    workspace = next(w for w in app.state['workspaces'] if w['path'] == str(source))
    row = None
    checkout_cleanup(monkeypatch)

    async def act(action, **values):
        nonlocal row
        args = {'workspaceId': workspace['id'], 'sessionId': session['id'], 'requestId': profile + '-' + action, **values}
        if row: args.update(id=row['id'], expectedRevision=row['revision'])
        report['calls'].append({'action': action, 'phase': 'before-effect'})
        save(result_path, report)
        try:
            result = (await app.dispatch('workspace.environments.' + action, args,
                                        command_id='native-' + args['requestId']))['result']
            report['calls'][-1] = {'action': action, 'result': result}
            if result.get('resource'):
                row = owner.get(app.data_dir, session['id'], result['resource']['id'])
            assert result['request']['state'] == 'completed', result
            return result
        finally:
            if row:
                row = owner.get(app.data_dir, session['id'], row['id'])
            else:
                original = app.operations.requests.read(session['id'], args['requestId'])
                report['originalRequest'] = original
                if original and original.get('resourceId'):
                    row = owner.get(app.data_dir, session['id'], original['resourceId'])
            save(result_path, report)

    try:
        started = await act('start', profileId=profile, preserveSource=True)
        row = owner.get(app.data_dir, session['id'], started['resource']['id'])
        report['owner'] = row
        assert row['preparedDependencies']['backend'] == 'docker'
        report['executionContract'] = started['resource']['executionContract']
        probe = shlex.join(['python', '-c', PROBE, str(row['resourceRequest']['tmpMiB'])])
        if profile == 'browser-test':
            probe += ' && node --version && test -f /workspace/node_modules/playwright/cli.js && command -v Xvfb && xvfb-run -a python -c ' + shlex.quote(
                "from playwright.sync_api import sync_playwright;p=sync_playwright().start();b=p.chromium.launch(args=['--no-sandbox']);"
                "page=b.new_page();page.set_content('<title>prepared</title>');assert page.title()=='prepared';b.close();p.stop()")
        checked = await act('execute', command=probe, timeoutSeconds=120)
        execution = checked['request']['result']['result']
        assert execution['exit_code'] == 0, execution
        report['runtimeProof'] = execution
        assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir() if p.is_file()}
        assert not (source / 'node_modules').exists() and not (source / '.venv').exists()
        report['passed'] = True
    finally:
        try:
            if row:
                row = owner.get(app.data_dir, session['id'], row['id'])
                pending = row.get('pendingRequest')
                assert not pending, 'Unsettled original operation; no cleanup/replay under a new identity'
                if row.get('environmentState') == 'running': await act('stop')
                assert row.get('environmentState') in {'stopped', 'not-started'}, row
                removed = await act('remove')
                result = removed['request']['result']
                assert result['retention']['verified'] is True
                archives = result.get('archives') or result.get('guestRemoval', {}).get('archives')
                assert archives
                for archive in archives:
                    path = Path(archive['destination'])
                    assert hashlib.sha256(path.read_bytes()).hexdigest() == archive['sha256']
                    shutil.copyfile(path, output / (profile + '-' + path.name))
                    if archive['source'] == '/artifacts' and report['passed']:
                        with tarfile.open(path) as t:
                            members = [m for m in t.getmembers() if m.isfile() and m.name.endswith('/runtime-proof.json')]
                            assert len(members) == 1
                            proof = json.loads(t.extractfile(members[0]).read())
                            assert proof['nativeMap'] is proof['tmpNoexec'] is True
                observed = owner.invoke(config, app.data_dir, row, 'reconcile', {'requestId': profile + '-absence'})
                report['absence'] = observed
                assert observed['observation']['absenceConfirmed'] is True
                inspected = test_dependency_layers.inspect(app.data_dir)
                assert inspected['complete'] and not inspected['protectedIds'], inspected
                retired = []
                test_dependency_layers.retire(app.data_dir, {x['id']: x for x in inspected['candidates']},
                                             lambda state, item: retired.append({'state': state, **item}))
                report['dependencyRetirement'] = retired
                report['dependencyAfter'] = test_dependency_layers.inspect(app.data_dir)
                require_absent(report['dependencyAfter'])
                report['cleanupComplete'] = True
        finally:
            if row:
                report['finalOwner'] = owner.get(app.data_dir, session['id'], row['id'])
                shutil.copytree(owner.storage.directory(app.data_dir, row), output / (profile + '-owner'), dirs_exist_ok=True)
            save(result_path, report)
            await app.close()
