# Release 0.20.134 qualification

This correction builds on 0.20.133, from merged main `63bf36070`. Development
qualification uses synthetic state in owned `dtu-unified-public-readiness-20261008`
and `/opt/unified-work-activity`; installed-host acceptance is separately authorized.

## Reproduction and changes

The user authorized repeated updates/restarts of spark-1 and navigation in their
existing Mac Edge window. After installing and reloading 0.20.133, a cold CPU-usage
chat opened with work details in 4.717 seconds; returning to that loaded chat took
1.616 and 1.605 seconds. Warm AHP/ACP opened in 367 ms and the previously failing
workspace-list chat opened in 240 ms. Warm CPU switches parsed zero additional
canonical event bytes; the remaining pause had a separate cause.

A finite 20-second live GIL sample contained 637 samples. 210 included deep copy,
130 through history admission in `SessionWarmup.run`, and 82 through the later
preparation snapshot. These copies materialized retained display history even
when a ready worker and unchanged canonical transcript already existed. Admission
now passes only identity/workspace metadata to its validators and stat probe;
preparation detaches the actual startup configuration. Explicit fork messages
remain included. `warmup.snapshot` timing makes future costs visible.

The update investigation also reproduced a staged release waiting without work
blockers. A live stack showed `verify_recorded`/`installed_graph` inspecting old
runtime generations while cleanup held the lifecycle lock needed by activation.
Qualification and recursive removal now use bounded cancellable subprocesses
outside that lock. Fresh configuration/process references, live leases, current,
previous and pending generations, transitive graph edges, receipts and shared
runtime checks precede atomic retirement. A receipt preserves interrupted removal;
partial renames remain uncertain and retained.

## Validation

- Warmup, automatic history and runtime retention: 114 DTU tests passed.
- Retention and adjacent lifecycle/update tests: 110 DTU tests passed before the
  final queue-budget regression, which is qualified separately below.
- Independent source reviews covered startup input parity and deletion authority.
- Final queue-budget regression: 28 retention tests passed.
- Frontend: 686 unit tests passed; two production builds had identical asset hashes.
- Release identity, notes, qualification and post-release contracts: 105 tests passed.

The initial warmup test invocation used a stale editable installation; it was
replaced with explicit candidate `PYTHONPATH=.` and is not counted as evidence.

## Limits and continued investigation

No final full local Python suite was rerun for these focused changes; the immutable
0.20.133 post-release full suite passed 7,152 tests. Its browser fixture failures
were repaired by merged #645 and #646, included here; later skipped browser lanes
are not claimed verified. Publication and exhaustive post-release checks remain
separate.

The large catalog (5,254 projects and 27,177 sessions on the inspected host) still
performs bounded background metadata reconciliation. There is also remaining
resource-graph maintenance cost. This release does not claim those follow-up
investigations complete or predeclare live 0.20.134 latency results.
