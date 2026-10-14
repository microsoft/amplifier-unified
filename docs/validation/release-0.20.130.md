# Release 0.20.130 qualification

Candidate: PR #632, including #623 at `d1ca5f45c`, performance fixes at
`619c1dc23`, and main `96618ee788` (#634). All application tests ran with
synthetic state in the owned `dtu-unified-public-readiness-20261008` environment.
No shared-host installation or restart was performed.

## Live investigation and component measurements

Read-only spark-1 probes reproduced seconds of delay on localhost, including a
47-byte `build.json` response, while the measured application-handler time was
sub-millisecond on slow health requests. That excludes browser connection-pool
limits as the sole explanation; the handler timer does not measure total latency.
External Python sampling identified event/history projection, resource traversal,
usage aggregation and persistence among the expensive paths. A later GIL profile
had 359 of 385 samples on the main thread. These samples identify work to improve,
not a precise attribution of every request's elapsed time.

Same-environment synthetic comparisons, median of three runs:

| Operation | Unchanged main | Candidate |
| --- | ---: | ---: |
| Event index, 48 model calls / 33,937,468 bytes | 198.1 ms | 76.2 ms |
| Disabled-budget admission report, 15,000 calls | 146.3 ms | 3.55 ms |

The candidate defers formatting recorded payloads until explicit inspection,
retains unrelated chat indexes on one failed read, uses the root event index for
plan summaries, and reuses usage aggregates when tool updates do not change their
inputs. Disabled-budget admission still enforces duplicate-call protection and
persists receipts. Enabled budgets retain their complete accounting path.

Automatic storage collection reads immutable resource bodies outside the app lock
and SQLite writer transaction, then revalidates all roots and source-index
bindings before deletion. Changed or uncertain reachability defers deletion.
Concurrent-writer, changed-root, changed-index and cancellation tests cover this.

These are component improvements, not a claim of resolved end-to-end live latency.
Large historical accounting persistence remains a follow-up candidate in #633.

## Checks

- 681 frontend unit tests passed; production assets rebuilt twice with identical
  file hashes for version 0.20.130.
- Final affected performance/history/GC/accounting/plan run: 157 passed. A broader
  integration run covering media, reservations, capacity, execution, navigation,
  history and response completion passed 366 tests before the last accounting
  refinements; those refinements are covered by the final 157-test run.
- Main #634/release/import-isolation integration: 116 passed, four optional runtime
  tests skipped in the host environment and then run with the native environment.
- Worker/authority/file-policy suite: 104 passed, five optional-runtime skips.
- Native Core, fresh workspace guidance, peer/goal ordering and task runtime:
  48 passed, no skips. The ordering fixture identifies admitted input separately
  from the new typed coordination observation; generation and receipt checks
  remain unchanged.
- Schedule/storage regression: 56 passed. The new schedule authorization tests
  fail before the fix and pass afterward.
- Browser acceptance passed for interim replies, demand loading, workspace
  awareness, model/effort selection, conversation switching and execution
  inspection. The switch test painted before the delayed server response
  (86.5 ms in the final run), preserved drafts and restored failed selections.
  Execution inspection exercised failed-read retry and copying complete content.
- The new performance regressions fail on unchanged main for GC lock ownership,
  eager recording formatting, unrelated-index eviction and plan hydration. The
  reasoning gesture and workspace transition tests also failed before their fixes.

The earlier full Python run completed with 7,006 passed, three failed, 159 skipped
and six passing subtests. The three failures were an outdated qualification
assertion, a storage-fixture race and a direct-script worker import defect. Each
was fixed and its affected suite rerun. This is not a claim that the final source
passed a second complete Python run.

The broad browser sweep also found failures on unchanged main. Baseline evidence
includes settings/navigation fixtures, execution controls and a post-reload media
playback timeout. The registry ownership fixture and stale execution-inspection
status assertion were repaired here. Remaining failures stay tracked under #629;
they must not be relabeled as successful acceptance or all dismissed as fixtures.

## Rollout limits

Publication still requires the selected-source package/frontend/native-runtime
receipts. Exhaustive hosted verification runs after publication and reports any
remaining failures separately. Installed PWA manifest adoption and spark-1 latency
after updating remain unverified. Direct Mac browser automation encountered an OS
permission prompt; no permission was granted or live browser state changed.

Detailed logs and profiles are retained in the workspace evidence directory
`output/release-followups-20261013`. Temporary resources and retention decisions
are recorded in the workspace manifest.
