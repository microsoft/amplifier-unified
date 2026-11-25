"""One cold original authorizes only the displayed cache move and native-call limit."""

import copy
import json

import pytest

from test_context_continuity import target_transition_fixture
from test_generation_skill_cache_recovery import mount_controls, skill_plan

OLD_GEN = "a" * 32
NEW_GEN = "b" * 32
MODULE = "amplifier-module-tool-skills"
URL = "https://github.com/microsoft/amplifier-bundle-skills"
SUBDIR = "modules/tool-skills"


def qualified_registry(home, generation):
    release = home / "updates" / "releases" / generation
    registry = release / "foundation"
    cache = registry / "cache" / "skills"
    cache.mkdir(parents=True)
    for directory in (home, home / "updates", release.parent, release, registry, cache.parent, cache):
        directory.chmod(0o700)
    source = {"url": URL, "ref": "main", "subdirectory": SUBDIR, "revision": "c" * 40}
    installed = {"name": MODULE, "version": "1.0.0", "cacheSource": {
        "url": URL, "subdirectory": SUBDIR, "revision": "c" * 40, "dirty": False,
        "trackedContentSha256": "d" * 64}}
    files = {"validated.json": {"generationSchema": 1, "workerProtocol": 1},
             "runtime-sources.json": {MODULE: source}, "runtime-installed.json": [installed]}
    for name, value in files.items():
        path = release / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    (cache / ".synthetic-skills.lock").touch(mode=0o600)
    return registry


def mounted_plan(registry):
    plan, slots = skill_plan(registry, include_injected=True)
    for row in (plan["tools"][0], plan["agents"]["research"]["tools"][0]):
        row["source"] = f"git+{URL}@main#subdirectory={SUBDIR}"
    return plan, slots


@pytest.mark.parametrize("after_offer", [
    "approve", "skip-move", "change-move", "change-generation", "new-source-checkpoint",
    "changed-selection", "changed-tool", "changed-cache", "changed-stop",
])
async def test_one_approved_legacy_cache_move(tmp_path, monkeypatch, after_offer):
    from amplifier_web.skill_cache_move import verified_move

    home = tmp_path / "host"
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    old, new = (qualified_registry(home, generation) for generation in (OLD_GEN, NEW_GEN))
    first = await target_transition_fixture(tmp_path / "before")
    old_plan, _ = mounted_plan(old)
    mount_controls(first, old_plan)
    with pytest.raises(RuntimeError, match="summary_work_paused"):
        await first.prepare()
    row = first.adapter._recovery_record()
    original_calls = copy.deepcopy(row["calls"])
    assert len(original_calls) == 1 and original_calls[0]["state"] == "completed"
    checkpoint = first.adapter.path.read_bytes()
    journal = first.adapter.recovery_path.read_bytes()

    second = await target_transition_fixture(tmp_path / "after")
    new_plan, slots = mounted_plan(new)
    controls = mount_controls(second, new_plan)
    assert slots == (("tools", 0), ("agents", "research", "tools", 0))
    second.coordinator.register_capability("web.context_transition.skill_cache_move",
        lambda current, expected, previous: verified_move(home, new, slots, current, expected, previous))
    second.adapter.path.write_bytes(checkpoint)
    second.adapter.recovery_path.write_bytes(journal)
    status = await controls.perform("context.recovery.status", {})
    assert "allowanceOffer" not in status and "cacheMoveOffer" not in status
    targeted = await controls.perform("context.recovery.status", {"cacheMoveFromGeneration": OLD_GEN})
    offer = targeted["cacheMoveOffer"]
    assert "allowanceOffer" not in targeted
    identity = {k: offer["cacheMove"][k] for k in ("cacheMoveId", "fromGeneration", "toGeneration")}
    assert identity["fromGeneration"] == OLD_GEN and identity["toGeneration"] == NEW_GEN
    assert second.adapter.path.read_bytes() == checkpoint and second.adapter.recovery_path.read_bytes() == journal
    assert not second.raw.compactions
    claim = {"offerId": offer["offerId"], "additionalCalls": 4, "cacheMove": identity}
    epoch = offer["expectedInterruptionRevision"]
    if after_offer == "skip-move":
        del claim["cacheMove"]
    elif after_offer == "change-move":
        claim["cacheMove"]["cacheMoveId"] = "0" * 64
    elif after_offer == "change-generation":
        claim["cacheMove"]["toGeneration"] = "c" * 32
    elif after_offer == "new-source-checkpoint":
        second.adapter.path.write_text("{}")
    elif after_offer == "changed-selection":
        controls.selection_revision = "changed-revision"
    elif after_offer == "changed-tool":
        second.coordinator.config["tools"][1]["config"]["mode"] = "changed"
    elif after_offer == "changed-cache":
        (new / "cache" / "skills" / "unexpected-skill.py").write_text("# additional contents\n")
    elif after_offer == "changed-stop":
        epoch += 1
    if after_offer != "approve":
        assert not second.adapter.accepts_allowance(claim, epoch)
        assert not second.raw.compactions and second.adapter.recovery_path.read_bytes() == journal
        return

    assert second.adapter.accepts_allowance(claim, epoch)
    second.origin.update(inputId="only-fresh", inputIds=["only-fresh"], generationId="fresh-generation",
                         recoveryAllowance=claim, allowanceStopRevision=epoch)
    assert (await second.prepare())["outcome"] == "transitioned"
    final = second.adapter._recovery_record()
    assert final["maxCalls"] == 1 and final["calls"][:1] == original_calls
    assert final["additionalAllowance"]["authorizationId"] == "only-fresh"
    assert len(final["calls"]) == 1 + len(second.raw.compactions) <= 5
    second.adapter._journal = None
    assert not second.adapter.accepts_allowance(claim, epoch)
    assert "cacheMoveOffer" not in await controls.perform("context.recovery.status", {"cacheMoveFromGeneration": OLD_GEN})
