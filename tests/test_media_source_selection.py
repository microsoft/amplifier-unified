"""Finite shipped source selection, distinct from synthetic image recipes.

These static/negative checks do not qualify the resolved installed image graph.
The integration owner also checks production prepare_manager/select_root and the
physical read/count/send/ACK path with this exact catalog in that graph.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from amplifier_web.host import media_assembly as assembly

MANIFEST = Path(assembly.__file__).with_name("media_sources.json")
# Authored semantic owners, not learned from the loaded callback graph. The store
# is included for cold/fork input semantics, not claimed as a mounted callback.
TRANSITION_OWNERS = {
    "amplifier_web/bundle_selection.py": "133f94e83d71fc71bce4bbbabfcc645dda5be801d6cc6061c95a26376a1327db",
    "amplifier_web/context_continuity.py": "caf354f9f6a37ae8f61e4379ef3f2b3cc77543ca7470019295b22c2a201917a9",
    "amplifier_web/host/session.py": "25613d9102f684b36f47ae11a9f57a39ddd18a3b4807cf2ee4a90ff49715604d",
    "amplifier_web/runtime_worker.py": "c258cf016022e5ce70ab4ca4096b207cb04dc0a477a24cbf896f68001ca72ad7",
    "amplifier_web/runtime_controls.py": "d37509a526f7c10df31c72a42041cf425cf972740ccec6ffbb56a16bb2d8d9ab",
    "amplifier_web/session_store.py": "358345b44538676c762b02f3fc40b0b01a5fe47c0af2725faee248947418c8c8",
    "amplifier_module_context_managed/boundary.py": "3600e81c3b26e458cdbd65037b7f38838dac61eb7cc5a50d481c809cbfef1136",
    "amplifier_module_context_managed/checkpoint.py": "3de7548aede6f5105c1d1af21190ea9428775c13e1fab09299345f3dc4457e43",
    "amplifier_module_context_managed/recovery.py": "4d311e7de9e1428cd247ba05910c47d496b554c66b47cff352b9cbcc9435d99b",
    "amplifier_module_context_managed/ordinary_transition.py": "9e48247e43fc371ecece198eaabee45a5ffb716ad9f5db36deaf7c00ff889b23",
    "amplifier_module_context_managed/transition.py": "2f31cbd987adc8ac0584863dcede59c1aa8c98902f98b8bf0511f435ba5e8683",
    "amplifier_module_context_managed/settled_maintenance.py": "86d876f143c0a89450b51c4d0c7dc656725667872857c99cd6e6aa71e330e5ef",
}


def catalog_digest(sources):
    return hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def assert_transition_closure(manifest):
    assert all(manifest["sources"].get(name) == digest for name, digest in TRANSITION_OWNERS.items())
    # The accepted decision namespace adds three explicit semantic owners while
    # this selection retains all transition owners and the combined session.
    assert len(manifest["sources"]) == 313
    assert catalog_digest(manifest["sources"]) == "61f0e3467d3478e04c398350bee26b70c5edc65c328ddfc9bbae9678864b7120"


def work_plan():
    return {
        "session": {"orchestrator": "loop-live", "context": {"module": "context-managed"}},
        "providers": [{"module": "provider-openai", "config": {"default_model": "gpt-6.1-sol"}}],
        "tools": [{"module": name, "config": {}} for name in assembly.WORK_TOOLS],
        "hooks": [{"module": name} for name in assembly.WORK_HOOKS],
        "agents": {name: {} for name in assembly.WORK_AGENTS},
    }


def test_shipped_transition_closure_is_explicit_and_host_bytes_match():
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["recipe"] == assembly.RECIPE == "unified-root-read-file-v1"
    assert manifest["status"] == "reviewed"
    assert manifest["accepted_sources"]["amplifier-module-context-managed"] == {
        "repository": "https://github.com/microsoft/amplifier-bundle-context-managed",
        "commit": "a4464af2027f4e03fe7fc388d8d8e3e8ff436da2",
        "tree": "30c4c0a1bf92f872e646bcb4e70cda27c474f0e8",
    }
    assert_transition_closure(manifest)
    root = Path(__file__).resolve().parents[1]
    for name, expected in manifest["sources"].items():
        if name.startswith("amplifier_web/"):
            assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
    assert assembly.MEDIA_MODEL == "gpt-6.1-sol"
    assert assembly.supported_plan(work_plan())


@pytest.mark.parametrize("missing", sorted(TRANSITION_OWNERS))
def test_finite_catalog_closure_rejects_missing_semantic_owner(missing):
    manifest = json.loads(MANIFEST.read_text())
    manifest["sources"].pop(missing)
    # Some owners are deliberately beyond the selector's minimum key set.
    # This static catalog assertion, not select_root alone, covers that omission.
    with pytest.raises(AssertionError):
        assert_transition_closure(manifest)


@pytest.mark.parametrize("change", ["pending", "missing-required"])
def test_production_selector_refuses_unreviewed_or_missing_required_before_resolution(monkeypatch, change):
    manifest = json.loads(MANIFEST.read_text())
    if change == "pending":
        manifest["status"] = "pending"
    else:
        manifest["sources"].pop("amplifier_web/host/session.py")
    read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k:
        json.dumps(manifest) if self == MANIFEST else read(self, *a, **k))
    def unexpected(*args):
        pytest.fail("Invalid catalog reached package resolution")
    monkeypatch.setattr(assembly.importlib.util, "find_spec", unexpected)
    assert assembly.select_root(work_plan()) is None


def test_selected_recipe_refuses_stale_source_and_unselected_callback():
    filename = str(Path(assembly.__file__).resolve())
    data = Path(filename).read_bytes()
    with pytest.raises(ValueError, match="source changed"):
        assembly.ReviewedRootRecipe(work_plan(), {filename: "0" * 64})
    recipe = assembly.ReviewedRootRecipe(work_plan(), {filename: hashlib.sha256(data).hexdigest()})
    def unselected_callback(*args):
        pass
    assert recipe.accepts_callable(unselected_callback) is False
    assert recipe.accepts_callable(assembly.supported_plan) is True


@pytest.mark.parametrize("change", ["model", "auth", "provider"])
def test_new_transition_selection_does_not_broaden_image_eligibility(monkeypatch, change):
    plan = copy.deepcopy(work_plan())
    provider = plan["providers"][0]
    if change == "model":
        provider["config"]["default_model"] = "gpt-6.2-astra"
    elif change == "auth":
        provider["config"]["auth_mode"] = "chatgpt_codex"
    else:
        provider["module"] = "provider-openai-chatgpt"
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("Unqualified route read catalog"))
    assert assembly.select_root(plan) is None
