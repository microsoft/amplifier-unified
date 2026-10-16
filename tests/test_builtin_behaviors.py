"""Default discovery, real Foundation composition, and portable shell resources."""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from configuration_admin import authorized_admin

from amplifier_web.builtin_behaviors import (
    APPENDED_DEFAULTS,
    COMPUTER_USE_BEHAVIOR_URI,
    CONTEXT_INTELLIGENCE_BEHAVIOR_URI,
    DEFAULT_BEHAVIORS,
    IMAGEGEN_BEHAVIOR_URI,
    ROUTING_BEHAVIOR_URI,
    SHELL_BEHAVIOR_URI,
    app_behaviors,
    resolve_builtin_behavior,
    resource_root,
)
from amplifier_web.bundles import BundleManager
from amplifier_web.host.config import load_config


EXPECTED_DEFAULTS = (
    SHELL_BEHAVIOR_URI,
    IMAGEGEN_BEHAVIOR_URI,
    COMPUTER_USE_BEHAVIOR_URI,
    CONTEXT_INTELLIGENCE_BEHAVIOR_URI,
    ROUTING_BEHAVIOR_URI,
)
EXPECTED_APPENDED_DEFAULTS = EXPECTED_DEFAULTS[1:]


def test_default_is_visible_without_writing_settings(tmp_path):
    assert tuple(DEFAULT_BEHAVIORS) == EXPECTED_DEFAULTS
    assert APPENDED_DEFAULTS == EXPECTED_APPENDED_DEFAULTS
    config = load_config(tmp_path, home=tmp_path / "app")
    assert config.app_bundles == list(EXPECTED_DEFAULTS)
    rows = BundleManager.entries(config.settings)
    assert [row["uri"] for row in rows] == list(EXPECTED_DEFAULTS)
    assert [row["name"] for row in rows] == [
        "Unified shell", "Image generation", "Computer use",
        "Context intelligence", "Model routing",
    ]
    assert all(row["enabled"] for row in rows)
    assert not config.settings_file.exists()
    assert resolve_builtin_behavior("git+https://example.org/custom@release") == "git+https://example.org/custom@release"


@pytest.mark.parametrize("explicit", [[], ["chosen"], [SHELL_BEHAVIOR_URI, "chosen"]])
def test_existing_lists_append_four_defaults_but_not_shell(explicit):
    settings = {"bundle": {"app": explicit}}
    before = copy.deepcopy(settings)
    assert app_behaviors(settings) == [*explicit, *EXPECTED_APPENDED_DEFAULTS]
    assert settings == before


@pytest.mark.parametrize("uri", EXPECTED_DEFAULTS)
@pytest.mark.parametrize("disabled_by", ["excluded", "entry"])
@pytest.mark.parametrize("saved_list", [False, True])
@pytest.mark.parametrize("disabled_suffix, readded_suffix", [
    ("@main", ".git@0123456789abcdef0123456789abcdef01234567"),
    (".git@0123456789abcdef0123456789abcdef01234567", "@main"),
])
def test_opt_out_and_explicit_readd_preserve_default_identity(
    uri, disabled_by, saved_list, disabled_suffix, readded_suffix,
):
    disabled_uri = uri.replace("@main#", f"{disabled_suffix}#")
    readded_uri = uri.replace("@main#", f"{readded_suffix}#")
    metadata = (
        {"excluded": [disabled_uri]}
        if disabled_by == "excluded"
        else {"entries": [{"uri": disabled_uri, "enabled": False}]}
    )
    settings = {"web_bundles": metadata}
    if saved_list:
        settings["bundle"] = {"app": ["chosen"]}
    before = copy.deepcopy(settings)
    defaults = EXPECTED_APPENDED_DEFAULTS if saved_list else EXPECTED_DEFAULTS
    prefix = ["chosen"] if saved_list else []
    assert app_behaviors(settings) == [*prefix, *(other for other in defaults if other != uri)]
    assert settings == before
    settings["bundle"] = {"app": ["chosen", readded_uri]}
    before = copy.deepcopy(settings)
    assert app_behaviors(settings) == [
        "chosen", readded_uri,
        *(other for other in EXPECTED_APPENDED_DEFAULTS if other != uri),
    ]
    assert settings == before


@pytest.mark.parametrize("uri", EXPECTED_APPENDED_DEFAULTS)
def test_existing_behavior_list_composes_defaults_once_without_mutating_settings(uri):
    settings = {"bundle": {"app": [SHELL_BEHAVIOR_URI, "chosen"]},
                "providers": [{"module": "provider-test", "config": {"image_generation": {"enabled": False}}}]}
    before = copy.deepcopy(settings)
    assert app_behaviors(settings) == [SHELL_BEHAVIOR_URI, "chosen", *EXPECTED_APPENDED_DEFAULTS]
    assert settings == before
    settings["bundle"]["app"].insert(0, uri)
    before = copy.deepcopy(settings)
    assert app_behaviors(settings) == [
        uri, SHELL_BEHAVIOR_URI, "chosen",
        *(other for other in EXPECTED_APPENDED_DEFAULTS if other != uri),
    ]
    assert settings == before


@pytest.mark.parametrize("uri", EXPECTED_DEFAULTS)
@pytest.mark.parametrize("source_suffix", [
    "@main", "@v1.2.3", ".git",
    ".git@0123456789abcdef0123456789abcdef01234567",
])
def test_default_dedup_keeps_first_explicit_source_and_order(uri, source_suffix):
    selected = uri.replace("@main#", f"{source_suffix}#")
    duplicate = uri.replace("@main#", ".git@another-revision#")
    settings = {"bundle": {"app": ["before", selected, "between", uri, duplicate, selected, "after"]}}
    before = copy.deepcopy(settings)
    assert app_behaviors(settings) == [
        "before", selected, "between", "after",
        *(other for other in EXPECTED_APPENDED_DEFAULTS if other != uri),
    ]
    assert settings == before


def test_all_explicit_pinned_defaults_keep_chosen_order():
    selected = [
        uri.replace("@main#", ".git@0123456789abcdef0123456789abcdef01234567#")
        for uri in reversed(EXPECTED_DEFAULTS)
    ]
    settings = {"bundle": {"app": [*selected, *EXPECTED_DEFAULTS]}}
    before = copy.deepcopy(settings)
    assert app_behaviors(settings) == selected
    assert settings == before


def test_unrelated_explicit_sources_are_not_normalized_or_deduplicated():
    unrelated = [
        "git+https://example.org/custom@release",
        "git+https://example.org/custom.git@other-release",
        IMAGEGEN_BEHAVIOR_URI.replace("github.com", "example.org"),
        IMAGEGEN_BEHAVIOR_URI.replace("microsoft/", "another-owner/"),
        IMAGEGEN_BEHAVIOR_URI.replace("behaviors/imagegen.yaml", "behaviors/custom.yaml"),
    ]
    explicit = [*unrelated, *unrelated]
    settings = {"bundle": {"app": explicit}}
    before = copy.deepcopy(settings)
    assert app_behaviors(settings) == [*explicit, *EXPECTED_APPENDED_DEFAULTS]
    assert settings == before


@pytest.mark.parametrize("uri", EXPECTED_DEFAULTS)
async def test_default_disable_remove_and_explicit_readd_survive_reload(tmp_path, uri):
    others = [other for other in EXPECTED_DEFAULTS if other != uri]
    manager = BundleManager(tmp_path / "app")
    args = {"workspace": str(tmp_path)}
    row = next(row for row in (await manager.perform("bundles.list", args))["bundles"] if row["uri"] == uri)
    await manager.perform("bundles.toggle", {**args, "id": row["id"], "enabled": False})
    assert load_config(tmp_path).app_bundles == others
    row = next(row for row in (await BundleManager(tmp_path).perform("bundles.list", args))["bundles"] if row["uri"] == uri)
    assert row["enabled"] is False
    await manager.perform("bundles.remove", {**args, "id": row["id"]})
    assert [row["uri"] for row in (await manager.perform("bundles.list", args))["bundles"]] == others
    await manager.perform("bundles.add", {**args, "uri": uri, "name": "Restored capability"})
    assert load_config(tmp_path).app_bundles == [*others, uri]


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
    assert (Path(composed.source_base_paths["unified"]) / "skills/coordinate-work/SKILL.md").is_file()
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
    assert "git+https://github.com/microsoft/amplifier-unified@main#subdirectory=skills" in result["content"]
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
@pytest.mark.parametrize("uri", EXPECTED_DEFAULTS)
async def test_default_toggle_uses_shared_action_path(tmp_path, origin, uri):
    import asyncio
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    app = AppService(tmp_path / "app", workspace=tmp_path)
    app.management = Management(app)
    try:
        await app.dispatch("session.create", {})
        row = next(row for row in BundleManager.entries({}) if row["uri"] == uri)
        args = {"id": row["id"], "enabled": False}
        if origin == "agent":
            admin = await authorized_admin(app,app.state['selectedSessionId'],effects=['bundles'],scopes=['global'])
            result = await admin("dispatch", {"action": "bundles.toggle", "args": args, "id": "toggle-default"})
        else:
            result = await app.dispatch("bundles.toggle", args, command_id="toggle-default")
        assert result["accepted"]
        await asyncio.gather(*tuple(app.tasks))
        assert load_config(tmp_path).app_bundles == [other for other in EXPECTED_DEFAULTS if other != uri]
        assert next(row for row in app.state["bundles"] if row["uri"] == uri)["enabled"] is False
    finally:
        await app.close()
