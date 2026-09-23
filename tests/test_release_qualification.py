"""Parallel release jobs must qualify and publish one immutable candidate."""
import importlib.util
import json
from pathlib import Path

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


def test_runtime_changes_after_tests_cannot_produce_a_receipt(qualified):
    root, evidence, expected, receipts, dist, runtime, snapshot = qualified
    snapshot['graph'] = [{'name': 'core', 'version': 'new-untested-version'}]
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


@pytest.mark.parametrize('change', ['resolved-source', 'loop-live', 'python-abi', 'architecture', 'rustc', 'maturin', 'build-flags'])
def test_cache_identity_changes_with_every_build_input(runtime_build, monkeypatch, change):
    root, runtime = runtime_build
    before = qualification.digest(qualification.build_identity(root, runtime))
    if change == 'resolved-source':
        (runtime / 'uv.lock').write_text('same main declaration, new resolved commit')
    elif change == 'loop-live':
        monkeypatch.setattr(qualification, 'checkout_identity', lambda path: 'b' * 40)
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
        assert 'permissions' not in jobs[lane]
    assert set(jobs['release']['needs']) == {'prepare', *qualification.LANES}
    assert jobs['release']['permissions'] == {'contents': 'write'}
    steps = jobs['release']['steps']
    verify = next(i for i, step in enumerate(steps) if 'verify-receipts' in step.get('run', ''))
    publish = next(i for i, step in enumerate(steps) if 'publish --tag' in step.get('run', ''))
    assert verify < publish


def test_browser_checks_are_manual_and_not_in_automatic_gates():
    workflows = ROOT / '.github/workflows'
    manual = yaml.load((workflows / 'browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(manual['on']) == {'workflow_dispatch'}
    assert manual['permissions'] == {'contents': 'read'}
    commands = '\n'.join(step.get('run', '') for step in manual['jobs']['browser']['steps'])
    assert 'playwright install' in commands
    assert 'test:session-health-browser' in commands
    assert '--context-limit --active-worker' in commands
    assert 'test:connectors-browser' in commands
    for path in workflows.glob('*.yml'):
        if path.name == 'browser-checks.yml':
            continue
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for job in workflow['jobs'].values():
            for step in job.get('steps', []):
                command = step.get('run', '')
                assert 'playwright' not in command, (path.name, command)
                assert '-browser' not in command, (path.name, command)
    release = yaml.load((workflows / 'release.yml').read_text(), Loader=yaml.BaseLoader)
    frontend = '\n'.join(step.get('run', '') for step in release['jobs']['frontend']['steps'])
    assert 'npm test --prefix frontend' in frontend
    assert 'npm run build --prefix frontend' in frontend
    assert 'git diff --exit-code -- amplifier_web/static' in frontend


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
