"""Default discovery, real Foundation composition, and portable shell resources."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from amplifier_web.builtin_behaviors import (
    SHELL_BEHAVIOR_URI, app_behaviors, resolve_builtin_behavior, resource_root,
)
from amplifier_web.bundles import BundleManager
from amplifier_web.host.config import load_config


def test_default_is_visible_without_writing_settings(tmp_path):
    config = load_config(tmp_path, home=tmp_path / "app")
    assert config.app_bundles == [SHELL_BEHAVIOR_URI]
    row, = BundleManager.entries(config.settings)
    assert row["name"] == "Unified shell" and row["enabled"]
    assert not config.settings_file.exists()
    assert app_behaviors({"bundle": {"app": []}}) == []
    assert app_behaviors({"bundle": {"app": ["chosen"]}}) == ["chosen"]
    assert app_behaviors({"web_bundles": {"excluded": [SHELL_BEHAVIOR_URI]}}) == []
    assert resolve_builtin_behavior("git+https://example.org/custom@release") == "git+https://example.org/custom@release"


async def test_default_disable_remove_and_explicit_readd_survive_reload(tmp_path):
    manager = BundleManager(tmp_path / "app")
    args = {"workspace": str(tmp_path)}
    row, = (await manager.perform("bundles.list", args))["bundles"]
    await manager.perform("bundles.toggle", {**args, "id": row["id"], "enabled": False})
    assert load_config(tmp_path).app_bundles == []
    row, = (await BundleManager(tmp_path).perform("bundles.list", args))["bundles"]
    assert row["enabled"] is False
    await manager.perform("bundles.remove", {**args, "id": row["id"]})
    assert (await manager.perform("bundles.list", args))["bundles"] == []
    await manager.perform("bundles.add", {**args, "uri": SHELL_BEHAVIOR_URI, "name": "Unified shell"})
    assert load_config(tmp_path).app_bundles == [SHELL_BEHAVIOR_URI]


async def load_behavior(tmp_path):
    from amplifier_foundation.registry import BundleRegistry
    # Keep tests offline; exercise Foundation's actual nested namespace and
    # include composition using the upstream minimal behavior's contract.
    upstream = tmp_path / "skills"
    (upstream / "behaviors").mkdir(parents=True)
    (upstream / "context").mkdir()
    (upstream / "bundle.yaml").write_text("bundle: {name: skills}\n")
    (upstream / "context/instructions.md").write_text("Use load_skill.\n")
    behavior = upstream / "behaviors/skills-tool.yaml"
    behavior.write_text(yaml.safe_dump({
        "bundle": {"name": "skills-tool-behavior"},
        "tools": [{"module": "tool-skills", "config": {"visibility": {"enabled": True}}}],
        "context": {"include": ["skills:context/instructions.md"]},
    }))
    manifest = yaml.safe_load((resource_root() / "behaviors/unified-shell.yaml").read_text())
    dependency = manifest["includes"][0]["bundle"]
    registry = BundleRegistry(home=tmp_path / "registry", strict=True,
        include_source_resolver=lambda source: upstream.as_uri() + "#subdirectory=behaviors/skills-tool.yaml" if source == dependency else None)
    loaded = await registry.load(resolve_builtin_behavior(SHELL_BEHAVIOR_URI))
    loaded.resolve_pending_context()
    return loaded


async def test_composition_preserves_host_and_resolves_skill_namespace(tmp_path):
    from amplifier_foundation.bundle import Bundle
    behavior = await load_behavior(tmp_path)
    root = Bundle(name="test-host", instruction="Keep the host instruction.",
        session={"orchestrator": {"module": "host-loop"}},
        providers=[{"module": "provider-test"}],
        tools=[{"module": "tool-skills", "config": {"skills": ["existing"], "visibility": {"enabled": False}}}])
    composed = root.compose(behavior)
    assert composed.instruction == root.instruction
    assert composed.session == root.session and composed.providers == root.providers
    tool, = composed.tools
    assert tool["module"] == "tool-skills"
    assert tool["config"]["skills"] == ["existing", ".amplifier/skills", "~/.amplifier/skills", "@unified:skills"]
    assert tool["config"]["visibility"]["enabled"] is True
    assert Path(composed.source_base_paths["unified"]).resolve() == resource_root().resolve()
    assert (Path(composed.source_base_paths["unified"]) / "skills/amplifier-shell/SKILL.md").is_file()
    assert any(Path(p).name == "shell-skills.md" for p in composed.context.values())


async def test_builtin_resources_export_without_local_paths(tmp_path):
    from amplifier_web.runtime_controls import RuntimeControls
    behavior = await load_behavior(tmp_path)
    coordinator = SimpleNamespace(config={}, get_capability=lambda name: None)
    controls = RuntimeControls.__new__(RuntimeControls)
    controls.coordinator = coordinator
    controls.prepared = SimpleNamespace(bundle=behavior)
    resources = controls.export_resources()
    result = BundleManager(tmp_path).export_document({}, effective_plan={"tools": behavior.tools}, resources=resources)
    assert "git+https://github.com/bkrabach/amplifier-unified@main#subdirectory=skills" in result["content"]
    assert str(resource_root()) not in result["content"]
    assert "amplifier-shell" in result["content"]
    assert any("main branch" in warning for warning in result["warnings"])


async def test_namespace_anchor_is_not_a_selectable_session_root(tmp_path):
    import json
    config = load_config(tmp_path, home=tmp_path / "app")
    config.registry_home.mkdir(parents=True)
    (config.registry_home / "registry.json").write_text(json.dumps({"bundles": {
        "unified": {"is_root": True, "uri": resource_root().as_uri()},
        "custom": {"is_root": True, "uri": "git+https://example.org/custom"},
    }}))
    result = await BundleManager(config.home).perform("bundles.list", {"workspace": str(tmp_path)})
    names = {row["name"] for row in result["registeredBundles"]}
    assert "unified" not in names and "custom" not in names
    await BundleManager(config.home).perform('bundles.add', {'workspace': str(tmp_path),
        'name': 'custom', 'uri': 'git+https://example.org/custom', 'role': 'standalone'})
    result = await BundleManager(config.home).perform('bundles.list', {'workspace': str(tmp_path)})
    assert 'custom' in {row['name'] for row in result['registeredBundles']}


@pytest.mark.parametrize("origin", ["ui", "agent"])
async def test_default_toggle_uses_shared_action_path(tmp_path, origin):
    import asyncio
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    app = AppService(tmp_path / "app", workspace=tmp_path)
    app.management = Management(app)
    try:
        await app.dispatch("session.create", {})
        row, = BundleManager.entries({})
        args = {"id": row["id"], "enabled": False}
        if origin == "agent":
            result = await app.app_bridge("dispatch", {"action": "bundles.toggle", "args": args, "id": "toggle-shell"}, app.state["selectedSessionId"])
        else:
            result = await app.dispatch("bundles.toggle", args, command_id="toggle-shell")
        assert result["accepted"]
        await asyncio.gather(*tuple(app.tasks))
        assert load_config(tmp_path).app_bundles == []
        assert app.state["bundles"][0]["enabled"] is False
    finally:
        await app.close()
