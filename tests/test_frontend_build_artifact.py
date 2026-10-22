"""Failed asset parity retains repair output without granting release proof."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('frontend_build_artifact', ROOT / 'scripts/frontend_build_artifact.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c',
        'user.email=fixture@example.invalid', *args], cwd=root, stderr=subprocess.DEVNULL)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    (root / 'frontend').mkdir(parents=True)
    (root / module.STATIC).mkdir(parents=True)
    (root / 'frontend/input.js').write_text('source')
    (root / 'package.json').write_text('{}')
    (root / '.gitignore').write_text('.env*\nfrontend/.env*\n')
    (root / module.STATIC / 'old.js').write_text('committed output')
    git(root, 'init', '-q'); git(root, 'add', '.'); git(root, 'commit', '-qm', 'source')
    (root / module.STATIC / 'old.js').unlink()
    (root / module.STATIC / 'new.js').write_text('new output')
    return root


def capture(root, tmp_path):
    output = tmp_path / 'build-output'
    manifest = module.create(root, output, {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '2'})
    return output, manifest


def test_generated_changes_are_retained_for_exact_source_without_qualifying_it(source, tmp_path):
    output, manifest = capture(source, tmp_path)
    assert module.verify(source, output) == manifest
    assert manifest['qualified'] is False and manifest['workflow']['GITHUB_RUN_ID'] == '123'
    assert set(manifest['assets']) == {'new.js'}
    assert (output / 'static/new.js').read_text() == 'new output'
    assert not (output / 'frontend').exists()
    # Committing only the recovered assets changes HEAD but not build inputs.
    git(source, 'add', '.'); git(source, 'commit', '-qm', 'assets')
    assert module.verify(source, output) == manifest


@pytest.mark.parametrize('change', ['source-dirty', 'source-committed', 'new-source', 'version', 'environment'])
def test_changed_build_inputs_cannot_reuse_old_output(source, tmp_path, change):
    output, _ = capture(source, tmp_path)
    path = 'package.json' if change == 'version' else '.env.local' if change == 'environment' else (
        'frontend/new.js' if change == 'new-source' else 'frontend/input.js')
    (source / path).write_text('different')
    if change == 'source-committed':
        git(source, 'add', '.'); git(source, 'commit', '-qm', 'new source')
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        module.verify(source, output)


@pytest.mark.parametrize('change', ['modified', 'missing', 'extra', 'extra-root', 'traversal', 'qualified', 'link', 'directory-link'])
def test_corrupt_or_redirected_artifact_is_rejected_without_writing_source(source, tmp_path, change):
    output, manifest = capture(source, tmp_path)
    before = (source / module.STATIC / 'new.js').read_bytes()
    asset = output / 'static/new.js'
    if change == 'modified': asset.write_text('other bytes')
    elif change == 'missing': asset.unlink()
    elif change == 'extra': (output / 'static/extra.js').write_text('extra')
    elif change == 'extra-root': (output / 'script.py').write_text('not executed')
    elif change == 'traversal':
        manifest['assets']['../escape'] = manifest['assets'].pop('new.js')
    elif change == 'qualified': manifest['qualified'] = True
    elif change == 'link':
        asset.unlink(); asset.symlink_to(source / 'frontend/input.js')
    elif change == 'directory-link':
        (output / 'static/external').symlink_to(source / 'frontend', target_is_directory=True)
    (output / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError): module.verify(source, output)
    assert (source / module.STATIC / 'new.js').read_bytes() == before


def test_ignored_environment_inputs_are_hashed_not_exported(source, tmp_path):
    (source / '.env.local').write_text('VITE_LABEL=fixture-private-input')
    output, manifest = capture(source, tmp_path)
    assert '.env.local' in manifest['source']['envFiles']
    assert 'fixture-private-input' not in (output / 'manifest.json').read_text()
    assert module.verify(source, output) == manifest


def test_capture_refuses_dirty_source_and_leaves_no_partial_bundle(source, tmp_path):
    (source / 'frontend/input.js').write_text('uncommitted source')
    with pytest.raises(subprocess.CalledProcessError): capture(source, tmp_path)
    assert not (tmp_path / 'build-output').exists()


def test_capture_refuses_links_and_oversize_before_publishing(source, tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'MAX_BYTES', 2)
    with pytest.raises(ValueError, match='size limit'): capture(source, tmp_path)
    assert not (tmp_path / 'build-output').exists()
    monkeypatch.setattr(module, 'MAX_BYTES', 10000)
    (source / module.STATIC / 'external').symlink_to(source / 'frontend', target_is_directory=True)
    with pytest.raises(ValueError, match='Linked'): capture(source, tmp_path)


def test_bundle_capture_does_not_overwrite_previous_output(source, tmp_path):
    output, manifest = capture(source, tmp_path)
    with pytest.raises(ValueError, match='already exists'): module.create(source, output, {})
    assert module.verify(source, output) == manifest


def test_workflow_retains_build_before_parity_without_weakening_proof():
    workflow = yaml.load((ROOT / '.github/workflows/work-workflows.yml').read_text(), Loader=yaml.BaseLoader)
    steps = workflow['jobs']['integration']['steps']
    names = [step.get('name') for step in steps]
    build = next(step for step in steps if step.get('name') == 'Build frontend')
    assert build['id'] == 'frontend-build'
    for name in ('Retain frontend build output', 'Upload frontend build output'):
        step = next(step for step in steps if step.get('name') == name)
        assert step['if'] == "${{ !cancelled() && steps.frontend-build.outcome == 'success' }}"
        assert names.index('Build frontend') < names.index(name) < names.index('Verify committed frontend assets')
    parity = next(step for step in steps if step.get('name') == 'Verify committed frontend assets')
    assert parity['run'] == 'git diff --exit-code -- amplifier_web/static'
    assert 'continue-on-error' not in parity
    proof = next(step for step in steps if step.get('name') == 'Record frontend proof')
    assert 'if' not in proof and names.index(proof['name']) > names.index(parity['name'])
    assert 'frontend-build-output' not in (ROOT / 'scripts/frontend_release_proof.py').read_text()
