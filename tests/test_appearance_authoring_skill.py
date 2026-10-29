"""Appearance guidance through the real loader, including a non-editable wheel.

Offline: no source registration, model calls, app writes or live-client changes.
The wheel probe uses installed Core/Foundation/tools from the test interpreter;
it qualifies packaged resource loading, not full host startup or browser design.
"""
from __future__ import annotations

import asyncio
import copy
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

async def _loader_probe(root, workspace, *, deferred=False, omitted=False):
    from amplifier_core import AmplifierSession
    from amplifier_foundation.bundle import Bundle
    from amplifier_foundation.mentions import BaseMentionResolver
    from amplifier_module_tool_filesystem import ReadTool
    from amplifier_module_tool_skills import mount

    workspace.mkdir(parents=True, exist_ok=True)
    empty = workspace / "empty-skills"
    empty.mkdir(exist_ok=True)
    # Obtain the real coordinator/hooks without initializing an inference loop.
    session = AmplifierSession({"session": {"orchestrator": {"module": "unused"},
                                           "context": {"module": "unused"}}})
    coordinator = session.coordinator
    coordinator.register_capability("session.working_dir", str(workspace))
    resolver = BaseMentionResolver(
        base_path=workspace, bundles={"unified": Bundle(name="unified", base_path=root)})
    if not deferred:
        coordinator.register_capability("mention_resolver", resolver)
    config = {"skills": [str(empty), *([] if omitted else ["@unified:skills"])],
              "visibility": {"placement": "request"}}
    original = copy.deepcopy(config)
    cleanup = await mount(coordinator, config)
    try:
        tool = coordinator.get("tools")["load_skill"]
        if deferred:
            assert "appearance-authoring" not in tool.skills
            coordinator.register_capability("mention_resolver", resolver)
        catalog = await coordinator.hooks.emit("provider:request", {"messages": []})
        assert config == original
        if omitted:
            missing = await tool.execute({"skill_name": "appearance-authoring"})
            assert not missing.success and "not found" in missing.error["message"]
            return
        assert "appearance-authoring" in catalog.context_injection
        assert "# Author an appearance" not in catalog.context_injection
        assert not tool.loaded_skills, "Catalog visibility must not load the body"
        info = await tool.execute({"info": "appearance-authoring"})
        assert info.success and "content" not in info.output
        assert not tool.loaded_skills
        listing = await tool.execute({"search": "appearance-authoring"})
        assert listing.success and "# Author an appearance" not in json.dumps(listing.output)
        loaded = await tool.execute({"skill_name": "appearance-authoring"})
        assert loaded.success and "# Author an appearance" in loaded.output["content"]
        assert loaded.output.get("context") != "fork", "Authoring must stay in the user's review loop"
        directory = Path(loaded.output["skill_directory"]).resolve()
        assert directory == (root / "skills/appearance-authoring").resolve()
        reader = ReadTool({"allowed_read_paths": [str(root)], "working_dir": str(workspace)}, coordinator)
        for relative in ("examples.md", "quiet-study.json", "../../docs/shell/themes.md",
                         "../../docs/shell/appearances.md", "../../docs/shell/appearance-contract.md"):
            path = (directory / relative).resolve(strict=True)
            result = await reader.execute({"file_path": str(path)})
            assert result.success, result.error
            assert result.output["total_lines"] == result.output["lines_read"]
            expected = path.read_text().splitlines()
            assert result.output["content"] == "\n".join(
                f"{number:6d}\t{line}" for number, line in enumerate(expected, 1))
    finally:
        await cleanup()


# The installed probe needs runtime dependencies only, not pytest/PyYAML.
if __name__ == "__main__":
    assert sys.argv[1] == "--installed-probe" and sys.flags.isolated
    installed = Path(sys.argv[2]).resolve(strict=True)
    sys.path.insert(0, str(installed))
    import amplifier_web
    from amplifier_web.builtin_behaviors import resource_root

    assert Path(amplifier_web.__file__).resolve().is_relative_to(installed)
    distribution = importlib.metadata.distribution("amplifier-unified")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    assert not direct.get("dir_info", {}).get("editable")
    assert resource_root().resolve() == installed / "amplifier_web/bundle_data"
    for entry in ("SKILL.md", "examples.md", "quiet-study.json"):
        assert "amplifier_web/bundle_data/skills/appearance-authoring/" + entry in {
            str(path) for path in distribution.files}
    asyncio.run(_loader_probe(resource_root(), Path(sys.argv[3])))
    print("APPEARANCE_SKILL_INSTALLED_OK")
    raise SystemExit(0)


import pytest
import yaml


def test_palette_example_compiles_and_validates():
    from amplifier_web.builtin_behaviors import resource_root
    from amplifier_web.themes import DEFINITION, compile_definition
    from amplifier_web.service import validate_theme
    from jsonschema import validate

    sample = json.loads((resource_root() / "skills/appearance-authoring/quiet-study.json").read_text())
    validate(sample["definition"], DEFINITION)
    css = compile_definition(SimpleNamespace(default_theme=lambda: "#amp-one{color:var(--a-ink)}"),
                             sample["definition"])
    validate_theme(css)
    assert 'data-theme-scheme="light"' in css and 'data-theme-scheme="dark"' in css


@pytest.mark.parametrize("deferred,omitted", [(False, False), (True, False), (False, True)])
async def test_real_loader_progressive_discovery_and_companions(tmp_path, monkeypatch, deferred, omitted):
    from amplifier_web.builtin_behaviors import resource_root

    monkeypatch.chdir(tmp_path)
    await _loader_probe(resource_root(), tmp_path / "unrelated-workspace", deferred=deferred, omitted=omitted)


async def test_app_shell_discovery_preserves_selected_loader_policy(tmp_path, monkeypatch):
    from amplifier_foundation.bundle import Bundle
    from amplifier_foundation.registry import BundleRegistry
    from amplifier_web.builtin_behaviors import (
        DEFAULT_BEHAVIORS, SHELL_BEHAVIOR_URI, resource_root, resolve_builtin_behavior,
    )
    from amplifier_web.host.config import HostConfig
    from amplifier_web.host.session import compose_configured_bundle

    # Resolve the actual shipped behavior with a finite offline upstream include.
    upstream = tmp_path / "upstream"
    (upstream / "behaviors").mkdir(parents=True)
    (upstream / "bundle.yaml").write_text("bundle: {name: skills}\n")
    (upstream / "behaviors/skills-tool.yaml").write_text(yaml.safe_dump({
        "bundle": {"name": "skills-tool-behavior"},
        "tools": [{"module": "tool-skills", "config": {
            "visibility": {"enabled": True, "max_tokens": 9000}}}],
    }))
    manifest = yaml.safe_load((resource_root() / "behaviors/unified-shell.yaml").read_text())
    dependency = manifest["includes"][0]["bundle"]
    registry = BundleRegistry(home=tmp_path / "registry", strict=True, include_source_resolver=(
        lambda uri: upstream.as_uri() + "#subdirectory=behaviors/skills-tool.yaml" if uri == dependency else None))
    behavior = await registry.load(resolve_builtin_behavior(SHELL_BEHAVIOR_URI))
    behavior.resolve_pending_context()
    selected = {"module": "tool-skills", "instance_id": "chosen-loader", "source": "chosen-source",
                "config": {"skills": ["chosen-skills"], "visibility": {"enabled": False, "max_tokens": 1200}}}
    root = Bundle(name="chosen-root", providers=[{"module": "provider-chosen", "config": {"model": "chosen"}}],
                  tools=[selected])
    before = copy.deepcopy(root.to_mount_plan())
    settings = {"bundle": {"app": [SHELL_BEHAVIOR_URI]},
                "web_bundles": {"excluded": [uri for uri in DEFAULT_BEHAVIORS if uri != SHELL_BEHAVIOR_URI]}}
    config = HostConfig(tmp_path / "app", tmp_path, settings, tmp_path / "registry")
    calls = []

    async def offline_load(_registry, _config, reference):
        calls.append(reference)
        return copy.deepcopy(behavior), reference

    monkeypatch.setattr("amplifier_web.host.session.load_configured_bundle", offline_load)
    result = await compose_configured_bundle(registry, root, config)
    tool, = [row for row in result.tools if row["module"] == "tool-skills"]
    assert tool == {**selected, "config": {**selected["config"], "skills": [
        "chosen-skills", ".amplifier/skills", "~/.amplifier/skills", "@unified:skills"]}}
    assert result.name == root.name and result.providers == root.providers
    assert root.to_mount_plan() == before
    assert calls == [resolve_builtin_behavior(SHELL_BEHAVIOR_URI)]
    assert result.source_base_paths["unified"] == behavior.source_base_paths["unified"]

    # Scoped settings may deliberately replace additive discovery; no forced skill.
    config.settings["overrides"] = {"tool-skills": {"config": {"skills": ["only-chosen"]}}}
    result = await compose_configured_bundle(registry, root, config)
    assert next(row for row in result.tools if row["module"] == "tool-skills")["config"]["skills"] == ["only-chosen"]
    calls.clear()
    config.settings = {"bundle": {"app": []}, "web_bundles": {"excluded": list(DEFAULT_BEHAVIORS)}}
    result = await compose_configured_bundle(registry, root, config)
    assert not calls and result.tools == root.tools


def test_wheel_installed_loader_outside_checkout(tmp_path):
    from hatchling.builders.wheel import WheelBuilder

    checkout = Path(__file__).resolve().parents[1]
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel = Path(next(WheelBuilder(str(checkout)).build(directory=str(wheels))))
    target = tmp_path / "installed"
    uv = shutil.which("uv")
    assert uv, "Wheel qualification requires uv (no packages are downloaded)"
    subprocess.run([uv, "--offline", "pip", "install", "--python", sys.executable,
                    "--target", str(target), "--no-index", "--no-deps", str(wheel)],
                   check=True, capture_output=True, text=True, timeout=60)
    # Do not forward credentials or include them in a subprocess failure report.
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "SYSTEMROOT") if key in os.environ}
    environment.update(HOME=str(tmp_path / "home"), AMPLIFIER_HOME=str(tmp_path / "home/.amplifier"))
    probe_python = os.environ.get("UNIFIED_APPEARANCE_PROBE_PYTHON", sys.executable)
    result = subprocess.run([probe_python, "-I", "-B", str(Path(__file__).resolve()),
                             "--installed-probe", str(target), str(tmp_path / "unrelated")],
                            cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "APPEARANCE_SKILL_INSTALLED_OK" in result.stdout
