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
    "amplifier_web/context_continuity.py": "af8421c48551e66c4ada360ee477ba666b79cfe8747210c683085ebfe8fe0670",
    "amplifier_web/host/session.py": "3c8cfa6965597af401be2b53f6c6aeb8603204eeb905c6e70319ca8690d7ddca",
    "amplifier_web/runtime_worker.py": "593840f8d769d86a9b79be2b6a739975ff5c76c6da6c37f70acf1e87dab7753f",
    "amplifier_web/runtime_controls.py": "d37509a526f7c10df31c72a42041cf425cf972740ccec6ffbb56a16bb2d8d9ab",
    "amplifier_web/session_store.py": "c1aaac31fbe7458b4c92f5b6529af3428421cd374f687b06d4420df67f31dd1d",
    "amplifier_module_context_managed/boundary.py": "c09150e7e1894ca7b80cf0d63cb732cabec4f431df63d76c481198ab00e258e3",
    "amplifier_module_context_managed/checkpoint.py": "3de7548aede6f5105c1d1af21190ea9428775c13e1fab09299345f3dc4457e43",
    "amplifier_module_context_managed/recovery.py": "4d311e7de9e1428cd247ba05910c47d496b554c66b47cff352b9cbcc9435d99b",
    "amplifier_module_context_managed/ordinary_transition.py": "9e48247e43fc371ecece198eaabee45a5ffb716ad9f5db36deaf7c00ff889b23",
    "amplifier_module_context_managed/transition.py": "2f31cbd987adc8ac0584863dcede59c1aa8c98902f98b8bf0511f435ba5e8683",
}


def catalog_digest(sources):
    return hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def assert_transition_closure(manifest):
    assert all(manifest["sources"].get(name) == digest for name, digest in TRANSITION_OWNERS.items())
    # The accepted decision namespace adds three explicit semantic owners while
    # this selection retains all transition owners and the combined session.
    assert len(manifest["sources"]) == 312
    assert catalog_digest(manifest["sources"]) == "faf9fd23350a6f08df936c58a7dfc5e41ffe59277b02745a1311459e4ff44c9c"


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
        "commit": "694cf37c00f3f09fbc16c6120289298759c0c06a",
        "tree": "3f1cf59873560586620f301db133c2f17567ec72",
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
