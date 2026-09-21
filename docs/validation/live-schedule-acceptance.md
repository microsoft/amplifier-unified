# Actual scheduled continuation acceptance

One explicitly authorized one-shot schedule fired on the production scheduler's real wall clock into its original saved task, using the existing `terra` / `gpt-5.6-terra` / `high` configuration. It produced the exact synthetic response requested. Another task remained selected in production Chromium, with its unsent draft intact.

The full host and worker were closed and restarted in the same fresh private home. The test waited for the actual 30-second scheduler lease transfer and two more poll intervals. The completed run and input identity remained unchanged; there was no new generation or provider admission. The durable task stayed active, and the original transcript hashes, selected task and draft were preserved.

The [redacted report](live-schedule-2026-09-20.json) contains **25 attributed passing checks**. The original live report passed 21 of 23 assertions: two fixture assertions incorrectly expected only one provider call. The real goal controller makes a response call and an evaluator call even with a one-turn cap. Both calls used the selected Terra provider, totaling exactly **two actual calls**, within the authorized maximum. A subsequent execution-disabled inspection confirmed the same exact two admission IDs and completed due receipt. It made **zero additional calls**. The original live report was retained unchanged; no all-green initial run is claimed.

Source host: `1e7273ef14c4d7504e0acc8af893957aa3e01809`. Exact live harness SHA-256 is recorded in the report. The installed streaming orchestrator was `9e5d4ff07c7f57fbdf233b869d94e18c910d83b9`; its goal evaluator runs after the turn even at the cap. The evidence does not equate the evaluator's achieved event or scheduled-run completion with completion of the durable task. No product changes were required.

The harness first completed offline runtime/browser preparation with seven checks, zero model calls, and no activated schedule. An initial preview timestamp precision mismatch was corrected before the paid run. The real run uses the ordinary background scheduler: it does not advance a fake clock, invoke `tick`, directly submit input, or retry an uncertain effect. The Work profile retains its include graph and instructions, and caps the synthetic loop to one iteration and the task to one turn.

```sh
PYTHONPATH=".:$MODULE_ROOT/repos/amplifier-module-hooks-approval" \
  /path/to/reviewed-runtime/python scripts/work_profile/live_schedule_acceptance.py \
  --prepare-only --provider terra --bundle /path/to/work/bundle.md \
  --module-root "$MODULE_ROOT" --output /tmp/fresh-schedule-preparation

# Only after preparation passes; requires a second fresh folder.
PYTHONPATH=".:$MODULE_ROOT/repos/amplifier-module-hooks-approval" \
  /path/to/reviewed-runtime/python scripts/work_profile/live_schedule_acceptance.py \
  --allow-live --provider terra --bundle /path/to/work/bundle.md \
  --module-root "$MODULE_ROOT" --output /tmp/fresh-live-schedule
```

The optional `saved_schedule_acceptance.py --run <finished-run> --output <new-folder>` reads saved evidence with runtime execution disabled. Two regression tests cover bounded response/evaluator accounting and rejection of changed, duplicate or missing admission IDs.

Scope: actual one-shot due execution and completed-run restart recovery. Recurrence, DST, missed runs, and ambiguous admission recovery are covered by separate deterministic tests; this live run does not replace them. Concrete preview and activation use the authenticated shared UI action path, while browser selection and draft preservation use the production browser. It does not claim clicks through every schedule form control.

All raw history, diagnostics and screenshots remain in private temporary folders. Generated credential copies are redacted after shutdown, and databases are scanned without mutation. No production home, account/model setting, scheduler, or task was changed. No deployment, version bump, or additional paid rerun occurred.
