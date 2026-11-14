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
copy. The Python files in `amplifier_scheduling/` are the sole authored implementation
and `tests/` is the sole policy/store test suite. The Scheduling package workflow
tests those files through their own installed wheel on Linux and Windows.
Unified selects a published artifact separately, as described below. Its scheduler,
authorization, Stop handling, execution admission and browser integration tests
remain with Unified. There is no new scheduler process or database migration.

Use a separate environment for the standalone distribution for now. Do not
install it alongside `amplifier-unified`: Unified owns the selected installed
package files. Scheduling is not independently replaceable with pip in that prefix.

A later history-preserving repository import can use this project directly.
Keep changes here until that import and an explicit authoring handoff are complete;
do not maintain a second implementation at the old repository-root path.
Independent publication and Unified's dependency selection are separate delivery
steps. Existing schedule databases, run identities and uncertain outcomes retain
their current authority throughout that transition.

## Unified artifact selection

Unified consumes scheduling 0.1.0 from the public `scheduling-v0.1.0` release.
`../scheduling-delivery/` is a generated snapshot of its wheel runtime and license;
`../scheduling.source.json` records the exact artifact, producer source, file hashes
and runtime requirements. Never edit this copy by hand. Component development and
publication continue here independently while Unified's selected version stays fixed.

To select a release, download its four published assets into a private directory,
verify the source-receipt SHA256 against the reviewed publication, and run from
the Unified checkout:

```sh
python scripts/scheduling_delivery.py select --dist /path/to/release-assets \
  --revision FULL_PUBLISHED_SOURCE_SHA --receipt-sha256 REVIEWED_RECEIPT_SHA256
python scripts/scheduling_delivery.py verify
```

Selection verifies the original wheel/sdist/source receipt using the existing
pure artifact checks and refuses a locally modified delivery copy. It needs the
reviewed artifacts and receipt checksum, not the producer's Git checkout or history.
Review the generated
selection and any runtime requirement change together; the current Windows `tzdata`
requirement is declared by Unified. Only Unified's package metadata is installed,
with the selected provenance/license under `amplifier_web/component_delivery/`.

Normal wheel, source archive and editable builds verify the selected snapshot
offline through Hatch. The source archive carries the snapshot, receipt and build
helpers, so rebuilding does not need Git, network access or the producer checkout.
Unified's editable install maps the selected copy; develop this standalone package
in its separate environment, not by modifying generated app delivery. The root test
suite exercises host integration against its selected artifact; the five standalone
policy/store tests remain with their matching component wheel.

Ordinary app staging and rollback retain one installed owner and use existing app
activation rules. They do not download or update scheduling independently, alter
schedule databases, discard newer schedules, or replay uncertain run effects.

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
continues after artifact publication and app snapshot selection. A later separate
distribution consumer must select an available public dependency and remove the
embedded mapping together. Git-source,
registry and archive delivery have different updater contracts; publishing these
assets does not silently add archive support to the host component graph.
