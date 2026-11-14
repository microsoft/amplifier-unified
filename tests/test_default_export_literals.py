"""Portable exports distinguish instruction references from Markdown examples."""

import copy
from types import SimpleNamespace

import pytest
import yaml

from amplifier_web.bundles import BundleManager
from amplifier_web.runtime_controls import RuntimeControls


def export_text(tmp_path, text, *, location="instruction", context=()):
    plan = {}
    resources = {"context": list(context)}
    if location == "instruction":
        resources["instruction"] = text
    elif location == "context":
        resources["context"].append({"name": "sample:context/guide.md", "text": text})
    else:
        plan["agents"] = {"worker": {location: text}}
    before = copy.deepcopy((plan, resources))
    result = BundleManager(tmp_path).export_document(
        {}, effective_plan=plan, resources=resources,
    )
    assert (plan, resources) == before
    document = yaml.safe_load(result["content"].split("---", 2)[1])
    if location in {"instruction", "context"}:
        return result["content"]
    return document["agents"]["worker"][location]


@pytest.mark.parametrize("location", [
    "instruction", "context", "instructions", "system_prompt",
])
def test_export_preserves_unresolved_code_examples_at_each_instruction_entry(tmp_path, location):
    text = (
        "Read `@example:context/not-a-resource.md` as an example.\n"
        "```yaml\n"
        'source: "@bundle:agents/agent-name"\n'
        "```\n"
        "Keep the surrounding instructions."
    )
    assert text in export_text(tmp_path, text, location=location)


def test_export_preserves_agent_singular_instruction_example(tmp_path):
    text = "Use `@example:context/not-a-resource.md` as the example."
    result = BundleManager(tmp_path).export_document(
        {}, effective_plan={"agents": {"worker": {"instruction": text}}},
        resources={},
    )
    document = yaml.safe_load(result["content"].split("---", 2)[1])
    assert document["agents"]["worker"]["instruction"] == text


@pytest.mark.parametrize("quoted_reference", [False, True])
def test_export_nested_mode_reference_keeps_annotated_header_example(tmp_path, quoted_reference):
    # Exact example field from modes:context/mode-schema-reference.md, inside
    # its fenced "Fully-annotated mode header", not a resource to resolve.
    example = (
        "```yaml\n"
        "  contributes:\n"
        "    agents:\n"
        "      agent-name:\n"
        '        source: "@bundle:agents/agent-name"\n'
        "```\n"
    )
    reference = "@modes:context/mode-schema-reference.md"
    # The current modes instructions quote this pointer. The resource collector
    # still follows it and supplies the schema as a separate context row.
    pointer = "`" + reference + "`" if quoted_reference else reference
    resources = [
        {"name": "modes:context/modes-instructions.md",
         "text": "Authoring: " + pointer},
        {"name": "modes:context/mode-schema-reference.md",
         "text": "Fully-annotated mode header:\n\n" + example},
    ]
    result = export_text(tmp_path, "@modes:context/modes-instructions.md", context=resources)
    assert example in result
    assert "Fully-annotated mode header:" in result
    if quoted_reference:
        assert "`" + reference + "`" in result
    else:
        assert "@modes:context/" not in result
    assert result.count("<!-- Bundle context: modes:context/mode-schema-reference.md -->") == 1


@pytest.mark.parametrize("literal", [
    "`@sample:context/real.md`",
    "``a ` tick and @sample:context/real.md``",
    "```\n@sample:context/real.md\n```\n",
    "~~~yaml\n@sample:context/real.md\n~~~\n",
    "   ````md\n```\n@sample:context/real.md\n   `````\n",
])
def test_only_prose_occurrences_expand_even_when_literal_resource_exists(tmp_path, literal):
    reference = "@sample:context/real.md"
    result = export_text(
        tmp_path, reference + "\n" + literal + "\n" + reference,
        context=[{"name": "sample:context/real.md", "text": "REAL RESOURCE"}],
    )
    assert literal in result
    assert result.count(reference) == 1
    assert result.count("REAL RESOURCE") == 2


@pytest.mark.parametrize("literal", [
    "```yaml\n@example:missing.md\n",
    "~~~\n@example:missing.md\n",
    "````yaml\n```\n@example:missing.md\n",
    "```\n@example:missing.md\n~~~\n",
])
def test_unclosed_fence_keeps_its_remainder_literal(tmp_path, literal):
    assert literal in export_text(tmp_path, literal)


@pytest.mark.parametrize("text", [
    "@example:missing.md",
    "`@example:missing.md` then @example:missing.md",
    "```yaml\n@example:literal.md\n```\n@example:missing.md",
    "```yaml\n@example:literal.md\n````\n@example:missing.md",
    "An unmatched ` delimiter does not hide @example:missing.md",
    "Mentioning a fence (```) does not hide @example:missing.md",
])
def test_real_missing_resource_still_blocks_export(tmp_path, text):
    with pytest.raises(ValueError, match="Missing portable bundle resource: example:missing.md"):
        export_text(tmp_path, text)


def test_nested_missing_resource_still_blocks_export(tmp_path):
    with pytest.raises(ValueError, match="Missing portable bundle resource: sample:missing.md"):
        export_text(tmp_path, "@sample:context/parent.md", context=[
            {"name": "sample:context/parent.md",
             "text": "`@example:literal.md` and @sample:missing.md"},
        ])


@pytest.mark.parametrize("separator", ["\n\n", "\n \t\n", "\r\n\r\n"])
def test_stray_backtick_cannot_hide_references_in_later_paragraph(tmp_path, separator):
    text = "Don`t forget @sample:context/a.md here." + separator + (
        "Later @sample:context/b.md and `code` end.")
    result = export_text(tmp_path, text, context=[
        {"name": "sample:context/a.md", "text": "FIRST RESOURCE"},
        {"name": "sample:context/b.md", "text": "SECOND RESOURCE"},
    ])
    assert "@sample:" not in result
    assert "`code`" in result
    assert result.count("FIRST RESOURCE") == result.count("SECOND RESOURCE") == 1


def test_context_cycle_keeps_existing_marker_and_literal_self_reference(tmp_path):
    result = export_text(tmp_path, "@sample:context/a.md", context=[
        {"name": "sample:context/a.md",
         "text": "A `@sample:context/a.md` @sample:context/b.md"},
        {"name": "sample:context/b.md", "text": "B @sample:context/a.md"},
    ])
    assert "`@sample:context/a.md`" in result
    assert "[Context already included: sample:context/a.md]" in result
    assert result.count("<!-- Bundle context: sample:context/a.md -->") == 1
    assert result.count("<!-- Bundle context: sample:context/b.md -->") == 1


def test_declarative_resource_references_remain_portable_and_strict(tmp_path):
    manager = BundleManager(tmp_path)
    resources = {"namespaces": [{
        "namespace": "sample", "uri": "git+https://github.com/example/bundle@abc123",
    }]}
    result = manager.export_document(
        {}, effective_plan={"tools": [{"module": "tool-skills",
                                      "config": {"skills": ["@sample:skills"]}}]},
        resources=resources,
    )
    document = yaml.safe_load(result["content"].split("---", 2)[1])
    assert document["tools"][0]["config"]["skills"] == [
        "git+https://github.com/example/bundle@abc123#subdirectory=skills",
    ]
    with pytest.raises(ValueError, match="Unknown resource namespace: missing"):
        manager.export_document(
            {}, effective_plan={"tools": [{"module": "tool-skills",
                                          "config": {"skills": ["@missing:skills"]}}]},
            resources=resources,
        )
    with pytest.raises(ValueError, match="An unresolved namespace reference remains in path"):
        manager.export_document(
            {}, effective_plan={"tools": [{"module": "tool-test",
                                          "config": {"path": "`@sample:missing.md`"}}]},
            resources=resources,
        )


@pytest.mark.asyncio
async def test_literal_examples_survive_foundation_export_roundtrip(tmp_path):
    foundation = pytest.importorskip("amplifier_foundation.registry")
    text = (
        "Example: `@example:context/missing.md`.\n"
        "```yaml\n"
        'source: "@bundle:agents/agent-name"\n'
        "```\n"
    )
    exported = BundleManager(tmp_path).export_document(
        {}, effective_plan={"agents": {"worker": {"instruction": text}}},
        resources={"instruction": text + "\n@sample:context/real.md", "context": [
            {"name": "sample:context/real.md", "text": "REAL RESOURCE"},
        ]},
    )
    target = tmp_path / exported["filename"]
    target.write_text(exported["content"], encoding="utf-8")
    loaded = await foundation.load_bundle(str(target))
    assert text in loaded.instruction
    assert "REAL RESOURCE" in loaded.instruction
    assert "@sample:context/real.md" not in loaded.instruction
    assert loaded.to_mount_plan()["agents"]["worker"]["instruction"] == text


def collected_export(tmp_path, monkeypatch, *, reference="foundation:context/shared/common-agent-base.md"):
    """Actual red shape: registration exists but the resource bundle never loaded."""
    from amplifier_foundation import Bundle, BundleRegistry
    from amplifier_foundation.paths.resolution import ResolvedSource
    from amplifier_foundation.sources import SimpleSourceResolver
    from amplifier_web.host.config import HostConfig
    root = tmp_path / "prepared/foundation"
    (root / "context/shared").mkdir(parents=True)
    (root / "context/shared/common-agent-base.md").write_text("FOUNDATION SHARED CONTEXT\n")
    (root / "bundle.yaml").write_text(
        "bundle: {name: foundation}\nincludes: [{bundle: forbidden-include}]\n"
        "tools: [{module: forbidden-tool}]\n")
    selected = "git+https://example.invalid/foundation@selected"
    registry = BundleRegistry(home=tmp_path / "registry", persist=False, read_persisted=False)
    registry.register({"foundation": selected})
    calls = []
    async def resolve(self, uri):
        calls.append(uri)
        assert uri == selected, "Only the exact registered selected source may resolve"
        return ResolvedSource(active_path=root, source_root=root)
    monkeypatch.setattr(SimpleSourceResolver, "resolve", resolve)
    bundle = Bundle(name="selected-root")
    plan = {"agents": {"context-intelligence:session-navigator": {
        "instruction": "Shared instructions: @" + reference}}}
    bundle.agents = copy.deepcopy(plan["agents"])
    controls = RuntimeControls.__new__(RuntimeControls)
    controls.prepared = SimpleNamespace(bundle=bundle)
    controls.coordinator = SimpleNamespace(
        config=plan, get_capability=lambda key: registry if key == "web.registry" else None)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = HostConfig(tmp_path / "app", workspace, {}, tmp_path / "registry")
    return controls, plan, registry, root, config, calls


@pytest.mark.asyncio
async def test_export_collects_registered_namespace_without_composing_its_bundle(tmp_path, monkeypatch):
    from amplifier_web.host.session import _bind_resource_namespaces
    from amplifier_web.host.components import HostComponents
    controls, plan, registry, root, config, calls = collected_export(tmp_path, monkeypatch)
    before = copy.deepcopy(plan)
    assert registry.get_state("foundation").local_path is None
    with pytest.raises(ValueError, match="Missing portable bundle resource"):
        BundleManager(tmp_path).export_document(
            {}, effective_plan=plan, resources=controls.export_resources())
    await _bind_resource_namespaces(controls.prepared.bundle, registry, config, HostComponents())
    resources = controls.export_resources()
    assert controls.prepared.bundle.source_base_paths == {"foundation": root}
    assert registry.get_state("foundation").local_path is None
    assert calls == ["git+https://example.invalid/foundation@selected"]
    assert controls.prepared.bundle.tools == [] and controls.prepared.bundle.includes == []
    assert {row["name"] for row in resources["context"]} == {
        "foundation:context/shared/common-agent-base.md"}
    exported = BundleManager(tmp_path).export_document({}, effective_plan=plan, resources=resources)
    assert "FOUNDATION SHARED CONTEXT" in exported["content"]
    assert plan == before
    # An unchanged composed binding never resolves again or loads a capability.
    await _bind_resource_namespaces(controls.prepared.bundle, registry, config, HostComponents())
    assert calls == ["git+https://example.invalid/foundation@selected"]


@pytest.mark.parametrize("reference", [
    "unknown:context/shared/common-agent-base.md", "foundation:context/missing.md",
    "foundation:../outside.md", "foundation:context/escape.md",
])
@pytest.mark.asyncio
async def test_export_registered_namespace_keeps_missing_unknown_and_escape_refusals(tmp_path, monkeypatch, reference):
    from amplifier_web.host.session import _bind_resource_namespaces
    from amplifier_web.host.components import HostComponents
    controls, plan, registry, root, config, calls = collected_export(tmp_path, monkeypatch, reference=reference)
    outside = tmp_path / "outside.md"
    outside.write_text("OUTSIDE MUST NOT BE EXPORTED")
    (root / "context/escape.md").symlink_to(outside)
    await _bind_resource_namespaces(controls.prepared.bundle, registry, config, HostComponents())
    resources = controls.export_resources()
    assert resources["context"] == []
    with pytest.raises(ValueError, match="Missing portable bundle resource"):
        BundleManager(tmp_path).export_document({}, effective_plan=plan, resources=resources)


@pytest.mark.asyncio
async def test_export_registered_namespace_transitive_cycle_is_bounded(tmp_path, monkeypatch):
    from amplifier_web.host.session import _bind_resource_namespaces
    from amplifier_web.host.components import HostComponents
    controls, plan, registry, root, config, calls = collected_export(tmp_path, monkeypatch)
    (root / "context/shared/common-agent-base.md").write_text("@foundation:context/second.md")
    (root / "context/second.md").write_text("@foundation:context/shared/common-agent-base.md")
    await _bind_resource_namespaces(controls.prepared.bundle, registry, config, HostComponents())
    resources = controls.export_resources()
    assert len(resources["context"]) == 2
    exported = BundleManager(tmp_path).export_document({}, effective_plan=plan, resources=resources)
    document = yaml.safe_load(exported["content"].split("---", 2)[1])
    assert "Context already included" in document["agents"]["context-intelligence:session-navigator"]["instruction"]


@pytest.mark.asyncio
async def test_resource_binding_honors_selected_source_and_does_not_resolve_unused_registrations(tmp_path, monkeypatch):
    from amplifier_foundation.paths.resolution import ResolvedSource
    from amplifier_foundation.sources import SimpleSourceResolver
    from amplifier_web.host.session import _bind_resource_namespaces
    from amplifier_web.host.components import HostComponents
    controls, plan, registry, root, config, calls = collected_export(tmp_path, monkeypatch)
    registry.register({"unused": "git+https://example.invalid/unused@must-not-resolve"})
    selected = "git+https://example.invalid/foundation@pinned"
    config.settings["sources"] = {"bundles": {"foundation": selected}}
    observed = []
    config._source_policy_observer = SimpleNamespace(
        observe=lambda source, *args, **kwargs: observed.append(source))
    async def resolve(self, uri):
        calls.append(uri)
        assert uri == selected
        return ResolvedSource(active_path=root, source_root=root)
    monkeypatch.setattr(SimpleSourceResolver, "resolve", resolve)
    components = HostComponents()
    components.select("tool-delegate", "git+https://example.invalid/foundation@module-pin")
    await _bind_resource_namespaces(controls.prepared.bundle, registry, config, components)
    assert calls == observed == [selected]
    assert controls.prepared.bundle._host_resource_namespaces["foundation"]["uri"] == selected


@pytest.mark.asyncio
async def test_resource_binding_preserves_existing_namespace_and_refuses_manifest_escape(tmp_path, monkeypatch):
    from amplifier_web.host.session import _bind_resource_namespaces
    from amplifier_web.host.components import HostComponents
    controls, plan, registry, root, config, calls = collected_export(tmp_path, monkeypatch)
    bundle = controls.prepared.bundle
    bundle.source_base_paths["foundation"] = root
    await _bind_resource_namespaces(bundle, registry, config, HostComponents())
    assert calls == []
    bundle.source_base_paths.clear()
    (root / "bundle.yaml").write_text("bundle: {name: foundation, namespace_root: ..}\n")
    with pytest.raises(ValueError, match="escapes its admitted root"):
        await _bind_resource_namespaces(bundle, registry, config, HostComponents())


@pytest.mark.asyncio
async def test_resource_namespace_admission_denial_never_falls_back(tmp_path, monkeypatch):
    from amplifier_foundation.sources import SimpleSourceResolver
    from amplifier_web.host.session import _bind_resource_namespaces
    from amplifier_web.host.components import HostComponents
    controls, plan, registry, root, config, calls = collected_export(tmp_path, monkeypatch)
    async def denied(self, uri):
        calls.append(uri)
        raise ValueError("selected source denied")
    monkeypatch.setattr(SimpleSourceResolver, "resolve", denied)
    with pytest.raises(ValueError, match="selected source denied"):
        await _bind_resource_namespaces(controls.prepared.bundle, registry, config, HostComponents())
    assert calls == ["git+https://example.invalid/foundation@selected"]
    assert controls.prepared.bundle.source_base_paths == {}


@pytest.mark.asyncio
async def test_snapshot_resource_references_do_not_load_current_registry(tmp_path, monkeypatch):
    from amplifier_foundation import Bundle
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.host.config import HostConfig
    from amplifier_web.host.session import compose_configured_bundle
    async def unexpected(*args):
        pytest.fail("A snapshot cannot resolve new resource sources")
    monkeypatch.setattr("amplifier_web.host.session._bind_resource_namespaces", unexpected)
    root = Bundle(name="saved", version=SNAPSHOT_VERSION,
                  agents={"worker": {"instruction": "@foundation:context/shared/common-agent-base.md"}})
    before = copy.deepcopy(root.to_mount_plan())
    result = await compose_configured_bundle(None, root, HostConfig(
        tmp_path / "app", tmp_path, {}, tmp_path / "registry"))
    assert result.agents == before["agents"] and result.source_base_paths == {}


@pytest.mark.asyncio
async def test_resource_binding_follows_punctuated_transitive_namespace(tmp_path, monkeypatch):
    from amplifier_foundation.paths.resolution import ResolvedSource
    from amplifier_foundation.sources import SimpleSourceResolver
    from amplifier_web.host.session import _bind_resource_namespaces
    from amplifier_web.host.components import HostComponents
    controls, plan, registry, root, config, calls = collected_export(
        tmp_path, monkeypatch, reference="foundation:context/shared/common-agent-base.md.")
    second = tmp_path / "prepared/second"
    second.mkdir()
    (second / "bundle.yaml").write_text("bundle: {name: second}\n")
    (second / "child.md").write_text("TRANSITIVE CHILD")
    (root / "context/shared/common-agent-base.md").write_text("@second:child.md")
    registry.register({"second": "git+https://example.invalid/second@selected"})
    async def resolve(self, uri):
        calls.append(uri)
        target = second if "second@" in uri else root
        return ResolvedSource(active_path=target, source_root=target)
    monkeypatch.setattr(SimpleSourceResolver, "resolve", resolve)
    await _bind_resource_namespaces(controls.prepared.bundle, registry, config, HostComponents())
    resources = controls.export_resources()
    assert {row["name"] for row in resources["context"]} == {
        "foundation:context/shared/common-agent-base.md.", "second:child.md"}
    exported = BundleManager(tmp_path).export_document({}, effective_plan=plan, resources=resources)
    assert "TRANSITIVE CHILD" in exported["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["removed-agent", "introduced-agent", "instruction-file"])
async def test_resource_binding_waits_for_effective_runtime_plan_and_instruction_files(
        tmp_path, monkeypatch, change):
    from amplifier_foundation import Bundle
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.host.components import prepare_bundle
    from amplifier_web.host.mentions import include_instruction_files
    from amplifier_web.host.session import apply_runtime_plan, compose_configured_bundle

    controls, plan, registry, root, config, calls = collected_export(tmp_path, monkeypatch)
    bundle = controls.prepared.bundle
    bundle.session = {"orchestrator": {"module": "loop-live"}, "context": {"module": "context-simple"}}
    bundle.providers = [{"module": "provider-fixture"}]
    config.settings.update(bundle={"app": []}, web_bundles={"excluded": list(DEFAULT_BEHAVIORS)})
    if change != "removed-agent":
        bundle.agents = {}
    bundle = await compose_configured_bundle(registry, bundle, config)
    assert not calls, "Initial composition must not resolve soon-to-be-removed references"
    edited = copy.deepcopy(bundle.to_mount_plan())
    edited["agents"] = {} if change != "introduced-agent" else plan["agents"]
    bundle = apply_runtime_plan(bundle, edited, config, config.workspace)
    if change == "instruction-file":
        (config.workspace / "AGENTS.md").write_text("@foundation:context/shared/common-agent-base.md")
    bundle = include_instruction_files(bundle, config_home=tmp_path / "config",
        execution_workspace=config.workspace, workspace=config.workspace)
    expected = copy.deepcopy(bundle.to_mount_plan())

    async def activate(self, **kwargs):
        assert self.to_mount_plan() == expected
        return SimpleNamespace(bundle=self, mount_plan=self.to_mount_plan())
    monkeypatch.setattr(Bundle, "prepare", activate)
    prepared = await prepare_bundle(bundle)
    assert prepared.bundle.to_mount_plan() == expected
    assert len(calls) == (0 if change == "removed-agent" else 1)
    assert ("foundation" in bundle.source_base_paths) is (change != "removed-agent")


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", ["file", "count", "total"])
async def test_context_discovery_bounds_do_not_become_startup_admission_limits(tmp_path, monkeypatch, bound):
    from amplifier_foundation import Bundle
    from amplifier_web.host.components import prepare_bundle

    controls, plan, registry, root, config, calls = collected_export(tmp_path, monkeypatch)
    bundle = controls.prepared.bundle
    bundle.agents = {}
    bundle.context = {}
    count, size = {"file": (1, 1_000_001), "count": (501, 1), "total": (9, 1_000_000)}[bound]
    for index in range(count):
        path = tmp_path / f"context-{index}.md"
        path.write_text("x" * size)
        bundle.context[f"fixture:{index}"] = path
    bundle._host_resource_context = (registry, config)
    before = copy.deepcopy(bundle.to_mount_plan())
    async def activate(self, **kwargs):
        return SimpleNamespace(bundle=self)
    monkeypatch.setattr(Bundle, "prepare", activate)
    prepared = await prepare_bundle(bundle)
    assert prepared.bundle.to_mount_plan() == before
    assert not calls and bundle._host_resource_scan_warnings
    if bound == "count":
        # Ordinary preparation succeeds; portable export still rejects its
        # independent file-count limit rather than silently accepting the plan.
        with pytest.raises(ValueError, match="500 file portable export limit"):
            controls.export_resources()
    else:
        assert controls.export_resources()["warnings"], "Discovery limits remain visible, not silent acceptance"


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", ["unchanged", "repinned", "removed", "explicit-resource",
                                 "ambiguous", "explicit-ambiguous", "equivalent-git"])
async def test_runtime_plan_resource_binding_uses_only_surviving_effective_module_pins(tmp_path, monkeypatch, edit):
    from amplifier_foundation import Bundle
    from amplifier_foundation.paths.resolution import ResolvedSource
    from amplifier_foundation.sources import SimpleSourceResolver
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.host.components import HostComponents, prepare_bundle
    from amplifier_web.host.session import apply_runtime_plan, compose_configured_bundle

    controls, plan, registry, root, config, calls = collected_export(tmp_path, monkeypatch)
    bundle = controls.prepared.bundle
    repository = "git+https://example.invalid/foundation"
    module_pin = repository + "@component-pin"
    bundle.tools = [{"module": "tool-delegate", "source": module_pin + "#subdirectory=modules/tool-delegate"}]
    bundle.session = {"orchestrator": {"module": "loop-live"}, "context": {"module": "context-simple"}}
    bundle.providers = [{"module": "provider-fixture"}]
    config.settings.update(bundle={"app": []}, web_bundles={"excluded": list(DEFAULT_BEHAVIORS)})
    bundle = await compose_configured_bundle(registry, bundle, config)
    # Reproduce an ordinary selected module pin without an already-composed
    # Foundation resource root, then lose the selector at apply_runtime_plan.
    components = HostComponents()
    components.select_bundle(bundle, {})
    bundle._host_components = components
    assert components.source("foundation", registry.find("foundation")) == module_pin
    edited = copy.deepcopy(bundle.to_mount_plan())
    expected = module_pin
    if edit == "repinned":
        expected = repository + "@edited-pin"
        edited["tools"][0]["source"] = expected + "#subdirectory=modules/tool-delegate"
    elif edit == "removed":
        edited["tools"] = []
        expected = registry.find("foundation")
    elif edit in {"explicit-resource", "explicit-ambiguous"}:
        expected = repository + "@resource-pin"
        config.settings["sources"] = {"bundles": {"foundation": expected}}
    if edit in {"ambiguous", "explicit-ambiguous"}:
        edited["tools"].append({"module": "tool-other",
            "source": repository + "@different-pin#subdirectory=modules/tool-other"})
    elif edit == "equivalent-git":
        edited["tools"].append({"module": "tool-other",
            "source": repository + ".git@component-pin#subdirectory=modules/tool-other"})
        # Preserve the canonical selector's equivalent chosen URL spelling.
        expected = repository + ".git@component-pin"
    bundle = apply_runtime_plan(bundle, edited, config, config.workspace)
    before = copy.deepcopy(bundle.to_mount_plan())
    async def resolve(self, uri):
        calls.append(uri)
        return ResolvedSource(active_path=root, source_root=root)
    monkeypatch.setattr(SimpleSourceResolver, "resolve", resolve)
    async def activate(self, **kwargs):
        assert self.to_mount_plan() == before
        return SimpleNamespace(bundle=self)
    monkeypatch.setattr(Bundle, "prepare", activate)
    if edit == "ambiguous":
        with pytest.raises(ValueError, match="ambiguous effective repository pins"):
            await prepare_bundle(bundle)
        assert not calls and bundle.source_base_paths == {}
        assert bundle.to_mount_plan() == before
        return
    await prepare_bundle(bundle)
    assert calls == [expected]
    assert bundle._host_resource_namespaces["foundation"]["uri"] == expected
    assert bundle.to_mount_plan() == before, "Resource pin selection must not rewrite effective activation rows"