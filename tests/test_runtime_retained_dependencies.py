"""Retained source pins resolve transitively without hiding version conflicts."""
import json
import shutil
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest
from packaging.markers import default_environment

from amplifier_web import runtime_environment as runtime
from amplifier_web import runtime_qualification as qualification
from amplifier_web import runtime_retained_dependencies as retained
from amplifier_web import selected_runtime_dependencies as selected
from amplifier_web import updates
from test_runtime_source_policy_resolver import BACKEND, git


NAME = 'fixture-component'
URL = 'https://example.invalid/retained-component'
OLD, NEW = 'a' * 40, 'b' * 40
BASE = {'project': {'name': 'worker', 'version': '1'}}


def encode(data):
    return ('\n'.join(json.dumps(key) + ' = ' + runtime.toml_value(value) for key, value in data.items()) + '\n').encode()


def row(name=NAME, ref=OLD, subdirectory=''):
    return {'kind': 'runtime dependency', 'package': name, 'url': URL,
            'ref': ref, 'subdirectory': subdirectory}


def reference(ref=OLD):
    return NAME + ' @ git+' + URL + '@' + ref


def test_generated_provenance_preserves_authored_policy_and_moving_sources():
    authored = 'other-package==3'
    data = {**BASE, 'tool': {'uv': {'override-dependencies': [authored],
        'constraint-dependencies': [NAME + '<2']}, 'another-owner': {'setting': True}}}
    result = tomllib.loads(runtime.augmented_manifest(encode(data), [row()]).decode())
    assert result['tool']['uv']['override-dependencies'] == [authored, reference()]
    assert result['tool']['uv']['constraint-dependencies'] == [NAME + '<2']
    assert result['tool']['another-owner'] == {'setting': True}
    assert result['tool']['amplifier-unified']['generated-source-overrides'] == {NAME: reference()}
    moving = tomllib.loads(runtime.augmented_manifest(encode(data), [row(ref='main')]).decode())
    assert moving['tool']['uv']['override-dependencies'] == [authored]
    assert 'amplifier-unified' not in moving['tool']


@pytest.mark.parametrize('override', [reference(), reference(NEW), NAME + '>=2'])
def test_authored_override_is_never_claimed_or_replaced(override):
    data = {**BASE, 'tool': {'uv': {'override-dependencies': [override]}}}
    if override != reference():
        with pytest.raises(runtime.ProtectedRuntimeSource):
            runtime.augmented_manifest(encode(data), [row()])
    else:
        result = tomllib.loads(runtime.augmented_manifest(encode(data), [row()]).decode())
        assert result['tool']['uv']['override-dependencies'] == [override]
        assert 'amplifier-unified' not in result['tool']


@pytest.mark.parametrize('change', ['authored', 'missing', 'changed', 'duplicate', 'unbound'])
async def test_refresh_refuses_unowned_or_changed_override_before_any_write(tmp_path, monkeypatch, change):
    home, generation = tmp_path / 'app', 'd' * 32
    stage = runtime.receipt_directory(home, generation); stage.mkdir(parents=True)
    content = runtime.augmented_manifest(encode(BASE), [row()])
    data = tomllib.loads(content.decode())
    policy = data['tool']['uv']['override-dependencies']
    marker = data['tool']['amplifier-unified']['generated-source-overrides']
    if change == 'authored': marker.clear()
    if change == 'missing': policy.clear()
    if change == 'changed': policy[0] = reference(NEW)
    if change == 'duplicate': policy.append(policy[0])
    if change == 'unbound': marker[NAME] = reference(NEW)
    project = runtime.prepare_project(home, generation, content=encode(data))
    path = project / 'pyproject.toml'
    before = path.read_bytes()
    async def unexpected(*args, **kwargs):
        pytest.fail('Refused reconciliation must not run the resolver')
    monkeypatch.setattr(updates, 'process', unexpected)
    with pytest.raises(ValueError, match='override'):
        await selected.refresh(SimpleNamespace(home=home), stage, project, {NAME: {
            'sourceKind': 'dependency', 'url': URL, 'ref': 'main', 'subdirectory': '',
            'previousRevision': OLD, 'revision': NEW}})
    assert path.read_bytes() == before


@pytest.mark.parametrize('case', ['moving-url', 'version-conflict', 'authored-constraint'])
async def test_real_resolver_preserves_pin_and_rejects_hidden_incompatibility(tmp_path, monkeypatch, case):
    uv = shutil.which('uv')
    assert uv, 'This resolver regression requires the qualification environment to provide uv'
    repo = tmp_path / 'repo'; repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    requirement = NAME + ('>=2' if case == 'version-conflict' else ' @ git+' + URL + '@main')
    (repo / 'pyproject.toml').write_text('[project]\nname="fixture-component"\nversion="1.0"\n'
        '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
    (repo / 'backend.py').write_text(BACKEND)
    hook = repo / 'hook'; hook.mkdir()
    hook.joinpath('pyproject.toml').write_text('[project]\nname="fixture-hook"\nversion="1.0"\n'
        'dependencies=[' + json.dumps(requirement) + ']\n[build-system]\nrequires=[]\n'
        'build-backend="backend"\nbackend-path=["."]\n')
    hook.joinpath('backend.py').write_text(BACKEND.replace('fixture_component', 'fixture_hook').replace(
        'fixture-component', 'fixture-hook').replace('Version: 1.0\\n',
        'Version: 1.0\\nRequires-Dist: ' + requirement + '\\n'))
    git(repo, 'add', '.'); git(repo, 'commit', '-m', 'retained')
    old = git(repo, 'rev-parse', 'HEAD')
    (repo / 'revision.txt').write_text('new')
    git(repo, 'add', '.'); git(repo, 'commit', '-m', 'new main')
    config = tmp_path / 'gitconfig'
    config.write_text('[url "' + repo.as_uri() + '"]\n\tinsteadOf = ' + URL + '\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('UV_NO_INDEX', '1')
    monkeypatch.delenv('UV_OFFLINE', raising=False)
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS', 'never')
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'uv-cache'))
    baseline = {**BASE}
    if case == 'authored-constraint':
        baseline['tool'] = {'uv': {'constraint-dependencies': [NAME + '>=2']}}
    content = runtime.augmented_manifest(encode(baseline), [row(ref=old), row('fixture-hook', old, 'hook')])
    project = runtime.prepare_project(tmp_path / 'app', 'e' * 32, content=content)
    command = [uv, 'sync', '--project', str(project), '--python', sys.executable]
    if case == 'moving-url':
        original = tomllib.loads(content.decode())
        del original['tool']['uv']['override-dependencies']
        del original['tool']['amplifier-unified']
        project.joinpath('pyproject.toml').write_bytes(encode(original))
        failed = subprocess.run(command, text=True, capture_output=True, timeout=90)
        assert failed.returncode != 0 and 'conflicting URLs' in failed.stderr
        project.joinpath('pyproject.toml').write_bytes(content)
    if case == 'authored-constraint':
        failed = subprocess.run(command, text=True, capture_output=True, timeout=90)
        assert failed.returncode != 0 and NAME in failed.stderr
        return
    await updates.process(*command, timeout=90)
    locked = runtime.locked_sources(project)
    assert locked[NAME].fragment == old
    assert locked['fixture-hook'].fragment == old
    if case == 'version-conflict':
        with pytest.raises(ValueError, match='version requirement'):
            await qualification.freeze(SimpleNamespace(home=tmp_path / 'app'), 'e' * 32, project)
        assert not (runtime.receipt_directory(tmp_path / 'app', 'e' * 32) / 'runtime.lock').exists()
    else:
        retained.verify(project)


@pytest.mark.parametrize('case', ['direct', 'active-extra', 'inactive-extra', 'inactive-marker', 'unrelated', 'root'])
def test_compatibility_check_is_limited_to_active_overridden_targets(case):
    requirement = NAME + '>=2'
    if case in {'active-extra', 'inactive-extra'}: requirement += ' ; extra == "use"'
    if case == 'inactive-marker': requirement += ' ; python_version < "1"'
    if case == 'unrelated': requirement = 'unrelated-package>=2'
    dist = lambda name, requires=(): SimpleNamespace(metadata={'Name': name}, version='1.0', requires=requires)
    rows = [dist(NAME), dist('bridge', [] if case == 'root' else [requirement]),
            dist('parent', ['bridge[use]'] if case == 'active-extra' else [])]
    root = [NAME + '>=2'] if case == 'root' else []
    if case in {'direct', 'active-extra', 'root'}:
        with pytest.raises(ValueError, match='version requirement'):
            retained.verify_metadata(rows, {NAME}, default_environment(), root)
    else:
        retained.verify_metadata(rows, {NAME}, default_environment(), root)
