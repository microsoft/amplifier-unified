"""Explicit latest qualification must never rewrite a prior receipt."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

spec = importlib.util.spec_from_file_location('latest_resolution', Path(__file__).parents[1] / 'scripts/resolve_amplifier_latest.py')
latest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(latest)


def test_amplifier_inventory_includes_extras_groups_and_transitive_lock():
    manifest = {'project': {'dependencies': ['amplifier-core>=1.6', 'third-party'],
                           'optional-dependencies': {'native': ['amplifier-native @ git+https://example.invalid/native@main']}},
                'dependency-groups': {'dev': ['Amplifier_Test[extra]', {'include-group': 'other'}]}}
    lock = {'package': [{'name': 'amplifier-transitive'}, {'name': 'third-party'}]}
    assert latest.amplifier_names(manifest, lock) == {'amplifier-core', 'amplifier-native', 'amplifier-test', 'amplifier-transitive'}


def test_latest_refresh_and_replay_use_separate_immutable_evidence(tmp_path):
    uv = shutil.which('uv')
    if not uv:
        pytest.skip('uv is required for the local Git resolver integration check')
    remote = tmp_path / 'remote'
    remote.mkdir()
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=remote, stderr=subprocess.DEVNULL, text=True).strip()
    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (remote / 'pyproject.toml').write_text('[project]\nname="amplifier-fixture-resolver"\nversion="0.1"\n')
    git('add', '.')
    git('commit', '-m', 'old')
    old = git('rev-parse', 'HEAD')
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="0.1"\nrequires-python=">=3.13"\ndependencies=[' + json.dumps('amplifier-fixture-resolver @ git+' + remote.as_uri() + '@main') + ']\n')
    subprocess.run([uv, 'lock', '--project', str(project)], check=True, capture_output=True)
    before = (project / 'uv.lock').read_bytes()
    (remote / 'marker').write_text('new')
    git('add', '.')
    git('commit', '-m', 'new')
    new = git('rev-parse', 'HEAD')
    replay = latest.qualify(project, tmp_path / 'replay', 'replay')
    assert replay['resolved'][0]['revision'] == old
    report = latest.qualify(project, tmp_path / 'latest', 'latest')
    assert report['resolved'][0]['revision'] == new
    assert report['resolved'][0]['requested'] == ['main']
    assert (tmp_path / 'latest/before.lock').read_bytes() == before
    assert (tmp_path / 'replay/resolved.lock').read_bytes() == before
    with pytest.raises(FileExistsError):
        latest.qualify(project, tmp_path / 'latest', 'latest')


def test_resolution_failure_restores_original_lock(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="0.1"\n')
    before = b'version = 1\n'
    (project / 'uv.lock').write_bytes(before)
    def fail(*args, **kwargs):
        (project / 'uv.lock').write_text('damaged candidate')
        raise subprocess.CalledProcessError(1, 'uv')
    with pytest.raises(subprocess.CalledProcessError):
        latest.qualify(project, tmp_path / 'failed', 'latest', run=fail)
    assert (project / 'uv.lock').read_bytes() == before
    assert (tmp_path / 'failed/before.lock').read_bytes() == before
    assert (tmp_path / 'failed/failed.lock').read_text() == 'damaged candidate'
