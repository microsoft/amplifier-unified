"""Host instruction policy through Foundation's real, fresh prompt renderer."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_foundation.mentions import parse_mentions
from amplifier_web.host.mentions import include_instruction_files


def factory(bundle, workspace):
    prepared = PreparedBundle(bundle.to_mount_plan(), BundleModuleResolver({}), bundle)
    session = SimpleNamespace(coordinator=SimpleNamespace(hooks=SimpleNamespace(emit=AsyncMock())))
    return prepared.create_system_prompt_factory(session, session_cwd=workspace)


@pytest.fixture(autouse=True)
def instruction_home(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setattr(Path, 'home', lambda: home)
    original_expanduser = Path.expanduser
    monkeypatch.setattr(Path, 'expanduser', lambda self: home / str(self)[2:]
                        if str(self).startswith('~/') else original_expanduser(self))
    return home


@pytest.mark.parametrize('body', [None, '', 'Original root instructions.'])
async def test_optional_instruction_files_reload_without_mutating_cached_bundle(tmp_path, body):
    home, workspace, cache = (tmp_path / name for name in ('home', 'workspace', 'bundle-cache'))
    for path in (home / '.amplifier', workspace / '.amplifier', cache / '.amplifier'):
        path.mkdir(parents=True)
    (cache / '.amplifier/AGENTS.md').write_text('CACHE-WORKSPACE-MUST-NOT-LOAD')
    bundle = Bundle(name='custom', instruction=body, base_path=cache)
    composed = include_instruction_files(bundle, execution_workspace=workspace)
    render = factory(composed, workspace)
    assert bundle.instruction == body
    assert bundle.context == {}
    repeated = include_instruction_files(composed, execution_workspace=workspace)
    assert repeated.instruction == composed.instruction
    assert repeated.context == composed.context
    assert repeated.context is not composed.context
    assert 'CACHE-WORKSPACE-MUST-NOT-LOAD' not in await render()
    (home / '.amplifier/AGENTS.md').write_text('GLOBAL-JOURNAL-POLICY\n@~/.amplifier/journal-rules.md')
    (home / '.amplifier/journal-rules.md').write_text('NESTED-JOURNAL-DESTINATION')
    (workspace / '.amplifier/AGENTS.md').write_text('PROJECT-JOURNAL-POLICY')
    (workspace / 'AGENTS.md').write_text('ROOT-JOURNAL-POLICY')
    first = await render()
    for sentinel in ('GLOBAL-JOURNAL-POLICY', 'NESTED-JOURNAL-DESTINATION',
                     'PROJECT-JOURNAL-POLICY', 'ROOT-JOURNAL-POLICY'):
        assert first.count(sentinel) == 1
    assert first.index('GLOBAL-JOURNAL-POLICY') < first.index('PROJECT-JOURNAL-POLICY') < first.index('ROOT-JOURNAL-POLICY')
    assert 'CACHE-WORKSPACE-MUST-NOT-LOAD' not in first
    (home / '.amplifier/AGENTS.md').write_text('UPDATED-GLOBAL-POLICY')
    second = await render()
    assert 'UPDATED-GLOBAL-POLICY' in second and 'GLOBAL-JOURNAL-POLICY' not in second
    assert 'NESTED-JOURNAL-DESTINATION' not in second
    (workspace / '.amplifier/AGENTS.md').unlink()
    assert 'PROJECT-JOURNAL-POLICY' not in await render()
    (workspace / 'AGENTS.md').write_text('UPDATED-ROOT-POLICY')
    assert 'UPDATED-ROOT-POLICY' in await render()
    (workspace / 'AGENTS.md').unlink()
    assert 'UPDATED-ROOT-POLICY' not in await render()
    (workspace / 'AGENTS.md').write_text('RECREATED-ROOT-POLICY')
    assert (await render()).count('RECREATED-ROOT-POLICY') == 1


async def test_existing_instruction_mentions_and_each_session_workspace_are_preserved(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    for path, text in ((first, 'FIRST-WORKSPACE'), (second, 'SECOND-WORKSPACE')):
        (path / '.amplifier').mkdir(parents=True)
        (path / '.amplifier/AGENTS.md').write_text(text)
    root = Bundle(name='work', instruction='Keep the root.\n@.amplifier/AGENTS.md')
    included = include_instruction_files(root, execution_workspace=first)
    assert included.instruction.count('@.amplifier/AGENTS.md') == 1
    assert included.instruction.startswith(root.instruction)
    first_prompt = await factory(included, first)()
    second_included = include_instruction_files(root, execution_workspace=second)
    second_prompt = await factory(second_included, second)()
    assert root.context == {}
    assert included.context != second_included.context
    assert first_prompt.count('FIRST-WORKSPACE') == 1 and 'SECOND-WORKSPACE' not in first_prompt
    assert second_prompt.count('SECOND-WORKSPACE') == 1 and 'FIRST-WORKSPACE' not in second_prompt


@pytest.mark.parametrize('with_entry_point', [False, True], ids=['root-only', 'both'])
async def test_root_workspace_guidance_and_scratch_reload_through_entry_point(tmp_path, with_entry_point):
    from amplifier_web.workspace_starters import DEVELOPMENT_INSTRUCTIONS
    workspace = tmp_path / 'container'
    (workspace / '.amplifier').mkdir(parents=True)
    (workspace / 'AGENTS.md').write_text(DEVELOPMENT_INSTRUCTIONS)
    if with_entry_point:
        (workspace / '.amplifier/AGENTS.md').write_text('@../AGENTS.md\n@../SCRATCH.md\n')
    (workspace / 'SCRATCH.md').write_text('ROOT-SCRATCH-INITIAL')
    (workspace / '.amplifier/SCRATCH.md').write_text('WRONG-SCRATCH')
    included = include_instruction_files(Bundle(name='work', instruction='Root bundle'),
                                         execution_workspace=workspace)
    render = factory(included, workspace)
    first = await render()
    assert first.count('The workspace root is never the source root') == 1
    assert first.count('ROOT-SCRATCH-INITIAL') == 1
    assert 'WRONG-SCRATCH' not in first
    (workspace / 'SCRATCH.md').write_text('ROOT-SCRATCH-UPDATED')
    second = await render()
    assert second.count('ROOT-SCRATCH-UPDATED') == 1 and 'ROOT-SCRATCH-INITIAL' not in second


async def test_selected_config_home_ignores_home_environment_and_cache_decoys(tmp_path, monkeypatch, instruction_home):
    config_home = tmp_path / 'selected settings'
    workspace = tmp_path / 'execution checkout'
    cache = tmp_path / 'bundle cache'
    decoys = (instruction_home / '.amplifier', tmp_path / 'environment-home',
              tmp_path / 'app-home', tmp_path / 'registry-home', cache / '.amplifier')
    for path in (config_home, workspace / '.amplifier', *decoys):
        path.mkdir(parents=True)
    for path in decoys:
        (path / 'AGENTS.md').write_text('DECOY-MUST-NOT-LOAD')
    monkeypatch.setenv('AMPLIFIER_HOME', str(decoys[1]))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(decoys[2]))
    (config_home / 'AGENTS.md').write_text('SELECTED-GLOBAL\n@./nested.md')
    (config_home / 'nested.md').write_text('SELECTED-NESTED')
    (workspace / '.amplifier/AGENTS.md').write_text('EXECUTION-PROJECT')
    (workspace / 'AGENTS.md').write_text('EXECUTION-ROOT')
    bundle = Bundle(name='work', instruction='Authored root', base_path=cache)
    included = include_instruction_files(bundle, config_home, workspace)
    prompt = await factory(included, workspace)()
    assert list(included.context.values()) == [
        config_home / 'AGENTS.md', workspace / '.amplifier/AGENTS.md', workspace / 'AGENTS.md']
    for sentinel in ('SELECTED-GLOBAL', 'SELECTED-NESTED', 'EXECUTION-PROJECT', 'EXECUTION-ROOT'):
        assert prompt.count(sentinel) == 1
    assert 'DECOY-MUST-NOT-LOAD' not in prompt
    assert bundle.context == {} and bundle.instruction == 'Authored root'


async def test_default_config_home_is_physical_home_not_amplifier_home(tmp_path, monkeypatch, instruction_home):
    physical = instruction_home / '.amplifier'
    environment = tmp_path / 'environment-home'
    workspace = tmp_path / 'workspace'
    for path in (physical, environment, workspace):
        path.mkdir(parents=True)
    (physical / 'AGENTS.md').write_text('PHYSICAL-HOME-DEFAULT')
    (environment / 'AGENTS.md').write_text('ENVIRONMENT-HOME-DECOY')
    monkeypatch.setenv('AMPLIFIER_HOME', str(environment))
    monkeypatch.chdir(workspace)
    included = include_instruction_files(Bundle(name='work'))
    assert list(included.context.values()) == [
        physical / 'AGENTS.md', workspace / '.amplifier/AGENTS.md', workspace / 'AGENTS.md']
    prompt = await factory(included, workspace)()
    assert prompt.count('PHYSICAL-HOME-DEFAULT') == 1
    assert 'ENVIRONMENT-HOME-DECOY' not in prompt


@pytest.mark.parametrize('use_selected_home', [False, True], ids=['default-home', 'selected-home'])
async def test_authored_literal_tilde_mentions_are_not_retargeted(tmp_path, instruction_home, use_selected_home):
    physical = instruction_home / '.amplifier'
    selected = tmp_path / 'selected-config'
    workspace = tmp_path / 'workspace'
    for path in (physical, selected, workspace):
        path.mkdir(parents=True)
    (physical / 'AGENTS.md').write_text('AUTHORED-PHYSICAL-HOME\n@~/notes.md')
    (instruction_home / 'notes.md').write_text('AUTHORED-HOME-NESTED')
    (selected / 'AGENTS.md').write_text('HOST-SELECTED-CONFIG')
    instruction = 'Keep this literal.\n@~/.amplifier/AGENTS.md\n`@~/not-loaded.md`'
    bundle = Bundle(name='work', instruction=instruction)
    included = include_instruction_files(bundle, selected if use_selected_home else None, workspace)
    assert included.instruction.startswith(instruction + '\n\n')
    assert included.instruction.count('@~/.amplifier/AGENTS.md') == 1
    assert bundle.instruction == instruction and bundle.context == {}
    prompt = await factory(included, workspace)()
    for sentinel in ('AUTHORED-PHYSICAL-HOME', 'AUTHORED-HOME-NESTED'):
        assert prompt.count(sentinel) == 1
    assert prompt.count('HOST-SELECTED-CONFIG') == (1 if use_selected_home else 0)


async def test_whitespace_paths_and_alias_collisions_are_deterministic_and_nonmutating(tmp_path):
    selected, workspace = tmp_path / 'settings with spaces', tmp_path / 'checkout with spaces'
    for path in (selected, workspace):
        path.mkdir()
    (selected / 'AGENTS.md').write_text('SPACE-GLOBAL')
    (workspace / 'AGENTS.md').write_text('SPACE-ROOT')
    authored = tmp_path / 'authored.md'
    authored.write_text('AUTHORED-CONTEXT')
    stem = '__unified_instruction_global'
    instruction = f'Authored references.\n@work:{stem}\n@work:{stem}_2'
    bundle = Bundle(name='work', instruction=instruction, context={stem: authored},
                    source_base_paths={'dependency': tmp_path / 'dependency-cache'},
                    _pending_context={f'work:{stem}_3': 'unavailable:rules.md'})
    included = include_instruction_files(bundle, selected, workspace)
    again = include_instruction_files(bundle, selected, workspace)
    repeated = include_instruction_files(included, selected, workspace)
    assert included.instruction == again.instruction == repeated.instruction
    assert included.context == again.context == repeated.context
    assert included.context is not bundle.context
    assert included.context[stem] == authored
    assert included.context[f'{stem}_4'] == selected / 'AGENTS.md'
    assert f'{stem}_2' not in included.context and f'{stem}_3' not in included.context
    assert parse_mentions(included.instruction)[-3:] == [
        f'@work:{stem}_4', '@work:__unified_instruction_project',
        '@work:__unified_instruction_workspace']
    assert included.source_base_paths == bundle.source_base_paths == {'dependency': tmp_path / 'dependency-cache'}
    assert bundle.context == {stem: authored} and bundle.instruction == instruction
    assert bundle._pending_context == {f'work:{stem}_3': 'unavailable:rules.md'}
    prompt = await factory(included, workspace)()
    for sentinel in ('AUTHORED-CONTEXT', 'SPACE-GLOBAL', 'SPACE-ROOT'):
        assert prompt.count(sentinel) == 1


@pytest.mark.parametrize('name', ['', None, 'root with spaces', 'root:other', 'root/path', 'root@other'])
def test_invalid_root_namespace_fails_explicitly_without_mutation(tmp_path, name):
    bundle = Bundle(name=name, instruction='Preserve authored instructions')
    with pytest.raises(ValueError, match='root bundle name usable as a mention namespace'):
        include_instruction_files(bundle, tmp_path / 'config', tmp_path / 'workspace')
    assert bundle.instruction == 'Preserve authored instructions'
    assert bundle.context == {} and bundle.source_base_paths == {}
