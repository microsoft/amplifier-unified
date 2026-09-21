# Live task continuity and execution-folder handoff

The opt-in runner `scripts/work_profile/continuity_handoff_acceptance.py` uses a
new private data directory, synthetic imported history and a synthetic Git
repository. It preserves the selected configured provider, model and reasoning
effort. It starts the real Unified HTTP service and worker, retains the Work
include graph and namespace resources, and uses shared task/worktree actions.
It does not open an existing user conversation or production app.

The context override is explicit and local for pre-merge qualification. Published
bundle/module sources continue to track `main`; tested commits are evidence only.
The module-root layout follows the integration workspace's reviewed worktrees.

```sh
PYTHONPATH=. /absolute/integration/.venv/bin/python \
  scripts/work_profile/continuity_handoff_acceptance.py \
  --allow-live --provider terra \
  --bundle /absolute/work-composition/bundle.md \
  --module-root /absolute/integration-workspace \
  --context-source /absolute/context-retention/modules/context-managed \
  --output /absolute/private/new-continuity-run
```

The output directory must not already exist. Provider calls are paid. Each model
phase has a 240-second deadline, summaries have a 120-second deadline, and the
handoff has a 90-second deadline. The synthetic saved task has a one-turn goal
cap so the harness never creates an unbounded automatic continuation. The saved
task remains active until explicitly completed; exhausting a turn cap does not
complete it. Each of four user inputs is explicit and sent once.

The pressure history is labelled synthetic imported evidence. Two real semantic
summaries must advance their covered boundary and retain the current correction
and an independent earlier reference. The harness then restarts the entire host
and worker, verifies durable task and goal restoration, and observes the module's
actual `restored` status before a later summary supersedes it. A managed Git
worktree handoff then has the real model call Bash with `pwd` and read an unknown
target token. Assertions compare authoritative tool results, source-file bytes,
canonical transcript prefixes, native identity, original history/settings home,
execution folder, task revision/correction, selected conversation and draft.

Recorded 2026-09-20: configured `terra` / `gpt-5.6-terra` / `high`, host source
`e37f37aa` plus the reviewed stop-admission fix, context source `9252e85`.
All 30 checks passed in 84.526 seconds. Four semantic compactions completed; the
first two boundaries advanced from message 20 to 23, and the saved message 23
checkpoint was observed restored after restart. No history-reader error occurred.
The context module's complete suite also passed 306 tests, including exact reminder
retention over two summaries and restore, ordinary-retention barriers and an
unresolved tool-call barrier.

This run found and fixed a real module issue: retaining a persisted ephemeral
system reminder previously prevented all later summary boundaries from moving
past its original position. Required reminders now remain verbatim in assembled
requests while later history can compact. Their role/metadata and the canonical
originals remain unchanged; summaries grant no new authority.

This is actual provider/host/tool evidence, not a visual browser or cross-device
test. It does not validate arbitrary providers, unbounded autonomous goals,
production deployment, or remote worktree migration. No original history was
replayed. Earlier exploratory outcomes are retained separately: one initial run
hit a saved-chat read error after an extra checkpoint, a second exposed the
stationary summary boundary, and a third passed before the direct restore-status
observation was added. The final run does not erase those earlier results.

Raw diagnostics and synthetic history stay private. After shutdown, remove only
literal selected credential copies from generated text/configuration files,
preserving originals outside the run. Scan SQLite without rewriting it; never
publish credentials or raw provider diagnostics in an acceptance report.
