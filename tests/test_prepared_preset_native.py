"""Opt-in actual default preset preparation; no model or runner certification."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from types import SimpleNamespace

import pytest

from amplifier_web import task_environments as environments
from amplifier_web import test_environment_configs as configs
from amplifier_web import test_environment_setup as setup
from amplifier_web.operations import Operations
from test_thin_docker_smart_tool import git


@pytest.mark.skipif(not os.environ.get('UNIFIED_PREPARED_PROOF_ROOT'),
                    reason='Opt-in actual default Python/browser preparation with registered owned resources.')
def test_native_default_prepared_preset_runtime():
    home = Path(os.environ['UNIFIED_PREPARED_PROOF_ROOT']).resolve()
    profile = os.environ['UNIFIED_PREPARED_PROFILE']
    assert profile in {'python-project', 'browser-test'}
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    result_path = home / 'result.json'
    assert not result_path.exists(), 'Inspect the original attempt; do not replay a preparation.'
    source = home / 'source'
    source.mkdir()
    (source / 'pyproject.toml').write_text(
        '[project]\nname="prepared-preset-proof"\nversion="1.0.0"\nrequires-python=">=3.13"\n'
        'dependencies=["packaging==25.0"]\n[dependency-groups]\ndev=["pytest==8.4.2"]\n[tool.uv]\npackage=false\n')
    subprocess.run(['uv', 'lock', '--python', '3.13'], cwd=source, check=True, capture_output=True)
    if profile == 'browser-test':
        (source / 'package.json').write_text('{"private":true,"dependencies":{"playwright":"1.58.0"}}')
        subprocess.run(['npm', 'install', '--package-lock-only', '--ignore-scripts', '--no-audit', '--no-fund'],
                       cwd=source, check=True, capture_output=True)
    git(source, 'init', '-q', '-b', 'prepared-preset')
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Proof', '-c', 'user.email=proof@example.invalid', 'commit', '-qm', 'Locked preset fixture')
    config = {'python': os.environ['UNIFIED_DTU_PYTHON'], 'backend': 'docker',
              'dockerHost': 'unix:///var/run/docker.sock', 'image': environments.APPROVED_IMAGE,
              'admissionRoot': str(home / 'admission')}
    app = SimpleNamespace(data_dir=home)
    app.operations = Operations(app)
    actor = {'sessionId': 'prepared-' + profile, 'principal': 'owned-preset-proof'}
    row = environments.prepare(home, actor['sessionId'], 'preset-proof', actor, str(source),
        configs.key(config), 'prepared-' + profile, backend_config=config, profile_id=profile)
    assert row['preparationRequired'] and not row.get('composeFile')
    root = environments.storage.directory(home, row)
    script = ("from pathlib import Path;from digital_twin_universe import lib;"
              "from digital_twin_universe.schemas import BackendConfig;import sys;"
              "print(lib.launch_id(sys.argv[2],config=BackendConfig(state_root=Path(sys.argv[1])/'backend',docker_host='unix:///var/run/docker.sock')))")
    universe = subprocess.check_output([config['python'], '-I', '-B', '-c', script,
                                       str(root), row['launchOperationId']], text=True).strip()
    builder = 'au-prepare-' + hashlib.sha256((str(root) + 'preset-' + profile + '-start').encode()).hexdigest()[:24]
    report = {'profileId': profile, 'row': row, 'universeId': universe, 'builder': builder,
              'sourceHead': git(source, 'rev-parse', 'HEAD'), 'calls': [], 'result': 'awaiting-registration'}
    setup.write_json(home / 'plan.json', report)
    deadline = time.monotonic() + 300
    while not (home / 'registered.json').exists():
        assert time.monotonic() < deadline, 'Register exact proof IDs before launch; no infrastructure created.'
        time.sleep(.1)
    def effect(action, **values):
        nonlocal row
        args = {'requestId': 'preset-' + profile + '-' + action, 'expectedRevision': row['revision'], **values}
        request, fresh = app.operations.requests.begin(row['sessionId'], 'workspace.environments.' + action,
                                                      args, actor, resource_id=row['id'])
        assert fresh
        report['calls'].append({'action': action, 'requestId': args['requestId'], 'state': 'admitting'})
        setup.write_json(result_path, report)
        outcome = environments.effect(app, row, action, args, config, request, lambda: None)
        report['calls'][-1].update(state='returned', result=outcome)
        report['dependencyLayers'] = [
            {'entry': str(path.parent.parent), **json.loads(path.read_text())}
            for path in (home / 'test-dependency-layers').glob('*/preparation/receipt.json')]
        setup.write_json(result_path, report)
        row = environments.get(home, row['sessionId'], row['id'])
        return outcome
    try:
        started = effect('start')
        assert started['request']['state'] == 'completed', started
        assert row['preparedDependencies']['backend'] == 'docker'
        report['preparedImage'] = row['preparedDependencies']['image']
        report['executionContract'] = started['resource']['executionContract']
        probe = ("import os,sys,json,packaging,pytest;from pathlib import Path;"
                 "assert sys.prefix=='/opt/unified-prepared/venv';"
                 "assert packaging.__version__=='25.0';assert pytest.__version__=='8.4.2';"
                 "assert Path(os.environ['AMPLIFIER_TEST_CONTRACT']).is_file();"
                 "print(json.dumps({'python':sys.executable,'prefix':sys.prefix,'packaging':packaging.__version__,'pytest':pytest.__version__}))")
        command = 'python -c ' + shlex.quote(probe)
        if profile == 'browser-test':
            browser = ("from playwright.sync_api import sync_playwright;"
                       "p=sync_playwright().start();b=p.chromium.launch(headless=True,args=['--no-sandbox']);"
                       "page=b.new_page();page.set_content('<title>prepared-browser</title>');"
                       "assert page.title()=='prepared-browser';print('chromium='+b.version);b.close();p.stop()")
            command += (' && node --version && test -f /workspace/node_modules/playwright/cli.js'
                        ' && command -v Xvfb && command -v xvfb-run && xvfb-run -a python -c ' + shlex.quote(browser))
        checked = effect('execute', command=command, timeoutSeconds=120)
        execution = checked['request']['result']['result']
        assert checked['request']['state'] == 'completed' and execution['exit_code'] == 0, checked
        report['runtimeProof'] = execution
        assert git(source, 'rev-parse', 'HEAD') == report['sourceHead']
        assert not (source / 'node_modules').exists() and not (source / '.venv').exists()
        report['sourceUnchanged'] = True
    finally:
        if row.get('environmentState') == 'running':
            assert effect('stop')['request']['state'] == 'completed'
        if row.get('environmentState') in {'stopped', 'not-started'}:
            report['cleanup'] = effect('remove')
            assert report['cleanup']['request']['state'] == 'completed', report['cleanup']
        report['result'] = 'passed' if report.get('runtimeProof', {}).get('exit_code') == 0 and row['status'] == 'retired' else 'incomplete'
        from amplifier_web import test_dependency_layers
        evidence = []
        candidates = test_dependency_layers.inspect(home)['candidates']
        test_dependency_layers.retire(home, {item['id']: item for item in candidates},
                                      lambda state, item: evidence.append({'state': state, **item}))
        report['dependencyLayerRetirement'] = evidence
        setup.write_json(result_path, report)
        app.operations.journal.close()