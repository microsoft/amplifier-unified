"""Root and child search use read policy, independently of write authority."""
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_host_session_resume import mounted_host as mounted_host

pytest.importorskip('amplifier_module_loop_live')

from amplifier_web.host.session import _apply_host_policy, _apply_settings
from amplifier_web.host.children import child_plan
from amplifier_web.read_policy import validate_mounted_read_policy


def root(tmp_path, tools, settings=None, agents=None):
    bundle = SimpleNamespace(tools=copy.deepcopy(tools), agents=agents or {}, providers=[], hooks=[], session={})
    config = SimpleNamespace(workspace=tmp_path, settings=settings or {}, providers=[])
    return _apply_host_policy(bundle, config)


def test_default_search_has_explicit_unrestricted_read_policy_only(tmp_path):
    bundle = root(tmp_path, [{'module': 'tool-filesystem'}, {'module': 'tool-search'}])
    fs, search = bundle.tools
    assert fs['config'] == {'allowed_write_paths': [str(tmp_path)]}
    assert search['config'] == {'allowed_paths': None, 'denied_paths': [],
        'grep': {'allowed_paths': None, 'denied_paths': []}, 'glob': {'allowed_paths': None, 'denied_paths': []}}


@pytest.mark.parametrize('allowed', [None, [], ['src']])
@pytest.mark.parametrize('layer', ['bundle', 'shared'])
def test_filesystem_read_policy_restricts_all_search_operations(tmp_path, allowed, layer):
    policy = {'allowed_read_paths': allowed, 'denied_read_paths': ['src/private']}
    tools = [{'module': 'tool-filesystem', 'config': policy if layer == 'bundle' else {}}, {'module': 'tool-search'}]
    settings = {'overrides': {'tool-filesystem': {'config': policy}}} if layer == 'shared' else {}
    bundle = root(tmp_path, tools, settings)
    search = bundle.tools[1]['config']
    expected = None if allowed is None else [str(tmp_path / p) for p in allowed]
    for cfg in (search, search['grep'], search['glob']):
        assert cfg['allowed_paths'] == expected
        assert cfg['denied_paths'] == [str(tmp_path / 'src/private')]
        assert 'allowed_write_paths' not in cfg


def test_shared_settings_preserve_bundle_and_per_tool_boundaries(tmp_path):
    original = {'allowed_paths': ['src'], 'denied_paths': ['src/private'],
                'grep': {'allowed_paths': ['src/code'], 'denied_paths': ['src/code/secret']}}
    bundle = SimpleNamespace(tools=[{'module': 'tool-search', 'config': copy.deepcopy(original)}],
                             agents={}, providers=[], hooks=[], session={})
    settings = {'config': {'tools': [{'module': 'tool-search', 'config': {'allowed_paths': None,
                'denied_paths': ['src/shared'], 'grep': {'allowed_paths': None, 'max_results': 9}}}]}}
    config = SimpleNamespace(workspace=tmp_path, settings=settings, providers=[])
    _apply_settings(bundle, config)
    _apply_host_policy(bundle, config)
    value = bundle.tools[0]['config']
    assert value['allowed_paths'] == [str(tmp_path / 'src')]
    assert value['grep']['allowed_paths'] == [str(tmp_path / 'src/code')]
    assert value['glob']['allowed_paths'] == [str(tmp_path / 'src')]
    assert set(value['grep']['denied_paths']) == {str(tmp_path / p) for p in ['src/private', 'src/shared', 'src/code/secret']}
    assert value['grep']['max_results'] == 9
    assert original['allowed_paths'] == ['src']


@pytest.mark.parametrize('layer', ['parent', 'overlay', 'spawn', 'retained'])
@pytest.mark.parametrize('allowed', [None, [], ['src']])
def test_child_preserves_search_and_filesystem_read_ceilings(tmp_path, layer, allowed):
    narrow = [{'module': 'tool-filesystem', 'config': {'allowed_read_paths': None, 'allowed_write_paths': []}},
              {'module': 'tool-search', 'config': {'allowed_paths': allowed, 'denied_paths': ['src/private'],
                  'grep': {'allowed_paths': ['src/code']}}}]
    broad = [{'module': 'tool-search', 'instance_id': 'different',
              'config': {'allowed_paths': None, 'denied_paths': [], 'grep': {'allowed_paths': None}}}]
    parent, overlay, retained = {'tools': narrow}, {'tools': broad}, None
    if layer == 'overlay':
        parent, overlay = {'tools': broad}, {'tools': narrow}
    elif layer == 'spawn':
        parent, overlay = {'tools': narrow, 'spawn': {'tools': broad}}, {}
    elif layer == 'retained':
        parent, overlay, retained = {'tools': broad}, {}, narrow
    before = copy.deepcopy((parent, overlay, retained))
    plan = child_plan(parent, overlay, working_dir=tmp_path, retained_image_tools=retained,
                      tool_inheritance={'exclude_tools': ['tool-filesystem']})
    searches = [row['config'] for row in plan['tools'] if row['module'] == 'tool-search']
    assert searches
    for cfg in searches:
        assert cfg['allowed_paths'] == (None if allowed is None else [str(tmp_path / p) for p in allowed])
        assert cfg['grep']['allowed_paths'] == ([] if allowed == [] else [str(tmp_path / 'src/code')])
        assert cfg['denied_paths'] == [str(tmp_path / 'src/private')]
    assert (parent, overlay, retained) == before


def test_search_exclusion_does_not_remove_retained_filesystem_read_ceiling(tmp_path):
    parent = {'tools': [{'module': 'tool-filesystem', 'config': {'allowed_read_paths': ['src']}}]}
    plan = child_plan(parent, {'tools': [{'module': 'tool-search'}]}, working_dir=tmp_path,
                      tool_inheritance={'exclude_tools': ['tool-filesystem']})
    assert plan['tools'][0]['config']['allowed_paths'] == [str(tmp_path / 'src')]


def test_old_recorded_reference_modules_are_rejected_without_fallback():
    old_search = type('GrepTool', (), {'__module__': 'amplifier_module_tool_search.grep'})()
    old_search.config = {'allowed_paths': None}
    old_search.path_policy = SimpleNamespace(allowed_roots=(Path.cwd(),))
    coordinator = SimpleNamespace(get=lambda key: {'grep': old_search})
    with pytest.raises(ValueError, match='Settings > Updates'):
        validate_mounted_read_policy(coordinator)
    old_reader = type('ReadTool', (), {'__module__': 'amplifier_module_tool_filesystem.read'})()
    old_reader.config = {'denied_read_paths': ['private']}
    with pytest.raises(ValueError, match='Settings > Updates'):
        validate_mounted_read_policy(SimpleNamespace(get=lambda key: {'read_file': old_reader}))


@pytest.mark.asyncio
@pytest.mark.parametrize('retained', [False, True])
@pytest.mark.parametrize('denied', [False, True])
async def test_actual_root_and_child_tools_share_read_policy_without_write_grant(tmp_path, retained, denied):
    pytest.importorskip('amplifier_module_tool_filesystem')
    pytest.importorskip('amplifier_module_tool_search')
    from amplifier_module_tool_filesystem.read import ReadTool
    from amplifier_module_tool_filesystem.write import WriteTool
    from amplifier_module_tool_search.grep import GrepTool
    from amplifier_module_tool_search.glob import GlobTool
    workspace, external = tmp_path / 'workspace', tmp_path / 'installed'
    workspace.mkdir()
    external.mkdir()
    source = external / 'producer.py'
    source.write_text('public_source = True\n')
    fs_config = {'denied_read_paths': [str(external)]} if denied else {}
    bundle = root(workspace, [{'module': 'tool-filesystem', 'config': fs_config}, {'module': 'tool-search'}])
    plan = {'tools': bundle.tools}
    child = child_plan(plan, {}, working_dir=workspace, retained_image_tools=plan['tools'] if retained else None)
    coordinator = SimpleNamespace(hooks=SimpleNamespace(emit=AsyncMock()))
    for value in (plan, child):
        fs, search = value['tools']
        file_config = {**fs['config'], 'working_dir': str(workspace)}
        read = await ReadTool(file_config, coordinator).execute({'file_path': str(source)})
        assert read.success is not denied
        for engine in (True, False):
            grep = GrepTool({**search['config']['grep'], 'working_dir': str(workspace)})
            if engine and not grep.use_ripgrep:
                pytest.fail('Focused acceptance requires actual ripgrep')
            grep.use_ripgrep = engine
            result = await grep.execute({'path': str(source), 'pattern': 'public_source', 'output_mode': 'content'})
            assert result.success is not denied
            if not denied:
                assert result.output['matches_count'] == 1
        result = await GlobTool({**search['config']['glob'], 'working_dir': str(workspace)}).execute(
            {'path': str(external), 'pattern': '*.py'})
        assert result.success is not denied
        assert not (await WriteTool(file_config, coordinator).execute({'file_path': str(source), 'content': 'changed'})).success
        assert source.read_text() == 'public_source = True\n'


def test_search_scalar_policy_and_nested_agent_boundary_survive_host_composition(tmp_path):
    tools = [{'module': 'tool-search', 'config': {'allowed_paths': 'src',
              'denied_paths': 'src/private', 'grep': {'allowed_paths': 'src/code'}}}]
    agents = {'nested': {'tools': [{'module': 'tool-search', 'config': {'allowed_paths': None}}]}}
    bundle = root(tmp_path, tools, agents=agents)
    for row in [bundle.tools[0], bundle.agents['nested']['tools'][0]]:
        cfg = row['config']
        assert cfg['allowed_paths'] == [str(tmp_path / 'src')]
        assert cfg['grep']['allowed_paths'] == [str(tmp_path / 'src/code')]
        assert cfg['glob']['denied_paths'] == [str(tmp_path / 'src/private')]


def test_search_policy_rebinds_only_repository_paths_at_handoff(tmp_path):
    old, new, external = tmp_path / 'original', tmp_path / 'checkout', tmp_path / 'external'
    bundle = SimpleNamespace(tools=[{'module': 'tool-search', 'config': {
        'allowed_paths': [str(old / 'src'), str(external)], 'denied_paths': [str(old / 'src/private')]}}], agents={})
    _apply_host_policy(bundle, SimpleNamespace(workspace=old, settings={}), execution_workspace=new)
    assert bundle.tools[0]['config']['allowed_paths'] == [str(new / 'src'), str(external)]
    assert bundle.tools[0]['config']['denied_paths'] == [str(new / 'src/private')]



async def test_qualification_rejects_old_policy_before_history_or_input_admission(mounted_host):
    from amplifier_web.host.session import prepare_manager
    h = mounted_host
    old = type('GrepTool', (), {'__module__': 'amplifier_module_tool_search.grep'})()
    old.config = {'allowed_paths': None}
    old.path_policy = SimpleNamespace(allowed_roots=(h.config.workspace,))
    previous = h.session.coordinator.get
    h.session.coordinator.get = lambda key: {'grep': old} if key == 'tools' else previous(key)
    with pytest.raises(ValueError, match='Settings > Updates'):
        await prepare_manager(h.config.workspace, runtime=h.runtime, qualification_readonly=True)
    h.session.execute.assert_not_called()
    h.session.cleanup.assert_awaited_once()
    assert not h.path.exists()
