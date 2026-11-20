"""Native preparation stays bound to the original contract until a fresh, scoped approval."""

import copy
from types import SimpleNamespace

import pytest

from amplifier_web.host.config import configure_skill_cache
from amplifier_web.runtime_controls import RuntimeControls
from test_context_continuity import target_transition_fixture


def skill_plan(registry, explicit=None):
    skills = {"module": "tool-skills", "config": {"skills": ["retained-skill"]}}
    if explicit is not None:
        skills["config"]["cache_dir"] = str(explicit)
    bundle = SimpleNamespace(
        tools=[copy.deepcopy(skills), {"module": "tool-fixture", "config": {"mode": "original"}}],
        agents={"research": {"tools": [copy.deepcopy(skills)]}},
    )
    configure_skill_cache(bundle, registry)
    return {"tools": bundle.tools, "agents": bundle.agents}


def mount_controls(case, plan):
    coordinator = case.coordinator
    coordinator.config.update(plan)
    coordinator.context.config["summary_max_calls"] = 1
    coordinator.register_capability("web.provider_catalog", {"connectionKeys": {"fixture": "fixture-connection"}})
    foreground = coordinator.get_capability("web.context_transition.foreground")
    controls = RuntimeControls(SimpleNamespace(coordinator=coordinator, session_id="session"),
                               SimpleNamespace(generation=None, queued_inputs=0))
    controls.selection_revision = case.origin["selectionRevision"]
    controls.selection_binding = case.origin["connection"]
    coordinator.register_capability("web.context_transition.foreground", foreground)
    return controls


@pytest.mark.parametrize("change,permitted", [
    ("same-generation", True),
    ("new-generation", False),
    ("same-explicit-path", True),
    ("changed-explicit-path", False),
    ("changed-nested-explicit-path", False),
    ("changed-tool-configuration", False),
    ("changed-nested-skill-source", False),
    ("explicit-qualified-paths", False),
])
async def test_confirmed_native_preparation_after_worker_generation_change(tmp_path, monkeypatch, change, permitted):
    home = tmp_path / "host"
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    old_registry = home / "updates" / "releases" / ("1" * 32) / "foundation"
    new_registry = home / "updates" / "releases" / ("2" * 32) / "foundation"
    explicit = tmp_path / "user-skill-cache" if "explicit" in change else None
    if change == "explicit-qualified-paths":
        explicit = old_registry / "cache" / "skills"
    old_plan = skill_plan(old_registry, explicit)
    first = await target_transition_fixture(tmp_path / "before")
    mount_controls(first, old_plan)
    with pytest.raises(RuntimeError, match="summary_work_paused"):
        await first.prepare()
    record = first.adapter._recovery_record()
    assert record["completedParts"] == len(first.raw.compactions) == 1
    assert record["calls"][0]["state"] == "completed"
    assert record["calls"][0]["report"]["method"] == "native"
    previous_offer = (await first.adapter.recovery_control_status())["allowanceOffer"]
    old_claim = {"offerId": previous_offer["offerId"], "additionalCalls": 4}
    checkpoint, journal = first.adapter.path.read_bytes(), first.adapter.recovery_path.read_bytes()

    registry = old_registry if change == "same-generation" else new_registry
    explicit_after = tmp_path / "other-cache" if change == "changed-explicit-path" else explicit
    if change == "explicit-qualified-paths":
        explicit_after = new_registry / "cache" / "skills"
    new_plan = skill_plan(registry, explicit_after)
    if change == "changed-nested-explicit-path":
        new_plan["agents"]["research"]["tools"][0]["config"]["cache_dir"] = str(tmp_path / "other-child-cache")
    if change == "changed-tool-configuration":
        new_plan["tools"][1]["config"]["mode"] = "different"
    if change == "changed-nested-skill-source":
        new_plan["agents"]["research"]["tools"][0]["config"]["skills"] = ["different-skill"]
    second = await target_transition_fixture(tmp_path / "after")
    controls = mount_controls(second, new_plan)
    second.adapter.path.write_bytes(checkpoint)
    second.adapter.recovery_path.write_bytes(journal)
    status = await controls.perform("context.recovery.status", {})
    assert second.adapter.path.read_bytes() == checkpoint and second.adapter.recovery_path.read_bytes() == journal
    assert not second.raw.compactions
    assert ("allowanceOffer" in status) is permitted
    if not permitted:
        assert not second.adapter.accepts_allowance(old_claim, previous_offer["expectedInterruptionRevision"])
        assert second.adapter.recovery_path.read_bytes() == journal and not second.raw.compactions
    else:
        offer = status["allowanceOffer"]
        second.origin.update(inputId="approved-once", inputIds=["approved-once"], generationId="fresh-generation",
                            recoveryAllowance={"offerId": offer["offerId"], "additionalCalls": 4},
                            allowanceStopRevision=offer["expectedInterruptionRevision"])
        assert (await second.prepare())["outcome"] == "transitioned"
        final = await controls.perform("context.recovery.status", {})
        assert final["state"] == "committed" and final["maxCalls"] == 1
        assert final["additionalAllowance"]["usedCalls"] == len(second.raw.compactions)
        assert second.adapter._recovery_record()["calls"][0] == record["calls"][0]
