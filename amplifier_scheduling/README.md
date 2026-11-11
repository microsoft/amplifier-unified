# Reusable scheduling policy and run store

This standard-library package owns recurrence calculation and durable admission
receipts. It has no Amplifier, provider, app, or tool dependency. The host supplies
explicit authorization, task/stop/question gates, input submission, and observed
run outcomes. It never runs model work or grants permission itself.

`normalize`, `next_after`, `preview`, and `due_occurrence` are pure functions over
explicit timestamps and IANA timezones. Once, absolute UTC intervals (minimum
one minute), daily local times, and selected weekdays are supported. Nonexistent
local times are skipped; folds use the first occurrence once. Due identity is
UTC. Preview and execution use the same policy. Missed policy `latest` selects
only the most recent occurrence; `skip` skips occurrences over one minute late.
No catch-up flood is produced after a long shutdown.

`ScheduleStore` owns a separate private SQLite database. Schedule edits use exact
revision CAS and stable command receipts. A database owner lease admits unique
`(schedule_id,due_utc)` runs, with stable IDs and no overlap. Run claims persist
before any external call. Hosts that create a destination first reserve its identity in `creating`. Hosts mark `submitting` before crossing an input
boundary, and must never retry ambiguous handoff. A new owner converts previous
creating/claimed/submitting/accepted/running runs to `unknown` and schedules to visible
`needs_review`, retaining the original input identity and evidence. Unknown runs
require deliberate reconciliation; they cannot release an overlapping new run.

Run history retains the most recent 100 terminal records per schedule by default;
active and unknown rows are never pruned. Idempotency receipts remain durable
rather than being evicted into permission to repeat an old mutation. Methods
perform no network operations; SQLite transactions never enclose provider or
runtime waits. Bounded history means old direct run reads can become unavailable,
not silently replaced by fabricated output.

## Build and test independently

This directory is the complete source project for the `amplifier-scheduling`
Python distribution. It needs Python 3.13 or newer and an IANA timezone database.
Windows installations declare `tzdata`; other platforms normally supply the
database through the operating system. On a minimal system without it, install
`tzdata` in the same Python environment.

From this directory, in an owned development environment:

```sh
uv build
uv venv .venv
uv pip install --python .venv/bin/python "dist/amplifier_scheduling-0.1.0-py3-none-any.whl[test]"
.venv/bin/python -m pytest -q tests
```

On Windows, use `.venv\Scripts\python.exe` for the interpreter paths. The wheel
contains only this library and its distribution metadata; the source archive also
contains its five policy/store tests. Those tests exercise DST gaps/folds, missed
runs, owner changes, unknown outcomes, revision checks, command idempotency and
retention using temporary SQLite databases. They require no app, native runtime,
provider, account, network service or sibling checkout. To check an installed
wheel, run these tests from a separate directory containing only the test file;
do not add a source checkout to `PYTHONPATH`.

## Ownership and delivery

The Python files here are the sole implementation and the tests here are the sole
policy/store test suite. Unified continues to bundle these same files and import
`amplifier_scheduling`; its scheduler, authorization, Stop handling, execution
admission and browser integration tests remain with Unified. There is no new
scheduler process, Unified runtime dependency or database migration in this packaging
step. The `0.1.0` component version is preparation metadata, not a publication or
an independently selected Unified dependency.

Use a separate environment for the standalone distribution for now. Do not
install it alongside `amplifier-unified`: Unified still owns the same installed
package files until a later dependency-selection change removes that overlap.

A later history-preserving repository import can use this project directly.
Independent publication and Unified's dependency selection are separate delivery
steps. Existing schedule databases, run identities and uncertain outcomes retain
their current authority throughout that transition.
