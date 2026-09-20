"""Adding a patch writer cannot bypass the existing file-access choice."""
from types import SimpleNamespace
from pathlib import Path

import pytest

from amplifier_web.host.session import _apply_host_policy


def apply(tmp_path, patch, shared):
    bundle = SimpleNamespace(tools=[{'module': 'tool-apply-patch', 'config': patch}], agents={
        'child': {'tools': [{'module': 'tool-apply-patch', 'config': {'engine': 'function'}}]}})
    config = SimpleNamespace(workspace=tmp_path, settings={
        'overrides': {'tool-filesystem': {'config': shared}}})
    return _apply_host_policy(bundle, config)


def test_shared_allow_and_deny_cover_root_and_child_patch_tools(tmp_path):
    policy = {'allowed_write_paths': [str(tmp_path / 'allowed')],
              'denied_write_paths': [str(tmp_path / 'allowed/private')]}
    result = apply(tmp_path, {'engine': 'native'}, policy)
    assert result.tools[0]['config'] == {'engine': 'native', **policy}
    assert result.agents['child']['tools'][0]['config'] == {'engine': 'function', **policy}


@pytest.mark.parametrize('shared,patch,expected', [
    (['.'], ['src'], ['src']),
    (['src'], ['.'], ['src']),
    (['src'], ['other'], []),
    ([], ['.'], []),
    (['.'], [], []),
])
def test_allowlists_intersect_instead_of_widening(tmp_path, shared, patch, expected):
    result = apply(tmp_path, {'allowed_write_paths': patch}, {'allowed_write_paths': shared})
    assert result.tools[0]['config']['allowed_write_paths'] == [str(tmp_path / p) for p in expected]


def test_patch_denials_survive_shared_policy_and_symlinks_resolve(tmp_path):
    (tmp_path / 'real').mkdir()
    (tmp_path / 'alias').symlink_to(tmp_path / 'real', target_is_directory=True)
    result = apply(tmp_path, {'allowed_write_paths': ['alias'], 'denied_write_paths': ['real/private']},
                   {'allowed_write_paths': ['real'], 'denied_write_paths': ['real/secret']})
    assert result.tools[0]['config'] == {'allowed_write_paths': [str(tmp_path / 'real')],
        'denied_write_paths': [str(tmp_path / 'real/private'), str(tmp_path / 'real/secret')]}


def test_absent_shared_policy_leaves_portable_patch_config_unchanged(tmp_path):
    patch = {'engine': 'function', 'allowed_write_paths': ['src']}
    assert apply(tmp_path, patch, {}).tools[0]['config'] == patch


@pytest.mark.parametrize('invalid', [None, '/somewhere', [None]])
def test_malformed_host_policy_fails_closed(tmp_path, invalid):
    with pytest.raises(ValueError, match='lists of strings'):
        apply(tmp_path, {}, {'allowed_write_paths': invalid})


@pytest.mark.parametrize('section', ['modules', 'config'])
def test_legacy_and_current_module_lists_restrict_patch_even_in_snapshots(tmp_path, section):
    bundle = SimpleNamespace(tools=[{'module': 'tool-apply-patch'}], agents={})
    settings = {section: {'tools': [{'module': 'tool-filesystem', 'config': {
        'allowed_write_paths': ['/home/example/old-linux-workspace']}}]}}
    _apply_host_policy(bundle, SimpleNamespace(workspace=tmp_path, settings=settings))
    assert bundle.tools[0]['config']['allowed_write_paths'] == [str(Path('/home/example/old-linux-workspace').resolve())]


def test_settings_precedence_and_multiple_filesystem_instances(tmp_path):
    bundle = SimpleNamespace(tools=[{'module': 'tool-apply-patch'}], agents={})
    settings = {
        'modules': {'tools': [{'module': 'tool-filesystem', 'config': {'allowed_write_paths': ['/old']}}]},
        'config': {'tools': [
            {'module': 'tool-filesystem', 'config': {'allowed_write_paths': [str(tmp_path)]}},
            {'module': 'tool-filesystem', 'id': 'narrow', 'config': {'allowed_write_paths': [str(tmp_path)]}},
        ]},
        'overrides': {'narrow': {'config': {'allowed_write_paths': [str(tmp_path / 'src')]}}},
    }
    _apply_host_policy(bundle, SimpleNamespace(workspace=tmp_path, settings=settings))
    assert bundle.tools[0]['config']['allowed_write_paths'] == [str(tmp_path / 'src')]
    settings['overrides']['tool-filesystem'] = {'config': {'allowed_write_paths': []}}
    _apply_host_policy(bundle, SimpleNamespace(workspace=tmp_path, settings=settings))
    assert bundle.tools[0]['config']['allowed_write_paths'] == []


def test_snapshot_and_child_patch_environment_paths_expand_before_intersection(tmp_path, monkeypatch):
    monkeypatch.setenv('PATCH_POLICY_ROOT', str(tmp_path))
    patch = {'allowed_write_paths': ['${PATCH_POLICY_ROOT}/src'],
             'denied_write_paths': ['${PATCH_POLICY_ROOT}/src/private']}
    bundle = SimpleNamespace(tools=[{'module': 'tool-apply-patch', 'config': patch}], agents={
        'child': {'tools': [{'module': 'tool-apply-patch', 'config': patch.copy()}]}})
    config = SimpleNamespace(workspace=tmp_path, settings={'overrides': {'tool-filesystem': {
        'config': {'allowed_write_paths': [str(tmp_path)], 'denied_write_paths': []}}}})
    _apply_host_policy(bundle, config)
    expected = {'allowed_write_paths': [str(tmp_path / 'src')],
                'denied_write_paths': [str(tmp_path / 'src/private')]}
    assert bundle.tools[0]['config'] == expected
    assert bundle.agents['child']['tools'][0]['config'] == expected
    assert patch['denied_write_paths'] == ['${PATCH_POLICY_ROOT}/src/private']


@pytest.mark.parametrize('in_child', [False, True])
def test_bundle_declared_filesystem_instance_policy_also_restricts_patch(tmp_path, in_child):
    filesystem = {'module': 'tool-filesystem', 'id': 'project-files'}
    bundle = SimpleNamespace(tools=[{'module': 'tool-apply-patch'}], agents={})
    if in_child:
        bundle.agents['child'] = {'tools': [filesystem, {'module': 'tool-apply-patch'}]}
    else:
        bundle.tools.append(filesystem)
    policy = {'allowed_write_paths': [str(tmp_path / 'src')],
              'denied_write_paths': [str(tmp_path / 'src/private')]}
    settings = {'overrides': {'project-files': {'config': policy}}}
    _apply_host_policy(bundle, SimpleNamespace(workspace=tmp_path, settings=settings))
    assert bundle.tools[0]['config'] == policy
    assert filesystem['config'] == policy
    if in_child:
        assert bundle.agents['child']['tools'][1]['config'] == policy
