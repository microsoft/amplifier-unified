# Active shell frame and workspace query reuse

This pass removes repeated projection work from shared browser updates. It does
not change accepted-command durability, worker execution or saved transcripts.
The governing principles are attention-bounded working sets and small updates
visiting their dependencies in the architecture vision.

## Changed work and regression witnesses

- Keep the admitted session index when all retirement candidates are independently
  protected by active work, runtime ownership, pending configuration or approval.
  Eligible idle rows still discard borrowed index aliases before both exclusive
  ownership proofs and still save before removal.
- Prepare one browser frame's shell client, composition and bootstrap hash once.
  Preserve bound-client authority, scope, preview/recovery and registration expiry.
  This is reuse within a frame, without a new persistent shell-descriptor cache.
- Keep one bounded detached workspace query summary. Distinct cold queries reuse
  it while list identity and committed workspace epochs agree. Every query pays
  its complete input-budget charge. Replacement never mutates prior job inputs.
  Committed and unknown invalidation immediately release the old registry;
  close also clears it. Expanded configuration is excluded, and alternative
  caller-owned registries are captured independently.
- A committed workspace replacement invalidates the admitted index even when the
  registry count does not change. This closes the freshness risk exposed by
  retaining indexes during active work.

All three old-behavior counterchecks failed as intended: four active-index cases,
shared input identity across distinct cold queries, and both weak-reference
retention cases. A separate same-count workspace replacement witness also failed
before its fix. Focused frame/residency (74), query/dependency/transport (59), and
final query/resource (65) checks passed with no skips; these overlapping groups
must not be summed. All 1,039 frontend tests passed. Both ownership proofs,
per-query budget refusal, preserved prior inputs, and other-client scope/expiry
behavior have direct assertions. An independent source review approved the final
implementation after the retention finding was fixed.

## Measurement method

The comparison uses a frozen copy of 27,508 sessions / 5,272 projects and a
274-chat workspace paged in groups of 40. It has four effective CPU cores and
8 GiB. Backing files are read-only; copied owners cannot execute and external
networking and copied automations remain disabled. Fourteen synthetic owners are
allowed. RuntimeManager and its subprocess workers are real, using Core 2.0.2
and a deterministic local 250 ms provider. Normal cold module qualification is
bypassed in this private fixture. This is neither live-provider acceptance nor an
atomic full-fidelity capture; all 32 captured event shards are checked, while
full Work-history fidelity is explicitly unqualified.

The fresh baseline is `3d56cca31254aa521cbab570747e5634c3c9f6f1`, whose product
tree matches the subsequently squashed #831 merge. The first candidate is
`218899a6df92bdd9a821495cfbc68715b9bbbd41`; it does not yet include workspace
summary reuse. The final measured source is
`48f08e299d0e5d3a086b3d1af034e3140d0a3230`, including the 0.20.178 bundled client.
Later evidence-only edits do not relabel that measurement.

Each serial stage exercises workspace selection/filter/clear/next/previous and
three warm sends into the same retained real worker. A separate 12-worker run
checks streaming, copied/synthetic chat switching and Stop. Every stage asserts
worker identity and zero remaining owned workers. Namespace setup is excluded.
Runs are sequential, browser contexts are fresh and service/filesystem caches are
warm; synthetic history grows. Three-send p95 is the observed maximum, not a
stable tail estimate or randomized causal attribution. Use the measurements as
regression signals, not a promise that all latency goals have been met.

A separate baseline profile collected 4,634 samples without read errors over
25 seconds at 100 Hz. Inclusive stacks overlap: browser frame building appeared
in 11.56 seconds, shell snapshots in 8.02, saving in 5.02, and query capture in
4.19. Input copying was the largest individual Python leaf at 2.41 seconds.
These are investigation signals, not shares of one user request or additive
request timings. Profiled runs are separate from timing comparisons.

## Current results and desired targets

The [aggregate receipt](frame-projection633-qualification.json) retains baseline,
intermediate candidate, final candidate, confirmation and original-code control
separately, including warnings and raw receipt hashes. Raw copied-owner identities
remain in private evidence. All functional checks passed and each run stopped its
owned workers. This is a correctness checkpoint, not proof of stable latency
improvement.

Warm-send acknowledgement, milliseconds (median / observed maximum):

| Active workers | Initial baseline | Final candidate | Candidate confirmation | Original-code control |
| --- | ---: | ---: | ---: | ---: |
| 1 | 801 / 805 | 717 / 738 | — | — |
| 6 | 993 / 1,059 | 913 / 942 | 933 / 945 | — |
| 12 | 1,418 / 1,504 | 1,236 / 1,410 | 1,389 / 1,713 | 1,320 / 1,407 |

The confirmation retained a slower twelve-worker maximum. Neither three sends
nor these sequential runs establish a stable tail improvement. At twelve workers,
streaming lag p95 was 336 ms initially, 356 ms for the final candidate, 385 ms on
confirmation and 334 ms for the original-code control. No streaming improvement
is claimed.

Warnings use both a 20% and a 100 ms increase over the initial baseline. Three
final-candidate warnings were investigated without discarding their first result:

- Six-worker filter: 455 ms, then 474 ms on confirmation, versus 344 ms initially.
  Both candidate samples meet the 500 ms goal but are slower than that baseline.
- Twelve-worker previous page: 769 ms, then 543 ms, versus 555 ms initially.
  Original code with the accumulated fixture measured 794 ms. All meet the
  one-second page goal; no repeatable candidate-specific regression was established.
- Twelve-worker copied-chat selection median: 535 ms, then 544 ms, versus 406 ms
  initially. Original code with the accumulated fixture measured 513 ms. This
  supports fixture/run variation as a substantial contributor, without proving
  the remaining difference is unrelated to the candidate.

The final twelve-worker filter was 507 ms and confirmation was 573 ms; the
original-code control was 908 ms. That target remains unmet even though the final
candidate was faster in this sample. The original-code control reused the same
accumulated synthetic history and fixed source `3d56cca3`; it is an additional
control, not a replacement baseline. No further tuning or measurements are queued:
the user requested a pause at this checkpoint.

The desired limits remain 300 ms p95 for warm acknowledgement, 1 second for
workspace/page display, 500 ms for filtering, and 250 ms p95 added streaming lag.
Functional success does not close #633 while those targets remain unmet. Resume
only as part of a subsequently selected performance priority.
