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

This directory is temporarily authored in Amplifier Unified while the planned
`amplifier-platform-scheduling` repository is unavailable. It is not a generated
copy or an independently published source selection. The Python files in
`amplifier_scheduling/` are the sole implementation and `tests/` is the sole
policy/store test suite. Unified maps this nested package directly into its normal
wheel and editable install, and continues to import `amplifier_scheduling`; its scheduler, authorization, Stop handling, execution
admission and browser integration tests remain with Unified. There is no new
scheduler process, Unified runtime dependency or database migration in this packaging
step. A version in this project does not itself establish publication or an
independently selected Unified dependency.

Use a separate environment for the standalone distribution for now. Do not
install it alongside `amplifier-unified`: Unified still owns the same installed
package files until a later dependency-selection change removes that overlap.

A later history-preserving repository import can use this project directly.
Keep changes here until that import and an explicit authoring handoff are complete;
do not maintain a second implementation at the old repository-root path.
Independent publication and Unified's dependency selection are separate delivery
steps. Existing schedule databases, run identities and uncertain outcomes retain
their current authority throughout that transition.

## Standalone releases while this source is in Unified

The **Scheduling package** workflow qualifies changes in pull requests. Its manual
publication takes one full commit SHA already merged into Unified's `main`.
It builds this project once and runs the existing five tests against that same
installed wheel, outside the source checkout, on Linux and Windows. It publishes
the wheel, source archive, `SHA256SUMS`, and `scheduling-source.json` under
`scheduling-v<version>` in the public Unified repository. The source receipt binds
the component version, exact Git commit/tree, source files and artifact hashes.

The component release is explicitly **not Latest**. It does not change Unified's
app version, application publisher, update feed or installed dependency selection.
The release owner must coordinate the component version before dispatch; existing
tags and published bytes are never replaced. Retrying an interrupted draft can
upload only missing matching assets; a completed release is verified without
rewriting it. An identity or checksum mismatch requires review.

For an interrupted publication, rerun only the failed jobs so the publisher
downloads the original successful build artifact. A whole workflow rerun builds
again; timestamped archives are not promised to reproduce identical bytes and
will be refused if they differ from an existing release. Keep the original
workflow artifact available until publication and its checks are complete.

Unified still bundles this package, so the separate-environment restriction above
continues after artifact publication. A later consumer change must select an
available public dependency and remove the embedded mapping together. Git-source,
registry and archive delivery have different updater contracts; publishing these
assets does not silently add archive support to the host component graph.
