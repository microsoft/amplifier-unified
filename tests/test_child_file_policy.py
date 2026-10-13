"""Child overlays and saved plans may narrow file tools, never widen them."""
import copy
import pytest

pytest.importorskip('amplifier_module_loop_live')
from amplifier_web.host.children import child_plan


@pytest.mark.parametrize('module', ['tool-filesystem', 'tool-apply-patch'])
@pytest.mark.parametrize('kind', ['read', 'write'])
@pytest.mark.parametrize('layer', ['overlay', 'spawn', 'retained'])
@pytest.mark.parametrize('allowed', [['src'], []])
def test_child_keeps_boundary_across_composition(tmp_path, module, kind, layer, allowed):
    key, deny = f'allowed_{kind}_paths', f'denied_{kind}_paths'
    narrow = {'module': module, 'config': {key: allowed, deny: ['src/private']}}
    broad = {'module': module, 'instance_id': 'renamed', 'config': {key: ['.'], deny: []}}
    parent, overlay, retained = {'tools': [narrow]}, {'tools': [broad]}, None
    if layer == 'spawn':
        parent['spawn'] = {'tools': [broad]}
        overlay = {}
    if layer == 'retained':
        parent = {'tools': [broad]}
        retained = [narrow]
    before = copy.deepcopy((parent, overlay, retained))
    result = child_plan(parent, overlay, working_dir=tmp_path, retained_image_tools=retained)
    for tool in result['tools']:
        assert tool['config'][key] == [str(tmp_path / p) for p in allowed]
        assert tool['config'][deny] == [str(tmp_path / 'src/private')]
    assert (parent, overlay, retained) == before


def test_excluding_filesystem_does_not_remove_its_patch_boundary(tmp_path):
    parent = {'tools': [{'module': 'tool-filesystem', 'config': {'allowed_write_paths': ['src']}}]}
    overlay = {'tools': [{'module': 'tool-apply-patch', 'config': {'allowed_write_paths': ['.']}}]}
    plan = child_plan(parent, overlay, tool_inheritance={'exclude_tools': ['tool-filesystem']}, working_dir=tmp_path)
    assert len(plan['tools']) == 1
    assert plan['tools'][0]['config']['allowed_write_paths'] == [str(tmp_path / 'src')]
