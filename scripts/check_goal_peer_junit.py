"""Require the exact native goal/peer regression cases, never import skips."""
from collections import Counter
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def variants(name, values):
    return {f"{name}[{value}]" for value in values}


EXPECTED = {
    "tests.test_goal_peer_continuation": {
        "test_eligibility_is_read_only_and_preserves_exact_goal_state",
        "test_irrelevant_queues_cannot_hide_qualified_continuation",
        "test_host_capacity_denial_is_read_only",
        "test_finite_generic_goal_without_peer_capability_is_unchanged",
    } | variants("test_qualified_peer_precedes_automatic_goal_turn", ["no-goal", "active-goal"])
    | variants("test_ineligible_receipts_do_not_gain_yield_authority", [
        "paused", "blocked", "completed", "revised", "stop", "owner", "unknown",
        "submitting", "notify", "unqualified", "ordinary", "foreign",
    ]) | variants("test_host_readiness_requires_actual_root_and_generation", [
        "binding0", "binding1", "binding2", "binding3",
    ]) | variants("test_yield_rechecks_after_awaited_real_bridge", [
        "pause", "revision", "goal-replaced", "progress", "cap", "capacity",
        "stop", "activation", "error",
    ]) | variants("test_goal_denials_do_not_probe_or_yield", [
        "cap", "capacity", "terminal-event", "foreign-event",
    ]),
    "tests.test_task_goal_runtime": variants("test_task_goal_creation_uses_shared_runtime_defaults", [
        f"{timing}-{loop}" for timing in ["before-execution", "during-execution"]
        for loop in ["work-loop", "anchors-overlay"]
    ]) | variants("test_app_pause_during_goal_evaluation_cannot_become_achievement", [
        "work-loop", "anchors-overlay",
    ]) | variants("test_revised_task_survives_old_goal_evaluation_without_replay", [
        "work-loop", "anchors-overlay",
    ]),
}


def check(path):
    root = ET.parse(path).getroot()
    cases = root.findall(".//testcase")
    expected = Counter((module, name) for module, names in EXPECTED.items() for name in names)
    actual = Counter((case.get("classname"), case.get("name")) for case in cases)
    if actual != expected:
        raise ValueError(f"Native goal/peer identities differ: missing={list((expected-actual).elements())}, "
                         f"unexpected/duplicate={list((actual-expected).elements())}")
    if any(node.tag in {"failure", "error", "skipped"} for node in root.iter()):
        raise ValueError("Native goal/peer results contain failure, error or skip")
    for suite in root.iter("testsuite"):
        if any(int(suite.get(key, "0")) for key in ("failures", "errors", "skipped")):
            raise ValueError("Native goal/peer suite counters report failure, error or skip")
    return len(cases)


if __name__ == "__main__":
    print(f"PASS: {check(Path(sys.argv[1]))} native goal/peer cases, zero skips")