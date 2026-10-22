"""Parallel release jobs must qualify and publish one immutable candidate."""
import importlib.util
import json
from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('qualification', ROOT / 'scripts/release_qualification.py')
qualification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualification)


@pytest.fixture
def selected(tmp_path, monkeypatch):
    evidence = tmp_path / 'host'
    evidence.mkdir()
    qualification.write(evidence / 'resolution.json', {'ok': True, 'resolved': [{'name': 'example', 'version': '1.0'}]})
    (evidence / 'qualified-overrides.txt').write_text('example==1.0\n')
    (evidence / 'development.lock').write_text('development inputs')
    app = {'revision': 'a' * 40, 'version': '1.2.3'}
    monkeypatch.setattr(qualification, 'identity', lambda root: app.copy())
    expected = qualification.candidate(tmp_path, evidence)
    return tmp_path, evidence, expected, app


@pytest.mark.parametrize('change', ['app', 'host', 'candidate'])
def test_candidate_refuses_changed_commit_graph_or_receipt(selected, change):
    root, evidence, expected, app = selected
    qualification.verify_candidate(root, evidence, expected)
    if change == 'app':
        app['revision'] = 'b' * 40
    elif change == 'host':
        (evidence / 'qualified-overrides.txt').write_text('example==2.0\n')
    else:
        value = json.loads((evidence / 'candidate.json').read_text())
        value['version'] = '9.9.9'
        qualification.write(evidence / 'candidate.json', value)
    with pytest.raises(ValueError):
        qualification.verify_candidate(root, evidence, expected)


@pytest.fixture
def qualified(selected, monkeypatch):
    root, evidence, expected, app = selected
    dist = root / 'dist'
    dist.mkdir()
    for name in ['app.whl', 'app.tar.gz', 'SHA256SUMS']:
        (dist / name).write_text(name)
    runtime = root / 'runtime'
    runtime.mkdir()
    snapshot = {'build': 'build-hash', 'recipes': 'b' * 40, 'graph': [{'name': 'core', 'version': '2.0'}]}
    qualification.write(runtime / 'qualified-runtime.json', snapshot)
    monkeypatch.setattr(qualification, 'runtime_snapshot', lambda *args: snapshot.copy())
    receipts = root / 'receipts'
    receipts.mkdir()
    qualification.write(receipts / 'qualified-runtime.json', snapshot)
    for lane in qualification.LANES:
        qualification.receipt(root, evidence, expected, lane, receipts / (lane + '.json'), runtime, dist)
    return root, evidence, expected, receipts, dist, runtime, snapshot


def test_all_three_jobs_and_unchanged_distributions_are_required(qualified):
    root, evidence, expected, receipts, dist, *_ = qualified
    qualification.verify_receipts(root, evidence, expected, receipts, dist)
    (receipts / 'frontend.json').unlink()
    with pytest.raises(FileNotFoundError):
        qualification.verify_receipts(root, evidence, expected, receipts, dist)


def test_old_browser_receipt_cannot_replace_frontend_qualification(qualified):
    root, evidence, expected, receipts, dist, *_ = qualified
    (receipts / 'frontend.json').rename(receipts / 'browser.json')
    with pytest.raises(FileNotFoundError):
        qualification.verify_receipts(root, evidence, expected, receipts, dist)


def test_old_python_receipt_cannot_replace_package_qualification(qualified):
    root, evidence, expected, receipts, dist, *_ = qualified
    (receipts / 'package.json').rename(receipts / 'python.json')
    with pytest.raises(FileNotFoundError):
        qualification.verify_receipts(root, evidence, expected, receipts, dist)


@pytest.mark.parametrize('change', ['other-candidate', 'wrong-lane', 'wheel', 'extra-artifact', 'runtime'])
def test_promotion_rejects_mixed_or_tampered_evidence(qualified, change):
    root, evidence, expected, receipts, dist, *_ = qualified
    if change in {'other-candidate', 'wrong-lane'}:
        value = json.loads((receipts / 'frontend.json').read_text())
        value['candidate' if change == 'other-candidate' else 'lane'] = 'other'
        qualification.write(receipts / 'frontend.json', value)
    elif change == 'wheel':
        (dist / 'app.whl').write_text('different bytes')
    elif change == 'extra-artifact':
        (dist / 'unexpected.whl').write_text('different artifact')
    else:
        qualification.write(receipts / 'qualified-runtime.json', {'graph': []})
    with pytest.raises(ValueError):
        qualification.verify_receipts(root, evidence, expected, receipts, dist)


@pytest.mark.parametrize('change', ['graph', 'native-binary'])
def test_runtime_changes_after_tests_cannot_produce_a_receipt(qualified, change):
    root, evidence, expected, receipts, dist, runtime, snapshot = qualified
    if change == 'graph':
        snapshot['graph'] = [{'name': 'core', 'version': 'new-untested-version'}]
    else:
        snapshot['coreWheel'] = {'native': [{'sha256': 'changed-binary'}]}
    with pytest.raises(ValueError, match='changed during qualification'):
        qualification.receipt(root, evidence, expected, 'runtime', receipts / 'runtime.json', runtime, dist)


@pytest.fixture
def runtime_build(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    for name in ['uv.lock', 'pyproject.toml', 'build-constraints.txt']:
        (runtime / name).write_text(name)
    monkeypatch.setattr(qualification, 'checkout_identity', lambda path: 'a' * 40)
    monkeypatch.setattr(qualification, 'run', lambda *args: ' '.join(args))
    monkeypatch.setattr(qualification.importlib.metadata, 'version', lambda name: '1.9.0')
    return tmp_path, runtime


@pytest.mark.parametrize('change', ['resolved-source', 'loop-live', 'recipes', 'python-abi', 'architecture', 'rustc', 'maturin', 'build-flags'])
def test_cache_identity_changes_with_every_build_input(runtime_build, monkeypatch, change):
    root, runtime = runtime_build
    before = qualification.digest(qualification.build_identity(root, runtime))
    if change == 'resolved-source':
        (runtime / 'uv.lock').write_text('same main declaration, new resolved commit')
    elif change in {'loop-live', 'recipes'}:
        monkeypatch.setattr(qualification, 'checkout_identity', lambda path: ('b' if path.name == change else 'a') * 40)
    elif change == 'python-abi':
        monkeypatch.setattr(qualification.sysconfig, 'get_config_var', lambda name: 'different-abi')
    elif change == 'architecture':
        monkeypatch.setattr(qualification.platform, 'machine', lambda: 'different-architecture')
    elif change == 'rustc':
        monkeypatch.setattr(qualification, 'run', lambda *args: 'changed toolchain' if args[0] == 'rustc' else ' '.join(args))
    elif change == 'maturin':
        monkeypatch.setattr(qualification.importlib.metadata, 'version', lambda name: '2.0.0')
    else:
        monkeypatch.setenv('RUSTFLAGS', '-C opt-level=1')
    assert qualification.digest(qualification.build_identity(root, runtime)) != before


def test_cache_identity_does_not_include_credentials(runtime_build, monkeypatch):
    root, runtime = runtime_build
    before = qualification.build_identity(root, runtime)
    monkeypatch.setenv('LOOP_LIVE_CI_READ_KEY', 'private-key-never-export')
    monkeypatch.setenv('GH_TOKEN', 'token-never-export')
    after = qualification.build_identity(root, runtime)
    assert before == after
    assert 'never-export' not in json.dumps(after)
    monkeypatch.setenv('CFLAGS', '-DPRIVATE_TOKEN=private-definition-never-export')
    with_flags = qualification.build_identity(root, runtime)
    assert with_flags != after
    assert 'never-export' not in json.dumps(with_flags)


@pytest.mark.parametrize('change', ['none', 'source-install', 'missing-wheel', 'pure-python', 'missing-native', 'cli'])
def test_core_wheel_receipt_rejects_source_or_cli_installations(monkeypatch, change):
    value = {'version': '2.0.1', 'direct': None, 'wheel': 'Root-Is-Purelib: false\nTag: cp311-abi3-manylinux_2_17_x86_64',
             'native': [{'name': 'amplifier_core/_engine.abi3.so', 'sha256': 'a' * 64}], 'cliInstalled': False}
    if change == 'source-install':
        value['direct'] = {'url': 'https://github.com/microsoft/amplifier-core', 'vcs_info': {'vcs': 'git'}}
    elif change == 'missing-wheel':
        value['wheel'] = None
    elif change == 'pure-python':
        value['wheel'] = 'Root-Is-Purelib: true'
    elif change == 'missing-native':
        value['native'] = []
    elif change == 'cli':
        value['cliInstalled'] = True
    monkeypatch.setattr(qualification, 'run', lambda *args: json.dumps(value))
    if change == 'none':
        assert qualification.core_wheel(Path('python')) == value
    else:
        with pytest.raises(ValueError, match='registry native wheel'):
            qualification.core_wheel(Path('python'))


def test_runtime_project_constrains_the_recorded_build_backend(tmp_path, monkeypatch):
    source = tmp_path / 'amplifier_web/runtime_deps'
    source.mkdir(parents=True)
    (source / 'pyproject.toml').write_text('[project]\nname="fixture"\n[tool.uv]\noverride-dependencies=[]\n')
    monkeypatch.setattr(qualification.importlib.metadata, 'version', lambda name: '1.9.0')
    runtime = tmp_path / 'runtime'
    qualification.runtime_project(tmp_path, runtime)
    assert 'build-constraint-dependencies = ["maturin==1.9.0"]' in (runtime / 'pyproject.toml').read_text()
    assert (runtime / 'build-constraints.txt').read_text() == 'maturin==1.9.0\n'


def test_workflow_requires_parallel_lanes_before_write_permission():
    jobs = yaml.load((ROOT / '.github/workflows/release.yml').read_text(), Loader=yaml.BaseLoader)['jobs']
    for lane in qualification.LANES:
        assert jobs[lane]['needs'] == 'prepare'
        # Proof lookup needs Actions artifact reads, never write permission.
        if lane == 'frontend':
            assert jobs[lane]['permissions'] == {'contents': 'read', 'actions': 'read'}
        else:
            assert 'permissions' not in jobs[lane]
    assert set(jobs['release']['needs']) == {'prepare', *qualification.LANES}
    assert jobs['release']['permissions'] == {'contents': 'write'}
    steps = jobs['release']['steps']
    verify = next(i for i, step in enumerate(steps) if 'verify-receipts' in step.get('run', ''))
    publish = next(i for i, step in enumerate(steps) if 'publish --tag' in step.get('run', ''))
    assert verify < publish


def test_exhaustive_checks_follow_publication_at_the_exact_released_source():
    workflows = ROOT / '.github/workflows'
    manual = yaml.load((workflows / 'browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(manual['on']) == {'workflow_dispatch', 'workflow_call'}
    assert manual['permissions'] == {'contents': 'read'}
    commands = '\n'.join(step.get('run', '') for step in manual['jobs']['browser']['steps'])
    assert 'playwright install' in commands
    assert 'scripts/browser_check_runner.py' in commands
    plan = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())
    invocations = [row['command'] for row in plan['scenarios']]
    assert ['node', 'tests/session-health-browser.mjs'] in invocations
    assert ['node', 'tests/session-health-browser.mjs', '--context-limit', '--active-worker'] in invocations
    assert ['node', 'tests/connectors-browser.mjs'] in invocations
    critical = yaml.load((workflows / 'public-readiness.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(critical['on']) == {'workflow_dispatch', 'workflow_call'}
    assert critical['permissions'] == {'contents': 'read'}
    steps = critical['jobs']['critical']['steps']
    assert steps[0]['with']['ref'] == '${{ inputs.revision || github.sha }}'
    commands = '\n'.join(step.get('run', '') for step in steps)
    for check in ('pytest', 'test_bundle_selection.py', 'test_service.py', 'test_browser_detail.py',
                  'npm test', 'recovery-group-browser.mjs', 'chat-menu-browser.mjs', 'public-first-chat-browser.mjs'):
        assert check in commands
    release = yaml.load((workflows / 'release.yml').read_text(), Loader=yaml.BaseLoader)
    assert 'public-readiness' not in release['jobs']
    post = yaml.load((workflows / 'post-release.yml').read_text(), Loader=yaml.BaseLoader)
    assert post['on']['workflow_run'] == {'workflows': ['Publish application release'], 'types': ['completed']}
    assert post['permissions'] == {'contents': 'read'}
    for lane in ('readiness', 'python', 'browser'):
        assert post['jobs'][lane]['needs'] == 'resolve'
        assert post['jobs'][lane]['with']['revision'] == '${{ needs.resolve.outputs.revision }}'
        if lane == 'python':
            assert post['jobs'][lane]['secrets'] == {
                'LOOP_LIVE_CI_READ_KEY': '${{ secrets.LOOP_LIVE_CI_READ_KEY }}'}
        else:
            assert 'secrets' not in post['jobs'][lane]
    resolve = post['jobs']['resolve']
    assert "conclusion == 'success'" in resolve['if']
    assert 'head_repository.full_name == github.repository' in resolve['if']
    assert 'head_branch' in resolve['if']
    assert 'host-source-resolution' in resolve['steps'][-1]['run']
    assert post['jobs']['report']['permissions'] == {'contents': 'read', 'issues': 'write'}
    assert 'always()' in post['jobs']['report']['if']
    assert set(post['jobs']['report']['needs']) == {'resolve', 'installed-package', 'readiness', 'python', 'browser', 'integrations'}
    frontend = '\n'.join(step.get('run', '') for step in release['jobs']['frontend']['steps'])
    assert 'npm test --prefix frontend' in frontend
    assert 'npm run build --prefix frontend' in frontend
    assert 'git diff --exit-code -- amplifier_web/static' in frontend


def test_installed_media_diagnostic_is_explicit_full_scenario_and_cannot_replace_readiness():
    workflow = yaml.load((ROOT / '.github/workflows/public-readiness.yml').read_text(), Loader=yaml.BaseLoader)
    choice = workflow['on']['workflow_dispatch']['inputs']['installed_media_diagnostic']
    assert choice['type'] == 'boolean' and choice['default'] == 'false'
    assert set(workflow['on']['workflow_call']['inputs']) == {'revision'}
    assert workflow['permissions'] == {'contents': 'read'}
    assert workflow['jobs']['critical']['if'] == '${{ !inputs.installed_media_diagnostic }}'
    job = workflow['jobs']['installed-media-diagnostic']
    assert job['if'] == "${{ github.event_name == 'workflow_dispatch' && inputs.installed_media_diagnostic }}"
    assert job['runs-on'] == 'ubuntu-latest' and job['timeout-minutes'] == '30'
    assert job['env'] == {'INLINE_MEDIA_MODE': 'installed'}
    assert 'INLINE_MEDIA_PHASES' not in job['env']
    steps = job['steps']
    assert steps[0]['with'] == {'ref': '${{ github.sha }}', 'fetch-depth': '0', 'persist-credentials': 'false'}
    assert "'git', 'diff', '--name-only', '-z'" in steps[1]['run']
    assert "'a5d1ab2397a00f76495c591835b5c9194e09f5a8', 'HEAD'" in steps[1]['run']
    assert 'assert changed <= allowed' in steps[1]['run']
    assert "'git', 'status', '--porcelain'" in steps[1]['run']
    # runner context is unavailable in job-level env. Use the runner's shell
    # variables in an ordinary step, before any path consumer executes.
    initialize = next(i for i, step in enumerate(steps)
                      if step.get('name') == 'Initialize owned diagnostic paths')
    assert initialize == 2 and steps[initialize]['shell'] == 'bash'
    assert steps[initialize]['run'].splitlines() == [
        "printf '%s\\n' \\",
        '  "INLINE_MEDIA_EVIDENCE_DIR=$RUNNER_TEMP/inline-media-diagnostic" \\',
        '  "INLINE_MEDIA_MP4=$RUNNER_TEMP/inline-media-diagnostic/synthetic.mp4" >> "$GITHUB_ENV"',
    ]
    assert '${{' not in steps[initialize]['run']
    commands = '\n'.join(step.get('run', '') for step in steps)
    assert 'uv sync --locked --group dev --group artifacts' in commands
    assert 'npm run build --prefix frontend' in commands
    assert 'e4a71c46fd41ecf6' in commands and 'staticSha256' in commands
    assert 'assert len(files) == 311' in commands
    assert "assert payload.hexdigest() == '1b4c114ffe44d4c7a5dcf8a2136fb363847feb4ec4b27aa5bbc5fe2435deb337'" in commands
    assert 'uv pip install --python .venv/bin/python --no-deps --reinstall .' in commands
    assert commands.count('node frontend/tests/inline-media-browser.mjs') == 1
    assert "result['fullScenario'] and result['coreChecks'] == 'PASS'" in commands
    assert 'INLINE_MEDIA_PHASES' not in commands and 'secrets.' not in str(job)
    upload = next(step for step in steps if step.get('uses') == 'actions/upload-artifact@v4')
    assert upload['if'] == "always() && steps.diagnostic-evidence.outcome == 'success'"
    assert upload['with']['path'].splitlines() == [
        '${{ runner.temp }}/inline-media-diagnostic/*.json',
        '${{ runner.temp }}/inline-media-diagnostic/*.png']
    assert upload['with']['if-no-files-found'] == 'error'


@pytest.mark.parametrize('extra', [None, 'amplifier_outputs/__init__.py',
                                  'frontend/index.html', 'assets/logo.svg', 'bundle.yaml'])
def test_media_diagnostic_product_guard_rejects_unlisted_changes(monkeypatch, extra):
    import subprocess
    workflow = yaml.load((ROOT / '.github/workflows/public-readiness.yml').read_text(), Loader=yaml.BaseLoader)
    guard = workflow['jobs']['installed-media-diagnostic']['steps'][1]['run']
    script = compile('\n'.join(guard.splitlines()[1:-1]), 'diagnostic-source-guard', 'exec')
    changed = ['.github/workflows/public-readiness.yml',
               'frontend/tests/inline-media-browser.mjs', 'tests/test_release_qualification.py']
    if extra:
        changed.append(extra)
    def git(args, **kwargs):
        assert args[:2] in (['git', 'diff'], ['git', 'status'])
        return '\0'.join(changed) + '\0' if args[1] == 'diff' else ''
    monkeypatch.setattr(subprocess, 'check_output', git)
    if extra:
        with pytest.raises(AssertionError):
            exec(script, {})
    else:
        exec(script, {})


@pytest.mark.parametrize(('click_attempted', 'primary_failed', 'click_settled', 'allowed'), [
    (False, False, None, True),
    (False, True, None, True),
    (True, False, None, False),
    (True, False, False, False),
    (True, False, True, True),
    (True, True, False, False),
    (True, True, True, False),
])
def test_media_diagnostic_cleanup_refuses_unknown_ui_command(
    click_attempted, primary_failed, click_settled, allowed,
):
    import re
    import subprocess
    source = (ROOT / 'frontend/tests/inline-media-browser.mjs').read_text()
    helper = re.search(r'function gifCanvasCleanupAllowed\([^)]*\)\{\n.*?\n\}', source, re.S)
    assert helper, 'Exercise the actual harness cleanup decision, not a copied predicate'
    args = json.dumps([click_attempted, primary_failed, click_settled])
    result = subprocess.run(
        ['node', '--input-type=module', '-e',
         helper.group(0) + '\nconsole.log(JSON.stringify(gifCanvasCleanupAllowed(...' + args + ')));'],
        capture_output=True, text=True, check=True, timeout=10,
    )
    assert json.loads(result.stdout) is allowed


def test_media_diagnostic_applies_ui_guard_before_cleanup_mutations():
    source = (ROOT / 'frontend/tests/inline-media-browser.mjs').read_text()
    phase = source.split("await phase('gif-canvas-errors',async()=>{", 1)[1].split(
        "await phase('endpoints'", 1)[0]
    click = "await inline.getByRole('button',{name:'Open in Canvas',exact:true}).click();"
    assert phase.count(click) == 3
    assert phase.count('clickAttempted=true;clickSettled=null;\n    ' + click) == 3
    assert 'clickOwned:{attempted:clickAttempted,settled:clickSettled,primaryFailed:!!primaryError}' in phase
    cleanup = phase.split('}finally{', 1)[1]
    guard = cleanup.index('if(!gifCanvasCleanupAllowed(clickAttempted,!!primaryError,clickSettled))')
    clear = cleanup.index("if(settled)settled=await cleanup('clear owned dirty view'")
    unmount = cleanup.index("if(settled)settled=await cleanup('unmount and close owned client'")
    assert guard < cleanup.index('blockedApplicationCleanup.add(page)') < clear < unmount
    assert 'if(clickAttempted&&!primaryError)' in cleanup
    assert "getByRole('button',{name:'Open in Canvas',exact:true})).toBeEnabled()" in cleanup
    final = source.split("await phase('cleanup',async()=>{", 1)[1]
    assert final.index('blockedApplicationCleanup.has(page)') < final.index("await action(page,'session.create'")


def test_full_python_suite_is_post_release_or_manual_but_fast_proofs_remain():
    workflows = ROOT / '.github/workflows'
    manual = yaml.load((workflows / 'python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(manual['on']) == {'workflow_dispatch', 'workflow_call'}
    assert manual['permissions'] == {'contents': 'read'}
    python = manual['jobs']['python']
    assert python['timeout-minutes'] == '90'
    complete = next(step for step in python['steps']
                    if step.get('name') == 'Run the complete Python suite')
    # Keep the entire suite and immediate node identities: a timeout must not
    # conceal which test failed or was still running behind quiet progress dots.
    assert 'uv run --no-sync pytest -v --tb=short --junitxml=output/python-checks/junit.xml' in complete['run']
    assert 'set -o pipefail' in complete['run']
    assert 'tee output/python-checks/pytest.log' in complete['run']
    assert '--maxfail' not in complete['run'] and ' tests/' not in complete['run']
    upload = next(step for step in python['steps'] if step.get('uses') == 'actions/upload-artifact@v4')
    assert upload['if'] == 'always()' and upload['with']['path'] == 'output/python-checks/'
    for filename in ('release.yml', 'work-workflows.yml'):
        jobs = yaml.load((workflows / filename).read_text(), Loader=yaml.BaseLoader)['jobs']
        for job in jobs.values():
            for step in job.get('steps', []):
                for line in step.get('run', '').splitlines():
                    if ' pytest -' in line or ' -m pytest ' in line:
                        assert 'tests/' in line, (filename, line)
    release = yaml.load((workflows / 'release.yml').read_text(), Loader=yaml.BaseLoader)
    package = '\n'.join(step.get('run', '') for step in release['jobs']['package']['steps'])
    assert 'uv build' in package and 'release.py" verify' in package
    assert 'dist/*.whl' in package and 'from amplifier_web.app_updates import PROBE' in package
    runtime = '\n'.join(step.get('run', '') for step in release['jobs']['runtime']['steps'])
    assert 'tests/test_runtime_module_cache.py' in runtime


def test_manual_python_selection_is_fixed_and_preserves_full_and_core_modes():
    workflow = yaml.load((ROOT / '.github/workflows/python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    selection = workflow['on']['workflow_dispatch']['inputs']['suite']
    assert selection['type'] == 'choice' and selection['default'] == 'full'
    assert selection['options'] == ['full', 'inline-chat-card', 'queued-configuration', 'security-redaction', 'patch-placement-recovery', 'patch-placement-native', 'managed-removal-recovery', 'shared-work-continuity', 'remote-test-origins', 'shared-work-native']
    assert workflow['on']['workflow_call']['inputs'] == {'revision': {'type': 'string', 'required': 'true'}}
    assert workflow['on']['workflow_call']['secrets'] == {'LOOP_LIVE_CI_READ_KEY': {'required': 'true'}}
    assert workflow['permissions'] == {'contents': 'read'}
    jobs = workflow['jobs']
    assert jobs['python']['if'] == "${{ !inputs.core_wheels && inputs.suite != 'remote-test-origins' }}"
    assert jobs['python']['env']['CHECK_SUITE'] == "${{ inputs.suite || 'full' }}"
    assert jobs['core-wheels']['if'] == "${{ inputs.core_wheels && (!inputs.suite || inputs.suite == 'full') }}"
    remote = jobs['remote-test-origins']
    assert remote['if'] == "${{ !inputs.core_wheels && inputs.suite == 'remote-test-origins' }}"
    assert remote['uses'] == './.github/workflows/remote-test-origins.yml'
    rejected = jobs['incompatible-selection']
    assert rejected['if'] == "${{ inputs.core_wheels && inputs.suite && inputs.suite != 'full' }}"
    assert 'exit 1' in rejected['steps'][0]['run']
    steps = jobs['python']['steps']
    assert steps[1]['name'] == 'Record and validate the fixed Python selection'
    native = next(step for step in steps if step.get('name') == 'Select the prepared native-controls interpreter')
    assert native['if'] == "${{ env.CHECK_SUITE == 'patch-placement-native' }}"
    assert native['run'] == 'echo "UNIFIED_RUNTIME_PYTHON=$GITHUB_WORKSPACE/.venv/bin/python" >> "$GITHUB_ENV"'
    complete = next(step for step in steps if step.get('name') == 'Run the complete Python suite')
    focused = next(step for step in steps if step.get('name') == 'Run the selected repair suite')
    assert complete['if'] == "${{ env.CHECK_SUITE == 'full' }}"
    assert focused['if'] == "${{ env.CHECK_SUITE != 'full' }}" and focused['shell'] == 'bash'
    assert 'mapfile -t targets < output/python-checks/targets.txt' in focused['run']
    assert 'test "${#targets[@]}" -gt 0' in focused['run']
    assert '"${targets[@]}"' in focused['run'] and 'set -o pipefail' in focused['run']
    assert '--junitxml=output/python-checks/junit.xml' in focused['run']
    assert 'tee output/python-checks/pytest.log' in focused['run']
    assert '${{' not in focused['run'] and '${{' not in steps[1]['run']
    receipts = next(step for step in steps if step.get('name') == 'Prepare pinned native receipt dependencies')
    installed = next(step for step in steps if step.get('name') == 'Verify installed native receipt boundary outside checkout')
    assert receipts['if'] == installed['if'] == "${{ env.CHECK_SUITE == 'shared-work-native' }}"
    assert '2b35fa334fb2fb2c5c948842e8c7bd691ce5e834' in receipts['run']
    assert 'NativeSendAdmission, NativeOutboundEnvelope' in receipts['run']
    assert 'unset PYTHONPATH' in installed['run']
    assert '--no-deps -r output/python-checks/installed-requirements.txt' in installed['run']
    assert 'uv pip check --python "$RUNNER_TEMP/native-receipt-installed/bin/python"' in installed['run']
    assert 'assert not actual.is_relative_to(source)' in installed['run']
    assert "('failure', 'error', 'skipped')" in installed['run']
    assert installed['run'].index('uv pip check') < installed['run'].index(' -m pytest')
    assert "result.returncode == 1 and len(cases) == 1" in installed['run']
    assert 'negative-control.json' in installed['run']
    assert 'origin-negative.json' in installed['run']
    assert 'test_effect_origin_joins_actual_admission_and_full_input_scope[new_input]' in installed['run']


@pytest.mark.parametrize(('suite', 'targets'), [
    ('full', []),
    ('inline-chat-card', ['tests/test_chat_references.py']),
    ('queued-configuration', ['tests/test_queued_configuration_boundary.py', 'tests/test_configuration_recovery_ui.py']),
    ('security-redaction', ['tests/test_agent_state.py', 'tests/test_provider_environment.py', 'tests/test_runtime_controls.py']),
    ('patch-placement-recovery', ['tests/test_patch_placement_recovery.py', 'tests/test_file_source_guard.py', 'tests/test_file_source_native_children.py', 'tests/test_workspace_writer_modules.py']),
    ('patch-placement-native', ['tests/test_patch_placement_native.py', 'tests/test_patch_placement_recovery.py', 'tests/test_configuration_authority.py', 'tests/test_queued_configuration_boundary.py', 'tests/test_runtime_controls.py']),
    ('managed-removal-recovery', ['tests/test_test_environment_worker.py', 'tests/test_task_environments.py', 'tests/test_environment_recovery.py', 'tests/test_prelaunch_source_failure.py', 'tests/test_environment_retention.py', 'tests/test_incus_environment_worker.py', 'tests/test_release_qualification.py']),
    ('shared-work-continuity', ['tests/test_task_continuity.py', 'tests/test_coordination_delivery.py', 'tests/test_work_invocation.py', 'tests/test_schedules.py', 'tests/test_collaboration_input.py', 'tests/test_coordination_revisions.py', 'tests/test_release_qualification.py', 'tests/test_voice_visual.py', 'tests/test_surface_context.py', 'tests/test_output_image_pairs.py', 'tests/test_memory_personalization.py', 'tests/test_capacity.py']),
    ('shared-work-native', ['tests/test_native_provider.py', 'tests/test_coordination_delivery.py', 'tests/test_work_origin.py', 'tests/test_capacity.py', 'tests/test_release_qualification.py']),
])
@pytest.mark.parametrize('missing', [False, True])
def test_python_selection_records_exact_source_and_refuses_missing_files(tmp_path, monkeypatch, suite, targets, missing):
    import subprocess
    workflow = yaml.load((ROOT / '.github/workflows/python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    run = workflow['jobs']['python']['steps'][1]['run']
    lines = run.splitlines()
    assert lines[0] == "python - <<'PY'" and lines[-1] == 'PY'
    script = compile('\n'.join(lines[1:-1]), 'workflow-python-selection', 'exec')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('CHECK_SUITE', suite)
    monkeypatch.setenv('GITHUB_RUN_ID', 'synthetic-run')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    def git(args, **kwargs):
        assert args[:2] == ['git', 'rev-parse']
        assert args[2] in ('HEAD', 'HEAD^{tree}')
        return ('a' if args[2] == 'HEAD' else 'b') * 40 + '\n'
    monkeypatch.setattr(subprocess, 'check_output', git)
    for target in targets[1:] if missing else targets:
        path = tmp_path / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    if missing and targets:
        with pytest.raises(SystemExit, match='Selected tests are missing: ' + targets[0]):
            exec(script, {})
    else:
        exec(script, {})
    record = json.loads((tmp_path / 'output/python-checks/selection.json').read_text())
    assert record == {'suite': suite, 'fullSuite': suite == 'full', 'targets': targets,
                      'revision': 'a' * 40, 'tree': 'b' * 40, 'runId': 'synthetic-run', 'runAttempt': '1',
                      'missing': targets[:1] if missing else []}
    assert (tmp_path / 'output/python-checks/targets.txt').read_text().splitlines() == targets


@pytest.mark.parametrize('suite', ['unknown', 'tests/test_service.py', 'full; echo unexpected'])
def test_python_selection_rejects_arbitrary_input_before_commands(tmp_path, monkeypatch, suite):
    import subprocess
    workflow = yaml.load((ROOT / '.github/workflows/python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    script = '\n'.join(workflow['jobs']['python']['steps'][1]['run'].splitlines()[1:-1])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('CHECK_SUITE', suite)
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **kw: pytest.fail('Invalid selection ran a command'))
    with pytest.raises(SystemExit, match='Unknown fixed Python suite:'):
        exec(compile(script, 'workflow-python-selection', 'exec'), {})
    assert not (tmp_path / 'output').exists()


def test_browser_retains_only_the_successfully_built_public_web_payload():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['permissions'] == {'contents': 'read'}
    steps = workflow['jobs']['browser']['steps']
    identity = next(index for index, step in enumerate(steps) if step.get('id') == 'build-source')
    assert steps[identity - 1]['uses'] == 'actions/checkout@v4'
    assert steps[identity]['run'] == 'echo "revision=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"'
    build = next(index for index, step in enumerate(steps) if step.get('id') == 'frontend-build')
    assert identity < build
    assert steps[build]['run'] == 'npm run build --prefix frontend'
    upload = steps[build + 1]
    assert upload['if'] == "${{ steps.frontend-build.outcome == 'success' }}"
    assert upload['uses'] == 'actions/upload-artifact@v4'
    assert upload['with'] == {
        'name': 'browser-static-${{ steps.build-source.outputs.revision }}-${{ github.run_attempt }}',
        'path': 'amplifier_web/static/', 'include-hidden-files': 'true', 'if-no-files-found': 'error'}
    assert steps[build + 2]['name'] == 'Install browser dependencies'
    # The artifact includes the builder's metadata and is retained before later
    # scenario failure; an unrun/failed build cannot upload the old tracked tree.
    vite = (ROOT / 'frontend/vite.config.js').read_text()
    assert 'emptyOutDir:true' in vite and 'static/build.json' in vite


def browser_runner_commands(steps):
    invocation = r'(?m)^(?:uv run --no-sync )?python scripts/browser_check_runner\.py(?:[ \t]|$)'
    return [step['run'] for step in steps if re.search(invocation, step.get('run', ''))]


def test_browser_runner_matcher_excludes_test_names_without_hiding_extra_invocations():
    initialize = 'python scripts/browser_check_runner.py --initialize'
    execute = 'uv run --no-sync python scripts/browser_check_runner.py --budget-seconds 1500'
    decoys = [
        'set -o pipefail\nuv run --no-sync pytest tests/test_browser_check_runner.py tests/test_release_qualification.py',
        'echo python scripts/browser_check_runner.py',
        'python scripts/browser_check_runner.py.backup',
    ]
    steps = [{'run': text} for text in (initialize, *decoys, execute)]
    assert browser_runner_commands(steps) == [initialize, execute]
    # A duplicate remains visible to the caller's exact two-command assertions.
    assert browser_runner_commands([*steps, {'run': execute}]) == [initialize, execute, execute]
    joined = initialize + '\n' + execute
    assert browser_runner_commands([{'run': joined}]) == [joined]


def test_canvas_qualification_is_fixed_manual_and_installed_not_a_release_gate():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    job = workflow['jobs']['canvas-qualification']
    assert job['if'] == "${{ github.event_name == 'workflow_dispatch' && inputs.canvas_qualification }}"
    assert job['runs-on'] == 'ubuntu-latest' and job['timeout-minutes'] == '20'
    assert workflow['permissions'] == {'contents': 'read'}
    assert 'secrets.' not in str(job)
    assert job['steps'][0]['with'] == {'ref': '${{ github.sha }}', 'persist-credentials': 'false'}
    commands = '\n'.join(step.get('run', '') for step in job['steps'])
    assert 'uv sync --locked --group dev --group artifacts --no-build-package amplifier-core' in commands
    assert 'npm ci --prefix frontend' in commands
    assert 'npx playwright install --with-deps chromium' in commands
    assert 'python scripts/canvas_qualification.py run' in commands
    upload = next(step for step in job['steps'] if step.get('uses') == 'actions/upload-artifact@v4')
    assert 'always()' in upload['if'] and upload['with']['if-no-files-found'] == 'error'
    source = (ROOT / 'scripts/canvas_qualification.py').read_text()
    assert 'from browser_check_runner import run_scenarios' in source
    assert "BROWSERS = ('canvas-chrome-browser.mjs', 'canvas-dirty-browser.mjs'," in source
    assert "'destination-history-browser.mjs', 'automation-hub-browser.mjs'," in source
    assert job['steps'][1]['env']['MIXED'] == (
        '${{ inputs.attachment_experience || inputs.multi_quote || inputs.peer_messages || inputs.compact_composer || inputs.mcp_boundary || inputs.app004_qualification || inputs.attachment_consumption '
        '|| inputs.destination_history || inputs.settings_close_diagnostic }}')
    assert "raise ValueError('Qualification already started; no replay')" in source
    assert "'installed-harness'" in source and "'installed-identity.json'" in source
    assert "assert static == json.loads((OUT / 'static.json').read_text())" in source


def test_app004_opt_in_is_fixed_manual_only_and_keeps_default_browser_budget():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch', 'workflow_call'}
    inputs = workflow['on']['workflow_dispatch']['inputs']
    assert set(inputs) == {'app004_qualification', 'mcp_boundary', 'attachment_consumption',
                           'canvas_qualification', 'destination_history', 'settings_close_diagnostic',
                           'multi_quote', 'attachment_experience', 'compact_composer', 'peer_messages'}
    assert inputs['canvas_qualification']['type'] == 'boolean'
    assert inputs['canvas_qualification']['default'] == 'false'
    assert inputs['multi_quote']['type'] == 'boolean'
    assert inputs['multi_quote']['default'] == 'false'
    assert workflow['permissions'] == {'contents': 'read'}
    assert inputs['attachment_experience']['type'] == 'boolean'
    assert inputs['attachment_experience']['default'] == 'false'
    assert inputs['peer_messages']['type'] == 'boolean'
    assert inputs['peer_messages']['default'] == 'false'
    assert inputs['settings_close_diagnostic']['type'] == 'boolean'
    assert inputs['settings_close_diagnostic']['default'] == 'false'
    assert inputs['destination_history']['type'] == 'boolean'
    assert inputs['destination_history']['default'] == 'false'
    assert inputs['attachment_consumption']['type'] == 'boolean'
    assert inputs['attachment_consumption']['default'] == 'false'
    assert inputs['app004_qualification']['type'] == 'boolean'
    assert inputs['app004_qualification']['default'] == 'false'
    assert inputs['mcp_boundary']['type'] == 'boolean'
    assert inputs['mcp_boundary']['default'] == 'false'
    assert inputs['compact_composer']['type'] == 'boolean'
    assert inputs['compact_composer']['default'] == 'false'
    assert workflow['on']['workflow_call'] == {
        'inputs': {'revision': {'type': 'string', 'required': 'true'}}}
    job = workflow['jobs']['browser']
    assert job['if'] == '${{ !inputs.canvas_qualification && !inputs.peer_messages && !inputs.mcp_boundary && !inputs.compact_composer && !inputs.destination_history && !inputs.settings_close_diagnostic && !inputs.attachment_experience }}'
    assert job['timeout-minutes'] == '30'
    mcp = workflow['jobs']['mcp-boundary']
    assert mcp['if'] == "${{ github.event_name == 'workflow_dispatch' && !inputs.canvas_qualification && !inputs.peer_messages && inputs.mcp_boundary && !inputs.compact_composer && !inputs.destination_history && !inputs.settings_close_diagnostic }}"
    history = workflow['jobs']['destination-history']
    assert history['if'] == '${{ !inputs.peer_messages && !inputs.compact_composer && (inputs.destination_history || inputs.settings_close_diagnostic) }}'
    assert history['steps'][0] == {
        'name': 'Require the fixed navigation selection for diagnostic mode',
        'if': '${{ inputs.settings_close_diagnostic && !inputs.destination_history }}',
        'run': 'exit 1',
    }
    assert history['steps'][1] == {
        'name': 'Reject mixed navigation selections',
        'if': '${{ inputs.mcp_boundary || inputs.app004_qualification || inputs.attachment_consumption || inputs.canvas_qualification || inputs.multi_quote || inputs.attachment_experience }}',
        'run': 'exit 1',
    }
    # Both opt-ins must fail visibly before setup, not silently drop APP004.
    incompatible = mcp['steps'][1]
    assert incompatible['name'] == 'Reject incompatible browser proof selections'
    assert incompatible['if'] == '${{ inputs.app004_qualification }}'
    assert incompatible['run'].splitlines() == [
        "echo 'MCP boundary and APP004 qualification are separate selections; neither proof was run.'",
        'exit 1',
    ]
    steps = job['steps']
    assert next(s for s in steps if s.get('name') == 'Reject mixed multi-quote selections') == {
        'name': 'Reject mixed multi-quote selections',
        'if': '${{ inputs.multi_quote && (inputs.attachment_consumption || inputs.app004_qualification || inputs.mcp_boundary) }}',
        'run': 'exit 1',
    }
    assert next(s for s in mcp['steps'] if s.get('name') == 'Reject mixed MCP and attachment selection') == {
        'name': 'Reject mixed MCP and attachment selection',
        'if': '${{ inputs.attachment_consumption || inputs.multi_quote || inputs.attachment_experience }}',
        'run': 'exit 1',
    }
    commands = browser_runner_commands(steps)
    assert len(commands) == 2
    flag = "${{ inputs.app004_qualification && '--app004-qualification' || '' }}"
    attachment_flag = "${{ inputs.attachment_consumption && '--attachment-consumption' || '' }}"
    assert all(command.count(flag) == 1 for command in commands)
    assert all(command.count(attachment_flag) == 1 for command in commands)
    # Empty/default selection executes exactly the preexisting plan and budget.
    assert commands[0].replace(flag, '').replace(attachment_flag, '').strip() == (
        'python scripts/browser_check_runner.py --plan frontend/tests/browser-checks.json '
        '--output output/browser-checks --initialize')
    assert commands[1].replace(flag, '').replace(attachment_flag, '').strip() == (
        'uv run --no-sync python scripts/browser_check_runner.py '
        '--plan frontend/tests/browser-checks.json --output output/browser-checks --budget-seconds 1500')
    assert next(i for i, s in enumerate(steps) if '--initialize' in s.get('run', '')) < next(
        i for i, s in enumerate(steps) if s.get('uses') == 'actions/setup-python@v5')
    contracts = [s for s in steps if s.get('name') == 'Verify fixed APP004 qualification contracts']
    assert len(contracts) == 1
    contract = contracts[0]
    assert contract['if'] == '${{ inputs.app004_qualification }}'
    assert contract['run'].splitlines() == [
        'set -o pipefail',
        'uv run --no-sync pytest -v --tb=short tests/test_browser_check_runner.py '
        'tests/test_release_qualification.py --junitxml=output/browser-checks/app004-contracts.xml '
        '2>&1 | tee output/browser-checks/app004-contracts.log',
    ]
    before = steps[:steps.index(contract)]
    assert any(s.get('uses') == 'actions/setup-node@v4' for s in before)
    assert any(s.get('run') == 'npm ci --prefix frontend' for s in before)
    assert any(s.get('run') == 'uv sync --locked --group dev --group artifacts' for s in before)
    assert any(s.get('with', {}).get('path') == 'amplifier_web/static/' for s in before)
    # The fixed attachment-only contracts are inactive for APP004/default.
    after = [step for step in steps[steps.index(contract) + 1:]
             if step.get('if') != '${{ inputs.attachment_consumption }}']
    next_step = after[0]
    assert next_step['name'] == 'Run all extended browser scenarios serially'
    # Multi-quote replaces only this explicitly selected run; default/APP004
    # retain the existing plan and implicit success gate on earlier failures.
    assert next_step['if'] == '${{ !inputs.multi_quote }}'
    assert next(s for s in steps if '--initialize' in s.get('run', ''))['if'] == '${{ !inputs.multi_quote }}'
    assert next(s for s in steps if s.get('name') == 'Run fixed multi-quote source and installed proof') == {
        'name': 'Run fixed multi-quote source and installed proof',
        'if': '${{ inputs.multi_quote }}',
        'run': 'uv run --no-sync python scripts/multi_quote_checks.py --output output/multi-quote --installed',
    }
    # Opt-in can neither mask a failed step nor bypass the existing always upload.
    continued = [step for step in steps if 'continue-on-error' in step]
    assert {step['id'] for step in continued} == {'attachment-contracts', 'attachment-client-contracts'}
    assert all(step['if'] == '${{ inputs.attachment_consumption }}' for step in continued)
    failures = next(step for step in steps if step.get('name') == 'Preserve failed attachment contracts')
    assert failures['run'] == 'exit 1' and 'always()' in failures['if']
    diagnostics = steps[-1]
    assert diagnostics['if'] == 'always()'
    assert diagnostics['uses'] == 'actions/upload-artifact@v4'
    assert 'output/browser-checks/' in diagnostics['with']['path']


def test_compact_composer_is_fixed_manual_only_with_independent_failure_evidence():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['permissions'] == {'contents': 'read'}
    assert 'compact_composer' not in workflow['on']['workflow_call']['inputs']
    job = workflow['jobs']['compact-composer']
    assert job['if'] == "${{ github.event_name == 'workflow_dispatch' && inputs.compact_composer }}"
    assert job['timeout-minutes'] == '30'
    steps = job['steps']
    assert steps[0]['with'] == {'ref': '${{ github.sha }}', 'persist-credentials': 'false'}
    assert steps[1] == {'name': 'Reject mixed selections',
                       'if': '${{ inputs.canvas_qualification || inputs.multi_quote || inputs.attachment_experience || inputs.peer_messages || inputs.mcp_boundary || inputs.attachment_consumption || inputs.app004_qualification || inputs.destination_history || inputs.settings_close_diagnostic }}',
                       'run': 'exit 1'}
    assert steps[2]['run'] == 'python scripts/compact_composer_checks.py --initialize'
    commands = [step['run'] for step in steps if 'run' in step]
    assert commands.count('uv run --no-sync python scripts/compact_composer_checks.py') == 1
    assert 'uv sync --locked --group dev --group artifacts' in commands
    assert 'npm ci --prefix frontend' in commands
    assert 'npm run build --prefix frontend' in commands
    assert all('${{' not in command for command in commands)
    assert all('continue-on-error' not in step for step in steps)
    uploads = [step for step in steps if step.get('uses') == 'actions/upload-artifact@v4']
    assert len(uploads) == 2
    assert uploads[0]['if'] == "${{ steps.compact-build.outcome == 'success' }}"
    assert uploads[0]['with']['path'] == 'amplifier_web/static/'
    assert uploads[0]['with']['include-hidden-files'] == 'true'
    assert uploads[1]['if'] == 'always()'
    assert uploads[1]['with']['path'] == 'output/compact-composer/'
    assert all(step['with']['if-no-files-found'] == 'error' for step in uploads)
    assert 'secrets.' not in str(job)


def test_runtime_cache_is_selected_after_fresh_resolution_without_fallback():
    jobs = yaml.load((ROOT / '.github/workflows/release.yml').read_text(), Loader=yaml.BaseLoader)['jobs']
    steps = jobs['runtime']['steps']
    resolve = next(i for i, step in enumerate(steps) if 'uv lock ' in step.get('run', ''))
    key = next(i for i, step in enumerate(steps) if ' cache-key ' in step.get('run', ''))
    restore = next(i for i, step in enumerate(steps) if step.get('uses') == 'actions/cache/restore@v4')
    assert resolve < key < restore
    assert '--refresh --upgrade' in steps[resolve]['run']
    assert 'restore-keys' not in steps[restore]['with']
    assert 'runtime-build-cache' in steps[restore]['with']['path']
    install = next(i for i, step in enumerate(steps) if 'uv sync --project' in step.get('run', ''))
    save = next(i for i, step in enumerate(steps) if step.get('uses') == 'actions/cache/save@v4')
    assert install < save
    assert 'uv cache clean amplifier-module-loop-live' in steps[install]['run']


def test_native_core_qualification_is_manual_and_cannot_publish():
    workflows = ROOT / '.github/workflows'
    entry = yaml.load((workflows / 'python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    native = yaml.load((workflows / 'core-wheels.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(entry['on']) == {'workflow_dispatch', 'workflow_call'}
    assert entry['on']['workflow_dispatch']['inputs']['core_wheels']['default'] == 'false'
    assert entry['jobs']['core-wheels']['uses'] == './.github/workflows/core-wheels.yml'
    assert set(native['on']) == {'workflow_call'}
    assert native['permissions'] == {'contents': 'read'}
    job = native['jobs']['runtime']
    assert job['if'] == "github.event_name == 'workflow_dispatch'"
    assert set(job['strategy']['matrix']['os']) == {'ubuntu-latest', 'ubuntu-24.04-arm'}
    commands = '\n'.join(step.get('run', '') for step in job['steps'])
    assert 'tests/test_runtime_module_cache.py' in commands
    assert 'runtime-snapshot' in commands and 'cmp ' in commands
    assert 'publish' not in commands
    assert job['timeout-minutes'] == '20'
    steps = job['steps']
    host = next(step['run'] for step in steps if step.get('name') == 'Install and record the released host Core wheel')
    runtime = next(step['run'] for step in steps if step.get('name') == 'Qualify real runtime behavior without model calls')
    assert host.count('--junitxml="$RUNNER_TEMP/host-core-junit.xml"') == 1
    assert runtime.count('--junitxml="$RUNNER_TEMP/runtime-core-junit.xml"') == 1
    assert runtime.index('cp ') < runtime.index('runtime-snapshot') < runtime.index('cmp ') < runtime.index('git rev-parse HEAD')
    retained = next(step for step in steps if step.get('uses') == 'actions/upload-artifact@v4')
    assert retained['if'] == 'always()'
    assert set(retained['with']['path'].splitlines()) == {
        '${{ runner.temp }}/' + name for name in (
            'app-revision.txt', 'host-core-wheel.json', 'host-core-junit.xml',
            'runtime-core-junit.xml', 'before-tests.json', 'core-runtime/pyproject.toml',
            'core-runtime/uv.lock', 'core-runtime/build-constraints.txt',
            'core-runtime/build-identity.json', 'core-runtime/qualified-runtime.json')
    }


@pytest.mark.parametrize('workflow', ['release.yml', 'core-wheels.yml'])
def test_recipe_probe_installs_its_declared_module_before_freezing_runtime(workflow):
    jobs = yaml.load((ROOT / '.github/workflows' / workflow).read_text(), Loader=yaml.BaseLoader)['jobs']
    commands = '\n'.join(step.get('run', '') for step in jobs['runtime']['steps'])
    recipe = next(line for line in commands.splitlines()
                  if 'uv add ' in line and '.ci/recipes/modules/tool-recipes' in line)
    assert '--frozen --editable' in recipe
    assert commands.index(recipe) < commands.index('uv lock ') < commands.index(' cache-key ')
    assert commands.index('uv sync --project') < commands.index('WARM_RECIPES_PATH=')
    # Dependencies stay owned by the checked-out module, not a stale hand list.
    assert 'tree-sitter' not in commands


def test_coordination_prepares_real_loop_before_first_contract_suite():
    workflow = yaml.load((ROOT / '.github/workflows/coordination.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['permissions'] == {'contents': 'read'}
    assert set(workflow['on']) == {'pull_request'}
    steps = workflow['jobs']['contracts']['steps']
    resolve = next(i for i, s in enumerate(steps) if s.get('id') == 'runtime-dependency')
    checkout = next(i for i, s in enumerate(steps)
                    if s.get('with', {}).get('repository') == 'microsoft/amplifier-module-loop-live')
    install = next(i for i, s in enumerate(steps) if s.get('name') == 'Prepare the declared runtime for mounted coordination tests')
    verify = next(i for i, s in enumerate(steps) if s.get('name') == 'Verify and retain coordination runtime readiness')
    contracts = next(i for i, s in enumerate(steps) if 'tests/test_coordination_delivery.py' in s.get('run', ''))
    assert resolve < checkout < install < verify < contracts
    assert 'runtime_deps/pyproject.toml' in steps[resolve]['run']
    assert steps[checkout]['with'] == {
        'repository': 'microsoft/amplifier-module-loop-live',
        'ref': '${{ steps.runtime-dependency.outputs.revision }}',
        'path': '.ci/loop-live', 'ssh-key': '${{ secrets.LOOP_LIVE_CI_READ_KEY }}',
        'persist-credentials': 'false'}
    assert '--python .venv/bin/python' in steps[install]['run'] and '.ci/loop-live' in steps[install]['run']
    assert 'amplifier-module-context-simple' in steps[install]['run']
    assert 'BundleLiveOrchestrator' in steps[verify]['run'] and 'selectedLoopRevision' in steps[verify]['run']
    assert 'importorskip' not in steps[verify]['run']
    assert not any('continue-on-error' in s for s in steps)
    assert any(s.get('name') == 'Verify mounted child identity and file boundaries' for s in steps[contracts + 1:])
    assert any(s.get('run') == 'node tests/coordination-browser.mjs' for s in steps)
    assert steps[-1]['if'] == 'always()'
    assert '${{ runner.temp }}/test-environments' in steps[-1]['with']['path']
    assert 'output/coordination-runtime-readiness.json' in steps[-1]['with']['path']
