"""Host instruction policy through Foundation's real, fresh prompt renderer."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_web.host.mentions import include_instruction_files


def factory(bundle, workspace):
    prepared = PreparedBundle(bundle.to_mount_plan(), BundleModuleResolver({}), bundle)
    session = SimpleNamespace(coordinator=SimpleNamespace(hooks=SimpleNamespace(emit=AsyncMock())))
    return prepared.create_system_prompt_factory(session, session_cwd=workspace)


@pytest.fixture(autouse=True)
def instruction_home(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    original_expanduser = Path.expanduser
    monkeypatch.setattr(Path, 'expanduser', lambda self: home / str(self)[2:]
                        if str(self).startswith('~/') else original_expanduser(self))
    return home


@pytest.mark.parametrize('body', ['', 'Original root instructions.'])
async def test_optional_instruction_files_reload_without_mutating_cached_bundle(tmp_path, body):
    home, workspace, cache = (tmp_path / name for name in ('home', 'workspace', 'bundle-cache'))
    for path in (home / '.amplifier', workspace / '.amplifier', cache / '.amplifier'):
        path.mkdir(parents=True)
    (cache / '.amplifier/AGENTS.md').write_text('CACHE-WORKSPACE-MUST-NOT-LOAD')
    bundle = Bundle(name='custom', instruction=body, base_path=cache)
    composed = include_instruction_files(bundle)
    render = factory(composed, workspace)
    assert bundle.instruction == body
    assert include_instruction_files(composed).instruction == composed.instruction
    assert 'CACHE-WORKSPACE-MUST-NOT-LOAD' not in await render()
    (home / '.amplifier/AGENTS.md').write_text('GLOBAL-JOURNAL-POLICY\n@~/.amplifier/journal-rules.md')
    (home / '.amplifier/journal-rules.md').write_text('NESTED-JOURNAL-DESTINATION')
    (workspace / '.amplifier/AGENTS.md').write_text('PROJECT-JOURNAL-POLICY')
    first = await render()
    for sentinel in ('GLOBAL-JOURNAL-POLICY', 'NESTED-JOURNAL-DESTINATION', 'PROJECT-JOURNAL-POLICY'):
        assert first.count(sentinel) == 1
    assert 'CACHE-WORKSPACE-MUST-NOT-LOAD' not in first
    (home / '.amplifier/AGENTS.md').write_text('UPDATED-GLOBAL-POLICY')
    second = await render()
    assert 'UPDATED-GLOBAL-POLICY' in second and 'GLOBAL-JOURNAL-POLICY' not in second
    assert 'NESTED-JOURNAL-DESTINATION' not in second
    (workspace / '.amplifier/AGENTS.md').unlink()
    assert 'PROJECT-JOURNAL-POLICY' not in await render()


async def test_existing_instruction_mentions_and_each_session_workspace_are_preserved(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    for path, text in ((first, 'FIRST-WORKSPACE'), (second, 'SECOND-WORKSPACE')):
        (path / '.amplifier').mkdir(parents=True)
        (path / '.amplifier/AGENTS.md').write_text(text)
    root = Bundle(name='work', instruction='Keep the root.\n@.amplifier/AGENTS.md')
    included = include_instruction_files(root)
    assert included.instruction.count('@.amplifier/AGENTS.md') == 1
    assert included.instruction.startswith(root.instruction)
    first_prompt = await factory(included, first)()
    second_prompt = await factory(included, second)()
    assert 'FIRST-WORKSPACE' in first_prompt and 'SECOND-WORKSPACE' not in first_prompt
    assert 'SECOND-WORKSPACE' in second_prompt and 'FIRST-WORKSPACE' not in second_prompt
