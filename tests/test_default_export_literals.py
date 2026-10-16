"""Portable exports distinguish instruction references from Markdown examples."""

import copy

import pytest
import yaml

from amplifier_web.bundles import BundleManager


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